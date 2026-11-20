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
    def test_small_conflict_stays_inline_and_the_validators_still_decide(self, api_conflict_origin_0608, monkeypatch):
        """Cases D and F: an inline mention needs no read, and a `chunks` submission that
        drops a side, leaves a chunk out, or declares an unwarranted supersede is refused
        by the existing codes -- leaving file, index and session untouched -- until a
        correct one is accepted."""
        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        monkeypatch.setenv("CONTEXT", "/flowgate")
        monkeypatch.setenv("DB_TYPE", "sqlite")
        from modules.flow_gate.api import token_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service as ai_svc

        base = b"head = 0\nfirst = 'base'\nmid_a = 1\nmid_b = 2\nmid_c = 3\nmid_d = 4\nsecond = 'base'\ntail = 9\n"
        ours = base.replace(b"first = 'base'", b"first = 'main'").replace(b"second = 'base'", b"second = 'main'")
        theirs = base.replace(b"first = 'base'", b"first = 'group'").replace(b"second = 'base'", b"second = 'group'")
        group = "gitapiprj.default.0682"
        merge_id, root = self._open_session(api_conflict_origin_0608, group, {"small.py": (base, ours, theirs)})
        try:
            self._resolve_small(monkeypatch, group, merge_id, root)
        finally:
            from modules.flow_gate.services import git_service as svc
            svc.abort_merge(group, merge_id)

