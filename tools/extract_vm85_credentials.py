#!/usr/bin/env python3
"""Read owner-authorized Motorola Nursery connection details using ADB.

Python 3.10+, standard library only. Raw Android logs stay in memory.
The legacy PowerShell helper is retained independently for existing users.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

PACKAGE_NAME = "com.fivegencare.com.motorola.nursery"
ORBWEB_ACCOUNT = "orbweb_user"
FLAGS = re.IGNORECASE | re.DOTALL
SID_PATTERN = r"startM2M\s+sid=(?P<sid>[A-Za-z0-9._-]+)\s+china="
ORBWEB_PATTERN = (
    r"startM2M:\s*executing.*?sid:(?P<sid>\S+)\s+"
    r"password:(?P<password>\S+)\s+initReady:"
)
RTSP_PATTERN = (
    r'rtsp://(?P<username>[^:/\s"]+):(?P<password>.+?)'
    r"@127\.0\.0\.1:(?P<port>\d+)/"
)
IP_PATTERN = (
    r"(?:connect|connected)\s+to\s+"
    r"(?P<ip>(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}))\s*:\s*77(?!\d)"
)
PRIVATE_NETWORKS = tuple(
    ipaddress.IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class HelperError(Exception):
    """A safe, user-facing error, never containing command output or credentials."""


class MissingValuesError(HelperError):
    """The live view did not emit all required credentials (exit status 2)."""


@dataclass(frozen=True, repr=False)
class Credentials:
    """Secrets must only be rendered for the explicit final result."""

    camera_ip: str | None = field(repr=False)
    sid: str = field(repr=False)
    orbweb_password: str = field(repr=False)
    rtsp_username: str = field(repr=False)
    rtsp_password: str = field(repr=False)

    def render(self) -> str:
        values = (
            (
                "Camera LAN IP address",
                self.camera_ip or "NOT DETECTED - check your router",
            ),
            ("Orbweb SID", self.sid),
            ("Orbweb account", ORBWEB_ACCOUNT),
            ("Orbweb device password", self.orbweb_password),
            ("RTSP username", self.rtsp_username),
            ("RTSP password", self.rtsp_password),
        )
        return "\n".join(f"{label:28}: {value}" for label, value in values)


def resolve_adb(custom_path: str | None = None) -> str:
    """Resolve only an explicit file or a PATH executable, never an implicit cwd."""
    if custom_path:
        path = Path(custom_path).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise HelperError("ADB was not found at the supplied path. Check --adb-path.")
    executable = shutil.which("adb")
    if executable:
        return executable
    raise HelperError(
        "ADB was not found in PATH. Install platform-tools or use --adb-path."
    )


def run_command(arguments: list[str], purpose: str, *, text_input: str | None = None):
    """Capture stdout and stderr without exposing either in failures."""
    try:
        result = subprocess.run(
            arguments,
            input=text_input,
            stdin=subprocess.DEVNULL if text_input is None else None,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise HelperError(
            f"Unable to {purpose}. Check the connection and retry."
        ) from None
    if result.returncode:
        raise HelperError(f"Unable to {purpose}. Check the connection and retry.")
    return result.stdout


class Adb:
    """All device operations target one explicitly resolved Android serial."""

    def __init__(self, executable: str):
        self.executable = executable
        self.serial: str | None = None

    def run(self, *arguments: str, purpose: str) -> str:
        selection = ["-s", self.serial] if self.serial else []
        return run_command([self.executable, *selection, *arguments], purpose)

    def select_device(self, requested: str | None = None) -> None:
        output = self.run("devices", purpose="list Android devices")
        devices = {}
        for line in output.splitlines():
            match = re.fullmatch(
                r"(\S+)\s+(device|unauthorized|offline)(?:\s+.*)?", line
            )
            if match:
                devices[match[1]] = match[2]
        if requested is not None:
            state = devices.get(requested)
            if state == "unauthorized":
                raise HelperError(
                    "The selected phone is unauthorized. Unlock it and accept "
                    "USB debugging."
                )
            if state != "device":
                raise HelperError(
                    "The selected device is absent or offline. Check adb devices."
                )
            self.serial = requested
        else:
            authorized = [
                serial for serial, state in devices.items() if state == "device"
            ]
            if len(authorized) > 1:
                raise HelperError(
                    "Multiple authorized devices found. Use --serial DEVICE_SERIAL "
                    "(see adb devices)."
                )
            if not authorized:
                raise HelperError(
                    "No authorized Android device found. Enable USB debugging, use a "
                    "data cable, unlock the phone and accept the authorization prompt. "
                    "Check adb devices for unauthorized or offline devices."
                )
            self.serial = authorized[0]
        if (
            self.run("get-state", purpose="check Android device state").strip()
            != "device"
        ):
            raise HelperError("The Android device is not ready. Reconnect and retry.")

    def check_app(self) -> None:
        output = self.run(
            "shell",
            "pm",
            "path",
            PACKAGE_NAME,
            purpose="check Motorola Nursery installation",
        )
        if not any(line.startswith("package:") for line in output.splitlines()):
            raise HelperError(
                "Motorola Nursery is not installed in the active Android user."
            )


def validate_ip(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        raise HelperError(
            "Camera IP must be a valid private LAN IPv4 address."
        ) from None
    if not any(address in network for network in PRIVATE_NETWORKS):
        raise HelperError("Camera IP must be a private LAN IPv4 address (RFC1918).")
    return str(address)


def decode_credential(value: str) -> str:
    """Decode URL escapes once, retaining literal plus and undecodable input."""
    try:
        return unquote(value, errors="strict")
    except UnicodeDecodeError:
        # Do not silently replace password bytes with Unicode replacement characters.
        return value


def extract_credentials(
    log_text: str, marker: str, camera_ip: str | None = None
) -> Credentials:
    """Apply PowerShell extraction rules, checking every observed camera identity."""
    position = log_text.rfind(marker)
    if not marker or position < 0:
        raise HelperError(
            "Fresh capture marker missing. Restart the helper before opening live view."
        )
    session = log_text[position + len(marker) :]
    sid_matches = list(re.finditer(SID_PATTERN, session, FLAGS))
    orbweb_matches = list(re.finditer(ORBWEB_PATTERN, session, FLAGS))
    sids = {match["sid"] for match in sid_matches + orbweb_matches}
    passwords = {match["password"] for match in orbweb_matches}
    if len(sids) > 1 or len(passwords) > 1:
        raise HelperError(
            "Multiple camera connections or conflicting Orbweb credentials detected. "
            "Open only the target camera and retry."
        )
    rtsp_matches = list(re.finditer(RTSP_PATTERN, session.replace(r"\/", "/"), re.I))
    # Compare raw pairs, case-sensitively, ignoring changing local tunnel ports.
    pairs = {(match["username"], match["password"]) for match in rtsp_matches}
    if len(pairs) > 1:
        raise HelperError(
            "Multiple RTSP credential pairs detected. "
            "Open only the target camera and retry."
        )
    sid = next(iter(sids), "").strip()
    password = next(iter(passwords), "")
    username, rtsp_password = next(iter(pairs), ("", ""))
    username = decode_credential(username)
    rtsp_password = decode_credential(rtsp_password)
    missing = [
        name
        for name, value in (
            ("Orbweb SID", sid),
            ("Orbweb device password", password),
            ("RTSP username", username),
            ("RTSP password", rtsp_password),
        )
        if not value
    ]
    if missing:
        raise MissingValuesError(
            "Not all values were found: " + ", ".join(missing) + ". "
            "Restart the helper and wait for live video in Motorola Nursery."
        )
    # Percent-decoded controls must never reach the terminal or clipboard.
    if any(
        ord(char) < 32 or 127 <= ord(char) <= 159
        for value in (sid, password, username, rtsp_password)
        for char in value
    ):
        raise HelperError("Invalid control characters in credentials. Capture again.")
    if camera_ip is not None:
        camera_ip = validate_ip(camera_ip)
    else:
        addresses = {
            validate_ip(match["ip"])
            for match in re.finditer(IP_PATTERN, session, FLAGS)
        }
        if len(addresses) > 1:
            raise HelperError(
                "Multiple LAN addresses detected. "
                "Verify the camera IP and use --camera-ip."
            )
        camera_ip = next(iter(addresses), None)
    return Credentials(camera_ip, sid, password, username, rtsp_password)


def copy_to_clipboard(value: str) -> None:
    """Use native clipboard tools only when the user supplies the opt-in flag."""
    if sys.platform == "darwin":
        candidates = [("pbcopy", [])]
    elif sys.platform == "win32":
        candidates = [
            (
                "powershell.exe",
                [
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "[Console]::InputEncoding = "
                    "[System.Text.UTF8Encoding]::new($false); "
                    "Set-Clipboard -Value ([Console]::In.ReadToEnd())",
                ],
            )
        ]
    else:
        candidates = [
            ("wl-copy", []),
            ("xclip", ["-selection", "clipboard"]),
            ("xsel", ["--clipboard", "--input"]),
        ]
    for name, arguments in candidates:
        executable = shutil.which(name)
        if executable:
            run_command([executable, *arguments], "copy to clipboard", text_input=value)
            return
    raise HelperError(
        "Clipboard tool unavailable. Copy the displayed values manually; "
        "on Linux install wl-clipboard (Wayland) or xclip/xsel (X11)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb-path", help="Path to adb or adb.exe")
    parser.add_argument("--serial", help="Authorized Android serial from adb devices")
    parser.add_argument("--camera-ip", help="Camera private LAN IPv4 address")
    parser.add_argument(
        "--copy-to-clipboard",
        action="store_true",
        help="Explicitly copy the six values",
    )
    options = parser.parse_args(argv)
    try:
        if options.camera_ip is not None:
            validate_ip(options.camera_ip)
        adb = Adb(resolve_adb(options.adb_path))
        adb.select_device(options.serial)
        adb.check_app()
        print("Motorola VM85CONNECT setup helper")
        print("Reads your authorized app session in memory; no log file is saved.")
        print("Keep terminal output private. Close Motorola Nursery before continuing.")
        input("When the app is closed, press Enter to start a fresh capture: ")
        marker = "HA_VM85_" + uuid.uuid4().hex
        adb.run(
            "shell",
            "log",
            "-t",
            "HA_VM85_SETUP",
            marker,
            purpose="start a fresh Android log capture",
        )
        print("On the phone, open Motorola Nursery and only the target VM85CONNECT.")
        input("Wait for live video, then return here and press Enter: ")
        log_text = adb.run(
            "logcat", "-d", "-v", "threadtime", purpose="read Android logs"
        )
        credentials = extract_credentials(log_text, marker, options.camera_ip)
        del log_text
        print("\nKeep these values private; they provide access to your camera.\n")
        rendered = credentials.render()
        print(rendered)
        print(
            "\nThe VM85 profile supplies RTSP port 6667 "
            "and path /blinkhd automatically."
        )
        if credentials.camera_ip is None:
            print("Find the camera IP in your router, or retry with --camera-ip.")
        if options.copy_to_clipboard:
            copy_to_clipboard(rendered)
            print("The six values were copied. Clear the clipboard after setup.")
        return 0
    except MissingValuesError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 2
    except HelperError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print(
            "\nCapture cancelled. Run again in an interactive terminal.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # Even unexpected failures must not print tracebacks containing log data.
        print(
            "FAILED: Unexpected helper failure. No diagnostic data was printed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
