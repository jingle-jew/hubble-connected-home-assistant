"""Tests for the pure-Python RTSP boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "hubble_connected" / "rtsp.py"
)
SPEC = importlib.util.spec_from_file_location("hubble_rtsp", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
rtsp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rtsp
SPEC.loader.exec_module(rtsp)

LOCAL_MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "hubble_connected" / "local.py"
)
LOCAL_SPEC = importlib.util.spec_from_file_location("hubble_local", LOCAL_MODULE_PATH)
assert LOCAL_SPEC is not None and LOCAL_SPEC.loader is not None
local = importlib.util.module_from_spec(LOCAL_SPEC)
sys.modules[LOCAL_SPEC.name] = local
LOCAL_SPEC.loader.exec_module(local)


class HubbleRtspTests(unittest.TestCase):
    """Exercise URL handling and the non-destructive RTSP probe."""

    def test_url_normalizes_path_and_escapes_credentials(self) -> None:
        endpoint = rtsp.HubbleRtspEndpoint(
            "192.0.2.10", 11046, "blinkhd", "user@example.com", "p:a ss"
        )
        self.assertEqual(endpoint.path, "/blinkhd")
        self.assertEqual(
            endpoint.url,
            "rtsp://user%40example.com:p%3Aa%20ss@192.0.2.10:11046/blinkhd",
        )
        self.assertEqual(endpoint.redacted_url, "rtsp://192.0.2.10:11046/blinkhd")

    def test_direct_rtsp_backend_forces_udp_transport(self) -> None:
        self.assertEqual(
            rtsp.stream_options_for_backend("local_rtsp"),
            {"rtsp_transport": "udp"},
        )

    def test_orbweb_mapping_forces_interleaved_tcp_transport(self) -> None:
        self.assertEqual(
            rtsp.stream_options_for_backend("orbweb_lan"),
            {"rtsp_transport": "tcp"},
        )
        self.assertEqual(rtsp.stream_options_for_backend("unknown"), {})

    def test_probe_accepts_rtsp_response(self) -> None:
        async def scenario() -> None:
            request_seen = asyncio.Event()

            async def handle(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                request = await reader.readuntil(b"\r\n\r\n")
                self.assertTrue(request.startswith(b"OPTIONS rtsp://"))
                self.assertIn(b"Authorization: Basic ", request)
                request_seen.set()
                writer.write(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                await rtsp.async_probe_rtsp(
                    rtsp.HubbleRtspEndpoint(
                        "127.0.0.1", port, "/blinkhd", "user", "pass"
                    )
                )
            self.assertTrue(request_seen.is_set())

        asyncio.run(scenario())

    def test_candidate_probe_keeps_only_rtsp_endpoints(self) -> None:
        async def scenario() -> None:
            async def handle(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            good_port = server.sockets[0].getsockname()[1]
            unused_server = await asyncio.start_server(handle, "127.0.0.1", 0)
            bad_port = unused_server.sockets[0].getsockname()[1]
            unused_server.close()
            await unused_server.wait_closed()
            good = rtsp.HubbleRtspEndpoint(
                "127.0.0.1", good_port, "/blinkhd", "user", "pass"
            )
            bad = rtsp.HubbleRtspEndpoint(
                "127.0.0.1", bad_port, "/blinkhd", "user", "pass"
            )
            async with server:
                found = await rtsp.async_probe_rtsp_candidates((good, bad), timeout=1.0)
            self.assertEqual(found, (good,))

        asyncio.run(scenario())

    def test_local_camera_list_and_temperature_parser(self) -> None:
        specs = local.parse_local_camera_specs(
            "Nursery=192.168.50.10, Office=192.168.50.11"
        )
        self.assertEqual(
            [(spec.name, spec.host) for spec in specs],
            [
                ("Nursery", "192.168.50.10"),
                ("Office", "192.168.50.11"),
            ],
        )
        self.assertEqual(local.parse_temperature("value_temperature: 21.6"), 21.6)
        self.assertEqual(local.parse_temperature("23.9"), 23.9)
        self.assertEqual(
            local.parse_command_value("get_resolution", "get_resolution: 720p_926"),
            "720p_926",
        )
        self.assertEqual(
            local.parse_int_value("get_wifi_strength", "get_wifi_strength: 98", 0, 100),
            98,
        )
        self.assertEqual(
            local.parse_mac_address("020000000001"),
            "02:00:00:00:00:01",
        )
        self.assertEqual(
            local.parse_mac_address("get_mac_address: 02:00:00:00:00:02"),
            "02:00:00:00:00:02",
        )

    def test_optional_getters_do_not_hide_supported_data(self) -> None:
        class PartialCameraClient(local.HubbleLocalClient):
            async def _async_command(self, command, timeout=5.0, value=None) -> str:
                del timeout, value
                if command in {
                    "get_default_version",
                    "get_wifi_connection_state",
                }:
                    raise local.HubbleLocalProtocolError("HTTP status 204")
                responses = {
                    "get_mac_address": ("get_mac_address: 02:00:00:00:00:02"),
                    "get_udid": "get_udid: local-test",
                    "get_version": "",
                    "get_model": "get_model: 3667",
                    "get_soc_version": "get_soc_version: -1",
                    "get_resolution": "get_resolution: 0",
                    "get_spk_volume": "",
                    "get_time_zone": "get_time_zone: -04.00",
                    "get_motion_area": "get_motion_area: 0",
                    "value_temperature": "value_temperature: 23.74",
                    "get_wifi_strength": "",
                    "get_video_bitrate": "",
                    "get_brightness": "get_brightness: 4",
                    "get_contrast": "get_contrast: 5",
                    "get_night_vision": "get_night_vision: 0",
                    "get_blink_led": "get_blink_led: 0",
                    "value_flipup": "value_flipup: 0",
                }
                return responses[command]

        async def scenario() -> None:
            client = PartialCameraClient(
                local.HubbleLocalCameraSpec("Legacy", "192.168.50.12")
            )
            data = await client.async_update()
            self.assertTrue(data.available)
            self.assertEqual(data.temperature, 23.74)
            self.assertEqual(data.model_code, "3667")
            self.assertIsNone(data.wifi_connection_state)
            self.assertIsNone(data.video_bitrate)
            self.assertEqual(data.brightness, 4)
            self.assertEqual(data.contrast, 5)
            self.assertEqual(data.night_vision, 0)
            self.assertEqual(data.blink_led, 0)
            self.assertEqual(data.flipup, 0)

        asyncio.run(scenario())

    def test_image_levels_are_read_and_written_with_verified_bounds(self) -> None:
        class ImageCameraClient(local.HubbleLocalClient):
            def __init__(self) -> None:
                super().__init__(
                    local.HubbleLocalCameraSpec("Nursery", "192.168.50.13")
                )
                self.commands: list[tuple[str, str | None]] = []

            async def _async_command(self, command, timeout=5.0, value=None) -> str:
                del timeout
                self.commands.append((command, value))
                return f"{command}: 0"

        async def scenario() -> None:
            client = ImageCameraClient()

            await client.async_set_brightness(1)
            await client.async_set_brightness(8)
            await client.async_set_contrast(1)
            await client.async_set_contrast(8)

            self.assertEqual(
                client.commands,
                [
                    ("set_brightness", "1"),
                    ("set_brightness", "8"),
                    ("set_contrast", "1"),
                    ("set_contrast", "8"),
                ],
            )
            with self.assertRaises(local.HubbleLocalConfigError):
                await client.async_set_brightness(0)
            with self.assertRaises(local.HubbleLocalConfigError):
                await client.async_set_brightness(9)
            with self.assertRaises(local.HubbleLocalConfigError):
                await client.async_set_contrast(0)
            with self.assertRaises(local.HubbleLocalConfigError):
                await client.async_set_contrast(9)

        asyncio.run(scenario())

    def test_0667_image_level_entities_are_suppressed(self) -> None:
        self.assertFalse(local.model_supports_image_level_entities("0667"))
        self.assertTrue(local.model_supports_image_level_entities("1667"))
        self.assertTrue(local.model_supports_image_level_entities(None))


if __name__ == "__main__":
    unittest.main()
