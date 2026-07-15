"""API client for MyTNB smart meter data.

This module is Home Assistant-free so it can be exercised standalone.

The TNB backend only answers requests made in the same order a browser
would issue them, and has two quirks this client hides from callers:

* The timeseries endpoint must be "armed" by loading the matching
  commodity page immediately before each request; otherwise it replies
  HTTP 200 with ``{"redirect": true, "redirectTo": "/login"}`` even when
  the session is valid.
* A freshly created session takes a moment to propagate on TNB's side,
  so requests made right after login can transiently get the same
  login-redirect response. Such responses are retried before being
  treated as an expired session.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")

_LOGIN_URL = "https://www.mytnb.com.my/api/sitecore/Account/Login"
_SSO_URL = "https://myaccount.mytnb.com.my/SSO/SSOHandler"
_ACCOUNT_BASE = "https://myaccount.mytnb.com.my"
_SMARTLIVING_BASE = "https://smartliving.myaccount.mytnb.com.my"
_DASHBOARD_URL = f"{_SMARTLIVING_BASE}/dashboard"
_TIMESERIES_URL = f"{_SMARTLIVING_BASE}/my_energy_request/timeseries"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# The smartliving WAF rejects requests without a browser-like header set.
_HTML_HEADERS = {
    "Host": "smartliving.myaccount.mytnb.com.my",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "DNT": "1",
    "Connection": "keep-alive",
    "User-Agent": _USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8"
    ),
}

_SMARTMETER_PATH_RE = re.compile(r"/AccountManagement/SmartMeter/Index/TRIL\?caNo=[^\"\s]+")
_FORM_FIELD_RE = re.compile(r'name="([^"]+)" value="([^"]*)"')
_SDPUDCID_RE = re.compile(r'"sdpudcid":"(\d+)"')
_POINT_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
_REQUEST_DATE_FORMAT = "%Y-%m-%d+00:00"

_SESSION_LAG_RETRIES = 3
_SESSION_LAG_DELAY = 2.0


class MyTNBError(Exception):
    """Base error for the MyTNB client."""


class MyTNBAuthError(MyTNBError):
    """Credentials rejected, or the session is missing/expired."""


class MyTNBConnectionError(MyTNBError):
    """Transient network or server-side failure."""


@dataclass(frozen=True, slots=True)
class EnergyPoint:
    """A single metered interval."""

    start: datetime
    value: float


@dataclass(slots=True)
class SmartMeterData:
    """Usage and cost timeseries for one meter."""

    usage: list[EnergyPoint]
    cost: list[EnergyPoint]


def extract_smartmeter_path(smartmeter_url: str) -> str:
    """Extract the smartmeter path from the URL pasted by the user."""
    match = _SMARTMETER_PATH_RE.search(smartmeter_url)
    if not match:
        raise MyTNBError("Could not find a SmartMeter/Index/TRIL?caNo=... path in the URL")
    return match.group(0)


class MyTNBClient:
    """Client for the MyTNB smart meter portal.

    The aiohttp session is owned by the caller; the client never closes it.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        smartmeter_url: str,
    ) -> None:
        """Initialize the client. Raises MyTNBError on a malformed URL."""
        self._session = session
        self._username = username
        self._password = password
        self._smartmeter_path = extract_smartmeter_path(smartmeter_url)
        self._auth_lock = asyncio.Lock()
        self._authenticated = False
        self.sdpudcid: str | None = None

    async def async_authenticate(self) -> str:
        """Run the full login flow; returns the meter's sdpudcid."""
        async with self._auth_lock:
            await self._login()
        assert self.sdpudcid is not None
        return self.sdpudcid

    async def async_get_data(self, start: datetime, end: datetime) -> SmartMeterData:
        """Fetch usage and cost, logging in (or re-logging-in) as needed."""
        await self._ensure_authenticated()
        try:
            return await self._fetch_all(start, end)
        except MyTNBAuthError as err:
            _LOGGER.debug("Session rejected (%s); logging in again", err)
            self._authenticated = False
            await self._ensure_authenticated()
            return await self._fetch_all(start, end)

    async def _ensure_authenticated(self) -> None:
        if self._authenticated:
            return
        async with self._auth_lock:
            if not self._authenticated:
                await self._login()

    async def _login(self) -> None:
        """Login → SSO → smartmeter page → dashboard (sdpudcid)."""
        self._authenticated = False
        self.sdpudcid = None
        self._session.cookie_jar.clear()

        try:
            async with self._session.post(
                _LOGIN_URL, data={"Email": self._username, "Password": self._password}
            ) as response:
                response.raise_for_status()
                form_fields = dict(_FORM_FIELD_RE.findall(await response.text()))
            if not form_fields:
                raise MyTNBAuthError("Login page returned no SSO form fields (bad credentials?)")

            async with self._session.post(_SSO_URL, data=form_fields) as response:
                if response.status != 200:
                    raise MyTNBAuthError(f"SSO handler rejected login with status {response.status}")

            async with self._session.get(f"{_ACCOUNT_BASE}{self._smartmeter_path}") as response:
                if response.status != 200:
                    raise MyTNBConnectionError(
                        f"Smartmeter page returned status {response.status}"
                    )
        except aiohttp.ClientError as err:
            raise MyTNBConnectionError(f"Login flow failed: {err}") from err

        self.sdpudcid = await self._fetch_sdpudcid()
        self._authenticated = True
        _LOGGER.debug("Login flow completed; sdpudcid=%s", self.sdpudcid)

    async def _fetch_sdpudcid(self) -> str:
        """Load the smartliving dashboard and extract the meter id."""
        for attempt in range(_SESSION_LAG_RETRIES):
            try:
                async with self._session.get(_DASHBOARD_URL, headers=_HTML_HEADERS) as response:
                    if response.status in (401, 403):
                        raise MyTNBAuthError(
                            f"Dashboard request rejected with status {response.status}"
                        )
                    response.raise_for_status()
                    text = await response.text()
            except aiohttp.TooManyRedirects:
                # A login redirect loop happens both for dead sessions and
                # transiently while a fresh session propagates; retry.
                text = ""
            except aiohttp.ClientError as err:
                raise MyTNBConnectionError(f"Dashboard request failed: {err}") from err

            if match := _SDPUDCID_RE.search(text):
                return match.group(1)

            _LOGGER.debug(
                "No sdpudcid in dashboard response (attempt %d/%d, length=%d)",
                attempt + 1,
                _SESSION_LAG_RETRIES,
                len(text),
            )
            if attempt < _SESSION_LAG_RETRIES - 1:
                await asyncio.sleep(_SESSION_LAG_DELAY)

        raise MyTNBAuthError(
            f"No sdpudcid in dashboard response after {_SESSION_LAG_RETRIES} attempts"
        )

    async def _fetch_all(self, start: datetime, end: datetime) -> SmartMeterData:
        """Fetch both metrics, tolerating a transient failure of one."""
        errors: list[MyTNBConnectionError] = []

        async def fetch(metric: str, granularity: str | None) -> list[EnergyPoint]:
            try:
                return await self._fetch_metric(metric, granularity, start, end)
            except MyTNBConnectionError as err:
                _LOGGER.warning("Fetching %s failed: %s", metric, err)
                errors.append(err)
                return []

        # Cost does not support sub-daily granularity.
        usage = await fetch("usage", "HOUR")
        cost = await fetch("cost", None)

        if len(errors) == 2:
            raise errors[0]
        return SmartMeterData(usage=usage, cost=cost)

    async def _fetch_metric(
        self, metric: str, granularity: str | None, start: datetime, end: datetime
    ) -> list[EnergyPoint]:
        """Arm and query the timeseries endpoint for one metric."""
        if self.sdpudcid is None:
            raise MyTNBAuthError("Not authenticated")

        headers = {
            **_HTML_HEADERS,
            "Referer": f"{_SMARTLIVING_BASE}/commodity/electric/{metric}",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.5",
            "X-Requested-With": "XMLHttpRequest",
            "X-Request": "JSON",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        params: dict[str, str] = {
            "metric": metric,
            "view": "BILL",
            "sdpudcid": self.sdpudcid,
            "start": start.strftime(_REQUEST_DATE_FORMAT),
            "end": end.strftime(_REQUEST_DATE_FORMAT),
        }
        if granularity:
            params["granularity"] = granularity

        for attempt in range(_SESSION_LAG_RETRIES):
            try:
                await self._visit_commodity_page(metric)
            except MyTNBAuthError:
                # Redirect loops on the commodity page are transient during
                # session propagation, same as the redirect-JSON below.
                if attempt < _SESSION_LAG_RETRIES - 1:
                    await asyncio.sleep(_SESSION_LAG_DELAY)
                    continue
                raise

            try:
                async with self._session.get(
                    _TIMESERIES_URL, headers=headers, params=params
                ) as response:
                    if response.status in (401, 403):
                        raise MyTNBAuthError(
                            f"Timeseries request rejected with status {response.status}"
                        )
                    response.raise_for_status()
                    try:
                        payload = await response.json()
                    except aiohttp.ContentTypeError as err:
                        # An expired session is served the HTML login page
                        # with status 200.
                        raise MyTNBAuthError("Timeseries endpoint returned non-JSON") from err
            except MyTNBError:
                raise
            except aiohttp.ClientError as err:
                raise MyTNBConnectionError(f"Timeseries request failed: {err}") from err

            if not (isinstance(payload, dict) and payload.get("redirect")):
                points = self._parse_points(payload)
                _LOGGER.debug("%s: fetched %d points", metric, len(points))
                return points

            _LOGGER.debug(
                "Timeseries %s redirected to %s (attempt %d/%d)",
                metric,
                payload.get("redirectTo"),
                attempt + 1,
                _SESSION_LAG_RETRIES,
            )
            if attempt < _SESSION_LAG_RETRIES - 1:
                await asyncio.sleep(_SESSION_LAG_DELAY)

        raise MyTNBAuthError(
            f"Timeseries {metric} kept redirecting to login after {_SESSION_LAG_RETRIES} attempts"
        )

    async def _visit_commodity_page(self, metric: str) -> None:
        """Load the commodity page for a metric to arm the timeseries endpoint."""
        url = f"{_SMARTLIVING_BASE}/commodity/electric/{metric}"
        try:
            async with self._session.get(url, headers=_HTML_HEADERS) as response:
                if response.status in (401, 403):
                    raise MyTNBAuthError(
                        f"Commodity page rejected with status {response.status}"
                    )
                response.raise_for_status()
        except aiohttp.TooManyRedirects as err:
            raise MyTNBAuthError("Commodity page stuck in a login redirect loop") from err
        except aiohttp.ClientError as err:
            raise MyTNBConnectionError(f"Commodity page request failed: {err}") from err

    @staticmethod
    def _parse_points(payload: Any) -> list[EnergyPoint]:
        """Extract sorted points from a timeseries payload, skipping nulls."""
        points: list[EnergyPoint] = []
        inner = payload.get("data") if isinstance(payload, dict) else None
        timeseries = inner.get("timeseries", []) if isinstance(inner, dict) else []
        for item in timeseries:
            if not isinstance(item, dict):
                continue
            for point in item.get("data", []):
                if not isinstance(point, dict):
                    continue
                value = point.get("value")
                dt_str = point.get("datetime")
                if value is None or not dt_str:
                    continue
                try:
                    start = datetime.strptime(dt_str, _POINT_DATETIME_FORMAT).replace(
                        tzinfo=TIMEZONE
                    )
                    points.append(EnergyPoint(start=start, value=float(value)))
                except (ValueError, TypeError):
                    continue
        points.sort(key=lambda p: p.start)
        return points
