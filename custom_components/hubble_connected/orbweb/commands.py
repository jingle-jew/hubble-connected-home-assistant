"""Read-only Hubble command getters carried by an Orbweb LAN mapping."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from urllib.parse import quote

from ..local import HubbleLocalProtocolError, parse_int_value, parse_temperature
from .pool import OrbwebLanMappingPool, OrbwebStablePort

ORBWEB_COMMAND_REMOTE_PORT = 80
MAX_HTTP_RESPONSE_SIZE = 64 * 1024
DEFAULT_MAPPING_TIMEOUT = 35.0


class HubbleOrbwebCommandError(Exception):
    """Base error for the command service transported over Orbweb."""


class HubbleOrbwebCommandCannotConnect(HubbleOrbwebCommandError):
    """The mapped camera command service could not be reached."""


class HubbleOrbwebCommandProtocolError(HubbleOrbwebCommandError):
    """The mapped camera command service returned an invalid response."""


class HubbleOrbwebCommandClient:
    """Query the 3667 HTTP command service through its encrypted LAN tunnel."""

    def __init__(self, mappings: OrbwebLanMappingPool, key: str) -> None:
        self._mappings = mappings
        self._key = key
        self._stable_port: OrbwebStablePort | None = None
        self._request_lock = asyncio.Lock()

    async def async_prepare(self, timeout: float = DEFAULT_MAPPING_TIMEOUT) -> None:
        """Establish or renew the authenticated multi-port mapping once."""
        try:
            async with asyncio.timeout(timeout):
                await self._mappings.async_get_mapping(self._key)
        except Exception as err:
            raise HubbleOrbwebCommandCannotConnect(type(err).__name__) from err

    async def async_get_temperature(self) -> float:
        """Read the camera temperature."""
        try:
            return parse_temperature(await self._async_command("value_temperature"))
        except (HubbleLocalProtocolError, ValueError) as err:
            raise HubbleOrbwebCommandProtocolError(str(err)) from err

    async def async_get_wifi_strength(self) -> int:
        """Read the camera-reported Wi-Fi quality percentage."""
        try:
            return parse_int_value(
                "get_wifi_strength",
                await self._async_command("get_wifi_strength"),
                0,
                100,
            )
        except (HubbleLocalProtocolError, ValueError) as err:
            raise HubbleOrbwebCommandProtocolError(str(err)) from err

    async def async_get_video_bitrate(self) -> int:
        """Read the active camera video bitrate in kbit/s."""
        try:
            return parse_int_value(
                "get_video_bitrate",
                await self._async_command("get_video_bitrate"),
                0,
                100_000,
            )
        except (HubbleLocalProtocolError, ValueError) as err:
            raise HubbleOrbwebCommandProtocolError(str(err)) from err

    async def _async_command(self, command: str, timeout: float = 5.0) -> str:
        path = f"/?action=command&command={quote(command, safe='')}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            "Host: camera\r\n"
            "Accept: text/plain\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        try:
            async with self._request_lock, asyncio.timeout(timeout):
                stable_port = await self._async_get_stable_port()
                reader, writer = await asyncio.open_connection(
                    stable_port.host,
                    stable_port.port,
                )
                try:
                    writer.write(request)
                    await writer.drain()
                    chunks: list[bytes] = []
                    response_size = 0
                    while True:
                        try:
                            chunk = await reader.read(16 * 1024)
                        except ConnectionResetError:
                            if chunks:
                                break
                            raise
                        if not chunk:
                            break
                        response_size += len(chunk)
                        if response_size > MAX_HTTP_RESPONSE_SIZE:
                            raise HubbleOrbwebCommandProtocolError(
                                "HTTP response exceeds the size limit"
                            )
                        chunks.append(chunk)
                    raw_response = b"".join(chunks)
                finally:
                    writer.close()
                    with suppress(OSError):
                        await writer.wait_closed()
        except HubbleOrbwebCommandProtocolError:
            raise
        except Exception as err:
            raise HubbleOrbwebCommandCannotConnect(type(err).__name__) from err

        header, separator, body = raw_response.partition(b"\r\n\r\n")
        if not separator:
            raise HubbleOrbwebCommandProtocolError("Incomplete HTTP response")
        status_line = header.split(b"\r\n", 1)[0]
        try:
            protocol, status, _reason = status_line.decode("ascii").split(" ", 2)
        except (UnicodeDecodeError, ValueError) as err:
            raise HubbleOrbwebCommandProtocolError("Invalid HTTP status line") from err
        if not protocol.startswith("HTTP/") or status != "200":
            raise HubbleOrbwebCommandProtocolError(f"Unexpected HTTP status {status}")
        try:
            return body.decode("utf-8").strip()
        except UnicodeDecodeError as err:
            raise HubbleOrbwebCommandProtocolError(
                "Command response is not UTF-8"
            ) from err

    async def _async_get_stable_port(self) -> OrbwebStablePort:
        stable_port = self._stable_port
        if stable_port is None or stable_port.is_closed:
            stable_port = await self._mappings.async_get_stable_mapping(
                self._key,
                ORBWEB_COMMAND_REMOTE_PORT,
            )
            self._stable_port = stable_port
        return stable_port
