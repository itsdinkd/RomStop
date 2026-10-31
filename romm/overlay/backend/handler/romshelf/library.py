"""The library as the matcher sees it: ROMs on RomStop's consoles with their file hashes."""

import fcntl
import hashlib
import json
import threading
import zlib
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import LIBRARY_BASE_PATH
from handler.database.base_handler import sync_session
from logger.logger import log
from models.platform import Platform
from models.rom import Rom, RomFileCategory

from .archives import ArchiveError, is_archive, read_rom
from .index import ROMSHELF_DIR
from .matching import PLATFORM_CONSOLE, LibraryFile, LibraryRom, header_size

HEADERLESS_CACHE = ROMSHELF_DIR / "headerless.json"
_HASH_LOCK_FILE = ROMSHELF_DIR / "headerless.lock"
# Only cartridge consoles whose dumps may carry a copier or iNES header, and only files small enough to read whole.
_HEADER_CONSOLES = {"nes", "snes", "pce"}
_MAX_HASH_BYTES = 64 * 1024 * 1024
# Reading ROMs is mostly waiting on the disk (often a network or Windows share), so several run at once.
_HASH_WORKERS = 6
_SAVE_EVERY = 200

_cache_lock = threading.Lock()
_hashing: dict[str, Any] = {"thread": None, "pending": 0}

# (cache key, file path, console) of a file whose headerless hashes aren't known yet.
Pending = tuple[str, Path, str]


def _load_cache() -> dict[str, list[str | None]]:
    try:
        return json.loads(HEADERLESS_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(updates: dict[str, list[str | None]]) -> None:
    """Add `updates` to the cache on disk, keeping what other requests saved meanwhile."""
    if not updates:
        return
    try:
        ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
        with _cache_lock:
            cache = _load_cache()
            cache.update(updates)
            tmp = HEADERLESS_CACHE.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(cache))
            tmp.replace(HEADERLESS_CACHE)
    except OSError as exc:
        log.warning(f"RomStop: could not save header hash cache: {exc}")


def headerless_hashes(path: Path, console: str | None) -> tuple[str | None, str | None]:
    """CRC32 and SHA-1 of the file without its header, or (None, None) when it has none."""
    try:
        if is_archive(path.name):
            # The game file inside, the same one RomM hashes.
            data = read_rom(path, max_bytes=_MAX_HASH_BYTES)
        else:
            if path.stat().st_size > _MAX_HASH_BYTES:
                return None, None
            data = path.read_bytes()
        size = len(data)
    except (OSError, ArchiveError):
        return None, None
    skip = header_size(console, data[:16], size)
    if not skip:
        return None, None
    body = data[skip:]
    return f"{zlib.crc32(body) & 0xFFFFFFFF:08x}", hashlib.sha1(body).hexdigest()


def _hash_files(pending: list[Pending]) -> Iterator[tuple[str, list[str | None]]]:
    with ThreadPoolExecutor(_HASH_WORKERS, thread_name_prefix="romshelf-hash") as pool:
        for (key, _, _), hashes in zip(pending, pool.map(lambda p: headerless_hashes(p[1], p[2]), pending)):
            yield key, list(hashes)


def _hash_all(pending: list[Pending]) -> None:
    """Fill in the cache for `pending`, saving as it goes so a restart keeps the work done."""
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    with _HASH_LOCK_FILE.open("a+") as lock:
        try:
            # Another RomM worker process is already at it.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _hashing["pending"] = 0
            return
        done: dict[str, list[str | None]] = {}
        try:
            for key, hashes in _hash_files(pending):
                done[key] = hashes
                _hashing["pending"] = len(pending) - len(done)
                if len(done) % _SAVE_EVERY == 0:
                    _save_cache(done)
            log.info(f"RomStop: checked {len(done)} ROMs for copier and iNES headers")
        except Exception as exc:  # A bad file must not stop the rest from being matched next time.
            log.warning(f"RomStop: header check stopped: {exc}")
        finally:
            _save_cache(done)
            _hashing["pending"] = 0
            fcntl.flock(lock, fcntl.LOCK_UN)


