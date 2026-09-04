"""Camera-side Orbweb authentication over the mapped command service."""

from __future__ import annotations

import asyncio
import json
import struct

from .framing import OrbwebProtocolError

ORBWEB_AUTH_REMOTE_PORT = 9001
ORBWEB_AUTH_ACCOUNT = "orbweb_user001"
DEFAULT_AUTH_TIMEOUT = 5.0
MAX_AUTH_RESPONSE_LENGTH = 64 * 1024

_MESSAGE_HEADER = struct.Struct("<II")
_MESSAGE_VERSION = 1
_AUTH_REQUEST = "P2P_USER_PASSWORD_REQ"
_AUTH_RESPONSE = "P2P_USER_PASSWORD_RSP"


class OrbwebAuthenticationError(OrbwebProtocolError):
    """The owned camera rejected or malformed its Orbweb authentication."""


async def async_authenticate_camera(
    host: str,
    port: int,
    *,
    p2p_server_id: str,
    account: str,
    password: str,
    timeout: float = DEFAULT_AUTH_TIMEOUT,
) -> None:
    """Authenticate one established tunnel through its mapped port 9001."""
    if not p2p_server_id or not account or not password:
        raise ValueError("Orbweb authentication fields must not be empty")
    if timeout <= 0:
        raise ValueError("Orbweb authentication timeout must be positive")

    request = {
        "CMD_ID": _AUTH_REQUEST,
        "P2PSERVERID": p2p_server_id,
        "NAME": account,
        "PASSWORD": password,
    }
    try:
        encoded = (
            json.dumps(
                request,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            + b"\0"
        )
    except (TypeError, UnicodeEncodeError) as err:
        raise ValueError("Orbweb authentication fields must be JSON strings") from err

    reader = None
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(host, port)
            writer.write(_MESSAGE_HEADER.pack(_MESSAGE_VERSION, len(encoded)))
            writer.write(encoded)
            await writer.drain()
            version, length = _MESSAGE_HEADER.unpack(
                await reader.readexactly(_MESSAGE_HEADER.size)
            )
            if version != _MESSAGE_VERSION:
                raise OrbwebAuthenticationError(
                    "Orbweb authentication response version is unsupported"
                )
            if not 1 <= length <= MAX_AUTH_RESPONSE_LENGTH:
                raise OrbwebAuthenticationError(
                    "Orbweb authentication response length is invalid"
                )
            raw_response = await reader.readexactly(length)
    except TimeoutError as err:
        raise OrbwebAuthenticationError(
            "Orbweb camera authentication timed out"
        ) from err
    except (EOFError, OSError, asyncio.IncompleteReadError) as err:
        raise OrbwebAuthenticationError(
            "Orbweb camera authentication transport failed"
        ) from err
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionError):
                pass

    try:
        response = json.loads(raw_response.rstrip(b"\0").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise OrbwebAuthenticationError(
            "Orbweb authentication response is not valid JSON"
        ) from err
    if not isinstance(response, dict):
        raise OrbwebAuthenticationError(
            "Orbweb authentication response must be an object"
        )
    if response.get("CMD_ID") not in {_AUTH_RESPONSE, None}:
        raise OrbwebAuthenticationError(
            "Orbweb authentication returned an unexpected command"
        )
    try:
        status = int(response["STATUS"])
    except (KeyError, TypeError, ValueError) as err:
        raise OrbwebAuthenticationError(
            "Orbweb authentication response has no valid status"
        ) from err
    if status != 0:
        raise OrbwebAuthenticationError("Orbweb camera rejected authentication")
