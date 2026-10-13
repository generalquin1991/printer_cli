# TE344 Printer CLI

English (default) | [简体中文](README.zh-CN.md)

This project separates application content, media layout, TSPL encoding, and USB execution. It talks directly to the printer over USB; it does not use the operating-system print queue.

## Requirements and media

Use the existing `.venv/bin/python` environment. Dependencies are listed in `requirements.txt` (Pillow, `qrcode`, and PyUSB). The default `media.default.json` describes eight 5 x 5 mm cells, with 2 mm horizontal and vertical gaps, at 300 DPI (12 dots/mm). Row pitch is 7 mm / 84 dots. Supply `--media-config PATH` to `print`, `render`, `feed`, `back`, or `capabilities` to use a different profile.

## Commands

```sh
# Render a QR row to a TSPL file and a visual preview; does not connect to USB
.venv/bin/python print_positions.py render qr --data Q1 --data Q2 --slot 1 \
  --output /tmp/qr.tspl --preview /tmp/qr.png

# Send a previously rendered job (opaque bytes) to the printer
.venv/bin/python print_positions.py send /tmp/qr.tspl

# Convenience: create and immediately execute one row containing eight QR codes
.venv/bin/python print_positions.py print qr \
  --data Q1 --data Q2 --data Q3 --data Q4 \
  --data Q5 --data Q6 --data Q7 --data Q8 \
  --slot 1 --module-size 1 --ecc M --present-rows 0

# Render an image supplied as a file, or as Base64 on stdin
.venv/bin/python print_positions.py render image --file ./logo.png --slot 1 \
  --output /tmp/logo.tspl --preview /tmp/logo.png
base64 < ./logo.png | .venv/bin/python print_positions.py render image \
  --base64-stdin --slot 1 --output /tmp/logo.tspl

# Text, status, device/media capabilities, and motion (one row per transfer)
.venv/bin/python print_positions.py print text --data A123 --slot 1 --present-rows 0
.venv/bin/python print_positions.py status --json
.venv/bin/python print_positions.py capabilities
.venv/bin/python print_positions.py feed 5
.venv/bin/python print_positions.py feed 5 --continuous
.venv/bin/python print_positions.py back 3
```

### Content and layout

- `print text`, `print qr`, and `print image` create one-row jobs and immediately execute them. `render` creates the binary TSPL print payload without USB access; `--preview` additionally writes a PNG of the rasterized row. `render` does not include presentation/return motion; `send` only transmits the saved bytes. The binary artifact is portable only to a compatible TSPL printer and matching media configuration.
- QR codes and text are generated in the application as monochrome raster content. Images are decoded and thresholded in the application. The backend receives only the final row raster and serializes it as a TSPL `BITMAP`; jobs do not use firmware text or `QRCODE` commands.
- Repeat `--data` for adjacent QR values starting at `--slot`. `--module-size` is 1–10 dots; `--ecc` accepts `L/M/Q/H`. Use short payloads for the 5 mm cells and verify printed codes with the intended scanner.
- Image input requires exactly one of `--file PATH` or `--base64-stdin`. `--fit contain` preserves aspect ratio and centers on the cell; `stretch` fills it. `--threshold` is 0–255. Image source files are limited to 30 MB and 20 megapixels.
- `--slot` is 1-based. `--present-rows` controls optional motion after printing and defaults to 5; use `0` to suppress it. `--return-rows` controls reverse motion before printing and defaults to 0. Same-row return alignment is not physically validated; do not assume a return count restores a prior label position.
- Text is printable ASCII and is rasterized with Pillow. `--font` (or legacy `--size`) selects application font size 1–5; it is not a printer font number. Legacy `print TEXT --size N --pos N` and slash-separated columns remain available for older manual workflows.
- `feed [N]` and `back [N]` accept 1–100 rows (default 1). By default they send and wait for each row separately. `feed N --continuous` sends one `FEED` command for `N` row pitches; it is not available for `back`. A completed status does not prove physical movement. These are relative movements, not undo or alignment operations.
- `--speed` supports `1, 1.5, 2, 2.5, 3, 3.5, 4, 5` IPS. `capabilities` validates and reports media geometry without connecting to the printer.

### JSON requests

An application can provide one request on stdin:

```sh
printf '%s' '{"type":"qr","data":["Q1","Q2"],"slot":1,"present_rows":0}' \
  | .venv/bin/python print_positions.py print --json -
```

The JSON `type` is `text`, `qr`, `image`, or `row`; image jobs use `image_base64`. A `row` job uses a `placements` array, with each entry specifying `type` (`text` or `qr`), `slot`, and `data`, so mixed content prints in one row and one print operation. Optional global fields include `font`, `ecc`, `module_size`, `fit`, `threshold`, `speed`, `present_rows`, and `return_rows`. Unknown fields are rejected. `print --json -` emits machine-readable output; `physical_output_verified:false` means software cannot confirm visible print quality. `send --json` is also available.

### Layer boundaries

- `printer/application/content.py`, `printer/application/jobs.py`, `printer/application/pipeline.py`: content, request models, and application pipeline.
- `printer/media/profile.py`, `printer/media/layout.py`: validated geometry and printer-independent row rasterization.
- `printer/backend/tspl.py`: serializes row rasters and motion as TSPL.
- `printer/execution/usb.py`: USB and readiness handling over opaque byte payloads.
- `printer/cli.py`: CLI orchestration. `print_positions.py` remains the compatibility entry point.

The implementation is grouped under `printer/`: application logic is in `printer/application/`, media geometry/layout in `printer/media/`, TSPL serialization in `printer/backend/`, USB execution in `printer/execution/`, and CLI orchestration in `printer/cli.py`. The root `printer_cli.py` and `print_positions.py` files are compatibility entry points. Tests are under `tests/`:

```sh
.venv/bin/python -m unittest discover -s tests -q
```

The `probe_te344.py` script is a separate diagnostic that performs a real test print and changes printer settings; it is not a read-only probe.

## Hardware notes

The TE344 has previously printed a full row of QR codes using its native QR command. The current application-raster pipeline has not been hardware-validated yet, and scan reliability, vertical centering, and the interaction between print and presentation movement still need physical checks. A prior long backfeed test reported paper-jam status; inspect the media path and ribbon before retrying after any uncertain movement. Never blindly resend after a USB write error because the printer may already have acted on the job.
