"""RomStop: romhacks for the ROMs in the library, installed as new ROMs through RomM's patcher."""

import asyncio
import shutil
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Body, File, Form, HTTPException
from fastapi import Path as PathVar
from fastapi import Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from decorators.auth import protected_route
from endpoints.sockets.scan import ScanPayload, queue_scan
from handler.auth.constants import Scope
from handler.auth.dependencies import get_permissions
from handler.database import db_rom_handler, db_save_handler, db_state_handler
from handler.database.base_handler import sync_session
from handler.filesystem import fs_asset_handler
from handler.redis_handler import low_prio_queue
from handler.romshelf import counters as romshelf_counters
from handler.romshelf import index as romshelf_index
from handler.romshelf import installs as romshelf_installs
from handler.romshelf import modpatch as romshelf_modpatch
from handler.romshelf import saved as romshelf_saved
from handler.romshelf import saves as romshelf_saves
from handler.romshelf.library import hashing_pending, library_rom, library_roms
from handler.romshelf.matching import (
    LEVEL_ORDER,
    LibraryMatcher,
    LibraryRom,
    Match,
    MatchLevel,
    match_rom,
    needed_rom_label,
)
from handler.romshelf.patch_source import NeedsChoice, PatchSourceError, download, patches_in, pick, write_upload
from handler.scan_handler import ScanType
from handler.sync.retroarch.emulator_names import RETROARCH_DIR_BY_ROMM_EMULATOR, ROMM_EMULATOR_BY_RETROARCH_DIR
from logger.logger import log
from models.rom import Rom
from utils.rom_patcher import PatcherError, PatcherInputError
from utils.router import APIRouter

router = APIRouter(prefix="/romshelf", tags=["romshelf"])

_SUMMARY_FIELDS = (
    "id",
    "title",
    "console",
    "baseGame",
    "baseRom",
    "authors",
    "type",
    "genre",
    "version",
    "released",
    "updated",
    "summary",
    "rating",
    "favs",
    "tags",
    "pageUrl",
    "downloads",
)


def _rom_json(rom: LibraryRom) -> dict[str, Any]:
    return {
        "id": rom.id,
        "name": rom.name or rom.fs_name_no_tags,
        "fs_name": rom.fs_name,
        "platform_id": rom.platform_id,
        "platform_slug": rom.platform_slug,
        "regions": list(rom.regions),
        "revision": rom.revision,
        "files": [
            {
                "id": f.id,
                "file_name": f.file_name,
                "size": f.size,
                "crc32": f.crc32,
                "sha1": f.sha1,
                "headerless_crc32": f.headerless_crc32,
                "headerless_sha1": f.headerless_sha1,
            }
            for f in rom.files
        ],
    }


def _match_json(m: Match, rom: LibraryRom | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"level": m.level.value, "reason": m.reason, "rom_id": m.rom_id, "file_id": m.file_id, "strip_header": m.strip_header}
    if rom:
        out["rom"] = _rom_json(rom)
    return out


def _version_levels(hack: dict[str, Any], rom: LibraryRom) -> dict[str, str]:
    """Each version's fit to `rom`, since a version may need a different base ROM than the current one."""
    out = {}
    for v in hack.get("versions") or []:
        if v.get("version"):
            out[v["version"]] = match_rom(hack, rom, v.get("baseRom") or hack.get("baseRom")).level.value
    return out


def _installs_by_hack() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in romshelf_installs.all_records():
        if romshelf_installs.library_path_exists(r):
            out.setdefault(r["hack_id"], []).append(_install_json(r))
    return out


def _install_json(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r.get(k) for k in ("id", "hack_id", "version", "rom_id", "status", "fs_name", "base_rom_id", "installed_at", "checksum_validated")}


async def _hack(hack_id: str) -> dict[str, Any]:
    index = await romshelf_index.get_index()
    hack = index["byId"].get(hack_id)
    if not hack:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No RomStop hack {hack_id}")
    return hack


