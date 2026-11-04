"""Store addresses and credential filenames; never private key material."""
import voluptuous as vol
from homeassistant import config_entries
from .core import DOMAIN, validate_host, credential_paths


class SatelliteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_input["host"] = validate_host(user_input["host"])
                await self.hass.async_add_executor_job(
                    credential_paths, self.hass.config.config_dir, user_input["key_name"])
            except (OSError, ValueError):
                errors["base"] = "invalid_settings"
            else:
                await self.async_set_unique_id(f'{user_input["host"]}:{user_input["port"]}')
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required("name", default="Satellite"): str,
            vol.Required("host"): str,
            vol.Required("port", default=22): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required("key_name", default="satellite_manager"): str,
        }))
