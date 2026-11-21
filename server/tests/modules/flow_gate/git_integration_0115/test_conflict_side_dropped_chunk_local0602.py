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

from test_git_integration_0115 import TestConflictSideDroppedChunkLocal0602 as _Scenario


class TestConflictSideDroppedChunkLocal0602(_Scenario):
    def test_exact_ours_only_is_rejected(self):
        # C1 — both sides changed the base line; submission keeps only ours.
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = self._marker_text("mainline change\n", "base line\n", "group change\n")
        assert _conflict_side_dropped(original, "mainline change\n") is True


    def test_exact_theirs_only_is_rejected(self):
        # C2 — the opposite direction: submission keeps only theirs.
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = self._marker_text("mainline change\n", "base line\n", "group change\n")
        assert _conflict_side_dropped(original, "group change\n") is True


    def test_both_sides_kept_passes(self):
        # C3 — both original lines survive verbatim, either order.
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = self._marker_text("mainline change\n", "base line\n", "group change\n")
        assert _conflict_side_dropped(original, "mainline change\ngroup change\n") is False
        assert _conflict_side_dropped(original, "group change\nmainline change\n") is False


    def test_synthesized_single_line_merge_passes(self):
        # C4 — a brand-new line that folds both sides' intent together is neither
        # side's exact text; it must not be treated as a dropped side.
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = self._marker_text(
            "enabled = ours_rule\n", "enabled = old\n", "enabled = theirs_rule\n",
        )
        assert _conflict_side_dropped(original, "enabled = combined_rule\n") is False


    def test_only_one_side_changed_is_exempt(self):
        # theirs matches base exactly (only ours actually changed over the ancestor) —
        # keeping ours and dropping the untouched, identical-to-base theirs text is a
        # normal, correct resolution, not a side drop.
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = self._marker_text("mainline change\n", "base line\n", "base line\n")
        assert _conflict_side_dropped(original, "mainline change\n") is False


    def test_same_line_elsewhere_does_not_hide_a_real_drop(self):
        # C6 — the chunk itself resolves to ours-only (a real drop of theirs), but
        # theirs' original text also happens to sit, unrelated, elsewhere in the file.
        # A whole-file membership check would wrongly call this "both present"; the
        # chunk-local, cursor-advancing check must still catch the drop.
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        original = (
            "group change\nnoise1\n"
            + self._marker_text("mainline change\n", "placeholder\n", "group change\n")
            + "noise2\n"
        )
        submitted = "group change\nnoise1\nmainline change\nnoise2\n"
        assert _conflict_side_dropped(original, submitted) is True


    def test_rear_coincidental_both_does_not_hide_an_early_one_side_drop(self):
        # Review finding: a later, unrelated ours+theirs pair must not
        # hide the exact ours-only resolution.  The following common context anchors the
        # one-line resolution window; the coincidental pair belongs to common text.
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        trailing_common = "noise1\nmainline change\ngroup change\n"
        original = (
            self._marker_text("mainline change\n", "base line\n", "group change\n")
            + trailing_common
        )
        submitted = "mainline change\n" + trailing_common
        assert _conflict_side_dropped(original, submitted) is True


    def test_no_conflict_markers_passes(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_dropped

        assert _conflict_side_dropped("no markers here\n", "no markers here\n") is False


    def test_classify_labels_manual_for_synthesized_merge(self):
        # C8 groundwork — the review screen's per-chunk classifier must agree with
        # _conflict_side_dropped's verdict: a synthesized merge is "manual", not a
        # side selection, so it can reach resolved_pending_review for human review.
        from modules.flow_gate.services.git.conflict import _classify_conflict_chunks

        original = self._marker_text(
            "enabled = ours_rule\n", "enabled = old\n", "enabled = theirs_rule\n",
        )
        origins = _classify_conflict_chunks("flags.txt", original, "enabled = combined_rule\n")
        assert len(origins) == 1
        assert origins[0]["selection"] == "manual"


    def test_leading_common_context_same_as_ours_does_not_hide_manual_merge(self):
        # Review finding: _select_conflict_chunk() used to search the
        # WHOLE submitted text for the earliest ours/theirs/both match, including
        # everything BEFORE the conflict chunk. If the ordinary, unedited common area
        # preceding the chunk happens to contain the same text as `ours`, that
        # coincidental, unrelated occurrence used to be picked up as this chunk's
        # "resolution" — even though the chunk itself was actually resolved manually
        # (synthesized) further down. The chunk's real resolved region must be anchored
        # by the common context around it, not searched unbounded from the start of the
        # file, so this must classify as "manual" and must NOT be rejected as a side
        # drop.
        from modules.flow_gate.services.git.conflict import (
            _classify_conflict_chunks,
            _conflict_side_dropped,
        )

        original = (
            "same line\n"
            + self._marker_text("same line\n", "base line\n", "theirs line\n")
            + "tail\n"
        )
        submitted = "same line\ncombined line\ntail\n"
        assert _conflict_side_dropped(original, submitted) is False

        origins = _classify_conflict_chunks("flags.txt", original, submitted)
        assert len(origins) == 1
        assert origins[0]["selection"] == "manual"


    def test_leading_common_context_same_as_ours_does_not_hide_both_resolution(self):
        # Same anchoring bug, the "both kept" direction: the chunk's real resolution
        # keeps both original lines verbatim (a normal, correct "both" merge), but the
        # earlier, unrelated common-area occurrence of `ours`' own text must not be
        # picked up as this chunk's one-sided "ours" match instead of its real,
        # chunk-local "both". Under the old unanchored search this misclassified as
        # "ours" and _conflict_side_dropped then wrongly rejected a resolution that
        # dropped nothing.
        from modules.flow_gate.services.git.conflict import (
            _classify_conflict_chunks,
            _conflict_side_dropped,
        )

        original = (
            "same line\n"
            + self._marker_text("same line\n", "base line\n", "theirs line\n")
            + "tail\n"
        )
        submitted = "same line\nsame line\ntheirs line\ntail\n"

        origins = _classify_conflict_chunks("flags.txt", original, submitted)
        assert len(origins) == 1
        assert origins[0]["selection"] == "both"
        assert _conflict_side_dropped(original, submitted) is False


    def test_side_subsequence_with_extra_text_is_manual(self):
        # rev4 review finding: preserving ours and adding synthesized text is not an
        # exact one-side selection.  The entire anchored window must be compared.
        from modules.flow_gate.services.git.conflict import (
            _classify_conflict_chunks,
            _conflict_side_dropped,
        )

        original = (
            self._marker_text("main\n", "base\n", "group\n")
            + "tail\n"
        )
        submitted = "main\ncombined extra\ntail\n"

        origins = _classify_conflict_chunks("flags.txt", original, submitted)
        assert len(origins) == 1
        assert origins[0]["selection"] == "manual"
        assert _conflict_side_dropped(original, submitted) is False


    def test_resolution_equal_to_next_common_uses_non_greedy_anchor(self):
        # rev3 review finding: the first `tail` is the exact ours-only resolution;
        # the second is the unchanged following common segment.  A greedy first-match
        # next-common anchor makes the chunk window empty and launders the drop as manual.
        from modules.flow_gate.services.git.conflict import (
            _classify_conflict_chunks,
            _conflict_side_dropped,
        )

        original = self._marker_text("tail\n", "base\n", "other\n") + "tail\n"
        submitted = "tail\ntail\n"

        origins = _classify_conflict_chunks("flags.txt", original, submitted)
        assert len(origins) == 1
        assert origins[0]["selection"] == "ours"
        assert origins[0]["start_line"] == 1
        assert origins[0]["end_line"] == 1
        assert _conflict_side_dropped(original, submitted) is True