def _status() -> dict[str, Any]:
    # `hashing`: library ROMs still being checked for copier/iNES headers in the background.
    return {**romshelf_index.status(), "hashing": hashing_pending()}


@protected_route(router.get, "/status", [Scope.ROMS_READ])
async def get_status(request: Request) -> dict[str, Any]:
    """Where the index comes from and when it was last fetched."""
    await romshelf_index.get_index()
    return _status()


@protected_route(router.post, "/refresh", [Scope.ROMS_WRITE])
async def refresh_index(request: Request) -> dict[str, Any]:
    """Fetch the index from the RomStop site now."""
    await romshelf_index.get_index(force=True)
    return _status()


def _list_hacks(index: dict[str, Any], can_see: Any, user_id: int) -> dict[str, Any]:
    roms = library_roms(can_see, wait_for_hashes=False)
    saved = set(romshelf_saved.saved_ids(user_id))
    matcher = LibraryMatcher(roms)
    installs = _installs_by_hack()
    hacks = []
    for hack in index["hacks"]:
        matches = matcher.matches(hack)
        best = matches[0].level.value if matches else MatchLevel.NONE.value
        summary = {k: hack.get(k) for k in _SUMMARY_FIELDS}
        summary["cover"] = (hack.get("screenshots") or [None])[0]
        summary["versionCount"] = len(hack.get("versions") or [])
        summary["downloadable"] = bool(patch_urls(hack, None))
        hacks.append(
            {
                **summary,
                "match": best,
                "matches": [{"rom_id": m.rom_id, "level": m.level.value} for m in matches[:20]],
                "installs": installs.get(hack["id"], []),
                "saved": hack["id"] in saved,
            }
        )
    return {
        "status": _status(),
        "consoles": index.get("consoles", {}),
        "platform_ids": sorted({r.platform_id for r in roms}),
        "hacks": hacks,
    }


@protected_route(router.get, "/hacks", [Scope.ROMS_READ])
async def list_hacks(request: Request) -> dict[str, Any]:
    """Every hack in the index, with how well the library's ROMs fit each one."""
    index = await romshelf_index.get_index()
    await romshelf_installs.finalize_pending()
    # Reading the library and matching it is blocking work; keep it off the event loop.
    return await asyncio.to_thread(_list_hacks, index, get_permissions(request).can_see_rom, request.user.id)


def _hack_matches(hack: dict[str, Any], can_see: Any) -> list[dict[str, Any]]:
    roms = [r for r in library_roms(can_see, wait_for_hashes=False) if r.console == hack.get("console")]
    by_id = {r.id: r for r in roms}
    return [
        {**_match_json(m, by_id[m.rom_id]), "versions": _version_levels(hack, by_id[m.rom_id])}
        for m in LibraryMatcher(roms).matches(hack)
    ]


@protected_route(router.get, "/hacks/{hack_id}", [Scope.ROMS_READ])
async def get_hack(request: Request, hack_id: Annotated[str, PathVar()]) -> dict[str, Any]:
    """One hack in full, with the library ROMs it fits."""
    hack = await _hack(hack_id)
    await romshelf_installs.finalize_pending()
    matches = await asyncio.to_thread(_hack_matches, hack, get_permissions(request).can_see_rom)
    saved = await asyncio.to_thread(romshelf_saved.saved_ids, request.user.id)
    return {
        "hack": hack_payload(hack),
        "needed": needed_rom_label(hack),
        "matches": matches,
        "installs": _installs_by_hack().get(hack_id, []),
        "saved": hack_id in saved,
    }


@protected_route(router.get, "/saved", [Scope.ROMS_USER_READ])
async def get_saved(request: Request) -> dict[str, Any]:
    """The signed-in user's saved hacks (Save Rom Hack), newest first."""
    return {"saved": await asyncio.to_thread(romshelf_saved.saved_ids, request.user.id)}


