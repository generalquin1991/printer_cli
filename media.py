"""Validated physical media geometry, independent of printer transport."""

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaProfile:
    name: str
    resolution_dpi: int
    dots_per_mm: float
    columns: int
    label_width_mm: float
    label_height_mm: float
    column_gap_mm: float
    row_gap_mm: float

    @property
    def cell_width_dots(self):
        return round(self.label_width_mm * self.dots_per_mm)

    @property
    def cell_height_dots(self):
        return round(self.label_height_mm * self.dots_per_mm)

    @property
    def column_pitch_dots(self):
        return round((self.label_width_mm + self.column_gap_mm) * self.dots_per_mm)

    @property
    def row_pitch_dots(self):
        return round((self.label_height_mm + self.row_gap_mm) * self.dots_per_mm)

    @property
    def media_width_mm(self):
        return self.columns * self.label_width_mm + (self.columns - 1) * self.column_gap_mm


def load_media_profile(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read media profile: {exc}") from exc
    expected = {"name", "resolution_dpi", "dots_per_mm", "columns",
                "label_width_mm", "label_height_mm", "column_gap_mm", "row_gap_mm"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("media profile must contain exactly: " + ", ".join(sorted(expected)))
    if not isinstance(raw["name"], str) or not raw["name"].strip():
        raise ValueError("media profile name must be a non-empty string")
    integer_fields = ("resolution_dpi", "columns")
    number_fields = ("dots_per_mm", "label_width_mm", "label_height_mm",
                     "column_gap_mm", "row_gap_mm")
    for key in integer_fields:
        if isinstance(raw[key], bool) or not isinstance(raw[key], int) or raw[key] <= 0:
            raise ValueError(f"media profile {key} must be a positive integer")
    for key in number_fields:
        if isinstance(raw[key], bool) or not isinstance(raw[key], (int, float)):
            raise ValueError(f"media profile {key} must be a positive number")
        if not math.isfinite(raw[key]):
            raise ValueError(f"media profile {key} must be finite")
        if raw[key] <= 0 and key in ("dots_per_mm", "label_width_mm", "label_height_mm"):
            raise ValueError(f"media profile {key} must be positive")
        if raw[key] < 0:
            raise ValueError(f"media profile {key} cannot be negative")
    if raw["columns"] > 99:
        raise ValueError("media profile columns must be at most 99")
    media = MediaProfile(**raw)
    if min(media.cell_width_dots, media.cell_height_dots, media.row_pitch_dots) < 1:
        raise ValueError("media profile dimensions must resolve to at least one dot")
    if round(media.media_width_mm * media.dots_per_mm) > 9999:
        raise ValueError("media profile width exceeds the TSPL coordinate limit")
    if media.row_pitch_dots > 9999:
        raise ValueError("media profile row pitch exceeds the TSPL distance limit")
    return media


DEFAULT_MEDIA = load_media_profile(Path(__file__).with_name("media.default.json"))
