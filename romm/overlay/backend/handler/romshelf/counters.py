"""RomStop download and play counts for this RomM, kept on the server.

Downloads: every install of a hack adds one, in a small SQLite file next to the index cache (`counts.sqlite`).
Plays: RomM already logs play sessions; a hack's plays are the sessions of the ROMs installed from it.
Both cover this RomM only, all users together."""

import sqlite3
from contextlib import closing
from typing import Any

from sqlalchemy import func, select

from handler.database.base_handler import sync_session
from models.play_session import PlaySession

from .index import ROMSHELF_DIR

COUNTS_DB = ROMSHELF_DIR / "counts.sqlite"


def _connect() -> sqlite3.Connection:
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(COUNTS_DB, timeout=10)
    con.execute(
        "CREATE TABLE IF NOT EXISTS downloads (hack_id TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0)"
    )
    return con


def add_download(hack_id: str) -> None:
    with closing(_connect()) as con, con:
        con.execute(
            "INSERT INTO downloads (hack_id, n) VALUES (?, 1) ON CONFLICT (hack_id) DO UPDATE SET n = n + 1",
            (hack_id,),
        )


def downloads(hack_id: str) -> int:
    with closing(_connect()) as con:
        row = con.execute("SELECT n FROM downloads WHERE hack_id = ?", (hack_id,)).fetchone()
    return int(row[0]) if row else 0


def plays(rom_ids: list[int]) -> int:
    """Play sessions RomM recorded for these ROMs."""
    if not rom_ids:
        return 0
    with sync_session.begin() as session:
        return int(session.scalar(select(func.count()).select_from(PlaySession).where(PlaySession.rom_id.in_(rom_ids))) or 0)


def counts(hack_id: str, install_records: list[dict[str, Any]]) -> dict[str, int]:
    rom_ids = sorted({r["rom_id"] for r in install_records if r.get("hack_id") == hack_id and r.get("rom_id")})
    return {"downloads": downloads(hack_id), "plays": plays(rom_ids)}
