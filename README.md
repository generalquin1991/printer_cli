# TE344 Printer CLI

English (default) | [简体中文](README.zh-CN.md)

This guide documents the command-line interfaces in `print_positions.py` and `probe_te344.py`. It is not a complete TSPL programming reference.

## Requirements and media configuration

Run commands from the project directory with the existing `.venv/bin/python` environment. The scripts require PyUSB and a working USB backend. They communicate directly with the printer over USB, not through the operating system print queue.

The current media configuration is fixed:

- Printer: TE344, USB VID:PID `1203:0273`.
- Resolution: 300 DPI, using 12 dots/mm.
- Eight cells per row; each label is 5 × 5 mm, with 2 mm column and row gaps.
- Full-row print area: 54 mm wide × 5 mm high.
- Row pitch: 7 mm (84 dots), including the gap.

## Commands for GUI and CLI integrations

Use the single-cell interface for new integrations. The `/` separator is only used by the legacy text interface.

```sh
.venv/bin/python print_positions.py print text --data "A123" --slot 3 --font 2 --present-rows 5 --return-rows 0 --speed 1.5
.venv/bin/python print_positions.py print text --data "B456" --slot 4 --font 2 --present-rows 5 --return-rows 6 --speed 1.5
.venv/bin/python print_positions.py print qr --data "https://example.com/123" --slot 3
.venv/bin/python print_positions.py status --json
.venv/bin/python print_positions.py capabilities
.venv/bin/python print_positions.py feed 1
.venv/bin/python print_positions.py back 1 --speed 1.5
```

- `--slot` selects a cell in the current row, from 1 to 8. It does not automatically find the next unused cell.
- `--present-rows N` controls extra forward motion after printing, in 7 mm rows, so a person can remove the label. Default: `5`; use `0` for no extra motion. Set it on each job or use JSON `present_rows`.
- `--return-rows N` controls reverse motion before printing, from `0` to `119`. Default: `0`. The current, unverified positioning estimate uses `0` for the first print and `previous present_rows + 1` to target another cell in that row. For example, after a job with `present_rows=5`, it estimates `return_rows=6`. This assumes one row of print motion plus the previous presentation motion; validate on the actual printer before relying on it.
- `--speed` sets print and paper-motion speed in IPS. Supported values are `1, 1.5, 2, 2.5, 3, 3.5, 4, 5`; default: `1.5`. Standalone `feed` and `back` commands also accept it.
- `--font` selects built-in font 1 to 5; default: `2`. Single-cell text that exceeds the 5 × 5 mm label boundary is rejected.
- QR codes use the printer's native TSPL `QRCODE` command. `--ecc` accepts `L/M/Q/H`, and `--module-size` sets module width in dots from 1 to 10. Defaults are `M` and `1`. The 5 × 5 mm label severely limits QR capacity and readability; test the actual payload, printer, and scanner.
- Single-cell text and QR payloads accept printable ASCII only, including JSON input. In this interface `/` is a literal character.
- `status --json` queries status without moving the media. `capabilities` always emits JSON and does not connect to the printer.
- `feed 0` and `back 0` succeed without connecting to the device or sending commands.

A GUI can send one JSON job on standard input:

```sh
printf '%s' '{"type":"text","data":"A123","slot":3,"font":2,"present_rows":5,"return_rows":0,"speed":1.5}' \
  | .venv/bin/python print_positions.py print --json -
```

Example QR job:

```json
{"type":"qr","data":"https://example.com/123","slot":3,"ecc":"M","module_size":1,"present_rows":5,"return_rows":6,"speed":1.5}
```

On success, JSON print commands emit `{"ok":true,...}` to stdout and diagnostics to stderr. Invalid input exits with code 2; device and print errors use 3 and 4. An error after a USB write may mean that the printer already executed part of the job, so do not retry blindly.

The legacy `print TEXT --size N --pos N` interface remains available for existing manual workflows. `--next` is disabled. Use `print text/qr` or JSON input for new integrations.

Print and paper-motion commands check the printer model and readiness first. `status` only queries status; `capabilities` and help commands do not connect to the device. Print and motion stop if the printer is not ready.

**Hardware test record (2026-10-12):** A previous long forward/reverse test reported `0x02: paper jam`. Manually tightening the ribbon and pressing Feed/Pause restored readiness. A test with `SET TEAR ON` and a one-row return did not reach the intended row. The current explicit `return_rows`/`present_rows` positioning model still needs a hardware test printing different cells in the same row. Do not use this positioning model in production until that test passes.

