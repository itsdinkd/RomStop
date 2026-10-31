"""Install a hack as a new ROM: patch, place the file, scan it in and fill its metadata from RomStop."""

import asyncio
import fcntl
import json
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from config import LIBRARY_BASE_PATH, ROM_PATCHER_MAX_FILE_SIZE_BYTES
from handler.database import db_collection_handler, db_rom_handler
from handler.database.base_handler import sync_session
from handler.filesystem import fs_resource_handler, fs_rom_handler
from logger.logger import log
from models.collection import Collection
from models.rom import Rom, RomFile
from utils.context import initialize_context
from utils.rom_patcher import apply_patch

from .archives import ArchiveError, extract_rom, is_archive
from .index import ROMSHELF_DIR
from . import modpatch
from .matching import PLATFORM_CONSOLE, header_size

INSTALLS_FILE = ROMSHELF_DIR / "installs.json"
COLLECTION_NAME = "RomStop"
OLD_COLLECTION_NAME = "Romshelf"  # installs from before the rename keep adding to it
_LOCK_FILE = ROMSHELF_DIR / "installs.lock"
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class InstallError(Exception):
    def __init__(self, message: str, code: str = "install_error", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@contextmanager
def _locked() -> Iterator[list[dict[str, Any]]]:
    """The install records, locked across API and worker processes while the block runs."""
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK_FILE.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            records = json.loads(INSTALLS_FILE.read_text()) if INSTALLS_FILE.exists() else []
        except ValueError:
            records = []
        yield records
        tmp = INSTALLS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, indent=1))
        tmp.replace(INSTALLS_FILE)
        fcntl.flock(lock, fcntl.LOCK_UN)


def all_records() -> list[dict[str, Any]]:
    try:
        return json.loads(INSTALLS_FILE.read_text())
    except (OSError, ValueError):
        return []


def output_file_name(hack: dict[str, Any], version: str | None, regions: list[str], ext: str) -> str:
    """'Title (USA) (v1.2) [RomStop].ext', tagged the way RomM's scanner reads region and version."""
    parts = [hack["title"]]
    if regions:
        parts.append(f"({', '.join(regions)})")
    if version:
        parts.append(f"({version if version.lower().startswith('v') else 'v' + version})")
    parts.append("[RomStop]")
    name = _BAD_CHARS.sub("", " ".join(parts)).strip(" .")
    return f"{name[:180]}{ext}"


def _strip_header(rom_path: Path, console: str | None, work: Path) -> tuple[Path, bytes]:
    """A headerless copy of the ROM, and the header that was removed."""
    data = rom_path.read_bytes()
    skip = header_size(console, data[:16], len(data))
    if not skip:
        return rom_path, b""
    out = work / f"headerless{rom_path.suffix}"
    out.write_bytes(data[skip:])
    return out, data[:skip]


# Emulators need the iNES / FDS header back; a SNES or PCE copier header is better left off.
_KEEP_HEADER = {"nes"}


async def install(
    *,
    hack: dict[str, Any],
    version: dict[str, Any] | None,
    rom_file: RomFile,
    base_rom: Rom,
    patch_path: Path,
    strip_header: bool,
    user_id: int,
    archive_member: str | None,
    replace: bool,
) -> dict[str, Any]:
    """Patch `rom_file` with `patch_path` and drop the result where the next scan finds it as a new ROM."""
    platform = base_rom.platform
    try:
        dest_dir = fs_rom_handler.get_roms_upload_path(platform.fs_slug)
    except ValueError as exc:
        raise InstallError(str(exc)) from exc

    src = fs_rom_handler.validate_path(rom_file.full_path)
    version_label = (version or {}).get("version") or hack.get("version")
    console = PLATFORM_CONSOLE.get(platform.slug)
    work = Path(tempfile.mkdtemp(prefix="romshelf_install_"))
    try:
        rom_path = src
        if is_archive(rom_file.file_name):
            # Patch the game file inside the archive; the hack is saved as a plain ROM next to it.
            try:
                rom_path = await asyncio.to_thread(
                    extract_rom, src, work / "rom", archive_member, ROM_PATCHER_MAX_FILE_SIZE_BYTES
                )
            except ArchiveError as exc:
                raise InstallError(str(exc), "archive") from exc
        ext = rom_path.suffix or Path(rom_file.file_name).suffix
        fs_name = output_file_name(hack, version_label, list(base_rom.regions or []), ext)
        dest = fs_rom_handler.validate_path(f"{dest_dir}/{fs_name}")
        if dest.exists() and not replace:
            raise InstallError(f"{fs_name} is already in your library.", "already_installed", 409)

        header = b""
        if strip_header:
            rom_path, header = _strip_header(rom_path, console, work)
        out = work / f"patched{ext}"
        validated = await apply_patch(rom_path, patch_path, out)
        if header and console in _KEEP_HEADER:
            patched = out.read_bytes()
            out.write_bytes(header + patched)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out), str(dest))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info(f"RomStop: installed {hack['id']} {version_label or ''} as {dest}")

    return _add_record(hack, version, version_label, base_rom, rom_file, dest_dir, fs_name, user_id, validated)


