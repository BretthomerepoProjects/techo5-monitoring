"""Explicit controls and persistent, opt-in Node Exporter recovery."""
import asyncio
from datetime import timedelta
import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.exceptions import HomeAssistantError
from .core import DOMAIN, Checker, CheckError
from .node_exporter import NodeManager, settings_for_node
from .recovery import RecoveryPolicy, recovery_action
from .ssh import probe

PLATFORMS = [Platform.SENSOR, Platform.BUTTON, Platform.SWITCH]
OPERATIONS = ("check", "deploy_node", "start_node", "stop_node", "uninstall_node")
SERVICES = (*OPERATIONS, "set_node_recovery")


def set_recovery(hass, runtime, enabled):
    runtime["settings"]["node_recovery"] = enabled
    entry = runtime["entry"]
    if entry.options.get("node_recovery", False) != enabled:
        hass.config_entries.async_update_entry(entry, options={**entry.options, "node_recovery": enabled})
    async_dispatcher_send(hass, DOMAIN + entry.entry_id)


async def run_operation(hass, entry_id, operation):
    runtime = hass.data[DOMAIN]["entries"].get(entry_id)
    if runtime is None or runtime["unloading"]:
        raise HomeAssistantError("Unknown or unloading satellite entry")
    task = asyncio.current_task()
    stopping = operation in {"stop_node", "uninstall_node"}
    if stopping:
        set_recovery(hass, runtime, False)
    if runtime["stop_task"] is not None:
        raise HomeAssistantError("A stop or uninstall is already pending")
    if runtime["task"] is not None and not stopping:
        raise HomeAssistantError("This satellite is busy")
    if stopping:
        runtime["stop_task"] = task
        try:
            async with asyncio.timeout(220):
                await runtime["idle"].wait()
            if runtime["unloading"]:
                raise HomeAssistantError("Satellite entry is unloading; recovery remains disabled")
        except TimeoutError:
            runtime["stop_task"] = None
            raise HomeAssistantError("Recovery is disabled; operation still busy. Retry Stop.") from None
        except BaseException:
            runtime["stop_task"] = None
            raise
    runtime["task"] = task
    runtime["idle"].clear()
    runtime["status"] = "checking" if operation in {"check", "monitor", "recover_node"} else operation
    automatic = operation in {"monitor", "recover_node"}
    recovery_attempted = False
    try:
        if operation in {"deploy_node", "start_node"}:
            settings_for_node(runtime["settings"])
            set_recovery(hass, runtime, True)
            runtime["recovery"].reset()
        async_dispatcher_send(hass, DOMAIN + entry_id)
        if operation in {"check", "monitor", "recover_node"}:
            result = await runtime["checker"].check(runtime["settings"])
            if runtime["settings"].get("collector_host"):
                result.update(await runtime["node"].execute(runtime["settings"], "inspect"))
            else:
                result["node_state"] = "not_configured"
            if automatic and runtime["settings"].get("node_recovery", False):
                if runtime["recovery_check_failed"]:
                    runtime["recovery"].reset()
                    runtime["recovery_check_failed"] = False
                recovery_attempted = True
                if recovery_action(result) is not None and runtime["recovery"].clock() >= runtime["recovery"].next_attempt:
                    runtime["status"] = "recovering"
                    async_dispatcher_send(hass, DOMAIN + entry_id)
                result.update(await runtime["recovery"].maintain(runtime["node"], runtime["settings"], result))
            runtime["result"] = result
        else:
            runtime["result"] = await runtime["node"].execute(runtime["settings"], operation.removesuffix("_node"))
        runtime["status"] = "checked" if operation in {"check", "monitor", "recover_node"} else "completed"
    except (CheckError, ValueError) as exc:
        code = str(exc) if isinstance(exc, CheckError) else "configure_node_exporter"
        if runtime["settings"].get("node_recovery", False) and operation != "check":
            runtime["recovery"].failed(code)
            runtime["recovery_check_failed"] = automatic and not recovery_attempted
        runtime["result"] = {"node_state": "unreachable" if code in {
            "connection_or_credentials_failed", "timeout", "node_timeout_check_before_retry",
            "host_key_rejected", "authentication_failed", "ssh_connection_refused",
            "ssh_connect_timeout", "ssh_negotiation_failed", "ssh_connection_lost",
            "ssh_network_failed", "ssh_channel_failed"} else "failed"}
        runtime["status"] = code
        raise HomeAssistantError("Satellite operation failed: " + code) from None
    except asyncio.CancelledError:
        runtime["result"] = {"node_state": "unknown"}
        runtime["status"] = "cancelled_check_before_retry"
        raise
    finally:
        runtime["task"] = None
        runtime["idle"].set()
        if runtime["stop_task"] is task:
            runtime["stop_task"] = None
        async_dispatcher_send(hass, DOMAIN + entry_id)


