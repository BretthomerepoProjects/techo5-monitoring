"""The publication guard must reject forced/stale staged sensitive content."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

GUARD = Path(__file__).resolve().parents[1] / "tools/check_public_tree.py"


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "tools").mkdir()
        shutil.copyfile(GUARD, self.root / "tools/check_public_tree.py")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
    def tearDown(self):
        self.temp.cleanup()
    def stage(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        subprocess.run(["git", "add", "--", name], cwd=self.root, check=True)
    def result(self):
        return subprocess.run([sys.executable, "tools/check_public_tree.py"], cwd=self.root, capture_output=True, text=True).returncode
    def test_normal_source_is_allowed(self):
        self.stage("component.py", "VALUE = 1")
        self.assertEqual(self.result(), 0)
    def test_key_file_is_rejected(self):
        self.stage("example.pub", "synthetic key-file sentinel")
        self.assertNotEqual(self.result(), 0)
    def test_staged_credential_pattern_is_rejected(self):
        sentinel = "gh" + "p_" + "A" * 32
        self.stage("component.py", "VALUE = " + repr(sentinel))
        self.assertNotEqual(self.result(), 0)
    def test_clean_worktree_cannot_hide_staged_credential(self):
        sentinel = "gh" + "p_" + "A" * 32
        self.stage("component.py", "VALUE = " + repr(sentinel))
        (self.root / "component.py").write_text("VALUE = 1")
        self.assertNotEqual(self.result(), 0)
