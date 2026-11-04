import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("satellite_core", ROOT / "custom_components/techo5_satellite_manager/core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
GOOD = '''schema=1
arch=aarch64
kernel=4.9.337-test
techo5=yes
root_mode=ro,noatime
online=0-1
uptime=1000.0
load=1.0 1.0 1.0
memory_total_kib=512000
memory_available_kib=256000
avahi=86
monitoring=absent
exporter_count=1
exporter_uid=86
exporter_gid=86
exporter_uids=86 86 86 86
exporter_gids=86 86 86 86
exporter_caps=0000000000000000
exporter_rss_kib=16000
'''


class Parsing(unittest.TestCase):
    def test_identity_is_observed_not_approved(self):
        self.assertEqual(core.parse_probe(GOOD)["exporter_identity"], "nonroot_observed")
        self.assertFalse(core.parse_probe(GOOD)["deployment_enabled"])

    def test_root_and_capabilities_are_reported_as_observations(self):
        for output in (GOOD.replace("exporter_uids=86 86 86 86", "exporter_uids=86 86 0 86"), GOOD.replace("exporter_uid=86", "exporter_uid=0"),
                       GOOD.replace("exporter_gid=86", "exporter_gid=0"),
                       GOOD.replace("exporter_caps=0000000000000000", "exporter_caps=1")):
            self.assertIn(core.parse_probe(output)["exporter_identity"], {"root_observed", "capabilities_observed"})

    def test_duplicate_unknown_missing_oversize_rejected(self):
        for output in (GOOD + "arch=aarch64\n", GOOD + "secret=password\n",
                       GOOD.replace("schema=1\n", ""), "x" * 8193,
                       GOOD.replace("exporter_uid=86\n", "")):
            with self.assertRaises(core.CheckError):
                core.parse_probe(output)

    def test_unsupported(self):
        with self.assertRaisesRegex(core.CheckError, "unsupported_device"):
            core.parse_probe(GOOD.replace("aarch64", "x86_64"))

    def test_private_ipv4_only(self):
        for host in ("127.0.0.1", "8.8.8.8", "::1", "host; reboot", "169.254.1.2"):
            with self.assertRaises(ValueError):
                core.validate_host(host)
        self.assertEqual(core.validate_host("10.77.0.10"), "10.77.0.10")

    def test_credentials_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("../key", "/tmp/key", "x/key", "x\\key", ""):
                with self.assertRaises(ValueError):
                    core.credential_paths(directory, name)
            base = Path(directory) / "satellite_manager/ssh"
            base.mkdir(parents=True)
            (base / "test_key").write_text("test only")
            (base / "known_hosts").write_text("test only")
            self.assertEqual(len(core.credential_paths(directory, "test_key")), 2)


class AsyncChecks(unittest.IsolatedAsyncioTestCase):
    async def test_no_connection_at_construction(self):
        async def forbidden(*args):
            self.fail("Unexpected connection")
        checker = core.Checker(forbidden)
        self.assertFalse(checker.busy)

    async def test_timeout_releases_busy(self):
        async def slow(*args):
            await asyncio.sleep(10)
        checker = core.Checker(slow, timeout=0.02)
        with self.assertRaisesRegex(core.CheckError, "timeout"):
            await checker.check({})
        self.assertFalse(checker.busy)

    async def test_concurrency_and_cancellation(self):
        entered = asyncio.Event()
        async def slow(*args):
            entered.set()
            await asyncio.sleep(10)
        checker = core.Checker(slow)
        task = asyncio.create_task(checker.check({}))
        await entered.wait()
        with self.assertRaisesRegex(core.CheckError, "busy"):
            await checker.check({})
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(checker.busy)

    async def test_success_fixed_probe(self):
        async def transport(settings, command):
            self.assertEqual(command, core.PROBE)
            return GOOD
        result = await core.Checker(transport).check({})
        self.assertIn("+00:00", result["checked_at"])


if __name__ == "__main__":
    unittest.main()
