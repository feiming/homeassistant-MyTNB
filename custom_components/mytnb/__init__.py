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
    # HA auto-detaches this session on HA shutdown or when this entry is
    # unloaded/reloaded, so we must not close it ourselves.
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
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: MyTNBConfigEntry) -> None:
    """Reload the entry so option changes (e.g. polling interval) take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MyTNBConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
