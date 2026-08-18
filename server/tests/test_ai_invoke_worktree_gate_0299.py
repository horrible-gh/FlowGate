"""AI 실행 워크트리 게이트 (flowgate.default.0299 — R0001, TR0008 §5-2 후속).

TR 작업범위 검증(D0004)은 잘못된 위치에서 이루어진 작업을 *보고 시점*에 잡는다.
이 게이트는 같은 사고를 *실행 시점*에 막는다 — 그룹 워크트리를 확인할 수 없으면
AI 실행을 아예 시작하지 않으므로, 원본 체크아웃(main)에 남을 작업 자체가 없다.

원격 CRUD 는 0205 부터 같은 방식으로 막혀 있었고 CLI cwd 경로만 열려 있었다.
그래서 이 테스트는 그 두 경로가 같은 규칙을 따르는지에 초점을 맞춘다:
연동 안 된 프로젝트는 그대로 통과, 연동된 프로젝트는 self-heal 한 번 뒤 409.
"""
from __future__ import annotations

import re
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


# ── T0004 작업 6/8: worktree_unavailable 409 follows the requested locale ────────
# NR0003 발견 6: this 409 used to be a fixed Korean f-string with no locale parameter
# at all -- invisible to 0355's locale-branch scanner because nothing about it looked
# like a locale branch. These pin the en/ja no-leak contract, the preserved dynamic
# cause + ko meaning, and the continuation_locale wiring from start_run().


@pytest.mark.parametrize(
    "locale,blocked_snippet,recovery_snippet",
    [
        ("en", "working folder", "Recover the group's Git state"),
        ("ja", "worktree", "再実行してください"),
    ],
)
def test_blocked_response_is_localized_and_has_no_korean(
    monkeypatch, wt, tmp_path, locale, blocked_snippet, recovery_snippet
):
    base = tmp_path / "proj" / "main"
    base.mkdir(parents=True)
    _wire(monkeypatch, worktree=wt, resolved=base)
    with pytest.raises(HTTPException) as exc:
        aiv._require_group_worktree("p", "default", "g", "main", locale=locale)
    message = exc.value.detail["message"]
    assert not re.search(r"[가-힣]", message)
    assert blocked_snippet in message
    assert recovery_snippet in message  # 복구 안내가 로케일마다 보존된다
    assert "worktree_missing" in message  # 동적 cause 가 문장에 그대로 들어간다
    # 구조화 payload 와 상태코드는 로케일과 무관하게 그대로다.
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "worktree_unavailable"
    assert exc.value.detail["cause"] == "worktree_missing"
    assert exc.value.detail["group_id"] == "g"
    assert "provision_error" in exc.value.detail


def test_ko_default_preserves_existing_meaning(monkeypatch, wt, tmp_path):
    """No locale argument (non-continuous run, or a legacy NULL continuation_locale)
    keeps the pre-T0004 Korean wording."""
    base = tmp_path / "proj" / "main"
    base.mkdir(parents=True)
    _wire(monkeypatch, worktree=wt, resolved=base)
    with pytest.raises(HTTPException) as exc:
        aiv._require_group_worktree("p", "default", "g", "main")
    message = exc.value.detail["message"]
    assert "워크트리" in message
    assert "그룹 Git 상태를 복구" in message


def test_unsupported_locale_falls_back_to_ko(monkeypatch, wt, tmp_path):
    base = tmp_path / "proj" / "main"
    base.mkdir(parents=True)
    _wire(monkeypatch, worktree=wt, resolved=base)
    with pytest.raises(HTTPException) as exc:
        aiv._require_group_worktree("p", "default", "g", "main", locale="fr")
    assert "워크트리" in exc.value.detail["message"]


def test_worktree_already_valid_is_unaffected_by_locale(monkeypatch, wt):
    """Existing pass-through behavior (already-valid worktree, self-heal success)
    must stay exception-free regardless of the locale argument."""
    _wire(monkeypatch, worktree=wt, resolved=wt)
    aiv._require_group_worktree("p", "default", "g", "main", locale="en")


def test_non_integrated_and_group_less_stay_unblocked_with_a_locale(monkeypatch):
    _wire(monkeypatch, enabled=False, resolved=None)
    aiv._require_group_worktree("p", "default", "g", "main", locale="ja")
    _wire(monkeypatch, resolved=None)
    aiv._require_group_worktree("p", "default", "", "main", locale="ja")


def test_start_run_wires_continuation_locale_into_the_worktree_gate(monkeypatch):
    """start_run() already carried continuation_locale for token/mention plumbing but
    never forwarded it to _require_group_worktree until T0004 작업 6. Pin the wiring
    directly rather than trusting the two functions independently."""
    captured = {}

    def _fake_require(project_id, module, group_id, branch, locale=None):
        captured["locale"] = locale
        raise HTTPException(
            status_code=409,
            detail={"code": "worktree_unavailable", "message": "stub"},
        )

    monkeypatch.setattr(aiv, "_require_group_worktree", _fake_require)
    monkeypatch.setattr(
        aiv.ai_settings_service, "resolve_effective",
        lambda _pid: {"providers": [{"id": "prov1"}], "source": "configured", "registered_count": 1},
    )
    monkeypatch.setattr(aiv.db_group_ai_leases, "get_active", lambda _gid: None)
    monkeypatch.setattr(aiv.db_docs, "get_by_id", lambda _doc_ref: {"branch": "main"})

    with pytest.raises(HTTPException):
        aiv.start_run(
            project_id="p", module="default", group_id="g", doc_ref="d",
            action_scope="new", mode="single",
            continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale="ja",
            issued_to="worker", api_base_url="http://x",
            mention_builder=lambda *_a, **_k: None,
        )

    assert captured["locale"] == "ja"
