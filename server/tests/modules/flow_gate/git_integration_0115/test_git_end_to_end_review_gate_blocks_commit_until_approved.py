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
    def test_review_gate_blocks_commit_until_approved(self, origin_repo):
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0120"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0120")
        (wt / "shared.py").write_text('"gate group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"gate mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "gate mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        before_head = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        # markers gone → resolved_pending_review, NOT merged: this is the whole point.
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"gate group version"\n"gate mainline version"\n',
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"
        fingerprint = out["result"]["review_fingerprint"]
        assert fingerprint
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == before_head
        assert db_git.get_session(merge_id)["status"] == "open"

        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint
        assert review["can_approve"] is True
        assert "shared.py" in {c["path"] for c in review["changes"]}
        # D0006 §3.3 / L0007 §2.4: which chunk resolved to which side, for the
        # review screen to overlay on the diff — "both" here because the submitted
        # content keeps ours followed by theirs.
        origins = [o for o in review["conflict_origins"] if o["path"] == "shared.py"]
        assert len(origins) == 1
        assert origins[0]["selection"] == "both"
        diff = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]
        # normalize CRLF: Path.write_text on Windows translates "\n" -> os.linesep
        # on write, same as every other content check in this file (E12 pattern).
        assert diff["new"]["content"].replace("\r\n", "\n") == '"gate group version"\n"gate mainline version"\n'

        # a stale/forged fingerprint is refused — nothing committed, session unchanged.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.approve_merge_review(
                group, merge_id, attempt_id=str(uuid.uuid4()),
                review_fingerprint="0" * 64, authority="human",
            )
        assert exc.value.code == "stale_review"
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == before_head

        attempt_id = str(uuid.uuid4())
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=attempt_id,
            review_fingerprint=fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert approved["result"]["review_state"] == "completed"
        assert db_git.get_session(merge_id)["status"] == "done"
        after_head = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()
        assert after_head != before_head

        # idempotent re-approval with the SAME attempt_id never creates a second commit.
        again = svc.approve_merge_review(
            group, merge_id, attempt_id=attempt_id,
            review_fingerprint=fingerprint, authority="human",
        )
        assert again["result"]["status"] == "already_applied"
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == after_head


