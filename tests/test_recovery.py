"""Desired-state selection, quiet healthy checks and bounded failure backoff."""
import asyncio
import importlib
import unittest
from unittest.mock import AsyncMock
from test_ssh import package

recovery=importlib.import_module(package.__name__+'.recovery')
core=importlib.import_module(package.__name__+'.core')


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_selects_start_for_retained_install_and_deploy_only_missing_or_invalid(self):
        for state,action in [('stopped','start'),('not_installed','deploy'),('invalid','deploy')]:
            manager=type('Manager',(),{})()
            manager.execute=AsyncMock(return_value={'node_state':'running','node_firewall':'restricted'})
            policy=recovery.RecoveryPolicy()
            await policy.maintain(manager,{}, {'node_state':state})
            manager.execute.assert_awaited_once_with({},action)

    async def test_healthy_never_restarts_or_downloads(self):
        manager=type('Manager',(),{})();manager.execute=AsyncMock(side_effect=AssertionError)
        policy=recovery.RecoveryPolicy()
        observed={'node_state':'running','node_firewall':'restricted'}
        self.assertEqual(await policy.maintain(manager,{},observed),observed)
        manager.execute.assert_not_called()

    async def test_missing_firewall_uses_start_but_partial_rules_require_review(self):
        self.assertEqual(recovery.recovery_action({'node_state':'running','node_firewall':'absent'}),'start')
        for observed in ({'node_state':'unmanaged'}, {'node_state':'running','node_firewall':'partial'}):
            with self.assertRaises(core.CheckError): recovery.recovery_action(observed)

    async def test_failures_back_off_and_cap_at_fifteen_minutes(self):
        now=[100.0];policy=recovery.RecoveryPolicy(clock=lambda:now[0])
        manager=type('Manager',(),{})();manager.execute=AsyncMock()
        for expected in (60,120,240,480,900,900):
            policy.failed('start_failed')
            self.assertEqual(policy.next_attempt-now[0],expected)
            await policy.maintain(manager,{}, {'node_state':'stopped'})
            manager.execute.assert_not_called()
            now[0]=policy.next_attempt
        policy.reset();self.assertEqual(policy.failures,0)

    async def test_recovery_must_observe_running_and_restricted_after_action(self):
        manager=type('Manager',(),{})();manager.execute=AsyncMock(return_value={'node_state':'stopped'})
        with self.assertRaisesRegex(core.CheckError,'recovery_not_running'):
            await recovery.RecoveryPolicy().maintain(manager,{}, {'node_state':'stopped'})

    async def test_cancel_does_not_become_success(self):
        manager=type('Manager',(),{})();manager.execute=AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await recovery.RecoveryPolicy().maintain(manager,{}, {'node_state':'stopped'})
