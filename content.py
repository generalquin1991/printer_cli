"""Application-level content generation; no printer protocol or USB code."""

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont, ImageOps


@dataclass(frozen=True)
class RasterContent:
    image: Image.Image
    kind: str


@dataclass(frozen=True)
class TextContent:
    text: str
    font: int = 2


def decode_image(image_bytes, threshold=128, max_bytes=30_000_000,
                 max_pixels=20_000_000):
    if len(image_bytes) > max_bytes:
        raise ValueError(f"image input exceeds {max_bytes // 1_000_000} MB")
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be 0..255")
    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise ValueError(f"cannot decode image: {exc}") from exc
    if image.width * image.height > max_pixels:
        raise ValueError(f"source image exceeds {max_pixels // 1_000_000} megapixels")
    try:
        image.load()
    except Exception as exc:
        raise ValueError(f"cannot decode image: {exc}") from exc
    image = ImageOps.exif_transpose(image).convert("RGBA")
    background = Image.new("RGBA", image.size, "white")
    image = Image.alpha_composite(background, image).convert("L")
    image = image.point(lambda value: 0 if value < threshold else 255).convert("1")
    return RasterContent(image, "image")


def generate_qr(data, ecc="M", module_dots=1):
    if not isinstance(data, str) or not data:
        raise ValueError("QR data must be a non-empty string")
    if ecc not in ("L", "M", "Q", "H"):
        raise ValueError("QR ECC must be one of L, M, Q, H")
    if isinstance(module_dots, bool) or not isinstance(module_dots, int) or not 1 <= module_dots <= 10:
        raise ValueError("QR module size must be 1..10 dots")
    import qrcode
    from qrcode.constants import (ERROR_CORRECT_H, ERROR_CORRECT_L,
                                  ERROR_CORRECT_M, ERROR_CORRECT_Q)

    levels = {"L": ERROR_CORRECT_L, "M": ERROR_CORRECT_M,
              "Q": ERROR_CORRECT_Q, "H": ERROR_CORRECT_H}
    qr = qrcode.QRCode(version=None, error_correction=levels[ecc],
                       box_size=module_dots, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("1")
    return RasterContent(image, "qr")


def generate_text(text, font=2):
    if not isinstance(text, str) or not text:
        raise ValueError("text must be a non-empty string")
    if any(ord(char) < 32 or ord(char) > 126 for char in text):
        raise ValueError("text must contain printable ASCII characters only")
    font_sizes = {1: 12, 2: 20, 3: 24, 4: 32, 5: 48}
    if isinstance(font, bool) or font not in font_sizes:
        raise ValueError("font must be 1..5")
    face = ImageFont.load_default(size=font_sizes[font])
    bounds = face.getbbox(text)
    image = Image.new("L", (bounds[2] - bounds[0], bounds[3] - bounds[1]), "white")
    ImageDraw.Draw(image).text((-bounds[0], -bounds[1]), text, font=face, fill="black")
    return RasterContent(image.point(lambda value: 0 if value < 128 else 255).convert("1"), "text")
