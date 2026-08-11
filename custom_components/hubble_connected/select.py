"""Verified writable controls for local Hubble cameras."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubbleConfigEntry
from .const import BITRATE_OPTIONS, NIGHT_VISION_OPTIONS
from .coordinator import HubbleLocalCoordinator
from .entity import HubbleLocalEntity
from .local import HubbleLocalError

NIGHT_VISION_VALUES = {option: value for value, option in NIGHT_VISION_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up controls confirmed against the MBP167 local API."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return
    entities: list[SelectEntity] = []
    for spec in entry.runtime_data.local_entity_specs:
        entities.extend(
            (
                HubbleNightVisionSelect(coordinator, spec.host),
                HubbleVideoBitrateSelect(coordinator, spec.host),
            )
        )
    async_add_entities(entities)


class HubbleNightVisionSelect(HubbleLocalEntity, SelectEntity):
    """Auto/on/off infrared night-vision control."""

    _attr_translation_key = "night_vision"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(NIGHT_VISION_VALUES)
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:night_vision"

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data.get(self._host)
        if data is None or data.night_vision is None:
            return None
        return NIGHT_VISION_OPTIONS.get(data.night_vision)

    async def async_select_option(self, option: str) -> None:
        try:
            value = NIGHT_VISION_VALUES[option]
            await self.coordinator.clients[self._host].async_set_night_vision(value)
        except (KeyError, HubbleLocalError) as err:
            raise HomeAssistantError(
                f"Unable to set Hubble night vision to {option}"
            ) from err
        await self.coordinator.async_request_refresh()


class HubbleVideoBitrateSelect(HubbleLocalEntity, SelectEntity):
    """Video bitrate control with firmware-verified values."""

    _attr_translation_key = "video_bitrate_setting"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [str(value) for value in BITRATE_OPTIONS]
    _attr_icon = "mdi:tune-variant"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:video_bitrate_setting"

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data.get(self._host)
        return (
            str(data.video_bitrate)
            if data is not None and data.video_bitrate is not None
            else None
        )

    async def async_select_option(self, option: str) -> None:
        try:
            value = int(option)
            await self.coordinator.clients[self._host].async_set_video_bitrate(value)
        except (ValueError, HubbleLocalError) as err:
            raise HomeAssistantError(
                f"Unable to set Hubble video bitrate to {option}"
            ) from err
        await self.coordinator.async_request_refresh()
