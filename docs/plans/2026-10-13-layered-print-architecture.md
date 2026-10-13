# Layered Print Architecture

## Objective

Separate content generation, media layout, printer-language encoding, and device execution while preserving the existing CLI workflows.

## Execution Plan

1. [x] Extract media profile and immutable content/job data types from the CLI script.
2. [x] Move image decoding and QR matrix generation into an application-content module with no USB or TSPL dependency.
3. [x] Add a layout module that places content into configured label slots and produces a printer-independent monochrome row raster.
4. [x] Move TSPL setup and bitmap serialization into a printer backend that consumes the row raster.
5. [x] Move USB discovery, transfer, and status handling behind an execution interface that accepts opaque job bytes.
6. [x] Keep the CLI as an adapter; add `render` and `send` commands while retaining `print text/qr/image` convenience commands.
7. [x] Add isolated unit tests, update both READMEs, and run hardware-free verification. Hardware acceptance remains manual.
8. [x] Organize implementation modules into a `printer/` package by layer, move tests under `tests/`, preserve root CLI entry points, and verify every documented path/command against the resulting tree.

## Constraints

- Preserve the current 8-slot, 5 x 5 mm label geometry and 2 mm horizontal/vertical gaps by default.
- Encode feed/back motion from the media profile; default multi-row requests execute one row at a time, with opt-in continuous FEED only.
- Content generation must not import USB or printer-language modules.
- Device execution must not interpret whether a job contains text, QR, or an image.
- Do not stage or remove the pre-existing untracked manual PDF or bytecode cache.

## Checkpoints

- Batch 1: steps 1-3, verify content/layout tests and default CLI compatibility.
- Batch 2: steps 4-7, verified rendered job bytes, mocked execution, CLI behavior, and documentation; 15 unit tests pass.
- Physical image/QR print and scan validation is not automated and must be reported separately.

## Follow-up: Package Layout

- Move application content, job models, and pipeline into `printer/application/`.
- Move media profiles and row layout into `printer/media/`.
- Move TSPL serialization into `printer/backend/` and USB execution into `printer/execution/`.
- Place CLI orchestration in `printer/cli.py`; keep `print_positions.py` and `printer_cli.py` as root compatibility entry points.
- Move unit tests into `tests/` and update imports, test commands, and architecture documentation.
- Verified with `.venv/bin/python -m unittest discover -s tests -q`, bytecode compilation, `capabilities`, and offline `render`.
