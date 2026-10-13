#!/usr/bin/env python3
"""TE344 CLI for configured label geometry and one-row printing."""

import argparse
import base64
import contextlib
import json
import sys
import time
from pathlib import Path

import usb.core
import usb.util
from content import decode_image
from layout import aligned_bitmap_bytes, fit_to_cell
from media import DEFAULT_MEDIA, MediaProfile, load_media_profile

VID = 0x1203
PID = 0x0273
# Built-in font dimensions are fixed by the printer firmware.
FONTS = {
    1: (8, 12),
    2: (12, 20),
    3: (16, 24),
    4: (24, 32),
    5: (32, 48),
}
DEFAULT_FONT = 2
QR_MODULE_DOTS = 1
QR_ECC = "M"
DEFAULT_SPEED = 1.5
DEFAULT_PRESENT_ROWS = 5
SPEEDS = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0)

STATUS = {
    0x00: "normal",
    0x01: "head opened",
    0x02: "paper jam",
    0x03: "paper jam and head opened",
    0x04: "out of paper",
    0x05: "out of paper and head opened",
    0x08: "out of ribbon",
    0x09: "out of ribbon and head opened",
    0x0A: "out of ribbon and paper jam",
    0x0B: "out of ribbon, paper jam and head opened",
    0x0C: "out of ribbon and out of paper",
    0x0D: "out of ribbon, out of paper and head opened",
    0x10: "pause",
    0x20: "printing",
    0x80: "other error",
}


def open_printer():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("TE344 not found (vid:pid 1203:0273)")
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    usb.util.claim_interface(dev, intf.bInterfaceNumber)
    ep_out = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_OUT,
    )
    ep_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_IN,
    )
    if ep_out is None or ep_in is None:
        sys.exit("bulk in/out endpoints not found")
    return dev, intf, ep_out, ep_in


def drain(ep_in, timeout=200):
    chunks = []
    while True:
        try:
            data = bytes(ep_in.read(64, timeout=timeout))
        except usb.core.USBError:
            break
        if not data:
            break
        chunks.append(data)
        timeout = 80
    return b"".join(chunks)


def write(ep_out, payload: bytes):
    sent = ep_out.write(payload, timeout=3000)
    text = payload.decode("ascii", "replace").replace("\r", "").strip()
    print(f"TX {sent} bytes")
    if text:
        print(text)
    return sent


def status(ep_out, ep_in):
    ep_out.write(b"\x1b!?", timeout=2000)
    data = drain(ep_in, timeout=1500)
    if not data:
        return None, "no reply"
    code = data[0]
    return code, STATUS.get(code, f"unknown 0x{code:02x}")


def wait_ready(ep_out, ep_in, timeout=8.0, wait_for_motion=False):
    deadline = time.time() + timeout
    last = None
    saw_motion = False
    while time.time() < deadline:
        code, name = status(ep_out, ep_in)
        if (code, name) != last:
            print(f"status 0x{code:02x}: {name}" if code is not None else f"status: {name}")
        last = (code, name)
        if code == 0x20:
            saw_motion = True
        elif code == 0x00 and (saw_motion or not wait_for_motion):
            return code, name
        elif code not in (None, 0x00, 0x20):
            return code, name
        time.sleep(0.25)
    if saw_motion and last and last[0] == 0x00:
        return last
    if not saw_motion:
        return last[0] if last else None, "no motion"
    return last if last else (None, "timeout")


def tspl_text(value):
    return value.replace("\\", "\\\\").replace('"', '\\["]')


def column_fields(text, pos, media=DEFAULT_MEDIA):
    fields = text.split("/")
    if pos < 1 or pos > media.columns:
        sys.exit(f"position must be 1..{media.columns}")
    if pos - 1 + len(fields) > media.columns:
        sys.exit(
            f"{len(fields)} fields starting at column {pos} pass the {media.columns} columns"
        )
    return fields


