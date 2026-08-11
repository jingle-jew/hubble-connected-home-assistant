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

    def test_model_code_is_read_from_registration_id(self) -> None:
        camera = cloud.HubbleCloudCamera(
            cloud_id=42,
            name="Basement",
            registration_id="AA3667-owned-camera",
            mac_address=None,
            device_model_id=None,
            firmware_version=None,
            status="online",
            is_available=True,
            time_zone=None,
            updated_at=None,
            snapshot_url=None,
            network_strength=None,
            remote_ip=None,
            cloud_temperature=None,
            settings={},
        )

        self.assertEqual(camera.model_code, "3667")

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

    def test_single_camera_uses_v6_to_recover_orbweb_credentials(self) -> None:
        async def scenario() -> None:
            registration_id = "01366700000000000000000000"
            session = FakeSession(
                [
                    FakeResponse(
                        200,
                        {
                            "status": "200",
                            "data": {
                                "name": "Basement",
                                "registration_id": registration_id,
                                "orbweb": {
                                    "sid": "private-target",
                                    "password": "private-password",
                                },
                            },
                        },
                    )
                ]
            )
            client = cloud.HubbleCloudClient(session)

            camera = await client.async_get_camera(
                cloud.HubbleCloudSession(token="secret-token"), registration_id
            )

            self.assertTrue(camera.has_orbweb_credentials)
            self.assertIn("/v6/devices/", session.requests[0][1])
            self.assertNotIn("private-password", repr(camera))

        asyncio.run(scenario())

    def test_subscription_inventory_exposes_camera_omitted_from_own(self) -> None:
        async def scenario() -> None:
            omitted_id = "01366700000000000000000000"
            session = FakeSession(
                [
                    FakeResponse(
                        200,
                        {
                            "status": "200",
                            "data": {
                                "devices": [
                                    {
                                        "name": "Basement",
                                        "plan_id": "free",
                                        "registration_id": omitted_id,
                                    },
                                    {
                                        "name": "Duplicate",
                                        "plan_id": "free",
                                        "registration_id": omitted_id,
                                    },
                                ]
                            },
                        },
                    )
                ]
            )
            client = cloud.HubbleCloudClient(session)

            identifiers = await client.async_get_subscription_camera_ids(
                cloud.HubbleCloudSession(token="secret-token")
            )

            self.assertEqual(identifiers, (omitted_id,))
            method, url, kwargs = session.requests[0]
            self.assertEqual(method, "get")
            self.assertEqual(
                url,
                "https://api.hubble.in/v2/devices/subscriptions",
            )
            self.assertEqual(kwargs["params"]["api_key"], "secret-token")

        asyncio.run(scenario())

    def test_subscription_inventory_rejects_missing_device_list(self) -> None:
        with self.assertRaises(cloud.HubbleCloudProtocolError):
            cloud.parse_subscription_camera_ids(
                {"status": "200", "data": {"plan_device_availability": {}}}
            )

    def test_parse_single_camera_omitted_from_inventory(self) -> None:
        camera = cloud.parse_camera(
            {
                "status": "200",
                "data": {
                    "id": 43,
                    "name": "Basement",
                    "registration_id": "01366700000000000000000000",
                    "mac_address": "02:00:00:00:00:02",
                    "firmware_version": "03.40.00",
                    "device_model_id": 901,
                    "attributes": {"p2p_protocol": "04_00"},
                },
            }
        )

        self.assertEqual(camera.name, "Basement")
        self.assertEqual(camera.registration_id[2:6], "3667")
        self.assertFalse(camera.has_orbweb_credentials)

    def test_parse_cloud_camera_ids_rejects_duplicates_and_bad_values(self) -> None:
        self.assertEqual(
            cloud.parse_cloud_camera_ids("camera123,\ncamera456"),
            ("camera123", "camera456"),
        )
        with self.assertRaises(cloud.HubbleCloudConfigError):
            cloud.parse_cloud_camera_ids("camera123,camera123")
        with self.assertRaises(cloud.HubbleCloudConfigError):
            cloud.parse_cloud_camera_ids("not/a/camera")

    def test_temperature_command_follows_job_until_complete(self) -> None:
        async def scenario() -> None:
            session = FakeSession(
                [
                    FakeResponse(
                        202,
                        {
                            "id": "job_12345678",
                            "responsePojo": {"status": 202},
                        },
                    ),
                    FakeResponse(202, {"data": {"status": "202"}}),
                    FakeResponse(
                        200,
                        {
                            "data": {
                                "status": "200",
                                "output": {
                                    "DeviceResponseMessage": (
                                        "value_temperature: 24.21"
                                    )
                                },
                            }
                        },
                    ),
                ]
            )
            client = cloud.HubbleCloudClient(session, job_poll_interval=0)
            auth = cloud.HubbleCloudSession(token="secret-token")

            temperature = await client.async_get_temperature(
                auth, "01366700000000000000000000"
            )

            self.assertEqual(temperature, 24.21)
            method, url, kwargs = session.requests[0]
            self.assertEqual(method, "post")
            self.assertTrue(url.endswith("/publish_command.json"))
            self.assertEqual(kwargs["json"]["command"], "VALUE_TEMPERATURE")
            self.assertIsNone(kwargs["json"]["attributes"])
            self.assertEqual(session.requests[1][0], "get")
            self.assertIn("/v1/jobs/", session.requests[1][1])

        asyncio.run(scenario())

    def test_integer_command_job_requires_matching_prefix_and_range(self) -> None:
        payload = {
            "data": {
                "status": "200",
                "output": {"DeviceResponseMessage": "get_wifi_strength: 85"},
            }
        }
        self.assertEqual(
            cloud.parse_integer_job(payload, "GET_WIFI_STRENGTH", 0, 100),
            85,
        )
        with self.assertRaises(cloud.HubbleCloudProtocolError):
            cloud.parse_integer_job(payload, "GET_VIDEO_BITRATE", 0, 100_000)
        with self.assertRaises(cloud.HubbleCloudProtocolError):
            cloud.parse_integer_job(payload, "GET_WIFI_STRENGTH", 0, 50)

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
