"""RTSP endpoint primitives shared by the config flow and camera entity."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote


class HubbleRtspError(Exception):
    """Base error for a camera RTSP endpoint."""


class HubbleRtspCannotConnect(HubbleRtspError):
    """The RTSP endpoint could not be reached."""


class HubbleRtspAuthError(HubbleRtspError):
    """The RTSP endpoint rejected the configured credentials."""


class HubbleRtspProtocolError(HubbleRtspError):
    """The endpoint did not answer with RTSP."""


@dataclass(frozen=True, slots=True)
class HubbleRtspEndpoint:
    """Connection information for a standard RTSP stream."""

    host: str
    port: int
    path: str
    username: str = ""
    password: str = ""

    def __post_init__(self) -> None:
        normalized_path = self.path.strip() or "/"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        object.__setattr__(self, "path", normalized_path)

    @property
    def authority_host(self) -> str:
        """Return a URL-safe host, including brackets for IPv6 literals."""
        if ":" in self.host and not self.host.startswith("["):
            return f"[{self.host}]"
        return self.host

    @property
    def url(self) -> str:
        """Build the RTSP URL consumed by Home Assistant's stream worker."""
        credentials = ""
        if self.username or self.password:
            credentials = (
                f"{quote(self.username, safe='')}:{quote(self.password, safe='')}@"
            )
        return f"rtsp://{credentials}{self.authority_host}:{self.port}{self.path}"

    @property
    def redacted_url(self) -> str:
        """Return a diagnostic URL that never contains credentials."""
        return f"rtsp://{self.authority_host}:{self.port}{self.path}"


def stream_options_for_backend(backend: str) -> dict[str, str]:
    """Return Home Assistant stream options for a Hubble backend."""
    if backend == "local_rtsp":
        return {"rtsp_transport": "udp"}
    if backend == "orbweb_lan":
        return {"rtsp_transport": "tcp"}
    return {}


async def async_probe_rtsp(endpoint: HubbleRtspEndpoint, timeout: float = 5.0) -> None:
    """Verify that the configured endpoint speaks RTSP without starting media."""
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
            try:
                auth = ""
                if endpoint.username or endpoint.password:
                    token = base64.b64encode(
                        f"{endpoint.username}:{endpoint.password}".encode()
                    ).decode()
                    auth = f"Authorization: Basic {token}\r\n"
                request = (
                    f"OPTIONS {endpoint.redacted_url} RTSP/1.0\r\n"
                    "CSeq: 1\r\n"
                    "User-Agent: HomeAssistant-HubbleConnected/0.1\r\n"
                    f"{auth}\r\n"
                )
                writer.write(request.encode("ascii"))
                await writer.drain()
                status_line = await reader.readline()
            finally:
                writer.close()
                await writer.wait_closed()
    except (TimeoutError, OSError) as err:
        raise HubbleRtspCannotConnect(str(err)) from err

    try:
        protocol, status, _reason = status_line.decode("ascii").split(" ", 2)
        status_code = int(status)
    except (UnicodeDecodeError, ValueError) as err:
        raise HubbleRtspProtocolError("Invalid RTSP status line") from err

    if protocol not in {"RTSP/1.0", "RTSP/1.1"}:
        raise HubbleRtspProtocolError(f"Unexpected protocol: {protocol}")
    if status_code == 401:
        raise HubbleRtspAuthError("RTSP credentials were rejected")
    if status_code >= 500:
        raise HubbleRtspCannotConnect(f"RTSP server returned {status_code}")


async def async_probe_rtsp_candidates(
    endpoints: Iterable[HubbleRtspEndpoint], timeout: float = 2.0
) -> tuple[HubbleRtspEndpoint, ...]:
    """Return only endpoints that answer a non-streaming RTSP probe."""

    async def _probe(
        endpoint: HubbleRtspEndpoint,
    ) -> HubbleRtspEndpoint | None:
        try:
            await async_probe_rtsp(endpoint, timeout=timeout)
        except HubbleRtspError:
            return None
        return endpoint

    results = await asyncio.gather(*(_probe(endpoint) for endpoint in endpoints))
    return tuple(endpoint for endpoint in results if endpoint is not None)
