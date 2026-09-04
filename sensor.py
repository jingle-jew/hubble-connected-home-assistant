"""Read-only sensors for local Hubble cameras."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HubbleConfigEntry
from .const import DOMAIN
from .coordinator import (
    HubbleCloudCoordinator,
    HubbleLocalCoordinator,
    HubbleOrbwebCommandCoordinator,
)
from .entity import HubbleLocalEntity


async def async_setup_entry(
    hass,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up local measurements for every configured camera."""
    coordinator = entry.runtime_data.local_coordinator
    entities: list[SensorEntity] = []
    if coordinator is not None:
        for spec in entry.runtime_data.local_entity_specs:
            entities.extend(
                (
                    HubbleTemperatureSensor(coordinator, spec.host),
                    HubbleWifiStrengthSensor(coordinator, spec.host),
                    HubbleVideoBitrateSensor(coordinator, spec.host),
                )
            )
        for spec in entry.runtime_data.image_level_entity_specs:
            entities.extend(
                (
                    HubbleBrightnessSensor(coordinator, spec.host),
                    HubbleContrastSensor(coordinator, spec.host),
                )
            )
    cloud_coordinator = entry.runtime_data.cloud_coordinator
    if cloud_coordinator is not None:
        for camera in entry.runtime_data.cloud_command_cameras:
            entities.extend(
                (
                    HubbleCommandTemperatureSensor(
                        cloud_coordinator,
                        camera.registration_id,
                        "cloud_publish_command",
                    ),
                    HubbleCommandWifiStrengthSensor(
                        cloud_coordinator,
                        camera.registration_id,
                        "cloud_publish_command",
                    ),
                    HubbleCommandVideoBitrateSensor(
                        cloud_coordinator,
                        camera.registration_id,
                        "cloud_publish_command",
                    ),
                )
            )
    orbweb_coordinator = entry.runtime_data.orbweb_command_coordinator
    if orbweb_coordinator is not None:
        for camera in entry.runtime_data.orbweb_command_cameras:
            entities.extend(
                (
                    HubbleCommandTemperatureSensor(
                        orbweb_coordinator,
                        camera.registration_id,
                        "local_orbweb_http",
                    ),
                    HubbleCommandWifiStrengthSensor(
                        orbweb_coordinator,
                        camera.registration_id,
                        "local_orbweb_http",
                    ),
                    HubbleCommandVideoBitrateSensor(
                        orbweb_coordinator,
                        camera.registration_id,
                        "local_orbweb_http",
                    ),
                    HubbleCommandBrightnessSensor(
                        orbweb_coordinator,
                        camera.registration_id,
                        "local_orbweb_http",
                    ),
                )
            )
    async_add_entities(entities)


type HubbleCommandCoordinator = HubbleCloudCoordinator | HubbleOrbwebCommandCoordinator


class HubbleCommandSensor(CoordinatorEntity[HubbleCommandCoordinator], SensorEntity):
    """Base sensor read through a camera command transport."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _value_field: str

    def __init__(
        self,
        coordinator: HubbleCommandCoordinator,
        registration_id: str,
        unique_suffix: str,
        source: str,
    ) -> None:
        super().__init__(coordinator)
        self._registration_id = registration_id
        self._source = source
        self._attr_unique_id = f"cloud:{registration_id}:{unique_suffix}"

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self._registration_id)
        return data is not None and getattr(data, self._value_field) is not None

    @property
    def native_value(self) -> float | int | None:
        data = self.coordinator.data.get(self._registration_id)
        return getattr(data, self._value_field) if data is not None else None

    @property
    def device_info(self) -> DeviceInfo:
        camera = self.coordinator.cameras[self._registration_id]
        connections = set()
        if camera.mac_address:
            connections.add((dr.CONNECTION_NETWORK_MAC, camera.mac_address))
        model_code = camera.model_code
        model = "Hubble camera"
        if model_code and model_code.isdigit():
            model = f"{model} ({model_code})"
        return DeviceInfo(
            identifiers={(DOMAIN, f"cloud:{camera.registration_id}")},
            connections=connections,
            name=camera.name,
            manufacturer="Hubble Connected / Motorola",
            model=model,
            sw_version=camera.firmware_version,
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"source": self._source}


class HubbleCommandTemperatureSensor(HubbleCommandSensor):
    """Temperature read through a camera command transport."""

    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1
    _value_field = "temperature"

    def __init__(
        self,
        coordinator: HubbleCommandCoordinator,
        registration_id: str,
        source: str,
    ) -> None:
        super().__init__(coordinator, registration_id, "temperature", source)


class HubbleCommandWifiStrengthSensor(HubbleCommandSensor):
    """Wi-Fi quality read through a camera command transport."""

    _attr_translation_key = "wifi_strength"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:wifi"
    _value_field = "wifi_strength"

    def __init__(
        self,
        coordinator: HubbleCommandCoordinator,
        registration_id: str,
        source: str,
    ) -> None:
        super().__init__(coordinator, registration_id, "wifi_strength", source)


class HubbleCommandVideoBitrateSensor(HubbleCommandSensor):
    """Video bitrate read through a camera command transport."""

    _attr_translation_key = "video_bitrate"
    _attr_native_unit_of_measurement = "kbit/s"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:speedometer"
    _value_field = "video_bitrate"

    def __init__(
        self,
        coordinator: HubbleCommandCoordinator,
        registration_id: str,
        source: str,
    ) -> None:
        super().__init__(coordinator, registration_id, "video_bitrate", source)


class HubbleCommandBrightnessSensor(HubbleCommandSensor):
    """Image brightness read through the mapped 3667 command service."""

    _attr_translation_key = "brightness"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:brightness-6"
    _value_field = "brightness"

    def __init__(
        self,
        coordinator: HubbleOrbwebCommandCoordinator,
        registration_id: str,
        source: str,
    ) -> None:
        super().__init__(coordinator, registration_id, "brightness", source)


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


class HubbleBrightnessSensor(HubbleLocalEntity, SensorEntity):
    """Current image brightness level reported by the camera."""

    _attr_translation_key = "brightness"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:brightness-6"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:brightness"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data.get(self._host)
        return data.brightness if data is not None else None


class HubbleContrastSensor(HubbleLocalEntity, SensorEntity):
    """Current image contrast level reported by the camera."""

    _attr_translation_key = "contrast"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:contrast-circle"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:contrast"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data.get(self._host)
        return data.contrast if data is not None else None