def _add_record(
    hack: dict[str, Any],
    version: dict[str, Any] | None,
    version_label: str | None,
    base_rom: Rom,
    rom_file: RomFile,
    dest_dir: str,
    fs_name: str,
    user_id: int,
    validated: bool,
) -> dict[str, Any]:
    platform = base_rom.platform
    record = {
        "id": uuid.uuid4().hex,
        "hack_id": hack["id"],
        "hack_title": hack["title"],
        "version": version_label,
        "base_rom_id": base_rom.id,
        "base_file_id": rom_file.id,
        "platform_id": platform.id,
        "fs_path": dest_dir,
        "fs_name": fs_name,
        "user_id": user_id,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "checksum_validated": validated,
        "status": "pending",
        "rom_id": None,
        # What finalize writes onto the new ROM; kept here so the worker needs no index.
        "metadata": _metadata_for(hack, version, base_rom),
    }
    with _locked() as records:
        records[:] = [r for r in records if not (r["platform_id"] == platform.id and r["fs_name"] == fs_name)]
        records.append(record)
    return record


async def install_mod(
    *,
    hack: dict[str, Any],
    version: dict[str, Any] | None,
    rom_file: RomFile,
    base_rom: Rom,
    package: Path,
    user_id: int,
    replace: bool,
) -> dict[str, Any]:
    """Install a Wii Riivolution or Vita rePatch mod onto `rom_file` as a new library entry.

    Wii: a folder "<name>.json" next to the platform's other games holding the Dolphin launcher "<name>.json" for
    the user's own disc image and the mod's files; the disc itself is left as it is. Vita: a new .vpk with the mod laid over the dump."""
    method = (hack.get("install") or {}).get("method")
    platform = base_rom.platform
    try:
        dest_dir = fs_rom_handler.get_roms_upload_path(platform.fs_slug)
    except ValueError as exc:
        raise InstallError(str(exc)) from exc
    src = fs_rom_handler.validate_path(rom_file.full_path)
    version_label = (version or {}).get("version") or hack.get("version")
    wii = method == "riivolution"
    if wii and not src.name.lower().endswith(modpatch.WII_DISC_EXTENSIONS):
        raise InstallError(
            f"Dolphin can't boot {rom_file.file_name} with a mod. Use a disc image (ISO, RVZ, WBFS, CISO, WIA or GCZ), not an archive.",
            "wrong_file",
        )
    # Wii: a folder named "<name>.json" holding "<name>.json", so ES-DE lists it as one game.
    fs_name = output_file_name(hack, version_label, list(base_rom.regions or []), ".json" if wii else ".vpk")
    dest = fs_rom_handler.validate_path(f"{dest_dir}/{fs_name}")
    if dest.exists():
        if not replace:
            raise InstallError(f"{fs_name} is already in your library.", "already_installed", 409)
        if wii and dest.is_dir():
            shutil.rmtree(dest)
    work = Path(tempfile.mkdtemp(prefix="romshelf_mod_"))
    try:
        files = await asyncio.to_thread(modpatch.unpack_mod, package, "wii" if wii else "vita", work / "mod")
        if wii:
            # The launcher points at the disc relative to its own folder, so it's built in place.
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                await asyncio.to_thread(modpatch.build_wii, disc=src, files=files, hack_id=hack["id"], title=hack["title"], folder=dest)
            except BaseException:
                shutil.rmtree(dest, ignore_errors=True)
                raise
        else:
            out = work / "patched.vpk"
            await asyncio.to_thread(modpatch.build_vita, dump=src, files=files, out=out)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(out), str(dest))
    except modpatch.ModError as exc:
        raise InstallError(str(exc), exc.code) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    log.info(f"RomStop: installed mod {hack['id']} {version_label or ''} as {dest}")
    return _add_record(hack, version, version_label, base_rom, rom_file, dest_dir, fs_name, user_id, True)


