"""Save import: decide where each save, state and state screenshot from the user's own folders goes in RomM.

The browser walks the folders the user picked and sends file paths, sizes, MD5s and the few config
files that name an emulator (RetroArch playlists, ES-DE gamelists and es_systems.xml). This module
matches each file to a ROM, works out the emulator and the name RomM should store it under, and
checks it against what RomM already holds. It never decides to overwrite: a file whose name is
taken by different content is a conflict the user resolves (keep both, or skip).

Stdlib only, so the tests can load it without RomM.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

# Battery and memory-card saves that belong to one game.
SAVE_EXTENSIONS = {
    ".srm", ".sav", ".sa1", ".sra", ".eep", ".fla", ".mpk", ".rtc", ".dsv", ".mcr", ".mcd",
    ".bkr", ".brm", ".bcr", ".nv", ".ram", ".dat", ".gci",
}
# RetroArch's `<game>.state`, `.state3`, `.state.auto`, as RomM's Cloud Sync parses them.
RETROARCH_STATE = re.compile(r"\.state\d*(?:\.auto)?$", re.IGNORECASE)
# Standalone emulators' state files.
OTHER_STATE = re.compile(r"\.(?:ss\d|p2s|mln|ppst|sstate|dst|ds\d)$", re.IGNORECASE)
# A folder whose files are states whatever their extension (DuckStation writes `.sav` states).
STATE_DIRS = {"states", "savestates", "sstates", "state", "ppsspp_state"}
SAVE_ROOTS = {"saves", "savefiles", "save", "memcards", "battery"}
# Folders of a RetroArch or ES-DE install that hold no saves.
SKIP_DIRS = {
    "system", "cores", "assets", "shaders", "thumbnails", "overlays", "autoconfig", "database",
    "info", "cheats", "filters", "downloads", "logs", "screenshots", "media", "downloaded_media",
    "themes", "resources", "bios", "firmware", "playlists", "config", "recordings",
}
# Shared memory cards hold many games, so they cannot be filed under one ROM.
SHARED_CARD = re.compile(r"(?:^mcd\d+\.ps2$|\.ps2$|^memorycard[ab]\b.*\.raw$|^shared_card_\d+\.mcd$)", re.IGNORECASE)
# Saves made of a folder of files per game.
FOLDER_SAVE_DIRS = {"savedata", "card a", "card b", "nand", "sdmc", "dev_hdd0", "mlc01", "bis", "wfs"}

# Standalone emulators recognised by a folder in the path, and RomM's emulator name for each.
STANDALONE = {
    "duckstation": "duckstation", "pcsx2": "pcsx2", "dolphin": "dolphin", "dolphin-emu": "dolphin",
    "ppsspp": "ppsspp", "melonds": "melonds", "azahar": "azahar", "citra": "citra", "lime3ds": "lime3ds",
    "cemu": "cemu", "rpcs3": "rpcs3", "ryujinx": "ryujinx", "desmume": "desmume", "xemu": "xemu",
    "flycast": "flycast", "mgba": "mgba", "snes9x": "snes9x", "mesen": "mesen", "bsnes": "bsnes",
    "redream": "redream", "xenia": "xenia", "vita3k": "vita3k", "eden": "eden", "suyu": "suyu",
}
# File types that only one system uses, to settle which of two same-named ROMs a save is for.
EXT_PLATFORMS = {
    ".dsv": {"nds"}, ".eep": {"n64"}, ".mpk": {"n64"}, ".fla": {"n64"}, ".sra": {"n64"},
    ".mcr": {"psx"}, ".mcd": {"psx"}, ".bkr": {"saturn", "segacd"}, ".bcr": {"segacd"},
    ".brm": {"segacd"}, ".gci": {"ngc", "gamecube"}, ".p2s": {"ps2"}, ".ppst": {"psp"},
}
# ES-DE system folder names whose RomM platform slug differs.
ESDE_SYSTEM_ALIASES = {
    "megadrive": "genesis", "mastersystem": "sms", "gc": "ngc", "gamecube": "ngc", "pcengine": "tg16",
    "sfc": "snes", "famicom": "nes", "gbc": "gbc", "gba": "gba", "n3ds": "3ds", "psx": "psx",
}

_TAGS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}


@dataclass(frozen=True)
class LibraryGame:
    """A ROM in RomM that a save can belong to."""

    id: int
    name: str
    fs_name: str
    fs_name_no_ext: str
    platform_id: int
    platform_slug: str
    platform_fs_slug: str
    platform_name: str
    crc32s: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ExistingAsset:
    kind: str  # "save" | "state"
    rom_id: int
    emulator: str | None
    file_name: str
    md5: str | None


@dataclass(frozen=True)
class DeviceFile:
    """A file in a folder the user picked; `path` starts with the picked folder's name."""

    path: str
    size: int = 0
    md5: str | None = None
    modified: float | None = None  # seconds since the epoch

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def parts(self) -> tuple[str, ...]:
        return PurePosixPath(self.path).parts


