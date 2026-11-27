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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from time import monotonic
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
# Account-level JSON embedded in the dashboard of any logged-in account,
# with or without a smart meter. Login/redirect pages served while a session
# is still propagating don't carry it, so a dashboard that has this but no
# sdpudcid means the account has no smart meter.
_ACCOUNT_MARKER_RE = re.compile(r'"billcyclesurn":"/billcycle\?[^"]*accountnumber=')
_POINT_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
_REQUEST_DATE_FORMAT = "%Y-%m-%d+00:00"

_SESSION_LAG_RETRIES = 3
_SESSION_LAG_DELAY = 2.0

# TNB takes the portal down for scheduled maintenance from 23:30 to 00:30
# Malaysia time every night and rejects sessions meanwhile (issue #2), so
# callers should avoid fetching inside this window. It is padded by five
# minutes each side so a fetch neither runs into the start nor hits the
# portal the instant it comes back.
MAINTENANCE_WINDOW_START = time(23, 25)
MAINTENANCE_WINDOW_END = time(0, 35)


class MyTNBError(Exception):
    """Base error for the MyTNB client."""


class MyTNBAuthError(MyTNBError):
    """Credentials rejected, or the session is missing/expired."""

    def __init__(self, message: str, trace: str = "") -> None:
        """Keep where the rejected request ended up, for diagnostics."""
        super().__init__(message)
        self.trace = trace


class MyTNBCredentialsError(MyTNBAuthError):
    """The login form itself rejected the email/password."""


class MyTNBNoSmartMeterError(MyTNBError):
    """Logged in fine, but the account has no smart meter (no sdpudcid)."""


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


def maintenance_window_end(now: datetime) -> datetime | None:
    """Return when TNB's nightly maintenance window ends, or None if outside it."""
    local = now.astimezone(TIMEZONE)
    if local.time() >= MAINTENANCE_WINDOW_START:
        end_date = local.date() + timedelta(days=1)
    elif local.time() < MAINTENANCE_WINDOW_END:
        end_date = local.date()
    else:
        return None
    return datetime.combine(end_date, MAINTENANCE_WINDOW_END, tzinfo=TIMEZONE)


def create_cookie_jar() -> aiohttp.CookieJar:
    """The cookie jar the client's session must use.

    By default aiohttp wraps a cookie value in double quotes when sending it
    back if the value holds characters such as + / = (as base64 tokens do).
    Browsers send the value as-is, and TNB's smartliving site doesn't
    recognize the quoted form of its e3AuthToken: it deletes the token and
    redirects to login (issue #6). Only tokens that happen to contain those
    characters break, which is why fetches failed only some of the time.
    """
    return aiohttp.CookieJar(quote_cookie=False)


def extract_smartmeter_path(smartmeter_url: str) -> str:
    """Extract the smartmeter path from the URL pasted by the user."""
    match = _SMARTMETER_PATH_RE.search(smartmeter_url)
    if not match:
        raise MyTNBError("Could not find a SmartMeter/Index/TRIL?caNo=... path in the URL")
    return match.group(0)


# Response headers that identify the CDN/WAF/backend that answered, which is
# what tells a TNB-side rejection apart from a session problem on our side.
_TRACE_HEADERS = ("server", "via", "x-cache", "x-azure-ref", "cf-ray", "x-amz-cf-pop")


_SAFE_COOKIE_CHARS_RE = re.compile(r"[A-Za-z0-9._-]")


def _describe_set_cookie(morsel: Any) -> str:
    """A Set-Cookie as 'name(attrs)': the attributes, never the value.

    The value's length (or 'empty') tells a newly issued token apart from
    the server deleting it, and path/domain show whether our jar will send
    it back on the next request.
    """
    attrs = [f"len={len(morsel.value)}" if morsel.value else "empty"]
    # Just which special characters occur, e.g. "+/=", which decides
    # whether a cookie jar that quotes values would mangle it (issue #6).
    if special := "".join(sorted(set(_SAFE_COOKIE_CHARS_RE.sub("", morsel.value)))):
        attrs.append(f"chars={special}")
    for attr in ("path", "domain", "max-age", "expires"):
        if morsel[attr]:
            attrs.append(f"{attr}={morsel[attr]}")
    return f"{morsel.key}({';'.join(attrs)})"