## 1. Help

```sh
.venv/bin/python print_positions.py --help
.venv/bin/python print_positions.py print --help
.venv/bin/python print_positions.py feed --help
.venv/bin/python print_positions.py back --help
```

`-h` is equivalent to `--help`. These commands do not connect to the printer or move media.

## 2. Legacy text printing

```sh
.venv/bin/python print_positions.py print TEXT [--size {1,2,3,4,5}] [--pos COLUMN]
```

- `TEXT` is required. Separate adjacent columns with `/`; quote the argument.
- `--size` selects built-in font 1 to 5; default: `2`. It is not a dimension in millimeters or a font scale factor.
- `--pos` selects the starting column, from 1 to 8; default: `1`.
- `--next` is disabled. Supplying it fails before connecting. Use the standalone `feed` command for explicit forward motion.

Print in column 1 from the current paper position:

```sh
.venv/bin/python print_positions.py print "zz" --size 1 --pos 1
```

Advance one row explicitly, then print in column 7:

```sh
.venv/bin/python print_positions.py feed 1
.venv/bin/python print_positions.py print "zz" --size 1 --pos 7
```

Print eight values starting at column 1 in one job:

```sh
.venv/bin/python print_positions.py print "1/2/3/4/5/6/7/8" --size 1
```

Print in columns 3, 4, and 5:

```sh
.venv/bin/python print_positions.py print "A/B/C" --size 2 --pos 3
```

Empty fields reserve a column without printing. This prints in columns 1 and 3, leaving column 2 blank:

```sh
.venv/bin/python print_positions.py print "A//C" --size 2 --pos 1
```

Leading and trailing empty fields also occupy columns. The starting column plus the number of fields must fit within the eight columns.

### Fonts and text limits

Character dimensions in dots:

| Font | Width × height |
| --- | --- |
| 1 | 8 × 12 |
| 2 | 12 × 20 |
| 3 | 16 × 24 |
| 4 | 24 × 32 |
| 5 | 32 × 48 |

Each cell is 60 × 60 dots. Text is centered using these dimensions. The legacy interface does not shrink, wrap, or clip oversized text, so it may cross label boundaries. Font 5 fits only one character of its listed width in a cell. The single-cell interface rejects oversized text.

Chinese font and encoding support is not configured. Use simple ASCII letters and numbers. In the legacy interface, `/` is always a column separator, and there is no option to print a literal slash. Do not pass newlines or raw printer commands as text.

### Print motion and paper position

- Each job sends `SET TEAR OFF` to disable firmware tear-off positioning that could change the between-job paper position.
- `present_rows` controls extra forward motion after `PRINT` and defaults to 5 rows. To print another cell in the same horizontal row, the current model uses `--return-rows 0` for the first job and estimates the next job's return as the preceding `present_rows + 1` (6 after the default presentation of 5).
- `--return-rows` describes paper position explicitly; the CLI does not persist state across processes. Pressing FEED, pulling or tearing media, or replacing the roll invalidates the assumed position. Establish a new baseline and start with `--return-rows 0`.
- `--next` is disabled. Use `--return-rows` for explicit reverse positioning.
- The CLI does not track physical row numbers. The caller must choose the return amount based on the previous job. Separate jobs can target different cells, but the two-row compensation model needs hardware validation before production use.
- Normal printing does not send `GAPDETECT` or `HOME` and does not automatically recover a lost paper-position baseline.

## 3. Forward motion: `feed`

```sh
.venv/bin/python print_positions.py feed [ROWS] [--speed IPS]
```

`ROWS` is an optional integer from 0 to 119, defaulting to 1. Each row advances 84 dots (7 mm) without printing. `0` is a no-op and does not connect to the printer. `--speed` adjusts motion speed.

```sh
.venv/bin/python print_positions.py feed
.venv/bin/python print_positions.py feed 2
```

The script sends `FEED ROWS×84`. TSPL's `FEED` argument is in dots, not label count; `ROWS` is the unit defined by this CLI.

## 4. Reverse motion: `back`

```sh
.venv/bin/python print_positions.py back [ROWS] [--speed IPS]
```

`ROWS` is an optional integer from 0 to 119, defaulting to 1. `0` is a no-op and does not connect to the printer. `--speed` adjusts motion speed.

```sh
.venv/bin/python print_positions.py back
```

The script sends `BACKFEED ROWS×84`. This is relative motion, not an undo operation: it does not erase printed content or guarantee alignment with an earlier row. Print jobs use `--return-rows` to request this motion explicitly. The accepted maximum of 119 rows is a protocol limit, not a safe reverse distance; keep return moves small and use a low speed when appropriate.

