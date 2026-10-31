"""Wii Riivolution and PS Vita rePatch mods, installed onto a library game the way the site's patcher does it.

Wii: a folder next to the disc image with a Dolphin game mod descriptor (.json) that boots the user's own
disc with the mod's Riivolution patches, plus the mod's files. The disc image is never changed or copied.

Vita: a new .vpk built from the user's dump with the mod's rePatch files swapped in, which Vita3K installs
like any game.

Mod packages are screened the same way as on the site: no desktop programs anywhere, and nothing that only
runs on a real console (taiHEN / Adrenaline plugins, Wii mods with no Riivolution XML).
"""

import json
import os
import re
import shutil
import struct
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

METHODS = ("riivolution", "vita-repatch")
WII_DISC_EXTENSIONS = (".iso", ".rvz", ".wbfs", ".ciso", ".wia", ".gcz")

_SEVEN_ZIP = shutil.which("7zz") or shutil.which("7z") or "/usr/bin/7zz"
_TIMEOUT = 300
_MAX_UNPACKED = 8 << 30
_PROGRAM = re.compile(
    r"\.(exe|com|bat|cmd|scr|msi|msp|dll|cpl|pif|ps1|psm1|vbs|vbe|wsf|wsh|hta|jar|js|jse|lnk|reg|sh|app|apk|dmg|pkg|deb|rpm|appimage|py|pyw)$",
    re.I,
)
_CONSOLE_PLUGIN = re.compile(r"\.(suprx|skprx|prx)$", re.I)
_RIIVOLUTION_XML = re.compile(r"(^|/)riivolution/[^/]+\.xml$", re.I)
_DISC_PATCH = re.compile(r"\.(xdelta|vcdiff|ppf|bps|ips|ups)$", re.I)
_TITLE_ID = re.compile(r"^[A-Z]{4}\d{5}$")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ModError(Exception):
    def __init__(self, message: str, code: str = "mod_error") -> None:
        super().__init__(message)
        self.code = code


def program_reason(name: str, head: bytes) -> str | None:
    """Why this file is a desktop program, or None. ELF files built for the Wii (PowerPC) or Vita (ARM) are fine."""
    if _PROGRAM.search(name):
        return f"{name} is a program or script"
    if head[:2] == b"MZ":
        return f"{name} is a Windows program"
    if head[:4] == b"\x7fELF" and len(head) >= 20:
        machine = struct.unpack("<H" if head[5] == 1 else ">H", head[18:20])[0]
        if machine not in (20, 40):
            return f"{name} is a desktop program"
    return None


def console_only_reason(console: str, paths: list[str]) -> str | None:
    plugin = next((p for p in paths if _CONSOLE_PLUGIN.search(p)), None)
    if plugin:
        return f"{plugin.rsplit('/', 1)[-1]} is a console plugin, which emulators don't load"
    if console == "wii" and not any(_RIIVOLUTION_XML.search(p) or _DISC_PATCH.search(p) for p in paths):
        return "it has no Riivolution XML or disc patch for Dolphin to load"
    return None


def _kind(path: Path) -> str | None:
    with path.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c") or head.startswith(b"Rar!"):
        return "7z"
    return None


