"""Verified numeric image controls for local Hubble cameras."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubbleConfigEntry
from .coordinator import HubbleLocalCoordinator, HubbleOrbwebCommandCoordinator
from .entity import HubbleLocalEntity, HubbleOrbwebCommandEntity
from .local import IMAGE_LEVEL_MAX, IMAGE_LEVEL_MIN, HubbleLocalError
from .orbweb.commands import HubbleOrbwebCommandError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up image controls only for models where they are stream-safe."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        entities: list[NumberEntity] = []
    else:
        entities = []
        for spec in entry.runtime_data.image_level_entity_specs:
            entities.extend(
                (
                    HubbleBrightnessNumber(coordinator, spec.host),
                    HubbleContrastNumber(coordinator, spec.host),
                )
            )
    orbweb_coordinator = entry.runtime_data.orbweb_command_coordinator
    if orbweb_coordinator is not None:
        entities.extend(
            HubbleOrbwebBrightnessNumber(
                orbweb_coordinator,
                camera.registration_id,
            )
            for camera in entry.runtime_data.orbweb_command_cameras
        )
    async_add_entities(entities)


class HubbleImageNumber(HubbleLocalEntity, NumberEntity):
    """Base class for bounded integer image controls."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = IMAGE_LEVEL_MIN
    _attr_native_max_value = IMAGE_LEVEL_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _value_field: str

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get(self._host)
        value = getattr(data, self._value_field) if data is not None else None
        return float(value) if value is not None else None

    async def _async_set(
        self,
        value: float,
        setter: Callable[[int], Awaitable[None]],
        setting_name: str,
    ) -> None:
        if not float(value).is_integer():
            raise HomeAssistantError(f"Hubble {setting_name} must be an integer")
        try:
            await setter(int(value))
        except HubbleLocalError as err:
            raise HomeAssistantError(
                f"Unable to set Hubble {setting_name} to {value:g}"
            ) from err
        await self.coordinator.async_request_refresh()


class HubbleBrightnessNumber(HubbleImageNumber):
    """Image brightness control."""

    _attr_translation_key = "brightness_setting"
    _attr_icon = "mdi:brightness-6"
    _value_field = "brightness"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:brightness_setting"

    async def async_set_native_value(self, value: float) -> None:
        await self._async_set(
            value,
            self.coordinator.clients[self._host].async_set_brightness,
            "image brightness",
        )


class HubbleContrastNumber(HubbleImageNumber):
    """Image contrast control."""

    _attr_translation_key = "contrast_setting"
    _attr_icon = "mdi:contrast-circle"
    _value_field = "contrast"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:contrast_setting"

    async def async_set_native_value(self, value: float) -> None:
        await self._async_set(
            value,
            self.coordinator.clients[self._host].async_set_contrast,
            "image contrast",
        )


class HubbleOrbwebBrightnessNumber(HubbleOrbwebCommandEntity, NumberEntity):
    """Official-app-mapped 3667 image brightness control."""

    _attr_translation_key = "brightness_setting"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:brightness-6"
    _attr_native_min_value = 1
    _attr_native_max_value = 8
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: HubbleOrbwebCommandCoordinator,
        registration_id: str,
    ) -> None:
        super().__init__(coordinator, registration_id)
        self._attr_unique_id = f"cloud:{registration_id}:brightness_setting"

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self._registration_id)
        return data is not None and data.brightness is not None

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get(self._registration_id)
        return (
            float(data.brightness)
            if data is not None and data.brightness is not None
            else None
        )

    async def async_set_native_value(self, value: float) -> None:
        if not float(value).is_integer():
            raise HomeAssistantError("Hubble image brightness must be an integer")
        try:
            await self.coordinator.clients[self._registration_id].async_set_brightness(
                int(value)
            )
        except HubbleOrbwebCommandError as err:
            raise HomeAssistantError(
                f"Unable to set Hubble image brightness to {value:g}"
            ) from err
        await self.coordinator.async_request_refresh()
