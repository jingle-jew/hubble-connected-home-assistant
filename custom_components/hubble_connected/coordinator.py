"""Coordinator for local read-only Hubble camera data."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
