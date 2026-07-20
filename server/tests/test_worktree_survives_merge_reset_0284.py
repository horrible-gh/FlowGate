"""flowgate.default.0284 — the runner keeps targeting the group branch worktree
after the ledger's registration flag is cleared (merge/push cleanup).

B0001 kept reporting fix-verification failures because a merge released the group
worktree (``worktree_registered=0``) and every re-run then silently dropped to the
base(main) tree — a tree WITHOUT the fix under test (NR0003 §4). 0284 T0005 makes the
on-disk branch worktree authoritative over the cleared flag: ``resolve_project_src_root``
recovers it while its directory survives, and only a genuinely pruned directory falls
back. ``effective_src_root_ex`` is deliberately left untouched so the raw ledger reason
stays visible for the TSR observability added in 0280.
"""
from __future__ import annotations

PROJECT = "flowgate"
GROUP = "flowgate.default.0284"
NAME = "FlowGate Live"
BRANCH = "fg-0284"


class _FakeGitDb:
    def __init__(self, cfg, state):
        self._cfg, self._state = cfg, state

    def get_config(self, _project_id):
        return self._cfg

    def get_state(self, _group_id):
        return self._state


def _install(monkeypatch, *, cfg, state, project_name=NAME):
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(git_service, "db_git", _FakeGitDb(cfg, state))
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: project_name)


def _make_worktree(tmp_path):
    wt = tmp_path / "src" / NAME / BRANCH
    wt.mkdir(parents=True)
    return wt


def test_recovers_on_disk_worktree_when_registration_cleared(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    from modules.flow_gate.services import git_service
    from modules.flow_gate.storage import paths as storage_paths

    wt = _make_worktree(tmp_path)
    _install(
        monkeypatch,
        cfg={"enabled": 1},
        state={"worktree_registered": 0, "branch": BRANCH, "status": "merged"},
    )

    # The low-level ledger call still reports the fallback reason honestly...
    assert git_service.effective_src_root_ex(PROJECT, GROUP) == (
        None,
        "worktree_unregistered",
    )
    # ...but the resolver the runner uses recovers the surviving worktree.
    assert storage_paths._group_worktree_on_disk(PROJECT, GROUP) == wt.resolve()
    assert (
        storage_paths.resolve_project_src_root(PROJECT, "main", group_id=GROUP)
        == wt.resolve()
    )
    # And a run there is classified by the tree it used, not the stale flag.
    assert storage_paths.classify_src_root(PROJECT, GROUP, wt) == "worktree"


def test_fallback_is_bounded_to_a_pruned_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    from modules.flow_gate.storage import paths as storage_paths

    # No worktree directory on disk (the slot was pruned) → nothing to recover.
    _install(
        monkeypatch,
        cfg={"enabled": 1},
        state={"worktree_registered": 0, "branch": BRANCH, "status": "merged"},
    )

    wt = storage_paths.src_root(NAME, BRANCH)
    assert not wt.is_dir()
    assert storage_paths._group_worktree_on_disk(PROJECT, GROUP) is None
    # classify reports the honest fallback reason when the tree is genuinely gone.
    assert storage_paths.classify_src_root(PROJECT, GROUP, wt) == "worktree_unregistered"


def test_recovery_requires_git_integration_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    from modules.flow_gate.storage import paths as storage_paths

    _make_worktree(tmp_path)  # the directory exists...
    _install(
        monkeypatch,
        cfg={"enabled": 0},  # ...but git integration is off for this project
        state={"worktree_registered": 0, "branch": BRANCH},
    )

    # A disabled project must not adopt a stray worktree directory.
    assert storage_paths._group_worktree_on_disk(PROJECT, GROUP) is None


def test_registered_worktree_is_unaffected(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    from modules.flow_gate.storage import paths as storage_paths

    wt = _make_worktree(tmp_path)
    _install(
        monkeypatch,
        cfg={"enabled": 1},
        state={"worktree_registered": 1, "branch": BRANCH},
    )

    # Normal path: effective_src_root already returns the worktree; recovery is moot.
    assert (
        storage_paths.resolve_project_src_root(PROJECT, "main", group_id=GROUP)
        == wt.resolve()
    )
    assert storage_paths.classify_src_root(PROJECT, GROUP, wt) == "worktree"
