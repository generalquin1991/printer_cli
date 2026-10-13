"""CLI orchestration; content, layout, TSPL, and USB live in separate modules."""

import argparse
import base64
import binascii
import json
import sys
from pathlib import Path

from content import RasterContent, TextContent, decode_image, generate_qr, generate_text
from jobs import Placement, PrintRequest
from media import DEFAULT_MEDIA, load_media_profile
from pipeline import render_request
from printer_executor import PrinterError, PrinterExecutor
from tspl_backend import TSPLBackend


DEFAULT_FONT = 2
DEFAULT_SPEED = 1.5
DEFAULT_PRESENT_ROWS = 5
SPEEDS = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0)


def nonnegative_rows(value):
    try:
        rows = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("row count must be an integer") from exc
    if rows < 0:
        raise argparse.ArgumentTypeError("row count must be 0 or greater")
    return rows


def motion_rows(value):
    try:
        rows = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("row count must be an integer") from exc
    if not 1 <= rows <= 100:
        raise argparse.ArgumentTypeError("motion row count must be from 1 to 100")
    return rows


def add_content_args(parser, *, required_target):
    parser.add_argument("target", nargs=None if required_target else "?",
                        choices=("text", "qr", "image") if required_target else None,
                        help="content type; print also accepts legacy slash-separated text")
    parser.add_argument("--data", action="append", help="content; repeat for adjacent QR slots")
    parser.add_argument("--slot", type=int, help="first target slot")
    parser.add_argument("--font", "--size", type=int, default=DEFAULT_FONT)
    parser.add_argument("--pos", type=int, default=1, help="legacy first column")
    parser.add_argument("--ecc", choices=("L", "M", "Q", "H"), default="M")
    parser.add_argument("--module-size", type=int, default=1)
    parser.add_argument("--file", help="image file for image content")
    parser.add_argument("--base64-stdin", action="store_true",
                        help="read a Base64-encoded image from stdin")
    parser.add_argument("--fit", choices=("contain", "stretch"), default="contain")
    parser.add_argument("--threshold", type=int, default=128)
    parser.add_argument("--media-config", default=str(Path(__file__).with_name("media.default.json")))
    parser.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED)
    return parser


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="print_positions.py",
                                     description="Render and execute TE344 print jobs")
    sub = parser.add_subparsers(dest="command", required=True)

    print_parser = sub.add_parser("print", help="render and immediately print content")
    add_content_args(print_parser, required_target=False)
    print_parser.add_argument("--present-rows", type=nonnegative_rows,
                              default=DEFAULT_PRESENT_ROWS)
    print_parser.add_argument("--return-rows", type=nonnegative_rows, default=0)
    print_parser.add_argument("--json", nargs="?", const="-", metavar="-",
                              help="read a JSON print request from stdin")
    print_parser.add_argument("--next", action="store_true", help=argparse.SUPPRESS)

    render_parser = sub.add_parser("render", help="render a TSPL job without connecting to USB")
    add_content_args(render_parser, required_target=True)
    render_parser.add_argument("--output", required=True, help="path for the binary TSPL job")
    render_parser.add_argument("--preview", help="optional PNG preview path")

    send_parser = sub.add_parser("send", help="send an existing opaque TSPL job file")
    send_parser.add_argument("job_file")
    send_parser.add_argument("--json", action="store_true")

    for name in ("feed", "back"):
        motion = sub.add_parser(name, help=f"move one row at a time, up to 100 rows")
        motion.add_argument("rows", nargs="?", type=motion_rows, default=1)
        if name == "feed":
            motion.add_argument("--continuous", action="store_true",
                                help="send one FEED command for all requested rows")
        motion.add_argument("--speed", type=float, choices=SPEEDS, default=DEFAULT_SPEED)
        motion.add_argument("--media-config",
                            default=str(Path(__file__).with_name("media.default.json")))
        motion.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="query printer readiness")
    status.add_argument("--json", action="store_true")

    capabilities = sub.add_parser("capabilities", help="print device and media capabilities")
    capabilities.add_argument("--media-config",
                              default=str(Path(__file__).with_name("media.default.json")))
    return parser.parse_args(argv)


