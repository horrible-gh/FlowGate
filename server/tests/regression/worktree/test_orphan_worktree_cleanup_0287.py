"""flowgate.default.0287 — an interrupted worktree teardown must not become permanent.

NR0004: `git worktree remove --force` ran under the 30 s local budget against a
full checkout on an SMB share, got killed mid-delete, and left the directory
standing with its `.git` link already gone. From then on every retry hit
``_cleanup_group_slot``'s two-state branch — "directory exists ⇒ healthy worktree"
— where git rejects the path forever ("is not a working tree" / "validation
failed … '.git' does not exist"), and the bare ``return False`` skipped the branch
delete AND the ledger unregister. Three groups were stuck that way.

The same ``is_dir()`` assumption also lived in the source-root resolvers, so the
corpse was handed to the test runner as the authoritative tree: a checkout holding
its test files but not the modules under test.

These tests pin both halves — the cleanup now finishes the job, and no resolver
accepts a directory without its `.git` link.
"""
from __future__ import annotations

import subprocess

import pytest

PROJECT = "flowgate"
GROUP = "flowgate.default.0287"
NAME = "FlowGate Live"
BRANCH = "fg-0287"
BASE = "main"


# ── doubles ──────────────────────────────────────────────────────────────────

class _FakeGitDb:
    def __init__(self, cfg, state):
        self._cfg, self._state = cfg, state
        self.unregistered: list[str] = []

    def get_config(self, _project_id):
        return self._cfg

    def get_state(self, _group_id):
        return dict(self._state) if self._state is not None else None

    def unregister_worktree(self, group_id):
        self.unregistered.append(group_id)
        self._state["worktree_registered"] = 0


def _completed(args, rc=0, out="", err=""):
    return subprocess.CompletedProcess(args, rc, out, err)


class _GitRecorder:
    """Stands in for ``_run_git``; scripted per git subcommand."""

    def __init__(self, *, listed=(), remove_rc=0, remove_err="", on_remove=None):
        self.listed = list(listed)
        self.remove_rc = remove_rc
        self.remove_err = remove_err
        self.on_remove = on_remove
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        self.kwargs = kwargs
        if args[:2] == ["worktree", "list"]:
            body = "".join(f"worktree {p}\n{extra}\n" for p, extra in self.listed)
            return _completed(args, 0, body)
        if args[:2] == ["worktree", "remove"]:
            if self.on_remove is not None:
                self.on_remove()
            return _completed(args, self.remove_rc, "", self.remove_err)
        if args[:1] == ["show-ref"]:
            return _completed(args, 1)  # no refs → skip the branch-delete arm
        return _completed(args, 0)

    def ran(self, *prefix) -> bool:
        return any(c[: len(prefix)] == list(prefix) for c in self.calls)


