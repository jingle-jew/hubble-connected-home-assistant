"""Asynchronous client for Orbweb direct-listener rendezvous."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .direct import (
    DirectAddressRegistrationRequest,
    DirectCandidate,
    DirectConnectionForward,
    parse_direct_connection_forward,
)
from .framing import (
    OrbwebCommand,
    OrbwebProtocolError,
    async_read_packet,
    async_write_packet,
)
from .rendezvous import RendezvousServers

DIRECT_RENDEZVOUS_PORT = 443
DEFAULT_DIRECT_REGISTRATION_TIMEOUT = 10.0
DEFAULT_DIRECT_FORWARD_TIMEOUT = 20.0

ConnectionFactory = Callable[
    [str, int],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


class DirectRendezvousRejected(OrbwebProtocolError):
    """The direct rendezvous server rejected a registration or connection."""

    def __init__(self, command: int) -> None:
        super().__init__(f"Direct rendezvous rejected by command 0x{command:x}")
        self.command = command


@dataclass(frozen=True, slots=True, repr=False)
class DirectListenerPlan:
    """Validated inputs for one listener registration on the TAT server."""

    host: str
    client_id: str = field(repr=False)
    candidates: tuple[DirectCandidate, ...]
    port: int = DIRECT_RENDEZVOUS_PORT

    @classmethod
    def from_rendezvous(
        cls,
        servers: RendezvousServers,
        *,
        client_id: str,
        candidates: tuple[DirectCandidate, ...],
    ) -> DirectListenerPlan:
        """Select the HTTPS-bootstrap TAT host used by native ``XListen``."""
        return cls(servers.tat_server, client_id, candidates)

    def __post_init__(self) -> None:
        if not self.host or not self.host.isascii():
            raise ValueError("Direct rendezvous host must be non-empty ASCII")
        if any(character in self.host for character in "/,@\\"):
            raise ValueError("Direct rendezvous host is invalid")
        if any(character.isspace() or ord(character) < 0x20 for character in self.host):
            raise ValueError("Direct rendezvous host contains invalid characters")
        if not 1 <= self.port <= 0xFFFF:
            raise ValueError("Direct rendezvous port must be between 1 and 65535")
        DirectAddressRegistrationRequest(
            self.client_id,
            self.candidates,
        ).packet()


class DirectListenerSession:
    """An open signaling session awaiting a peer's forwarded endpoints."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client_id: str,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._client_id = client_id
        self._closed = False

    @property
    def is_closed(self) -> bool:
        """Return whether ownership of the signaling socket has ended."""
        return self._closed

    async def async_wait_for_forward(
        self,
        *,
        timeout: float = DEFAULT_DIRECT_FORWARD_TIMEOUT,
    ) -> DirectConnectionForward:
        """Wait for the server to forward the peer's candidate list."""
        if self._closed:
            raise RuntimeError("Direct listener session is closed")
        async with asyncio.timeout(timeout):
            packet = await async_read_packet(self._reader)
        if packet.command in {
            OrbwebCommand.CONN_REG_ADDR_FAIL,
            OrbwebCommand.CONN_DIR_CONN_FAIL,
        }:
            raise DirectRendezvousRejected(packet.command)
        return parse_direct_connection_forward(
            packet,
            expected_client_id=self._client_id,
        )

    async def async_close(self) -> None:
        """Close the signaling socket exactly once."""
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        await self._writer.wait_closed()

    async def __aenter__(self) -> DirectListenerSession:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.async_close()


async def async_open_direct_listener(
    plan: DirectListenerPlan,
    *,
    connection_factory: ConnectionFactory = asyncio.open_connection,
    timeout: float = DEFAULT_DIRECT_REGISTRATION_TIMEOUT,
) -> DirectListenerSession:
    """Register listener candidates and return the still-open session."""
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await connection_factory(plan.host, plan.port)
            await async_write_packet(
                writer,
                DirectAddressRegistrationRequest(
                    plan.client_id,
                    plan.candidates,
                ).packet(),
            )
            response = await async_read_packet(reader)
        if response.command == OrbwebCommand.CONN_REG_ADDR_FAIL:
            raise DirectRendezvousRejected(response.command)
        if response.command != OrbwebCommand.CONN_REG_ADDR_RSP:
            raise OrbwebProtocolError(
                "Expected direct registration response; "
                f"received 0x{response.command:x}"
            )
        if response.payload:
            raise OrbwebProtocolError(
                "Direct registration response payload must be empty"
            )
        return DirectListenerSession(reader, writer, plan.client_id)
    except BaseException:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        raise
