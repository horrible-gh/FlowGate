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

from test_git_integration_0115 import TestConflictSideDroppedAdjacentGroups0604 as _Scenario


class TestConflictSideDroppedAdjacentGroups0604(_Scenario):
    @pytest.mark.parametrize("gap", [1, 2, 3])
    def test_manual_merge_unlocks_exact_side_only_through_three_common_lines(self, gap):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = self._two_chunk_original(gap)
        submitted = self._two_chunk_submission("first combined\n", gap, "second theirs\n")
        assert _conflict_side_dropped(original, submitted) is False


    @pytest.mark.parametrize("side", ["ours", "theirs"])
    def test_all_one_side_is_still_rejected_inside_a_nearby_group(self, side):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        submitted = self._two_chunk_submission(
            f"first {side}\n", 2, f"second {side}\n",
        )
        assert _conflict_side_dropped(self._two_chunk_original(2), submitted) is True


    def test_alternating_ours_and_theirs_without_synthesis_is_rejected(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        submitted = self._two_chunk_submission("first ours\n", 2, "second theirs\n")
        assert _conflict_side_dropped(self._two_chunk_original(2), submitted) is True


    def test_four_common_lines_break_the_group(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        submitted = self._two_chunk_submission("first combined\n", 4, "second theirs\n")
        assert _conflict_side_dropped(self._two_chunk_original(4), submitted) is True


    def test_baseless_chunk_breaks_group_and_does_not_hide_adjacent_drop(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = (
            "head\n"
            + self._marker("legacy ours\n", None, "legacy theirs\n")
            + self._gap(1)
            + self._marker("normal ours\n", "normal base\n", "normal theirs\n")
            + "tail\n"
        )
        submitted = "head\nlegacy combined\n" + self._gap(1) + "normal ours\ntail\n"
        assert _conflict_side_dropped(original, submitted) is True


    def test_baseless_chunk_keeps_later_manual_chunk_anchored(self):
        from modules.flow_gate.services.git.conflict import (
            _classify_conflict_chunks,
            _conflict_side_dropped,
        )

        original = (
            "head\n"
            + self._marker("legacy ours\n", None, "legacy theirs\n")
            + self._gap(1)
            + self._marker("normal ours\n", "normal base\n", "normal theirs\n")
            + "tail\n"
        )
        submitted = "head\nlegacy combined\n" + self._gap(1) + "normal combined\ntail\n"
        assert _conflict_side_dropped(original, submitted) is False
        assert [row["selection"] for row in _classify_conflict_chunks(
            "baseless.py", original, submitted,
        )] == ["manual", "manual"]


    def test_one_side_changed_manual_chunk_cannot_unlock_neighbor(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = (
            "head\n"
            + self._marker("one changed\n", "one base\n", "one base\n")
            + self._gap(1)
            + self._marker("both ours\n", "both base\n", "both theirs\n")
            + "tail\n"
        )
        submitted = "head\none manual\n" + self._gap(1) + "both ours\ntail\n"
        assert _conflict_side_dropped(original, submitted) is True


    def test_three_chunk_chain_is_one_group(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = (
            "head\n"
            + self._marker("zero ours\n", "zero base\n", "zero theirs\n")
            + self._gap(2, "gap-a")
            + self._marker("one ours\n", "one base\n", "one theirs\n")
            + self._gap(3, "gap-b")
            + self._marker("two ours\n", "two base\n", "two theirs\n")
            + "tail\n"
        )
        submitted = (
            "head\nzero combined\n" + self._gap(2, "gap-a")
            + "one ours\n" + self._gap(3, "gap-b") + "two theirs\ntail\n"
        )
        assert _conflict_side_dropped(original, submitted) is False


    def test_real_work_plan_0599_reduction_passes_without_changing_labels(self):
        from modules.flow_gate.services.git.conflict import (
            _classify_conflict_chunks,
            _conflict_side_dropped,
        )

        original, submissions = self._work_plan_0599_reduction()
        assert _conflict_side_dropped(original, submissions["merged"]) is False
        assert [row["selection"] for row in _classify_conflict_chunks(
            "server/modules/flow_gate/documents/routers/work_plan.py",
            original,
            submissions["merged"],
        )] == ["manual", "manual", "manual", "theirs"]


    @pytest.mark.parametrize("side", ["ours", "theirs"])
    def test_real_work_plan_0599_reduction_still_rejects_all_one_side(self, side):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original, submissions = self._work_plan_0599_reduction()
        assert _conflict_side_dropped(original, submissions[side]) is True


    def test_existing_workplaneditor_and_i18n_syntheses_stay_accepted(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        i18n = self._marker("restore old\n", "no restore\n", "restore new\n")
        assert _conflict_side_dropped(i18n, "restore combined wording\n") is False

        editor = (
            "head\n"
            + self._marker("disabled = ours\n", "disabled = base\n", "disabled = theirs\n")
            + self._gap(2)
            + self._marker("comment added\n", "comment base\n", "comment base\n")
            + "tail\n"
        )
        submitted = "head\ndisabled = combined\n" + self._gap(2) + "comment added\ntail\n"
        assert _conflict_side_dropped(editor, submitted) is False


