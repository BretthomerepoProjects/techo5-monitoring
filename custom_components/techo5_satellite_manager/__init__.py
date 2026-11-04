"""Explicit Node Exporter controls with read-only periodic monitoring."""
import asyncio
from datetime import timedelta
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.exceptions import HomeAssistantError
from .core import DOMAIN, Checker, CheckError
from .node_exporter import NodeManager
from .ssh import probe

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]
SERVICES = ("check", "deploy_node", "start_node", "stop_node", "uninstall_node")


async def run_operation(hass, entry_id, operation):
    runtime = hass.data[DOMAIN]["entries"].get(entry_id)
    if runtime is None:
        raise HomeAssistantError("Unknown satellite entry")
    if runtime["task"] is not None:
        raise HomeAssistantError("This satellite is busy")
    runtime["task"] = asyncio.current_task()
    runtime["status"] = "checking" if operation == "check" else operation
    async_dispatcher_send(hass, DOMAIN + entry_id)
    try:
        if operation == "check":
            result = await runtime["checker"].check(runtime["settings"])
            if runtime["settings"].get("collector_host"):
                result.update(await runtime["node"].execute(runtime["settings"], "inspect"))
            else:
                result["node_state"] = "not_configured"
            runtime["result"] = result
        else:
            runtime["result"] = await runtime["node"].execute(
                runtime["settings"], operation.removesuffix("_node"))
        runtime["status"] = "checked" if operation == "check" else "completed"
    except CheckError as exc:
        runtime["result"] = {"node_state": "unreachable" if str(exc) in {
            "connection_or_credentials_failed", "timeout", "node_timeout_check_before_retry",
            "host_key_rejected", "authentication_failed"} else "failed"}
        runtime["status"] = str(exc)
        raise HomeAssistantError("Satellite operation failed: " + str(exc)) from None
    except asyncio.CancelledError:
        runtime["result"] = {"node_state": "unknown"}
        runtime["status"] = "cancelled_check_before_retry"
        raise
    finally:
        runtime["task"] = None
        async_dispatcher_send(hass, DOMAIN + entry_id)


async def async_setup_entry(hass, entry):
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {"entries": {}}
        for name in SERVICES:
            async def handler(call, operation=name):
                await run_operation(hass, call.data["entry_id"], operation)
            async_register_admin_service(hass, DOMAIN, name, handler,
                schema=vol.Schema({vol.Required("entry_id"): str}))
    transport = lambda settings, command, payload=None: probe(hass, settings, command, payload)
    runtime = {
        "settings": {**entry.data, **entry.options}, "status": "not_checked", "result": {},
        "task": None, "checker": Checker(transport),
        "node": NodeManager(transport, hass.async_add_executor_job),
    }
    hass.data[DOMAIN]["entries"][entry.entry_id] = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        async def monitor(now):
            if runtime["task"] is not None:
                return
            try:
                await run_operation(hass, entry.entry_id, "check")
            except HomeAssistantError:
                pass  # Sanitized failure is shown on the status sensor; no noisy raw logs.
        # No startup provisioning, deployment, or recovery. Polls are read-only.
        if runtime["settings"].get("collector_host"):
            entry.async_on_unload(async_track_time_interval(hass, monitor, timedelta(seconds=60)))
        entry.async_on_unload(entry.add_update_listener(reload_entry))
    except Exception:
        hass.data[DOMAIN]["entries"].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]["entries"]:
            for name in SERVICES:
                hass.services.async_remove(DOMAIN, name)
            hass.data.pop(DOMAIN)
        raise
    return True


async def reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


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
        for name in SERVICES:
            hass.services.async_remove(DOMAIN, name)
        hass.data.pop(DOMAIN)
    return True
