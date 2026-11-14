"""Send with FTP for a library file, or a save or save state: where it is and where it goes (ftp.py does the transfer).

Sending works on the file itself, so it doesn't wait for RomM's scan: "Install + Send with FTP" starts the send as
soon as the patched file is written (`send_install`), and the Send with FTP button works both for a library game
(`send_rom`) and for an install the scan hasn't picked up yet."""

import posixpath
from pathlib import PurePosixPath
from typing import Any

from config.config_manager import ROMSTOP_HACKS_FOLDER
from handler.filesystem import fs_asset_handler, fs_rom_handler
from handler.sync.retroarch.emulator_names import RETROARCH_DIR_BY_ROMM_EMULATOR
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


# ---------------------------------------------------------------- saves and save states
# A save goes to RetroArch's saves (or states) folder from the FTP settings, or to a folder the user types in, which
# is remembered for them. It is named after the game's file, the way RetroArch looks for it. A save already on the
# device is never overwritten: it is renamed to "<name>.bak-<time>" first, and only when the user says to replace it.


def _save_target(s: dict[str, Any], kind: str, asset: Any, rom: Any) -> dict[str, Any]:
    in_hacks = PurePosixPath(rom.fs_path).name == ROMSTOP_HACKS_FOLDER
    content_dir = PurePosixPath(ftp.remote_dir(s, rom.platform.fs_slug, in_hacks)).name
    core_dir = RETROARCH_DIR_BY_ROMM_EMULATOR.get(asset.emulator or "") or (asset.emulator or None)
    return {
        "settings_dir": ftp.save_dir(s, kind, core_dir, content_dir),
        "file_name": ftp.device_save_name(asset.file_name, rom.fs_name_no_ext or ""),
        "core_dir": core_dir,
        "content_dir": content_dir,
    }


def save_plan(kind: str, asset: Any, rom: Any, user_id: int, folder: str | None = None, file_name: str | None = None) -> dict[str, Any]:
    """What sending this save would do: the folder from the settings, the file name on the device, and whether a
    file of that name is already there (checked over FTP; `check_error` says why it couldn't be)."""
    s = ftp.load()
    if not s["enabled"] or not s["host"]:
        raise ftp.FtpError("Send with FTP is off. An admin can turn it on under Settings > Send with FTP.", "not_configured")
    target = _save_target(s, kind, asset, rom)
    dest_dir = ftp.clean_remote_dir(folder) if folder else target["settings_dir"]
    name = ftp.clean_file_name(file_name) if file_name else target["file_name"]
    out = {
        **target,
        "kind": kind,
        "target": ftp.target_name(s),
        "console": rom.platform.custom_name or rom.platform.name or rom.platform.fs_slug,
        "game": rom.name or rom.fs_name_no_ext,
        "emulator": asset.emulator,
        "dir": dest_dir,
        "path": posixpath.join(dest_dir, name) if dest_dir else None,
        "exists": None,
        "check_error": None,
        "recent_paths": ftp.recent_paths(user_id),
    }
    if out["path"]:
        try:
            out["exists"] = ftp.remote_exists(s, out["path"])
        except ftp.FtpError as exc:
            out["check_error"] = str(exc)
    return out


def send_save(kind: str, asset: Any, rom: Any, user_id: int, folder: str | None, file_name: str | None, replace: bool) -> dict[str, Any]:
    s = ftp.load()
    if not s["enabled"] or not s["host"]:
        raise ftp.FtpError("Send with FTP is off. An admin can turn it on under Settings > Send with FTP.", "not_configured")
    target = _save_target(s, kind, asset, rom)
    dest_dir = ftp.clean_remote_dir(folder) if folder else target["settings_dir"]
    if not dest_dir:
        what = "saves" if kind == "save" else "save states"
        raise ftp.FtpError(f"No {what} folder is set in Settings > Send with FTP. Pick a folder for this one.", "no_save_folder")
    name = ftp.clean_file_name(file_name) if file_name else target["file_name"]
    try:
        local = fs_asset_handler.validate_path(asset.full_path)
    except ValueError as exc:
        raise ftp.FtpError(str(exc), "bad_path") from exc
    if not local.is_file():
        raise ftp.FtpError(f"{asset.file_name} is missing from disk.", "missing_file", 404)
    path = posixpath.join(dest_dir, name)
    if not replace and ftp.remote_exists(s, path):
        raise ftp.FtpError(f"{name} is already on {ftp.target_name(s)}. Choose to replace it (the old one is kept as a .bak copy).", "exists", 409)
    if folder:
        ftp.remember_path(user_id, dest_dir)
    log.info(f"RomStop: sending {kind} {asset.file_name} to {ftp.target_name(s)} as {path}")
    upload = ftp.Upload(local, path, local.stat().st_size, backup=True)
    return ftp.start(s, [upload], rom_id=rom.id, user_id=user_id, dest=dest_dir, source=str(local))