def _describe_hop(response: aiohttp.ClientResponse) -> str:
    """One hop as 'STATUS host/path', plus cookies set and edge headers.

    Query strings are dropped (they carry the caNo and sdpudcid) and only
    cookie names are shown, never values, so this is safe to log.
    """
    url = response.url
    parts = [f"{response.status} {url.host}{url.path}"]
    # Names of the cookies this request carried: shows whether a token the
    # previous response issued was actually sent back.
    sent = response.request_info.headers.get("Cookie", "")
    sent_names = sorted({c.split("=", 1)[0].strip() for c in sent.split(";") if c.strip()})
    parts.append(f"sent={','.join(sent_names) or 'none'}")
    if set_cookies := [
        _describe_set_cookie(m) for m in sorted(response.cookies.values(), key=lambda m: m.key)
    ]:
        parts.append(f"set-cookie={','.join(set_cookies)}")
    edge = [f"{h}={response.headers[h]}" for h in _TRACE_HEADERS if h in response.headers]
    if edge:
        parts.append(" ".join(edge))
    return " ".join(parts)


def _describe_chain(
    history: Sequence[aiohttp.ClientResponse], final: aiohttp.ClientResponse | None = None
) -> str:
    """The redirect chain of a request, first hop first."""
    hops = [*history, final] if final is not None else list(history)
    return " -> ".join(_describe_hop(hop) for hop in hops) or "(no response)"


_URL_QUERY_RE = re.compile(r"(https?://[^\s'\"?]+)\?[^\s'\"]*")


def _safe_error(err: Exception) -> str:
    """An aiohttp error's text with URL query strings removed.

    aiohttp includes the full request URL in its error messages, and ours
    carry the contract account number (caNo) and the meter id (sdpudcid).
    """
    return _URL_QUERY_RE.sub(r"\1", str(err))


