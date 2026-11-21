"""Git integration 0115: independently selectable scenario module."""
from __future__ import annotations

from test_git_integration_0115 import (
    Path,
    _API_0594_OURS,
    _API_0594_PATHS,
    _API_0594_THEIRS,
    _FILE_CLONE,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _ScriptedConflictModel0608,
    _TMP_STORAGE,
    _api_conflict_run_0608,
    _checkout_0608,
    _chunk_sides_0608,
    _git,
    _git_can_clone_local_file_url,
    _history_blob_0608,
    _ident_of,
    _index_conflict_stages_0608,
    _make_untracked_collision,
    _migration_applied,
    _node_syntax_checker_available,
    _real_reads_for_conflict_0608,
    _resolve_token_over_urlopen_0608,
    _seed_wf_done_root,
    _session_json_0608,
    _unmerged_0608,
    act_origin,
    adopt_origin,
    api_conflict_origin_0608,
    author_origin,
    base64,
    base_origin,
    contextmanager,
    grpexp_origin,
    held_locks,
    hold_lock,
    lm,
    needs_git,
    needs_node_syntax,
    noop_origin,
    nowork_surface_origin,
    origin_repo,
    os,
    patch_store,
    project_scoped_admission_origin,
    pytest,
    resolve_base_dirty_origin,
    seed,
    shutil,
    sqlite3,
    subprocess,
    sys,
    tempfile,
    terminal_reopen_origin,
    tmp_db,
    untracked_conflict_origin,
    untracked_origin,
    untracked_remove_origin,
)

from test_git_integration_0115 import TestGitEndToEnd as _Scenario


class TestGitEndToEnd(_Scenario):
    def test_adjacent_chunk_group_resolve_token_end_to_end(self, origin_repo, monkeypatch):
        """Real zdiff3 two-chunk merge: all-side rejects, synthesis reaches review."""
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from modules.flow_gate.api.v1 import git_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git_service import GitServiceError
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0604"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        base_text = (
            "def adjacent():\n"
            "    first = 'base'\n"
            "    bridge_1 = 1\n"
            "    bridge_2 = 2\n"
            "    bridge_3 = 3\n"
            "    second = 'base'\n"
        )
        ours_text = base_text.replace("'base'", "'ours'")
        theirs_text = base_text.replace("'base'", "'theirs'")
        (seedwt / "adjacent.py").write_text(base_text, encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add adjacent chunk base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0604")
        (wt / "adjacent.py").write_text(theirs_text, encoding="utf-8")
        (seedwt / "adjacent.py").write_text(ours_text, encoding="utf-8")
        _git(["commit", "-am", "change adjacent chunks on main"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        finalized = svc.finalize(group, "merge")
        assert finalized["result"]["status"] == "conflict"
        merge_id = finalized["result"]["merge_id"]
        conflict = svc.list_conflicts(group, merge_id)["files"][0]["content"]
        assert conflict.count("<<<<<<<") == 2
        conflict_root = svc.resolve_conflict_src_root(group, merge_id)
        before = (conflict_root / "adjacent.py").read_bytes()

        app = FastAPI()
        app.include_router(git_routes.router)

        @app.exception_handler(GitServiceError)
        async def _handler(request: Request, exc: GitServiceError):  # noqa: ANN202
            error = {"code": exc.code, "message": exc.message}
            if exc.details:
                error["details"] = exc.details
            return JSONResponse(status_code=exc.status, content={"ok": False, "error": error})

        monkeypatch.setattr(
            git_routes, "verify_bearer",
            lambda request: {
                "action_scope": "resolve_conflict", "group_id": group,
                "merge_id": merge_id, "token_id": "tok_adjacent_0604", "project": "gitprj",
            },
        )
        client = TestClient(app, raise_server_exceptions=False)
        endpoint = f"/api/v1/groups/{group}/git/merge/{merge_id}/resolve-token"

        for one_side in (ours_text, theirs_text):
            response = client.post(endpoint, json={
                "files": [{"path": "adjacent.py", "content": one_side}],
                "complete": True,
            }, headers={"Authorization": "Bearer test"})
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "conflict_side_dropped"
            assert (conflict_root / "adjacent.py").read_bytes() == before

        combined = (
            "def adjacent():\n"
            "    first = 'ours+theirs'\n"
            "    bridge_1 = 1\n"
            "    bridge_2 = 2\n"
            "    bridge_3 = 3\n"
            "    second = 'theirs'\n"
        )
        accepted = client.post(endpoint, json={
            "files": [{"path": "adjacent.py", "content": combined}],
            "complete": True,
        }, headers={"Authorization": "Bearer test"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["result"]["status"] == "resolved_pending_review"
        review = svc.get_merge_review(group, merge_id)["result"]
        origins = [row for row in review["conflict_origins"] if row["path"] == "adjacent.py"]
        assert [row["selection"] for row in origins] == ["manual", "theirs"]

        aborted = svc.abort_merge(group, merge_id)
        assert aborted["result"]["status"] == "waiting"


