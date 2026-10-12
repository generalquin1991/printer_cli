#!/usr/bin/env python3
"""TE344 row motion and one-row printing.

One row is a 5 mm label plus the 2 mm gap, 7 mm, 84 dots at 12 dots/mm (300 dpi).
"""

import argparse
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


def move_rows(ep_out, ep_in, command, rows):
    dots = rows * ROW_DOTS
    # CLS drops the previous image so a feed does not reprint it.
    job = f"CLS\r\nSET BACK ON\r\nSPEED 2\r\n{command} {dots}\r\n"
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


def print_fields(ep_out, ep_in, text, font, pos, new_row):
    fields = column_fields(text, pos)
    width_mm = COLUMNS * LABEL_MM + (COLUMNS - 1) * GAP_MM
    where = "next row" if new_row else "current row"
    print(f"print {text!r} size {font} at column {pos} on {where}")

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
        "SPEED 2\r\n"
        "DENSITY 12\r\n"
    )
    # HOME can trigger a long gap-seeking move on this short-label stock.
    # Keep setup non-moving; the explicit FEED below handles --next.
    write(ep_out, setup.encode())

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
    print("done")


def row_count(value):
    rows = int(value)
    if rows < 1:
        raise argparse.ArgumentTypeError("row count must be at least 1")
    if rows * ROW_DOTS > 9999:
        raise argparse.ArgumentTypeError("row count is too large")
    return rows


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Print on the TE344 8-column row")
    sub = parser.add_subparsers(dest="cmd", required=True)
    feed = sub.add_parser("feed", help="advance rows, 7 mm each")
    feed.add_argument("rows", nargs="?", type=row_count, default=1)
    back = sub.add_parser("back", help="reverse rows, 7 mm each")
    back.add_argument("rows", nargs="?", type=row_count, default=1)
    print_cmd = sub.add_parser("print", help="print slash-separated text into columns")
    print_cmd.add_argument("text", help="values separated by /, e.g. 1/2/3/4/5/6/7/8")
    print_cmd.add_argument("--size", type=int, required=True, choices=(1, 2, 3, 4, 5))
    print_cmd.add_argument("--pos", type=int, default=1, help="first column, 1-8")
    print_cmd.add_argument("--next", action="store_true", help="print on the next row")
    return parser.parse_args(argv)


def main():
    args = parse_args(sys.argv[1:])
    dev, intf, ep_out, ep_in = open_printer()
    try:
        prepare(ep_out, ep_in)
        if args.cmd == "feed":
            move_rows(ep_out, ep_in, "FEED", args.rows)
        elif args.cmd == "back":
            move_rows(ep_out, ep_in, "BACKFEED", args.rows)
        else:
            print_fields(ep_out, ep_in, args.text, args.size, args.pos, args.next)
    finally:
        usb.util.release_interface(dev, intf.bInterfaceNumber)
        usb.util.dispose_resources(dev)


if __name__ == "__main__":
    main()
