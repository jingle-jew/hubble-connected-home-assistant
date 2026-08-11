"""Tests for the clean-room Orbweb integration primitives."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import struct
import sys
import types
import unittest
from pathlib import Path

ORBWEB_PATH = (
    Path(__file__).parents[1] / "custom_components" / "hubble_connected" / "orbweb"
)


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ORBWEB_PATH / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


package = types.ModuleType("hubble_orbweb")
package.__path__ = [str(ORBWEB_PATH)]
sys.modules[package.__name__] = package
framing = _load_module("hubble_orbweb.framing", "framing.py")
crypto = _load_module("hubble_orbweb.crypto", "crypto.py")
handshake = _load_module("hubble_orbweb.handshake", "handshake.py")
identity = _load_module("hubble_orbweb.identity", "identity.py")
nat = _load_module("hubble_orbweb.nat", "nat.py")
requests = _load_module("hubble_orbweb.requests", "requests.py")
rendezvous = _load_module("hubble_orbweb.rendezvous", "rendezvous.py")
responses = _load_module("hubble_orbweb.responses", "responses.py")
control = _load_module("hubble_orbweb.control", "control.py")
direct = _load_module("hubble_orbweb.direct", "direct.py")
direct_client = _load_module("hubble_orbweb.direct_client", "direct_client.py")
direct_listeners = _load_module("hubble_orbweb.direct_listeners", "direct_listeners.py")
direct_race = _load_module("hubble_orbweb.direct_race", "direct_race.py")
tunnel = _load_module("hubble_orbweb.tunnel", "tunnel.py")
port_mapping = _load_module("hubble_orbweb.port_mapping", "port_mapping.py")
authentication = _load_module("hubble_orbweb.authentication", "authentication.py")
direct_tunnel = _load_module("hubble_orbweb.direct_tunnel", "direct_tunnel.py")
lan_client = _load_module("hubble_orbweb.lan_client", "lan_client.py")
pool = _load_module("hubble_orbweb.pool", "pool.py")
shunt = _load_module("hubble_orbweb.shunt", "shunt.py")
shunt_client = _load_module("hubble_orbweb.shunt_client", "shunt_client.py")
nat_client = _load_module("hubble_orbweb.nat_client", "nat_client.py")


class _FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self, *, content_type=None):
        return self._payload


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


class _MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.drain_calls = 0

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        self.drain_calls += 1


class _SocketWriter(_MemoryWriter):
    def __init__(self, local_port: int) -> None:
        super().__init__()
        self.local_port = local_port
        self.closed = False

    def get_extra_info(self, name: str, default=None):
        if name == "sockname":
            return ("192.0.2.10", self.local_port)
        return default

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        return None


class _FakeConnectionFactory:
    def __init__(self, responses_by_connection: list[bytes]) -> None:
        self.responses_by_connection = responses_by_connection
        self.calls: list[tuple[str, int, tuple[str, int]]] = []
        self.writers: list[_SocketWriter] = []

    async def __call__(self, host: str, port: int, *, local_addr):
        index = len(self.calls)
        self.calls.append((host, port, local_addr))
        reader = asyncio.StreamReader()
        reader.feed_data(self.responses_by_connection[index])
        reader.feed_eof()
        if index == 0:
            local_port = 41000
        elif local_addr[1] == 0:
            local_port = 42000
        else:
            local_port = local_addr[1]
        writer = _SocketWriter(local_port)
        self.writers.append(writer)
        return reader, writer


class _ShuntConnectionFactory:
    def __init__(self, response: bytes, *, delay: float = 0) -> None:
        self.response = response
        self.delay = delay
        self.calls: list[tuple[str, int]] = []
        self.writer = _SocketWriter(43000)

    async def __call__(self, host: str, port: int):
        self.calls.append((host, port))
        reader = asyncio.StreamReader()
        if self.delay:
            loop = asyncio.get_running_loop()
            loop.call_later(self.delay, reader.feed_data, self.response)
            loop.call_later(self.delay, reader.feed_eof)
        else:
            reader.feed_data(self.response)
            reader.feed_eof()
        return reader, self.writer


class _FakeServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeSocket:
    def __init__(self, host: str, port: int) -> None:
        self._sockname = (host, port)

    def getsockname(self):
        return self._sockname


class _LoopbackServerFactory:
    def __init__(self, selected_port: int = 46000) -> None:
        self.selected_port = selected_port
        self.calls: list[tuple[str, int]] = []
        self.callbacks: list[object] = []
        self.servers: list[_FakeServer] = []

    async def __call__(self, callback, *, host: str, port: int):
        self.calls.append((host, port))
        self.callbacks.append(callback)
        server = _FakeServer(host, self.selected_port if port == 0 else port)
        server.sockets = [_FakeSocket(host, server.port)]
        self.servers.append(server)
        return server


class _DirectServerFactory:
    def __init__(
        self,
        *,
        fail_once: tuple[str, int] | None = None,
    ) -> None:
        self.fail_once = fail_once
        self.calls: list[tuple[str, int]] = []
        self.callbacks: list[object] = []
        self.servers: list[_FakeServer] = []

    async def __call__(self, callback, *, host: str, port: int):
        self.calls.append((host, port))
        if self.fail_once == (host, port):
            self.fail_once = None
            raise OSError("simulated bind collision")
        server = _FakeServer(host, port)
        self.callbacks.append(callback)
        self.servers.append(server)
        return server


class OrbwebFramingTests(unittest.TestCase):
    """Exercise the exact CBasePacket byte boundaries."""

    def test_round_trip_masks_only_complete_non_final_words(self) -> None:
        payload = bytes.fromhex("010203040506070809")
        packet = framing.BasePacket(framing.OrbwebCommand.KEY_EXCHANGE, payload)
        encoded = framing.encode_packet(packet)

        self.assertEqual(encoded[:8], struct.pack("<ii", 0x03, len(payload)))
        self.assertNotEqual(encoded[8:12], payload[:4])
        self.assertNotEqual(encoded[12:16], payload[4:8])
        self.assertEqual(encoded[16:], payload[8:])
        self.assertEqual(framing.decode_packet(encoded), packet)

    def test_incremental_buffer_returns_only_complete_packets(self) -> None:
        first = framing.encode_packet(framing.BasePacket(0x67, b"client"))
        second = framing.encode_packet(framing.BasePacket(0x73, b"done"))
        decoder = framing.BasePacketBuffer()

        self.assertEqual(decoder.feed(first[:5]), [])
        self.assertEqual(
            decoder.feed(first[5:] + second[:3]),
            [framing.BasePacket(0x67, b"client")],
        )
        self.assertEqual(decoder.pending_bytes, 3)
        self.assertEqual(
            decoder.feed(second[3:]),
            [framing.BasePacket(0x73, b"done")],
        )
        self.assertEqual(decoder.pending_bytes, 0)

    def test_rejects_oversized_length_before_waiting_for_payload(self) -> None:
        decoder = framing.BasePacketBuffer()
        invalid = struct.pack("<ii", 0x67, framing.MAX_PAYLOAD_LENGTH + 1)
        with self.assertRaises(framing.OrbwebProtocolError):
            decoder.feed(invalid)


class OrbwebCryptoTests(unittest.TestCase):
    """Lock the statically reconstructed DH and AES inputs to test vectors."""

    def test_small_dh_vector_and_wire_encoding(self) -> None:
        exchange = crypto.KeyExchange.from_private(
            modulus=23, generator=5, private_value=6
        )
        self.assertEqual(exchange.public_value, 8)
        self.assertEqual(exchange.shared_secret(6, 19), 2)
        self.assertEqual(exchange.encode(), struct.pack("<qqq", 23, 5, 8))

    def test_key_and_iv_vectors(self) -> None:
        self.assertEqual(
            crypto.derive_aes_key(123456789).hex(),
            "25f9e794323b453885f5181f1b624d0b",
        )
        key = bytes(range(16))
        expected = bytearray(16)
        expected[2] = 15
        expected[5] = 14
        expected[8] = 29
        expected[12] = 23
        expected[14] = 12
        self.assertEqual(crypto.derive_iv(key), bytes(expected))

    def test_pkcs7_full_block_and_validation(self) -> None:
        aligned = b"a" * 16
        padded = crypto.pkcs7_pad(aligned)
        self.assertEqual(padded[-16:], b"\x10" * 16)
        self.assertEqual(crypto.pkcs7_unpad(padded), aligned)
        with self.assertRaises(ValueError):
            crypto.pkcs7_unpad(b"invalid-padding\x02")


class OrbwebHandshakeTests(unittest.IsolatedAsyncioTestCase):
    """Validate the complete command-0x03 byte exchange in memory."""

    async def test_negotiates_key_material_and_masks_request(self) -> None:
        exchange = crypto.KeyExchange.from_private(
            modulus=23, generator=5, private_value=6
        )
        response = framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.KEY_EXCHANGE,
                struct.pack("<qq", 19, 0),
            )
        )
        reader = asyncio.StreamReader()
        reader.feed_data(response)
        reader.feed_eof()
        writer = _MemoryWriter()

        material = await handshake.async_negotiate_tunnel_key(
            reader, writer, exchange, private_value=6
        )

        self.assertEqual(material.shared_secret, 2)
        self.assertEqual(material.key, crypto.derive_aes_key(2))
        self.assertEqual(material.iv, crypto.derive_iv(material.key))
        self.assertEqual(repr(material), "TunnelKeyMaterial()")
        self.assertEqual(writer.drain_calls, 1)
        request = framing.decode_packet(bytes(writer.data))
        self.assertEqual(request.command, framing.OrbwebCommand.KEY_EXCHANGE)
        self.assertEqual(request.payload, struct.pack("<qqq", 23, 5, 8))

    async def test_rejects_wrong_response_command(self) -> None:
        exchange = crypto.KeyExchange.from_private(
            modulus=23, generator=5, private_value=6
        )
        reader = asyncio.StreamReader()
        reader.feed_data(
            framing.encode_packet(
                framing.BasePacket(framing.OrbwebCommand.TUNNEL_ACCESS)
            )
        )
        reader.feed_eof()

        with self.assertRaises(framing.OrbwebProtocolError):
            await handshake.async_negotiate_tunnel_key(
                reader, _MemoryWriter(), exchange, private_value=6
            )

    async def test_rejects_nonzero_response_reserved_field(self) -> None:
        exchange = crypto.KeyExchange.from_private(
            modulus=23, generator=5, private_value=6
        )
        reader = asyncio.StreamReader()
        reader.feed_data(
            framing.encode_packet(
                framing.BasePacket(
                    framing.OrbwebCommand.KEY_EXCHANGE,
                    struct.pack("<qq", 19, 1),
                )
            )
        )
        reader.feed_eof()

        with self.assertRaises(framing.OrbwebProtocolError):
            await handshake.async_negotiate_tunnel_key(
                reader, _MemoryWriter(), exchange, private_value=6
            )

    async def test_accepts_camera_exchange_and_sends_native_response(self) -> None:
        request_exchange = crypto.KeyExchange.from_private(
            modulus=23, generator=5, private_value=6
        )
        reader = asyncio.StreamReader()
        reader.feed_data(
            framing.encode_packet(
                framing.BasePacket(
                    framing.OrbwebCommand.KEY_EXCHANGE,
                    request_exchange.encode(),
                )
            )
        )
        reader.feed_eof()
        writer = _MemoryWriter()

        material = await handshake.async_accept_tunnel_key(
            reader,
            writer,
            private_value=7,
        )

        self.assertEqual(material.shared_secret, pow(8, 7, 23))
        response = framing.decode_packet(bytes(writer.data))
        self.assertEqual(response.command, 0x03)
        self.assertEqual(response.payload, struct.pack("<qq", pow(5, 7, 23), 0))

    async def test_server_rejects_malformed_camera_exchange(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(
            framing.encode_packet(
                framing.BasePacket(
                    framing.OrbwebCommand.KEY_EXCHANGE,
                    struct.pack("<qq", 23, 5),
                )
            )
        )
        reader.feed_eof()

        with self.assertRaises(framing.OrbwebProtocolError):
            await handshake.async_accept_tunnel_key(
                reader,
                _MemoryWriter(),
                private_value=7,
            )


class OrbwebTunnelTests(unittest.TestCase):
    """Lock the native ReqInfoHeader and per-packet AES boundary."""

    _HEADER = tunnel.TunnelHeader(
        request_id=23,
        operation=0,
        role=tunnel.TunnelStreamRole.LISTENER,
        local_port=10245,
        remote_port=6667,
    )

    def test_req_info_header_uses_native_little_endian_layout(self) -> None:
        encoded = self._HEADER.encode()

        self.assertEqual(
            encoded,
            struct.pack("<iiHHHH", 23, 0, 1, 10245, 6667, 0),
        )
        self.assertEqual(tunnel.TunnelHeader.decode(encoded), self._HEADER)

    def test_rejects_unknown_role_operation_and_reserved_field(self) -> None:
        invalid_frames = (
            struct.pack("<iiHHHH", 23, 0, 3, 10245, 6667, 0),
            struct.pack("<iiHHHH", 23, -1, 1, 10245, 6667, 0),
            struct.pack("<iiHHHH", 23, 0, 1, 10245, 6667, 1),
        )
        for raw in invalid_frames:
            with self.subTest(raw=raw), self.assertRaises(framing.OrbwebProtocolError):
                tunnel.TunnelHeader.decode(raw)

    def test_mapping_announcement_has_confirmed_eight_byte_shape(self) -> None:
        announcement = tunnel.MappingAnnouncement(10245, 6667, 23)
        packet = announcement.packet()

        self.assertEqual(packet.command, framing.OrbwebCommand.TUNNEL_ACCESS)
        self.assertEqual(packet.payload, struct.pack("<HHi", 10245, 6667, 23))
        self.assertEqual(tunnel.MappingAnnouncement.parse(packet), announcement)

    @unittest.skipUnless(
        importlib.util.find_spec("cryptography"),
        "cryptography is supplied by Home Assistant",
    )
    def test_aes_resets_iv_and_round_trips_complete_tunnel_packet(self) -> None:
        cipher = tunnel.TunnelCipher(
            bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
            bytes.fromhex("101112131415161718191a1b1c1d1e1f"),
        )

        expected = bytes.fromhex("0982f24bc68578eef85eaf7b46f97668")
        self.assertEqual(cipher.encrypt(b"orbweb"), expected)
        self.assertEqual(cipher.encrypt(b"orbweb"), expected)
        self.assertNotIn("00010203", repr(cipher))

        packet = tunnel.encode_tunnel_packet(self._HEADER, b"orbweb", cipher)
        decoded = tunnel.decode_tunnel_packet(packet, cipher)
        self.assertEqual(decoded.header, self._HEADER)
        self.assertEqual(decoded.payload, b"orbweb")
        self.assertEqual(repr(decoded), "TunnelFrame()")

    def test_rejects_malformed_tunnel_packets_before_decryption(self) -> None:
        cipher = tunnel.TunnelCipher(b"k" * 16, b"i" * 16)
        malformed = (
            framing.BasePacket(framing.OrbwebCommand.TUNNEL_KEEPALIVE),
            framing.BasePacket(
                framing.OrbwebCommand.TUNNEL_DATA,
                self._HEADER.encode() + b"short",
            ),
        )
        for packet in malformed:
            with (
                self.subTest(command=packet.command),
                self.assertRaises(framing.OrbwebProtocolError),
            ):
                tunnel.decode_tunnel_packet(packet, cipher)


@unittest.skipUnless(
    importlib.util.find_spec("cryptography"),
    "cryptography is supplied by Home Assistant",
)
class OrbwebPortMappingTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the loopback-to-encrypted-tunnel boundary end to end."""

    async def test_forwards_tcp_and_completes_native_disconnect(self) -> None:
        accepted = asyncio.get_running_loop().create_future()

        async def accept_tunnel(reader, writer) -> None:
            accepted.set_result((reader, writer))

        server = await asyncio.start_server(
            accept_tunnel,
            host="127.0.0.1",
            port=0,
        )
        tunnel_port = server.sockets[0].getsockname()[1]
        mapping_reader, mapping_writer = await asyncio.open_connection(
            "127.0.0.1", tunnel_port
        )
        peer_reader, peer_writer = await accepted
        cipher = tunnel.TunnelCipher(b"k" * 16, b"i" * 16)
        mapping = await port_mapping.async_open_port_mapping(
            mapping_reader,
            mapping_writer,
            cipher,
            keepalive_initial_delay=60,
        )
        local_reader, local_writer = await asyncio.open_connection(
            mapping.host, mapping.port
        )

        local_writer.write(b"OPTIONS rtsp://camera/blinkhd RTSP/1.0\r\n\r\n")
        await local_writer.drain()
        announcement = tunnel.MappingAnnouncement.parse(
            await framing.async_read_packet(peer_reader)
        )
        request = tunnel.decode_tunnel_packet(
            await framing.async_read_packet(peer_reader), cipher
        )

        self.assertEqual(announcement.local_port, mapping.port)
        self.assertEqual(announcement.remote_port, 6667)
        self.assertEqual(request.header.request_id, announcement.request_id)
        self.assertEqual(request.header.operation, 0)
        self.assertEqual(request.header.role, tunnel.TunnelStreamRole.LISTENER)
        self.assertTrue(request.payload.startswith(b"OPTIONS rtsp://"))

        response_payload = b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n"
        response_header = tunnel.TunnelHeader(
            announcement.request_id,
            0,
            tunnel.TunnelStreamRole.CONNECTOR,
            announcement.remote_port,
            announcement.local_port,
        )
        await framing.async_write_packet(
            peer_writer,
            tunnel.encode_tunnel_packet(response_header, response_payload, cipher),
        )
        self.assertEqual(
            await asyncio.wait_for(local_reader.readexactly(len(response_payload)), 1),
            response_payload,
        )

        local_writer.close()
        await local_writer.wait_closed()
        disconnect = tunnel.decode_tunnel_packet(
            await framing.async_read_packet(peer_reader), cipher
        )
        self.assertEqual(
            disconnect.header.operation,
            tunnel.TunnelOperation.DISCONNECT_REQUEST,
        )
        self.assertEqual(disconnect.payload, b"")

        disconnect_response = tunnel.encode_tunnel_packet(
            tunnel.TunnelHeader(
                announcement.request_id,
                tunnel.TunnelOperation.DISCONNECT_RESPONSE,
                tunnel.TunnelStreamRole.CONNECTOR,
                announcement.remote_port,
                announcement.local_port,
            ),
            b"",
            cipher,
        )
        await framing.async_write_packet(peer_writer, disconnect_response)
        confirmation = tunnel.decode_tunnel_packet(
            await framing.async_read_packet(peer_reader), cipher
        )
        self.assertEqual(
            confirmation.header.operation,
            tunnel.TunnelOperation.DISCONNECT_CONFIRM,
        )

        # Camera data can already be in flight when the local still-image
        # consumer closes. Correctly routed late data must not tear down the
        # tunnel or leak into another local connection.
        late_data = tunnel.encode_tunnel_packet(
            tunnel.TunnelHeader(
                announcement.request_id,
                1,
                tunnel.TunnelStreamRole.CONNECTOR,
                announcement.remote_port,
                announcement.local_port,
            ),
            b"late media",
            cipher,
        )
        await asyncio.sleep(0)
        await framing.async_write_packet(peer_writer, late_data)
        await asyncio.sleep(0)
        self.assertFalse(mapping.is_closed)

        # A repeated disconnect control for the retired request is harmless.
        await framing.async_write_packet(peer_writer, disconnect_response)
        await asyncio.sleep(0)
        self.assertFalse(mapping.is_closed)

        await mapping.async_close()
        peer_writer.close()
        await peer_writer.wait_closed()
        server.close()
        await server.wait_closed()
        self.assertTrue(mapping.is_closed)
        self.assertIsNone(mapping.terminal_error)

    async def test_sends_and_accepts_empty_native_keepalives(self) -> None:
        accepted = asyncio.get_running_loop().create_future()

        async def accept_tunnel(reader, writer) -> None:
            accepted.set_result((reader, writer))

        server = await asyncio.start_server(
            accept_tunnel,
            host="127.0.0.1",
            port=0,
        )
        tunnel_port = server.sockets[0].getsockname()[1]
        mapping_reader, mapping_writer = await asyncio.open_connection(
            "127.0.0.1", tunnel_port
        )
        peer_reader, peer_writer = await accepted
        mapping = await port_mapping.async_open_port_mapping(
            mapping_reader,
            mapping_writer,
            tunnel.TunnelCipher(b"k" * 16, b"i" * 16),
            keepalive_initial_delay=0.01,
            keepalive_interval=1,
        )

        keepalive = await asyncio.wait_for(framing.async_read_packet(peer_reader), 1)
        self.assertEqual(
            keepalive,
            framing.BasePacket(framing.OrbwebCommand.TUNNEL_KEEPALIVE),
        )
        await framing.async_write_packet(peer_writer, keepalive)
        await asyncio.sleep(0)
        self.assertFalse(mapping.is_closed)

        await mapping.async_close()
        peer_writer.close()
        await peer_writer.wait_closed()
        server.close()
        await server.wait_closed()

    async def test_maps_authentication_and_rtsp_ports_on_one_tunnel(
        self,
    ) -> None:
        accepted = asyncio.get_running_loop().create_future()

        async def accept_tunnel(reader, writer) -> None:
            accepted.set_result((reader, writer))

        server = await asyncio.start_server(
            accept_tunnel,
            host="127.0.0.1",
            port=0,
        )
        mapping_reader, mapping_writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        peer_reader, peer_writer = await accepted
        cipher = tunnel.TunnelCipher(b"k" * 16, b"i" * 16)
        mapping = await port_mapping.async_open_port_mapping(
            mapping_reader,
            mapping_writer,
            cipher,
            additional_remote_ports=(9001,),
            keepalive_initial_delay=60,
        )

        self.assertNotEqual(mapping.port, mapping.local_port(9001))
        _reader, writer = await asyncio.open_connection(
            mapping.host, mapping.local_port(9001)
        )
        writer.write(b"auth")
        await writer.drain()
        announcement = tunnel.MappingAnnouncement.parse(
            await framing.async_read_packet(peer_reader)
        )
        request = tunnel.decode_tunnel_packet(
            await framing.async_read_packet(peer_reader), cipher
        )

        self.assertEqual(announcement.remote_port, 9001)
        self.assertEqual(announcement.local_port, mapping.local_port(9001))
        self.assertEqual(request.payload, b"auth")

        writer.close()
        await writer.wait_closed()
        await mapping.async_close()
        peer_writer.close()
        await peer_writer.wait_closed()
        server.close()
        await server.wait_closed()

    async def test_rejects_non_loopback_bind_before_listening(self) -> None:
        reader = asyncio.StreamReader()
        writer = _SocketWriter(41000)

        with self.assertRaises(ValueError):
            await port_mapping.async_open_port_mapping(
                reader,
                writer,
                tunnel.TunnelCipher(b"k" * 16, b"i" * 16),
                host="192.0.2.10",
            )

        self.assertFalse(writer.closed)


class OrbwebAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    """Lock the camera-side port-9001 request and response framing."""

    async def test_sends_native_authentication_request(self) -> None:
        received = asyncio.get_running_loop().create_future()

        async def handle_client(reader, writer) -> None:
            version, length = struct.unpack("<II", await reader.readexactly(8))
            payload = await reader.readexactly(length)
            received.set_result((version, payload))
            response = b'{"CMD_ID":"P2P_USER_PASSWORD_RSP","STATUS":"0"}\0'
            writer.write(struct.pack("<II", 1, len(response)) + response)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, host="127.0.0.1", port=0)
        await authentication.async_authenticate_camera(
            "127.0.0.1",
            server.sockets[0].getsockname()[1],
            p2p_server_id="camera_ctok_TOKEN#007",
            account="orbweb_user001",
            password="camera-secret",
        )
        version, raw_request = await received
        request = json.loads(raw_request.rstrip(b"\0"))

        self.assertEqual(version, 1)
        self.assertEqual(request["CMD_ID"], "P2P_USER_PASSWORD_REQ")
        self.assertEqual(request["P2PSERVERID"], "camera_ctok_TOKEN#007")
        self.assertEqual(request["NAME"], "orbweb_user001")
        self.assertEqual(request["PASSWORD"], "camera-secret")

        server.close()
        await server.wait_closed()

    async def test_rejects_camera_authentication_failure(self) -> None:
        async def handle_client(reader, writer) -> None:
            _version, length = struct.unpack("<II", await reader.readexactly(8))
            await reader.readexactly(length)
            response = b'{"CMD_ID":"P2P_USER_PASSWORD_RSP","STATUS":"-1"}\0'
            writer.write(struct.pack("<II", 1, len(response)) + response)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle_client, host="127.0.0.1", port=0)
        with self.assertRaises(authentication.OrbwebAuthenticationError):
            await authentication.async_authenticate_camera(
                "127.0.0.1",
                server.sockets[0].getsockname()[1],
                p2p_server_id="camera_ctok_TOKEN#007",
                account="orbweb_user001",
                password="wrong-secret",
            )

        server.close()
        await server.wait_closed()


