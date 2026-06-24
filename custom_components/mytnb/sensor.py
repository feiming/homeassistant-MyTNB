"""Sensor platform for MyTNB integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from typing import Any
import zoneinfo

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.components.sensor import (
    SensorDeviceClass,
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
_MYT = zoneinfo.ZoneInfo("Asia/Kuala_Lumpur")

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="usage",
        name="Last Interval Usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="cost",
        name="Last Interval Cost",
        native_unit_of_measurement="MYR",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:currency-usd",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MyTNB sensors from a config entry."""
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

        try:
            if not await self.api.authenticate():
                raise Exception("Authentication failed")
        except Exception as err:
            _LOGGER.error("Error authenticating: %s", err)
            raise

        # Fetch from start of current month to cover the full billing period
        now = datetime.now()
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_str = start_date.strftime("%Y-%m-%d+00:00")
        end_str = now.strftime("%Y-%m-%d+00:00")

        _LOGGER.debug(
            "Fetching data %s to %s (view=%s, granularity=%s)",
            start_str, end_str, DEFAULT_VIEW, DEFAULT_GRANULARITY,
        )

        data: dict[str, Any] = {}

        for metric in ["usage", "cost"]:
            try:
                result = await self.api.get_data(
                    metric, DEFAULT_VIEW, DEFAULT_GRANULARITY, start_str, end_str
                )
                data[metric] = result
                points = self._extract_points(result)
                _LOGGER.debug("%s: %d data points fetched", metric, len(points))
            except Exception as err:
                _LOGGER.error("Error fetching %s data: %s", metric, err)
                data[metric] = None

        try:
            data["sdpudcid"] = await self.api.get_sdpudcid()
        except Exception as err:
            _LOGGER.error("Error fetching sdpudcid: %s", err)
            data["sdpudcid"] = None

        self._import_statistics(data)

        _LOGGER.debug("Data update completed in %.2f seconds", time.time() - start_time)
        return data

    def _extract_points(self, metric_data: Any) -> list[tuple[datetime, float]]:
        """Extract sorted (datetime, value) pairs from API response, skipping nulls."""
        points: list[tuple[datetime, float]] = []
        if not isinstance(metric_data, dict) or "data" not in metric_data:
            return points
        inner = metric_data["data"]
        if not isinstance(inner, dict) or "timeseries" not in inner:
            return points
        for item in inner.get("timeseries", []):
            if not isinstance(item, dict):
                continue
            for point in item.get("data", []):
                if not isinstance(point, dict):
                    continue
                val = point.get("value")
                dt_str = point.get("datetime")
                if val is None or not dt_str:
                    continue
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=_MYT)
                    points.append((dt, float(val)))
                except (ValueError, TypeError):
                    pass
        points.sort(key=lambda x: x[0])
        return points

    def _import_statistics(self, data: dict[str, Any]) -> None:
        """Push timeseries data into HA long-term statistics for Energy dashboard."""
        targets = [
            ("usage", f"{DOMAIN}:energy_usage_{self.entry.entry_id}", "MyTNB Monthly Usage", UnitOfEnergy.KILO_WATT_HOUR),
            ("cost",  f"{DOMAIN}:energy_cost_{self.entry.entry_id}",  "MyTNB Monthly Cost",  "MYR"),
        ]
        for metric_key, statistic_id, name, unit in targets:
            if not data.get(metric_key):
                continue
            points = self._extract_points(data[metric_key])
            if not points:
                continue

            cumulative = 0.0
            statistics: list[StatisticData] = []
            for dt, val in points:
                cumulative += val
                statistics.append(StatisticData(start=dt, state=val, sum=cumulative))

            metadata = StatisticMetaData(
                has_mean=False,
                has_sum=True,
                name=name,
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_of_measurement=unit,
            )
            try:
                async_import_statistics(self.hass, metadata, statistics)
                _LOGGER.debug("Imported %d %s statistics", len(statistics), metric_key)
            except Exception as err:
                _LOGGER.warning("Failed to import %s statistics: %s", metric_key, err)


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
        """Return the most recent non-null 30-min interval value."""
        if self.coordinator.data is None:
            return None
        metric_data = self.coordinator.data.get(self.entity_description.key)
        if metric_data is None:
            return None
        points = self.coordinator._extract_points(metric_data)
        if not points:
            return None
        _, val = points[-1]
        return round(val, 6)

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
