#!/usr/bin/env python3
"""RomStop for EmulationStation DE and Knulli/Batocera.

Adds a "RomStop" system to the frontend with one entry per hack on the site. Launching an entry
finds the hack's base ROM among the ROMs already on this device (by checksum), applies the patch
mirrored on the site, saves the result in that console's ROM folder and adds it to its game list.

Base ROMs are never downloaded: only files already on the device are read.

    python3 romstop.py setup      # detect the frontend, add the system, fetch the hack list
    python3 romstop.py sync       # refresh the hack list
    python3 romstop.py install <entry.romstop>
    python3 romstop.py remove     # take the RomStop system out again (installed hacks stay)

Standard library only, Python 3.8+.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

VERSION = "1.0.0"
DEFAULT_SITE = "https://itsdinkd.github.io/Romshelf/"
INDEX_FORMAT = 1
EXT = ".romstop"
REFRESH_STUB = "Refresh hack list" + EXT
MAX_ROM_BYTES = 256 * 1024 * 1024
BATOCERA_API = "http://127.0.0.1:1234"

# RomStop console id -> ROM folder names, most common first.
ESDE_FOLDERS = {
    "nes": ["nes", "famicom"], "snes": ["snes", "sfc", "snesna"], "n64": ["n64"], "gc": ["gc"],
    "gb": ["gb"], "gbc": ["gbc"], "gba": ["gba"], "nds": ["nds"], "vb": ["virtualboy"],
    "sms": ["mastersystem"], "genesis": ["genesis", "megadrive"], "segacd": ["segacd", "megacd"],
    "32x": ["sega32x"], "saturn": ["saturn"], "dreamcast": ["dreamcast"], "gg": ["gamegear"],
    "pce": ["pcengine", "tg16"], "neogeo": ["neogeo"], "psx": ["psx"], "ps2": ["ps2"], "psp": ["psp"],
    "wii": ["wii"], "vita": ["psvita"],
    "atari2600": ["atari2600"], "lynx": ["atarilynx"], "msx": ["msx", "msx1"], "arcade": ["arcade", "mame"],
}
BATOCERA_FOLDERS = {
    **ESDE_FOLDERS,
    "nes": ["nes"], "snes": ["snes"], "gc": ["gamecube"], "genesis": ["megadrive"], "segacd": ["segacd"],
    "pce": ["pcengine"], "lynx": ["lynx"], "msx": ["msx1", "msx"], "arcade": ["mame", "fbneo"],
}
PATCHABLE = ("IPS", "BPS", "UPS")


# Set when running as the packaged Windows romstop.exe (PyInstaller) rather than as a script.
FROZEN = bool(getattr(sys, "frozen", False))
SELF = Path(sys.executable if FROZEN else os.path.abspath(__file__))


class RomStopError(Exception):
    pass


# ---------------------------------------------------------------- patching

def _vlq(data: bytes, pos: int):
    value, shift = 0, 1
    while True:
        if pos >= len(data):
            raise RomStopError("Patch ended unexpectedly")
        x = data[pos]
        pos += 1
        value += (x & 0x7F) * shift
        if x & 0x80:
            return value, pos
        shift <<= 7
        value += shift


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def hex32(n: int) -> str:
    return "%08X" % n


def patch_kind(patch: bytes):
    if patch[:5] == b"PATCH":
        return "IPS"
    if patch[:4] == b"BPS1":
        return "BPS"
    if patch[:4] == b"UPS1":
        return "UPS"
    return None


def patch_source_crc(patch: bytes):
    """CRC32 of the ROM a BPS or UPS patch was made against. IPS records none."""
    if patch_kind(patch) in ("BPS", "UPS") and len(patch) >= 16:
        return int.from_bytes(patch[-12:-8], "little")
    return None


def apply_ips(rom: bytes, patch: bytes) -> bytes:
    pos, out, truncate = 5, bytearray(rom), None
    while True:
        if pos + 3 > len(patch):
            raise RomStopError("IPS patch ended unexpectedly")
        if patch[pos:pos + 3] == b"EOF":
            pos += 3
            if len(patch) - pos == 3:
                truncate = int.from_bytes(patch[pos:pos + 3], "big")
            break
        offset = int.from_bytes(patch[pos:pos + 3], "big")
        size = int.from_bytes(patch[pos + 3:pos + 5], "big")
        pos += 5
        if size == 0:
            size = int.from_bytes(patch[pos:pos + 2], "big")
            chunk = bytes([patch[pos + 2]]) * size
            pos += 3
        else:
            chunk = patch[pos:pos + size]
            if len(chunk) != size:
                raise RomStopError("IPS patch ended unexpectedly")
            pos += size
        if offset + size > len(out):
            out.extend(b"\0" * (offset + size - len(out)))
        out[offset:offset + size] = chunk
    if truncate is not None:
        del out[truncate:]
    return bytes(out)


def _footer(patch: bytes, name: str):
    if crc32(patch[:-4]) != int.from_bytes(patch[-4:], "little"):
        raise RomStopError("%s patch is corrupt (patch checksum mismatch)" % name)
    return int.from_bytes(patch[-12:-8], "little"), int.from_bytes(patch[-8:-4], "little")


def apply_bps(rom: bytes, patch: bytes) -> bytes:
    if len(patch) < 19:
        raise RomStopError("BPS patch is too small")
    source_crc, target_crc = _footer(patch, "BPS")
    if crc32(rom) != source_crc:
        raise RomStopError("Base ROM checksum is %s, the patch needs %s" % (hex32(crc32(rom)), hex32(source_crc)))
    pos = 4
    _, pos = _vlq(patch, pos)
    target_size, pos = _vlq(patch, pos)
    meta, pos = _vlq(patch, pos)
    pos += meta
    out = bytearray(target_size)
    out_pos = source_rel = target_rel = 0
    end = len(patch) - 12
    while pos < end:
        data, pos = _vlq(patch, pos)
        cmd, length = data & 3, (data >> 2) + 1
        if out_pos + length > target_size:
            raise RomStopError("BPS patch writes past the end of the output")
        if cmd == 0:
            chunk = rom[out_pos:out_pos + length]
            out[out_pos:out_pos + len(chunk)] = chunk
            out_pos += length
        elif cmd == 1:
            out[out_pos:out_pos + length] = patch[pos:pos + length]
            pos += length
            out_pos += length
        else:
            d, pos = _vlq(patch, pos)
            delta = -(d >> 1) if d & 1 else d >> 1
            if cmd == 2:
                source_rel += delta
                chunk = rom[source_rel:source_rel + length]
                out[out_pos:out_pos + len(chunk)] = chunk
                source_rel += length
                out_pos += length
            else:
                target_rel += delta
                for _ in range(length):  # may overlap with what it writes, so byte by byte
                    out[out_pos] = out[target_rel]
                    out_pos += 1
                    target_rel += 1
    if crc32(out) != target_crc:
        raise RomStopError("Patched ROM checksum doesn't match what the patch expects")
    return bytes(out)


def apply_ups(rom: bytes, patch: bytes) -> bytes:
    if len(patch) < 16:
        raise RomStopError("UPS patch is too small")
    source_crc, target_crc = _footer(patch, "UPS")
    if crc32(rom) != source_crc:
        raise RomStopError("Base ROM checksum is %s, the patch needs %s" % (hex32(crc32(rom)), hex32(source_crc)))
    pos = 4
    _, pos = _vlq(patch, pos)
    target_size, pos = _vlq(patch, pos)
    out = bytearray(rom[:target_size].ljust(target_size, b"\0"))
    at, end = 0, len(patch) - 12
    while pos < end:
        skip, pos = _vlq(patch, pos)
        at += skip
        while pos < end:
            x = patch[pos]
            pos += 1
            if x == 0:
                break
            if at < target_size:
                out[at] ^= x
            at += 1
        at += 1
    if crc32(out) != target_crc:
        raise RomStopError("Patched ROM checksum doesn't match what the patch expects")
    return bytes(out)


def apply_patch(rom: bytes, patch: bytes) -> bytes:
    kind = patch_kind(patch)
    if kind == "IPS":
        return apply_ips(rom, patch)
    if kind == "BPS":
        return apply_bps(rom, patch)
    if kind == "UPS":
        return apply_ups(rom, patch)
    raise RomStopError("Unsupported patch format (supported: IPS, BPS, UPS)")


# ---------------------------------------------------------------- ROM variants

def to_z64(rom: bytes) -> bytes:
    """Byte-swapped (.v64) and little-endian (.n64) N64 dumps to big-endian .z64, which patches target."""
    head = rom[:4]
    if head == b"\x37\x80\x40\x12":
        a = bytearray(rom)
        a[0::2], a[1::2] = rom[1::2], rom[0::2]
        return bytes(a)
    if head == b"\x40\x12\x37\x80":
        a = bytearray(rom)
        a[0::4], a[1::4], a[2::4], a[3::4] = rom[3::4], rom[2::4], rom[1::4], rom[0::4]
        return bytes(a)
    return rom


def variants(console: str, rom: bytes):
    """The forms of a dump a patch might target: as-is, without a copier/iNES header, or N64 in .z64 order."""
    out = {"raw": rom}
    if console == "n64":
        z = to_z64(rom)
        if z is not rom:
            out["z64"] = z
    if len(rom) % 1024 == 512:
        out["noheader"] = rom[512:]
    if console == "nes" and rom[:4] == b"NES\x1a":
        out["noines"] = rom[16:]
    return out


def checksums(console: str, rom: bytes):
    return {k: {"crc32": hex32(crc32(v)), "sha1": hashlib.sha1(v).hexdigest().upper()} for k, v in variants(console, rom).items()}


# ---------------------------------------------------------------- name matching (fallback for IPS without checksums)

def _norm(s: str) -> str:
    s = re.sub(r"\.[^.]+$", "", s.lower())
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"^the\s|,\s*the\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def name_score(file_name: str, wanted: str) -> float:
    have = set(_norm(file_name).split())
    want = _norm(wanted).split()
    if not want:
        return 0.0
    score = sum(w in have for w in want) / len(want)
    extra = len(have - set(want))  # "Super Mario World 2" shouldn't beat "Super Mario World"
    return score - 0.05 * extra


# ---------------------------------------------------------------- frontend layout

def is_batocera() -> bool:
    return Path("/userdata/roms").is_dir() and Path("/userdata/system").is_dir()


def default_data_dir() -> Path:
    if is_batocera():
        return Path("/userdata/system/romstop")
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "RomStop"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "romstop"


def find_esde_home():
    env = os.environ.get("ESDE_APPDATA_DIR")
    candidates = [Path(env)] if env else []
    candidates += [Path.home() / "ES-DE", Path.home() / ".emulationstation"]
    # Portable ES-DE keeps its data in an ES-DE folder next to ES-DE.exe, where romstop.exe may have been put.
    candidates += [SELF.parent / "ES-DE", SELF.parent, SELF.parent.parent / "ES-DE"]
    for c in candidates:
        if (c / "settings" / "es_settings.xml").is_file() or (c / "es_settings.xml").is_file():
            return c
    return None


def esde_setting(home: Path, name: str):
    for f in (home / "settings" / "es_settings.xml", home / "es_settings.xml"):
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'<(?:string|bool)\s+name="%s"\s+value="([^"]*)"' % re.escape(name), text)
            if m:
                return m.group(1)
    return None


def expand_esde_path(value: str, home: Path) -> Path:
    value = value.replace("%ESPATH%", str(home.parent)).replace("%HOME%", str(Path.home()))
    return Path(os.path.expanduser(value))


class Layout:
    """Where the frontend keeps ROMs, game lists and custom systems."""

    def __init__(self, cfg: dict):
        self.frontend = cfg["frontend"]
        self.roms = Path(cfg["roms"])
        self.esde_home = Path(cfg["esde_home"]) if cfg.get("esde_home") else None
        self.legacy_gamelists = bool(cfg.get("legacy_gamelists"))
        self.media = Path(cfg["media"]) if cfg.get("media") else self.esde_home / "downloaded_media" if self.esde_home else None
        self.folders = BATOCERA_FOLDERS if self.frontend == "batocera" else ESDE_FOLDERS
        self.batocera_conf = Path(cfg["batocera_conf"]) if cfg.get("batocera_conf") else self.roms.parent / "system" / "batocera.conf"

    def gamelist(self, system: str) -> Path:
        if self.frontend == "batocera" or self.legacy_gamelists or not self.esde_home:
            return self.roms / system / "gamelist.xml"
        return self.esde_home / "gamelists" / system / "gamelist.xml"

    def cover_files(self, rel: str):
        """Where the frontend looks for an entry's cover, and the gamelist <image> value if it needs one."""
        stem = rel[: -len(EXT)] if rel.endswith(EXT) else rel
        if self.frontend == "batocera":
            return [self.shelf / "images" / (stem + ".png")], "./images/%s.png" % stem
        if not self.media:
            return [], None
        # The default theme shows miximages in the game list, other themes covers; the same art serves both.
        return [self.media / "romstop" / kind / (stem + ".png") for kind in ("covers", "miximages")], None

    def system_folders(self, console: str):
        return self.folders.get(console, [console])

    def output_folder(self, console: str) -> Path:
        names = self.system_folders(console)
        for n in names:
            if (self.roms / n).is_dir():
                return self.roms / n
        return self.roms / names[0]

    @property
    def shelf(self) -> Path:
        return self.roms / "romstop"