@dataclass(frozen=True)
class DeviceRom:
    path: str
    crc32: str | None = None


@dataclass
class EmulatorHints:
    """What the RetroArch and ES-DE config files say about which emulator runs a game."""

    by_game: dict[str, str] = field(default_factory=dict)  # normalized game stem -> emulator
    by_system_game: dict[tuple[str, str], str] = field(default_factory=dict)  # (system, stem) -> emulator
    by_system: dict[str, str] = field(default_factory=dict)  # ES-DE system -> emulator


@dataclass
class Override:
    rom_id: int | None = None
    emulator: str | None = None
    rename: bool | None = None
    skip_emulator: bool = False  # the user cleared the emulator on purpose


# ---------------------------------------------------------------- names


def normalize(name: str | None) -> str:
    """A loose key for comparing game names: no tags, case, accents or punctuation."""
    if not name:
        return ""
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    t = _TAGS.sub("", t).replace("&", " and ")
    words = [_ROMAN.get(w, w) for w in re.findall(r"[a-z0-9]+", t) if w not in ("the", "a", "an")]
    return "".join(words)


def classify(path: str) -> str | None:
    """`save`, `state`, `screenshot`, `shared_card`, `folder_save`, or None for a file that is not a save."""
    p = PurePosixPath(path)
    name = p.name
    dirs = [d.lower() for d in p.parts[:-1]]
    if name.startswith(".") or any(d in SKIP_DIRS for d in dirs[1:]):
        return None
    lower = name.lower()
    if lower.endswith(".png"):
        return "screenshot" if RETROARCH_STATE.search(lower[:-4]) else None
    if any(d in FOLDER_SAVE_DIRS for d in dirs):
        return "folder_save"
    if SHARED_CARD.search(lower):
        return "shared_card"
    if RETROARCH_STATE.search(lower) or OTHER_STATE.search(lower):
        return "state"
    ext = os.path.splitext(lower)[1]
    if any(d in STATE_DIRS for d in dirs) and ext in {".sav", ".state", ".sstate"}:
        return "state"
    if ext in SAVE_EXTENSIONS:
        return "save"
    return None


def split_name(kind: str, name: str) -> tuple[str, str]:
    """(game stem, suffix) of a save or state file name, e.g. ("Game", ".state1")."""
    if kind == "screenshot":
        stem, suffix = split_name("state", name[:-4])
        return stem, suffix + name[-4:]
    m = RETROARCH_STATE.search(name) if kind == "state" else None
    if m:
        return name[: m.start()], name[m.start():]
    stem, ext = os.path.splitext(name)
    return stem, ext


def _stems_to_try(kind: str, stem: str, ext: str) -> list[str]:
    """The game names a file may be named after, most likely first."""
    stems = [stem]
    # DuckStation per-game cards: `<title>_1.mcd`.
    if ext.lower() == ".mcd" and re.search(r"_\d$", stem):
        stems.append(stem[:-2])
    return stems


def tagged_name(name: str, kind: str, when: datetime) -> str:
    """`name` with RomM's version tag (` [YYYY-MM-DD_HH-MM-SS]`) before its save or state suffix."""
    stem, suffix = split_name(kind, name)
    stem = re.sub(r" \[\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\]$", "", stem)
    return f"{stem} [{when.strftime('%Y-%m-%d_%H-%M-%S')}]{suffix}"


# ---------------------------------------------------------------- emulators


def emulator_from_path(path: str, core_dirs: Mapping[str, str], systems: Iterable[str] = ()) -> tuple[str | None, str | None]:
    """(emulator, how it was found) from where the file sits.

    RetroArch's "sort by core" puts files in `saves/<core name>/` or `states/<core name>/`; RomM's
    Cloud Sync uses the same folders, so the name is converted with its table (`core_dirs`) and kept
    verbatim when unknown, as RomM does. A folder named after a system (`systems`, RetroArch's
    "sort by content directory") is not a core."""
    parts = PurePosixPath(path).parts
    system_keys = {s.lower() for s in systems}
    for i in range(len(parts) - 2, 0, -1):
        if parts[i - 1].lower() in SAVE_ROOTS | STATE_DIRS:
            if parts[i] in core_dirs or _system_key(parts[i]) not in system_keys and parts[i].lower() not in system_keys:
                return core_dirs.get(parts[i], parts[i]), "core folder"
            break
    lowered = [p.lower() for p in parts[:-1]]
    if not any("retroarch" in p for p in lowered):
        for p in reversed(lowered):
            for key, emulator in STANDALONE.items():
                if p == key or p.startswith(key + "-") or p.startswith(key + " ") or p.startswith(key + "_"):
                    return emulator, "emulator folder"
    return None, None


