"""Tests for the coordinator's handling of TNB's nightly maintenance window.

Home Assistant isn't installed for these tests, so the handful of names
coordinator.py imports from it are replaced with minimal stand-ins. That is
enough to exercise the coordinator's own decisions (fetch or defer, and what
polling interval to ask for); it does not exercise Home Assistant's scheduler.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "mytnb"
_PACKAGE = "mytnb_under_test"


class _FakeDataUpdateCoordinator:
    """The slice of DataUpdateCoordinator that MyTNBCoordinator relies on."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, *, name, config_entry, update_interval) -> None:
        self.hass = hass
        self.config_entry = config_entry
        self.update_interval = update_interval
        self.data = None


class _FakeUpdateFailed(Exception):
    pass


class _FakeConfigEntryAuthFailed(Exception):
    pass


@pytest.fixture
def coordinator_module(monkeypatch):
    """Import coordinator.py against stand-in Home Assistant modules."""
    stubs = {
        "homeassistant": MagicMock(),
        "homeassistant.components": MagicMock(),
        "homeassistant.components.recorder": MagicMock(),
        "homeassistant.components.recorder.models": MagicMock(),
        "homeassistant.components.recorder.statistics": MagicMock(),
        "homeassistant.config_entries": MagicMock(),
        "homeassistant.const": MagicMock(),
        "homeassistant.core": MagicMock(),
        "homeassistant.exceptions": MagicMock(
            ConfigEntryAuthFailed=_FakeConfigEntryAuthFailed
        ),
        "homeassistant.helpers": MagicMock(),
        "homeassistant.helpers.update_coordinator": MagicMock(
            DataUpdateCoordinator=_FakeDataUpdateCoordinator,
            UpdateFailed=_FakeUpdateFailed,
        ),
        "homeassistant.util": MagicMock(),
        "homeassistant.util.unit_conversion": MagicMock(),
    }
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)

    # A bare package pointing at the component directory lets coordinator.py's
    # relative imports resolve without running the integration's __init__.py.
    package = types.ModuleType(_PACKAGE)
    package.__path__ = [str(_COMPONENT_DIR)]
    monkeypatch.setitem(sys.modules, _PACKAGE, package)
    for submodule in ("api", "const", "coordinator"):
        monkeypatch.delitem(sys.modules, f"{_PACKAGE}.{submodule}", raising=False)

    module = importlib.import_module(f"{_PACKAGE}.coordinator")
    yield module
    for submodule in ("api", "const", "coordinator"):
        sys.modules.pop(f"{_PACKAGE}.{submodule}", None)


@pytest.fixture
def set_now(coordinator_module, monkeypatch):
    """Return a function that fixes the coordinator's clock (Malaysia time)."""

    def _set(*args: int) -> datetime:
        now = datetime(*args, tzinfo=coordinator_module.TIMEZONE)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now.replace(tzinfo=None)

        monkeypatch.setattr(coordinator_module, "datetime", _FrozenDatetime)
        return now

    return _set


@pytest.fixture
def fresh_data(coordinator_module):
    return coordinator_module.SmartMeterData(usage=[], cost=[])


@pytest.fixture
def coordinator(coordinator_module, fresh_data):
    """A coordinator with the default 6-hour interval and a fake client."""
    client = SimpleNamespace(
        sdpudcid="40000000000020022594",
        async_get_data=AsyncMock(return_value=fresh_data),
    )
    entry = SimpleNamespace(options={})
    coord = coordinator_module.MyTNBCoordinator(MagicMock(), entry, client)
    coord._async_insert_statistics = AsyncMock()
    return coord


async def test_failed_metric_keeps_its_last_data(coordinator, coordinator_module, set_now):
    """Issue #6: cost bounced while usage fetched keeps showing the old cost."""
    point = coordinator_module.EnergyPoint
    old_cost = [point(start=datetime(2026, 11, 14, tzinfo=coordinator_module.TIMEZONE), value=9.5)]
    new_usage = [point(start=datetime(2026, 11, 15, tzinfo=coordinator_module.TIMEZONE), value=1.2)]
    coordinator.data = coordinator_module.SmartMeterData(usage=[], cost=old_cost)
    coordinator.client.async_get_data.return_value = coordinator_module.SmartMeterData(
        usage=new_usage, cost=[]
    )
    set_now(2026, 11, 16, 14, 0)

    data = await coordinator._async_update_data()

    assert data.usage == new_usage
    assert data.cost == old_cost
    # only freshly fetched points go to statistics, never the carried-over ones
    imported = {c.args[0]: c.args[1] for c in coordinator._async_insert_statistics.await_args_list}
    assert imported == {"usage": new_usage, "cost": []}


async def test_poll_outside_window_fetches(coordinator, fresh_data, set_now):
    set_now(2026, 11, 16, 14, 0)

    assert await coordinator._async_update_data() is fresh_data

    coordinator.client.async_get_data.assert_awaited_once()
    assert coordinator.update_interval == timedelta(hours=6)