@protected_route(router.put, "/saved/{hack_id}", [Scope.ROMS_USER_WRITE])
async def save_hack(request: Request, hack_id: Annotated[str, PathVar()]) -> dict[str, Any]:
    """Save a hack to come back to, say once its game is in the library."""
    await _hack(hack_id)
    await asyncio.to_thread(romshelf_saved.set_saved, request.user.id, hack_id, True)
    return {"id": hack_id, "saved": True}


@protected_route(router.delete, "/saved/{hack_id}", [Scope.ROMS_USER_WRITE])
async def unsave_hack(request: Request, hack_id: Annotated[str, PathVar()]) -> dict[str, Any]:
    """Take a hack off the saved list. Works for hacks the index no longer has."""
    await asyncio.to_thread(romshelf_saved.set_saved, request.user.id, hack_id, False)
    return {"id": hack_id, "saved": False}


@protected_route(router.get, "/counts/{hack_id}", [Scope.ROMS_READ])
async def get_counts(request: Request, hack_id: Annotated[str, PathVar()]) -> dict[str, Any]:
    """How often this RomM installed (downloads) and played (play sessions of the installed ROMs) a hack."""
    await _hack(hack_id)
    return {"counts": await asyncio.to_thread(romshelf_counters.counts, hack_id, romshelf_installs.all_records())}


def _count_download(hack_id: str) -> None:
    try:
        romshelf_counters.add_download(hack_id)
    except sqlite3.Error as exc:  # A count is never worth failing an install over.
        log.warning(f"RomStop: could not count the download of {hack_id}: {exc}")


# Download servers that turn away automated requests (robots.txt "Disallow: /" or a referrer check).
# RomM never fetches from them; the user can still click through and drop the file.
_MANUAL_ONLY_HOSTS = ("dl.vimm.net", "romhacking.net")


def manual_only(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _MANUAL_ONLY_HOSTS)


# When a version lists one patch in several formats, the first of these is tried first. BPS and UPS
# carry checksums, so the wrong ROM fails loudly instead of producing a broken game.
_FORMAT_PREFERENCE = (".bps", ".ups", ".xdelta", ".vcdiff", ".ppf", ".aps", ".rup", ".bdf", ".pmsr", ".ips")


