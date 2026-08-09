"""Tests for the redacted Orbweb packet decoder."""

from __future__ import annotations

import importlib.util
import struct
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tools" / "orbweb_packet_decoder.py"
SPEC = importlib.util.spec_from_file_location("orbweb_packet_decoder", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
decoder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = decoder
SPEC.loader.exec_module(decoder)


class OrbwebPacketDecoderTests(unittest.TestCase):
    """Exercise framing and the unusual body-mask boundary."""

    def test_scans_consecutive_known_packets(self) -> None:
        raw = struct.pack("<ii", 0x258, 3) + b"abc" + struct.pack("<ii", 0x25C, 0)
        packets = decoder.scan_base_packets(raw)
        self.assertEqual(
            [(item.command, item.payload_length, item.offset) for item in packets],
            [(0x258, 3, 0), (0x25C, 0, 11)],
        )

    def test_unknown_command_is_hidden_by_default(self) -> None:
        raw = struct.pack("<ii", 0x1234, 0)
        self.assertEqual(decoder.scan_base_packets(raw), [])
        self.assertEqual(
            decoder.scan_base_packets(raw, include_unknown=True)[0].command,
            0x1234,
        )

    def test_mask_leaves_final_four_bytes_unchanged(self) -> None:
        original = bytes.fromhex("0102030405060708")
        masked = decoder.unmask_body(original)
        self.assertNotEqual(masked[:4], original[:4])
        self.assertEqual(masked[4:], original[4:])
        self.assertEqual(decoder.unmask_body(masked), original)


if __name__ == "__main__":
    unittest.main()
