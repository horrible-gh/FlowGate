"""flowgate.v02test.0068: a group that already merged its own branch into the work base.

F80(자동 타이틀) ran Git up to push twice, so ``Merge branch '<group>' into '<work base>'``
put three of its ledger commits into every later work-base tip.  The next update
merged cleanly (or its conflict was resolved) and then failed rule 6
(``floor_contains_group_work``) when recording the new floor, which rolled the
merge back - every update after a push failed the same way.

Shared real-git fixtures come from test_group_work_base_scope_0665.py.
"""
from __future__ import annotations

import pytest

from tests.regression.group_git.test_group_work_base_scope_0665 import (  # noqa: F401
    _git,
    _ledger_commit,
    _scope,
    _state,
    patch_store,
    proj,
    pytestmark,
    storage_dir,
)


def _seed_documents_directly(monkeypatch):
    """The TR rows below are seeded straight into the store (no card edit in play)."""
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    def passthrough(_operation, *args, **_kwargs):
        return next(a for a in reversed(args) if callable(a))()
    monkeypatch.setitem(ports._handlers, ports.DOCUMENT_WRITE_GATE, passthrough)


def _deliver(proj, gid: str, ref: str) -> str:
    """Merge ``ref`` of the group repository into origin v0.2 with the FlowGate subject."""
    branch = gid.replace(".", "_")
    _git(["fetch", "origin"], cwd=proj.seed)
    _git(["checkout", "-B", "v0.2", "origin/v0.2"], cwd=proj.seed)
    _git(["fetch", str(proj.wt(gid)), ref], cwd=proj.seed)
    _git(["-c", "user.name=FlowGate", "-c", "user.email=flowgate@localhost",
          "merge", "--no-ff", "FETCH_HEAD", "-m", f"Merge branch '{branch}' into 'v0.2'"],
         cwd=proj.seed)
    _git(["push", "origin", "v0.2"], cwd=proj.seed)
    sha = _git(["rev-parse", "HEAD"], cwd=proj.seed).strip()
    _git(["checkout", "main"], cwd=proj.seed)
    return sha


def test_update_after_the_group_merged_its_own_branch_into_the_work_base(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    _seed_documents_directly(monkeypatch)
    gid = proj.group(40, work_base="v0.2")
    c1 = proj.commit(gid, "delivered.txt", "d\n")
    _ledger_commit(gid, proj.pid, 2, c1)
    _deliver(proj, gid, "HEAD")                     # Git run: merge + push into v0.2
    tip = proj.origin_commit("v0.2", "other_flow.txt", "o\n")
    c2 = proj.commit(gid, "after.txt", "a\n")
    _ledger_commit(gid, proj.pid, 3, c2)

    result = svc.update_from_base(gid)["result"]

    assert result["source_sha"] == tip
    assert _state(gid)["work_base_sync_sha"] == tip
    assert db_git.list_work_base_log(gid)[-1]["kind"] == "update"
    # The delivered commit is work-base history now; the later one is still group work.
    assert _scope(proj.pid, gid) == {"after.txt"}
    # The recorded floor stays usable for every later scope read and update.
    tip2 = proj.origin_commit("v0.2", "other_flow_2.txt", "o2\n")
    assert svc.update_from_base(gid)["result"]["source_sha"] == tip2
    assert _scope(proj.pid, gid) == {"after.txt"}


def test_group_work_that_reached_the_work_base_off_its_own_branch_still_fails_rule_6(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    _seed_documents_directly(monkeypatch)
    gid = proj.group(41, work_base="v0.2")
    branch = gid.replace(".", "_")
    fork = _state(gid)["work_base_sha"]
    proj.wt_git(gid, "checkout", "-q", "-b", "side", fork)
    c1 = proj.commit(gid, "side.txt", "s\n")
    proj.wt_git(gid, "checkout", "-q", branch)
    proj.wt_git(gid, "merge", "--no-ff", "side", "-m", "Merge attempt ref 'side'")
    _ledger_commit(gid, proj.pid, 2, c1)
    _deliver(proj, gid, "side")                     # not the group's own tip
    proj.origin_commit("v0.2", "other_flow.txt", "o\n")
    head = proj.wt_git(gid, "rev-parse", "HEAD")

    with pytest.raises(GitServiceError) as exc:
        svc.update_from_base(gid)

    assert exc.value.code == "group_work_base_record_failed"
    assert exc.value.details["reason"] == "floor_contains_group_work"
    assert exc.value.details["ledger_commit"] == c1
    assert _state(gid)["work_base_sync_sha"] is None
    assert proj.wt_git(gid, "rev-parse", "HEAD") == head
