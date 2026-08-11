"""Build camera stream bindings without coupling cloud video to LAN metadata."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from .cloud import HubbleCloudCamera
from .discovery import normalize_mac
from .local import HubbleLocalCameraSpec


@dataclass(frozen=True, slots=True)
class HubbleOrbwebStreamBinding:
    """Non-lossy link between one owned cloud camera and an Orbweb stream."""

    key: str
    name: str
    unique_id: str
    device_identifier: str
    route_host: str | None
    target_id: str = field(repr=False)
    auth_password: str = field(repr=False)


def build_orbweb_stream_bindings(
    cloud_cameras: tuple[HubbleCloudCamera, ...],
    local_specs: tuple[HubbleLocalCameraSpec, ...],
    local_macs: Mapping[str, str | None],
    direct_hosts: Collection[str],
) -> tuple[HubbleOrbwebStreamBinding, ...]:
    """Build Orbweb streams, including cloud-only cameras without a LAN MAC."""
    cloud_by_mac = {
        normalize_mac(camera.mac_address or ""): camera
        for camera in cloud_cameras
        if camera.has_orbweb_credentials and normalize_mac(camera.mac_address or "")
    }
    represented_registration_ids: set[str] = set()
    bindings: list[HubbleOrbwebStreamBinding] = []

    for spec in local_specs:
        camera_mac = normalize_mac(
            spec.cloud_mac or local_macs.get(spec.host) or ""
        )
        camera = cloud_by_mac.get(camera_mac)
        if camera is None or camera.registration_id in represented_registration_ids:
            continue
        represented_registration_ids.add(camera.registration_id)
        if spec.host in direct_hosts:
            continue
        if camera.orbweb_sid is None or camera.orbweb_password is None:
            continue
        bindings.append(
            HubbleOrbwebStreamBinding(
                key=spec.host,
                name=spec.name,
                unique_id=f"{spec.host}:camera",
                device_identifier=f"camera:{spec.host}",
                route_host=spec.host,
                target_id=camera.orbweb_sid,
                auth_password=camera.orbweb_password,
            )
        )

    for camera in cloud_cameras:
        if (
            not camera.has_orbweb_credentials
            or camera.registration_id in represented_registration_ids
            or camera.orbweb_sid is None
            or camera.orbweb_password is None
        ):
            continue
        key = f"cloud:{camera.registration_id}"
        bindings.append(
            HubbleOrbwebStreamBinding(
                key=key,
                name=camera.name,
                unique_id=f"{key}:camera",
                device_identifier=key,
                route_host=None,
                target_id=camera.orbweb_sid,
                auth_password=camera.orbweb_password,
            )
        )

    return tuple(bindings)
