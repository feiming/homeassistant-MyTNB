"""Tests for the MyTNB API client."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import (
    DASHBOARD_PAGE,
    LOGIN_REDIRECT_JSON,
    SDPUDCID,
    SESSION_COOKIE_VALUE,
    SMARTMETER_URL,
    api,
)

START = datetime(2026, 6, 14, tzinfo=api.TIMEZONE)
END = datetime(2026, 7, 15, tzinfo=api.TIMEZONE)


def timeseries_payload(metric: str, points: list[tuple[str, float | None]]) -> dict:
    """Build a minimal timeseries response body."""
    return {
        "data": {
            "timeseries": [
                {"data": [{"datetime": dt, "value": value, "metric": metric.upper()}]}
                for dt, value in points
            ]
        }
    }


USAGE_OK = timeseries_payload("usage", [("2026-07-12 23:30", 0.85)])
COST_OK = timeseries_payload("cost", [("2026-07-12 00:00", 26.42)])


async def test_authenticate_success(client, fake_tnb):
    assert await client.async_authenticate() == SDPUDCID
    assert client.sdpudcid == SDPUDCID
    assert fake_tnb.login_count == 1


async def test_authenticate_bad_credentials(client, fake_tnb):
    """A login page without SSO form fields means the credentials were rejected."""
    fake_tnb.login_body = "<html>Invalid email or password</html>"
    with pytest.raises(api.MyTNBCredentialsError):
        await client.async_authenticate()


async def test_dashboard_session_lag_is_retried(client, fake_tnb):
    """The first dashboard hit after login can miss the sdpudcid; retry wins."""
    fake_tnb.dashboard_bodies = ["<html>login redirect</html>", DASHBOARD_PAGE]
    assert await client.async_authenticate() == SDPUDCID


async def test_dashboard_never_propagates_raises_auth_error(client, fake_tnb):
    fake_tnb.dashboard_bodies = ["<html>nope</html>"]
    with pytest.raises(api.MyTNBAuthError) as exc_info:
        await client.async_authenticate()
    # The password was accepted; this must not trigger a reauth flow.
    assert not isinstance(exc_info.value, api.MyTNBCredentialsError)


NO_METER_DASHBOARD = (
    '<script>var x = {"billcyclesurn":"/billcycle?orgname=TNB&accountnumber=123",'
    '"commodities":[]};</script>'
)


async def test_dashboard_without_meter_raises_no_smart_meter(client, fake_tnb):
    """A logged-in dashboard with no sdpudcid means the account has no meter."""
    fake_tnb.dashboard_bodies = [NO_METER_DASHBOARD]
    with pytest.raises(api.MyTNBNoSmartMeterError):
        await client.async_authenticate()


async def test_session_lag_then_meterless_dashboard_is_no_smart_meter(client, fake_tnb):
    """Lag pages before the real dashboard don't hide the no-meter verdict."""
    fake_tnb.dashboard_bodies = ["<html>login redirect</html>", NO_METER_DASHBOARD]
    with pytest.raises(api.MyTNBNoSmartMeterError):
        await client.async_authenticate()


async def test_no_smart_meter_is_not_auth_error(client, fake_tnb):
    """It must not trigger the re-login loop or a reauth flow."""
    fake_tnb.dashboard_bodies = [NO_METER_DASHBOARD]
    with pytest.raises(api.MyTNBError) as exc_info:
        await client.async_authenticate()
    assert not isinstance(exc_info.value, api.MyTNBAuthError)


async def test_invalid_smartmeter_url_rejected(session):
    with pytest.raises(api.MyTNBError):
        api.MyTNBClient(session, "user@example.com", "secret", "https://example.com/nope")


async def test_get_data_cold_start_logs_in_and_parses(client, fake_tnb):
    """async_get_data logs in by itself and returns parsed, sorted points."""
    fake_tnb.timeseries = {
        "usage": [
            timeseries_payload(
                "usage",
                [
                    ("2026-07-12 23:30", 0.85),  # deliberately out of order
                    ("2026-07-12 23:00", 0.5),
                    ("2026-07-12 22:30", None),  # nulls are skipped
                ],
            )
        ],
        "cost": [COST_OK],
    }

    data = await client.async_get_data(START, END)

    assert fake_tnb.login_count == 1
    assert [p.value for p in data.usage] == [0.5, 0.85]
    assert data.usage[-1].start == datetime(2026, 7, 12, 23, 30, tzinfo=api.TIMEZONE)
    assert len(data.cost) == 1
    assert data.cost[0].value == 26.42
    # each timeseries request must have been armed by a commodity page visit
    assert fake_tnb.commodity_visits == ["usage", "cost"]


async def test_timeseries_redirect_json_is_retried(client, fake_tnb):
    """A redirect-to-login JSON is re-armed and retried, not fatal."""
    fake_tnb.timeseries = {
        "usage": [LOGIN_REDIRECT_JSON, USAGE_OK],
        "cost": [COST_OK],
    }

    data = await client.async_get_data(START, END)

    assert len(data.usage) == 1
    assert len(data.cost) == 1
    assert fake_tnb.login_count == 1  # retried without a full re-login
    assert fake_tnb.commodity_visits == ["usage", "usage", "cost"]


async def test_expired_session_triggers_relogin(client, fake_tnb, monkeypatch):
    """If every retry gets the redirect JSON, the client re-logins and recovers."""
    monkeypatch.setattr(api, "_SESSION_LAG_RETRIES", 1)
    fake_tnb.timeseries = {
        "usage": [LOGIN_REDIRECT_JSON, USAGE_OK],
        "cost": [COST_OK],
    }

    data = await client.async_get_data(START, END)

    assert fake_tnb.login_count == 2
    assert len(data.usage) == 1
    assert len(data.cost) == 1


