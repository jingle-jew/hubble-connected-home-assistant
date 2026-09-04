"""Bidirectional TCP candidate race for Orbweb direct connections."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable

from .direct import DirectCandidate
from .direct_listeners import (
    MAX_DIRECT_LISTENER_PORT,
    MIN_DIRECT_LISTENER_PORT,
    AcceptedDirectConnection,
    DirectListenerPool,
)

DEFAULT_DIRECT_RACE_TIMEOUT = 10.0

ConnectionFactory = Callable[
    ..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]
PortFactory = Callable[[], int]


async def async_race_direct_candidates(
    pool: DirectListenerPool,
    peer_candidates: tuple[DirectCandidate, ...],
    *,
    connection_factory: ConnectionFactory = asyncio.open_connection,
    port_factory: PortFactory | None = None,
    timeout: float = DEFAULT_DIRECT_RACE_TIMEOUT,
) -> AcceptedDirectConnection:
    """Return the first inbound or outbound candidate socket to succeed."""
    if pool.is_closed:
        raise RuntimeError("Direct listener pool is closed")
    if not peer_candidates:
        raise ValueError("At least one peer candidate is required")
    if port_factory is None:
        port_factory = _random_direct_port

    attempts: list[tuple[str, int, DirectCandidate]] = []
    for local in pool.candidates:
        for peer in peer_candidates:
            attempts.append((local.address, _validate_port(port_factory()), peer))

    tasks: set[asyncio.Task[AcceptedDirectConnection]] = {
        asyncio.create_task(pool.async_accept())
    }
    for local_address, local_port, peer in attempts:
        tasks.add(
            asyncio.create_task(
                _async_connect_candidate(
                    local_address,
                    local_port,
                    peer,
                    connection_factory,
                )
            )
        )

    winner: AcceptedDirectConnection | None = None
    try:
        async with asyncio.timeout(timeout):
            pending = set(tasks)
            while winner is None:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    try:
                        winner = task.result()
                    except OSError:
                        continue
                    if winner is not None:
                        break
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        losers = [
            result
            for result in results
            if isinstance(result, AcceptedDirectConnection) and result is not winner
        ]
        await _close_connections(losers)

    assert winner is not None
    return winner


async def _async_connect_candidate(
    local_address: str,
    local_port: int,
    peer: DirectCandidate,
    connection_factory: ConnectionFactory,
) -> AcceptedDirectConnection:
    reader, writer = await connection_factory(
        peer.address,
        peer.port,
        local_addr=(local_address, local_port),
    )
    return AcceptedDirectConnection(reader, writer)


def _validate_port(value: int) -> int:
    if not MIN_DIRECT_LISTENER_PORT <= value <= MAX_DIRECT_LISTENER_PORT:
        raise ValueError("Direct race port must be between 8192 and 65535")
    return value


def _random_direct_port() -> int:
    span = MAX_DIRECT_LISTENER_PORT - MIN_DIRECT_LISTENER_PORT + 1
    return MIN_DIRECT_LISTENER_PORT + secrets.randbelow(span)


async def _close_connections(
    connections: list[AcceptedDirectConnection],
) -> None:
    for connection in connections:
        connection.writer.close()
    await asyncio.gather(
        *(connection.writer.wait_closed() for connection in connections),
        return_exceptions=True,
    )
