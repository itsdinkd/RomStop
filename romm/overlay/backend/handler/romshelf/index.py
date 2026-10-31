"""The RomStop hack index: fetched from the RomStop site and cached on disk."""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx2

from config import RESOURCES_BASE_PATH
from logger.logger import log
from utils import get_version
from utils.context import ctx_httpx_client


def _env(name: str, default: str) -> str:
    """ROMSTOP_<name>, or the ROMSHELF_<name> that installs from before the rename may still set."""
    return os.environ.get(f"ROMSTOP_{name}") or os.environ.get(f"ROMSHELF_{name}") or default


ROMSHELF_SITE_URL = _env("SITE_URL", "https://romstop.pages.dev/").rstrip("/") + "/"
ROMSHELF_INDEX_URL = _env("INDEX_URL", urljoin(ROMSHELF_SITE_URL, "device/index.json"))
# How often to ask the site whether the index changed. The check is a conditional request (ETag /
# Last-Modified), so it costs a few hundred bytes unless there is something new.
ROMSHELF_INDEX_TTL = int(_env("INDEX_TTL_SECONDS", str(10 * 60)))
ROMSHELF_DIR = Path(RESOURCES_BASE_PATH) / "romshelf"
INDEX_CACHE = ROMSHELF_DIR / "index.json"
INDEX_META = ROMSHELF_DIR / "index.meta.json"

USER_AGENT = f"RomM/{get_version()} RomStop-integration (+{ROMSHELF_SITE_URL})"

_lock = asyncio.Lock()
_cache: dict[str, Any] | None = None
_fetched_at = 0.0
_last_error: str | None = None


def _absolute(url: str | None) -> str | None:
    if not url:
        return None
    return urljoin(ROMSHELF_SITE_URL, url)


def _prepare(index: dict[str, Any]) -> dict[str, Any]:
    """Resolve site-relative paths so the UI can use them as they are."""
    for hack in index.get("hacks", []):
        hack["pageUrl"] = _absolute(hack.get("page"))
        shots = [s for s in (_absolute(x) for x in hack.get("screenshots") or []) if s]
        title_screen = _absolute(hack.get("titleScreen"))
        hack["screenshots"] = [title_screen, *(s for s in shots if s != title_screen)] if title_screen else shots
        # Patches mirrored with RomStop: site-relative paths or absolute URLs (e.g. release assets).
        for p in hack.get("patches") or []:
            p["url"] = _absolute(p.get("path"))
        hack.setdefault("versions", [])
        hack.setdefault("ratings", [])
        hack.setdefault("links", [])
    index["byId"] = {h["id"]: h for h in index.get("hacks", [])}
    return index


def _read_disk() -> tuple[dict[str, Any] | None, float]:
    try:
        return json.loads(INDEX_CACHE.read_text()), INDEX_CACHE.stat().st_mtime
    except (OSError, ValueError):
        return None, 0.0


def _read_validators() -> dict[str, str]:
    try:
        meta = json.loads(INDEX_META.read_text())
        return {k: v for k, v in meta.items() if k in ("etag", "last_modified") and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


async def _download(have_copy: bool) -> dict[str, Any] | None:
    """The index from the site, or None when it hasn't changed since the copy we have."""
    if ROMSHELF_INDEX_URL.startswith("file://"):
        # A local index, for offline use and testing.
        data = json.loads(Path(ROMSHELF_INDEX_URL.removeprefix("file://")).read_text())
        validators: dict[str, str] = {}
    else:
        headers = {"User-Agent": USER_AGENT}
        old = _read_validators() if have_copy else {}
        if old.get("etag"):
            headers["If-None-Match"] = old["etag"]
        if old.get("last_modified"):
            headers["If-Modified-Since"] = old["last_modified"]
        client = ctx_httpx_client.get()
        res = await client.get(ROMSHELF_INDEX_URL, headers=headers, timeout=60, follow_redirects=True)
        if res.status_code == 304 and have_copy:
            return None
        res.raise_for_status()
        data = res.json()
        validators = {k: v for k, v in (("etag", res.headers.get("etag")), ("last_modified", res.headers.get("last-modified"))) if v}
    if not isinstance(data, dict) or not isinstance(data.get("hacks"), list):
        raise ValueError("RomStop index has no hack list")
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(INDEX_CACHE)
    INDEX_META.write_text(json.dumps(validators))
    return data


async def get_index(force: bool = False) -> dict[str, Any]:
    """The current index. Falls back to the last good copy when the site is unreachable."""
    global _cache, _fetched_at, _last_error
    async with _lock:
        if _cache is None:
            disk, mtime = _read_disk()
            if disk:
                _cache, _fetched_at = _prepare(disk), mtime
        stale = time.time() - _fetched_at > ROMSHELF_INDEX_TTL
        if force or _cache is None or stale:
            try:
                fresh = await _download(have_copy=_cache is not None and not force)
                if fresh is not None:
                    _cache = _prepare(fresh)
                    log.info(f"RomStop: loaded {len(_cache.get('hacks', []))} hacks from {ROMSHELF_INDEX_URL}")
                _fetched_at = time.time()
                _last_error = None
            except (httpx2.HTTPError, ValueError, OSError) as exc:
                _last_error = str(exc) or exc.__class__.__name__
                log.warning(f"RomStop: could not fetch {ROMSHELF_INDEX_URL}: {_last_error}")
                if _cache is None:
                    _cache = _prepare({"format": 1, "consoles": {}, "hacks": []})
        return _cache


def status() -> dict[str, Any]:
    return {
        "index_url": ROMSHELF_INDEX_URL,
        "site_url": ROMSHELF_SITE_URL,
        "fetched_at": _fetched_at or None,
        "generated": (_cache or {}).get("generated"),
        "hack_count": len((_cache or {}).get("hacks", [])),
        "error": _last_error,
    }
