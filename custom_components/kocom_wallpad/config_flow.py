"""Config flow for Kocom Wallpad."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import DEFAULT_TCP_PORT, DOMAIN


class KocomConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow for Kocom Wallpad."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host: str = user_input[CONF_HOST]
            port: int | None = user_input[CONF_PORT]

            if not host.strip():
                errors[CONF_HOST] = "invalid_host"

            # Preserve serial paths and existing host/unique ID spelling.
            if host.startswith("/"):
                port = None
            elif type(port) is not int or not 1 <= port <= 65535:
                errors[CONF_PORT] = "invalid_port"

            if not errors:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=host,
                    data={CONF_HOST: host, CONF_PORT: port}
                )

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_TCP_PORT): int,
        })
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