@pytest.mark.parametrize(
    ("now", "expected_delay"),
    [
        ((2026, 11, 16, 23, 30), timedelta(hours=1, minutes=5)),
        ((2026, 11, 16, 23, 59), timedelta(minutes=36)),
        ((2026, 11, 17, 0, 15), timedelta(minutes=20)),
    ],
)
async def test_poll_in_window_is_deferred(
    coordinator, coordinator_module, set_now, now, expected_delay
):
    """During maintenance TNB isn't contacted and the old data stays in place."""
    previous = coordinator_module.SmartMeterData(usage=[], cost=[])
    coordinator.data = previous
    set_now(*now)

    assert await coordinator._async_update_data() is previous

    coordinator.client.async_get_data.assert_not_awaited()
    coordinator._async_insert_statistics.assert_not_awaited()
    # The next poll is scheduled for when the window ends.
    assert coordinator.update_interval == expected_delay


async def test_interval_restored_after_deferred_poll(
    coordinator, coordinator_module, fresh_data, set_now
):
    """The poll that runs once the window is over goes back to normal polling."""
    coordinator.data = coordinator_module.SmartMeterData(usage=[], cost=[])
    set_now(2026, 11, 17, 0, 15)
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(minutes=20)

    set_now(2026, 11, 17, 0, 35)
    assert await coordinator._async_update_data() is fresh_data

    coordinator.client.async_get_data.assert_awaited_once()
    assert coordinator.update_interval == timedelta(hours=6)


async def test_first_poll_in_window_still_fetches(coordinator, fresh_data, set_now):
    """At startup there is no earlier data to keep showing, so try anyway."""
    assert coordinator.data is None
    set_now(2026, 11, 17, 0, 15)

    assert await coordinator._async_update_data() is fresh_data

    coordinator.client.async_get_data.assert_awaited_once()
    assert coordinator.update_interval == timedelta(hours=6)


async def test_configured_interval_is_respected(coordinator_module, fresh_data, set_now):
    """The interval restored after a deferral is the one set in the options."""
    client = SimpleNamespace(sdpudcid="1", async_get_data=AsyncMock(return_value=fresh_data))
    entry = SimpleNamespace(options={coordinator_module.CONF_UPDATE_INTERVAL_HOURS: 12})
    coord = coordinator_module.MyTNBCoordinator(MagicMock(), entry, client)
    coord._async_insert_statistics = AsyncMock()
    coord.data = fresh_data

    set_now(2026, 11, 17, 0, 0)
    await coord._async_update_data()
    assert coord.update_interval == timedelta(minutes=35)

    set_now(2026, 11, 17, 0, 35)
    await coord._async_update_data()
    assert coord.update_interval == timedelta(hours=12)


