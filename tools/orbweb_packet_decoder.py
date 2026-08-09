#!/usr/bin/env python3
"""Decode Orbweb CBasePacket headers without exposing packet payloads.

The decoder accepts classic PCAP files with Ethernet or raw-IP packets. TCP
payloads are reassembled per direction before scanning. Output contains only
an anonymous stream number, direction, command ID and declared payload length;
camera identifiers, addresses, credentials and packet bodies are never shown.
"""

from __future__ import annotations

import argparse
import socket
import struct
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_PAYLOAD_LENGTH = 4 * 1024 * 1024
PCAP_HEADER_LENGTH = 24
PCAP_RECORD_HEADER_LENGTH = 16

COMMAND_NAMES = {
    0x00: "TUNNEL_DATA",
    0x01: "TUNNEL_KEEPALIVE",
    0x03: "KEY_EXCHANGE",
    0x07: "TUNNEL_ACCESS",
    0x67: "CONN_REG_CLIENT_REQ",
    0x68: "CONN_REG_CLIENT_RSP",
    0x69: "CONN_REG_CLIENT_FAIL",
    0x6A: "CONN_HOST_REQ",
    0x6C: "CONN_HOST_RSP",
    0x6D: "CONN_HOST_FAIL",
    0x73: "CONN_DEREG_REQ",
    0x74: "CONN_DEREG_RSP",
    0x77: "CONN_REG_NIC_REQ",
    0x78: "CONN_REG_NIC_RSP",
    0x7B: "CONN_REG_NIC_FAIL",
    0xC8: "CONN_REG_ADDR_REQ",
    0xC9: "CONN_REG_ADDR_RSP",
    0xCA: "CONN_REG_ADDR_FAIL",
    0xCB: "CONN_DIR_CONN_REQ",
    0xCC: "CONN_DIR_CONN_FORWARD",
    0xCD: "CONN_DIR_CONN_RSP",
    0xCE: "CONN_DIR_CONN_FAIL",
    0x258: "NAT_INFO_REQ",
    0x259: "NAT_INFO_RSP",
    0x25C: "NAT_PROBE_10241_ACK",
    0x25F: "NAT_PROBE_10242_ACK",
    0x262: "NAT_PROBE_ACK_3",
    0x265: "NAT_PROBE_ACK_4",
    0x267: "NAT_TYPE_QUERY",
    0x268: "NAT_TYPE_RESULT",
    0x2BC: "TCP_SHUNT_REG_REQ",
    0x2BD: "TCP_SHUNT_REG_RSP",
    0x2BE: "TCP_SHUNT_REG_FAIL_RSP",
}


@dataclass(frozen=True)
class PacketHeader:
    """A decoded eight-byte little-endian CBasePacket header."""

    command: int
    payload_length: int
    offset: int


@dataclass(frozen=True)
class TransportPayload:
    """Network payload metadata used internally for stream reassembly."""

    protocol: str
    source: tuple[bytes, int]
    destination: tuple[bytes, int]
    sequence: int | None
    payload: bytes


def unmask_body(data: bytes) -> bytes:
    """Apply the reversible Orbweb body mask used by CBasePacket.

    The native loop XORs a four-byte word only while more than four bytes
    remain. Consequently a final block of one through four bytes is unchanged.
    """

    result = bytearray(data)
    offset = 0
    remaining = len(result)
    while remaining > 4:
        word = int.from_bytes(result[offset : offset + 4], "little")
        result[offset : offset + 4] = (word ^ 0x350B).to_bytes(4, "little")
        offset += 4
        remaining -= 4
    return bytes(result)


def scan_base_packets(
    data: bytes, *, include_unknown: bool = False
) -> list[PacketHeader]:
    """Decode consecutive CBasePacket records from the stream boundary.

    Starting only at offset zero is intentional. Searching arbitrary offsets
    produces misleading zero-length command matches in unrelated TLS, HTTP and
    padded data captured in the same PCAP.
    """

    packets: list[PacketHeader] = []
    offset = 0
    while offset + 8 <= len(data):
        command, payload_length = struct.unpack_from("<ii", data, offset)
        known = command in COMMAND_NAMES
        valid_length = 0 <= payload_length <= MAX_PAYLOAD_LENGTH
        end = offset + 8 + payload_length
        if valid_length and end <= len(data) and (known or include_unknown):
            packets.append(PacketHeader(command, payload_length, offset))
            offset = end
        else:
            break
    return packets


