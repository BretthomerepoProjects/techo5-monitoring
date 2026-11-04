"""Optional encrypted ESPHome bootstrap followed by a verified SSH test."""
import asyncio
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .core import DOMAIN, validate_host, CheckError
from .bootstrap import BootstrapError, public_identity, host_trust, prepare
from .ha_bootstrap import HABootstrap, switch_choices
from .ssh import probe


class BootstrapFlow:
    """Transient key material is kept in the flow only, never in config entries."""
    async def async_step_bootstrap(self, user_input=None):
        errors = {}
        choices = switch_choices(self.hass, self._settings['host'])
        if not choices:
            return self.async_abort(reason='no_bootstrap_device')
        if user_input is not None:
            try:
                if not user_input.get('confirm_replace', False):
                    raise BootstrapError('confirmation_required')
                public_keys, fingerprint = await self.hass.async_add_executor_job(
                    public_identity, self.hass.config.config_dir, self._settings['key_name'],
                    user_input.get('additional_keys', ''))
                backend = HABootstrap(self.hass, self._settings, user_input['ssh_switch'],
                                      self.context.get('user_id'))
                self._server_key = await prepare(backend, public_keys, confirmed=True)
                trusted = await self.hass.async_add_executor_job(
                    host_trust, self.hass.config.config_dir, self._settings['host'],
                    self._settings['port'], self._server_key)
                # Clear public key lists after use; only a key filename is persisted.
                del public_keys
                return await self.async_step_verify() if trusted else await self.async_step_trust()
            except BootstrapError as exc:
                errors['base'] = str(exc)
            except (OSError, ValueError, TimeoutError):
                errors['base'] = 'bootstrap_failed'
            except Exception:
                errors['base'] = 'bootstrap_failed'
        return self.async_show_form(step_id='bootstrap', errors=errors,
            description_placeholders={'host': self._settings['host'], 'fingerprint': self._key_fingerprint},
            data_schema=vol.Schema({
                vol.Required('ssh_switch'): vol.In(choices),
                vol.Optional('additional_keys', default=''): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)),
                vol.Required('confirm_replace', default=False): bool,
            }))

    async def async_step_trust(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get('trust_host_key', False):
                errors['base'] = 'confirmation_required'
            else:
                try:
                    # host_trust's keyword-only parameter is intentionally supplied inside
                    # the executor; do not perform credential-file I/O on HA's event loop.
                    from functools import partial
                    await self.hass.async_add_executor_job(partial(
                        host_trust, self.hass.config.config_dir, self._settings['host'],
                        self._settings['port'], self._server_key, accept=True))
                    return await self.async_step_verify()
                except BootstrapError as exc:
                    errors['base'] = str(exc)
                except Exception:
                    errors['base'] = 'bootstrap_failed'
        return self.async_show_form(step_id='trust', errors=errors,
            description_placeholders={'host': self._settings['host'],
                                      'fingerprint': self._server_key.get_fingerprint()},
            data_schema=vol.Schema({vol.Required('trust_host_key', default=False): bool}))

    async def async_step_verify(self, user_input=None):
        errors = {}
        try:
            async with asyncio.timeout(20):
                # No mutation here. Failure retries test SSH only, never key provisioning.
                await probe(self.hass, self._settings, "printf 'ssh_ready\\n'")
            return self.finish()
        except CheckError as exc:
            errors['base'] = str(exc)
        except (OSError, ValueError, TimeoutError):
            errors['base'] = 'connection_or_credentials_failed'
        return self.async_show_form(step_id='verify', errors=errors,
                                    description_placeholders={'host': self._settings['host']},
                                    data_schema=vol.Schema({}))


class SatelliteConfigFlow(BootstrapFlow, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SatelliteOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self._settings = {k: user_input[k] for k in ('name', 'host', 'port', 'key_name')}
                self._settings['host'] = validate_host(self._settings['host'])
                _, self._key_fingerprint = await self.hass.async_add_executor_job(
                    public_identity, self.hass.config.config_dir, self._settings['key_name'])
            except (OSError, ValueError, BootstrapError):
                errors['base'] = 'invalid_settings'
            else:
                await self.async_set_unique_id(f"{self._settings['host']}:{self._settings['port']}")
                self._abort_if_unique_id_configured()
                if user_input.get('prepare_ssh', True):
                    return await self.async_step_bootstrap()
                return await self.async_step_verify()
        return self.async_show_form(step_id='user', errors=errors, data_schema=vol.Schema({
            vol.Required('name', default='Satellite'): str,
            vol.Required('host'): str,
            vol.Required('port', default=22): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required('key_name', default='satellite_manager'): str,
            vol.Required('prepare_ssh', default=True): bool,
        }))

    def finish(self):
        return self.async_create_entry(title=self._settings['name'], data=self._settings)


class SatelliteOptionsFlow(BootstrapFlow, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        self._settings = dict(self.config_entry.data)
        try:
            _, self._key_fingerprint = await self.hass.async_add_executor_job(
                public_identity, self.hass.config.config_dir, self._settings['key_name'])
        except (BootstrapError, OSError, ValueError):
            return self.async_abort(reason='invalid_key')
        return await self.async_step_bootstrap()

    def finish(self):
        return self.async_create_entry(title='', data=dict(self.config_entry.options))
