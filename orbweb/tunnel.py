"""Encrypted data framing for an established Orbweb tunnel."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from .crypto import AES_BLOCK_SIZE, pkcs7_pad, pkcs7_unpad
from .framing import BasePacket, OrbwebCommand, OrbwebProtocolError

_HEADER = struct.Struct("<iiHHHH")
_MAPPING_ANNOUNCEMENT = struct.Struct("<HHi")
HEADER_LENGTH = _HEADER.size


class TunnelStreamRole(IntEnum):
    """Side that created the proxied socket represented by a data frame."""

    LISTENER = 1
    CONNECTOR = 2


class TunnelOperation(IntEnum):
    """Negative control values carried in the operation/sequence field."""

    DISCONNECT_RESPONSE = -2
    DISCONNECT_CONFIRM = -3
    DISCONNECT_REQUEST = -4


@dataclass(frozen=True, slots=True)
class TunnelHeader:
    """The native 16-byte ReqInfoHeader carried before ciphertext."""

    request_id: int
    operation: int
    role: TunnelStreamRole
    local_port: int
    remote_port: int
    reserved: int = 0

    def encode(self) -> bytes:
        """Encode the header in the little-endian x86_64 wire layout."""
        _validate_header(self)
        try:
            return _HEADER.pack(
                self.request_id,
                self.operation,
                int(self.role),
                self.local_port,
                self.remote_port,
                self.reserved,
            )
        except struct.error as err:
            raise OrbwebProtocolError("Tunnel header value is out of range") from err

    @classmethod
    def decode(cls, raw: bytes) -> TunnelHeader:
        """Decode and strictly validate one native ReqInfoHeader."""
        if len(raw) != HEADER_LENGTH:
            raise OrbwebProtocolError(
                f"Tunnel header has {len(raw)} bytes; expected {HEADER_LENGTH}"
            )
        request_id, operation, role, local_port, remote_port, reserved = _HEADER.unpack(
            raw
        )
        try:
            decoded_role = TunnelStreamRole(role)
        except ValueError as err:
            raise OrbwebProtocolError(f"Unknown tunnel stream role: {role}") from err
        header = cls(
            request_id=request_id,
            operation=operation,
            role=decoded_role,
            local_port=local_port,
            remote_port=remote_port,
            reserved=reserved,
        )
        _validate_header(header)
        return header


@dataclass(frozen=True, slots=True)
class MappingAnnouncement:
    """Command-0x07 notice sent when a local mapping accepts a client."""

    local_port: int
    remote_port: int
    request_id: int

    def packet(self) -> BasePacket:
        """Build the native eight-byte mapping announcement."""
        _validate_port(self.local_port, "local")
        _validate_port(self.remote_port, "remote")
        _validate_request_id(self.request_id)
        return BasePacket(
            OrbwebCommand.TUNNEL_ACCESS,
            _MAPPING_ANNOUNCEMENT.pack(
                self.local_port,
                self.remote_port,
                self.request_id,
            ),
        )

    @classmethod
    def parse(cls, packet: BasePacket) -> MappingAnnouncement:
        """Parse a strict command-0x07 mapping announcement."""
        if packet.command != OrbwebCommand.TUNNEL_ACCESS:
            raise OrbwebProtocolError(
                f"Expected mapping announcement, received 0x{packet.command:x}"
            )
        if len(packet.payload) != _MAPPING_ANNOUNCEMENT.size:
            raise OrbwebProtocolError(
                f"Mapping announcement has {len(packet.payload)} bytes; expected 8"
            )
        local_port, remote_port, request_id = _MAPPING_ANNOUNCEMENT.unpack(
            packet.payload
        )
        result = cls(local_port, remote_port, request_id)
        _validate_port(result.local_port, "local")
        _validate_port(result.remote_port, "remote")
        _validate_request_id(result.request_id)
        return result


@dataclass(frozen=True, slots=True, repr=False)
class TunnelFrame:
    """Decrypted tunnel data without a repr that could expose RTSP headers."""

    header: TunnelHeader
    payload: bytes

    def __repr__(self) -> str:
        """Keep proxied application data out of diagnostics."""
        return f"{type(self).__name__}()"


class TunnelCipher:
    """AES-128-CBC with the native per-frame IV reset rule."""

    __slots__ = ("_iv", "_key")

    def __init__(self, key: bytes, iv: bytes) -> None:
        if len(key) != AES_BLOCK_SIZE:
            raise ValueError("Orbweb AES key must be exactly 16 bytes")
        if len(iv) != AES_BLOCK_SIZE:
            raise ValueError("Orbweb AES IV must be exactly 16 bytes")
        self._key = bytes(key)
        self._iv = bytes(iv)

    def __repr__(self) -> str:
        """Keep negotiated key material out of diagnostics."""
        return f"{type(self).__name__}()"

    def encrypt(self, plaintext: bytes) -> bytes:
        """Pad and encrypt one payload with a fresh native IV context."""
        encryptor = self._context(encrypt=True)
        padded = pkcs7_pad(plaintext)
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt and validate one independently chained payload."""
        if not ciphertext or len(ciphertext) % AES_BLOCK_SIZE:
            raise OrbwebProtocolError(
                "Tunnel ciphertext is empty or not AES-block aligned"
            )
        decryptor = self._context(encrypt=False)
        try:
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            return pkcs7_unpad(padded)
        except ValueError as err:
            raise OrbwebProtocolError("Tunnel ciphertext padding is invalid") from err

    def _context(self, *, encrypt: bool):
        try:
            from cryptography.hazmat.primitives.ciphers import (
                Cipher,
                algorithms,
                modes,
            )
        except ImportError as err:
            raise RuntimeError(
                "Orbweb tunnel encryption requires Home Assistant's "
                "cryptography package"
            ) from err

        cipher = Cipher(algorithms.AES(self._key), modes.CBC(self._iv))
        return cipher.encryptor() if encrypt else cipher.decryptor()


