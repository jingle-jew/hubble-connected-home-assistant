"""Conservative LAN discovery using cloud MACs and the kernel ARP table."""

from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any

from .cloud import HubbleCloudCamera
from .local import HubbleLocalCameraSpec, HubbleLocalClient, HubbleLocalError

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
        if normalize_mac(spec.cloud_mac or "") not in cloud_polled_3667_macs
    )


async def async_discover_cloud_cameras(
    hass: Any,
    cameras: tuple[HubbleCloudCamera, ...],
    configured: tuple[HubbleLocalCameraSpec, ...],
) -> tuple[HubbleLocalCameraSpec, ...]:
    """Discover owned cloud cameras by MAC and verify unknown ARP neighbors."""
    try:
        arp_text = await hass.async_add_executor_job(ARP_PATH.read_text, "utf-8")
    except OSError:
        return configured

    arp_by_mac = parse_arp_table(arp_text)
    cloud_mac_by_host = {
        host: mac
        for camera in cameras
        if (mac := normalize_mac(camera.mac_address or ""))
        if (host := arp_by_mac.get(mac))
    }
    configured = tuple(
        spec
        if spec.cloud_mac or spec.host not in cloud_mac_by_host
        else HubbleLocalCameraSpec(
            name=spec.name,
            host=spec.host,
            source=spec.source,
            cloud_mac=cloud_mac_by_host[spec.host],
        )
        for spec in configured
    )
    configured_hosts = {spec.host for spec in configured}
    discovered: list[HubbleLocalCameraSpec] = []
    for camera in cameras:
        mac = normalize_mac(camera.mac_address or "")
        host = arp_by_mac.get(mac)
        if not host or host in configured_hosts:
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
            )
        )

    discovered_hosts = {spec.host for spec in discovered}
    semaphore = asyncio.Semaphore(LOCAL_PROBE_CONCURRENCY)

    async def verify(
        expected_mac: str,
        spec: HubbleLocalCameraSpec,
        timeout: float = 5.0,
    ) -> HubbleLocalCameraSpec | None:
        try:
            async with semaphore:
                local_mac = await HubbleLocalClient(spec).async_get_mac_address(
                    timeout=timeout
                )
        except HubbleLocalError:
            return None
        return spec if normalize_mac(local_mac) == expected_mac else None

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
        if host not in configured_hosts and host not in discovered_hosts
    ]
    local_verified = await asyncio.gather(
        *(verify(mac, spec, LOCAL_PROBE_TIMEOUT) for mac, spec in local_candidates)
    )
    discovered.extend(spec for spec in local_verified if spec is not None)
    return configured + tuple(discovered)
