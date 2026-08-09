"""Strict parsers for the captured Orbweb rendezvous responses."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .framing import BasePacket, OrbwebCommand, OrbwebProtocolError

SERVER_UTILITY_RESPONSE_LENGTH = 5 * 32
HOST_NIC_RESPONSE_LENGTH = 0x20C


@dataclass(frozen=True, slots=True)
class ServerUtilityResponse:
    """Five server-address slots returned by command ``0x68``.

    Their native parameter names are not available, so the slots intentionally
    remain positional until the NAT state machine establishes their roles.
    """

    addresses: tuple[str, str, str, str, str]


@dataclass(frozen=True, slots=True, repr=False)
class HostNicResponse:
    """Fixed command-``0x78`` host capability response."""

    target_id: str = field(repr=False)
    sdk_version: str
    client_id: str = field(repr=False)
    host_nic_count: int
    connection_type_config: int
    reserved: int

    @property
    def uses_qtcp(self) -> bool:
        """Mirror the native major-version transport selection."""
        major, _, _ = _version_triplet(self.sdk_version)
        return major > 3


def parse_server_utility_response(packet: BasePacket) -> ServerUtilityResponse:
    """Parse the five fixed-width address slots in command ``0x68``."""
    _expect_command(packet, OrbwebCommand.CONN_REG_CLIENT_RSP)
    if len(packet.payload) != SERVER_UTILITY_RESPONSE_LENGTH:
        raise OrbwebProtocolError(
            "Server-utility response has "
            f"{len(packet.payload)} bytes; expected {SERVER_UTILITY_RESPONSE_LENGTH}"
        )
    addresses = tuple(
        _padded_ascii(packet.payload[offset : offset + 32], "server_address")
        for offset in range(0, SERVER_UTILITY_RESPONSE_LENGTH, 32)
    )
    return ServerUtilityResponse(
        addresses=(
            addresses[0],
            addresses[1],
            addresses[2],
            addresses[3],
            addresses[4],
        )
    )


def parse_host_nic_response(
    packet: BasePacket,
    *,
    expected_target_id: str | None = None,
    expected_client_id: str | None = None,
) -> HostNicResponse:
    """Parse command ``0x78`` and optionally bind it to the request IDs."""
    _expect_command(packet, OrbwebCommand.CONN_REG_NIC_RSP)
    if len(packet.payload) != HOST_NIC_RESPONSE_LENGTH:
        raise OrbwebProtocolError(
            f"Host-NIC response has {len(packet.payload)} bytes; "
            f"expected {HOST_NIC_RESPONSE_LENGTH}"
        )

    target_id = _padded_ascii(packet.payload[0:128], "target_id")
    sdk_version = _padded_ascii(packet.payload[128:255], "sdk_version")
    client_id = _padded_ascii(packet.payload[255:512], "client_id")
    host_nic_count, connection_type_config, reserved = struct.unpack_from(
        "<III", packet.payload, 512
    )
    _version_triplet(sdk_version)

    if expected_target_id is not None and target_id != expected_target_id:
        raise OrbwebProtocolError("Host-NIC response target ID does not match")
    if expected_client_id is not None and client_id != expected_client_id:
        raise OrbwebProtocolError("Host-NIC response client ID does not match")
    if host_nic_count == 0:
        raise OrbwebProtocolError("Host-NIC response reports no network interface")

    return HostNicResponse(
        target_id=target_id,
        sdk_version=sdk_version,
        client_id=client_id,
        host_nic_count=host_nic_count,
        connection_type_config=connection_type_config,
        reserved=reserved,
    )


def _expect_command(packet: BasePacket, command: OrbwebCommand) -> None:
    if packet.command != command:
        raise OrbwebProtocolError(
            f"Expected Orbweb command 0x{command:x}; received 0x{packet.command:x}"
        )


def _padded_ascii(data: bytes, field_name: str) -> str:
    value, separator, padding = data.partition(b"\0")
    if not value:
        raise OrbwebProtocolError(f"{field_name} is empty")
    if separator and any(padding):
        raise OrbwebProtocolError(f"{field_name} has non-zero padding")
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError as err:
        raise OrbwebProtocolError(f"{field_name} is not ASCII") from err
    if any(character.isspace() or ord(character) < 0x20 for character in decoded):
        raise OrbwebProtocolError(f"{field_name} contains invalid characters")
    return decoded


def _version_triplet(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) < 3 or any(not part.isdigit() for part in parts[:3]):
        raise OrbwebProtocolError("Host SDK version is not numeric x.y.z")
    return int(parts[0]), int(parts[1]), int(parts[2])