def detect_config(args) -> dict:
    cfg = {"site": args.site or DEFAULT_SITE}
    frontend = args.frontend or ("batocera" if is_batocera() else "esde")
    cfg["frontend"] = frontend
    if frontend == "batocera":
        cfg["roms"] = str(Path(args.roms or "/userdata/roms"))
    else:
        home = Path(args.esde_home) if args.esde_home else find_esde_home()
        if home is None:
            raise RomStopError("Couldn't find ES-DE's settings (ES-DE/settings/es_settings.xml). Start ES-DE once first. "
                                "For a portable ES-DE, put romstop next to ES-DE.exe and run it again, or pass --esde-home <folder>.")
        cfg["esde_home"] = str(home)
        romdir = esde_setting(home, "ROMDirectory")
        cfg["roms"] = str(Path(args.roms) if args.roms else expand_esde_path(romdir, home) if romdir else Path.home() / "ROMs")
        cfg["legacy_gamelists"] = esde_setting(home, "LegacyGamelistFileLocation") == "true"
        media = esde_setting(home, "MediaDirectory")
        if media:
            cfg["media"] = str(expand_esde_path(media, home))
    return cfg


# ---------------------------------------------------------------- state

class Shelf:
    def __init__(self, data_dir: Path):
        self.data = data_dir
        self.cfg_path = data_dir / "config.json"
        self.cfg = json.loads(self.cfg_path.read_text()) if self.cfg_path.is_file() else None

    @property
    def layout(self) -> Layout:
        if not self.cfg:
            raise RomStopError("RomStop isn't set up yet. Run: python3 romstop.py setup")
        return Layout(self.cfg)

    @property
    def site(self) -> str:
        s = (self.cfg or {}).get("site") or DEFAULT_SITE
        return s if s.endswith("/") else s + "/"

    def save_cfg(self):
        self.data.mkdir(parents=True, exist_ok=True)
        self.cfg_path.write_text(json.dumps(self.cfg, indent=2))

    def log(self, msg: str):
        print(msg)
        try:
            self.data.mkdir(parents=True, exist_ok=True)
            with open(self.data / "romstop.log", "a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        except OSError:
            pass

    # -- network

    local_site = None  # set when packaging from a built site, so files are read from disk

    def fetch(self, rel: str) -> bytes:
        if self.local_site and not re.match(r"^[a-z]+://", rel):
            return (self.local_site / rel).read_bytes()
        url = rel if re.match(r"^[a-z]+://", rel) else self.site + rel.lstrip("/")
        req = urllib.request.Request(url, headers={"User-Agent": "romstop-device/" + VERSION})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def index(self, refresh: bool = False) -> dict:
        path = self.data / "index.json"
        bundled = SELF.parent / "index.json"  # shipped in the drag-and-drop zips
        if not refresh and not path.is_file() and bundled.is_file():
            return json.loads(bundled.read_text(encoding="utf-8"))
        if refresh or not path.is_file():
            raw = self.fetch("device/index.json")
            idx = json.loads(raw)
            if idx.get("format", 0) > INDEX_FORMAT:
                raise RomStopError("The hack list needs a newer RomStop tool. Run setup again to update it.")
            self.data.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return idx
        return json.loads(path.read_text(encoding="utf-8"))

    # -- checksum cache, so rescans only read new or changed files

    def _cache(self) -> dict:
        if not hasattr(self, "_cache_data"):
            p = self.data / "checksums.json"
            try:
                self._cache_data = json.loads(p.read_text()) if p.is_file() else {}
            except ValueError:
                self._cache_data = {}
        return self._cache_data

    def save_cache(self):
        if hasattr(self, "_cache_data"):
            self.data.mkdir(parents=True, exist_ok=True)
            (self.data / "checksums.json").write_text(json.dumps(self._cache_data))

    def file_checksums(self, console: str, path: Path):
        """Yields (member or None, {variant: {crc32, sha1}}) for a ROM file or each ROM inside a .zip."""
        st = path.stat()
        key = "%s|%s|%d|%d" % (console, path, st.st_size, int(st.st_mtime))
        cache = self._cache()
        if key not in cache:
            entries = []
            if path.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(path) as z:
                        for info in z.infolist():
                            if not info.is_dir() and info.file_size <= MAX_ROM_BYTES:
                                entries.append([info.filename, checksums(console, z.read(info))])
                except (zipfile.BadZipFile, OSError, NotImplementedError):
                    pass
            else:
                entries.append([None, checksums(console, path.read_bytes())])
            cache[key] = entries
        return cache[key]


def read_rom(path: Path, member):
    if member is None:
        return path.read_bytes()
    with zipfile.ZipFile(path) as z:
        return z.read(member)


# ---------------------------------------------------------------- game lists

def _load_gamelist(path: Path):
    if path.is_file():
        try:
            return ET.parse(path)
        except ET.ParseError:
            shutil.copy(path, str(path) + ".bak")
    return ET.ElementTree(ET.Element("gameList"))


def _write_gamelist(tree, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(ET, "indent"):
        ET.indent(tree)
    tmp = path.with_suffix(".xml.tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, path)


def update_gamelist(path: Path, entries, prune_prefix=None):
    """Add or update <game> entries by path, keeping fields we don't set (favorites, play counts).
    With prune_prefix, entries under it that aren't in `entries` are removed."""
    tree = _load_gamelist(path)
    root = tree.getroot()
    by_path = {g.findtext("path"): g for g in root.findall("game")}
    wanted = set()
    for e in entries:
        wanted.add(e["path"])
        g = by_path.get(e["path"])
        if g is None:
            g = ET.SubElement(root, "game")
        for k, v in e.items():
            if v is None or v == "":
                continue
            el = g.find(k)
            if el is None:
                el = ET.SubElement(g, k)
            el.text = str(v)
    if prune_prefix is not None:
        for p, g in by_path.items():
            if p and p.startswith(prune_prefix) and p not in wanted:
                root.remove(g)
    _write_gamelist(tree, path)


def es_date(s):
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", s or "")
    return "%s%s%sT000000" % (m.group(1), m.group(2) or "01", m.group(3) or "01") if m else None


def game_entry(hack: dict, path: str, desc: str):
    r = hack.get("rating") or {}
    return {
        "path": path,
        "name": hack["title"],
        "desc": desc,
        "rating": "%.2f" % (r["score"] / 5) if r.get("score") else None,
        "releasedate": es_date(hack.get("updated") or hack.get("released")),
        "developer": ", ".join(hack.get("authors") or []),
        "publisher": "RomStop",
        "genre": hack.get("genre") or hack.get("type"),
    }


# ---------------------------------------------------------------- emulator choice

def compat_lines(hack: dict, frontend: str):
    """What the hack's pages say about emulators, and what RomStop will launch it with here."""
    c = hack.get("compatibility") or {}
    lines = []
    if c.get("summary"):
        lines.append("Emulators: " + c["summary"])
    pick = (hack.get("play") or {}).get("batocera" if frontend == "batocera" else "esde") or {}
    names = c.get("names") or {}
    if pick.get("pinned") and not pick.get("ok"):
        lines.append("None of the emulators this device runs is known to work with this hack, so it may not play here.")
    elif pick.get("pinned") and pick.get("emulators"):
        lines.append("RomStop sets the patched game to run with %s." % names.get(pick["emulators"][0], pick["emulators"][0]))
    return lines


def _system_labels(path: Path, system: str):
    try:
        root = ET.parse(str(path)).getroot()
    except (OSError, ET.ParseError):
        return None
    for sys_el in root.findall("system"):
        if (sys_el.findtext("name") or "").strip() == system:
            return [c.get("label") or "" for c in sys_el.findall("command")]
    return None


def esde_system_files(lay: Layout):
    """es_systems.xml files ES-DE reads: the user's custom one first, then the bundled ones we can find."""
    files = []
    if lay.esde_home:
        files.append(lay.esde_home / "custom_systems" / "es_systems.xml")
    plat = "windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux"
    roots = [SELF.parent, SELF.parent.parent]
    if lay.esde_home:
        roots += [lay.esde_home, lay.esde_home.parent]
    for env in ("ProgramFiles", "LOCALAPPDATA"):
        if os.environ.get(env):
            roots += [Path(os.environ[env]) / "ES-DE", Path(os.environ[env]) / "Programs" / "ES-DE"]
    roots += [Path("/usr/share/es-de"), Path("/usr/local/share/es-de"), Path("/app/share/es-de")]
    files += [r / "resources" / "systems" / plat / "es_systems.xml" for r in roots]
    return files


def esde_labels(lay: Layout, system: str):
    """Alternative-emulator labels ES-DE offers for `system`, or None when its system file can't be found
    (the AppImage keeps it inside the image)."""
    for f in esde_system_files(lay):
        if f.is_file():
            labels = _system_labels(f, system)
            if labels is not None:
                return labels
    return None


def pick_emulator(lay: Layout, hack: dict, system: str):
    """(setting, name) to launch a patched hack with on this frontend, or None to keep the frontend's default.
    ES-DE: the <altemulator> label. Batocera/Knulli: {"emulator", "core"}."""
    pick = (hack.get("play") or {}).get("batocera" if lay.frontend == "batocera" else "esde") or {}
    if not pick.get("pinned") or not pick.get("use"):
        return None
    names = (hack.get("compatibility") or {}).get("names") or {}
    first = (pick.get("emulators") or [""])[0]
    if lay.frontend == "batocera":
        return pick["use"][0], names.get(first, first)
    have = esde_labels(lay, system)
    for label in pick["use"]:
        if have is None or label in have:
            return label, label
    return None


def set_batocera_game_emulator(conf: Path, system: str, rom_name: str, emulator: str, core: str):
    """Per-game emulator and core in batocera.conf: system["rom.ext"].emulator=... / .core=..."""
    key = '%s["%s"]' % (system, rom_name)
    try:
        lines = conf.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = [l for l in lines if not (l.startswith(key + ".emulator=") or l.startswith(key + ".core="))]
    lines += [key + ".emulator=" + emulator, key + ".core=" + core]
    conf.parent.mkdir(parents=True, exist_ok=True)
    tmp = conf.with_suffix(".conf.romstop-tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, conf)


# ---------------------------------------------------------------- frontend system entries

def safe_name(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s).strip().rstrip(".")
    return s[:120] or "hack"


def hack_stubs(hack: dict, consoles: dict):
    """One entry per patch file (or one for a hack whose patch isn't mirrored). Returns (relative path, stub data)."""
    short = (consoles.get(hack["console"]) or {}).get("short") or hack["console"].upper()
    patches = [p for p in hack.get("patches") or [] if p.get("format") in PATCHABLE]
    base = "%s/%s" % (safe_name(short), safe_name(hack["title"]))
    if len(patches) <= 1:
        return [(base + EXT, {"id": hack["id"], "patch": patches[0]["name"] if patches else None})]
    return [("%s - %s%s" % (base, safe_name(Path(p["name"]).stem), EXT), {"id": hack["id"], "patch": p["name"]}) for p in patches]


def stub_description(shelf: Shelf, hack: dict, patch_name) -> str:
    lines = [hack.get("summary") or ""]
    if hack.get("description") and hack["description"] != hack.get("summary"):
        lines += ["", hack["description"]]
    base = hack.get("baseRom") or {}
    req = base.get("title") or hack.get("baseGame")
    if base.get("crc32"):
        req += " (CRC32 %s)" % base["crc32"]
    lines += ["", "Needs: " + req, "By: " + ", ".join(hack.get("authors") or ["unknown"])]
    if hack.get("version"):
        lines.append("Version: " + hack["version"])
    r = hack.get("rating") or {}
    if r.get("score"):
        lines.append("Rating: %.1f / 5 from %d votes%s" % (r["score"], r.get("count", 0), " (sample data)" if r.get("sample") else ""))
    lines += compat_lines(hack, shelf.layout.frontend)
    patch = next((p for p in hack.get("patches") or [] if p["name"] == patch_name), None)
    if patch:
        credit = patch.get("sourceLabel") or patch.get("sourceUrl")
        lines.append("Patch: %s (%s)%s" % (patch["name"], patch["format"], ", mirrored from " + credit if credit else ""))
        lines += ["", "Launch to patch your own copy of the base ROM and add it to the game list."]
    else:
        link = next((l["url"] for l in hack.get("links") or []), shelf.site + hack.get("page", ""))
        why = "The author asked not to rehost it" if hack.get("noMirror") else "RomStop doesn't mirror this patch yet"
        lines += ["", "%s. Download it from %s and put the .ips/.bps/.ups file in romstop/patches/%s/ in your ROMs folder, then launch this entry." % (why, link, hack["id"])]
    return "\n".join(lines)


def write_system_definition(shelf: Shelf):
    lay = shelf.layout
    script = shelf.data / "romstop.py"
    if lay.frontend == "batocera":
        cfg_dir = Path("/userdata/system/configs/emulationstation")
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "es_systems_romstop.cfg").write_text(batocera_system_cfg(str(lay.shelf), "python3 %s install %%ROM%%" % script), encoding="utf-8")
        # A Ports entry too, so the list can be refreshed even if the custom system doesn't show up.
        ports = lay.roms / "ports"
        if ports.is_dir():
            (ports / "RomStop - Refresh hack list.sh").write_text("#!/bin/bash\npython3 %s sync\n" % script)
            os.chmod(ports / "RomStop - Refresh hack list.sh", 0o755)
        return cfg_dir / "es_systems_romstop.cfg"

    if FROZEN:
        command = '"%s" install %%ROM%%' % (shelf.data / SELF.name)
    else:
        python = shelf.cfg.get("python") or sys.executable
        if os.name == "nt":
            w = Path(python).with_name("pythonw.exe")  # no console window popping up over ES-DE
            python = str(w) if w.is_file() else python
        command = '"%s" "%s" install %%ROM%%' % (python, script)
    path = lay.esde_home / "custom_systems" / "es_systems.xml"
    _write_gamelist(esde_systems(_load_systems(path), command), path)
    return path


def esde_systems(tree, command: str):
    """Add (or replace) the romstop system in an ES-DE es_systems.xml tree."""
    root = tree.getroot()
    for s in root.findall("system"):
        if s.findtext("name") == "romstop":
            root.remove(s)
    s = ET.SubElement(root, "system")
    for k, v in (("name", "romstop"), ("fullname", "RomStop"), ("path", "%ROMPATH%/romstop"), ("extension", EXT), ("command", command), ("platform", "romstop"), ("theme", "romstop")):
        el = ET.SubElement(s, k)
        el.text = v
    s.find("command").set("label", "RomStop")
    return tree


def batocera_system_cfg(shelf_dir: str, command: str) -> str:
    return ('<?xml version="1.0"?>\n<systemList>\n  <system>\n    <name>romstop</name>\n    <fullname>RomStop</fullname>\n'
            "    <path>%s</path>\n    <extension>%s</extension>\n    <command>%s</command>\n"
            "    <platform>ignore</platform>\n    <theme>romstop</theme>\n  </system>\n</systemList>\n" % (shelf_dir, EXT, command))


def _load_systems(path: Path):
    tree = _load_gamelist(path)
    if tree.getroot().tag != "systemList":
        tree = ET.ElementTree(ET.Element("systemList"))
    return tree


def remove_system_definition(shelf: Shelf):
    lay = shelf.layout
    if lay.frontend == "batocera":
        for p in (Path("/userdata/system/configs/emulationstation/es_systems_romstop.cfg"), lay.roms / "ports" / "RomStop - Refresh hack list.sh"):
            if p.exists():
                p.unlink()
        return
    path = lay.esde_home / "custom_systems" / "es_systems.xml"
    if path.is_file():
        tree = _load_systems(path)
        for s in tree.getroot().findall("system"):
            if s.findtext("name") == "romstop":
                tree.getroot().remove(s)
        _write_gamelist(tree, path)


# ---------------------------------------------------------------- commands

def sync(shelf: Shelf, idx=None) -> str:
    lay = shelf.layout
    idx = idx or shelf.index(refresh=True)
    consoles = idx.get("consoles", {})
    shelf_dir = lay.shelf
    shelf_dir.mkdir(parents=True, exist_ok=True)
    keep, entries = {REFRESH_STUB}, []
    (shelf_dir / REFRESH_STUB).write_text(json.dumps({"action": "sync"}))
    entries.append({"path": "./" + REFRESH_STUB, "name": "Refresh hack list", "desc": "Fetches the latest hack list from %s.\n\nTool version %s." % (shelf.site, VERSION), "publisher": "RomStop"})
    ready = 0
    for hack in idx.get("hacks", []):
        # Wii / Vita mod packages are installed by hand, not patched onto one ROM, so they get no entry.
        if hack.get("install"):
            continue
        for rel, data in hack_stubs(hack, consoles):
            keep.add(rel)
            p = shelf_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(data)
            if not p.is_file() or p.read_text() != text:
                p.write_text(text)
            entry = game_entry(hack, "./" + rel, stub_description(shelf, hack, data["patch"]))
            if hack.get("cover"):
                files, image = lay.cover_files(rel)
                if image:
                    entry["image"] = image
                save_cover(shelf, hack["cover"], files)
            entries.append(entry)
            ready += bool(data["patch"])
    # Drop entries for hacks that left the site.
    for p in shelf_dir.rglob("*" + EXT):
        if p.relative_to(shelf_dir).as_posix() not in keep:
            p.unlink()
    for d in sorted((d for d in shelf_dir.rglob("*") if d.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    update_gamelist(lay.gamelist("romstop"), entries, prune_prefix="./")
    return "Hack list updated: %d hacks, %d ready to patch." % (len(idx.get("hacks", [])), ready)


def save_cover(shelf: Shelf, src: str, files):
    """Cover art is a nicety: fetch it once and carry on if it can't be had."""
    missing = [f for f in files if not f.is_file()]
    if not missing:
        return
    try:
        data = shelf.fetch(src)
    except Exception:
        return
    for f in missing:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)


def get_patch(shelf: Shelf, hack: dict, patch_name):
    """The patch bytes: the mirrored file (cached), or one the user dropped into the hack's patches folder."""
    folder = shelf.layout.shelf / "patches" / hack["id"]
    info = next((p for p in hack.get("patches") or [] if p["name"] == patch_name), None)
    if info:
        cache = shelf.data / "patches" / hack["id"]
        cached = cache / safe_name(info["name"])
        if cached.is_file() and (not info.get("crc32") or hex32(crc32(cached.read_bytes())) == info["crc32"].upper()):
            return cached.read_bytes(), info
        data = shelf.fetch(info["path"])
        if info.get("crc32") and hex32(crc32(data)) != info["crc32"].upper():
            raise RomStopError("The downloaded patch is damaged (checksum mismatch). Try again later.")
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data, info
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.suffix.lower() in (".ips", ".bps", ".ups"):
                return p.read_bytes(), {"name": p.name, "format": p.suffix[1:].upper()}
    link = next((l["url"] for l in hack.get("links") or []), shelf.site + hack.get("page", ""))
    raise RomStopError("No patch file for this hack yet. Download it from %s and put the .ips/.bps/.ups file in %s" % (link, folder))


def rom_candidates(lay: Layout, console: str, exts, everywhere: bool):
    exts = {e.lower() for e in exts} | {".zip"}
    roots = [lay.roms] if everywhere else [lay.roms / n for n in lay.system_folders(console)]
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and Path(dirpath, d) != lay.shelf]
            for f in files:
                p = Path(dirpath, f)
                if p.suffix.lower() in exts and p not in seen and "[RomStop]" not in f and "[Romshelf]" not in f:  # [Romshelf]: hacks installed before the rename
                    seen.add(p)
                    try:
                        if p.stat().st_size <= MAX_ROM_BYTES:
                            yield p
                    except OSError:
                        pass


def find_base_rom(shelf: Shelf, hack: dict, patch: bytes, exts):
    """Returns (path, zip member, variant, how) for the base ROM, or None."""
    lay = shelf.layout
    console = hack["console"]
    base = hack.get("baseRom") or {}
    src = patch_source_crc(patch)
    want = []  # (field, value, from the patch itself)
    if src is not None:
        want.append(("crc32", hex32(src), True))
    if base.get("crc32"):
        want.append(("crc32", base["crc32"].upper(), False))
    if base.get("sha1"):
        want.append(("sha1", base["sha1"].upper(), False))

    if want:
        for everywhere in (False, True):
            for path in rom_candidates(lay, console, exts, everywhere):
                try:
                    found = shelf.file_checksums(console, path)
                except OSError:
                    continue
                for member, sums in found:
                    if any(s[field] == value for field, value, _ in want for s in sums.values()):
                        if src is None:
                            return path, member, None, "checksum"
                        # BPS/UPS check the exact input, so it has to be a form of this dump the patch was made from.
                        exact = next((v for v, s in sums.items() if s["crc32"] == hex32(src)), None)
                        if exact:
                            return path, member, exact, "checksum"
        return None

    # IPS with no recorded checksum: the closest file name in the console's folders.
    title = base.get("title") or hack.get("baseGame") or ""
    best = None
    for path in rom_candidates(lay, console, exts, False):
        score = name_score(path.name, title)
        if base.get("region") and base["region"].lower() in path.name.lower():
            score += 0.1
        if score >= 0.6 and (best is None or score > best[0]):
            best = (score, path)
    if best:
        member = None
        if best[1].suffix.lower() == ".zip":
            with zipfile.ZipFile(best[1]) as z:
                names = [n for n in z.namelist() if Path(n).suffix.lower() in exts]
                if not names:
                    return None
                member = names[0]
        return best[1], member, None, "name"
    return None


def prepare_input(console: str, rom: bytes, variant, base: dict, kind: str) -> bytes:
    """Pick the form of the dump to patch. `variant` is set when a BPS/UPS checksum matched it exactly."""
    v = variants(console, rom)
    if variant:
        return v[variant]
    if "z64" in v:
        return v["z64"]
    header = (base.get("header") or "").lower()
    if console == "snes" and "noheader" in v and not ("headered" in header and "unheadered" not in header):
        return v["noheader"]
    return rom


def install(shelf: Shelf, stub: Path) -> str:
    try:
        data = json.loads(stub.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RomStopError("Can't read %s" % stub)
    if data.get("action") == "sync":
        return sync(shelf)
    lay = shelf.layout
    idx = shelf.index()
    hack = next((h for h in idx.get("hacks", []) if h["id"] == data.get("id")), None)
    if not hack:
        raise RomStopError("This hack isn't on RomStop any more. Launch \"Refresh hack list\".")
    patch, info = get_patch(shelf, hack, data.get("patch"))
    kind = patch_kind(patch)
    if not kind:
        raise RomStopError("%s isn't an IPS, BPS or UPS patch" % info["name"])
    exts = (idx.get("consoles", {}).get(hack["console"]) or {}).get("extensions") or []
    base = hack.get("baseRom") or {}
    shelf.log("Looking for the base ROM for %s in %s" % (hack["title"], lay.roms))
    try:
        found = find_base_rom(shelf, hack, patch, exts)
    finally:
        shelf.save_cache()
    need = base.get("title") or hack.get("baseGame")
    if not found:
        src = patch_source_crc(patch) or (int(base["crc32"], 16) if base.get("crc32") else None)
        raise RomStopError("No matching base ROM found. %s needs %s%s. Put it in %s and launch again." % (
            hack["title"], need, " (CRC32 %s)" % hex32(src) if src is not None else "", lay.output_folder(hack["console"])))
    path, member, variant, how = found
    rom = read_rom(path, member)
    out = apply_patch(prepare_input(hack["console"], rom, variant, base, kind), patch)

    src_name = Path(member or path.name)
    ext = ".z64" if hack["console"] == "n64" else src_name.suffix.lower()
    if ext not in [e.lower() for e in exts] and exts:
        ext = exts[0]
    label = hack["title"]
    if len([p for p in hack.get("patches") or [] if p.get("format") in PATCHABLE]) > 1:
        label += " - " + Path(info["name"]).stem
    if hack.get("version"):
        label += " (%s)" % hack["version"]
    folder = lay.output_folder(hack["console"])
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_name(label + " [RomStop]") + ext
    (folder / name).write_bytes(out)

    credit = info.get("sourceLabel") or info.get("sourceUrl")
    desc = "%s\n\nBy: %s\nPatched by RomStop from %s%s." % (
        hack.get("summary") or "", ", ".join(hack.get("authors") or ["unknown"]), path.name + ("/" + member if member else ""), ", patch from " + credit if credit else "")
    entry = game_entry(hack, "./" + name, desc)
    chosen = pick_emulator(lay, hack, folder.name)
    if chosen and lay.frontend == "batocera":
        set_batocera_game_emulator(lay.batocera_conf, folder.name, name, chosen[0]["emulator"], chosen[0]["core"])
    elif chosen:
        entry["altemulator"] = chosen[0]
    update_gamelist(lay.gamelist(folder.name), [entry])
    shelf.log("Wrote %s%s" % (folder / name, " (runs with %s)" % chosen[1] if chosen else ""))
    msg = "%s is ready in %s." % (hack["title"], folder.name)
    if chosen:
        msg += " It's set to run with %s. %s" % (chosen[1], (hack.get("compatibility") or {}).get("summary") or "")
    elif ((hack.get("play") or {}).get("batocera" if lay.frontend == "batocera" else "esde") or {}).get("pinned"):
        msg += " Heads up: %s" % ((hack.get("compatibility") or {}).get("summary") or "this hack needs a particular emulator.")
    if how == "name":
        msg += " The base ROM (%s) was picked by file name because this patch records no checksum, so if it doesn't boot, check it's %s." % (path.name, need)
    if lay.frontend == "esde":
        msg += " Restart ES-DE (or Main menu > Utilities > Rescan ROM directory) to see it."
    return msg


# ---------------------------------------------------------------- drag-and-drop packages

def auto_config(near: Path) -> dict:
    """Configuration for a copy that was dragged into place rather than set up: the ROM folder is the one
    holding the romstop folder this entry (or the tool) sits in."""
    shelf_dir = next((p for p in [near, *near.parents] if p.name.lower() == "romstop"), None)
    if shelf_dir is None:
        raise RomStopError("RomStop isn't set up yet. Put the romstop folder in your ROMs folder, or run: romstop setup")
    cfg = {"site": DEFAULT_SITE, "roms": str(shelf_dir.parent), "frontend": "batocera" if is_batocera() else "esde"}
    if cfg["frontend"] == "esde":
        home = find_esde_home()
        if home:
            cfg["esde_home"] = str(home)
            cfg["legacy_gamelists"] = esde_setting(home, "LegacyGamelistFileLocation") == "true"
            media = esde_setting(home, "MediaDirectory")
            if media:
                cfg["media"] = str(expand_esde_path(media, home))
    return cfg


PACKAGES = {
    # name: (frontend, ROMs folder in the zip, tool file name, command)
    "RomStop-ES-DE-Windows": ("esde", "ROMs", "romstop.exe", '"%ROMPATH%/romstop/romstop.exe" install %ROM%'),
    "RomStop-ES-DE-SteamDeck-Linux": ("esde", "ROMs", "romstop.py", 'python3 "%ROMPATH%/romstop/romstop.py" install %ROM%'),
    "RomStop-Knulli-Batocera": ("batocera", "roms", "romstop.py", "python3 /userdata/roms/romstop/romstop.py install %ROM%"),
}

HOW_TO = {
    "esde": """RomStop for EmulationStation DE
================================

Nothing to install or run. Drag two folders onto your existing ES-DE setup:

1. Open the ES-DE folder in this zip and drag its contents (custom_systems, gamelists and downloaded_media)
   into your ES-DE folder: %s
   If you already have a custom_systems\\es_systems.xml of your own, copy the <system> block
   from this one into it instead of replacing it.
2. Open the ROMs folder in this zip and drag the romstop folder into your ROMs folder
   (the one that holds snes, gba, ...).
3. Restart ES-DE. A "RomStop" system appears with a folder per console and an entry per hack.

Pick a hack and launch it: RomStop looks for that hack's base ROM among your ROMs (by checksum),
patches it and saves it in the console's folder. Restart ES-DE or use
Main menu > Utilities > Rescan ROM directory to see it in the game list.
"Refresh hack list" fetches the latest hacks from the site.
%s""",
    "batocera": """RomStop for Knulli / Batocera
==============================

Nothing to install or run. Drag the two folders in this zip (roms and system) onto your device's
share: over the network (\\\\KNULLI\\share or \\\\BATOCERA\\share), or onto the SD card's share
partition. They merge with the folders already there.

Then restart EmulationStation (or Game settings > Update gamelists). A "RomStop" system appears
with a folder per console and an entry per hack. Under Ports there is also "RomStop - Refresh hack list".

Pick a hack and launch it: RomStop looks for that hack's base ROM among your ROMs (by checksum),
patches it and saves it in the console's folder, then reloads the game lists.
""",
}


def package(index_path: Path, out: Path, exe=None, tool=None, site_root=None):
    """Build the drag-and-drop zips: the system definition, the hack entries and the tool, laid out like the
    folders they go into on the device."""
    import tempfile
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    tool = tool or Path(os.path.abspath(__file__))
    made = []
    out.mkdir(parents=True, exist_ok=True)
    for name, (frontend, roms_name, tool_name, command) in PACKAGES.items():
        src = exe if tool_name.endswith(".exe") else tool
        if not src:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            roms = stage / roms_name
            cfg = {"frontend": frontend, "roms": str(roms), "site": DEFAULT_SITE}
            if frontend == "esde":
                cfg["esde_home"] = str(stage / "ES-DE")
            shelf = Shelf(stage / "_data")
            shelf.cfg = cfg
            shelf.local_site = site_root
            sync(shelf, idx)
            shutil.copy(str(src), str(roms / "romstop" / tool_name))
            shutil.copy(str(index_path), str(roms / "romstop" / "index.json"))
            drop = roms / "romstop" / "patches"
            drop.mkdir()
            (drop / "README.txt").write_text("For hacks whose patch isn't mirrored on RomStop: put the patch file in a folder named after the hack's id, e.g. patches/%s/hack.bps\n" % (idx["hacks"][0]["id"] if idx.get("hacks") else "hack-id"))
            if frontend == "esde":
                _write_gamelist(esde_systems(ET.ElementTree(ET.Element("systemList")), command), stage / "ES-DE" / "custom_systems" / "es_systems.xml")
                where = r"C:\Users\<you>\ES-DE (or the ES-DE folder next to ES-DE.exe for a portable install)" if tool_name.endswith(".exe") else "~/ES-DE (on a Steam Deck: /home/deck/ES-DE)"
                extra = "" if tool_name.endswith(".exe") else "\nRomStop runs with the Python 3 that SteamOS and Bazzite already have.\n"
                text = HOW_TO["esde"] % (where, extra)
            else:
                cfg_dir = stage / "system" / "configs" / "emulationstation"
                cfg_dir.mkdir(parents=True)
                (cfg_dir / "es_systems_romstop.cfg").write_text(batocera_system_cfg("/userdata/roms/romstop", command), encoding="utf-8")
                (roms / "ports").mkdir()
                with open(roms / "ports" / "RomStop - Refresh hack list.sh", "w", newline="\n") as f:
                    f.write("#!/bin/bash\npython3 /userdata/roms/romstop/romstop.py sync\n")
                text = HOW_TO["batocera"]
            (stage / "How to install.txt").write_text(text.replace("\n", "\r\n"), encoding="utf-8")
            shutil.rmtree(stage / "_data", ignore_errors=True)
            zpath = out / (name + ".zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for p in sorted(stage.rglob("*")):
                    rel = p.relative_to(stage).as_posix()
                    if p.is_dir():
                        continue
                    info = zipfile.ZipInfo(rel, time.localtime(p.stat().st_mtime)[:6])
                    info.compress_type = zipfile.ZIP_DEFLATED
                    if p.suffix in (".sh", ".py"):
                        info.external_attr = 0o755 << 16
                    z.writestr(info, p.read_bytes())
            made.append(zpath)
    return made


# ---------------------------------------------------------------- feedback on screen

def show(shelf, title: str, msg: str, error: bool = False):
    if shelf:
        shelf.log(("ERROR: " if error else "") + msg)
    else:
        print(msg)
    if os.environ.get("ROMSTOP_QUIET"):
        return
    try:
        if shelf and shelf.cfg and shelf.cfg.get("frontend") == "batocera":
            req = urllib.request.Request(BATOCERA_API + "/notify", data=msg.encode("utf-8"), method="POST")
            urllib.request.urlopen(req, timeout=3).read()
            return
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10 if error else 0x40)
            return
        for cmd in (["zenity", "--error" if error else "--info", "--title", title, "--text", msg, "--width", "520"],
                    ["kdialog", "--title", title, "--error" if error else "--msgbox", msg],
                    ["notify-send", title, msg]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, timeout=120)
                return
    except Exception:
        pass


def hide_console():
    """The packaged exe is a console program so setup can print; hide its window when ES-DE launches an entry."""
    if FROZEN and os.name == "nt":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass


def reload_later():
    """Batocera/Knulli: ask EmulationStation to reload game lists once it's back from the launched entry."""
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "_reload"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _reload():
    for _ in range(20):
        time.sleep(3)
        try:
            with urllib.request.urlopen(BATOCERA_API + "/isIdle", timeout=3) as r:
                if r.status != 200:
                    continue
            urllib.request.urlopen(BATOCERA_API + "/reloadgames", timeout=10).read()
            return
        except Exception:
            continue


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # hack titles in a Windows console code page
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="romstop", description="RomStop for EmulationStation DE and Knulli/Batocera")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("setup", help="add the RomStop system to the frontend and fetch the hack list")
    s.add_argument("--frontend", choices=["esde", "batocera"])
    s.add_argument("--roms", help="ROM folder (default: from ES-DE settings, or /userdata/roms)")
    s.add_argument("--esde-home", help="ES-DE folder (default: ~/ES-DE)")
    s.add_argument("--site", help="RomStop site URL (default: %s)" % DEFAULT_SITE)
    sub.add_parser("sync", help="refresh the hack list")
    i = sub.add_parser("install", help="patch the hack an entry points to")
    i.add_argument("stub")
    sub.add_parser("remove", help="remove the RomStop system (installed hacks stay)")
    k = sub.add_parser("package", help="build the drag-and-drop zips (used by the site build)")
    k.add_argument("--index", required=True)
    k.add_argument("--out", required=True)
    k.add_argument("--exe", help="romstop.exe for the Windows zip")
    k.add_argument("--site-root", help="the built site (dist), to take cover art from")
    sub.add_parser("_reload")
    ap.add_argument("--data-dir", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.cmd is None and FROZEN:
        # Double-clicked romstop.exe: run setup and keep the window open to show the result.
        code = main(["setup"])
        input("\nPress Enter to close.")
        return code

    if args.cmd == "_reload":
        return _reload()
    if args.cmd == "package":
        for z in package(Path(args.index), Path(args.out), Path(args.exe) if args.exe else None, site_root=Path(args.site_root) if args.site_root else None):
            print(z)
        return 0
    here = SELF.parent
    data_dir = Path(args.data_dir) if args.data_dir else here if (here / "config.json").is_file() else default_data_dir()
    shelf = Shelf(data_dir)
    try:
        if shelf.cfg is None and args.cmd in ("install", "sync"):
            shelf.cfg = auto_config(Path(os.path.abspath(args.stub)) if args.cmd == "install" else SELF.parent)
            shelf.save_cfg()
        if args.cmd == "setup":
            shelf.cfg = detect_config(args)
            shelf.cfg["python"] = sys.executable
            shelf.save_cfg()
            target = data_dir / SELF.name
            if SELF.resolve() != target.resolve():
                shutil.copy(str(SELF), str(target))
            lay = shelf.layout
            print("Frontend: %s\nROMs: %s\nGame lists: %s" % ("Knulli/Batocera" if lay.frontend == "batocera" else "ES-DE", lay.roms, lay.gamelist("<system>").parent.parent))
            print("System definition: %s" % write_system_definition(shelf))
            msg = sync(shelf)
            if lay.frontend == "batocera":
                try:
                    urllib.request.urlopen(BATOCERA_API + "/reloadgames", timeout=10).read()
                    msg += " RomStop is now in the system list."
                except Exception:
                    msg += " Update the game lists (Game settings > Update gamelists) or restart to see RomStop."
            else:
                msg += " Restart ES-DE to see the RomStop system."
            print(msg)
        elif args.cmd == "sync":
            print(sync(shelf))
        elif args.cmd == "remove":
            remove_system_definition(shelf)
            shutil.rmtree(shelf.layout.shelf, ignore_errors=True)
            print("RomStop system removed. Patched ROMs and %s are left in place." % data_dir)
        elif args.cmd == "install":
            hide_console()
            msg = install(shelf, Path(args.stub))
            if shelf.layout.frontend == "batocera":
                reload_later()
            show(shelf, "RomStop", msg)
        else:
            ap.print_help()
    except (RomStopError, OSError, ValueError) as e:
        msg = str(e)
        if isinstance(e, urllib.error.URLError):
            msg = "Couldn't reach %s (%s). Check the device is online." % (shelf.site, getattr(e, "reason", e))
        if args.cmd == "install":
            show(shelf, "RomStop", msg, error=True)
        else:
            print("RomStop: %s" % msg, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
