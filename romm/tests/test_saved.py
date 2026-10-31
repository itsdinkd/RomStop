"""Tests for RomStop's saved hacks in RomM. Run: python -m unittest discover romm/tests"""

import importlib.util
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/saved.py"


def _load(tmp: Path):
    stubs = {
        "handler": types.ModuleType("handler"),
        "handler.romshelf": types.ModuleType("handler.romshelf"),
        "handler.romshelf.index": types.SimpleNamespace(ROMSHELF_DIR=tmp),
    }
    with mock.patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("handler.romshelf.saved", _PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class SavedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_saved_hacks_are_per_user_and_survive_a_restart(self):
        mod = _load(self.tmp)
        self.assertEqual(mod.saved_ids(1), [])
        mod.set_saved(1, "rhdn-1", True)
        time.sleep(0.01)
        mod.set_saved(1, "smwc-2", True)
        mod.set_saved(2, "rhdn-1", True)
        # A fresh load reads the same file, as after a RomM restart.
        mod = _load(self.tmp)
        self.assertEqual(mod.saved_ids(1), ["smwc-2", "rhdn-1"])  # newest first
        self.assertEqual(mod.saved_ids(2), ["rhdn-1"])
        self.assertTrue((self.tmp / "saved.sqlite").exists())

    def test_saving_twice_keeps_one_entry_and_unsaving_removes_it(self):
        mod = _load(self.tmp)
        mod.set_saved(1, "rhdn-1", True)
        mod.set_saved(1, "rhdn-1", True)
        self.assertEqual(mod.saved_ids(1), ["rhdn-1"])
        mod.set_saved(1, "rhdn-1", False)
        mod.set_saved(1, "never-saved", False)
        self.assertEqual(mod.saved_ids(1), [])


if __name__ == "__main__":
    unittest.main()
