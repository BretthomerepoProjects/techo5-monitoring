"""Match an SSH switch to its encrypted ESPHome runtime and exact ssh_keys action."""
import asyncio
import asyncssh
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from .bootstrap import BootstrapError, server_host_key


def target_for_switch(hass, entity_id, host):
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or entity.platform != 'esphome' or entity.domain != 'switch' or entity.disabled:
        raise BootstrapError('invalid_target')
    # Do not infer a security control from a user-renamed entity_id.
    if (entity.original_name or '').strip().casefold() not in ('ssh', 'ssh access', 'enable ssh'):
        raise BootstrapError('invalid_target')
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    runtime = getattr(entry, 'runtime_data', None)
    if entry is None or entry.domain != 'esphome' or runtime is None or not runtime.available or runtime.device_info is None:
        raise BootstrapError('invalid_target')
    if str(runtime.client.connected_address) != host:
        raise BootstrapError('target_mismatch')
    if not runtime.client.noise_psk:
        raise BootstrapError('encryption_required')
    service = runtime.device_info.name.replace('-', '_') + '_ssh_keys'
    if not hass.services.has_service('esphome', service):
        raise BootstrapError('missing_action')
    state = hass.states.get(entity_id)
    if state is None or state.state not in ('on', 'off'):
        raise BootstrapError('invalid_target')
    return service


def switch_choices(hass, host):
    choices = {}
    for entity in er.async_get(hass).entities.values():
        try:
            target_for_switch(hass, entity.entity_id, host)
        except BootstrapError:
            continue
        choices[entity.entity_id] = entity.name or entity.original_name or entity.entity_id
    return choices


class HABootstrap:
    def __init__(self, hass, settings, switch, user_id):
        self.hass, self.settings, self.switch = hass, settings, switch
        self.context = Context(user_id=user_id)
        self.action = None

    async def validate(self):
        self.action = target_for_switch(self.hass, self.switch, self.settings['host'])

    async def enable_ssh(self):
        await self.hass.services.async_call('switch', 'turn_on', {'entity_id': self.switch},
                                            blocking=True, context=self.context)

    async def push_keys(self, public_keys):
        # Revalidate IP, encryption and action immediately before the key-list write.
        await self.validate()
        await self.hass.services.async_call('esphome', self.action, {'keys': public_keys},
                                            blocking=True, context=self.context)

    async def wait_listening(self):
        for attempt in range(12):
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(
                    self.settings['host'], self.settings['port']), timeout=1)
                writer.close()
                await writer.wait_closed()
                return
            except (OSError, TimeoutError):
                await asyncio.sleep(1)
        raise BootstrapError('ssh_not_listening')

    async def server_key(self):
        # Public handshake only; options/file handling runs in the executor.
        return await server_host_key(self.settings['host'], self.settings['port'],
                                     self.hass.async_add_executor_job)
