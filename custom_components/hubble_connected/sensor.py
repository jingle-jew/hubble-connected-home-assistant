"""Read-only sensors for local Hubble cameras."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubbleConfigEntry
from .coordinator import HubbleLocalCoordinator
from .entity import HubbleLocalEntity


async def async_setup_entry(
    hass,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up local measurements for every configured camera."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return
    entities: list[SensorEntity] = []
    for spec in entry.runtime_data.local_camera_specs:
        entities.extend(
            (
                HubbleTemperatureSensor(coordinator, spec.host),
                HubbleWifiStrengthSensor(coordinator, spec.host),
                HubbleVideoBitrateSensor(coordinator, spec.host),
            )
        )
    async_add_entities(entities)


class HubbleTemperatureSensor(HubbleLocalEntity, SensorEntity):
    """Temperature reported by the camera's local Nuvoton API."""

    _attr_has_entity_name = True
    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:temperature"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get(self._host)
        return data.temperature if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"host": self._host, "source": "local_http"}


class HubbleWifiStrengthSensor(HubbleLocalEntity, SensorEntity):
    """Camera-reported Wi-Fi quality percentage."""

    _attr_has_entity_name = True
    _attr_translation_key = "wifi_strength"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:wifi"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:wifi_strength"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data.get(self._host)
        return data.wifi_strength if data is not None else None


class HubbleVideoBitrateSensor(HubbleLocalEntity, SensorEntity):
    """Current video bitrate selected by the camera."""

    _attr_has_entity_name = True
    _attr_translation_key = "video_bitrate"
    _attr_native_unit_of_measurement = "kbit/s"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:video_bitrate"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data.get(self._host)
        return data.video_bitrate if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str | int]:
        data = self.coordinator.data.get(self._host)
        attributes: dict[str, str | int] = {"host": self._host}
        if data is None:
            return attributes
        for key in (
            "resolution",
            "soc_version",
            "timezone",
            "night_vision",
            "speaker_volume",
        ):
            value = getattr(data, key)
            if value is not None:
                attributes[key] = value
        return attributes
