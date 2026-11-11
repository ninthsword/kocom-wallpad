"""Config flow for Kocom Wallpad."""

from __future__ import annotations

from typing import Any, cast

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import CONF_CUTOFF_RELEASE_AFTER_LIGHT, DEFAULT_TCP_PORT, DOMAIN


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

            cutoff_release = user_input.get(CONF_CUTOFF_RELEASE_AFTER_LIGHT, False)
            if type(cutoff_release) is not bool:
                errors[CONF_CUTOFF_RELEASE_AFTER_LIGHT] = "invalid_cutoff_release"

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
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_CUTOFF_RELEASE_AFTER_LIGHT: cutoff_release,
                    }
                )

        # Core 2026.11 types forms with probatio.Schema; voluptuous stays valid at runtime.
        schema = cast(Any, vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_TCP_PORT): int,
            vol.Required(CONF_CUTOFF_RELEASE_AFTER_LIGHT, default=False): bool,
        }))
        if user_input is not None:
            # Show the rejected values again so the user corrects them instead of retyping both.
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change only the optional supplemental send for this entry."""
        entry = self._get_reconfigure_entry()
        stored = entry.data.get(CONF_CUTOFF_RELEASE_AFTER_LIGHT)
        current = stored is True
        errors: dict[str, str] = {}
        if user_input is not None:
            value = user_input.get(CONF_CUTOFF_RELEASE_AFTER_LIGHT, current)
            if type(value) is not bool:
                errors[CONF_CUTOFF_RELEASE_AFTER_LIGHT] = "invalid_cutoff_release"
            else:
                # Native equality treats numeric 0/1 as booleans. Remove only
                # that malformed equivalent so the native merge stores a bool.
                if type(stored) is not bool and stored == value:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={key: item for key, item in entry.data.items()
                              if key != CONF_CUTOFF_RELEASE_AFTER_LIGHT},
                    )
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_CUTOFF_RELEASE_AFTER_LIGHT: value},
                    reload_even_if_entry_is_unchanged=False,
                )
        schema = cast(Any, vol.Schema({
            vol.Required(CONF_CUTOFF_RELEASE_AFTER_LIGHT, default=current): bool,
        }))
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)