class OrbwebIdentityTests(unittest.TestCase):
    """Lock the persistent token and native client-ID boundaries."""

    _TOKEN = "ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF"

    def test_generated_token_has_capture_compatible_shape(self) -> None:
        token = identity.generate_client_token()
        identity.validate_client_token(token)
        self.assertEqual(tuple(map(len, token.split("-"))), (8, 4, 4, 4, 12))

    def test_builds_exact_client_id_and_zero_pads_counter(self) -> None:
        client_id = identity.build_client_id("target", self._TOKEN, 7)
        self.assertEqual(
            client_id,
            "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007",
        )

    def test_identity_increments_and_wraps_native_counter(self) -> None:
        client = identity.OrbwebClientIdentity(self._TOKEN, 254)
        self.assertTrue(client.next_client_id("target").endswith("#255"))
        self.assertTrue(client.next_client_id("target").endswith("#000"))

    def test_rejects_invalid_fields_and_hides_token_from_repr(self) -> None:
        client = identity.OrbwebClientIdentity(self._TOKEN, 0)
        self.assertNotIn(self._TOKEN, repr(client))
        with self.assertRaises(ValueError):
            identity.build_client_id("caméra", self._TOKEN, 0)
        with self.assertRaises(ValueError):
            identity.build_client_id("target", "not-a-token", 0)
        with self.assertRaises(ValueError):
            identity.build_client_id("target", self._TOKEN, 256)


class OrbwebNatTests(unittest.TestCase):
    """Lock the capture-confirmed NAT structures and command table."""

    _CLIENT_ID = "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007_01"

    def test_builds_native_nat_worker_suffix(self) -> None:
        self.assertEqual(
            nat.build_nat_client_id(
                "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007",
                0,
                1,
            ),
            self._CLIENT_ID,
        )

    def test_serializes_exact_nat_info_request(self) -> None:
        packet = nat.NatInfoRequest(
            self._CLIENT_ID,
            "192.0.2.10",
            42424,
        ).packet()

        self.assertEqual(packet.command, 0x258)
        self.assertEqual(len(packet.payload), 290)
        self.assertEqual(
            packet.payload[0 : len(self._CLIENT_ID)],
            self._CLIENT_ID.encode(),
        )
        self.assertEqual(packet.payload[255:265], b"192.0.2.10")
        self.assertEqual(struct.unpack_from("<H", packet.payload, 288), (42424,))

    def test_serializes_query_and_four_probe_commands(self) -> None:
        query = nat.NatTypeQuery(self._CLIENT_ID).packet()
        probes = [
            nat.NatProbeRequest(self._CLIENT_ID, index).packet() for index in range(4)
        ]

        self.assertEqual(query.command, 0x267)
        self.assertEqual(query.payload, self._CLIENT_ID.encode())
        self.assertEqual(
            [packet.command for packet in probes],
            [0x25B, 0x25E, 0x261, 0x264],
        )
        self.assertTrue(all(packet.payload == query.payload for packet in probes))

    def test_parses_initial_and_final_nat_response(self) -> None:
        initial = nat.parse_nat_info_response(
            framing.BasePacket(
                framing.OrbwebCommand.NAT_INFO_RSP,
                _nat_response_payload(
                    self._CLIENT_ID,
                    nat_type=nat.UNKNOWN_NAT_VALUE,
                    behind=1,
                ),
            ),
            expected_client_id=self._CLIENT_ID,
        )
        final = nat.parse_nat_info_response(
            framing.BasePacket(
                framing.OrbwebCommand.NAT_TYPE_RESULT,
                _nat_response_payload(
                    self._CLIENT_ID,
                    nat_type=3,
                    behind=nat.UNKNOWN_NAT_VALUE,
                ),
            )
        )

        self.assertEqual(initial.local_address, "192.0.2.10")
        self.assertEqual(initial.public_address, "198.51.100.20")
        self.assertFalse(initial.nat_type_is_known)
        self.assertTrue(initial.behind_is_known)
        self.assertEqual(final.nat_type, 3)
        self.assertTrue(final.nat_type_is_known)
        self.assertFalse(final.behind_is_known)
        self.assertNotIn(self._CLIENT_ID, repr(final))

    def test_rejects_wrong_identity_bad_padding_and_invalid_fields(self) -> None:
        packet = framing.BasePacket(
            framing.OrbwebCommand.NAT_INFO_RSP,
            _nat_response_payload(self._CLIENT_ID, nat_type=3, behind=1),
        )
        with self.assertRaises(framing.OrbwebProtocolError):
            nat.parse_nat_info_response(packet, expected_client_id="other")

        malformed = bytearray(packet.payload)
        malformed[303] = 1
        with self.assertRaises(framing.OrbwebProtocolError):
            nat.parse_nat_info_response(
                framing.BasePacket(packet.command, bytes(malformed))
            )

        with self.assertRaises(ValueError):
            nat.NatInfoRequest(self._CLIENT_ID, "not-an-ip", 42424).packet()
        with self.assertRaises(ValueError):
            nat.NatProbeRequest(self._CLIENT_ID, 4).packet()


class OrbwebNatClientTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the NAT socket state machine entirely in memory."""

    _CLIENT_ID = "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007_01"

    def test_plan_selects_first_and_second_server_slots(self) -> None:
        plan = nat_client.NatTraversalPlan.from_server_utilities(_server_utilities())

        self.assertEqual(plan.control_endpoint, ("192.0.2.1", 10240))
        self.assertEqual(
            plan.probe_endpoints,
            (
                ("192.0.2.1", 10241),
                ("192.0.2.1", 10242),
                ("192.0.2.2", 10241),
                ("192.0.2.2", 10242),
            ),
        )

    async def test_runs_control_four_probes_and_final_query(self) -> None:
        initial = framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.NAT_INFO_RSP,
                _nat_response_payload(
                    self._CLIENT_ID,
                    nat_type=nat.UNKNOWN_NAT_VALUE,
                    behind=1,
                ),
            )
        )
        final = framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.NAT_TYPE_RESULT,
                _nat_response_payload(
                    self._CLIENT_ID,
                    nat_type=3,
                    behind=nat.UNKNOWN_NAT_VALUE,
                ),
            )
        )
        acknowledgements = [
            framing.encode_packet(framing.BasePacket(command))
            for command in nat.NAT_PROBE_ACK_COMMANDS
        ]
        factory = _FakeConnectionFactory([initial + final, *acknowledgements])

        result = await nat_client.async_detect_nat(
            client_id=self._CLIENT_ID,
            local_address="192.0.2.10",
            utilities=_server_utilities(),
            open_connection=factory,
        )

        self.assertEqual(
            [(host, port) for host, port, _ in factory.calls],
            [
                ("192.0.2.1", 10240),
                ("192.0.2.1", 10241),
                ("192.0.2.1", 10242),
                ("192.0.2.2", 10241),
                ("192.0.2.2", 10242),
            ],
        )
        self.assertEqual(
            [local_addr for _, _, local_addr in factory.calls],
            [
                ("192.0.2.10", 0),
                ("192.0.2.10", 0),
                ("192.0.2.10", 42000),
                ("192.0.2.10", 42000),
                ("192.0.2.10", 42000),
            ],
        )
        written_commands = [
            [
                packet.command
                for packet in framing.BasePacketBuffer().feed(bytes(writer.data))
            ]
            for writer in factory.writers
        ]
        self.assertEqual(
            written_commands,
            [[0x258, 0x267], [0x25B], [0x25E], [0x261], [0x264]],
        )
        self.assertEqual(result.final.nat_type, 3)
        self.assertEqual(result.probe_local_port, 42000)
        self.assertTrue(all(writer.closed for writer in factory.writers))

    async def test_stops_after_initial_response_when_not_behind_nat(self) -> None:
        response = framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.NAT_INFO_RSP,
                _nat_response_payload(
                    self._CLIENT_ID,
                    nat_type=0,
                    behind=0,
                ),
            )
        )
        factory = _FakeConnectionFactory([response])

        result = await nat_client.async_detect_nat(
            client_id=self._CLIENT_ID,
            local_address="192.0.2.10",
            utilities=_server_utilities(),
            open_connection=factory,
        )

        self.assertEqual(len(factory.calls), 1)
        self.assertIs(result.initial, result.final)
        self.assertIsNone(result.probe_local_port)

    async def test_rejects_wrong_probe_acknowledgement(self) -> None:
        initial = framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.NAT_INFO_RSP,
                _nat_response_payload(
                    self._CLIENT_ID,
                    nat_type=nat.UNKNOWN_NAT_VALUE,
                    behind=1,
                ),
            )
        )
        wrong_ack = framing.encode_packet(
            framing.BasePacket(framing.OrbwebCommand.NAT_PROBE_ACK_4)
        )
        factory = _FakeConnectionFactory([initial, wrong_ack])

        with self.assertRaises(framing.OrbwebProtocolError):
            await nat_client.async_detect_nat(
                client_id=self._CLIENT_ID,
                local_address="192.0.2.10",
                utilities=_server_utilities(),
                open_connection=factory,
            )
        self.assertTrue(all(writer.closed for writer in factory.writers))


class OrbwebDirectTests(unittest.TestCase):
    """Lock the captured TCP-443 direct rendezvous messages."""

    _CLIENT_ID = "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007"
    _PEER_ID = "peer_ctok_12345678-1234-1234-1234-123456789ABC#008"
    _CANDIDATES = (
        direct.DirectCandidate("192.0.2.10", 41000),
        direct.DirectCandidate("198.51.100.20", 42000),
    )

    def test_serializes_listener_address_registration(self) -> None:
        packet = direct.DirectAddressRegistrationRequest(
            self._CLIENT_ID,
            self._CANDIDATES,
        ).packet()

        self.assertEqual(packet.command, 0xC8)
        self.assertEqual(
            packet.payload,
            (f"{self._CLIENT_ID};192.0.2.10:41000;198.51.100.20:42000\0").encode(),
        )

    def test_serializes_peer_connection_request(self) -> None:
        packet = direct.DirectConnectionRequest(
            self._PEER_ID,
            self._CLIENT_ID,
            self._CANDIDATES,
        ).packet()

        self.assertEqual(packet.command, 0xCB)
        self.assertEqual(
            packet.payload,
            (
                f"{self._PEER_ID};{self._CLIENT_ID};"
                "192.0.2.10:41000;198.51.100.20:42000\0"
            ).encode(),
        )

    def test_parses_capture_observed_double_nul_forward(self) -> None:
        packet = framing.BasePacket(
            framing.OrbwebCommand.CONN_DIR_CONN_FORWARD,
            (f"{self._PEER_ID};{self._CLIENT_ID};203.0.113.30:43000\0\0").encode(),
        )

        response = direct.parse_direct_connection_forward(
            packet,
            expected_client_id=self._CLIENT_ID,
        )

        self.assertEqual(response.peer_id, self._PEER_ID)
        self.assertEqual(response.client_id, self._CLIENT_ID)
        self.assertEqual(
            response.candidates,
            (direct.DirectCandidate("203.0.113.30", 43000),),
        )
        self.assertNotIn(self._PEER_ID, repr(response))

    def test_rejects_identity_mismatch_and_bad_termination(self) -> None:
        valid_body = (f"{self._PEER_ID};{self._CLIENT_ID};203.0.113.30:43000").encode()
        with self.assertRaises(framing.OrbwebProtocolError):
            direct.parse_direct_connection_forward(
                framing.BasePacket(0xCC, valid_body + b"\0"),
                expected_client_id="different-client",
            )
        with self.assertRaises(framing.OrbwebProtocolError):
            direct.parse_direct_connection_forward(
                framing.BasePacket(0xCC, valid_body + b"\0\0\0")
            )

    def test_rejects_invalid_candidate_and_empty_list(self) -> None:
        with self.assertRaises(ValueError):
            direct.DirectCandidate("2001:db8::1", 41000)
        with self.assertRaises(ValueError):
            direct.DirectAddressRegistrationRequest(
                self._CLIENT_ID,
                (),
            ).packet()


class OrbwebDirectListenerPoolTests(unittest.IsolatedAsyncioTestCase):
    """Validate atomic shared-port listeners with in-memory factories."""

    _ADDRESSES = ("192.0.2.10", "198.51.100.20")

    async def test_binds_same_native_range_port_on_every_address(self) -> None:
        factory = _DirectServerFactory()
        pool = await direct_listeners.async_open_direct_listener_pool(
            self._ADDRESSES,
            server_factory=factory,
            port_factory=lambda: 41000,
        )

        self.assertEqual(
            factory.calls,
            [("192.0.2.10", 41000), ("198.51.100.20", 41000)],
        )
        self.assertEqual(
            pool.candidates,
            (
                direct.DirectCandidate("192.0.2.10", 41000),
                direct.DirectCandidate("198.51.100.20", 41000),
            ),
        )
        self.assertEqual(pool.port, 41000)
        await pool.async_close()
        self.assertTrue(all(server.closed for server in factory.servers))

    async def test_partial_bind_failure_cleans_up_and_retries(self) -> None:
        factory = _DirectServerFactory(fail_once=("198.51.100.20", 41000))
        ports = iter((41000, 42000))

        pool = await direct_listeners.async_open_direct_listener_pool(
            self._ADDRESSES,
            server_factory=factory,
            port_factory=lambda: next(ports),
        )

        self.assertTrue(factory.servers[0].closed)
        self.assertEqual(pool.port, 42000)
        self.assertEqual(
            factory.calls[-2:],
            [
                ("192.0.2.10", 42000),
                ("198.51.100.20", 42000),
            ],
        )
        await pool.async_close()

    async def test_accept_transfers_socket_ownership(self) -> None:
        factory = _DirectServerFactory()
        pool = await direct_listeners.async_open_direct_listener_pool(
            ("192.0.2.10",),
            server_factory=factory,
            port_factory=lambda: 41000,
        )
        reader = asyncio.StreamReader()
        writer = _SocketWriter(41000)
        factory.callbacks[0](reader, writer)

        accepted = await pool.async_accept(timeout=0.1)

        self.assertIs(accepted.reader, reader)
        self.assertIs(accepted.writer, writer)
        await pool.async_close()
        self.assertFalse(writer.closed)
        writer.close()

    async def test_close_reclaims_unclaimed_accepted_socket(self) -> None:
        factory = _DirectServerFactory()
        pool = await direct_listeners.async_open_direct_listener_pool(
            ("192.0.2.10",),
            server_factory=factory,
            port_factory=lambda: 41000,
        )
        writer = _SocketWriter(41000)
        factory.callbacks[0](asyncio.StreamReader(), writer)

        await pool.async_close()

        self.assertTrue(writer.closed)
        with self.assertRaises(RuntimeError):
            await pool.async_accept()

    async def test_rejects_nonadvertisable_address_and_port(self) -> None:
        with self.assertRaises(ValueError):
            await direct_listeners.async_open_direct_listener_pool(
                ("127.0.0.1",),
                server_factory=_DirectServerFactory(),
                port_factory=lambda: 41000,
            )
        with self.assertRaises(ValueError):
            await direct_listeners.async_open_direct_listener_pool(
                ("192.0.2.10",),
                server_factory=_DirectServerFactory(),
                port_factory=lambda: 1024,
            )


class OrbwebDirectRaceTests(unittest.IsolatedAsyncioTestCase):
    """Verify that exactly one bidirectional candidate socket survives."""

    async def _pool(self, addresses=("192.0.2.10",)):
        return await direct_listeners.async_open_direct_listener_pool(
            addresses,
            server_factory=_DirectServerFactory(),
            port_factory=lambda: 41000,
        )

    async def test_first_outbound_success_wins_and_closes_other_successes(
        self,
    ) -> None:
        pool = await self._pool(("192.0.2.10", "198.51.100.20"))
        calls = []
        writers = []

        async def connect(host, port, *, local_addr):
            calls.append((host, port, local_addr))
            writer = _SocketWriter(local_addr[1])
            writers.append(writer)
            return asyncio.StreamReader(), writer

        ports = iter((43000, 44000))
        winner = await direct_race.async_race_direct_candidates(
            pool,
            (direct.DirectCandidate("203.0.113.30", 45000),),
            connection_factory=connect,
            port_factory=lambda: next(ports),
        )

        self.assertEqual(len(calls), 2)
        self.assertIn(winner.writer, writers)
        self.assertEqual(sum(not writer.closed for writer in writers), 1)
        winner.writer.close()
        await pool.async_close()

    async def test_inbound_socket_can_win(self) -> None:
        factory = _DirectServerFactory()
        pool = await direct_listeners.async_open_direct_listener_pool(
            ("192.0.2.10",),
            server_factory=factory,
            port_factory=lambda: 41000,
        )
        inbound_writer = _SocketWriter(41000)
        inbound_reader = asyncio.StreamReader()
        factory.callbacks[0](inbound_reader, inbound_writer)

        async def blocked_connect(host, port, *, local_addr):
            await asyncio.Future()

        winner = await direct_race.async_race_direct_candidates(
            pool,
            (direct.DirectCandidate("203.0.113.30", 45000),),
            connection_factory=blocked_connect,
            port_factory=lambda: 43000,
        )

        self.assertIs(winner.reader, inbound_reader)
        self.assertIs(winner.writer, inbound_writer)
        await pool.async_close()
        self.assertFalse(inbound_writer.closed)
        inbound_writer.close()

    async def test_timeout_cancels_failed_candidate_set(self) -> None:
        pool = await self._pool()

        async def failed_connect(host, port, *, local_addr):
            raise OSError("simulated unreachable candidate")

        with self.assertRaises(TimeoutError):
            await direct_race.async_race_direct_candidates(
                pool,
                (direct.DirectCandidate("203.0.113.30", 45000),),
                connection_factory=failed_connect,
                port_factory=lambda: 43000,
                timeout=0.01,
            )

        await pool.async_close()

    async def test_invalid_source_port_starts_no_connections(self) -> None:
        pool = await self._pool()
        calls = []

        async def connect(host, port, *, local_addr):
            calls.append((host, port, local_addr))

        with self.assertRaises(ValueError):
            await direct_race.async_race_direct_candidates(
                pool,
                (direct.DirectCandidate("203.0.113.30", 45000),),
                connection_factory=connect,
                port_factory=lambda: 1024,
            )

        self.assertEqual(calls, [])
        await pool.async_close()


class OrbwebDirectClientTests(unittest.IsolatedAsyncioTestCase):
    """Exercise direct-listener signaling without contacting the network."""

    _CLIENT_ID = "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007"
    _PEER_ID = "peer_ctok_12345678-1234-1234-1234-123456789ABC#008"
    _CANDIDATES = (direct.DirectCandidate("192.0.2.10", 41000),)

    def _plan(self) -> object:
        servers = rendezvous.RendezvousServers(
            "tat.example.invalid",
            "relay.example.invalid",
        )
        return direct_client.DirectListenerPlan.from_rendezvous(
            servers,
            client_id=self._CLIENT_ID,
            candidates=self._CANDIDATES,
        )

    async def test_registers_waits_for_forward_and_preserves_socket(self) -> None:
        response = framing.encode_packet(
            framing.BasePacket(framing.OrbwebCommand.CONN_REG_ADDR_RSP)
        ) + framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.CONN_DIR_CONN_FORWARD,
                (f"{self._PEER_ID};{self._CLIENT_ID};198.51.100.20:42000\0\0").encode(),
            )
        )
        factory = _ShuntConnectionFactory(response)

        session = await direct_client.async_open_direct_listener(
            self._plan(),
            connection_factory=factory,
        )

        self.assertEqual(factory.calls, [("tat.example.invalid", 443)])
        request = framing.decode_packet(bytes(factory.writer.data))
        self.assertEqual(request.command, 0xC8)
        self.assertFalse(session.is_closed)

        forwarded = await session.async_wait_for_forward()
        self.assertEqual(
            forwarded.candidates,
            (direct.DirectCandidate("198.51.100.20", 42000),),
        )
        self.assertFalse(factory.writer.closed)
        await session.async_close()
        self.assertTrue(factory.writer.closed)
        self.assertTrue(session.is_closed)

    async def test_registration_rejection_closes_socket(self) -> None:
        factory = _ShuntConnectionFactory(
            framing.encode_packet(
                framing.BasePacket(framing.OrbwebCommand.CONN_REG_ADDR_FAIL)
            )
        )

        with self.assertRaises(direct_client.DirectRendezvousRejected):
            await direct_client.async_open_direct_listener(
                self._plan(),
                connection_factory=factory,
            )

        self.assertTrue(factory.writer.closed)

    async def test_rejects_nonempty_registration_response(self) -> None:
        factory = _ShuntConnectionFactory(
            framing.encode_packet(
                framing.BasePacket(
                    framing.OrbwebCommand.CONN_REG_ADDR_RSP,
                    b"unexpected",
                )
            )
        )

        with self.assertRaises(framing.OrbwebProtocolError):
            await direct_client.async_open_direct_listener(
                self._plan(),
                connection_factory=factory,
            )

        self.assertTrue(factory.writer.closed)

    def test_plan_uses_tat_server_and_hides_client_id(self) -> None:
        plan = self._plan()
        self.assertEqual(plan.host, "tat.example.invalid")
        self.assertNotIn(self._CLIENT_ID, repr(plan))
        with self.assertRaises(ValueError):
            direct_client.DirectListenerPlan(
                "bad host",
                self._CLIENT_ID,
                self._CANDIDATES,
            )


class OrbwebDirectTunnelTests(unittest.IsolatedAsyncioTestCase):
    """Compose signaling, candidate race, key exchange, and port mapping."""

    _CLIENT_ID = "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007"
    _PEER_ID = "peer_ctok_12345678-1234-1234-1234-123456789ABC#008"

    async def test_transfers_winning_socket_to_loopback_mapping(self) -> None:
        signaling_response = framing.encode_packet(
            framing.BasePacket(framing.OrbwebCommand.CONN_REG_ADDR_RSP)
        ) + framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.CONN_DIR_CONN_FORWARD,
                (f"{self._PEER_ID};{self._CLIENT_ID};198.51.100.20:42000\0\0").encode(),
            )
        )
        signaling_factory = _ShuntConnectionFactory(signaling_response)
        listener_factory = _DirectServerFactory()
        mapping_factory = _LoopbackServerFactory()
        winner_writer = _SocketWriter(43000)

        async def connect_peer(host, port, *, local_addr):
            self.assertEqual((host, port), ("198.51.100.20", 42000))
            self.assertEqual(local_addr, ("192.0.2.10", 43000))
            reader = asyncio.StreamReader()
            reader.feed_data(
                framing.encode_packet(
                    framing.BasePacket(
                        framing.OrbwebCommand.KEY_EXCHANGE,
                        struct.pack("<qqq", 23, 5, 8),
                    )
                )
            )
            return reader, winner_writer

        listener = await direct_tunnel.async_open_direct_tunnel_listener(
            rendezvous.RendezvousServers(
                "tat.example.invalid", "relay.example.invalid"
            ),
            client_id=self._CLIENT_ID,
            local_addresses=("192.0.2.10",),
            listener_server_factory=listener_factory,
            listener_port_factory=lambda: 41000,
            signaling_connection_factory=signaling_factory,
        )

        self.assertEqual(
            listener.candidates,
            (direct.DirectCandidate("192.0.2.10", 41000),),
        )
        self.assertNotIn(self._CLIENT_ID, repr(listener))
        mapping = await listener.async_accept_port_mapping(
            race_connection_factory=connect_peer,
            race_port_factory=lambda: 43000,
            mapping_server_factory=mapping_factory,
            keepalive_initial_delay=60,
            private_value=7,
        )

        registration = framing.decode_packet(bytes(signaling_factory.writer.data))
        self.assertEqual(
            registration.command,
            framing.OrbwebCommand.CONN_REG_ADDR_REQ,
        )
        key_response = framing.decode_packet(bytes(winner_writer.data))
        self.assertEqual(
            key_response,
            framing.BasePacket(
                framing.OrbwebCommand.KEY_EXCHANGE,
                struct.pack("<qq", pow(5, 7, 23), 0),
            ),
        )
        self.assertTrue(listener.is_closed)
        self.assertTrue(signaling_factory.writer.closed)
        self.assertTrue(listener_factory.servers[0].closed)
        self.assertFalse(winner_writer.closed)
        self.assertEqual((mapping.host, mapping.port), ("127.0.0.1", 46000))

        await mapping.async_close()
        self.assertTrue(winner_writer.closed)

    async def test_handshake_failure_reclaims_winning_socket(self) -> None:
        signaling_response = framing.encode_packet(
            framing.BasePacket(framing.OrbwebCommand.CONN_REG_ADDR_RSP)
        ) + framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.CONN_DIR_CONN_FORWARD,
                (f"{self._PEER_ID};{self._CLIENT_ID};198.51.100.20:42000\0").encode(),
            )
        )
        winner_writer = _SocketWriter(43000)

        async def connect_peer(host, port, *, local_addr):
            reader = asyncio.StreamReader()
            reader.feed_data(
                framing.encode_packet(
                    framing.BasePacket(framing.OrbwebCommand.TUNNEL_KEEPALIVE)
                )
            )
            return reader, winner_writer

        listener = await direct_tunnel.async_open_direct_tunnel_listener(
            rendezvous.RendezvousServers(
                "tat.example.invalid", "relay.example.invalid"
            ),
            client_id=self._CLIENT_ID,
            local_addresses=("192.0.2.10",),
            listener_server_factory=_DirectServerFactory(),
            listener_port_factory=lambda: 41000,
            signaling_connection_factory=_ShuntConnectionFactory(signaling_response),
        )

        with self.assertRaises(framing.OrbwebProtocolError):
            await listener.async_accept_port_mapping(
                race_connection_factory=connect_peer,
                race_port_factory=lambda: 43000,
                mapping_server_factory=_LoopbackServerFactory(),
                private_value=7,
            )

        self.assertTrue(listener.is_closed)
        self.assertTrue(winner_writer.closed)


class OrbwebShuntTests(unittest.TestCase):
    """Lock the native TCP shunt registration wire format."""

    _CLIENT_ID = "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007"
    _PEER_ID = "peer_ctok_12345678-1234-1234-1234-123456789ABC#042"
    _CANDIDATES = (
        shunt.ShuntCandidate("198.51.100.20", 41000),
        shunt.ShuntCandidate("203.0.113.30", 42000),
    )

    def test_serializes_client_registration_with_native_terminators(self) -> None:
        packet = shunt.ShuntRegistrationRequest(
            client_id=self._CLIENT_ID,
            nat_types=(3, 3),
            candidates=self._CANDIDATES,
        ).packet()

        self.assertEqual(packet.command, 0x2BC)
        self.assertEqual(
            packet.payload,
            (
                f"0;{self._CLIENT_ID};2;3:3;198.51.100.20:41000;203.0.113.30:42000;\0"
            ).encode(),
        )

    def test_serializes_peer_bound_registration_mode(self) -> None:
        packet = shunt.ShuntRegistrationRequest(
            client_id=self._CLIENT_ID,
            peer_id=self._PEER_ID,
            nat_types=(3,),
            candidates=(self._CANDIDATES[0],),
        ).packet()

        self.assertTrue(
            packet.payload.startswith(
                f"1;{self._CLIENT_ID};{self._PEER_ID};1;3;".encode()
            )
        )

    def test_parses_peer_candidates_and_binds_local_identity(self) -> None:
        payload = (
            f"3:3;{self._PEER_ID};{self._CLIENT_ID};"
            "192.0.2.10:43000;198.51.100.40:44000;\0"
        ).encode()
        parsed = shunt.parse_shunt_registration_response(
            framing.BasePacket(
                framing.OrbwebCommand.TCP_SHUNT_REG_RSP,
                payload,
            ),
            expected_client_id=self._CLIENT_ID,
        )

        self.assertEqual(parsed.nat_types, (3, 3))
        self.assertEqual(parsed.peer_id, self._PEER_ID)
        self.assertEqual(parsed.candidates[0].address, "192.0.2.10")
        self.assertEqual(parsed.candidates[1].port, 44000)
        self.assertNotIn(self._CLIENT_ID, repr(parsed))
        self.assertNotIn("192.0.2.10", repr(parsed.candidates[0]))

    def test_rejects_identity_count_and_termination_mismatches(self) -> None:
        with self.assertRaises(ValueError):
            shunt.ShuntRegistrationRequest(
                client_id=self._CLIENT_ID,
                nat_types=(3,),
                candidates=self._CANDIDATES,
            ).packet()

        invalid = framing.BasePacket(
            framing.OrbwebCommand.TCP_SHUNT_REG_RSP,
            f"3;{self._PEER_ID};other;192.0.2.1:1234;".encode(),
        )
        with self.assertRaises(framing.OrbwebProtocolError):
            shunt.parse_shunt_registration_response(invalid)

        mismatched = framing.BasePacket(
            framing.OrbwebCommand.TCP_SHUNT_REG_RSP,
            f"3;{self._PEER_ID};other;192.0.2.1:1234;\0".encode(),
        )
        with self.assertRaises(framing.OrbwebProtocolError):
            shunt.parse_shunt_registration_response(
                mismatched,
                expected_client_id=self._CLIENT_ID,
            )


class OrbwebShuntClientTests(unittest.IsolatedAsyncioTestCase):
    """Exercise shunt retries and rejection handling in memory."""

    _CLIENT_ID = "target_ctok_ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF#007"
    _PEER_ID = "peer_ctok_12345678-1234-1234-1234-123456789ABC#042"

    def _request(self):
        return shunt.ShuntRegistrationRequest(
            client_id=self._CLIENT_ID,
            nat_types=(3,),
            candidates=(shunt.ShuntCandidate("198.51.100.20", 41000),),
        )

    def _response(self):
        payload = (
            f"3;{self._PEER_ID};{self._CLIENT_ID};203.0.113.30:42000;\0"
        ).encode()
        return framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.TCP_SHUNT_REG_RSP,
                payload,
            )
        )

    async def test_registers_on_first_utility_slot_and_returns_peer(self) -> None:
        factory = _ShuntConnectionFactory(self._response())

        result = await shunt_client.async_register_with_shunt(
            utilities=_server_utilities(),
            request=self._request(),
            open_connection=factory,
        )

        self.assertEqual(factory.calls, [("192.0.2.1", 10243)])
        packets = framing.BasePacketBuffer().feed(bytes(factory.writer.data))
        self.assertEqual([packet.command for packet in packets], [0x2BC])
        self.assertEqual(result.peer_id, self._PEER_ID)
        self.assertTrue(factory.writer.closed)

    async def test_retries_without_restarting_partial_read(self) -> None:
        factory = _ShuntConnectionFactory(self._response(), delay=0.025)

        await shunt_client.async_register_with_shunt(
            utilities=_server_utilities(),
            request=self._request(),
            retry_interval=0.01,
            open_connection=factory,
        )

        packets = framing.BasePacketBuffer().feed(bytes(factory.writer.data))
        self.assertGreaterEqual(len(packets), 2)
        self.assertTrue(all(packet.command == 0x2BC for packet in packets))

    async def test_reports_native_failure_command(self) -> None:
        failure = framing.encode_packet(
            framing.BasePacket(
                framing.OrbwebCommand.TCP_SHUNT_REG_FAIL_RSP,
            )
        )
        factory = _ShuntConnectionFactory(failure)

        with self.assertRaises(shunt_client.ShuntRegistrationRejected):
            await shunt_client.async_register_with_shunt(
                utilities=_server_utilities(),
                request=self._request(),
                open_connection=factory,
            )
        self.assertTrue(factory.writer.closed)


class OrbwebRequestTests(unittest.TestCase):
    """Lock the three captured rendezvous request structures."""

    def test_registration_is_variable_length_without_nul(self) -> None:
        packet = requests.ClientRegistrationRequest("client-id").packet()
        self.assertEqual(packet.command, 0x67)
        self.assertEqual(packet.payload, b"client-id")

    def test_host_nic_layout(self) -> None:
        packet = requests.HostNicRequest(
            target_id="target",
            client_id="client",
            local_ip_count=2,
            connection_type_config=8,
            timeout_ms=2000,
        ).packet()

        self.assertEqual(packet.command, 0x77)
        self.assertEqual(len(packet.payload), 0x20C)
        self.assertEqual(packet.payload[0:7], b"target\0")
        self.assertEqual(packet.payload[128:134], b"4.3.17")
        self.assertEqual(packet.payload[255:262], b"client\0")
        self.assertEqual(
            struct.unpack_from("<III", packet.payload, 512),
            (2, 8, 2000),
        )

    def test_host_connection_layout(self) -> None:
        packet = requests.HostConnectionRequest(
            target_id="target",
            client_id="client",
            client_token="token",
            secondary_server="server",
            secondary_server_port=443,
        ).packet()

        self.assertEqual(packet.command, 0x6A)
        self.assertEqual(len(packet.payload), 0x322)
        self.assertEqual(packet.payload[0:7], b"target\0")
        self.assertEqual(packet.payload[258:265], b"client\0")
        self.assertEqual(packet.payload[513:519], b"token\0")
        self.assertEqual(packet.payload[768:775], b"server\0")
        self.assertEqual(struct.unpack_from("<H", packet.payload, 800), (443,))

    def test_secret_fields_are_not_in_object_representations(self) -> None:
        request = requests.HostConnectionRequest(
            target_id="target-secret",
            client_id="client-secret",
            client_token="token-secret",
            secondary_server="server-secret",
            secondary_server_port=443,
        )
        representation = repr(request)
        self.assertNotIn("secret", representation)

    def test_rejects_oversized_and_non_ascii_fields(self) -> None:
        with self.assertRaises(ValueError):
            requests.ClientRegistrationRequest("x" * 256).packet()
        with self.assertRaises(ValueError):
            requests.HostNicRequest(
                target_id="caméra",
                client_id="client",
                local_ip_count=1,
                connection_type_config=8,
                timeout_ms=2000,
            ).packet()


class OrbwebRendezvousTests(unittest.TestCase):
    """Validate the HTTPS bootstrap reconstructed from the native SDK."""

    def test_parses_native_response_fields_in_connection_order(self) -> None:
        result = rendezvous.parse_rendezvous_response(
            {"errno": "0", "relayip": "relay.example", "tatip": "tat.example"}
        )
        self.assertEqual(result.tat_server, "tat.example")
        self.assertEqual(result.relay_server, "relay.example")
        self.assertEqual(
            result.connection_candidates,
            ("tat.example", "relay.example"),
        )

    def test_rejects_server_error_and_invalid_server_fields(self) -> None:
        with self.assertRaises(rendezvous.OrbwebRendezvousRejected):
            rendezvous.parse_rendezvous_response(
                {"errno": 1003, "relayip": "relay", "tatip": "tat"}
            )
        with self.assertRaises(rendezvous.OrbwebRendezvousProtocolError):
            rendezvous.parse_rendezvous_response(
                {"errno": "0", "relayip": "https://relay", "tatip": "tat"}
            )

    def test_client_matches_observed_android_request_shape(self) -> None:
        async def scenario() -> None:
            session = _FakeSession(
                _FakeResponse(
                    200,
                    {"errno": "0", "relayip": "relay", "tatip": "tat"},
                )
            )
            client = rendezvous.OrbwebRendezvousClient(session)
            servers = await client.async_get_servers("camera-session")

            self.assertEqual(servers.connection_candidates, ("tat", "relay"))
            url, kwargs = session.requests[0]
            self.assertEqual(
                url,
                "https://rdz.orbwebsys.com/api/device/connection",
            )
            self.assertEqual(
                kwargs["json"],
                {"role": "client", "id": "camera-session", "token": ""},
            )
            self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

        asyncio.run(scenario())

    def test_request_repr_hides_target_id(self) -> None:
        request = rendezvous.RendezvousRequest("target-secret")
        self.assertNotIn("secret", repr(request))


class OrbwebResponseTests(unittest.TestCase):
    """Lock the two fixed response layouts to capture-derived structures."""

    def test_parses_five_positional_server_addresses(self) -> None:
        addresses = ("192.0.2.1", "192.0.2.2", "192.0.2.1", "192.0.2.1", "192.0.2.2")
        payload = b"".join(value.encode().ljust(32, b"\0") for value in addresses)
        parsed = responses.parse_server_utility_response(
            framing.BasePacket(framing.OrbwebCommand.CONN_REG_CLIENT_RSP, payload)
        )
        self.assertEqual(parsed.addresses, addresses)

    def test_host_nic_response_binds_request_ids_and_version(self) -> None:
        payload = _host_nic_response_payload("target", "client", "4.3.17")
        parsed = responses.parse_host_nic_response(
            framing.BasePacket(framing.OrbwebCommand.CONN_REG_NIC_RSP, payload),
            expected_target_id="target",
            expected_client_id="client",
        )
        self.assertEqual(parsed.sdk_version, "4.3.17")
        self.assertEqual(parsed.host_nic_count, 1)
        self.assertEqual(parsed.connection_type_config, 15)
        self.assertEqual(parsed.reserved, 0)
        self.assertTrue(parsed.uses_qtcp)
        self.assertNotIn("target", repr(parsed))

    def test_host_nic_response_rejects_wrong_identity(self) -> None:
        payload = _host_nic_response_payload("other", "client", "3.2.1")
        packet = framing.BasePacket(
            framing.OrbwebCommand.CONN_REG_NIC_RSP,
            payload,
        )
        with self.assertRaises(framing.OrbwebProtocolError):
            responses.parse_host_nic_response(
                packet,
                expected_target_id="target",
            )


class OrbwebControlTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the complete rendezvous control exchange in memory."""

    async def test_writes_captured_command_sequence(self) -> None:
        server_payload = b"".join(
            value.encode().ljust(32, b"\0")
            for value in ("192.0.2.1", "192.0.2.2") * 2 + ("192.0.2.1",)
        )
        incoming = b"".join(
            framing.encode_packet(packet)
            for packet in (
                framing.BasePacket(
                    framing.OrbwebCommand.CONN_REG_CLIENT_RSP,
                    server_payload,
                ),
                framing.BasePacket(
                    framing.OrbwebCommand.CONN_REG_NIC_RSP,
                    _host_nic_response_payload("target", "client", "4.3.17"),
                ),
                framing.BasePacket(framing.OrbwebCommand.CONN_HOST_RSP),
                framing.BasePacket(framing.OrbwebCommand.CONN_DEREG_RSP),
            )
        )
        reader = asyncio.StreamReader()
        reader.feed_data(incoming)
        reader.feed_eof()
        writer = _MemoryWriter()

        result = await control.async_run_control_exchange(
            reader,
            writer,
            target_id="target",
            client_id="client",
            client_token="token",
            secondary_server="secondary",
            local_ip_count=1,
        )

        decoder = framing.BasePacketBuffer()
        written = decoder.feed(bytes(writer.data))
        self.assertEqual(
            [packet.command for packet in written],
            [0x67, 0x77, 0x6A, 0x73],
        )
        self.assertEqual(writer.drain_calls, 4)
        self.assertEqual(result.host_nic.host_nic_count, 1)
        self.assertEqual(len(result.server_utilities.addresses), 5)

    async def test_stops_on_registration_rejection(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(
            framing.encode_packet(
                framing.BasePacket(framing.OrbwebCommand.CONN_REG_CLIENT_FAIL)
            )
        )
        reader.feed_eof()
        writer = _MemoryWriter()

        with self.assertRaises(control.RendezvousControlRejected):
            await control.async_run_control_exchange(
                reader,
                writer,
                target_id="target",
                client_id="client",
                client_token="token",
                secondary_server="secondary",
                local_ip_count=1,
            )
        self.assertEqual(writer.drain_calls, 1)


class _FakeLanTunnelListener:
    def __init__(self, mapping: object, *, block: bool = False) -> None:
        self.mapping = mapping
        self.block = block
        self.accept_calls: list[dict] = []
        self.closed = False
        self.cancelled = False

    async def async_accept_port_mapping(self, **kwargs):
        self.accept_calls.append(kwargs)
        if self.block:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return self.mapping

    async def async_close(self) -> None:
        self.closed = True


class _AuthenticatedFakeMapping:
    host = "127.0.0.1"

    def __init__(self) -> None:
        self.closed = False

    def local_port(self, remote_port: int) -> int:
        if remote_port != 9001:
            raise KeyError(remote_port)
        return 49001

    async def async_close(self) -> None:
        self.closed = True


class OrbwebLanClientTests(unittest.IsolatedAsyncioTestCase):
    """Verify direct listener/control ordering and shared ownership cleanup."""

    _TARGET = "camera-session"
    _TOKEN = "ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF"
    _CLIENT_ID = f"{_TARGET}_ctok_{_TOKEN}#007"

    def _control_responses(self) -> bytes:
        server_payload = b"".join(
            value.encode().ljust(32, b"\0")
            for value in ("192.0.2.1", "192.0.2.2") * 2 + ("192.0.2.1",)
        )
        return b"".join(
            framing.encode_packet(packet)
            for packet in (
                framing.BasePacket(
                    framing.OrbwebCommand.CONN_REG_CLIENT_RSP,
                    server_payload,
                ),
                framing.BasePacket(
                    framing.OrbwebCommand.CONN_REG_NIC_RSP,
                    _host_nic_response_payload(self._TARGET, self._CLIENT_ID, "4.3.17"),
                ),
                framing.BasePacket(framing.OrbwebCommand.CONN_HOST_RSP),
                framing.BasePacket(framing.OrbwebCommand.CONN_DEREG_RSP),
            )
        )

    async def test_composes_listener_before_control_and_returns_mapping(
        self,
    ) -> None:
        mapping = object()
        listener = _FakeLanTunnelListener(mapping)
        listener_calls = []

        async def open_listener(servers, **kwargs):
            listener_calls.append((servers, kwargs))
            return listener

        writer = _SocketWriter(43000)
        control_calls = []

        async def connect_control(host, port):
            control_calls.append((host, port))
            reader = asyncio.StreamReader()
            reader.feed_data(self._control_responses())
            reader.feed_eof()
            return reader, writer

        result = await lan_client.async_open_lan_rtsp_mapping(
            rendezvous.RendezvousServers(
                "tat.example.invalid", "relay.example.invalid"
            ),
            target_id=self._TARGET,
            identity=identity.OrbwebClientIdentity(self._TOKEN, _counter=6),
            local_addresses=("192.0.2.10",),
            connection_factory=connect_control,
            direct_listener_opener=open_listener,
        )

        self.assertIs(result, mapping)
        self.assertEqual(control_calls, [("tat.example.invalid", 443)])
        self.assertEqual(listener_calls[0][1]["client_id"], self._CLIENT_ID)
        self.assertTrue(listener.closed)
        self.assertTrue(writer.closed)
        self.assertEqual(listener.accept_calls[0]["remote_port"], 6667)
        packets = framing.BasePacketBuffer().feed(bytes(writer.data))
        self.assertEqual(
            [packet.command for packet in packets],
            [0x67, 0x77, 0x6A, 0x73],
        )
        self.assertEqual(
            struct.unpack_from("<III", packets[1].payload, 512),
            (1, 1, 10_000),
        )
        self.assertEqual(
            packets[2].payload[768:800].rstrip(b"\0"),
            b"relay.example.invalid",
        )

    async def test_control_failure_cancels_pending_mapping(self) -> None:
        listener = _FakeLanTunnelListener(object(), block=True)

        async def open_listener(servers, **kwargs):
            return listener

        reader = asyncio.StreamReader()
        reader.feed_data(
            framing.encode_packet(
                framing.BasePacket(framing.OrbwebCommand.CONN_REG_CLIENT_FAIL)
            )
        )
        reader.feed_eof()
        writer = _SocketWriter(43000)

        async def connect_control(host, port):
            return reader, writer

        with self.assertRaises(control.RendezvousControlRejected):
            await lan_client.async_open_lan_rtsp_mapping(
                rendezvous.RendezvousServers(
                    "tat.example.invalid", "relay.example.invalid"
                ),
                target_id=self._TARGET,
                identity=identity.OrbwebClientIdentity(self._TOKEN, _counter=6),
                local_addresses=("192.0.2.10",),
                connection_factory=connect_control,
                direct_listener_opener=open_listener,
            )

        self.assertTrue(listener.cancelled)
        self.assertTrue(listener.closed)
        self.assertTrue(writer.closed)

    async def test_authenticates_mapped_port_before_returning(self) -> None:
        mapping = _AuthenticatedFakeMapping()
        listener = _FakeLanTunnelListener(mapping)

        async def open_listener(_servers, **_kwargs):
            return listener

        writer = _SocketWriter(43000)

        async def connect_control(_host, _port):
            reader = asyncio.StreamReader()
            reader.feed_data(self._control_responses())
            reader.feed_eof()
            return reader, writer

        auth_calls = []

        async def authenticate(host, port, **kwargs):
            auth_calls.append((host, port, kwargs))

        result = await lan_client.async_open_lan_rtsp_mapping(
            rendezvous.RendezvousServers(
                "tat.example.invalid", "relay.example.invalid"
            ),
            target_id=self._TARGET,
            identity=identity.OrbwebClientIdentity(self._TOKEN, _counter=6),
            local_addresses=("192.0.2.10",),
            auth_password="camera-secret",
            connection_factory=connect_control,
            direct_listener_opener=open_listener,
            authentication_exchange=authenticate,
        )

        self.assertIs(result, mapping)
        self.assertEqual(
            listener.accept_calls[0]["additional_remote_ports"],
            (80, 8080, 51108, 9001),
        )
        self.assertEqual(auth_calls[0][0:2], ("127.0.0.1", 49001))
        self.assertEqual(auth_calls[0][2]["p2p_server_id"], self._CLIENT_ID)
        self.assertEqual(auth_calls[0][2]["account"], "orbweb_user001")
        self.assertEqual(auth_calls[0][2]["password"], "camera-secret")
        self.assertFalse(mapping.closed)


class _PoolMapping:
    def __init__(self, port: int) -> None:
        self.host = "127.0.0.1"
        self.port = port
        self.is_closed = False
        self.terminal_error = None

    async def async_close(self) -> None:
        self.is_closed = True


class _PoolRendezvousClient:
    def __init__(self) -> None:
        self.targets: list[str] = []

    async def async_get_servers(self, target_id: str):
        self.targets.append(target_id)
        return rendezvous.RendezvousServers(
            "tat.example.invalid", "relay.example.invalid"
        )


class OrbwebLanMappingPoolTests(unittest.IsolatedAsyncioTestCase):
    """Verify lazy reuse, replacement, and secret-safe diagnostics."""

    async def test_stable_port_survives_upstream_replacement(self) -> None:
        upstream_ports: list[int] = []
        upstream_servers: list[asyncio.AbstractServer] = []

        async def open_upstream(reader, writer) -> None:
            payload = await reader.readexactly(4)
            writer.write(payload.upper())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        for _index in range(2):
            server = await asyncio.start_server(open_upstream, host="127.0.0.1", port=0)
            upstream_servers.append(server)
            upstream_ports.append(server.sockets[0].getsockname()[1])

        connector_calls = 0

        async def connect_upstream():
            nonlocal connector_calls
            port = upstream_ports[connector_calls]
            connector_calls += 1
            return await asyncio.open_connection("127.0.0.1", port)

        stable_port = pool.OrbwebStablePort(connect_upstream)
        await stable_port.async_start()
        original_port = stable_port.port
        try:
            for payload in (b"one!", b"two!"):
                reader, writer = await asyncio.open_connection(
                    stable_port.host, stable_port.port
                )
                writer.write(payload)
                await writer.drain()
                self.assertEqual(await reader.readexactly(4), payload.upper())
                writer.close()
                await writer.wait_closed()

            self.assertEqual(stable_port.port, original_port)
            self.assertEqual(connector_calls, 2)
        finally:
            await stable_port.async_close()
            for server in upstream_servers:
                server.close()
                await server.wait_closed()

    async def test_reuses_mapping_and_replaces_closed_transport(self) -> None:
        rendezvous_client = _PoolRendezvousClient()
        mappings: list[_PoolMapping] = []
        calls: list[dict] = []

        async def open_mapping(servers, **kwargs):
            calls.append({"servers": servers, **kwargs})
            mapping = _PoolMapping(45000 + len(mappings))
            mappings.append(mapping)
            return mapping

        mapping_pool = pool.OrbwebLanMappingPool(
            object(),
            {"192.0.2.10": "private-target"},
            auth_passwords={"192.0.2.10": "camera-secret"},
            rendezvous_client=rendezvous_client,
            source_address_resolver=lambda _host: asyncio.sleep(0, result="192.0.2.20"),
            mapping_opener=open_mapping,
            identity=identity.OrbwebClientIdentity(
                client_token="ABCDEFGH-IJKL-MNOP-QRST-UVWXYZABCDEF",
                _counter=6,
            ),
        )

        first = await mapping_pool.async_get_mapping("192.0.2.10")
        self.assertIs(await mapping_pool.async_get_mapping("192.0.2.10"), first)
        first.is_closed = True
        second = await mapping_pool.async_get_mapping("192.0.2.10")

        second.terminal_error = ConnectionResetError()
        third = await mapping_pool.async_get_mapping("192.0.2.10")

        self.assertIsNot(second, first)
        self.assertIsNot(third, second)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["local_addresses"], ("192.0.2.20",))
        self.assertEqual(calls[0]["auth_password"], "camera-secret")
        self.assertEqual(
            rendezvous_client.targets,
            ["private-target", "private-target", "private-target"],
        )
        self.assertNotIn("private-target", repr(mapping_pool))
        self.assertNotIn("camera-secret", repr(mapping_pool))

        await mapping_pool.async_close()
        self.assertTrue(third.is_closed)
        with self.assertRaises(RuntimeError):
            await mapping_pool.async_get_mapping("192.0.2.10")

    async def test_cloud_key_routes_source_via_rendezvous_server(self) -> None:
        rendezvous_client = _PoolRendezvousClient()
        resolved_hosts: list[str] = []
        calls: list[dict] = []

        async def resolve_source(host: str) -> str:
            resolved_hosts.append(host)
            return "192.0.2.20"

        async def open_mapping(servers, **kwargs):
            calls.append({"servers": servers, **kwargs})
            return _PoolMapping(45000)

        mapping_pool = pool.OrbwebLanMappingPool(
            object(),
            {"cloud:registration-1": "private-target"},
            auth_passwords={"cloud:registration-1": "camera-secret"},
            source_route_hosts={"cloud:registration-1": None},
            rendezvous_client=rendezvous_client,
            source_address_resolver=resolve_source,
            mapping_opener=open_mapping,
        )

        await mapping_pool.async_get_mapping("cloud:registration-1")

        self.assertEqual(resolved_hosts, ["tat.example.invalid"])
        self.assertEqual(calls[0]["local_addresses"], ("192.0.2.20",))
        self.assertEqual(rendezvous_client.targets, ["private-target"])
        await mapping_pool.async_close()


