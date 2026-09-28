"""TR2 LIVE precheck and project source-lock admission.

The approval caller keeps ``source_lock`` held through backup, apply, validation,
commit and any rollback.  The diagnostic path only reads and never records an
attempt or grants mutation authority.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.documents import tr2_service as tr2
from modules.flow_gate.documents.tr2_apply_adapter import adapter
from modules.flow_gate.services import git_service, path_exclusion_rules


@dataclass(frozen=True)
class LockedSource:
    project_id: str
    group_id: str
    root: Path
    holder: str


def _approval_root(project_id: str, group_id: str) -> Path:
    cfg = db_git.get_config(project_id)
    state = db_git.get_state(group_id)
    if (not cfg or not cfg.get("enabled") or not state
            or not state.get("worktree_registered") or not state.get("branch")
            or state.get("project_id") != project_id):
        raise tr2.Tr2ValidationError("tr2_git_unavailable", "source_root")
    root = tr2.resolve_source_root(project_id, group_id).resolve()
    if not root.is_dir():
        raise tr2.Tr2ValidationError("tr2_git_unavailable", "source_root")
    return root


@contextmanager
def source_lock(project_id: str, group_id: str, *, request_key: str | None = None):
    """Existing DB-backed project Git mutex; no in-process-only substitute."""
    # A fresh holder matters: the existing DB lock verifies ownership by holder
    # after a duplicate INSERT. Reusing request_key here would let a concurrent
    # duplicate mistake the first request's row for its own lock acquisition.
    holder = f"tr2:{group_id}:{uuid.uuid4().hex}"
    if not git_service._acquire_lock(project_id, holder):
        raise tr2.Tr2ValidationError("tr2_source_locked", "source_lock")
    try:
        yield LockedSource(project_id, group_id, _approval_root(project_id, group_id), holder)
    finally:
        db_git.release_lock(project_id, holder)


def _clean(root: Path) -> None:
    pending = git_service.probe_worktree_pending_changes(root)
    if pending is None:
        raise tr2.Tr2ValidationError("tr2_git_unavailable", "worktree_status")
    if pending:
        raise tr2.Tr2ValidationError("tr2_worktree_dirty", "worktree_status")


def assert_source_lock(locked: LockedSource) -> None:
    owner = db_git.get_lock(locked.project_id)
    if not owner or owner.get("holder") != locked.holder:
        raise tr2.Tr2ValidationError("tr2_source_locked", "source_lock")
    if _approval_root(locked.project_id, locked.group_id) != locked.root:
        raise tr2.Tr2ValidationError("tr2_git_unavailable", "source_root")


def _committable(spec: dict) -> None:
    for rel in tr2.target_set(spec):
        if path_exclusion_rules.is_excluded_path(rel):
            raise tr2.Tr2ValidationError("tr2_path_unsafe", rel,
                                         {"reason": "target is excluded from source commits"})


def authoritative_precheck(doc_id: str, locked: LockedSource, *,
                           expected_revision: int | None = None,
                           expected_spec_fingerprint: str | None = None) -> dict:
    """Fresh authoritative check inside the lock, before backup/source writes."""
    assert_source_lock(locked)
    doc = db_docs.get_by_id(doc_id)
    if (not doc or doc.get("type_code") != tr2.TR2_TYPE_CODE
            or doc.get("project_id") != locked.project_id
            or doc.get("group_id") != locked.group_id
            or doc.get("doc_review_status") != "pending_review"):
        raise tr2.Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    revision = doc.get("revision_no") or 0
    if expected_revision is not None and revision != expected_revision:
        raise tr2.Tr2ValidationError("tr2_spec_changed", "revision_no",
                                     {"current_revision_no": revision})
    head = tr2.effective_head_for(doc_id)
    if not head or head.get("result_doc_id") != doc_id or head.get("type") != "TR2":
        raise tr2.Tr2ValidationError("tr2_workflow_conflict", "workflow_head")
    body = tr2.load_body(tr2.canonical_path_for_doc(doc))
    canonical = tr2.canonicalize(tr2.validate(body, doc=doc))
    tr2.verify_pair(doc_id, canonical)
    spec = canonical["edit_spec"]
    _committable(spec)
    fingerprint = tr2.spec_fingerprint(spec)
    if expected_spec_fingerprint is not None and fingerprint != expected_spec_fingerprint:
        raise tr2.Tr2ValidationError("tr2_spec_changed", "spec_fingerprint")
    _clean(locked.root)
    baseline = body.get("baseline_fingerprint")
    if not isinstance(baseline, str) or not tr2._SOURCE_HEX.fullmatch(baseline):
        raise tr2.Tr2ValidationError("tr2_history_invariant_error", "baseline_fingerprint")
    result = adapter.evaluate(spec, locked.root, baseline=baseline)
    if not result["ready"]:
        raise tr2.Tr2ValidationError(result["code"], "edit_spec", result)
    return {"document": doc, "body": body, "spec": spec,
            "revision_no": revision, "spec_fingerprint": fingerprint,
            "source_root": locked.root, "evaluation": result}


def apply_locked(doc_id: str, locked: LockedSource, backup_root, *,
                 expected_revision: int | None = None,
                 expected_spec_fingerprint: str | None = None) -> dict:
    """Connected precheck -> snapshot -> apply seam for the approval owner.

    The caller retains the same source lock while it validates and commits. No
    public route calls this helper; diagnostic precheck remains read-only.
    """
    checked = authoritative_precheck(
        doc_id, locked, expected_revision=expected_revision,
        expected_spec_fingerprint=expected_spec_fingerprint)
    spec = checked["spec"]
    baseline = checked["evaluation"]["baseline_fingerprint"]
    bundle_id = adapter.create_backup(spec, locked.root, backup_root,
                                      baseline=baseline)
    applied = adapter.apply_all(spec, locked.root, backup_root, bundle_id)
    return {**checked, "backup_bundle_id": bundle_id, "apply": applied}


def restore_locked(locked: LockedSource, spec: dict, baseline: str,
                   backup_root, bundle_id: str, *, restart: bool = False) -> dict:
    """Restore and verify the baseline under the same source lock."""
    assert_source_lock(locked)
    result = (adapter.recover(backup_root, bundle_id, source_root=locked.root)
              if restart else adapter.restore(backup_root, bundle_id,
                                              source_root=locked.root))
    if tr2.target_fingerprint(spec, locked.root) != baseline:
        raise tr2.Tr2ValidationError("tr2_recovery_required", "baseline_fingerprint",
                                     {"reason": "rollback did not restore baseline"})
    return result


def diagnostic_precheck(doc_id: str) -> dict:
    """Read-only preview. It is never a write permit or an approval attempt."""
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != tr2.TR2_TYPE_CODE:
        raise tr2.Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    body = tr2.load_body(tr2.canonical_path_for_doc(doc))
    spec = tr2.canonicalize(tr2.validate(body, doc=doc))["edit_spec"]
    _committable(spec)
    root = _approval_root(doc["project_id"], doc["group_id"])
    result = adapter.evaluate(spec, root, baseline=body.get("baseline_fingerprint"))
    pending = git_service.probe_worktree_pending_changes(root)
    result["worktree_clean"] = pending is False
    result["ready"] = bool(result["ready"] and pending is False)
    if pending is True:
        result["code"] = "tr2_worktree_dirty"
    elif pending is None:
        result["code"] = "tr2_git_unavailable"
    return result
