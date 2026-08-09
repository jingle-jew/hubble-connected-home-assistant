"""Lazy, reusable direct-LAN mappings for multiple owned cameras."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from functools import partial
from typing import Any

from .identity import OrbwebClientIdentity
from .lan_client import async_open_lan_rtsp_mapping
from .port_mapping import OrbwebPortMapping
from .rendezvous import OrbwebRendezvousClient

SourceAddressResolver = Callable[[str], Awaitable[str]]
MappingOpener = Callable[..., Awaitable[OrbwebPortMapping]]
UpstreamConnector = Callable[
    [], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]

_LOGGER = logging.getLogger(__name__)


class OrbwebStablePort:
    """Keep one loopback port stable while Orbweb transports are replaced."""

    host = "127.0.0.1"

    def __init__(self, upstream_connector: UpstreamConnector) -> None:
        self._upstream_connector = upstream_connector
        self._server: asyncio.AbstractServer | None = None
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self.port = 0
        self.is_closed = False
        self.terminal_error: BaseException | None = None

    async def async_start(self) -> None:
        """Bind the stable listener before returning its endpoint."""
        if self._server is not None or self.is_closed:
            raise RuntimeError("Orbweb stable port cannot be started")
        self._server = await asyncio.start_server(
            self._async_handle_client,
            host=self.host,
            port=0,
        )
        sockets = self._server.sockets
        if not sockets:
            await self.async_close()
            raise RuntimeError("Orbweb stable port has no listening socket")
        self.port = int(sockets[0].getsockname()[1])

    async def _async_handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Bridge one client to the currently healthy Orbweb mapping."""
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        self._writers.add(writer)
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            upstream_reader, upstream_writer = await self._upstream_connector()
            self._writers.add(upstream_writer)
            relays = {
                asyncio.create_task(_async_relay(reader, upstream_writer)),
                asyncio.create_task(_async_relay(upstream_reader, writer)),
            }
            done, pending = await asyncio.wait(
                relays, return_when=asyncio.FIRST_COMPLETED
            )
            for relay in pending:
                relay.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for relay in done:
                relay.result()
        # The connector already classifies protocol/setup failures. A local
        # proxy client should simply see EOF and retry the same stable URL.
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Stable Orbweb client disconnected: %s", type(err).__name__)
        finally:
            for owned_writer in (writer, upstream_writer):
                if owned_writer is None:
                    continue
                self._writers.discard(owned_writer)
                owned_writer.close()
                with suppress(OSError):
                    await owned_writer.wait_closed()
            if task is not None:
                self._client_tasks.discard(task)

    async def async_close(self) -> None:
        """Close the stable listener and every active relay."""
        if self.is_closed:
            return
        self.is_closed = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in tuple(self._writers):
            writer.close()
        current = asyncio.current_task()
        tasks = tuple(task for task in self._client_tasks if task is not current)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._client_tasks.clear()
        self._writers.clear()


