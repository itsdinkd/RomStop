"""Decide whether a ROM in the library is the one a RomStop hack is made for."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any

# RomStop console id -> RomM platform slugs that hold the same games.
CONSOLE_PLATFORMS: dict[str, tuple[str, ...]] = {
    "nes": ("nes", "famicom", "fds"),
    "snes": ("snes", "sfam"),
    "n64": ("n64",),
    "gc": ("ngc",),
    "gb": ("gb",),
    "gbc": ("gbc",),
    "gba": ("gba",),
    "nds": ("nds",),
    "vb": ("virtualboy",),
    "sms": ("sms",),
    "genesis": ("genesis",),
    "segacd": ("segacd",),
    "32x": ("sega32",),
    "saturn": ("saturn",),
    "dreamcast": ("dc",),
    "gg": ("gamegear",),
    "pce": ("tg16", "pc-engine", "turbografx-cd"),
    "neogeo": ("neogeoaes", "neogeomvs"),
    "psx": ("psx",),
    "ps2": ("ps2",),
    "psp": ("psp",),
    "wii": ("wii",),
    "vita": ("psvita",),
    "atari2600": ("atari2600",),
    "lynx": ("lynx",),
    "msx": ("msx", "msx2"),
    "arcade": ("arcade",),
}

PLATFORM_CONSOLE: dict[str, str] = {
    slug: console for console, slugs in CONSOLE_PLATFORMS.items() for slug in slugs
}

_REGION_ALIASES: dict[str, str] = {
    "u": "USA",
    "us": "USA",
    "usa": "USA",
    "america": "USA",
    "north america": "USA",
    "j": "Japan",
    "jp": "Japan",
    "jpn": "Japan",
    "japan": "Japan",
    "e": "Europe",
    "eu": "Europe",
    "eur": "Europe",
    "europe": "Europe",
    "w": "World",
    "world": "World",
    "uk": "UK",
    "k": "Korea",
    "kr": "Korea",
    "korea": "Korea",
    "g": "Germany",
    "de": "Germany",
    "germany": "Germany",
    "f": "France",
    "fr": "France",
    "france": "France",
    "s": "Spain",
    "es": "Spain",
    "spain": "Spain",
    "i": "Italy",
    "it": "Italy",
    "italy": "Italy",
    "a": "Australia",
    "au": "Australia",
    "australia": "Australia",
    "br": "Brazil",
    "brazil": "Brazil",
}

_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


class MatchLevel(StrEnum):
    # Checksums match: this is the ROM the hack was made for.
    EXACT = "exact"
    # No checksum to compare, but the file is named like the base ROM, or the game and region agree.
    LIKELY = "likely"
    # Same game, but a different region, revision or dump.
    WRONG_VERSION = "wrong_version"
    NONE = "none"


# Best first, so `min` over levels by this order picks the strongest match.
LEVEL_ORDER = {MatchLevel.EXACT: 0, MatchLevel.LIKELY: 1, MatchLevel.WRONG_VERSION: 2, MatchLevel.NONE: 3}


@dataclass(frozen=True)
class LibraryFile:
    id: int
    file_name: str
    size: int
    crc32: str | None = None
    sha1: str | None = None
    # Hashes with a copier / iNES header removed, when the file has one.
    headerless_crc32: str | None = None
    headerless_sha1: str | None = None


@dataclass(frozen=True)
class LibraryRom:
    id: int
    platform_id: int
    platform_slug: str
    name: str | None
    fs_name: str
    fs_name_no_tags: str
    regions: tuple[str, ...] = ()
    revision: str | None = None
    alternative_names: tuple[str, ...] = ()
    files: tuple[LibraryFile, ...] = ()

    @property
    def console(self) -> str | None:
        return PLATFORM_CONSOLE.get(self.platform_slug)


@dataclass
class Match:
    level: MatchLevel
    rom_id: int
    file_id: int | None = None
    # Why the level was chosen, for the UI ("checksum", "file name", "title and region", "region", ...).
    reason: str = ""
    # Strip the header before patching: the hack's checksum is of the headerless dump.
    strip_header: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# Called for every library ROM name against every hack, so the result is kept.
@lru_cache(maxsize=1 << 16)
def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = _TAGS.sub("", t)
    t = t.replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", t)
    words = [_ROMAN.get(w, w) for w in words if w not in ("the", "a", "an")]
    return "".join(words)


def tags_of(name: str) -> list[str]:
    return [chunk.strip() for tag in re.findall(r"\(([^)]*)\)", name) for chunk in tag.split(",")]


def normalize_region(region: str | None) -> str | None:
    if not region:
        return None
    return _REGION_ALIASES.get(region.strip().lower(), region.strip())


def base_rom_regions(base_rom: dict[str, Any]) -> set[str]:
    """Regions the base ROM is for, from its `region` field or the tags in its No-Intro title."""
    found: set[str] = set()
    if base_rom.get("region"):
        for part in re.split(r"[,/]", base_rom["region"]):
            if r := normalize_region(part):
                found.add(r)
    if not found:
        for tag in tags_of(base_rom.get("title") or ""):
            r = normalize_region(tag)
            if r and r in _REGION_ALIASES.values():
                found.add(r)
    return found


def rom_regions(rom: LibraryRom) -> set[str]:
    regions = {r for r in (normalize_region(x) for x in rom.regions) if r}
    if not regions:
        for tag in tags_of(rom.fs_name):
            r = normalize_region(tag)
            if r and r in _REGION_ALIASES.values():
                regions.add(r)
    return regions


def _revision_of(name: str) -> str | None:
    for tag in tags_of(name):
        m = re.fullmatch(r"(?i)rev\s*([0-9a-z.]+)", tag)
        if m:
            return m.group(1).lower()
    return None


def regions_compatible(wanted: set[str], have: set[str]) -> bool:
    """World and USA/Europe-style multi-region dumps satisfy any region they list."""
    if not wanted or not have:
        return True
    if "World" in have:
        return True
    return bool(wanted & have)


def same_game(hack: dict[str, Any], rom: LibraryRom) -> bool:
    wanted = {normalize_title(hack.get("baseGame"))}
    if base_title := (hack.get("baseRom") or {}).get("title"):
        wanted.add(normalize_title(base_title))
    wanted.discard("")
    have = {normalize_title(n) for n in (rom.name, rom.fs_name_no_tags, *rom.alternative_names)}
    have.discard("")
    return bool(wanted & have)


def _stem(file_name: str) -> str:
    return file_name.rsplit(".", 1)[0] if "." in file_name else file_name


def match_rom(hack: dict[str, Any], rom: LibraryRom, base_rom: dict[str, Any] | None = None) -> Match:
    """How well `rom` fits `hack` (or one of its versions, when `base_rom` is that version's base ROM)."""
    base = base_rom or hack.get("baseRom") or {}
    if rom.console != hack.get("console"):
        return Match(MatchLevel.NONE, rom.id)

    # crc32/sha1 are what the patch expects (for NES, Romhacking.net's include the iNES header);
    # headerlessCrc32/headerlessSha1 are the No-Intro checksums of the dump without a header.
    want_crc = (base.get("crc32") or "").lower() or None
    want_sha1 = (base.get("sha1") or "").lower() or None
    bare_crc = (base.get("headerlessCrc32") or "").lower() or None
    bare_sha1 = (base.get("headerlessSha1") or "").lower() or None
    has_checksum = bool(want_crc or want_sha1 or bare_crc or bare_sha1)
    # The patch was made against a file with a header when its checksum isn't the headerless one.
    patch_has_header = bool((want_crc and bare_crc and want_crc != bare_crc) or (want_sha1 and bare_sha1 and want_sha1 != bare_sha1))

    def same(a: str | None, b: str | None) -> bool:
        return bool(a and b and a == b)

    for f in rom.files:
        if same(f.sha1, want_sha1) or same(f.crc32, want_crc):
            return Match(MatchLevel.EXACT, rom.id, f.id, "checksum")
    for f in rom.files:
        if same(f.headerless_sha1, want_sha1) or same(f.headerless_crc32, want_crc):
            return Match(MatchLevel.EXACT, rom.id, f.id, "checksum without header", strip_header=True)
        if same(f.headerless_sha1, bare_sha1) or same(f.headerless_crc32, bare_crc):
            # The right dump with a header of its own: patch it as is when the patch expects a header.
            if patch_has_header:
                return Match(MatchLevel.EXACT, rom.id, f.id, "checksum")
            return Match(MatchLevel.EXACT, rom.id, f.id, "checksum without header", strip_header=True)
        if same(f.sha1, bare_sha1) or same(f.crc32, bare_crc):
            if patch_has_header:
                # The right game data, but the patch's offsets count a header this file lacks.
                return Match(MatchLevel.WRONG_VERSION, rom.id, f.id, "header")
            return Match(MatchLevel.EXACT, rom.id, f.id, "checksum")

    main_file = rom.files[0].id if len(rom.files) == 1 else None
    base_title = base.get("title") or ""
    named = [f for f in rom.files if base_title and _stem(f.file_name).lower() == base_title.lower()]
    if named and not has_checksum:
        return Match(MatchLevel.LIKELY, rom.id, named[0].id, "file name")

    if not same_game(hack, rom):
        return Match(MatchLevel.NONE, rom.id)

    if has_checksum:
        # Same game, but no file has the checksum: a different region, revision or a bad dump.
        return Match(MatchLevel.WRONG_VERSION, rom.id, main_file, _mismatch_reason(base, rom) or "checksum")

    reason = _mismatch_reason(base, rom)
    if reason:
        return Match(MatchLevel.WRONG_VERSION, rom.id, main_file, reason)
    return Match(MatchLevel.LIKELY, rom.id, main_file, "title and region")


def _mismatch_reason(base: dict[str, Any], rom: LibraryRom) -> str | None:
    if not regions_compatible(base_rom_regions(base), rom_regions(rom)):
        return "region"
    want_rev = (base.get("revision") or "").lower().removeprefix("rev").strip() or _revision_of(base.get("title") or "")
    have_rev = (rom.revision or "").lower().removeprefix("rev").strip() or _revision_of(rom.fs_name)
    if want_rev and want_rev != have_rev:
        return "revision"
    return None


def best_match(hack: dict[str, Any], roms: list[LibraryRom]) -> list[Match]:
    """Every library ROM that is the same game as the hack's base, best first."""
    return LibraryMatcher(roms).matches(hack)


class LibraryMatcher:
    """Matches hacks against a whole library without comparing every hack to every ROM.

    match_rom only finds a fit when a checksum, the file name or the game title agrees, so the ROMs are
    indexed by those and only the ROMs sharing one with a hack are checked. The results are the same as
    calling match_rom on every ROM of the hack's console."""

    def __init__(self, roms: Iterable[LibraryRom]):
        self.by_id: dict[int, LibraryRom] = {}
        self._keys: dict[tuple[str, str, str], list[LibraryRom]] = {}
        for rom in roms:
            console = rom.console
            if not console:
                continue
            self.by_id[rom.id] = rom
            keys = set()
            for f in rom.files:
                keys.update(("sum", h) for h in (f.crc32, f.sha1, f.headerless_crc32, f.headerless_sha1) if h)
                keys.add(("stem", _stem(f.file_name).lower()))
            keys.update(("title", t) for t in (normalize_title(n) for n in (rom.name, rom.fs_name_no_tags, *rom.alternative_names)) if t)
            for kind, value in keys:
                self._keys.setdefault((console, kind, value), []).append(rom)

    def candidates(self, hack: dict[str, Any], base_rom: dict[str, Any] | None = None) -> list[LibraryRom]:
        base = base_rom or hack.get("baseRom") or {}
        console = hack.get("console") or ""
        keys = [("sum", (base.get(k) or "").lower()) for k in ("crc32", "sha1", "headerlessCrc32", "headerlessSha1")]
        if base.get("title"):
            keys.append(("stem", base["title"].lower()))
        # same_game compares against the hack's own base game, whichever version is asked about.
        keys += [("title", normalize_title(hack.get("baseGame"))), ("title", normalize_title((hack.get("baseRom") or {}).get("title")))]
        found: dict[int, LibraryRom] = {}
        for kind, value in keys:
            if value:
                for rom in self._keys.get((console, kind, value), ()):
                    found[rom.id] = rom
        return list(found.values())

    def matches(self, hack: dict[str, Any], base_rom: dict[str, Any] | None = None) -> list[Match]:
        """Every library ROM that fits the hack, best first."""
        found = [m for m in (match_rom(hack, r, base_rom) for r in self.candidates(hack, base_rom)) if m.level != MatchLevel.NONE]
        return sorted(found, key=lambda m: (LEVEL_ORDER[m.level], m.rom_id))


def needed_rom_label(hack: dict[str, Any], base_rom: dict[str, Any] | None = None) -> str:
    """'Mega Man 2 (Japan)' style label for the ROM a hack needs."""
    base = base_rom or hack.get("baseRom") or {}
    if base.get("title"):
        return base["title"]
    region = base.get("region")
    return f"{hack.get('baseGame', '')} ({region})" if region else hack.get("baseGame", "")


# Copier headers: SNES dumps are a multiple of 1 KiB plus 512 bytes; iNES files start with "NES\x1a".
def header_size(console: str | None, data_start: bytes, size: int) -> int:
    if console == "nes" and data_start[:4] == b"NES\x1a":
        return 16
    if console == "nes" and data_start[:4] == b"FDS\x1a":
        return 16
    if console == "snes" and size % 1024 == 512:
        return 512
    if console in ("pce",) and size % 1024 == 512:
        return 512
    return 0
