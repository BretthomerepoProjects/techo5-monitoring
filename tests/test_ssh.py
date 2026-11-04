"""Real SSH on loopback with disposable keys; no production access."""
import asyncio
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import asyncssh
from test_core import GOOD, ROOT

package = types.ModuleType("satellite_test_package")
package.__path__ = [str(ROOT / "custom_components/techo5_satellite_manager")]
sys.modules[package.__name__] = package
ssh = importlib.import_module(package.__name__ + ".ssh")
core = importlib.import_module(package.__name__ + ".core")


class Server(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_public_key(self, username, key):
        return username == "root" and key == self.allowed


class SSHTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "satellite_manager/ssh"
        self.base.mkdir(parents=True)
        self.key = asyncssh.generate_private_key("ssh-ed25519")
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        (self.base / "test_key").write_bytes(self.key.export_private_key())
        Server.allowed = self.key.convert_to_public()
        self.mode = "ok"
        self.commands = []

        async def process(proc):
            self.commands.append(proc.command)
            if self.mode == "slow":
                await asyncio.sleep(0.3)
            proc.stdout.write("x" * 9000 if self.mode == "large" else GOOD)
            proc.exit(0)

        self.server = await asyncssh.create_server(
            Server, "127.0.0.1", 0, server_host_keys=[self.host_key], process_factory=process)
        self.port = self.server.get_port()
        self.write_host(self.host_key)
        self.hass = types.SimpleNamespace(
            config=types.SimpleNamespace(config_dir=self.temp.name),
            async_add_executor_job=asyncio.to_thread)
        self.settings = {"host": "10.77.0.10", "port": self.port, "key_name": "test_key"}
        # Only tests may route the private-IP setting to the disposable loopback server.
        self.redirect = patch.object(ssh, "validate_host", return_value="127.0.0.1")
        self.redirect.start()

    def write_host(self, key):
        (self.base / "known_hosts").write_text(
            f"[127.0.0.1]:{self.port} " + key.export_public_key().decode())

    async def asyncTearDown(self):
        self.redirect.stop()
        self.server.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def test_verified_ssh_fixed_command(self):
        result = await ssh.probe(self.hass, self.settings, core.PROBE)
        self.assertEqual(result, GOOD)
        self.assertEqual(self.commands, [core.PROBE])

    async def test_wrong_host_key_never_runs_command(self):
        self.write_host(asyncssh.generate_private_key("ssh-ed25519"))
        with self.assertRaisesRegex(core.CheckError, "host_key_rejected"):
            await ssh.probe(self.hass, self.settings, core.PROBE)
        self.assertFalse(self.commands)

    async def test_output_limit(self):
        self.mode = "large"
        with self.assertRaisesRegex(core.CheckError, "output_limit"):
            await ssh.probe(self.hass, self.settings, core.PROBE)

    async def test_timeout_cleans_busy(self):
        self.mode = "slow"
        checker = core.Checker(lambda settings, cmd: ssh.probe(self.hass, settings, cmd), timeout=0.1)
        with self.assertRaisesRegex(core.CheckError, "timeout"):
            await checker.check(self.settings)
        self.assertFalse(checker.busy)

    async def test_wrong_client_key(self):
        (self.base / "test_key").write_bytes(asyncssh.generate_private_key("ssh-ed25519").export_private_key())
        with self.assertRaisesRegex(core.CheckError, "authentication_failed"):
            await ssh.probe(self.hass, self.settings, core.PROBE)
        self.assertFalse(self.commands)
