"""A group with nothing to merge opens no Git UI.

The human rejection of the earlier implementation identified four surfaces at once:

    Nothing to merge, yet the warning toast and Git dialog still appear;
    the merge dialog still appears and the document still shows a merge section.

All four surfaces read **one decision**: whether this group has anything to merge.
This suite therefore establishes that decision on real Git: create an actual base
repository and ``git worktree`` in a temporary directory, then run ``_group_has_changes``,
``group_finalize_is_noop``, ``get_finalize_state``, and ``run_approve_git_action`` unchanged.
Only the DB layer is substituted: 0115/0332 already cover the ledger contract, while
this suite must establish what Git actually answers.

Contracts:

* A group with no work shows no merge/push selection block in the AC approval dialog.
* An existing gated slot loses its gate when there is nothing to merge (document merge section).
* If the accompanying git_action fails because there is nothing to do, report it quietly:
  no warning toast and no automatic Git-panel opening.
* Preserve every gate, warning, and conflict for groups with **real work**.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-no-work-0548-")
)

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import git_service as svc  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("no-work-finalize-0548")

_PROJECT = "test2"
_PROJECT_NAME = "test2"
_GROUP = "test2.default.0013"
_BRANCH = "test2_default_0013"


def _git(args, cwd):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


class _Slot:
    """A real base checkout outside the repository and a real group worktree based on it."""

    def __init__(self, root: Path):
        self.root = root
        self.base = root / _PROJECT_NAME / "main"
        self.work = root / _PROJECT_NAME / _BRANCH

    def src_root(self, project_name: str, branch: str) -> Path:
        return self.root / project_name / branch

    @property
    def cfg(self) -> dict:
        return {
            "enabled": 1, "base_branch": "main", "default_finalize_action": "merge",
            "author_name": None, "author_email": None,
        }

    def state(self, **over) -> dict:
        row = {
            "group_id": _GROUP, "branch": _BRANCH, "status": "none",
            "worktree_registered": 1, "merge_id": None, "merge_commit": None,
        }
        row.update(over)
        return row


@pytest.fixture
def slot(monkeypatch):
    root = _SCRATCH / f"slot-{os.urandom(6).hex()}"
    (root / _PROJECT_NAME).mkdir(parents=True)
    s = _Slot(root)
    s.base.mkdir(parents=True)
    _git(["init", "-b", "main"], s.base)
    (s.base / "README.md").write_text("base\n", encoding="utf-8")
    _git(["add", "-A"], s.base)
    _git(["commit", "-m", "base"], s.base)
    # Real worktree: the group branch forks from the base tip and has no changes yet.
    _git(["worktree", "add", "-b", _BRANCH, str(s.work), "main"], s.base)

    monkeypatch.setattr(svc, "src_root", s.src_root)
    monkeypatch.setattr(svc, "_project_of_group", lambda group_id: _PROJECT)
    monkeypatch.setattr(svc, "_project_name", lambda project_id: _PROJECT_NAME)
    yield s
    remove_tree(root)


# ── 1. Is there anything to merge? Real Git answers ──────────────────────────

@needs_git
def test_a_clean_branch_at_the_base_tip_has_nothing_to_merge(slot):
    assert svc._group_has_changes(slot.cfg, slot.state(), _PROJECT_NAME) is False


@needs_git
def test_an_uncommitted_edit_in_the_worktree_counts_as_work(slot):
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")

    assert svc._group_has_changes(slot.cfg, slot.state(), _PROJECT_NAME) is True


@needs_git
def test_a_commit_ahead_of_base_counts_as_work(slot):
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    _git(["add", "-A"], slot.work)
    _git(["commit", "-m", "work"], slot.work)

    assert svc._group_has_changes(slot.cfg, slot.state(), _PROJECT_NAME) is True


@needs_git
def test_a_group_with_no_branch_is_proven_empty_not_unknown(slot):
    """Revision 3 rejection: do not treat an inability to determine changes through Git as proof of changes.
    A group never assigned a branch has nowhere for source changes to exist."""
    assert svc._group_has_changes(slot.cfg, slot.state(branch=""), _PROJECT_NAME) is False


@needs_git
def test_a_branch_git_cannot_count_with_no_worktree_dir_is_proven_empty(slot):
    """The exact failure shape in 0013: the ledger names a branch, but neither its ref nor directory exists."""
    ghost = slot.state(branch="test2_default_9999")

    assert svc._group_has_changes(slot.cfg, ghost, _PROJECT_NAME) is False


@needs_git
def test_a_worktree_that_exists_but_git_cannot_answer_for_stays_unknown(slot, monkeypatch):
    """The opposite case: the directory exists, but the base checkout is not a repository and cannot be measured.
    Only here retain None (conservatively keep the gate), since work may be hidden there."""
    broken = slot.root / "broken"
    (broken / _PROJECT_NAME / _BRANCH).mkdir(parents=True)
    monkeypatch.setattr(
        svc, "src_root", lambda project_name, branch: broken / project_name / branch,
    )

    assert svc._group_has_changes(slot.cfg, slot.state(), _PROJECT_NAME) is None


# ── 2. One decision: group_finalize_is_noop ──────────────────────────────────

@needs_git
def test_a_no_work_slot_is_a_finalize_no_op(slot, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: slot.state())

    assert svc.group_finalize_is_noop(_GROUP) is True


@needs_git
def test_a_slot_with_real_work_is_never_a_no_op(slot, monkeypatch):
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: slot.state())

    assert svc.group_finalize_is_noop(_GROUP) is False


@needs_git
def test_a_slot_torn_down_by_the_no_work_auto_discard_is_a_no_op(slot, monkeypatch):
    """Origin of the warning toast/Git dialog in the human rejection: immediately after AC approval
    auto-discarded a no-work slot and unregistered it, the accompanying git_action failed with
    ``Git integration is not active for group …``."""
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: slot.state(worktree_registered=0),
    )

    assert svc.group_finalize_is_noop(_GROUP) is True


@needs_git
@pytest.mark.parametrize("status", ["conflict", "merging", "merged", "pushed"])
def test_a_live_or_terminal_git_slot_is_never_quiet(slot, monkeypatch, status):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: slot.state(status=status))

    assert svc.group_finalize_is_noop(_GROUP) is False


@needs_git
@pytest.mark.parametrize("leave_real_work", [
    "uncommitted edit", "commit ahead of base",
])
def test_a_stale_unregistered_slot_with_real_work_is_never_a_no_op(
    slot, monkeypatch, leave_real_work,
):
    """Revision 5 rejection: ``db_git.unregister_worktree`` clears only the flag and retains ``branch``.
    If that branch's on-disk worktree still contains real unreported edits,
    ``worktree_registered=0`` alone must not justify a quiet result."""
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    if leave_real_work == "commit ahead of base":
        _git(["add", "-A"], slot.work)
        _git(["commit", "-m", "work"], slot.work)
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: slot.state(worktree_registered=0),
    )

    assert svc.group_finalize_is_noop(_GROUP) is False


# ── 3. Approval's accompanying git_action (warning toast / Git dialog) ────────

@needs_git
def test_the_approval_ride_along_reports_a_no_work_failure_quietly(slot, monkeypatch):
    """Run the real ``finalize`` unchanged: the slot is already released, so it fails with 409.
    Report that 409 quietly, without a toast or automatic panel opening."""
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: slot.state(worktree_registered=0),
    )

    out = svc.run_approve_git_action(_GROUP, "merge")

    # Final approval no longer disguises a Git failure as an approved quiet no-op.
    assert out["ok"] is False
    assert out["terminal"] is False
    assert out.get("quiet") is None
    assert out["error"]["code"] == "invalid_state"


@needs_git
def test_the_approval_ride_along_still_warns_for_a_stale_unregistered_slot_with_real_work(
    slot, monkeypatch,
):
    """Automated review rejection (rev5): if ``worktree_registered=0`` but the branch's on-disk
    worktree retains real unreported edits, the accompanying failure must remain
    an actionable warning rather than becoming quiet."""
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: slot.state(worktree_registered=0),
    )

    out = svc.run_approve_git_action(_GROUP, "merge")

    assert out["ok"] is False
    assert out.get("quiet") is None
    assert out["error"]["code"] == "invalid_state"


@needs_git
def test_a_real_finalize_failure_on_a_working_group_still_warns(slot, monkeypatch):
    """failures for groups with real work remain actionable warnings."""
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: slot.state())

    def _busy(group_id, action, commit_message=None, **kwargs):
        raise svc.GitServiceError(409, "git_busy", "another git operation is in progress")

    monkeypatch.setattr(svc, "finalize", _busy)
    out = svc.run_approve_git_action(_GROUP, "merge")

    assert out["ok"] is False
    assert out.get("quiet") is None
    assert out["error"]["code"] == "git_busy"


# ── 4. UI state: get_finalize_state (merge dialog / document merge section) ──

@pytest.fixture
def finalize_state_deps(slot, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(svc.db_git, "get_open_session_by_group", lambda group_id: None)
    monkeypatch.setattr(
        svc, "resolve_commit_message", lambda group_id: ("chore: approve", "fallback"),
    )
    monkeypatch.setattr(svc, "_group_root_wf_done", lambda group_id: False)
    return slot


@needs_git
def test_the_ac_approval_preview_offers_no_merge_choice_when_there_is_nothing_to_merge(
    finalize_state_deps, monkeypatch,
):
    """Merge dialog: ``context=approval`` formerly replaced status 'none' with awaiting_choice
    unconditionally; do not replace it when there is no work."""
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: finalize_state_deps.state(),
    )

    state = svc.get_finalize_state(_GROUP, preview_ac=True)["state"]

    assert state["status"] == "none"
    assert state["choices"] == []
    assert state["action_axes"] is None


@needs_git
def test_the_ac_approval_preview_still_offers_the_choice_when_the_group_did_work(
    finalize_state_deps, monkeypatch,
):
    """Preserve the original the earlier implementation §B contract: offer choices when work exists."""
    (finalize_state_deps.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: finalize_state_deps.state(),
    )

    state = svc.get_finalize_state(_GROUP, preview_ac=True)["state"]

    assert state["status"] == "awaiting_choice"
    assert state["choices"]
    assert state["preview"] is True


@needs_git
def test_a_pending_slot_with_nothing_to_merge_loses_the_document_git_section(
    finalize_state_deps, monkeypatch,
):
    """Document merge section: GitFinalizePanel appears only when ``status !== 'none'``.
    Converging an empty, already-gated slot to none therefore removes the section."""
    discarded: list[str] = []
    monkeypatch.setattr(
        svc.db_git, "get_state",
        lambda group_id: finalize_state_deps.state(status="awaiting_choice"),
    )
    monkeypatch.setattr(svc, "_auto_discard_group", lambda project_id, group_id: (
        discarded.append(group_id) or svc.DISCARDED_STATUS
    ))

    state = svc.get_finalize_state(_GROUP)["state"]

    assert discarded == [_GROUP]
    assert state["status"] == "none"
    assert state["choices"] == []


@needs_git
@pytest.mark.parametrize("status", ["awaiting_choice", "waiting"])
def test_a_pending_slot_that_really_has_work_keeps_its_gate(
    finalize_state_deps, monkeypatch, status,
):
    (finalize_state_deps.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: finalize_state_deps.state(status=status),
    )
    monkeypatch.setattr(svc, "_auto_discard_group", lambda project_id, group_id: (
        pytest.fail("a group with real work must never be auto-discarded")
    ))

    state = svc.get_finalize_state(_GROUP)["state"]

    assert state["status"] == status
    assert state["choices"]


@needs_git
def test_an_operator_parked_waiting_slot_is_left_alone_even_with_no_work(
    finalize_state_deps, monkeypatch,
):
    """``waiting`` is the result of a person choosing "나중에". Even when empty, do not silently
    remove a slot a person explicitly established (0115 TestGitActions0162 contract).
    The preview fix above prevents no-work groups from receiving that choice in the first place,
    so this exception introduces no new gap."""
    monkeypatch.setattr(
        svc.db_git, "get_state",
        lambda group_id: finalize_state_deps.state(status="waiting"),
    )
    monkeypatch.setattr(svc, "_auto_discard_group", lambda project_id, group_id: (
        pytest.fail("an operator-parked waiting slot must never be auto-discarded")
    ))

    state = svc.get_finalize_state(_GROUP)["state"]

    assert state["status"] == "waiting"


@needs_git
def test_a_conflict_slot_is_never_converged_away(finalize_state_deps, monkeypatch):
    """A conflict is a live session: never silently remove it even when it looks empty."""
    monkeypatch.setattr(
        svc.db_git, "get_state",
        lambda group_id: finalize_state_deps.state(status="conflict"),
    )
    monkeypatch.setattr(svc, "_auto_discard_group", lambda project_id, group_id: (
        pytest.fail("a conflict session must never be auto-discarded")
    ))

    state = svc.get_finalize_state(_GROUP)["state"]

    assert state["status"] == "conflict"
