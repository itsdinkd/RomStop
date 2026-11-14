"""Send with FTP: copy a game from the library to another device's FTP server.

Meant for consoles and handhelds that run their own FTP server, such as a jailbroken PS5's FTP payload next to
RetroArch: RomM patches the hack, then pushes the finished ROM to `<base path>/<console folder>/[Hacks/]<file>`.
Nothing here is device-specific; presets (PRESETS) only fill in the settings form.

Settings live in RomM resources `romshelf/ftp.json` (owner read/write only), set by an admin. The password is
encrypted with a key derived from ROMM_AUTH_SECRET_KEY and is never sent back to the browser. Transfers run in a
background thread; their progress is kept in `romshelf/ftp.sqlite` so any RomM worker can report it."""

import base64
import ftplib
import hashlib
import json
import os
import posixpath
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from config import ROMM_AUTH_SECRET_KEY

from .index import ROMSHELF_DIR

SETTINGS_FILE = ROMSHELF_DIR / "ftp.json"
JOBS_DB = ROMSHELF_DIR / "ftp.sqlite"

HACKS_MODES = ("match", "always", "never")
EXISTING_MODES = ("replace", "skip")

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "name": "",
    "preset": None,
    "host": "",
    "port": 21,
    "username": "",
    "tls": False,
    "passive": True,
    "base_path": "/",
    # RomM platform folder (fs_slug) -> folder on the device. A platform not listed uses its RomM folder name.
    "folders": {},
    # "match": a game in RomM's Hacks folder goes to the device's Hacks folder too; "always" / "never".
    "hacks": "match",
    "hacks_folder": "Hacks",
    "existing": "replace",
    "timeout": 30,
}

# Starting points for the settings form, not hardcoded behavior: every value can be changed after picking one.
PRESETS: dict[str, dict[str, Any]] = {
    "ps5-retroarch": {
        "label": "PS5 (RetroArch)",
        "description": (
            "A jailbroken PS5 running an FTP server payload, with RetroArch reading games from its content folder, "
            "one folder per console (content/snes, content/nes...). The base path is RetroArch's content folder, "
            "/data/homebrew/<RetroArch's title id>/content (PPSA99169 on one tested install). The etaHEN FTP server "
            "listens on port 1337; the standalone ftpsrv payload uses 2121. Test connection lists the folders it finds."
        ),
        "settings": {
            "name": "PS5",
            "port": 1337,
            "username": "",
            "tls": False,
            "passive": True,
            "base_path": "/data/homebrew/PPSA99169/content",
            "folders": {},
            "hacks": "match",
            "hacks_folder": "Hacks",
            "existing": "replace",
        },
    },
    "retroarch": {
        "label": "RetroArch device (generic)",
        "description": "Any device with an FTP server and a RetroArch content folder with one folder per console.",
        "settings": {"port": 21, "base_path": "/content", "folders": {}, "hacks": "match"},
    },
}


