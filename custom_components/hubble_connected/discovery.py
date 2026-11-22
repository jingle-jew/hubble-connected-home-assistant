"""Conservative LAN discovery using cloud MACs and the kernel ARP table."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cloud import HubbleCloudCamera
from .local import (
    HubbleLocalCameraData,
    HubbleLocalCameraSpec,
    HubbleLocalClient,
    HubbleLocalError,
    model_supports_image_level_entities,
)

ARP_PATH = Path("/proc/net/arp")
LOCAL_PROBE_CONCURRENCY = 12
LOCAL_PROBE_TIMEOUT = 1.5


def normalize_mac(value: str) -> str:
    """Return twelve lowercase hexadecimal characters or an empty string."""
    hexadecimal = "0123456789abcdef"
    compact = "".join(
        character for character in value.lower() if character in hexadecimal
    )
    return compact if len(compact) == 12 else ""


def parse_arp_table(value: str) -> dict[str, str]:
    """Map complete ARP MAC addresses to IPv4 addresses."""
    entries: dict[str, str] = {}
    for line in value.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[2] != "0x2":
            continue
        mac = normalize_mac(fields[3])
        try:
            address = ipaddress.ip_address(fields[0])
        except ValueError:
            continue
        if (
            mac
            and isinstance(address, ipaddress.IPv4Address)
            and address.is_private
            and not address.is_loopback
            and not address.is_multicast
        ):
            entries[mac] = str(address)
    return entries


def select_local_entity_specs(
    specs: tuple[HubbleLocalCameraSpec, ...],
    cloud_command_cameras: tuple[HubbleCloudCamera, ...],
) -> tuple[HubbleLocalCameraSpec, ...]:
    """Exclude cloud-polled 3667 routes from unsupported local entities."""
    cloud_polled_3667_macs = {
        mac
        for camera in cloud_command_cameras
        if camera.model_code == "3667"
        if (mac := normalize_mac(camera.mac_address or ""))
    }
    return tuple(
        spec
        for spec in specs
        if spec.cloud_3667_id is None
        and normalize_mac(spec.cloud_mac or "") not in cloud_polled_3667_macs
    )


def select_image_level_entity_specs(
    specs: tuple[HubbleLocalCameraSpec, ...],
    local_data: Mapping[str, HubbleLocalCameraData],
    cloud_cameras: tuple[HubbleCloudCamera, ...],
) -> tuple[HubbleLocalCameraSpec, ...]:
    """Keep image controls only on models where changes preserve streaming."""
    cloud_by_mac = {
        mac: camera
        for camera in cloud_cameras
        if (mac := normalize_mac(camera.mac_address or ""))
    }
    selected: list[HubbleLocalCameraSpec] = []
    for spec in specs:
        data = local_data.get(spec.host)
        model_code = data.model_code if data is not None else None
        if model_code is None:
            mac = normalize_mac(
                spec.cloud_mac or (data.mac if data is not None else "") or ""
            )
            camera = cloud_by_mac.get(mac)
            model_code = camera.model_code if camera is not None else None
        if model_supports_image_level_entities(model_code):
            selected.append(spec)
    return tuple(selected)


async def async_discover_cloud_cameras(
    hass: Any,
    cameras: tuple[HubbleCloudCamera, ...],
    configured: tuple[HubbleLocalCameraSpec, ...],
    camera_hosts: Mapping[str, str] | None = None,
) -> tuple[HubbleLocalCameraSpec, ...]:
    """Discover owned cloud cameras by MAC and verify unknown ARP neighbors."""
    cameras_by_id = {camera.registration_id: camera for camera in cameras}
    configured_by_host = {spec.host: spec for spec in configured}
    explicit_ids: set[str] = set()
    for registration_id, host in (camera_hosts or {}).items():
        camera = cameras_by_id.get(registration_id)
        spec = configured_by_host.get(host) or HubbleLocalCameraSpec(
            name=camera.name if camera else f"Hubble {host}",
            host=host,
            source="cloud_manual",
        )
        configured_by_host[host] = replace(
            spec,
            cloud_mac=camera.mac_address if camera else None,
            cloud_registration_id=registration_id,
        )
        explicit_ids.add(registration_id)

    try:
        arp_text = await hass.async_add_executor_job(ARP_PATH.read_text, "utf-8")
    except OSError:
        arp_text = ""

    arp_by_mac = parse_arp_table(arp_text)
    cloud_by_mac = {
        mac: camera
        for camera in cameras
        if (mac := normalize_mac(camera.mac_address or ""))
    }
    mac_by_host = {host: mac for mac, host in arp_by_mac.items()}
    semaphore = asyncio.Semaphore(LOCAL_PROBE_CONCURRENCY)

    async def read_mac(spec: HubbleLocalCameraSpec) -> str:
        try:
            async with semaphore:
                return normalize_mac(
                    await HubbleLocalClient(spec).async_get_mac_address(
                        timeout=LOCAL_PROBE_TIMEOUT
                    )
                )
        except HubbleLocalError:
            return ""

    # The ARP cache may be empty when a user first supplies an IP. An explicit
    # association also covers cameras whose direct HTTP getter is unavailable.
    to_verify = [
        spec
        for spec in configured_by_host.values()
        if cloud_by_mac
        and not (spec.cloud_registration_id or spec.cloud_mac)
        and spec.host not in mac_by_host
    ]
    if to_verify:
        macs = await asyncio.gather(*(read_mac(spec) for spec in to_verify))
        if "" in macs:
            try:
                refreshed_arp = await hass.async_add_executor_job(
                    ARP_PATH.read_text, "utf-8"
                )
            except OSError:
                pass
            else:
                arp_by_mac.update(parse_arp_table(refreshed_arp))
                mac_by_host.update({host: mac for mac, host in arp_by_mac.items()})
        mac_by_host.update(
            (spec.host, mac) for spec, mac in zip(to_verify, macs, strict=True) if mac
        )

    represented_ids = set(explicit_ids)
    configured_specs = []
    for spec in configured_by_host.values():
        if spec.cloud_registration_id is not None:
            configured_specs.append(spec)
            continue
        mac = normalize_mac(spec.cloud_mac or mac_by_host.get(spec.host, ""))
        camera = cloud_by_mac.get(mac)
        if camera is not None:
            if camera.registration_id in represented_ids:
                continue
            represented_ids.add(camera.registration_id)
            spec = replace(
                spec, cloud_mac=mac, cloud_registration_id=camera.registration_id
            )
        configured_specs.append(spec)

    discovered: list[HubbleLocalCameraSpec] = []
    discovered_hosts: set[str] = set()
    for camera in cameras:
        mac = normalize_mac(camera.mac_address or "")
        host = arp_by_mac.get(mac)
        if (
            not host
            or host in configured_by_host
            or host in discovered_hosts
            or camera.registration_id in represented_ids
        ):
            continue
        # The cloud inventory already establishes ownership of this exact MAC.
        # Preserve that association even when the optional local HTTP command
        # service is unavailable, so video can still fall back to Orbweb.
        discovered.append(
            HubbleLocalCameraSpec(
                name=camera.name,
                host=host,
                source="cloud_arp",
                cloud_mac=mac,
                cloud_registration_id=camera.registration_id,
            )
        )
        discovered_hosts.add(host)

    # Some locally reachable legacy cameras can disappear from every current
    # cloud inventory endpoint. Probe only neighbors already known to the
    # kernel, and accept one only when the Hubble getter confirms the ARP MAC.
    local_candidates = [
        (
            mac,
            HubbleLocalCameraSpec(
                name=f"Hubble {host}",
                host=host,
                source="arp_probe",
            ),
        )
        for mac, host in arp_by_mac.items()
        if host not in configured_by_host
        and host not in discovered_hosts
        and mac not in cloud_by_mac
    ]
    local_macs = await asyncio.gather(*(read_mac(spec) for _, spec in local_candidates))
    discovered.extend(
        spec
        for (expected, spec), mac in zip(local_candidates, local_macs, strict=True)
        if expected == mac
    )
    return (*configured_specs, *discovered)
