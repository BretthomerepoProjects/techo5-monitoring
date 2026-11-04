"""HA adapter lifecycle tests with synthetic services and entries, no live HA."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

COMP = Path(__file__).resolve().parents[1] / "custom_components/techo5_satellite_manager"


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.services = {}
        self.timers = []
        def module(name, **values):
            result = types.ModuleType(name)
            result.__dict__.update(values)
            return result
        class HAError(Exception):
            pass
        stubs = {
            "homeassistant": module("homeassistant"),
            "homeassistant.const": module("homeassistant.const", Platform=types.SimpleNamespace(SENSOR="sensor", BUTTON="button", SWITCH="switch")),
            "homeassistant.helpers": module("homeassistant.helpers"),
            "homeassistant.helpers.service": module("homeassistant.helpers.service",
                async_register_admin_service=lambda hass, domain, name, handler, schema:
                    self.services.update({name: handler})),
            "homeassistant.helpers.dispatcher": module("homeassistant.helpers.dispatcher", async_dispatcher_send=lambda *args: None),
            "homeassistant.helpers.event": module("homeassistant.helpers.event",
                async_track_time_interval=lambda hass, callback, interval:
                    self.timers.append((callback, interval)) or (lambda: None)),
            "homeassistant.exceptions": module("homeassistant.exceptions", HomeAssistantError=HAError),
        }
        self.modules = patch.dict(sys.modules, stubs)
        self.modules.start()
        name = "ha_adapter_test"
        spec = importlib.util.spec_from_file_location(name, COMP / "__init__.py", submodule_search_locations=[str(COMP)])
        self.adapter = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.adapter
        spec.loader.exec_module(self.adapter)
        self.hass = types.SimpleNamespace(data={}, config=types.SimpleNamespace(config_dir="unused"),
            async_add_executor_job=asyncio.to_thread,
            config_entries=types.SimpleNamespace(async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True), async_reload=AsyncMock(),
                async_update_entry=lambda entry, options: setattr(entry, "options", options)),
            services=types.SimpleNamespace(async_remove=lambda *args: None))
        self.entry = types.SimpleNamespace(entry_id="synthetic", title="Synthetic satellite",
            data={"host": "10.77.0.10", "port": 22, "key_name": "test_key"},
            options={"collector_host": "10.77.0.20", "exporter_port": 9100},
            async_on_unload=lambda callback: None, add_update_listener=lambda callback: lambda: None)
        self.network = patch.object(self.adapter, "probe", new=AsyncMock(side_effect=AssertionError("Unexpected SSH")))
        self.probe = self.network.start()

    async def asyncTearDown(self):
        self.network.stop()
        self.modules.stop()
        for name in list(sys.modules):
            if name == "ha_adapter_test" or name.startswith("ha_adapter_test."):
                del sys.modules[name]

    def runtime(self):
        return self.hass.data[self.adapter.DOMAIN]["entries"][self.entry.entry_id]

    async def test_setup_never_connects_and_registers_explicit_admin_controls(self):
        await self.adapter.async_setup_entry(self.hass, self.entry)
        self.probe.assert_not_called()
        self.assertEqual(set(self.services), set(self.adapter.SERVICES))
        self.assertEqual(len(self.timers), 1)
        self.assertEqual(self.timers[0][1].total_seconds(), 60)
        self.assertEqual(self.runtime()["settings"]["collector_host"], "10.77.0.20")

    async def test_monitor_only_inspects_and_stop_does_not_enable_recovery(self):
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["checker"].check = AsyncMock(return_value={"arch": "aarch64"})
        runtime["node"].execute = AsyncMock(return_value={"node_state": "stopped"})
        await self.services["stop_node"](types.SimpleNamespace(data={"entry_id": "synthetic"}))
        await self.timers[0][0](None)
        self.assertEqual([call.args[1] for call in runtime["node"].execute.await_args_list], ["stop", "inspect"])
        self.assertEqual(runtime["result"]["node_state"], "stopped")

    async def test_service_handlers_keep_their_distinct_actions(self):
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["node"].execute = AsyncMock(return_value={"node_state": "stopped"})
        for service in ("deploy_node", "start_node", "stop_node", "uninstall_node"):
            await self.services[service](types.SimpleNamespace(data={"entry_id": "synthetic"}))
        self.assertEqual([call.args[1] for call in runtime["node"].execute.await_args_list],
                         ["deploy", "start", "stop", "uninstall"])

    async def test_busy_cancellation_and_unload_do_not_leave_stale_success(self):
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        entered = asyncio.Event()
        async def waiting(*args):
            entered.set()
            await asyncio.Future()
        runtime["node"].execute = waiting
        task = asyncio.create_task(self.adapter.run_operation(self.hass, "synthetic", "deploy_node"))
        await entered.wait()
        with self.assertRaisesRegex(self.adapter.HomeAssistantError, "busy"):
            await self.adapter.run_operation(self.hass, "synthetic", "check")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(runtime["task"])
        self.assertEqual(runtime["result"]["node_state"], "unknown")
        self.assertTrue(await self.adapter.async_unload_entry(self.hass, self.entry))
        self.assertNotIn(self.adapter.DOMAIN, self.hass.data)

    async def test_unconfigured_entries_keep_manual_checks_without_monitor_timer(self):
        self.entry.options = {}
        await self.adapter.async_setup_entry(self.hass, self.entry)
        self.assertFalse(self.timers)
        self.runtime()["checker"].check = AsyncMock(return_value={})
        await self.adapter.run_operation(self.hass, "synthetic", "check")
        self.assertEqual(self.runtime()["result"]["node_state"], "not_configured")
        self.probe.assert_not_called()


    async def test_manual_check_never_recovers_even_when_enabled(self):
        self.entry.options["node_recovery"] = True
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["checker"].check = AsyncMock(return_value={})
        runtime["node"].execute = AsyncMock(return_value={"node_state": "stopped"})
        await self.adapter.run_operation(self.hass, "synthetic", "check")
        self.assertEqual([call.args[1] for call in runtime["node"].execute.await_args_list], ["inspect"])
        self.assertTrue(self.entry.options["node_recovery"])

    async def test_enable_recovers_immediately_and_flag_change_does_not_reload(self):
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["checker"].check = AsyncMock(return_value={})
        runtime["node"].execute = AsyncMock(side_effect=[{"node_state": "stopped"},
            {"node_state": "running", "node_firewall": "restricted"}])
        await self.services["set_node_recovery"](types.SimpleNamespace(data={"entry_id": "synthetic", "enabled": True}))
        self.assertTrue(self.entry.options["node_recovery"])
        self.assertEqual([call.args[1] for call in runtime["node"].execute.await_args_list], ["inspect", "start"])
        await self.adapter.reload_entry(self.hass, self.entry)
        self.hass.config_entries.async_reload.assert_not_called()

    async def test_stop_while_unreachable_persists_off_across_reload(self):
        self.entry.options["node_recovery"] = True
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["node"].execute = AsyncMock(side_effect=self.adapter.CheckError("ssh_connection_refused"))
        with self.assertRaises(self.adapter.HomeAssistantError):
            await self.services["stop_node"](types.SimpleNamespace(data={"entry_id": "synthetic"}))
        self.assertFalse(self.entry.options["node_recovery"])
        await self.adapter.async_unload_entry(self.hass, self.entry)
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        self.assertFalse(runtime["settings"]["node_recovery"])
        runtime["checker"].check = AsyncMock(return_value={})
        runtime["node"].execute = AsyncMock(return_value={"node_state": "stopped"})
        await self.timers[-1][0](None)
        self.assertEqual([call.args[1] for call in runtime["node"].execute.await_args_list], ["inspect"])

    async def test_stop_queues_after_recovery_and_blocks_reenable_until_finished(self):
        self.entry.options["node_recovery"] = True
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["checker"].check = AsyncMock(return_value={})
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def execute(settings, action):
            calls.append(action)
            if action == "start":
                entered.set(); await release.wait()
                return {"node_state": "running", "node_firewall": "restricted"}
            return {"node_state": "stopped"}
        runtime["node"].execute = execute
        recovering = asyncio.create_task(self.timers[0][0](None))
        await entered.wait()
        stopping = asyncio.create_task(self.services["stop_node"](types.SimpleNamespace(data={"entry_id": "synthetic"})))
        await asyncio.sleep(0)
        self.assertFalse(self.entry.options["node_recovery"])
        with self.assertRaises(self.adapter.HomeAssistantError):
            await self.services["set_node_recovery"](types.SimpleNamespace(data={"entry_id": "synthetic", "enabled": True}))
        await self.timers[0][0](None)
        release.set()
        await asyncio.gather(recovering, stopping)
        self.assertEqual(calls, ["inspect", "start", "stop"])
        self.assertFalse(self.entry.options["node_recovery"])
        self.assertIsNone(runtime["stop_task"])

    async def test_start_enables_recovery_and_uninstall_disables_it(self):
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["node"].execute = AsyncMock(return_value={"node_state": "running"})
        await self.services["start_node"](types.SimpleNamespace(data={"entry_id": "synthetic"}))
        self.assertTrue(self.entry.options["node_recovery"])
        await self.services["uninstall_node"](types.SimpleNamespace(data={"entry_id": "synthetic"}))
        self.assertFalse(self.entry.options["node_recovery"])

    async def test_repeated_monitor_checks_respect_mutation_backoff(self):
        self.entry.options["node_recovery"] = True
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime(); now = [100.0]
        runtime["recovery"].clock = lambda: now[0]
        runtime["checker"].check = AsyncMock(return_value={})
        calls = []
        async def execute(settings, action):
            calls.append(action)
            if action == "start": raise self.adapter.CheckError("start_failed")
            return {"node_state": "stopped"}
        runtime["node"].execute = execute
        await self.timers[0][0](None)
        now[0] += 1
        await self.timers[0][0](None)
        self.assertEqual(calls, ["inspect", "start", "inspect"])
        self.assertEqual(runtime["recovery"].failures, 1)

    async def test_restored_enabled_flag_waits_for_monitor_not_setup(self):
        self.entry.options["node_recovery"] = True
        await self.adapter.async_setup_entry(self.hass, self.entry)
        self.probe.assert_not_called()
        self.assertTrue(self.runtime()["settings"]["node_recovery"])


    async def test_unreachable_enable_is_persistent_and_online_check_recovers(self):
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["checker"].check = AsyncMock(side_effect=self.adapter.CheckError("ssh_connection_refused"))
        with self.assertRaises(self.adapter.HomeAssistantError):
            await self.services["set_node_recovery"](types.SimpleNamespace(data={"entry_id": "synthetic", "enabled": True}))
        self.assertTrue(self.entry.options["node_recovery"])
        self.assertGreater(runtime["recovery"].failures, 0)
        runtime["checker"].check = AsyncMock(return_value={})
        runtime["node"].execute = AsyncMock(side_effect=[{"node_state": "stopped"},
            {"node_state": "running", "node_firewall": "restricted"}])
        await self.timers[0][0](None)
        self.assertEqual([call.args[1] for call in runtime["node"].execute.await_args_list], ["inspect", "start"])
        self.assertEqual(runtime["recovery"].failures, 0)

    async def test_unload_cancels_recovery_and_queued_stop_with_off_intent_retained(self):
        self.entry.options["node_recovery"] = True
        await self.adapter.async_setup_entry(self.hass, self.entry)
        runtime = self.runtime()
        runtime["checker"].check = AsyncMock(return_value={})
        entered = asyncio.Event()
        async def execute(settings, action):
            if action == "start":
                entered.set(); await asyncio.Future()
            return {"node_state": "stopped"}
        runtime["node"].execute = execute
        recovering = asyncio.create_task(self.timers[0][0](None))
        await entered.wait()
        stopping = asyncio.create_task(self.services["stop_node"](types.SimpleNamespace(data={"entry_id": "synthetic"})))
        await asyncio.sleep(0)
        self.assertTrue(await self.adapter.async_unload_entry(self.hass, self.entry))
        self.assertFalse(self.entry.options["node_recovery"])
        self.assertTrue(recovering.cancelled())
        self.assertTrue(stopping.cancelled())