class FtpError(Exception):
    def __init__(self, message: str, code: str = "ftp_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


# ------------------------------------------------------------------ settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"romstop-ftp:{ROMM_AUTH_SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(password: str) -> str:
    return _fernet().encrypt(password.encode()).decode()


def _decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        # ROMM_AUTH_SECRET_KEY changed since the password was saved.
        raise FtpError("The saved FTP password can't be read any more. Enter it again in the FTP settings.", "password_unreadable") from None


def _clean_folder(value: Any, field: str) -> str:
    text = str(value or "").strip().replace("\\", "/").strip("/")
    if any(part in ("..", ".") for part in text.split("/") if part):
        raise FtpError(f"{field} can't contain '.' or '..'.", "bad_settings")
    return "/".join(p for p in text.split("/") if p)


def _validate(raw: dict[str, Any]) -> dict[str, Any]:
    s = {k: raw.get(k, v) for k, v in DEFAULTS.items()}
    s["enabled"] = bool(s["enabled"])
    s["name"] = str(s["name"] or "").strip()[:60]
    s["preset"] = s["preset"] if s["preset"] in PRESETS else None
    s["host"] = str(s["host"] or "").strip()
    if any(c in s["host"] for c in "/@ \t"):
        raise FtpError("Enter the host as a name or IP address, without ftp:// or a path.", "bad_settings")
    try:
        s["port"] = int(s["port"])
        s["timeout"] = int(s["timeout"])
    except (TypeError, ValueError):
        raise FtpError("Port and timeout must be numbers.", "bad_settings") from None
    if not 1 <= s["port"] <= 65535:
        raise FtpError("Port must be between 1 and 65535.", "bad_settings")
    s["timeout"] = min(max(s["timeout"], 5), 300)
    s["username"] = str(s["username"] or "").strip()
    s["tls"] = bool(s["tls"])
    s["passive"] = bool(s["passive"])
    s["base_path"] = "/" + _clean_folder(s["base_path"], "Base path")
    if not isinstance(s["folders"], dict):
        raise FtpError("Folders must map a platform to a folder.", "bad_settings")
    s["folders"] = {
        str(k).strip(): _clean_folder(v, f"Folder for {k}") for k, v in s["folders"].items() if str(k).strip() and str(v or "").strip()
    }
    if s["hacks"] not in HACKS_MODES:
        raise FtpError("Hacks folder must be match, always or never.", "bad_settings")
    s["hacks_folder"] = _clean_folder(s["hacks_folder"], "Hacks folder") or "Hacks"
    if s["existing"] not in EXISTING_MODES:
        raise FtpError("Existing files must be replaced or skipped.", "bad_settings")
    if s["enabled"] and not s["host"]:
        raise FtpError("Enter the FTP server's host to turn Send with FTP on.", "bad_settings")
    return s


def _read_raw() -> dict[str, Any]:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def load() -> dict[str, Any]:
    """The saved settings, with the encrypted password under `password_enc`."""
    raw = _read_raw()
    try:
        s = _validate(raw)
    except FtpError:
        s = dict(DEFAULTS)
    s["password_enc"] = raw.get("password_enc") or None
    return s


def public(s: dict[str, Any] | None = None) -> dict[str, Any]:
    """Settings for the admin form: everything but the password, which only says whether one is saved."""
    s = s or load()
    out = {k: v for k, v in s.items() if k != "password_enc"}
    out["password_set"] = bool(s.get("password_enc"))
    return out


def merged(update: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """`update` laid over the saved settings. `password` omitted or None keeps the saved one; `clear_password` removes it."""
    current = current or load()
    s = _validate({**{k: v for k, v in current.items() if k != "password_enc"}, **{k: v for k, v in update.items() if k in DEFAULTS}})
    if update.get("clear_password"):
        s["password_enc"] = None
    elif isinstance(update.get("password"), str) and update["password"] != "":
        s["password_enc"] = _encrypt(update["password"])
    else:
        s["password_enc"] = current.get("password_enc")
    return s


def save(update: dict[str, Any]) -> dict[str, Any]:
    s = merged(update)
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(s, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SETTINGS_FILE)
    return public(s)


def target_name(s: dict[str, Any]) -> str:
    return s.get("name") or (PRESETS.get(s.get("preset") or "", {}).get("label")) or s.get("host") or "FTP"


def status() -> dict[str, Any]:
    """What a user who may send needs to know: whether sending is on and what to call the target."""
    s = load()
    ready = bool(s["enabled"] and s["host"])
    return {"enabled": ready, "name": target_name(s) if ready else None}


# ------------------------------------------------------------------ paths


def remote_dir(s: dict[str, Any], platform_fs_slug: str, in_hacks_folder: bool) -> str:
    """The device folder a game of this platform goes to."""
    folder = s["folders"].get(platform_fs_slug) or _clean_folder(platform_fs_slug, "Platform folder")
    if not folder:
        raise FtpError("This game has no platform folder.", "bad_platform")
    parts = [s["base_path"], folder]
    if s["hacks"] == "always" or (s["hacks"] == "match" and in_hacks_folder):
        parts.append(s["hacks_folder"])
    return posixpath.normpath(posixpath.join(*parts))


@dataclass(frozen=True)
class Upload:
    local: Path
    remote: str
    size: int


def plan(local_path: Path, dest_dir: str) -> list[Upload]:
    """A file goes into `dest_dir`; a folder game (multi-disc, a Wii mod folder...) keeps its layout under `dest_dir/<folder>`."""
    if local_path.is_file():
        return [Upload(local_path, posixpath.join(dest_dir, local_path.name), local_path.stat().st_size)]
    if not local_path.is_dir():
        raise FtpError(f"{local_path.name} is missing from disk.", "missing_file", 404)
    out = []
    for path in sorted(local_path.rglob("*")):
        if path.is_file() and not path.is_symlink():
            rel = path.relative_to(local_path).as_posix()
            out.append(Upload(path, posixpath.join(dest_dir, local_path.name, rel), path.stat().st_size))
    if not out:
        raise FtpError(f"{local_path.name} is an empty folder.", "missing_file", 404)
    return out


# ------------------------------------------------------------------ FTP


def connect(s: dict[str, Any], factory: Callable[..., ftplib.FTP] | None = None) -> ftplib.FTP:
    if not s.get("host"):
        raise FtpError("Send with FTP isn't set up yet. An admin can set it up under Settings > Send with FTP.", "not_configured")
    cls = factory or (ftplib.FTP_TLS if s["tls"] else ftplib.FTP)
    ftp = cls()
    try:
        ftp.connect(s["host"], s["port"], timeout=s["timeout"])
        ftp.login(s["username"] or "anonymous", _decrypt(s.get("password_enc")))
        if s["tls"] and hasattr(ftp, "prot_p"):
            ftp.prot_p()
        ftp.set_pasv(s["passive"])
    except ftplib.error_perm as exc:
        _quit(ftp)
        raise FtpError(f"The FTP server refused the sign-in: {exc}", "login_failed") from exc
    except (OSError, EOFError, ftplib.Error) as exc:
        _quit(ftp)
        raise FtpError(f"Couldn't reach {s['host']}:{s['port']}: {exc}. Is the FTP server running on the device?", "unreachable") from exc
    return ftp


def _quit(ftp: ftplib.FTP) -> None:
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


def make_dirs(ftp: ftplib.FTP, path: str) -> None:
    """mkdir -p on the server. Folders that exist already answer with an error, which is fine."""
    current = ""
    for part in [p for p in path.split("/") if p]:
        current = f"{current}/{part}"
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass


def remote_size(ftp: ftplib.FTP, path: str) -> int | None:
    try:
        ftp.voidcmd("TYPE I")
        size = ftp.size(path)
        return int(size) if size is not None else None
    except (ftplib.error_perm, ValueError):
        return None


def list_folders(ftp: ftplib.FTP, path: str) -> list[str]:
    """Folder names under `path`, best effort (MLSD where the server has it, else NLST)."""
    try:
        return sorted(name for name, facts in ftp.mlsd(path, facts=["type"]) if facts.get("type") == "dir")
    except (ftplib.error_perm, ftplib.error_reply, ftplib.error_proto):
        pass
    try:
        names = ftp.nlst(path)
    except ftplib.error_perm:
        return []
    return sorted({posixpath.basename(n.rstrip("/")) for n in names} - {"", ".", ".."})


def test(s: dict[str, Any], factory: Callable[..., ftplib.FTP] | None = None) -> dict[str, Any]:
    """Sign in, check the base path and list its folders, so the admin can match platforms to them."""
    ftp = connect(s, factory)
    try:
        try:
            ftp.cwd(s["base_path"])
        except ftplib.error_perm as exc:
            raise FtpError(f"Signed in, but {s['base_path']} isn't a folder on the device: {exc}", "bad_base_path") from exc
        return {"ok": True, "welcome": (ftp.getwelcome() or "")[:200], "folders": list_folders(ftp, s["base_path"])}
    finally:
        _quit(ftp)


def send(
    s: dict[str, Any],
    uploads: list[Upload],
    progress: Callable[[int, str], None] | None = None,
    factory: Callable[..., ftplib.FTP] | None = None,
) -> dict[str, int]:
    """Upload every file. Returns how many were sent and skipped."""
    ftp = connect(s, factory)
    done = 0
    sent = skipped = 0
    try:
        for up in uploads:
            make_dirs(ftp, posixpath.dirname(up.remote))
            if s["existing"] == "skip" and remote_size(ftp, up.remote) is not None:
                skipped += 1
                done += up.size
                if progress:
                    progress(done, up.remote)
                continue

            def chunk(block: bytes, _remote: str = up.remote) -> None:
                nonlocal done
                done += len(block)
                if progress:
                    progress(done, _remote)

            try:
                with up.local.open("rb") as f:
                    ftp.storbinary(f"STOR {up.remote}", f, blocksize=256 * 1024, callback=chunk)
            except ftplib.error_perm as exc:
                raise FtpError(f"The device refused {up.remote}: {exc}", "refused") from exc
            sent += 1
    except (OSError, EOFError, ftplib.error_temp, ftplib.error_reply, ftplib.error_proto) as exc:
        raise FtpError(f"The transfer stopped: {exc}", "transfer_failed") from exc
    finally:
        _quit(ftp)
    return {"sent": sent, "skipped": skipped}


# ------------------------------------------------------------------ jobs


def _jobs() -> sqlite3.Connection:
    ROMSHELF_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(JOBS_DB, timeout=10)
    con.execute(
        "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, rom_id INTEGER, user_id INTEGER, status TEXT, total INTEGER, done INTEGER,"
        " current TEXT, message TEXT, code TEXT, sent INTEGER, skipped INTEGER, target TEXT, dest TEXT, started REAL, updated REAL)"
    )
    return con


_JOB_FIELDS = ("id", "rom_id", "user_id", "status", "total", "done", "current", "message", "code", "sent", "skipped", "target", "dest", "started", "updated")
_STALE_SECONDS = 600
# A send queued after an install waits behind RomM's other background jobs (scans) on the worker.
_QUEUED_SECONDS = 2 * 3600


def job(job_id: str) -> dict[str, Any] | None:
    with closing(_jobs()) as con:
        row = con.execute(f"SELECT {', '.join(_JOB_FIELDS)} FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    out = dict(zip(_JOB_FIELDS, row))
    # A job whose worker died (RomM restarted mid-transfer) would otherwise read as running forever.
    idle = time.time() - (out["updated"] or 0)
    if (out["status"] == "running" and idle > _STALE_SECONDS) or (out["status"] == "queued" and idle > _QUEUED_SECONDS):
        out["status"], out["code"], out["message"] = "failed", "stalled", "The transfer stopped answering. Try sending again."
    return out


def running_for(rom_id: int) -> dict[str, Any] | None:
    """A send of this game that is queued or under way, so a second click doesn't start another."""
    with closing(_jobs()) as con:
        row = con.execute(
            "SELECT id FROM jobs WHERE rom_id = ? AND status IN ('running', 'queued') ORDER BY started DESC LIMIT 1", (rom_id,)
        ).fetchone()
    found = job(row[0]) if row else None
    return found if found and found["status"] in ("running", "queued") else None


def _update(job_id: str, **fields: Any) -> None:
    fields["updated"] = time.time()
    with closing(_jobs()) as con, con:
        con.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), job_id))


