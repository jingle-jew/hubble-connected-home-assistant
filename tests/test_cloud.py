"""Tests for the isolated Hubble cloud boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "hubble_connected" / "cloud.py"
)
SPEC = importlib.util.spec_from_file_location("hubble_cloud", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
cloud = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cloud
SPEC.loader.exec_module(cloud)


class FakeResponse:
    """Minimal aiohttp-like response context manager."""

    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self, *, content_type=None):
        return self._payload


class FakeSession:
    """Capture request shapes without making network calls."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs):
        self.requests.append(("post", url, kwargs))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs):
        self.requests.append(("get", url, kwargs))
        return self.responses.pop(0)


class HubbleCloudTests(unittest.TestCase):
    """Validate authentication and inventory parsing contracts."""

    def test_parse_inventory_preserves_stream_and_diagnostic_fields(self) -> None:
        cameras = cloud.parse_camera_inventory(
            {
                "status": "200",
                "data": [
                    {
                        "id": 42,
                        "name": "Nursery",
                        "registration_id": "registration-1",
                        "mac_address": "02:00:00:00:00:01",
                        "device_model_id": 167,
                        "firmware_version": "1.2.3",
                        "status": "online",
                        "is_available": True,
                        "time_zone": -4,
                        "updated_at": "2026-08-07T12:00:00Z",
                        "snaps_url": "https://example.invalid/snapshot.jpg",
                        "orbweb": {"sid": "sid-value", "password": "secret"},
                        "device_settings": [
                            {"key": "night_vision", "value": "auto"},
                            {"key": "temperature_unit", "value": "C"},
                        ],
                        "device_status": {
                            "environment": {"temperature": 22.5},
                            "device": {
                                "network_strength": "95",
                                "remote_ip": "192.0.2.1",
                            },
                        },
                    }
                ],
            }
        )
        self.assertEqual(len(cameras), 1)
        camera = cameras[0]
        self.assertEqual(camera.name, "Nursery")
        self.assertEqual(camera.cloud_temperature, 22.5)
        self.assertEqual(camera.network_strength, "95")
        self.assertEqual(camera.settings["temperature_unit"], "C")
        self.assertTrue(camera.has_orbweb_credentials)
        self.assertNotIn("secret", repr(camera))

    def test_client_matches_observed_android_request_shape(self) -> None:
        async def scenario() -> None:
            session = FakeSession(
                [
                    FakeResponse(
                        200,
                        {"status": "200", "data": {"authentication_token": "t"}},
                    ),
                    FakeResponse(200, {"status": "200", "data": []}),
                ]
            )
            client = cloud.HubbleCloudClient(session)
            auth = await client.async_authenticate("owner@example.com", "pw")
            cameras = await client.async_get_cameras(auth)

            self.assertEqual(cameras, ())
            method, url, kwargs = session.requests[0]
            self.assertEqual(method, "post")
            self.assertEqual(
                url,
                "https://api.hubble.in/v4/users/authentication_token.json",
            )
            self.assertEqual(
                kwargs["json"],
                {"login": "owner@example.com", "password": "pw"},
            )
            self.assertEqual(kwargs["headers"]["X-Auth-Tenant"], "hubble")
            self.assertEqual(kwargs["headers"]["X-Application-Platform"], "android")

            method, url, kwargs = session.requests[1]
            self.assertEqual(method, "get")
            self.assertEqual(url, "https://api.hubble.in/v6/devices/own.json")
            self.assertEqual(kwargs["params"]["suppress_response_codes"], "1")
            self.assertEqual(kwargs["params"]["api_key"], "t")

        asyncio.run(scenario())

    def test_auth_failure_is_distinct_from_transport_failure(self) -> None:
        async def scenario() -> None:
            response = {
                "status": "401",
                "message": "authentication failed",
            }
            session = FakeSession([FakeResponse(200, response)])
            client = cloud.HubbleCloudClient(session)
            with self.assertRaises(cloud.HubbleCloudAuthError):
                await client.async_authenticate("owner@example.com", "wrong")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
