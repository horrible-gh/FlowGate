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
    def test_conflict_side_dropped_422_flows_through_resolve_token_and_retries(
        self, origin_repo, monkeypatch,
    ):
        # In response to a rejected review: the review demanded proof that
        # `conflict_side_dropped` isn't just raised in-process — it must survive the real
        # POST /groups/{g}/git/merge/{m}/resolve-token route (git_routes.py:490) and be
        # handled by ai_invoke_service._api_execute's conflict_pending branch exactly like
        # every other non-2xx status (tool_result failure + continue), then let a corrected
        # resubmission succeed on retry. Only `verify_bearer` is stubbed (the worker-token
        # auth boundary itself is covered by test_tokens_resolve_conflict_scope_0233.py /
        # test_resolve_conflict_issue_lease_block_0447.py) and `urllib.request.urlopen` is
        # redirected into a real FastAPI TestClient carrying the real router — everything
        # downstream (git_routes.post_merge_resolve_token, git_service.resolve_conflicts,
        # ai_invoke_service._resolve_conflict, ai_invoke_service._api_execute) runs
        # unmocked, against the real conflict session created by finalize() below.
        import io
        import threading
        import time
        import urllib.error
        import urllib.parse
        import urllib.request

        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from modules.flow_gate.api.v1 import git_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service as ai_svc
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git_service import GitServiceError
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0114"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "sidecheck4.py").write_text("base line\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add sidecheck4 base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0114")
        (wt / "sidecheck4.py").write_text('"group change"\n', encoding="utf-8")
        (seedwt / "sidecheck4.py").write_text('"mainline change"\n', encoding="utf-8")
        _git(["commit", "-am", "mainline change to sidecheck4"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        # This test is about the HTTP retry mechanic (422 → corrected
        # resubmission → merged), not the review gate — record_auto_authority is
        # the officially supported bypass a human's [AI 호출] would have set before
        # this same worker token was minted (see test_conflict_resolve_flow above).
        svc.record_auto_authority(group, merge_id, True)

        # real FastAPI app carrying the real resolve-token route, plus the same global
        # GitServiceError envelope handler routers.main installs in production (belt and
        # braces — post_merge_resolve_token already catches GitServiceError itself).
        app = FastAPI()
        app.include_router(git_routes.router)

        @app.exception_handler(GitServiceError)
        async def _handler(request: Request, exc: GitServiceError):  # noqa: ANN202
            error: dict = {"code": exc.code, "message": exc.message}
            if exc.details:
                error["details"] = exc.details
            return JSONResponse(status_code=exc.status, content={"ok": False, "error": error})

        client = TestClient(app, raise_server_exceptions=False)
        monkeypatch.setattr(
            git_routes, "verify_bearer",
            lambda request: {
                "action_scope": "resolve_conflict", "group_id": group,
                "merge_id": merge_id, "token_id": "tok_rc_test_0478", "project": "gitprj",
            },
        )

        raw_calls: list[tuple[int, dict]] = []

        class _FakeHTTPResponse:
            def __init__(self, status, body):
                self.status = status
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        def _fake_urlopen(req, timeout=120):
            parsed = urllib.parse.urlsplit(req.full_url)
            resp = client.post(parsed.path, content=req.data, headers=dict(req.header_items()))
            raw_calls.append((resp.status_code, resp.json()))
            if 200 <= resp.status_code < 300:
                return _FakeHTTPResponse(resp.status_code, resp.content)
            raise urllib.error.HTTPError(
                req.full_url, resp.status_code, "", resp.headers, io.BytesIO(resp.content),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        # turn 1: the worker drops the mainline side entirely (same payload as (i) above,
        # this time submitted through the real HTTP tool the worker actually calls).
        # turn 2: the worker retries with both sides kept, exactly the (ii) shape.
        # The model is offered the read tools plus resolve_git_conflict (a
        # list of specs, not one forced name) and picks resolve_git_conflict itself.
        attempts: list[str] = []
        offered: list[list[str]] = []

        def fake_model(*args):
            offered.append([spec["name"] for spec in args[5]])
            tool_name = ai_svc._RESOLVE_TOOL_NAME
            attempts.append(tool_name)
            if len(attempts) == 1:
                payload = {
                    "files": [{"path": "sidecheck4.py", "content": '"group change"\n'}],
                    "complete": True,
                }
            else:
                payload = {
                    "files": [{
                        "path": "sidecheck4.py",
                        "content": '"combined:"\n"mainline change"\n"group change"\n',
                    }],
                    "complete": True,
                }
            return "resolving", {"id": f"tc{len(attempts)}", "name": tool_name, "input": payload}, {
                "role": "assistant", "content": "resolving", "tool_calls": [],
            }

        monkeypatch.setattr(ai_svc, "_call_openai", fake_model)
        monkeypatch.setattr(ai_svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")
        tool_results: list[str] = []
        _orig_tool_result_msg = ai_svc._tool_result_msg

        def _spy_tool_result_msg(kind, tool_call, text):
            tool_results.append(text)
            return _orig_tool_result_msg(kind, tool_call, text)

        monkeypatch.setattr(ai_svc, "_tool_result_msg", _spy_tool_result_msg)

        run = {
            "project_id": "gitprj", "chain_source": "system", "run_id": "aiv_conflict_retry_0478",
            "docs_target": 0, "raw_token": "tok_rc_test_0478", "action_scope": "resolve_conflict",
            "mode": "single", "group_id": group, "merge_id": merge_id,
            "api_base_url": "http://fake-host/api/v1",
            "cancel_event": threading.Event(), "started_mono": time.monotonic(), "timeout_sec": 30,
        }
        provider = {
            "id": "aip_api_0478", "exec_type": "api", "kind": "openai",
            "api_base_url": "http://fake-host", "api_model": "test-model",
        }

        result = ai_svc._api_execute(provider, "prompt", run)

        # (a) the real resolve-token HTTP round trip actually produced the new 422.
        assert [status for status, _ in raw_calls] == [422, 200]
        assert raw_calls[0][1]["error"]["code"] == "conflict_side_dropped"
        assert raw_calls[1][1]["result"]["status"] == "merged"

        # (b) _api_execute's conflict_pending branch treated the 422 exactly like any other
        # non-2xx resolve failure — tool_result + continue, no special-cased short-circuit —
        # and the worker's second, corrected attempt was retried and accepted.
        assert attempts == [ai_svc._RESOLVE_TOOL_NAME, ai_svc._RESOLVE_TOOL_NAME]
        assert offered[0][-1] == ai_svc._RESOLVE_TOOL_NAME and "read_source_file" in offered[0]
        assert not {"write_source_file", "patch_source_file", "remove_source_file"} & set(offered[0])
        assert len(tool_results) == 2
        assert "Conflict resolve failed (HTTP 422)" in tool_results[0]
        assert "conflict_side_dropped" in tool_results[0]
        assert "merged" in tool_results[1]
        assert result == ("started_ok", None)

        # the retry actually merged for real — same production side effect as (ii) above.
        assert db_git.get_session(merge_id)["status"] == "done"


