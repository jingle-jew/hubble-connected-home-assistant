"""Capture-verified Orbweb NAT message structures.

This module intentionally stops at serialization and parsing. Socket creation,
probe timing and route selection belong to the future NAT state machine.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from ipaddress import IPv4Address

from .framing import BasePacket, OrbwebCommand, OrbwebProtocolError

NAT_CLIENT_ID_LENGTH = 255
NAT_LOCAL_ADDRESS_LENGTH = 33
NAT_PUBLIC_ADDRESS_LENGTH = 32
NAT_INFO_REQUEST_LENGTH = 290
NAT_INFO_RESPONSE_LENGTH = 340
UNKNOWN_NAT_VALUE = 0xFFFFFFFF

NAT_PROBE_REQUEST_COMMANDS = (
    OrbwebCommand.NAT_PROBE_1_REQ,
    OrbwebCommand.NAT_PROBE_2_REQ,
    OrbwebCommand.NAT_PROBE_3_REQ,
    OrbwebCommand.NAT_PROBE_4_REQ,
)
NAT_PROBE_ACK_COMMANDS = (
    OrbwebCommand.NAT_PROBE_10241_ACK,
    OrbwebCommand.NAT_PROBE_10242_ACK,
    OrbwebCommand.NAT_PROBE_ACK_3,
    OrbwebCommand.NAT_PROBE_ACK_4,
)


def build_nat_client_id(
    rendezvous_client_id: str,
    local_address_index: int,
    detector_number: int,
) -> str:
    """Append the native ``_%d%d`` NAT-worker suffix to a client ID."""
    if local_address_index < 0 or detector_number < 0:
        raise ValueError("NAT client-ID indices must not be negative")
    nat_client_id = f"{rendezvous_client_id}_{local_address_index}{detector_number}"
    _encode_ascii_field(
        nat_client_id,
        NAT_CLIENT_ID_LENGTH,
        "nat_client_id",
    )
    return nat_client_id


@dataclass(frozen=True, slots=True, repr=False)
class NatInfoRequest:
    """Initial command-``0x258`` NAT candidate request."""

    client_id: str = field(repr=False)
    local_address: str
    local_port: int

    def packet(self) -> BasePacket:
        """Serialize the capture-confirmed 290-byte request."""
        payload = bytearray(NAT_INFO_REQUEST_LENGTH)
        payload[0:255] = _encode_ascii_field(
            self.client_id,
            NAT_CLIENT_ID_LENGTH,
            "client_id",
        )
        payload[255:288] = _encode_ipv4_field(
            self.local_address,
            NAT_LOCAL_ADDRESS_LENGTH,
            "local_address",
        )
        struct.pack_into("<H", payload, 288, _validate_port(self.local_port))
        return BasePacket(OrbwebCommand.NAT_INFO_REQ, bytes(payload))


@dataclass(frozen=True, slots=True, repr=False)
class NatTypeQuery:
    """Command-``0x267`` query carrying the NAT worker identity verbatim."""

    client_id: str = field(repr=False)

    def packet(self) -> BasePacket:
        """Serialize the variable-length query without a trailing NUL."""
        payload = _encode_ascii(self.client_id, "client_id")
        if not payload or len(payload) >= NAT_CLIENT_ID_LENGTH:
            raise ValueError("client_id must contain 1 to 254 ASCII bytes")
        return BasePacket(OrbwebCommand.NAT_TYPE_QUERY, payload)


@dataclass(frozen=True, slots=True, repr=False)
class NatProbeRequest:
    """One of the four ordered NAT probe requests."""

    client_id: str = field(repr=False)
    probe_index: int

    def packet(self) -> BasePacket:
        """Serialize a probe using the native four-entry command table."""
        if not 0 <= self.probe_index < len(NAT_PROBE_REQUEST_COMMANDS):
            raise ValueError("probe_index must be between 0 and 3")
        payload = _encode_ascii(self.client_id, "client_id")
        if not payload or len(payload) >= NAT_CLIENT_ID_LENGTH:
            raise ValueError("client_id must contain 1 to 254 ASCII bytes")
        return BasePacket(NAT_PROBE_REQUEST_COMMANDS[self.probe_index], payload)


@dataclass(frozen=True, slots=True, repr=False)
class NatInfoResponse:
    """Candidate addresses and flags returned in commands 0x259/0x268."""

    command: OrbwebCommand
    client_id: str = field(repr=False)
    local_address: str
    local_port: int
    nat_type: int
    behind: int
    public_address: str
    public_port: int

    @property
    def nat_type_is_known(self) -> bool:
        """Return whether the server replaced its uint32 sentinel."""
        return self.nat_type != UNKNOWN_NAT_VALUE

    @property
    def behind_is_known(self) -> bool:
        """Return whether the server replaced its uint32 sentinel."""
        return self.behind != UNKNOWN_NAT_VALUE


def parse_nat_info_response(
    packet: BasePacket,
    *,
    expected_client_id: str | None = None,
) -> NatInfoResponse:
    """Parse and bind one fixed-size initial or final NAT response."""
    try:
        command = OrbwebCommand(packet.command)
    except ValueError as err:
        raise OrbwebProtocolError(
            f"Unexpected NAT response command 0x{packet.command:x}"
        ) from err
    if command not in (
        OrbwebCommand.NAT_INFO_RSP,
        OrbwebCommand.NAT_TYPE_RESULT,
    ):
        raise OrbwebProtocolError(
            f"Unexpected NAT response command 0x{packet.command:x}"
        )
    if len(packet.payload) != NAT_INFO_RESPONSE_LENGTH:
        raise OrbwebProtocolError(
            f"NAT response has {len(packet.payload)} bytes; "
            f"expected {NAT_INFO_RESPONSE_LENGTH}"
        )

    client_id = _decode_ascii_field(packet.payload[0:255], "client_id")
    local_address = _decode_ipv4_field(packet.payload[255:288], "local_address")
    local_port = struct.unpack_from("<H", packet.payload, 288)[0]
    if any(packet.payload[290:292]):
        raise OrbwebProtocolError("NAT response has non-zero local padding")
    nat_type, behind = struct.unpack_from("<II", packet.payload, 292)
    if any(packet.payload[300:304]):
        raise OrbwebProtocolError("NAT response has non-zero flag padding")
    public_address = _decode_ipv4_field(packet.payload[304:336], "public_address")
    public_port = struct.unpack_from("<H", packet.payload, 336)[0]
    if any(packet.payload[338:340]):
        raise OrbwebProtocolError("NAT response has non-zero public padding")
    if expected_client_id is not None and client_id != expected_client_id:
        raise OrbwebProtocolError("NAT response client ID does not match")
    if local_port == 0 or public_port == 0:
        raise OrbwebProtocolError("NAT response contains a zero port")

    return NatInfoResponse(
        command=command,
        client_id=client_id,
        local_address=local_address,
        local_port=local_port,
        nat_type=nat_type,
        behind=behind,
        public_address=public_address,
        public_port=public_port,
    )


def _encode_ascii(value: str, field_name: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError(f"{field_name} must contain only ASCII") from err
    if b"\0" in encoded:
        raise ValueError(f"{field_name} must not contain NUL")
    return encoded


def _encode_ascii_field(value: str, size: int, field_name: str) -> bytes:
    encoded = _encode_ascii(value, field_name)
    if not encoded or len(encoded) >= size:
        raise ValueError(f"{field_name} must contain 1 to {size - 1} ASCII bytes")
    return encoded.ljust(size, b"\0")


def _encode_ipv4_field(value: str, size: int, field_name: str) -> bytes:
    try:
        normalized = str(IPv4Address(value))
    except ValueError as err:
        raise ValueError(f"{field_name} must be an IPv4 address") from err
    return _encode_ascii_field(normalized, size, field_name)


def _decode_ascii_field(data: bytes, field_name: str) -> str:
    value, separator, padding = data.partition(b"\0")
    if not value:
        raise OrbwebProtocolError(f"{field_name} is empty")
    if not separator or any(padding):
        raise OrbwebProtocolError(f"{field_name} has invalid padding")
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as err:
        raise OrbwebProtocolError(f"{field_name} is not ASCII") from err


def _decode_ipv4_field(data: bytes, field_name: str) -> str:
    value = _decode_ascii_field(data, field_name)
    try:
        return str(IPv4Address(value))
    except ValueError as err:
        raise OrbwebProtocolError(f"{field_name} is not IPv4") from err


def _validate_port(port: int) -> int:
    if not 1 <= port <= 0xFFFF:
        raise ValueError("port must be between 1 and 65535")
    return port
