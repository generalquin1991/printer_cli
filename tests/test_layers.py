from PIL import Image
import unittest

from printer.application.content import RasterContent, decode_image, generate_qr
from printer.application.jobs import Placement, PrintRequest
from printer.media.layout import render_row
from printer.media.profile import MediaProfile


class ContentAndLayoutTests(unittest.TestCase):
    def test_qr_is_generated_as_application_raster(self):
        qr = generate_qr("Q1", ecc="M", module_dots=1)
        self.assertEqual(qr.kind, "qr")
        self.assertEqual(qr.image.mode, "1")
        self.assertEqual(qr.image.size, (29, 29))
        colors = {color: count for count, color in qr.image.getcolors()}
        self.assertGreater(colors[0], 0)
        self.assertGreater(colors[255], 0)

    def test_print_request_is_an_immutable_layer_contract(self):
        item = RasterContent(Image.new("1", (2, 2), 0), "image")
        request = PrintRequest((Placement(2, item),), present_rows=0)
        self.assertEqual(request.placements[0].slot, 2)
        with self.assertRaises(AttributeError):
            request.present_rows = 1

    def test_image_decoder_applies_alpha_background_and_threshold(self):
        from io import BytesIO

        encoded = BytesIO()
        Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(encoded, format="PNG")
        image = decode_image(encoded.getvalue()).image
        self.assertEqual(image.mode, "1")
        self.assertTrue(all(image.getpixel((x, y)) for x in range(2) for y in range(2)))

    def test_layout_places_items_in_configured_slots(self):
        media = MediaProfile("test", 100, 4, 3, 2, 2, 1, 1)
        pixels = Image.new("1", (2, 2), 0)
        row = render_row({2: RasterContent(pixels, "image")}, media)
        self.assertEqual(row.size, (32, 8))
        self.assertEqual(row.getpixel((15, 3)), 0)
        self.assertEqual(row.getpixel((0, 0)), 1)

    def test_layout_rejects_slot_outside_media(self):
        media = MediaProfile("test", 100, 4, 3, 2, 2, 1, 1)
        item = RasterContent(Image.new("1", (1, 1), 0), "image")
        with self.assertRaisesRegex(ValueError, "slot must"):
            render_row({4: item}, media)


if __name__ == "__main__":
    unittest.main()
