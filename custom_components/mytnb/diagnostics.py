"""Diagnostics support for the Tenaga Nasional integration."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from . import MyTNBConfigEntry
from .const import CONF_SMARTMETER_URL

TO_REDACT = [CONF_USERNAME, CONF_PASSWORD, CONF_SMARTMETER_URL]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MyTNBConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    def summarize(points: list) -> dict[str, Any]:
        return {
            "points": len(points),
            "first": points[0].start.isoformat() if points else None,
            "last": points[-1].start.isoformat() if points else None,
        }

    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "sdpudcid": coordinator.client.sdpudcid,
        "last_update_success": coordinator.last_update_success,
        "usage": summarize(data.usage) if data else None,
        "cost": summarize(data.cost) if data else None,
    }
