"""Synthetic CPU policy validation and HA intent/recovery lifecycle."""
import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock
import test_lifecycle as lifecycle

comp=Path(__file__).resolve().parents[1]/"custom_components/techo5_satellite_manager"
p=types.ModuleType("cpu_unit");p.__path__=[str(comp)];sys.modules[p.__name__]=p
cpu=importlib.import_module("cpu_unit.cpu_policy")
GOOD="cpu_policy_state=all_online\ncpu_online=0-3\ncpu_minimum=4\ncpu_thermal_limit=4\n"

class Parser(unittest.TestCase):
    def test_valid_constrained_and_malformed_observations(self):
        self.assertEqual(cpu.parse_cpu_status(GOOD)["cpu_minimum"],4)
        self.assertEqual(cpu.parse_cpu_status(GOOD.replace("all_online","constrained").replace("cpu_thermal_limit=4","cpu_thermal_limit=2"))["cpu_thermal_limit"],2)
        for bad in [GOOD+"cpu_minimum=4\n",GOOD.replace("0-3","0-999"),GOOD.replace("minimum=4","minimum=0"),GOOD+"raw_log=private\n"]:
            with self.subTest(bad=bad),self.assertRaises(cpu.CheckError):cpu.parse_cpu_status(bad)

class Delivery(unittest.IsolatedAsyncioTestCase):
    async def test_small_verified_stdin_command_and_timeout(self):
        transport=AsyncMock(return_value=GOOD)
        manager=cpu.CpuManager(transport,asyncio.to_thread)
        await manager.execute({"host":"10.77.0.10"},"enable")
        args=transport.await_args.args
        self.assertLess(len(args[1].encode()),1024)
        self.assertIn(b"num_base_custom1",args[2])
        with self.assertRaises(ValueError):await manager.execute({"host":"10.77.0.10"},"bad")

class Lifecycle(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=lifecycle.AdapterTests.asyncSetUp
    asyncTearDown=lifecycle.AdapterTests.asyncTearDown
    runtime=lifecycle.AdapterTests.runtime

    async def setup_cpu(self,options=None):
        self.entry.options={} if options is None else options
        await self.adapter.async_setup_entry(self.hass,self.entry)
        r=self.runtime()
        r["cpu"].execute=AsyncMock(return_value={"cpu_policy_state":"automatic","cpu_minimum":1})
        r["checker"].check=AsyncMock(return_value={})
        return r

    async def call(self,enabled):
        await self.services["set_cpu_all_cores"](types.SimpleNamespace(data={"entry_id":"synthetic","enabled":enabled}))

    async def test_opt_in_without_metrics_and_reapply_after_reboot(self):
        r=await self.setup_cpu()
        self.assertFalse(self.timers)
        await self.call(True)
        self.assertEqual([c.args[1] for c in r["cpu"].execute.await_args_list],["inspect","enable"])
        self.assertTrue(self.entry.options["cpu_all_cores"])
        self.assertEqual(len(self.timers),1)
        r["cpu"].execute.reset_mock()
        await self.timers[0][0](None)
        self.assertEqual([c.args[1] for c in r["cpu"].execute.await_args_list],["inspect","enable"])
        self.probe.assert_not_called()

    async def test_off_offline_stays_pending_and_eventually_restores(self):
        r=await self.setup_cpu({"cpu_all_cores":True})
        r["cpu"].execute.side_effect=self.adapter.CheckError("ssh_network_failed")
        with self.assertRaises(self.adapter.HomeAssistantError):await self.call(False)
        self.assertFalse(self.entry.options["cpu_all_cores"])
        self.assertTrue(self.entry.options["cpu_restore_pending"])
        r["cpu_retry"].reset()
        r["cpu"].execute.side_effect=None
        await self.timers[0][0](None)
        self.assertEqual(r["cpu"].execute.await_args.args[1],"disable")
        self.assertFalse(self.entry.options["cpu_restore_pending"])

    async def test_busy_off_preserves_intent_without_overlapping_ssh(self):
        r=await self.setup_cpu({"cpu_all_cores":True})
        r["task"]=object()
        await self.call(False)
        r["cpu"].execute.assert_not_called()
        self.assertTrue(self.entry.options["cpu_restore_pending"])
        r["task"]=None
        await self.timers[0][0](None)
        self.assertEqual(r["cpu"].execute.await_args.args[1],"disable")

    async def test_thermal_constraint_never_rewrites_minimum_or_limits(self):
        r=await self.setup_cpu({"cpu_all_cores":True})
        r["cpu"].execute.return_value={"cpu_policy_state":"constrained","cpu_minimum":4}
        await self.timers[0][0](None)
        self.assertEqual(r["cpu"].execute.await_count,1)
        self.assertEqual(r["cpu_result"]["cpu_policy_state"],"constrained")

    async def test_unsupported_and_transport_failures_back_off_independently(self):
        r=await self.setup_cpu({"cpu_all_cores":True})
        r["cpu"].execute.return_value={"cpu_policy_state":"unsupported"}
        await self.timers[0][0](None)
        self.assertEqual(r["cpu_error"],"cpu_policy_unsupported")
        await self.timers[0][0](None)
        self.assertEqual(r["cpu"].execute.await_count,1)
        self.assertEqual(r["recovery"].failures,0)

    async def test_saved_cpu_only_intent_registers_monitor_on_setup(self):
        await self.setup_cpu({"cpu_restore_pending":True})
        self.assertEqual(len(self.timers),1)
        self.probe.assert_not_called()
