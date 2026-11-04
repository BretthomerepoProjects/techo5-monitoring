"""Run the real manager in an isolated Linux temp tree with fake firewall tools."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("framing_test_package")
package.__path__ = [str(ROOT / "custom_components/techo5_satellite_manager")]
sys.modules[package.__name__] = package
node = importlib.import_module(package.__name__ + ".node_exporter")


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"), "Requires a local POSIX shell")
class StdinFraming(unittest.TestCase):
    def test_large_script_and_binary_payload_survive_small_exec_request(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "payload with spaces"
            script = "#" + "x" * 12000 + '\nset -eu\ncat > "$2"\nprintf done\n'
            payload = b'\n\x00' + bytes(range(256)) * 256
            command, stream = node.script_request(script, "deploy", str(output), "unused", 9100,
                                                  "aarch64", "0" * 64, payload)
            self.assertLess(len(command.encode()), 1024)
            result = subprocess.run(command, shell=True, input=stream, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(result.stdout, b"done")

    def test_corrupted_script_never_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist"
            command, stream = node.script_request('printf bad > "$2"\n', "inspect", str(output),
                                                  "unused", 9100, "aarch64", "0" * 64)
            result = subprocess.run(command, shell=True, input=b"X" + stream[1:], capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"),
                     "Requires a local POSIX shell; no satellite access")
class ShellLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="t5sm-test-")
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.base = self.data / "ha-satellite-manager"
        self.install = self.base / "node"
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.boot = self.root / "boot.sh"
        self.boot.touch()
        self.rules = self.root / "rules.json"
        self.rules.write_text(json.dumps([["unrelated-existing-rule"]]))
        self.script = (ROOT / "custom_components/techo5_satellite_manager/node_manager.sh").read_text()
        self.script = (self.script.replace("/data", str(self.data))
                       .replace("/etc/techo5/boot.sh", str(self.boot))
                       .replace("/etc/inittab", str(self.root / "inittab"))
                       .replace("/etc/init.d", str(self.root / "init.d")))
        self.payload = Path("/bin/sleep").read_bytes()  # Native fixture stays in the temp tree only.
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.env = {**os.environ, "PATH": str(self.tools) + ":" + os.environ["PATH"],
                    "T5SM_RULES": str(self.rules), "T5SM_INSTALL": str(self.install)}
        self.tool("id", '#!/bin/sh\necho 0\n')
        self.tool("stat", '''#!/usr/bin/python3
import os, stat, sys
info=os.stat(sys.argv[-1])
uid=0 if info.st_uid == os.getuid() else info.st_uid
mode=format(stat.S_IMODE(info.st_mode), "o")
print({"%u": str(uid), "%a": mode, "%u:%a": str(uid) + ":" + mode, "%s": str(info.st_size)}[sys.argv[2]])
''')
        self.tool("uname", '#!/bin/sh\ncase "$1" in -m) echo aarch64;; -r) echo "${T5SM_KERNEL:-4.9.337-test}";; esac\n')
        self.tool("pidof", '''#!/usr/bin/python3
import os
from pathlib import Path
if os.environ.get("T5SM_FOREIGN"):
    print(999999)
else:
    for path in Path("/proc").glob("[0-9]*/exe"):
        try:
            if os.readlink(path) == os.environ["T5SM_INSTALL"] + "/node_exporter":
                print(path.parent.name)
        except OSError:
            pass
''')
        self.tool("setsid", '''#!/usr/bin/python3
import os, sys
os.setsid()
os.execv(sys.argv[1], [sys.argv[1], "60"])
''')
        self.tool("iptables-legacy", '''#!/usr/bin/python3
import json, os, sys
from pathlib import Path
path=Path(os.environ["T5SM_RULES"])
rules=json.loads(path.read_text())
args=sys.argv[1:]
if args == ["--version"]:
    print("iptables v1.8.7 (legacy)"); sys.exit(0)
op, chain=args[:2]
if os.environ.get("T5SM_NO_COMMENT") and "comment" in args: sys.exit(1)
if op == "-S":
    print("-N " + chain)
    for rule in rules: print("-A " + chain + " " + " ".join(rule))
    sys.exit(0)
if op == "-I":
    if os.environ.get("T5SM_FAIL_ALLOW") and args[-1] == "ACCEPT": sys.exit(1)
    rules.insert(int(args[2])-1, args[3:])
elif op == "-C":
    sys.exit(0 if args[2:] in rules else 1)
elif op == "-D":
    try: rules.remove(args[2:])
    except ValueError: sys.exit(1)
else: sys.exit(1)
path.write_text(json.dumps(rules))
''')
        self.tool("iptables", '#!/bin/sh\nexec iptables-legacy "$@"\n')

    def tool(self, name, content):
        path = self.tools / name
        path.write_text(content)
        path.chmod(0o700)

    def call(self, action, payload=None, collector="10.77.0.20", expect=0, port=9100):
        result = subprocess.run(["sh", "-c", self.script, "--", action,
            "10.77.0.10", collector, str(port), "aarch64", self.digest,
            str(len(payload) if payload else 0)], input=payload, env=self.env,
            capture_output=True, timeout=25)
        self.assertEqual(result.returncode, expect, result.stdout.decode() + result.stderr.decode())
        return result.stdout.decode()

    def tearDown(self):
        # Reap only processes whose executable is inside this test's temporary tree.
        for path in Path("/proc").glob("[0-9]*/exe"):
            try:
                if os.readlink(path) == str(self.install / "node_exporter"):
                    os.kill(int(path.parent.name), signal.SIGTERM)
            except OSError:
                pass
        self.temp.cleanup()

    def test_inspect_is_read_only(self):
        self.assertIn("node_state=not_installed", self.call("inspect"))
        self.assertFalse(self.base.exists())

    def test_full_lifecycle_is_idempotent_and_preserves_unrelated_firewall(self):
        self.assertIn("node_state=running", self.call("deploy", self.payload))
        pid = (self.install / "pid").read_text()
        self.assertIn("node_firewall=restricted", self.call("inspect"))
        self.call("deploy")
        self.assertEqual((self.install / "pid").read_text(), pid)
        self.assertEqual(len(json.loads(self.rules.read_text())), 3)
        self.call("stop")
        self.assertEqual(json.loads(self.rules.read_text()), [["unrelated-existing-rule"]])
        self.assertIn("node_state=stopped", self.call("inspect"))
        self.call("start")
        self.call("uninstall")
        self.assertFalse(self.install.exists())
        self.assertEqual(json.loads(self.rules.read_text()), [["unrelated-existing-rule"]])

    def test_wrong_checksum_never_starts_or_opens_firewall(self):
        self.assertIn("binary_checksum_failed", self.call("deploy", b"bad", expect=1))
        self.assertFalse((self.install / "node_exporter").exists())
        self.assertEqual(len(json.loads(self.rules.read_text())), 1)

    def test_firewall_failure_rolls_back_added_rules(self):
        self.env["T5SM_FAIL_ALLOW"] = "1"
        self.call("deploy", self.payload, expect=1)
        self.assertEqual(json.loads(self.rules.read_text()), [["unrelated-existing-rule"]])
        self.assertFalse((self.install / "pid").exists())

    def test_unmanaged_exporter_is_never_adopted_or_killed(self):
        self.env["T5SM_FOREIGN"] = "1"
        self.assertIn("node_state=unmanaged", self.call("inspect"))
        self.assertIn("unmanaged_exporter", self.call("deploy", self.payload, expect=1))

    def test_init_respawn_is_refused_without_editing(self):
        content = "pilot:respawn:/data/node-exporter/start.sh\n"
        (self.root / "inittab").write_text(content)
        self.assertIn("external_supervisor", self.call("deploy", self.payload, expect=1))
        self.assertEqual((self.root / "inittab").read_text(), content)

    def test_unknown_firmware_and_symlink_are_refused(self):
        self.env["T5SM_KERNEL"] = "6.1-test"
        self.assertIn("unsupported_firmware", self.call("deploy", self.payload, expect=1))
        del self.env["T5SM_KERNEL"]
        self.base.symlink_to(self.root)
        self.assertIn("unsafe_path", self.call("deploy", self.payload, expect=1))

    def test_stop_uses_saved_endpoint_then_deploy_can_reconfigure(self):
        self.call("deploy", self.payload)
        self.call("start", collector="10.77.0.30", expect=1)
        self.call("stop", collector="10.77.0.30")
        self.assertEqual(len(json.loads(self.rules.read_text())), 1)
        self.call("deploy", collector="10.77.0.30")
        self.assertIn("10.77.0.30", (self.install / "config").read_text())

    def test_remote_lock_blocks_mutations_and_is_not_stolen(self):
        self.base.mkdir(mode=0o700)
        (self.base / "operation.lock").mkdir()
        self.assertIn("remote_busy", self.call("deploy", self.payload, expect=1))
        self.assertTrue((self.base / "operation.lock").exists())

    def test_existing_listener_is_refused_before_firewall_changes(self):
        import socket
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.assertIn("exporter_port_in_use", self.call("deploy", self.payload,
                port=listener.getsockname()[1], expect=1))
        self.assertEqual(json.loads(self.rules.read_text()), [["unrelated-existing-rule"]])


    def test_corrupt_managed_payload_is_backed_up_and_repaired(self):
        self.call("deploy", self.payload)
        self.call("stop")
        (self.install / "node_exporter").write_bytes(b"synthetic corrupt payload")
        self.assertIn("node_state=invalid", self.call("inspect"))
        self.call("deploy", self.payload)
        self.assertEqual((self.install / "node_exporter.previous").read_bytes(), b"synthetic corrupt payload")
        self.assertEqual((self.install / "node_exporter").read_bytes(), self.payload)
        self.call("uninstall")
        self.assertFalse(self.install.exists())

    def test_corrupt_payload_with_missing_ownership_manifest_is_not_adopted(self):
        self.call("deploy", self.payload)
        self.call("stop")
        (self.install / "node_exporter").write_bytes(b"unknown payload")
        (self.install / "config").unlink()
        self.assertIn("invalid_config", self.call("deploy", self.payload, expect=1))
        self.assertEqual((self.install / "node_exporter").read_bytes(), b"unknown payload")


    def test_kernel_without_comment_match_uses_owned_plain_rules(self):
        self.env["T5SM_NO_COMMENT"] = "1"
        self.call("deploy", self.payload)
        self.assertEqual((self.install / "firewall_mode").read_text(), "plain\n")
        self.assertIn("node_firewall=restricted", self.call("inspect"))
        self.assertTrue(all("comment" not in rule for rule in json.loads(self.rules.read_text())))
        self.call("uninstall")
        self.assertEqual(json.loads(self.rules.read_text()), [["unrelated-existing-rule"]])

    def test_unclaimed_plain_rule_is_not_adopted_or_deleted(self):
        existing = ["-s", "10.77.0.20/32", "-d", "10.77.0.10/32", "-p", "tcp", "--dport", "9100", "-j", "ACCEPT"]
        self.rules.write_text(json.dumps([existing, ["unrelated-existing-rule"]]))
        self.assertIn("firewall_requires_review", self.call("deploy", self.payload, expect=1))
        self.assertFalse((self.install / "firewall_mode").exists())
        self.assertEqual(json.loads(self.rules.read_text()), [existing, ["unrelated-existing-rule"]])

    def test_old_comment_rules_remain_manageable_during_upgrade(self):
        self.call("deploy", self.payload)
        self.call("stop")
        (self.install / "firewall_mode").unlink()
        allow = ["-s", "10.77.0.20/32", "-d", "10.77.0.10/32", "-p", "tcp", "--dport", "9100", "-m", "comment", "--comment", "t5sm-node-allow", "-j", "ACCEPT"]
        deny = ["-d", "10.77.0.10/32", "-p", "tcp", "--dport", "9100", "-m", "comment", "--comment", "t5sm-node-deny", "-j", "DROP"]
        self.rules.write_text(json.dumps([allow, deny, ["unrelated-existing-rule"]]))
        self.assertIn("node_firewall=restricted", self.call("inspect"))
        self.call("start")
        self.assertEqual((self.install / "firewall_mode").read_text(), "comment\n")
        self.call("uninstall")
        self.assertEqual(json.loads(self.rules.read_text()), [["unrelated-existing-rule"]])

    def test_endpoint_change_refuses_unclaimed_new_plain_rule(self):
        self.call("deploy", self.payload)
        self.call("stop")
        existing = ["-s", "10.77.0.30/32", "-d", "10.77.0.10/32", "-p", "tcp", "--dport", "9100", "-j", "ACCEPT"]
        self.rules.write_text(json.dumps([existing, ["unrelated-existing-rule"]]))
        self.assertIn("firewall_requires_review", self.call("deploy", collector="10.77.0.30", expect=1))
        self.assertIn("10.77.0.20", (self.install / "config").read_text())
        self.assertEqual(json.loads(self.rules.read_text()), [existing, ["unrelated-existing-rule"]])
