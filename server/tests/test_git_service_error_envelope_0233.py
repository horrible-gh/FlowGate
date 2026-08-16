"""flowgate.default.0233 B0001 — unresolved git-conflict mention-copy/AI-invoke 500.

The conflict [멘트복사]([token/issue]) and [AI호출]([ai-invoke/start]) buttons build
their mention through git_service.list_conflicts. When the pending card's merge_id
points at a merge session that is no longer `open` (stale header card, or a session
closed by another tab / the 0229 auto-resolver), list_conflicts raises
GitServiceError(404). GitServiceError is a plain domain Exception, so — unlike the
git_routes handlers, which each wrap it in `_guard` — these two endpoints let it leak
out as a bodyless HTTP 500 (empty toast, no guidance).

NR0003 fix: a single global GitServiceError handler in routers.main converts it to the
same {"ok": false, "error": {code, message}} envelope for every route. These tests pin
that behavior at the route boundary:
  (a) non-open session ⇒ the git envelope with the real 4xx status, NEVER 500;
  (b) an open/live conflict session still returns 200 (+ mention for token/issue).

Follows the house TestClient pattern (minimal app + real routers), so the assertions
exercise the actual token_routes / ai_invoke_routes code paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.auth.middleware import get_current_user  # noqa: E402
from modules.flow_gate.api import token_routes  # noqa: E402
from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services.git_service import GitServiceError  # noqa: E402


# The production handler from routers.main, replicated here (importing the full app
# pulls in startup/bootstrap — every TestClient test in this suite assembles a
# minimal app the same way). A regression that drops the handler makes list_conflicts'
# GitServiceError leak again and these tests flip to 500.
def _install_git_error_handler(app: FastAPI) -> None:
    @app.exception_handler(GitServiceError)
    async def _handler(request: Request, exc: GitServiceError):  # noqa: ANN202
        error: dict = {"code": exc.code, "message": exc.message}
        if exc.details:
            error["details"] = exc.details
        return JSONResponse(status_code=exc.status, content={"ok": False, "error": error})


_OPEN_CONFLICTS = {
    "branch": "grp/0233",
    "base_branch": "main",
    "files": [
        {
            "path": "app/x.py",
            "conflict_count": 1,
            "content": "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n",
        }
    ],
}


def _stale_session(*_a, **_kw):
    raise GitServiceError(404, "not_found", "merge session 5 not found")


# ── /token/issue (copy-mention) ──────────────────────────────────────────────

def _token_client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(token_routes.router)
    _install_git_error_handler(app)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin"}
    monkeypatch.setattr(token_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    monkeypatch.setattr(token_routes, "_resolve_group", lambda p, g: "flowgate.default.0233")
    monkeypatch.setattr(token_routes, "has_permission", lambda *a, **k: True)
    monkeypatch.setattr(
        token_routes.token_service, "issue",
        lambda **kw: {
            "raw_token": "RAW", "token_id": "tok_1",
            "expires_at": "2026-07-15T00:00:00+00:00",
            "scratch_dir": "/tmp/scratch", "group_id": "flowgate.default.0233",
        },
    )
    return TestClient(app, raise_server_exceptions=False)


def _issue_body():
    return {"project": "flowgate", "group": "0233",
            "action_scope": "resolve_conflict", "merge_id": 5}


def test_token_issue_stale_session_returns_envelope_not_500(monkeypatch):
    client = _token_client(monkeypatch)
    monkeypatch.setattr(token_routes.git_service, "list_conflicts", _stale_session)

    resp = client.post("/api/v1/token/issue", json=_issue_body())

    assert resp.status_code == 404          # not 500 — the whole bug
    assert resp.json() == {
        "ok": False,
        "error": {"code": "not_found", "message": "merge session 5 not found"},
    }


def test_token_issue_open_session_still_returns_mention(monkeypatch):
    client = _token_client(monkeypatch)
    monkeypatch.setattr(token_routes.git_service, "list_conflicts",
                        lambda *a, **k: dict(_OPEN_CONFLICTS))

    resp = client.post("/api/v1/token/issue", json=_issue_body())

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "Git conflict auto-resolve task" in body["mention"]
    assert "merge_id: 5" in body["mention"]


# ── /ai-invoke/start (AI invoke) ─────────────────────────────────────────────

def _ai_client(monkeypatch, *, list_conflicts, captured_mentions: list[str] | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    _install_git_error_handler(app)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    monkeypatch.setattr(token_routes.git_service, "list_conflicts", list_conflicts)

    # Faithfully reproduce the resolve_conflict path: start_run invokes the injected
    # mention_builder, whose conflict branch calls list_conflicts. A GitServiceError
    # there must propagate exactly as it does in production (start_run's except only
    # catches HTTPException/LookupError/ValueError — never GitServiceError).
    def fake_start_run(**kw):
        mention = kw["mention_builder"]("RAW", "/tmp/scratch")
        if captured_mentions is not None:
            captured_mentions.append(mention)
        return {"run_id": "aiv_1", "status": "running"}

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", fake_start_run)
    return TestClient(app, raise_server_exceptions=False)


def _start_body():
    return {"project": "flowgate", "group": "0233",
            "action_scope": "resolve_conflict", "mode": "single", "merge_id": 5}


def test_ai_invoke_stale_session_returns_envelope_not_500(monkeypatch):
    client = _ai_client(monkeypatch, list_conflicts=_stale_session)

    resp = client.post("/api/v1/ai-invoke/start",
                       json=_start_body(), headers={"Authorization": "Bearer tok"})

    assert resp.status_code == 404          # not 500
    assert resp.json() == {
        "ok": False,
        "error": {"code": "not_found", "message": "merge session 5 not found"},
    }


def test_ai_invoke_open_session_starts(monkeypatch):
    client = _ai_client(monkeypatch, list_conflicts=lambda *a, **k: dict(_OPEN_CONFLICTS))

    resp = client.post("/api/v1/ai-invoke/start",
                       json=_start_body(), headers={"Authorization": "Bearer tok"})

    assert resp.status_code == 200
    assert resp.json()["run_id"] == "aiv_1"


def test_ai_invoke_prepends_conflict_delivery_message(monkeypatch):
    captured_mentions: list[str] = []
    client = _ai_client(
        monkeypatch,
        list_conflicts=lambda *a, **k: dict(_OPEN_CONFLICTS),
        captured_mentions=captured_mentions,
    )
    body = {
        **_start_body(),
        "messages": ["현재 소스를 과거 버전으로 되돌리지 말고 변경을 보존해 주세요."],
    }

    resp = client.post(
        "/api/v1/ai-invoke/start",
        json=body,
        headers={"Authorization": "Bearer tok", "x-locale": "ko"},
    )

    assert resp.status_code == 200
    assert len(captured_mentions) == 1
    assert captured_mentions[0].startswith(
        "## 사용자 메세지\n---\n"
        "현재 소스를 과거 버전으로 되돌리지 말고 변경을 보존해 주세요."
    )
    assert "Git conflict auto-resolve task" in captured_mentions[0]
