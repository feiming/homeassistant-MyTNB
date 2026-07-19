"""Config flow for the Tenaga Nasional integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import NumberSelector, NumberSelectorConfig

from .api import MyTNBAuthError, MyTNBClient, MyTNBConnectionError, MyTNBError
from .const import (
    CONF_SMARTMETER_URL,
    CONF_UPDATE_INTERVAL_HOURS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_SMARTMETER_URL): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class MyTNBConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tenaga Nasional."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> MyTNBOptionsFlow:
        """Get the options flow for this handler."""
        return MyTNBOptionsFlow()

    async def _async_validate(self, data: dict[str, Any]) -> tuple[str | None, dict[str, str]]:
        """Try the full login flow; returns (sdpudcid, errors)."""
        # detach() (not close()) releases the session without closing the
        # shared HA connector it borrows; HA also detaches it at shutdown.
        session = async_create_clientsession(self.hass)
        try:
            client = MyTNBClient(
                session,
                data[CONF_USERNAME],
                data[CONF_PASSWORD],
                data[CONF_SMARTMETER_URL],
            )
        except MyTNBError:
            session.detach()
            return None, {CONF_SMARTMETER_URL: "invalid_url"}

        try:
            sdpudcid = await client.async_authenticate()
        except MyTNBAuthError:
            return None, {"base": "invalid_auth"}
        except MyTNBConnectionError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected exception validating credentials")
            return None, {"base": "unknown"}
        finally:
            session.detach()
        return sdpudcid, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            sdpudcid, errors = await self._async_validate(user_input)
            if not errors:
                await self.async_set_unique_id(sdpudcid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"MyTNB ({user_input[CONF_USERNAME]})", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle reauthentication when credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password and revalidate."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**reauth_entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            _, errors = await self._async_validate(data)
            if not errors:
                return self.async_update_reload_and_abort(reauth_entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            description_placeholders={CONF_USERNAME: reauth_entry.data[CONF_USERNAME]},
            errors=errors,
        )


class MyTNBOptionsFlow(OptionsFlow):
    """Handle options for the Tenaga Nasional integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user adjust the polling interval."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL_HOURS, DEFAULT_UPDATE_INTERVAL_HOURS
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_UPDATE_INTERVAL_HOURS, default=current): NumberSelector(
                    NumberSelectorConfig(min=1, max=24, step=1)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
