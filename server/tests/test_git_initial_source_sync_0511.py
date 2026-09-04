"""Initial AI source-access worktree sync — git mechanics (flowgate.default.0511 T0004).

git_service.ensure_initial_group_source_sync() is the ONE forced reset+clean of a
group worktree to the current configured base HEAD, performed exactly once per
group. These tests drive real git repositories (a real `git worktree add`, so the
worktree shares the base repo's object store exactly like production) and a real
sqlite-migration-backed store, so the reset/clean/verify/marker-persist sequence
is proven against actual git behavior rather than mocked expectations.

The trigger judgement itself (raw tool_registry kind) lives in ai_invoke_service
and is covered by test_ai_invoke_initial_source_sync_0511.py — this file only
proves what happens once that judgement has already said "sync".
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from modules.flow_gate.services import git_service


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
    )


def _base_repo(tmp_path: Path) -> Path:
    base = tmp_path / "base"
    base.mkdir()
    _git(base, "init", "-b", "main")
    _git(base, "config", "user.name", "FlowGate Test")
    _git(base, "config", "user.email", "flowgate@example.invalid")
    (base / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(base, "add", ".")
    _git(base, "commit", "-m", "seed")
    return base


class _Fixture:
    """A base repo + a REAL linked worktree (git worktree add), forked before the
    base advances — the exact stale-worktree shape T0004 exists to fix."""

    def __init__(self, tmp_path: Path):
        self.base = _base_repo(tmp_path)
        _git(self.base, "branch", "group/test")
        self.wt = tmp_path / "wt"
        _git(self.base, "worktree", "add", str(self.wt), "group/test")
        # Base advances AFTER the worktree forked — the staleness T0004 closes.
        (self.base / "advance.txt").write_text("advance\n", encoding="utf-8")
        _git(self.base, "add", ".")
        _git(self.base, "commit", "-m", "base advances")
        self.base_head = _git(self.base, "rev-parse", "HEAD").stdout.strip()
        self.fork_head = _git(self.wt, "rev-parse", "HEAD").stdout.strip()
        assert self.base_head != self.fork_head


def _wire(monkeypatch, fx: _Fixture, *, enabled=True, base_branch="main",
          worktree_registered=1, initial_sync_at=None, project_name="proj"):
    monkeypatch.setattr(
        git_service.db_git, "get_config",
        lambda _pid: (
            {"enabled": enabled, "base_branch": base_branch} if enabled is not None else None
        ),
    )
    monkeypatch.setattr(
        git_service.db_git, "get_state",
        lambda _gid: {
            "branch": "group/test",
            "worktree_registered": worktree_registered,
            "initial_source_sync_at": initial_sync_at,
        },
    )
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: project_name)
    monkeypatch.setattr(
        git_service, "src_root",
        lambda name, br: fx.base if br == base_branch else fx.wt,
    )
    monkeypatch.setattr(git_service, "_acquire_lock", lambda _project, _holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda _project, _holder: None)
    monkeypatch.setattr(git_service.db_tr_ledger, "commit_rows_by_group", lambda _gid: [])


def test_git_disabled_is_a_no_op(monkeypatch, tmp_path):
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx, enabled=False)
    result = git_service.ensure_initial_group_source_sync("p", "default", "g")
    assert result == {"performed": False, "reason": "git_disabled", "sha": None}
    # Nothing touched the worktree.
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.fork_head


def test_first_sync_resets_and_cleans_to_base_head(monkeypatch, tmp_path):
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)
    # Simulate pre-sync AI/TR debris in the stale worktree: a local tracked commit
    # and an untracked file — both must be discarded (T0004 §24, §35.7/§35.8).
    (fx.wt / "seed.txt").write_text("tracked edit\n", encoding="utf-8")
    _git(fx.wt, "commit", "-am", "local tracked edit")
    (fx.wt / "untracked.txt").write_text("debris\n", encoding="utf-8")

    persisted = {}
    monkeypatch.setattr(
        git_service.db_git, "set_initial_source_sync",
        lambda gid, sha: persisted.update(group_id=gid, sha=sha),
    )

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result == {"performed": True, "reason": "ok", "sha": fx.base_head}
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.base_head
    assert (fx.wt / "advance.txt").exists()          # base's new file is now present
    assert not (fx.wt / "untracked.txt").exists()     # untracked debris discarded
    assert (fx.wt / "seed.txt").read_text(encoding="utf-8") == "seed\n"  # tracked edit discarded
    assert persisted == {"group_id": "g", "sha": fx.base_head}


def test_configured_base_branch_is_honored_not_hardcoded_main(monkeypatch, tmp_path):
    fx = _Fixture(tmp_path)
    # Rename the base checkout's branch so "main" would resolve to nothing —
    # proves base_branch_for()/cfg.base_branch drives resolution, not a literal.
    _git(fx.base, "branch", "-m", "main", "trunk")
    monkeypatch.setattr(
        git_service.db_git, "set_initial_source_sync", lambda gid, sha: None,
    )
    _wire(monkeypatch, fx, base_branch="trunk")

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result["performed"] is True
    assert result["sha"] == fx.base_head
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.base_head


def test_ignored_files_are_not_forced_out(monkeypatch, tmp_path):
    """T0004 §25: `clean -fd`, never `-x` — ignored files are out of scope."""
    fx = _Fixture(tmp_path)
    (fx.wt / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    (fx.wt / "keep.ignored").write_text("do not touch\n", encoding="utf-8")
    monkeypatch.setattr(git_service.db_git, "set_initial_source_sync", lambda gid, sha: None)
    _wire(monkeypatch, fx)

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result["performed"] is True
    assert (fx.wt / "keep.ignored").exists()


def test_exactly_once_marker_precheck_skips_a_second_sync(monkeypatch, tmp_path):
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx, initial_sync_at="2026-09-04T00:00:00+09:00")
    called = []
    monkeypatch.setattr(
        git_service.db_git, "set_initial_source_sync",
        lambda gid, sha: called.append((gid, sha)),
    )

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result == {"performed": False, "reason": "already_synced", "sha": None}
    assert called == []
    # The worktree was never touched — still at its original fork point.
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.fork_head


def test_retry_after_transient_marker_failure_does_not_double_reset_in_a_way_that_breaks(
    monkeypatch, tmp_path,
):
    """T0004 §29/§35.11: sync+marker succeed, so a later retry sees the marker and
    skips. This proves the OTHER shape too — a first attempt whose marker write
    failed leaves the marker absent, so a retry legitimately redoes the (harmless,
    same-sha) reset and this time persists successfully."""
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)
    calls = {"n": 0}

    def _flaky_persist(gid, sha):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient db failure")

    monkeypatch.setattr(git_service.db_git, "set_initial_source_sync", _flaky_persist)

    first = git_service.ensure_initial_group_source_sync("p", "default", "g")
    assert first["reason"] == "marker_persist_failed"

    second = git_service.ensure_initial_group_source_sync("p", "default", "g")
    assert second == {"performed": True, "reason": "ok", "sha": fx.base_head}
    assert calls["n"] == 2


def test_worktree_missing_blocks_rather_than_resets(monkeypatch, tmp_path):
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx, worktree_registered=0)
    result = git_service.ensure_initial_group_source_sync("p", "default", "g")
    assert result["performed"] is False
    assert result["reason"] == "worktree_missing"


def test_git_busy_does_not_reset(monkeypatch, tmp_path):
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda _project, _holder: False)
    result = git_service.ensure_initial_group_source_sync("p", "default", "g")
    assert result == {"performed": False, "reason": "git_busy", "sha": None}
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.fork_head


def test_marker_persist_failure_still_reports_the_reset(monkeypatch, tmp_path):
    """T0004 §28: a marker write failure must be visible to the caller (which then
    blocks the run) even though the destructive reset itself already happened —
    the caller, not this function, decides whether that is safe to retry."""
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)

    def _boom(_gid, _sha):
        raise RuntimeError("db down")

    monkeypatch.setattr(git_service.db_git, "set_initial_source_sync", _boom)

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result == {"performed": False, "reason": "marker_persist_failed", "sha": None}
    # The git-level work still landed — only the marker failed to persist.
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.base_head


def test_head_mismatch_blocks_the_marker(monkeypatch, tmp_path):
    """T0004 §26: if HEAD verification cannot confirm the reset landed, the
    marker must never be written."""
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)
    persisted = []
    monkeypatch.setattr(
        git_service.db_git, "set_initial_source_sync",
        lambda gid, sha: persisted.append((gid, sha)),
    )
    real_run_git = git_service._run_git

    def _tampered_run_git(args, *, cwd=None, **kwargs):
        proc = real_run_git(args, cwd=cwd, **kwargs)
        if list(args) == ["rev-parse", "HEAD"] and cwd == fx.wt:
            proc = subprocess.CompletedProcess(proc.args, 0, "0" * 40, "")
        return proc

    monkeypatch.setattr(git_service, "_run_git", _tampered_run_git)

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result == {"performed": False, "reason": "head_mismatch", "sha": None}
    assert persisted == []


def test_legacy_source_history_skips_destructive_reset_and_backfills(monkeypatch, tmp_path):
    """T0004 §16-19/§24-25: a tr_commit_ledger row is trustworthy evidence of prior
    real source work — the reset must be skipped and the marker backfilled from
    the worktree's OWN current HEAD, never the base's."""
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)
    monkeypatch.setattr(
        git_service.db_tr_ledger, "commit_rows_by_group",
        lambda _gid: [{"id": 1, "doc_id": "flowgate.default.0001.0004-TR", "state": "live"}],
    )
    persisted = {}
    monkeypatch.setattr(
        git_service.db_git, "set_initial_source_sync",
        lambda gid, sha: persisted.update(group_id=gid, sha=sha),
    )

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result == {
        "performed": False, "reason": "legacy_source_history", "sha": fx.fork_head,
    }
    # The worktree is untouched — its own (pre-existing) HEAD is what got backfilled.
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.fork_head
    assert persisted == {"group_id": "g", "sha": fx.fork_head}


def test_legacy_history_lookup_failure_is_read_as_ambiguous_and_skips_reset(monkeypatch, tmp_path):
    """T0004 §19: an unclear signal must never be treated as license to reset."""
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)

    def _boom(_gid):
        raise RuntimeError("ledger query failed")

    monkeypatch.setattr(git_service.db_tr_ledger, "commit_rows_by_group", _boom)
    monkeypatch.setattr(git_service.db_git, "set_initial_source_sync", lambda gid, sha: None)

    result = git_service.ensure_initial_group_source_sync("p", "default", "g")

    assert result["performed"] is False
    assert result["reason"] == "legacy_source_history"
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.fork_head


def test_config_lookup_failure_never_raises_and_never_resets(monkeypatch, tmp_path):
    """Same contract as ai_invoke_service._require_group_worktree's own get_config
    try/except: an unreadable config must never block a run (T0004 introduces no
    new fragility here)."""
    fx = _Fixture(tmp_path)

    def _boom(_pid):
        raise RuntimeError("config lookup exploded")

    monkeypatch.setattr(git_service.db_git, "get_config", _boom)
    result = git_service.ensure_initial_group_source_sync("p", "default", "g")
    assert result == {"performed": False, "reason": "config_lookup_failed", "sha": None}


def test_outer_exception_after_config_never_raises_and_never_resets(monkeypatch, tmp_path):
    """A failure AFTER config is confirmed enabled (i.e. once a sync was already
    judged necessary) is the genuine "error" catch-all -- still never raises, and
    still never touches the worktree."""
    fx = _Fixture(tmp_path)
    _wire(monkeypatch, fx)

    def _boom(_gid):
        raise RuntimeError("state lookup exploded")

    monkeypatch.setattr(git_service.db_git, "get_state", _boom)
    result = git_service.ensure_initial_group_source_sync("p", "default", "g")
    assert result == {"performed": False, "reason": "error", "sha": None}
    assert _git(fx.wt, "rev-parse", "HEAD").stdout.strip() == fx.fork_head
