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

from test_git_integration_0115 import TestFinalizeActionContract0331 as _Scenario


class TestFinalizeActionContract0331(_Scenario):
    def test_action_vocabulary_is_additive(self):
        from modules.flow_gate.services import git_service as svc

        assert svc.ACTION_VALUES == (
            "merge", "merge_only", "push", "commit_push", "commit_only", "wait",
        )
        # Deliberately untouched. The axis UI reads `action_axes` (below), so the
        # legacy choice lists stay frozen as the fallback an older client renders
        # (NR0005 §8 "additive"); widening them would change what that client
        # shows without it knowing the new semantics. The project default stays
        # narrow because its DB CHECK still only allows merge/push/wait.
        assert svc.DEFAULT_FINALIZE_ACTION_VALUES == ("merge", "push", "wait")
        assert svc.FINALIZE_MAIN_CHOICES == ("merge", "merge_only", "wait")
        assert svc.FINALIZE_AUX_CHOICES == ("push",)


    def test_finalize_state_publishes_the_axis_matrix(self, git_active_group):
        from modules.flow_gate.services import git_service as svc

        state = svc.get_finalize_state(git_active_group)["state"]
        axes = state["action_axes"]
        # Approved v4 order — 머지 → 커밋 → 대기 — comes from the server, so the
        # three finalize surfaces cannot drift into different orders.
        assert axes["scopes"] == ["merge", "commit", "none"]
        assert axes["matrix"] == {
            "merge": {"push": "merge", "no_push": "merge_only"},
            "commit": {"push": "commit_push", "no_push": "commit_only"},
            "none": {"push": "push", "no_push": "wait"},
        }
        # 3 scopes x push on/off covers the whole vocabulary exactly once: no
        # action is unreachable from the UI and none is offered twice.
        cells = [a for pair in axes["matrix"].values() for a in pair.values()]
        assert sorted(cells) == sorted(svc.ACTION_VALUES)
        # `push` is NOT a committing action any more (it 409s on dirty instead of
        # absorbing), so the panel must not ask for a commit subject there.
        assert set(axes["commit_actions"]) == set(svc.ACTION_VALUES) - {"push", "wait"}
        # Additive: the legacy lists ride along untouched for an older client.
        assert state["choices"] == list(svc.FINALIZE_MAIN_CHOICES)
        assert state["aux_choices"] == list(svc.FINALIZE_AUX_CHOICES)


    def test_axis_matrix_is_absent_when_nothing_is_actionable(self, git_active_group):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        # A terminal slot offers no choices; the axis payload has to disappear
        # with them or the panel would render a live control over a done group.
        db_git.set_status(git_active_group, "merged")
        state = svc.get_finalize_state(git_active_group)["state"]
        assert state["action_axes"] is None
        assert state["choices"] == []


    def test_finalize_validator_accepts_the_new_actions(self, git_active_group, monkeypatch):
        from modules.flow_gate.services import git_service as svc

        # The base checkout does not exist here either; the origin sync that runs before the
        # worktree check cannot start git in a missing directory (Windows raises
        # NotADirectoryError for it), and it is not what this test is about.
        monkeypatch.setattr(svc, "ensure_origin_matches_config", lambda *_a, **_k: None)

        # An unknown action stops at the 422 gate; the two new ones pass it and
        # only fail later on this group's missing worktree dir (409). That gap is
        # what proves the shared ACTION_VALUES gate let them through.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(git_active_group, "commit_rebase")
        assert (exc.value.status, exc.value.code) == (422, "invalid_request")

        for action in ("commit_push", "commit_only"):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.finalize(git_active_group, action)
            assert (exc.value.status, exc.value.code) == (409, "invalid_state")
            assert "worktree directory is missing" in exc.value.message


    def test_approval_ride_along_shares_the_action_gate(self, git_active_group):
        from modules.flow_gate.services import git_service as svc

        doc = {"type_code": "AC", "group_id": git_active_group}
        assert svc.precheck_approve_git_action(doc, "commit_push") == git_active_group
        for action in ("commit_only", "wait"):
            with pytest.raises(svc.GitServiceError) as denied:
                svc.precheck_approve_git_action(doc, action)
            assert (denied.value.status, denied.value.code) == (422, "invalid_request")
        with pytest.raises(svc.GitServiceError) as exc:
            svc.precheck_approve_git_action(doc, "commit_rebase")
        assert exc.value.status == 422


    def test_project_default_action_stays_narrow(self, seed):
        from modules.flow_gate.services import git_service as svc

        # save_config validates against DEFAULT_FINALIZE_ACTION_VALUES, so a
        # commit_* project default is still refused (and refused before any write).
        for action in ("commit_push", "commit_only"):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.save_config("gitprj", {
                    "repo_url": "https://example.com/team/repo.git",
                    "default_finalize_action": action,
                })
            assert exc.value.status == 422


    def test_commit_actions_never_reach_the_merge_session_validator(self, seed):
        from modules.flow_gate.db import git_integration as db_git

        # db_git.ACTION_VALUES gates the finalize_action persisted on a conflict
        # session. Only merge/merge_only can open one — commit_push/commit_only
        # return before create_session — so it is intentionally NOT widened. Pin
        # both halves: the narrow list, and its refusal of a commit_* value. If a
        # later change ever routes commit_push through a session this test names
        # the reason the insert blows up.
        assert db_git.ACTION_VALUES == ("merge", "merge_only", "push", "wait")
        for session_capable in ("merge", "merge_only"):
            assert session_capable in db_git.ACTION_VALUES
        with pytest.raises(ValueError):
            db_git.create_session(self.GROUP, ["f.txt"], finalize_action="commit_push")


