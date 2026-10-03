"""Manifest-authorized test asset reads and CAS edits for approved contract-2 TS."""
from __future__ import annotations

import hashlib
import json

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
    try:
        live = test_basis_service.resolve(doc, parsed["cases"])
    except ValueError as exc:
        raise test_run_service._http_error(409, "basis_unavailable", detail=str(exc)) from exc
    if live["basis_id"] != basis["basis_id"]:
        raise test_run_service._http_error(409, "basis_stale", basis_id=basis["basis_id"],
                                          live_basis_id=live["basis_id"])


def manifest(ts_id: str) -> dict:
    doc, parsed, basis = _load(ts_id)
    try:
        live = test_basis_service.resolve(doc, parsed["cases"])
        valid = live["basis_id"] == basis["basis_id"]
    except ValueError:
        valid = False
    return {"doc_id": ts_id, "basis_id": basis["basis_id"],
            "manifest_hash": basis["test_assets"]["manifest_hash"],
            "source": basis["source"], "assets": basis.get("manifest") or [],
            "basis_valid": valid}


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


def update(ts_id: str, path: str, *, expected_hash: str, content: str,
           actor_id: str, locale: str = "ko") -> dict:
    if not isinstance(content, str) or len(content.encode("utf-8")) > 2 * 1024 * 1024:
        raise test_run_service._http_error(422, "test_asset_content_invalid")
    with test_run_service._admission_lock:
        doc, parsed, basis = _load(ts_id)
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
        try:
            with get_store().transaction():
                # Recheck the approved report inside the serialized mutation boundary.
                fresh = db_docs.get_by_id(ts_id)
                if not fresh or fresh.get("revision_no") != doc.get("revision_no"):
                    raise test_run_service._http_error(409, "ts_changed", doc_id=ts_id)
                fresh_paired = test_run_service._active_tsr_for_ts(fresh)
                if fresh_paired and fresh_paired.get("doc_review_status") == "approved":
                    raise test_run_service._http_error(409, "tsr_already_approved",
                                                      tsr_doc_id=fresh_paired["doc_id"])
                target.write_bytes(new)
                successor = test_basis_service.resolve(doc, parsed["cases"])
                if successor["basis_id"] == basis["basis_id"]:
                    raise RuntimeError("test_asset_basis_unchanged")
                updated = db_docs.update(ts_id, {
                    "meta": test_basis_service.metadata_with_basis(doc, successor),
                })
                if not updated:
                    raise RuntimeError("test_basis_update_failed")
                facts = {"actor": actor_id, "path": path, "old_hash": old_hash,
                         "new_hash": new_hash, "old_basis_id": basis["basis_id"],
                         "basis_id": successor["basis_id"],
                         "manifest_hash": successor["test_assets"]["manifest_hash"],
                         "tsr_doc_id": (paired or {}).get("doc_id")}
                for event in ("test_spec_asset_updated", "test_spec_basis_superseded",
                              "test_spec_basis_created", "test_spec_results_invalidated"):
                    db_events.insert_event(ts_id, event, note=json.dumps(facts, ensure_ascii=False))
                # Initialization is last: its own failure handler restores the TSR file,
                # while this enclosing transaction restores DB rows and the asset bytes.
                initialized = test_basis_service.initialize(updated, parsed, successor,
                                                            locale=locale)
        except Exception:
            target.write_bytes(old)
            raise
    return {"path": path, "content_hash": new_hash, "basis_id": successor["basis_id"],
            "test_basis": successor, "initialization_run_id": initialized["run_id"],
            "tsr_doc_id": initialized["tsr_doc_id"]}
