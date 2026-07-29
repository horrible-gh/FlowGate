"""flowgate.default.0350 — route boundary for POST …/git/base-remove.

`test_git_integration_0115.py::TestBaseRemoveAndRetry0350` exercises
`git_service.base_remove` directly, so the *service* is well covered. The HTTP
route that TR0005 added alongside it was not: nothing asserted the path string,
the request-body field name, the RBAC dependency, or that a `GitServiceError`
leaves as the git envelope rather than a bodyless 500. Every one of those is a
contract the client depends on —

    GitUntrackedConflictDialog.vue / GitStatusPanel.vue
      → POST `/api/v1/projects/${projectId}/git/base-remove`  { files: [...] }

and the matching client assertion lives in
`client/tests/main/GitUntrackedConflictDialog.spec.ts`. Both sides pin the same
literal, so a rename on either side turns one of them red instead of shipping a
404 into the recovery flow that is supposed to unblock a stalled merge.

Follows the house minimal-app TestClient pattern (see
test_git_service_error_envelope_0233.py): real router, overridden auth.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import git_routes  # noqa: E402
from modules.flow_gate.auth.middleware import get_current_user  # noqa: E402
from modules.flow_gate.services.git_service import GitServiceError  # noqa: E402

# The exact URL the two client components build. Kept as a literal, not an
# f-string over the route object, so a route rename cannot silently follow it.
_PATH = "/api/v1/projects/flowgate/git/base-remove"
_REVERT_PATH = "/api/v1/projects/flowgate/git/base-revert"


def _client(*, is_admin: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "usr_admin",
        "is_admin": is_admin,
    }
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def calls(monkeypatch):
    """Records what the route hands to the service layer."""
    seen: dict[str, list] = {"remove": [], "revert": []}

    def fake_remove(project_id, files):
        seen["remove"].append((project_id, files))
        return {"ok": True, "result": {"removed": True, "files": list(files)}}

    def fake_revert(project_id, files):
        seen["revert"].append((project_id, files))
        return {"ok": True, "result": {"reverted": True}}

    monkeypatch.setattr(git_routes.git_service, "base_remove", fake_remove)
    monkeypatch.setattr(git_routes.git_service, "base_revert", fake_revert)
    return seen


class TestBaseRemoveRoute0350:
    def test_registered_at_the_path_the_client_calls(self):
        routes = {
            (route.path, method)
            for route in git_routes.router.routes
            for method in getattr(route, "methods", set())
        }
        assert ("/api/v1/projects/{project_id}/git/base-remove", "POST") in routes

    def test_delegates_the_files_list_verbatim(self, calls):
        payload = {"files": ["clash.txt", "nested/dir/other.txt"]}

        resp = _client().post(_PATH, json=payload)

        assert resp.status_code == 200
        assert resp.json()["result"]["files"] == payload["files"]
        assert calls["remove"] == [("flowgate", payload["files"])]

    def test_body_field_is_files_and_defaults_to_empty(self, calls):
        # An omitted list must still reach the service, which owns the 422 —
        # the route never invents its own validation message for this.
        resp = _client().post(_PATH, json={})

        assert resp.status_code == 200
        assert calls["remove"] == [("flowgate", [])]

    @pytest.mark.parametrize(
        ("status", "code", "details"),
        [
            (422, "path_ignored", {"files": ["keys.secret"]}),
            (409, "git_busy", {}),
            (409, "invalid_state", {}),
        ],
    )
    def test_service_error_becomes_the_git_envelope_never_500(
        self, monkeypatch, status, code, details
    ):
        def boom(project_id, files):
            raise GitServiceError(status, code, "nope", details or None)

        monkeypatch.setattr(git_routes.git_service, "base_remove", boom)

        resp = _client().post(_PATH, json={"files": ["x.txt"]})

        assert resp.status_code == status
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["code"] == code
        assert body["error"]["message"] == "nope"
        if details:
            assert body["error"]["details"] == details

    def test_requires_the_edit_permission(self, calls, monkeypatch):
        # Non-admin with no grants: the destructive route must refuse before the
        # service is ever reached.
        monkeypatch.setattr(
            git_routes, "_has_permission", lambda *a, **k: False, raising=False
        )
        monkeypatch.setattr(
            "modules.flow_gate.rbac.decorators._permission_service.has_permission",
            lambda *a, **k: False,
        )

        resp = _client(is_admin=False).post(_PATH, json={"files": ["x.txt"]})

        assert resp.status_code == 403
        assert calls["remove"] == []

    def test_never_folded_into_the_tracked_revert_route(self, calls):
        """T0004 §2.1: the destructive delete stays a separate entry point.

        base-revert restores tracked content that still exists in HEAD;
        base-remove discards the only copy of a file that was never committed.
        Routing one through the other would make an undoable action reachable
        from an undo-safe button.
        """
        client = _client()
        client.post(_REVERT_PATH, json={"files": ["tracked.txt"]})

        assert calls["revert"] == [("flowgate", ["tracked.txt"])]
        assert calls["remove"] == []

    def test_handler_is_sync_so_it_runs_off_the_event_loop(self):
        """`base_remove` shells out to git; an `async def` handler would block
        the loop for the whole subprocess. Mirrors base-commit/base-revert."""
        for handler in (
            git_routes.post_git_base_remove,
            git_routes.post_git_base_commit,
            git_routes.post_git_base_revert,
        ):
            assert not inspect.iscoroutinefunction(handler), handler.__name__
