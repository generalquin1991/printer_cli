"""Printer-independent request types passed between application layers."""

from dataclasses import dataclass

from content import RasterContent, TextContent


@dataclass(frozen=True)
class Placement:
    slot: int
    content: RasterContent | TextContent


@dataclass(frozen=True)
class PrintRequest:
    placements: tuple[Placement, ...]
    present_rows: int = 5
    return_rows: int = 0
    speed: float = 1.5
    fit: str = "contain"
