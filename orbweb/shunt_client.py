"""Asynchronous Orbweb TCP shunt registration client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import Protocol

from .framing import (
    OrbwebCommand,
    OrbwebProtocolError,
    async_read_packet,
    async_write_packet,
)
from .responses import ServerUtilityResponse
from .shunt import (
    ShuntRegistrationRequest,
    ShuntRegistrationResponse,
    parse_shunt_registration_response,
)

SHUNT_PORT = 10243
DEFAULT_SHUNT_TIMEOUT = 10.0
DEFAULT_SHUNT_RETRY_INTERVAL = 0.1


class _Writer(Protocol):
    """Small StreamWriter surface used by the client and its tests."""

    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


OpenConnection = Callable[
    ...,
    Awaitable[tuple[asyncio.StreamReader, _Writer]],
]


@dataclass(frozen=True, slots=True, repr=False)
class ShuntRegistrationPlan:
    """Native shunt destination derived from the first utility slot."""

    server: str = field(repr=False)
    port: int = SHUNT_PORT

    @classmethod
    def from_server_utilities(
        cls,
        utilities: ServerUtilityResponse,
    ) -> ShuntRegistrationPlan:
        """Select the utility address passed to native ``RegShuntServer``."""
        try:
            server = str(IPv4Address(utilities.addresses[0]))
        except ValueError as err:
            raise ValueError("Shunt server must be an IPv4 address") from err
        return cls(server)


class ShuntRegistrationRejected(OrbwebProtocolError):
    """The shunt server returned native command ``0x2be``."""


async def async_register_with_shunt(
    *,
    utilities: ServerUtilityResponse,
    request: ShuntRegistrationRequest,
    timeout: float = DEFAULT_SHUNT_TIMEOUT,
    retry_interval: float = DEFAULT_SHUNT_RETRY_INTERVAL,
    open_connection: OpenConnection = asyncio.open_connection,
) -> ShuntRegistrationResponse:
    """Register local candidates and return the peer candidates.

    The native client resends command ``0x2bc`` every 100 ms until a response
    arrives. One persistent read task mirrors that behavior without cancelling
    and restarting a partially completed TCP read between retries.
    """
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if retry_interval <= 0:
        raise ValueError("retry_interval must be greater than zero")
    plan = ShuntRegistrationPlan.from_server_utilities(utilities)

    async with asyncio.timeout(timeout):
        reader, writer = await open_connection(plan.server, plan.port)
        response_task = asyncio.create_task(async_read_packet(reader))
        try:
            while not response_task.done():
                await async_write_packet(writer, request.packet())
                await asyncio.wait(
                    {response_task},
                    timeout=retry_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            packet = response_task.result()
            if packet.command == OrbwebCommand.TCP_SHUNT_REG_FAIL_RSP:
                raise ShuntRegistrationRejected(
                    "Orbweb TCP shunt registration was rejected"
                )
            return parse_shunt_registration_response(
                packet,
                expected_client_id=request.client_id,
            )
        finally:
            if not response_task.done():
                response_task.cancel()
                with suppress(asyncio.CancelledError):
                    await response_task
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionError):
                pass
