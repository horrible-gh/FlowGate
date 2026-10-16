"""0066 T#3 — fetch-only refresh (D#1 §3-7, E12).

Remote-tracking refs move (pruned: a branch deleted on origin loses its tracking ref);
nothing local does: no ff-only of the base branch, the base checkout and the Group
worktree are untouched, and only R is taken (never B).
"""
from __future__ import annotations

import subprocess

from manual_merge_0066_support import (
    _git, group, held_locks, manual, needs_git, refused, rev, spy_locks, work,
)
from test_git_merge_target_0594 import patch_store, proj, storage_dir  # noqa: F401 — fixtures


def _refresh(pid, gid=None):
    from modules.flow_gate.services.git import remote_refresh
    return remote_refresh.fetch_refs(pid, gid)


def _tree_state(repo):
    return (_git(["rev-parse", "HEAD"], cwd=repo).strip(),
            _git(["status", "--porcelain", "--untracked-files=all"], cwd=repo),
            sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*")
                   if p.is_file() and ".git" not in p.relative_to(repo).parts))


@needs_git
def test_fetch_refs_moves_only_remote_tracking_refs_under_r(proj, monkeypatch):
    gid = group(proj, 1)
    (proj.wt(gid) / "draft.txt").write_text("uncommitted\n", encoding="utf-8")
    proj.origin_commit("main", "upstream.txt", "upstream\n")
    base_before, wt_before = _tree_state(proj.base), _tree_state(proj.wt(gid))
    local_main = rev(proj.base, "main")
    seen = spy_locks(monkeypatch)
    out = _refresh(proj.pid)
    assert out["fetched"] is True and out["base_branch"] == "main"
    assert out["behind_count"] == 1 and out["ahead_count"] == 0 and out["group"] is None
    assert rev(proj.base, "main") == local_main                       # no ff-only
    assert rev(proj.base, "refs/remotes/origin/main") == _git(["rev-parse", "main"], cwd=proj.bare).strip()
    assert _tree_state(proj.base) == base_before and _tree_state(proj.wt(gid)) == wt_before
    domains = [d for op, d, *_ in seen if op == "acquire"]
    assert domains == ["R"]
    assert held_locks(proj.pid) == []
    # the recovery fetch keeps its own contract: it fast-forwards
    from modules.flow_gate.services import git_service as svc
    assert svc.manual_fetch(proj.pid)["result"]["advanced"] is True
    assert rev(proj.base, "main") != local_main


@needs_git
def test_fetch_refs_syncs_origin_url_and_reports_failures(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    group(proj, 1)                                          # provisions the base checkout
    _git(["remote", "set-url", "origin", "file:///nowhere/at/all.git"], cwd=proj.base)
    _refresh(proj.pid)
    assert _git(["remote", "get-url", "origin"], cwd=proj.base).strip() == proj.bare.as_uri()
    real = svc._run_git

    def run(args, **kwargs):
        if args[:3] == ["fetch", "--prune", "origin"]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: unable to access")
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", run)
    error = refused(_refresh, proj.pid)
    assert error.status == 500 and error.code == "git_error"
    assert held_locks(proj.pid) == []


@needs_git
def test_fetch_refs_group_view_matches_the_merge_results(proj):
    from modules.flow_gate.services.git import manual_merge
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    out = _refresh(proj.pid, gid)
    view = manual_merge.merge_results_view(gid)
    assert out["group"]["group_id"] == gid
    assert out["group"]["work_base"]["work_base_ref"] == "main"
    assert out["group"]["work_base"]["remote_branch_missing"] is False
    assert [{k: r[k] for k in ("merge_id", "target", "remote", "push_available", "push_blocked_reason")}
            for r in view["results"]] == out["group"]["results"]
    assert out["group"]["push_candidates"] == 1
    assert refused(_refresh, proj.pid, "otherprj.default.0001").code == "invalid_request"


@needs_git
def test_fetch_refs_prunes_a_branch_deleted_on_origin_and_manual_fetch_is_unchanged(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from manual_merge_0066_support import spy_git, try_rev
    group(proj, 1)
    _git(["config", "fetch.prune", "false"], cwd=proj.base)
    _git(["fetch", "origin"], cwd=proj.base)
    assert try_rev(proj.base, "refs/remotes/origin/release")
    _git(["update-ref", "-d", "refs/heads/release"], cwd=proj.bare)
    _refresh(proj.pid)
    assert try_rev(proj.base, "refs/remotes/origin/release") is None
    assert try_rev(proj.base, "refs/remotes/origin/main")
    seen = spy_git(monkeypatch)
    svc.manual_fetch(proj.pid)                              # its own fetch arguments stay as they were
    assert ["fetch", "--prune", "origin"] not in [a[:3] for a in seen]


@needs_git
def test_fetch_refs_refuses_when_git_is_off(proj):
    from modules.flow_gate.services import git_service as svc
    group(proj, 1)
    cfg = svc.db_git.get_config(proj.pid)
    svc.save_config(proj.pid, {"repo_url": cfg["repo_url"], "provider": "generic",
                               "base_branch": "main", "enabled": False})
    assert refused(_refresh, proj.pid).code == "invalid_state"
