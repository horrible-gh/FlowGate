"""Manifest-authorized test asset reads and CAS edits for approved contract-2 TS."""
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


def _load(ts_id: str) -> tuple[dict, dict, dict]:
    doc, parsed = test_run_service.load_spec_ts(ts_id)
    if doc.get("doc_review_status") != "approved":
        raise test_run_service._http_error(409, "doc_not_approved", doc_id=ts_id)
    basis = test_basis_service.current(doc)
    if not basis:
        raise test_run_service._http_error(409, "basis_missing", doc_id=ts_id)
    return doc, parsed, basis


def _entry(doc: dict, basis: dict, path: str) -> dict:
    # Membership, not a path prefix, grants authority. Check eligibility again in
    # case stored metadata is malformed or was written by an older implementation.
    entries = {item["path"]: item for item in basis.get("manifest") or []}
    if path not in entries or not test_basis_service._test_only_path(path):
        raise test_run_service._http_error(403, "test_asset_not_allowlisted", path=path)
    target = (test_basis_service.source_root(doc) / path).resolve()
    if (not target.is_relative_to(test_basis_service.source_root(doc).resolve())
            or target != test_basis_service.source_root(doc).resolve() / path
            or not target.is_file()):
        raise test_run_service._http_error(409, "test_asset_missing", path=path)
    return entries[path]


def _require_live(doc: dict, parsed: dict, basis: dict) -> None:
    error = test_basis_service.verdict_error(
        test_basis_service.verdict(doc, basis, parsed["cases"]), test_run_service._http_error,
        doc_id=doc["doc_id"])
    if error is not None:
        raise error


def manifest(ts_id: str) -> dict:
    doc, parsed, basis = _load(ts_id)
    judged = test_basis_service.verdict(doc, basis, parsed["cases"], memo=True)
    return {"doc_id": ts_id, "basis_id": basis["basis_id"],
            "manifest_hash": basis["test_assets"]["manifest_hash"],
            "source": basis["source"], "binding": basis.get("binding"),
            "assets": basis.get("manifest") or [],
            "basis_valid": judged["basis_valid"],
            "basis_verdict": {"state": judged["state"], "reasons": judged["reasons"]}}


def content(ts_id: str, path: str) -> dict:
    doc, parsed, basis = _load(ts_id)
    entry = _entry(doc, basis, path)
    _require_live(doc, parsed, basis)
    target = test_basis_service.source_root(doc) / path
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["content_hash"]:
        raise test_run_service._http_error(409, "asset_hash_changed", path=path)
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise test_run_service._http_error(422, "test_asset_not_utf8", path=path) from exc
    return {"path": path, "content": body, "content_hash": entry["content_hash"],
            "role": entry["role"], "basis_id": basis["basis_id"]}


