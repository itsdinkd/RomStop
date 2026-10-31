"""Tests for the RomStop matcher that runs inside RomM. Run: python -m unittest discover romm/tests"""

import importlib.util
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/matching.py"
_spec = importlib.util.spec_from_file_location("romshelf_matching", _PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

HACK = {
    "id": "mm2-plus",
    "console": "nes",
    "baseGame": "Mega Man 2",
    "baseRom": {"title": "Mega Man 2 (USA)", "region": "USA", "crc32": "0fcfc04d"},
}


def rom(fs_name, *, slug="nes", regions=(), crc=None, headerless=None, name=None, rid=1):
    files = (m.LibraryFile(id=rid * 10, file_name=fs_name, size=262160, crc32=crc, headerless_crc32=headerless),)
    stem = fs_name.rsplit(".", 1)[0]
    return m.LibraryRom(
        id=rid,
        platform_id=1,
        platform_slug=slug,
        name=name,
        fs_name=fs_name,
        fs_name_no_tags=stem.split(" (")[0],
        regions=regions,
        files=files,
    )


class MatchTests(unittest.TestCase):
    def test_checksum_is_exact(self):
        r = m.match_rom(HACK, rom("Mega Man 2 (USA).nes", crc="0fcfc04d"))
        self.assertEqual(r.level, m.MatchLevel.EXACT)
        self.assertFalse(r.strip_header)

    def test_headerless_checksum_is_exact_and_strips_header(self):
        r = m.match_rom(HACK, rom("mm2.nes", crc="deadbeef", headerless="0fcfc04d"))
        self.assertEqual(r.level, m.MatchLevel.EXACT)
        self.assertTrue(r.strip_header)

    def test_japanese_rom_is_wrong_version(self):
        r = m.match_rom(HACK, rom("Rockman 2 - Dr. Wily no Nazo (Japan).nes", name="Mega Man 2", crc="aaaa0000"))
        self.assertEqual(r.level, m.MatchLevel.WRONG_VERSION)
        self.assertEqual(r.reason, "region")

    def test_other_game_is_none(self):
        self.assertEqual(m.match_rom(HACK, rom("Mega Man 3 (USA).nes", crc="1")).level, m.MatchLevel.NONE)

    def test_other_console_is_none(self):
        self.assertEqual(m.match_rom(HACK, rom("Mega Man 2 (USA).sfc", slug="snes", crc="0fcfc04d")).level, m.MatchLevel.NONE)

    def test_famicom_platform_counts_as_nes(self):
        self.assertEqual(m.match_rom(HACK, rom("Mega Man 2 (USA).nes", slug="famicom", crc="0fcfc04d")).level, m.MatchLevel.EXACT)

    def test_no_checksum_uses_file_name_then_title_and_region(self):
        hack = {**HACK, "baseRom": {"title": "Mega Man 2 (USA)"}}
        self.assertEqual(m.match_rom(hack, rom("Mega Man 2 (USA).nes")).reason, "file name")
        r = m.match_rom(hack, rom("Mega Man II [!].nes", regions=("US",)))
        self.assertEqual((r.level, r.reason), (m.MatchLevel.LIKELY, "title and region"))
        self.assertEqual(m.match_rom(hack, rom("Mega Man 2 (Europe).nes")).level, m.MatchLevel.WRONG_VERSION)

    def test_revision_mismatch(self):
        hack = {**HACK, "baseRom": {"title": "Mega Man 2 (USA) (Rev 1)"}}
        r = m.match_rom(hack, rom("Mega Man 2 (USA).nes"))
        self.assertEqual((r.level, r.reason), (m.MatchLevel.WRONG_VERSION, "revision"))

    def test_world_dump_fits_any_region(self):
        hack = {**HACK, "baseRom": {"region": "USA"}}
        self.assertEqual(m.match_rom(hack, rom("Mega Man 2 (World).nes")).level, m.MatchLevel.LIKELY)

    def test_best_match_orders_and_drops_unrelated(self):
        roms = [rom("Mega Man 2 (Japan).nes", crc="1", rid=1), rom("Zelda (USA).nes", rid=2), rom("Mega Man 2 (USA).nes", crc="0fcfc04d", rid=3)]
        self.assertEqual([x.rom_id for x in m.best_match(HACK, roms)], [3, 1])

    def test_needed_rom_label(self):
        self.assertEqual(m.needed_rom_label(HACK), "Mega Man 2 (USA)")
        self.assertEqual(m.needed_rom_label({"baseGame": "Mega Man 2", "baseRom": {"region": "Japan"}}), "Mega Man 2 (Japan)")

    def test_header_size(self):
        self.assertEqual(m.header_size("nes", b"NES\x1a....", 40976), 16)
        self.assertEqual(m.header_size("nes", b"\x00\x00\x00\x00", 40960), 0)
        self.assertEqual(m.header_size("snes", b"", 1024 * 1024 + 512), 512)
        self.assertEqual(m.header_size("snes", b"", 1024 * 1024), 0)

    def test_normalize_title(self):
        self.assertEqual(m.normalize_title("The Legend of Zelda: A Link to the Past (USA)"), m.normalize_title("Legend of Zelda, A Link to the Past"))
        self.assertEqual(m.normalize_title("Mega Man II"), m.normalize_title("Mega Man 2"))


# Romhacking.net style: crc32 is what the patch expects (with the iNES header), headerlessCrc32 is No-Intro's.
RHDN = {
    "id": "mm2-rhdn",
    "console": "nes",
    "baseGame": "Mega Man 2",
    "baseRom": {"title": "Mega Man 2 (USA)", "crc32": "AAAA1111", "headerlessCrc32": "0fcfc04d"},
}


class HeaderlessChecksumTests(unittest.TestCase):
    def test_headered_file_matching_the_patch_is_exact_without_stripping(self):
        r = m.match_rom(RHDN, rom("Mega Man 2 (USA).nes", crc="aaaa1111", headerless="0fcfc04d"))
        self.assertEqual((r.level, r.strip_header), (m.MatchLevel.EXACT, False))

    def test_other_header_same_dump_is_exact_and_keeps_its_header(self):
        r = m.match_rom(RHDN, rom("Mega Man 2 (USA).nes", crc="bbbb2222", headerless="0fcfc04d"))
        self.assertEqual((r.level, r.strip_header), (m.MatchLevel.EXACT, False))

    def test_headerless_file_when_the_patch_needs_a_header(self):
        r = m.match_rom(RHDN, rom("Mega Man 2 (USA).nes", crc="0fcfc04d"))
        self.assertEqual((r.level, r.reason), (m.MatchLevel.WRONG_VERSION, "header"))

    def test_headerless_patch_strips_the_files_header(self):
        hack = {**RHDN, "baseRom": {"title": "Mega Man 2 (USA)", "crc32": "0FCFC04D", "headerlessCrc32": "0fcfc04d"}}
        r = m.match_rom(hack, rom("Mega Man 2 (USA).nes", crc="cccc3333", headerless="0fcfc04d"))
        self.assertEqual((r.level, r.strip_header), (m.MatchLevel.EXACT, True))
        r = m.match_rom(hack, rom("Mega Man 2 (USA).nes", crc="0fcfc04d"))
        self.assertEqual((r.level, r.strip_header), (m.MatchLevel.EXACT, False))

    def test_only_headerless_checksum_known(self):
        hack = {**RHDN, "baseRom": {"title": "Mega Man 2 (USA)", "headerlessCrc32": "0fcfc04d"}}
        self.assertEqual(m.match_rom(hack, rom("x.nes", crc="1", headerless="0fcfc04d")).level, m.MatchLevel.EXACT)
        self.assertEqual(m.match_rom(hack, rom("Mega Man 2 (Japan).nes", crc="1", headerless="2")).level, m.MatchLevel.WRONG_VERSION)


class LibraryMatcherTests(unittest.TestCase):
    def test_same_results_as_checking_every_rom(self):
        roms = [
            rom("Mega Man 2 (USA).nes", crc="aaaa1111", rid=1),
            rom("x.nes", crc="1", headerless="0fcfc04d", rid=2),
            rom("Mega Man II [!].nes", regions=("US",), rid=3),
            rom("Mega Man 2 (Europe).nes", rid=4),
            rom("Rockman 2 (Japan).nes", name="Mega Man 2", rid=5),
            rom("Zelda (USA).nes", crc="0fcfc04d", rid=6),
            rom("Mega Man 2 (USA).sfc", slug="snes", crc="0fcfc04d", rid=7),
            rom("Mega Man 2 (USA) (Rev 1).nes", rid=8),
        ]
        hacks = [
            HACK,
            RHDN,
            {**HACK, "baseRom": {"title": "Mega Man 2 (USA)"}},
            {**HACK, "baseRom": {"title": "Mega Man 2 (USA) (Rev 1)"}},
            {**HACK, "baseRom": {"region": "USA"}},
            {**RHDN, "baseRom": {"title": "Mega Man 2 (USA)", "crc32": "0FCFC04D"}},
            {"id": "z", "console": "nes", "baseGame": "Zelda", "baseRom": {"title": "Zelda (USA)"}},
        ]
        lm = m.LibraryMatcher(roms)
        for hack in hacks:
            want = sorted((x.rom_id, x.level, x.reason) for x in (m.match_rom(hack, r) for r in roms) if x.level != m.MatchLevel.NONE)
            got = sorted((x.rom_id, x.level, x.reason) for x in lm.matches(hack))
            self.assertEqual(got, want, hack)

    def test_version_base_rom(self):
        lm = m.LibraryMatcher([rom("Something.nes", crc="deadbeef", rid=9)])
        self.assertEqual([x.rom_id for x in lm.matches(HACK, {"crc32": "DEADBEEF"})], [9])


if __name__ == "__main__":
    unittest.main()
