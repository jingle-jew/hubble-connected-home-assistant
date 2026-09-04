"""Capture-verified Orbweb TCP shunt registration messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address

from .framing import BasePacket, OrbwebCommand, OrbwebProtocolError

MAX_SHUNT_REGISTRATION_PAYLOAD = 510


@dataclass(frozen=True, slots=True, repr=False)
class ShuntCandidate:
    """One public TCP candidate exchanged through the shunt server."""

    address: str
    port: int

    def __post_init__(self) -> None:
        try:
            address = str(IPv4Address(self.address))
        except ValueError as err:
            raise ValueError("Shunt candidate address must be IPv4") from err
        if not 1 <= self.port <= 0xFFFF:
            raise ValueError("Shunt candidate port must be between 1 and 65535")
        object.__setattr__(self, "address", address)

    def wire_value(self) -> str:
        """Return the native ``IPv4:port`` representation."""
        return f"{self.address}:{self.port}"


@dataclass(frozen=True, slots=True, repr=False)
class ShuntRegistrationRequest:
    """Native command-``0x2bc`` registration request."""

    client_id: str = field(repr=False)
    nat_types: tuple[int, ...]
    candidates: tuple[ShuntCandidate, ...]
    peer_id: str | None = field(default=None, repr=False)

    def packet(self) -> BasePacket:
        """Serialize the semicolon-delimited, NUL-terminated request."""
        client_id = _validate_identity(self.client_id, "client_id")
        if not self.candidates:
            raise ValueError("At least one shunt candidate is required")
        if len(self.nat_types) != len(self.candidates):
            raise ValueError("NAT types and shunt candidates must have equal counts")

        nat_types = ":".join(_encode_nat_type(value) for value in self.nat_types)
        candidates = (
            ";".join(candidate.wire_value() for candidate in self.candidates) + ";"
        )
        if self.peer_id is None:
            body = f"0;{client_id};{len(self.candidates)};{nat_types};{candidates}"
        else:
            peer_id = _validate_identity(self.peer_id, "peer_id")
            body = (
                f"1;{client_id};{peer_id};{len(self.candidates)};"
                f"{nat_types};{candidates}"
            )

        payload = body.encode("ascii") + b"\0"
        if len(payload) > MAX_SHUNT_REGISTRATION_PAYLOAD:
            raise ValueError("Shunt registration exceeds the native 510-byte buffer")
        return BasePacket(OrbwebCommand.TCP_SHUNT_REG_REQ, payload)


@dataclass(frozen=True, slots=True, repr=False)
class ShuntRegistrationResponse:
    """Peer identity and candidates returned by command ``0x2bd``."""

    nat_types: tuple[int, ...]
    peer_id: str = field(repr=False)
    client_id: str = field(repr=False)
    candidates: tuple[ShuntCandidate, ...]


def parse_shunt_registration_response(
    packet: BasePacket,
    *,
    expected_client_id: str | None = None,
) -> ShuntRegistrationResponse:
    """Parse a successful shunt response and bind its local identity."""
    if packet.command != OrbwebCommand.TCP_SHUNT_REG_RSP:
        raise OrbwebProtocolError(
            "Expected Orbweb command "
            f"0x{OrbwebCommand.TCP_SHUNT_REG_RSP:x}; "
            f"received 0x{packet.command:x}"
        )
    body = _decode_nul_terminated_ascii(packet.payload)
    fields = body.split(";")
    if len(fields) < 5 or fields[-1] != "":
        raise OrbwebProtocolError("Shunt response has invalid field boundaries")

    nat_types = _parse_nat_types(fields[0])
    peer_id = _parse_identity(fields[1], "peer_id")
    client_id = _parse_identity(fields[2], "client_id")
    try:
        candidates = tuple(_parse_candidate(value) for value in fields[3:-1])
    except ValueError as err:
        raise OrbwebProtocolError("Shunt response has an invalid candidate") from err
    if not candidates or len(candidates) != len(nat_types):
        raise OrbwebProtocolError(
            "Shunt response NAT types and candidates have unequal counts"
        )
    if expected_client_id is not None and client_id != expected_client_id:
        raise OrbwebProtocolError("Shunt response client ID does not match")

    return ShuntRegistrationResponse(
        nat_types=nat_types,
        peer_id=peer_id,
        client_id=client_id,
        candidates=candidates,
    )


def _validate_identity(value: str, field_name: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError(f"{field_name} must contain only ASCII") from err
    if not encoded or any(separator in value for separator in ("\0", ";", ":")):
        raise ValueError(f"{field_name} contains an invalid separator")
    return value


def _parse_identity(value: str, field_name: str) -> str:
    try:
        return _validate_identity(value, field_name)
    except ValueError as err:
        raise OrbwebProtocolError(f"Shunt response {field_name} is invalid") from err


def _encode_nat_type(value: int) -> str:
    if not -(2**31) <= value < 2**31:
        raise ValueError("NAT type must fit a signed 32-bit integer")
    return str(value)


def _parse_nat_types(value: str) -> tuple[int, ...]:
    fields = value.split(":")
    if not fields or any(not field for field in fields):
        raise OrbwebProtocolError("Shunt response has no NAT types")
    try:
        values = tuple(int(field, 10) for field in fields)
        for item in values:
            _encode_nat_type(item)
    except ValueError as err:
        raise OrbwebProtocolError("Shunt response has an invalid NAT type") from err
    return values


def _parse_candidate(value: str) -> ShuntCandidate:
    address, separator, port = value.rpartition(":")
    if not separator or not port.isdigit():
        raise ValueError("Invalid candidate boundary")
    return ShuntCandidate(address, int(port, 10))


def _decode_nul_terminated_ascii(payload: bytes) -> str:
    if not payload or payload[-1:] != b"\0" or b"\0" in payload[:-1]:
        raise OrbwebProtocolError("Shunt response is not exactly NUL-terminated")
    try:
        return payload[:-1].decode("ascii")
    except UnicodeDecodeError as err:
        raise OrbwebProtocolError("Shunt response is not ASCII") from err
