"""Satellite and Node Exporter observations from explicit actions and read-only checks."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .core import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SatelliteStatus(entry), NodeExporterStatus(entry), LogStreamerStatus(entry), CpuPolicyStatus(entry)])


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
            "manufacturer": "TECHO5", "model": "Linux satellite"}

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, DOMAIN + self.entry.entry_id, self.async_write_ha_state))

    @property
    def native_value(self):
        return self.hass.data[DOMAIN]["entries"][self.entry.entry_id]["status"]

    @property
    def extra_state_attributes(self):
        runtime = self.hass.data[DOMAIN]["entries"][self.entry.entry_id]
        return {"entry_id": self.entry.entry_id, **runtime["result"], **runtime["stream_result"],
                "stream_error": runtime["stream_error"],
                **runtime["stream_recovery"].attributes(runtime["settings"].get("stream_recovery", False)),
                "deployment_enabled": bool(runtime["settings"].get("collector_host")),
                **runtime["recovery"].attributes(runtime["settings"].get("node_recovery", False))}


class NodeExporterStatus(SatelliteStatus):
    _attr_name = "Node Exporter"
    _attr_icon = "mdi:chart-line"

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = entry.entry_id + "_node_exporter"

    @property
    def native_value(self):
        runtime = self.hass.data[DOMAIN]["entries"][self.entry.entry_id]
        return runtime["result"].get("node_state", "unknown")


class LogStreamerStatus(SatelliteStatus):
    _attr_name = "Log streamer"
    _attr_icon = "mdi:text-box-search"

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = entry.entry_id + "_log_streamer"

    @property
    def native_value(self):
        return self.hass.data[DOMAIN]["entries"][self.entry.entry_id]["stream_result"].get("stream_state", "unknown")


class CpuPolicyStatus(SatelliteStatus):
    _attr_name = "CPU core policy"
    _attr_icon = "mdi:cpu-64-bit"

    def __init__(self, entry):
        super().__init__(entry)
        self._attr_unique_id = entry.entry_id + "_cpu_policy"

    @property
    def native_value(self):
        return self.hass.data[DOMAIN]["entries"][self.entry.entry_id]["cpu_result"].get("cpu_policy_state", "unknown")

    @property
    def extra_state_attributes(self):
        runtime = self.hass.data[DOMAIN]["entries"][self.entry.entry_id]
        return {**runtime["cpu_result"], "cpu_error": runtime["cpu_error"],
                "enabled": runtime["settings"].get("cpu_all_cores", False),
                "restore_pending": runtime["settings"].get("cpu_restore_pending", False),
                "retry_failures": runtime["cpu_retry"].failures}
