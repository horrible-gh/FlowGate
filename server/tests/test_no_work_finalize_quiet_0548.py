"""머지할 게 없는 그룹은 Git 화면을 하나도 띄우지 않는다 (flowgate.default.0548 T0004).

TR0005 rev4 의 사람 반려는 네 표면을 한꺼번에 지목했다.

    머지할게 없는데...
    경고 토스트가 그대로 뜬다 / 깃 다이얼로그도 그대로 뜬다
    머지 다이얼로그가 그대로 뜬다 / 문서에 머지 섹션이 그대로 뜬다

네 표면은 전부 **하나의 판정** — "이 그룹에 머지할 것이 있는가" — 을 읽는다. 그래서 이
시험도 그 판정을 진짜 git 위에서 고정한다: 임시 폴더에 실제 base 저장소와 실제
``git worktree`` 를 만들고, ``_group_has_changes`` / ``group_finalize_is_noop`` /
``get_finalize_state`` / ``run_approve_git_action`` 을 그 위에서 그대로 돌린다. DB 층만
대역이다(원장 계약은 0115·0332 가 이미 잡고 있고, 여기서 증명할 것은 git 이 뭐라고
답하느냐이므로).

고정하는 계약:

* 작업 없는 그룹은 AC 승인 창에 머지/푸시 선택 블록을 **띄우지 않는다**(머지 다이얼로그).
* 이미 gate 에 들어와 버린 슬롯도 머지할 게 없으면 gate 를 잃는다(문서의 머지 섹션).
* 승인에 딸려 온 git_action 이 "할 게 없어서" 실패하면 quiet 로 보고한다 — 경고 토스트도
  Git 패널 자동 오픈도 없다.
* 반대로 **진짜 작업이 있는** 그룹의 gate·경고·충돌은 하나도 건드리지 않는다.
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

_SERVER_DIR = Path(__file__).resolve().parents[1]
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
    """저장소 밖의 진짜 base 체크아웃 + 그 위의 진짜 그룹 worktree."""

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
    # 진짜 worktree — 그룹 브랜치는 base tip 에서 갈라져 나오고 아직 아무 것도 없다.
    _git(["worktree", "add", "-b", _BRANCH, str(s.work), "main"], s.base)

    monkeypatch.setattr(svc, "src_root", s.src_root)
    monkeypatch.setattr(svc, "_project_of_group", lambda group_id: _PROJECT)
    monkeypatch.setattr(svc, "_project_name", lambda project_id: _PROJECT_NAME)
    yield s
    remove_tree(root)


# ── 1. "머지할 게 있는가" — 진짜 git 이 답한다 ───────────────────────────────

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
    """rev3 반려: "Git으로 변경 유무를 확인할 수 없음"을 "변경이 있음"으로 취급하지 말라.
    브랜치가 배정된 적 없는 그룹은 소스 변경이 살 곳 자체가 없다."""
    assert svc._group_has_changes(slot.cfg, slot.state(branch=""), _PROJECT_NAME) is False


@needs_git
def test_a_branch_git_cannot_count_with_no_worktree_dir_is_proven_empty(slot):
    """0013 이 실제로 걸린 모양 — 원장에는 브랜치 이름이 있는데 ref 도 디렉터리도 없다."""
    ghost = slot.state(branch="test2_default_9999")

    assert svc._group_has_changes(slot.cfg, ghost, _PROJECT_NAME) is False


@needs_git
def test_a_worktree_that_exists_but_git_cannot_answer_for_stays_unknown(slot, monkeypatch):
    """반대편 — 디렉터리는 살아 있는데 base 체크아웃이 저장소가 아니라 셀 수 없다.
    여기서만 None(=보수적으로 gate 유지)이 남는다. 작업이 숨어 있을 수 있기 때문."""
    broken = slot.root / "broken"
    (broken / _PROJECT_NAME / _BRANCH).mkdir(parents=True)
    monkeypatch.setattr(
        svc, "src_root", lambda project_name, branch: broken / project_name / branch,
    )

    assert svc._group_has_changes(slot.cfg, slot.state(), _PROJECT_NAME) is None


# ── 2. 하나의 판정: group_finalize_is_noop ───────────────────────────────────

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
    """사람 반려의 경고 토스트/깃 다이얼로그가 태어난 자리. AC 승인이 스스로
    no-work auto-discard 를 돌려 슬롯을 unregister 한 직후, 딸려 온 git_action 이
    ``Git integration is not active for group …`` 로 깨졌다."""
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
    """rev5 반려: ``db_git.unregister_worktree`` 는 플래그만 내리고 ``branch`` 는
    그대로 남긴다. 그래서 그 브랜치의 on-disk worktree 에 실제 미신고 변경이 남아
    있으면, ``worktree_registered=0`` 만 보고 조용히 quiet 처리해서는 안 된다."""
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    if leave_real_work == "commit ahead of base":
        _git(["add", "-A"], slot.work)
        _git(["commit", "-m", "work"], slot.work)
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: slot.state(worktree_registered=0),
    )

    assert svc.group_finalize_is_noop(_GROUP) is False


# ── 3. 승인에 딸려 온 git_action (경고 토스트 / 깃 다이얼로그) ───────────────

@needs_git
def test_the_approval_ride_along_reports_a_no_work_failure_quietly(slot, monkeypatch):
    """실제 ``finalize`` 를 그대로 돌린다 — 슬롯이 이미 내려간 뒤라 409 로 깨지고,
    그 409 가 quiet 로 보고되어야 한다(토스트 없음 · 패널 자동 오픈 없음)."""
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(
        svc.db_git, "get_state", lambda group_id: slot.state(worktree_registered=0),
    )

    out = svc.run_approve_git_action(_GROUP, "merge")

    # ok stays honest (the action did not run); quiet is the display verdict.
    assert out["ok"] is False
    assert out["quiet"] is True
    assert out["error"]["code"] == "invalid_state"


@needs_git
def test_the_approval_ride_along_still_warns_for_a_stale_unregistered_slot_with_real_work(
    slot, monkeypatch,
):
    """자동 리뷰 반려(rev5): ``worktree_registered=0`` 인데 그 브랜치의 on-disk
    worktree 에 실제 미신고 변경이 남아 있으면, ride-along 실패는 quiet 가 아니라
    그대로 actionable warning 이어야 한다."""
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
    """T0004 §4 — 진짜 작업이 있는 그룹의 실패는 그대로 actionable warning 이다."""
    (slot.work / "real.py").write_text("y = 2\n", encoding="utf-8")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: slot.cfg)
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: slot.state())

    def _busy(group_id, action, commit_message=None):
        raise svc.GitServiceError(409, "git_busy", "another git operation is in progress")

    monkeypatch.setattr(svc, "finalize", _busy)
    out = svc.run_approve_git_action(_GROUP, "merge")

    assert out["ok"] is False
    assert out.get("quiet") is None
    assert out["error"]["code"] == "git_busy"


# ── 4. 화면이 읽는 상태: get_finalize_state (머지 다이얼로그 / 문서 머지 섹션) ─

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
    """머지 다이얼로그. ``context=approval`` 이 status 'none' 을 무조건 awaiting_choice
    로 바꿔치기하던 자리 — 작업이 없으면 바꿔치기하지 않는다."""
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
    """0197 T0004 §B 의 원래 계약은 그대로다 — 작업이 있으면 선택지가 나온다."""
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
    """문서의 머지 섹션. GitFinalizePanel 은 ``status !== 'none'`` 일 때만 뜨므로,
    이미 gate 에 들어와 앉은 빈 슬롯을 none 으로 수렴시키는 것이 곧 섹션 제거다."""
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
    """`waiting` 은 사람이 "나중에" 를 고른 결과다 — 비어 있어도 사람이 세운 슬롯을
    조용히 허물지 않는다(0115 TestGitActions0162 가 고정한 계약). 위의 preview 수정으로
    작업 없는 그룹은 애초에 그 선택지를 받지 못하므로 이 예외로 새로 새는 곳도 없다."""
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
    """충돌은 살아 있는 세션이다 — 비어 보이더라도 절대 조용히 치우지 않는다."""
    monkeypatch.setattr(
        svc.db_git, "get_state",
        lambda group_id: finalize_state_deps.state(status="conflict"),
    )
    monkeypatch.setattr(svc, "_auto_discard_group", lambda project_id, group_id: (
        pytest.fail("a conflict session must never be auto-discarded")
    ))

    state = svc.get_finalize_state(_GROUP)["state"]

    assert state["status"] == "conflict"
