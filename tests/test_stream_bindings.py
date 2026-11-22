"""Tests for cloud/LAN camera stream binding selection."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

PACKAGE = "hubble_stream_bindings_test"
package = types.ModuleType(PACKAGE)
package.__path__ = []
sys.modules[PACKAGE] = package


def load(name: str):
    path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "hubble_connected"
        / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("const")
cloud = load("cloud")
local = load("local")
load("discovery")
manual = load("manual_orbweb")
rtsp = load("rtsp")
bindings = load("stream_bindings")


def camera(
    *,
    registration_id: str = "registration-1",
    name: str = "Basement",
    mac_address: str | None = None,
    orbweb_sid: str | None = "orbweb-target",
    orbweb_password: str | None = "orbweb-secret",
):
    return cloud.HubbleCloudCamera(
        cloud_id=42,
        name=name,
        registration_id=registration_id,
        mac_address=mac_address,
        device_model_id=167,
        firmware_version="1.2.3",
        status="online",
        is_available=True,
        time_zone=-4,
        updated_at="2026-08-10T20:00:00Z",
        snapshot_url=None,
        network_strength="90",
        remote_ip=None,
        cloud_temperature=22.0,
        settings={},
        orbweb_sid=orbweb_sid,
        orbweb_password=orbweb_password,
    )


class HubbleStreamBindingTests(unittest.TestCase):
    """Keep Orbweb video independent from optional LAN metadata."""

    def test_manual_vm85_uses_separate_credentials_without_exposing_them(self) -> None:
        camera = manual.build_manual_orbweb_camera(
            name="Nursery VM85",
            host="192.168.50.20",
            target_id="synthetic-vm85-sid",
            auth_account="vm85-account",
            auth_password="orbweb-secret",
            rtsp_username="rtsp-user",
            rtsp_password=" stream@secret ",
        )
        binding = bindings.build_manual_orbweb_stream_binding(camera)
        endpoint = rtsp.HubbleRtspEndpoint(
            "127.0.0.1",
            31000,
            binding.rtsp_path,
            binding.rtsp_username,
            binding.rtsp_password,
        )
        self.assertEqual(binding.route_host, camera.host)
        self.assertEqual(binding.auth_account, "vm85-account")
        self.assertEqual(binding.auth_password, "orbweb-secret")
        self.assertEqual(binding.rtsp_port, 6667)
        self.assertEqual(
            endpoint.url,
            "rtsp://rtsp-user:%20stream%40secret%20@127.0.0.1:31000/blinkhd",
        )
        self.assertEqual(binding.unique_id, f"{camera.key}:camera")
        for private_value in (
            camera.name,
            camera.host,
            camera.target_id,
            camera.auth_account,
            camera.auth_password,
            camera.rtsp_username,
            camera.rtsp_password,
        ):
            self.assertNotIn(private_value, repr(camera))
            self.assertNotIn(private_value, repr(binding))
            self.assertNotIn(private_value, repr(endpoint))

    def test_manual_vm85_rejects_hosts_outside_private_lan_ranges(self) -> None:
        for host in ("127.0.0.1", "169.254.1.1", "192.0.2.10", "0.0.0.0", "::1"):
            with (
                self.subTest(host=host),
                self.assertRaises(manual.ManualOrbwebConfigError),
            ):
                manual.build_manual_orbweb_camera(
                    name="Nursery VM85",
                    host=host,
                    target_id="synthetic-vm85-sid",
                    auth_account="vm85-account",
                    auth_password="orbweb-secret",
                    rtsp_username="rtsp-user",
                    rtsp_password="rtsp-secret",
                )

    def test_cloud_camera_without_mac_still_gets_orbweb_binding(self) -> None:
        result = bindings.build_orbweb_stream_bindings(
            (camera(mac_address=None),),
            (),
            {},
            (),
        )

        self.assertEqual(len(result), 1)
        binding = result[0]
        self.assertEqual(binding.key, "cloud:registration-1")
        self.assertEqual(binding.name, "Basement")
        self.assertIsNone(binding.route_host)
        self.assertEqual(binding.unique_id, "cloud:registration-1:camera")
        self.assertEqual(binding.device_identifier, "cloud:registration-1")
        self.assertNotIn("orbweb-target", repr(binding))
        self.assertNotIn("orbweb-secret", repr(binding))

    def test_mac_matched_local_camera_keeps_existing_identity(self) -> None:
        spec = local.HubbleLocalCameraSpec(
            name="Nursery",
            host="192.168.50.12",
            source="cloud_arp",
            cloud_mac="020000000003",
        )
        result = bindings.build_orbweb_stream_bindings(
            (camera(mac_address="02:00:00:00:00:03"),),
            (spec,),
            {spec.host: None},
            (),
        )

        self.assertEqual(len(result), 1)
        binding = result[0]
        self.assertEqual(binding.key, spec.host)
        self.assertEqual(binding.route_host, spec.host)
        self.assertEqual(binding.unique_id, f"{spec.host}:camera")
        self.assertEqual(binding.device_identifier, f"camera:{spec.host}")

    def test_direct_rtsp_camera_does_not_get_duplicate_cloud_binding(self) -> None:
        spec = local.HubbleLocalCameraSpec(
            name="Nursery",
            host="192.168.50.12",
            source="cloud_arp",
            cloud_mac="020000000003",
        )
        result = bindings.build_orbweb_stream_bindings(
            (camera(mac_address="02:00:00:00:00:03"),),
            (spec,),
            {spec.host: "02:00:00:00:00:03"},
            {spec.host},
        )

        self.assertEqual(result, ())

    def test_discovered_camera_without_video_route_is_logged_without_identifiers(
        self,
    ) -> None:
        owned = camera(
            registration_id="AA7101-synthetic-camera",
            name="Private camera name",
            mac_address="02:00:00:00:00:01",
            orbweb_sid="private-orbweb-sid",
            orbweb_password=None,
        )
        with self.assertLogs(bindings.__name__, level="WARNING") as logs:
            result = bindings.build_orbweb_stream_bindings((owned,), (), {}, ())

        self.assertEqual(result, ())
        self.assertIn("found 1 camera(s) with no supported video route", logs.output[0])
        self.assertIn("missing Orbweb credentials", logs.output[0])
        for value in (
            owned.registration_id,
            owned.name,
            owned.mac_address,
            owned.orbweb_sid,
        ):
            self.assertNotIn(value, logs.output[0])

    def test_direct_rtsp_without_orbweb_credentials_does_not_log_missing_video(
        self,
    ) -> None:
        owned = camera(
            mac_address="02:00:00:00:00:01",
            orbweb_sid=None,
            orbweb_password=None,
        )
        for registration_id in (owned.registration_id, None):
            spec = local.HubbleLocalCameraSpec(
                name="Local camera",
                host="192.0.2.12",
                cloud_registration_id=registration_id,
            )
            with (
                self.subTest(match_by_registration_id=registration_id is not None),
                self.assertNoLogs(bindings.__name__, level="WARNING"),
            ):
                self.assertEqual(
                    bindings.build_orbweb_stream_bindings(
                        (owned,), (spec,), {spec.host: owned.mac_address}, {spec.host}
                    ),
                    (),
                )

    def test_linked_3667_shares_identity_and_video_and_command_routes(self) -> None:
        owned = camera(registration_id="XX3667-test-camera", mac_address=None)
        spec = local.HubbleLocalCameraSpec(
            name="Local camera",
            host="192.168.50.12",
            cloud_registration_id=owned.registration_id,
        )
        (video,) = bindings.build_orbweb_stream_bindings((owned,), (spec,), {}, ())
        (commands,) = bindings.build_orbweb_command_bindings((owned,), (spec,))

        self.assertEqual(video.device_identifier, f"cloud:{owned.registration_id}")
        self.assertEqual(video.unique_id, f"cloud:{owned.registration_id}:camera")
        self.assertEqual(video.key, commands.key)
        self.assertEqual(video.route_host, commands.route_host)
        self.assertEqual(commands.route_host, spec.host)
        self.assertEqual(
            bindings.build_orbweb_stream_bindings((owned,), (spec,), {}, {spec.host}),
            (),
        )


if __name__ == "__main__":
    unittest.main()
