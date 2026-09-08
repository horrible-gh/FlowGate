"""flowgate.default.0481 T0010 rev6 — the three things the 2026-09-08 19:xx rejection hit.

1. [AI에게 맡기기] answered "기준 브랜치 AI 정리를 시작하지 못했습니다." and then, for the next
   two minutes, "이 프로젝트의 AI 정리가 진행 중입니다.". Reproduced against a copy of the
   reviewer's own database: `resolve_base_dirty` synthesises a group id (`<project>.none.0000`)
   that has no row in `groups`, and BOTH `group_ai_leases.group_id` and `tokens.group_id` are
   foreign keys into it — the run died on a raw IntegrityError before it ever reached a
   worker, and `tokens.action_scope` rejected the scope on top of that. Neither was visible to
   the existing suites, which stub `start_run` away or use the constraint-free in-memory
   store, so the feature had never once run against a real database.
2. The same raw exception is not one of the three the start route rolls back on, so the
   project admission lease it had already taken survived the failure.
3. A question typed at the merge approval screen was launched with the ORDINARY conflict
   mention and none of the conversation, so the run answered "there are no conflicts, tell me
   what is confusing" and — when it obeyed the resolve instruction it had been handed — its
   own submission re-froze the candidate and made its own answer `stale_run`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import project_ai_leases  # noqa: E402


class _Request:
    headers = {"x-locale": "ko"}
    url = type("URL", (), {"scheme": "http", "hostname": "localhost", "port": 80})()


# ── 1. the schema has to admit every scope the code mints ────────────────────

_CHECK_RE = re.compile(r"action_scope\s+TEXT\s+NOT\s+NULL\s*\n?\s*CHECK\s*\(action_scope\s+IN\s*\(([^)]*)\)\)")


def _newest_token_scope_allowlist() -> set[str]:
    """The action_scope values the CURRENT sqlite schema accepts for a token row.

    Read from the last migration that rebuilds the constraint, exactly the way the
    database ends up: later files win.
    """
    migrations = sorted((_SERVER_DIR / "sql" / "migrations" / "sqlite").glob("*.sql"))
    allowed: set[str] = set()
    for path in migrations:
        match = _CHECK_RE.search(path.read_text(encoding="utf-8"))
        if match:
            allowed = {value.strip().strip("'") for value in match.group(1).split(",")}
    return allowed


def test_every_startable_scope_is_accepted_by_the_tokens_check_constraint():
    """The bug behind rejection 1, as an invariant.

    0482 T0011 added `resolve_base_dirty` to `_ALLOWED_SCOPES` and gave it the identity
    fallthrough in `_TOKEN_SCOPE`, so `token_service.issue` stored that literal string —
    while the tokens CHECK constraint still listed eight other values. Every press ended in
    `sqlite3.IntegrityError: CHECK constraint failed`. Nothing compared the two lists, so
    the next scope to be added would repeat it; this is that comparison.
    """
    from modules.flow_gate.api.v1 import ai_invoke_routes

    minted = {
        ai_invoke_routes._TOKEN_SCOPE.get(scope, scope)
        for scope in ai_invoke_routes._ALLOWED_SCOPES
    }
    allowed = _newest_token_scope_allowlist()
    assert allowed, "no tokens action_scope CHECK found in the sqlite migrations"
    assert "resolve_base_dirty" in allowed
    assert minted <= allowed, f"scopes the code mints but the schema rejects: {sorted(minted - allowed)}"


# ── 2. a project-scoped run takes no group lease and mints a group-less token ──

def _admission_stubs(monkeypatch, issued: dict, refuse_group_lease: bool = True):
    from modules.flow_gate.services.ai_invoke import admission

    def _no_group_lease(*_a, **_kw):
        raise AssertionError("a project-scoped run must not touch the group lease")

    if refuse_group_lease:
        monkeypatch.setattr(admission.db_group_ai_leases, "get_active", _no_group_lease)
        monkeypatch.setattr(admission.db_group_ai_leases, "acquire", _no_group_lease)
        monkeypatch.setattr(admission.db_group_ai_leases, "activate", _no_group_lease)
    monkeypatch.setattr(
        admission.ai_settings_service, "resolve_effective",
        lambda _p: {"providers": [{"id": "prov-1", "name": "P1"}], "source": "project",
                    "registered_count": 1},
    )
    monkeypatch.setattr(admission.db_docs, "get_by_id", lambda _d: None)
    monkeypatch.setattr(admission.db_docs, "get_group_max_seq", lambda _g: 0)

    def _issue(**kwargs):
        issued.update(kwargs)
        return {"raw_token": "raw", "token_id": "tok-1", "scratch_dir": "/tmp/scratch"}

    monkeypatch.setattr(admission.token_service, "issue", _issue)
    return admission


def test_project_scoped_run_never_touches_the_group_lease(monkeypatch):
    """Rejection 1's root cause, pinned.

    `<project>.none.0000` has no `groups` row, so `group_ai_leases.acquire` answered
    `FOREIGN KEY constraint failed` on a real database — invisible to every suite because
    the in-memory lease store enforces nothing. The run's admission is the PROJECT lease;
    this asserts the group lease is never reached at all.
    """
    issued: dict = {}
    admission = _admission_stubs(monkeypatch, issued)
    monkeypatch.setattr(admission._svc(), "_next_run_id", lambda: "aiv_20260908_000999")
    monkeypatch.setattr(admission._svc(), "_worker", lambda *_a, **_kw: None)
    monkeypatch.setattr(admission._svc(), "_create_scratch", lambda *_a, **_kw: "/tmp/scratch")
    result = admission.start_run(
        project_id="p1", module="none", group_id="p1.none.0000", doc_ref="p1",
        action_scope="resolve_base_dirty", mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to="u1", api_base_url="http://localhost/api/v1",
        mention_builder=lambda _t, _s: "## mention\n",
    )
    assert result["run_id"] == "aiv_20260908_000999"
    # `tokens.group_id` is a foreign key too: the token must carry no group, which is also
    # what remote_tool_service already documents for these tokens (project-root routing).
    assert issued["group_id"] is None
    assert issued["action_scope"] == "resolve_base_dirty"


def test_a_raw_start_failure_does_not_strand_the_project_lease(monkeypatch):
    """Rejection 2: why one failed press disabled the button for two minutes.

    The route rolled back on HTTPException / LookupError / ValueError only. An
    IntegrityError therefore left the acquiring lease behind, and the very next press —
    and every press until the 120s TTL — answered `base_dirty_run_in_progress`, which the
    panel renders as "이 프로젝트의 AI 정리가 진행 중입니다.".
    """
    import sqlite3

    from modules.flow_gate.api.v1 import ai_invoke_routes

    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": _p})
    monkeypatch.setattr(
        ai_invoke_routes.git_service, "project_git_status",
        lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}},
    )
    monkeypatch.setattr(ai_invoke_routes, "_operator_facing_api_base", lambda _r: "http://localhost/api/v1")
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()

    def _boom(**_kw):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _boom)
    body = ai_invoke_routes.AiInvokeStartRequest(
        project="p1", module="none", action_scope="resolve_base_dirty", mode="single",
    )
    with pytest.raises(sqlite3.IntegrityError):
        ai_invoke_routes.start_ai_invoke(body, _Request())
    # The defect still surfaces as the 500 it is, but the next press is not blocked by it.
    assert project_ai_leases.get_active("p1") is None


def test_run_lease_lost_also_releases_the_acquiring_lease(monkeypatch):
    from modules.flow_gate.api.v1 import ai_invoke_routes

    monkeypatch.setattr(ai_invoke_routes, "_require_user", lambda _r: {"issued_to": "u", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda _p: {"id": _p})
    monkeypatch.setattr(
        ai_invoke_routes.git_service, "project_git_status",
        lambda _p: {"status": {"base_dirty": {"files": ["a.py"]}}},
    )
    monkeypatch.setattr(ai_invoke_routes, "_operator_facing_api_base", lambda _r: "http://localhost/api/v1")
    monkeypatch.setattr(project_ai_leases, "_memory_mode", lambda: True)
    project_ai_leases._memory.clear()
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", lambda **_kw: {"run_id": ""})
    body = ai_invoke_routes.AiInvokeStartRequest(
        project="p1", module="none", action_scope="resolve_base_dirty", mode="single",
    )
    response = ai_invoke_routes.start_ai_invoke(body, _Request())
    assert response.status_code == 409
    assert json.loads(response.body)["code"] == "run_lease_lost"
    assert project_ai_leases.get_active("p1") is None


# ── 3. the merge review conversation ─────────────────────────────────────────

_BRIEF = {
    "review_state": "resolved_pending_review",
    "base_head": "aaaaaaa",
    "merge_head": "bbbbbbb",
    "resolver_provider": "Claude Haiku 4.5",
    "changes": [{"path": "README.md", "status": "M"}],
    "conversation": [
        {"role": "human", "message": "이번엔 뭐가 문제였지?", "status": "accepted"},
        {"role": "ai", "message": "충돌이 없습니다", "status": "stale_run"},
    ],
    "last_error": None,
    "held_test_operations": [],
}


def _conversation_mention(monkeypatch, **kwargs) -> str:
    from modules.flow_gate.api import token_routes

    monkeypatch.setattr(
        token_routes.git_service, "review_conversation_brief",
        lambda _g, _m: dict(_BRIEF),
    )
    return token_routes._build_mention_for_token(
        doc_ref="", group_id="p1.default.0001", project_id="p1",
        scratch_dir="/tmp/s", raw_token="raw", api_base_url="http://localhost/api/v1",
        action_scope="resolve_conflict", locale="ko", merge_id=7,
        review_conversation=True, **kwargs,
    )


def test_review_conversation_mention_replays_the_conversation(monkeypatch):
    """Rejection 3: "내 대화를 잘 못알아먹는 느낌이다 … 컨텍스트를 어떻게 주고있는건가?".

    Nothing of the conversation reached the run, so "이번엔 뭐가 문제였지?" had no
    antecedent and the answer came back about the current git state instead.
    """
    mention = _conversation_mention(monkeypatch)
    assert "## Conversation so far" in mention
    assert "이번엔 뭐가 문제였지?" in mention
    assert "[stale_run]" in mention
    assert "README.md" in mention
    assert "resolved_pending_review" in mention


def test_review_conversation_mention_is_not_a_resolver_prompt(monkeypatch):
    """The prompt that produced "conflict_count: 0 … resolve 요청을 보낼 수 없습니다".

    A conversation turn used to receive the resolver's task section and the bound resolve
    endpoint. Both are gone; the task is to answer.
    """
    mention = _conversation_mention(monkeypatch)
    assert "Git conflict auto-resolve task" not in mention
    assert "Bound resolve endpoint" not in mention
    assert "resolve-token" not in mention
    assert "## Conflict session" not in mention
    assert "Answer it." in mention


def test_review_conversation_write_turn_still_gets_the_write_plan_channel(monkeypatch):
    from modules.flow_gate.api import token_routes

    from modules.flow_gate.db import git_integration as db_git

    monkeypatch.setattr(db_git, "get_session", lambda _m: None)
    mention = _conversation_mention(monkeypatch, write_requested_by_human=True)
    assert "write-plan-token" in mention
    # …and still no resolve endpoint: a write turn submits a PLAN, never a resolution.
    assert "resolve-token" not in mention


def test_a_conversation_turn_may_not_submit_a_resolution(monkeypatch):
    """Both discarded answers in the rejected transcript, at their source.

    The turn's own run submitted a resolution (because the mention told it to), the
    submission re-froze the candidate, and the new fingerprint made the run's own answer
    fail the identity check — so the human got "…결과를 버렸습니다(stale_run). 다시
    지시하십시오." instead of the answer that had actually arrived.
    """
    from modules.flow_gate.services import git_service
    from modules.flow_gate.db import git_integration as db_git

    session = {"merge_id": 7, "group_id": "p1.default.0001"}
    monkeypatch.setattr(
        git_service, "_session_context",
        lambda _g, _m: (session, {"base_branch": "main"}, "p1", Path(".")),
    )
    monkeypatch.setattr(db_git, "touch_session", lambda _m: None)
    monkeypatch.setattr(
        db_git, "session_context",
        lambda _s: {"pending_conversation_run_id": "aiv_conv_1"},
    )
    with pytest.raises(git_service.GitServiceError) as excinfo:
        git_service.resolve_conflicts(
            "p1.default.0001", 7, [{"path": "README.md", "content": "x"}], True,
            resolver_run_id="aiv_conv_1",
        )
    assert excinfo.value.code == "review_conversation_cannot_resolve"
    assert excinfo.value.status == 409
