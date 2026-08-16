"""Hubble Connected integration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from go2rtc_client import Go2RtcRestClient
from homeassistant.components.go2rtc import _DATA_GO2RTC
from homeassistant.components.go2rtc.const import HA_MANAGED_URL
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud import (
    HubbleCloudCamera,
    HubbleCloudClient,
    HubbleCloudError,
    parse_cloud_camera_ids,
)
from .const import (
    CONF_CLOUD_CAMERA_IDS,
    CONF_CLOUD_LOGIN,
    CONF_CLOUD_PASSWORD,
    CONF_LOCAL_CAMERAS,
    DIRECT_RTSP_PASSWORD,
    DIRECT_RTSP_PATH,
    DIRECT_RTSP_PORT,
    DIRECT_RTSP_USERNAME,
    PLATFORMS,
)
from .coordinator import (
    HubbleCloudCoordinator,
    HubbleLocalCoordinator,
    HubbleOrbwebCommandCoordinator,
)
from .discovery import (
    async_discover_cloud_cameras,
    select_image_level_entity_specs,
    select_local_entity_specs,
)
from .local import (
    HubbleLocalCameraSpec,
    parse_local_camera_specs,
)
from .orbweb import OrbwebLanMappingPool
from .restream import HubbleRestreamError, HubbleRtspRestreamManager
from .rtsp import HubbleRtspEndpoint, async_probe_rtsp_candidates
from .stream_bindings import (
    HubbleOrbwebCommandBinding,
    HubbleOrbwebStreamBinding,
    build_orbweb_command_bindings,
    build_orbweb_stream_bindings,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class HubbleRuntimeData:
    """Runtime state shared by Hubble Connected entities."""

    local_camera_specs: tuple[HubbleLocalCameraSpec, ...]
    local_entity_specs: tuple[HubbleLocalCameraSpec, ...]
    image_level_entity_specs: tuple[HubbleLocalCameraSpec, ...]
    local_coordinator: HubbleLocalCoordinator | None
    cloud_coordinator: HubbleCloudCoordinator | None
    orbweb_command_coordinator: HubbleOrbwebCommandCoordinator | None
    cloud_cameras: tuple[HubbleCloudCamera, ...]
    cloud_command_cameras: tuple[HubbleCloudCamera, ...]
    orbweb_command_cameras: tuple[HubbleCloudCamera, ...]
    orbweb_command_bindings: tuple[HubbleOrbwebCommandBinding, ...]
    direct_rtsp_endpoints: dict[str, HubbleRtspEndpoint]
    orbweb_streams: tuple[HubbleOrbwebStreamBinding, ...]
    orbweb_mappings: OrbwebLanMappingPool | None
    rtsp_normalization_keys: frozenset[str]
    rtsp_restream: HubbleRtspRestreamManager | None


type HubbleConfigEntry = ConfigEntry[HubbleRuntimeData]

_LEGACY_BRIDGE_KEYS = frozenset(
    {"host", "port", "path", "username", "password", "stream_camera_host"}
)
_SUBSCRIPTION_DISCOVERY_MODEL_CODES = frozenset({"3667"})
_LOCAL_ENTITY_SUFFIXES = frozenset(
    {
        "ceiling_mount",
        "brightness",
        "brightness_setting",
        "connectivity",
        "contrast",
        "contrast_setting",
        "indicator_led",
        "night_vision",
        "temperature",
        "video_bitrate",
        "video_bitrate_setting",
        "wifi_strength",
    }
)
_LOCAL_IMAGE_ENTITY_SUFFIXES = frozenset(
    {"brightness", "brightness_setting", "contrast", "contrast_setting"}
)
_ORBWEB_3667_UNSUPPORTED_ENTITY_SUFFIXES = frozenset({"contrast", "contrast_setting"})


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
    configured_cloud_camera_ids = parse_cloud_camera_ids(
        entry.options.get(
            CONF_CLOUD_CAMERA_IDS,
            entry.data.get(CONF_CLOUD_CAMERA_IDS, ""),
        )
    )
    cloud_cameras: tuple[HubbleCloudCamera, ...] = ()
    command_camera_candidates: tuple[HubbleCloudCamera, ...] = ()
    cloud_command_cameras: tuple[HubbleCloudCamera, ...] = ()
    orbweb_command_cameras: tuple[HubbleCloudCamera, ...] = ()
    cloud_coordinator = None
    orbweb_command_coordinator = None
    cloud_client = None
    cloud_session = None
    cloud_login = entry.options.get(
        CONF_CLOUD_LOGIN, entry.data.get(CONF_CLOUD_LOGIN, "")
    )
    cloud_password = entry.options.get(
        CONF_CLOUD_PASSWORD, entry.data.get(CONF_CLOUD_PASSWORD, "")
    )
    if cloud_login and cloud_password:
        cloud_client = HubbleCloudClient(async_get_clientsession(hass))
        try:
            cloud_session = await cloud_client.async_authenticate(
                cloud_login, cloud_password
            )
        except HubbleCloudError as err:
            _LOGGER.warning(
                "Hubble cloud authentication unavailable; local entities remain "
                "active: %s",
                type(err).__name__,
            )
        else:
            try:
                cloud_cameras = await cloud_client.async_get_cameras(cloud_session)
            except HubbleCloudError as err:
                _LOGGER.warning(
                    "Hubble cloud inventory unavailable; local entities remain "
                    "active: %s",
                    type(err).__name__,
                )

            inventory_ids = {camera.registration_id for camera in cloud_cameras}
            recovered_cameras: list[HubbleCloudCamera] = []
            try:
                subscription_camera_ids = (
                    await cloud_client.async_get_subscription_camera_ids(cloud_session)
                )
            except HubbleCloudError as err:
                _LOGGER.warning(
                    "Hubble subscription inventory unavailable; normal cloud "
                    "inventory remains active: %s",
                    type(err).__name__,
                )
                subscription_camera_ids = ()
            for registration_id in subscription_camera_ids:
                if (
                    registration_id in inventory_ids
                    or registration_id[2:6] not in _SUBSCRIPTION_DISCOVERY_MODEL_CODES
                ):
                    continue
                try:
                    recovered_cameras.append(
                        await cloud_client.async_get_camera(
                            cloud_session, registration_id
                        )
                    )
                except HubbleCloudError as err:
                    _LOGGER.warning(
                        "Subscription-discovered Hubble 3667 is unavailable: %s",
                        type(err).__name__,
                    )
            inventory_ids.update(camera.registration_id for camera in recovered_cameras)

            manual_cameras: list[HubbleCloudCamera] = []
            for index, registration_id in enumerate(
                configured_cloud_camera_ids, start=1
            ):
                if registration_id in inventory_ids:
                    continue
                try:
                    manual_cameras.append(
                        await cloud_client.async_get_camera(
                            cloud_session, registration_id
                        )
                    )
                except HubbleCloudError as err:
                    _LOGGER.warning(
                        "Manual Hubble cloud camera %d is unavailable: %s",
                        index,
                        type(err).__name__,
                    )
            additional_cameras = (*recovered_cameras, *manual_cameras)
            command_camera_candidates = (
                *(camera for camera in cloud_cameras if camera.model_code == "3667"),
                *additional_cameras,
            )
            cloud_cameras = (*cloud_cameras, *additional_cameras)

            diagnostic_path = Path(hass.config.path("hubble_cloud_structure.json"))
            diagnostic_payload = [
                {
                    "name": camera.name,
                    "device_model_id": camera.device_model_id,
                    "root_keys": list(camera.structure_root_keys),
                    "orbweb_keys": list(camera.structure_orbweb_keys),
                    "device_status_keys": list(camera.structure_device_status_keys),
                    "device_keys": list(camera.structure_device_keys),
                    "has_mac": bool(camera.mac_address),
                    "has_orbweb": camera.has_orbweb_credentials,
                }
                for camera in cloud_cameras
            ]
            await hass.async_add_executor_job(
                diagnostic_path.write_text,
                json.dumps(diagnostic_payload, indent=2, sort_keys=True),
                "utf-8",
            )

    local_camera_specs = await async_discover_cloud_cameras(
        hass, cloud_cameras, configured_camera_specs
    )
    local_entity_specs = select_local_entity_specs(
        local_camera_specs, command_camera_candidates
    )
    coordinator = None
    if local_entity_specs:
        coordinator = HubbleLocalCoordinator(hass, local_entity_specs)
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
    coordinator_data = coordinator.data if coordinator is not None else {}
    image_level_entity_specs = select_image_level_entity_specs(
        local_entity_specs,
        coordinator_data,
        cloud_cameras,
    )
    local_macs = {
        spec.host: (
            coordinator_data[spec.host].mac if spec.host in coordinator_data else None
        )
        for spec in local_camera_specs
    }
    orbweb_streams = build_orbweb_stream_bindings(
        cloud_cameras,
        local_camera_specs,
        local_macs,
        direct_rtsp_endpoints,
    )
    orbweb_command_bindings = build_orbweb_command_bindings(
        command_camera_candidates,
        local_camera_specs,
    )
    orbweb_target_ids = {binding.key: binding.target_id for binding in orbweb_streams}
    orbweb_auth_passwords = {
        binding.key: binding.auth_password for binding in orbweb_streams
    }
    orbweb_route_hosts = {binding.key: binding.route_host for binding in orbweb_streams}
    for binding in orbweb_command_bindings:
        orbweb_target_ids[binding.key] = binding.target_id
        orbweb_auth_passwords[binding.key] = binding.auth_password
        orbweb_route_hosts[binding.key] = binding.route_host
    orbweb_mappings = (
        OrbwebLanMappingPool(
            async_get_clientsession(hass),
            orbweb_target_ids,
            auth_passwords=orbweb_auth_passwords,
            source_route_hosts=orbweb_route_hosts,
        )
        if orbweb_target_ids
        else None
    )
    orbweb_command_registration_ids = {
        binding.registration_id for binding in orbweb_command_bindings
    }
    orbweb_command_cameras = tuple(
        camera
        for camera in command_camera_candidates
        if camera.registration_id in orbweb_command_registration_ids
    )
    cloud_command_cameras = tuple(
        camera
        for camera in command_camera_candidates
        if camera.registration_id not in orbweb_command_registration_ids
    )
    if orbweb_command_cameras and orbweb_mappings is not None:
        orbweb_command_coordinator = HubbleOrbwebCommandCoordinator(
            hass,
            orbweb_mappings,
            orbweb_command_cameras,
            orbweb_command_bindings,
        )
        await orbweb_command_coordinator.async_config_entry_first_refresh()
    if cloud_command_cameras and cloud_client is not None and cloud_session is not None:
        cloud_coordinator = HubbleCloudCoordinator(
            hass,
            cloud_client,
            cloud_session,
            cloud_command_cameras,
        )
        await cloud_coordinator.async_config_entry_first_refresh()
    model_by_target = {
        camera.orbweb_sid: camera.model_code
        for camera in cloud_cameras
        if camera.orbweb_sid is not None
    }
    rtsp_normalization_keys = frozenset(
        binding.key
        for binding in orbweb_streams
        if model_by_target.get(binding.target_id) == "3667"
    )
    rtsp_restream = _create_rtsp_restream(hass, rtsp_normalization_keys)
    entry.runtime_data = HubbleRuntimeData(
        local_camera_specs=local_camera_specs,
        local_entity_specs=local_entity_specs,
        image_level_entity_specs=image_level_entity_specs,
        local_coordinator=coordinator,
        cloud_coordinator=cloud_coordinator,
        orbweb_command_coordinator=orbweb_command_coordinator,
        cloud_cameras=cloud_cameras,
        cloud_command_cameras=cloud_command_cameras,
        orbweb_command_cameras=orbweb_command_cameras,
        orbweb_command_bindings=orbweb_command_bindings,
        direct_rtsp_endpoints=direct_rtsp_endpoints,
        orbweb_streams=orbweb_streams,
        orbweb_mappings=orbweb_mappings,
        rtsp_normalization_keys=rtsp_normalization_keys,
        rtsp_restream=rtsp_restream,
    )
    removed_entities = _remove_suppressed_local_entities(
        hass,
        entry,
        local_camera_specs,
        local_entity_specs,
    )
    removed_entities += _remove_unsafe_image_entities(
        hass,
        entry,
        local_entity_specs,
        image_level_entity_specs,
    )
    removed_entities += _remove_unsupported_orbweb_command_entities(
        hass,
        entry,
        orbweb_command_cameras,
    )
    if removed_entities:
        _LOGGER.info(
            "Removed %d unsupported or unsafe Hubble entities",
            removed_entities,
        )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HubbleConfigEntry) -> bool:
    """Unload a Hubble Connected config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if entry.runtime_data.rtsp_restream is not None:
            try:
                await entry.runtime_data.rtsp_restream.async_close()
            except HubbleRestreamError as err:
                _LOGGER.warning("Normalized RTSP cleanup failed: %s", err)
        if entry.runtime_data.orbweb_mappings is not None:
            await entry.runtime_data.orbweb_mappings.async_close()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: HubbleConfigEntry) -> None:
    """Reload entities when local camera options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_suppressed_local_entities(
    hass: HomeAssistant,
    entry: HubbleConfigEntry,
    local_camera_specs: tuple[HubbleLocalCameraSpec, ...],
    local_entity_specs: tuple[HubbleLocalCameraSpec, ...],
) -> int:
    """Remove obsolete local-only entities while preserving camera entities."""
    active_hosts = {spec.host for spec in local_entity_specs}
    suppressed_hosts = {
        spec.host for spec in local_camera_specs if spec.host not in active_hosts
    }
    stale_unique_ids = {
        f"{host}:{suffix}"
        for host in suppressed_hosts
        for suffix in _LOCAL_ENTITY_SUFFIXES
    }
    if not stale_unique_ids:
        return 0

    registry = er.async_get(hass)
    removed = 0
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in stale_unique_ids:
            continue
        registry.async_remove(entity.entity_id)
        removed += 1
    return removed


def _remove_unsafe_image_entities(
    hass: HomeAssistant,
    entry: HubbleConfigEntry,
    local_entity_specs: tuple[HubbleLocalCameraSpec, ...],
    image_level_entity_specs: tuple[HubbleLocalCameraSpec, ...],
) -> int:
    """Remove image entities that can terminate video on unsafe models."""
    safe_hosts = {spec.host for spec in image_level_entity_specs}
    unsafe_hosts = {
        spec.host for spec in local_entity_specs if spec.host not in safe_hosts
    }
    stale_unique_ids = {
        f"{host}:{suffix}"
        for host in unsafe_hosts
        for suffix in _LOCAL_IMAGE_ENTITY_SUFFIXES
    }
    if not stale_unique_ids:
        return 0

    registry = er.async_get(hass)
    removed = 0
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in stale_unique_ids:
            continue
        registry.async_remove(entity.entity_id)
        removed += 1
    return removed


def _remove_unsupported_orbweb_command_entities(
    hass: HomeAssistant,
    entry: HubbleConfigEntry,
    cameras: tuple[HubbleCloudCamera, ...],
) -> int:
    """Remove 3667 controls whose local command returns no value."""
    stale_unique_ids = {
        f"cloud:{camera.registration_id}:{suffix}"
        for camera in cameras
        for suffix in _ORBWEB_3667_UNSUPPORTED_ENTITY_SUFFIXES
    }
    if not stale_unique_ids:
        return 0

    registry = er.async_get(hass)
    removed = 0
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id not in stale_unique_ids:
            continue
        registry.async_remove(entity.entity_id)
        removed += 1
    return removed


def _create_rtsp_restream(
    hass: HomeAssistant,
    normalization_keys: frozenset[str],
) -> HubbleRtspRestreamManager | None:
    """Use HA-managed go2rtc only when a 3667 needs media normalization."""
    if not normalization_keys:
        return None
    config = hass.data.get(_DATA_GO2RTC)
    if config is None or config.url != HA_MANAGED_URL:
        _LOGGER.warning(
            "Hubble 3667 media normalization requires Home Assistant-managed go2rtc"
        )
        return None

    client = Go2RtcRestClient(config.session, config.url)

    async def async_delete_stream(name: str) -> None:
        async with config.session.delete(
            urljoin(config.url, "api/streams"), params={"src": name}
        ) as response:
            response.raise_for_status()

    return HubbleRtspRestreamManager(client.streams, async_delete_stream)
