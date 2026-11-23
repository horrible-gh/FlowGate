"""Verified, resumable downloads and bounded archive extraction for terminal installs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import urllib.request
import zipfile

from .terminal_errors import BundleError

CHUNK = 1024 * 1024


def verify(path: Path, spec: dict) -> bool:
    if not path.is_file() or path.stat().st_size != spec["size_bytes"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest() == spec["sha256"]


def check_cancel(cancel):
    if cancel.is_set():
        raise BundleError("terminal_install_cancelled", "Installation cancelled", 409)


def fetch(spec, directory: Path, cancel, progress, from_dir: Path | None = None):
    """Keep .part on interruption; a hash mismatch is never promoted or reused."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / spec["filename"]
    if verify(target, spec):
        progress(spec["size_bytes"], spec["size_bytes"])
        return target
    partial = target.with_name(target.name + ".part")
    check_cancel(cancel)
    if from_dir is not None:
        # Offline mode never silently downloads; originals are opened read-only.
        candidates = list(from_dir.rglob(spec["filename"]))
        source = next((p for p in candidates if verify(p, spec)), None)
        if source is None and spec.get("expanded"):
            # The manifest pins the extracted executable/DLL hashes too, so an
            # unpacked offline bundle has the same integrity checks as downloads.
            expanded = directory / (spec["filename"] + ".expanded")
            for member in spec["expanded"]:
                check_cancel(cancel)
                local = next((p for p in from_dir.rglob(member["filename"]) if verify(p, member)), None)
                if local is None:
                    raise BundleError("terminal_offline_file_invalid", "Missing or unverified offline file: " + member["filename"], 422)
                dest = expanded / member["relative_path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(local, dest)
                if not verify(dest, member):
                    raise BundleError("terminal_sha256_mismatch", "Offline file changed while copying", 422)
            progress(spec["size_bytes"], spec["size_bytes"])
            return expanded
        if source is None:
            raise BundleError("terminal_offline_file_invalid", "Missing or unverified offline file: " + spec["filename"], 422)
        with source.open("rb") as src, partial.open("wb") as dst:
            done = 0
            for chunk in iter(lambda: src.read(CHUNK), b""):
                check_cancel(cancel)
                dst.write(chunk)
                done += len(chunk)
                progress(done, spec["size_bytes"])
    else:
        offset = partial.stat().st_size if partial.exists() else 0
        if offset >= spec["size_bytes"]:
            if verify(partial, spec):
                os.replace(partial, target)
                return target
            partial.unlink()
            offset = 0
        headers = {"User-Agent": "FlowGate-Terminal/1", "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(spec["url"], headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status == 206:
                content_range = response.headers.get("Content-Range", "")
                if not content_range.startswith(f"bytes {offset}-") or not content_range.endswith(f"/{spec['size_bytes']}"):
                    raise BundleError("terminal_download_range_invalid", "Invalid download range", 502)
            elif response.status == 200:
                offset = 0  # Server ignored Range: replace, never append.
            else:
                raise BundleError("terminal_download_failed", "Unexpected download response", 502)
            with partial.open("ab" if offset else "wb") as dst:
                for chunk in iter(lambda: response.read(CHUNK), b""):
                    check_cancel(cancel)
                    offset += len(chunk)
                    if offset > spec["size_bytes"]:
                        raise BundleError("terminal_download_size_mismatch", "Download exceeds catalog size", 502)
                    dst.write(chunk)
                    progress(offset, spec["size_bytes"])
    check_cancel(cancel)
    if not verify(partial, spec):
        partial.unlink(missing_ok=True)
        raise BundleError("terminal_sha256_mismatch", "Downloaded file failed SHA-256 verification", 422)
    os.replace(partial, target)
    return target


def extract(archive: Path, destination: Path, cancel):
    """Only regular files/directories; reject absolute paths, links and zip bombs."""
    if archive.name.endswith(".tar.bz2"):
        import tarfile
        with tarfile.open(archive, "r:bz2") as source:
            total = 0
            for item in source:
                check_cancel(cancel)
                name = PurePosixPath(item.name.replace(chr(92), "/"))
                total += item.size
                if (name.is_absolute() or ".." in name.parts or ":" in item.name
                        or not (item.isfile() or item.isdir()) or total > 2 * 1024 ** 3):
                    raise BundleError("terminal_archive_invalid", "Unsafe archive member", 422)
                target = destination.joinpath(*name.parts)
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(item) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst, CHUNK)
        return
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if sum(item.file_size for item in entries) > 1024 * 1024 * 1024:
            raise BundleError("terminal_archive_invalid", "Archive expands beyond limit", 422)
        for item in entries:
            check_cancel(cancel)
            name = PurePosixPath(item.filename.replace("\\", "/"))
            mode = item.external_attr >> 16
            if (name.is_absolute() or ".." in name.parts or ":" in item.filename
                    or stat.S_ISLNK(mode)):
                raise BundleError("terminal_archive_invalid", "Unsafe archive member", 422)
            target = destination.joinpath(*name.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
