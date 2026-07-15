"""Tests for the MyTNB API client."""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import DASHBOARD_PAGE, LOGIN_REDIRECT_JSON, SDPUDCID, SMARTMETER_URL, api

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
    with pytest.raises(api.MyTNBAuthError):
        await client.async_authenticate()


async def test_dashboard_session_lag_is_retried(client, fake_tnb):
    """The first dashboard hit after login can miss the sdpudcid; retry wins."""
    fake_tnb.dashboard_bodies = ["<html>login redirect</html>", DASHBOARD_PAGE]
    assert await client.async_authenticate() == SDPUDCID


async def test_dashboard_never_propagates_raises_auth_error(client, fake_tnb):
    fake_tnb.dashboard_bodies = ["<html>nope</html>"]
    with pytest.raises(api.MyTNBAuthError):
        await client.async_authenticate()


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
