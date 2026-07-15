"""Base entity for the Tenaga Nasional integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MyTNBCoordinator


class MyTNBEntity(CoordinatorEntity[MyTNBCoordinator]):
    """Base entity attached to the smart meter device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MyTNBCoordinator, description: EntityDescription) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        meter_id = coordinator.client.sdpudcid
        self._attr_unique_id = f"{meter_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(meter_id))},
            name="MyTNB Smart Meter",
            manufacturer="Tenaga Nasional Berhad",
            serial_number=str(meter_id),
        )
