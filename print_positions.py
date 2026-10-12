#!/usr/bin/env python3
"""TE344 row motion and one-row printing.

One row is a 5 mm label plus the 2 mm gap, 7 mm, 84 dots at 12 dots/mm (300 dpi).
"""

import argparse
import contextlib
import json
import sys
import time

import usb.core
import usb.util

VID = 0x1203
PID = 0x0273
DOTS_PER_MM = 12
LABEL_MM = 5
GAP_MM = 2
COLUMNS = 8
CELL = LABEL_MM * DOTS_PER_MM  # 60
PITCH = (LABEL_MM + GAP_MM) * DOTS_PER_MM  # 84
ROW_DOTS = PITCH
# Built-in fonts, width x height in dots. Font 5 is the largest that fits in 5 mm.
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


def column_fields(text, pos):
    fields = text.split("/")
    if pos < 1 or pos > COLUMNS:
        sys.exit(f"position must be 1..{COLUMNS}")
    if pos - 1 + len(fields) > COLUMNS:
        sys.exit(
            f"{len(fields)} fields starting at column {pos} pass the {COLUMNS} columns"
        )
    return fields


def field_commands(fields, pos, font):
    font_w, font_h = FONTS[font]
    y = (CELL - font_h) // 2
    lines = []
    for offset, field in enumerate(fields):
        if field == "":
            continue
        column = pos - 1 + offset
        text_w = font_w * len(field)
        x = column * PITCH + max(0, (CELL - text_w) // 2)
        lines.append(f'TEXT {x},{y},"{font}",0,1,1,"{tspl_text(field)}"\r\n')
    return "".join(lines)


def validate_payload(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ValueError(f"{label} must contain printable ASCII characters only")
    return value


def text_command(value, slot, font):
    validate_payload(value, "text")
    if not 1 <= slot <= COLUMNS:
        raise ValueError(f"slot must be 1..{COLUMNS}")
    if font not in FONTS:
        raise ValueError("font must be 1..5")
    font_w, font_h = FONTS[font]
    if len(value) * font_w > CELL or font_h > CELL:
        raise ValueError("text does not fit inside the 5 x 5 mm label at this font")
    return field_commands([value], slot, font)


def qr_command(value, slot, ecc=QR_ECC, module_dots=QR_MODULE_DOTS):
    validate_payload(value, "QR data")
    if '"' in value:
        raise ValueError('QR data cannot contain a double quote')
    if not 1 <= slot <= COLUMNS:
        raise ValueError(f"slot must be 1..{COLUMNS}")
    if ecc not in ("L", "M", "Q", "H"):
        raise ValueError("QR ECC must be one of L, M, Q, H")
    if not 1 <= module_dots <= 10:
        raise ValueError("QR module size must be 1..10 dots")
    x = (slot - 1) * PITCH
    # The printer chooses the QR version from the payload. Keep the origin inset
    # so the symbol's quiet area is less likely to touch the label boundary.
    return f'QRCODE {x + 4},4,{ecc},{module_dots},A,0,M2,S7,"{value}"\r\n'


def make_print_job(kind, value, slot, font=DEFAULT_FONT, ecc=QR_ECC,
                   module_dots=QR_MODULE_DOTS, speed=DEFAULT_SPEED):
    validate_speed(speed)
    if kind == "text":
        content = text_command(value, slot, font)
    elif kind == "qr":
        content = qr_command(value, slot, ecc, module_dots)
    else:
        raise ValueError("type must be text or qr")
    width_mm = COLUMNS * LABEL_MM + (COLUMNS - 1) * GAP_MM
    setup = (
        f"SIZE {width_mm} mm, {LABEL_MM} mm\r\n"
        f"GAP {GAP_MM} mm, 0 mm\r\n"
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


def capabilities():
    return {
        "device": "TE344",
        "resolution_dpi": 300,
        "dots_per_mm": DOTS_PER_MM,
        "label_mm": {"width": LABEL_MM, "height": LABEL_MM},
        "gap_mm": GAP_MM,
        "slot_count": COLUMNS,
        "slot_pitch_mm": LABEL_MM + GAP_MM,
        "presentation": {"default_rows": DEFAULT_PRESENT_ROWS,
                         "same_row_return_rows": DEFAULT_PRESENT_ROWS + 1,
                         "mechanism": "explicit_feed_backfeed",
                         "reposition_verified": False},
        "speed_ips": {"default": DEFAULT_SPEED, "supported": list(SPEEDS)},
        "types": ["text", "qr"],
        "text_fonts": {str(key): {"width_dots": value[0], "height_dots": value[1]}
                       for key, value in FONTS.items()},
        "qr": {"ecc": ["L", "M", "Q", "H"], "module_dots": [1, 10]},
    }


def json_job(raw):
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
        raise ValueError("JSON job slot must be an integer from 1 to 8")
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
    if not 0 <= present_rows <= 9999 // ROW_DOTS:
        raise ValueError("JSON job present_rows must be an integer from 0 to 119")
    if isinstance(return_rows, bool) or not isinstance(return_rows, int):
        raise ValueError("JSON job return_rows must be an integer from 0 to 119")
    if not 0 <= return_rows <= 9999 // ROW_DOTS:
        raise ValueError("JSON job return_rows must be an integer from 0 to 119")
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


def move_rows(ep_out, ep_in, command, rows, speed=DEFAULT_SPEED):
    if rows == 0:
        print(f"{command}: no movement (0 rows)")
        return
    speed = validate_speed(speed)
    dots = rows * ROW_DOTS
    # CLS drops the previous image so a feed does not reprint it.
    job = f"CLS\r\nSPEED {speed:g}\r\n{command} {dots}\r\n"
    print(f"{command} {rows} row(s), {dots} dots ({rows * (LABEL_MM + GAP_MM)} mm)")
    write(ep_out, job.encode())
    time.sleep(0.4 * rows + 0.5)
    code, name = status(ep_out, ep_in)
    if code == 0x20:
        time.sleep(0.4 * rows + 0.4)
        code, name = status(ep_out, ep_in)
    print(f"status 0x{code:02x}: {name}" if code is not None else f"status: {name}")
    if code not in (0x00, None) and name not in ("normal", "printing"):
        sys.exit(f"{command} failed: {name}")
    if code == 0x20:
        sys.exit(f"{command} still moving")
    print("done")


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
            return
        elif code not in (None, 0x00, 0x20):
            sys.exit(f"printer error: {name}")
        time.sleep(0.05)
    sys.exit("printer did not run")


def print_fields(ep_out, ep_in, text, font, pos, new_row,
                 present_rows=DEFAULT_PRESENT_ROWS,
                 speed=DEFAULT_SPEED, return_rows=0):
    fields = column_fields(text, pos)
    width_mm = COLUMNS * LABEL_MM + (COLUMNS - 1) * GAP_MM
    where = "next row" if new_row else "current row"
    print(f"print {text!r} size {font} at column {pos} on {where}")

    if not 0 <= present_rows <= 9999 // ROW_DOTS:
        raise ValueError("present_rows must be an integer from 0 to 119")
    if not 0 <= return_rows <= 9999 // ROW_DOTS:
        raise ValueError("return_rows must be an integer from 0 to 119")

    # Configure gap media without recalibrating or homing on every print.
    # GAPDETECT actively feeds stock to calibrate; GAP only sets gap size.
    setup = (
        f"SIZE {width_mm} mm, {LABEL_MM} mm\r\n"
        f"GAP {GAP_MM} mm, 0 mm\r\n"
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

    move_rows(ep_out, ep_in, "BACKFEED", return_rows, speed)

    if new_row:
        # TSPL FEED takes dots: 7 mm * 12 dots/mm = one 84-dot row.
        write(ep_out, f"FEED {ROW_DOTS}\r\n".encode())
        wait_cycle(ep_out, ep_in, timeout=20)

    job = (
        "CLS\r\n"
        + field_commands(fields, pos, font)
        + "PRINT 1,1\r\n"
    )
    write(ep_out, job.encode())
    wait_cycle(ep_out, ep_in, timeout=10)
    move_rows(ep_out, ep_in, "FEED", present_rows, speed)
    print("done")


def print_typed(ep_out, ep_in, kind, value, slot, font, ecc, module_dots,
                present_rows=DEFAULT_PRESENT_ROWS, speed=DEFAULT_SPEED,
                return_rows=0):
    payload = make_print_job(kind, value, slot, font, ecc, module_dots,
                             speed).encode("ascii")
    print(f"print {kind} at slot {slot}")
    write(ep_out, b"SET TEAR OFF\r\n")
    move_rows(ep_out, ep_in, "BACKFEED", return_rows, speed)
    write(ep_out, payload)
    wait_cycle(ep_out, ep_in, timeout=10)
    move_rows(ep_out, ep_in, "FEED", present_rows, speed)
    print("done")


def row_count(value):
    rows = int(value)
    if rows < 0:
        raise argparse.ArgumentTypeError("row count must be 0 or greater")
    if rows * ROW_DOTS > 9999:
        raise argparse.ArgumentTypeError("row count is too large")
    return rows


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Print on the TE344 8-slot label row")
    sub = parser.add_subparsers(dest="cmd", required=True)
    feed = sub.add_parser("feed", help="advance rows, 7 mm each (0 is a no-op)")
    feed.add_argument("rows", nargs="?", type=row_count, default=1)
    feed.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED,
                      help="paper motion speed in IPS")
    back = sub.add_parser("back", help="reverse rows, 7 mm each (0 is a no-op)")
    back.add_argument("rows", nargs="?", type=row_count, default=1)
    back.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED,
                      help="paper motion speed in IPS")
    print_cmd = sub.add_parser("print", help="print text or a QR code on one slot")
    print_cmd.add_argument(
        "target", nargs="?", help="text, qr, or legacy slash-separated text"
    )
    print_cmd.add_argument("--data", help="text or QR payload for text/qr mode")
    print_cmd.add_argument("--slot", type=int, help="target slot, 1-8")
    print_cmd.add_argument("--font", "--size", dest="font", type=int,
                           choices=(1, 2, 3, 4, 5), default=DEFAULT_FONT)
    print_cmd.add_argument("--pos", type=int, default=1, help="legacy first column, 1-8")
    print_cmd.add_argument("--ecc", choices=("L", "M", "Q", "H"), default=QR_ECC)
    print_cmd.add_argument("--module-size", type=int, choices=range(1, 11),
                           default=QR_MODULE_DOTS, help="QR module width in dots")
    print_cmd.add_argument("--present-rows", type=row_count,
                           default=DEFAULT_PRESENT_ROWS,
                           help="extra feed after print for label removal, 7 mm per row")
    print_cmd.add_argument("--return-rows", type=row_count, default=0,
                           help="rows to backfeed before print (0-119); same-row estimate is previous present-rows + 1")
    print_cmd.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED,
                           help="print and paper motion speed in IPS")
    print_cmd.add_argument("--json", nargs="?", const="-", metavar="-",
                           help="read one JSON print job from stdin (use '-')")
    print_cmd.add_argument("--next", action="store_true", help=argparse.SUPPRESS)
    status_cmd = sub.add_parser("status", help="query printer readiness")
    status_cmd.add_argument("--json", action="store_true", help="emit JSON")
    sub.add_parser("capabilities", help="print supported device capabilities as JSON")
    return parser.parse_args(argv)


def emit_json(value):
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def execute_print(args):
    if args.json is not None:
        if args.json != "-":
            raise ValueError("only --json - (stdin) is supported")
        return json_job(sys.stdin.read())
    if args.target in ("text", "qr"):
        if args.data is None:
            raise ValueError("--data is required for print text/qr")
        if args.slot is None:
            raise ValueError("--slot is required for print text/qr")
        if args.next:
            raise ValueError("--next is obsolete; use --return-rows to select a physical row")
        return (args.target, args.data, args.slot, args.font, args.ecc,
                args.module_size, args.present_rows, args.return_rows, args.speed)
    if args.target is None:
        raise ValueError("provide text, qr, or --json -")
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
        emit_json(capabilities())
        return 0
    if args.cmd == "print":
        try:
            job = execute_print(args)
            kind, value, slot, font, ecc, module_dots, _, _, speed = job
            if kind != "legacy":
                make_print_job(kind, value, slot, font, ecc, module_dots, speed)
        except ValueError as exc:
            if args.json is not None:
                emit_json({"ok": False, "error": str(exc), "code": "invalid_job"})
                return 2
            print(f"error: {exc}", file=sys.stderr)
            return 2
    machine_output = args.cmd == "status" and args.json
    if args.cmd == "print" and args.json is not None:
        machine_output = True
    if args.cmd in ("feed", "back") and args.rows == 0:
        print("no movement (0 rows)")
        return 0
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
                                 present_rows, speed, return_rows)
                else:
                    print_typed(ep_out, ep_in, kind, value, slot, font, ecc,
                                module_dots, present_rows, speed, return_rows)
            if machine_output:
                emit_json({"ok": True, "device": "TE344", "type": kind,
                           "slot": slot, "data_length": len(value),
                           "present_rows": present_rows, "return_rows": return_rows,
                           "speed_ips": speed})
            return 0
        prepare(ep_out, ep_in)
        move_rows(ep_out, ep_in, "FEED" if args.cmd == "feed" else "BACKFEED",
                  args.rows, args.speed)
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