def mark_failed(job_id: str, exc: FtpError) -> None:
    _update(job_id, status="failed", message=str(exc), code=exc.code)


def create_job(
    s: dict[str, Any] | None,
    uploads: list[Upload],
    *,
    rom_id: int,
    user_id: int,
    dest: str,
    status: str = "running",
    error: FtpError | None = None,
) -> str:
    """Record a send. `queued` waits for RomM's worker; `failed` (with `error`) reports a send that couldn't start."""
    job_id = uuid.uuid4().hex
    now = time.time()
    with closing(_jobs()) as con, con:
        con.execute(
            "INSERT INTO jobs (id, rom_id, user_id, status, total, done, current, message, code, sent, skipped, target, dest, started, updated)"
            " VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, 0, 0, ?, ?, ?, ?)",
            (
                job_id,
                rom_id,
                user_id,
                status,
                sum(u.size for u in uploads),
                str(error) if error else None,
                error.code if error else None,
                target_name(s) if s else "FTP",
                dest,
                now,
                now,
            ),
        )
    return job_id


def run_job(job_id: str, s: dict[str, Any], uploads: list[Upload], factory: Callable[..., ftplib.FTP] | None = None) -> dict[str, Any]:
    """Do the transfer for a recorded job, keeping its progress up to date. Never raises."""
    total = sum(u.size for u in uploads)
    _update(job_id, status="running", total=total)
    last = [0.0]

    def progress(done: int, current: str) -> None:
        if time.monotonic() - last[0] >= 0.5:
            last[0] = time.monotonic()
            _update(job_id, done=done, current=current)

    try:
        result = send(s, uploads, progress, factory)
        _update(job_id, status="done", done=total, sent=result["sent"], skipped=result["skipped"], current=None)
    except FtpError as exc:
        _update(job_id, status="failed", message=str(exc), code=exc.code)
    except Exception as exc:  # Anything else still has to end the job, or the page waits forever.
        _update(job_id, status="failed", message=f"Sending failed: {exc}", code="error")
    return job(job_id) or {}


def start(
    s: dict[str, Any],
    uploads: list[Upload],
    *,
    rom_id: int,
    user_id: int,
    dest: str,
    factory: Callable[..., ftplib.FTP] | None = None,
    background: bool = True,
) -> dict[str, Any]:
    """Record a job and run the transfer in a thread. Poll `job(id)` for progress."""
    job_id = create_job(s, uploads, rom_id=rom_id, user_id=user_id, dest=dest)
    if background:
        threading.Thread(target=run_job, args=(job_id, s, uploads, factory), name=f"romstop-ftp-{job_id[:8]}", daemon=True).start()
        return job(job_id) or {}
    return run_job(job_id, s, uploads, factory)


def iter_presets() -> Iterator[dict[str, Any]]:
    for key, p in PRESETS.items():
        yield {"id": key, "label": p["label"], "description": p["description"], "settings": p["settings"]}

