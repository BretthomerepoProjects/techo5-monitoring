"""Manual-only TECHO5 Satellite Manager prototype."""
import asyncio
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.exceptions import HomeAssistantError
from .core import DOMAIN, Checker, CheckError
from .ssh import probe

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass, entry):
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {"entries": {}, "checker": Checker(lambda settings, cmd: probe(hass, settings, cmd))}

        async def check(call):
            state = hass.data[DOMAIN]
            entry_id = call.data["entry_id"]
            runtime = state["entries"].get(entry_id)
            if runtime is None:
                raise HomeAssistantError("Unknown satellite entry")
            if state["checker"].busy:
                raise HomeAssistantError("Another satellite check is running")
            task = asyncio.current_task()
            runtime["task"] = task
            runtime["status"] = "checking"
            async_dispatcher_send(hass, DOMAIN + entry_id)
            try:
                result = await state["checker"].check(runtime["settings"])
                runtime["result"] = result
                runtime["status"] = "checked"
            except CheckError as exc:
                runtime["result"] = {}  # Never present stale success as current evidence.
                runtime["status"] = str(exc)
                raise HomeAssistantError("Satellite check failed: " + str(exc)) from None
            except asyncio.CancelledError:
                runtime["status"] = "cancelled"
                raise
            finally:
                runtime["task"] = None
                async_dispatcher_send(hass, DOMAIN + entry_id)

        async_register_admin_service(hass, DOMAIN, "check", check,
                                     schema=vol.Schema({vol.Required("entry_id"): str}))
    hass.data[DOMAIN]["entries"][entry.entry_id] = {
        "settings": dict(entry.data), "status": "not_checked", "result": {}, "task": None}
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        hass.data[DOMAIN]["entries"].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]["entries"]:
            hass.services.async_remove(DOMAIN, "check")
            hass.data.pop(DOMAIN)
        raise
    return True


async def async_unload_entry(hass, entry):
    state = hass.data[DOMAIN]
    runtime = state["entries"][entry.entry_id]
    task = runtime["task"]
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    state["entries"].pop(entry.entry_id)
    if not state["entries"]:
        hass.services.async_remove(DOMAIN, "check")
        hass.data.pop(DOMAIN)
    return True
