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

from test_git_integration_0115 import TestTerminalReopenReprovision0532 as _Scenario


class TestTerminalReopenReprovision0532(_Scenario):
    def test_reopen_reprovisions_from_the_advanced_base_not_bare_c1(self, terminal_reopen_origin):
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        seedwt = terminal_reopen_origin["seedwt"]
        bare = terminal_reopen_origin["bare"]
        project_id = "gittermprj"
        group = f"{project_id}.default.0100"

        # C1: the TR's merged commit, landed and pushed to base — base is now B1 = B0+C1.
        (seedwt / "c1.txt").write_text("c1 content\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "feat: C1 merged content"], cwd=seedwt)
        c1 = _git(["rev-parse", "HEAD"], cwd=seedwt).strip()
        _git(["push", "origin", "main"], cwd=seedwt)

        # Base advances further AFTER the merge — B2, unrelated to C1/the TR's group.
        (seedwt / "b2.txt").write_text("later base work\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "chore: B2 later base commit"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        # No retained group branch anywhere (neither local nor on origin) — the review's
        # exact scenario: a fresh/never-provisioned slot reopening straight from C1.
        assert svc.ensure_worktree(
            project_id, "default", group,
            trigger="timemachine_reopen", start_point=c1,
        ) == "ok"

        wt = src_root("GitTermProj", "gittermprj_default_0100")
        assert wt.is_dir()
        # C1 is preserved (T0007 §4 condition 2) ...
        assert (wt / "c1.txt").read_text(encoding="utf-8") == "c1 content\n"
        # ... AND the later base commit is NOT silently dropped (T0007 §4 condition 1).
        assert (wt / "b2.txt").read_text(encoding="utf-8") == "later base work\n"
        log = _git(["log", "--format=%H"], cwd=wt).splitlines()
        assert c1 in log


    def test_reopen_fails_closed_when_the_base_no_longer_contains_c1(self, terminal_reopen_origin):
        """T0007 §11 fail-closed — if the current base does not contain C1 as an
        ancestor (the base/history relationship cannot be trusted), reopen must refuse
        rather than guess which commit to fork from."""
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        project_id = "gittermprj"

        # Provisions the base checkout locally (clone), via an unrelated bootstrap group.
        assert svc.ensure_worktree(project_id, "default", f"{project_id}.default.0001") == "ok"
        base_root = src_root("GitTermProj", "main")

        # An orphan commit that shares no history with the current base at all — created
        # straight in the local base checkout, so the object exists without needing a
        # remote round trip, but it is an ancestor of nothing on `main`.
        tree = _git(["rev-parse", "HEAD^{tree}"], cwd=base_root).strip()
        orphan = _git(["commit-tree", "-m", "orphan", tree], cwd=base_root).strip()

        group = f"{project_id}.default.0101"
        assert svc.ensure_worktree(
            project_id, "default", group,
            trigger="timemachine_reopen", start_point=orphan,
        ) == "failed"