def _metadata_for(hack: dict[str, Any], version: dict[str, Any] | None, base_rom: Rom) -> dict[str, Any]:
    by = ", ".join(hack.get("authors") or [])
    lines = [hack.get("description") or hack.get("summary") or ""]
    lines.append("")
    kind = f"{hack['type']} hack" if hack.get("type") else "Romhack"
    lines.append(f"{kind} of {hack.get('baseGame')}" + (f" by {by}." if by else "."))
    if version and version.get("changelog"):
        lines.append("")
        lines.append(f"Changes in {version['version']}:")
        lines.extend(f"- {c}" for c in version["changelog"])
    compat = (hack.get("compatibility") or {}).get("summary")
    if compat:
        lines.append("")
        lines.append(f"Emulators: {compat}")
    if hack.get("pageUrl"):
        lines.append("")
        lines.append(f"RomStop: {hack['pageUrl']}")
    released = (version or {}).get("date") or hack.get("released")
    release_ms = None
    if released:
        try:
            release_ms = int(datetime.fromisoformat(str(released)[:10]).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            release_ms = None
    genres = [g for g in (hack.get("type"), hack.get("genre")) if g]
    return {
        "name": hack["title"],
        "summary": "\n".join(lines).strip(),
        "manual_metadata": {
            "genres": genres,
            "developers": hack.get("authors") or [],
            "companies": hack.get("authors") or [],
            "first_release_date": release_ms,
            "alternative_names": [f"{hack['title']} ({hack.get('baseGame')} hack)"],
        },
        "url_cover": (hack.get("screenshots") or [None])[0] or base_rom.url_cover or None,
        "url_screenshots": hack.get("screenshots") or [],
    }


def _find_rom(record: dict[str, Any]) -> Rom | None:
    with sync_session.begin() as session:
        rom_id = session.scalar(
            select(Rom.id)
            .where(Rom.platform_id == record["platform_id"], Rom.fs_name == record["fs_name"], Rom.fs_path == record["fs_path"])
            .limit(1)
        )
    return db_rom_handler.get_rom(rom_id) if rom_id else None


async def finalize(record_id: str) -> dict[str, Any] | None:
    """Write RomStop's metadata onto the ROM a scan created for an install. No-op until the scan has run."""
    record = next((r for r in all_records() if r["id"] == record_id), None)
    if not record or record["status"] == "done":
        return record
    rom = _find_rom(record)
    if not rom:
        return record

    meta = record["metadata"]
    data: dict[str, Any] = {
        "name": meta["name"],
        "summary": meta["summary"],
        "manual_metadata": meta["manual_metadata"],
    }
    locked = set(rom.locked_fields or [])
    if meta.get("url_cover"):
        try:
            path_s, path_l = await fs_resource_handler.get_cover(entity=rom, overwrite=True, url_cover=meta["url_cover"])
            data.update({"url_cover": meta["url_cover"], "path_cover_s": path_s, "path_cover_l": path_l})
            locked.add("url_cover")
        except Exception as exc:  # A missing cover must not leave the install unfinished.
            log.warning(f"RomStop: cover for {record['fs_name']} failed: {exc}")
    if meta.get("url_screenshots"):
        try:
            paths = await fs_resource_handler.get_rom_screenshots(rom=rom, overwrite=True, url_screenshots=meta["url_screenshots"])
            data.update({"path_screenshots": paths, "url_screenshots": []})
        except Exception as exc:
            log.warning(f"RomStop: screenshots for {record['fs_name']} failed: {exc}")
    data["locked_fields"] = sorted(locked)
    db_rom_handler.update_rom(rom.id, data)

    try:
        collection = db_collection_handler.get_collection_by_name(
            COLLECTION_NAME, record["user_id"]
        ) or db_collection_handler.get_collection_by_name(OLD_COLLECTION_NAME, record["user_id"])
        if not collection:
            collection = db_collection_handler.add_collection(
                Collection(
                    name=COLLECTION_NAME,
                    description="Romhacks installed from RomStop.",
                    user_id=record["user_id"],
                    is_public=False,
                )
            )
        db_collection_handler.add_roms_to_collection(collection.id, [rom.id])
    except Exception as exc:
        log.warning(f"RomStop: could not add {rom.id} to the {COLLECTION_NAME} collection: {exc}")

    with _locked() as records:
        for r in records:
            if r["id"] == record_id:
                r.update({"status": "done", "rom_id": rom.id, "finalized_at": datetime.now(timezone.utc).isoformat()})
                record = r
    log.info(f"RomStop: {record['fs_name']} is ROM {rom.id}")
    return record


async def finalize_pending() -> None:
    for r in all_records():
        if r["status"] == "pending":
            await finalize(r["id"])


@initialize_context()
async def finalize_job(record_id: str) -> None:
    """RQ entry point, queued to run after the scan that picks up the new file."""
    # The scan may still be committing; retry briefly, then leave it to the next page view.
    for _ in range(5):
        rec = await finalize(record_id)
        if not rec or rec["status"] == "done":
            return
        await asyncio.sleep(2)


def library_path_exists(record: dict[str, Any]) -> bool:
    return Path(LIBRARY_BASE_PATH, record["fs_path"], record["fs_name"]).exists()


def installed_for(hack_id: str) -> list[dict[str, Any]]:
    return [r for r in all_records() if r["hack_id"] == hack_id and library_path_exists(r)]


def forget_missing() -> None:
    """Drop records whose file is gone, so a deleted hack can be installed again."""
    with _locked() as records:
        records[:] = [r for r in records if library_path_exists(r)]

