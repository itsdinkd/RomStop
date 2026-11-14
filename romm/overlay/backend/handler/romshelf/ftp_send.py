"""Send with FTP for a library file: where its files are and where they go (see ftp.py for the transfer).

Sending works on the file itself, so it doesn't wait for RomM's scan: "Install + Send with FTP" starts the send as
soon as the patched file is written (`send_install`), and the Send with FTP button works both for a library game
(`send_rom`) and for an install the scan hasn't picked up yet."""

from pathlib import PurePosixPath
from typing import Any

from config.config_manager import ROMSTOP_HACKS_FOLDER
from handler.filesystem import fs_rom_handler
from logger.logger import log

from . import ftp


def _send(fs_path: str, fs_name: str, platform_fs_slug: str, *, rom_id: int | None, user_id: int) -> dict[str, Any]:
    """Start sending `<library>/<fs_path>/<fs_name>` (a file, or a folder game) in the background."""
    settings = ftp.load()
    if not settings["enabled"] or not settings["host"]:
        raise ftp.FtpError("Send with FTP is off. An admin can turn it on under Settings > Send with FTP.", "not_configured")
    try:
        local = fs_rom_handler.validate_path(f"{fs_path}/{fs_name}")
    except ValueError as exc:
        raise ftp.FtpError(str(exc), "bad_path") from exc
    in_hacks = PurePosixPath(fs_path).name == ROMSTOP_HACKS_FOLDER
    dest = ftp.remote_dir(settings, platform_fs_slug, in_hacks)
    uploads = ftp.plan(local, dest)
    log.info(f"RomStop: sending {fs_name} to {ftp.target_name(settings)} ({dest})")
    return ftp.start(settings, uploads, rom_id=rom_id, user_id=user_id, dest=dest, source=str(local))


def send_rom(rom: Any, user_id: int) -> dict[str, Any]:
    """A game in the library."""
    return _send(rom.fs_path, rom.fs_name, rom.platform.fs_slug, rom_id=rom.id, user_id=user_id)


def send_install(record: dict[str, Any], user_id: int) -> dict[str, Any]:
    """An installed hack, straight from its install record: works before the scan has made it a library game."""
    return _send(record["fs_path"], record["fs_name"], record["platform_fs_slug"], rom_id=record.get("rom_id"), user_id=user_id)


def send_install_or_record_failure(record: dict[str, Any], user_id: int) -> str:
    """For Install + Send with FTP: start the send and return its job id. A send that can't start is recorded as a
    failed job with its reason, so the install still succeeds and the page can say why the send didn't."""
    try:
        return send_install(record, user_id)["id"]
    except ftp.FtpError as exc:
        log.warning(f"RomStop: not sending {record['fs_name']} with FTP: {exc}")
        return ftp.create_job(None, [], rom_id=record.get("rom_id"), user_id=user_id, dest="", status="failed", error=exc)
