"""Pre-change basis of a Self-check work unit (flowgate.v02.0114 T#3, D 0006 3.3, 8-3).

"What did the tree look like before this change" is not the HEAD at the time of the final
run: once the change is committed that HEAD already holds it. So the basis is written once,
when the work unit's first token is issued, and never rewritten.

* Where: ``token_service.issue`` only -- the one place a token row is created. Manual issue,
  the AI run admission and the continuation chain all pass it, so no caller carries code.
* What: the group worktree's HEAD, for a group-bound TR(new) draft token or a TR edit token.
  A tree with uncommitted changes, a missing worktree or any failure records "none"; no other
  value (a later HEAD, the first Self-check's HEAD, a clean current commit) stands in.
* Work unit: a TR and its rework. A TR(new) draft unit is keyed by (project, group, the
  token's sequence document) while it is unregistered; registration (token consumption)
  attaches it to the TR. A rework / re-issued token inherits the unit's anchor and never
  creates a second one; a TR that has no anchor gets "none".
* First issue only: HEAD is read only when the token is proven to be its draft unit's first
  issue. A unit whose earlier issue left no anchor (a failed write or lookup, or a token from
  before anchors existed) stays "none" on every re-issue -- by then HEAD may hold the change.
* Graph: an attempt run's basis is its ``input_commit`` (unchanged meaning).

Writing happens after the token row committed and outside its transaction; a failure is
logged by the caller and never fails or reshapes the issue.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import tr_self_check_anchors as db_anchors

GIT_TIMEOUT_SEC = 10


def _git(root: Path, *args: str) -> bytes:
    done = subprocess.run(["git", "-C", str(root), *args], shell=False, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=GIT_TIMEOUT_SEC, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout


def probe_group_basis(project_id: str, group_id: str) -> tuple[str, Optional[str], Optional[str]]:
    """``(state, base_commit, reason)`` of the group worktree now. Only a clean tree's HEAD is a
    basis; everything else is "none" with its reason."""
    from modules.flow_gate.services import git_service
    try:
        root, reason = git_service.effective_src_root_ex(project_id, group_id)
        if reason != "worktree" or not root or not Path(root).is_dir():
            return db_anchors.NONE, None, "worktree_unavailable"
        status = _git(Path(root), "status", "--porcelain=v1", "-z", "--untracked-files=all")
        if status:
            return db_anchors.NONE, None, "dirty_worktree"
        head = _git(Path(root), "rev-parse", "HEAD").decode("ascii").strip()
        if not head:
            return db_anchors.NONE, None, "probe_failed"
        return db_anchors.RECORDED, head, None
    except Exception:
        return db_anchors.NONE, None, "probe_failed"


def _is_tr(doc: Optional[dict], project_id: str, group_id: str) -> bool:
    return bool(doc) and str(doc.get("type_code") or "").upper() == "TR" \
        and doc.get("project_id") == project_id and doc.get("group_id") == group_id


def _first_issue_basis(project_id: str, group_id: str, doc_ref: str,
                       token_id: str) -> tuple[str, Optional[str], Optional[str]]:
    """The basis a draft unit with no anchor gets. HEAD is read only when this token is proven
    to be the unit's first issue: an earlier issue of the unit that left no anchor (its write
    or lookup failed, or it predates anchors) means the change may already be in HEAD, so the
    unit stays "none" for good. An unreadable issue history proves nothing and is "none" too."""
    try:
        earlier = db_anchors.unaccounted_draft_issues(project_id, group_id, doc_ref, token_id)
    except Exception:
        return db_anchors.NONE, None, "issue_history_unavailable"
    if earlier:
        return db_anchors.NONE, None, "earlier_issue_without_anchor"
    return probe_group_basis(project_id, group_id)


def record_on_issue(token: dict) -> Optional[dict]:
    """The anchor ``token`` (the issued row's data) belongs to, created if the unit has none.

    Returns None for a token that is no Self-check work unit's (another scope, no group, a
    non-TR step, Self-check off for the project)."""
    scope = token.get("action_scope")
    project_id, group_id = token.get("project"), token.get("group_id")
    doc_ref, token_id = token.get("doc_ref"), token.get("token_id")
    if scope not in ("new", "edit") or not (project_id and group_id and doc_ref and token_id):
        return None
    if not db_projects.tr_self_check_enabled(project_id):
        return None
    if scope == "edit":
        if not _is_tr(db_documents.get_by_id(doc_ref), project_id, group_id):
            return None
        anchor = db_anchors.get_for_tr(doc_ref)
        if anchor is None:
            # A registered TR without an anchor (registered before this existed, or its draft
            # recorded nothing): its change may already be in HEAD -- never take HEAD now.
            anchor = db_anchors.create_for_tr(project_id, group_id, doc_ref, state=db_anchors.NONE,
                                              base_commit=None, reason="no_anchor_at_registration",
                                              token_id=token_id)
    else:
        from modules.flow_gate.services import remote_tool_service  # lazy -- import cycle
        step, lookup_failed = remote_tool_service._worker_token_step_type_result(token)
        if not lookup_failed and str(step or "").upper() != "TR":
            return None
        anchor = db_anchors.get_open(project_id, group_id, doc_ref)
        if anchor is None:
            if lookup_failed:
                state, commit, reason = db_anchors.NONE, None, "step_lookup_failed"
            else:
                state, commit, reason = _first_issue_basis(project_id, group_id, doc_ref, token_id)
            anchor = db_anchors.create_open(project_id, group_id, doc_ref, state=state,
                                            base_commit=commit, reason=reason, token_id=token_id)
    db_anchors.map_token(token_id, anchor["anchor_id"])
    return anchor


def link_registration(token: dict, doc_id: Optional[str]) -> bool:
    """At the registration of a TR by its TR(new) token: the draft unit's anchor joins the TR.
    The anchor's commit and state are not touched. True when a link was written."""
    if token.get("action_scope") != "new" or not doc_id or not token.get("token_id"):
        return False
    if not _is_tr(db_documents.get_by_id(doc_id), token.get("project"), token.get("group_id")):
        return False
    anchor = db_anchors.get_for_token(token["token_id"])
    if anchor is None or anchor.get("tr_doc_id"):
        return False
    return db_anchors.link_to_tr(anchor["anchor_id"], doc_id) > 0


def resolve_for_run(run: dict) -> Optional[dict]:
    """The pre-change anchor an after-run is compared against, or None (no basis)."""
    if run.get("attempt_id"):
        commit = run.get("input_commit")
        return {"anchor_id": None, "state": db_anchors.RECORDED if commit else db_anchors.NONE,
                "base_commit": commit or None, "source": "graph_input_commit",
                "reason": None if commit else "input_commit_missing"}
    anchor = None
    if run.get("tr_doc_id"):
        anchor = db_anchors.get_for_tr(run["tr_doc_id"])
    if anchor is None and run.get("owner_token_id"):
        anchor = db_anchors.get_for_token(run["owner_token_id"])
        # A draft anchor that has since joined another TR is not this run's basis.
        if anchor is not None and run.get("tr_doc_id") and anchor.get("tr_doc_id") not in (None, run["tr_doc_id"]):
            anchor = None
    if anchor is None:
        return None
    return {**anchor, "source": "token_issue"}
