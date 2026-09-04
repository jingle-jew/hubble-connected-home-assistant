"""HTTPS bootstrap for the Orbweb rendezvous servers.

This module implements only the discovery request observed in the Android SDK.
It deliberately does not open a tunnel or log the camera session identifier.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

DEFAULT_RENDEZVOUS_HOST = "rdz.orbwebsys.com"
CONNECTION_PATH = "/api/device/connection"
REQUEST_TIMEOUT = 10

REQUEST_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "OrbwebM2M/4.3.17/HomeAssistant",
}


class OrbwebRendezvousError(Exception):
    """Base error for the Orbweb HTTPS bootstrap."""


class OrbwebRendezvousCannotConnect(OrbwebRendezvousError):
    """The rendezvous HTTPS endpoint could not be reached."""


class OrbwebRendezvousProtocolError(OrbwebRendezvousError):
    """The rendezvous endpoint returned an unsupported response."""


class OrbwebRendezvousRejected(OrbwebRendezvousError):
    """The rendezvous endpoint rejected the camera session identifier."""

    def __init__(self, error_code: str) -> None:
        super().__init__(f"Orbweb rendezvous rejected the request ({error_code})")
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class RendezvousServers:
    """Server pair returned by the Orbweb connection bootstrap."""

    tat_server: str
    relay_server: str

    @property
    def connection_candidates(self) -> tuple[str, str]:
        """Return the order passed to the native connection client."""
        return (self.tat_server, self.relay_server)


@dataclass(frozen=True, slots=True, repr=False)
class RendezvousRequest:
    """Observed client bootstrap body; the target ID is sensitive metadata."""

    target_id: str = field(repr=False)

    def json(self) -> dict[str, str]:
        """Build the exact JSON object produced by the Android native SDK."""
        return {
            "role": "client",
            "id": _bounded_ascii(self.target_id, 255, "target_id"),
            "token": "",
        }


def parse_rendezvous_response(payload: Any) -> RendezvousServers:
    """Parse the three fields read by ``cURL::getRelayServerIP``."""
    if not isinstance(payload, Mapping):
        raise OrbwebRendezvousProtocolError("Rendezvous response is not an object")

    error_code = _error_code(payload.get("errno"))
    if error_code != "0":
        raise OrbwebRendezvousRejected(error_code)

    return RendezvousServers(
        tat_server=_server_name(payload.get("tatip"), "tatip"),
        relay_server=_server_name(payload.get("relayip"), "relayip"),
    )


class OrbwebRendezvousClient:
    """Small async client using a Home Assistant-managed HTTP session."""

    def __init__(
        self,
        session: Any,
        host: str = DEFAULT_RENDEZVOUS_HOST,
    ) -> None:
        self._session = session
        self._host = _rendezvous_host(host)

    async def async_get_servers(self, target_id: str) -> RendezvousServers:
        """Resolve the TCP rendezvous pair for one camera session ID."""
        request = RendezvousRequest(target_id)
        try:
            async with self._session.post(
                f"https://{self._host}{CONNECTION_PATH}",
                headers=REQUEST_HEADERS,
                json=request.json(),
                timeout=REQUEST_TIMEOUT,
            ) as response:
                payload = await response.json(content_type=None)
                status = int(response.status)
        except OrbwebRendezvousError:
            raise
        except (TypeError, ValueError) as err:
            raise OrbwebRendezvousProtocolError(
                "Rendezvous response is not valid JSON"
            ) from err
        except (OSError, TimeoutError) as err:
            raise OrbwebRendezvousCannotConnect(str(err)) from err
        except Exception as err:
            raise OrbwebRendezvousCannotConnect(
                f"Rendezvous request failed: {type(err).__name__}"
            ) from err

        if not 200 <= status < 300:
            raise OrbwebRendezvousCannotConnect(f"Rendezvous returned HTTP {status}")
        return parse_rendezvous_response(payload)


def _error_code(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value and value.isascii():
        return value
    raise OrbwebRendezvousProtocolError("Rendezvous errno is missing")


def _server_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise OrbwebRendezvousProtocolError(f"Rendezvous {field_name} is missing")
    try:
        server = _bounded_ascii(value, 255, field_name)
    except ValueError as err:
        raise OrbwebRendezvousProtocolError(str(err)) from err
    if any(character in server for character in "/,@\\"):
        raise OrbwebRendezvousProtocolError(
            f"Rendezvous {field_name} is not a server name"
        )
    if any(character.isspace() or ord(character) < 0x20 for character in server):
        raise OrbwebRendezvousProtocolError(
            f"Rendezvous {field_name} contains invalid characters"
        )
    return server


def _rendezvous_host(value: str) -> str:
    host = _bounded_ascii(value, 255, "rendezvous_host")
    if any(character in host for character in ":/,@\\"):
        raise ValueError("rendezvous_host must be a hostname or IPv4 address")
    if any(character.isspace() or ord(character) < 0x20 for character in host):
        raise ValueError("rendezvous_host contains invalid characters")
    return host


def _bounded_ascii(value: str, maximum: int, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must not be empty")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError(f"{field_name} must contain only ASCII") from err
    if len(encoded) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} bytes")
    return value
