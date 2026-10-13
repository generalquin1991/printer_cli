#!/usr/bin/env python3
"""Compatibility import and entry point for :mod:`printer.cli`."""

from printer.cli import *
from printer.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
