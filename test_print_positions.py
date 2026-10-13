import base64
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import printer_cli
from content import RasterContent
from jobs import Placement, PrintRequest
from media import DEFAULT_MEDIA, MediaProfile
from pipeline import render_request
from printer_executor import PrinterExecutor
from tspl_backend import TSPLBackend


class PipelineTests(unittest.TestCase):
    def test_qr_and_text_are_rendered_as_one_row_bitmap_job(self):
        args = printer_cli.parse_args([
            "print", "qr", "--data", "Q1", "--data", "Q2", "--slot", "1",
            "--present-rows", "0",
        ])
        request = printer_cli.request_from_args(args, DEFAULT_MEDIA)
        rendered = render_request(request, DEFAULT_MEDIA)
        self.assertEqual(len(request.placements), 2)
        self.assertIn(b"BITMAP 0,0,81,60,0,", rendered.payload)
        self.assertEqual(rendered.payload.count(b"PRINT 1,1"), 1)
        self.assertNotIn(b"QRCODE", rendered.payload)

        text_args = printer_cli.parse_args([
            "print", "text", "--data", "A", "--slot", "1", "--present-rows", "0"
        ])
        text_job = render_request(
            printer_cli.request_from_args(text_args, DEFAULT_MEDIA), DEFAULT_MEDIA
        )
        self.assertIn(b"BITMAP 0,0,81,60,0,", text_job.payload)
        self.assertNotIn(b"TEXT ", text_job.payload)

    def test_custom_profile_controls_rendered_geometry(self):
        media = MediaProfile("custom", 300, 12, 2, 5, 5, 1, 3)
        request = PrintRequest((Placement(2, RasterContent(
            Image.new("1", (8, 8), 0), "image")),), present_rows=0)
        rendered = render_request(request, media)
        self.assertTrue(rendered.payload.startswith(b"SIZE 11 mm, 5 mm\r\n"))
        self.assertIn(b"GAP 3 mm, 0 mm\r\n", rendered.payload)
        self.assertIn(b"BITMAP 0,0,17,60,0,", rendered.payload)

    def test_invalid_duplicate_slots_fail_before_encoding(self):
        image = RasterContent(Image.new("1", (4, 4), 0), "image")
        request = PrintRequest((Placement(1, image), Placement(1, image)))
        with self.assertRaisesRegex(ValueError, "duplicate slots"):
            render_request(request, DEFAULT_MEDIA)


class CliTests(unittest.TestCase):
    def test_base64_image_request_is_decoded_without_usb(self):
        source = BytesIO()
        Image.new("RGB", (4, 4), "black").save(source, format="PNG")
        encoded = base64.b64encode(source.getvalue())
        class FakeStdin:
            buffer = BytesIO(encoded)
        args = printer_cli.parse_args([
            "print", "image", "--base64-stdin", "--slot", "1", "--present-rows", "0"
        ])
        with patch("printer_cli.sys.stdin", FakeStdin()):
            request = printer_cli.request_from_args(args, DEFAULT_MEDIA)
        self.assertEqual(request.placements[0].content.kind, "image")

    def test_json_image_job_accepts_base64(self):
        source = BytesIO()
        Image.new("RGB", (3, 3), "white").save(source, format="PNG")
        data = base64.b64encode(source.getvalue()).decode("ascii")
        request = printer_cli.request_from_json(
            '{"type":"image","image_base64":"' + data + '","slot":1}',
            DEFAULT_MEDIA,
        )
        self.assertEqual(request.placements[0].content.kind, "image")

    def test_json_input_rejects_boolean_numeric_options(self):
        with self.assertRaisesRegex(ValueError, "unsupported print speed"):
            printer_cli.request_from_json(
                '{"type":"text","data":"A","slot":1,"speed":true}',
                DEFAULT_MEDIA,
            )
        with self.assertRaisesRegex(ValueError, "threshold"):
            printer_cli.request_from_json(
                '{"type":"image","image_base64":"","slot":1,"threshold":true}',
                DEFAULT_MEDIA,
            )

    def test_render_writes_job_and_preview_without_connecting_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            job_path = Path(directory) / "test.tspl"
            preview_path = Path(directory) / "preview.png"
            argv = ["print_positions.py", "render", "qr", "--data", "Q1",
                    "--slot", "1", "--output", str(job_path),
                    "--preview", str(preview_path)]
            output = StringIO()
            with (patch("sys.argv", argv),
                  patch.object(printer_cli.PrinterExecutor, "connect",
                               side_effect=AssertionError("render must not use USB")),
                  redirect_stdout(output)):
                self.assertEqual(printer_cli.main(), 0)
            self.assertIn(b"BITMAP", job_path.read_bytes())
            with Image.open(preview_path) as preview:
                self.assertEqual(preview.size,
                                 ((DEFAULT_MEDIA.columns - 1) * DEFAULT_MEDIA.column_pitch_dots
                                  + DEFAULT_MEDIA.cell_width_dots,
                                  DEFAULT_MEDIA.cell_height_dots))

    def test_send_passes_job_bytes_unchanged_to_executor(self):
        class FakeExecutor:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def prepare(self):
                pass
            def execute(self, payload, **kwargs):
                calls.append((payload, kwargs))
                return {"cycle_observed": True, "status": "normal"}

        calls = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "opaque.tspl"
            payload = b"arbitrary\x00opaque bytes\r\n"
            path.write_bytes(payload)
            with patch.object(printer_cli.PrinterExecutor, "connect", return_value=FakeExecutor()):
                self.assertEqual(printer_cli.main(["send", str(path)]), 0)
        self.assertEqual(calls, [(payload, {"observe_cycle": True})])

    def test_feed_and_back_remain_one_row(self):
        with self.assertRaises(SystemExit):
            printer_cli.parse_args(["feed", "2"])
        backend = TSPLBackend(DEFAULT_MEDIA)
        self.assertIn(b"FEED 84\r\n", backend.encode_motion("FEED", 1))
        self.assertIn(b"BACKFEED 84\r\n", backend.encode_motion("BACKFEED", 1))


class ExecutionTests(unittest.TestCase):
    def test_executor_sends_opaque_bytes_and_observes_busy_ready(self):
        class FakeTransport:
            def __init__(self):
                self.writes = []
            def write(self, payload):
                self.writes.append(payload)
            def close(self):
                pass

        transport = FakeTransport()
        executor = PrinterExecutor(transport)
        with (patch.object(executor, "status", side_effect=[(0x20, "printing"),
                                                              (0x00, "normal")]),
              patch("printer_executor.time.sleep")):
            result = executor.execute(b"\x00opaque job\xff", timeout=1)
        self.assertEqual(transport.writes, [b"\x00opaque job\xff"])
        self.assertTrue(result["cycle_observed"])

    def test_motion_commands_are_bounded_and_profile_derived(self):
        media = MediaProfile("wide pitch", 300, 12, 8, 5, 5, 2, 3)
        backend = TSPLBackend(media)
        self.assertIn(b"FEED 96\r\n", backend.encode_motion("FEED", 1))
        with self.assertRaisesRegex(ValueError, "distance limit"):
            backend.encode_motion("FEED", 105)


if __name__ == "__main__":
    unittest.main()