async def test_each_poll_logs_in_fresh(client, fake_tnb):
    """A later poll must not reuse the earlier, long-expired session (issue #6).

    Fetching with the stale session first left running installs stuck in a
    login redirect loop while a reload (fresh login) worked, so every poll
    logs in before its first request, exactly like startup.
    """
    fake_tnb.timeseries = {"usage": [USAGE_OK], "cost": [COST_OK]}

    await client.async_get_data(START, END)
    data = await client.async_get_data(START, END)

    assert fake_tnb.login_count == 2
    assert len(data.usage) == 1
    assert len(data.cost) == 1
    # one armed request per metric per poll: no doomed attempt on a stale session
    assert fake_tnb.commodity_visits == ["usage", "cost", "usage", "cost"]


async def test_rejection_is_logged_with_diagnostics(client, fake_tnb, monkeypatch, caplog):
    """A rejected session logs why at WARNING, without leaking secrets (issue #6)."""
    monkeypatch.setattr(api, "_SESSION_LAG_RETRIES", 1)
    fake_tnb.timeseries = {
        "usage": [{**LOGIN_REDIRECT_JSON, "errorMessage": "Session timed out"}],
        "cost": [COST_OK],
    }

    with caplog.at_level("DEBUG", logger="mytnb_api"), pytest.raises(api.MyTNBAuthError):
        await client.async_get_data(START, END)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    # one warning per login: the up-front one and the retry
    assert len(warnings) == 2
    last = warnings[-1]
    assert "redirectTo='/login'" in last
    assert "errorMessage='Session timed out'" in last
    assert "/my_energy_request/timeseries" in last
    assert "/commodity/electric/usage" in last
    assert "login #2" in last
    assert "since login" in last
    assert "ASP.NET_SessionId" in last  # cookie names are shown...

    everything = caplog.text
    assert SESSION_COOKIE_VALUE not in everything  # ...but never their values
    assert "secret" not in everything  # the password
    assert "caNo=" not in everything  # query strings are dropped


async def test_one_metric_failing_is_tolerated(client, fake_tnb):
    """A transient 500 on one metric returns empty for it, keeps the other."""
    fake_tnb.timeseries = {"usage": [USAGE_OK], "cost": [500]}

    data = await client.async_get_data(START, END)

    assert len(data.usage) == 1
    assert data.cost == []


async def test_both_metrics_failing_raises(client, fake_tnb):
    fake_tnb.timeseries = {"usage": [500], "cost": [500]}

    with pytest.raises(api.MyTNBConnectionError):
        await client.async_get_data(START, END)


async def test_forbidden_timeseries_is_auth_error(client, fake_tnb):
    """401/403 must not be swallowed by the partial-failure tolerance."""
    fake_tnb.timeseries = {"usage": [403], "cost": [COST_OK]}

    with pytest.raises(api.MyTNBAuthError):
        # relogin is attempted once, then the auth error surfaces
        await client.async_get_data(START, END)
    assert fake_tnb.login_count == 2


async def test_timeseries_redirect_loop_is_not_credentials_error(client, fake_tnb, monkeypatch):
    """A persistent login redirect on timeseries is a session problem, not a bad password."""
    monkeypatch.setattr(api, "_SESSION_LAG_RETRIES", 1)
    fake_tnb.timeseries = {"usage": [LOGIN_REDIRECT_JSON], "cost": [COST_OK]}

    with pytest.raises(api.MyTNBAuthError) as exc_info:
        await client.async_get_data(START, END)
    assert not isinstance(exc_info.value, api.MyTNBCredentialsError)


def test_parse_points_handles_malformed_payloads():
    assert api.MyTNBClient._parse_points(None) == []
    assert api.MyTNBClient._parse_points({}) == []
    assert api.MyTNBClient._parse_points({"data": []}) == []
    assert api.MyTNBClient._parse_points({"data": {"timeseries": ["junk", {"data": ["junk"]}]}}) == []
    assert api.MyTNBClient._parse_points(
        {"data": {"timeseries": [{"data": [{"datetime": "not a date", "value": 1}]}]}}
    ) == []


def test_extract_smartmeter_path():
    path = api.extract_smartmeter_path(SMARTMETER_URL)
    assert path == "/AccountManagement/SmartMeter/Index/TRIL?caNo=TESTCA"


@pytest.mark.parametrize(
    ("now", "expected_end"),
    [
        ((2026, 7, 15, 23, 24), None),
        ((2026, 7, 15, 23, 25), (2026, 7, 16, 0, 35)),
        ((2026, 7, 16, 0, 15), (2026, 7, 16, 0, 35)),
        ((2026, 7, 16, 0, 34), (2026, 7, 16, 0, 35)),
        ((2026, 7, 16, 0, 35), None),
        ((2026, 7, 16, 12, 0), None),
    ],
)
def test_maintenance_window_end(now, expected_end):
    """Polls around midnight Malaysia time are deferred to the window's end (issue #2)."""
    end = api.maintenance_window_end(datetime(*now, tzinfo=api.TIMEZONE))
    assert end == (expected_end and datetime(*expected_end, tzinfo=api.TIMEZONE))


def test_maintenance_window_uses_malaysia_time():
    """A UTC timestamp is judged by its Malaysia-time clock, not its own."""
    # 16:15 UTC is 00:15 the next day in Malaysia.
    end = api.maintenance_window_end(datetime(2026, 7, 15, 16, 15, tzinfo=ZoneInfo("UTC")))
    assert end == datetime(2026, 7, 16, 0, 35, tzinfo=api.TIMEZONE)
