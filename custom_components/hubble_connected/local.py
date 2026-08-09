"""Local read-only Hubble camera protocol."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import quote


class HubbleLocalError(Exception):
    """Base error for the local camera command API."""


class HubbleLocalConfigError(HubbleLocalError):
    """The configured local camera list is invalid."""


class HubbleLocalCannotConnect(HubbleLocalError):
    """A camera did not answer its local command API."""


class HubbleLocalProtocolError(HubbleLocalError):
    """A camera returned an unexpected local API response."""


ParsedValue = TypeVar("ParsedValue")


@dataclass(frozen=True, slots=True)
class HubbleLocalCameraSpec:
    """User-facing name and private LAN address for one camera."""

    name: str
    host: str
    source: str = "manual"


@dataclass(frozen=True, slots=True)
class HubbleLocalCameraData:
    """Latest read-only state for one camera."""

    spec: HubbleLocalCameraSpec
    temperature: float | None
    wifi_strength: int | None
    wifi_connection_state: str | None
    video_bitrate: int | None
    blink_led: int | None
    flipup: int | None
    available: bool
    mac: str | None = None
    udid: str | None = None
    firmware_version: str | None = None
    default_version: str | None = None
    model_code: str | None = None
    soc_version: str | None = None
    night_vision: int | None = None
    resolution: str | None = None
    speaker_volume: int | None = None
    timezone: str | None = None
    motion_area: str | None = None
    error: str | None = None


def parse_local_camera_specs(value: str) -> tuple[HubbleLocalCameraSpec, ...]:
    """Parse `Name=IP` entries separated by commas or newlines."""
    if not value.strip():
        return ()

    specs: list[HubbleLocalCameraSpec] = []
    seen_hosts: set[str] = set()
    for raw_entry in re.split(r"[,\n]", value):
        entry = raw_entry.strip()
        if not entry:
            continue
        if "=" in entry:
            name, host = (part.strip() for part in entry.split("=", 1))
        else:
            host = entry
            name = f"Hubble {host}"
        if not name:
            raise HubbleLocalConfigError("A camera name cannot be empty")
        try:
            address = ipaddress.ip_address(host)
        except ValueError as err:
            raise HubbleLocalConfigError(f"Invalid camera IP: {host}") from err
        if not address.is_private or address.is_loopback or address.is_multicast:
            raise HubbleLocalConfigError(
                f"Camera IP must be a private LAN address: {host}"
            )
        normalized_host = str(address)
        if normalized_host in seen_hosts:
            raise HubbleLocalConfigError(f"Duplicate camera IP: {host}")
        seen_hosts.add(normalized_host)
        specs.append(HubbleLocalCameraSpec(name=name, host=normalized_host))
    return tuple(specs)


def parse_temperature(response: str) -> float:
    """Parse both `value_temperature: 21.6` and bare numeric responses."""
    match = re.fullmatch(
        r"\s*(?:value_temperature\s*:\s*)?(-?\d+(?:\.\d+)?)\s*", response
    )
    if match is None:
        raise HubbleLocalProtocolError(
            f"Unexpected temperature response: {response[:80]!r}"
        )
    value = float(match.group(1))
    if not -50 <= value <= 100:
        raise HubbleLocalProtocolError(f"Implausible temperature: {value}")
    return value


def parse_command_value(command: str, response: str) -> str:
    """Extract a value from the camera's `command: value` response."""
    prefix, separator, value = response.partition(":")
    if not separator or prefix.strip() != command or not value.strip():
        raise HubbleLocalProtocolError(
            f"Unexpected {command} response: {response[:80]!r}"
        )
    return value.strip()


def parse_int_value(command: str, response: str, minimum: int, maximum: int) -> int:
    """Parse and range-check an integer getter response."""
    value = parse_command_value(command, response)
    try:
        parsed = int(value)
    except ValueError as err:
        raise HubbleLocalProtocolError(
            f"Unexpected integer for {command}: {value!r}"
        ) from err
    if not minimum <= parsed <= maximum:
        raise HubbleLocalProtocolError(f"Out-of-range value for {command}: {parsed}")
    return parsed