def _is_login_url(url: Any) -> bool:
    return "login" in str(getattr(url, "path", "")).lower()


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
        self.sdpudcid: str | None = None
        # For diagnostics only: which login this is and when it happened.
        self._login_count = 0
        self._login_at: float | None = None

    def _session_state(self) -> str:
        """Login number, session age and cookie names held, for diagnostics."""
        age = "never logged in"
        if self._login_at is not None:
            age = f"{monotonic() - self._login_at:.1f}s since login"
        by_domain: dict[str, set[str]] = {}
        for morsel in self._session.cookie_jar:
            by_domain.setdefault(morsel["domain"] or "(host-only)", set()).add(morsel.key)
        cookies = "; ".join(
            f"{domain}: {','.join(sorted(names))}" for domain, names in sorted(by_domain.items())
        )
        return f"login #{self._login_count}, {age}, cookies=[{cookies or 'none'}]"

    async def async_authenticate(self) -> str:
        """Run the full login flow; returns the meter's sdpudcid."""
        async with self._auth_lock:
            await self._login()
        assert self.sdpudcid is not None
        return self.sdpudcid

    async def async_get_data(self, start: datetime, end: datetime) -> SmartMeterData:
        """Fetch usage and cost on a freshly logged-in session.

        Every fetch starts with a full login rather than reusing the session
        from the previous poll. Polls are hours apart, so that session has
        always expired by then and a fetch with it would only be bounced.
        Logging in up front sends every poll down the same path as startup.
        """
        await self._relogin()
        try:
            return await self._fetch_all(start, end)
        except MyTNBAuthError as err:
            _LOGGER.debug("Session rejected (%s); logging in again", err)
            await self._relogin()
            return await self._fetch_all(start, end)

    async def _relogin(self) -> None:
        async with self._auth_lock:
            await self._login()

    async def _login(self) -> None:
        """Login → SSO → smartmeter page → dashboard (sdpudcid)."""
        self.sdpudcid = None
        self._session.cookie_jar.clear()
        self._login_count += 1
        self._login_at = monotonic()

        try:
            async with self._session.post(
                _LOGIN_URL, data={"Email": self._username, "Password": self._password}
            ) as response:
                response.raise_for_status()
                form_fields = dict(_FORM_FIELD_RE.findall(await response.text()))
                _LOGGER.debug(
                    "Login #%d: login form: %s (sso fields=%s)",
                    self._login_count,
                    _describe_chain(response.history, response),
                    ",".join(sorted(form_fields)) or "none",
                )
            if not form_fields:
                raise MyTNBCredentialsError(
                    "Login page returned no SSO form fields (bad credentials?)"
                )

            async with self._session.post(_SSO_URL, data=form_fields) as response:
                _LOGGER.debug(
                    "Login #%d: SSO handler: %s",
                    self._login_count,
                    _describe_chain(response.history, response),
                )
                if response.status != 200:
                    raise MyTNBAuthError(f"SSO handler rejected login with status {response.status}")

            async with self._session.get(f"{_ACCOUNT_BASE}{self._smartmeter_path}") as response:
                _LOGGER.debug(
                    "Login #%d: smartmeter page: %s",
                    self._login_count,
                    _describe_chain(response.history, response),
                )
                if response.status != 200:
                    raise MyTNBConnectionError(
                        f"Smartmeter page returned status {response.status}"
                    )
        except aiohttp.TooManyRedirects as err:
            _LOGGER.warning(
                "Login #%d stuck in a redirect loop: %s",
                self._login_count,
                _describe_chain(err.history),
            )
            raise MyTNBConnectionError(f"Login flow failed: {_safe_error(err)}") from err
        except aiohttp.ClientError as err:
            raise MyTNBConnectionError(f"Login flow failed: {_safe_error(err)}") from err

        self.sdpudcid = await self._fetch_sdpudcid()
        _LOGGER.debug(
            "Login flow completed; sdpudcid=%s; %s", self.sdpudcid, self._session_state()
        )

    async def _fetch_sdpudcid(self) -> str:
        """Load the smartliving dashboard and extract the meter id."""
        account_page = False
        trace = "(no response)"
        for attempt in range(_SESSION_LAG_RETRIES):
            try:
                async with self._session.get(_DASHBOARD_URL, headers=_HTML_HEADERS) as response:
                    trace = _describe_chain(response.history, response)
                    if response.status in (401, 403):
                        # The WAF can reject a request while a fresh session
                        # is still propagating, same as the redirect loop
                        # below; retry before treating it as a dead session.
                        if attempt == _SESSION_LAG_RETRIES - 1:
                            _LOGGER.warning(
                                "MyTNB dashboard rejected the session: %s; %s",
                                trace,
                                self._session_state(),
                            )
                            raise MyTNBAuthError(
                                f"Dashboard request rejected with status {response.status}"
                            )
                        text = ""
                    else:
                        response.raise_for_status()
                        text = await response.text()
            except aiohttp.TooManyRedirects as err:
                # A login redirect loop happens both for dead sessions and
                # transiently while a fresh session propagates; retry.
                text = ""
                trace = f"redirect loop: {_describe_chain(err.history)}"
            except aiohttp.ClientError as err:
                raise MyTNBConnectionError(f"Dashboard request failed: {_safe_error(err)}") from err

            if match := _SDPUDCID_RE.search(text):
                _LOGGER.debug("Dashboard: %s; %s", trace, self._session_state())
                return match.group(1)

            account_page = bool(_ACCOUNT_MARKER_RE.search(text))
            _LOGGER.debug(
                "No sdpudcid in dashboard response (attempt %d/%d, length=%d, "
                "account_page=%s): %s",
                attempt + 1,
                _SESSION_LAG_RETRIES,
                len(text),
                account_page,
                trace,
            )
            if attempt < _SESSION_LAG_RETRIES - 1:
                await asyncio.sleep(_SESSION_LAG_DELAY)

        if account_page:
            raise MyTNBNoSmartMeterError(
                "Logged in, but the MyTNB dashboard lists no smart meter for this account"
            )
        _LOGGER.warning(
            "MyTNB dashboard never returned the meter after %d attempts; last response: %s; %s",
            _SESSION_LAG_RETRIES,
            trace,
            self._session_state(),
        )
        raise MyTNBAuthError(
            f"No sdpudcid in dashboard response after {_SESSION_LAG_RETRIES} attempts"
        )

    async def _fetch_all(self, start: datetime, end: datetime) -> SmartMeterData:
        """Fetch both metrics, tolerating a failure of one.

        That includes one metric being bounced to login: if the other one
        was fetched on the same session, the session is fine, and TNB has
        been rejecting just the cost request while usage works (issue #6).
        Failing the whole poll for that threw away the usage data too, so
        nothing updated. Only when both fail does the error propagate (an
        auth error first, so the caller logs in again).
        """
        errors: list[MyTNBError] = []

        async def fetch(metric: str, granularity: str | None) -> list[EnergyPoint]:
            try:
                return await self._fetch_metric(metric, granularity, start, end)
            except (MyTNBConnectionError, MyTNBAuthError) as err:
                _LOGGER.warning("Fetching %s failed: %s", metric, err)
                errors.append(err)
                return []

        # Cost does not support sub-daily granularity.
        usage = await fetch("usage", "HOUR")
        cost = await fetch("cost", None)

        if len(errors) == 2:
            raise next((e for e in errors if isinstance(e, MyTNBAuthError)), errors[0])
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

        commodity_trace = timeseries_trace = "(no response)"
        redirect_info = ""
        for attempt in range(_SESSION_LAG_RETRIES):
            try:
                commodity_trace = await self._visit_commodity_page(metric)
            except MyTNBAuthError as err:
                # Redirect loops on the commodity page are transient during
                # session propagation, same as the redirect-JSON below.
                if attempt < _SESSION_LAG_RETRIES - 1:
                    await asyncio.sleep(_SESSION_LAG_DELAY)
                    continue
                _LOGGER.warning(
                    "MyTNB %s commodity page kept rejecting the session after %d attempts: "
                    "%s; %s",
                    metric,
                    _SESSION_LAG_RETRIES,
                    err.trace or "(no trace)",
                    self._session_state(),
                )
                raise

            try:
                async with self._session.get(
                    _TIMESERIES_URL, headers=headers, params=params
                ) as response:
                    timeseries_trace = _describe_chain(response.history, response)
                    if response.status in (401, 403):
                        _LOGGER.warning(
                            "MyTNB %s timeseries rejected the session: %s; "
                            "commodity page: %s; %s",
                            metric,
                            timeseries_trace,
                            commodity_trace,
                            self._session_state(),
                        )
                        raise MyTNBAuthError(
                            f"Timeseries request rejected with status {response.status}"
                        )
                    response.raise_for_status()
                    try:
                        payload = await response.json()
                    except aiohttp.ContentTypeError as err:
                        # An expired session is served the HTML login page
                        # with status 200.
                        _LOGGER.warning(
                            "MyTNB %s timeseries returned %s instead of JSON: %s; "
                            "commodity page: %s; %s",
                            metric,
                            response.content_type,
                            timeseries_trace,
                            commodity_trace,
                            self._session_state(),
                        )
                        raise MyTNBAuthError("Timeseries endpoint returned non-JSON") from err
            except MyTNBError:
                raise
            except aiohttp.ClientError as err:
                raise MyTNBConnectionError(f"Timeseries request failed: {_safe_error(err)}") from err

            if not (isinstance(payload, dict) and payload.get("redirect")):
                points = self._parse_points(payload)
                _LOGGER.debug("%s: fetched %d points", metric, len(points))
                return points

            # Everything TNB says about the redirect, minus the (empty) data.
            redirect_info = ", ".join(
                f"{key}={payload[key]!r}"
                for key in ("redirectTo", "errorMessage", "errors", "fieldErrors")
                if payload.get(key)
            )
            _LOGGER.debug(
                "Timeseries %s redirected (attempt %d/%d): %s; timeseries: %s; "
                "commodity page: %s; %s",
                metric,
                attempt + 1,
                _SESSION_LAG_RETRIES,
                redirect_info,
                timeseries_trace,
                commodity_trace,
                self._session_state(),
            )
            if attempt < _SESSION_LAG_RETRIES - 1:
                await asyncio.sleep(_SESSION_LAG_DELAY)

        _LOGGER.warning(
            "MyTNB %s timeseries kept redirecting to login after %d attempts: %s; "
            "timeseries: %s; commodity page: %s; %s",
            metric,
            _SESSION_LAG_RETRIES,
            redirect_info,
            timeseries_trace,
            commodity_trace,
            self._session_state(),
        )
        raise MyTNBAuthError(
            f"Timeseries {metric} kept redirecting to login after {_SESSION_LAG_RETRIES} attempts"
        )

    async def _visit_commodity_page(self, metric: str) -> str:
        """Load the commodity page for a metric to arm the timeseries endpoint.

        Returns a description of where the request ended up, for diagnostics.
        """
        url = f"{_SMARTLIVING_BASE}/commodity/electric/{metric}"
        try:
            async with self._session.get(url, headers=_HTML_HEADERS) as response:
                trace = _describe_chain(response.history, response)
                if response.status in (401, 403):
                    raise MyTNBAuthError(
                        f"Commodity page rejected with status {response.status}", trace
                    )
                response.raise_for_status()
        except aiohttp.TooManyRedirects as err:
            raise MyTNBAuthError(
                "Commodity page stuck in a login redirect loop",
                f"redirect loop: {_describe_chain(err.history)}",
            ) from err
        except aiohttp.ClientError as err:
            raise MyTNBConnectionError(f"Commodity page request failed: {_safe_error(err)}") from err

        if _is_login_url(response.url):
            # Served with 200, so the request "succeeds", but the endpoint was
            # not armed: the timeseries request that follows will be bounced.
            _LOGGER.debug(
                "Commodity page %s landed on a login page: %s; %s",
                metric,
                trace,
                self._session_state(),
            )
        else:
            # The jar right before the timeseries request: shows whether the
            # auth token the page just issued is there to be sent.
            _LOGGER.debug("Commodity page %s: %s; %s", metric, trace, self._session_state())
        return trace

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