def _pcap_records(path: Path) -> tuple[int, list[bytes]]:
    raw = path.read_bytes()
    if len(raw) < PCAP_HEADER_LENGTH:
        raise ValueError("PCAP header is truncated")
    magic = raw[:4]
    if magic in {b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"}:
        endian = "<"
    elif magic in {b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"}:
        endian = ">"
    else:
        raise ValueError("Only classic PCAP input is supported")

    link_type = struct.unpack_from(f"{endian}I", raw, 20)[0]
    records: list[bytes] = []
    offset = PCAP_HEADER_LENGTH
    while offset + PCAP_RECORD_HEADER_LENGTH <= len(raw):
        _, _, captured_length, _ = struct.unpack_from(f"{endian}IIII", raw, offset)
        offset += PCAP_RECORD_HEADER_LENGTH
        end = offset + captured_length
        if end > len(raw):
            raise ValueError("PCAP record is truncated")
        records.append(raw[offset:end])
        offset = end
    return link_type, records


def _network_packet(link_type: int, frame: bytes) -> bytes | None:
    if link_type == 101:  # LINKTYPE_RAW
        return frame
    if link_type != 1 or len(frame) < 14:  # LINKTYPE_ETHERNET
        return None
    ether_type = int.from_bytes(frame[12:14], "big")
    offset = 14
    if ether_type in {0x8100, 0x88A8} and len(frame) >= 18:
        ether_type = int.from_bytes(frame[16:18], "big")
        offset = 18
    return frame[offset:] if ether_type == 0x0800 else None


def _transport_payload(packet: bytes) -> TransportPayload | None:
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    header_length = (packet[0] & 0x0F) * 4
    total_length = int.from_bytes(packet[2:4], "big")
    if header_length < 20 or len(packet) < total_length:
        return None
    protocol = packet[9]
    source_ip = packet[12:16]
    destination_ip = packet[16:20]
    transport = packet[header_length:total_length]

    if protocol == socket.IPPROTO_TCP and len(transport) >= 20:
        source_port, destination_port, sequence = struct.unpack_from("!HHI", transport)
        tcp_header_length = (transport[12] >> 4) * 4
        if tcp_header_length < 20 or tcp_header_length > len(transport):
            return None
        return TransportPayload(
            "tcp",
            (source_ip, source_port),
            (destination_ip, destination_port),
            sequence,
            transport[tcp_header_length:],
        )
    if protocol == socket.IPPROTO_UDP and len(transport) >= 8:
        source_port, destination_port, length = struct.unpack_from("!HHH", transport)
        if length < 8 or length > len(transport):
            return None
        return TransportPayload(
            "udp",
            (source_ip, source_port),
            (destination_ip, destination_port),
            None,
            transport[8:length],
        )
    return None


def _tcp_chunks(segments: dict[int, bytes]) -> Iterable[bytes]:
    current = bytearray()
    next_sequence: int | None = None
    for sequence, payload in sorted(segments.items()):
        if not payload:
            continue
        if next_sequence is None or sequence > next_sequence:
            if current:
                yield bytes(current)
            current = bytearray(payload)
            next_sequence = sequence + len(payload)
            continue
        overlap = next_sequence - sequence
        if overlap < len(payload):
            current.extend(payload[overlap:])
            next_sequence += len(payload) - overlap
    if current:
        yield bytes(current)


def decode_pcap(
    path: Path, *, include_unknown: bool = False
) -> list[tuple[str, str, PacketHeader]]:
    """Return redacted stream labels and decoded Orbweb headers."""

    link_type, records = _pcap_records(path)
    tcp_segments: dict[
        tuple[tuple[bytes, int], tuple[bytes, int]], dict[int, bytes]
    ] = defaultdict(dict)
    udp_payloads: list[TransportPayload] = []
    for frame in records:
        packet = _network_packet(link_type, frame)
        transport = _transport_payload(packet) if packet else None
        if transport is None or not transport.payload:
            continue
        if transport.protocol == "tcp" and transport.sequence is not None:
            key = (transport.source, transport.destination)
            tcp_segments[key].setdefault(transport.sequence, transport.payload)
        elif transport.protocol == "udp":
            udp_payloads.append(transport)

    conversations: dict[
        frozenset[tuple[bytes, int]], tuple[int, tuple[bytes, int]]
    ] = {}

    def label_for(transport: TransportPayload) -> tuple[str, str]:
        conversation = frozenset({transport.source, transport.destination})
        if conversation not in conversations:
            conversations[conversation] = (
                len(conversations) + 1,
                transport.source,
            )
        number, side_a = conversations[conversation]
        direction = "A->B" if transport.source == side_a else "B->A"
        return f"stream-{number}", direction

    decoded: list[tuple[str, str, PacketHeader]] = []
    for (source, destination), segments in tcp_segments.items():
        transport = TransportPayload("tcp", source, destination, None, b"")
        label, direction = label_for(transport)
        for chunk in _tcp_chunks(segments):
            for header in scan_base_packets(chunk, include_unknown=include_unknown):
                decoded.append((label, direction, header))
    for transport in udp_payloads:
        label, direction = label_for(transport)
        for header in scan_base_packets(
            transport.payload, include_unknown=include_unknown
        ):
            decoded.append((label, direction, header))
    return decoded


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcap", type=Path, help="classic PCAP file to inspect")
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="also report structurally valid command IDs not in the known map",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    for label, direction, header in decode_pcap(
        args.pcap, include_unknown=args.include_unknown
    ):
        name = COMMAND_NAMES.get(header.command, "UNKNOWN")
        print(
            f"{label} {direction} offset={header.offset} "
            f"command=0x{header.command:x}({name}) "
            f"payload_length={header.payload_length}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
