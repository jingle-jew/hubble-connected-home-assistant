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
        if isinstance(self._payload, Exception):
            raise self._payload
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

    def test_partner_headers_are_used_only_after_authentication_rejection(self) -> None:
        async def scenario() -> None:
            session = FakeSession(
                [
                    FakeResponse(424, ValueError("non-JSON response")),
                    FakeResponse(
                        201,
                        {"status": "200", "data": {"authentication_token": "t"}},
                    ),
                    FakeResponse(
                        201,
                        {"status": "200", "data": {"authentication_token": "standard"}},
                    ),
                    FakeResponse(200, {"status": "200", "data": []}),
                    FakeResponse(200, {"status": "200", "data": []}),
                ]
            )
            client = cloud.HubbleCloudClient(session)

            with self.assertLogs(cloud.__name__, level="DEBUG") as logs:
                partner = await client.async_authenticate("owner@example.com", "pw")
            self.assertEqual(
                [record.levelname for record in logs.records],
                ["DEBUG", "DEBUG", "DEBUG", "INFO"],
            )
            self.assertIn(
                "Attempting Hubble cloud authentication (hubble profile)",
                logs.output[0],
            )
            self.assertIn("authentication rejected (hubble profile)", logs.output[1])
            self.assertIn(
                "Attempting Hubble cloud authentication (hubble_partner profile)",
                logs.output[2],
            )
            self.assertIn(
                "authentication succeeded (hubble_partner profile)", logs.output[3]
            )
            standard = await client.async_authenticate("other@example.com", "pw")
            await client.async_get_cameras(partner)
            await client.async_get_cameras(standard)

            self.assertEqual(
                session.requests[0][2]["headers"]["X-Auth-Tenant"], "hubble"
            )
            self.assertEqual(
                session.requests[1][2]["headers"]["X-Auth-Tenant"],
                "hubble_partner",
            )
            self.assertEqual(
                session.requests[2][2]["headers"]["X-Auth-Tenant"], "hubble"
            )
            self.assertEqual(
                session.requests[3][2]["headers"]["X-Auth-Tenant"], "hubble_partner"
            )
            self.assertEqual(session.requests[3][2]["params"]["api_key"], "t")
            self.assertEqual(
                session.requests[4][2]["headers"]["X-Auth-Tenant"], "hubble"
            )
            self.assertEqual(session.requests[4][2]["params"]["api_key"], "standard")

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
            session = FakeSession(
                [FakeResponse(200, response), FakeResponse(200, response)]
            )
            client = cloud.HubbleCloudClient(session)
            with (
                self.assertLogs(cloud.__name__, level="WARNING") as logs,
                self.assertRaises(cloud.HubbleCloudAuthError),
            ):
                await client.async_authenticate("owner@example.com", "wrong")
            self.assertEqual(len(logs.records), 1)
            self.assertIn(
                "authentication failed: all profiles were rejected", logs.output[0]
            )

        asyncio.run(scenario())


