"""origin/repo_url invariant across every network Git entry point
(flowgate.default.0361 — B0001/NR0003/T0004).

B0001: changing a project's Git integration `repo_url` never reached an EXISTING
base checkout's actual `origin` remote — only a fresh clone/adopt ever wired it.
`save_config()` only updates the DB row, and the "checkout" provision path used
to be a bare pass-through with no origin check at all, so every fetch/push that
followed (`manual_fetch`, new group-worktree provisioning, `update_from_base`,
finalize's merge/push, cross-branch merge, the push-rejected re-review recovery)
kept silently talking to the stale remote. `/remote/show` on a commit that only
exists on the newly-connected GitHub remote then 404'd forever.

NR0003 introduces one shared helper, `ensure_origin_matches_config`, called
immediately before every one of those fetch/push sites. These tests exercise the
helper against a REAL local git repository (init + `remote add/get-url/set-url`
only — no clone, no network, so nothing here needs `file://` and nothing skips
the way `test_git_integration_0115.needs_git` does on git-for-Windows), plus two
of the entry points directly implicated by B0001's own reproduction steps: the
"existing checkout" provision pass-through (B0001 §3.2/§5 step 6) and new
group-worktree provisioning (B0001 §4 "신규 group worktree가 오래된 base에서 생성될
수 있음"). `manual_fetch`'s own fast-forward behavior already has a dedicated
host-portable suite (test_git_manual_fetch_0320.py); this file adds the missing
"does it sync origin FIRST" assertion there via the same monkeypatch style.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-origin-sync-0361-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

import pytest  # noqa: E402

from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services.git import branches as branch_service  # noqa: E402
from modules.flow_gate.services.git import merge_target  # noqa: E402
from modules.flow_gate.services.git.credentials import GitServiceError  # noqa: E402

# Reused verbatim (not re-implemented) so the E2E fixtures below drive the SAME
# real-git harness as the rest of the Git integration suite: `_git` is a raising
# subprocess wrapper, `needs_git` skips (never fails) where this platform's git
# cannot clone a local `file://` origin (git-for-Windows), and `_seed_wf_done_root`
# plants the approved R root `finalize()`'s root_wf_done gate requires. Importing
# these (rather than duplicating them) is the same pattern already proven by
# test_merge_timeout_recovery_0607.py, whose own module-scoped `patch_store`/
# `tmp_db` get a fresh instance per importing test file (pytest fixture scope is
# keyed to the REQUESTING module, not the defining one).
from test_git_integration_0115 import (  # noqa: E402,F401 — fixtures used by name
    _git,
    _seed_wf_done_root,
    needs_git,
    patch_store,
    tmp_db,
)


class _Proc:
    """Minimal stand-in for subprocess.CompletedProcess (returncode + stderr)."""

    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _origin_url(root: Path) -> str:
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=root,
        capture_output=True, text=True,
    )
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


# ── unit: ensure_origin_matches_config against a real repo ──────────────────

def test_sets_url_when_origin_is_stale(tmp_path):
    root = _init_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://old.example/repo.git"],
        cwd=root, check=True,
    )
    svc.ensure_origin_matches_config(root, "https://new.example/repo.git")
    assert _origin_url(root) == "https://new.example/repo.git"


def test_adds_origin_when_missing(tmp_path):
    root = _init_repo(tmp_path)
    assert _origin_url(root) == ""
    svc.ensure_origin_matches_config(root, "https://new.example/repo.git")
    assert _origin_url(root) == "https://new.example/repo.git"


def test_already_matching_origin_is_a_noop(tmp_path):
    # NR0003 §12 test I.
    root = _init_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://same.example/repo.git"],
        cwd=root, check=True,
    )
    svc.ensure_origin_matches_config(root, "https://same.example/repo.git")
    assert _origin_url(root) == "https://same.example/repo.git"


def test_blank_repo_url_is_a_noop(tmp_path):
    root = _init_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://old.example/repo.git"],
        cwd=root, check=True,
    )
    svc.ensure_origin_matches_config(root, "")
    assert _origin_url(root) == "https://old.example/repo.git"


def test_sync_failure_raises_and_leaves_origin_untouched(tmp_path, monkeypatch):
    # NR0003 §11/§12 test J: a failed set-url must not pass silently, and must
    # not leave origin looking synced when it is not.
    root = _init_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://old.example/repo.git"],
        cwd=root, check=True,
    )
    real_run_git = svc._run_git

    def fake_run_git(args, **kwargs):
        if args[:2] == ["remote", "set-url"]:
            return _Proc(returncode=1, stderr="fatal: forced failure")
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)
    with pytest.raises(GitServiceError):
        svc.ensure_origin_matches_config(root, "https://new.example/repo.git")
    assert _origin_url(root) == "https://old.example/repo.git"


# ── integration: existing-checkout provision (B0001 §3.2/§5 step 6) ─────────

def test_provision_existing_checkout_syncs_origin_before_returning(monkeypatch, tmp_path):
    """The exact B0001 root cause: `state == 'checkout'` used to be a bare
    pass-through with no origin check at all."""
    base_root = tmp_path / "base"
    calls: list[list[str]] = []

    monkeypatch.setattr(svc, "src_root", lambda name, branch: base_root)
    monkeypatch.setattr(svc, "_judge_base_slot", lambda root, branch: "checkout")

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    result = svc._provision_base_locked(
        {"base_branch": "main", "repo_url": "https://new.example/repo.git"},
        "proj", "proj_name", "manual",
    )
    assert result == {
        "status": "ok", "mode": "none", "reason": None,
        "snapshot_commit": None, "snapshot_at": None,
    }
    assert ["remote", "get-url", "origin"] in calls
    assert ["remote", "set-url", "origin", "https://new.example/repo.git"] in calls


def test_provision_existing_checkout_sync_failure_reports_not_raises(monkeypatch, tmp_path):
    # provision_base()/_provision_base_locked() never raise (docstring contract) —
    # a sync failure must come back as a reported failure, not an exception.
    base_root = tmp_path / "base"
    monkeypatch.setattr(svc, "src_root", lambda name, branch: base_root)
    monkeypatch.setattr(svc, "_judge_base_slot", lambda root, branch: "checkout")

    def fake_run_git(args, **kwargs):
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        if args[:2] == ["remote", "set-url"]:
            return _Proc(returncode=1, stderr="fatal: forced failure")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    result = svc._provision_base_locked(
        {"base_branch": "main", "repo_url": "https://new.example/repo.git"},
        "proj", "proj_name", "manual",
    )
    assert result["status"] == "failed"
    assert result["mode"] == "none"
    assert result["reason"] == "git_error"


def test_provision_existing_checkout_already_synced_is_untouched(monkeypatch, tmp_path):
    base_root = tmp_path / "base"
    calls: list[list[str]] = []

    monkeypatch.setattr(svc, "src_root", lambda name, branch: base_root)
    monkeypatch.setattr(svc, "_judge_base_slot", lambda root, branch: "checkout")

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://same.example/repo.git")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    result = svc._provision_base_locked(
        {"base_branch": "main", "repo_url": "https://same.example/repo.git"},
        "proj", "proj_name", "manual",
    )
    assert result["status"] == "ok"
    assert not any(c[:2] == ["remote", "set-url"] for c in calls), (
        "an already-matching origin must not be rewritten"
    )


# ── integration: manual_fetch syncs origin before it fetches ────────────────

def test_manual_fetch_syncs_origin_before_fetching(monkeypatch):
    calls: list[list[str]] = []

    monkeypatch.setattr(
        svc, "_require_enabled_config",
        lambda pid: {"base_branch": "main", "username": None,
                     "repo_url": "https://new.example/repo.git"},
    )
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc, "src_root", lambda name, branch: Path("/base"))
    monkeypatch.setattr(svc, "_judge_base_slot", lambda root, branch: "checkout")
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_acquire_lock", lambda pid, holder: True)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(svc.db_git, "release_lock", lambda pid, holder: None)
    monkeypatch.setattr(svc, "_ref_exists", lambda repo, ref: False)
    monkeypatch.setattr(svc, "_dirty", lambda repo, include_untracked=True: False)
    monkeypatch.setattr(svc, "_base_ahead_behind", lambda root, branch: (0, 0))

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    svc.manual_fetch("proj")

    sync_idx = calls.index(
        ["remote", "set-url", "origin", "https://new.example/repo.git"]
    )
    fetch_idx = calls.index(["fetch", "origin"])
    assert sync_idx < fetch_idx, (
        f"origin must be synced BEFORE fetch runs, got call order {calls}"
    )


# ── integration: new group-worktree provisioning (B0001 §4) ─────────────────

def test_ensure_worktree_locked_syncs_origin_before_fetching(monkeypatch, tmp_path):
    """B0001 §4: 'a new group worktree can be created from a stale base' — this
    is the worktree.py fetch NR0003 §5.2/§8.1 names explicitly."""
    from modules.flow_gate.services.git.worktree import _ensure_worktree_locked

    calls: list[list[str]] = []
    base_root = tmp_path / "base"
    wt_path = tmp_path / "wt"
    cfg = {
        "base_branch": "main", "username": None,
        "repo_url": "https://new.example/repo.git",
    }

    monkeypatch.setattr(svc, "src_root", lambda name, branch: (
        base_root if branch == "main" else wt_path
    ))
    monkeypatch.setattr(svc, "resolve_group_work_base_ref", lambda *a, **k: "main")
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(
        svc, "_provision_base_locked",
        lambda cfg, project_id, project_name, trigger: {
            "status": "ok", "mode": "none", "reason": None,
            "snapshot_commit": None, "snapshot_at": None,
        },
    )
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: None)

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        if args[:1] == ["fetch"]:
            return _Proc(returncode=1, stderr="stop before real worktree add")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)
    fail_calls: list[tuple] = []
    monkeypatch.setattr(
        svc, "_fail_worktree",
        lambda project_id, group_id, branch, reason: fail_calls.append(
            (project_id, group_id, branch, reason)
        ),
    )

    result = _ensure_worktree_locked(
        cfg, "proj", "proj_name", "flowgate.default.0361", "flowgate_default_0361",
        trigger="test",
    )

    assert result == "failed"  # the stubbed fetch above intentionally fails
    assert fail_calls, "the failed fetch must still be reported"
    sync_idx = calls.index(
        ["remote", "set-url", "origin", "https://new.example/repo.git"]
    )
    fetch_idx = calls.index(["fetch", "origin"])
    assert sync_idx < fetch_idx, (
        f"origin must be synced BEFORE the new-worktree fetch runs, got {calls}"
    )


# ── the remaining NR0003 §12 D~H entry points (added on rejection rev0: these
# were the ones actually missing — see rejection at the top of this revision) ──
#
# D: update_from_base()               → finalize.py
# E: normal finalize's fetch/push     → finalize.py (merge action, real B remote)
# F: cross-branch merge fetch/push    → branches.py (real B remote)
# G: push-rejected re-review recovery → git_service.py::_refreeze_for_re_review
# H: push-only paths                  → finalize.py::manual_push,
#                                        worktree.py::_cleanup_group_slot retro-delete
#
# Each gets BOTH directions the rejection named explicitly: (1) a stale-A/
# configured-B condition proving the sync actually runs before the network
# command (call order for D/G/H; a real second bare remote's actual ref state
# for E/F, per the rejection's own "호출 순서(또는 실제 B remote 결과)"), and
# (2) a forced sync failure proving the network command is never reached.


# ── D: update_from_base() (NR0003 §12 test D) ───────────────────────────────

def test_update_from_base_syncs_origin_before_fetching(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    base_root = tmp_path / "base"
    wt_path = tmp_path / "wt"
    cfg = {"base_branch": "main", "repo_url": "https://new.example/repo.git"}
    state = {"branch": "flowgate_default_9001", "status": "waiting"}

    monkeypatch.setattr(
        svc, "_finalize_context",
        lambda group_id: (cfg, state, "proj", base_root, wt_path),
    )
    monkeypatch.setattr(svc.db_git, "get_open_session_by_group", lambda group_id: None)
    monkeypatch.setattr(svc, "guard_base_free", lambda project_id: None)
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_acquire_lock", lambda pid, holder: True)
    monkeypatch.setattr(svc.db_git, "release_lock", lambda pid, holder: None)
    monkeypatch.setattr(svc, "_dirty", lambda root, include_untracked=True: False)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        if args[:1] == ["fetch"]:
            # Stop right after the fetch this test cares about — the ff-only
            # merge/absorb/group-merge steps that follow need real git state
            # this unit test does not build.
            return _Proc(returncode=1, stderr="stop before merge --ff-only")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    with pytest.raises(GitServiceError):
        svc.update_from_base("flowgate.default.9001")

    sync_idx = calls.index(
        ["remote", "set-url", "origin", "https://new.example/repo.git"]
    )
    fetch_idx = calls.index(["fetch", "origin"])
    assert sync_idx < fetch_idx, (
        f"origin must be synced BEFORE update_from_base fetches, got {calls}"
    )


def test_update_from_base_sync_failure_stops_before_fetch(monkeypatch, tmp_path):
    base_root = tmp_path / "base"
    wt_path = tmp_path / "wt"
    cfg = {"base_branch": "main", "repo_url": "https://new.example/repo.git"}
    state = {"branch": "flowgate_default_9002", "status": "waiting"}

    monkeypatch.setattr(
        svc, "_finalize_context",
        lambda group_id: (cfg, state, "proj", base_root, wt_path),
    )
    monkeypatch.setattr(svc.db_git, "get_open_session_by_group", lambda group_id: None)
    monkeypatch.setattr(svc, "guard_base_free", lambda project_id: None)
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_acquire_lock", lambda pid, holder: True)
    monkeypatch.setattr(svc.db_git, "release_lock", lambda pid, holder: None)
    monkeypatch.setattr(svc, "_dirty", lambda root, include_untracked=True: False)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")

    calls: list[list[str]] = []

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    def boom(root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)

    with pytest.raises(GitServiceError) as caught:
        svc.update_from_base("flowgate.default.9002")
    assert caught.value.code == "git_error"
    assert not any(c[:1] == ["fetch"] for c in calls), (
        "fetch must not run when origin sync fails"
    )


# ── G: _refreeze_for_re_review(reason="push_rejected") (NR0003 §12 test G) ──

def test_refreeze_push_rejected_syncs_origin_before_fetching(monkeypatch):
    calls: list[list[str]] = []
    cfg = {"repo_url": "https://new.example/repo.git", "username": None}

    monkeypatch.setattr(svc, "_project_of_group", lambda group_id: "proj")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: cfg)
    monkeypatch.setattr(svc.db_git, "set_session_context", lambda merge_id, context: None)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        if args[:1] == ["fetch"]:
            # Stop here (reconciling) — this test only cares whether the sync
            # ran before this fetch, not the full redo-merge recovery below it.
            return _Proc(returncode=1, stderr="stop before merge --ff-only")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    result = svc._refreeze_for_re_review(
        "flowgate.default.9003", 1, Path("/base"), "main", {}, "push_rejected",
    )

    assert result["result"]["status"] == "reconciling"
    sync_idx = calls.index(
        ["remote", "set-url", "origin", "https://new.example/repo.git"]
    )
    fetch_idx = calls.index(["fetch", "origin"])
    assert sync_idx < fetch_idx, (
        f"origin must be synced BEFORE the push-rejected recovery fetch, got {calls}"
    )


def test_refreeze_push_rejected_sync_failure_stops_before_fetch(monkeypatch):
    cfg = {"repo_url": "https://new.example/repo.git", "username": None}

    monkeypatch.setattr(svc, "_project_of_group", lambda group_id: "proj")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: cfg)
    monkeypatch.setattr(svc.db_git, "set_session_context", lambda merge_id, context: None)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")

    calls: list[list[str]] = []

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    def boom(root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)

    with pytest.raises(GitServiceError) as caught:
        svc._refreeze_for_re_review(
            "flowgate.default.9004", 1, Path("/base"), "main", {}, "push_rejected",
        )
    assert caught.value.code == "git_error"
    assert not any(c[:1] == ["fetch"] for c in calls), (
        "the push-rejected recovery fetch must not run when origin sync fails"
    )


# ── H: push-only paths — manual_push() (NR0003 §12 test H) ─────────────────

def test_manual_push_syncs_origin_before_pushing(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    branch = "flowgate_default_9201"

    monkeypatch.setattr(
        svc, "_require_enabled_config",
        lambda pid: {"base_branch": "main", "username": None,
                     "repo_url": "https://new.example/repo.git"},
    )
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc.db_git, "list_states_of_project", lambda pid: [{"branch": branch}])
    monkeypatch.setattr(svc, "src_root", lambda name, br: tmp_path)
    monkeypatch.setattr(svc, "guard_base_free", lambda pid: None)
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_acquire_lock", lambda pid, holder: True)
    monkeypatch.setattr(svc.db_git, "release_lock", lambda pid, holder: None)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        if args[:1] == ["push"]:
            return _Proc(returncode=1, stderr="stop after push attempt")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    with pytest.raises(GitServiceError):
        svc.manual_push("proj", branch)

    sync_idx = calls.index(
        ["remote", "set-url", "origin", "https://new.example/repo.git"]
    )
    push_idx = calls.index(["push", "origin", branch])
    assert sync_idx < push_idx, (
        f"origin must be synced BEFORE manual_push pushes, got {calls}"
    )


def test_manual_push_sync_failure_stops_before_push(monkeypatch, tmp_path):
    branch = "flowgate_default_9202"

    monkeypatch.setattr(
        svc, "_require_enabled_config",
        lambda pid: {"base_branch": "main", "username": None,
                     "repo_url": "https://new.example/repo.git"},
    )
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc.db_git, "list_states_of_project", lambda pid: [{"branch": branch}])
    monkeypatch.setattr(svc, "src_root", lambda name, br: tmp_path)
    monkeypatch.setattr(svc, "guard_base_free", lambda pid: None)
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_acquire_lock", lambda pid, holder: True)
    monkeypatch.setattr(svc.db_git, "release_lock", lambda pid, holder: None)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")

    calls: list[list[str]] = []

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    def boom(cwd, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)

    with pytest.raises(GitServiceError) as caught:
        svc.manual_push("proj", branch)
    assert caught.value.code == "git_error"
    assert not any(c[:1] == ["push"] for c in calls), (
        "push must not run when origin sync fails"
    )


# ── H: push-only paths — _cleanup_group_slot retro-delete (NR0003 §12 test H) ──

def test_cleanup_group_slot_syncs_origin_before_retro_delete_push(monkeypatch, tmp_path):
    from modules.flow_gate.services.git.worktree import _cleanup_group_slot

    calls: list[list[str]] = []
    branch = "flowgate_default_9301"
    base_root = tmp_path / "base"
    wt_path = tmp_path / "wt"  # never created: exercises the "already gone" path

    monkeypatch.setattr(svc.db_git, "get_config", lambda pid: {
        "enabled": True, "base_branch": "main",
        "repo_url": "https://new.example/repo.git",
    })
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc.db_git, "get_state", lambda gid: {
        "worktree_registered": True, "status": "merged", "branch": branch,
    })
    monkeypatch.setattr(svc, "_is_group_disposed", lambda gid: False)
    monkeypatch.setattr(svc, "src_root", lambda name, br: base_root if br == "main" else wt_path)
    base_root.mkdir(parents=True)
    (base_root / ".git").mkdir()
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(svc.db_git, "unregister_worktree", lambda gid: None)

    def fake_ref_exists(root, ref):
        # No local work branch left (already deleted); only the pre-0172 leftover
        # origin ref this retro-delete targets.
        return ref == f"refs/remotes/origin/{branch}"

    monkeypatch.setattr(svc, "_ref_exists", fake_ref_exists)

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["remote", "get-url"]:
            return _Proc(returncode=0, stdout="https://old.example/repo.git")
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    result = _cleanup_group_slot("proj", "flowgate.default.9301")

    assert result is True
    sync_idx = calls.index(
        ["remote", "set-url", "origin", "https://new.example/repo.git"]
    )
    push_idx = calls.index(["push", "origin", "--delete", branch])
    assert sync_idx < push_idx, (
        f"origin must be synced BEFORE the retro-delete push, got {calls}"
    )


def test_cleanup_group_slot_retro_delete_sync_failure_skips_push_but_still_cleans_up(
    monkeypatch, tmp_path,
):
    from modules.flow_gate.services.git.worktree import _cleanup_group_slot

    branch = "flowgate_default_9302"
    base_root = tmp_path / "base"
    wt_path = tmp_path / "wt"

    monkeypatch.setattr(svc.db_git, "get_config", lambda pid: {
        "enabled": True, "base_branch": "main",
        "repo_url": "https://new.example/repo.git",
    })
    monkeypatch.setattr(svc, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(svc.db_git, "get_state", lambda gid: {
        "worktree_registered": True, "status": "merged", "branch": branch,
    })
    monkeypatch.setattr(svc, "_is_group_disposed", lambda gid: False)
    monkeypatch.setattr(svc, "src_root", lambda name, br: base_root if br == "main" else wt_path)
    base_root.mkdir(parents=True)
    (base_root / ".git").mkdir()
    monkeypatch.setattr(svc, "git_available", lambda: True)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(svc.db_git, "unregister_worktree", lambda gid: None)

    def fake_ref_exists(root, ref):
        return ref == f"refs/remotes/origin/{branch}"

    monkeypatch.setattr(svc, "_ref_exists", fake_ref_exists)

    calls: list[list[str]] = []

    def fake_run_git(args, **kwargs):
        calls.append(list(args))
        return _Proc(returncode=0)

    monkeypatch.setattr(svc, "_run_git", fake_run_git)

    def boom(root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)

    # _cleanup_group_slot()'s docstring contract: best-effort, never raises.
    result = _cleanup_group_slot("proj", "flowgate.default.9302")

    assert result is True, (
        "a sync failure on the best-effort retro-delete must not fail cleanup"
    )
    assert not any(c[:1] == ["push"] for c in calls), (
        "push must not run when origin sync fails"
    )


# ── F: cross-branch merge fetch/push, real second remote (NR0003 §12 test F) ──

@pytest.fixture
def dual_remote_branch_repo(tmp_path, monkeypatch):
    """test_git_branches_0594.py's own `repo` fixture, wired to TWO bare remotes:
    `origin` stays pointed at the stale one (`remote_a`) exactly as an existing
    checkout would after `save_config()` touched only the DB row (NR0003 §3);
    `_branch_context` reports `remote_b` as the current config's `repo_url`."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    remote_a = tmp_path / "remote_a.git"
    subprocess.run(["git", "init", "--bare", str(remote_a)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_a)], cwd=root, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=root, check=True, capture_output=True)
    # remote_b starts as an exact clone of remote_a — same history, so the merge
    # target's ff-only-to-remote check (if it ever finds a counterpart) still works.
    remote_b = tmp_path / "remote_b.git"
    subprocess.run(
        ["git", "clone", "--bare", str(remote_a), str(remote_b)],
        check=True, capture_output=True,
    )

    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(svc, "get_storage_root", lambda: storage)
    monkeypatch.setattr(
        branch_service, "_branch_context",
        lambda project_id: ({"enabled": 1, "repo_url": str(remote_b)}, root, "main"),
    )
    monkeypatch.setattr(svc, "_base_root_of", lambda project_id: root)
    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(svc, "_acquire_lock", lambda project_id, holder: True)
    monkeypatch.setattr(svc.db_git, "release_lock", lambda project_id, holder: None)
    monkeypatch.setattr(svc.db_git, "list_states_of_project", lambda project_id: [])
    monkeypatch.setattr(svc.db_git, "list_open_sessions", lambda: [])
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "list_open_groups_by_work_base", lambda project_id, ref: [])

    subprocess.run(["git", "branch", "develop"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=root, check=True)
    (root / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=root, check=True)

    return {"root": root, "remote_a": remote_a, "remote_b": remote_b}


def test_merge_branches_uses_reconfigured_origin_not_stale_remote(dual_remote_branch_repo):
    root = dual_remote_branch_repo["root"]
    remote_a = dual_remote_branch_repo["remote_a"]
    remote_b = dual_remote_branch_repo["remote_b"]
    assert _origin_url(root) == str(remote_a), "fixture precondition: origin starts stale"
    a_heads_before = _git(["ls-remote", "--heads", str(remote_a)])

    result = svc.merge_branches("flowgate", "feature", "develop")

    assert result["pushed"] is True
    assert _origin_url(root) == str(remote_b), (
        "merge_branches must sync origin to the reconfigured repo_url"
    )
    b_heads = _git(["ls-remote", "--heads", str(remote_b)])
    assert "refs/heads/develop" in b_heads, "the push must have landed on B"
    assert _git(["ls-remote", "--heads", str(remote_a)]) == a_heads_before, (
        "stale remote A must never receive the push"
    )


def test_merge_branches_sync_failure_stops_before_fetch_or_push(
    dual_remote_branch_repo, monkeypatch,
):
    root = dual_remote_branch_repo["root"]
    remote_a = dual_remote_branch_repo["remote_a"]
    remote_b = dual_remote_branch_repo["remote_b"]
    b_heads_before = _git(["ls-remote", "--heads", str(remote_b)])

    def boom(base_root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)

    with pytest.raises(GitServiceError) as caught:
        svc.merge_branches("flowgate", "feature", "develop")
    assert caught.value.code == "git_error"
    assert _origin_url(root) == str(remote_a), "a failed sync must leave origin untouched"
    assert _git(["ls-remote", "--heads", str(remote_b)]) == b_heads_before, (
        "push must not reach B when origin sync fails"
    )


# ── E: normal finalize's fetch/push, real second remote (NR0003 §12 test E) ──
#
# The full ensure_worktree()/finalize() harness, mirroring TestGitEndToEnd in
# test_git_integration_0115.py (its own module docstring: each importing test
# file gets a fresh module-scoped patch_store/tmp_db instance).

@pytest.fixture(scope="module")
def sync_project(patch_store, tmp_db):
    from modules.flow_gate.db import projects

    if projects.get_by_id("originsyncprj") is None:
        projects.create({"project_id": "originsyncprj", "project_name": "OriginSyncProj"})
    yield


@pytest.fixture(scope="module")
def dual_origin(sync_project):
    from modules.flow_gate.services import git_service as gsvc

    tmp = Path(tempfile.mkdtemp(prefix="fg-origin-sync-e2e-"))
    remote_a = tmp / "remote_a.git"
    remote_b = tmp / "remote_b.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(remote_a)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(remote_a)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)
    # remote_b starts as an exact clone of remote_a so this finalize's own
    # ff-only-to-origin/main step (if it finds a counterpart) succeeds cleanly.
    _git(["clone", "--bare", str(remote_a), str(remote_b)])

    gsvc.save_config("originsyncprj", {
        "repo_url": remote_a.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"remote_a": remote_a, "remote_b": remote_b, "tmp": tmp}
    gsvc.delete_config("originsyncprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
def test_finalize_merge_uses_reconfigured_origin_not_stale_remote(dual_origin):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as gsvc
    from modules.flow_gate.storage.paths import src_root

    remote_a, remote_b = dual_origin["remote_a"], dual_origin["remote_b"]
    group = "originsyncprj.default.9101"
    assert gsvc.ensure_worktree("originsyncprj", "default", group) == "ok"
    base_root = src_root("OriginSyncProj", "main")
    # Re-establish the stale-A/configured-B precondition fresh, regardless of
    # what an earlier test in this module already synced.
    _git(["remote", "set-url", "origin", remote_a.as_uri()], cwd=base_root)
    gsvc.save_config("originsyncprj", {"repo_url": remote_b.as_uri(), "enabled": True})
    assert _origin_url(base_root) == remote_a.as_uri(), (
        "save_config() alone must not touch the existing checkout's origin"
    )
    a_head_before = _git(["rev-parse", "main"], cwd=remote_a).strip()

    wt = src_root("OriginSyncProj", "originsyncprj_default_9101")
    (wt / "feature.txt").write_text("branch work\n", encoding="utf-8")
    _seed_wf_done_root(group, project_id="originsyncprj")
    db_git.set_status(group, "awaiting_choice")

    out = gsvc.finalize(group, "merge")
    assert out["result"]["status"] == "merged"
    assert out["result"]["pushed"] is True

    assert _origin_url(base_root) == remote_b.as_uri(), (
        "finalize's merge/push must sync origin to the reconfigured repo_url"
    )
    base_head = _git(["rev-parse", "main"], cwd=base_root).strip()
    assert _git(["rev-parse", "main"], cwd=remote_b).strip() == base_head, (
        "the merge push must have landed on the reconfigured remote B"
    )
    assert _git(["rev-parse", "main"], cwd=remote_a).strip() == a_head_before, (
        "stale remote A must be untouched by this finalize"
    )


@needs_git
def test_finalize_merge_sync_failure_stops_before_fetch_or_push(dual_origin, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as gsvc
    from modules.flow_gate.storage.paths import src_root

    remote_a, remote_b = dual_origin["remote_a"], dual_origin["remote_b"]
    group = "originsyncprj.default.9102"
    assert gsvc.ensure_worktree("originsyncprj", "default", group) == "ok"
    base_root = src_root("OriginSyncProj", "main")
    _git(["remote", "set-url", "origin", remote_a.as_uri()], cwd=base_root)
    gsvc.save_config("originsyncprj", {"repo_url": remote_b.as_uri(), "enabled": True})

    wt = src_root("OriginSyncProj", "originsyncprj_default_9102")
    (wt / "feature.txt").write_text("branch work\n", encoding="utf-8")
    _seed_wf_done_root(group, project_id="originsyncprj")
    db_git.set_status(group, "awaiting_choice")

    b_head_before = _git(["rev-parse", "main"], cwd=remote_b).strip()

    def boom(root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(gsvc, "ensure_origin_matches_config", boom)

    with pytest.raises(gsvc.GitServiceError) as caught:
        gsvc.finalize(group, "merge")
    assert caught.value.code == "git_error"
    assert _origin_url(base_root) == remote_a.as_uri(), (
        "a failed sync must leave origin untouched"
    )
    assert _git(["rev-parse", "main"], cwd=remote_b).strip() == b_head_before, (
        "push must never reach B when origin sync fails"
    )


# ── I: merge-review approval's conditional push (rejection rev1 gap) ────────
#
# `approve_merge_review()`'s own push — issued from `_conditionally_push_or_
# reconcile()` via `git push --force-with-lease=... origin <base_branch>` —
# never appeared in NR0003 §5's own file-by-file grep of git_service.py
# (which only found the `_refreeze_for_re_review` fetch at line 2671/2674);
# it is a push-only entry point with no preceding fetch, exactly the shape
# §12 test H describes, and §12 test J's sync-failure policy (no fetch/push,
# no worktree/index/HEAD change, clear error) applies identically. These
# tests drive the helper directly against real bare remotes, mirroring the
# cross-branch-merge fixture above, rather than re-deriving the full merge-
# session/fingerprint/commit machinery `approve_merge_review()` needs before
# it ever reaches this call — `_complete_merge_review()` (itself unrelated to
# origin sync: approval-intent bookkeeping, slot cleanup, event emission) is
# stubbed out exactly as production leaves it, called only with the outcome.

@pytest.fixture
def dual_remote_merge_review_repo(tmp_path, monkeypatch):
    """A real base checkout carrying a local commit not yet on either remote —
    the state `approve_merge_review()` is in right after `context["merge_commit"]
    = commit_sha` and right before it calls `_conditionally_push_or_reconcile`.
    `origin` stays stale at `remote_a` exactly as an existing checkout's would
    after `save_config()` touched only the DB row (NR0003 §3)."""
    root = tmp_path / "base"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    remote_a = tmp_path / "remote_a.git"
    subprocess.run(["git", "init", "--bare", str(remote_a)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_a)], cwd=root, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=root, check=True, capture_output=True)
    # remote_b starts as an exact clone of remote_a, so the lease this push
    # carries (built from the locally cached origin/main, since no
    # `expected_remote_head` CAS value is set in these tests) still matches
    # B's real current tip.
    remote_b = tmp_path / "remote_b.git"
    subprocess.run(
        ["git", "clone", "--bare", str(remote_a), str(remote_b)],
        check=True, capture_output=True,
    )
    # The local "merge commit" approve_merge_review() already created before
    # ever calling _conditionally_push_or_reconcile.
    (root / "merged.txt").write_text("merge result\n", encoding="utf-8")
    subprocess.run(["git", "add", "merged.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "merge"], cwd=root, check=True, capture_output=True)

    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    return {"root": root, "remote_a": remote_a, "remote_b": remote_b}


def test_conditionally_push_or_reconcile_uses_reconfigured_origin_not_stale_remote(
    dual_remote_merge_review_repo, monkeypatch,
):
    root = dual_remote_merge_review_repo["root"]
    remote_a = dual_remote_merge_review_repo["remote_a"]
    remote_b = dual_remote_merge_review_repo["remote_b"]
    assert _origin_url(root) == str(remote_a), "fixture precondition: origin starts stale"
    a_head_before = _git(["rev-parse", "main"], cwd=remote_a).strip()

    completed = []
    monkeypatch.setattr(
        svc, "_complete_merge_review",
        lambda group_id, merge_id, project_id, context, *, pushed: (
            completed.append(pushed)
            or {"ok": True, "result": {"status": "merged", "pushed": pushed}}
        ),
    )

    cfg = {"repo_url": str(remote_b), "username": None}
    session = {"finalize_action": "merge"}
    result = svc._conditionally_push_or_reconcile(
        "flowgate.default.9401", 1, session, "proj", root, cfg, "main", {},
    )

    assert completed == [True], "the review must complete with pushed=True"
    assert result["result"]["pushed"] is True
    assert _origin_url(root) == str(remote_b), (
        "the conditional push must sync origin to the reconfigured repo_url before pushing"
    )
    local_head = _git(["rev-parse", "main"], cwd=root).strip()
    assert _git(["rev-parse", "main"], cwd=remote_b).strip() == local_head, (
        "the approved merge commit must land on the reconfigured remote B"
    )
    assert _git(["rev-parse", "main"], cwd=remote_a).strip() == a_head_before, (
        "stale remote A must never receive the approved merge push"
    )


def test_conditionally_push_or_reconcile_sync_failure_stops_before_push(
    dual_remote_merge_review_repo, monkeypatch,
):
    root = dual_remote_merge_review_repo["root"]
    remote_a = dual_remote_merge_review_repo["remote_a"]
    remote_b = dual_remote_merge_review_repo["remote_b"]
    b_head_before = _git(["rev-parse", "main"], cwd=remote_b).strip()

    def boom(base_root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)
    monkeypatch.setattr(
        svc, "_complete_merge_review",
        lambda *a, **k: pytest.fail("the review must not complete when the push is skipped"),
    )

    cfg = {"repo_url": str(remote_b), "username": None}
    session = {"finalize_action": "merge"}
    with pytest.raises(GitServiceError) as caught:
        svc._conditionally_push_or_reconcile(
            "flowgate.default.9402", 1, session, "proj", root, cfg, "main", {},
        )
    assert caught.value.code == "git_error"
    assert _origin_url(root) == str(remote_a), "a failed sync must leave origin untouched"
    assert _git(["rev-parse", "main"], cwd=remote_b).strip() == b_head_before, (
        "push must never reach B when origin sync fails"
    )


# ── J/K: reconcile's and crash-recovery's own `ls-remote origin` reads
# (rejection rev3 gap — these two never went through the shared helper at all)
#
# `_query_remote_ref()` (reconcile_push_session) and `_query_target_remote()`
# (interrupted finalize attempt crash/startup recovery) each re-read the
# current config's `cfg` before querying, but the `ls-remote origin` command
# itself ran straight against the checkout's actual `origin` remote with no
# preceding sync — the ONE thing every other network Git entry point in this
# module already does. A stuck `reconciling` session, or a process that died
# mid finalize, can sit long enough for the operator to repoint `repo_url` at
# a different remote before either of these reads runs; reading stale
# `origin` here silently governs approval-completion, rollback, and manual-
# reconciliation decisions on the WRONG remote's data (see each production
# docstring above). `remote_a`/`remote_b` are given DIVERGENT `main` tips
# below (not just different remote objects with identical content) so a read
# landing on the wrong one is caught by SHA, not merely by URL.

@pytest.fixture
def dual_remote_ref_repo(tmp_path, monkeypatch):
    """A real base checkout whose `origin` stays stale at `remote_a` exactly as
    an existing checkout's would after `save_config()` touched only the DB row
    (NR0003 §3). `remote_b` is pushed one commit AHEAD of `remote_a` so their
    `main` tips are distinguishable SHAs, not just distinguishable URLs."""
    root = tmp_path / "base"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
    remote_a = tmp_path / "remote_a.git"
    subprocess.run(["git", "init", "--bare", str(remote_a)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_a)], cwd=root, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=root, check=True, capture_output=True)
    remote_b = tmp_path / "remote_b.git"
    subprocess.run(
        ["git", "clone", "--bare", str(remote_a), str(remote_b)],
        check=True, capture_output=True,
    )
    # Diverge B from A via a throwaway clone, so A's and B's `main` land on
    # DIFFERENT SHAs and a stale-origin read is distinguishable from a
    # correct one, not just "same value, harmlessly".
    b_work = tmp_path / "b_work"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote_b), str(b_work)],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=b_work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=b_work, check=True)
    (b_work / "b_only.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(["git", "add", "b_only.txt"], cwd=b_work, check=True)
    subprocess.run(
        ["git", "commit", "-m", "b diverges"], cwd=b_work, check=True, capture_output=True,
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=b_work, check=True, capture_output=True)

    monkeypatch.setattr(svc, "_load_secret_for", lambda cfg: "")
    return {"root": root, "remote_a": remote_a, "remote_b": remote_b}


def test_query_remote_ref_uses_reconfigured_origin_not_stale_remote(dual_remote_ref_repo):
    root = dual_remote_ref_repo["root"]
    remote_a = dual_remote_ref_repo["remote_a"]
    remote_b = dual_remote_ref_repo["remote_b"]
    assert _origin_url(root) == str(remote_a), "fixture precondition: origin starts stale"
    sha_b = _git(["rev-parse", "main"], cwd=remote_b).strip()

    cfg = {"repo_url": str(remote_b), "username": None}
    observed = svc._query_remote_ref(root, cfg, "main")

    assert observed == sha_b, (
        "reconcile_push_session's remote-ref read must observe the reconfigured "
        "remote B, not whatever origin pointed at when the base checkout was "
        "provisioned"
    )
    assert _origin_url(root) == str(remote_b), (
        "the ls-remote read must sync origin to the reconfigured repo_url first"
    )


def test_query_remote_ref_sync_failure_returns_none_without_ls_remote(
    dual_remote_ref_repo, monkeypatch,
):
    root = dual_remote_ref_repo["root"]
    remote_a = dual_remote_ref_repo["remote_a"]
    remote_b = dual_remote_ref_repo["remote_b"]

    def boom(base_root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)

    calls: list[list[str]] = []
    real_run_git = svc._run_git

    def tracking_run_git(args, **kwargs):
        calls.append(list(args))
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", tracking_run_git)

    cfg = {"repo_url": str(remote_b), "username": None}
    observed = svc._query_remote_ref(root, cfg, "main")

    assert observed is None, (
        "a sync failure must fold into the existing best-effort None contract, "
        "not surface as an unhandled exception mid-reconciliation"
    )
    assert not any(c[:1] == ["ls-remote"] for c in calls), (
        "ls-remote must never run against a possibly-stale origin when sync fails"
    )
    assert _origin_url(root) == str(remote_a), "a failed sync must leave origin untouched"


def test_query_target_remote_uses_reconfigured_origin_not_stale_remote(
    dual_remote_ref_repo, monkeypatch,
):
    root = dual_remote_ref_repo["root"]
    remote_a = dual_remote_ref_repo["remote_a"]
    remote_b = dual_remote_ref_repo["remote_b"]
    assert _origin_url(root) == str(remote_a), "fixture precondition: origin starts stale"
    sha_b = _git(["rev-parse", "main"], cwd=remote_b).strip()

    monkeypatch.setattr(
        svc.db_git, "get_config",
        lambda project_id: {"repo_url": str(remote_b), "username": None},
    )
    ctx = merge_target.MergeTargetContext(
        project_id="originsyncprj", base_branch="main", target_branch="main",
        is_project_base=True, root=root, merge_id=1,
    )

    reachable, observed = merge_target._query_target_remote(ctx)

    assert reachable is True
    assert observed == sha_b, (
        "interrupted-attempt recovery's target-remote read must observe the "
        "reconfigured remote B, not whatever origin pointed at when the base "
        "checkout was provisioned"
    )
    assert _origin_url(root) == str(remote_b), (
        "the ls-remote read must sync origin to the reconfigured repo_url first"
    )


def test_query_target_remote_sync_failure_returns_unreachable_without_ls_remote(
    dual_remote_ref_repo, monkeypatch,
):
    root = dual_remote_ref_repo["root"]
    remote_a = dual_remote_ref_repo["remote_a"]
    remote_b = dual_remote_ref_repo["remote_b"]

    monkeypatch.setattr(
        svc.db_git, "get_config",
        lambda project_id: {"repo_url": str(remote_b), "username": None},
    )

    def boom(base_root, repo_url):
        raise GitServiceError(500, "git_error", "forced sync failure")

    monkeypatch.setattr(svc, "ensure_origin_matches_config", boom)

    calls: list[list[str]] = []
    real_run_git = svc._run_git

    def tracking_run_git(args, **kwargs):
        calls.append(list(args))
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", tracking_run_git)

    ctx = merge_target.MergeTargetContext(
        project_id="originsyncprj", base_branch="main", target_branch="main",
        is_project_base=True, root=root, merge_id=1,
    )

    reachable, observed = merge_target._query_target_remote(ctx)

    assert (reachable, observed) == (False, None), (
        "a sync failure must be treated the same as an unreachable remote, not "
        "surface as an unhandled exception mid-recovery"
    )
    assert not any(c[:1] == ["ls-remote"] for c in calls), (
        "ls-remote must never run against a possibly-stale origin when sync fails"
    )
    assert _origin_url(root) == str(remote_a), "a failed sync must leave origin untouched"
