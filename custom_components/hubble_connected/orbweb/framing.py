"""Binary framing for the clean-room Orbweb client."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from enum import IntEnum

HEADER_LENGTH = 8
MAX_PAYLOAD_LENGTH = 4 * 1024 * 1024
_BODY_MASK = 0x350B


class OrbwebProtocolError(ValueError):
    """Raised when an Orbweb frame violates the reconstructed protocol."""


class OrbwebCommand(IntEnum):
    """Commands confirmed by native analysis or packet capture."""

    TUNNEL_DATA = 0x00
    TUNNEL_KEEPALIVE = 0x01
    KEY_EXCHANGE = 0x03
    TUNNEL_ACCESS = 0x07
    CONN_REG_CLIENT_REQ = 0x67
    CONN_REG_CLIENT_RSP = 0x68
    CONN_REG_CLIENT_FAIL = 0x69
    CONN_HOST_REQ = 0x6A
    CONN_HOST_RSP = 0x6C
    CONN_HOST_FAIL = 0x6D
    CONN_DEREG_REQ = 0x73
    CONN_DEREG_RSP = 0x74
    CONN_REG_NIC_REQ = 0x77
    CONN_REG_NIC_RSP = 0x78
    CONN_REG_NIC_FAIL = 0x7B
    CONN_REG_ADDR_REQ = 0xC8
    CONN_REG_ADDR_RSP = 0xC9
    CONN_REG_ADDR_FAIL = 0xCA
    CONN_DIR_CONN_REQ = 0xCB
    CONN_DIR_CONN_FORWARD = 0xCC
    CONN_DIR_CONN_RSP = 0xCD
    CONN_DIR_CONN_FAIL = 0xCE
    NAT_INFO_REQ = 0x258
    NAT_INFO_RSP = 0x259
    NAT_PROBE_1_REQ = 0x25B
    NAT_PROBE_10241_ACK = 0x25C
    NAT_PROBE_2_REQ = 0x25E
    NAT_PROBE_10242_ACK = 0x25F
    NAT_PROBE_3_REQ = 0x261
    NAT_PROBE_ACK_3 = 0x262
    NAT_PROBE_4_REQ = 0x264
    NAT_PROBE_ACK_4 = 0x265
    NAT_TYPE_QUERY = 0x267
    NAT_TYPE_RESULT = 0x268
    TCP_SHUNT_REG_REQ = 0x2BC
    TCP_SHUNT_REG_RSP = 0x2BD
    TCP_SHUNT_REG_FAIL_RSP = 0x2BE


@dataclass(frozen=True, slots=True)
class BasePacket:
    """A decoded Orbweb CBasePacket."""

    command: int
    payload: bytes = b""

    @property
    def payload_length(self) -> int:
        """Return the payload size declared on the wire."""
        return len(self.payload)


def apply_body_mask(data: bytes) -> bytes:
    """Apply the symmetric native CBasePacket body mask.

    The native loop masks a little-endian four-byte word only while more than
    four bytes remain. The final one-to-four bytes are therefore unchanged.
    Calling this function twice restores the original body.
    """

    result = bytearray(data)
    offset = 0
    remaining = len(result)
    while remaining > 4:
        word = int.from_bytes(result[offset : offset + 4], "little")
        result[offset : offset + 4] = (word ^ _BODY_MASK).to_bytes(4, "little")
        offset += 4
        remaining -= 4
    return bytes(result)


def _validate_payload_length(payload_length: int) -> None:
    if not 0 <= payload_length <= MAX_PAYLOAD_LENGTH:
        raise OrbwebProtocolError(f"Invalid Orbweb payload length: {payload_length}")


def encode_packet(packet: BasePacket, *, mask_payload: bool = True) -> bytes:
    """Encode one packet exactly as CBasePacket sends it."""

    _validate_payload_length(packet.payload_length)
    try:
        header = struct.pack("<ii", int(packet.command), packet.payload_length)
    except struct.error as err:
        raise OrbwebProtocolError("Orbweb command does not fit int32") from err
    payload = apply_body_mask(packet.payload) if mask_payload else packet.payload
    return header + payload


def decode_packet(frame: bytes, *, unmask_payload: bool = True) -> BasePacket:
    """Decode exactly one complete frame."""

    if len(frame) < HEADER_LENGTH:
        raise OrbwebProtocolError("Orbweb frame header is truncated")
    command, payload_length = struct.unpack_from("<ii", frame)
    _validate_payload_length(payload_length)
    expected_length = HEADER_LENGTH + payload_length
    if len(frame) != expected_length:
        raise OrbwebProtocolError(
            f"Orbweb frame has {len(frame)} bytes; expected {expected_length}"
        )
    payload = frame[HEADER_LENGTH:]
    if unmask_payload:
        payload = apply_body_mask(payload)
    return BasePacket(command, payload)


class BasePacketBuffer:
    """Incrementally decode CBasePacket frames from a TCP byte stream."""

    def __init__(self, *, unmask_payload: bool = True) -> None:
        self._buffer = bytearray()
        self._unmask_payload = unmask_payload

    @property
    def pending_bytes(self) -> int:
        """Return bytes waiting for the rest of a frame."""
        return len(self._buffer)

    def feed(self, data: bytes) -> list[BasePacket]:
        """Add a TCP chunk and return every newly completed packet."""

        self._buffer.extend(data)
        packets: list[BasePacket] = []
        while len(self._buffer) >= HEADER_LENGTH:
            _, payload_length = struct.unpack_from("<ii", self._buffer)
            _validate_payload_length(payload_length)
            frame_length = HEADER_LENGTH + payload_length
            if len(self._buffer) < frame_length:
                break
            frame = bytes(self._buffer[:frame_length])
            del self._buffer[:frame_length]
            packets.append(decode_packet(frame, unmask_payload=self._unmask_payload))
        return packets


async def async_read_packet(
    reader: asyncio.StreamReader, *, unmask_payload: bool = True
) -> BasePacket:
    """Read one complete packet from an asyncio TCP stream."""

    header = await reader.readexactly(HEADER_LENGTH)
    command, payload_length = struct.unpack("<ii", header)
    _validate_payload_length(payload_length)
    payload = await reader.readexactly(payload_length)
    if unmask_payload:
        payload = apply_body_mask(payload)
    return BasePacket(command, payload)


async def async_write_packet(
    writer: asyncio.StreamWriter,
    packet: BasePacket,
    *,
    mask_payload: bool = True,
) -> None:
    """Write and drain one complete packet to an asyncio TCP stream."""

    writer.write(encode_packet(packet, mask_payload=mask_payload))
    await writer.drain()
