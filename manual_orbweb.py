"""Configuration model for manually provisioned owned Orbweb cameras."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from hashlib import sha256


class ManualOrbwebConfigError(ValueError):
    """A manual Orbweb camera configuration is invalid."""


@dataclass(frozen=True, slots=True)
class ManualOrbwebCamera:
    """Owner-provided connection details for one Orbweb camera."""

    name: str
    host: str
    target_id: str = field(repr=False)
    auth_account: str = field(repr=False)
    auth_password: str = field(repr=False)
    rtsp_username: str = field(repr=False)
    rtsp_password: str = field(repr=False)
    rtsp_port: int = 6667
    rtsp_path: str = "/blinkhd"
    model: str = "Motorola VM85CONNECT"

    @property
    def key(self) -> str:
        """Return a stable opaque internal key without storing the SID in it."""
        digest = sha256(self.target_id.encode("ascii")).hexdigest()[:16]
        return f"manual:{digest}"

    @property
    def unique_id(self) -> str:
        """Return the camera entity unique ID."""
        return f"{self.key}:camera"


def build_manual_orbweb_camera(
    *,
    name: str,
    host: str,
    target_id: str,
    auth_account: str,
    auth_password: str,
    rtsp_username: str,
    rtsp_password: str,
) -> ManualOrbwebCamera:
    """Validate and normalize a manually provisioned VM85 camera."""
    name = name.strip()
    if not name:
        raise ManualOrbwebConfigError("Camera name must not be empty")

    try:
        address = ipaddress.ip_address(host.strip())
    except ValueError as err:
        raise ManualOrbwebConfigError("Camera IP address is invalid") from err
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_multicast
    ):
        raise ManualOrbwebConfigError("Camera must use a private IPv4 LAN address")

    normalized: dict[str, str] = {}
    for label, value in {
        "Orbweb SID": target_id,
        "Orbweb account": auth_account,
        "Orbweb password": auth_password,
        "RTSP username": rtsp_username,
        "RTSP password": rtsp_password,
    }.items():
        value = value.strip()
        if not value:
            raise ManualOrbwebConfigError(f"{label} must not be empty")
        if not value.isascii():
            raise ManualOrbwebConfigError(f"{label} must contain ASCII only")
        if label == "Orbweb SID" and len(value) > 128:
            raise ManualOrbwebConfigError("Orbweb SID is too long")
        normalized[label] = value

    return ManualOrbwebCamera(
        name=name,
        host=str(address),
        target_id=normalized["Orbweb SID"],
        auth_account=normalized["Orbweb account"],
        auth_password=normalized["Orbweb password"],
        rtsp_username=normalized["RTSP username"],
        rtsp_password=normalized["RTSP password"],
    )
