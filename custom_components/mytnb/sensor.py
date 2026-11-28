"""Sensor platform for the Tenaga Nasional integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MyTNBConfigEntry
from .api import TIMEZONE, EnergyPoint, MeterReading, SmartMeterData
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

    value_fn: Callable[[SmartMeterData], float | date | None]
    points_fn: Callable[[SmartMeterData], list[EnergyPoint]] | None = None
    from_bill: bool = False


def _bill(attr: str) -> Callable[[SmartMeterData], Any]:
    """Read an attribute of the latest bill, if there is one."""
    return lambda data: getattr(data.bill, attr) if data.bill else None


def _meter(
    unit: str, direction: str | None, field: str
) -> Callable[[SmartMeterData], float | None]:
    """Read a field of one meter register from the latest bill."""

    def value(data: SmartMeterData) -> float | None:
        reading: MeterReading | None = data.bill.meter(unit, direction) if data.bill else None
        return getattr(reading, field) if reading else None

    return value


def _bill_money(key: str) -> MyTNBSensorDescription:
    return MyTNBSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=CURRENCY_MYR,
        state_class=SensorStateClass.MEASUREMENT,
        from_bill=True,
        value_fn=_bill(key),
    )


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
    # From the latest bill PDF.
    MyTNBSensorDescription(
        key="nem_balance",
        translation_key="nem_balance",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        from_bill=True,
        value_fn=_bill("nem_balance_kwh"),
    ),
    MyTNBSensorDescription(
        key="nem_expiry",
        translation_key="nem_expiry",
        device_class=SensorDeviceClass.DATE,
        from_bill=True,
        value_fn=_bill("nem_expiry"),
    ),
    _bill_money("previous_balance"),
    _bill_money("current_nem_charge"),
    _bill_money("rounding_adjustment"),
    _bill_money("nem_accumulated_balance"),
    _bill_money("nem_balance_carried_forward"),
    # Cumulative meter registers: usable as Energy dashboard sources.
    MyTNBSensorDescription(
        key="meter_import_reading",
        translation_key="meter_import_reading",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        from_bill=True,
        value_fn=_meter("kWh", "import", "current"),
    ),
    MyTNBSensorDescription(
        key="meter_export_reading",
        translation_key="meter_export_reading",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        from_bill=True,
        value_fn=_meter("kWh", "export", "current"),
    ),
    MyTNBSensorDescription(
        key="billed_import",
        translation_key="billed_import",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        from_bill=True,
        value_fn=_meter("kWh", "import", "usage"),
    ),
    MyTNBSensorDescription(
        key="billed_export",
        translation_key="billed_export",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        from_bill=True,
        value_fn=_meter("kWh", "export", "usage"),
    ),
    MyTNBSensorDescription(
        key="maximum_demand",
        translation_key="maximum_demand",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        from_bill=True,
        value_fn=_meter("kW", None, "usage"),
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
        MyTNBSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
        if coordinator.bill_enabled or not description.from_bill
    )


class MyTNBSensor(MyTNBEntity, SensorEntity):
    """A MyTNB smart meter sensor."""

    entity_description: MyTNBSensorDescription

    @property
    def native_value(self) -> float | date | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose how fresh the underlying (lagged) data is."""
        if self.entity_description.points_fn is None:
            return {}
        points = self.entity_description.points_fn(self.coordinator.data)
        if not points:
            return {}
        return {"data_as_of": points[-1].start.isoformat()}
