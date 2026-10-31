"""Tests for the save import planner that runs inside RomM. Run: python -m unittest discover romm/tests"""

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/saves.py"
_spec = importlib.util.spec_from_file_location("romshelf_saves", _PATH)
s = importlib.util.module_from_spec(_spec)
sys.modules["romshelf_saves"] = s
_spec.loader.exec_module(s)

CORE_DIRS = {"Snes9x": "snes9x", "mGBA": "mgba", "Beetle PSX HW": "mednafen_psx_hw"}
NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)


def game(gid, fs_name, platform="snes", name=None, crc=None, pid=None):
    stem = fs_name.rsplit(".", 1)[0]
    return s.LibraryGame(
        id=gid,
        name=name or stem.split(" (")[0],
        fs_name=fs_name,
        fs_name_no_ext=stem,
        platform_id=pid or {"snes": 1, "gba": 2, "psx": 3, "nds": 4, "genesis": 5}[platform],
        platform_slug=platform,
        platform_fs_slug=platform,
        platform_name=platform.upper(),
        crc32s=frozenset({crc} if crc else ()),
    )


LIB = s.Library([
    game(1, "Super Mario World (USA).sfc", crc="b19ed489"),
    game(2, "Pokemon Emerald (USA, Europe).gba", "gba"),
    game(3, "Final Fantasy VII (USA) (Disc 1).chd", "psx"),
    game(4, "Tetris (World).sfc"),
    game(5, "Tetris (World).gba", "gba"),
    game(6, "Super Mario World [RomStop] (v1.2).sfc", name="Kaizo Mario"),
])


def plan(files, existing=(), **kw):
    kw.setdefault("core_dirs", CORE_DIRS)
    kw.setdefault("now", NOW)
    return s.plan([s.DeviceFile(**f) if isinstance(f, dict) else s.DeviceFile(f) for f in files], LIB, existing, **kw)


def row(rows, name):
    return next(r for r in rows if r["name"] == name)


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(s.classify("saves/Game.srm"), "save")
        self.assertEqual(s.classify("states/Snes9x/Game.state"), "state")
        self.assertEqual(s.classify("states/Game.state12"), "state")
        self.assertEqual(s.classify("states/Game.state.auto"), "state")
        self.assertEqual(s.classify("states/Game.state1.png"), "screenshot")
        self.assertEqual(s.classify("mGBA/Game.ss3"), "state")
        self.assertEqual(s.classify("duckstation/savestates/Game_1.sav"), "state")
        self.assertEqual(s.classify("PCSX2/memcards/Mcd001.ps2"), "shared_card")
        self.assertEqual(s.classify("PPSSPP/memstick/PSP/SAVEDATA/ULUS10041/DATA.BIN"), "folder_save")
        self.assertIsNone(s.classify("RetroArch/system/scph5501.bin"))
        self.assertIsNone(s.classify("RetroArch/saves/Game.sfc"))
        self.assertIsNone(s.classify("RetroArch/screenshots/Game.png"))
        # A picked folder named like a skipped one still counts.
        self.assertEqual(s.classify("system/Game.srm"), "save")

    def test_split_and_tag(self):
        self.assertEqual(s.split_name("state", "Game.state.auto"), ("Game", ".state.auto"))
        self.assertEqual(s.split_name("screenshot", "Game.state2.png"), ("Game", ".state2.png"))
        self.assertEqual(s.split_name("save", "Game (USA).srm"), ("Game (USA)", ".srm"))
        self.assertEqual(s.tagged_name("Game.state.auto", "state", NOW), "Game [2026-10-29_12-00-00].state.auto")
        self.assertEqual(s.tagged_name("Game [2025-01-01_00-00-00].srm", "save", NOW), "Game [2026-10-29_12-00-00].srm")


