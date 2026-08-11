"""Verified writable controls for local Hubble cameras."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubbleConfigEntry
from .const import (
    BITRATE_OPTIONS,
    NIGHT_VISION_OPTIONS,
    ORBWEB_3667_BITRATE_OPTIONS,
)
from .coordinator import HubbleLocalCoordinator, HubbleOrbwebCommandCoordinator
from .entity import HubbleLocalEntity, HubbleOrbwebCommandEntity
from .local import HubbleLocalError
from .orbweb.commands import HubbleOrbwebCommandError

NIGHT_VISION_VALUES = {option: value for value, option in NIGHT_VISION_OPTIONS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up controls confirmed against the MBP167 local API."""
    coordinator = entry.runtime_data.local_coordinator
    entities: list[SelectEntity] = []
    if coordinator is not None:
        for spec in entry.runtime_data.local_entity_specs:
            entities.extend(
                (
                    HubbleNightVisionSelect(coordinator, spec.host),
                    HubbleVideoBitrateSelect(coordinator, spec.host),
                )
            )
    orbweb_coordinator = entry.runtime_data.orbweb_command_coordinator
    if orbweb_coordinator is not None:
        for camera in entry.runtime_data.orbweb_command_cameras:
            entities.extend(
                (
                    HubbleOrbwebNightVisionSelect(
                        orbweb_coordinator,
                        camera.registration_id,
                    ),
                    HubbleOrbwebVideoBitrateSelect(
                        orbweb_coordinator,
                        camera.registration_id,
                    ),
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


class HubbleOrbwebNightVisionSelect(HubbleOrbwebCommandEntity, SelectEntity):
    """Official-app-mapped auto/on/off night-vision control for the 3667."""

    _attr_translation_key = "night_vision"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(NIGHT_VISION_VALUES)
    _attr_icon = "mdi:weather-night"

    def __init__(
        self,
        coordinator: HubbleOrbwebCommandCoordinator,
        registration_id: str,
    ) -> None:
        super().__init__(coordinator, registration_id)
        self._attr_unique_id = f"cloud:{registration_id}:night_vision"

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self._registration_id)
        return data is not None and data.night_vision in NIGHT_VISION_OPTIONS

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data.get(self._registration_id)
        return (
            NIGHT_VISION_OPTIONS.get(data.night_vision)
            if data is not None
            else None
        )

    async def async_select_option(self, option: str) -> None:
        try:
            value = NIGHT_VISION_VALUES[option]
            await self.coordinator.clients[
                self._registration_id
            ].async_set_night_vision(value)
        except (KeyError, HubbleOrbwebCommandError) as err:
            raise HomeAssistantError(
                f"Unable to set Hubble night vision to {option}"
            ) from err
        await self.coordinator.async_request_refresh()


class HubbleOrbwebVideoBitrateSelect(HubbleOrbwebCommandEntity, SelectEntity):
    """Official-app-mapped 3667 Orbweb video-bitrate control."""

    _attr_translation_key = "video_bitrate_setting"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [str(value) for value in ORBWEB_3667_BITRATE_OPTIONS]
    _attr_icon = "mdi:tune-variant"

    def __init__(
        self,
        coordinator: HubbleOrbwebCommandCoordinator,
        registration_id: str,
    ) -> None:
        super().__init__(coordinator, registration_id)
        self._attr_unique_id = f"cloud:{registration_id}:video_bitrate_setting"

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self._registration_id)
        return (
            data is not None
            and data.video_bitrate in ORBWEB_3667_BITRATE_OPTIONS
        )

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data.get(self._registration_id)
        return (
            str(data.video_bitrate)
            if data is not None
            and data.video_bitrate in ORBWEB_3667_BITRATE_OPTIONS
            else None
        )

    async def async_select_option(self, option: str) -> None:
        try:
            value = int(option)
            if value not in ORBWEB_3667_BITRATE_OPTIONS:
                raise ValueError
            await self.coordinator.clients[
                self._registration_id
            ].async_set_video_bitrate(value)
        except (ValueError, HubbleOrbwebCommandError) as err:
            raise HomeAssistantError(
                f"Unable to set Hubble video bitrate to {option}"
            ) from err
        await self.coordinator.async_request_refresh()