def parse_mac_address(response: str) -> str:
    """Parse bare and command-prefixed `get_mac_address` responses."""
    prefix, separator, value = response.partition(":")
    candidate = (
        value.strip()
        if separator and prefix.strip() == "get_mac_address"
        else response.strip()
    )
    compact = re.sub(r"[^0-9a-f]", "", candidate.lower())
    if len(compact) != 12 or not re.fullmatch(r"[0-9a-f]{12}", compact):
        raise HubbleLocalProtocolError(
            f"Unexpected MAC address response: {response[:80]!r}"
        )
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


class HubbleLocalClient:
    """Minimal HTTP client for read-only Nuvoton command getters."""

    def __init__(self, spec: HubbleLocalCameraSpec) -> None:
        self.spec = spec
        self._metadata_loaded = False
        self._metadata: dict[str, str | int | None] = {}
        self._temperature: float | None = None
        self._wifi_strength: int | None = None
        self._wifi_connection_state: str | None = None
        self._video_bitrate: int | None = None
        self._night_vision: int | None = None
        self._blink_led: int | None = None
        self._flipup: int | None = None
        self._request_lock = asyncio.Lock()

    async def async_update(self) -> HubbleLocalCameraData:
        """Fetch metadata once and changing measurements on every update."""
        try:
            if not self._metadata_loaded:
                await self._async_load_metadata()
                self._metadata_loaded = True
            self._temperature = await self._async_optional_value(
                "value_temperature", parse_temperature
            )
            self._wifi_connection_state = await self._async_optional_value(
                "get_wifi_connection_state",
                lambda response: parse_command_value(
                    "get_wifi_connection_state", response
                ),
            )
            self._wifi_strength = await self._async_optional_value(
                "get_wifi_strength",
                lambda response: parse_int_value("get_wifi_strength", response, 0, 100),
            )
            self._video_bitrate = await self._async_optional_value(
                "get_video_bitrate",
                lambda response: parse_int_value(
                    "get_video_bitrate", response, 0, 100_000
                ),
            )
            self._night_vision = await self._async_optional_value(
                "get_night_vision",
                lambda response: parse_int_value("get_night_vision", response, 0, 2),
            )
            self._blink_led = await self._async_optional_value(
                "get_blink_led",
                lambda response: parse_int_value("get_blink_led", response, 0, 1),
            )
            self._flipup = await self._async_optional_value(
                "value_flipup",
                lambda response: parse_int_value("value_flipup", response, 0, 1),
            )
        except HubbleLocalError as err:
            return self._data(available=False, error=str(err))
        if all(
            value is None
            for value in (
                self._temperature,
                self._wifi_connection_state,
                self._wifi_strength,
                self._video_bitrate,
                self._night_vision,
                self._blink_led,
                self._flipup,
            )
        ):
            return self._data(
                available=False,
                error="No supported changing getter returned a value",
            )
        return self._data(available=True)

    async def _async_load_metadata(self) -> None:
        """Read stable device metadata, tolerating unsupported getters."""
        commands = {
            "mac": "get_mac_address",
            "udid": "get_udid",
            "firmware_version": "get_version",
            "default_version": "get_default_version",
            "model_code": "get_model",
            "soc_version": "get_soc_version",
            "resolution": "get_resolution",
            "speaker_volume": "get_spk_volume",
            "timezone": "get_time_zone",
            "motion_area": "get_motion_area",
        }
        for field, command in commands.items():
            try:
                response = await self._async_command(command)
                value: str | int
                if field == "mac":
                    value = parse_mac_address(response)
                else:
                    value = parse_command_value(command, response)
                if field in {"night_vision", "speaker_volume"}:
                    value = int(value)
            except (HubbleLocalProtocolError, ValueError):
                value = None
            self._metadata[field] = value

    async def _async_optional_value(
        self,
        command: str,
        parser: Callable[[str], ParsedValue],
    ) -> ParsedValue | None:
        """Read one optional getter without hiding other supported values."""
        try:
            return parser(await self._async_command(command))
        except (HubbleLocalProtocolError, ValueError):
            return None

    async def async_get_mac_address(self, timeout: float = 5.0) -> str:
        """Read and normalize the camera MAC without loading all metadata."""
        return parse_mac_address(
            await self._async_command("get_mac_address", timeout=timeout)
        )

    def _data(
        self, *, available: bool, error: str | None = None
    ) -> HubbleLocalCameraData:
        """Build an immutable coordinator snapshot from cached values."""
        return HubbleLocalCameraData(
            spec=self.spec,
            temperature=self._temperature,
            wifi_strength=self._wifi_strength,
            wifi_connection_state=self._wifi_connection_state,
            video_bitrate=self._video_bitrate,
            blink_led=self._blink_led,
            flipup=self._flipup,
            available=available,
            mac=self._string_metadata("mac"),
            udid=self._string_metadata("udid"),
            firmware_version=self._string_metadata("firmware_version"),
            default_version=self._string_metadata("default_version"),
            model_code=self._string_metadata("model_code"),
            soc_version=self._string_metadata("soc_version"),
            night_vision=self._night_vision,
            resolution=self._string_metadata("resolution"),
            speaker_volume=self._integer_metadata("speaker_volume"),
            timezone=self._string_metadata("timezone"),
            motion_area=self._string_metadata("motion_area"),
            error=error,
        )

    async def async_set_video_bitrate(self, value: int) -> None:
        """Set one of the bitrates verified on the MBP167 firmware."""
        from .const import BITRATE_OPTIONS

        if value not in BITRATE_OPTIONS:
            raise HubbleLocalConfigError(f"Unsupported video bitrate: {value}")
        await self._async_set_command("set_video_bitrate", value)
        self._video_bitrate = value

    async def async_set_night_vision(self, value: int) -> None:
        """Set night vision mode: 0 auto, 1 on, 2 off."""
        from .const import NIGHT_VISION_OPTIONS

        if value not in NIGHT_VISION_OPTIONS:
            raise HubbleLocalConfigError(f"Unsupported night vision mode: {value}")
        await self._async_set_command("set_night_vision", value)
        self._night_vision = value

    async def async_set_blink_led(self, enabled: bool) -> None:
        """Enable or disable the camera indicator LED."""
        value = int(enabled)
        await self._async_set_command("set_blink_led", value)
        self._blink_led = value

    async def async_set_flipup(self, enabled: bool) -> None:
        """Enable or disable ceiling-mount vertical image flip."""
        value = int(enabled)
        await self._async_set_command("set_flipup", value)
        self._flipup = value

    async def _async_set_command(self, command: str, value: int) -> None:
        response = await self._async_command(command, value=str(value))
        result = parse_int_value(command, response, -999, 999)
        if result != 0:
            raise HubbleLocalProtocolError(
                f"Camera rejected {command} with result {result}"
            )

    def _string_metadata(self, field: str) -> str | None:
        value = self._metadata.get(field)
        return value if isinstance(value, str) else None

    def _integer_metadata(self, field: str) -> int | None:
        value = self._metadata.get(field)
        return value if isinstance(value, int) else None

    async def _async_command(
        self, command: str, timeout: float = 5.0, value: str | None = None
    ) -> str:
        path = f"/?action=command&command={quote(command, safe='')}"
        if value is not None:
            path += f"&value={quote(value, safe='')}"
        host_header = self.spec.host
        if ":" in host_header:
            host_header = f"[{host_header}]"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Accept: text/plain\r\n"
            "Connection: close\r\n\r\n"
        )
        try:
            async with self._request_lock, asyncio.timeout(timeout):
                reader, writer = await asyncio.open_connection(self.spec.host, 80)
                try:
                    writer.write(request.encode("ascii"))
                    await writer.drain()
                    raw_response = await reader.read(64 * 1024)
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (TimeoutError, OSError) as err:
            raise HubbleLocalCannotConnect(str(err)) from err

        header, separator, body = raw_response.partition(b"\r\n\r\n")
        if not separator:
            raise HubbleLocalProtocolError("Incomplete HTTP response")
        status_line = header.split(b"\r\n", 1)[0]
        try:
            _protocol, status, _reason = status_line.decode("ascii").split(" ", 2)
            status_code = int(status)
        except (UnicodeDecodeError, ValueError) as err:
            raise HubbleLocalProtocolError("Invalid HTTP status line") from err
        if status_code != 200:
            raise HubbleLocalProtocolError(f"HTTP status {status_code}")
        try:
            return body.decode("utf-8").strip()
        except UnicodeDecodeError as err:
            raise HubbleLocalProtocolError("Response is not UTF-8 text") from err
