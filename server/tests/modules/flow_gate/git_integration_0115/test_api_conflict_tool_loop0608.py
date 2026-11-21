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

from test_git_integration_0115 import TestApiConflictToolLoop0608 as _Scenario


class TestApiConflictToolLoop0608(_Scenario):
    def test_0594_scale_conflict_is_read_and_resolved_through_the_api_tool_loop(
        self, api_conflict_origin_0608, monkeypatch,
    ):
        """Cases A, B, C and E: 0594's real blobs, a location-only mention, reads through
        the real remote pipeline, six partial submits, `resolved_pending_review`."""
        import json

        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        monkeypatch.setenv("CONTEXT", "/flowgate")
        monkeypatch.setenv("DB_TYPE", "sqlite")
        from modules.flow_gate.api import token_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service as ai_svc
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git import conflict as git_conflict

        for rev in (_API_0594_OURS, _API_0594_THEIRS):
            probe = subprocess.run(["git", "cat-file", "-e", f"{rev}^{{commit}}"],
                                   cwd=str(_checkout_0608()), capture_output=True)
            if probe.returncode:
                pytest.fail(f"0594 replay needs commit {rev} in {_checkout_0608()} (full clone required)")
        base_rev = subprocess.run(["git", "merge-base", _API_0594_OURS, _API_0594_THEIRS],
                                  cwd=str(_checkout_0608()), capture_output=True, text=True).stdout.strip()
        files = {
            path: (_history_blob_0608(base_rev, path), _history_blob_0608(_API_0594_OURS, path),
                   _history_blob_0608(_API_0594_THEIRS, path))
            for path in _API_0594_PATHS
        }
        group = "gitapiprj.default.0681"
        merge_id, root = self._open_session(api_conflict_origin_0608, group, files)
        try:
            self._resolve_0594(monkeypatch, group, merge_id, root, files)
        finally:
            svc.abort_merge(group, merge_id)

