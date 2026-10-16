"""0066 T#3 — final approval after a manual merge (D#1 §3-6(8), Q1/Q2, Q451).

The Group stays Git-active after a manual merge, so the AC final approval takes its usual
``git_action`` path: it merges only what is left (never the earlier merge again), and with
nothing left it completes through the existing no-work path and tears the slot down.

Q451 (2026-10-15): the AC approval route gets no unreported-change check in T#3. The check
keeps living where it is, ``POST /documents/workflow/finalize``, and is verified there
end to end: after a manual merge an unreported path still blocks (the measure starts at the
recorded scope floor, a manual merge does not stand in for reporting) and the same path
passes once a group document reports it. The AC route's missing check is a follow-up item.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from manual_merge_0066_support import _git, files_at, manual, needs_git, rev, state, work
from test_git_merge_target_0594 import patch_store, proj, storage_dir  # noqa: F401 — fixtures

USER = {"user_id": "reviewer", "is_admin": True}


def _seed(p, seq: int) -> tuple[str, str]:
    """R root in progress + one pending AC pointing at it (the 0555/0607 shape)."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso
    gid = p.group(seq)
    store = get_store()
    if store._fetch_one("SELECT 1 AS ok FROM users WHERE user_id = ?", [USER["user_id"]]) is None:
        now = now_iso()
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin,"
            " first_login_required, created_at, updated_at) VALUES (?, ?, ?, 'x', 1, 1, 0, ?, ?)",
            [USER["user_id"], USER["user_id"], f'{USER["user_id"]}@test', now, now])
    if db_groups.get_by_id(gid) is None:
        db_groups.create({"group_id": gid, "project_id": p.pid, "module": "default", "title": "t3 approval"})
    root_id, ac_id = f"{gid}.0001-R", f"{gid}.0002-AC"
    db_docs.create({"doc_id": root_id, "project_id": p.pid, "module": "default", "group_id": gid,
                    "type_code": "R", "seq": 1, "title": "root",
                    "file_path": f"documents/{gid}/0001-R.md"})
    db_docs.update(root_id, {"doc_review_status": "wf_in_progress"})
    if db_wfseq.get_sequence_by_doc_id(root_id) is None:
        db_wfseq.insert_sequence(root_id)
    db_docs.create({"doc_id": ac_id, "project_id": p.pid, "module": "default", "group_id": gid,
                    "type_code": "AC", "seq": 2, "title": "final approval", "target_id": root_id})
    db_docs.update(ac_id, {"doc_review_status": "pending_review", "target_id": root_id})
    return gid, ac_id


def _approve(doc_id: str, git_action: str = "merge") -> tuple[int, dict]:
    from modules.flow_gate.workflow.routers import workflow
    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve", workflow.DocumentBodyRequest(doc_id=doc_id, git_action=git_action), USER, None))
    return response.status_code, json.loads(response.body.decode("utf-8"))


def _status(doc_id: str) -> str:
    from modules.flow_gate.db import documents as db_docs
    return (db_docs.get_by_id(doc_id) or {}).get("doc_review_status")


@needs_git
def test_final_approval_merges_only_what_is_left_after_a_manual_merge(proj):
    gid, ac_id = _seed(proj, 1)
    work(proj, gid, "one.txt", "one\n")
    first = manual(gid, push=True)
    m1 = _git(["rev-parse", "main"], cwd=proj.bare).strip()
    assert first["status"] == "merged" and state(gid)["worktree_registered"]
    tip = work(proj, gid, "two.txt", "two\n")
    code, body = _approve(ac_id)
    assert code == 200, body
    assert _status(ac_id) == "approved"
    head = _git(["rev-parse", "main"], cwd=proj.bare).strip()
    assert _git(["rev-parse", f"{head}^1"], cwd=proj.bare).strip() == m1      # on top of the manual merge
    assert _git(["rev-parse", f"{head}^2"], cwd=proj.bare).strip() == tip
    assert {"one.txt", "two.txt"} <= set(proj.origin_files("main"))
    log = _git(["log", "--merges", "--format=%H", "main"], cwd=proj.bare).split()
    assert log.count(m1) == 1                                 # the earlier merge is not repeated


