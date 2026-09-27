"""Lazy Source Bundle ensure and reuse, with a database-owned build slot."""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from modules.flow_gate.db import source_bundles as db
from modules.flow_gate.services import source_bundle_materializer as materializer

WAIT_SECONDS = 125


def _public(row, reused=False):
    return {
        "bundle_id": row["bundle_id"], "status": row["status"], "scope": "whole_source",
        "source_revision": row["source_revision"], "source_dirty": bool(row["source_dirty"]),
        "content_fingerprint": row["content_fingerprint"], "bundle_sha256": row["bundle_sha256"],
        "exclusion_policy_version": row["exclusion_policy_version"],
        "file_count": row["file_count"], "byte_size": row["byte_size"],
        "created_at": row["created_at"], "expires_at": row["expires_at"], "reused": reused,
    }


def _integrity(row):
    """Verify the immutable artifact before reuse; no live worktree is opened here."""
    try:
        root = materializer.bundle_path(row["project_id"], row["bundle_id"])
        raw = (root / "manifest.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["bundle_sha256"]:
            return False
        manifest = json.loads(raw)
        if manifest["content_fingerprint"] != row["content_fingerprint"] or manifest["policy"] != db.POLICY_VERSION:
            return False
        source = root / "source"
        expected = {entry["path"]: entry for entry in manifest["files"]}
        actual = set()
        actual_dirs = set()
        if materializer._linked(source.lstat()) or not source.is_dir():
            return False
        for item in source.rglob("*"):
            st = item.lstat()
            if materializer._linked(st):
                return False
            if item.is_file():
                relative = item.relative_to(source).as_posix()
                actual.add(relative)
                if relative not in expected:
                    return False
                h = hashlib.sha256()
                with item.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(chunk)
                if h.hexdigest() != expected[relative]["sha256"]:
                    return False
            elif item.is_dir():
                actual_dirs.add(item.relative_to(source).as_posix())
            else:
                return False
        return actual == set(expected) and actual_dirs == set(manifest["dirs"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def ensure(project_id: str, group_id: str):
    """Return a path-free identity. A caller needing files uses bundle_path internally."""
    deadline = time.monotonic() + materializer.BUILD_SECONDS
    root = materializer.resolve_worktree(project_id, group_id)
    baseline = materializer.inspect_source(root, deadline)
    for candidate in db.reusable(
        project_id, group_id, baseline["source_revision"], baseline["source_dirty"],
        baseline["content_fingerprint"], datetime.now(timezone.utc).isoformat()
    ):
        if _integrity(candidate):
            return _public(candidate, reused=True)

    owner, bundle_id = db.claim(project_id, group_id)
    if owner is None:
        wait_until = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < wait_until:
            previous = db.get(bundle_id)
            if previous is None:
                build = db.slot(project_id, group_id)
                if build and build["bundle_id"] == bundle_id and build["outcome"] == "failed":
                    raise materializer.SourceBundleError(
                        build["failure_code"] or "build_failed", "Source Bundle build failed"
                    )
            if previous and previous["status"] == "failed":
                raise materializer.SourceBundleError(
                    previous["failure_code"] or "build_failed",
                    previous["failure_reason"] or "Source Bundle build failed",
                )
            if previous and previous["status"] == "created":
                if (previous["source_revision"] == baseline["source_revision"] and
                    bool(previous["source_dirty"]) == baseline["source_dirty"] and
                    previous["content_fingerprint"] == baseline["content_fingerprint"] and
                    _integrity(previous)):
                    return _public(previous, reused=True)
                raise materializer.SourceBundleError(
                    "source_changed", "source changed during concurrent Bundle build"
                )
            time.sleep(0.1)
        raise materializer.SourceBundleError("build_wait_timeout", "concurrent Bundle build did not complete")

    final = materializer.bundle_path(project_id, bundle_id)
    try:
        db.start(bundle_id, project_id, group_id)
        metadata = materializer.materialize(root, project_id, bundle_id, baseline, deadline)
        if materializer.resolve_worktree(project_id, group_id) != root:
            raise materializer.SourceBundleError("source_changed", "exact group worktree changed during capture")
        created = db.created(bundle_id, owner, metadata)
        return _public(created)
    except Exception as exc:
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        code = exc.code if isinstance(exc, materializer.SourceBundleError) else "build_failed"
        reason = exc.message if isinstance(exc, materializer.SourceBundleError) else "Source Bundle build failed"
        db.failed(bundle_id, owner, code, reason)
        raise materializer.SourceBundleError(code, reason) from exc


def bundle_source_path(bundle_id: str) -> Path:
    row = db.get(bundle_id)
    if row is None or row["status"] != "created" or not _integrity(row):
        raise materializer.SourceBundleError("bundle_unavailable", "Source Bundle is unavailable")
    return materializer.bundle_path(row["project_id"], bundle_id) / "source"
