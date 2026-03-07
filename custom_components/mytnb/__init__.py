"""The Tenaga Nasional integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import MyTNBAPI

PLATFORMS: list[Platform] = [Platform.SENSOR]

type MyTNBConfigEntry = ConfigEntry[MyTNBAPI]


async def async_setup_entry(hass: HomeAssistant, entry: MyTNBConfigEntry) -> bool:
    """Set up Tenaga Nasional from a config entry."""
    api = MyTNBAPI(
        entry.data["username"],
        entry.data["password"],
        entry.data["smartmeter_url"],
        entry.data["sdpudcid"],
    )

    # Store API instance in runtime_data
    entry.runtime_data = api

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyTNBConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
