"""Direct-LAN Orbweb client composed from the verified protocol stages."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .authentication import (
    DEFAULT_AUTH_TIMEOUT,
    ORBWEB_AUTH_ACCOUNT,
    ORBWEB_AUTH_REMOTE_PORT,
    async_authenticate_camera,
)
from .control import RendezvousControlResult, async_run_control_exchange
from .direct_client import DEFAULT_DIRECT_FORWARD_TIMEOUT
from .direct_race import DEFAULT_DIRECT_RACE_TIMEOUT
from .direct_tunnel import (
    DEFAULT_TUNNEL_HANDSHAKE_TIMEOUT,
    OrbwebDirectTunnelListener,
    async_open_direct_tunnel_listener,
)
from .identity import OrbwebClientIdentity
from .port_mapping import DEFAULT_REMOTE_RTSP_PORT, OrbwebPortMapping
from .rendezvous import RendezvousServers

CONTROL_PORT = 443
DIRECT_LAN_CONNECTION_MODE = 1
DIRECT_LAN_TIMEOUT_MS = 10_000
DEFAULT_CONTROL_CONNECT_TIMEOUT = 10.0
DEFAULT_CONTROL_EXCHANGE_TIMEOUT = 20.0
# Match the complete listener set created by the official Orbweb Android SDK.
# The RTSP listener is supplied separately as ``remote_port``.
ORBWEB_ADDITIONAL_REMOTE_PORTS = (80, 8080, 51108, ORBWEB_AUTH_REMOTE_PORT)

ConnectionFactory = Callable[..., Awaitable[tuple[Any, Any]]]
DirectListenerOpener = Callable[..., Awaitable[OrbwebDirectTunnelListener]]
ControlExchange = Callable[..., Awaitable[RendezvousControlResult]]
AuthenticationExchange = Callable[..., Awaitable[None]]


class OrbwebControlCannotConnect(ConnectionError):
    """Neither rendezvous control candidate accepted a TCP connection."""


async def async_open_lan_rtsp_mapping(
    servers: RendezvousServers,
    *,
    target_id: str,
    identity: OrbwebClientIdentity,
    local_addresses: tuple[str, ...],
    remote_port: int = DEFAULT_REMOTE_RTSP_PORT,
    connect_timeout: float = DEFAULT_CONTROL_CONNECT_TIMEOUT,
    control_timeout: float = DEFAULT_CONTROL_EXCHANGE_TIMEOUT,
    auth_account: str = ORBWEB_AUTH_ACCOUNT,
    auth_password: str | None = None,
    auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
    forward_timeout: float = DEFAULT_DIRECT_FORWARD_TIMEOUT,
    race_timeout: float = DEFAULT_DIRECT_RACE_TIMEOUT,
    handshake_timeout: float = DEFAULT_TUNNEL_HANDSHAKE_TIMEOUT,
    connection_factory: ConnectionFactory = asyncio.open_connection,
    direct_listener_opener: DirectListenerOpener = (async_open_direct_tunnel_listener),
    control_exchange: ControlExchange = async_run_control_exchange,
    authentication_exchange: AuthenticationExchange = (async_authenticate_camera),
) -> OrbwebPortMapping:
    """Ask one owned camera to establish a direct encrypted RTSP mapping.

    This direct-only stage intentionally omits NAT/shunt/relay fallback. It is
    sufficient for the capture-verified same-LAN route and fails closed when
    the camera cannot reach one of the advertised local addresses.
    """
    if not target_id or not target_id.isascii():
        raise ValueError("target_id must be non-empty ASCII")
    if not local_addresses:
        raise ValueError("At least one local address is required")
    if connect_timeout <= 0 or control_timeout <= 0:
        raise ValueError("Orbweb control timeouts must be greater than zero")

    client_id = identity.next_client_id(target_id)
    listener = await direct_listener_opener(
        servers,
        client_id=client_id,
        local_addresses=local_addresses,
    )
    mapping_task = asyncio.create_task(
        listener.async_accept_port_mapping(
            forward_timeout=forward_timeout,
            race_timeout=race_timeout,
            handshake_timeout=handshake_timeout,
            remote_port=remote_port,
            additional_remote_ports=ORBWEB_ADDITIONAL_REMOTE_PORTS
            if auth_password is not None
            else (),
        )
    )
    writer = None
    control_task: asyncio.Task[RendezvousControlResult] | None = None
    established_mapping: OrbwebPortMapping | None = None
    try:
        connected_host, reader, writer = await _async_open_control_connection(
            servers,
            connection_factory=connection_factory,
            timeout=connect_timeout,
        )
        secondary_server = (
            servers.relay_server
            if connected_host == servers.tat_server
            else servers.tat_server
        )
        control_task = asyncio.create_task(
            _async_control_exchange_with_timeout(
                control_exchange,
                reader,
                writer,
                timeout=control_timeout,
                target_id=target_id,
                client_id=client_id,
                client_token=identity.client_token,
                secondary_server=secondary_server,
                local_ip_count=len(local_addresses),
                connection_type_config=DIRECT_LAN_CONNECTION_MODE,
                timeout_ms=DIRECT_LAN_TIMEOUT_MS,
            )
        )
        await _async_require_both(control_task, mapping_task)
        established_mapping = mapping_task.result()
        if auth_password is not None:
            await authentication_exchange(
                established_mapping.host,
                established_mapping.local_port(ORBWEB_AUTH_REMOTE_PORT),
                p2p_server_id=client_id,
                account=auth_account,
                password=auth_password,
                timeout=auth_timeout,
            )
        return established_mapping
    except BaseException:
        for task in (control_task, mapping_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (control_task, mapping_task) if task is not None),
            return_exceptions=True,
        )
        if established_mapping is not None:
            await established_mapping.async_close()
        raise
    finally:
        await listener.async_close()
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionError):
                pass


async def _async_open_control_connection(
    servers: RendezvousServers,
    *,
    connection_factory: ConnectionFactory,
    timeout: float,
) -> tuple[str, Any, Any]:
    last_error: BaseException | None = None
    for host in servers.connection_candidates:
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await connection_factory(host, CONTROL_PORT)
            return host, reader, writer
        except (OSError, TimeoutError) as err:
            last_error = err
    raise OrbwebControlCannotConnect(
        "Orbweb rendezvous control candidates are unreachable"
    ) from last_error


async def _async_control_exchange_with_timeout(
    exchange: ControlExchange,
    reader: Any,
    writer: Any,
    *,
    timeout: float,
    **kwargs: Any,
) -> RendezvousControlResult:
    async with asyncio.timeout(timeout):
        return await exchange(reader, writer, **kwargs)


async def _async_require_both(
    control_task: asyncio.Task[RendezvousControlResult],
    mapping_task: asyncio.Task[OrbwebPortMapping],
) -> None:
    tasks = {control_task, mapping_task}
    done, pending = await asyncio.wait(
        tasks,
        return_when=asyncio.FIRST_EXCEPTION,
    )
    error = next(
        (
            task.exception()
            for task in done
            if not task.cancelled() and task.exception() is not None
        ),
        None,
    )
    if error is not None:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise error
    await asyncio.gather(*tasks)