def _preconditions(ts_id: str, doc: dict, basis: dict) -> dict | None:
    """Edit preconditions; returns the active TSR. Checked before and after capture."""
    paired = test_run_service._active_tsr_for_ts(doc)
    if paired and paired.get("doc_review_status") == "approved":
        raise test_run_service._http_error(409, "tsr_already_approved",
                                          tsr_doc_id=paired["doc_id"])
    pending = db_test_runs.get_pending_failure_origin(ts_id)
    if pending:
        raise test_run_service._http_error(409, "failure_origin_pending",
                                          run_id=pending["run_id"])
    latest = db_test_runs.latest_spec_result(
        ts_id, doc.get("revision_no") or 0, basis["basis_id"])
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
    """CAS edit of one manifest asset, producing a successor Basis from a new capture.

    Lock order is the Group's G, then the admission lock (the order approval capture and
    rebind also follow): under G the whole Basis verdict is taken again, then bytes are
    written and captured, and the successor must differ from the approved source only at
    ``path`` (a product change needs TS reopen and approval, D §3.5); the preconditions are
    checked again under the admission lock and the DB transaction before anything is
    stored. The write itself is a staged atomic replacement inside the recovery scope: any
    failure, the write included, leaves or restores the old asset bytes and rolls the DB
    back; a new Bundle then has no Pin and expires by TTL.
    """
    from modules.flow_gate.services.git import lock_manager
    if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise test_run_service._http_error(422, "test_asset_content_invalid")
    with test_run_service._admission_lock:
        doc, parsed, basis = _load(ts_id)
        _preconditions(ts_id, doc, basis)
        entry = _entry(doc, basis, path)
        _require_live(doc, parsed, basis)
        target = test_basis_service.source_root(doc) / path
        old = target.read_bytes()
        old_hash = hashlib.sha256(old).hexdigest()
        if expected_hash != entry["content_hash"] or expected_hash != old_hash:
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
    try:
        # The first verdict ran before G: judge the whole Basis again now that no other
        # source mutation can run, so a product change made meanwhile is refused here.
        _require_live(doc, parsed, basis)
        current_bytes = target.read_bytes()
        if hashlib.sha256(current_bytes).hexdigest() != old_hash:
            raise test_run_service._http_error(409, "expected_hash_mismatch", path=path,
                                              current_hash=hashlib.sha256(current_bytes).hexdigest())
        try:
            _replace_bytes(target, new)
            successor = test_basis_service.capture(doc, parsed["cases"], locked=True)
            if successor["basis_id"] == basis["basis_id"]:
                raise RuntimeError("test_asset_basis_unchanged")
            if not test_basis_service.edited_only(basis, successor, path, old):
                # Something besides this asset changed between the recheck and the capture.
                raise test_run_service._http_error(
                    409, "basis_stale", doc_id=ts_id, basis_state=test_basis_service.STALE,
                    reasons=[test_basis_service.REASON_SOURCE], basis_id=basis["basis_id"])
            with test_run_service._admission_lock, get_store().transaction():
                # Recheck the approved report inside the serialized mutation boundary.
                fresh = db_docs.get_by_id(ts_id)
                if not fresh or fresh.get("revision_no") != doc.get("revision_no"):
                    raise test_run_service._http_error(409, "ts_changed", doc_id=ts_id)
                fresh_basis = test_basis_service.current(fresh)
                if not fresh_basis or fresh_basis["basis_id"] != basis["basis_id"]:
                    raise test_run_service._http_error(409, "basis_stale", doc_id=ts_id,
                                                      reasons=[test_basis_service.REASON_BASIS_REPLACED])
                paired = _preconditions(ts_id, fresh, basis)
                updated = db_docs.update(ts_id, {
                    "meta": test_basis_service.metadata_with_basis(fresh, successor),
                })
                if not updated:
                    raise RuntimeError("test_basis_update_failed")
                test_basis_service.pin(updated, successor)
                facts = {"actor": actor_id, "path": path, "old_hash": old_hash,
                         "new_hash": new_hash, "old_basis_id": basis["basis_id"],
                         "basis_id": successor["basis_id"],
                         "manifest_hash": successor["test_assets"]["manifest_hash"],
                         "bundle_id": successor["binding"]["bundle_id"],
                         "tsr_doc_id": (paired or {}).get("doc_id")}
                for event in ("test_spec_asset_updated", "test_spec_basis_superseded",
                              "test_spec_basis_created", "test_spec_results_invalidated"):
                    db_events.insert_event(ts_id, event, note=json.dumps(facts, ensure_ascii=False))
                # Initialization is last: its own failure handler restores the TSR file,
                # while this enclosing transaction restores DB rows and the asset bytes.
                initialized = test_basis_service.initialize(updated, parsed, successor,
                                                            locale=locale)
        except Exception:
            # A failed replacement left the old bytes; restore only bytes this edit wrote.
            if hashlib.sha256(target.read_bytes()).hexdigest() == new_hash:
                _replace_bytes(target, old)
            raise
    finally:
        lock_manager.release(ctx, outcome.lock_key)
    return {"path": path, "content_hash": new_hash, "basis_id": successor["basis_id"],
            "test_basis": successor, "initialization_run_id": initialized["run_id"],
            "tsr_doc_id": initialized["tsr_doc_id"]}
