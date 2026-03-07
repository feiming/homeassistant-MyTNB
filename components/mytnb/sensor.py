"""Sensor platform for MyTNB integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import MyTNBAPI
from .const import (
    ATTR_GRANULARITY,
    ATTR_METRIC,
    ATTR_SDPUDCID,
    ATTR_VIEW,
    DEFAULT_GRANULARITY,
    DEFAULT_VIEW,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="usage",
        name="Energy Usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="cost",
        name="Energy Cost",
        native_unit_of_measurement="MYR",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:currency-usd",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MyTNB sensors from a config entry."""
    # Get API from runtime_data (set in __init__.py)
    api: MyTNBAPI = entry.runtime_data

    coordinator = MyTNBCoordinator(hass, api, entry)

    await coordinator.async_config_entry_first_refresh()

    async_add_entities(
        MyTNBSensor(coordinator, entry, description) for description in SENSOR_DESCRIPTIONS
    )


class MyTNBCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """MyTNB data update coordinator."""

    def __init__(self, hass: HomeAssistant, api: MyTNBAPI, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=30),
        )
        self.api = api
        self.entry = entry

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from MyTNB API."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        start_str = start_date.strftime("%Y-%m-%d+00:00")
        end_str = end_date.strftime("%Y-%m-%d+00:00")

        data: dict[str, Any] = {}

        for metric in ["usage", "cost"]:
            try:
                result = await self.hass.async_add_executor_job(
                    self.api.get_data,
                    metric,
                    DEFAULT_VIEW,
                    DEFAULT_GRANULARITY,
                    start_str,
                    end_str,
                )
                data[metric] = result
            except Exception as err:
                _LOGGER.error("Error fetching %s data: %s", metric, err)
                data[metric] = None

        # Get sdpudcid for attributes
        try:
            sdpudcid = await self.hass.async_add_executor_job(self.api.get_sdpudcid)
            data["sdpudcid"] = sdpudcid
        except Exception as err:
            _LOGGER.error("Error fetching sdpudcid: %s", err)
            data["sdpudcid"] = None

        return data


class MyTNBSensor(CoordinatorEntity[MyTNBCoordinator], SensorEntity):
    """Representation of a MyTNB sensor."""

    def __init__(
        self,
        coordinator: MyTNBCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None

        metric_data = self.coordinator.data.get(self.entity_description.key)
        if metric_data is None:
            return None

        # Extract the latest value from the timeseries data
        # The API returns data in format: {"data": [{"timestamp": "...", "value": ...}, ...]}
        # or {"data": {"timestamp": value, ...}} (dict format)
        if isinstance(metric_data, dict) and "data" in metric_data:
            data_points = metric_data["data"]
            
            # Handle list format
            if isinstance(data_points, list) and len(data_points) > 0:
                # Get the most recent data point
                latest = data_points[-1]
                if isinstance(latest, dict) and "value" in latest:
                    return float(latest["value"])
            
            # Handle dict format (keys are timestamps, values are numbers or dicts)
            elif isinstance(data_points, dict):
                values = []
                for key, value in data_points.items():
                    if isinstance(value, (int, float)):
                        values.append((key, float(value)))
                    elif isinstance(value, dict):
                        # Try to find numeric value in nested dict
                        if "value" in value:
                            val = value["value"]
                            if isinstance(val, (int, float)):
                                values.append((key, float(val)))
                        elif "usage" in value and self.entity_description.key == "usage":
                            val = value["usage"]
                            if isinstance(val, (int, float)):
                                values.append((key, float(val)))
                        elif "cost" in value and self.entity_description.key == "cost":
                            val = value["cost"]
                            if isinstance(val, (int, float)):
                                values.append((key, float(val)))
                        # If dict has numeric keys, try those
                        elif len(value) > 0:
                            for k, v in value.items():
                                if isinstance(v, (int, float)):
                                    values.append((key, float(v)))
                                    break
                
                if values:
                    # Sort by key (timestamp) to get latest
                    sorted_values = sorted(values, key=lambda x: str(x[0]))
                    if sorted_values:
                        return sorted_values[-1][1]

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if self.coordinator.data:
            if sdpudcid := self.coordinator.data.get("sdpudcid"):
                attrs[ATTR_SDPUDCID] = sdpudcid
            attrs[ATTR_METRIC] = self.entity_description.key
            attrs[ATTR_VIEW] = DEFAULT_VIEW
            attrs[ATTR_GRANULARITY] = DEFAULT_GRANULARITY
        return attrs
