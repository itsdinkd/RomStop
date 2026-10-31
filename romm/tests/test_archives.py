"""Tests for finding and unpacking the game file in archived ROMs. Run: python -m unittest discover romm/tests"""

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/archives.py"
_spec = importlib.util.spec_from_file_location("romshelf_archives", _PATH)
a = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a)

ROM = b"NES\x1a" + bytes(range(256)) * 64


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def zip(self, members):
        path = self.tmp / "game.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return path

    def test_is_archive(self):
        self.assertTrue(a.is_archive("Mega Man 2 (USA).ZIP"))
        self.assertTrue(a.is_archive("x.7z"))
        self.assertFalse(a.is_archive("x.nes"))

    def test_picks_the_game_over_a_bigger_readme(self):
        path = self.zip({"readme.txt": b"x" * 100_000, "cover.png": b"x" * 90_000, "Game (USA).nes": ROM})
        self.assertEqual(a.rom_member(path).name, "Game (USA).nes")

    def test_extracts_the_game_file(self):
        path = self.zip({"readme.txt": b"hi", "sub/Game (USA).nes": ROM})
        out = a.extract_rom(path, self.tmp / "work")
        self.assertEqual(out.name, "Game (USA).nes")
        self.assertEqual(out.read_bytes(), ROM)

    def test_named_member(self):
        path = self.zip({"a.nes": ROM, "b.nes": ROM + b"more"})
        self.assertEqual(a.extract_rom(path, self.tmp / "w", "a.nes").read_bytes(), ROM)
        with self.assertRaises(a.ArchiveError):
            a.rom_member(path, "c.nes")

    def test_names_cannot_escape_the_work_folder(self):
        path = self.zip({"../../evil.nes": ROM})
        out = a.extract_rom(path, self.tmp / "work")
        self.assertEqual(out.parent, self.tmp / "work")

    def test_size_limit(self):
        path = self.zip({"g.nes": ROM})
        with self.assertRaises(a.ArchiveError):
            a.extract_rom(path, self.tmp / "w", max_bytes=10)

    def test_reads_the_game_without_unpacking(self):
        path = self.zip({"readme.txt": b"hi", "sub/Game (USA).nes": ROM})
        self.assertEqual(a.read_rom(path), ROM)
        with self.assertRaises(a.ArchiveError):
            a.read_rom(path, max_bytes=10)

    def test_bad_zip(self):
        path = self.tmp / "bad.zip"
        path.write_bytes(b"not a zip")
        with self.assertRaises(a.ArchiveError):
            a.rom_member(path)

    @unittest.skipUnless(shutil.which("7zz") or shutil.which("7z"), "7-Zip not installed")
    def test_7z(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "Game (USA).sfc").write_bytes(ROM * 4)
        (src / "info.nfo").write_bytes(b"x" * 500_000)
        path = self.tmp / "game.7z"
        subprocess.run([a._SEVEN_ZIP, "a", "-bd", str(path), "Game (USA).sfc", "info.nfo"], cwd=src, check=True, capture_output=True)
        out = a.extract_rom(path, self.tmp / "work")
        self.assertEqual(out.name, "Game (USA).sfc")
        self.assertEqual(out.read_bytes(), ROM * 4)
        self.assertEqual(a.read_rom(path), ROM * 4)


if __name__ == "__main__":
    unittest.main()
