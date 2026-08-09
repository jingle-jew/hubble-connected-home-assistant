"""Loopback TCP mapping carried by an established Orbweb tunnel."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from ipaddress import IPv4Address
from typing import Any

from .framing import (
    BasePacket,
    OrbwebCommand,
    OrbwebProtocolError,
    async_read_packet,
    async_write_packet,
)
from .tunnel import (
    MappingAnnouncement,
    TunnelCipher,
    TunnelFrame,
    TunnelHeader,
    TunnelOperation,
    TunnelStreamRole,
    decode_tunnel_packet,
    encode_tunnel_packet,
)

DEFAULT_REMOTE_RTSP_PORT = 6667
DEFAULT_KEEPALIVE_INITIAL_DELAY = 2.0
DEFAULT_KEEPALIVE_INTERVAL = 8.0
DEFAULT_STREAM_CHUNK_SIZE = 16 * 1024
MAX_MISSED_KEEPALIVES = 5
MAX_RETIRED_STREAMS = 256

ServerFactory = Callable[..., Awaitable[Any]]

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _MappedStream:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    request_id: int
    local_port: int
    remote_port: int
    send_sequence: int = 0
    receive_sequence: int = 0
    remote_disconnect: bool = False
    task: asyncio.Task[None] | None = None


class OrbwebPortMapping:
    """Expose camera TCP ports through one established Orbweb tunnel."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        cipher: TunnelCipher,
        *,
        remote_port: int,
        keepalive_initial_delay: float,
        keepalive_interval: float,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._cipher = cipher
        self._remote_port = _validate_port(remote_port, "remote")
        self._keepalive_initial_delay = _validate_delay(
            keepalive_initial_delay, "initial keepalive"
        )
        self._keepalive_interval = _validate_delay(
            keepalive_interval, "keepalive interval"
        )
        self._servers: dict[int, Any] = {}
        self._local_host = ""
        self._local_ports: dict[int, int] = {}
        self._streams: dict[int, _MappedStream] = {}
        self._retired_streams: dict[int, tuple[int, int]] = {}
        self._next_request_id = 0
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._missed_keepalives = 0
        self._started_at: float | None = None
        self._sent_keepalives = 0
        self._received_keepalives = 0
        self._sent_tunnel_frames = 0
        self._received_tunnel_frames = 0
        self._opened_remote_ports: dict[int, int] = {}
        self._last_received_frame: tuple[int, int, int] | None = None
        self._closed = False
        self._terminal_error: BaseException | None = None

    def __repr__(self) -> str:
        """Avoid exposing proxied application data in diagnostics."""
        return f"{type(self).__name__}()"

    @property
    def host(self) -> str:
        """Return the loopback address of the local TCP endpoint."""
        return self._local_host

    @property
    def port(self) -> int:
        """Return the selected local TCP port."""
        return self.local_port(self._remote_port)

    @property
    def remote_port(self) -> int:
        """Return the camera-side loopback port represented by this mapping."""
        return self._remote_port

    def local_port(self, remote_port: int) -> int:
        """Return the loopback port representing one camera-side port."""
        try:
            return self._local_ports[_validate_port(remote_port, "remote")]
        except KeyError as err:
            raise KeyError("Remote port is not mapped on this tunnel") from err

    @property
    def is_closed(self) -> bool:
        """Return whether listener and tunnel ownership have ended."""
        return self._closed

    @property
    def terminal_error(self) -> BaseException | None:
        """Return the protocol or transport error that stopped the mapping."""
        return self._terminal_error

    def _start(
        self,
        servers: dict[int, Any],
        host: str,
        local_ports: dict[int, int],
    ) -> None:
        self._servers = servers
        self._local_host = host
        self._local_ports = local_ports
        self._started_at = asyncio.get_running_loop().time()
        self._reader_task = asyncio.create_task(self._async_receive_loop())
        self._keepalive_task = asyncio.create_task(self._async_keepalive_loop())

    def _on_local_client(
        self,
        remote_port: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if self._closed:
            writer.close()
            return
        request_id = self._allocate_request_id()
        stream = _MappedStream(
            reader,
            writer,
            request_id,
            self.local_port(remote_port),
            remote_port,
        )
        self._opened_remote_ports[remote_port] = (
            self._opened_remote_ports.get(remote_port, 0) + 1
        )
        self._streams[request_id] = stream
        stream.task = asyncio.create_task(self._async_forward_local(stream))
        stream.task.add_done_callback(self._local_task_done)

    def _allocate_request_id(self) -> int:
        for _ in range(0x80000000):
            request_id = self._next_request_id
            self._next_request_id = (request_id + 1) & 0x7FFFFFFF
            if request_id not in self._streams:
                self._retired_streams.pop(request_id, None)
                return request_id
        raise RuntimeError("Orbweb tunnel request ID space is exhausted")

    def _local_task_done(self, task: asyncio.Task[None]) -> None:
        if self._closed or task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._fail(error)

    async def _async_forward_local(self, stream: _MappedStream) -> None:
        try:
            await self._async_send_packet(
                MappingAnnouncement(
                    stream.local_port,
                    stream.remote_port,
                    stream.request_id,
                ).packet()
            )
            while data := await stream.reader.read(DEFAULT_STREAM_CHUNK_SIZE):
                header = self._header(stream, stream.send_sequence)
                stream.send_sequence += 1
                await self._async_send_packet(
                    encode_tunnel_packet(header, data, self._cipher)
                )
        finally:
            if not self._closed and not stream.remote_disconnect:
                await self._async_send_control(
                    stream, TunnelOperation.DISCONNECT_REQUEST
                )
            await _async_close_writer(stream.writer)

    async def _async_receive_loop(self) -> None:
        try:
            while not self._closed:
                packet = await async_read_packet(self._reader)
                if packet.command == OrbwebCommand.TUNNEL_KEEPALIVE:
                    if packet.payload:
                        raise OrbwebProtocolError(
                            "Tunnel keepalive payload must be empty"
                        )
                    self._received_keepalives += 1
                    self._missed_keepalives = 0
                    continue
                if packet.command != OrbwebCommand.TUNNEL_DATA:
                    raise OrbwebProtocolError(
                        f"Unexpected established-tunnel command: 0x{packet.command:x}"
                    )
                frame = decode_tunnel_packet(packet, self._cipher)
                self._received_tunnel_frames += 1
                self._last_received_frame = (
                    frame.header.request_id,
                    frame.header.operation,
                    len(frame.payload),
                )
                await self._async_dispatch_frame(frame)
        except asyncio.CancelledError:
            raise
        except BaseException as err:
            if not self._closed:
                self._fail(err)

    async def _async_dispatch_frame(self, frame: TunnelFrame) -> None:
        header = frame.header
        stream = self._streams.get(header.request_id)
        if stream is None:
            retired_ports = self._retired_streams.get(header.request_id)
            if retired_ports is not None:
                local_port, remote_port = retired_ports
                if (
                    header.role == TunnelStreamRole.CONNECTOR
                    and header.local_port == remote_port
                    and header.remote_port == local_port
                    and (header.operation >= 0 or not frame.payload)
                ):
                    # TCP bytes can already be in flight when HA closes a
                    # still-image RTSP request. The retired local socket has
                    # no consumer, so discard only correctly routed late data.
                    return
            raise OrbwebProtocolError(
                f"Tunnel data references unknown request {header.request_id}"
            )
        if (
            header.role != TunnelStreamRole.CONNECTOR
            or header.local_port != stream.remote_port
            or header.remote_port != stream.local_port
        ):
            raise OrbwebProtocolError("Tunnel data does not match its mapping")

        if header.operation >= 0:
            if header.operation != stream.receive_sequence:
                raise OrbwebProtocolError("Tunnel data sequence is not contiguous")
            stream.receive_sequence += 1
            stream.writer.write(frame.payload)
            await stream.writer.drain()
            return

        if frame.payload:
            raise OrbwebProtocolError(
                "Tunnel disconnect control contains application data"
            )
        operation = TunnelOperation(header.operation)
        if operation == TunnelOperation.DISCONNECT_REQUEST:
            stream.remote_disconnect = True
            await self._async_send_control(stream, TunnelOperation.DISCONNECT_RESPONSE)
            await self._async_stop_local_stream(stream, remove=False)
        elif operation == TunnelOperation.DISCONNECT_RESPONSE:
            await self._async_send_control(stream, TunnelOperation.DISCONNECT_CONFIRM)
            await self._async_stop_local_stream(stream, remove=True)
        else:
            await self._async_stop_local_stream(stream, remove=True)

    async def _async_keepalive_loop(self) -> None:
        try:
            await asyncio.sleep(self._keepalive_initial_delay)
            while not self._closed:
                await self._async_send_packet(
                    BasePacket(OrbwebCommand.TUNNEL_KEEPALIVE)
                )
                self._missed_keepalives += 1
                if self._missed_keepalives > MAX_MISSED_KEEPALIVES:
                    raise OrbwebProtocolError("Orbweb tunnel keepalive timed out")
                await asyncio.sleep(self._keepalive_interval)
        except asyncio.CancelledError:
            raise
        except BaseException as err:
            if not self._closed:
                self._fail(err)

    async def _async_send_control(
        self,
        stream: _MappedStream,
        operation: TunnelOperation,
    ) -> None:
        await self._async_send_packet(
            encode_tunnel_packet(
                self._header(stream, operation),
                b"",
                self._cipher,
            )
        )

    async def _async_send_packet(self, packet: BasePacket) -> None:
        async with self._write_lock:
            await async_write_packet(self._writer, packet)
        if packet.command == OrbwebCommand.TUNNEL_KEEPALIVE:
            self._sent_keepalives += 1
        elif packet.command == OrbwebCommand.TUNNEL_DATA:
            self._sent_tunnel_frames += 1

    @staticmethod
    def _header(
        stream: _MappedStream,
        operation: int,
    ) -> TunnelHeader:
        return TunnelHeader(
            stream.request_id,
            int(operation),
            TunnelStreamRole.LISTENER,
            stream.local_port,
            stream.remote_port,
        )

    async def _async_stop_local_stream(
        self,
        stream: _MappedStream,
        *,
        remove: bool,
    ) -> None:
        task = stream.task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _async_close_writer(stream.writer)
        if remove:
            self._streams.pop(stream.request_id, None)
            if len(self._retired_streams) >= MAX_RETIRED_STREAMS:
                self._retired_streams.pop(next(iter(self._retired_streams)))
            self._retired_streams[stream.request_id] = (
                stream.local_port,
                stream.remote_port,
            )

    def _fail(self, error: BaseException) -> None:
        if self._terminal_error is None:
            self._terminal_error = error
            loop_time = asyncio.get_running_loop().time()
            age = loop_time - self._started_at if self._started_at is not None else 0.0
            last_request, last_operation, last_size = self._last_received_frame or (
                None,
                None,
                None,
            )
            opened_ports = ",".join(
                f"{port}:{count}"
                for port, count in sorted(self._opened_remote_ports.items())
            )
            reason = (
                str(error)
                if isinstance(error, OrbwebProtocolError)
                else type(error).__name__
            )
            _LOGGER.warning(
                "Orbweb tunnel mapping stopped: %s reason=%s age=%.1fs port=%s "
                "keepalive_tx=%s keepalive_rx=%s frame_tx=%s frame_rx=%s "
                "active=%s retired=%s opened=%s last_request=%s "
                "last_operation=%s last_size=%s",
                type(error).__name__,
                reason,
                age,
                self._local_ports.get(self._remote_port),
                self._sent_keepalives,
                self._received_keepalives,
                self._sent_tunnel_frames,
                self._received_tunnel_frames,
                len(self._streams),
                len(self._retired_streams),
                opened_ports or "none",
                last_request,
                last_operation,
                last_size,
            )
        asyncio.create_task(self.async_close())

    async def async_close(self) -> None:
        """Close the local endpoint, mapped streams, and owned tunnel socket."""
        if self._closed:
            return
        self._closed = True

        servers = tuple(self._servers.values())
        self._servers.clear()
        for server in servers:
            server.close()

        current = asyncio.current_task()
        background = tuple(
            task
            for task in (self._reader_task, self._keepalive_task)
            if task is not None and task is not current and not task.done()
        )
        for task in background:
            task.cancel()

        streams = tuple(self._streams.values())
        self._streams.clear()
        self._retired_streams.clear()
        for stream in streams:
            stream.remote_disconnect = True
            task = stream.task
            if task is not None and task is not current and not task.done():
                task.cancel()
            stream.writer.close()

        await asyncio.gather(*background, return_exceptions=True)
        await asyncio.gather(
            *(
                stream.task
                for stream in streams
                if stream.task is not None and stream.task is not current
            ),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(stream.writer.wait_closed() for stream in streams),
            return_exceptions=True,
        )
        # Python 3.13 waits for active clients in Server.wait_closed(). Close
        # the mapped streams first so shutdown cannot wait on its own sockets.
        await asyncio.gather(
            *(server.wait_closed() for server in servers),
            return_exceptions=True,
        )
        await _async_close_writer(self._writer)

    async def __aenter__(self) -> OrbwebPortMapping:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.async_close()


async def async_open_port_mapping(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cipher: TunnelCipher,
    *,
    remote_port: int = DEFAULT_REMOTE_RTSP_PORT,
    additional_remote_ports: tuple[int, ...] = (),
    host: str = "127.0.0.1",
    port: int = 0,
    server_factory: ServerFactory = asyncio.start_server,
    keepalive_initial_delay: float = DEFAULT_KEEPALIVE_INITIAL_DELAY,
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
) -> OrbwebPortMapping:
    """Bind safe loopback endpoints and take ownership of the tunnel socket."""
    normalized_host = _validate_loopback_host(host)
    if not 0 <= port <= 0xFFFF:
        raise ValueError("Local mapping port must be between 0 and 65535")
    remote_ports = (remote_port, *additional_remote_ports)
    normalized_remote_ports = tuple(
        _validate_port(value, "remote") for value in remote_ports
    )
    if len(set(normalized_remote_ports)) != len(normalized_remote_ports):
        raise ValueError("Orbweb remote mapping ports must be unique")
    mapping = OrbwebPortMapping(
        reader,
        writer,
        cipher,
        remote_port=remote_port,
        keepalive_initial_delay=keepalive_initial_delay,
        keepalive_interval=keepalive_interval,
    )
    servers: dict[int, Any] = {}
    local_ports: dict[int, int] = {}
    try:
        for current_remote_port in normalized_remote_ports:
            server = await server_factory(
                partial(mapping._on_local_client, current_remote_port),
                host=normalized_host,
                port=port if current_remote_port == remote_port else 0,
            )
            servers[current_remote_port] = server
            local_ports[current_remote_port] = _server_port(server)
        mapping._start(servers, normalized_host, local_ports)
        return mapping
    except BaseException:
        if not mapping._servers:
            for server in servers.values():
                server.close()
            for server in servers.values():
                await server.wait_closed()
        await mapping.async_close()
        raise


def _server_port(server: Any) -> int:
    sockets = getattr(server, "sockets", None)
    if not sockets:
        raise RuntimeError("Loopback server exposes no listening socket")
    ports = {socket.getsockname()[1] for socket in sockets}
    if len(ports) != 1:
        raise RuntimeError("Loopback server did not bind exactly one TCP port")
    return _validate_port(ports.pop(), "local")


def _validate_loopback_host(host: str) -> str:
    try:
        address = IPv4Address(host)
    except ValueError as err:
        raise ValueError("Local mapping host must be an IPv4 address") from err
    if not address.is_loopback:
        raise ValueError("Local mapping host must be loopback-only")
    return str(address)


def _validate_port(port: int, label: str) -> int:
    if not 1 <= port <= 0xFFFF:
        raise ValueError(f"Orbweb {label} port must be between 1 and 65535")
    return port


def _validate_delay(value: float, label: str) -> float:
    if value <= 0:
        raise ValueError(f"Orbweb {label} delay must be positive")
    return value


async def _async_close_writer(writer: asyncio.StreamWriter) -> None:
    if not writer.is_closing():
        writer.close()
    try:
        await writer.wait_closed()
    except (BrokenPipeError, ConnectionError):
        pass
