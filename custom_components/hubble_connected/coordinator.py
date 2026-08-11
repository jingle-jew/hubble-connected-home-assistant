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
class HubbleCloudCameraData:
    """Latest cloud-command state for a camera missing from inventory."""

    camera: HubbleCloudCamera
    temperature: float | None
    wifi_strength: int | None
    video_bitrate: int | None
    available: bool
    error: str | None = None


class HubbleCloudCoordinator(DataUpdateCoordinator[dict[str, HubbleCloudCameraData]]):
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

    async def _async_update_data(self) -> dict[str, HubbleCloudCameraData]:
        async def update(camera: HubbleCloudCamera) -> HubbleCloudCameraData:
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
            return HubbleCloudCameraData(
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
