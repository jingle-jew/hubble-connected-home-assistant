"""Synthetic Motorola Nursery log and mocked ADB tests; no device access."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).parents[1] / "tools" / "extract_vm85_credentials.py"
SPEC = importlib.util.spec_from_file_location("vm85_helper_test", PATH)
assert SPEC and SPEC.loader
helper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helper
SPEC.loader.exec_module(helper)
MARKER = "HA_VM85_synthetic_marker"
SESSION = """
startM2M sid=synthetic-sid china=false
startM2M: executing sid:synthetic-sid password:synthetic-orb-secret initReady:true
rtsp://synthetic-user:synthetic-rtsp-secret@127.0.0.1:31415/blinkhd
connected to 192.168.50.20:77
"""


class ExtractionTests(unittest.TestCase):
    def extract(self, session=SESSION, ip=None):
        return helper.extract_credentials(MARKER + session, MARKER, ip)

    def test_complete_extraction_and_six_values(self):
        result = self.extract()
        self.assertEqual(result.camera_ip, "192.168.50.20")
        self.assertEqual(result.sid, "synthetic-sid")
        self.assertEqual(result.orbweb_password, "synthetic-orb-secret")
        self.assertEqual(result.rtsp_username, "synthetic-user")
        self.assertEqual(result.rtsp_password, "synthetic-rtsp-secret")
        self.assertEqual(len(result.render().splitlines()), 6)
        self.assertIn("orbweb_user", result.render())
        self.assertNotIn("synthetic", repr(result))

    def test_missing_marker(self):
        with self.assertRaisesRegex(helper.HelperError, "marker missing"):
            helper.extract_credentials(SESSION, MARKER)

    def test_only_values_after_last_marker_are_used(self):
        old = SESSION.replace("synthetic-sid", "old-synthetic-sid")
        result = helper.extract_credentials(MARKER + old + MARKER + SESSION, MARKER)
        self.assertEqual(result.sid, "synthetic-sid")

    def test_stale_values_cannot_fill_missing_fields(self):
        with self.assertRaises(helper.MissingValuesError):
            helper.extract_credentials(SESSION + MARKER, MARKER)

    def test_each_missing_credential(self):
        for prefix, label in (
            ("startM2M", "Orbweb SID"),
            ("startM2M: executing", "Orbweb device password"),
            ("rtsp://", "RTSP username"),
        ):
            with self.subTest(label=label):
                session = "\n".join(
                    line for line in SESSION.splitlines() if not line.startswith(prefix)
                )
                with self.assertRaisesRegex(helper.MissingValuesError, label):
                    self.extract(session)

    def test_encoded_credentials_escaped_slashes_and_literal_plus(self):
        session = SESSION.replace(
            "rtsp://synthetic-user:synthetic-rtsp-secret@",
            r"rtsp:\/\/user%40example.com:p%3Aa+ss%20%25%40@",
        ).replace("/blinkhd", r"\/blinkhd")
        result = self.extract(session)
        self.assertEqual(result.rtsp_username, "user@example.com")
        self.assertEqual(result.rtsp_password, "p:a+ss %@")

    def test_decode_once_unicode_and_malformed_escapes(self):
        for encoded, decoded in (
            ("p%C3%A9%2520+", "pé%20+"),
            ("literal%ZZ%", "literal%ZZ%"),
            ("invalid%FF", "invalid%FF"),
        ):
            with self.subTest(encoded=encoded):
                result = self.extract(SESSION.replace("synthetic-rtsp-secret", encoded))
                self.assertEqual(result.rtsp_password, decoded)

    def test_encoded_controls_rejected_without_echo(self):
        for value in ("secret%0Ainjection", "secret%1B[2J", "secret%C2%85"):
            with self.subTest(value=value):
                with self.assertRaises(helper.HelperError) as raised:
                    self.extract(SESSION.replace("synthetic-rtsp-secret", value))
                self.assertNotIn("secret", str(raised.exception))

    def test_repeated_credentials_with_different_tunnel_ports(self):
        self.assertEqual(
            self.extract(SESSION + SESSION.replace("31415", "55555")).sid,
            "synthetic-sid",
        )

    def test_multiple_sid_forms_are_rejected_case_sensitively(self):
        additions = (
            "startM2M sid=other-synthetic-sid china=false",
            "startM2M sid=SYNTHETIC-sid china=false",
            "startM2M: executing sid:other-sid password:other-secret initReady:true",
        )
        for addition in additions:
            with self.subTest(addition=addition):
                with self.assertRaisesRegex(helper.HelperError, "Multiple camera"):
                    self.extract(SESSION + addition)

    def test_conflicting_orbweb_passwords(self):
        with self.assertRaisesRegex(helper.HelperError, "conflicting Orbweb"):
            self.extract(
                SESSION + SESSION.replace("synthetic-orb-secret", "other-secret")
            )

    def test_multiple_rtsp_pairs_raw_case_sensitive_comparison(self):
        for replacement in (
            "other-secret",
            "SYNTHETIC-rtsp-secret",
            "%73ynthetic-rtsp-secret",
        ):
            with self.subTest(replacement=replacement):
                with self.assertRaisesRegex(helper.HelperError, "Multiple RTSP"):
                    self.extract(
                        SESSION + SESSION.replace("synthetic-rtsp-secret", replacement)
                    )

    def test_orbweb_details_can_supply_sid_without_first_pattern(self):
        self.assertEqual(self.extract(SESSION.split("\n", 2)[2]).sid, "synthetic-sid")

    def test_missing_ip_is_not_fatal(self):
        result = self.extract(SESSION.replace("connected to", "unrelated"))
        self.assertIsNone(result.camera_ip)
        self.assertIn("NOT DETECTED", result.render())

    def test_manual_ip_overrides_detection(self):
        self.assertEqual(self.extract(ip="10.0.0.20").camera_ip, "10.0.0.20")

    def test_ip_ranges_validation_and_port_boundary(self):
        for address in ("192.168.999.1", "192.0.2.1", "127.0.0.1", "::1", "172.32.0.1"):
            with self.subTest(address=address):
                with self.assertRaises(helper.HelperError) as raised:
                    self.extract(ip=address)
                self.assertNotIn(address, str(raised.exception))
        self.assertIsNone(self.extract(SESSION.replace(":77\n", ":777\n")).camera_ip)
        for address in ("10.0.0.20", "172.16.0.20", "172.31.0.20", "192.168.50.20"):
            with self.subTest(address=address):
                self.assertEqual(
                    self.extract(SESSION.replace("192.168.50.20", address)).camera_ip,
                    address,
                )

    def test_multiple_ips_require_manual_selection(self):
        session = SESSION + "\nconnect to 10.0.0.20:77"
        with self.assertRaisesRegex(helper.HelperError, "Multiple LAN addresses"):
            self.extract(session)
        self.assertEqual(self.extract(session, "10.0.0.20").camera_ip, "10.0.0.20")

    def test_errors_never_include_partial_credentials(self):
        for session in (
            SESSION.replace("rtsp://", "missing://"),
            SESSION + SESSION.replace("synthetic-sid", "other-sid"),
            SESSION + SESSION.replace("synthetic-rtsp-secret", "other-secret"),
        ):
            with self.subTest(session=session):
                with self.assertRaises(helper.HelperError) as raised:
                    self.extract(session)
                self.assertNotIn("synthetic", str(raised.exception))
                self.assertNotIn("other-secret", str(raised.exception))


class AdbTests(unittest.TestCase):
    def test_path_detection(self):
        with patch.object(
            helper.shutil, "which", return_value="/synthetic/adb"
        ) as which:
            self.assertEqual(helper.resolve_adb(), "/synthetic/adb")
            which.assert_called_once_with("adb")
        with patch.object(helper.shutil, "which", return_value=None):
            with self.assertRaisesRegex(helper.HelperError, "not found in PATH"):
                helper.resolve_adb()

    def test_custom_path_with_spaces_and_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "platform tools" / "adb.exe"
            path.parent.mkdir()
            path.touch()
            self.assertEqual(helper.resolve_adb(str(path)), str(path.resolve()))
            with self.assertRaises(helper.HelperError):
                helper.resolve_adb(str(path.parent / "missing"))

    def select(self, output, serial=None):
        adb = helper.Adb("adb")
        with patch.object(adb, "run", side_effect=[output, "device\n"]):
            adb.select_device(serial)
        return adb

    def test_no_device(self):
        with self.assertRaisesRegex(helper.HelperError, "No authorized"):
            self.select("List of devices attached\n")

    def test_multiple_devices_require_explicit_serial(self):
        output = "List of devices attached\nsynthetic-a\tdevice\nsynthetic-b\tdevice\n"
        with self.assertRaisesRegex(helper.HelperError, "--serial"):
            self.select(output)
        self.assertEqual(self.select(output, "synthetic-b").serial, "synthetic-b")

    def test_unauthorized_offline_and_missing_selected_device(self):
        output = "synthetic-a\tunauthorized\nsynthetic-b\toffline\n"
        for serial in (None, "synthetic-a", "synthetic-b", "missing"):
            with self.subTest(serial=serial):
                with self.assertRaises(helper.HelperError):
                    self.select(output, serial)

    def test_single_authorized_device_ignores_unauthorized(self):
        adb = self.select("synthetic-a\tunauthorized\nsynthetic-b\tdevice\n")
        self.assertEqual(adb.serial, "synthetic-b")

    def test_state_checked_after_selection(self):
        adb = helper.Adb("adb")
        with patch.object(adb, "run", side_effect=["synthetic-a\tdevice", "offline"]):
            with self.assertRaisesRegex(helper.HelperError, "not ready"):
                adb.select_device()

    def test_app_absent_and_present(self):
        adb = helper.Adb("adb")
        with patch.object(adb, "run", return_value=""):
            with self.assertRaisesRegex(helper.HelperError, "not installed"):
                adb.check_app()
        with patch.object(adb, "run", return_value="package:/synthetic/base.apk\n"):
            adb.check_app()

    def test_command_uses_argument_list_explicit_serial_and_no_shell(self):
        adb = helper.Adb("/synthetic platform tools/adb")
        adb.serial = "synthetic-serial"
        with patch.object(helper.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "device", "")
            self.assertEqual(adb.run("get-state", purpose="check state"), "device")
            args, kwargs = run.call_args
            self.assertEqual(args[0], [adb.executable, "-s", adb.serial, "get-state"])
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], 30)

    def test_command_failure_timeout_and_os_error_do_not_leak(self):
        secret = "synthetic-private-command-output"
        outcomes = (
            subprocess.CompletedProcess([], 1, secret, secret),
            subprocess.TimeoutExpired([secret], 30, output=secret, stderr=secret),
            OSError(secret),
        )
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome)):
                with patch.object(helper.subprocess, "run") as run:
                    if isinstance(outcome, Exception):
                        run.side_effect = outcome
                    else:
                        run.return_value = outcome
                    with self.assertRaises(helper.HelperError) as raised:
                        helper.run_command(["adb"], "read Android logs")
                    self.assertNotIn(secret, str(raised.exception))


class WorkflowTests(unittest.TestCase):
    def run_main(self, argv=None):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = helper.main(argv or [])
        return status, output.getvalue(), errors.getvalue()

    def test_full_workflow_and_clipboard_opt_in(self):
        for opt_in in (False, True):
            with self.subTest(opt_in=opt_in):

                def command(arguments, purpose, **kwargs):
                    if arguments[-1] == "devices":
                        return "synthetic-device\tdevice\n"
                    if arguments[-1] == "get-state":
                        return "device"
                    if "path" in arguments:
                        return "package:/synthetic/base.apk"
                    if "log" in arguments:
                        self.marker = arguments[-1]
                        return ""
                    if "logcat" in arguments:
                        return self.marker + SESSION
                    self.fail("Unexpected ADB operation")

                with (
                    patch.object(helper, "resolve_adb", return_value="adb"),
                    patch.object(helper, "run_command", side_effect=command) as run,
                    patch("builtins.input", return_value=""),
                    patch.object(helper, "copy_to_clipboard") as clipboard,
                ):
                    status, output, errors = self.run_main(
                        ["--copy-to-clipboard"] if opt_in else []
                    )
                    self.assertEqual(status, 0)
                    self.assertFalse(errors)
                    self.assertIn("synthetic-orb-secret", output)
                    self.assertEqual(clipboard.call_count, int(opt_in))
                    self.assertEqual(run.call_count, 5)
                    self.assertRegex(self.marker, r"^HA_VM85_[a-f0-9]{32}$")

    def test_main_failure_and_incomplete_exit_status(self):
        for error, expected in (
            (helper.HelperError("safe error"), 1),
            (helper.MissingValuesError("missing fields"), 2),
            (RuntimeError("synthetic-private-data"), 1),
        ):
            with self.subTest(expected=expected):
                with patch.object(helper, "resolve_adb", side_effect=error):
                    status, output, errors = self.run_main()
                self.assertEqual(status, expected)
                self.assertNotIn("synthetic-private-data", output + errors)
                self.assertNotIn("Traceback", errors)

    def test_clipboard_native_tools_receive_secrets_only_on_stdin(self):
        for platform, name in (
            ("darwin", "pbcopy"),
            ("win32", "powershell.exe"),
            ("linux", "wl-copy"),
        ):
            with self.subTest(platform=platform):
                with (
                    patch.object(helper.sys, "platform", platform),
                    patch.object(
                        helper.shutil,
                        "which",
                        side_effect=lambda tool: tool if tool == name else None,
                    ),
                    patch.object(helper, "run_command") as run,
                ):
                    helper.copy_to_clipboard("synthetic-private-data")
                    args, kwargs = run.call_args
                    self.assertNotIn("synthetic-private-data", str(args))
                    self.assertEqual(kwargs["text_input"], "synthetic-private-data")

    @unittest.skipUnless(os.name == "posix", "Executable fake ADB requires POSIX")
    def test_cli_without_site_packages_with_real_subprocesses(self):
        # The fake stores only a marker, never a log capture. All emitted data
        # is generated from synthetic literals, just like the unit fixtures.
        with tempfile.TemporaryDirectory() as directory:
            adb_path = Path(directory) / "platform tools" / "adb"
            adb_path.parent.mkdir()
            adb_path.write_text(
                f"#!{sys.executable}\n"
                "import sys\nfrom pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "marker = Path(__file__).with_suffix('.marker')\n"
                "if args == ['devices']:\n"
                "    print('synthetic-device\\tdevice')\n"
                "elif args[-1] == 'get-state':\n"
                "    print('device')\n"
                "elif 'path' in args:\n"
                "    print('package:/synthetic/base.apk')\n"
                "elif 'log' in args:\n"
                "    marker.write_text(args[-1])\n"
                "elif 'logcat' in args:\n"
                f"    print(marker.read_text() + {SESSION!r})\n"
                "else:\n    sys.exit(1)\n"
            )
            adb_path.chmod(0o700)
            result = subprocess.run(
                [sys.executable, "-I", "-S", str(PATH), "--adb-path", str(adb_path)],
                input="\n\n",
                capture_output=True,
                text=True,
                timeout=15,
                shell=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIn("synthetic-orb-secret", result.stdout)
            self.assertIn("synthetic-rtsp-secret", result.stdout)

    def test_clipboard_unavailable(self):
        with patch.object(helper.shutil, "which", return_value=None):
            with self.assertRaisesRegex(
                helper.HelperError, "Clipboard tool unavailable"
            ):
                helper.copy_to_clipboard("synthetic-private-data")


if __name__ == "__main__":
    unittest.main()
