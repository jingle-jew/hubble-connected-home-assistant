"""Local TCP listener pool for Orbweb direct connections."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Any

from .direct import DirectCandidate

MIN_DIRECT_LISTENER_PORT = 0x2000
MAX_DIRECT_LISTENER_PORT = 0xFFFF
DEFAULT_DIRECT_BIND_ATTEMPTS = 8

ClientConnectedCallback = Callable[
    [asyncio.StreamReader, asyncio.StreamWriter],
    None,
]
ServerFactory = Callable[..., Awaitable[Any]]
PortFactory = Callable[[], int]


@dataclass(frozen=True, slots=True)
class AcceptedDirectConnection:
    """One accepted candidate socket whose ownership leaves the pool."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter


class DirectListenerPool:
    """Listeners sharing one port across all advertised local addresses."""

    def __init__(
        self,
        servers: tuple[Any, ...],
        candidates: tuple[DirectCandidate, ...],
    ) -> None:
        self._servers = servers
        self.candidates = candidates
        self._queue: asyncio.Queue[AcceptedDirectConnection] = asyncio.Queue()
        self._pending_writers: set[asyncio.StreamWriter] = set()
        self._closed = False

    @property
    def port(self) -> int:
        """Return the port shared by every listener."""
        return self.candidates[0].port

    @property
    def is_closed(self) -> bool:
        """Return whether the listener sockets have been closed."""
        return self._closed

    def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if self._closed:
            writer.close()
            return
        self._pending_writers.add(writer)
        self._queue.put_nowait(AcceptedDirectConnection(reader, writer))

    async def async_accept(
        self,
        *,
        timeout: float | None = None,
    ) -> AcceptedDirectConnection:
        """Wait for one peer and transfer ownership of its socket."""
        if self._closed:
            raise RuntimeError("Direct listener pool is closed")
        if timeout is None:
            accepted = await self._queue.get()
        else:
            async with asyncio.timeout(timeout):
                accepted = await self._queue.get()
        self._pending_writers.discard(accepted.writer)
        return accepted

    async def async_close(self) -> None:
        """Close listeners and accepted sockets not yet handed to a caller."""
        if self._closed:
            return
        self._closed = True
        for server in self._servers:
            server.close()
        await asyncio.gather(
            *(server.wait_closed() for server in self._servers),
            return_exceptions=True,
        )
        pending = tuple(self._pending_writers)
        self._pending_writers.clear()
        for writer in pending:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in pending),
            return_exceptions=True,
        )

    async def __aenter__(self) -> DirectListenerPool:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.async_close()


async def async_open_direct_listener_pool(
    addresses: tuple[str, ...],
    *,
    server_factory: ServerFactory = asyncio.start_server,
    port_factory: PortFactory | None = None,
    bind_attempts: int = DEFAULT_DIRECT_BIND_ATTEMPTS,
) -> DirectListenerPool:
    """Bind the same native-range TCP port on every local IPv4 address."""
    validated = _validate_addresses(addresses)
    if bind_attempts < 1:
        raise ValueError("bind_attempts must be at least one")
    if port_factory is None:
        port_factory = _random_direct_port

    last_error: OSError | None = None
    for _ in range(bind_attempts):
        port = _validate_port(port_factory())
        candidates = tuple(DirectCandidate(address, port) for address in validated)
        pool = DirectListenerPool((), candidates)
        callback: ClientConnectedCallback = pool._on_client
        servers: list[Any] = []
        try:
            for address in validated:
                servers.append(await server_factory(callback, host=address, port=port))
        except OSError as err:
            last_error = err
            pool._servers = tuple(servers)
            await pool.async_close()
            continue
        except BaseException:
            pool._servers = tuple(servers)
            await pool.async_close()
            raise

        pool._servers = tuple(servers)
        return pool

    assert last_error is not None
    raise last_error


def _validate_addresses(addresses: tuple[str, ...]) -> tuple[str, ...]:
    if not addresses:
        raise ValueError("At least one direct listener address is required")
    validated: list[str] = []
    for value in addresses:
        try:
            address = IPv4Address(value)
        except ValueError as err:
            raise ValueError("Direct listener address must be IPv4") from err
        if address.is_unspecified or address.is_loopback or address.is_multicast:
            raise ValueError("Direct listener address is not LAN-advertisable")
        normalized = str(address)
        if normalized in validated:
            raise ValueError("Direct listener addresses must be unique")
        validated.append(normalized)
    return tuple(validated)


def _validate_port(value: int) -> int:
    if not MIN_DIRECT_LISTENER_PORT <= value <= MAX_DIRECT_LISTENER_PORT:
        raise ValueError("Direct listener port must be between 8192 and 65535")
    return value


def _random_direct_port() -> int:
    span = MAX_DIRECT_LISTENER_PORT - MIN_DIRECT_LISTENER_PORT + 1
    return MIN_DIRECT_LISTENER_PORT + secrets.randbelow(span)