def _mk_tree(root, *, linked: bool):
    """A worktree directory, with or without its `.git` link."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "server").mkdir(exist_ok=True)
    (root / "server" / "keep.py").write_text("x = 1\n", encoding="utf-8")
    if linked:
        (root / ".git").write_text("gitdir: ../main/.git/worktrees/x\n", encoding="utf-8")
    return root


@pytest.fixture
def slot(monkeypatch, tmp_path):
    """A ledger-registered, merged slot whose worktree dir is on disk."""
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    from modules.flow_gate.services import git_service

    db = _FakeGitDb(
        {"enabled": 1, "base_branch": BASE},
        {"worktree_registered": 1, "branch": BRANCH, "status": "merged"},
    )
    monkeypatch.setattr(git_service, "db_git", db)
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: NAME)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_is_group_disposed", lambda _gid: False)

    base_root = tmp_path / "src" / NAME / BASE
    (base_root / ".git").mkdir(parents=True)
    wt = tmp_path / "src" / NAME / BRANCH
    return db, base_root, wt


# ── cleanup: the three states ────────────────────────────────────────────────

def test_orphan_without_registration_is_reclaimed(slot, monkeypatch):
    """State C (0275): directory on disk, git has no entry for it at all.

    `worktree remove` answers "is not a working tree" forever, so the slot could
    never reach the ledger unregister below it.
    """
    from modules.flow_gate.services import git_service

    db, base_root, wt = slot
    _mk_tree(wt, linked=False)
    git = _GitRecorder(listed=[])  # git knows of no worktrees
    monkeypatch.setattr(git_service, "_run_git", git)

    assert git_service._cleanup_group_slot(PROJECT, GROUP) is True
    assert not wt.exists()                      # directory actually reclaimed
    assert db.unregistered == [GROUP]           # ledger row released
    # A path git refuses to own is never handed to `worktree remove` again.
    assert not git.ran("worktree", "remove")
    assert git.ran("worktree", "prune")


def test_prunable_registration_is_reclaimed(slot, monkeypatch):
    """State B (0282/0284): admin entry survives but git flags it prunable."""
    from modules.flow_gate.services import git_service

    db, base_root, wt = slot
    _mk_tree(wt, linked=False)
    git = _GitRecorder(listed=[(str(wt), "prunable gitdir file points to non-existent location")])
    monkeypatch.setattr(git_service, "_run_git", git)

    assert git_service._cleanup_group_slot(PROJECT, GROUP) is True
    assert not wt.exists()
    assert db.unregistered == [GROUP]


def test_healthy_worktree_still_uses_git_remove(slot, monkeypatch):
    """Regression guard: the normal path must not change — git does the removal."""
    from modules.flow_gate.services import git_service

    db, base_root, wt = slot
    _mk_tree(wt, linked=True)
    import shutil

    git = _GitRecorder(
        listed=[(str(wt), "branch refs/heads/" + BRANCH)],
        on_remove=lambda: shutil.rmtree(wt),
    )
    monkeypatch.setattr(git_service, "_run_git", git)

    assert git_service._cleanup_group_slot(PROJECT, GROUP) is True
    assert git.ran("worktree", "remove")
    assert db.unregistered == [GROUP]


def test_removal_gets_its_own_timeout(slot, monkeypatch):
    """NR0004 §3: the 30 s local budget is what killed git mid-delete."""
    from modules.flow_gate.services import git_service

    _db, _base_root, wt = slot
    _mk_tree(wt, linked=True)
    seen = {}

    class _Timed(_GitRecorder):
        def __call__(self, args, **kwargs):
            if args[:2] == ["worktree", "remove"]:
                seen["timeout"] = kwargs.get("timeout")
                import shutil

                shutil.rmtree(wt)
            return super().__call__(args, **kwargs)

    monkeypatch.setattr(
        git_service, "_run_git", _Timed(listed=[(str(wt), "branch refs/heads/" + BRANCH)])
    )
    git_service._cleanup_group_slot(PROJECT, GROUP)

    assert seen["timeout"] == git_service.GIT_WORKTREE_RM_TIMEOUT_SEC
    assert seen["timeout"] > git_service.GIT_LOCAL_TIMEOUT_SEC


def test_live_worktree_that_cannot_be_removed_still_defers(slot, monkeypatch):
    """A genuinely registered worktree blocked by a lock keeps its ledger row, so a
    later sweep retries. Only orphans are reclaimed directly."""
    from modules.flow_gate.services import git_service

    db, base_root, wt = slot
    _mk_tree(wt, linked=True)
    git = _GitRecorder(
        listed=[(str(wt), "branch refs/heads/" + BRANCH)],
        remove_rc=1,
        remove_err="fatal: could not remove: Permission denied",
    )
    monkeypatch.setattr(git_service, "_run_git", git)

    assert git_service._cleanup_group_slot(PROJECT, GROUP) is False
    assert wt.exists()          # never force-deleted behind git's back
    assert db.unregistered == []


def test_remove_interrupted_midway_is_finished_on_the_spot(slot, monkeypatch):
    """The exact field failure: remove dies partway, taking `.git` with it.

    Before the fix this returned False and the slot was stuck forever; now the same
    call re-classifies the wreckage as an orphan and completes the teardown.
    """
    from modules.flow_gate.services import git_service

    db, base_root, wt = slot
    _mk_tree(wt, linked=True)

    def _interrupted():
        (wt / ".git").unlink()          # link gone, content half-erased
        (wt / "server" / "keep.py").unlink()

    git = _GitRecorder(
        listed=[(str(wt), "branch refs/heads/" + BRANCH)],
        remove_rc=-1,
        remove_err="timeout_expired",
        on_remove=_interrupted,
    )
    monkeypatch.setattr(git_service, "_run_git", git)

    assert git_service._cleanup_group_slot(PROJECT, GROUP) is True
    assert not wt.exists()
    assert db.unregistered == [GROUP]


def test_undeterminable_registration_defers_instead_of_deleting(slot, monkeypatch):
    """If git cannot answer, deleting a possibly-live worktree is the one
    irreversible mistake — defer and let the next sweep decide."""
    from modules.flow_gate.services import git_service

    db, base_root, wt = slot
    _mk_tree(wt, linked=True)

    class _Broken(_GitRecorder):
        def __call__(self, args, **kwargs):
            if args[:2] == ["worktree", "list"]:
                self.calls.append(list(args))
                return _completed(args, -1, "", "timeout_expired")
            return super().__call__(args, **kwargs)

    monkeypatch.setattr(git_service, "_run_git", _Broken())

    assert git_service._cleanup_group_slot(PROJECT, GROUP) is False
    assert wt.exists()
    assert db.unregistered == []


def test_registration_probe_matches_across_path_spellings(slot, monkeypatch, tmp_path):
    """`git worktree list` reports the real path (a UNC share in production) while
    src_root() builds the junction form — string comparison never matches."""
    from modules.flow_gate.services import git_service

    _db, base_root, wt = slot
    _mk_tree(wt, linked=True)
    # Same directory, spelled with a redundant '.' segment.
    spelled = wt.parent / "." / wt.name
    monkeypatch.setattr(
        git_service, "_run_git", _GitRecorder(listed=[(str(spelled), "branch x")])
    )

    assert git_service._classify_worktree_dir(base_root, wt) == "live"


def test_prunable_entry_is_not_counted_as_registered(slot, monkeypatch):
    from modules.flow_gate.services import git_service

    _db, base_root, wt = slot
    _mk_tree(wt, linked=True)   # link intact, but git says the entry is stale
    monkeypatch.setattr(
        git_service, "_run_git", _GitRecorder(listed=[(str(wt), "prunable gitdir file …")])
    )

    assert git_service._registered_worktrees(base_root) == set()
    assert git_service._classify_worktree_dir(base_root, wt) == "orphan"


# ── resolvers: a corpse is never the source tree ─────────────────────────────

def test_effective_src_root_rejects_a_broken_directory(slot, monkeypatch):
    from modules.flow_gate.services import git_service

    _db, _base_root, wt = slot
    _mk_tree(wt, linked=False)

    root, reason = git_service.effective_src_root_ex(PROJECT, GROUP)
    assert root is None
    assert reason == git_service.SRC_ROOT_DIR_BROKEN
    # Distinct from "the directory is gone" — the two need different diagnoses.
    assert git_service.SRC_ROOT_DIR_BROKEN != git_service.SRC_ROOT_DIR_MISSING


def test_on_disk_recovery_rejects_a_broken_directory(slot, monkeypatch):
    """0284's post-merge recovery must not resurrect a half-erased tree."""
    from modules.flow_gate.storage import paths as storage_paths

    db, _base_root, wt = slot
    db._state["worktree_registered"] = 0        # the state 0284 T0005 recovers from
    _mk_tree(wt, linked=False)

    assert storage_paths._group_worktree_on_disk(PROJECT, GROUP) is None
    assert (
        storage_paths.resolve_project_src_root(PROJECT, BASE, group_id=GROUP)
        != wt.resolve()
    )


