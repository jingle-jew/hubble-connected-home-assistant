"""Coordinator for local read-only Hubble camera data."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .cloud import (
    HubbleCloudCamera,
    HubbleCloudClient,
    HubbleCloudError,
    HubbleCloudSession,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .local import HubbleLocalCameraData, HubbleLocalCameraSpec, HubbleLocalClient
from .orbweb.commands import HubbleOrbwebCommandClient, HubbleOrbwebCommandError
from .orbweb.pool import OrbwebLanMappingPool
from .stream_bindings import HubbleOrbwebCommandBinding

_LOGGER = logging.getLogger(__name__)


class HubbleLocalCoordinator(DataUpdateCoordinator[dict[str, HubbleLocalCameraData]]):
    """Poll changing local values while preserving per-camera availability."""

    def __init__(
        self, hass: HomeAssistant, specs: tuple[HubbleLocalCameraSpec, ...]
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_local",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.clients = {spec.host: HubbleLocalClient(spec) for spec in specs}

    async def _async_update_data(self) -> dict[str, HubbleLocalCameraData]:
        results = await asyncio.gather(
            *(client.async_update() for client in self.clients.values())
        )
        return {result.spec.host: result for result in results}


@dataclass(frozen=True, slots=True)
class HubbleCommandCameraData:
    """Latest state returned by read-only camera getters."""

    camera: HubbleCloudCamera
    temperature: float | None
    wifi_strength: int | None
    video_bitrate: int | None
    available: bool
    brightness: int | None = None
    contrast: int | None = None
    night_vision: int | None = None
    flipup: int | None = None
    error: str | None = None


class HubbleCloudCoordinator(DataUpdateCoordinator[dict[str, HubbleCommandCameraData]]):
    """Poll manual cloud cameras independently from the local camera path."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: HubbleCloudClient,
        session: HubbleCloudSession,
        cameras: tuple[HubbleCloudCamera, ...],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_cloud_commands",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.session = session
        self.cameras = {camera.registration_id: camera for camera in cameras}

    async def _async_update_data(self) -> dict[str, HubbleCommandCameraData]:
        async def update(camera: HubbleCloudCamera) -> HubbleCommandCameraData:
            errors: list[str] = []

            async def read(name: str, command):
                try:
                    return await command(self.session, camera.registration_id)
                except HubbleCloudError as err:
                    errors.append(f"{name}:{type(err).__name__}")
                    return None

            temperature = await read("temperature", self.client.async_get_temperature)
            wifi_strength = await read(
                "wifi_strength", self.client.async_get_wifi_strength
            )
            video_bitrate = await read(
                "video_bitrate", self.client.async_get_video_bitrate
            )
            return HubbleCommandCameraData(
                camera=camera,
                temperature=temperature,
                wifi_strength=wifi_strength,
                video_bitrate=video_bitrate,
                available=any(
                    value is not None
                    for value in (temperature, wifi_strength, video_bitrate)
                ),
                error=",".join(errors) or None,
            )

        results = await asyncio.gather(
            *(update(camera) for camera in self.cameras.values())
        )
        return {result.camera.registration_id: result for result in results}


class HubbleOrbwebCommandCoordinator(
    DataUpdateCoordinator[dict[str, HubbleCommandCameraData]]
):
    """Poll 3667 getters through the camera's Orbweb-mapped LAN port 80."""

    def __init__(
        self,
        hass: HomeAssistant,
        mappings: OrbwebLanMappingPool,
        cameras: tuple[HubbleCloudCamera, ...],
        bindings: tuple[HubbleOrbwebCommandBinding, ...],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_orbweb_commands",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.cameras = {camera.registration_id: camera for camera in cameras}
        self.bindings = {
            binding.registration_id: binding for binding in bindings
        }
        self.clients = {
            registration_id: HubbleOrbwebCommandClient(mappings, binding.key)
            for registration_id, binding in self.bindings.items()
        }

    async def _async_update_data(self) -> dict[str, HubbleCommandCameraData]:
        async def update(camera: HubbleCloudCamera) -> HubbleCommandCameraData:
            client = self.clients[camera.registration_id]
            errors: list[str] = []

            try:
                await client.async_prepare()
            except HubbleOrbwebCommandError as err:
                return HubbleCommandCameraData(
                    camera=camera,
                    temperature=None,
                    wifi_strength=None,
                    video_bitrate=None,
                    available=False,
                    error=f"transport:{type(err).__name__}",
                )

            async def read(name: str, command):
                try:
                    return await command()
                except HubbleOrbwebCommandError as err:
                    errors.append(f"{name}:{type(err).__name__}")
                    return None

            temperature = await read("temperature", client.async_get_temperature)
            wifi_strength = await read(
                "wifi_strength", client.async_get_wifi_strength
            )
            video_bitrate = await read(
                "video_bitrate", client.async_get_video_bitrate
            )
            brightness = await read("brightness", client.async_get_brightness)
            night_vision = await read(
                "night_vision", client.async_get_night_vision
            )
            flipup = await read("flipup", client.async_get_flipup)
            return HubbleCommandCameraData(
                camera=camera,
                temperature=temperature,
                wifi_strength=wifi_strength,
                video_bitrate=video_bitrate,
                brightness=brightness,
                contrast=None,
                night_vision=night_vision,
                flipup=flipup,
                available=any(
                    value is not None
                    for value in (
                        temperature,
                        wifi_strength,
                        video_bitrate,
                        brightness,
                        night_vision,
                        flipup,
                    )
                ),
                error=",".join(errors) or None,
            )

        results = await asyncio.gather(
            *(update(camera) for camera in self.cameras.values())
        )
        return {result.camera.registration_id: result for result in results}