async def _async_relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Copy bytes until either side closes."""
    while data := await reader.read(64 * 1024):
        writer.write(data)
        await writer.drain()


class OrbwebLanMappingPool:
    """Open one loopback RTSP mapping per camera, only when requested."""

    def __init__(
        self,
        session: Any,
        target_ids: Mapping[str, str],
        *,
        auth_passwords: Mapping[str, str] | None = None,
        rendezvous_client: OrbwebRendezvousClient | None = None,
        source_address_resolver: SourceAddressResolver | None = None,
        mapping_opener: MappingOpener = async_open_lan_rtsp_mapping,
        identity: OrbwebClientIdentity | None = None,
    ) -> None:
        self._target_ids = dict(target_ids)
        self._auth_passwords = dict(auth_passwords or {})
        if not self._auth_passwords.keys() <= self._target_ids.keys():
            raise ValueError("Orbweb password has no matching camera target")
        self._rendezvous = rendezvous_client or OrbwebRendezvousClient(session)
        self._source_address_resolver = (
            source_address_resolver or async_source_ipv4_address
        )
        self._mapping_opener = mapping_opener
        self._identity = identity or OrbwebClientIdentity()
        self._mappings: dict[str, OrbwebPortMapping] = {}
        self._stable_ports: dict[str, OrbwebStablePort] = {}
        self._locks = {host: asyncio.Lock() for host in self._target_ids}
        self._stable_locks = {host: asyncio.Lock() for host in self._target_ids}
        self._closed = False

    def __repr__(self) -> str:
        """Report only a count; camera identifiers are sensitive metadata."""
        return f"{type(self).__name__}(camera_count={len(self._target_ids)})"

    @property
    def hosts(self) -> frozenset[str]:
        """Return local camera hosts with an Orbweb stream identity."""
        return frozenset(self._target_ids)

    async def async_get_mapping(self, host: str) -> OrbwebPortMapping:
        """Return a healthy mapping, establishing or replacing it as needed."""
        if self._closed:
            raise RuntimeError("Orbweb mapping pool is closed")
        try:
            target_id = self._target_ids[host]
            lock = self._locks[host]
        except KeyError as err:
            raise KeyError("Camera has no Orbweb stream identity") from err

        async with lock:
            if self._closed:
                raise RuntimeError("Orbweb mapping pool is closed")
            mapping = self._mappings.get(host)
            if (
                mapping is not None
                and not mapping.is_closed
                and mapping.terminal_error is None
            ):
                return mapping
            if mapping is not None:
                await mapping.async_close()

            servers, source_address = await asyncio.gather(
                self._rendezvous.async_get_servers(target_id),
                self._source_address_resolver(host),
            )
            mapping_kwargs: dict[str, Any] = {
                "target_id": target_id,
                "identity": self._identity,
                "local_addresses": (source_address,),
            }
            if auth_password := self._auth_passwords.get(host):
                mapping_kwargs["auth_password"] = auth_password
            mapping = await self._mapping_opener(
                servers,
                **mapping_kwargs,
            )
            self._mappings[host] = mapping
            return mapping

    async def async_get_stable_mapping(self, host: str) -> OrbwebStablePort:
        """Return a persistent endpoint backed by renewable Orbweb mappings."""
        if self._closed:
            raise RuntimeError("Orbweb mapping pool is closed")
        try:
            lock = self._stable_locks[host]
        except KeyError as err:
            raise KeyError("Camera has no Orbweb stream identity") from err

        async with lock:
            if self._closed:
                raise RuntimeError("Orbweb mapping pool is closed")
            stable_port = self._stable_ports.get(host)
            if stable_port is not None and not stable_port.is_closed:
                return stable_port
            stable_port = OrbwebStablePort(partial(self._async_connect_upstream, host))
            await stable_port.async_start()
            self._stable_ports[host] = stable_port
            return stable_port

    async def _async_connect_upstream(
        self, host: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect a stable client to a current transport, retrying once."""
        for attempt in range(2):
            mapping = await self.async_get_mapping(host)
            try:
                return await asyncio.open_connection(mapping.host, mapping.port)
            except OSError:
                await mapping.async_close()
                if attempt:
                    raise
        raise RuntimeError("Orbweb upstream retry exhausted")

    async def async_close(self) -> None:
        """Close every owned mapping and reject later stream requests."""
        if self._closed:
            return
        self._closed = True
        stable_ports = tuple(self._stable_ports.values())
        mappings = tuple(self._mappings.values())
        self._stable_ports.clear()
        self._mappings.clear()
        await asyncio.gather(
            *(stable_port.async_close() for stable_port in stable_ports),
            *(mapping.async_close() for mapping in mappings),
            return_exceptions=True,
        )


async def async_source_ipv4_address(remote_host: str) -> str:
    """Resolve the local IPv4 selected by the kernel route to a camera."""
    return await asyncio.to_thread(_source_ipv4_address, remote_host)


def _source_ipv4_address(remote_host: str) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect selects a route without transmitting an application packet.
        sock.connect((remote_host, 9))
        return str(sock.getsockname()[0])
    finally:
        sock.close()