def field_commands(fields, pos, font, media=DEFAULT_MEDIA):
    font_w, font_h = FONTS[font]
    cell_w = media.cell_width_dots
    cell_h = media.cell_height_dots
    y = (cell_h - font_h) // 2
    lines = []
    for offset, field in enumerate(fields):
        if field == "":
            continue
        column = pos - 1 + offset
        text_w = font_w * len(field)
        x = column * media.column_pitch_dots + max(0, (cell_w - text_w) // 2)
        lines.append(f'TEXT {x},{y},"{font}",0,1,1,"{tspl_text(field)}"\r\n')
    return "".join(lines)


def validate_payload(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ValueError(f"{label} must contain printable ASCII characters only")
    return value


def text_command(value, slot, font, media=DEFAULT_MEDIA):
    validate_payload(value, "text")
    if not 1 <= slot <= media.columns:
        raise ValueError(f"slot must be 1..{media.columns}")
    if font not in FONTS:
        raise ValueError("font must be 1..5")
    font_w, font_h = FONTS[font]
    if len(value) * font_w > media.cell_width_dots or font_h > media.cell_height_dots:
        raise ValueError("text does not fit inside the configured label at this font")
    return field_commands([value], slot, font, media)


def qr_command(value, slot, ecc=QR_ECC, module_dots=QR_MODULE_DOTS,
               media=DEFAULT_MEDIA):
    validate_payload(value, "QR data")
    if '"' in value:
        raise ValueError('QR data cannot contain a double quote')
    if not 1 <= slot <= media.columns:
        raise ValueError(f"slot must be 1..{media.columns}")
    if ecc not in ("L", "M", "Q", "H"):
        raise ValueError("QR ECC must be one of L, M, Q, H")
    if not 1 <= module_dots <= 10:
        raise ValueError("QR module size must be 1..10 dots")
    x = (slot - 1) * media.column_pitch_dots + media.cell_width_dots // 2
    y = media.cell_height_dots // 2
    # The TE344 needs a cell-center anchor with the X/J alignment options to print visibly.
    return (f'QRCODE {x},{y},{ecc},{module_dots},A,0,'
            f'X{media.cell_width_dots},J5,M2,S7,"{value}"\r\n')


def make_print_job(kind, value, slot, font=DEFAULT_FONT, ecc=QR_ECC,
                   module_dots=QR_MODULE_DOTS, speed=DEFAULT_SPEED,
                   media=DEFAULT_MEDIA):
    validate_speed(speed)
    if kind == "text":
        content = text_command(value, slot, font, media)
    elif kind == "qr":
        values = value if isinstance(value, list) else [value]
        if (not values or len(values) > media.columns
                or slot + len(values) - 1 > media.columns):
            raise ValueError("QR data count must fit in the remaining slots of the row")
        content = "".join(qr_command(item, slot + index, ecc, module_dots, media)
                          for index, item in enumerate(values))
    elif kind == "image":
        raise ValueError("image jobs require make_image_job")
    else:
        raise ValueError("type must be text or qr")
    setup = (
        f"SIZE {media.media_width_mm:g} mm, {media.label_height_mm:g} mm\r\n"
        f"GAP {media.row_gap_mm:g} mm, 0 mm\r\n"
        "OFFSET 0 mm\r\n"
        "LIMITFEED 20 mm\r\n"
        "SET TEAR OFF\r\n"
        "SET PEEL OFF\r\n"
        "SET CUTTER OFF\r\n"
        "DIRECTION 1\r\n"
        "REFERENCE 0,0\r\n"
        "SHIFT 0\r\n"
        f"SPEED {speed:g}\r\n"
        "DENSITY 12\r\n"
    )
    return setup + "CLS\r\n" + content + "PRINT 1,1\r\n"


def image_bitmap(image_bytes, slot, fit="contain", threshold=128, media=DEFAULT_MEDIA):
    if not 1 <= slot <= media.columns:
        raise ValueError(f"slot must be 1..{media.columns}")
    cell_w, cell_h = media.cell_width_dots, media.cell_height_dots
    content = decode_image(image_bytes, threshold)
    source = fit_to_cell(content, media, fit)
    byte_width, raw = aligned_bitmap_bytes(source)
    x = (slot - 1) * media.column_pitch_dots + (cell_w - byte_width * 8) // 2
    return x, 0, byte_width, cell_h, raw


def make_image_job(image_bytes, slot, speed=DEFAULT_SPEED, fit="contain",
                   threshold=128, media=DEFAULT_MEDIA):
    x, y, byte_width, height, bitmap = image_bitmap(
        image_bytes, slot, fit, threshold, media
    )
    validate_speed(speed)
    header = (
        f"SIZE {media.media_width_mm:g} mm, {media.label_height_mm:g} mm\r\n"
        f"GAP {media.row_gap_mm:g} mm, 0 mm\r\n"
        "OFFSET 0 mm\r\nLIMITFEED 20 mm\r\n"
        "SET TEAR OFF\r\nSET PEEL OFF\r\nSET CUTTER OFF\r\n"
        "DIRECTION 1\r\nREFERENCE 0,0\r\nSHIFT 0\r\n"
        f"SPEED {speed:g}\r\nDENSITY 12\r\nCLS\r\n"
        f"BITMAP {x},{y},{byte_width},{height},0,"
    ).encode("ascii")
    return header + bitmap + f"\r\nPRINT 1,1\r\n".encode("ascii")


def capabilities():
    return capabilities_for(DEFAULT_MEDIA)


def capabilities_for(media):
    return {
        "device": "TE344",
        "media": media.name,
        "resolution_dpi": media.resolution_dpi,
        "dots_per_mm": media.dots_per_mm,
        "label_mm": {"width": media.label_width_mm, "height": media.label_height_mm},
        "gap_mm": {"column": media.column_gap_mm, "row": media.row_gap_mm},
        "slot_count": media.columns,
        "media_width_mm": media.media_width_mm,
        "slot_pitch_mm": media.label_width_mm + media.column_gap_mm,
        "row_pitch_mm": media.label_height_mm + media.row_gap_mm,
        "presentation": {"default_rows": DEFAULT_PRESENT_ROWS,
                         "same_row_return_rows": DEFAULT_PRESENT_ROWS + 1,
                         "mechanism": "explicit_feed_backfeed",
                         "reposition_verified": False},
        "speed_ips": {"default": DEFAULT_SPEED, "supported": list(SPEEDS)},
        "types": ["text", "qr", "image"],
        "text_fonts": {str(key): {"width_dots": value[0], "height_dots": value[1]}
                       for key, value in FONTS.items()},
        "qr": {"ecc": ["L", "M", "Q", "H"], "module_dots": [1, 10]},
    }


def json_job(raw, media=DEFAULT_MEDIA):
    try:
        job = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(job, dict):
        raise ValueError("JSON job must be an object")
    unknown = set(job) - {
        "type", "data", "slot", "font", "ecc", "module_size",
        "present_rows", "return_rows", "speed"
    }
    if unknown:
        raise ValueError(f"unknown JSON job field(s): {', '.join(sorted(unknown))}")
    kind = job.get("type")
    value = job.get("data")
    slot = job.get("slot")
    if isinstance(slot, bool) or not isinstance(slot, int):
        raise ValueError(f"JSON job slot must be an integer from 1 to {media.columns}")
    if not 1 <= slot <= media.columns:
        raise ValueError(f"JSON job slot must be an integer from 1 to {media.columns}")
    font = job.get("font", DEFAULT_FONT)
    ecc = job.get("ecc", QR_ECC)
    module_dots = job.get("module_size", QR_MODULE_DOTS)
    present_rows = job.get("present_rows", DEFAULT_PRESENT_ROWS)
    return_rows = job.get("return_rows", 0)
    speed = job.get("speed", DEFAULT_SPEED)
    if isinstance(font, bool) or not isinstance(font, int):
        raise ValueError("JSON job font must be an integer from 1 to 5")
    if font not in FONTS:
        raise ValueError("JSON job font must be an integer from 1 to 5")
    if isinstance(module_dots, bool) or not isinstance(module_dots, int):
        raise ValueError("JSON job module_size must be an integer from 1 to 10")
    if not 1 <= module_dots <= 10:
        raise ValueError("JSON job module_size must be an integer from 1 to 10")
    if ecc not in ("L", "M", "Q", "H"):
        raise ValueError("JSON job ecc must be one of L, M, Q, H")
    if isinstance(present_rows, bool) or not isinstance(present_rows, int):
        raise ValueError("JSON job present_rows must be an integer from 0 to 119")
    max_rows = 9999 // media.row_pitch_dots
    if not 0 <= present_rows <= max_rows:
        raise ValueError(f"JSON job present_rows must be an integer from 0 to {max_rows}")
    if isinstance(return_rows, bool) or not isinstance(return_rows, int):
        raise ValueError("JSON job return_rows must be an integer from 0 to 119")
    if not 0 <= return_rows <= max_rows:
        raise ValueError(f"JSON job return_rows must be an integer from 0 to {max_rows}")
    validate_speed(speed)
    return (kind, value, slot, font, ecc, module_dots, present_rows,
            return_rows, float(speed))


def validate_speed(speed):
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ValueError("speed must be one of: " + ", ".join(map(str, SPEEDS)))
    if speed not in SPEEDS:
        raise ValueError("speed must be one of: " + ", ".join(map(str, SPEEDS)))
    return float(speed)


def prepare(ep_out, ep_in):
    drain(ep_in, timeout=100)
    ep_out.write(b"~!T\r\n", timeout=2000)
    model = drain(ep_in, timeout=1500)
    print(f"model reply: {model!r}")
    if model.strip() != b"TE344":
        sys.exit(
            "Printer did not answer model TE344. "
            "If it is in dump mode, power it off and on, then run this again."
        )
    code, name = wait_ready(ep_out, ep_in, timeout=3)
    if code != 0x00:
        sys.exit(f"printer not ready: {name}")


def move_rows(ep_out, ep_in, command, rows, speed=DEFAULT_SPEED, media=DEFAULT_MEDIA):
    if rows < 0 or rows * media.row_pitch_dots > 9999:
        raise ValueError("row movement exceeds the TSPL distance limit")
    if rows == 0:
        print(f"{command}: no movement (0 rows)")
        return {"command_sent": False, "status_code": None, "status": "no movement"}
    speed = validate_speed(speed)
    dots = rows * media.row_pitch_dots
    # CLS drops the previous image so a feed does not reprint it.
    job = f"CLS\r\nSPEED {speed:g}\r\n{command} {dots}\r\n"
    print(f"{command} {rows} row(s), {dots} dots ({rows * (media.label_height_mm + media.row_gap_mm):g} mm)")
    write(ep_out, job.encode())
    time.sleep(0.4 * rows + 0.5)
    code, name = status(ep_out, ep_in)
    if code == 0x20:
        time.sleep(0.4 * rows + 0.4)
        code, name = status(ep_out, ep_in)
    print(f"status 0x{code:02x}: {name}" if code is not None else f"status: {name}")
    if code is None:
        sys.exit(f"{command} was sent, but the printer returned no status; movement is unknown")
    if code not in (0x00, None) and name not in ("normal", "printing"):
        sys.exit(f"{command} failed: {name}")
    if code == 0x20:
        sys.exit(f"{command} still moving")
    print("command completed; paper movement is not confirmed by the status response")
    return {"command_sent": True, "status_code": code, "status": name,
            "movement_confirmed": False}


def wait_cycle(ep_out, ep_in, timeout=5.0):
    """Return after the printer starts a move and comes back to ready."""
    deadline = time.time() + timeout
    last = None
    saw_motion = False
    while time.time() < deadline:
        code, name = status(ep_out, ep_in)
        if (code, name) != last:
            print(f"status 0x{code:02x}: {name}" if code is not None else f"status: {name}")
        last = (code, name)
        if code == 0x20:
            saw_motion = True
        elif saw_motion and code == 0x00:
            return {"cycle_observed": True, "final_status_code": code}
        elif code not in (None, 0x00, 0x20):
            sys.exit(f"printer error: {name}")
        time.sleep(0.05)
    sys.exit("no complete busy-to-ready cycle was observed; output may have occurred, do not retry blindly")


def print_fields(ep_out, ep_in, text, font, pos, new_row,
                 present_rows=DEFAULT_PRESENT_ROWS,
                 speed=DEFAULT_SPEED, return_rows=0, media=DEFAULT_MEDIA):
    fields = column_fields(text, pos, media)
    where = "next row" if new_row else "current row"
    print(f"print {text!r} size {font} at column {pos} on {where}")

    max_rows = 9999 // media.row_pitch_dots
    if not 0 <= present_rows <= max_rows:
        raise ValueError(f"present_rows must be an integer from 0 to {max_rows}")
    if not 0 <= return_rows <= max_rows:
        raise ValueError(f"return_rows must be an integer from 0 to {max_rows}")

    # Configure gap media without recalibrating or homing on every print.
    # GAPDETECT actively feeds stock to calibrate; GAP only sets gap size.
    setup = (
        f"SIZE {media.media_width_mm:g} mm, {media.label_height_mm:g} mm\r\n"
        f"GAP {media.row_gap_mm:g} mm, 0 mm\r\n"
        "OFFSET 0 mm\r\n"
        "LIMITFEED 20 mm\r\n"
        "SET TEAR OFF\r\n"
        "SET PEEL OFF\r\n"
        "SET CUTTER OFF\r\n"
        "DIRECTION 1\r\n"
        "REFERENCE 0,0\r\n"
        "SHIFT 0\r\n"
        f"SPEED {speed:g}\r\n"
        "DENSITY 12\r\n"
    )
    # HOME can trigger a long gap-seeking move on this short-label stock.
    write(ep_out, setup.encode())

    move_rows(ep_out, ep_in, "BACKFEED", return_rows, speed, media)

    if new_row:
        # TSPL FEED takes dots: 7 mm * 12 dots/mm = one 84-dot row.
        write(ep_out, f"FEED {media.row_pitch_dots}\r\n".encode())
        wait_cycle(ep_out, ep_in, timeout=20)

    job = (
        "CLS\r\n"
        + field_commands(fields, pos, font, media)
        + "PRINT 1,1\r\n"
    )
    write(ep_out, job.encode())
    wait_cycle(ep_out, ep_in, timeout=10)
    move_rows(ep_out, ep_in, "FEED", present_rows, speed, media)
    print("done")


def print_typed(ep_out, ep_in, kind, value, slot, font, ecc, module_dots,
                present_rows=DEFAULT_PRESENT_ROWS, speed=DEFAULT_SPEED,
                return_rows=0, media=DEFAULT_MEDIA):
    payload = make_print_job(kind, value, slot, font, ecc, module_dots,
                             speed, media).encode("ascii")
    print(f"print {kind} at slot {slot}")
    write(ep_out, b"SET TEAR OFF\r\n")
    move_rows(ep_out, ep_in, "BACKFEED", return_rows, speed, media)
    write(ep_out, payload)
    wait_cycle(ep_out, ep_in, timeout=10)
    move_rows(ep_out, ep_in, "FEED", present_rows, speed, media)
    print("done")


def print_image(ep_out, ep_in, payload, slot, present_rows=DEFAULT_PRESENT_ROWS,
                speed=DEFAULT_SPEED, return_rows=0, fit="contain",
                threshold=128, media=DEFAULT_MEDIA):
    max_rows = 9999 // media.row_pitch_dots
    if not 0 <= present_rows <= max_rows:
        raise ValueError("present_rows is too large for configured row pitch")
    if not 0 <= return_rows <= max_rows:
        raise ValueError("return_rows is too large for configured row pitch")
    job = make_image_job(payload, slot, speed, fit, threshold, media)
    print(f"print image at slot {slot}")
    move_rows(ep_out, ep_in, "BACKFEED", return_rows, speed, media)
    ep_out.write(job, timeout=3000)
    print(f"TX {len(job)} bytes (TSPL bitmap)")
    wait_cycle(ep_out, ep_in, timeout=10)
    move_rows(ep_out, ep_in, "FEED", present_rows, speed, media)
    print("done")


def row_count(value):
    rows = int(value)
    if rows < 0:
        raise argparse.ArgumentTypeError("row count must be 0 or greater")
    if rows > 9999:
        raise argparse.ArgumentTypeError("row count is too large")
    return rows


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Print on configured label media")
    sub = parser.add_subparsers(dest="cmd", required=True)
    feed = sub.add_parser("feed", help="advance exactly one configured media row")
    feed.add_argument("rows", nargs="?", type=int, choices=(1,), default=1)
    feed.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED,
                      help="paper motion speed in IPS")
    feed.add_argument("--json", action="store_true", help="emit a JSON result")
    feed.add_argument("--media-config", default=str(Path(__file__).with_name("media.default.json")))
    back = sub.add_parser("back", help="reverse exactly one configured media row")
    back.add_argument("rows", nargs="?", type=int, choices=(1,), default=1)
    back.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED,
                      help="paper motion speed in IPS")
    back.add_argument("--json", action="store_true", help="emit a JSON result")
    back.add_argument("--media-config", default=str(Path(__file__).with_name("media.default.json")))
    print_cmd = sub.add_parser("print", help="print text, QR, or an image on one slot")
    print_cmd.add_argument(
        "target", nargs="?", help="text, qr, image, or legacy slash-separated text"
    )
    print_cmd.add_argument("--data", action="append",
                           help="text or QR payload; repeat for multiple QR cells in one row")
    print_cmd.add_argument("--slot", type=int, help="target slot, based on configured column count")
    print_cmd.add_argument("--font", "--size", dest="font", type=int,
                           choices=(1, 2, 3, 4, 5), default=DEFAULT_FONT)
    print_cmd.add_argument("--pos", type=int, default=1, help="legacy first column, 1-8")
    print_cmd.add_argument("--ecc", choices=("L", "M", "Q", "H"), default=QR_ECC)
    print_cmd.add_argument("--module-size", type=int, choices=range(1, 11),
                           default=QR_MODULE_DOTS, help="QR module width in dots")
    print_cmd.add_argument("--present-rows", type=row_count,
                           default=DEFAULT_PRESENT_ROWS,
                           help="extra feed after print for label removal, one configured row per unit")
    print_cmd.add_argument("--return-rows", type=row_count, default=0,
                           help="rows to backfeed before print (0-119); same-row estimate is previous present-rows + 1")
    print_cmd.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED,
                           help="print and paper motion speed in IPS")
    print_cmd.add_argument("--media-config", default=str(Path(__file__).with_name("media.default.json")),
                           help="JSON media profile (default: media.default.json)")
    print_cmd.add_argument("--file", help="image file for print image")
    print_cmd.add_argument("--base64-stdin", action="store_true",
                           help="read Base64-encoded image bytes from stdin")
    print_cmd.add_argument("--fit", choices=("contain", "stretch"), default="contain")
    print_cmd.add_argument("--threshold", type=int, default=128,
                           help="grayscale threshold, 0-255")
    print_cmd.add_argument("--json", nargs="?", const="-", metavar="-",
                           help="read one JSON print job from stdin (use '-')")
    print_cmd.add_argument("--next", action="store_true", help=argparse.SUPPRESS)
    status_cmd = sub.add_parser("status", help="query printer readiness")
    status_cmd.add_argument("--json", action="store_true", help="emit JSON")
    caps = sub.add_parser("capabilities", help="print supported device capabilities as JSON")
    caps.add_argument("--media-config", default=str(Path(__file__).with_name("media.default.json")))
    return parser.parse_args(argv)


