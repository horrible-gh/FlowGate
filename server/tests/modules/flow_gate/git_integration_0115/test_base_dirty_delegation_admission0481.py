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

from test_git_integration_0115 import TestBaseDirtyDelegationAdmission0481 as _Scenario


class TestBaseDirtyDelegationAdmission0481(_Scenario):
    def test_group_worktree_gate_lets_a_project_scoped_run_through(self, project_scoped_admission_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services.ai_invoke import admission

        # The same arguments start_run passes for a resolve_base_dirty press.
        admission._require_group_worktree(
            "psaprj", "none", self.PHANTOM_GROUP, "main",
            locale="ko", action_scope="resolve_base_dirty",
        )
        # …and it must not have INVENTED the group on the way through. Before this fix the
        # gate's ensure_worktree self-heal happily provisioned a real branch
        # (`psaprj_none_0000`), a checked-out worktree and a group_git_state row for a group
        # that does not exist — junk that outlives the press.
        assert db_git.get_state(self.PHANTOM_GROUP) is None


    def test_initial_source_sync_gate_lets_a_project_scoped_run_through(self, project_scoped_admission_origin):
        from modules.flow_gate.services.ai_invoke import admission

        admission._ensure_initial_source_sync(
            "psaprj", "none", self.PHANTOM_GROUP, "resolve_base_dirty", "psaprj", locale="ko",
        )


    def test_an_ordinary_scope_still_goes_through_the_base_tree_guard(self, project_scoped_admission_origin):
        # The base-tree guard itself (0299 R0001) must not be weakened for the scopes it was
        # written for. An `edit` run on a group with no worktree still engages the gate, and
        # the gate's self-heal provisions the group — the exact work the project-scoped skip
        # above must NOT do for a group that does not exist.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services.ai_invoke import admission

        assert db_git.get_state("psaprj.default.0001") is None
        admission._require_group_worktree(
            "psaprj", "default", "psaprj.default.0001", "main",
            locale="ko", action_scope="edit",
        )
        state = db_git.get_state("psaprj.default.0001")
        assert state is not None and state["branch"] == "psaprj_default_0001"


