"""Git integration 0115: independently selectable scenario module."""
from __future__ import annotations

from test_git_integration_0115 import (
    TestConflictSideDroppedAdjacentGroups0604,
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

from test_git_integration_0115 import TestConflictSupersede0604 as _Scenario


class TestConflictSupersede0604(_Scenario):
    def test_violations_list_every_rejected_chunk_with_original_line_range(self):
        from modules.flow_gate.services.git.conflict import (
            _conflict_side_dropped,
            _conflict_side_violations,
        )

        original = (
            "head\n"
            + self._marker("a ours\n", "a base\n", "a theirs\n")
            + "".join(f"gap-{i}\n" for i in range(5))
            + self._marker("b ours\n", "b base\n", "b theirs\n")
            + "tail\n"
        )
        submitted = "head\na theirs\n" + "".join(f"gap-{i}\n" for i in range(5)) + "b ours\ntail\n"
        violations = _conflict_side_violations(original, submitted)
        assert [(v["chunk"], v["start_line"], v["end_line"], v["side"], v["dropped_side"])
                for v in violations] == [(0, 2, 8, "theirs", "ours"), (1, 14, 20, "ours", "theirs")]
        lines = original.splitlines()
        assert lines[1].startswith("<<<<<<<") and lines[7].startswith(">>>>>>>")
        assert lines[13].startswith("<<<<<<<") and lines[19].startswith(">>>>>>>")
        assert _conflict_side_dropped(original, submitted) is True
        assert _conflict_side_violations(original, "head\na both\n" + "".join(
            f"gap-{i}\n" for i in range(5)) + "b both\ntail\n") == []


    def test_0395_reduction_theirs_exact_without_declaration_is_a_violation(self):
        from modules.flow_gate.services.git.conflict import _conflict_side_violations

        original = self._original(self.OURS_0395, self.THEIRS_0395)
        violations = _conflict_side_violations(original, self._submitted(self.THEIRS_0395))
        assert [v["side"] for v in violations] == ["theirs"]


    def test_0395_reduction_valid_theirs_declaration_passes_with_evidence(self):
        original = self._original(self.OURS_0395, self.THEIRS_0395)
        problems, records = self._check(
            "server/tests/test_work_plan_0395.py", original, self._submitted(self.THEIRS_0395),
            {"side": "theirs", "reason": "theirs already carries every ours test"},
        )
        assert problems == []
        assert len(records) == 1
        record = records[0]
        assert (record["kept_side"], record["dropped_side"]) == ("theirs", "ours")
        assert len(record["preserved_lines"]) == 5
        assert [row["line"].strip() for row in record["changed_lines"]] == [
            "future_raw = '{\"wp_version\":2,\"binding\":\"advisory\"}\\n'",
            "assert restored.json()[\"body\"][\"wp_version\"] == 1",
            "(11, bad_dir / \"future.json\", '{\"wp_version\":2}\\n', \"wp_version_unsupported\"),",
        ]
        assert [row["replacement"].strip() for row in record["changed_lines"]][1] == (
            "assert restored.json()[\"body\"][\"wp_version\"] == wp.WP_VERSION_SUPPORTED"
        )


    def test_0395_reduction_ours_declaration_is_rejected_because_theirs_lines_vanish(self):
        original = self._original(self.OURS_0395, self.THEIRS_0395)
        problems, records = self._check(
            "t.py", original, self._submitted(self.OURS_0395),
            {"side": "ours", "reason": "ours is enough"},
        )
        assert records == []
        assert [p["condition"] for p in problems] == ["dropped_lines_removed"]
        assert "from modules.flow_gate.services import work_plan_service as wp" in [
            line.strip() for line in problems[0]["removed_lines"]
        ]


    def test_line_removed_from_its_place_rejects_the_declaration(self):
        # theirs no longer has ours' `error` assertion anywhere in the chunk.
        theirs = self.THEIRS_0395.replace(
            "    assert restored.json()[\"error\"] == 'wp_version_unsupported'\n", "",
        )
        original = self._original(self.OURS_0395, theirs)
        problems, _records = self._check(
            "t.py", original, self._submitted(theirs), {"side": "theirs", "reason": "r"},
        )
        assert [p["condition"] for p in problems] == ["dropped_lines_removed"]
        assert problems[0]["removed"] == 1


    def test_same_text_elsewhere_in_the_file_is_not_evidence(self):
        # The dropped ours line survives only OUTSIDE the chunk (in common text) — that
        # is not "kept in place", so the declaration still fails.
        ours = "keep me\nours only line\n"
        theirs = "keep me\ntheirs line one\ntheirs line two\n"
        original = "ours only line\n" + self._marker(ours, "", theirs) + "tail\n"
        submitted = "ours only line\n" + theirs + "tail\n"
        problems, _records = self._check("t.py", original, submitted, {"side": "theirs", "reason": "r"})
        assert [p["condition"] for p in problems] == ["dropped_lines_removed"]


    @pytest.mark.parametrize("ours,theirs", [
        ("x = 1\ny = 2\n", "x = 1\ny = 3\n"),                  # preserved 1 == changed 1
        ("x = 1\ny = 2\nz = 2\n", "x = 1\ny = 3\nz = 3\n"),    # preserved 1 <  changed 2
        ("y = 2\n", "y = 3\n"),                                # preserved 0
    ])
    def test_preserved_must_outnumber_changed(self, ours, theirs):
        original = self._original(ours, theirs, base="")
        problems, records = self._check(
            "t.py", original, self._submitted(theirs, base=""), {"side": "theirs", "reason": "r"},
        )
        assert records == []
        assert [p["condition"] for p in problems] == ["insufficient_preserved"]


    def test_declared_side_must_match_the_side_actually_kept(self):
        original = self._original(self.OURS_0395, self.THEIRS_0395)
        problems, _records = self._check(
            "t.py", original, self._submitted(self.THEIRS_0395), {"side": "ours", "reason": "r"},
        )
        assert [(p["condition"], p["selected_side"]) for p in problems] == [("side_mismatch", "theirs")]


    def test_declaration_on_a_file_without_violation_is_rejected(self):
        # The synthesized group already passes, so a declaration is never needed there.
        original, submissions = TestConflictSideDroppedAdjacentGroups0604._work_plan_0599_reduction()
        problems, _records = self._check(
            "work_plan.py", original, submissions["merged"], {"side": "theirs", "reason": "r"},
        )
        assert [p["condition"] for p in problems] == ["no_violation"]


    @pytest.mark.parametrize("supersede,conditions", [
        ({"side": "both", "reason": "r"}, ["side_invalid"]),
        ({"side": "theirs", "reason": "   "}, ["reason_empty"]),
        ("theirs", ["malformed"]),
    ])
    def test_malformed_declaration_is_rejected(self, supersede, conditions):
        original = self._original(self.OURS_0395, self.THEIRS_0395)
        problems, _records = self._check(
            "t.py", original, self._submitted(self.THEIRS_0395), supersede,
        )
        assert [p["condition"] for p in problems] == conditions


    def test_one_declaration_must_hold_for_every_violating_chunk(self):
        # Two independent chunks, both theirs-exact: the second has no evidence.
        original = (
            "head\n"
            + self._marker(self.OURS_0395, "", self.THEIRS_0395)
            + "".join(f"gap-{i}\n" for i in range(5))
            + self._marker("value = 'ours'\n", "value = 'base'\n", "value = 'theirs'\n")
            + "tail\n"
        )
        submitted = "head\n" + self.THEIRS_0395 + "".join(f"gap-{i}\n" for i in range(5)) + "value = 'theirs'\ntail\n"
        problems, _records = self._check("t.py", original, submitted, {"side": "theirs", "reason": "r"})
        assert [(p["chunk"], p["condition"]) for p in problems] == [(1, "insufficient_preserved")]


    def test_classify_labels_are_unchanged_by_a_declaration(self):
        from modules.flow_gate.services.git.conflict import _classify_conflict_chunks

        original = self._original(self.OURS_0395, self.THEIRS_0395)
        rows = _classify_conflict_chunks("t.py", original, self._submitted(self.THEIRS_0395))
        assert [row["selection"] for row in rows] == ["theirs"]


