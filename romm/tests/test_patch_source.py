"""Tests for how the RomM integration reads patch downloads. Run: python -m unittest discover romm/tests"""

import importlib.util
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

# patch_source imports RomM modules; stand in for the few names it uses.
for name, attrs in {
    "httpx2": {"HTTPError": Exception, "URL": None},
    "config": {"ROM_PATCHER_MAX_FILE_SIZE_BYTES": 64 * 1024 * 1024},
    "utils": {},
    "utils.context": {"ctx_httpx_client": None},
    "utils.rom_patcher": {"SUPPORTED_PATCH_EXTENSIONS": {".ips", ".bps", ".ups", ".xdelta"}},
    "handler": {},
    "handler.romshelf": {"__path__": []},
    "handler.romshelf.index": {"USER_AGENT": "test"},
}.items():
    sys.modules.setdefault(name, types.ModuleType(name)).__dict__.update(attrs)

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/patch_source.py"
_spec = importlib.util.spec_from_file_location("handler.romshelf.patch_source", _PATH)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

BPS = b"BPS1" + b"\x80" * 3 + b"\x00" * 12


class PatchesInTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def zip(self, members: dict[str, bytes]) -> Path:
        path = self.dir / "hack.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return path

    def test_finds_the_patch(self):
        found = ps.patches_in(self.zip({"Hack/hack.bps": BPS, "readme.txt": b"hi"}), self.dir / "w")
        self.assertEqual([f.name for f in found], ["Hack/hack.bps"])

    def test_refuses_an_archive_with_a_program(self):
        with self.assertRaises(ps.PatchSourceError) as cm:
            ps.patches_in(self.zip({"hack.bps": BPS, "Flips.exe": b"MZ\x90\x00"}), self.dir / "w")
        self.assertEqual(cm.exception.code, "unsafe")

    def test_refuses_a_renamed_windows_program(self):
        with self.assertRaises(ps.PatchSourceError):
            ps.patches_in(self.zip({"hack.bps": BPS, "data.bin": b"MZ\x90\x00"}), self.dir / "w")

    def test_refuses_a_self_extracting_download(self):
        exe = self.dir / "hack.exe"
        exe.write_bytes(b"MZ\x90\x00" + b"\x00" * 60)
        with self.assertRaises(ps.PatchSourceError):
            ps.patches_in(exe, self.dir / "w")


if __name__ == "__main__":
    unittest.main()