class MatchTests(unittest.TestCase):
    def test_exact_name_needs_no_rename(self):
        r = row(plan(["RetroArch/saves/Super Mario World (USA).srm"]), "Super Mario World (USA).srm")
        self.assertEqual((r["status"], r["match"], r["rename"]), ("new", "exact", False))
        self.assertEqual(r["rom"]["id"], 1)

    def test_different_name_is_renamed_to_the_rom(self):
        r = plan(["saves/final fantasy 7.srm"])[0]
        self.assertEqual(r["match"], "similar")
        self.assertTrue(r["rename"])
        self.assertEqual(r["target_name"], "Final Fantasy VII (USA) (Disc 1).srm")
        # A RomStop hack of the same game makes a loose name ambiguous.
        self.assertEqual(plan(["saves/super mario world.srm"])[0]["status"], "ambiguous")

    def test_case_only_difference(self):
        r = plan(["saves/SUPER MARIO WORLD (USA).srm"])[0]
        self.assertEqual((r["match"], r["target_name"]), ("name", "Super Mario World (USA).srm"))

    def test_loose_name_and_keep_original(self):
        r = plan(["saves/Pokemon Emerald.srm"])[0]
        self.assertEqual((r["match"], r["target_name"]), ("similar", "Pokemon Emerald (USA, Europe).srm"))
        kept = plan(["saves/Pokemon Emerald.srm"], overrides={"saves/Pokemon Emerald.srm": s.Override(rename=False)})[0]
        self.assertEqual((kept["rename"], kept["target_name"]), (False, "Pokemon Emerald.srm"))

    def test_device_rom_checksum_finds_renamed_game(self):
        files = ["saves/SMW hack test.srm"]
        self.assertEqual(plan(files)[0]["status"], "unmatched")
        r = plan(files, device_roms=[s.DeviceRom("roms/snes/SMW hack test.sfc", "B19ED489")])[0]
        self.assertEqual((r["match"], r["rom"]["id"]), ("checksum", 1))

    def test_same_name_on_two_systems_asks_unless_a_hint_settles_it(self):
        self.assertEqual(plan(["saves/Tetris (World).srm"])[0]["status"], "ambiguous")
        by_folder = plan(["saves/Tetris (World).srm"], device_roms=[s.DeviceRom("ES-DE/ROMs/gba/Tetris (World).gba")])[0]
        self.assertEqual(by_folder["rom"]["id"], 5)
        by_save_folder = plan(["RetroArch/saves/snes/Tetris (World).srm"])[0]
        self.assertEqual(by_save_folder["rom"]["id"], 4)
        self.assertIsNone(by_save_folder["emulator"])  # a system folder is not a core
        one_system = plan(["saves/Tetris (World).srm"], platform_id=2)[0]
        self.assertEqual(one_system["rom"]["id"], 5)
        picked = plan(["saves/Tetris (World).srm"], overrides={"saves/Tetris (World).srm": s.Override(rom_id=4)})[0]
        self.assertEqual((picked["status"], picked["match"]), ("new", "chosen"))

    def test_romshelf_hack_names_keep_their_brackets(self):
        r = plan(["saves/Super Mario World [RomStop] (v1.2).srm"])[0]
        self.assertEqual((r["match"], r["rom"]["id"]), ("exact", 6))

    def test_duckstation_card_suffix(self):
        r = plan(["duckstation/memcards/final fantasy vii (usa) (disc 1)_1.mcd"])[0]
        self.assertEqual(r["target_name"], "Final Fantasy VII (USA) (Disc 1)_1.mcd")
        self.assertEqual(r["emulator"], "duckstation")

    def test_unsupported_rows(self):
        rows = plan(["PCSX2/memcards/Mcd001.ps2", "PPSSPP/memstick/PSP/SAVEDATA/ULUS10041/DATA.BIN"])
        self.assertEqual({r["status"] for r in rows}, {"unsupported"})