def _core_from_command(command: str) -> str | None:
    m = re.search(r"([A-Za-z0-9_]+)_libretro", command)
    if m:
        return m.group(1).lower()
    m = re.search(r"%EMULATOR_([A-Z0-9_-]+)%", command)
    if m and m.group(1) != "RETROARCH":
        name = m.group(1).lower()
        return STANDALONE.get(name, name)
    return None


def parse_es_systems(text: str) -> dict[str, list[tuple[str, str]]]:
    """ES-DE system -> [(command label, emulator)], the default command first."""
    systems: dict[str, list[tuple[str, str]]] = {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return systems
    for system in root.iter("system"):
        name = (system.findtext("name") or "").strip().lower()
        commands = []
        for cmd in system.findall("command"):
            emulator = _core_from_command(cmd.text or "")
            if emulator:
                commands.append(((cmd.get("label") or "").strip(), emulator))
        if name and commands:
            systems[name] = commands
    return systems


def _label_emulator(label: str, commands: list[tuple[str, str]]) -> str | None:
    for cmd_label, emulator in commands:
        if cmd_label == label:
            return emulator
    return None


def emulator_hints(
    playlists: Iterable[str] = (),
    gamelists: Iterable[tuple[str, str]] = (),
    es_systems: Iterable[str] = (),
) -> EmulatorHints:
    """Read RetroArch playlists, ES-DE gamelists (path, text) and es_systems.xml files."""
    hints = EmulatorHints()
    for text in playlists:
        try:
            items = json.loads(text).get("items", [])
        except (ValueError, AttributeError):
            continue
        for item in items:
            core = item.get("core_path") or ""
            m = re.search(r"([A-Za-z0-9_]+)_libretro", core)
            path = (item.get("path") or "").split("#")[0].replace("\\", "/")
            if m and path:
                stem = os.path.splitext(PurePosixPath(path).name)[0]
                hints.by_game.setdefault(normalize(stem), m.group(1).lower())

    commands: dict[str, list[tuple[str, str]]] = {}
    for text in es_systems:
        for system, cmds in parse_es_systems(text).items():
            commands.setdefault(system, cmds)
    for system, cmds in commands.items():
        hints.by_system[system] = cmds[0][1]

    for path, text in gamelists:
        parts = PurePosixPath(path.replace("\\", "/")).parts
        system = parts[-2].lower() if len(parts) >= 2 else ""
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        cmds = commands.get(system, [])
        label = root.findtext("alternativeEmulator/label")
        if label and _label_emulator(label.strip(), cmds):
            hints.by_system[system] = _label_emulator(label.strip(), cmds)  # type: ignore[assignment]
        for game in root.iter("game"):
            alt = (game.findtext("altemulator") or "").strip()
            rom_path = (game.findtext("path") or "").replace("\\", "/")
            emulator = _label_emulator(alt, cmds) if alt else None
            if emulator and rom_path:
                stem = os.path.splitext(PurePosixPath(rom_path).name)[0]
                hints.by_system_game[(system, normalize(stem))] = emulator
    return hints


# ---------------------------------------------------------------- matching


def _platform_keys(game: LibraryGame) -> set[str]:
    return {game.platform_slug.lower(), game.platform_fs_slug.lower()}


def _system_key(system: str) -> str:
    s = system.lower()
    return ESDE_SYSTEM_ALIASES.get(s, s)


class Library:
    """RomM's ROMs indexed for save matching."""

    def __init__(self, games: Iterable[LibraryGame]):
        self.games = {g.id: g for g in games}
        self.exact: dict[str, list[LibraryGame]] = defaultdict(list)
        self.folded: dict[str, list[LibraryGame]] = defaultdict(list)
        self.loose: dict[str, list[LibraryGame]] = defaultdict(list)
        self.by_crc: dict[str, list[LibraryGame]] = defaultdict(list)
        for g in self.games.values():
            self.exact[g.fs_name_no_ext].append(g)
            self.folded[g.fs_name_no_ext.casefold()].append(g)
            for key in {normalize(g.fs_name_no_ext), normalize(g.name)} - {""}:
                self.loose[key].append(g)
            for crc in g.crc32s:
                self.by_crc[crc.lower()].append(g)
        self.system_keys = {k for g in self.games.values() for k in _platform_keys(g)}

    def find(self, stem: str, crcs: Iterable[str]) -> tuple[str | None, list[LibraryGame]]:
        """(how it matched, candidates) for a game name and the checksums of device ROMs with that name."""
        if self.exact.get(stem):
            return "exact", self.exact[stem]
        if self.folded.get(stem.casefold()):
            return "name", self.folded[stem.casefold()]
        by_crc = {g.id: g for crc in crcs for g in self.by_crc.get(crc.lower(), [])}
        if by_crc:
            return "checksum", list(by_crc.values())
        key = normalize(stem)
        if key and self.loose.get(key):
            return "similar", self.loose[key]
        return None, []


def narrow(candidates: list[LibraryGame], ext: str, systems: set[str]) -> list[LibraryGame]:
    """Drop candidates on the wrong system, using the file type and the system folders the save or a
    device ROM with its name sits in. Never narrows to nothing."""
    for wanted in (EXT_PLATFORMS.get(ext.lower(), set()), {_system_key(s) for s in systems} | {s.lower() for s in systems}):
        if len(candidates) <= 1 or not wanted:
            continue
        kept = [g for g in candidates if _platform_keys(g) & wanted]
        if kept:
            candidates = kept
    return candidates


# ---------------------------------------------------------------- plan


def _row_base(f: DeviceFile, kind: str) -> dict[str, Any]:
    return {
        "path": f.path,
        "name": f.name,
        "kind": kind,
        "size": f.size,
        "status": None,
        "reason": None,
        "match": None,
        "rom": None,
        "candidates": [],
        "emulator": None,
        "emulator_source": None,
        "rename": False,
        "target_name": f.name,
        "keep_both_name": None,
        "screenshot_path": None,
    }


def game_json(g: LibraryGame) -> dict[str, Any]:
    return {
        "id": g.id,
        "name": g.name,
        "fs_name": g.fs_name,
        "fs_name_no_ext": g.fs_name_no_ext,
        "platform_id": g.platform_id,
        "platform_slug": g.platform_slug,
        "platform_name": g.platform_name,
    }


def plan(
    files: Iterable[DeviceFile],
    library: Library,
    existing: Iterable[ExistingAsset],
    device_roms: Iterable[DeviceRom] = (),
    hints: EmulatorHints | None = None,
    *,
    platform_id: int | None = None,
    detect_emulator: bool = True,
    platform_emulators: Mapping[int, str] | None = None,
    core_dirs: Mapping[str, str] | None = None,
    overrides: Mapping[str, Override] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """One row per save, state or unsupported save file, saying what the import would do with it.

    Statuses: `new` (upload), `in_romm` (RomM has these exact bytes), `conflict` (RomM has a
    different file under the target name: keep both or skip, the user decides), `unmatched`,
    `ambiguous` (several games fit; the user picks), `unsupported`.
    """
    hints = hints or EmulatorHints()
    core_dirs = core_dirs or {}
    overrides = overrides or {}
    platform_emulators = platform_emulators or {}
    now = now or datetime.now(timezone.utc)
    files = list(files)

    roms_by_stem: dict[str, list[DeviceRom]] = defaultdict(list)
    for r in device_roms:
        roms_by_stem[os.path.splitext(PurePosixPath(r.path).name)[0].casefold()].append(r)

    # What each emulator has already been used for, to place saves for the same emulator.
    taken: dict[tuple[str, int], dict[tuple[str | None, str], str | None]] = defaultdict(dict)
    hashes: dict[tuple[str, int], set[str]] = defaultdict(set)
    for a in existing:
        taken[(a.kind, a.rom_id)][(a.emulator, a.file_name.casefold())] = a.md5
        if a.md5:
            hashes[(a.kind, a.rom_id)].add(a.md5.lower())

    in_batch: set[tuple[str, int, str]] = set()
    screenshots = {f.path[:-4].casefold(): f for f in files if classify(f.path) == "screenshot"}
    rows: list[dict[str, Any]] = []

    # Newest first, so when two copies of one save compete for a name the newer is the plain upload.
    for f in sorted(files, key=lambda f: -(f.modified or 0)):
        kind = classify(f.path)
        if kind in (None, "screenshot"):
            continue
        row = _row_base(f, kind)
        rows.append(row)
        if kind == "shared_card":
            row.update(status="unsupported", reason="Shared memory card: holds many games, so it can't be filed under one game yet.")
            continue
        if kind == "folder_save":
            row.update(status="unsupported", reason="Folder save (one folder of files per game): not imported yet.")
            continue

        ov = overrides.get(f.path, Override())
        stem, suffix = split_name(kind, f.name)
        ext = os.path.splitext(f.name)[1]

        # Emulator: from where the file sits, then the config files, then the user's per-system choice.
        emulator, source = emulator_from_path(f.path, core_dirs, library.system_keys)

        # Game.
        match, candidates, matched_stem = None, [], stem
        for s in _stems_to_try(kind, stem, ext):
            device = roms_by_stem.get(s.casefold(), [])
            crcs = [r.crc32 for r in device if r.crc32]
            match, candidates = library.find(s, crcs)
            if candidates:
                matched_stem = s
                break
        if platform_id is not None:
            candidates = [g for g in candidates if g.platform_id == platform_id]
        device = roms_by_stem.get(matched_stem.casefold(), [])
        systems = {PurePosixPath(r.path).parts[-2] for r in device if len(PurePosixPath(r.path).parts) >= 2}
        systems |= {d for d in f.parts[:-1] if d.lower() in library.system_keys or _system_key(d) in library.system_keys}
        candidates = narrow(candidates, ext, systems)

        if ov.rom_id is not None and ov.rom_id in library.games:
            game = library.games[ov.rom_id]
            match = "chosen"
            candidates = [game]
        if not candidates:
            row.update(status="unmatched", reason="No game in RomM has this name. Pick one to import it.")
            continue
        row["candidates"] = [game_json(g) for g in candidates]
        if len(candidates) > 1:
            row.update(status="ambiguous", match=match, reason="More than one game in RomM fits. Pick which one.")
            continue
        game = candidates[0]
        row.update(match=match, rom=game_json(game))

        if detect_emulator and not emulator:
            key = normalize(matched_stem)
            system_names = {s.lower() for s in systems} | _platform_keys(game)
            for system in system_names:
                if (system, key) in hints.by_system_game:
                    emulator, source = hints.by_system_game[(system, key)], "ES-DE game setting"
                    break
            if not emulator and key in hints.by_game:
                emulator, source = hints.by_game[key], "RetroArch playlist"
            if not emulator:
                for system in system_names:
                    if system in hints.by_system:
                        emulator, source = hints.by_system[system], "ES-DE system emulator"
                        break
        elif not detect_emulator:
            emulator, source = None, None
        if not emulator and game.platform_id in platform_emulators:
            emulator, source = platform_emulators[game.platform_id], "your choice for this system"
        if ov.emulator:
            emulator, source = ov.emulator, "your choice"
        elif ov.skip_emulator:
            emulator, source = None, None
        row.update(emulator=emulator, emulator_source=source)

        # Name: RomM's web player and Cloud Sync find a save by the ROM's file name.
        renamed = game.fs_name_no_ext + f.name[len(matched_stem):]
        rename = renamed != f.name if ov.rename is None else ov.rename and renamed != f.name
        target = renamed if rename else f.name
        row.update(rename=rename, target_name=target)
        if renamed != f.name:
            row["renamed_name"] = renamed

        if kind == "state":
            shot = screenshots.get(f.path.casefold())
            if shot:
                row["screenshot_path"] = shot.path

        bucket = (kind, game.id)
        md5 = (f.md5 or "").lower()
        if md5 and (kind, game.id, md5) in in_batch:
            row.update(status="in_romm", reason="Same file as another one in this import.")
            continue
        if md5 and md5 in hashes[bucket]:
            row.update(status="in_romm", reason="RomM already has this exact file.")
            continue
        names = taken[bucket]
        if (emulator, target.casefold()) in names:
            when = datetime.fromtimestamp(f.modified, timezone.utc) if f.modified else now
            alt = tagged_name(target, kind, when)
            while (emulator, alt.casefold()) in names:
                when += timedelta(seconds=1)
                alt = tagged_name(target, kind, when)
            row.update(status="conflict", keep_both_name=alt,
                       reason="RomM already has a different file with this name. Keep both, or skip this one.")
            names[(emulator, alt.casefold())] = md5 or None
        else:
            row["status"] = "new"
            names[(emulator, target.casefold())] = md5 or None
        if md5:
            in_batch.add((kind, game.id, md5))

    order = {"conflict": 0, "ambiguous": 1, "unmatched": 2, "new": 3, "in_romm": 4, "unsupported": 5}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["path"].casefold()))
    return rows
