"""Tests for installing Wii Riivolution and Vita rePatch mods. Run: python -m unittest discover romm/tests"""

import importlib.util
import json
import shutil
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "overlay/backend/handler/romshelf/modpatch.py"
_spec = importlib.util.spec_from_file_location("romshelf_modpatch", _PATH)
mp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mp)

XML = """<wiidisc version="1" root="/NewerSMBW">
  <id game="SMN"><region type="E"/><region type="P"/></id>
  <options>
    <section name="Newer Super Mario Bros. Wii">
      <option name="Newer Super Mario Bros. Wii" id="newer" default="1">
        <choice name="Enabled"><patch id="NewerFiles"/></choice>
      </option>
      <option name="Debug"><choice name="On"/></option>
    </section>
  </options>
  <patch id="NewerFiles"><folder external="/NewerRes" disc="/Stage/"/></patch>
</wiidisc>"""


def sfo(title_id: str) -> bytes:
    key, val = b"TITLE_ID\0", title_id.encode() + b"\0"
    keys = 20 + 16
    data = keys + len(key) + (-(keys + len(key)) % 4)
    b = bytearray(data + len(val))
    struct.pack_into("<4sIIII", b, 0, b"\0PSF", 0x101, keys, data, 1)
    struct.pack_into("<HHIII", b, 20, 0, 0x0204, len(val), 16, 0)
    b[keys : keys + len(key)] = key
    b[data:] = val
    return bytes(b)


class ModPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def zip(self, name, members):
        path = self.tmp / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for n, data in members.items():
                zf.writestr(n, data)
        return path

    def test_refuses_programs_and_console_only_mods(self):
        with self.assertRaisesRegex(mp.ModError, "program"):
            mp.unpack_mod(self.zip("a.zip", {"riivolution/a.xml": XML, "setup.exe": b"MZ"}), "wii", self.tmp / "a")
        with self.assertRaisesRegex(mp.ModError, "real console"):
            mp.unpack_mod(self.zip("b.zip", {"tai/mod.suprx": b"x"}), "vita", self.tmp / "b")
        with self.assertRaisesRegex(mp.ModError, "real console"):
            mp.unpack_mod(self.zip("c.zip", {"apps/boot.dol": b"x"}), "wii", self.tmp / "c")
        # A PowerPC ELF is console code, not a desktop program.
        ppc = b"\x7fELF\x01\x02\x01" + bytes(11) + b"\x00\x14" + bytes(44)
        files = mp.unpack_mod(self.zip("d.zip", {"riivolution/a.xml": XML, "code/loader.elf": ppc}), "wii", self.tmp / "d")
        self.assertEqual(sorted(files), ["code/loader.elf", "riivolution/a.xml"])

    def test_reads_wii_game_ids(self):
        iso = bytearray(0x8010)
        iso[0:6] = b"SMNE01"
        self.assertEqual(mp.wii_game_id(bytes(iso)), "SMNE01")
        rvz = bytearray(0x8010)
        rvz[0:4], rvz[0x58:0x5E] = b"RVZ\x01", b"RMGP01"
        self.assertEqual(mp.wii_game_id(bytes(rvz)), "RMGP01")

    def test_builds_a_dolphin_launcher_next_to_the_disc(self):
        disc = self.tmp / "roms/wii/New Super Mario Bros. Wii (USA).iso"
        disc.parent.mkdir(parents=True)
        disc.write_bytes(b"SMNE01" + bytes(0x8000))
        files = mp.unpack_mod(self.zip("m.zip", {"NewerSMBW/riivolution/Newer.xml": XML, "NewerSMBW/NewerRes/a.arc": b"\x09"}), "wii", self.tmp / "m")
        folder = self.tmp / "roms/wii/Newer (USA) [RomStop].json"
        r = mp.build_wii(disc=disc, files=files, hack_id="gb-newer", title="Newer: Wii", folder=folder)
        d = json.loads((folder / r.launcher).read_text())
        self.assertEqual(r.launcher, "Newer (USA) [RomStop].json")
        self.assertEqual(d["base-file"], "../New Super Mario Bros. Wii (USA).iso")
        self.assertEqual(d["riivolution"]["patches"][0]["xml"], "RomStop/gb-newer/NewerSMBW/riivolution/Newer.xml")
        self.assertEqual(d["riivolution"]["patches"][0]["root"], "RomStop/gb-newer/NewerSMBW")
        self.assertEqual(d["riivolution"]["patches"][0]["options"][0], {"section-name": "Newer Super Mario Bros. Wii", "option-id": "newer", "option-name": "Newer Super Mario Bros. Wii", "choice": 1})
        self.assertTrue((folder / "RomStop/gb-newer/NewerSMBW/NewerRes/a.arc").exists())
        jp = self.tmp / "jp.iso"
        jp.write_bytes(b"SMNJ01" + bytes(0x8000))
        with self.assertRaisesRegex(mp.ModError, "for SMN"):
            mp.build_wii(disc=jp, files=files, hack_id="x", title="x", folder=self.tmp / "x")
        loose = mp.build_wii(disc=disc, files=files, hack_id="gb-newer", title="Newer: Wii", folder=self.tmp / "roms/wii/loose")
        self.assertEqual(loose.launcher, "Newer Wii.json")

    def test_lays_a_repatch_mod_over_the_vpk(self):
        vpk = self.zip("game.vpk", {"eboot.bin": b"EBOOT", "sce_sys/param.sfo": sfo("PCSE00491"), "data/a.bin": b"old"})
        files = mp.unpack_mod(self.zip("mod.zip", {"rePatch/PCSE00491/data/a.bin": b"new", "rePatch/PCSE00491/data/b.bin": b"added"}), "vita", self.tmp / "m")
        out = self.tmp / "out.vpk"
        r = mp.build_vita(dump=vpk, files=files, out=out)
        self.assertEqual((r.title_id, r.replaced, r.added), ("PCSE00491", 1, 1))
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.read("data/a.bin"), b"new")
            self.assertEqual(z.read("data/b.bin"), b"added")
            self.assertEqual(z.read("eboot.bin"), b"EBOOT")
        other = mp.unpack_mod(self.zip("o.zip", {"rePatch/PCSB00566/x": b"x"}), "vita", self.tmp / "o")
        with self.assertRaisesRegex(mp.ModError, "PCSB00566.*PCSE00491"):
            mp.build_vita(dump=vpk, files=other, out=self.tmp / "o.vpk")


if __name__ == "__main__":
    unittest.main()
