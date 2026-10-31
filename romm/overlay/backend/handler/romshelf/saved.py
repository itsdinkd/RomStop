"""Saved hacks ("Save Rom Hack"): a per-user bookmark list on the server.

Mostly for hacks whose game isn't in the library yet, so the user remembers to add the ROM and install them later.
Kept in a small SQLite file next to the index cache (`saved.sqlite`), keyed by RomM user id, so the list follows
the user to every device. A hack the index later drops stays saved and simply isn't listed."""

import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from .index import ROMSHELF_DIR

SAVED_DB = ROMSHELF_DIR / "saved.sqlite"


def _connect() -> sqlite3.Connection:
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(SAVED_DB, timeout=10)
    con.execute(
        "CREATE TABLE IF NOT EXISTS saved ("
        " user_id INTEGER NOT NULL, hack_id TEXT NOT NULL, saved_at TEXT NOT NULL,"
        " PRIMARY KEY (user_id, hack_id))"
    )
    return con


def saved_ids(user_id: int) -> list[str]:
    """The user's saved hack ids, newest first."""
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT hack_id FROM saved WHERE user_id = ? ORDER BY saved_at DESC, hack_id", (user_id,)
        ).fetchall()
    return [r[0] for r in rows]


def set_saved(user_id: int, hack_id: str, saved: bool) -> None:
    """Save or unsave one hack. Saving twice keeps the first date."""
    with closing(_connect()) as con, con:
        if saved:
            con.execute(
                "INSERT OR IGNORE INTO saved (user_id, hack_id, saved_at) VALUES (?, ?, ?)",
                (user_id, hack_id, datetime.now(timezone.utc).isoformat()),
            )
        else:
            con.execute("DELETE FROM saved WHERE user_id = ? AND hack_id = ?", (user_id, hack_id))
