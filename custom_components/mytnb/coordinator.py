"""Data update coordinator for the Tenaga Nasional integration."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

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
    BillData,
    EnergyPoint,
    MyTNBAuthError,
    MyTNBClient,
    MyTNBConnectionError,
    MyTNBCredentialsError,
    MyTNBError,
    MyTNBNoSmartMeterError,
    SmartMeterData,
    maintenance_window_end,
)
from .const import (
    CONF_BILL_ENABLED,
    CONF_BILL_INTERVAL_DAYS,
    CONF_BILL_TIME,
    CONF_UPDATE_INTERVAL_HOURS,
    CURRENCY_MYR,
    DEFAULT_BILL_ENABLED,
    DEFAULT_BILL_INTERVAL_DAYS,
    DEFAULT_BILL_TIME,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    FETCH_WINDOW,
)

_LOGGER = logging.getLogger(__name__)


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

        options = entry.options
        self.bill_enabled: bool = options.get(CONF_BILL_ENABLED, DEFAULT_BILL_ENABLED)
        self.bill_interval = timedelta(
            days=int(options.get(CONF_BILL_INTERVAL_DAYS, DEFAULT_BILL_INTERVAL_DAYS))
        )
        self.bill_time: time = time.fromisoformat(options.get(CONF_BILL_TIME, DEFAULT_BILL_TIME))
        self.bill: BillData | None = None
        self.bill_fetched_at: datetime | None = None

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
            # TNB rejects sessions for a while around midnight each night, so
            # even a freshly logged-in session keeps being redirected to the
            # login page (issue #2). The password is fine, so retry on the next
            # poll instead of stopping updates until the user reauthenticates.
            raise UpdateFailed(f"MyTNB session rejected, will retry: {err}") from err
        except MyTNBNoSmartMeterError as err:
            raise UpdateFailed(f"No smart meter found on this MyTNB account: {err}") from err
        except MyTNBConnectionError as err:
            raise UpdateFailed(f"Could not fetch data from MyTNB: {err}") from err

        await self._async_insert_statistics("usage", data.usage, UnitOfEnergy.KILO_WATT_HOUR)
        await self._async_insert_statistics("cost", data.cost, CURRENCY_MYR)
        if self.bill_due(now):
            await self._async_fetch_bill(now)
        data.bill = self.bill
        return data

    def next_bill_check(self) -> datetime | None:
        """When the bill is next due: the configured time, every N days.

        None means as soon as possible (nothing fetched yet since startup).
        """
        if self.bill_fetched_at is None:
            return None
        day = self.bill_fetched_at.astimezone(TIMEZONE).date() + self.bill_interval
        return datetime.combine(day, self.bill_time, tzinfo=TIMEZONE)

    def bill_due(self, now: datetime) -> bool:
        """Whether a bill fetch should run now."""
        if not self.bill_enabled or maintenance_window_end(now) is not None:
            return False
        next_check = self.next_bill_check()
        return next_check is None or now >= next_check

    async def async_refresh_bill_if_due(self, _fired_at: datetime | None = None) -> None:
        """Scheduled check at the configured time; pushes a new bill to sensors.

        Runs between smart meter polls, so it must not reset their timer:
        listeners are notified directly instead of via async_set_updated_data.
        """
        now = datetime.now(TIMEZONE)
        if self.data is None or not self.bill_due(now):
            return
        if await self._async_fetch_bill(now):
            self.data.bill = self.bill
            self.async_update_listeners()

    async def _async_fetch_bill(self, now: datetime) -> bool:
        """Fetch the latest bill; on failure keep the previous one.

        The bill only changes monthly, so a failed fetch must not fail the
        whole update and take the smart meter sensors down with it. The
        fetch time is only advanced on success, so the next poll retries.
        """
        try:
            self.bill = await self.client.async_get_bill()
        except MyTNBError as err:
            _LOGGER.warning("Could not fetch the latest bill, keeping the previous one: %s", err)
            return False
        self.bill_fetched_at = now
        _LOGGER.debug("Fetched latest bill; next check %s", self.next_bill_check())
        return True

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
