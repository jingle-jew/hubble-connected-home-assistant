"""Capture-verified Orbweb direct rendezvous messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address

from .framing import BasePacket, OrbwebCommand, OrbwebProtocolError

MAX_DIRECT_SIGNAL_PAYLOAD = 4096


@dataclass(frozen=True, slots=True, repr=False)
class DirectCandidate:
    """One TCP endpoint advertised through direct rendezvous."""

    address: str
    port: int

    def __post_init__(self) -> None:
        try:
            address = str(IPv4Address(self.address))
        except ValueError as err:
            raise ValueError("Direct candidate address must be IPv4") from err
        if not 1 <= self.port <= 0xFFFF:
            raise ValueError("Direct candidate port must be between 1 and 65535")
        object.__setattr__(self, "address", address)

    def wire_value(self) -> str:
        """Return the native ``IPv4:port`` representation."""
        return f"{self.address}:{self.port}"


@dataclass(frozen=True, slots=True, repr=False)
class DirectAddressRegistrationRequest:
    """Native command-``0xc8`` listener address registration."""

    client_id: str = field(repr=False)
    candidates: tuple[DirectCandidate, ...]

    def packet(self) -> BasePacket:
        """Serialize ``CLIENT_ID;IP:PORT...;NUL``."""
        return _request_packet(
            OrbwebCommand.CONN_REG_ADDR_REQ,
            (_validate_identity(self.client_id, "client_id"),),
            self.candidates,
        )


@dataclass(frozen=True, slots=True, repr=False)
class DirectConnectionRequest:
    """Native command-``0xcb`` peer connection request."""

    peer_id: str = field(repr=False)
    client_id: str = field(repr=False)
    candidates: tuple[DirectCandidate, ...]

    def packet(self) -> BasePacket:
        """Serialize ``PEER_ID;CLIENT_ID;IP:PORT...;NUL``."""
        return _request_packet(
            OrbwebCommand.CONN_DIR_CONN_REQ,
            (
                _validate_identity(self.peer_id, "peer_id"),
                _validate_identity(self.client_id, "client_id"),
            ),
            self.candidates,
        )


@dataclass(frozen=True, slots=True, repr=False)
class DirectConnectionForward:
    """Peer identity and endpoints forwarded by command ``0xcc``."""

    peer_id: str = field(repr=False)
    client_id: str = field(repr=False)
    candidates: tuple[DirectCandidate, ...]


def parse_direct_connection_forward(
    packet: BasePacket,
    *,
    expected_client_id: str | None = None,
) -> DirectConnectionForward:
    """Parse a forwarded direct request and bind its local identity."""
    if packet.command != OrbwebCommand.CONN_DIR_CONN_FORWARD:
        raise OrbwebProtocolError(
            "Expected Orbweb command "
            f"0x{OrbwebCommand.CONN_DIR_CONN_FORWARD:x}; "
            f"received 0x{packet.command:x}"
        )
    body = _decode_direct_ascii(packet.payload, allow_double_nul=True)
    fields = body.split(";")
    if len(fields) < 3:
        raise OrbwebProtocolError("Direct forward has too few fields")

    peer_id = _parse_identity(fields[0], "peer_id")
    client_id = _parse_identity(fields[1], "client_id")
    try:
        candidates = tuple(_parse_candidate(value) for value in fields[2:])
    except ValueError as err:
        raise OrbwebProtocolError("Direct forward has an invalid candidate") from err
    if not candidates:
        raise OrbwebProtocolError("Direct forward has no candidates")
    if expected_client_id is not None and client_id != expected_client_id:
        raise OrbwebProtocolError("Direct forward client ID does not match")

    return DirectConnectionForward(peer_id, client_id, candidates)


def _request_packet(
    command: OrbwebCommand,
    identities: tuple[str, ...],
    candidates: tuple[DirectCandidate, ...],
) -> BasePacket:
    if not candidates:
        raise ValueError("At least one direct candidate is required")
    fields = identities + tuple(item.wire_value() for item in candidates)
    payload = ";".join(fields).encode("ascii") + b"\0"
    if len(payload) > MAX_DIRECT_SIGNAL_PAYLOAD:
        raise ValueError("Direct rendezvous payload exceeds the safety limit")
    return BasePacket(command, payload)


def _validate_identity(value: str, field_name: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError(f"{field_name} must contain only ASCII") from err
    if not encoded or any(separator in value for separator in ("\0", ";")):
        raise ValueError(f"{field_name} contains an invalid separator")
    return value


def _parse_identity(value: str, field_name: str) -> str:
    try:
        return _validate_identity(value, field_name)
    except ValueError as err:
        raise OrbwebProtocolError(f"Direct forward {field_name} is invalid") from err


def _parse_candidate(value: str) -> DirectCandidate:
    address, separator, port = value.rpartition(":")
    if not separator or not port.isdigit():
        raise ValueError("Invalid direct candidate boundary")
    return DirectCandidate(address, int(port, 10))


def _decode_direct_ascii(payload: bytes, *, allow_double_nul: bool) -> str:
    trailing_nuls = len(payload) - len(payload.rstrip(b"\0"))
    allowed = {1, 2} if allow_double_nul else {1}
    if trailing_nuls not in allowed:
        raise OrbwebProtocolError("Direct payload has invalid NUL termination")
    body = payload[:-trailing_nuls]
    if b"\0" in body:
        raise OrbwebProtocolError("Direct payload contains an embedded NUL")
    try:
        return body.decode("ascii")
    except UnicodeDecodeError as err:
        raise OrbwebProtocolError("Direct payload is not ASCII") from err
