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

from test_git_integration_0115 import TestNoWorkFinalizeSurfaces0548 as _Scenario


class TestNoWorkFinalizeSurfaces0548(_Scenario):
    def test_the_whole_no_work_approval_says_nothing_about_git(self, nowork_surface_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnowork.default.0013"
        assert svc.ensure_worktree("gitnowork", "default", group) == "ok"
        # …and then nobody touches a single source file.

        # 1) 머지 다이얼로그 — the AC confirm dialog asks with context=approval while
        #    the root is still wf_in_progress. No work ⇒ no choice block.
        preview = svc.get_finalize_state(group, preview_ac=True)["state"]
        assert preview["status"] == "none"
        assert preview["choices"] == []
        assert preview["action_axes"] is None

        # 2) the approval itself: the root flips to wf_done and the approve path
        #    realizes the git transition, which discards the empty slot (0199).
        _seed_wf_done_root(group, project_id="gitnowork")
        svc.realize_wf_done_transition(group)
        assert db_git.get_state(group)["worktree_registered"] == 0

        # 3) A stale ride-along is an honest Git failure now; it can no longer be
        #    hidden as a quiet success after approval has already stood.
        out = svc.run_approve_git_action(group, "merge")
        assert out["ok"] is False
        assert out["terminal"] is False
        assert out.get("quiet") is None
        assert out["error"]["code"] == "invalid_state"

        # 4) 문서에 머지 섹션 — GitFinalizePanel renders only on status != 'none'.
        assert svc.get_finalize_state(group)["state"]["status"] == "none"

        # And none of this quiet ever became a silent merge/push.
        assert self._origin_main_commits(nowork_surface_origin) == 1
        assert "gitnowork_default_0013" not in _git(
            ["ls-remote", "--heads", str(nowork_surface_origin["bare"])]
        )


    def test_a_group_that_really_worked_keeps_every_git_surface(self, nowork_surface_origin):
        """T0004 §4 — the other half of the contract, on the same real repo."""
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitnowork.default.0014"
        assert svc.ensure_worktree("gitnowork", "default", group) == "ok"
        wt = src_root("GitNoWork", "gitnowork_default_0014")
        (wt / "work.txt").write_text("real work\n", encoding="utf-8")

        preview = svc.get_finalize_state(group, preview_ac=True)["state"]
        assert preview["status"] == "awaiting_choice"
        assert preview["choices"]

        _seed_wf_done_root(group, project_id="gitnowork")
        svc.realize_wf_done_transition(group)
        assert svc.get_finalize_state(group)["state"]["status"] == "awaiting_choice"


    def test_a_stale_pending_slot_with_nothing_in_it_loses_the_gate(
        self, nowork_surface_origin,
    ):
        """The durable form of 문서에 머지 섹션: a slot that entered the gate before
        0199's entry guard existed (or while divergence was briefly unmeasurable)
        used to sit in `awaiting_choice` forever with nothing to finalize."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnowork.default.0015"
        assert svc.ensure_worktree("gitnowork", "default", group) == "ok"
        _seed_wf_done_root(group, project_id="gitnowork")
        db_git.set_status(group, "awaiting_choice")

        assert svc.get_finalize_state(group)["state"]["status"] == "none"
        assert db_git.get_state(group)["worktree_registered"] == 0
        # The header's pending list drops it in the same breath.
        out = svc.project_git_status("gitnowork")["status"]
        assert group not in {p["group_id"] for p in out["pending"]}


