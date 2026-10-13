"""Serialize printer-independent row rasters and motion into TSPL bytes."""

from layout import aligned_bitmap_bytes
from media import MediaProfile


class TSPLBackend:
    def __init__(self, media, density=12):
        if not isinstance(media, MediaProfile):
            raise TypeError("media must be MediaProfile")
        if not 1 <= density <= 15:
            raise ValueError("density must be 1..15")
        self.media = media
        self.density = density

    def encode_print(self, row, speed=1.5):
        if isinstance(speed, bool) or speed not in (1, 1.5, 2, 2.5, 3, 3.5, 4, 5):
            raise ValueError("unsupported print speed")
        expected = (self.media.columns - 1) * self.media.column_pitch_dots + self.media.cell_width_dots
        if row.size != (expected, self.media.cell_height_dots):
            raise ValueError("row raster dimensions do not match the configured media")
        byte_width, bitmap = aligned_bitmap_bytes(row)
        header = (
            f"SIZE {self.media.media_width_mm:g} mm, {self.media.label_height_mm:g} mm\r\n"
            f"GAP {self.media.row_gap_mm:g} mm, 0 mm\r\n"
            "OFFSET 0 mm\r\nLIMITFEED 20 mm\r\n"
            "SET TEAR OFF\r\nSET PEEL OFF\r\nSET CUTTER OFF\r\n"
            "DIRECTION 1\r\nREFERENCE 0,0\r\nSHIFT 0\r\n"
            f"SPEED {speed:g}\r\nDENSITY {self.density}\r\nCLS\r\n"
            f"BITMAP 0,0,{byte_width},{row.height},0,"
        ).encode("ascii")
        return header + bitmap + b"\r\nPRINT 1,1\r\n"

    def encode_motion(self, command, rows, speed=1.5):
        if command not in ("FEED", "BACKFEED"):
            raise ValueError("motion command must be FEED or BACKFEED")
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
            raise ValueError("rows must be a non-negative integer")
        dots = rows * self.media.row_pitch_dots
        if dots > 9999:
            raise ValueError("row movement exceeds the TSPL distance limit")
        if isinstance(speed, bool) or speed not in (1, 1.5, 2, 2.5, 3, 3.5, 4, 5):
            raise ValueError("unsupported paper-motion speed")
        return f"CLS\r\nSPEED {speed:g}\r\n{command} {dots}\r\n".encode("ascii")
