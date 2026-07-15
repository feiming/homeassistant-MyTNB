"""Sensor platform for the Tenaga Nasional integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyTNBConfigEntry
from .api import TIMEZONE, EnergyPoint, SmartMeterData
from .const import CURRENCY_MYR
from .entity import MyTNBEntity


def _sum_current_month(points: list[EnergyPoint]) -> float | None:
    """Sum values for the current calendar month."""
    month_start = datetime.now(TIMEZONE).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    monthly = [p.value for p in points if p.start >= month_start]
    return round(sum(monthly), 6) if monthly else None


def _latest(points: list[EnergyPoint]) -> float | None:
    """Value of the most recent interval."""
    return round(points[-1].value, 6) if points else None


@dataclass(frozen=True, kw_only=True)
class MyTNBSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[SmartMeterData], float | None]
    points_fn: Callable[[SmartMeterData], list[EnergyPoint]]


SENSOR_DESCRIPTIONS: tuple[MyTNBSensorDescription, ...] = (
    MyTNBSensorDescription(
        key="usage_latest",
        translation_key="usage_latest",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _latest(data.usage),
        points_fn=lambda data: data.usage,
    ),
    MyTNBSensorDescription(
        key="cost_latest",
        translation_key="cost_latest",
        native_unit_of_measurement=CURRENCY_MYR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _latest(data.cost),
        points_fn=lambda data: data.cost,
    ),
    MyTNBSensorDescription(
        key="usage_monthly",
        translation_key="usage_monthly",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _sum_current_month(data.usage),
        points_fn=lambda data: data.usage,
    ),
    MyTNBSensorDescription(
        key="cost_monthly",
        translation_key="cost_monthly",
        native_unit_of_measurement=CURRENCY_MYR,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _sum_current_month(data.cost),
        points_fn=lambda data: data.cost,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyTNBConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MyTNB sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        MyTNBSensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class MyTNBSensor(MyTNBEntity, SensorEntity):
    """A MyTNB smart meter sensor."""

    entity_description: MyTNBSensorDescription

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose how fresh the underlying (lagged) data is."""
        points = self.entity_description.points_fn(self.coordinator.data)
        if not points:
            return {}
        return {"data_as_of": points[-1].start.isoformat()}
