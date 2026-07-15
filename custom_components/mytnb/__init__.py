"""The Tenaga Nasional integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import MyTNBClient, MyTNBError
from .const import CONF_SMARTMETER_URL
from .coordinator import MyTNBCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type MyTNBConfigEntry = ConfigEntry[MyTNBCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MyTNBConfigEntry) -> bool:
    """Set up Tenaga Nasional from a config entry."""
    # A dedicated (non-shared) session so the portal's cookies stay isolated.
    session = async_create_clientsession(hass)
    try:
        client = MyTNBClient(
            session,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            entry.data[CONF_SMARTMETER_URL],
        )
    except MyTNBError as err:
        # A malformed smartmeter URL cannot heal on retry.
        raise ConfigEntryError(str(err)) from err

    coordinator = MyTNBCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Setup is retried with a fresh session; don't leak this one.
        await session.close()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(session.close)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyTNBConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
