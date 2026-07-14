"""Data update coordinator for the MyTNB integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from typing import Any
import zoneinfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MyTNBAPI, MyTNBAuthError
from .const import DEFAULT_GRANULARITY, DEFAULT_VIEW, DOMAIN

_LOGGER = logging.getLogger(__name__)
_MYT = zoneinfo.ZoneInfo("Asia/Kuala_Lumpur")


class MyTNBCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """MyTNB data update coordinator."""

    def __init__(self, hass: HomeAssistant, api: MyTNBAPI, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # TNB publishes data roughly two days late, and each fetch covers
            # a sliding 30-day window, so once a day is plenty.
            update_interval=timedelta(hours=24),
        )
        self.api = api
        self.entry = entry

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from MyTNB API, re-authenticating if the session expired."""
        start_time = time.time()

        try:
            data = await self._fetch_all()
        except MyTNBAuthError as err:
            _LOGGER.info("MyTNB session invalid (%s); logging in again", err)
            if not await self.api.authenticate():
                raise UpdateFailed("Re-authentication with MyTNB failed") from err
            try:
                data = await self._fetch_all()
            except MyTNBAuthError as err2:
                raise UpdateFailed(f"Still unauthorized after re-login: {err2}") from err2

        _LOGGER.debug("Data update completed in %.2f seconds", time.time() - start_time)
        return data

    async def _fetch_all(self) -> dict[str, Any]:
        """Fetch usage and cost data using the current session."""
        now = datetime.now(_MYT)
        start_date = now - timedelta(days=30)
        end_date = now + timedelta(days=1)
        start_str = start_date.strftime("%Y-%m-%d+00:00")
        end_str = end_date.strftime("%Y-%m-%d+00:00")

        _LOGGER.debug(
            "Fetching data %s to %s (view=%s, granularity=%s)",
            start_str, end_str, DEFAULT_VIEW, DEFAULT_GRANULARITY,
        )

        data: dict[str, Any] = {}

        for metric in ["usage", "cost"]:
            # Cost API does not support MIN30 granularity
            granularity = DEFAULT_GRANULARITY if metric == "usage" else None
            try:
                result = await self.api.get_data(
                    metric, DEFAULT_VIEW, granularity, start_str, end_str
                )
                data[metric] = result
                points = self._extract_points(result)
                _LOGGER.debug("%s: %d data points fetched", metric, len(points))
            except MyTNBAuthError:
                raise
            except Exception as err:
                _LOGGER.error("Error fetching %s data: %s", metric, err)
                data[metric] = None

        if data["usage"] is None and data["cost"] is None:
            raise UpdateFailed("Failed to fetch both usage and cost data")

        data["sdpudcid"] = await self.api.get_sdpudcid()
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

    def _monthly_points(self, metric_data: Any) -> list[tuple[datetime, float]]:
        """Extract points for the current calendar month only."""
        now = datetime.now(_MYT)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return [(dt, val) for dt, val in self._extract_points(metric_data) if dt >= month_start]
