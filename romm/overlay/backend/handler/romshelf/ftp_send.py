"""Send with FTP for a library game: where its files are and where they go (see ftp.py for the transfer).

Two ways in: the Send with FTP button (`send_now`, runs in the web process) and "Send with FTP after installing"
(`queue`, called when an install is finalized). The latter runs on RomM's background worker, so it does not depend
on the install page staying open until RomM's scan has picked the new file up."""

import threading
from pathlib import PurePosixPath
from typing import Any

from config.config_manager import ROMSTOP_HACKS_FOLDER
from handler.database import db_rom_handler
from handler.filesystem import fs_rom_handler
from handler.redis_handler import low_prio_queue
from logger.logger import log

from . import ftp

# A big disc image over a console's Wi-Fi can take a while.
_JOB_TIMEOUT = 6 * 3600


def prepare(rom: Any) -> tuple[dict[str, Any], list[ftp.Upload], str]:
    """The settings, the files and the device folder for sending `rom`."""
    settings = ftp.load()
    if not settings["enabled"] or not settings["host"]:
        raise ftp.FtpError("Send with FTP is off. An admin can turn it on under Settings > Send with FTP.", "not_configured")
    try:
        local = fs_rom_handler.validate_path(rom.full_path)
    except ValueError as exc:
        raise ftp.FtpError(str(exc), "bad_path") from exc
    in_hacks = PurePosixPath(rom.fs_path).name == ROMSTOP_HACKS_FOLDER
    dest = ftp.remote_dir(settings, rom.platform.fs_slug, in_hacks)
    return settings, ftp.plan(local, dest), dest


def send_now(rom: Any, user_id: int) -> dict[str, Any]:
    """Start sending `rom` in this process; a send already under way for it is returned instead."""
    running = ftp.running_for(rom.id)
    if running:
        return running
    settings, uploads, dest = prepare(rom)
    log.info(f"RomStop: sending {rom.fs_name} to {ftp.target_name(settings)} ({dest})")
    return ftp.start(settings, uploads, rom_id=rom.id, user_id=user_id, dest=dest)


def queue(rom_id: int, user_id: int) -> str | None:
    """After an install: queue the send on RomM's worker and return its job id. A send that can't start
    (Send with FTP turned off, file missing...) is recorded as failed, so the install page can say why."""
    rom = db_rom_handler.get_rom(rom_id)
    if not rom:
        return None
    running = ftp.running_for(rom_id)
    if running:
        return running["id"]
    try:
        settings, uploads, dest = prepare(rom)
    except ftp.FtpError as exc:
        log.warning(f"RomStop: not sending {rom.fs_name} with FTP: {exc}")
        return ftp.create_job(None, [], rom_id=rom_id, user_id=user_id, dest="", status="failed", error=exc)
    job_id = ftp.create_job(settings, uploads, rom_id=rom_id, user_id=user_id, dest=dest, status="queued")
    log.info(f"RomStop: queued sending {rom.fs_name} to {ftp.target_name(settings)} ({dest})")
    try:
        low_prio_queue.enqueue(run_queued, job_id, rom_id, job_timeout=_JOB_TIMEOUT)
    except Exception as exc:  # No worker queue (say Redis is down): send from this process instead.
        log.info(f"RomStop: FTP send not queued ({exc}); sending now")
        threading.Thread(target=ftp.run_job, args=(job_id, settings, uploads), daemon=True).start()
    return job_id


def run_queued(job_id: str, rom_id: int) -> None:
    """RQ entry point: send a queued job, reading the settings and the file again as they are now."""
    rom = db_rom_handler.get_rom(rom_id)
    try:
        if not rom:
            raise ftp.FtpError("The installed game is no longer in the library.", "missing_file", 404)
        settings, uploads, _dest = prepare(rom)
    except ftp.FtpError as exc:
        ftp.mark_failed(job_id, exc)
        return
    ftp.run_job(job_id, settings, uploads)
