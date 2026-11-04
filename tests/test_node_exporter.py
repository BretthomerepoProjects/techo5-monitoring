"""Synthetic settings, delivery and operation tests; no device access."""
import asyncio
import hashlib
import importlib
import io
import tarfile
import unittest
from unittest.mock import patch
import test_ssh as ssh_tests
from test_core import GOOD

package, ssh = ssh_tests.package, ssh_tests.ssh

node = importlib.import_module(package.__name__ + ".node_exporter")
core = importlib.import_module(package.__name__ + ".core")
SETTINGS = {"host": "10.77.0.10", "collector_host": "10.77.0.20", "port": 22, "exporter_port": 9100}


def response(state="stopped"):
    return f"node_state={state}\nnode_firewall=absent\nnode_version=1.12.1\n"


class Validation(unittest.TestCase):
    def test_settings_refuse_injection_public_hosts_ssh_port_and_self(self):
        for changes in ({"collector_host": "x; reboot"}, {"collector_host": "8.8.8.8"},
                        {"exporter_port": 22}, {"exporter_port": True},
                        {"collector_host": SETTINGS["host"]}, {"exporter_port": 65536}):
            with self.assertRaises(ValueError):
                node.settings_for_node({**SETTINGS, **changes})

    def test_response_is_closed_schema_and_does_not_claim_scraping(self):
        self.assertFalse(node.parse_status(response("running"))["prometheus_scrape_verified"])
        for output in (response() + "node_state=running\n", response() + "secret=value\n",
                       "", response().replace("stopped", "invented")):
            with self.assertRaises(core.CheckError):
                node.parse_status(output)

    def test_archive_extracts_only_exact_regular_member_and_verifies_both_hashes(self):
        payload = b"synthetic binary"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            member = tarfile.TarInfo("node_exporter-1.12.1.linux-arm64/node_exporter")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        data = stream.getvalue()
        pins = ("arm64", hashlib.sha256(data).hexdigest(), hashlib.sha256(payload).hexdigest())
        with patch.dict(node.PAYLOADS, {"aarch64": pins}):
            self.assertEqual(node.unpack_payload(data, "aarch64"), payload)
            with self.assertRaisesRegex(core.CheckError, "download_checksum_failed"):
                node.unpack_payload(data + b"bad", "aarch64")
        with patch.dict(node.PAYLOADS, {"aarch64": (*pins[:2], "0" * 64)}):
            with self.assertRaisesRegex(core.CheckError, "binary_checksum_failed"):
                node.unpack_payload(data, "aarch64")


class Operations(unittest.IsolatedAsyncioTestCase):
    async def test_inspection_never_downloads_or_mutates(self):
        calls = []
        async def transport(settings, command, payload=None):
            calls.append((command, payload))
            return GOOD if command == core.PROBE else response()
        with patch.object(node, "download_payload", side_effect=AssertionError):
            result = await node.NodeManager(transport, asyncio.to_thread).execute(SETTINGS, "inspect")
        self.assertEqual(result["node_state"], "stopped")
        self.assertEqual(len(calls), 2)
        self.assertIn(" -- inspect ", calls[1][0])
        self.assertIsNone(calls[0][1])
        self.assertIsInstance(calls[1][1], bytes)
        self.assertLess(len(calls[1][0].encode()), 1024)
        self.assertIn(b'if [ "$action" = inspect ]', calls[1][1])

    async def test_deploy_downloads_only_missing_payload_then_streams(self):
        calls = []
        async def transport(settings, command, payload=None):
            calls.append((command, payload))
            if command == core.PROBE:
                return GOOD
            return response("not_installed" if len(calls) == 2 else "running")
        with patch.object(node, "download_payload", return_value=b"verified payload") as download:
            result = await node.NodeManager(transport, asyncio.to_thread).execute(SETTINGS, "deploy")
        download.assert_called_once_with("aarch64")
        self.assertTrue(calls[-1][1].endswith(b"verified payload"))
        self.assertGreater(len(calls[-1][1]), len(b"verified payload"))
        self.assertIn(" -- deploy ", calls[-1][0])
        self.assertEqual(result["node_state"], "running")

    async def test_start_and_stop_never_download_or_redeploy(self):
        for action in ("start", "stop", "uninstall"):
            calls = []
            async def transport(settings, command, payload=None):
                calls.append(command)
                return GOOD if command == core.PROBE else response()
            with patch.object(node, "download_payload", side_effect=AssertionError):
                await node.NodeManager(transport, asyncio.to_thread).execute(SETTINGS, action)
            self.assertIn(" -- " + action + " ", calls[-1])

    async def test_unknown_firmware_and_unmanaged_process_refused_before_mutation(self):
        for facts, status in ((GOOD.replace("4.9.337-test", "6.0-test"), response()),
                              (GOOD, response("unmanaged"))):
            calls = []
            async def transport(settings, command, payload=None):
                calls.append(command)
                return facts if command == core.PROBE else status
            with self.assertRaises(core.CheckError):
                await node.NodeManager(transport, asyncio.to_thread).execute(SETTINGS, "deploy")
            self.assertLessEqual(len(calls), 2)

    async def test_timeout_has_check_before_retry_error(self):
        async def transport(*args):
            raise TimeoutError
        with self.assertRaisesRegex(core.CheckError, "node_timeout_check_before_retry"):
            await node.NodeManager(transport, asyncio.to_thread).execute(SETTINGS, "deploy")


class BinarySSH(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ssh_tests.SSHTests.asyncSetUp
    asyncTearDown = ssh_tests.SSHTests.asyncTearDown
    write_host = ssh_tests.SSHTests.write_host
    async def test_binary_upload_is_exact_and_host_verification_is_preserved(self):
        self.server.close()
        await self.server.wait_closed()
        received = []
        async def process(proc):
            received.append(await proc.stdin.read())
            proc.stdout.write(b"node_state=running\n")
            proc.exit(0)
        import asyncssh
        from test_ssh import Server
        self.server = await asyncssh.create_server(
            Server, "127.0.0.1", self.port, server_host_keys=[self.host_key],
            process_factory=process, encoding=None)
        payload = bytes(range(256)) * 1024
        result = await ssh.probe(self.hass, self.settings, "synthetic-upload", payload)
        self.assertEqual(received, [payload])
        self.assertEqual(result, "node_state=running\n")

    async def test_connection_options_are_prepared_off_event_loop(self):
        import threading
        main_thread = threading.get_ident()
        original = ssh.connection_options
        def prepare(*args):
            self.assertNotEqual(threading.get_ident(), main_thread)
            return original(*args)
        with patch.object(ssh, "connection_options", side_effect=prepare):
            await ssh.probe(self.hass, self.settings, core.PROBE)
