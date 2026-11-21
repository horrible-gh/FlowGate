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

from test_git_integration_0115 import TestBaseRemoveAndRetry0350 as _Scenario


class TestBaseRemoveAndRetry0350(_Scenario):
    def test_remove_deletes_and_validates(self, untracked_remove_origin):
        from modules.flow_gate.services import git_service as svc

        base = untracked_remove_origin["base"]
        (base / "gone.txt").write_text("delete me\n", encoding="utf-8")

        # path validation happens before anything reaches git
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", [])
        assert exc.value.status == 422
        for bad in ["/etc/passwd", "../outside.txt", "a/../../b"]:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.base_remove("utcrmprj", [bad])
            assert exc.value.status == 422 and exc.value.code == "invalid_request"

        # a TRACKED file is never a delete target — that is base_revert's job
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", ["README.md"])
        assert exc.value.status == 422 and exc.value.code == "invalid_request"
        assert exc.value.details == {"files": ["README.md"]}
        assert (base / "README.md").is_file()   # untouched by the refusal

        # a gitignore'd path gets its own honest code, never a silent force-clean
        (base / "keys.secret").write_text("shh\n", encoding="utf-8")
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", ["keys.secret"])
        assert exc.value.code == "path_ignored"
        assert exc.value.details == {"files": ["keys.secret"]}
        (base / "keys.secret").unlink()

        # a path git does not currently see as untracked (never existed) is refused
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", ["never_existed.txt"])
        assert exc.value.status == 422 and exc.value.code == "invalid_request"
        assert exc.value.details == {"files": ["never_existed.txt"]}

        out = svc.base_remove("utcrmprj", ["gone.txt"])["result"]
        assert out["results"] == [{"path": "gone.txt", "result": "removed"}]
        assert not (base / "gone.txt").exists()
        assert "gone.txt" not in out["remaining_untracked"]

