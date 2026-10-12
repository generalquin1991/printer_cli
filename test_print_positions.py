import json
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

import print_positions as printer


class PrintJobTests(unittest.TestCase):
    def test_capabilities_describe_fixed_media(self):
        caps = printer.capabilities()
        self.assertEqual(caps["resolution_dpi"], 300)
        self.assertEqual(caps["label_mm"], {"width": 5, "height": 5})
        self.assertEqual(caps["slot_count"], 8)
        self.assertEqual(caps["slot_pitch_mm"], 7)
        self.assertEqual(caps["speed_ips"]["default"], 1.5)
        self.assertEqual(caps["presentation"]["default_rows"], 5)
        self.assertEqual(caps["presentation"]["same_row_return_rows"], 6)

    def test_text_command_targets_selected_slot(self):
        command = printer.make_print_job("text", "A/1", 3)
        self.assertIn('TEXT 180,20,"2",0,1,1,"A/1"', command)
        self.assertIn("SIZE 54 mm, 5 mm", command)
        self.assertIn("SPEED 1.5", command)
        self.assertIn("SET TEAR OFF", command)
        self.assertNotIn("BACKFEED", command)
        self.assertNotIn("\r\nFEED ", command)
        self.assertIn("PRINT 1,1", command)

    def test_print_speed_is_validated_and_applied(self):
        command = printer.make_print_job("text", "A", 1, speed=2.5)
        self.assertIn("SPEED 2.5", command)
        with self.assertRaisesRegex(ValueError, "speed must"):
            printer.make_print_job("text", "A", 1, speed=0.5)

    def test_text_rejects_out_of_bounds_and_invalid_slot(self):
        with self.assertRaisesRegex(ValueError, "does not fit"):
            printer.make_print_job("text", "123456", 1, font=2)
        with self.assertRaisesRegex(ValueError, "slot must"):
            printer.make_print_job("text", "A", 9)

    def test_qr_command_uses_native_tspl_and_slot_offset(self):
        command = printer.make_print_job("qr", "https://example.com/123", 2)
        self.assertIn(
            'QRCODE 114,30,M,1,A,0,X60,J5,M2,S7,"https://example.com/123"',
            command,
        )

    def test_json_job_defaults_and_validation(self):
        job = printer.json_job('{"type":"text","data":"OK","slot":8}')
        self.assertEqual(job, ("text", "OK", 8, 2, "M", 1, 5, 0, 1.5))
        with self.assertRaisesRegex(ValueError, "integer"):
            printer.json_job('{"type":"qr","data":"x","slot":true}')
        with self.assertRaisesRegex(ValueError, "unknown JSON job"):
            printer.json_job('{"type":"text","data":"A","slot":1,"fonts":2}')
        with self.assertRaisesRegex(ValueError, "printable ASCII"):
            printer.make_print_job("qr", "你好", 1)

    def test_json_output_is_compact_valid_json(self):
        payload = {"ok": True, "type": "text", "slot": 1}
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_cli_accepts_recommended_json_stdin_form(self):
        args = printer.parse_args(["print", "--json", "-"])
        self.assertEqual(args.cmd, "print")
        self.assertEqual(args.json, "-")
        with patch("sys.stdin", StringIO('{"type":"text","data":"A","slot":1}')):
            self.assertEqual(
                printer.execute_print(args),
                ("text", "A", 1, 2, "M", 1, 5, 0, 1.5),
            )

    def test_cli_presentation_rows_default_and_override(self):
        default_args = printer.parse_args(
            ["print", "text", "--data", "A", "--slot", "1"]
        )
        custom_args = printer.parse_args(
            ["print", "text", "--data", "A", "--slot", "1",
             "--present-rows", "3"]
        )
        self.assertEqual(default_args.present_rows, 5)
        self.assertEqual(custom_args.present_rows, 3)

    def test_zero_rows_is_a_noop(self):
        self.assertEqual(printer.row_count("0"), 0)
        with (
            patch.object(printer, "write") as write,
            patch.object(printer, "status") as status,
        ):
            printer.move_rows(None, None, "FEED", 0)
        write.assert_not_called()
        status.assert_not_called()

    def test_cli_feed_zero_does_not_open_device(self):
        output = StringIO()
        with (
            patch("sys.argv", ["print_positions.py", "feed", "0"]),
            patch.object(printer, "open_printer", side_effect=AssertionError("opened")),
            redirect_stdout(output),
        ):
            self.assertEqual(printer.main(), 0)
        self.assertIn("no movement", output.getvalue())

    def test_motion_command_uses_selected_speed(self):
        with (
            patch.object(printer, "write") as write,
            patch.object(printer, "status", return_value=(0x00, "normal")),
            patch.object(printer.time, "sleep"),
        ):
            printer.move_rows(None, None, "FEED", 2, 2.5)
        self.assertIn(b"SPEED 2.5", write.call_args.args[1])

    def test_json_speed_and_explicit_same_row_return(self):
        job = printer.json_job(
            '{"type":"text","data":"A","slot":1,"present_rows":1,"return_rows":2,"speed":1}'
        )
        self.assertEqual(job[-3:], (1, 2, 1.0))
        with self.assertRaisesRegex(ValueError, "speed must"):
            printer.json_job(
                '{"type":"text","data":"A","slot":1,"speed":0.5}'
            )
        with self.assertRaisesRegex(ValueError, "present_rows must"):
            printer.json_job(
                '{"type":"text","data":"A","slot":1,"present_rows":120}'
            )
        larger_return = printer.json_job(
            '{"type":"text","data":"A","slot":1,"return_rows":6}'
        )
        self.assertEqual(larger_return[-2], 6)
        with self.assertRaisesRegex(ValueError, "return_rows must"):
            printer.json_job(
                '{"type":"text","data":"A","slot":1,"return_rows":120}'
            )

    def test_print_job_disables_firmware_tear_mode(self):
        command = printer.make_print_job("text", "A", 1)
        self.assertIn("SET TEAR OFF", command)
        self.assertNotIn("SET TEAR ON", command)

    def test_legacy_next_is_rejected_before_connecting(self):
        args = printer.parse_args(["print", "A", "--size", "2", "--next"])
        with self.assertRaisesRegex(ValueError, "--next is obsolete"):
            printer.execute_print(args)

    def test_print_explicitly_returns_same_row_and_presents_afterward(self):
        calls = []
        with (
            patch.object(printer, "write", side_effect=lambda *_: calls.append(("write",))),
            patch.object(printer, "move_rows", side_effect=lambda *_a: calls.append(("move", _a[2], _a[3]))),
            patch.object(printer, "make_print_job", wraps=printer.make_print_job) as make_job,
            patch.object(printer, "wait_cycle", side_effect=lambda *_a, **_k:
                  calls.append(("wait",))),
        ):
            printer.print_typed(None, None, "text", "A", 2, 2, "M", 1,
                                1, 1.5, 2)
        self.assertEqual(calls, [("write",), ("move", "BACKFEED", 2),
                                 ("write",), ("wait",), ("move", "FEED", 1)])
        self.assertEqual(make_job.call_args.args[-1], 1.5)

    def test_status_json_is_machine_readable(self):
        output = StringIO()
        fake_dev = object()
        fake_intf = type("Interface", (), {"bInterfaceNumber": 0})()
        fake_out = object()
        fake_in = object()
        with (
            patch("sys.argv", ["print_positions.py", "status", "--json"]),
            patch.object(printer, "open_printer", return_value=(fake_dev, fake_intf,
                                                                  fake_out, fake_in)),
            patch.object(printer, "status", return_value=(0x00, "normal")),
            patch.object(printer.usb.util, "release_interface"),
            patch.object(printer.usb.util, "dispose_resources"),
            redirect_stdout(output),
        ):
            self.assertEqual(printer.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), {
            "ok": True, "device": "TE344", "ready": True,
            "status_code": 0, "status": "normal",
        })


if __name__ == "__main__":
    unittest.main()
