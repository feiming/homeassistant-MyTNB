"""Sensor platform for MyTNB integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
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
        start_time = time.time()
        _LOGGER.debug("Starting data update")
        
        # Authenticate (login and access smartmeter) before fetching data
        auth_start = time.time()
        try:
            _LOGGER.debug("Authenticating with MyTNB API...")
            if not await self.api.authenticate():
                raise Exception("Authentication failed")
            auth_duration = time.time() - auth_start
            _LOGGER.debug("Authentication successful (took %.2f seconds)", auth_duration)
        except Exception as err:
            auth_duration = time.time() - auth_start
            _LOGGER.error("Error authenticating (took %.2f seconds): %s", auth_duration, err)
            raise

        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        start_str = start_date.strftime("%Y-%m-%d+00:00")
        end_str = end_date.strftime("%Y-%m-%d+00:00")
        
        _LOGGER.debug(
            "Fetching data for date range: %s to %s (view=%s, granularity=%s)",
            start_str,
            end_str,
            DEFAULT_VIEW,
            DEFAULT_GRANULARITY,
        )

        data: dict[str, Any] = {}
        metric_durations: dict[str, float] = {}

        for metric in ["usage", "cost"]:
            metric_start = time.time()
            try:
                _LOGGER.debug("Fetching %s data...", metric)
                result = await self.api.get_data(
                    metric,
                    DEFAULT_VIEW,
                    DEFAULT_GRANULARITY,
                    start_str,
                    end_str,
                )
                metric_duration = time.time() - metric_start
                metric_durations[metric] = metric_duration
                
                # Debug: Analyze result structure
                if isinstance(result, dict):
                    if "data" in result:
                        inner_data = result["data"]
                        if isinstance(inner_data, dict) and "timeseries" in inner_data:
                            timeseries = inner_data["timeseries"]
                            if isinstance(timeseries, list):
                                total_points = sum(
                                    len(item.get("data", []))
                                    for item in timeseries
                                    if isinstance(item, dict) and "data" in item
                                )
                                _LOGGER.debug(
                                    "%s data retrieved: %d timeseries entries, %d total data points (took %.2f seconds)",
                                    metric.capitalize(),
                                    len(timeseries),
                                    total_points,
                                    metric_duration,
                                )
                                
                                # Log sample data point if available
                                for item in timeseries:
                                    if isinstance(item, dict) and "data" in item:
                                        item_data = item["data"]
                                        if isinstance(item_data, list) and len(item_data) > 0:
                                            sample = item_data[0]
                                            _LOGGER.debug(
                                                "%s sample data point: %s",
                                                metric.capitalize(),
                                                sample,
                                            )
                                            break
                            else:
                                _LOGGER.debug(
                                    "%s data structure: timeseries is not a list (type: %s)",
                                    metric.capitalize(),
                                    type(timeseries).__name__,
                                )
                        else:
                            _LOGGER.debug(
                                "%s data structure: unexpected format, keys: %s",
                                metric.capitalize(),
                                list(inner_data.keys()) if isinstance(inner_data, dict) else type(inner_data).__name__,
                            )
                    else:
                        _LOGGER.debug(
                            "%s data: missing 'data' key, keys: %s",
                            metric.capitalize(),
                            list(result.keys()),
                        )
                else:
                    _LOGGER.debug(
                        "%s data: unexpected type %s",
                        metric.capitalize(),
                        type(result).__name__,
                    )
                
                data[metric] = result
            except Exception as err:
                metric_duration = time.time() - metric_start
                metric_durations[metric] = metric_duration
                _LOGGER.error(
                    "Error fetching %s data (took %.2f seconds): %s",
                    metric,
                    metric_duration,
                    err,
                )
                data[metric] = None

        # Get sdpudcid for attributes
        sdpudcid_start = time.time()
        try:
            _LOGGER.debug("Fetching sdpudcid...")
            sdpudcid = await self.api.get_sdpudcid()
            sdpudcid_duration = time.time() - sdpudcid_start
            _LOGGER.debug("SDPUDCID retrieved: %s (took %.2f seconds)", sdpudcid, sdpudcid_duration)
            data["sdpudcid"] = sdpudcid
        except Exception as err:
            sdpudcid_duration = time.time() - sdpudcid_start
            _LOGGER.error("Error fetching sdpudcid (took %.2f seconds): %s", sdpudcid_duration, err)
            data["sdpudcid"] = None

        total_duration = time.time() - start_time
        usage_duration = metric_durations.get("usage", 0.0)
        cost_duration = metric_durations.get("cost", 0.0)
        _LOGGER.debug(
            "Data update completed in %.2f seconds (auth: %.2fs, usage: %.2fs, cost: %.2fs, sdpudcid: %.2fs)",
            total_duration,
            auth_duration,
            usage_duration,
            cost_duration,
            sdpudcid_duration if "sdpudcid" in data else 0.0,
        )
        
        # Summary of retrieved data
        _LOGGER.debug(
            "Update summary: usage=%s, cost=%s, sdpudcid=%s",
            "present" if data.get("usage") else "None",
            "present" if data.get("cost") else "None",
            data.get("sdpudcid", "None"),
        )

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
        # The API returns data in format: {"data": {"timeseries": [{"data": [{"datetime": "...", "value": ...}], ...}, ...], ...}}
        
        # Handle nested structure: {"data": {"timeseries": [...]}}
        if isinstance(metric_data, dict) and "data" in metric_data:
            inner_data = metric_data["data"]
            if isinstance(inner_data, dict) and "timeseries" in inner_data:
                timeseries = inner_data["timeseries"]
                if isinstance(timeseries, list) and len(timeseries) > 0:
                    # Flatten timeseries into a list of data points
                    data_points = []
                    for item in timeseries:
                        if isinstance(item, dict) and "data" in item:
                            item_data = item["data"]
                            if isinstance(item_data, list) and len(item_data) > 0:
                                data_points.extend(item_data)
                    
                    if data_points:
                        # Sort by datetime to get the most recent data point
                        def get_datetime(point):
                            if isinstance(point, dict):
                                dt = point.get("datetime") or ""
                                # Handle datetime format "2026-03-02 00:00" for proper sorting
                                return dt
                            return ""
                        
                        sorted_points = sorted(data_points, key=get_datetime)
                        if sorted_points:
                            latest = sorted_points[-1]
                            if isinstance(latest, dict) and "value" in latest:
                                val = latest["value"]
                                if val is not None:
                                    try:
                                        return float(val)
                                    except (ValueError, TypeError):
                                        _LOGGER.warning("Could not convert value to float: %s", val)
                                        return None

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