def test_broken_directory_is_reported_in_the_tsr_kind(slot, monkeypatch):
    """The run must be labelled with WHY, not silently as 'worktree'."""
    from modules.flow_gate.storage import paths as storage_paths

    _db, _base_root, wt = slot
    _mk_tree(wt, linked=False)

    assert storage_paths.classify_src_root(PROJECT, GROUP, wt) == "worktree_dir_broken"


def test_ensure_worktree_does_not_report_a_corpse_as_ready(slot, monkeypatch):
    """The idempotence gate used to answer 'ok' (and emit git_worktree_ready) for
    any existing directory, so a broken slot was never re-provisioned."""
    from modules.flow_gate.services import git_service

    _db, base_root, wt = slot
    _mk_tree(wt, linked=False)
    ready = []
    monkeypatch.setattr(
        git_service, "_emit_worktree_ready",
        lambda *a, **k: ready.append(a),
    )
    monkeypatch.setattr(git_service, "_fail_worktree", lambda *a, **k: None)
    monkeypatch.setattr(
        git_service, "_provision_base_locked",
        lambda *a, **k: {"status": "ok", "reason": None},
    )

    result = git_service._ensure_worktree_locked(
        {"enabled": 1, "base_branch": BASE}, PROJECT, NAME, GROUP, BRANCH, "test",
    )

    assert result != "ok"
    assert ready == []