def unpack_mod(package: Path, console: str, out: Path) -> dict[str, Path]:
    """Unpack a mod package (zip, 7z or rar) into `out`: {path in the package: file}. Refuses programs and
    console-only mods; nothing in the package is ever run."""
    kind = _kind(package)
    if not kind:
        raise ModError(f"{package.name} isn't a zip, 7z or rar mod package.")
    out.mkdir(parents=True, exist_ok=True)
    root = out.resolve()
    if kind == "zip":
        try:
            with zipfile.ZipFile(package) as zf:
                if sum(i.file_size for i in zf.infolist()) > _MAX_UNPACKED:
                    raise ModError("The mod is too large to unpack.")
                for info in zf.infolist():
                    name = info.filename.replace("\\", "/")
                    if info.is_dir() or name.startswith("__MACOSX/"):
                        continue
                    target = (out / name).resolve()
                    if not target.is_relative_to(root):
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise ModError(f"{package.name} is not a readable zip.") from exc
    else:
        try:
            subprocess.run(  # trunk-ignore(bandit/B603)
                [_SEVEN_ZIP, "x", "-y", "-snld", f"-o{out}", str(package)], capture_output=True, check=True, timeout=_TIMEOUT
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ModError(f"Could not unpack {package.name}.") from exc
    files: dict[str, Path] = {}
    for f in sorted(out.rglob("*")):
        if not f.is_file() or f.is_symlink() or not f.resolve().is_relative_to(root):
            continue
        rel = f.relative_to(out).as_posix()
        with f.open("rb") as fh:
            why = program_reason(f.name, fh.read(64))
        if why:
            raise ModError(f"This mod includes a program ({why}), so RomStop won't use it.", "unsafe")
        files[rel] = f
    if not files:
        raise ModError(f"{package.name} is empty.")
    why = console_only_reason(console, list(files))
    if why:
        raise ModError(f"This mod only runs on a real console: {why}.", "unsafe")
    return files


# ---- Wii -------------------------------------------------------------------------------------------


def wii_game_id(head: bytes) -> str | None:
    """The 6-character game id from the first bytes of an ISO, WBFS, RVZ/WIA or CISO image."""

    def at(o: int) -> str | None:
        s = head[o : o + 6].decode("ascii", "replace")
        return s if re.fullmatch(r"[A-Z0-9]{6}", s) else None

    magic = head[:4]
    if magic == b"WBFS":
        return at(0x200)
    if magic in (b"RVZ\x01", b"WIA\x01"):
        return at(0x58)
    if magic == b"CISO":
        return at(0x8000)
    return at(0)


def read_wii_game_id(disc: Path) -> str | None:
    with disc.open("rb") as fh:
        return wii_game_id(fh.read(0x8006))


@dataclass
class RiivolutionXml:
    game: str | None
    regions: list[str]
    root: str
    # [(section name, [(option name, option id, default choice)])]
    sections: list[tuple[str, list[tuple[str, str | None, int]]]] = field(default_factory=list)


def parse_riivolution(text: str) -> RiivolutionXml:
    if "<!ENTITY" in text:
        raise ModError("The mod's Riivolution XML declares entities, which RomStop doesn't read.")
    try:
        doc = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ModError(f"The mod's Riivolution XML can't be read ({exc}).") from exc
    ident = doc.find("id")
    sections = []
    for s in doc.iterfind("options/section"):
        options = []
        for o in s.iterfind("option"):
            try:
                default = int(o.get("default") or 0)
            except ValueError:
                default = 0
            options.append((o.get("name") or "", o.get("id"), default or (1 if o.find("choice") is not None else 0)))
        sections.append((s.get("name") or "", options))
    return RiivolutionXml(
        game=ident.get("game") if ident is not None else None,
        regions=[r.get("type") or "" for r in ident.iterfind("region")] if ident is not None else [],
        root=doc.get("root") or "",
        sections=sections,
    )


def xml_matches_game(x: RiivolutionXml, game_id: str | None) -> bool:
    if not game_id or not x.game:
        return True
    if not game_id.startswith(x.game):
        return False
    return not x.regions or game_id[3] in x.regions


def sd_root_of(xml_path: str) -> str:
    """The folder that holds riivolution/ (the SD card root the XML's paths are relative to)."""
    i = xml_path.lower().rfind("riivolution/")
    return xml_path[:i] if i >= 0 else ""


def safe_name(s: str) -> str:
    return _BAD_CHARS.sub("", s).strip(" .")[:150] or "mod"


@dataclass
class WiiInstall:
    folder: Path
    launcher: str
    patches: int


def build_wii(*, disc: Path, files: dict[str, Path], hack_id: str, title: str, folder: Path) -> WiiInstall:
    """Write `folder` with "<title>.json" (Dolphin's game mod descriptor, pointing at `disc` by relative path)
    and the mod's files under RomStop/<hack id>/.

    A folder named like "<name>.json" gets a launcher of the same name, which ES-DE shows as one game (it treats
    a folder named with a game extension as that file) and RomM still lists as its own entry."""
    game_id = read_wii_game_id(disc)
    patches = []
    other = set()
    mod_dir = f"RomStop/{safe_name(hack_id)}/"
    for path, f in files.items():
        if not _RIIVOLUTION_XML.search(path):
            continue
        x = parse_riivolution(f.read_text("utf-8", "replace"))
        if not xml_matches_game(x, game_id):
            if x.game:
                other.add(x.game + (f" ({'/'.join(x.regions)})" if x.regions else ""))
            continue
        options = [
            {"section-name": s, **({"option-id": oid} if oid else {}), "option-name": name, "choice": choice}
            for s, opts in x.sections
            for name, oid, choice in opts
            if choice
        ]
        patches.append({"xml": mod_dir + path, "root": (mod_dir + sd_root_of(path)).rstrip("/"), "options": options})
    if not patches:
        if other:
            raise ModError(f"This mod is for {', '.join(sorted(other))}, but this disc is {game_id}.", "wrong_game")
        raise ModError("This mod has no Riivolution XML, so Dolphin has nothing to load.")
    launcher = folder.name if folder.name.lower().endswith(".json") else f"{safe_name(title)}.json"
    folder.mkdir(parents=True, exist_ok=True)
    for path, f in files.items():
        target = folder / mod_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, target)
    descriptor = {
        "type": "dolphin-game-mod-descriptor",
        "version": 1,
        # Relative, so the library can be mounted anywhere (RomM's container, a PC, a handheld).
        "base-file": Path(os.path.relpath(disc, folder)).as_posix(),
        "display-name": title,
        "riivolution": {"patches": patches},
    }
    (folder / launcher).write_text(json.dumps(descriptor, indent=2))
    return WiiInstall(folder, launcher, len(patches))


