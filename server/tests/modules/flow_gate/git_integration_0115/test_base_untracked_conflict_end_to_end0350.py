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

from test_git_integration_0115 import TestBaseUntrackedConflictEndToEnd0350 as _Scenario


class TestBaseUntrackedConflictEndToEnd0350(_Scenario):
    def test_merge_and_merge_only_both_hit_the_same_409(self, untracked_conflict_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_conflict_origin["base"]
        for action, group in [
            ("merge", "utcprj.default.0350a"),
            ("merge_only", "utcprj.default.0350b"),
        ]:
            _make_untracked_collision(svc, db_git, src_root, group, "clash.txt")
            (base / "clash.txt").write_text("base version\n", encoding="utf-8")
            try:
                with pytest.raises(svc.GitServiceError) as exc:
                    svc.finalize(group, action)
                assert exc.value.status == 409
                assert exc.value.code == "base_untracked_conflict"
                assert exc.value.details == {"files": ["clash.txt"], "stage": "merge", "cause": "conflict_other"}
            finally:
                (base / "clash.txt").unlink()

