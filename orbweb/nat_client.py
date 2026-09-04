"""Asynchronous Orbweb TCP NAT detection state machine."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import Any, Protocol

from .framing import (
    OrbwebCommand,
    OrbwebProtocolError,
    async_read_packet,
    async_write_packet,
)
from .nat import (
    NAT_PROBE_ACK_COMMANDS,
    NatInfoRequest,
    NatInfoResponse,
    NatProbeRequest,
    NatTypeQuery,
    parse_nat_info_response,
)
from .responses import ServerUtilityResponse

NAT_CONTROL_PORT = 10240
NAT_PROBE_PORTS = (10241, 10242, 10241, 10242)
DEFAULT_NAT_TIMEOUT = 10.0


class _Writer(Protocol):
    """Small StreamWriter surface used by the state machine and tests."""

    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def get_extra_info(self, name: str, default: Any = None) -> Any: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


OpenConnection = Callable[
    ...,
    Awaitable[tuple[asyncio.StreamReader, _Writer]],
]


@dataclass(frozen=True, slots=True, repr=False)
class NatTraversalPlan:
    """Capture-verified server order for one NAT worker."""

    primary_server: str = field(repr=False)
    secondary_server: str = field(repr=False)

    @classmethod
    def from_server_utilities(
        cls,
        utilities: ServerUtilityResponse,
    ) -> NatTraversalPlan:
        """Select the native first and second server-utility slots."""
        primary = _validate_ipv4(utilities.addresses[0], "primary NAT server")
        secondary = _validate_ipv4(utilities.addresses[1], "secondary NAT server")
        return cls(primary, secondary)

    @property
    def control_endpoint(self) -> tuple[str, int]:
        """Return the long-lived initial/query endpoint."""
        return self.primary_server, NAT_CONTROL_PORT

    @property
    def probe_endpoints(self) -> tuple[tuple[str, int], ...]:
        """Return the four native probe destinations in order."""
        return (
            (self.primary_server, NAT_PROBE_PORTS[0]),
            (self.primary_server, NAT_PROBE_PORTS[1]),
            (self.secondary_server, NAT_PROBE_PORTS[2]),
            (self.secondary_server, NAT_PROBE_PORTS[3]),
        )


@dataclass(frozen=True, slots=True, repr=False)
class NatTraversalResult:
    """Initial and final server observations for one local interface."""

    initial: NatInfoResponse
    final: NatInfoResponse
    probe_local_port: int | None


async def async_detect_nat(
    *,
    client_id: str,
    local_address: str,
    utilities: ServerUtilityResponse,
    timeout: float = DEFAULT_NAT_TIMEOUT,
    open_connection: OpenConnection = asyncio.open_connection,
) -> NatTraversalResult:
    """Run one native-compatible TCP NAT worker.

    The connection factory is injectable so the complete transition sequence
    can be verified without contacting Orbweb. Callers decide which local
    interfaces receive a worker and whether the result is used for P2P.
    """
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    normalized_local_address = _validate_ipv4(
        local_address,
        "local_address",
    )
    plan = NatTraversalPlan.from_server_utilities(utilities)

    async with asyncio.timeout(timeout):
        control_reader, control_writer = await _open(
            open_connection,
            plan.control_endpoint,
            (normalized_local_address, 0),
        )
        try:
            control_local_port = _writer_local_port(control_writer)
            await async_write_packet(
                control_writer,
                NatInfoRequest(
                    client_id,
                    normalized_local_address,
                    control_local_port,
                ).packet(),
            )
            initial = parse_nat_info_response(
                await async_read_packet(control_reader),
                expected_client_id=client_id,
            )
            if initial.command != OrbwebCommand.NAT_INFO_RSP:
                raise OrbwebProtocolError(
                    "Initial NAT exchange did not return command 0x259"
                )
            if initial.behind == 0:
                return NatTraversalResult(initial, initial, None)

            probe_local_port = await _run_probes(
                client_id=client_id,
                local_address=normalized_local_address,
                plan=plan,
                open_connection=open_connection,
            )
            await async_write_packet(
                control_writer,
                NatTypeQuery(client_id).packet(),
            )
            final = parse_nat_info_response(
                await async_read_packet(control_reader),
                expected_client_id=client_id,
            )
            if final.command != OrbwebCommand.NAT_TYPE_RESULT:
                raise OrbwebProtocolError(
                    "Final NAT exchange did not return command 0x268"
                )
            return NatTraversalResult(initial, final, probe_local_port)
        finally:
            await _close_writer(control_writer)


async def _run_probes(
    *,
    client_id: str,
    local_address: str,
    plan: NatTraversalPlan,
    open_connection: OpenConnection,
) -> int:
    probe_local_port = 0
    for probe_index, endpoint in enumerate(plan.probe_endpoints):
        reader, writer = await _open(
            open_connection,
            endpoint,
            (local_address, probe_local_port),
        )
        try:
            actual_local_port = _writer_local_port(writer)
            if probe_local_port == 0:
                probe_local_port = actual_local_port
            elif actual_local_port != probe_local_port:
                raise OrbwebProtocolError(
                    "NAT probes did not preserve their local source port"
                )
            await async_write_packet(
                writer,
                NatProbeRequest(client_id, probe_index).packet(),
            )
            response = await async_read_packet(reader)
            expected_command = NAT_PROBE_ACK_COMMANDS[probe_index]
            if response.command != expected_command or response.payload:
                raise OrbwebProtocolError(
                    f"NAT probe {probe_index + 1} returned an invalid acknowledgement"
                )
        finally:
            await _close_writer(writer)
    return probe_local_port


async def _open(
    open_connection: OpenConnection,
    endpoint: tuple[str, int],
    local_addr: tuple[str, int],
) -> tuple[asyncio.StreamReader, _Writer]:
    return await open_connection(
        endpoint[0],
        endpoint[1],
        local_addr=local_addr,
    )


def _writer_local_port(writer: _Writer) -> int:
    sockname = writer.get_extra_info("sockname")
    if (
        not isinstance(sockname, tuple)
        or len(sockname) < 2
        or not isinstance(sockname[1], int)
        or not 1 <= sockname[1] <= 0xFFFF
    ):
        raise OrbwebProtocolError("NAT socket has no valid local port")
    return sockname[1]


async def _close_writer(writer: _Writer) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (BrokenPipeError, ConnectionError):
        pass


def _validate_ipv4(value: str, field_name: str) -> str:
    try:
        return str(IPv4Address(value))
    except ValueError as err:
        raise ValueError(f"{field_name} must be an IPv4 address") from err
