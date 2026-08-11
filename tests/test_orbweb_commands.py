"""Tests for 3667 read-only getters carried by an Orbweb LAN mapping."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

INTEGRATION_PATH = (
    Path(__file__).parents[1] / "custom_components" / "hubble_connected"
)
PACKAGE = "hubble_orbweb_commands_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(INTEGRATION_PATH)]
sys.modules[PACKAGE] = package


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cloud = load(f"{PACKAGE}.cloud", INTEGRATION_PATH / "cloud.py")
local = load(f"{PACKAGE}.local", INTEGRATION_PATH / "local.py")
load(f"{PACKAGE}.discovery", INTEGRATION_PATH / "discovery.py")
bindings = load(
    f"{PACKAGE}.stream_bindings",
    INTEGRATION_PATH / "stream_bindings.py",
)
orbweb_package = types.ModuleType(f"{PACKAGE}.orbweb")
orbweb_package.__path__ = [str(INTEGRATION_PATH / "orbweb")]
sys.modules[orbweb_package.__name__] = orbweb_package
pool_stub = types.ModuleType(f"{PACKAGE}.orbweb.pool")
pool_stub.OrbwebLanMappingPool = object
pool_stub.OrbwebStablePort = object
sys.modules[pool_stub.__name__] = pool_stub
commands = load(
    f"{PACKAGE}.orbweb.commands",
    INTEGRATION_PATH / "orbweb" / "commands.py",
)


def camera(
    *,
    registration_id: str = "AA3667-owned-camera",
    mac_address: str | None = "02:00:00:00:00:03",
    orbweb_sid: str | None = "orbweb-target",
    orbweb_password: str | None = "orbweb-secret",
):
    return cloud.HubbleCloudCamera(
        cloud_id=42,
        name="Nursery",
        registration_id=registration_id,
        mac_address=mac_address,
        device_model_id=167,
        firmware_version="1.2.3",
        status="online",
        is_available=True,
        time_zone=-4,
        updated_at="2026-08-11T12:00:00Z",
        snapshot_url=None,
        network_strength="90",
        remote_ip=None,
        cloud_temperature=22.0,
        settings={},
        orbweb_sid=orbweb_sid,
        orbweb_password=orbweb_password,
    )


class _StablePort:
    host = "127.0.0.1"
    is_closed = False

    def __init__(self, port: int) -> None:
        self.port = port


class _MappingPool:
    def __init__(self, stable_port: _StablePort) -> None:
        self.stable_port = stable_port
        self.calls: list[tuple[str, int]] = []
        self.mapping_calls: list[str] = []

    async def async_get_mapping(self, key: str):
        self.mapping_calls.append(key)
        return object()

    async def async_get_stable_mapping(self, key: str, remote_port: int):
        self.calls.append((key, remote_port))
        return self.stable_port


class HubbleOrbwebCommandBindingTests(unittest.TestCase):
    """Keep getter routing independent from the selected video backend."""

    def test_direct_rtsp_3667_still_gets_local_command_route(self) -> None:
        spec = local.HubbleLocalCameraSpec(
            name="Nursery",
            host="192.168.50.12",
            source="cloud_arp",
            cloud_mac="020000000003",
        )

        result = bindings.build_orbweb_command_bindings((camera(),), (spec,))

        self.assertEqual(len(result), 1)
        binding = result[0]
        self.assertEqual(binding.registration_id, "AA3667-owned-camera")
        self.assertEqual(binding.key, spec.host)
        self.assertEqual(binding.route_host, spec.host)
        self.assertNotIn("orbweb-target", repr(binding))
        self.assertNotIn("orbweb-secret", repr(binding))

    def test_3667_can_route_without_arp_match(self) -> None:
        result = bindings.build_orbweb_command_bindings(
            (camera(mac_address=None),),
            (),
        )

        self.assertEqual(result[0].key, "cloud:AA3667-owned-camera")
        self.assertIsNone(result[0].route_host)

    def test_non_3667_keeps_existing_command_transport(self) -> None:
        self.assertEqual(
            bindings.build_orbweb_command_bindings(
                (camera(registration_id="AA1667-owned-camera"),),
                (),
            ),
            (),
        )


class HubbleOrbwebCommandClientTests(unittest.IsolatedAsyncioTestCase):
    """Lock the verified paths, parsing, and mapped camera port."""

    async def test_reads_verified_getters_from_mapped_port_80(self) -> None:
        request_lines: list[str] = []
        responses = {
            "value_temperature": "value_temperature: 22.67",
            "get_wifi_strength": "get_wifi_strength: 85",
            "get_video_bitrate": "get_video_bitrate: 600",
            "get_brightness": "get_brightness: 8",
            "get_night_vision": "get_night_vision: 0",
            "value_flipup": "value_flipup: 1",
        }

        async def handle(reader, writer) -> None:
            request = await reader.readuntil(b"\r\n\r\n")
            request_line = request.decode("ascii").split("\r\n", 1)[0]
            request_lines.append(request_line)
            command = request_line.split("command=", 1)[1].split(" ", 1)[0]
            body = responses[command].encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        mappings = _MappingPool(_StablePort(port))
        client = commands.HubbleOrbwebCommandClient(mappings, "camera-key")
        try:
            await client.async_prepare()
            self.assertEqual(await client.async_get_temperature(), 22.67)
            self.assertEqual(await client.async_get_wifi_strength(), 85)
            self.assertEqual(await client.async_get_video_bitrate(), 600)
            self.assertEqual(await client.async_get_brightness(), 8)
            self.assertEqual(await client.async_get_night_vision(), 0)
            self.assertEqual(await client.async_get_flipup(), 1)
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(mappings.calls, [("camera-key", 80)])
        self.assertEqual(mappings.mapping_calls, ["camera-key"])
        self.assertEqual(
            request_lines,
            [
                "GET /?action=command&command=value_temperature HTTP/1.1",
                "GET /?action=command&command=get_wifi_strength HTTP/1.1",
                "GET /?action=command&command=get_video_bitrate HTTP/1.1",
                "GET /?action=command&command=get_brightness HTTP/1.1",
                "GET /?action=command&command=get_night_vision HTTP/1.1",
                "GET /?action=command&command=value_flipup HTTP/1.1",
            ],
        )

    async def test_rejects_3667_contrast_getter_http_204(self) -> None:
        async def handle(reader, writer) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = commands.HubbleOrbwebCommandClient(
            _MappingPool(_StablePort(port)),
            "camera-key",
        )
        try:
            with self.assertRaisesRegex(
                commands.HubbleOrbwebCommandProtocolError,
                "Unexpected HTTP status 204",
            ):
                await client.async_get_contrast()
        finally:
            server.close()
            await server.wait_closed()

    async def test_writes_verified_3667_settings_with_acknowledgement(self) -> None:
        request_lines: list[str] = []
        responses = {
            "set_brightness": b"set_brightness: 0",
            "set_video_bitrate": b'{"value":"0"}',
            "set_night_vision": b"set_night_vision: 0",
            "set_flipup": b'{"value":0}',
        }

        async def handle(reader, writer) -> None:
            request = await reader.readuntil(b"\r\n\r\n")
            request_line = request.decode("ascii").split("\r\n", 1)[0]
            request_lines.append(request_line)
            command = request_line.split("command=", 1)[1].split("&", 1)[0]
            body = responses[command]
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = commands.HubbleOrbwebCommandClient(
            _MappingPool(_StablePort(port)),
            "camera-key",
        )
        try:
            await client.async_set_brightness(4)
            await client.async_set_video_bitrate(300)
            await client.async_set_night_vision(2)
            await client.async_set_flipup(True)
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(
            request_lines,
            [
                "GET /?action=command&command=set_brightness&value=4 HTTP/1.1",
                "GET /?action=command&command=set_video_bitrate&value=300 HTTP/1.1",
                "GET /?action=command&command=set_night_vision&value=2 HTTP/1.1",
                "GET /?action=command&command=set_flipup&value=1 HTTP/1.1",
            ],
        )

    async def test_rejects_unverified_3667_setting_values(self) -> None:
        client = commands.HubbleOrbwebCommandClient(
            _MappingPool(_StablePort(1)),
            "camera-key",
        )
        with self.assertRaises(commands.HubbleOrbwebCommandProtocolError):
            await client.async_set_brightness(0)
        with self.assertRaises(commands.HubbleOrbwebCommandProtocolError):
            await client.async_set_video_bitrate(200)
        with self.assertRaises(commands.HubbleOrbwebCommandProtocolError):
            await client.async_set_night_vision(3)

    async def test_rejects_malformed_getter_response(self) -> None:
        async def handle(_reader, writer) -> None:
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"
                b"get_wifi_strength: invalid"
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = commands.HubbleOrbwebCommandClient(
            _MappingPool(_StablePort(port)),
            "camera-key",
        )
        try:
            with self.assertRaises(commands.HubbleOrbwebCommandProtocolError):
                await client.async_get_wifi_strength()
        finally:
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
