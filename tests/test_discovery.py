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


class HubbleAsyncDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    """Validate conservative discovery without real network traffic."""

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
