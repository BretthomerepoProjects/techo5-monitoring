"""One status sensor per satellite, updated only on an administrator check."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .core import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SatelliteStatus(entry)])


class SatelliteStatus(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Satellite manager status"
    _attr_icon = "mdi:satellite-uplink"

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = entry.entry_id + "_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title,
            "manufacturer": "TECHO5", "model": "Satellite (manual checks)"}

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, DOMAIN + self.entry.entry_id, self.async_write_ha_state))

    @property
    def native_value(self):
        return self.hass.data[DOMAIN]["entries"][self.entry.entry_id]["status"]

    @property
    def extra_state_attributes(self):
        runtime = self.hass.data[DOMAIN]["entries"][self.entry.entry_id]
        return {"entry_id": self.entry.entry_id, "deployment_enabled": False,
                **runtime["result"]}
