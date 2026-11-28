"""Data update coordinator for the Tenaga Nasional integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    TIMEZONE,
    EnergyPoint,
    MyTNBAuthError,
    MyTNBClient,
    MyTNBConnectionError,
    MyTNBCredentialsError,
    MyTNBNoSmartMeterError,
    SmartMeterData,
    maintenance_window_end,
)
from .const import (
    CONF_UPDATE_INTERVAL_HOURS,
    CURRENCY_MYR,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    FETCH_WINDOW,
    RETRY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Latest statistics rows to read when resuming an import: a full day of
# hourly rows plus the row before that day, with some margin.
_LAST_ROWS = 30


def _minutes(delta: timedelta) -> str:
    return f"{round(delta.total_seconds() / 60)} min"


class MyTNBCoordinator(DataUpdateCoordinator[SmartMeterData]):
    """Fetches smart meter data and maintains long-term statistics."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: MyTNBClient) -> None:
        """Initialize the coordinator."""
        hours = entry.options.get(CONF_UPDATE_INTERVAL_HOURS, DEFAULT_UPDATE_INTERVAL_HOURS)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(hours=hours),
        )
        self.client = client
        self._poll_interval = timedelta(hours=hours)
        self._consecutive_failures = 0

    def _schedule_retry(self) -> timedelta:
        """Poll again soon after a transient failure, backing off each time.

        TNB rejects valid sessions in stretches lasting minutes (issue #6).
        Retries within one poll are seconds apart, so they land in the same
        stretch. Waiting the full poll interval (6 hours by default) left a
        running integration stale for hours, which is why a reload, which
        simply tried again later, seemed to fix it. Retry after 10 minutes,
        doubling each time, capped at the poll interval.
        """
        self._consecutive_failures += 1
        retry_in = min(
            RETRY_INTERVAL * 2 ** (self._consecutive_failures - 1), self._poll_interval
        )
        self.update_interval = retry_in
        return retry_in

    async def _async_update_data(self) -> SmartMeterData:
        """Fetch data and import it into long-term statistics."""
        now = datetime.now(TIMEZONE)

        # TNB rejects sessions around midnight each night (issue #2). Skip a
        # poll that lands in that window and run it once the window is over.
        # With nothing fetched yet (startup) there is no data to keep showing,
        # so try anyway and let the normal retry handling deal with a failure.
        window_end = maintenance_window_end(now)
        if window_end is not None and self.data is not None:
            self.update_interval = window_end - now
            _LOGGER.debug("In TNB's nightly maintenance window; deferring poll to %s", window_end)
            return self.data
        self.update_interval = self._poll_interval

        try:
            data = await self.client.async_get_data(now - FETCH_WINDOW, now + timedelta(days=1))
        except MyTNBCredentialsError as err:
            raise ConfigEntryAuthFailed(f"Authentication with MyTNB failed: {err}") from err
        except MyTNBAuthError as err:
            # TNB rejects valid sessions for a while around midnight each night
            # (issue #2) and in stretches at other times (issue #6). The
            # password is fine, so retry soon instead of stopping updates
            # until the user reauthenticates.
            retry_in = self._schedule_retry()
            raise UpdateFailed(
                f"MyTNB session rejected, will retry in {_minutes(retry_in)}: {err}"
            ) from err
        except MyTNBNoSmartMeterError as err:
            raise UpdateFailed(f"No smart meter found on this MyTNB account: {err}") from err
        except MyTNBConnectionError as err:
            retry_in = self._schedule_retry()
            raise UpdateFailed(
                f"Could not fetch data from MyTNB, will retry in {_minutes(retry_in)}: {err}"
            ) from err
        self._consecutive_failures = 0

        await self._async_insert_statistics("usage", data.usage, UnitOfEnergy.KILO_WATT_HOUR)
        await self._async_insert_statistics("cost", data.cost, CURRENCY_MYR)

        # One metric can fail while the other is fetched (issue #6: TNB
        # bouncing just the cost request). Keep showing that metric's last
        # good data rather than blanking its sensors until it recovers.
        if self.data is not None:
            if not data.usage:
                data.usage = self.data.usage
            if not data.cost:
                data.cost = self.data.cost
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

        # Continue the running sum from what's already imported, so it stays
        # monotonic even though each fetch only covers a sliding window. The
        # last imported day is redone hour by hour rather than continued after
        # its last row: cost used to be imported as one row per day at 00:00
        # holding the whole day, and adding that day's hours after it would
        # count the day twice. Redoing it also completes a day that was only
        # partly published when it was imported. Rows are overwritten by start.
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, _LAST_ROWS, statistic_id, True, {"sum"}
        )
        rows = last_stats.get(statistic_id) or []  # newest first
        cumulative = 0.0
        resume_after: datetime | None = None  # import hours after this one
        if rows:
            starts = [datetime.fromtimestamp(row["start"], tz=TIMEZONE) for row in rows]
            day_start = starts[0].replace(hour=0)
            before = next((i for i, start in enumerate(starts) if start < day_start), None)
            fetched_day = any(day_start <= hour < day_start + timedelta(days=1) for hour in hourly)
            if fetched_day and (before is not None or len(rows) < _LAST_ROWS):
                # Redo the day from the sum just before it (0 if nothing is).
                # The row before can be a whole day earlier (daily rows), so
                # only its sum is used: the redo starts at the day's 00:00.
                if before is not None:
                    cumulative = rows[before]["sum"] or 0.0
                resume_after = day_start - timedelta(hours=1)
            else:
                # The fetch doesn't cover that day (or its start wasn't found):
                # redoing it would drop its amount from the sum, so continue
                # after the last row instead.
                cumulative = rows[0]["sum"] or 0.0
                resume_after = starts[0]

        stats: list[StatisticData] = []
        for hour in sorted(hourly):
            if resume_after is not None and hour <= resume_after:
                continue
            cumulative += hourly[hour]
            stats.append(StatisticData(start=hour, state=hourly[hour], sum=cumulative))

        if not stats:
            return

        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"MyTNB {metric.capitalize()}",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class=EnergyConverter.UNIT_CLASS
            if unit == UnitOfEnergy.KILO_WATT_HOUR
            else None,
            unit_of_measurement=unit,
        )
        async_add_external_statistics(self.hass, metadata, stats)
        _LOGGER.debug("Imported %d hourly %s statistics", len(stats), metric)
