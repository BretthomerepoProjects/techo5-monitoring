"""HACS metadata and deterministic branding contain only reusable project data."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_hacs_layout_and_manifest(self):
        components = [p for p in (ROOT / "custom_components").iterdir() if p.is_dir() and p.name != "__pycache__"]
        self.assertEqual([p.name for p in components], ["techo5_satellite_manager"])
        manifest = json.loads((components[0] / "manifest.json").read_text())
        self.assertTrue({"domain", "documentation", "issue_tracker", "codeowners", "name", "version"} <= manifest.keys())
        hacs = json.loads((ROOT / "hacs.json").read_text())
        self.assertEqual(hacs["name"], manifest["name"])
        self.assertFalse(hacs.get("content_in_root", False))
    def test_icon_matches_metadata_free_generator(self):
        spec = importlib.util.spec_from_file_location("icon_builder", ROOT / "tools/build_icon.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        icon = (ROOT / "custom_components/techo5_satellite_manager/brand/icon.png").read_bytes()
        self.assertEqual(icon, builder.make_icon())
