"""Specification-authorized test asset reads and CAS edits for approved contract-2 TS.

0684 T#2 (D#1 §3-7): the authority is the approved specification itself -- the Case
automation_ref and test_assets paths that pass the Test Asset Policy -- not a stored Basis.
Reads hash only the requested file. An edit swaps one file atomically under the Group's
source lock and leaves an event; it creates no successor Basis, no capture and no run.
The results made on the old bytes become stale by identity (their Basis no longer equals
the live source), so the screen offers [run again].
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import events as db_events
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services import test_basis_service, test_run_service, test_spec_service


def _load(ts_id: str) -> tuple[dict, dict]:
    doc, parsed = test_run_service.load_spec_ts(ts_id)
    if doc.get("doc_review_status") != "approved":
        raise test_run_service._http_error(409, "doc_not_approved", doc_id=ts_id)
    return doc, parsed


def _entry(doc: dict, parsed: dict, path: str) -> dict:
    # Membership in the approved specification, not a path prefix, grants authority. The
    # same Test Asset Policy the run's manifest uses is checked again; Git tracking is not
    # asked (untracked assets are edited like tracked ones); the file itself must be safe to
    # reach on disk.
    entries = {item["path"]: item for item in test_basis_service.asset_manifest(doc, parsed["cases"])}
    if path not in entries or test_basis_service.asset_kind(path) is None:
        raise test_run_service._http_error(403, "test_asset_not_allowlisted", path=path)
    try:
        test_basis_service.safe_asset_file(test_basis_service.source_root(doc), path)
    except ValueError as exc:
        raise test_run_service._http_error(409, str(exc), path=path) from exc
    return entries[path]


def manifest(ts_id: str) -> dict:
    """The approved specification's assets with their current hashes (display path).

    The validity shown is the newest result's Basis judged against the memo of the last
    measurement (never a full measurement on this request).
    """
    doc, parsed = _load(ts_id)
    assets = test_basis_service.asset_manifest(doc, parsed["cases"])
    latest = db_test_runs.latest_spec_run(ts_id, doc.get("revision_no") or 0)
    basis = (test_spec_service.load_result_meta(latest.get("result_meta")).get("test_basis")
             if latest else None)
    judged = (test_basis_service.verdict(doc, basis, parsed["cases"], memo=True)
              if latest else None)
    return {"doc_id": ts_id, "basis_id": (basis or {}).get("basis_id"),
            "manifest_hash": test_basis_service.canonical_hash(assets),
            "source": (basis or {}).get("source"), "binding": (basis or {}).get("binding"),
            "assets": assets,
            "basis_valid": (None if judged is None or judged["state"] == test_basis_service.UNCHECKED
                            else judged["basis_valid"]),
            "basis_verdict": ({"state": judged["state"], "reasons": judged["reasons"]}
                              if judged else None)}


def content(ts_id: str, path: str) -> dict:
    doc, parsed = _load(ts_id)
    entry = _entry(doc, parsed, path)
    raw = (test_basis_service.source_root(doc) / path).read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise test_run_service._http_error(422, "test_asset_not_utf8", path=path) from exc
    return {"path": path, "content": body, "content_hash": content_hash, "role": entry["role"]}


def _preconditions(ts_id: str, doc: dict) -> dict | None:
    """Edit preconditions; returns the active TSR. Checked before and under the lock."""
    paired = test_run_service._active_tsr_for_ts(doc)
    if paired and paired.get("doc_review_status") == "approved":
        raise test_run_service._http_error(409, "tsr_already_approved",
                                          tsr_doc_id=paired["doc_id"])
    # 0684 T#1 (D#1 §3-7): a queued or running run is measuring/executing this source.
    running = db_test_runs.get_running_by_doc(ts_id)
    if running:
        raise test_run_service._http_error(409, "run_in_progress", run_id=running["run_id"])
    pending = db_test_runs.get_pending_failure_origin(ts_id)
    if pending:
        raise test_run_service._http_error(409, "failure_origin_pending",
                                          run_id=pending["run_id"])
    latest = db_test_runs.latest_spec_run(ts_id, doc.get("revision_no") or 0)
    if latest and latest.get("failure_origin") in {"product_defect", "hold"}:
        raise test_run_service._http_error(409, "test_asset_edit_wrong_failure_origin",
                                          failure_origin=latest["failure_origin"])
    return paired


def _replace_bytes(target: Path, data: bytes) -> None:
    """Atomic staged replacement: the asset holds its old or its new bytes, never a mix.

    The bytes are written and synced to a sibling file first, then swapped in with one
    ``os.replace``; a write failure leaves the target untouched and the stage is removed.
    """
    staged = target.with_name("." + target.name + ".fg-asset-" + uuid.uuid4().hex)
    try:
        with staged.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copymode(target, staged)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)


def update(ts_id: str, path: str, *, expected_hash: str, content: str,
           actor_id: str, locale: str = "ko") -> dict:
    """CAS edit of one specification asset (D#1 §3-7).

    Lock order is the Group's G, then the admission lock. Under G the preconditions and
    the expected hash are checked again and the bytes are swapped atomically; the event is
    written in the admission lock's transaction. Any failure after the swap restores the
    old bytes. Nothing else is produced: no Basis, no capture, no run.
    """
    from modules.flow_gate.services.git import lock_manager
    if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise test_run_service._http_error(422, "test_asset_content_invalid")
    with test_run_service._admission_lock:
        doc, parsed = _load(ts_id)
        _preconditions(ts_id, doc)
        _entry(doc, parsed, path)
        target = test_basis_service.source_root(doc) / path
        old = target.read_bytes()
        old_hash = hashlib.sha256(old).hexdigest()
        if expected_hash != old_hash:
            raise test_run_service._http_error(409, "expected_hash_mismatch",
                                              path=path, current_hash=old_hash)
    new = content.encode("utf-8")
    new_hash = hashlib.sha256(new).hexdigest()
    if new_hash == old_hash:
        raise test_run_service._http_error(409, "test_asset_unchanged", path=path)
    outcome, ctx = lock_manager.acquire_group(doc.get("project_id"), doc.get("group_id"),
                                              holder_kind="source_mutation")
    if not outcome.ok:
        raise test_run_service._http_error(
            409, "source_busy", **lock_manager.outcome_details(outcome))
    paired = None
    try:
        _entry(doc, parsed, path)  # the path is still safe to write (no link swapped in)
        current_bytes = target.read_bytes()
        if hashlib.sha256(current_bytes).hexdigest() != old_hash:
            raise test_run_service._http_error(409, "expected_hash_mismatch", path=path,
                                              current_hash=hashlib.sha256(current_bytes).hexdigest())
        try:
            with test_run_service._admission_lock, get_store().transaction():
                # Recheck inside the serialized mutation boundary.
                fresh = db_docs.get_by_id(ts_id)
                if not fresh or fresh.get("revision_no") != doc.get("revision_no"):
                    raise test_run_service._http_error(409, "ts_changed", doc_id=ts_id)
                paired = _preconditions(ts_id, fresh)
                _replace_bytes(target, new)
                db_events.insert_event(ts_id, "test_spec_asset_updated", note=json.dumps({
                    "actor": actor_id, "path": path, "old_hash": old_hash, "new_hash": new_hash,
                    "tsr_doc_id": (paired or {}).get("doc_id"),
                    # D#1 §3-7 step 3: earlier results are stale by identity, nothing is rewritten.
                    "results": "stale_by_identity",
                }, ensure_ascii=False))
        except Exception:
            # A failed replacement left the old bytes; restore only bytes this edit wrote.
            if hashlib.sha256(target.read_bytes()).hexdigest() == new_hash:
                _replace_bytes(target, old)
            raise
    finally:
        lock_manager.release(ctx, outcome.lock_key)
    return {"path": path, "content_hash": new_hash, "old_hash": old_hash,
            "tsr_doc_id": (paired or {}).get("doc_id"), "results_stale": True}
