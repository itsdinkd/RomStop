"""ROMs kept inside .zip, .7z or .rar archives: find the game file inside and pull it out for patching.

Emulators read archived ROMs, so libraries often keep them that way. RomM hashes the largest file in an
archive, so the same file is the one RomStop matches and patches.
"""

import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

ARCHIVE_EXTENSIONS = (".zip", ".7z", ".rar")
_SEVEN_ZIP = shutil.which("7zz") or shutil.which("7z") or "/usr/bin/7zz"
_TIMEOUT = 120
# Files that sit next to a ROM in an archive but are never the game itself.
_NOT_A_ROM = {
    ".txt", ".nfo", ".diz", ".md", ".pdf", ".doc", ".rtf", ".htm", ".html", ".url", ".xml", ".dat",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".sav", ".srm", ".state", ".sta", ".cht",
    ".ips", ".bps", ".ups", ".xdelta", ".ppf", ".aps",
}


class ArchiveError(Exception):
    pass


@dataclass(frozen=True)
class Member:
    name: str
    size: int


def is_archive(file_name: str) -> bool:
    return file_name.lower().endswith(ARCHIVE_EXTENSIONS)


def _list(path: Path) -> list[Member]:
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                if any(i.flag_bits & 0x1 for i in zf.infolist()):
                    raise ArchiveError("The ROM archive is encrypted.")
                return [Member(i.filename, i.file_size) for i in zf.infolist() if not i.is_dir()]
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"{path.name} is not a readable zip file.") from exc
    try:
        out = subprocess.run(
            [_SEVEN_ZIP, "l", "-slt", "-ba", str(path)], capture_output=True, text=True, timeout=_TIMEOUT, check=True
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise ArchiveError(f"Could not read {path.name}.") from exc
    members, entry = [], {}
    for line in [*out.splitlines(), ""]:
        if not line.strip():
            if entry.get("Path") and entry.get("Folder", "-") != "+":
                members.append(Member(entry["Path"], int(entry.get("Size") or 0)))
            entry = {}
        elif " = " in line:
            k, v = line.split(" = ", 1)
            entry[k.strip()] = v
    return members


def rom_member(path: Path, wanted: str | None = None) -> Member:
    """The game file inside the archive: `wanted` when given, else the largest file that isn't a readme,
    image, save or patch."""
    members = _list(path)
    if wanted:
        for m in members:
            if m.name == wanted:
                return m
        raise ArchiveError(f"{wanted} is not in {path.name}.")
    games = [m for m in members if Path(m.name).suffix.lower() not in _NOT_A_ROM] or members
    if not games:
        raise ArchiveError(f"{path.name} is empty.")
    return max(games, key=lambda m: m.size)


def read_rom(path: Path, max_bytes: int | None = None) -> bytes:
    """The game file's bytes, read straight from the archive without writing it to disk."""
    member = rom_member(path)
    if max_bytes is not None and member.size > max_bytes:
        raise ArchiveError(f"{Path(member.name).name} is too large to read.")
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                return zf.read(member.name)
        except (zipfile.BadZipFile, KeyError) as exc:
            raise ArchiveError(f"{path.name} is not a readable zip file.") from exc
    try:
        return subprocess.run(
            [_SEVEN_ZIP, "e", "-so", str(path), member.name], capture_output=True, timeout=_TIMEOUT, check=True
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise ArchiveError(f"Could not unpack {path.name}.") from exc


def extract_rom(path: Path, work: Path, wanted: str | None = None, max_bytes: int | None = None) -> Path:
    """Copy the game file out of the archive into `work` and return its path. The archive is not changed."""
    member = rom_member(path, wanted)
    if max_bytes is not None and member.size > max_bytes:
        raise ArchiveError(f"{Path(member.name).name} is too large to patch.")
    work.mkdir(parents=True, exist_ok=True)
    # Only the base name is kept, so names like ../x can't escape the work folder.
    target = work / (Path(member.name).name or "rom.bin")
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf, zf.open(member.name) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return target
    out = work / "unpacked"
    try:
        subprocess.run(
            [_SEVEN_ZIP, "e", "-y", "-snld", f"-o{out}", str(path), member.name],
            capture_output=True, timeout=_TIMEOUT, check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ArchiveError(f"Could not unpack {path.name}.") from exc
    found = [f for f in out.iterdir() if f.is_file() and not f.is_symlink()] if out.is_dir() else []
    if len(found) != 1:
        raise ArchiveError(f"Could not unpack {Path(member.name).name} from {path.name}.")
    found[0].replace(target)
    return target
