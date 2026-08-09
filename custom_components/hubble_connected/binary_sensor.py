"""Read-only binary sensors for local Hubble cameras."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubbleConfigEntry
from .coordinator import HubbleLocalCoordinator
from .entity import HubbleLocalEntity


async def async_setup_entry(
    hass,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one connectivity diagnostic per configured camera."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return
    async_add_entities(
        HubbleConnectivitySensor(coordinator, spec.host)
        for spec in entry.runtime_data.local_camera_specs
    )


class HubbleConnectivitySensor(HubbleLocalEntity, BinarySensorEntity):
    """Connectivity reported by the camera's Wi-Fi getter."""

    _attr_translation_key = "connectivity"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:connectivity"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data.get(self._host)
        if data is None or data.wifi_connection_state is None:
            return None
        return data.wifi_connection_state.upper() == "CONNECTED"
