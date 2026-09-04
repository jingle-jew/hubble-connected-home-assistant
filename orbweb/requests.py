"""Rendezvous request layouts confirmed against the Android capture."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .framing import BasePacket, OrbwebCommand

HOST_NIC_LENGTH = 0x20C
HOST_CONNECTION_LENGTH = 0x322
SDK_VERSION = "4.3.17"


def _ascii_field(value: str, size: int, field_name: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError(f"{field_name} must contain only ASCII") from err
    if not encoded:
        raise ValueError(f"{field_name} must not be empty")
    if len(encoded) > size:
        raise ValueError(f"{field_name} has {len(encoded)} bytes; maximum is {size}")
    return encoded


def _unsigned_int(value: int, bits: int, field_name: str) -> int:
    if not 0 <= value < 1 << bits:
        raise ValueError(f"{field_name} does not fit uint{bits}")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ClientRegistrationRequest:
    """Variable-length command-0x67 client registration."""

    client_id: str

    def packet(self) -> BasePacket:
        """Build the registration packet without a trailing NUL."""
        payload = _ascii_field(self.client_id, 255, "client_id")
        return BasePacket(OrbwebCommand.CONN_REG_CLIENT_REQ, payload)


@dataclass(frozen=True, slots=True, repr=False)
class HostNicRequest:
    """Fixed 524-byte command-0x77 host capability request."""

    target_id: str
    client_id: str
    local_ip_count: int
    connection_type_config: int
    timeout_ms: int
    sdk_version: str = SDK_VERSION

    def packet(self) -> BasePacket:
        """Build the exact zero-filled native structure."""
        payload = bytearray(HOST_NIC_LENGTH)
        payload[0:128] = _padded_field(self.target_id, 128, "target_id")
        payload[128:255] = _padded_field(self.sdk_version, 127, "sdk_version")
        payload[255:512] = _padded_field(self.client_id, 257, "client_id")
        struct.pack_into(
            "<III",
            payload,
            512,
            _unsigned_int(self.local_ip_count, 32, "local_ip_count"),
            _unsigned_int(self.connection_type_config, 32, "connection_type_config"),
            _unsigned_int(self.timeout_ms, 32, "timeout_ms"),
        )
        return BasePacket(OrbwebCommand.CONN_REG_NIC_REQ, bytes(payload))


@dataclass(frozen=True, slots=True, repr=False)
class HostConnectionRequest:
    """Fixed 802-byte command-0x6a selected-host request."""

    target_id: str
    client_id: str
    client_token: str
    secondary_server: str
    secondary_server_port: int

    def packet(self) -> BasePacket:
        """Build the selected-host request confirmed in the PCAP."""
        payload = bytearray(HOST_CONNECTION_LENGTH)
        payload[0:258] = _padded_field(self.target_id, 258, "target_id")
        payload[258:513] = _padded_field(self.client_id, 255, "client_id")
        payload[513:768] = _padded_field(self.client_token, 255, "client_token")
        payload[768:800] = _padded_field(self.secondary_server, 32, "secondary_server")
        struct.pack_into(
            "<H",
            payload,
            800,
            _unsigned_int(self.secondary_server_port, 16, "secondary_server_port"),
        )
        return BasePacket(OrbwebCommand.CONN_HOST_REQ, bytes(payload))


def _padded_field(value: str, size: int, field_name: str) -> bytes:
    encoded = _ascii_field(value, size, field_name)
    return encoded.ljust(size, b"\0")
