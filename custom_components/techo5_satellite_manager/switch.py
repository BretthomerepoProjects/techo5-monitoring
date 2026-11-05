"""Persistent per-satellite recovery intent; turning off does not stop a process."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .core import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([NodeRecoverySwitch(entry), StreamRecoverySwitch(entry)])


class NodeRecoverySwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Automatic Node Exporter recovery"
    _attr_icon = "mdi:restart-alert"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = entry.entry_id + "_node_recovery"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, DOMAIN + self.entry.entry_id, self.async_write_ha_state))

    @property
    def is_on(self):
        return self.hass.data[DOMAIN]["entries"][self.entry.entry_id]["settings"].get("node_recovery", False)

    async def async_turn_on(self, **kwargs):
        await self.hass.services.async_call(DOMAIN, "set_node_recovery",
            {"entry_id": self.entry.entry_id, "enabled": True}, blocking=True, context=self._context)

    async def async_turn_off(self, **kwargs):
        await self.hass.services.async_call(DOMAIN, "set_node_recovery",
            {"entry_id": self.entry.entry_id, "enabled": False}, blocking=True, context=self._context)


class StreamRecoverySwitch(NodeRecoverySwitch):
    _attr_name = "Automatic log streamer recovery"

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = entry.entry_id + "_stream_recovery"

    @property
    def is_on(self):
        return self.hass.data[DOMAIN]["entries"][self.entry.entry_id]["settings"].get("stream_recovery", False)

    async def async_turn_on(self, **kwargs):
        await self.hass.services.async_call(DOMAIN, "set_stream_recovery",
            {"entry_id": self.entry.entry_id, "enabled": True}, blocking=True, context=self._context)

    async def async_turn_off(self, **kwargs):
        await self.hass.services.async_call(DOMAIN, "set_stream_recovery",
            {"entry_id": self.entry.entry_id, "enabled": False}, blocking=True, context=self._context)
