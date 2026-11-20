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

from test_git_integration_0115 import TestGitEndToEnd as _Scenario


class TestGitEndToEnd(_Scenario):
    def test_conflict_resolve_flow(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0102"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0102")

        # group edits shared.py …
        (wt / "shared.py").write_text('"group version"\n', encoding="utf-8")
        # … while origin main moves the same line via the seed worktree
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)  # catch up (earlier merges landed)
        (seedwt / "shared.py").write_text('"mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        assert out["result"]["conflict_files"] == ["shared.py"]
        # 0205: conflict sessions no longer hold the project mutex; the base is
        # protected by the open-session guard instead.
        assert held_locks("gitprj") == []
        assert svc.open_merge_session_of_project("gitprj")["merge_id"] == merge_id
        group2 = "gitprj.default.0103"
        assert svc.ensure_worktree("gitprj", "default", group2) == "ok"
        _seed_wf_done_root(group2, project_id=group2.split(".", 1)[0])
        db_git.set_status(group2, "awaiting_choice")
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group2, "merge")
        assert exc.value.code == "merge_conflict_open"

        conflicts = svc.list_conflicts(group, merge_id)
        assert conflicts["files"][0]["path"] == "shared.py"
        content = conflicts["files"][0]["content"]
        assert "<<<<<<<" in content
        assert "|||||||" in content, "zdiff3 base marker must be present"
        assert conflicts["files"][0]["conflict_count"] == 1

        # Verify base content matches the common ancestor
        # Extract base section from zdiff3 conflict markers
        import re
        marker_pattern = r'^<{7}[^\n]*\n(.*?)^\|{7}[^\n]*\n(.*?)^={7}\n(.*?)^>{7}'
        match = re.search(marker_pattern, content, re.MULTILINE | re.DOTALL)
        assert match, "Should parse zdiff3 conflict with base marker"
        ours_content, base_content, theirs_content = match.groups()
        assert base_content.strip() == '"line1"', "Base marker should separate common ancestor content"
        assert ours_content.strip() == '"mainline version"', "Merge target (main) should be ours"
        assert theirs_content.strip() == '"group version"', "Merge source (group branch) should be theirs"

        # markers left in the submitted content → 422, nothing written
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "shared.py",
                "content": conflicts["files"][0]["content"],
            }], True)
        assert exc.value.code == "conflict_markers_remain"

        # a file outside the session → 422 (E12)
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "README.md", "content": "x\n",
            }], True)
        assert exc.value.status == 422

        # 0481 T0008: a resolved general merge now stops at resolved_pending_review
        # unless auto_authority was recorded (D0006 §3.2/§3.4). This test is about
        # the merge/push mechanics, not the review gate itself (covered separately
        # in test_git_merge_review_gate_0481.py), so it opts into the pre-existing
        # immediate-merge behavior through the officially supported bypass.
        svc.record_auto_authority(group, merge_id, True)
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"group version"\n"mainline version"\n',
        }], True)
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is True
        # flowgate.default.0232 B0001: the conflict-resolution merge commit must
        # ALSO carry a conventional Merge subject, not the reused work subject.
        # This path likewise precedes the merge with an absorb commit, so reusing
        # the finalize subject stamped two commits of identical title+diff onto
        # origin ("same code committed twice"). Guard the fix here too — without
        # this assertion the conflict path could silently regress to the old
        # resolve_commit_message()[0] subject while test_finalize_wait_then_merge
        # (clean path only) still passes.
        top_subject = _git(["log", "-1", "--format=%s", "main"], cwd=origin_repo["bare"]).strip()
        assert top_subject.startswith("Merge branch ")
        absorb_subject = _git(["log", "-1", "--format=%s", "main^2"], cwd=origin_repo["bare"]).strip()
        assert top_subject != absorb_subject  # a normal work+merge pair, not a duplicate
        assert held_locks("gitprj") == []
        session = db_git.get_session(merge_id)
        assert session["status"] == "done"