class HubbleCloudLoggingTests(unittest.IsolatedAsyncioTestCase):
    """Distinguish authentication and discovery outcomes in redacted logs."""

    async def test_authentication_errors_are_distinct_and_redacted(self) -> None:
        login = "private-owner@example.com"
        password = "private-account-password"
        private_url = "https://example.com/?api_key=private-api-token"
        for response, error, message in (
            (
                FakeResponse(200, ValueError(private_url)),
                cloud.HubbleCloudProtocolError,
                "authentication received an invalid response",
            ),
            (
                FakeResponse(200, {"data": {"unexpected": password}}),
                cloud.HubbleCloudProtocolError,
                "authentication received an invalid response",
            ),
            (
                FakeResponse(200, TimeoutError(private_url)),
                cloud.HubbleCloudCannotConnect,
                "could not connect to the cloud service",
            ),
        ):
            with self.subTest(error=error, message=message):
                session = FakeSession([response])
                client = cloud.HubbleCloudClient(session)
                with (
                    self.assertLogs(cloud.__name__, level="DEBUG") as logs,
                    self.assertRaises(error),
                ):
                    await client.async_authenticate(login, password)
                self.assertEqual(len(session.requests), 1)
                self.assertEqual(logs.records[-1].levelname, "WARNING")
                self.assertIn(message, logs.output[-1])
                for value in (login, password, private_url, "private-api-token"):
                    self.assertNotIn(value, "\n".join(logs.output))

    async def test_successful_empty_inventory_is_logged(self) -> None:
        client = cloud.HubbleCloudClient(
            FakeSession(
                [
                    FakeResponse(200, {"data": []}),
                    FakeResponse(200, {"data": {"devices": []}}),
                ]
            )
        )
        with self.assertLogs(cloud.__name__, level="INFO") as logs:
            result = await client.async_get_account_cameras(
                cloud.HubbleCloudSession(token="private-api-token"), (), validate=True
            )
        self.assertEqual(result, ((), ()))
        self.assertIn("owned inventory returned 0 camera(s)", logs.output[0])
        self.assertIn("subscription inventory returned 0 camera(s)", logs.output[1])
        self.assertIn("completed with an empty inventory (0 cameras)", logs.output[2])

    async def test_owned_inventory_error_is_logged_in_both_modes(self) -> None:
        for validate in (False, True):
            for response, error, message in (
                (
                    FakeResponse(401, {}),
                    cloud.HubbleCloudAuthError,
                    "cloud authentication rejected",
                ),
                (
                    FakeResponse(200, {"data": "private-inventory-data"}),
                    cloud.HubbleCloudProtocolError,
                    "invalid cloud response",
                ),
            ):
                with self.subTest(validate=validate, error=error):
                    client = cloud.HubbleCloudClient(
                        FakeSession(
                            [response, FakeResponse(200, {"data": {"devices": []}})]
                        )
                    )
                    session = cloud.HubbleCloudSession(token="private-api-token")
                    with self.assertLogs(cloud.__name__, level="INFO") as logs:
                        if validate:
                            with self.assertRaises(error):
                                await client.async_get_account_cameras(
                                    session, (), validate=True
                                )
                        else:
                            result = await client.async_get_account_cameras(session, ())
                            self.assertEqual(result, ((), ()))
                    self.assertEqual(logs.records[0].levelname, "WARNING")
                    self.assertIn(message, logs.output[0])
                    output = "\n".join(logs.output)
                    if not validate:
                        self.assertIn(
                            "found 0 camera(s) from an incomplete inventory", output
                        )
                    self.assertNotIn("empty inventory", output)
                    self.assertNotIn("private-inventory-data", output)

    async def test_subscription_only_7101_is_recovered_without_manual_id(self) -> None:
        registration_id = "AA7101-synthetic-camera"
        subscription = {"data": {"devices": [{"registration_id": registration_id}]}}
        camera_record = {
            "registration_id": registration_id,
            "name": "Private camera name",
            "mac_address": "02:00:00:00:00:01",
            "orbweb": {
                "sid": "private-orbweb-sid",
                "password": "private-orbweb-password",
                "private-response-key": True,
            },
        }
        for manual_ids in ((), (registration_id,)):
            with self.subTest(manual_lookup=bool(manual_ids)):
                session = FakeSession(
                    [
                        FakeResponse(
                            200, {"data": {"authentication_token": "private-api-token"}}
                        ),
                        FakeResponse(200, {"data": []}),
                        FakeResponse(200, subscription),
                        FakeResponse(200, {"data": camera_record}),
                    ]
                )
                client = cloud.HubbleCloudClient(session)
                with self.assertLogs(cloud.__name__, level="DEBUG") as logs:
                    auth = await client.async_authenticate(
                        "owner@example.com", "private-account-password"
                    )
                    inventory, commands = await client.async_get_account_cameras(
                        auth, manual_ids
                    )
                output = "\n".join(logs.output)
                self.assertIn("authentication succeeded (hubble profile)", output)
                self.assertIn("subscription inventory returned 1 camera(s)", output)
                self.assertEqual(len(session.requests), 4)
                self.assertTrue(
                    session.requests[3][1].endswith(f"/{registration_id}.json")
                )
                (discovered,) = inventory
                self.assertTrue(discovered.has_orbweb_credentials)
                self.assertEqual(commands, inventory if manual_ids else ())
                self.assertIn(
                    "individual lookup recovered 1 subscription camera(s)", output
                )
                self.assertIn("discovery found 1 camera(s)", output)
                for value in (
                    registration_id,
                    "Private camera name",
                    "02:00:00:00:00:01",
                    "private-api-token",
                    "owner@example.com",
                    "private-account-password",
                    "private-orbweb-sid",
                    "private-orbweb-password",
                    "private-response-key",
                ):
                    self.assertNotIn(value, output)

    async def test_subscription_lookups_preserve_owned_and_3667_cameras(self) -> None:
        owned = "AA7101-synthetic-owned"
        existing = "AA3667-synthetic-camera"
        new = "AA7101-synthetic-camera"
        unrelated = "AA9999-synthetic-device"
        session = FakeSession(
            [
                FakeResponse(200, {"data": [{"registration_id": owned}]}),
                FakeResponse(
                    200,
                    {
                        "data": {
                            "devices": [
                                {"registration_id": key}
                                for key in (owned, existing, existing, new, unrelated)
                            ]
                        }
                    },
                ),
                FakeResponse(200, {"data": {"registration_id": existing}}),
                FakeResponse(
                    200,
                    {
                        "data": {
                            "registration_id": new,
                            "orbweb": {"sid": "private-sid"},
                        }
                    },
                ),
            ]
        )
        with self.assertLogs(cloud.__name__, level="INFO") as logs:
            inventory, commands = await cloud.HubbleCloudClient(
                session
            ).async_get_account_cameras(
                cloud.HubbleCloudSession(token="test-token"), ()
            )
        self.assertEqual(
            [camera.registration_id for camera in inventory], [owned, existing, new]
        )
        self.assertEqual([camera.registration_id for camera in commands], [existing])
        self.assertEqual(len(session.requests), 4)
        self.assertFalse(inventory[-1].has_orbweb_credentials)
        self.assertIn("recovered 2 subscription camera(s)", "\n".join(logs.output))
        self.assertIn("outside supported automatic lookup", "\n".join(logs.output))

    async def test_failed_7101_lookup_keeps_other_cameras_and_manual_validation(
        self,
    ) -> None:
        registration_id = "AA7101-synthetic-camera"
        existing = "AA3667-synthetic-camera"
        for case, response, error, reason in (
            (
                "detail request failed",
                FakeResponse(404, {}),
                cloud.HubbleCloudCannotConnect,
                "cloud connection failed",
            ),
            (
                "missing registration ID",
                FakeResponse(200, {"data": {}}),
                cloud.HubbleCloudProtocolError,
                "invalid cloud response",
            ),
            (
                "different registration ID",
                FakeResponse(200, {"data": {"registration_id": "AA7101-other-camera"}}),
                cloud.HubbleCloudProtocolError,
                "invalid cloud response",
            ),
        ):
            with self.subTest(case=case):
                session = FakeSession(
                    [
                        FakeResponse(200, {"data": [{"registration_id": existing}]}),
                        FakeResponse(
                            200,
                            {
                                "data": {
                                    "devices": [{"registration_id": registration_id}]
                                }
                            },
                        ),
                        response,
                    ]
                    * 2
                )
                client = cloud.HubbleCloudClient(session)
                auth = cloud.HubbleCloudSession(token="test-token")
                with self.assertLogs(cloud.__name__, level="DEBUG") as logs:
                    inventory, commands = await client.async_get_account_cameras(
                        auth, (), validate=True
                    )
                    with self.assertRaises(error):
                        await client.async_get_account_cameras(
                            auth, (registration_id,), validate=True
                        )
                self.assertEqual(
                    [camera.registration_id for camera in inventory], [existing]
                )
                self.assertEqual(commands, inventory)
                output = "\n".join(logs.output)
                self.assertIn(f"individual camera lookup failed: {reason}", output)
                self.assertIn("found 1 camera(s) from an incomplete inventory", output)
                self.assertNotIn(registration_id, output)


if __name__ == "__main__":
    unittest.main()
