"""Diagnostics for Hubble Connected."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HubbleConfigEntry
from .discovery import normalize_mac


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HubbleConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without credentials."""
    direct_endpoints = entry.runtime_data.direct_rtsp_endpoints
    orbweb_mappings = entry.runtime_data.orbweb_mappings
    coordinator = entry.runtime_data.local_coordinator
    cloud_coordinator = entry.runtime_data.cloud_coordinator
    cloud_macs = {
        normalize_mac(camera.mac_address)
        for camera in entry.runtime_data.cloud_cameras
        if camera.mac_address
    }
    discovered_cloud_macs = {
        normalize_mac(spec.cloud_mac or "")
        for spec in entry.runtime_data.local_camera_specs
        if spec.cloud_mac
    }
    local_cameras: list[dict[str, Any]] = []
    if coordinator is not None:
        for index, data in enumerate(coordinator.data.values(), start=1):
            local_cameras.append(
                {
                    "camera_index": index,
                    "discovery_source": data.spec.source,
                    "cloud_match": (
                        normalize_mac(data.spec.cloud_mac or data.mac or "")
                        in cloud_macs
                    ),
                    "available": data.available,
                    "temperature_c": data.temperature,
                    "wifi_connection_state": data.wifi_connection_state,
                    "wifi_strength_percent": data.wifi_strength,
                    "video_bitrate_kbit_s": data.video_bitrate,
                    "brightness": data.brightness,
                    "contrast": data.contrast,
                    "indicator_led": data.blink_led,
                    "ceiling_mount": data.flipup,
                    "firmware_version": data.firmware_version,
                    "default_version": data.default_version,
                    "model_code": data.model_code,
                    "soc_version": data.soc_version,
                    "night_vision": data.night_vision,
                    "resolution": data.resolution,
                    "speaker_volume": data.speaker_volume,
                    "has_error": data.error is not None,
                }
            )
    backends: list[str] = []
    if direct_endpoints:
        backends.append("local_rtsp")
    if orbweb_mappings is not None:
        backends.append("orbweb_lan")
    backend = "+".join(backends) if backends else "local_only"
    return {
        "backend": backend,
        "direct_rtsp_camera_count": len(direct_endpoints),
        "orbweb_lan_camera_count": (
            len(orbweb_mappings.hosts) if orbweb_mappings is not None else 0
        ),
        "verified_controls": {
            "ceiling_mount": {"off": 0, "on": 1},
            "indicator_led": {"off": 0, "on": 1},
            "night_vision": {"auto": 0, "on": 1, "off": 2},
            "video_bitrate_kbit_s": [100, 200, 300, 400, 600, 800, 1000],
            "brightness": {"minimum": 1, "maximum": 8, "step": 1},
            "contrast": {"minimum": 1, "maximum": 8, "step": 1},
        },
        "local_cameras": local_cameras,
        "cloud_command_cameras": [
            {
                "camera_index": index,
                "model_code": (
                    data.camera.registration_id[2:6]
                    if len(data.camera.registration_id) >= 6
                    else None
                ),
                "available": data.available,
                "temperature_c": data.temperature,
                "wifi_strength_percent": data.wifi_strength,
                "video_bitrate_kbit_s": data.video_bitrate,
                "has_error": data.error is not None,
            }
            for index, data in enumerate(
                cloud_coordinator.data.values() if cloud_coordinator else (),
                start=1,
            )
        ],
        "cloud_inventory": [
            {
                "camera_index": index,
                "device_model_id": camera.device_model_id,
                "firmware_version": camera.firmware_version,
                "status": camera.status,
                "is_available": camera.is_available,
                "network_strength": camera.network_strength,
                "cloud_temperature_c": camera.cloud_temperature,
                "setting_keys": sorted(camera.settings),
                "has_snapshot": bool(camera.snapshot_url),
                "has_mac": bool(normalize_mac(camera.mac_address or "")),
                "arp_match": (
                    normalize_mac(camera.mac_address or "") in discovered_cloud_macs
                ),
                "has_orbweb_credentials": camera.has_orbweb_credentials,
            }
            for index, camera in enumerate(entry.runtime_data.cloud_cameras, start=1)
        ],
    }
