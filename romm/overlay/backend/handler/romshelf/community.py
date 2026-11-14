"""RomStop's own ratings, favorites and download counts, read from the RomStop site and folded into the index.

The site keeps them in `/api/community` (a Cloudflare Pages Function), not in the device index, because they change
all the time. This module:

- fetches `GET /api/community` every few minutes and adds RomStop's numbers to each hack the same way the site does:
  a "RomStop" row joins `ratings` and the headline `rating` is re-averaged by count, RomStop favs add to `favs`
  (Total Favs), and site downloads plus RomM installs add to `downloads`;
- reports each install done here with `POST /api/community {hack, install: true, client}`, so the site's download
  counter includes RomM. `client` is a random id this RomM keeps in `romshelf/client_id`; the site counts one
  install per RomM per hack. Set ROMSTOP_SHARE_INSTALLS=0 to turn reporting off.

Failures never get in the way: without the site the index keeps its scraped numbers."""

import asyncio
import json
import os
import time
import uuid
from typing import Any
from urllib.parse import urljoin

from .index import ROMSHELF_DIR, ROMSHELF_SITE_URL, USER_AGENT

ROMSTOP_SOURCE = "RomStop"
COMMUNITY_URL = os.environ.get("ROMSTOP_COMMUNITY_URL") or urljoin(ROMSHELF_SITE_URL, "api/community")
COMMUNITY_TTL = int(os.environ.get("ROMSTOP_COMMUNITY_TTL_SECONDS") or 10 * 60)
CLIENT_ID_FILE = ROMSHELF_DIR / "client_id"
# Fields the merge changes; the scraped values are kept under this key so it can run again with new stats.
_BASE = "_scraped"


def share_installs() -> bool:
    return (os.environ.get("ROMSTOP_SHARE_INSTALLS") or "1").strip().lower() not in ("0", "false", "no", "off")


def parse_stats(data: Any) -> dict[str, tuple[float, int, int, int]]:
    """`{hackId: [ratingSum, ratingCount, favs, downloads, rommInstalls]}` from the site, as
    (ratingSum, ratingCount, favs, downloads incl. RomM installs). Bad entries are skipped."""
    out: dict[str, tuple[float, int, int, int]] = {}
    stats = data.get("stats") if isinstance(data, dict) and data.get("enabled") else None
    if not isinstance(stats, dict):
        return out
    for hack_id, row in stats.items():
        if not isinstance(hack_id, str) or not isinstance(row, list) or len(row) < 3:
            continue
        nums = [x if isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 else 0 for x in row[:5]]
        nums += [0] * (5 - len(nums))
        out[hack_id] = (float(nums[0]), int(nums[1]), int(nums[2]), int(nums[3]) + int(nums[4]))
    return out


def apply_stats(hacks: list[dict[str, Any]], stats: dict[str, tuple[float, int, int, int]]) -> None:
    """Adds RomStop's numbers to each hack, in place. Safe to call again: it always starts from the scraped values."""
    for hack in hacks:
        base = hack.get(_BASE)
        if base is None:
            base = hack[_BASE] = {k: hack.get(k) for k in ("ratings", "rating", "favs", "downloads")}
        ratings = list(base["ratings"] or [])
        rating, favs, downloads = base["rating"], base["favs"], base["downloads"]
        s = stats.get(hack.get("id", ""))
        if s:
            rating_sum, rating_count, site_favs, site_downloads = s
            if rating_count > 0:
                score = rating_sum / rating_count
                ratings.append({"source": ROMSTOP_SOURCE, "score": score, "display": f"{score:.1f} / 5", "count": rating_count})
                # Count-weighted, like the site's aggregate(): a scraped score with no count carries no weight.
                old_count = int((rating or {}).get("count") or 0)
                old_score = float((rating or {}).get("score") or 0)
                total = old_count + rating_count
                rating = {"score": round((old_score * old_count + rating_sum) / total, 2), "count": total}
            favs = (favs or 0) + site_favs
            downloads = (downloads or 0) + site_downloads
        hack["ratings"], hack["rating"], hack["favs"], hack["downloads"] = ratings, rating, favs, downloads


def public(hack: dict[str, Any]) -> dict[str, Any]:
    """The hack without this module's bookkeeping."""
    return {k: v for k, v in hack.items() if k != _BASE}


_lock = asyncio.Lock()
_stats: dict[str, tuple[float, int, int, int]] = {}
_fetched_at = 0.0
_tasks: set[asyncio.Task[Any]] = set()


async def _fetch() -> dict[str, tuple[float, int, int, int]] | None:
    from logger.logger import log
    from utils.context import ctx_httpx_client

    try:
        res = await ctx_httpx_client.get().get(COMMUNITY_URL, headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)
        res.raise_for_status()
        return parse_stats(res.json())
    except Exception as exc:  # The site being down must never break RomM's pages.
        log.warning(f"RomStop: could not load ratings from {COMMUNITY_URL}: {exc}")
        return None


def invalidate() -> None:
    """Fetch the stats again on the next request (the Refresh button)."""
    global _fetched_at
    _fetched_at = 0.0


async def apply(index: dict[str, Any]) -> dict[str, Any]:
    """The index with fresh RomStop stats folded in (fetched at most every COMMUNITY_TTL seconds)."""
    global _stats, _fetched_at
    async with _lock:
        if time.time() - _fetched_at > COMMUNITY_TTL:
            fresh = await _fetch()
            _fetched_at = time.time()
            if fresh is not None:
                _stats = fresh
        # The index remembers which stats it holds, so a newly fetched index gets them too.
        if index.get("_community_at") != _fetched_at:
            apply_stats(index.get("hacks", []), _stats)
            index["_community_at"] = _fetched_at
    return index


def client_id() -> str:
    """This RomM's random id for the site's install counter, made on first use."""
    try:
        got = CLIENT_ID_FILE.read_text().strip()
        uuid.UUID(got)
        return got
    except (OSError, ValueError):
        pass
    new = str(uuid.uuid4())
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_ID_FILE.write_text(new)
    return new


async def _report(hack_id: str) -> None:
    from logger.logger import log
    from utils.context import ctx_httpx_client

    try:
        body = json.dumps({"hack": hack_id, "install": True, "client": client_id()})
        res = await ctx_httpx_client.get().post(
            COMMUNITY_URL,
            content=body,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            timeout=20,
        )
        if res.status_code >= 400:
            log.warning(f"RomStop: the site did not count the install of {hack_id}: HTTP {res.status_code}")
    except Exception as exc:  # A count is never worth failing an install over.
        log.warning(f"RomStop: could not report the install of {hack_id}: {exc}")


def report_install(hack_id: str) -> None:
    """Tells the site about an install, in the background."""
    if not share_installs():
        return
    task = asyncio.get_running_loop().create_task(_report(hack_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
