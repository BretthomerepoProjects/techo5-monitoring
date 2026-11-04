"""Administrator-only device-page controls."""
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from .core import DOMAIN

CONTROLS = (
    ("check", "Check satellite", "mdi:check-network"),
    ("deploy_node", "Deploy and start Node Exporter", "mdi:download-network"),
    ("start_node", "Start Node Exporter", "mdi:play"),
    ("stop_node", "Stop Node Exporter", "mdi:stop"),
    ("uninstall_node", "Uninstall Node Exporter", "mdi:delete"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SatelliteButton(entry, *control) for control in CONTROLS])


class SatelliteButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, action, name, icon):
        self.entry = entry
        self.action = action
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = entry.entry_id + "_" + action
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_press(self):
        await self.hass.services.async_call(
            DOMAIN, self.action, {"entry_id": self.entry.entry_id},
            blocking=True, context=self._context)
