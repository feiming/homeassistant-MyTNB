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
