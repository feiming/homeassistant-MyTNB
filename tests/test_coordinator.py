"""Tests for the coordinator: TNB's nightly maintenance window and the bill.

Home Assistant isn't installed for these tests, so the handful of names
coordinator.py imports from it are replaced with minimal stand-ins. That is
enough to exercise the coordinator's own decisions (fetch or defer, and what
polling interval to ask for); it does not exercise Home Assistant's scheduler.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import date, datetime, timedelta
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
        self.async_update_listeners = MagicMock()


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
def bill(coordinator_module):
    return coordinator_module.BillData(nem_balance_kwh=970.0)


@pytest.fixture
def coordinator(coordinator_module, fresh_data, bill):
    """A coordinator with the default 6-hour interval and a fake client."""
    client = SimpleNamespace(
        sdpudcid="40000000000020022594",
        async_get_data=AsyncMock(return_value=fresh_data),
        async_get_bill=AsyncMock(return_value=bill),
    )
    entry = SimpleNamespace(options={})
    coord = coordinator_module.MyTNBCoordinator(MagicMock(), entry, client)
    coord._async_insert_statistics = AsyncMock()
    return coord


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
    client = SimpleNamespace(
        sdpudcid="1",
        async_get_data=AsyncMock(return_value=fresh_data),
        async_get_bill=AsyncMock(return_value=None),
    )
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


def make_coordinator(coordinator_module, fresh_data, bill, **options):
    client = SimpleNamespace(
        sdpudcid="1",
        async_get_data=AsyncMock(return_value=fresh_data),
        async_get_bill=AsyncMock(return_value=bill),
    )
    coord = coordinator_module.MyTNBCoordinator(
        MagicMock(), SimpleNamespace(options=options), client
    )
    coord._async_insert_statistics = AsyncMock()
    return coord


async def test_first_poll_fetches_bill(coordinator, bill, set_now):
    set_now(2026, 11, 16, 14, 0)

    data = await coordinator._async_update_data()

    assert data.bill is bill
    coordinator.client.async_get_bill.assert_awaited_once()


async def test_bill_not_refetched_before_next_check(coordinator, coordinator_module, bill, set_now):
    """Default: the 1st of each month at 09:00. Polls before then reuse the bill.

    The fixture bill has no bill date, so a fetch since the 1st counts as current.
    """
    tz = coordinator_module.TIMEZONE
    now = set_now(2026, 11, 16, 14, 0)
    await coordinator._async_update_data()
    assert coordinator.next_bill_check(now) == datetime(2026, 12, 1, 9, 0, tzinfo=tz)

    set_now(2026, 11, 17, 9, 0)
    data = await coordinator._async_update_data()
    set_now(2026, 12, 1, 8, 59)
    await coordinator._async_update_data()

    assert data.bill is bill
    assert coordinator.client.async_get_bill.await_count == 1

    now = set_now(2026, 12, 1, 9, 0)
    await coordinator._async_update_data()
    assert coordinator.client.async_get_bill.await_count == 2
    assert coordinator.next_bill_check(now) == datetime(2027, 1, 1, 9, 0, tzinfo=tz)


async def test_bill_checked_daily_until_new_bill(coordinator_module, fresh_data, set_now):
    """From the chosen day, check daily until a bill dated near it arrives."""
    tz = coordinator_module.TIMEZONE
    BillData = coordinator_module.BillData
    november = BillData(bill_date=date(2026, 11, 2), nem_balance_kwh=970.0)
    december = BillData(bill_date=date(2026, 12, 4), nem_balance_kwh=990.0)
    coord = make_coordinator(
        coordinator_module,
        fresh_data,
        november,
        **{
            coordinator_module.CONF_BILL_DAY: 3.0,  # NumberSelector gives floats
            coordinator_module.CONF_BILL_TIME: "06:30:00",
        },
    )
    get_bill = coord.client.async_get_bill

    # Startup: November's bill (dated the 2nd) counts for the cycle from 3 Nov.
    now = set_now(2026, 11, 16, 14, 0)
    coord.data = await coord._async_update_data()
    coord.async_update_listeners = MagicMock()
    assert coord.next_bill_check(now) == datetime(2026, 12, 3, 6, 30, tzinfo=tz)

    # 3 Dec: still November's bill, so it's checked again the next day...
    now = set_now(2026, 12, 3, 6, 30)
    await coord.async_refresh_bill_if_due()
    assert get_bill.await_count == 2
    assert coord.next_bill_check(now) == datetime(2026, 12, 4, 6, 30, tzinfo=tz)
    set_now(2026, 12, 3, 20, 0)
    await coord._async_update_data()
    assert get_bill.await_count == 2

    # ...when December's (late) bill is in; then nothing until 3 Jan.
    get_bill.return_value = december
    now = set_now(2026, 12, 4, 6, 30)
    await coord.async_refresh_bill_if_due()
    assert coord.data.bill is december
    assert coord.next_bill_check(now) == datetime(2027, 1, 3, 6, 30, tzinfo=tz)
    set_now(2026, 12, 20, 6, 30)
    await coord.async_refresh_bill_if_due()
    assert get_bill.await_count == 3


async def test_bill_day_before_bill_arrives(coordinator_module, fresh_data, set_now):
    """Last month's bill doesn't count, so a day before arrival still works."""
    tz = coordinator_module.TIMEZONE
    october = coordinator_module.BillData(bill_date=date(2026, 10, 2))
    coord = make_coordinator(coordinator_module, fresh_data, october)
    coord.bill = october
    coord.bill_fetched_at = datetime(2026, 10, 20, 9, 0, tzinfo=tz)

    assert coord.bill_due(datetime(2026, 11, 1, 9, 0, tzinfo=tz))
    coord.bill_fetched_at = datetime(2026, 11, 1, 9, 0, tzinfo=tz)
    assert coord.next_bill_check(datetime(2026, 11, 1, 9, 0, tzinfo=tz)) == datetime(
        2026, 11, 2, 9, 0, tzinfo=tz
    )


async def test_bill_day_past_month_end_uses_last_day(coordinator_module, fresh_data, bill):
    tz = coordinator_module.TIMEZONE
    coord = make_coordinator(
        coordinator_module, fresh_data, bill, **{coordinator_module.CONF_BILL_DAY: 31}
    )

    def next_after_fetch(*args: int) -> datetime:
        coord.bill_fetched_at = datetime(*args, tzinfo=tz)
        return coord.next_bill_check(coord.bill_fetched_at)

    assert next_after_fetch(2027, 1, 31, 10, 0) == datetime(2027, 2, 28, 9, 0, tzinfo=tz)
    assert next_after_fetch(2028, 1, 31, 10, 0) == datetime(2028, 2, 29, 9, 0, tzinfo=tz)
    assert next_after_fetch(2026, 11, 16, 14, 0) == datetime(2026, 11, 30, 9, 0, tzinfo=tz)
    assert next_after_fetch(2026, 12, 31, 10, 0) == datetime(2027, 1, 31, 9, 0, tzinfo=tz)
    assert next_after_fetch(2027, 1, 5, 10, 0) == datetime(2027, 1, 31, 9, 0, tzinfo=tz)


async def test_bill_disabled_never_fetches(coordinator_module, fresh_data, bill, set_now):
    coord = make_coordinator(
        coordinator_module, fresh_data, bill, **{coordinator_module.CONF_BILL_ENABLED: False}
    )
    set_now(2026, 11, 16, 14, 0)

    data = await coord._async_update_data()
    await coord.async_refresh_bill_if_due()

    assert data.bill is None
    coord.client.async_get_bill.assert_not_awaited()


async def test_bill_failure_keeps_previous_bill_and_retries_next_poll(
    coordinator, coordinator_module, bill, set_now
):
    """A failed fetch must not fail the update, drop the last bill, or wait a day."""
    set_now(2026, 11, 16, 14, 0)
    await coordinator._async_update_data()

    set_now(2026, 12, 1, 9, 0)
    coordinator.client.async_get_data.return_value = coordinator_module.SmartMeterData(
        usage=[], cost=[]
    )
    coordinator.client.async_get_bill.side_effect = coordinator_module.MyTNBConnectionError(
        "PrintPDF2 did not return a PDF"
    )
    data = await coordinator._async_update_data()
    assert data.bill is bill

    set_now(2026, 12, 1, 15, 0)  # next poll, same day: still due, so retried
    coordinator.client.async_get_bill.side_effect = None
    await coordinator._async_update_data()
    assert coordinator.client.async_get_bill.await_count == 3


async def test_bill_failure_on_first_poll_still_succeeds(
    coordinator, coordinator_module, set_now
):
    set_now(2026, 11, 16, 14, 0)
    coordinator.client.async_get_bill.side_effect = coordinator_module.MyTNBError("no bill")

    data = await coordinator._async_update_data()

    assert data.bill is None


async def test_scheduled_check_pushes_new_bill_to_sensors(
    coordinator, coordinator_module, fresh_data, set_now
):
    """The time trigger updates sensors without touching the poll schedule."""
    set_now(2026, 11, 16, 14, 0)
    await coordinator._async_update_data()
    coordinator.data = fresh_data
    interval = coordinator.update_interval

    new_bill = coordinator_module.BillData(nem_balance_kwh=1000.0)
    coordinator.client.async_get_bill.return_value = new_bill
    set_now(2026, 12, 1, 9, 0)
    await coordinator.async_refresh_bill_if_due()

    assert coordinator.data.bill is new_bill
    coordinator.async_update_listeners.assert_called_once()
    assert coordinator.update_interval == interval


async def test_scheduled_check_not_due_does_nothing(coordinator, fresh_data, set_now):
    set_now(2026, 11, 16, 14, 0)
    await coordinator._async_update_data()
    coordinator.data = fresh_data

    set_now(2026, 11, 16, 20, 0)
    await coordinator.async_refresh_bill_if_due()

    assert coordinator.client.async_get_bill.await_count == 1
    coordinator.async_update_listeners.assert_not_called()


async def test_bill_never_fetched_in_maintenance_window(coordinator, fresh_data, set_now):
    """Even a first poll inside the window (which still fetches usage) skips the bill."""
    set_now(2026, 11, 16, 23, 45)

    data = await coordinator._async_update_data()

    coordinator.client.async_get_data.assert_awaited_once()
    coordinator.client.async_get_bill.assert_not_awaited()
    assert data.bill is None


async def test_no_smart_meter_is_a_clear_update_failure(
    coordinator, coordinator_module, set_now
):
    set_now(2026, 11, 16, 14, 0)
    coordinator.client.async_get_data.side_effect = coordinator_module.MyTNBNoSmartMeterError(
        "no meter"
    )

    with pytest.raises(_FakeUpdateFailed, match="No smart meter found"):
        await coordinator._async_update_data()