def encode_tunnel_packet(
    header: TunnelHeader,
    payload: bytes,
    cipher: TunnelCipher,
) -> BasePacket:
    """Build one command-0x00 body; CBasePacket masking happens afterward."""
    return BasePacket(
        OrbwebCommand.TUNNEL_DATA,
        header.encode() + cipher.encrypt(payload),
    )


def decode_tunnel_packet(
    packet: BasePacket,
    cipher: TunnelCipher,
) -> TunnelFrame:
    """Validate and decrypt one already-unmasked command-0x00 packet."""
    if packet.command != OrbwebCommand.TUNNEL_DATA:
        raise OrbwebProtocolError(
            f"Expected tunnel data, received 0x{packet.command:x}"
        )
    minimum_length = HEADER_LENGTH + AES_BLOCK_SIZE
    if len(packet.payload) < minimum_length:
        raise OrbwebProtocolError(
            "Tunnel data body has "
            f"{len(packet.payload)} bytes; expected at least {minimum_length}"
        )
    header = TunnelHeader.decode(packet.payload[:HEADER_LENGTH])
    plaintext = cipher.decrypt(packet.payload[HEADER_LENGTH:])
    return TunnelFrame(header, plaintext)


def _validate_header(header: TunnelHeader) -> None:
    _validate_request_id(header.request_id)
    if header.operation < 0:
        try:
            TunnelOperation(header.operation)
        except ValueError as err:
            raise OrbwebProtocolError(
                f"Unknown tunnel control operation: {header.operation}"
            ) from err
    _validate_port(header.local_port, "local")
    _validate_port(header.remote_port, "remote")
    if header.reserved != 0:
        raise OrbwebProtocolError("Tunnel header reserved field is nonzero")


def _validate_request_id(request_id: int) -> None:
    if not 0 <= request_id <= 0x7FFFFFFF:
        raise OrbwebProtocolError("Tunnel request ID is outside signed int32")


def _validate_port(port: int, label: str) -> None:
    if not 1 <= port <= 0xFFFF:
        raise OrbwebProtocolError(f"Tunnel {label} port is outside uint16")
