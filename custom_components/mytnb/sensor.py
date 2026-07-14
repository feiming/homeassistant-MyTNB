"""Sensor platform for MyTNB integration."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any
import zoneinfo

from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN, get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_GRANULARITY,
    ATTR_METRIC,
    ATTR_SDPUDCID,
    ATTR_VIEW,
    DEFAULT_GRANULARITY,
    DEFAULT_VIEW,
    DOMAIN,
)
from .coordinator import MyTNBCoordinator

_LOGGER = logging.getLogger(__name__)
_MYT = zoneinfo.ZoneInfo("Asia/Kuala_Lumpur")

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="usage_latest",
        name="Latest Usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="cost_latest",
        name="Latest Cost",
        native_unit_of_measurement="MYR",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:currency-usd",
    ),
    SensorEntityDescription(
        key="usage_monthly",
        name="Monthly Usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="cost_monthly",
        name="Monthly Cost",
        native_unit_of_measurement="MYR",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:currency-usd",
    ),
    # History sensors deliberately have no state_class: their data arrives
    # ~2 days late, and a state_class would make the recorder compile
    # statistics from the live state at the current time, mixing "now"
    # rows into the backdated statistics imported in _import_statistics.
    SensorEntityDescription(
        key="usage_history",
        name="Usage History",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        icon="mdi:lightning-bolt",
    ),
    SensorEntityDescription(
        key="cost_history",
        name="Cost History",
        native_unit_of_measurement="MYR",
        icon="mdi:currency-usd",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MyTNB sensors from a config entry."""
    coordinator: MyTNBCoordinator = entry.runtime_data

    async_add_entities(
        MyTNBSensor(coordinator, entry, description) for description in SENSOR_DESCRIPTIONS
    )


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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"MyTNB ({entry.data[CONF_USERNAME]})",
            manufacturer="Tenaga Nasional Berhad",
        )

    def _metric_and_agg(self) -> tuple[str, str]:
        """Parse key into (metric, aggregation): e.g. 'usage_latest' -> ('usage', 'latest')."""
        metric, agg = self.entity_description.key.rsplit("_", 1)
        return metric, agg

    async def async_added_to_hass(self) -> None:
        """Import statistics for the data fetched before the entity existed."""
        await super().async_added_to_hass()
        if self.entity_description.key in ("usage_history", "cost_history"):
            await self._async_import_statistics()

    @callback
    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        if self.entity_description.key in ("usage_history", "cost_history") and self.entity_id:
            self.hass.async_create_task(self._async_import_statistics())

    async def _async_import_statistics(self) -> None:
        if not self.coordinator.data:
            return
        metric, _ = self._metric_and_agg()
        points = self.coordinator._extract_points(self.coordinator.data.get(metric))
        if not points:
            return

        # Long-term statistics require hour-aligned timestamps; bucket in case
        # any points aren't already hour-aligned.
        hourly: dict[datetime, float] = {}
        for dt, val in points:
            hour = dt.replace(minute=0, second=0, microsecond=0)
            hourly[hour] = hourly.get(hour, 0.0) + val

        # Continue from the last imported statistic so the running sum stays
        # monotonic even though each fetch only covers a sliding 30-day window.
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, self.entity_id, True, {"sum"}
        )
        cumulative = 0.0
        last_start: datetime | None = None
        if rows := last_stats.get(self.entity_id):
            cumulative = rows[0]["sum"] or 0.0
            last_start = datetime.fromtimestamp(rows[0]["start"], tz=_MYT)

        stats: list[StatisticData] = []
        for hour in sorted(hourly):
            if last_start is not None and hour <= last_start:
                continue
            val = hourly[hour]
            cumulative += val
            stats.append(StatisticData(start=hour, state=val, sum=cumulative))

        if not stats:
            return

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=self.entity_description.name,
            source=RECORDER_DOMAIN,
            statistic_id=self.entity_id,
            unit_of_measurement=self.entity_description.native_unit_of_measurement,
        )
        try:
            async_import_statistics(self.hass, metadata, stats)
            _LOGGER.debug("Imported %d %s intervals for %s", len(stats), metric, self.entity_id)
        except Exception as err:
            _LOGGER.warning("Failed to import %s statistics: %s", metric, err)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        metric, agg = self._metric_and_agg()
        metric_data = self.coordinator.data.get(metric)
        if metric_data is None:
            return None
        if agg == "latest":
            points = self.coordinator._extract_points(metric_data)
            if not points:
                return None
            _, val = points[-1]
            return round(val, 6)
        elif agg == "history":
            points = self.coordinator._extract_points(metric_data)
            if not points:
                return None
            _, val = points[-1]
            return round(val, 6)
        else:  # monthly
            points = self.coordinator._monthly_points(metric_data)
            if not points:
                return None
            return round(sum(v for _, v in points), 6)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {}
        if self.coordinator.data is None:
            return attrs
        metric, agg = self._metric_and_agg()
        if sdpudcid := self.coordinator.data.get("sdpudcid"):
            attrs[ATTR_SDPUDCID] = sdpudcid
        attrs[ATTR_METRIC] = metric
        attrs[ATTR_VIEW] = DEFAULT_VIEW
        attrs[ATTR_GRANULARITY] = DEFAULT_GRANULARITY if metric == "usage" else "default"
        metric_data = self.coordinator.data.get(metric)
        if metric_data is not None:
            points = self.coordinator._extract_points(metric_data)
            if points:
                latest_dt, _ = points[-1]
                attrs["data_as_of"] = latest_dt.isoformat()
        return attrs
