"""Shared entities for local Hubble cameras."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODEL_NAMES
from .coordinator import HubbleLocalCoordinator


class HubbleLocalEntity(CoordinatorEntity[HubbleLocalCoordinator]):
    """Base entity attached to a physical Hubble camera device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HubbleLocalCoordinator, host: str) -> None:
        super().__init__(coordinator)
        self._host = host

    @property
    def available(self) -> bool:
        data = self.coordinator.data.get(self._host)
        return data is not None and data.available

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data.get(self._host)
        spec = self.coordinator.clients[self._host].spec
        model_code = data.model_code if data is not None else None
        model = MODEL_NAMES.get(model_code or "", "Hubble LAN camera")
        if model_code:
            model = f"{model} ({model_code})"
        connections = set()
        mac = data.mac if data is not None and data.mac else spec.cloud_mac
        if mac:
            connections.add((dr.CONNECTION_NETWORK_MAC, mac))
        return DeviceInfo(
            identifiers={(DOMAIN, f"camera:{self._host}")},
            connections=connections,
            name=spec.name,
            manufacturer="Hubble Connected / Motorola",
            model=model,
            sw_version=data.firmware_version if data is not None else None,
        )
