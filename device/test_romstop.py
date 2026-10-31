"""Tests for the device tool: python3 -m unittest discover device"""
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import romstop as rs  # noqa: E402

os.environ["ROMSTOP_QUIET"] = "1"


def vlq(n):
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        n -= 1


def u32(n):
    return (n & 0xFFFFFFFF).to_bytes(4, "little")


def footer(body, src, dst):
    head = body + u32(zlib.crc32(src)) + u32(zlib.crc32(dst))
    return head + u32(zlib.crc32(head))


def make_bps(src, dst):
    """SourceRead where bytes match, TargetRead where they differ."""
    body = bytearray(b"BPS1" + vlq(len(src)) + vlq(len(dst)) + vlq(0))
    i = 0
    while i < len(dst):
        same = i < len(src) and src[i] == dst[i]
        j = i
        while j < len(dst) and (j < len(src) and src[j] == dst[j]) == same:
            j += 1
        body += vlq(((j - i - 1) << 2) | (0 if same else 1))
        if not same:
            body += dst[i:j]
        i = j
    return footer(bytes(body), src, dst)


def make_ups(src, dst):
    body = bytearray(b"UPS1" + vlq(len(src)) + vlq(len(dst)))
    last = i = 0
    get = lambda b, k: b[k] if k < len(b) else 0  # noqa: E731
    while i < len(dst):
        if get(src, i) == dst[i]:
            i += 1
            continue
        body += vlq(i - last)
        while i < len(dst) and get(src, i) != dst[i]:
            body.append(get(src, i) ^ dst[i])
            i += 1
        body.append(0)
        i += 1
        last = i
    return footer(bytes(body), src, dst)


ROM = bytes(range(256)) * 4  # 1 KiB
HACKED = ROM[:100] + b"ROMSTOP" + ROM[108:] + b"extra"


class Patching(unittest.TestCase):
    def test_ips(self):
        patch = b"PATCH" + b"\x00\x00\x02\x00\x02\xaa\xbb" + b"\x00\x00\x0a\x00\x00\x00\x03\xff" + b"\x00\x04\x02\x00\x01\x99" + b"EOF"
        out = rs.apply_patch(ROM, patch)
        self.assertEqual(out[:4], bytes([0, 1, 0xAA, 0xBB]))
        self.assertEqual(out[10:14], bytes([0xFF, 0xFF, 0xFF, 13]))
        self.assertEqual(len(out), 1027)
        self.assertEqual(out[1026], 0x99)
        self.assertEqual(len(rs.apply_patch(ROM, b"PATCHEOF\x00\x00\x10")), 16)

    def test_bps_all_actions(self):
        target = ROM[:8] + b"HI" + ROM[20:24] + b"HI" + ROM[20:24]
        act = lambda cmd, n: vlq(((n - 1) << 2) | cmd)  # noqa: E731
        body = b"BPS1" + vlq(len(ROM)) + vlq(len(target)) + vlq(0) + act(0, 8) + act(1, 2) + b"HI" + act(2, 4) + vlq(20 * 2) + act(3, 6) + vlq(8 * 2)
        self.assertEqual(rs.apply_patch(ROM, footer(body, ROM, target)), target)

    def test_bps_and_ups_roundtrip(self):
        self.assertEqual(rs.apply_patch(ROM, make_bps(ROM, HACKED)), HACKED)
        self.assertEqual(rs.apply_patch(ROM, make_ups(ROM, HACKED)), HACKED)
        self.assertEqual(rs.patch_source_crc(make_bps(ROM, HACKED)), zlib.crc32(ROM))

    def test_wrong_base_and_corrupt_patch(self):
        with self.assertRaisesRegex(rs.RomStopError, "needs"):
            rs.apply_patch(bytes(1024), make_bps(ROM, HACKED))
        bad = bytearray(make_bps(ROM, HACKED))
        bad[-1] ^= 0xFF
        with self.assertRaisesRegex(rs.RomStopError, "corrupt"):
            rs.apply_patch(ROM, bytes(bad))

    def test_n64_byte_orders(self):
        z64 = b"\x80\x37\x12\x40" + bytes(range(60))
        v64 = bytearray(z64)
        v64[0::2], v64[1::2] = z64[1::2], z64[0::2]
        n64 = bytearray(z64)
        for k in range(4):
            n64[k::4] = z64[3 - k::4]
        self.assertEqual(rs.to_z64(bytes(v64)), z64)
        self.assertEqual(rs.to_z64(bytes(n64)), z64)
        self.assertIs(rs.to_z64(z64), z64)

    def test_name_score_prefers_exact_title(self):
        self.assertGreater(rs.name_score("Super Mario World (USA).sfc", "Super Mario World"), rs.name_score("Super Mario World 2 - Yoshi's Island (USA).sfc", "Super Mario World"))