@needs_git
def test_final_approval_with_nothing_left_completes_through_the_no_work_path(proj):
    gid, ac_id = _seed(proj, 1)
    work(proj, gid, "one.txt", "one\n")
    manual(gid, push=True)
    origin_before = _git(["rev-parse", "main"], cwd=proj.bare).strip()
    code, body = _approve(ac_id)
    assert code == 200, body
    assert _status(ac_id) == "approved"
    assert _git(["rev-parse", "main"], cwd=proj.bare).strip() == origin_before   # no new merge
    assert not proj.wt(gid).exists() and not state(gid).get("worktree_registered")
    assert "one.txt" in files_at(proj.base, "main") and rev(proj.base, "main") == origin_before


def _seed_for_workflow_finalize(p, seq: int) -> tuple[str, str]:
    """R root in progress, no pending step document: what /documents/workflow/finalize takes."""
    from modules.flow_gate.db import documents as db_docs
    gid, ac_id = _seed(p, seq)
    db_docs.update(ac_id, {"doc_review_status": "approved"})
    return gid, f"{gid}.0001-R"


def _report(p, gid: str, seq: int, paths: list[str]) -> None:
    """An approved TR of the group whose stored scope verdict reports ``paths``."""
    from modules.flow_gate.db import documents as db_docs
    doc_id = f"{gid}.{seq:04d}-TR"
    db_docs.create({"doc_id": doc_id, "project_id": p.pid, "module": "default", "group_id": gid,
                    "type_code": "TR", "seq": seq, "title": "report",
                    "file_path": f"documents/{gid}/{seq:04d}-TR.md",
                    "meta": json.dumps({"tr_scope": {"reported": {"count": len(paths), "items": paths}}})})
    db_docs.update(doc_id, {"doc_review_status": "approved"})


def _workflow_finalize(doc_id: str) -> dict:
    from modules.flow_gate.workflow.routers import workflow
    return workflow.finalize_workflow_endpoint(workflow.DocumentBodyRequest(doc_id=doc_id), USER)


@needs_git
def test_workflow_finalize_blocks_a_manually_merged_unreported_path_until_it_is_reported(proj, monkeypatch):
    """Q451 acceptance: the real endpoint runs the real check (checked=true) after a manual
    merge; the unreported path is refused with 409 and nothing changes; once a group TR
    reports it the same endpoint passes and the root becomes wf_done."""
    from modules.flow_gate.services import tr_scope_service
    gid, root_id = _seed_for_workflow_finalize(proj, 1)
    work(proj, gid, "one.txt", "one\n")
    assert manual(gid, push=True)["status"] == "merged"
    assert "one.txt" in proj.origin_files("main")
    real = tr_scope_service.evaluate_group_unreported
    checks = []

    def spy(project_id, group_id):
        out = real(project_id, group_id)
        checks.append(out)
        return out
    monkeypatch.setattr(tr_scope_service, "evaluate_group_unreported", spy)
    with pytest.raises(HTTPException) as info:
        _workflow_finalize(root_id)
    assert info.value.status_code == 409
    assert info.value.detail["code"] == "unresolved_unreported_changes"
    assert "one.txt" in [entry["path"] for entry in info.value.detail["unresolved"]]
    assert checks[-1]["checked"] is True
    assert _status(root_id) == "wf_in_progress"
    _report(proj, gid, 3, ["one.txt"])
    done = _workflow_finalize(root_id)
    assert done["document"]["doc_review_status"] == "wf_done" and _status(root_id) == "wf_done"
    assert len(checks) == 2 and checks[-1]["checked"] is True and checks[-1]["unreported"] == []


@needs_git
def test_a_manual_merge_does_not_replace_reporting_for_the_unreported_check(proj):
    """Q2: the check still measures from the recorded scope floor; a manually merged path
    that no group document reports stays unreported (tr_scope_service is unchanged)."""
    from modules.flow_gate.services import tr_scope_service
    gid, _ac = _seed(proj, 1)
    work(proj, gid, "one.txt", "one\n")
    manual(gid, push=True)
    scope = tr_scope_service.evaluate_group_unreported(proj.pid, gid)
    assert scope["checked"] is True
    assert "one.txt" in [entry["path"] for entry in scope["unreported"]]
