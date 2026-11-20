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

from test_git_integration_0115 import TestBaseUntrackedCommit0296 as _Scenario


class TestBaseUntrackedCommit0296(_Scenario):
    def test_untracked_is_listed_but_never_widens_the_guard(self, untracked_origin):
        """R1's first half: the new file becomes *visible* without becoming
        *dirty*. Folding it into base_dirty would make every `__pycache__` entry
        block merge finalize for every group — the regression 0165.0009 fixed."""
        from modules.flow_gate.services import git_service as svc

        base = untracked_origin["base"]
        (base / "brand_new.md").write_text("made in the UI\n", encoding="utf-8")
        (base / "sub").mkdir()
        (base / "sub" / "nested.txt").write_text("nested\n", encoding="utf-8")
        (base / "keys.secret").write_text("shh\n", encoding="utf-8")   # .gitignore'd

        st = svc.project_git_status("untrkprj")["status"]
        # E3 scope untouched: an uncommitted NEW file blocks nothing.
        # rev6: ai_run rides alongside (the project AI-cleanup lease as server truth).
        assert st["base_dirty"] == {
            "dirty": False, "files": [], "merge_in_progress": None, "ai_run": None,
        }
        # Directories are expanded to individual paths — a bare "sub/" entry is
        # not a `git add` target the operator can reason about.
        assert sorted(st["base_untracked"]["files"]) == ["brand_new.md", "sub/nested.txt"]
        assert st["base_untracked"]["count"] == 2
        # .gitignore'd paths are absent: they can never be committed, so offering
        # them would promise a fix that does not exist (NR §C4).
        assert "keys.secret" not in st["base_untracked"]["files"]

        # the save-time editor probe carries the same split
        probe = svc.base_checkout_dirty_status("untrkprj")
        assert probe["dirty"] is False and probe["files"] == []
        assert sorted(probe["untracked"]) == ["brand_new.md", "sub/nested.txt"]


    def test_commit_by_path_puts_the_file_in_new_worktrees(self, untracked_origin):
        """The whole point of B0001: commit → the AI worker can finally read it.

        Asserted end-to-end against a real `git worktree add`, because the fix
        only means anything if the file lands in the tree the worker resolves."""
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        out = svc.base_commit(
            "untrkprj", "feat: add the file the agent could not see",
            ["brand_new.md", "sub/nested.txt"],
        )["result"]
        assert out["committed"] is True
        assert out["files"] == ["brand_new.md", "sub/nested.txt"]
        assert out["subject"] == "feat: add the file the agent could not see"
        assert out["remaining"] == []
        # the ignored file is still on disk, and still not offered
        assert out["remaining_untracked"] == []

        group = "untrkprj.default.0296"
        assert svc.ensure_worktree("untrkprj", "default", group) == "ok"
        wt = src_root("UntrkProj", "untrkprj_default_0296")
        assert (wt / "brand_new.md").read_text(encoding="utf-8") == "made in the UI\n"
        assert (wt / "sub" / "nested.txt").is_file()


    def test_omitting_paths_keeps_the_legacy_tracked_only_scope(self, untracked_origin):
        """R1 must ADD an affordance, not widen the default one. A plain
        base-commit still ignores untracked files — otherwise build artifacts
        would ride into base history on every commit-then-merge click."""
        from modules.flow_gate.services import git_service as svc

        base = untracked_origin["base"]
        (base / "README.md").write_text("edited\n", encoding="utf-8")          # tracked
        (base / "artifact.tmp").write_text("build junk\n", encoding="utf-8")   # untracked

        out = svc.base_commit("untrkprj", None)["result"]
        assert out["committed"] is True
        assert out["files"] == ["README.md"]
        assert out["remaining"] == []
        # untouched, exactly as before this change
        assert out["remaining_untracked"] == ["artifact.tmp"]
        assert (base / "artifact.tmp").is_file()


    def test_gitignored_path_is_refused_with_its_own_code(self, untracked_origin):
        """`git add -f` is NOT the answer — an ignored file is ignored on purpose.
        Say so, and name the files, so the operator stops retrying a commit that
        can never succeed (NR §C4)."""
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_commit("untrkprj", None, ["keys.secret"])
        assert exc.value.status == 422
        assert exc.value.code == "path_ignored"
        assert exc.value.details == {"files": ["keys.secret"]}


    def test_path_validation_and_unknown_paths(self, untracked_origin):
        from modules.flow_gate.services import git_service as svc

        # traversal/absolute paths are rejected BEFORE the lock (no side effects)
        for bad in ["/etc/passwd", "../outside.txt", "a/../../b"]:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.base_commit("untrkprj", None, [bad])
            assert exc.value.status == 422 and exc.value.code == "invalid_request"

        # a clean path with nothing pending is a 422 naming it, not a silent no-op
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_commit("untrkprj", None, ["README.md"])
        assert exc.value.status == 422
        assert exc.value.code == "invalid_request"
        assert exc.value.details == {"files": ["README.md"]}

        # blank/duplicate entries collapse rather than reaching git
        out = svc.base_commit("untrkprj", None, ["artifact.tmp", "artifact.tmp", "  "])["result"]
        assert out["committed"] is True and out["files"] == ["artifact.tmp"]