def _host_nic_response_payload(
    target_id: str,
    client_id: str,
    sdk_version: str,
) -> bytes:
    payload = bytearray(0x20C)
    payload[0:128] = target_id.encode().ljust(128, b"\0")
    payload[128:255] = sdk_version.encode().ljust(127, b"\0")
    payload[255:512] = client_id.encode().ljust(257, b"\0")
    struct.pack_into("<III", payload, 512, 1, 15, 0)
    return bytes(payload)


def _nat_response_payload(
    client_id: str,
    *,
    nat_type: int,
    behind: int,
) -> bytes:
    payload = bytearray(340)
    payload[0:255] = client_id.encode().ljust(255, b"\0")
    payload[255:288] = b"192.0.2.10".ljust(33, b"\0")
    struct.pack_into("<H", payload, 288, 42424)
    struct.pack_into("<II", payload, 292, nat_type, behind)
    payload[304:336] = b"198.51.100.20".ljust(32, b"\0")
    struct.pack_into("<H", payload, 336, 52525)
    return bytes(payload)


def _server_utilities():
    return responses.ServerUtilityResponse(
        addresses=(
            "192.0.2.1",
            "192.0.2.2",
            "192.0.2.3",
            "192.0.2.4",
            "192.0.2.5",
        )
    )


if __name__ == "__main__":
    unittest.main()
