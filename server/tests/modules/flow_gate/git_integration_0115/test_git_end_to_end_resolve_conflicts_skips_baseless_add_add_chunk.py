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
    def test_resolve_conflicts_skips_baseless_add_add_chunk(self, origin_repo):
        # Completion (iii): a chunk with NO "|||||||" section at all (an old session
        # created before zdiff3) must not be checked — base=None means there is no
        # ancestor to diff against, so a one-sided resolution of such a chunk still passes.
        #
        # git_service always forces `-c merge.conflictStyle=zdiff3`, so even an add/add
        # conflict (no real common-ancestor blob for the path) still emits a "|||||||"
        # header line with an EMPTY body — real, but not the case this criterion is about.
        # To reproduce the genuinely marker-less shape a pre-zdiff3 session would have left
        # on disk, this test drives a real conflict session end-to-end through finalize()
        # and then overwrites the on-disk file with hand-written 2-way markers (no base
        # line) before calling the real, unmocked resolve_conflicts().
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0112"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0112")
        # added independently on the group side — no common ancestor for this path
        (wt / "onlynew.py").write_text('"group-only addition"\n', encoding="utf-8")

        (seedwt / "onlynew.py").write_text('"mainline-only addition"\n', encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline adds onlynew.py independently"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        conflicts = svc.list_conflicts(group, merge_id)
        entry = next(f for f in conflicts["files"] if f["path"] == "onlynew.py")
        assert "<<<<<<<" in entry["content"]

        conflict_root = svc.resolve_conflict_src_root(group, merge_id)
        (conflict_root / "onlynew.py").write_text(
            "<<<<<<< HEAD\n"
            '"mainline-only addition"\n'
            "=======\n"
            '"group-only addition"\n'
            ">>>>>>> gitprj_default_0112\n",
            encoding="utf-8",
        )
        no_base = svc.list_conflicts(group, merge_id)
        no_base_entry = next(f for f in no_base["files"] if f["path"] == "onlynew.py")
        assert "|||||||" not in no_base_entry["content"]

        # keeps only ONE side — no base means nothing to compare against, so this
        # must pass exactly like it did before base checks (markers gone → done).
        # Opt into the pre-existing immediate-merge behavior (see the
        # note in test_conflict_resolve_flow above) — this test is about the
        # baseless-chunk skip, not the review gate.
        svc.record_auto_authority(group, merge_id, True)
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "onlynew.py",
            "content": '"group-only addition"\n',
        }], True)
        assert out["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"