def hash_in_background(pending: list[Pending]) -> None:
    """Start checking `pending` for headers, unless a check is already running."""
    t = _hashing["thread"]
    if not pending or (t and t.is_alive()):
        return
    _hashing["pending"] = len(pending)
    t = threading.Thread(target=_hash_all, args=(pending,), name="romshelf-headers", daemon=True)
    _hashing["thread"] = t
    t.start()


def hashing_pending() -> int:
    """How many ROMs are still being checked for headers; their header-based matches show up after."""
    t = _hashing["thread"]
    return _hashing["pending"] if t and t.is_alive() else 0


def _rom_files(rom: Rom, console: str | None, cache: dict[str, list[str | None]], pending: list[Pending]) -> tuple[LibraryFile, ...]:
    """The ROM's game files. A file whose headerless hashes aren't cached yet goes without them and is
    added to `pending`."""
    files = []
    for f in rom.files:
        if f.category not in (None, RomFileCategory.GAME):
            continue
        crc_h = sha_h = None
        if console in _HEADER_CONSOLES:
            key = f"{f.id}:{f.file_size_bytes}:{f.last_modified}"
            path = Path(LIBRARY_BASE_PATH, f.full_path)
            if key in cache:
                crc_h, sha_h = cache[key]
            else:
                pending.append((key, path, console))
        files.append(
            LibraryFile(
                id=f.id,
                file_name=f.file_name,
                size=f.file_size_bytes,
                crc32=(f.crc_hash or "").lower() or None,
                sha1=(f.sha1_hash or "").lower() or None,
                headerless_crc32=crc_h,
                headerless_sha1=sha_h,
            )
        )
    return tuple(files)


def to_library_rom(rom: Rom, cache: dict[str, list[str | None]], pending: list[Pending]) -> LibraryRom:
    slug = rom.platform.slug
    return LibraryRom(
        id=rom.id,
        platform_id=rom.platform_id,
        platform_slug=slug,
        name=rom.name,
        fs_name=rom.fs_name,
        fs_name_no_tags=rom.fs_name_no_tags,
        regions=tuple(rom.regions or ()),
        revision=rom.revision,
        alternative_names=tuple(rom.alternative_names or ()),
        files=_rom_files(rom, PLATFORM_CONSOLE.get(slug), cache, pending),
    )


def _to_library_roms(rows: list[Rom], wait_for_hashes: bool) -> list[LibraryRom]:
    cache = _load_cache()
    pending: list[Pending] = []
    roms = [to_library_rom(r, cache, pending) for r in rows]
    if pending and wait_for_hashes:
        fresh = dict(_hash_files(pending))
        _save_cache(fresh)
        cache.update(fresh)
        roms = [to_library_rom(r, cache, []) for r in rows]
    elif pending:
        hash_in_background(pending)
    return roms


def library_roms(can_see: Callable[[Any], bool] | None = None, wait_for_hashes: bool = True) -> list[LibraryRom]:
    """Every ROM the caller can see on a platform RomStop covers.

    Header checks read whole files, which takes minutes for a big library the first time. With
    `wait_for_hashes` off, files not checked yet are checked in the background and match by their
    plain checksums until then."""
    with sync_session.begin() as session:
        query = (
            select(Rom)
            .join(Platform, Rom.platform_id == Platform.id)
            .where(Platform.slug.in_(list(PLATFORM_CONSOLE)))
            .where(Rom.missing_from_fs.is_(False))
            .options(selectinload(Rom.files), selectinload(Rom.platform))
        )
        rows = [r for r in session.scalars(query).unique() if can_see is None or can_see(r)]
        return _to_library_roms(rows, wait_for_hashes)


def library_rom(rom_id: int) -> LibraryRom | None:
    with sync_session.begin() as session:
        rom = session.scalar(
            select(Rom).where(Rom.id == rom_id).options(selectinload(Rom.files), selectinload(Rom.platform))
        )
        return _to_library_roms([rom], wait_for_hashes=True)[0] if rom else None
