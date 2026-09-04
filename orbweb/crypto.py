"""Key-exchange and padding primitives reconstructed from Orbweb."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

AES_BLOCK_SIZE = 16


@dataclass(frozen=True, slots=True)
class KeyExchange:
    """The signed 64-bit values carried by tunnel command 0x03."""

    modulus: int
    generator: int
    public_value: int

    def encode(self) -> bytes:
        """Encode the client P, G and A values in native wire order."""
        try:
            return struct.pack("<qqq", self.modulus, self.generator, self.public_value)
        except struct.error as err:
            raise ValueError("Key-exchange value does not fit signed int64") from err

    @classmethod
    def from_private(
        cls, *, modulus: int, generator: int, private_value: int
    ) -> KeyExchange:
        """Construct the public value A = G^a mod P."""
        _validate_dh_values(modulus, generator, private_value)
        return cls(
            modulus=modulus,
            generator=generator,
            public_value=pow(generator, private_value, modulus),
        )

    def shared_secret(self, private_value: int, peer_public_value: int) -> int:
        """Calculate B^a mod P after validating the small integer domain."""
        _validate_dh_values(self.modulus, self.generator, private_value)
        if not 0 < peer_public_value < self.modulus:
            raise ValueError("Peer public value is outside the modulus")
        return pow(peer_public_value, private_value, self.modulus)


def _validate_dh_values(modulus: int, generator: int, private_value: int) -> None:
    if modulus <= 2:
        raise ValueError("Modulus must be greater than two")
    if not 1 < generator < modulus:
        raise ValueError("Generator is outside the modulus")
    if private_value <= 0:
        raise ValueError("Private value must be positive")


def derive_aes_key(shared_secret: int) -> bytes:
    """Derive the legacy AES-128 key from a decimal shared integer."""

    if shared_secret < 0:
        raise ValueError("Shared secret must not be negative")
    decimal_secret = str(shared_secret).encode("ascii")
    return hashlib.md5(decimal_secret, usedforsecurity=False).digest()


def derive_iv(key: bytes) -> bytes:
    """Reproduce the native 16-byte IV derived from the AES key."""

    if len(key) != AES_BLOCK_SIZE:
        raise ValueError("Orbweb AES key must be exactly 16 bytes")
    iv = bytearray(AES_BLOCK_SIZE)
    iv[2] = (key[15] + key[0]) & 0xFF
    iv[5] = (key[1] + key[13]) & 0xFF
    iv[8] = (key[15] + key[14]) & 0xFF
    iv[12] = (key[11] + key[12]) & 0xFF
    iv[14] = (key[3] + key[9]) & 0xFF
    return bytes(iv)


def pkcs7_pad(data: bytes, block_size: int = AES_BLOCK_SIZE) -> bytes:
    """Pad bytes exactly as the native AES wrapper does."""

    if not 1 < block_size <= 255:
        raise ValueError("PKCS#7 block size must be between 2 and 255")
    padding_length = block_size - (len(data) % block_size)
    return data + bytes([padding_length]) * padding_length


def pkcs7_unpad(data: bytes, block_size: int = AES_BLOCK_SIZE) -> bytes:
    """Validate and remove PKCS#7 padding."""

    if not 1 < block_size <= 255:
        raise ValueError("PKCS#7 block size must be between 2 and 255")
    if not data or len(data) % block_size:
        raise ValueError("Padded data is empty or not block aligned")
    padding_length = data[-1]
    if not 0 < padding_length <= block_size:
        raise ValueError("Invalid PKCS#7 padding length")
    if data[-padding_length:] != bytes([padding_length]) * padding_length:
        raise ValueError("Invalid PKCS#7 padding bytes")
    return data[:-padding_length]
