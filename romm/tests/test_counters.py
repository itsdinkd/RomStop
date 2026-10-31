"""Tests for RomStop's RomM download/play counts. Run: python -m unittest discover romm/tests"""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/counters.py"


class _Query:
    """Stands in for select(func.count()).select_from(PlaySession).where(...), remembering the ROM ids asked for."""

    def __init__(self):
        self.rom_ids = None

    def select_from(self, _):
        return self

    def where(self, cond):
        self.rom_ids = cond
        return self


class _Session:
    def __init__(self, sessions_by_rom):
        self.sessions_by_rom = sessions_by_rom

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def scalar(self, query):
        return sum(self.sessions_by_rom.get(i, 0) for i in query.rom_ids)


def _load(tmp: Path, sessions_by_rom):
    index_mod = types.SimpleNamespace(ROMSHELF_DIR=tmp)
    play_session = types.SimpleNamespace(PlaySession=types.SimpleNamespace(rom_id=types.SimpleNamespace(in_=lambda ids: list(ids))))
    stubs = {
        "sqlalchemy": types.SimpleNamespace(func=types.SimpleNamespace(count=lambda: None), select=lambda *_: _Query()),
        "handler": types.ModuleType("handler"),
        "handler.database": types.ModuleType("handler.database"),
        "handler.database.base_handler": types.SimpleNamespace(sync_session=_Session(sessions_by_rom)),
        "handler.romshelf": types.ModuleType("handler.romshelf"),
        "handler.romshelf.index": index_mod,
        "models": types.ModuleType("models"),
        "models.play_session": play_session,
    }
    with mock.patch.dict(sys.modules, stubs):
        spec = importlib.util.spec_from_file_location("handler.romshelf.counters", _PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_downloads_are_kept_in_a_sqlite_file(self):
        mod = _load(self.tmp, {})
        self.assertEqual(mod.downloads("rhdn-1"), 0)
        mod.add_download("rhdn-1")
        mod.add_download("rhdn-1")
        mod.add_download("smwc-2")
        # A fresh load reads the same file, as after a RomM restart.
        mod = _load(self.tmp, {})
        self.assertEqual(mod.downloads("rhdn-1"), 2)
        self.assertEqual(mod.downloads("smwc-2"), 1)
        self.assertTrue((self.tmp / "counts.sqlite").exists())

    def test_plays_are_the_play_sessions_of_installed_roms(self):
        mod = _load(self.tmp, {11: 3, 12: 2, 99: 7})
        records = [
            {"hack_id": "rhdn-1", "rom_id": 11},
            {"hack_id": "rhdn-1", "rom_id": 12},
            {"hack_id": "rhdn-1", "rom_id": None},  # install still being scanned
            {"hack_id": "other", "rom_id": 99},
        ]
        mod.add_download("rhdn-1")
        self.assertEqual(mod.counts("rhdn-1", records), {"downloads": 1, "plays": 5})
        self.assertEqual(mod.counts("never-installed", records), {"downloads": 0, "plays": 0})


if __name__ == "__main__":
    unittest.main()
