"""Composition of Orbweb direct rendezvous into a loopback mapping."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .direct import DirectCandidate
from .direct_client import (
    DEFAULT_DIRECT_FORWARD_TIMEOUT,
    DEFAULT_DIRECT_REGISTRATION_TIMEOUT,
    DirectListenerPlan,
    DirectListenerSession,
    async_open_direct_listener,
)
from .direct_client import (
    ConnectionFactory as SignalingConnectionFactory,
)
from .direct_listeners import (
    DEFAULT_DIRECT_BIND_ATTEMPTS,
    DirectListenerPool,
    PortFactory,
    ServerFactory,
    async_open_direct_listener_pool,
)
from .direct_race import (
    DEFAULT_DIRECT_RACE_TIMEOUT,
    async_race_direct_candidates,
)
from .direct_race import (
    ConnectionFactory as RaceConnectionFactory,
)
from .direct_race import (
    PortFactory as RacePortFactory,
)
from .handshake import async_accept_tunnel_key
from .port_mapping import (
    DEFAULT_KEEPALIVE_INITIAL_DELAY,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_REMOTE_RTSP_PORT,
    OrbwebPortMapping,
    async_open_port_mapping,
)
from .port_mapping import (
    ServerFactory as MappingServerFactory,
)
from .rendezvous import RendezvousServers
from .tunnel import TunnelCipher

DEFAULT_TUNNEL_HANDSHAKE_TIMEOUT = 10.0

DirectListenerOpener = Callable[..., Awaitable[DirectListenerSession]]
DirectPoolOpener = Callable[..., Awaitable[DirectListenerPool]]


class OrbwebDirectTunnelListener:
    """Registered direct listener awaiting one camera connection."""

    def __init__(
        self,
        pool: DirectListenerPool,
        signaling: DirectListenerSession,
    ) -> None:
        self._pool = pool
        self._signaling = signaling
        self._closed = False

    def __repr__(self) -> str:
        """Keep identities and candidate addresses out of diagnostics."""
        return f"{type(self).__name__}()"

    @property
    def candidates(self) -> tuple[DirectCandidate, ...]:
        """Return local endpoints that were registered with rendezvous."""
        return self._pool.candidates

    @property
    def is_closed(self) -> bool:
        """Return whether listener and signaling ownership have ended."""
        return self._closed

    async def async_accept_port_mapping(
        self,
        *,
        forward_timeout: float = DEFAULT_DIRECT_FORWARD_TIMEOUT,
        race_timeout: float = DEFAULT_DIRECT_RACE_TIMEOUT,
        handshake_timeout: float = DEFAULT_TUNNEL_HANDSHAKE_TIMEOUT,
        remote_port: int = DEFAULT_REMOTE_RTSP_PORT,
        additional_remote_ports: tuple[int, ...] = (),
        race_connection_factory: RaceConnectionFactory = asyncio.open_connection,
        race_port_factory: RacePortFactory | None = None,
        mapping_server_factory: MappingServerFactory = asyncio.start_server,
        keepalive_initial_delay: float = DEFAULT_KEEPALIVE_INITIAL_DELAY,
        keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
        private_value: int | None = None,
    ) -> OrbwebPortMapping:
        """Accept, authenticate, and transfer one direct socket to a mapping."""
        if self._closed:
            raise RuntimeError("Orbweb direct tunnel listener is closed")
        if handshake_timeout <= 0:
            raise ValueError("handshake_timeout must be greater than zero")

        connection = None
        transferred = False
        try:
            forwarded = await self._signaling.async_wait_for_forward(
                timeout=forward_timeout
            )
            connection = await async_race_direct_candidates(
                self._pool,
                forwarded.candidates,
                connection_factory=race_connection_factory,
                port_factory=race_port_factory,
                timeout=race_timeout,
            )
            await self.async_close()
            async with asyncio.timeout(handshake_timeout):
                material = await async_accept_tunnel_key(
                    connection.reader,
                    connection.writer,
                    private_value=private_value,
                )
            mapping = await async_open_port_mapping(
                connection.reader,
                connection.writer,
                TunnelCipher(material.key, material.iv),
                remote_port=remote_port,
                additional_remote_ports=additional_remote_ports,
                server_factory=mapping_server_factory,
                keepalive_initial_delay=keepalive_initial_delay,
                keepalive_interval=keepalive_interval,
            )
            transferred = True
            return mapping
        finally:
            await self.async_close()
            if connection is not None and not transferred:
                connection.writer.close()
                try:
                    await connection.writer.wait_closed()
                except (BrokenPipeError, ConnectionError):
                    pass

    async def async_close(self) -> None:
        """Close signaling and unclaimed direct-listener sockets exactly once."""
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(
            self._signaling.async_close(),
            self._pool.async_close(),
            return_exceptions=True,
        )

    async def __aenter__(self) -> OrbwebDirectTunnelListener:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.async_close()


async def async_open_direct_tunnel_listener(
    servers: RendezvousServers,
    *,
    client_id: str,
    local_addresses: tuple[str, ...],
    listener_server_factory: ServerFactory = asyncio.start_server,
    listener_port_factory: PortFactory | None = None,
    listener_bind_attempts: int = DEFAULT_DIRECT_BIND_ATTEMPTS,
    signaling_connection_factory: SignalingConnectionFactory = (
        asyncio.open_connection
    ),
    signaling_timeout: float = DEFAULT_DIRECT_REGISTRATION_TIMEOUT,
    pool_opener: DirectPoolOpener = async_open_direct_listener_pool,
    signaling_opener: DirectListenerOpener = async_open_direct_listener,
) -> OrbwebDirectTunnelListener:
    """Bind local candidates, then register them with the TAT server."""
    pool = await pool_opener(
        local_addresses,
        server_factory=listener_server_factory,
        port_factory=listener_port_factory,
        bind_attempts=listener_bind_attempts,
    )
    try:
        signaling = await signaling_opener(
            DirectListenerPlan.from_rendezvous(
                servers,
                client_id=client_id,
                candidates=pool.candidates,
            ),
            connection_factory=signaling_connection_factory,
            timeout=signaling_timeout,
        )
    except BaseException:
        await pool.async_close()
        raise
    return OrbwebDirectTunnelListener(pool, signaling)