def _listed(hack: dict[str, Any], chosen: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(name, url) of the patches RomStop lists for the chosen version."""
    versions = hack.get("versions") or []
    is_current = chosen is None or (bool(versions) and chosen is versions[0])
    want = (chosen or {}).get("version") or hack.get("version")
    return [
        (p.get("name") or p["url"].rsplit("/", 1)[-1], p["url"])
        for p in hack.get("patches") or []
        if p.get("url") and (p.get("version") == want if p.get("version") else is_current)
    ]


def patch_urls(hack: dict[str, Any], chosen: dict[str, Any] | None, choice: str | None = None, ask: bool = False) -> list[str]:
    """Where to fetch the chosen version's patch, best first: RomStop's mirror, then the source site's file.
    The server fetches these, so the user never has to visit another site.

    With `choice` naming one of the listed patches, only that one. With `ask`, raises NeedsChoice when the
    version lists different patches (say, a main and an alternate edition) and none was chosen."""
    listed = _listed(hack, chosen)
    picked = [url for name, url in listed if choice and name == choice]
    if picked:
        return picked[:1]
    if ask and len({PurePosixPath(name).stem for name, _ in listed}) > 1:
        raise NeedsChoice([name for name, _ in listed])
    rank = {ext: i for i, ext in enumerate(_FORMAT_PREFERENCE)}
    urls = [url for _, url in sorted(listed, key=lambda n: rank.get(PurePosixPath(n[0]).suffix.lower(), len(rank)))]
    versions = hack.get("versions") or []
    if (chosen is None or (bool(versions) and chosen is versions[0])) and hack.get("downloadUrl") and not manual_only(hack["downloadUrl"]):
        urls.append(hack["downloadUrl"])
    return list(dict.fromkeys(urls))


async def _fetch_first(urls: list[str], work: Path) -> Path:
    """Download the first source that answers. A download holding a program stops the search."""
    last: PatchSourceError | None = None
    for i, url in enumerate(urls):
        dest = work / f"src-{i}"
        dest.mkdir()
        try:
            return await download(url, dest)
        except PatchSourceError as exc:
            log.info(f"RomStop: patch source {url} failed: {exc}")
            last = exc
    raise last or PatchSourceError(
        "RomStop has no download for this version yet. If you have the patch, drop it here.",
        "browser_download",
    )


def hack_payload(hack: dict[str, Any]) -> dict[str, Any]:
    patches = hack.get("patches") or []
    formats = list(dict.fromkeys(p["format"] for p in patches if p.get("format")))
    # Versions RomStop has a patch for, so older versions without one can say so up front.
    current = ((hack.get("versions") or [{}])[0]).get("version") or hack.get("version")
    have = list(dict.fromkeys(p.get("version") or current for p in patches if p.get("url")))
    return {
        **{k: v for k, v in hack.items() if k not in ("patches", "page")},
        "patchFormats": formats,
        "patchVersions": [v for v in have if v],
        # Mod packages (Wii / Vita) are downloaded by the user: RomStop's mirror first, else the source's file.
        "installUrl": ((patches[0].get("url") if patches and not hack.get("noMirror") else None) or hack.get("downloadUrl"))
        if hack.get("install")
        else None,
    }


@protected_route(router.get, "/roms/{rom_id}", [Scope.ROMS_READ])
async def get_rom_hacks(request: Request, rom_id: Annotated[int, PathVar(ge=1)]) -> dict[str, Any]:
    """The hacks made for this ROM's game, and whether this exact ROM is the one each needs."""
    rom_row = db_rom_handler.get_rom(rom_id)
    if not rom_row or not get_permissions(request).can_see_rom(rom_row):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ROM {rom_id} not found")
    rom = await asyncio.to_thread(library_rom, rom_id)
    index = await romshelf_index.get_index()
    await romshelf_installs.finalize_pending()
    installs = _installs_by_hack()
    own_install = next((r for r in romshelf_installs.all_records() if r.get("rom_id") == rom_id), None)

    hacks = []
    if rom and rom.console:
        for hack in index["hacks"]:
            if hack.get("console") != rom.console:
                continue
            m = match_rom(hack, rom)
            if m.level == MatchLevel.NONE:
                continue
            hacks.append(
                {
                    "hack": hack_payload(hack),
                    "needed": needed_rom_label(hack),
                    "match": _match_json(m),
                    "versions": _version_levels(hack, rom),
                    "installs": installs.get(hack["id"], []),
                }
            )
    hacks.sort(key=lambda h: (LEVEL_ORDER[MatchLevel(h["match"]["level"])], h["hack"]["title"].lower()))
    source_hack = index["byId"].get(own_install["hack_id"]) if own_install else None
    return {
        "rom": _rom_json(rom) if rom else None,
        "supported": bool(rom and rom.console),
        # Set when this ROM is itself a hack installed from RomStop.
        "installed_from": _install_json(own_install) if own_install else None,
        "installed_hack": (
            {k: source_hack.get(k) for k in ("id", "title", "version", "pageUrl", "baseGame")} if source_hack else None
        ),
        "hacks": hacks,
        "status": _status(),
    }


def _error(exc: Exception, status_code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    detail: dict[str, Any] = {"message": str(exc), "code": getattr(exc, "code", "error")}
    if isinstance(exc, NeedsChoice):
        detail["candidates"] = exc.candidates
    return HTTPException(status_code=getattr(exc, "status", status_code), detail=detail)


@protected_route(router.post, "/install", [Scope.ROMS_WRITE])
async def install_hack(
    request: Request,
    hack_id: Annotated[str, Form()],
    rom_id: Annotated[int, Form(ge=1)],
    file_id: Annotated[int | None, Form()] = None,
    version: Annotated[str | None, Form()] = None,
    patch_member: Annotated[str | None, Form(description="Which patch to use when the download holds several.")] = None,
    archive_member: Annotated[str | None, Form(description="File inside a ZIP ROM to patch.")] = None,
    replace: Annotated[bool, Form()] = False,
    patch_file: Annotated[UploadFile | None, File(description="The patch or its archive, when the site needs a browser download.")] = None,
) -> dict[str, Any]:
    """Download the hack's patch, apply it to one of this ROM's files and add the result to the library as a new ROM.
    Wii Riivolution and Vita rePatch mods are laid over the game instead (a Dolphin launcher, or a patched .vpk)."""
    hack = await _hack(hack_id)
    method = (hack.get("install") or {}).get("method")
    if method and method not in romshelf_modpatch.METHODS:
        # SD-card folders and console plugins aren't something to lay over one game file.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": f"{hack['title']} is installed on the console or emulator, not patched. Follow the install steps on its page.", "code": "install_only"},
        )
    perms = get_permissions(request)
    base_rom = db_rom_handler.get_rom(rom_id)
    if not base_rom or not perms.can_see_rom(base_rom):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ROM {rom_id} not found")
    lib = await asyncio.to_thread(library_rom, rom_id)
    if not lib:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ROM {rom_id} not found")

    versions = hack.get("versions") or []
    chosen = next((v for v in versions if v.get("version") == version), None) if version else (versions[0] if versions else None)
    if version and not chosen and version != hack.get("version"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"message": f"{hack['title']} has no version {version}", "code": "bad_version"})
    base = (chosen or {}).get("baseRom") or hack.get("baseRom")
    m = match_rom(hack, lib, base)

    target_file_id = file_id or m.file_id or (lib.files[0].id if len(lib.files) == 1 else None)
    rom_file = next((f for f in base_rom.files if f.id == target_file_id), None)
    if not rom_file:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"message": "Pick which of this ROM's files to patch.", "code": "choose_file"})
    if rom_file.missing_from_fs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"message": f"{rom_file.file_name} is missing from disk.", "code": "missing_file"})

    if method:
        return await _install_mod(request, hack, chosen, base_rom, rom_file, patch_file, replace, m)

    work = Path(tempfile.mkdtemp(prefix="romshelf_patch_"))
    try:
        if patch_file is not None:
            src = write_upload(await patch_file.read(), patch_file.filename or "patch", work)
        else:
            src = await _fetch_first(patch_urls(hack, chosen, patch_member, ask=True), work)
        # patch_member names either one of the listed patches or a file inside the download.
        listed_names = {name for name, _ in _listed(hack, chosen)}
        patch = pick(patches_in(src, work), None if patch_member in listed_names else patch_member)
        record = await romshelf_installs.install(
            hack=hack,
            version=chosen,
            rom_file=rom_file,
            base_rom=base_rom,
            patch_path=patch.path,
            strip_header=m.strip_header,
            user_id=request.user.id,
            archive_member=archive_member,
            replace=replace,
        )
    except (PatchSourceError, romshelf_installs.InstallError) as exc:
        raise _error(exc) from exc
    except PatcherInputError as exc:
        raise _error(exc) from exc
    except PatcherError as exc:
        log.error(f"RomStop: patching {hack_id} failed: {exc}")
        raise HTTPException(status_code=500, detail={"message": "Patching failed. Is this the right ROM for the hack?", "code": "patch_failed"}) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)

    await _scan_then_finalize(request, base_rom.platform_id, record)
    _count_download(hack_id)
    return {**_install_json(record), "needed": needed_rom_label(hack, base), "match": m.level.value}


