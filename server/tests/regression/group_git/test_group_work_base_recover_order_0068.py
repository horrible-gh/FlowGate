"""0068: legacy floor recovery anchors below the earliest group commit in the history.

Ledger rows can be written out of commit order. When an older group commit was already
merged into the work base, anchoring below the lowest-id row yields a candidate that
contains that commit (``fork_contains_group_work``) although a provable fork exists.
"""
from __future__ import annotations

from tests.regression.group_git.test_group_work_base_scope_0665 import (  # noqa: F401
    _forget_floor,
    _ledger_commit,
    _state,
    patch_store,
    proj,
    pytest,
    pytestmark,
    storage_dir,
)


def _seed_documents_directly(monkeypatch):
    """The TR rows below are seeded straight into the store (no card edit in play)."""
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    def passthrough(_operation, *args, **_kwargs):
        return next(a for a in reversed(args) if callable(a))()
    monkeypatch.setitem(ports._handlers, ports.DOCUMENT_WRITE_GATE, passthrough)


def test_recovery_uses_the_earliest_group_commit_not_the_lowest_ledger_id(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    _seed_documents_directly(monkeypatch)
    gid = proj.group(68, work_base="v0.2")
    fork = _state(gid)["work_base_sha"]
    c1 = proj.commit(gid, "one.txt", "1\n")
    proj.wt_git(gid, "push", "-q", "origin", "HEAD:v0.2")          # c1 delivered to v0.2
    proj.base_git("fetch", "-q", "origin")
    proj.base_git("branch", "-f", "v0.2", "origin/v0.2")
    c2 = proj.commit(gid, "two.txt", "2\n")
    _ledger_commit(gid, proj.pid, 2, c2)                             # lower id, later commit
    _ledger_commit(gid, proj.pid, 3, c1)
    _forget_floor(gid)

    result = svc.collect_scope_changes(proj.pid, gid)
    assert result["available"], result
    state = _state(gid)
    assert state["work_base_state"] == "verified"
    assert state["work_base_sha"] == fork
    assert set(result["paths"]) == {"one.txt", "two.txt"}
