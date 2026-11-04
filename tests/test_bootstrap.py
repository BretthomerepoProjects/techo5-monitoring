import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
import asyncssh
from test_core import ROOT

spec = importlib.util.spec_from_file_location('bootstrap_core', ROOT/'custom_components/techo5_satellite_manager/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class Keys(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)/'satellite_manager/ssh'
        self.base.mkdir(parents=True)
        self.private = asyncssh.generate_private_key('ssh-ed25519')
        (self.base/'test_key').write_bytes(self.private.export_private_key())
    def tearDown(self):
        self.temp.cleanup()
    def test_derives_only_public_key(self):
        public, fingerprint = bootstrap.public_identity(self.temp.name, 'test_key')
        self.assertEqual(asyncssh.import_public_key(public), self.private.convert_to_public())
        self.assertNotIn('PRIVATE', public)
        self.assertEqual(fingerprint, self.private.get_fingerprint())
    def test_requires_valid_public_additional_keys(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.public_identity(self.temp.name, 'test_key', 'invalid key input')
    def test_private_key_cannot_be_an_additional_public_key(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.public_identity(self.temp.name, 'test_key', self.private.export_private_key().decode())
    def test_invalid_key_filename_is_rejected(self):
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.public_identity(self.temp.name, '../test_key')
    def test_new_host_requires_explicit_acceptance(self):
        key = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
        self.assertFalse(bootstrap.host_trust(self.temp.name, '10.77.0.10', 22, key))
        self.assertFalse((self.base/'known_hosts').exists())
        self.assertTrue(bootstrap.host_trust(self.temp.name, '10.77.0.10', 22, key, accept=True))
        self.assertTrue(bootstrap.host_trust(self.temp.name, '10.77.0.10', 22, key))
    def test_changed_saved_host_key_is_never_overwritten(self):
        first = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
        changed = asyncssh.generate_private_key('ssh-ed25519').convert_to_public()
        bootstrap.host_trust(self.temp.name, '10.77.0.10', 22, first, accept=True)
        before = (self.base/'known_hosts').read_bytes()
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'host_key_changed'):
            bootstrap.host_trust(self.temp.name, '10.77.0.10', 22, changed, accept=True)
        self.assertEqual(before, (self.base/'known_hosts').read_bytes())


class Backend:
    def __init__(self, fail=None): self.calls, self.fail = [], fail
    async def record(self, name):
        self.calls.append(name)
        if self.fail == name: raise bootstrap.BootstrapError('synthetic_failure')
    async def validate(self): await self.record('validate')
    async def enable_ssh(self): await self.record('enable')
    async def push_keys(self, keys): await self.record('push_public')
    async def wait_listening(self): await self.record('listen')
    async def server_key(self): await self.record('host_key'); return 'synthetic result'


class Sequence(unittest.IsolatedAsyncioTestCase):
    async def test_enable_then_push_then_test_order(self):
        backend = Backend()
        await bootstrap.prepare(backend, 'synthetic public payload', confirmed=True)
        self.assertEqual(backend.calls, ['validate','enable','push_public','listen','host_key'])
    async def test_no_side_effect_without_confirmation(self):
        backend = Backend()
        with self.assertRaises(bootstrap.BootstrapError):
            await bootstrap.prepare(backend, 'synthetic public payload', confirmed=False)
        self.assertFalse(backend.calls)
    async def test_invalid_target_stops_before_switch_or_key_write(self):
        backend = Backend(fail='validate')
        with self.assertRaises(bootstrap.BootstrapError):
            await bootstrap.prepare(backend, 'synthetic public payload', confirmed=True)
        self.assertEqual(backend.calls, ['validate'])
    async def test_failed_key_write_does_not_attempt_ssh(self):
        backend = Backend(fail='push_public')
        with self.assertRaises(bootstrap.BootstrapError):
            await bootstrap.prepare(backend, 'synthetic public payload', confirmed=True)
        self.assertEqual(backend.calls, ['validate','enable','push_public'])
