"""AI 실행 워크트리 게이트 (flowgate.default.0299 — R0001, TR0008 §5-2 후속).

TR 작업범위 검증(D0004)은 잘못된 위치에서 이루어진 작업을 *보고 시점*에 잡는다.
이 게이트는 같은 사고를 *실행 시점*에 막는다 — 그룹 워크트리를 확인할 수 없으면
AI 실행을 아예 시작하지 않으므로, 원본 체크아웃(main)에 남을 작업 자체가 없다.

원격 CRUD 는 0205 부터 같은 방식으로 막혀 있었고 CLI cwd 경로만 열려 있었다.
그래서 이 테스트는 그 두 경로가 같은 규칙을 따르는지에 초점을 맞춘다:
연동 안 된 프로젝트는 그대로 통과, 연동된 프로젝트는 self-heal 한 번 뒤 409.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from modules.flow_gate.services import ai_invoke_service as aiv


@pytest.fixture()
def wt(tmp_path: Path) -> Path:
    path = tmp_path / "proj" / "grp_branch"
    path.mkdir(parents=True)
    return path


def _wire(monkeypatch, *, enabled=True, branch="grp_branch", worktree=None,
          resolved=None, ensure_result="failed", provision_error=None, session=None):
    """Stub the whole neighbourhood so the guard is tested, not git."""
    monkeypatch.setattr(aiv.db_git, "get_config", lambda pid: {"enabled": enabled} if enabled is not None else None)
    monkeypatch.setattr(
        aiv.db_git, "get_state",
        lambda gid: {"branch": branch, "provision_error": provision_error},
    )
    monkeypatch.setattr(aiv.git_service, "_project_name", lambda pid: "proj")
    monkeypatch.setattr(aiv.git_service, "src_root", lambda name, br: worktree or Path("/nonexistent"))
    monkeypatch.setattr(aiv.git_service, "open_merge_session_of_project", lambda pid: session)
    monkeypatch.setattr(
        aiv.git_service, "ensure_worktree",
        lambda pid, module, gid, trigger=None: ensure_result,
    )
    calls: list[int] = []

    def _resolve(project_id, fallback_branch, group_id=None):
        calls.append(1)
        # 두 번째 호출(= self-heal 이후)에만 워크트리를 돌려주는 시나리오를 위해
        # resolved 를 리스트로도 받는다.
        if isinstance(resolved, list):
            return resolved[min(len(calls), len(resolved)) - 1]
        return resolved

    monkeypatch.setattr(aiv.storage_paths, "resolve_project_src_root", _resolve)
    return calls


def test_passes_when_the_run_lands_in_the_group_worktree(monkeypatch, wt):
    _wire(monkeypatch, worktree=wt, resolved=wt)
    aiv._require_group_worktree("p", "default", "g", "main")  # 예외 없음


def test_blocks_when_resolution_fell_back_to_the_base_tree(monkeypatch, wt, tmp_path):
    """폴백된 main 경로는 '해결됨'이 아니다 — 여기가 원래 뚫려 있던 구멍이다."""
    base = tmp_path / "proj" / "main"
    base.mkdir(parents=True)
    _wire(monkeypatch, worktree=wt, resolved=base)
    with pytest.raises(HTTPException) as exc:
        aiv._require_group_worktree("p", "default", "g", "main")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "worktree_unavailable"
    assert exc.value.detail["cause"] == "worktree_missing"


def test_self_heal_retry_rescues_a_recoverable_group(monkeypatch, wt, tmp_path):
    """원격 쓰기 게이트와 같은 모양 — ensure_worktree 한 번은 시도하고 통과시킨다."""
    base = tmp_path / "proj" / "main"
    base.mkdir(parents=True)
    calls = _wire(monkeypatch, worktree=wt, resolved=[base, wt], ensure_result="ok")
    aiv._require_group_worktree("p", "default", "g", "main")
    assert len(calls) == 2  # 최초 해석 + self-heal 이후 재해석


def test_non_integrated_project_is_untouched(monkeypatch):
    """연동이 없는 프로젝트에는 요구할 워크트리 자체가 없다."""
    _wire(monkeypatch, enabled=False, resolved=None)
    aiv._require_group_worktree("p", "default", "g", "main")


def test_group_less_run_is_untouched(monkeypatch):
    _wire(monkeypatch, resolved=None)
    aiv._require_group_worktree("p", "default", "", "main")


def test_blocking_cause_is_reported(monkeypatch, wt, tmp_path):
    """원인 없는 '워크트리 없음'은 사후에 고칠 수 없다 (0280 NR0003 §4-B)."""
    base = tmp_path / "proj" / "main"
    base.mkdir(parents=True)
    _wire(monkeypatch, worktree=wt, resolved=base, provision_error="clone timed out")
    with pytest.raises(HTTPException) as exc:
        aiv._require_group_worktree("p", "default", "g", "main")
    assert exc.value.detail["cause"] == "provision_failed"
    assert exc.value.detail["provision_error"] == "clone timed out"

    _wire(monkeypatch, worktree=wt, resolved=base, session={"group_id": "other"})
    with pytest.raises(HTTPException) as exc2:
        aiv._require_group_worktree("p", "default", "g", "main")
    assert exc2.value.detail["cause"] == "merge_conflict_open"


def test_config_lookup_failure_does_not_block_a_run(monkeypatch):
    """위반 탐지가 정상 실행을 막는 것이 원래 사고보다 나쁘다."""
    def _boom(pid):
        raise RuntimeError("db down")

    monkeypatch.setattr(aiv.db_git, "get_config", _boom)
    aiv._require_group_worktree("p", "default", "g", "main")


def test_is_group_worktree_rejects_none_and_mismatch(monkeypatch, wt, tmp_path):
    _wire(monkeypatch, worktree=wt)
    assert aiv._is_group_worktree("p", "g", None) is False
    assert aiv._is_group_worktree("p", "g", tmp_path / "elsewhere") is False
    assert aiv._is_group_worktree("p", "g", wt) is True


def test_is_group_worktree_is_false_when_the_ledger_has_no_branch(monkeypatch, wt):
    _wire(monkeypatch, worktree=wt, branch="")
    assert aiv._is_group_worktree("p", "g", wt) is False
