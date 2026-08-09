"""Orbweb TCP rendezvous control exchange.

The exchange stops at the control-plane boundary. It does not start the NAT
workers or expose a media port, so Home Assistant does not invoke it yet.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .framing import (
    BasePacket,
    OrbwebCommand,
    OrbwebProtocolError,
    async_read_packet,
    async_write_packet,
)
from .requests import (
    ClientRegistrationRequest,
    HostConnectionRequest,
    HostNicRequest,
)
from .responses import (
    HostNicResponse,
    ServerUtilityResponse,
    parse_host_nic_response,
    parse_server_utility_response,
)


@dataclass(frozen=True, slots=True)
class RendezvousControlResult:
    """Validated responses required by the later NAT state machine."""

    server_utilities: ServerUtilityResponse
    host_nic: HostNicResponse


class RendezvousControlRejected(OrbwebProtocolError):
    """The rendezvous server rejected one stage of the control exchange."""

    def __init__(self, command: int) -> None:
        super().__init__(f"Rendezvous control rejected by command 0x{command:x}")
        self.command = command


async def async_run_control_exchange(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    target_id: str,
    client_id: str,
    client_token: str,
    secondary_server: str,
    local_ip_count: int,
    secondary_server_port: int = 443,
    connection_type_config: int = 15,
    timeout_ms: int = 2000,
) -> RendezvousControlResult:
    """Run the captured ``0x67``/``0x77``/``0x6a`` control sequence."""
    registration_request = ClientRegistrationRequest(client_id).packet()
    await async_write_packet(
        writer,
        registration_request,
    )
    registration = await async_read_packet(reader)
    _raise_if_rejected(
        registration,
        expected=OrbwebCommand.CONN_REG_CLIENT_RSP,
        rejected=OrbwebCommand.CONN_REG_CLIENT_FAIL,
    )
    server_utilities = parse_server_utility_response(registration)

    await async_write_packet(
        writer,
        HostNicRequest(
            target_id=target_id,
            client_id=client_id,
            local_ip_count=local_ip_count,
            connection_type_config=connection_type_config,
            timeout_ms=timeout_ms,
        ).packet(),
    )
    host_nic_packet = await async_read_packet(reader)
    _raise_if_rejected(
        host_nic_packet,
        expected=OrbwebCommand.CONN_REG_NIC_RSP,
        rejected=OrbwebCommand.CONN_REG_NIC_FAIL,
    )
    host_nic = parse_host_nic_response(
        host_nic_packet,
        expected_target_id=target_id,
        expected_client_id=client_id,
    )

    await async_write_packet(
        writer,
        HostConnectionRequest(
            target_id=target_id,
            client_id=client_id,
            client_token=client_token,
            secondary_server=secondary_server,
            secondary_server_port=secondary_server_port,
        ).packet(),
    )
    host_response = await async_read_packet(reader)
    _raise_if_rejected(
        host_response,
        expected=OrbwebCommand.CONN_HOST_RSP,
        rejected=OrbwebCommand.CONN_HOST_FAIL,
    )
    if host_response.payload:
        raise OrbwebProtocolError("Host response payload must be empty")

    await async_write_packet(
        writer,
        BasePacket(
            OrbwebCommand.CONN_DEREG_REQ,
            registration_request.payload,
        ),
    )
    deregistration = await async_read_packet(reader)
    if deregistration.command != OrbwebCommand.CONN_DEREG_RSP:
        raise OrbwebProtocolError(
            f"Expected deregistration response; received 0x{deregistration.command:x}"
        )
    if deregistration.payload:
        raise OrbwebProtocolError("Deregistration response payload must be empty")

    return RendezvousControlResult(server_utilities, host_nic)


def _raise_if_rejected(
    packet: BasePacket,
    *,
    expected: OrbwebCommand,
    rejected: OrbwebCommand,
) -> None:
    if packet.command == rejected:
        raise RendezvousControlRejected(packet.command)
    if packet.command != expected:
        raise OrbwebProtocolError(
            f"Expected Orbweb command 0x{expected:x}; received 0x{packet.command:x}"
        )