async def _install_mod(request: Request, hack: dict[str, Any], chosen: dict[str, Any] | None, base_rom: Any, rom_file: Any, patch_file: UploadFile | None, replace: bool, m: Match) -> dict[str, Any]:
    """Wii Riivolution / Vita rePatch: fetch the mod package and lay it over the game (see handler/romshelf/modpatch.py)."""
    work = Path(tempfile.mkdtemp(prefix="romshelf_modsrc_"))
    try:
        if patch_file is not None:
            package = write_upload(await patch_file.read(), patch_file.filename or "mod.zip", work)
        else:
            package = await _fetch_first(patch_urls(hack, chosen), work)
        record = await romshelf_installs.install_mod(
            hack=hack, version=chosen, rom_file=rom_file, base_rom=base_rom, package=package, user_id=request.user.id, replace=replace
        )
    except (PatchSourceError, romshelf_installs.InstallError) as exc:
        raise _error(exc) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)
    await _scan_then_finalize(request, base_rom.platform_id, record)
    _count_download(hack["id"])
    return {**_install_json(record), "needed": needed_rom_label(hack), "match": m.level.value}


async def _scan_then_finalize(request: Request, platform_id: int, record: dict[str, Any]) -> None:
    try:
        job = await queue_scan(
            ScanPayload(type=ScanType.QUICK, platforms=[platform_id], apis=[]),
            started_by_user_id=request.user.id,
        )
        low_prio_queue.enqueue(romshelf_installs.finalize_job, record["id"], depends_on=job, job_timeout=300)
    except Exception as exc:  # A busy scanner is fine: the next scan picks the file up and the next page view finishes it.
        log.info(f"RomStop: scan not queued now ({exc}); the install finishes after the next scan")