def load_input_image(args):
    if bool(args.file) == bool(args.base64_stdin):
        raise ValueError("image requires exactly one of --file or --base64-stdin")
    if args.file:
        try:
            with Path(args.file).open("rb") as image_file:
                data = image_file.read(30_000_001)
        except OSError as exc:
            raise ValueError(f"cannot read image input: {exc}") from exc
        if len(data) > 30_000_000:
            raise ValueError("image input exceeds 30 MB")
    else:
        data = sys.stdin.buffer.read(40_000_001)
        if len(data) > 40_000_000:
            raise ValueError("Base64 image input exceeds 40 MB")
        try:
            data = base64.b64decode(b"".join(data.split()), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("stdin must contain valid Base64 image data") from exc
    return decode_image(data, args.threshold)


def validate_ascii(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        raise ValueError(f"{label} must contain printable ASCII characters only")
    return value


def request_from_json(raw, media):
    try:
        job = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(job, dict):
        raise ValueError("JSON print request must be an object")
    allowed = {"type", "data", "image_base64", "slot", "placements", "font", "ecc",
               "module_size", "present_rows", "return_rows", "speed", "fit", "threshold"}
    unknown = set(job) - allowed
    if unknown:
        raise ValueError(f"unknown JSON field(s): {', '.join(sorted(unknown))}")
    kind = job.get("type")
    slot = job.get("slot")
    if kind != "row" and (isinstance(slot, bool) or not isinstance(slot, int)
                          or not 1 <= slot <= media.columns):
        raise ValueError(f"slot must be an integer from 1 to {media.columns}")
    if kind == "row" and any(key in job for key in ("data", "image_base64", "slot")):
        raise ValueError("row jobs specify data and slots inside placements only")
    font = job.get("font", DEFAULT_FONT)
    ecc = job.get("ecc", "M")
    module_size = job.get("module_size", 1)
    speed = job.get("speed", DEFAULT_SPEED)
    present_rows = job.get("present_rows", DEFAULT_PRESENT_ROWS)
    return_rows = job.get("return_rows", 0)
    fit = job.get("fit", "contain")
    if isinstance(font, bool) or not isinstance(font, int):
        raise ValueError("font must be an integer")
    if isinstance(module_size, bool) or not isinstance(module_size, int):
        raise ValueError("module_size must be an integer")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or speed not in SPEEDS:
        raise ValueError("unsupported print speed")
    if kind == "qr" and (not 1 <= module_size <= 10 or ecc not in ("L", "M", "Q", "H")):
        raise ValueError("invalid QR module size or ECC")
    threshold = job.get("threshold", 128)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 255:
        raise ValueError("threshold must be an integer from 0 to 255")
    placements = []
    if kind == "text":
        value = validate_ascii(job.get("data"), "text")
        placements.append(Placement(slot, TextContent(value, font)))
    elif kind == "qr":
        data = job.get("data")
        values = data if isinstance(data, list) else [data]
        if not values:
            raise ValueError("QR data must contain at least one value")
        placements.extend(Placement(slot + index,
                                    generate_qr(validate_ascii(value, "QR data"), ecc,
                                                module_size))
                          for index, value in enumerate(values))
    elif kind == "image":
        image_data = job.get("image_base64")
        if not isinstance(image_data, str):
            raise ValueError("image jobs require image_base64")
        if len(image_data) > 40_000_000:
            raise ValueError("Base64 image input exceeds 30 MB decoded limit")
        try:
            decoded = base64.b64decode(image_data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("image_base64 must be valid Base64") from exc
        placements.append(Placement(slot, decode_image(decoded, job.get("threshold", 128))))
    elif kind == "row":
        items = job.get("placements")
        if not isinstance(items, list) or not 1 <= len(items) <= media.columns:
            raise ValueError(f"row placements must be a list of 1..{media.columns} items")
        seen_slots = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each row placement must be an object")
            item_allowed = {"type", "slot", "data", "font", "ecc", "module_size"}
            item_unknown = set(item) - item_allowed
            if item_unknown:
                raise ValueError(f"unknown placement field(s): {', '.join(sorted(item_unknown))}")
            item_type = item.get("type")
            item_slot = item.get("slot")
            if (isinstance(item_slot, bool) or not isinstance(item_slot, int)
                    or not 1 <= item_slot <= media.columns):
                raise ValueError(f"placement slot must be an integer from 1 to {media.columns}")
            if item_slot in seen_slots:
                raise ValueError(f"row contains duplicate slot {item_slot}")
            seen_slots.add(item_slot)
            if item_type == "text":
                item_font = item.get("font", font)
                if isinstance(item_font, bool) or not isinstance(item_font, int):
                    raise ValueError("placement font must be an integer")
                value = validate_ascii(item.get("data"), "text")
                generate_text(value, item_font)
                placements.append(Placement(item_slot, TextContent(value, item_font)))
            elif item_type == "qr":
                item_ecc = item.get("ecc", ecc)
                item_size = item.get("module_size", module_size)
                if (isinstance(item_size, bool) or not isinstance(item_size, int)
                        or not 1 <= item_size <= 10
                        or item_ecc not in ("L", "M", "Q", "H")):
                    raise ValueError("invalid placement QR module size or ECC")
                value = validate_ascii(item.get("data"), "QR data")
                placements.append(Placement(item_slot,
                                            generate_qr(value, item_ecc, item_size)))
            else:
                raise ValueError("row placement type must be text or qr")
    else:
        raise ValueError("type must be text, qr, image, or row")
    if kind == "text":
        generate_text(placements[0].content.text, font)
    if fit not in ("contain", "stretch"):
        raise ValueError("fit must be contain or stretch")
    for name, rows in (("present_rows", present_rows), ("return_rows", return_rows)):
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        if rows * media.row_pitch_dots > 9999:
            raise ValueError(f"{name} exceeds the TSPL movement limit")
    if kind != "row" and slot + len(placements) - 1 > media.columns:
        raise ValueError("content exceeds the remaining slots in the row")
    return PrintRequest(tuple(placements), present_rows, return_rows, speed, fit)


def request_from_args(args, media):
    if getattr(args, "json", None) == "-":
        return request_from_json(sys.stdin.read(), media)
    target = args.target
    if target == "image":
        if args.data is not None or args.slot is None:
            raise ValueError("print image requires --slot and does not accept --data")
        placements = [Placement(args.slot, load_input_image(args))]
    elif target == "qr":
        if not args.data or args.slot is None:
            raise ValueError("print qr requires --data and --slot")
        placements = [Placement(args.slot + index,
                                generate_qr(validate_ascii(value, "QR data"),
                                            args.ecc, args.module_size))
                      for index, value in enumerate(args.data)]
    elif target == "text":
        if not args.data or len(args.data) != 1 or args.slot is None:
            raise ValueError("print text requires one --data value and --slot")
        placements = [Placement(args.slot,
                                TextContent(validate_ascii(args.data[0], "text"), args.font))]
    elif target is None or target not in ("text", "qr", "image"):
        if getattr(args, "command", None) == "render":
            raise ValueError("render target must be text, qr, or image")
        if target is None:
            raise ValueError("print requires text, qr, image, or a legacy positional string")
        if args.data is not None or args.file or args.base64_stdin or args.slot is not None:
            raise ValueError("legacy text mode accepts positional text only")
        if args.next:
            raise ValueError("--next is obsolete; use --return-rows")
        fields = target.split("/")
        placements = [Placement(args.pos + index,
                                TextContent(validate_ascii(text, "text"), args.font))
                      for index, text in enumerate(fields) if text]
    else:
        raise ValueError("unsupported content type")
    if args.slot is not None and not 1 <= args.slot <= media.columns:
        raise ValueError(f"slot must be 1..{media.columns}")
    if not placements:
        raise ValueError("print request contains no content")
    if any(not 1 <= item.slot <= media.columns for item in placements):
        raise ValueError(f"content exceeds the configured {media.columns} slots")
    if target == "text":
        generate_text(placements[0].content.text, args.font)
    if target in ("qr",) and (not 1 <= args.module_size <= 10):
        raise ValueError("QR module size must be 1..10")
    if not 0 <= args.threshold <= 255:
        raise ValueError("threshold must be 0..255")
    for rows in (getattr(args, "present_rows", 0), getattr(args, "return_rows", 0)):
        if rows * media.row_pitch_dots > 9999:
            raise ValueError("row movement exceeds the TSPL distance limit")
    return PrintRequest(tuple(placements), getattr(args, "present_rows", 0),
                        getattr(args, "return_rows", 0), args.speed, args.fit)


def emit_json(value):
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))


def execute_motion(executor, backend, command, rows, speed, continuous=False):
    max_rows = 9999 // backend.media.row_pitch_dots
    if isinstance(rows, bool) or not isinstance(rows, int) or not 1 <= rows <= max_rows:
        raise ValueError(f"motion row count must be from 1 to {max_rows}")
    if continuous:
        payload = backend.encode_motion(command, rows, speed)
        print(f"{command} {rows} row(s), {rows * backend.media.row_pitch_dots} dots (single command)")
        try:
            result = executor.execute(payload, observe_cycle=False,
                                      settle_seconds=0.4 * rows + 0.5)
        except PrinterError as exc:
            raise MotionExecutionError(command, rows, 0, exc, continuous=True) from exc
        print(f"status 0x{result['status_code']:02x}: {result['status']}")
        return {"command_sent": True, "commands_sent": 1,
                "rows_submitted": rows, **result,
                "movement_confirmed_by_status": False}
    last_result = None
    completed = 0
    for index in range(rows):
        payload = backend.encode_motion(command, 1, speed)
        print(f"{command} row {index + 1}/{rows}, {backend.media.row_pitch_dots} dots")
        try:
            last_result = executor.execute(payload, observe_cycle=False, settle_seconds=0.9)
        except PrinterError as exc:
            raise MotionExecutionError(command, rows, completed, exc) from exc
        completed += 1
        print(f"status 0x{last_result['status_code']:02x}: {last_result['status']}")
    return {"command_sent": True, "commands_sent": completed,
            "rows_submitted": completed,
            **last_result, "movement_confirmed_by_status": False}


class MotionExecutionError(PrinterError):
    def __init__(self, command, rows_requested, rows_completed, cause, continuous=False):
        self.command = command
        self.rows_requested = rows_requested
        self.rows_completed = rows_completed
        self.continuous = continuous
        if continuous:
            message = (f"{command} {rows_requested} was sent as one command; "
                       f"physical movement is uncertain: {cause}")
        else:
            message = (f"{command} stopped after {rows_completed}/{rows_requested} "
                       f"status-confirmed rows; movement of the current row is uncertain: {cause}")
        super().__init__(message)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "capabilities":
        try:
            media = load_media_profile(args.media_config)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        emit_json({"device": "TE344", "media": media.name,
                   "resolution_dpi": media.resolution_dpi,
                   "dots_per_mm": media.dots_per_mm,
                   "label_mm": {"width": media.label_width_mm,
                                "height": media.label_height_mm},
                   "gap_mm": {"column": media.column_gap_mm, "row": media.row_gap_mm},
                   "slot_count": media.columns, "media_width_mm": media.media_width_mm,
                   "row_pitch_mm": media.label_height_mm + media.row_gap_mm,
                   "types": ["text", "qr", "image", "row"]})
        return 0

    if args.command == "send":
        try:
            with Path(args.job_file).open("rb") as job_file:
                payload = job_file.read(100_000_001)
            if not payload or len(payload) > 100_000_000:
                raise ValueError("job file must contain 1 to 100 MB")
            with PrinterExecutor.connect() as executor:
                executor.prepare()
                result = executor.execute(payload, observe_cycle=True)
            if args.json:
                emit_json({"ok": True, "command_sent": True, **result,
                           "physical_output_verified": False})
            else:
                print(f"sent {len(payload)} bytes; status {result['status']}")
            return 0
        except (OSError, ValueError, PrinterError) as exc:
            if args.json:
                emit_json({"ok": False, "error": str(exc)})
            else:
                print(f"error: {exc}", file=sys.stderr)
            return 3

    if args.command == "status":
        try:
            with PrinterExecutor.connect() as executor:
                code, name = executor.status()
            result = {"ok": code == 0, "device": "TE344", "ready": code == 0,
                      "status_code": code, "status": name}
            if args.json:
                emit_json(result)
            else:
                print(f"status: {name}")
            return 0 if code == 0 else 3
        except PrinterError as exc:
            if args.json:
                emit_json({"ok": False, "error": str(exc)})
            else:
                print(f"error: {exc}", file=sys.stderr)
            return 3

    try:
        media = load_media_profile(args.media_config)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command in ("print", "render"):
        try:
            request = request_from_args(args, media)
            rendered = render_request(request, media)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.command == "render":
            try:
                if args.preview and Path(args.output).resolve() == Path(args.preview).resolve():
                    raise ValueError("TSPL output and PNG preview paths must differ")
                Path(args.output).write_bytes(rendered.payload)
                if args.preview:
                    rendered.raster.save(args.preview, format="PNG")
            except (OSError, ValueError) as exc:
                print(f"error: cannot write rendered output: {exc}", file=sys.stderr)
                return 2
            print(f"rendered {len(rendered.payload)} bytes to {args.output}")
            return 0
        try:
            with PrinterExecutor.connect() as executor:
                executor.prepare()
                backend = TSPLBackend(media)
                if request.return_rows:
                    execute_motion(executor, backend, "BACKFEED",
                                   request.return_rows, request.speed)
                result = executor.execute(rendered.payload, observe_cycle=True)
                if request.present_rows:
                    execute_motion(executor, backend, "FEED",
                                   request.present_rows, request.speed)
            if args.json is not None:
                emit_json({"ok": True, "device": "TE344", "command_sent": True,
                           "printer_cycle_observed": result["cycle_observed"],
                           "physical_output_verified": False,
                           "present_rows": request.present_rows,
                           "return_rows": request.return_rows})
            else:
                print("print complete; physical output is not software-verified")
            return 0
        except PrinterError as exc:
            if args.json is not None:
                emit_json({"ok": False, "error": str(exc), "code": "printer_error"})
            else:
                print(f"printer error: {exc}", file=sys.stderr)
            return 4

    command = "FEED" if args.command == "feed" else "BACKFEED"
    try:
        backend = TSPLBackend(media)
        with PrinterExecutor.connect() as executor:
            executor.prepare()
            result = execute_motion(executor, backend, command, args.rows, args.speed,
                                    continuous=getattr(args, "continuous", False))
        if args.json:
            emit_json({"ok": True, "device": "TE344", "command": command,
                       "rows": args.rows, **result})
        return 0
    except (ValueError, PrinterError) as exc:
        if args.json:
            failure = {"ok": False, "error": str(exc), "code": "printer_error"}
            if isinstance(exc, MotionExecutionError):
                failure.update(command=exc.command, rows_requested=exc.rows_requested,
                               rows_completed=exc.rows_completed)
            emit_json(failure)
        else:
            print(f"printer error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
