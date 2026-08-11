"""Tests for conservative cloud-to-LAN discovery helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

PACKAGE = "hubble_discovery_test"
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


load("cloud")
local = load("local")
discovery = load("discovery")


class HubbleDiscoveryTests(unittest.TestCase):
    """Validate ARP parsing without touching the network."""

    def test_parse_arp_table_keeps_only_complete_entries(self) -> None:
        table = """IP address HW type Flags HW address Mask Device
192.168.50.10 0x1 0x2 02:00:00:00:00:01 * br0
192.168.50.11 0x1 0x0 00:00:00:00:00:00 * br0
192.168.50.12 0x1 0x2 02-00-00-00-00-02 * br0
"""
        self.assertEqual(
            discovery.parse_arp_table(table),
            {
                "020000000001": "192.168.50.10",
                "020000000002": "192.168.50.12",
            },
        )

    def test_parse_arp_table_rejects_public_and_invalid_addresses(self) -> None:
        table = """IP address HW type Flags HW address Mask Device
8.8.8.8 0x1 0x2 02:00:00:00:00:01 * br0
not-an-ip 0x1 0x2 02:00:00:00:00:02 * br0
192.168.50.12 0x1 0x2 02:00:00:00:00:03 * br0
"""
        self.assertEqual(
            discovery.parse_arp_table(table),
            {"020000000003": "192.168.50.12"},
        )

    def test_cloud_polled_3667_keeps_stream_route_without_local_entities(
        self,
    ) -> None:
        legacy = local.HubbleLocalCameraSpec(
            name="Nursery",
            host="192.168.50.11",
            source="cloud_arp",
            cloud_mac="020000000002",
        )
        cloud_polled = local.HubbleLocalCameraSpec(
            name="Basement",
            host="192.168.50.12",
            source="cloud_arp",
            cloud_mac="020000000003",
        )
        camera = types.SimpleNamespace(
            model_code="3667",
            mac_address="02:00:00:00:00:03",
        )

        selected = discovery.select_local_entity_specs(
            (legacy, cloud_polled), (camera,)
        )

        self.assertEqual(selected, (legacy,))

    def test_non_3667_and_manual_specs_keep_local_entities(self) -> None:
        manual = local.HubbleLocalCameraSpec(
            name="Manual",
            host="192.168.50.10",
        )
        cloud_matched = local.HubbleLocalCameraSpec(
            name="Nursery",
            host="192.168.50.11",
            source="cloud_arp",
            cloud_mac="020000000002",
        )
        camera = types.SimpleNamespace(
            model_code="1667",
            mac_address="02:00:00:00:00:02",
        )

        selected = discovery.select_local_entity_specs(
            (manual, cloud_matched), (camera,)
        )

        self.assertEqual(selected, (manual, cloud_matched))

    def test_image_controls_use_cloud_model_when_local_metadata_is_missing(
        self,
    ) -> None:
        unsafe = local.HubbleLocalCameraSpec(
            name="Salon",
            host="192.168.50.10",
            cloud_mac="020000000001",
        )
        safe = local.HubbleLocalCameraSpec(
            name="Nursery",
            host="192.168.50.11",
            cloud_mac="020000000002",
        )
        cameras = (
            types.SimpleNamespace(
                model_code="0667", mac_address="02:00:00:00:00:01"
            ),
            types.SimpleNamespace(
                model_code="1667", mac_address="02:00:00:00:00:02"
            ),
        )

        selected = discovery.select_image_level_entity_specs(
            (unsafe, safe), {}, cameras
        )

        self.assertEqual(selected, (safe,))


class HubbleAsyncDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    """Validate conservative discovery without real network traffic."""

    async def test_cloud_arp_match_does_not_require_local_http(self) -> None:
        table = """IP address HW type Flags HW address Mask Device
192.168.50.12 0x1 0x2 02:00:00:00:00:03 * br0
"""

        class FakeHass:
            async def async_add_executor_job(self, _function, *_args):
                return table

        class FailingClient:
            def __init__(self, _spec):
                raise AssertionError(
                    "Cloud-owned camera must not require an HTTP probe"
                )

        camera = types.SimpleNamespace(
            name="Basement",
            mac_address="02:00:00:00:00:03",
        )
        original_client = discovery.HubbleLocalClient
        discovery.HubbleLocalClient = FailingClient
        try:
            specs = await discovery.async_discover_cloud_cameras(
                FakeHass(), (camera,), ()
            )
        finally:
            discovery.HubbleLocalClient = original_client

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].host, "192.168.50.12")
        self.assertEqual(specs[0].source, "cloud_arp")
        self.assertEqual(specs[0].cloud_mac, "020000000003")

    async def test_manual_camera_inherits_matching_cloud_mac(self) -> None:
        table = """IP address HW type Flags HW address Mask Device
192.168.50.12 0x1 0x2 02:00:00:00:00:03 * br0
"""

        class FakeHass:
            async def async_add_executor_job(self, _function, *_args):
                return table

        camera = types.SimpleNamespace(
            name="Basement",
            mac_address="02:00:00:00:00:03",
        )
        configured = (
            local.HubbleLocalCameraSpec(
                name="Manual basement",
                host="192.168.50.12",
            ),
        )

        specs = await discovery.async_discover_cloud_cameras(
            FakeHass(), (camera,), configured
        )

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "Manual basement")
        self.assertEqual(specs[0].source, "manual")
        self.assertEqual(specs[0].cloud_mac, "020000000003")

    async def test_unknown_arp_neighbor_still_requires_local_verification(self) -> None:
        table = """IP address HW type Flags HW address Mask Device
192.168.50.12 0x1 0x2 02:00:00:00:00:03 * br0
"""

        class FakeHass:
            async def async_add_executor_job(self, _function, *_args):
                return table

        class FailingClient:
            def __init__(self, spec):
                self.spec = spec

            async def async_get_mac_address(self, timeout=5.0):
                del timeout
                raise local.HubbleLocalCannotConnect("not a Hubble camera")

        original_client = discovery.HubbleLocalClient
        discovery.HubbleLocalClient = FailingClient
        try:
            specs = await discovery.async_discover_cloud_cameras(FakeHass(), (), ())
        finally:
            discovery.HubbleLocalClient = original_client

        self.assertEqual(specs, ())

    async def test_discovers_hubble_neighbor_missing_from_cloud(self) -> None:
        table = """IP address HW type Flags HW address Mask Device
192.168.50.1 0x1 0x2 02:00:00:00:00:01 * br0
192.168.50.12 0x1 0x2 02:00:00:00:00:03 * br0
"""

        class FakeHass:
            async def async_add_executor_job(self, _function, *_args):
                return table

        class FakeClient:
            def __init__(self, spec):
                self.spec = spec

            async def async_get_mac_address(self, timeout=5.0):
                del timeout
                if self.spec.host == "192.168.50.12":
                    return "02:00:00:00:00:03"
                raise local.HubbleLocalCannotConnect("not a Hubble camera")

        original_client = discovery.HubbleLocalClient
        discovery.HubbleLocalClient = FakeClient
        try:
            specs = await discovery.async_discover_cloud_cameras(FakeHass(), (), ())
        finally:
            discovery.HubbleLocalClient = original_client

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].host, "192.168.50.12")
        self.assertEqual(specs[0].source, "arp_probe")


if __name__ == "__main__":
    unittest.main()
