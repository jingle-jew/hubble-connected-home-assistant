"""Tests for model-scoped go2rtc RTSP normalization."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

PACKAGE = "hubble_restream_test"
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


rtsp = load("rtsp")
restream = load("restream")


class FakeStreams:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | list[str]]] = []

    async def add(self, name: str, sources: str | list[str]) -> None:
        self.calls.append((name, sources))


class HubbleRestreamTests(unittest.TestCase):
    """Keep credentials in go2rtc and expose only a stable loopback endpoint."""

    def test_registers_once_and_replaces_changed_dynamic_source(self) -> None:
        async def scenario() -> None:
            streams = FakeStreams()
            deleted: list[str] = []

            async def delete(name: str) -> None:
                deleted.append(name)

            manager = restream.HubbleRtspRestreamManager(streams, delete)
            first_source = rtsp.HubbleRtspEndpoint(
                "127.0.0.1", 30123, "/blinkhd", "user", "secret"
            )
            second_source = rtsp.HubbleRtspEndpoint(
                "127.0.0.1", 30456, "/blinkhd", "user", "secret"
            )

            first = await manager.async_get_endpoint("cloud:owned", first_source)
            repeated = await manager.async_get_endpoint("cloud:owned", first_source)
            second = await manager.async_get_endpoint("cloud:owned", second_source)

            self.assertEqual(first, repeated)
            self.assertEqual(first, second)
            self.assertEqual(first.host, "127.0.0.1")
            self.assertEqual(first.port, 18554)
            self.assertNotIn("secret", first.url)
            self.assertEqual(len(streams.calls), 2)
            for _name, source in streams.calls:
                self.assertIsInstance(source, str)
                self.assertIn("#video=copy#audio=copy", source)

            await manager.async_close()
            self.assertEqual(deleted, [first.path.removeprefix("/")])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
