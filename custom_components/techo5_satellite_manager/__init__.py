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
from .streamer import StreamManager, StreamRecoveryPolicy, settings_for_stream

PLATFORMS = [Platform.SENSOR, Platform.BUTTON, Platform.SWITCH]
OPERATIONS = ("check", "deploy_node", "start_node", "stop_node", "uninstall_node", "deploy_stream", "start_stream", "stop_stream", "uninstall_stream")
SERVICES = (*OPERATIONS, "set_node_recovery", "set_stream_recovery")


def set_recovery(hass, runtime, enabled, service="node"):
    key = service + "_recovery"
    runtime["settings"][key] = enabled
    entry = runtime["entry"]
    if entry.options.get(key, False) != enabled:
        hass.config_entries.async_update_entry(entry, options={**entry.options, key: enabled})
    async_dispatcher_send(hass, DOMAIN + entry.entry_id)


async def refresh_stream(hass, runtime, automatic):
    if not runtime["settings"].get("log_host"):
        runtime["stream_result"] = {"stream_state": "not_configured"}
        return
    try:
        result = await runtime["stream"].execute(runtime["settings"], "inspect")
        if automatic and runtime["settings"].get("stream_recovery", False):
            result = await runtime["stream_recovery"].maintain(runtime["stream"], runtime["settings"], result)
        runtime["stream_result"] = result
        runtime["stream_error"] = None
    except CheckError as exc:
        runtime["stream_result"] = {"stream_state": "unknown", "log_ingestion_verified": False}
        runtime["stream_error"] = str(exc)
        if automatic and runtime["settings"].get("stream_recovery", False):
            runtime["stream_recovery"].failed(str(exc))


async def run_operation(hass, entry_id, operation):
    runtime = hass.data[DOMAIN]["entries"].get(entry_id)
    if runtime is None or runtime["unloading"]:
        raise HomeAssistantError("Unknown or unloading satellite entry")
    task = asyncio.current_task()
    is_stream = operation.endswith("_stream")
    stopping = operation in {"stop_node", "uninstall_node", "stop_stream", "uninstall_stream"}
    if stopping:
        set_recovery(hass, runtime, False, "stream" if is_stream else "node")
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
        if is_stream:
            if operation in {"deploy_stream", "start_stream"}:
                settings_for_stream(runtime["settings"])
                set_recovery(hass, runtime, True, "stream")
                runtime["stream_recovery"].reset()
            if operation == "recover_stream":
                await refresh_stream(hass, runtime, True)
                if runtime["stream_error"]:
                    runtime["status"] = runtime["stream_error"]
                    raise HomeAssistantError("Satellite operation failed: " + runtime["stream_error"])
            else:
                runtime["stream_result"] = await runtime["stream"].execute(
                    runtime["settings"], operation.removesuffix("_stream"))
                runtime["stream_error"] = None
            runtime["status"] = "completed"
            return
        if operation in {"check", "monitor", "recover_node"}:
            await refresh_stream(hass, runtime, operation != "check")
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
        if is_stream:
            code = str(exc) if isinstance(exc, CheckError) else "configure_log_streamer"
            runtime["stream_result"] = {"stream_state": "unknown", "log_ingestion_verified": False}
            runtime["stream_error"] = code
            runtime["status"] = code
            if runtime["settings"].get("stream_recovery", False): runtime["stream_recovery"].failed(code)
            raise HomeAssistantError("Satellite operation failed: " + code) from None
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
        if is_stream: runtime["stream_result"] = {"stream_state": "unknown", "log_ingestion_verified": False}
        else: runtime["result"] = {"node_state": "unknown"}
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
        async def stream_recovery_handler(call):
            runtime = hass.data[DOMAIN]["entries"].get(call.data["entry_id"])
            if runtime is None or runtime["unloading"]: raise HomeAssistantError("Unknown satellite entry")
            enabled = call.data["enabled"]
            if enabled:
                try: settings_for_stream(runtime["settings"])
                except (ValueError, KeyError): raise HomeAssistantError("Configure log streamer first") from None
                if runtime["stop_task"] is not None: raise HomeAssistantError("Stop/uninstall pending")
            set_recovery(hass, runtime, enabled, "stream")
            runtime["stream_recovery"].reset()
            if enabled and runtime["task"] is None:
                await run_operation(hass, call.data["entry_id"], "recover_stream")
        async_register_admin_service(hass, DOMAIN, "set_stream_recovery", stream_recovery_handler,
            schema=vol.Schema({vol.Required("entry_id"): str, vol.Required("enabled"): bool}))
    transport = lambda settings, command, payload=None: probe(hass, settings, command, payload)
    idle = asyncio.Event()
    idle.set()
    runtime = {"entry": entry, "settings": {**entry.data, **entry.options},
        "status": "not_checked", "result": {}, "task": None, "stop_task": None,
        "idle": idle, "unloading": False, "checker": Checker(transport),
        "node": NodeManager(transport, hass.async_add_executor_job), "recovery": RecoveryPolicy(),
        "recovery_check_failed": False,
        "stream": StreamManager(transport, hass.async_add_executor_job),
        "stream_recovery": StreamRecoveryPolicy(), "stream_result": {}, "stream_error": None}
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
        if runtime["settings"].get("collector_host") or runtime["settings"].get("log_host"):
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
        old = {key: value for key, value in runtime["settings"].items() if key not in {"node_recovery", "stream_recovery"}}
        new = {key: value for key, value in saved.items() if key not in {"node_recovery", "stream_recovery"}}
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
