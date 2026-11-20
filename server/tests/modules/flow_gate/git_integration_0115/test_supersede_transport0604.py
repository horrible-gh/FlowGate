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

from test_git_integration_0115 import TestSupersedeTransport0604 as _Scenario


class TestSupersedeTransport0604(_Scenario):
    @pytest.mark.parametrize("route", ["resolve", "resolve-token"])
    def test_route_forwards_supersede_to_resolve_conflicts(self, monkeypatch, route):
        client, captured = self._client(monkeypatch)
        response = client.post(
            f"/api/v1/groups/gitprj.default.0690/git/merge/7/{route}",
            json={"files": [
                {"path": "a.py", "content": "x\n", "supersede": self.DECL},
                {"path": "b.py", "content": "y\n"},
            ], "complete": False},
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200, response.text
        files = captured[0]["files"]
        assert files[0]["supersede"] == self.DECL
        assert files[1].get("supersede") is None


    @pytest.mark.parametrize("route", ["resolve", "resolve-token"])
    def test_unknown_supersede_key_and_top_level_extra_stay_forbidden(self, monkeypatch, route):
        client, captured = self._client(monkeypatch)
        base = f"/api/v1/groups/gitprj.default.0690/git/merge/7/{route}"
        bad_decl = client.post(base, json={"files": [
            {"path": "a.py", "content": "x\n", "supersede": {**self.DECL, "force": True}},
        ]}, headers={"Authorization": "Bearer test"})
        assert bad_decl.status_code == 422
        extra = client.post(base, json={"files": [], "approve": True},
                            headers={"Authorization": "Bearer test"})
        assert extra.status_code == 422
        assert captured == []


    def test_api_worker_forwards_supersede_verbatim(self, monkeypatch):
        import io
        import json as _json

        from modules.flow_gate.services.ai_invoke import worker

        sent: list = []

        class _Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            sent.append(_json.loads(req.data.decode("utf-8")))
            return _Resp(b'{"ok": true}')

        monkeypatch.setattr(worker.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(worker.provider_api, "_resolve_transport_api_base", lambda run: "http://x/api/v1")
        monkeypatch.setattr(worker.provider_api, "_sanitize_diagnostic_base", lambda base: base)
        files = [{"path": "a.py", "content": "x\n", "supersede": self.DECL}]
        status, _body = worker._resolve_conflict(
            {"group_id": "g", "merge_id": 1}, "tok", {"files": files, "complete": True},
        )
        assert status == 200
        assert sent[0]["files"] == files


    def test_resolve_tool_schema_declares_optional_supersede(self):
        from modules.flow_gate.services.ai_invoke.runtime import _RESOLVE_TOOL_SCHEMA

        item = _RESOLVE_TOOL_SCHEMA["properties"]["files"]["items"]
        # 0608 T0007: content OR chunks (resolve_conflicts 422s unless exactly one is sent)
        assert item["required"] == ["path"]
        assert item["properties"]["chunks"]["items"]["required"] == ["chunk", "content"]
        supersede = item["properties"]["supersede"]
        assert supersede["properties"]["side"]["enum"] == ["ours", "theirs"]
        assert supersede["required"] == ["side", "reason"]
        assert "conflict_side_dropped" in supersede["description"]


    def test_conflict_mention_explains_supersede_conditions(self, monkeypatch):
        # token_routes reaches config.Settings(), which requires these (same values as
        # test_scratch_sentinel_cross_surface_0506.py).
        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        monkeypatch.setenv("CONTEXT", "/flowgate")
        monkeypatch.setenv("DB_TYPE", "sqlite")
        from modules.flow_gate.api import token_routes

        monkeypatch.setattr(token_routes.git_service, "list_conflicts", lambda g, m: {
            "ok": True, "merge_id": m, "branch": "b", "base_branch": "main", "kind": "merge",
            "tr_conflict": None,
            "files": [{"path": "a.py", "content": "<<<<<<< a\nx\n=======\ny\n>>>>>>> b\n", "conflict_count": 1}],
        })
        mention = token_routes._build_conflict_mention(
            group_id="gitprj.default.0690", project_id="gitprj", merge_id=7,
            scratch_dir="/tmp/scratch", raw_token="tok", api_base_url="http://x/api/v1",
        )
        section = mention.split("## Superset declaration (`supersede`)", 1)[1].split("## ", 1)[0]
        assert '"supersede": {"side": "ours|theirs"' in section
        for needle in ("conflict_side_dropped", "conflict_supersede_invalid", "ALREADY contains",
                       "never auto-approved", "Do not invent lines"):
            assert needle in section
        assert "Normally the fix is to merge both sides" in section


