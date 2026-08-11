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


cloud = load("cloud")
local = load("local")
load("discovery")
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


if __name__ == "__main__":
    unittest.main()
