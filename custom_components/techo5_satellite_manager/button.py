"""Run the existing administrator-only check from the device page."""
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from .core import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CheckSatelliteButton(entry)])


class CheckSatelliteButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Check satellite"
    _attr_icon = "mdi:check-network"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = entry.entry_id + "_check"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    async def async_press(self):
        await self.hass.services.async_call(
            DOMAIN, "check", {"entry_id": self.entry.entry_id},
            blocking=True, context=self._context)
