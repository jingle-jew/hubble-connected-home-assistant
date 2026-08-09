"""Direct-tunnel key handshake for the clean-room Orbweb client."""

from __future__ import annotations

import asyncio
import secrets
import struct
from dataclasses import dataclass

from .crypto import KeyExchange, derive_aes_key, derive_iv
from .framing import (
    BasePacket,
    OrbwebCommand,
    OrbwebProtocolError,
    async_read_packet,
    async_write_packet,
)


@dataclass(frozen=True, slots=True, repr=False)
class TunnelKeyMaterial:
    """Derived values retained by the encrypted tunnel layer."""

    shared_secret: int
    key: bytes
    iv: bytes

    def __repr__(self) -> str:
        """Keep negotiated secrets out of diagnostics."""
        return f"{type(self).__name__}()"


async def async_negotiate_tunnel_key(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    exchange: KeyExchange,
    *,
    private_value: int,
) -> TunnelKeyMaterial:
    """Perform the confirmed command-0x03 client exchange.

    This function starts only after rendezvous has supplied a connected direct
    or relay transport. It deliberately performs no socket discovery and logs
    no key material.
    """

    await async_write_packet(
        writer,
        BasePacket(OrbwebCommand.KEY_EXCHANGE, exchange.encode()),
    )
    response = await async_read_packet(reader)
    if response.command != OrbwebCommand.KEY_EXCHANGE:
        raise OrbwebProtocolError(
            f"Expected key-exchange response, received 0x{response.command:x}"
        )
    if len(response.payload) != 16:
        raise OrbwebProtocolError(
            f"Key-exchange response has {len(response.payload)} bytes; expected 16"
        )
    peer_public_value, reserved = struct.unpack("<qq", response.payload)
    if reserved != 0:
        raise OrbwebProtocolError("Key-exchange response reserved field is nonzero")
    try:
        shared_secret = exchange.shared_secret(private_value, peer_public_value)
    except ValueError as err:
        raise OrbwebProtocolError("Invalid peer key-exchange value") from err
    key = derive_aes_key(shared_secret)
    return TunnelKeyMaterial(shared_secret, key, derive_iv(key))


async def async_accept_tunnel_key(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    private_value: int | None = None,
) -> TunnelKeyMaterial:
    """Receive the camera's 24-byte exchange and send the native 16-byte reply."""
    request = await async_read_packet(reader)
    if request.command != OrbwebCommand.KEY_EXCHANGE:
        raise OrbwebProtocolError(
            f"Expected key-exchange request, received 0x{request.command:x}"
        )
    if len(request.payload) != 24:
        raise OrbwebProtocolError(
            f"Key-exchange request has {len(request.payload)} bytes; expected 24"
        )
    modulus, generator, peer_public_value = struct.unpack("<qqq", request.payload)
    if private_value is None:
        private_value = secrets.randbelow(0x7FFFFFFE) + 1

    try:
        response_exchange = KeyExchange.from_private(
            modulus=modulus,
            generator=generator,
            private_value=private_value,
        )
        shared_secret = response_exchange.shared_secret(
            private_value,
            peer_public_value,
        )
    except ValueError as err:
        raise OrbwebProtocolError("Invalid peer key-exchange values") from err

    await async_write_packet(
        writer,
        BasePacket(
            OrbwebCommand.KEY_EXCHANGE,
            struct.pack("<qq", response_exchange.public_value, 0),
        ),
    )
    key = derive_aes_key(shared_secret)
    return TunnelKeyMaterial(shared_secret, key, derive_iv(key))