# ---- PS Vita ---------------------------------------------------------------------------------------


def sfo_title_id(sfo: bytes) -> str | None:
    if len(sfo) < 20 or sfo[:4] != b"\x00PSF":
        return None
    keys, data, count = struct.unpack_from("<III", sfo, 8)
    for i in range(count):
        e = 20 + i * 16
        if e + 16 > len(sfo):
            break
        key_off, _fmt, length, _max, data_off = struct.unpack_from("<HHIII", sfo, e)
        k = keys + key_off
        if sfo[k : sfo.index(b"\x00", k)] == b"TITLE_ID":
            v = data + data_off
            return sfo[v : v + length].split(b"\x00", 1)[0].decode("ascii", "replace")
    return None


def repatch_titles(paths: list[str]) -> dict[str, tuple[str, list[str]]]:
    """The rePatch folders in a mod package, by title id: {id: (prefix, files)}."""
    out: dict[str, tuple[str, list[str]]] = {}
    for p in paths:
        parts = p.split("/")
        i = next((k for k in range(1, len(parts)) if parts[k - 1].lower() == "repatch" and _TITLE_ID.match(parts[k])), -1)
        if i < 0:
            i = next(
                (k for k in range(len(parts) - 1) if _TITLE_ID.match(parts[k]) and (k == 0 or parts[k - 1].lower() != "readdcont")),
                -1,
            )
        if i < 0 or i == len(parts) - 1:
            continue
        prefix = "/".join(parts[: i + 1]) + "/"
        entry = out.setdefault(parts[i], (prefix, []))
        if p.startswith(entry[0]):
            entry[1].append(p)
    return out


@dataclass
class VitaInstall:
    title_id: str
    replaced: int
    added: int


def build_vita(*, dump: Path, files: dict[str, Path], out: Path) -> VitaInstall:
    """Write `out`, a .vpk of the user's dump (a .vpk or a zip of the game folder) with the mod's rePatch files
    laid over it. The dump is not changed."""
    try:
        src = zipfile.ZipFile(dump)
    except zipfile.BadZipFile as exc:
        raise ModError(f"{dump.name} isn't a .vpk or zip of a Vita game.", "wrong_game") from exc
    with src:
        infos = [i for i in src.infolist() if not i.is_dir()]
        sfo = next((i for i in infos if re.search(r"(^|/)sce_sys/param\.sfo$", i.filename, re.I)), None)
        if not sfo:
            raise ModError("This doesn't look like a Vita game dump: there's no sce_sys/param.sfo inside.", "wrong_game")
        base = sfo.filename[: -len("sce_sys/param.sfo")]
        title_id = sfo_title_id(src.read(sfo))
        if not title_id:
            raise ModError("Couldn't read the game's title id from param.sfo.", "wrong_game")
        titles = repatch_titles(list(files))
        if title_id not in titles:
            ids = ", ".join(titles)
            raise ModError(f"This mod is for {ids}, but this game is {title_id}." if ids else "The mod has no rePatch folder.", "wrong_game")
        prefix, mod_paths = titles[title_id]
        swaps = {base + p[len(prefix) :]: files[p] for p in mod_paths}
        replaced = 0
        tmp = out.with_suffix(".part")
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as dst:
            for info in infos:
                swap = swaps.pop(info.filename, None)
                if swap:
                    dst.write(swap, info.filename, zipfile.ZIP_DEFLATED, compresslevel=1)
                    replaced += 1
                    continue
                with src.open(info) as r, dst.open(_copy_info(info), "w", force_zip64=info.file_size > 0x7FFFFFFF) as w:
                    shutil.copyfileobj(r, w, 1 << 20)
            for name, f in swaps.items():
                dst.write(f, name, zipfile.ZIP_DEFLATED, compresslevel=1)
        tmp.replace(out)
    return VitaInstall(title_id, replaced, len(swaps))


def _copy_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    z = zipfile.ZipInfo(info.filename, info.date_time)
    z.compress_type = zipfile.ZIP_STORED if info.compress_type == zipfile.ZIP_STORED else zipfile.ZIP_DEFLATED
    z._compresslevel = 1  # compress_level is only public from Python 3.13
    z.external_attr = info.external_attr
    return z