async def test_failed_poll_retries_soon_with_backoff(coordinator, coordinator_module, set_now):
    """Issue #6: retry a rejected poll after minutes, not the 6-hour interval."""
    set_now(2026, 11, 16, 14, 0)
    get_data = coordinator.client.async_get_data
    get_data.side_effect = coordinator_module.MyTNBAuthError("redirect")

    intervals = []
    for _ in range(7):
        with pytest.raises(_FakeUpdateFailed):
            await coordinator._async_update_data()
        intervals.append(coordinator.update_interval)
    assert intervals == [
        timedelta(minutes=10),
        timedelta(minutes=20),
        timedelta(minutes=40),
        timedelta(minutes=80),
        timedelta(minutes=160),
        timedelta(minutes=320),
        timedelta(hours=6),  # capped at the poll interval
    ]

    # a success goes back to the normal interval and resets the backoff
    get_data.side_effect = None
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(hours=6)
    get_data.side_effect = coordinator_module.MyTNBConnectionError("timeout")
    with pytest.raises(_FakeUpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(minutes=10)


async def test_no_smart_meter_does_not_retry_early(coordinator, coordinator_module, set_now):
    """A missing meter won't heal in minutes; keep the normal interval."""
    set_now(2026, 11, 16, 14, 0)
    coordinator.client.async_get_data.side_effect = coordinator_module.MyTNBNoSmartMeterError("x")
    with pytest.raises(_FakeUpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(hours=6)


async def test_session_rejection_outside_window_retries_without_reauth(
    coordinator, coordinator_module, set_now
):
    """A rejected session is a retryable failure, never a reauth prompt."""
    set_now(2026, 11, 16, 14, 0)
    coordinator.client.async_get_data.side_effect = coordinator_module.MyTNBAuthError("redirect")

    with pytest.raises(_FakeUpdateFailed):
        await coordinator._async_update_data()


async def test_bad_credentials_still_trigger_reauth(coordinator, coordinator_module, set_now):
    set_now(2026, 11, 16, 14, 0)
    coordinator.client.async_get_data.side_effect = coordinator_module.MyTNBCredentialsError(
        "bad password"
    )

    with pytest.raises(_FakeConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_no_smart_meter_is_a_clear_update_failure(
    coordinator, coordinator_module, set_now
):
    set_now(2026, 11, 16, 14, 0)
    coordinator.client.async_get_data.side_effect = coordinator_module.MyTNBNoSmartMeterError(
        "no meter"
    )

    with pytest.raises(_FakeUpdateFailed, match="No smart meter found"):
        await coordinator._async_update_data()


# --- statistics import ---------------------------------------------------


@pytest.fixture
def stats_env(coordinator_module, monkeypatch):
    """Run the real _async_insert_statistics against an in-memory recorder.

    `stored` holds the existing rows, newest first, as the recorder's
    get_last_statistics returns them; `imported` collects what gets written.
    """
    env = SimpleNamespace(stored=[], imported=[])

    async def executor_job(func, hass, number_of_stats, statistic_id, *args):
        return {statistic_id: env.stored[:number_of_stats]} if env.stored else {}

    recorder = SimpleNamespace(async_add_executor_job=executor_job)
    monkeypatch.setattr(coordinator_module, "get_instance", lambda hass: recorder)
    monkeypatch.setattr(coordinator_module, "StatisticData", lambda **kw: kw)
    monkeypatch.setattr(coordinator_module, "StatisticMetaData", lambda **kw: kw)
    monkeypatch.setattr(
        coordinator_module,
        "async_add_external_statistics",
        lambda hass, metadata, stats: env.imported.extend(stats),
    )
    return env


def _hours(module, day: tuple[int, int, int], value: float, count: int = 24) -> list:
    start = datetime(*day, tzinfo=module.TIMEZONE)
    return [module.EnergyPoint(start=start + timedelta(hours=h), value=value) for h in range(count)]


def _row(module, when: datetime, total: float) -> dict:
    return {"start": when.timestamp(), "sum": total}


async def _insert(coordinator, points):
    coordinator._async_insert_statistics = type(coordinator)._async_insert_statistics.__get__(
        coordinator
    )
    await coordinator._async_insert_statistics("cost", points, "MYR")


async def test_daily_cost_rows_switch_to_hourly_without_double_counting(
    coordinator, coordinator_module, stats_env
):
    """Cost used to be imported as one row per day at 00:00 holding the whole day.

    Switching to hourly must redo that last day from the sum before it, or
    its 01:00-23:00 hours would be added on top of the whole-day row.
    """
    tz = coordinator_module.TIMEZONE
    stats_env.stored = [
        _row(coordinator_module, datetime(2026, 11, 26, tzinfo=tz), 100.0),  # whole 26 Nov: 12
        _row(coordinator_module, datetime(2026, 11, 25, tzinfo=tz), 88.0),
    ]
    points = _hours(coordinator_module, (2026, 11, 25), 0.5) + _hours(
        coordinator_module, (2026, 11, 26), 0.5
    )

    await _insert(coordinator, points)

    imported = stats_env.imported
    assert imported[0]["start"] == datetime(2026, 11, 26, tzinfo=tz)  # 25 Nov left alone
    assert len(imported) == 24
    assert imported[0]["sum"] == 88.5
    assert imported[-1]["sum"] == 100.0  # same day total as the daily row: no double count


async def test_hourly_rows_redo_the_last_day_consistently(
    coordinator, coordinator_module, stats_env
):
    """With hourly rows already there, redoing the last day reproduces its sums."""
    tz = coordinator_module.TIMEZONE
    last_day = datetime(2026, 11, 26, tzinfo=tz)
    stats_env.stored = [
        _row(coordinator_module, last_day + timedelta(hours=h), 176.0 + h + 1)
        for h in reversed(range(24))
    ] + [_row(coordinator_module, last_day - timedelta(hours=1), 176.0)]
    points = _hours(coordinator_module, (2026, 11, 26), 1.0) + _hours(
        coordinator_module, (2026, 11, 27), 1.0, count=3
    )

    await _insert(coordinator, points)

    by_start = {row["start"]: row["sum"] for row in stats_env.imported}
    assert by_start[last_day + timedelta(hours=23)] == 200.0  # unchanged
    assert by_start[datetime(2026, 11, 27, 2, tzinfo=tz)] == 203.0


async def test_last_day_missing_from_fetch_continues_after_it(
    coordinator, coordinator_module, stats_env
):
    """Redoing a day the fetch doesn't cover would drop it from the sum."""
    tz = coordinator_module.TIMEZONE
    stats_env.stored = [
        _row(coordinator_module, datetime(2026, 11, 26, tzinfo=tz), 100.0),
        _row(coordinator_module, datetime(2026, 11, 25, tzinfo=tz), 88.0),
    ]
    points = _hours(coordinator_module, (2026, 11, 27), 0.5, count=2)

    await _insert(coordinator, points)

    assert [row["sum"] for row in stats_env.imported] == [100.5, 101.0]


async def test_first_import_takes_everything(coordinator, coordinator_module, stats_env):
    points = _hours(coordinator_module, (2026, 11, 25), 0.5, count=4)

    await _insert(coordinator, points)

    assert [row["sum"] for row in stats_env.imported] == [0.5, 1.0, 1.5, 2.0]
