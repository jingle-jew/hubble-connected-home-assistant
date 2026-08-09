"""Verified writable switches for local Hubble cameras."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HubbleConfigEntry
from .coordinator import HubbleLocalCoordinator
from .entity import HubbleLocalEntity
from .local import HubbleLocalCameraData, HubbleLocalError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HubbleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switches confirmed against the MBP167 local API."""
    coordinator = entry.runtime_data.local_coordinator
    if coordinator is None:
        return
    entities: list[SwitchEntity] = []
    for spec in entry.runtime_data.local_camera_specs:
        entities.extend(
            (
                HubbleIndicatorLedSwitch(coordinator, spec.host),
                HubbleCeilingMountSwitch(coordinator, spec.host),
            )
        )
    async_add_entities(entities)


class HubbleLocalSwitch(HubbleLocalEntity, SwitchEntity):
    """Base class for acknowledged boolean camera settings."""

    _attr_entity_category = EntityCategory.CONFIG

    async def _async_set(
        self,
        enabled: bool,
        setter: Callable[[bool], Awaitable[None]],
        setting_name: str,
    ) -> None:
        try:
            await setter(enabled)
        except HubbleLocalError as err:
            raise HomeAssistantError(
                f"Unable to set Hubble {setting_name} to {enabled}"
            ) from err
        await self.coordinator.async_request_refresh()


class HubbleIndicatorLedSwitch(HubbleLocalSwitch):
    """Camera indicator LED control."""

    _attr_translation_key = "indicator_led"
    _attr_icon = "mdi:led-on"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:indicator_led"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data.get(self._host)
        return _integer_boolean(data, "blink_led")

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set(
            True,
            self.coordinator.clients[self._host].async_set_blink_led,
            "indicator LED",
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(
            False,
            self.coordinator.clients[self._host].async_set_blink_led,
            "indicator LED",
        )


class HubbleCeilingMountSwitch(HubbleLocalSwitch):
    """Vertical image flip used when the camera is ceiling-mounted."""

    _attr_translation_key = "ceiling_mount"
    _attr_icon = "mdi:image-flip-vertical"

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator, host)
        self._attr_unique_id = f"{host}:ceiling_mount"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data.get(self._host)
        return _integer_boolean(data, "flipup")

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set(
            True,
            self.coordinator.clients[self._host].async_set_flipup,
            "ceiling mount",
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(
            False,
            self.coordinator.clients[self._host].async_set_flipup,
            "ceiling mount",
        )


def _integer_boolean(data: HubbleLocalCameraData | None, field: str) -> bool | None:
    if data is None:
        return None
    value = getattr(data, field)
    return bool(value) if value in {0, 1} else None
