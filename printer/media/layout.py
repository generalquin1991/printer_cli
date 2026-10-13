"""Place application content on configured media without printer commands."""

from PIL import Image

from ..application.content import RasterContent, TextContent, generate_text
from .profile import MediaProfile


def fit_to_cell(content, media, fit="contain"):
    if not isinstance(content, RasterContent):
        raise TypeError("content must be RasterContent")
    if fit not in ("contain", "stretch"):
        raise ValueError("fit must be contain or stretch")
    source = content.image.convert("1")
    cell_size = (media.cell_width_dots, media.cell_height_dots)
    if fit == "contain":
        source.thumbnail(cell_size, Image.Resampling.NEAREST)
        canvas = Image.new("1", cell_size, 1)
        canvas.paste(source, ((cell_size[0] - source.width) // 2,
                              (cell_size[1] - source.height) // 2))
        return canvas
    return source.resize(cell_size, Image.Resampling.NEAREST)


def render_row(items_by_slot, media, fit="contain"):
    if not isinstance(media, MediaProfile):
        raise TypeError("media must be MediaProfile")
    width = (media.columns - 1) * media.column_pitch_dots + media.cell_width_dots
    row = Image.new("1", (width, media.cell_height_dots), 1)
    for slot, content in items_by_slot.items():
        if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= media.columns:
            raise ValueError(f"slot must be 1..{media.columns}")
        if isinstance(content, TextContent):
            content = generate_text(content.text, content.font)
            if (content.image.width > media.cell_width_dots
                    or content.image.height > media.cell_height_dots):
                raise ValueError("text does not fit inside the configured label")
        if not isinstance(content, RasterContent):
            raise TypeError("slot content must be TextContent or RasterContent")
        if content.kind == "text":
            cell = Image.new("1", (media.cell_width_dots, media.cell_height_dots), 1)
            cell.paste(content.image, ((media.cell_width_dots - content.image.width) // 2,
                                       (media.cell_height_dots - content.image.height) // 2))
        else:
            cell = fit_to_cell(content, media, fit)
        x = (slot - 1) * media.column_pitch_dots
        row.paste(cell, (x, 0))
    return row


def aligned_bitmap_bytes(image):
    """Return row-major, MSB-first monochrome data padded to whole bytes."""
    byte_width = (image.width + 7) // 8
    padded = Image.new("1", (byte_width * 8, image.height), 1)
    padded.paste(image.convert("1"), (0, 0))
    return byte_width, padded.tobytes()
