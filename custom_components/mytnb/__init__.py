"""The Tenaga Nasional integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .api import MyTNBAPI
from .const import CONF_SMARTMETER_URL

PLATFORMS: list[Platform] = [Platform.SENSOR]

type MyTNBConfigEntry = ConfigEntry[MyTNBAPI]

TO_REDACT = [
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_SMARTMETER_URL,
]


async def async_setup_entry(hass: HomeAssistant, entry: MyTNBConfigEntry) -> bool:
    """Set up Tenaga Nasional from a config entry."""
    api = MyTNBAPI(
        entry.data["username"],
        entry.data["password"],
        entry.data["smartmeter_url"],
    )

    # Store API instance in runtime_data
    entry.runtime_data = api

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyTNBConfigEntry) -> bool:
    """Unload a config entry."""
    # Close the API session when unloading
    if api := entry.runtime_data:
        await api.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MyTNBConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.
    
    Note: All sensitive data (passwords, usernames, URLs) is redacted
    using async_redact_data to prevent credential exposure.
    """
    api = entry.runtime_data
    
    # Gather diagnostic information
    # Following Home Assistant's recommended pattern from:
    # https://developers.home-assistant.io/docs/core/integration_diagnostics/
    diagnostics: dict[str, Any] = {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "entry_options": async_redact_data(entry.options, TO_REDACT),
        "entry_info": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "unique_id": entry.unique_id,
            "state": entry.state.value if entry.state else None,
        },
    }
    
    # Add API state information if available
    # Note: We never expose credentials from the API instance
    if api:
        api_info: dict[str, Any] = {
            "session_active": api._session is not None and not api._session.closed if api._session else False,
            "sdpudcid_cached": api._sdpudcid is not None,
        }
        
        # Only include cached sdpudcid to avoid making API calls during diagnostics
        # sdpudcid is a device identifier (not a credential) but included for troubleshooting
        if api._sdpudcid:
            api_info["sdpudcid"] = api._sdpudcid
        
        diagnostics["api"] = api_info
    
    return diagnostics