def emit_json(value):
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def execute_print(args, media=DEFAULT_MEDIA):
    if args.json is not None:
        if args.json != "-":
            raise ValueError("only --json - (stdin) is supported")
        return json_job(sys.stdin.read(), media)
    if args.target in ("text", "qr"):
        if args.data is None:
            raise ValueError("--data is required for print text/qr")
        if args.slot is None:
            raise ValueError("--slot is required for print text/qr")
        if args.target == "text":
            if len(args.data) != 1:
                raise ValueError("print text accepts exactly one --data value")
            value = args.data[0]
        else:
            value = args.data
        if args.next:
            raise ValueError("--next is obsolete; use --return-rows to select a physical row")
        return (args.target, value, args.slot, args.font, args.ecc,
                args.module_size, args.present_rows, args.return_rows, args.speed)
    if args.target == "image":
        if args.slot is None:
            raise ValueError("--slot is required for print image")
        if bool(args.file) == bool(args.base64_stdin):
            raise ValueError("print image requires exactly one of --file or --base64-stdin")
        if args.data is not None:
            raise ValueError("--data is not used by print image")
        try:
            raw = Path(args.file).read_bytes() if args.file else sys.stdin.buffer.read(30_000_001)
        except OSError as exc:
            raise ValueError(f"cannot read image input: {exc}") from exc
        if len(raw) > 30_000_000:
            raise ValueError("image input exceeds 30 MB")
        if args.base64_stdin:
            try:
                raw = base64.b64decode(b"".join(raw.split()), validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError("stdin must contain valid Base64 image data") from exc
        if not raw:
            raise ValueError("image input is empty")
        return ("image", raw, args.slot, args.font, args.ecc,
                args.module_size, args.present_rows, args.return_rows, args.speed)
    if args.target is None:
        raise ValueError("provide text, qr, image, or --json -")
    if args.data is not None or args.slot is not None:
        raise ValueError("--data and --slot require print text or print qr")
    if args.json is not None:
        raise ValueError("legacy print syntax cannot be combined with --json")
    if args.next:
        raise ValueError("--next is obsolete; use --return-rows to select a physical row")
    return ("legacy", args.target, args.pos, args.font, args.ecc,
            args.module_size, args.present_rows, args.return_rows, args.speed)


def main():
    args = parse_args(sys.argv[1:])
    if args.cmd == "capabilities":
        try:
            emit_json(capabilities_for(load_media_profile(args.media_config)))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        media = load_media_profile(args.media_config) if args.cmd in ("print", "feed", "back") else DEFAULT_MEDIA
    except (AttributeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "print":
        try:
            job = execute_print(args, media)
            (kind, value, slot, font, ecc, module_dots, present_rows,
             return_rows, speed) = job
            if (present_rows * media.row_pitch_dots > 9999
                    or return_rows * media.row_pitch_dots > 9999):
                raise ValueError("row movement exceeds the TSPL distance limit for this media profile")
            if kind == "image":
                make_image_job(value, slot, speed, args.fit, args.threshold, media)
            elif kind != "legacy":
                make_print_job(kind, value, slot, font, ecc, module_dots, speed, media)
        except ValueError as exc:
            if args.json is not None:
                emit_json({"ok": False, "error": str(exc), "code": "invalid_job"})
                return 2
            print(f"error: {exc}", file=sys.stderr)
            return 2
    machine_output = args.cmd in ("status", "feed", "back") and args.json
    if args.cmd == "print" and args.json is not None:
        machine_output = True
    try:
        dev, intf, ep_out, ep_in = open_printer()
    except SystemExit as exc:
        message = str(exc)
        if machine_output:
            emit_json({"ok": False, "error": message, "code": "device_error"})
            return 3
        print(message, file=sys.stderr)
        return 3
    try:
        if args.cmd == "status":
            code, name = status(ep_out, ep_in)
            result = {"ok": code == 0x00, "device": "TE344", "ready": code == 0x00,
                      "status_code": code, "status": name}
            if args.json:
                emit_json(result)
            else:
                print(f"status 0x{code:02x}: {name}" if code is not None
                      else f"status: {name}")
            return 0 if code == 0x00 else 3
        if args.cmd == "print":
            (kind, value, slot, font, ecc, module_dots, present_rows,
             return_rows, speed) = job
            with contextlib.redirect_stdout(sys.stderr if machine_output else sys.stdout):
                prepare(ep_out, ep_in)
                if kind == "legacy":
                    print_fields(ep_out, ep_in, value, font, slot, args.next,
                                 present_rows, speed, return_rows, media)
                elif kind == "image":
                    print_image(ep_out, ep_in, value, slot, present_rows, speed,
                                return_rows, args.fit, args.threshold, media)
                else:
                    print_typed(ep_out, ep_in, kind, value, slot, font, ecc,
                                module_dots, present_rows, speed, return_rows, media)
            if machine_output:
                emit_json({"ok": True, "device": "TE344", "type": kind,
                           "slot": slot, "data_length": len(value),
                           "present_rows": present_rows, "return_rows": return_rows,
                           "speed_ips": speed, "command_sent": True,
                           "printer_cycle_observed": True,
                           "physical_output_verified": False})
            return 0
        with contextlib.redirect_stdout(sys.stderr if machine_output else sys.stdout):
            prepare(ep_out, ep_in)
            motion = move_rows(ep_out, ep_in,
                               "FEED" if args.cmd == "feed" else "BACKFEED",
                               args.rows, args.speed, media)
        if machine_output:
            emit_json({"ok": True, "device": "TE344", "command": args.cmd.upper(),
                       "rows": args.rows, "speed_ips": args.speed,
                       "command_sent": motion["command_sent"],
                       "status_code": motion["status_code"],
                       "movement_confirmed_by_status": motion.get(
                           "movement_confirmed", False)})
        return 0
    except SystemExit as exc:
        message = str(exc)
        if machine_output:
            emit_json({"ok": False, "error": message, "code": "printer_error"})
            return 4
        print(message, file=sys.stderr)
        return 4
    finally:
        usb.util.release_interface(dev, intf.bInterfaceNumber)
        usb.util.dispose_resources(dev)


if __name__ == "__main__":
    raise SystemExit(main())
