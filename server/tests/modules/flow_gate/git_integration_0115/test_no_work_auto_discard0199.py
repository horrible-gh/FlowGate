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

from test_git_integration_0115 import TestNoWorkAutoDiscard0199 as _Scenario


class TestNoWorkAutoDiscard0199(_Scenario):
    def test_realize_transition_discards_no_work_group(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0210"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        assert db_git.get_state(group)["worktree_registered"] == 1
        _seed_wf_done_root(group)

        # Eager approval-time realization: no work → auto-discard, not awaiting_choice.
        svc.realize_wf_done_transition(group)

        state = db_git.get_state(group)
        # Slot torn down: unregistered, DB status left at the neutral "none"
        # ("discarded" is a response label only — the status CHECK has no such value).
        assert state["worktree_registered"] == 0
        assert state["status"] == "none"
        # No finalize gate is surfaced for a discarded group.
        assert svc.get_finalize_state(group)["state"]["status"] == "none"
        # Base got NO empty merge commit; origin got NO leaked work branch.
        assert self._origin_main_commits(noop_origin) == 1        # init only
        assert "gitnoop_default_0210" not in self._origin_heads(noop_origin)


    def test_no_work_group_absent_from_status_lists(self, noop_origin):
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0211"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        _seed_wf_done_root(group)

        # Aggregation-time realization also discards; the group never appears as a
        # pending finalize nor as an active slot.
        out = svc.project_git_status("gitnoop")["status"]
        assert group not in {p["group_id"] for p in out["pending"]}
        assert group not in {s["group_id"] for s in out["slots"]}


    def test_real_work_group_still_gated(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitnoop.default.0212"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        wt = src_root("GitNoop", "gitnoop_default_0212")
        # An uncommitted worktree edit is real, mergeable work (finalize absorbs it).
        (wt / "work.txt").write_text("real work\n", encoding="utf-8")
        _seed_wf_done_root(group)

        svc.realize_wf_done_transition(group)
        state = db_git.get_state(group)
        assert state["status"] == "awaiting_choice"    # real work → keep the gate
        assert state["worktree_registered"] == 1


    def test_finalize_merge_no_change_short_circuits(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0213"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        # Force the gate open on a clean worktree (branch at base, no work) and
        # explicitly pick merge — the no-change guard must still refuse to stamp an
        # empty merge commit / push.
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "merge")["result"]
        assert out["status"] == "discarded"
        assert out["pushed"] is False
        assert out["merge_commit"] is None
        # No empty merge commit on base/origin, no leaked branch, slot cleaned.
        assert self._origin_main_commits(noop_origin) == 1
        assert "gitnoop_default_0213" not in self._origin_heads(noop_origin)
        assert db_git.get_state(group)["worktree_registered"] == 0


    def test_finalize_push_no_change_short_circuits(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0214"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "push")["result"]
        assert out["status"] == "discarded"
        # push of a no-work group must NOT leak an empty branch to origin.
        assert "gitnoop_default_0214" not in self._origin_heads(noop_origin)
        assert db_git.get_state(group)["worktree_registered"] == 0


