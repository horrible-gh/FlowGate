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

from test_git_integration_0115 import TestLock as _Scenario


class TestLock(_Scenario):
    def test_acquire_busy_release_reenter(self, seed):
        from modules.flow_gate.services.git import lock_manager as lm

        ctx_a, ctx_b = lm.new_context(), lm.new_context()
        first = lm.acquire("B", "gitprj", holder_kind="base_mutation", ctx=ctx_a, mode=lm.NO_WAIT)
        assert first.ok
        busy = lm.acquire("B", "gitprj", holder_kind="publish", ctx=ctx_b, mode=lm.NO_WAIT)
        assert busy.kind == lm.BUSY and busy.blocker["holder_kind"] == "base_mutation"
        with pytest.raises(lm.LockProgramError):
            lm.release(ctx_b, first.lock_key)         # a non-holder cannot release
        assert [r["holder_kind"] for r in held_locks("gitprj")] == ["base_mutation"]
        again = lm.acquire("B", "gitprj", holder_kind="base_mutation", ctx=ctx_a, mode=lm.NO_WAIT)
        assert again.ok and again.reentrant
        lm.release(ctx_a, first.lock_key)
        assert held_locks("gitprj") != []              # still held once (re-entrant count)
        lm.release(ctx_a, first.lock_key)
        assert held_locks("gitprj") == []


    def test_different_groups_hold_g_at_once_and_same_group_is_busy(self, seed):
        from modules.flow_gate.services.git import lock_manager as lm

        ctx_a, ctx_b, ctx_c = lm.new_context(), lm.new_context(), lm.new_context()
        ga = lm.acquire("G", "gitprj", group_id="gitprj.default.g1", holder_kind="source_mutation",
                        ctx=ctx_a, mode=lm.NO_WAIT)
        gb = lm.acquire("G", "gitprj", group_id="gitprj.default.g2", holder_kind="source_mutation",
                        ctx=ctx_b, mode=lm.NO_WAIT)
        same = lm.acquire("G", "gitprj", group_id="gitprj.default.g1", holder_kind="tr_commit",
                          ctx=ctx_c, mode=lm.NO_WAIT)
        assert ga.ok and gb.ok and same.kind == lm.BUSY
        lm.release(ctx_a, ga.lock_key)
        lm.release(ctx_b, gb.lock_key)
        assert held_locks("gitprj") == []