async def async_setup_entry(hass, entry):
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {"entries": {}}
        for name in OPERATIONS:
            async def handler(call, operation=name):
                await run_operation(hass, call.data["entry_id"], operation)
            async_register_admin_service(hass, DOMAIN, name, handler,
                schema=vol.Schema({vol.Required("entry_id"): str}))
        async def recovery_handler(call):
            runtime = hass.data[DOMAIN]["entries"].get(call.data["entry_id"])
            if runtime is None or runtime["unloading"]:
                raise HomeAssistantError("Unknown satellite entry")
            enabled = call.data["enabled"]
            if enabled:
                try:
                    settings_for_node(runtime["settings"])
                except (ValueError, KeyError):
                    raise HomeAssistantError("Configure Node Exporter first") from None
                if runtime["stop_task"] is not None:
                    raise HomeAssistantError("Stop/uninstall pending; recovery remains disabled")
            set_recovery(hass, runtime, enabled)
            runtime["recovery"].reset()
            if enabled and runtime["task"] is None:
                await run_operation(hass, call.data["entry_id"], "recover_node")
        async_register_admin_service(hass, DOMAIN, "set_node_recovery", recovery_handler,
            schema=vol.Schema({vol.Required("entry_id"): str, vol.Required("enabled"): bool}))
    transport = lambda settings, command, payload=None: probe(hass, settings, command, payload)
    idle = asyncio.Event()
    idle.set()
    runtime = {"entry": entry, "settings": {**entry.data, **entry.options},
        "status": "not_checked", "result": {}, "task": None, "stop_task": None,
        "idle": idle, "unloading": False, "checker": Checker(transport),
        "node": NodeManager(transport, hass.async_add_executor_job), "recovery": RecoveryPolicy(),
        "recovery_check_failed": False}
    hass.data[DOMAIN]["entries"][entry.entry_id] = runtime
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        async def monitor(now):
            if runtime["task"] is not None or runtime["stop_task"] is not None or runtime["unloading"]:
                return
            try:
                await run_operation(hass, entry.entry_id, "monitor")
            except HomeAssistantError:
                pass
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
    runtime = hass.data.get(DOMAIN, {}).get("entries", {}).get(entry.entry_id)
    saved = {**entry.data, **entry.options}
    if runtime is not None:
        old = {key: value for key, value in runtime["settings"].items() if key != "node_recovery"}
        new = {key: value for key, value in saved.items() if key != "node_recovery"}
        if old == new:
            runtime["settings"] = saved
            async_dispatcher_send(hass, DOMAIN + entry.entry_id)
            return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    state = hass.data[DOMAIN]
    runtime = state["entries"][entry.entry_id]
    runtime["unloading"] = True
    tasks = {task for task in (runtime["task"], runtime["stop_task"]) if task is not None}
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        runtime["unloading"] = False
        return False
    state["entries"].pop(entry.entry_id)
    if not state["entries"]:
        for name in SERVICES:
            hass.services.async_remove(DOMAIN, name)
        hass.data.pop(DOMAIN)
    return True
