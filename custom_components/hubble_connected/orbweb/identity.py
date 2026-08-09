"""Persistent client identity used by the Orbweb rendezvous protocol."""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass, field

CLIENT_TOKEN_GROUPS = (8, 4, 4, 4, 12)
CLIENT_TOKEN_PATTERN = re.compile(r"^[A-Z]{8}-[A-Z]{4}-[A-Z]{4}-[A-Z]{4}-[A-Z]{12}$")
CLIENT_ID_MARKER = "_ctok_"
MAX_COUNTER = 0xFF


def generate_client_token() -> str:
    """Generate a capture-compatible persistent installation token.

    The Android capture proves the shape and persistence boundary, but not the
    original SDK's entropy source. ``secrets`` avoids reproducing its obsolete
    pseudo-random implementation while preserving the observed wire format.
    """
    groups = (
        "".join(secrets.choice(string.ascii_uppercase) for _ in range(size))
        for size in CLIENT_TOKEN_GROUPS
    )
    return "-".join(groups)


def validate_client_token(client_token: str) -> None:
    """Reject tokens that do not match the observed 8-4-4-4-12 format."""
    if CLIENT_TOKEN_PATTERN.fullmatch(client_token) is None:
        raise ValueError("client_token must use uppercase ASCII groups 8-4-4-4-12")


def build_client_id(target_id: str, client_token: str, counter: int) -> str:
    """Build the exact ``SID_ctok_TOKEN#NNN`` rendezvous identifier."""
    try:
        target_id.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError("target_id must contain only ASCII") from err
    if not target_id:
        raise ValueError("target_id must not be empty")
    validate_client_token(client_token)
    if not 0 <= counter <= MAX_COUNTER:
        raise ValueError("counter must be between 0 and 255")
    return f"{target_id}{CLIENT_ID_MARKER}{client_token}#{counter:03d}"


@dataclass(slots=True, repr=False)
class OrbwebClientIdentity:
    """Own one persistent token and the native-compatible session counter."""

    client_token: str = field(default_factory=generate_client_token)
    _counter: int = field(default_factory=lambda: secrets.randbelow(256))

    def __post_init__(self) -> None:
        validate_client_token(self.client_token)
        if not 0 <= self._counter <= MAX_COUNTER:
            raise ValueError("counter must be between 0 and 255")

    def next_client_id(self, target_id: str) -> str:
        """Advance modulo 256, matching ``GenP2PServerID``, and format it."""
        self._counter = (self._counter + 1) & MAX_COUNTER
        return build_client_id(target_id, self.client_token, self._counter)
