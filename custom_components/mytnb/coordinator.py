"""Data update coordinator for the Tenaga Nasional integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    TIMEZONE,
    EnergyPoint,
    MyTNBAuthError,
    MyTNBClient,
    MyTNBConnectionError,
    SmartMeterData,
)
from .const import CURRENCY_MYR, DOMAIN, FETCH_WINDOW, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class MyTNBCoordinator(DataUpdateCoordinator[SmartMeterData]):
    """Fetches smart meter data and maintains long-term statistics."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: MyTNBClient) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> SmartMeterData:
        """Fetch data and import it into long-term statistics."""
        now = datetime.now(TIMEZONE)
        try:
            data = await self.client.async_get_data(now - FETCH_WINDOW, now + timedelta(days=1))
        except MyTNBAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication with MyTNB failed: {err}") from err
        except MyTNBConnectionError as err:
            raise UpdateFailed(f"Could not fetch data from MyTNB: {err}") from err

        await self._async_insert_statistics("usage", data.usage, UnitOfEnergy.KILO_WATT_HOUR)
        await self._async_insert_statistics("cost", data.cost, CURRENCY_MYR)
        return data

    async def _async_insert_statistics(
        self, metric: str, points: list[EnergyPoint], unit: str
    ) -> None:
        """Import points as external long-term statistics.

        TNB publishes data roughly two days late, so the recorder's normal
        state-based statistics would attribute values to the wrong time.
        External statistics let us backdate each interval to when the
        energy was actually used, which also feeds the Energy dashboard.
        """
        if not points:
            return

        statistic_id = f"{DOMAIN}:{metric}_{self.client.sdpudcid}"

        # Statistics are hourly; sum sub-hourly points into their hour.
        hourly: dict[datetime, float] = {}
        for point in points:
            hour = point.start.replace(minute=0, second=0, microsecond=0)
            hourly[hour] = hourly.get(hour, 0.0) + point.value

        # Continue from the last imported statistic so the running sum stays
        # monotonic even though each fetch only covers a sliding window.
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        cumulative = 0.0
        last_start: datetime | None = None
        if rows := last_stats.get(statistic_id):
            cumulative = rows[0]["sum"] or 0.0
            last_start = datetime.fromtimestamp(rows[0]["start"], tz=TIMEZONE)

        stats: list[StatisticData] = []
        for hour in sorted(hourly):
            if last_start is not None and hour <= last_start:
                continue
            cumulative += hourly[hour]
            stats.append(StatisticData(start=hour, state=hourly[hour], sum=cumulative))

        if not stats:
            return

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"MyTNB {metric.capitalize()}",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=unit,
        )
        async_add_external_statistics(self.hass, metadata, stats)
        _LOGGER.debug("Imported %d hourly %s statistics", len(stats), metric)