class Device(unittest.TestCase):
    """End to end against a fake site (file://) and a fake ES-DE / Batocera layout."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.site = self.tmp / "site"
        (self.site / "device").mkdir(parents=True)
        (self.site / "patches" / "smw-hack").mkdir(parents=True)
        self.bps = make_bps(ROM, HACKED)
        (self.site / "device" / "media").mkdir()
        (self.site / "device" / "media" / "smw-hack.png").write_bytes(b"\x89PNG fake")
        (self.site / "patches" / "smw-hack" / "hack.bps").write_bytes(self.bps)
        self.index = {
            "format": 1,
            "consoles": {"snes": {"name": "Super Nintendo", "short": "SNES", "extensions": [".sfc", ".smc"]}, "nes": {"name": "NES", "short": "NES", "extensions": [".nes"]}},
            "hacks": [
                {"id": "smw-hack", "title": "Test Hack", "console": "snes", "cover": "device/media/smw-hack.png", "baseGame": "Super Test World", "baseRom": {"title": "Super Test World (USA)"},
                 "authors": ["Someone"], "type": "Complete", "version": "1.1", "updated": "2026-05-02", "summary": "A test.", "description": "A test hack.",
                 "rating": {"score": 4.5, "count": 10, "sample": False}, "page": "hack/smw-hack", "links": [{"label": "Source", "url": "https://example.org/hack"}],
                 "patches": [{"path": "patches/smw-hack/hack.bps", "name": "hack.bps", "format": "BPS", "size": len(self.bps), "crc32": "%08X" % zlib.crc32(self.bps), "sourceLabel": "example.org"}]},
                {"id": "nes-ips", "title": "NES Thing", "console": "nes", "baseGame": "Some Game", "baseRom": {"title": "Some Game (USA)", "region": "USA"},
                 "authors": ["A"], "type": "Improvement", "summary": "IPS.", "description": "", "page": "hack/nes-ips", "links": [], "patches": [], "noMirror": True},
            ],
        }
        self.write_index()
        self.data = self.tmp / "data"

    def write_index(self):
        (self.site / "device" / "index.json").write_text(json.dumps(self.index))

    def run_tool(self, *args):
        return rs.main(["--data-dir", str(self.data), *args])

    def esde(self):
        home = self.tmp / "ES-DE"
        (home / "settings").mkdir(parents=True)
        roms = self.tmp / "ROMs"
        (home / "settings" / "es_settings.xml").write_text('<?xml version="1.0"?>\n<string name="ROMDirectory" value="%s" />\n' % roms)
        (roms / "snes").mkdir(parents=True)
        (roms / "nes").mkdir(parents=True)
        self.assertEqual(self.run_tool("setup", "--frontend", "esde", "--esde-home", str(home), "--site", self.site.as_uri() + "/"), 0)
        return home, roms

    def test_esde_setup_lists_hacks(self):
        home, roms = self.esde()
        systems = ET.parse(home / "custom_systems" / "es_systems.xml").getroot()
        sys_ = systems.find("system")
        self.assertEqual(sys_.findtext("path"), "%ROMPATH%/romstop")
        self.assertIn("install %ROM%", sys_.findtext("command"))
        self.assertTrue((roms / "romstop" / "SNES" / "Test Hack.romstop").is_file())
        self.assertTrue((roms / "romstop" / "Refresh hack list.romstop").is_file())
        games = {g.findtext("path"): g for g in ET.parse(home / "gamelists" / "romstop" / "gamelist.xml").getroot()}
        g = games["./SNES/Test Hack.romstop"]
        self.assertEqual(g.findtext("rating"), "0.90")
        self.assertEqual(g.findtext("developer"), "Someone")
        self.assertIn("mirrored from example.org", g.findtext("desc"))
        self.assertIn("asked not to rehost", games["./NES/NES Thing.romstop"].findtext("desc"))
        for kind in ("covers", "miximages"):
            self.assertEqual((home / "downloaded_media" / "romstop" / kind / "SNES" / "Test Hack.png").read_bytes(), b"\x89PNG fake")

    def test_esde_install_strips_copier_header(self):
        home, roms = self.esde()
        (roms / "snes" / "Super Test World (USA).smc").write_bytes(bytes(512) + ROM)
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        out = roms / "snes" / "Test Hack (1.1) [RomStop].smc"
        self.assertEqual(out.read_bytes(), HACKED)
        game = ET.parse(home / "gamelists" / "snes" / "gamelist.xml").getroot().find("game")
        self.assertEqual(game.findtext("path"), "./" + out.name)
        self.assertEqual(game.findtext("name"), "Test Hack")
        self.assertEqual(game.findtext("releasedate"), "20260502T000000")

    def needs_bsnes(self):
        self.index["hacks"][0]["compatibility"] = {"works": ["bsnes"], "broken": ["snes9x"], "summary": "Works on bsnes. Doesn't work on Snes9x.", "names": {"bsnes": "bsnes", "snes9x": "Snes9x"}}
        self.index["hacks"][0]["play"] = {
            "esde": {"use": ["bsnes", "bsnes-hd", "bsnes (Standalone)"], "emulators": ["bsnes"], "ok": True, "pinned": True},
            "batocera": {"use": [{"emulator": "libretro", "core": "bsnes"}], "emulators": ["bsnes"], "ok": True, "pinned": True},
        }
        self.write_index()

    def test_esde_install_sets_the_emulator_the_page_asks_for(self):
        self.needs_bsnes()
        home, roms = self.esde()
        games = {g.findtext("path"): g for g in ET.parse(home / "gamelists" / "romstop" / "gamelist.xml").getroot()}
        desc = games["./SNES/Test Hack.romstop"].findtext("desc")
        self.assertIn("Emulators: Works on bsnes. Doesn't work on Snes9x.", desc)
        self.assertIn("run with bsnes", desc)
        (roms / "snes" / "Super Test World (USA).sfc").write_bytes(ROM)
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        game = ET.parse(home / "gamelists" / "snes" / "gamelist.xml").getroot().find("game")
        self.assertEqual(game.findtext("altemulator"), "bsnes")

    def test_esde_uses_a_label_this_install_has(self):
        self.needs_bsnes()
        home, roms = self.esde()
        custom = home / "custom_systems" / "es_systems.xml"
        tree = ET.parse(custom)
        snes = ET.SubElement(tree.getroot(), "system")
        ET.SubElement(snes, "name").text = "snes"
        for label in ("Snes9x - Current", "bsnes (Standalone)"):
            ET.SubElement(snes, "command", label=label).text = "x"
        tree.write(custom)
        (roms / "snes" / "Super Test World (USA).sfc").write_bytes(ROM)
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        game = ET.parse(home / "gamelists" / "snes" / "gamelist.xml").getroot().find("game")
        self.assertEqual(game.findtext("altemulator"), "bsnes (Standalone)")

    def test_no_emulator_set_without_compatibility_notes(self):
        home, roms = self.esde()
        (roms / "snes" / "Super Test World (USA).sfc").write_bytes(ROM)
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        self.assertIsNone(ET.parse(home / "gamelists" / "snes" / "gamelist.xml").getroot().find("game").find("altemulator"))

    def test_finds_base_rom_in_a_zip_in_another_folder(self):
        home, roms = self.esde()
        (roms / "misc").mkdir()
        with zipfile.ZipFile(roms / "misc" / "stw.zip", "w") as z:
            z.writestr("Super Test World (USA).sfc", ROM)
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        self.assertEqual((roms / "snes" / "Test Hack (1.1) [RomStop].sfc").read_bytes(), HACKED)

    def test_no_base_rom_reports_what_is_needed(self):
        home, roms = self.esde()
        (roms / "snes" / "Other Game.sfc").write_bytes(bytes(1024))
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 1)
        log = (self.data / "romstop.log").read_text()
        self.assertIn("Super Test World (USA) (CRC32 %08X)" % zlib.crc32(ROM), log)

    def test_link_only_hack_uses_patch_dropped_by_user(self):
        home, roms = self.esde()
        stub = roms / "romstop" / "NES" / "NES Thing.romstop"
        self.assertEqual(self.run_tool("install", str(stub)), 1)
        self.assertIn("No patch file", (self.data / "romstop.log").read_text())
        nes = b"NES\x1a" + bytes(12) + ROM
        (roms / "nes" / "Some Game (USA).nes").write_bytes(nes)
        (roms / "nes" / "Some Other Game (Japan).nes").write_bytes(nes)
        drop = roms / "romstop" / "patches" / "nes-ips"
        drop.mkdir(parents=True)
        (drop / "thing.ips").write_bytes(b"PATCH\x00\x00\x10\x00\x01\x42EOF")
        self.assertEqual(self.run_tool("install", str(stub)), 0)
        out = (roms / "nes" / "NES Thing [RomStop].nes").read_bytes()
        self.assertEqual(out[:4], b"NES\x1a")
        self.assertEqual(out[16], 0x42)

    def test_sync_removes_hacks_that_left_the_site(self):
        home, roms = self.esde()
        self.index["hacks"] = self.index["hacks"][:1]
        self.write_index()
        self.assertEqual(self.run_tool("sync"), 0)
        self.assertFalse((roms / "romstop" / "NES").exists())
        paths = [g.findtext("path") for g in ET.parse(home / "gamelists" / "romstop" / "gamelist.xml").getroot()]
        self.assertEqual(sorted(paths), ["./Refresh hack list.romstop", "./SNES/Test Hack.romstop"])

    def test_keeps_existing_custom_systems(self):
        home = self.tmp / "ES-DE"
        (home / "custom_systems").mkdir(parents=True)
        (home / "custom_systems" / "es_systems.xml").write_text("<systemList><system><name>mine</name></system></systemList>")
        (self.tmp / "ROMs").mkdir()
        self.assertEqual(self.run_tool("setup", "--frontend", "esde", "--esde-home", str(home), "--roms", str(self.tmp / "ROMs"), "--site", self.site.as_uri()), 0)
        self.assertEqual(self.run_tool("setup", "--frontend", "esde", "--esde-home", str(home), "--roms", str(self.tmp / "ROMs"), "--site", self.site.as_uri()), 0)
        names = [s.findtext("name") for s in ET.parse(home / "custom_systems" / "es_systems.xml").getroot()]
        self.assertEqual(names, ["mine", "romstop"])
        self.assertEqual(self.run_tool("remove"), 0)
        names = [s.findtext("name") for s in ET.parse(home / "custom_systems" / "es_systems.xml").getroot()]
        self.assertEqual(names, ["mine"])

    def test_drag_and_drop_zip_works_without_setup(self):
        idx = self.tmp / "index.json"
        idx.write_text(json.dumps(self.index))
        exe = self.tmp / "fake.exe"
        exe.write_bytes(b"MZ")
        made = {z.stem: z for z in rs.package(idx, self.tmp / "pkg", exe=exe, site_root=self.site)}
        self.assertEqual(sorted(made), ["RomStop-ES-DE-SteamDeck-Linux", "RomStop-ES-DE-Windows", "RomStop-Knulli-Batocera"])
        with zipfile.ZipFile(made["RomStop-ES-DE-Windows"]) as z:
            names = z.namelist()
            self.assertIn("ROMs/romstop/romstop.exe", names)
            self.assertIn("ES-DE/gamelists/romstop/gamelist.xml", names)
            self.assertIn("ES-DE/downloaded_media/romstop/covers/SNES/Test Hack.png", names)
            self.assertIn("romstop.exe\" install %ROM%", z.read("ES-DE/custom_systems/es_systems.xml").decode())
        with zipfile.ZipFile(made["RomStop-Knulli-Batocera"]) as z:
            self.assertIn("python3 /userdata/roms/romstop/romstop.py install %ROM%", z.read("system/configs/emulationstation/es_systems_romstop.cfg").decode())
            self.assertIn("roms/romstop/gamelist.xml", z.namelist())
            self.assertIn("roms/romstop/SNES/Test Hack.romstop", z.namelist())
            self.assertIn("roms/romstop/images/SNES/Test Hack.png", z.namelist())

        # Drag the Steam Deck zip onto an existing ES-DE setup and launch an entry, with no setup step.
        dev = self.tmp / "device"
        with zipfile.ZipFile(made["RomStop-ES-DE-SteamDeck-Linux"]) as z:
            z.extractall(dev)
        (dev / "ES-DE" / "settings").mkdir(parents=True)
        (dev / "ES-DE" / "settings" / "es_settings.xml").write_text("<string name=\"ROMDirectory\" value=\"%s\" />" % (dev / "ROMs"))
        (dev / "ROMs" / "snes").mkdir()
        (dev / "ROMs" / "snes" / "stw.sfc").write_bytes(ROM)
        cache = self.data / "patches" / "smw-hack"  # as if downloaded earlier; the zips don't carry patches
        cache.mkdir(parents=True)
        (cache / "hack.bps").write_bytes(self.bps)
        os.environ["ESDE_APPDATA_DIR"] = str(dev / "ES-DE")
        old_self = rs.SELF
        rs.SELF = dev / "ROMs" / "romstop" / "romstop.py"
        try:
            self.assertEqual(self.run_tool("install", str(dev / "ROMs" / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        finally:
            rs.SELF = old_self
            del os.environ["ESDE_APPDATA_DIR"]
        self.assertEqual((dev / "ROMs" / "snes" / "Test Hack (1.1) [RomStop].sfc").read_bytes()[:100], HACKED[:100])
        self.assertTrue((dev / "ES-DE" / "gamelists" / "snes" / "gamelist.xml").is_file())

    def test_batocera_gamelists_live_in_rom_folders(self):
        roms = self.tmp / "userdata" / "roms"
        (roms / "snes").mkdir(parents=True)
        self.data.mkdir()
        cfg = {"frontend": "batocera", "roms": str(roms), "site": self.site.as_uri()}
        (self.data / "config.json").write_text(json.dumps(cfg))
        self.assertEqual(self.run_tool("sync"), 0)
        self.assertTrue((roms / "romstop" / "images" / "SNES" / "Test Hack.png").is_file())
        games = {g.findtext("path"): g for g in ET.parse(roms / "romstop" / "gamelist.xml").getroot()}
        self.assertEqual(games["./SNES/Test Hack.romstop"].findtext("image"), "./images/SNES/Test Hack.png")
        (roms / "snes" / "stw.sfc").write_bytes(ROM)
        rs.reload_later = lambda: None
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        self.assertTrue((roms / "snes" / "Test Hack (1.1) [RomStop].sfc").is_file())
        self.assertEqual(ET.parse(roms / "snes" / "gamelist.xml").getroot().find("game").findtext("name"), "Test Hack")

    def test_batocera_install_sets_per_game_core(self):
        self.needs_bsnes()
        roms = self.tmp / "userdata" / "roms"
        (roms / "snes").mkdir(parents=True)
        conf = self.tmp / "userdata" / "system" / "batocera.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text('global.ratio=auto\nsnes["Test Hack (1.1) [RomStop].sfc"].core=snes9x\n')
        self.data.mkdir()
        (self.data / "config.json").write_text(json.dumps({"frontend": "batocera", "roms": str(roms), "site": self.site.as_uri()}))
        self.assertEqual(self.run_tool("sync"), 0)
        (roms / "snes" / "stw.sfc").write_bytes(ROM)
        rs.reload_later = lambda: None
        self.assertEqual(self.run_tool("install", str(roms / "romstop" / "SNES" / "Test Hack.romstop")), 0)
        self.assertEqual(conf.read_text().splitlines(), [
            "global.ratio=auto",
            'snes["Test Hack (1.1) [RomStop].sfc"].emulator=libretro',
            'snes["Test Hack (1.1) [RomStop].sfc"].core=bsnes',
        ])


class Covers(unittest.TestCase):
    def test_cover_art_is_written_and_recorded_in_the_index(self):
        try:
            import covers
        except ImportError:
            self.skipTest("Pillow isn't installed")
        tmp = Path(tempfile.mkdtemp())
        idx = {"consoles": {"snes": {"short": "SNES", "colors": ["#7c5cff", "#3a2a8c"]}},
               "hacks": [{"id": "h1", "title": "The Legend of Zelda: Parallel Worlds", "console": "snes", "authors": ["SePH"]}]}
        (tmp / "index.json").write_text(json.dumps(idx))
        covers.main(tmp / "index.json", tmp / "media")
        from PIL import Image
        self.assertEqual(Image.open(tmp / "media" / "h1.png").size, (600, 800))
        self.assertEqual(json.loads((tmp / "index.json").read_text())["hacks"][0]["cover"], "device/media/h1.png")


if __name__ == "__main__":
    unittest.main()
