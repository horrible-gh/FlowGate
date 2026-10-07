"""Capture a whole source worktree into a durable immutable Source Bundle."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from modules.flow_gate.storage import paths as storage_paths
# 0684 T#2: the scan, exclusion and hash rules live in the neutral source fingerprint
# module (the Test Basis measures with them, Bundle-free). The Bundle code below keeps
# using them under their old names until T#4 removes Source Bundles.
from modules.flow_gate.services import source_fingerprint as _fingerprint_rules
from modules.flow_gate.services.source_fingerprint import (  # noqa: F401 -- re-exported
    _SECRET, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, _check_time, _excluded, _fingerprint,
    _hash_file, _identity, _linked, _mode, _safe_file, _safe_name, _same, _scan, inspect_source,
    resolve_worktree,
)
from modules.flow_gate.services.source_fingerprint import SourceFingerprintError as SourceBundleError

POLICY_VERSION = "source-bundle-v1"
BUILD_SECONDS = _fingerprint_rules.MEASURE_SECONDS
TTL_HOURS = max(1, int(os.getenv("FLOWGATE_BUNDLE_TTL_HOURS", "24")))


def bundle_path(project_id: str, bundle_id: str) -> Path:
    if not re.fullmatch(r"sb_[0-9a-f]{32}", bundle_id):
        raise SourceBundleError("invalid_bundle", "invalid Bundle id")
    project_key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
    return storage_paths.get_storage_root(project_id) / "source-bundles" / project_key / bundle_id


def materialize(root: Path, project_id: str, bundle_id: str, baseline: dict, deadline):
    started = time.monotonic()
    final = bundle_path(project_id, bundle_id)
    parent = final.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / ("." + bundle_id + ".building")
    if stage.exists() or final.exists():
        raise SourceBundleError("namespace_conflict", "Bundle namespace already exists")
    stage.mkdir()
    published = False
    try:
        source_dir = stage / "source"
        source_dir.mkdir()
        copied = []
        for name, st in baseline["dirs"]:
            (source_dir / PurePosixPath(name)).mkdir(parents=True, exist_ok=True)
        copied_bytes = 0
        copy_started = time.monotonic()
        for name, st in baseline["files"]:
            item = _hash_file(root, name, st, deadline, source_dir / PurePosixPath(name))
            copied_bytes += item["size"]
            if copied_bytes > MAX_TOTAL_BYTES:
                raise SourceBundleError("resource_limit", "Source Bundle total byte limit exceeded")
            copied.append(item)
        copy_duration_ms = int((time.monotonic() - copy_started) * 1000)
        if _fingerprint(copied, baseline["dirs"]) != baseline["content_fingerprint"]:
            raise SourceBundleError("source_changed", "source changed between scan and copy")
        verify_started = time.monotonic()
        verified = inspect_source(root, deadline)
        if verified["content_fingerprint"] != baseline["content_fingerprint"] or (
            verified["source_revision"], verified["source_dirty"]
        ) != (baseline["source_revision"], baseline["source_dirty"]):
            raise SourceBundleError("source_changed", "source changed after copy")
        manifest = {"schema": 1, "policy": POLICY_VERSION,
                    "revision": baseline["source_revision"], "dirty": baseline["source_dirty"],
                    "content_fingerprint": baseline["content_fingerprint"], "files": copied,
                    "dirs": [name for name, _st in baseline["dirs"]]}
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        manifest_path = stage / "manifest.json"
        with manifest_path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        manifest_path.chmod(stat.S_IREAD)
        for path in source_dir.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD)
        source_dir.chmod(stat.S_IREAD | stat.S_IEXEC)
        _check_time(deadline)
        stage.rename(final)
        published = True
        verify_duration_ms = int((time.monotonic() - verify_started) * 1000)
        now = datetime.now(timezone.utc)
        return {"source_revision": baseline["source_revision"], "source_dirty": baseline["source_dirty"],
                "content_fingerprint": baseline["content_fingerprint"],
                "bundle_sha256": hashlib.sha256(encoded).hexdigest(),
                "file_count": len(copied), "byte_size": copied_bytes,
                "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=TTL_HOURS)).isoformat(),
                "metrics": {"bundle_scan_duration_ms": baseline.get("scan_duration_ms", 0),
                            "bundle_copy_duration_ms": copy_duration_ms,
                            "bundle_verify_duration_ms": verify_duration_ms,
                            "bundle_fingerprint_duration_ms": baseline.get("fingerprint_duration_ms", 0),
                            "bundle_file_count": len(copied), "bundle_byte_size": copied_bytes,
                            "bundle_reused": False,
                            "bundle_build_duration_ms": int((time.monotonic() - started) * 1000)}}
    finally:
        if not published and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