**The numeric maximum is not a safe reverse distance.** Long reverse movements can loosen or wrinkle media or ribbon and cause jams. Use small steps only when reverse motion is needed and the paper path is healthy. Do not repeatedly reverse to recover from a fault.

Both `feed` and `back` send `CLS` and the selected `SPEED`, but do not reset `SIZE` or `GAP` or calibrate media. The manual's `SET BACK` controls label return after cutting; it is not ribbon synchronization and is not sent.

## 5. USB probe and box test: `probe_te344.py`

```sh
.venv/bin/python probe_te344.py
```

This script does not parse command-line arguments. It opens the USB device and displays its serial number, interfaces, and endpoints; queries status, printer information, and model; sets `SIZE 5 mm, 5 mm` and `GAP 2 mm, 0 mm`; draws `BOX 4,4,28,28,2` and executes `PRINT 1`; then reads status again.

**This script prints and changes the media-size setting.** It uses a 5 mm-wide test area instead of the main script's 54 mm full-row area, and does not use the main script's model and readiness checks. Do not run `probe_te344.py --help`: it ignores arguments and still runs the print test.

## 6. Logs and errors

- `model reply: b'TE344'`: expected printer model received.
- `TX ... bytes`: command bytes written, followed by the command text. This does not confirm a successful physical print.
- `status 0x00: normal`: printer reports normal status.
- `status 0x20: printing`: printer is busy; this may also appear during paper motion and does not confirm text was printed.
- `done`: the script completed its flow; it does not confirm alignment or print quality.

Recognized status codes:

| Code | Meaning |
| --- | --- |
| `0x01` | Print head open |
| `0x02` | Paper jam / paper-motion detection fault |
| `0x03` | Paper jam and print head open |
| `0x04` | Out of paper |
| `0x05` | Out of paper and print head open |
| `0x08` | Out of ribbon |
| `0x09` | Out of ribbon and print head open |
| `0x0A` | Out of ribbon and paper jam |
| `0x0B` | Out of ribbon, paper jam, and print head open |
| `0x0C` | Out of ribbon and out of paper |
| `0x0D` | Out of ribbon, out of paper, and print head open |
| `0x10` | Pause |
| `0x80` | Other error |

Common errors:

- `TE344 not found`: the specified USB device was not found.
- `Printer did not answer model TE344`: unexpected model response; printing stops.
- `printer not ready: ...`: pre-job readiness check failed; no subsequent print or motion command was sent.
- `printer error: ...`: an error was detected while waiting; part of the job may have run.
- `printer did not run`: no complete busy-to-ready cycle was confirmed. The printer may still have moved; do not blindly retry.
- `no reply`: no status response was received. The current post-motion check for `feed/back` may still print `done` without a response; this does not confirm successful movement.

On `paper jam`, inspect the media, ribbon, and sensor position. Gap detection or positioning faults can also produce this status; the code alone does not prove a mechanical jam. The CLI has no fault-clear or reset command.

## 7. Commands not yet available

The CLI provides `print`, `status`, `capabilities`, `feed`, and `back`. It does not provide calibration, reset, job cancellation, copy count, Chinese printing, automatic unused-slot selection, or configurable media dimensions.

Fixed print settings include `OFFSET 0 mm`, `LIMITFEED 20 mm`, tear-off/peel/cutter disabled, `DIRECTION 1`, `REFERENCE 0,0`, `SHIFT 0`, and `DENSITY 12`. `SPEED` is set by `--speed` or JSON `speed`, defaulting to 1.5 IPS.

`GAPDETECT` actively feeds media to calibrate it, and `HOME` seeks a label position. Neither is a motion-free setup command; both are excluded from normal printing.

Protocol reference: the locally downloaded [TSC TSPL/TSPL2 Programming Manual 3.0](TSPL_TSPL2_Programming_Manual_3.0.pdf), especially the `PRINT`, `BACKFEED & BACKUP`, `SET BACK`, `SET TEAR`, and `SET RIBBON` sections. It defines `PRINT` label sets/copies and `BACKFEED` reverse distances in dots, and warns that inappropriate reverse distances can jam or wrinkle media. `SET RIBBON` does not adjust ribbon tension; `SET BACK` is not ribbon synchronization; and `SET TEAR ON` does not promise to return to the same horizontal row. The two-row compensation model for `PRINT + presentation FEED + same-row BACKFEED` still needs validation on this printer.