@protected_route(router.get, "/installs/{record_id}", [Scope.ROMS_READ])
async def get_install(request: Request, record_id: Annotated[str, PathVar()]) -> dict[str, Any]:
    """An install's progress; finishes it when the scan has already created the ROM."""
    record = await romshelf_installs.finalize(record_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Install not found")
    return _install_json(record)


# ---------------------------------------------------------------- save import
# The browser reads the user's save folders and uploads through RomM's own /api/saves and /api/states;
# these routes only say which game, emulator and name each file gets. Nothing here writes or deletes.

_MAX_PLAN_FILES = 20000
_MAX_CONFIG_TEXT = 8 * 1024 * 1024


def _save_games(can_see: Any) -> list[romshelf_saves.LibraryGame]:
    with sync_session.begin() as session:
        query = (
            select(Rom)
            .where(Rom.missing_from_fs.is_(False))
            .options(selectinload(Rom.files), selectinload(Rom.platform))
        )
        games = []
        for r in session.scalars(query).unique():
            if not can_see(r) or not r.platform:
                continue
            crcs = {(f.crc_hash or "").lower() for f in r.files} | {(r.crc_hash or "").lower()}
            games.append(
                romshelf_saves.LibraryGame(
                    id=r.id,
                    name=r.name or r.fs_name_no_ext,
                    fs_name=r.fs_name,
                    fs_name_no_ext=r.fs_name_no_ext,
                    platform_id=r.platform_id,
                    platform_slug=r.platform.slug or "",
                    platform_fs_slug=r.platform.fs_slug or "",
                    platform_name=r.platform.custom_name or r.platform.name or r.platform.slug or "",
                    crc32s=frozenset(crcs - {""}),
                )
            )
        return games


async def _existing_assets(user_id: int) -> list[romshelf_saves.ExistingAsset]:
    assets = []
    rows = [("save", a) for a in await asyncio.to_thread(db_save_handler.get_saves, user_id=user_id)]
    rows += [("state", a) for a in await asyncio.to_thread(db_state_handler.get_states, user_id=user_id)]
    for kind, a in rows:
        md5 = a.content_hash or await fs_asset_handler.compute_content_hash(a.full_path)
        assets.append(romshelf_saves.ExistingAsset(kind, a.rom_id, a.emulator, a.file_name, md5))
    return assets


def _text_list(value: Any, total: list[int]) -> list[str]:
    out = []
    for item in value or []:
        text = item.get("text") if isinstance(item, dict) else item
        if isinstance(text, str):
            total[0] += len(text)
            out.append(text)
    if total[0] > _MAX_CONFIG_TEXT:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Config files are too large")
    return out


@protected_route(router.get, "/saves/options", [Scope.ROMS_READ, Scope.ASSETS_READ])
async def save_import_options(request: Request) -> dict[str, Any]:
    """Systems and games, and RomM's emulator names, for the import page's pickers."""
    games = await asyncio.to_thread(_save_games, get_permissions(request).can_see_rom)
    platforms: dict[int, dict[str, Any]] = {}
    for g in games:
        p = platforms.setdefault(g.platform_id, {"id": g.platform_id, "name": g.platform_name, "slug": g.platform_slug, "roms": 0})
        p["roms"] += 1
    return {
        "user": request.user.username,
        "platforms": sorted(platforms.values(), key=lambda p: p["name"].casefold()),
        "games": [romshelf_saves.game_json(g) for g in sorted(games, key=lambda g: g.fs_name_no_ext.casefold())],
        "emulators": sorted(set(RETROARCH_DIR_BY_ROMM_EMULATOR) | set(romshelf_saves.STANDALONE.values())),
    }


@protected_route(router.post, "/saves/plan", [Scope.ROMS_READ, Scope.ASSETS_READ])
async def plan_save_import(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """What importing these files would do: the game, emulator and name for each, checked against
    the saves and states the signed-in user already has in RomM."""
    raw_files = payload.get("files") or []
    if len(raw_files) > _MAX_PLAN_FILES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=f"Too many files (over {_MAX_PLAN_FILES})")
    try:
        files = [
            romshelf_saves.DeviceFile(path=str(f["path"]), size=int(f.get("size") or 0), md5=f.get("md5"), modified=f.get("modified"))
            for f in raw_files
        ]
        device_roms = [romshelf_saves.DeviceRom(path=str(r["path"]), crc32=r.get("crc32")) for r in payload.get("device_roms") or []]
        overrides = {
            str(path): romshelf_saves.Override(
                rom_id=o.get("rom_id"), emulator=o.get("emulator") or None, rename=o.get("rename"), skip_emulator=bool(o.get("skip_emulator"))
            )
            for path, o in (payload.get("overrides") or {}).items()
        }
        platform_emulators = {int(k): str(v) for k, v in (payload.get("platform_emulators") or {}).items() if v}
        platform_id = int(payload["platform_id"]) if payload.get("platform_id") else None
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Bad import request: {exc}") from None

    total = [0]
    gamelists = [
        (str(g.get("path", "")), g["text"]) for g in payload.get("gamelists") or [] if isinstance(g, dict) and isinstance(g.get("text"), str)
    ]
    _text_list([t for _, t in gamelists], total)
    hints = romshelf_saves.emulator_hints(
        playlists=_text_list(payload.get("playlists"), total),
        gamelists=gamelists,
        es_systems=_text_list(payload.get("es_systems"), total),
    )

    games = await asyncio.to_thread(_save_games, get_permissions(request).can_see_rom)
    existing = await _existing_assets(request.user.id)
    rows = await asyncio.to_thread(
        romshelf_saves.plan,
        files,
        romshelf_saves.Library(games),
        existing,
        device_roms,
        hints,
        platform_id=platform_id,
        detect_emulator=bool(payload.get("detect_emulator", True)),
        platform_emulators=platform_emulators,
        core_dirs=ROMM_EMULATOR_BY_RETROARCH_DIR,
        overrides=overrides,
    )
    return {"user": request.user.username, "rows": rows}