class EmulatorTests(unittest.TestCase):
    def test_core_folder(self):
        r = plan(["RetroArch/states/Snes9x/Super Mario World (USA).state1"])[0]
        self.assertEqual((r["emulator"], r["emulator_source"]), ("snes9x", "core folder"))
        self.assertEqual(r["target_name"], "Super Mario World (USA).state1")
        unknown = plan(["saves/Snes9x 2010/Super Mario World (USA).srm"])[0]
        self.assertEqual(unknown["emulator"], "Snes9x 2010")  # kept verbatim, like RomM's Cloud Sync

    def test_toggle_off_uses_the_system_choice(self):
        r = plan(["RetroArch/states/Snes9x/Super Mario World (USA).state1"], detect_emulator=False, platform_emulators={1: "bsnes"})[0]
        self.assertEqual((r["emulator"], r["emulator_source"]), ("bsnes", "your choice for this system"))

    def test_playlists_and_es_de(self):
        playlist = json.dumps({"items": [{"path": "D:/ROMs/gba/Pokemon Emerald (USA, Europe).zip#x.gba", "core_path": "C:\\RetroArch\\cores\\mgba_libretro.dll"}]})
        es_systems = """<systemList>
          <system><name>snes</name><command label="Snes9x - Current">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/snes9x_libretro.dll %ROM%</command>
            <command label="bsnes">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/bsnes_libretro.dll %ROM%</command></system>
          <system><name>psx</name><command label="Beetle PSX HW">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mednafen_psx_hw_libretro.dll %ROM%</command>
            <command label="DuckStation (Standalone)">%EMULATOR_DUCKSTATION% -batch %ROM%</command></system>
        </systemList>"""
        gamelist_psx = "<gameList><alternativeEmulator><label>DuckStation (Standalone)</label></alternativeEmulator></gameList>"
        gamelist_snes = "<gameList><game><path>./Super Mario World (USA).sfc</path><altemulator>bsnes</altemulator></game></gameList>"
        hints = s.emulator_hints([playlist], [("ES-DE/gamelists/psx/gamelist.xml", gamelist_psx), ("ES-DE/gamelists/snes/gamelist.xml", gamelist_snes)], [es_systems])
        rows = plan(["saves/Pokemon Emerald (USA, Europe).srm", "saves/Final Fantasy VII (USA) (Disc 1).srm", "saves/Super Mario World (USA).srm", "saves/Tetris (World).srm"],
                    hints=hints, platform_id=None, overrides={"saves/Tetris (World).srm": s.Override(rom_id=4)})
        self.assertEqual(row(rows, "Pokemon Emerald (USA, Europe).srm")["emulator"], "mgba")
        self.assertEqual(row(rows, "Final Fantasy VII (USA) (Disc 1).srm")["emulator"], "duckstation")
        self.assertEqual(row(rows, "Super Mario World (USA).srm")["emulator_source"], "ES-DE game setting")
        self.assertEqual(row(rows, "Super Mario World (USA).srm")["emulator"], "bsnes")
        self.assertEqual(row(rows, "Tetris (World).srm")["emulator"], "snes9x")  # the system's default command


class ExistingTests(unittest.TestCase):
    """Nothing in RomM is ever overwritten."""

    def test_same_bytes_are_skipped(self):
        existing = [s.ExistingAsset("save", 1, "snes9x", "Super Mario World (USA).srm", "abc")]
        r = plan([{"path": "saves/Snes9x/Super Mario World (USA).srm", "md5": "ABC"}], existing)[0]
        self.assertEqual(r["status"], "in_romm")

    def test_different_bytes_under_the_same_name_is_a_conflict(self):
        existing = [s.ExistingAsset("state", 1, "snes9x", "Super Mario World (USA).state1", "old")]
        r = plan([{"path": "states/Snes9x/Super Mario World (USA).state1", "md5": "new", "modified": NOW.timestamp()}], existing)[0]
        self.assertEqual(r["status"], "conflict")
        self.assertEqual(r["keep_both_name"], "Super Mario World (USA) [2026-10-29_12-00-00].state1")

    def test_other_emulator_is_not_a_conflict(self):
        existing = [s.ExistingAsset("save", 1, "bsnes", "Super Mario World (USA).srm", "old")]
        r = plan([{"path": "saves/Snes9x/Super Mario World (USA).srm", "md5": "new"}], existing)[0]
        self.assertEqual(r["status"], "new")

    def test_two_copies_in_one_import(self):
        files = [
            {"path": "a/saves/Snes9x/Super Mario World (USA).srm", "md5": "x", "modified": 2},
            {"path": "b/saves/Snes9x/Super Mario World (USA).srm", "md5": "x", "modified": 1},
            {"path": "c/saves/Snes9x/Super Mario World (USA).srm", "md5": "y", "modified": 0},
        ]
        statuses = {r["path"][0]: r["status"] for r in plan(files)}
        self.assertEqual(statuses, {"a": "new", "b": "in_romm", "c": "conflict"})

    def test_state_screenshot_rides_with_its_state(self):
        rows = plan(["states/Snes9x/Super Mario World (USA).state1", "states/Snes9x/Super Mario World (USA).state1.png"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["screenshot_path"], "states/Snes9x/Super Mario World (USA).state1.png")


if __name__ == "__main__":
    unittest.main()
