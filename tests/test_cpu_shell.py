"""Exercise the real policy shell against synthetic proc/sys trees only."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"), "Requires POSIX shell")
class CpuShell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cpu-policy-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ("data", "run", "proc/hps", "proc/sys/kernel/random", "sys/devices/system/cpu", "etc/techo5", "tools"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / "data").chmod(0o755)
        self.write("etc/techo5/boot.sh", "# synthetic\n")
        self.write("proc/sys/kernel/random/boot_id", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n")
        self.write("sys/devices/system/cpu/present", "0-3\n")
        self.write("sys/devices/system/cpu/online", "0-3\n")
        for name in ("num_limit_thermal", "num_limit_power_serv", "num_limit_custom1", "num_limit_custom2"):
            self.write("proc/hps/" + name, "4\n")
        self.write("proc/hps/enabled", "1\n")
        self.write("proc/hps/num_base_custom1", "1\n")
        self.tool("id", "#!/bin/sh\necho 0\n")
        self.tool("uname", '#!/bin/sh\ncase "$1" in -m) echo aarch64;; -r) echo 4.9.337-test;; esac\n')
        self.tool("sleep", "#!/bin/sh\nexit 0\n")
        # The fixture is owned by the test user. Present only that UID as root.
        self.tool("stat", '''#!/usr/bin/env python3
import os,stat,sys
s=os.stat(sys.argv[-1]);uid=0 if s.st_uid==os.getuid() else s.st_uid
mode=format(stat.S_IMODE(s.st_mode),'o')
print({'%u':str(uid),'%a':mode,'%u:%a':str(uid)+':'+mode}[sys.argv[2]])
''')
        component = Path(__file__).resolve().parents[1] / "custom_components/techo5_satellite_manager"
        script = (component / "cpu_manager.sh").read_text()
        script = re.sub(r"(?<![a-zA-Z0-9_/])/(?:proc|sys|data|run|etc)(?=/|[\s\"]|$)",
                        lambda match: str(self.root) + match[0], script)
        self.script = self.root / "manager.sh"
        self.script.write_text(script)
        self.env = {**os.environ, "PATH": str(self.root / "tools") + ":" + os.environ["PATH"]}

    def write(self, name, text):
        (self.root / name).write_text(text)

    def tool(self, name, text):
        path = self.root / "tools" / name
        path.write_text(text)
        path.chmod(0o700)

    def run_policy(self, action, success=True):
        result = subprocess.run(["sh", str(self.script), action], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result.stdout

    def test_enable_idempotent_disable_restores_original_minimum(self):
        self.assertIn("cpu_policy_state=all_online", self.run_policy("enable"))
        baseline = self.root / "data/ha-satellite-manager/cpu-policy/baseline"
        original = baseline.read_text()
        self.run_policy("enable")
        self.assertEqual(baseline.read_text(), original)
        self.run_policy("disable")
        self.assertEqual((self.root / "proc/hps/num_base_custom1").read_text(), "1\n")
        self.assertFalse(baseline.exists())
        self.assertEqual((self.root / "proc/hps/enabled").read_text(), "1\n")

    def test_reboot_takes_new_baseline_and_does_not_restore_old_boot(self):
        self.run_policy("enable")
        self.write("proc/sys/kernel/random/boot_id", "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee\n")
        self.write("proc/hps/num_base_custom1", "2\n")
        self.run_policy("disable")
        self.assertEqual((self.root / "proc/hps/num_base_custom1").read_text(), "2\n")
        self.run_policy("enable")
        self.run_policy("disable")
        self.assertEqual((self.root / "proc/hps/num_base_custom1").read_text(), "2\n")

    def test_thermal_cap_is_never_overridden(self):
        self.write("proc/hps/num_limit_thermal", "2\n")
        self.write("sys/devices/system/cpu/online", "0-1\n")
        self.assertIn("cpu_policy_state=constrained", self.run_policy("enable"))
        self.assertEqual((self.root / "proc/hps/num_limit_thermal").read_text(), "2\n")

    def test_external_change_refuses_restore_and_retains_baseline(self):
        self.run_policy("enable")
        self.write("proc/hps/num_base_custom1", "3\n")
        self.assertIn("cpu_policy_conflict", self.run_policy("disable", False))
        self.assertTrue((self.root / "data/ha-satellite-manager/cpu-policy/baseline").exists())

    def test_unsupported_interface_never_changes_cpu_policy(self):
        self.write("proc/hps/enabled", "0\n")
        self.assertIn("cpu_policy_state=unsupported", self.run_policy("enable"))
        self.assertFalse((self.root / "data/ha-satellite-manager").exists())

    def test_inspect_is_read_only_and_symlink_baseline_is_rejected(self):
        self.run_policy("inspect")
        self.assertFalse((self.root / "data/ha-satellite-manager").exists())
        self.run_policy("enable")
        baseline = self.root / "data/ha-satellite-manager/cpu-policy/baseline"
        baseline.unlink()
        baseline.symlink_to(self.root / "proc/hps/num_base_custom1")
        self.assertIn("unsafe_path", self.run_policy("disable", False))
        self.assertEqual((self.root / "proc/hps/num_base_custom1").read_text(), "4\n")
