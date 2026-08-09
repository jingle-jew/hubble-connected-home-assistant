"""Hubble Connected integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import HubbleCloudCamera, HubbleCloudClient, HubbleCloudError
from .const import (
    CONF_CLOUD_LOGIN,
    CONF_CLOUD_PASSWORD,
    CONF_LOCAL_CAMERAS,
    DIRECT_RTSP_PASSWORD,
    DIRECT_RTSP_PATH,
    DIRECT_RTSP_PORT,
    DIRECT_RTSP_USERNAME,
    PLATFORMS,
)
from .coordinator import HubbleLocalCoordinator
from .discovery import async_discover_cloud_cameras, normalize_mac
from .local import HubbleLocalCameraSpec, parse_local_camera_specs
from .orbweb import OrbwebLanMappingPool
from .rtsp import HubbleRtspEndpoint, async_probe_rtsp_candidates

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class HubbleRuntimeData:
    """Runtime state shared by Hubble Connected entities."""

    local_camera_specs: tuple[HubbleLocalCameraSpec, ...]
    local_coordinator: HubbleLocalCoordinator | None
    cloud_cameras: tuple[HubbleCloudCamera, ...]
    direct_rtsp_endpoints: dict[str, HubbleRtspEndpoint]
    orbweb_mappings: OrbwebLanMappingPool | None


type HubbleConfigEntry = ConfigEntry[HubbleRuntimeData]

_LEGACY_BRIDGE_KEYS = frozenset(
    {"host", "port", "path", "username", "password", "stream_camera_host"}
)


async def async_migrate_entry(hass: HomeAssistant, entry: HubbleConfigEntry) -> bool:
    """Remove unsupported development-bridge settings from older entries."""
    if entry.version >= 2:
        return True
    data = {
        key: value
        for key, value in entry.data.items()
        if key not in _LEGACY_BRIDGE_KEYS
    }
    options = {
        key: value
        for key, value in entry.options.items()
        if key not in _LEGACY_BRIDGE_KEYS
    }
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=2,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HubbleConfigEntry) -> bool:
    """Set up Hubble Connected from a config entry."""
    configured_camera_specs = parse_local_camera_specs(
        entry.options.get(CONF_LOCAL_CAMERAS, entry.data.get(CONF_LOCAL_CAMERAS, ""))
    )
    cloud_cameras: tuple[HubbleCloudCamera, ...] = ()
    cloud_login = entry.options.get(
        CONF_CLOUD_LOGIN, entry.data.get(CONF_CLOUD_LOGIN, "")
    )
    cloud_password = entry.options.get(
        CONF_CLOUD_PASSWORD, entry.data.get(CONF_CLOUD_PASSWORD, "")
    )
    if cloud_login and cloud_password:
        try:
            cloud_client = HubbleCloudClient(async_get_clientsession(hass))
            cloud_session = await cloud_client.async_authenticate(
                cloud_login, cloud_password
            )
            cloud_cameras = await cloud_client.async_get_cameras(cloud_session)
        except HubbleCloudError as err:
            _LOGGER.warning(
                "Hubble cloud inventory unavailable; local entities remain active: %s",
                type(err).__name__,
            )

    local_camera_specs = await async_discover_cloud_cameras(
        hass, cloud_cameras, configured_camera_specs
    )
    coordinator = None
    if local_camera_specs:
        coordinator = HubbleLocalCoordinator(hass, local_camera_specs)
        await coordinator.async_config_entry_first_refresh()
    direct_candidates = (
        HubbleRtspEndpoint(
            host=spec.host,
            port=DIRECT_RTSP_PORT,
            path=DIRECT_RTSP_PATH,
            username=DIRECT_RTSP_USERNAME,
            password=DIRECT_RTSP_PASSWORD,
        )
        for spec in local_camera_specs
    )
    direct_rtsp_endpoints = {
        endpoint.host: endpoint
        for endpoint in await async_probe_rtsp_candidates(direct_candidates)
    }
    cloud_by_mac = {
        normalize_mac(camera.mac_address or ""): camera
        for camera in cloud_cameras
        if camera.has_orbweb_credentials and camera.mac_address
    }
    orbweb_target_ids: dict[str, str] = {}
    orbweb_auth_passwords: dict[str, str] = {}
    if coordinator is not None:
        for host, data in coordinator.data.items():
            camera = cloud_by_mac.get(normalize_mac(data.mac or ""))
            if (
                host not in direct_rtsp_endpoints
                and camera is not None
                and camera.orbweb_sid is not None
                and camera.orbweb_password is not None
            ):
                orbweb_target_ids[host] = camera.orbweb_sid
                orbweb_auth_passwords[host] = camera.orbweb_password
    orbweb_mappings = (
        OrbwebLanMappingPool(
            async_get_clientsession(hass),
            orbweb_target_ids,
            auth_passwords=orbweb_auth_passwords,
        )
        if orbweb_target_ids
        else None
    )
    entry.runtime_data = HubbleRuntimeData(
        local_camera_specs=local_camera_specs,
        local_coordinator=coordinator,
        cloud_cameras=cloud_cameras,
        direct_rtsp_endpoints=direct_rtsp_endpoints,
        orbweb_mappings=orbweb_mappings,
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HubbleConfigEntry) -> bool:
    """Unload a Hubble Connected config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.runtime_data.orbweb_mappings is not None:
        await entry.runtime_data.orbweb_mappings.async_close()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: HubbleConfigEntry) -> None:
    """Reload entities when local camera options change."""
    await hass.config_entries.async_reload(entry.entry_id)
