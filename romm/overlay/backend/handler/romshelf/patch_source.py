"""Get a hack's patch file: downloaded from the site that publishes it, or dropped in by the user."""

import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx2

from config import ROM_PATCHER_MAX_FILE_SIZE_BYTES
from utils.context import ctx_httpx_client
from utils.rom_patcher import SUPPORTED_PATCH_EXTENSIONS

from .index import USER_AGENT

ARCHIVE_EXTENSIONS = (".zip", ".7z", ".rar")
# Programs and scripts. A download holding one is refused: nothing in it is used or run.
EXECUTABLE_EXTENSIONS = frozenset(
    ".exe .com .bat .cmd .scr .msi .msp .dll .cpl .pif .ps1 .psm1 .vbs .vbe .wsf .wsh .hta .jar .js .jse .lnk .reg"
    " .sh .app .apk .dmg .pkg .deb .rpm .appimage .py .pyw".split()
)
_SEVEN_ZIP = "/usr/bin/7zz"
_BSDTAR = "bsdtar"
_ARCHIVE_TIMEOUT = 60


class PatchSourceError(Exception):
    """The patch could not be fetched or read. `code` tells the UI what to offer next."""

    def __init__(self, message: str, code: str = "patch_error") -> None:
        super().__init__(message)
        self.code = code


class NeedsChoice(PatchSourceError):
    """The archive holds several patches; the user picks one."""

    def __init__(self, candidates: list[str]) -> None:
        super().__init__("The download holds several patches. Pick the one to apply.", "choose_patch")
        self.candidates = candidates


@dataclass
class PatchFile:
    path: Path
    name: str


def _is_program(path: Path) -> bool:
    if path.suffix.lower() in EXECUTABLE_EXTENSIONS:
        return True
    with path.open("rb") as fh:
        head = fh.read(4)
    return head[:2] == b"MZ" or head == b"\x7fELF"


def _refuse_program(name: str) -> PatchSourceError:
    return PatchSourceError(f"The download includes a program ({name}), so RomStop won't use it.", "unsafe")


def _is_patch(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_PATCH_EXTENSIONS


def _looks_like_html(head: bytes) -> bool:
    start = head.lstrip()[:15].lower()
    return start.startswith((b"<!doctype", b"<html", b"<head", b"<?xml"))


async def download(url: str, dest_dir: Path) -> Path:
    """Fetch `url` into `dest_dir`. A page instead of a file means the site wants a browser."""
    client = ctx_httpx_client.get()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream, application/zip, */*"}
    try:
        async with client.stream("GET", url, headers=headers, timeout=60, follow_redirects=True) as res:
            if res.status_code in (401, 403, 429, 503):
                raise PatchSourceError(
                    f"The site refused the download (HTTP {res.status_code}). Download it in your browser and drop it here.",
                    "browser_download",
                )
            res.raise_for_status()
            name = _filename(res.headers.get("content-disposition"), str(res.url))
            target = dest_dir / ("download" + Path(name).suffix.lower())
            size = 0
            with target.open("wb") as fh:
                async for chunk in res.aiter_bytes():
                    size += len(chunk)
                    if size > ROM_PATCHER_MAX_FILE_SIZE_BYTES:
                        raise PatchSourceError("The download is larger than RomM's patch size limit.")
                    fh.write(chunk)
    except httpx2.HTTPError as exc:
        raise PatchSourceError(
            f"Could not download the patch ({exc.__class__.__name__}). Download it in your browser and drop it here.",
            "browser_download",
        ) from exc
    with target.open("rb") as fh:
        if _looks_like_html(fh.read(512)):
            raise PatchSourceError(
                "The site sent a web page instead of the patch file. Download it in your browser and drop it here.",
                "browser_download",
            )
    final = dest_dir / _safe_name(name, target.suffix)
    target.rename(final)
    return final


def _safe_name(name: str, fallback_suffix: str) -> str:
    base = Path(name).name.replace("\x00", "") or f"patch{fallback_suffix}"
    return base[:200]


def _filename(disposition: str | None, url: str) -> str:
    if disposition:
        for part in disposition.split(";"):
            part = part.strip()
            if part.lower().startswith("filename*="):
                return part.split("''", 1)[-1].strip('"')
            if part.lower().startswith("filename="):
                return part.split("=", 1)[1].strip('"')
    return Path(httpx2.URL(url).path).name or "patch"


def _sniff_archive(path: Path) -> str | None:
    with path.open("rb") as fh:
        head = fh.read(8)
    if head.startswith(b"PK\x03\x04"):
        return ".zip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ".7z"
    if head.startswith(b"Rar!"):
        return ".rar"
    return None


def _extract(archive: Path, kind: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if kind == ".zip":
        with zipfile.ZipFile(archive) as zf:
            total = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                total += info.file_size
                if total > ROM_PATCHER_MAX_FILE_SIZE_BYTES * 4:
                    raise PatchSourceError("The archive is too large to unpack.")
                target = (out / info.filename).resolve()
                if not target.is_relative_to(out.resolve()):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        return
    cmd = [_SEVEN_ZIP, "x", "-y", "-snld", f"-o{out}", str(archive)] if kind == ".7z" else [_BSDTAR, "-xf", str(archive), "-C", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=_ARCHIVE_TIMEOUT, shell=False)  # trunk-ignore(bandit/B603)
    except (subprocess.SubprocessError, OSError) as exc:
        raise PatchSourceError(f"Could not unpack the {kind} archive. Unpack it yourself and drop the patch file here.") from exc


def patches_in(path: Path, work_dir: Path) -> list[PatchFile]:
    """The patch files `path` is or holds. Refuses any download that is or holds a program."""
    if _is_program(path):
        raise _refuse_program(path.name)
    if _is_patch(path.name) and not _sniff_archive(path):
        return [PatchFile(path, path.name)]
    kind = _sniff_archive(path) or (path.suffix.lower() if path.suffix.lower() in ARCHIVE_EXTENSIONS else None)
    if not kind:
        raise PatchSourceError(f"{path.name} is not a patch RomM can apply ({', '.join(sorted(SUPPORTED_PATCH_EXTENSIONS))}).")
    out = work_dir / "unpacked"
    _extract(path, kind, out)
    root = out.resolve()
    found = []
    for f in sorted(out.rglob("*")):
        if f.is_file() and not f.is_symlink() and f.resolve().is_relative_to(root):
            if _is_program(f):
                raise _refuse_program(f.name)
            if _is_patch(f.name):
                found.append(PatchFile(f, str(f.relative_to(out))))
            elif _sniff_archive(f):
                # Some hacks ship a zip inside the zip.
                found.extend(patches_in(f, work_dir / f"nested-{len(found)}"))
    if not found:
        raise PatchSourceError("The download has no patch file RomM can apply.")
    return found


def pick(candidates: list[PatchFile], choice: str | None) -> PatchFile:
    if choice:
        for c in candidates:
            if c.name == choice:
                return c
        raise PatchSourceError(f"{choice} is not in the download.")
    if len(candidates) == 1:
        return candidates[0]
    raise NeedsChoice([c.name for c in candidates])


def write_upload(data: bytes, name: str, work_dir: Path) -> Path:
    if len(data) > ROM_PATCHER_MAX_FILE_SIZE_BYTES:
        raise PatchSourceError("The file is larger than RomM's patch size limit.")
    if not data:
        raise PatchSourceError("The file is empty.")
    target = work_dir / _safe_name(name, ".bin")
    target.write_bytes(data)
    return target

