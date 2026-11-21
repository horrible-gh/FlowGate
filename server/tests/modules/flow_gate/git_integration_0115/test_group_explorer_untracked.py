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

from test_git_integration_0115 import TestGroupExplorerUntracked as _Scenario


class TestGroupExplorerUntracked(_Scenario):
    def test_tree_and_changes_surface_untracked(self, grpexp_origin):
        from modules.flow_gate.services import git_service as svc

        wt = self._provision(svc)
        # A brand-new file (never git add-ed) plus a tracked edit — the exact asymmetry
        # the original bug report described ("edits show up but new files do not").
        (wt / "pkg").mkdir(exist_ok=True)
        (wt / "pkg" / "new_module.py").write_text("print('new')\n", encoding="utf-8")
        (wt / "README.md").write_text("hello\nchanged\n", encoding="utf-8")
        # Exposure-filtered kinds must NOT leak in.
        (wt / ".hidden_new").write_text("x\n", encoding="utf-8")
        (wt / "cache.db").write_text("x\n", encoding="utf-8")

        tree = svc.read_group_tree("grpexpprj", self.GROUP)["data"]
        paths = {n["path"] for n in tree["nodes"] if n["type"] == "file"}
        assert "pkg/new_module.py" in paths            # new file now appears in the tree
        assert "README.md" in paths                    # committed file still there
        assert tree["worktree_untracked"] == ["pkg/new_module.py"]  # separate channel, filtered
        assert ".hidden_new" not in paths and "cache.db" not in paths

        changes = svc.read_group_changes("grpexpprj", self.GROUP)["data"]["changes"]
        by_path = {c["path"]: c["status"] for c in changes}
        assert by_path.get("pkg/new_module.py") == "?"  # untracked marker
        assert by_path.get("README.md") == "M"          # tracked edit unaffected
        assert ".hidden_new" not in by_path and "cache.db" not in by_path


    def test_blob_falls_back_to_worktree_for_untracked(self, grpexp_origin):
        from modules.flow_gate.services import git_service as svc

        wt = self._provision(svc)
        # write_bytes (not write_text) so the disk content is exactly what the blob
        # reader returns — no platform \n → \r\n translation to confuse the assertion.
        (wt / "fresh.txt").write_bytes(b"fresh content\n")

        # Untracked file: no commit object, so it is read off disk with commit=None.
        blob = svc.read_group_blob("grpexpprj", self.GROUP, "fresh.txt")["data"]
        assert blob["content"] == "fresh content\n"
        assert blob["commit"] is None and blob["untracked"] is True
        assert blob["binary"] is False

        # Passing the tree's own commit as ref must still resolve the untracked file
        # (the client pins ref to the tree commit; untracked has no commit of its own).
        _, _, head = svc.resolve_group_ref("grpexpprj", self.GROUP)
        pinned = svc.read_group_blob("grpexpprj", self.GROUP, "fresh.txt", head)["data"]
        assert pinned["content"] == "fresh content\n" and pinned["untracked"] is True

        # A committed file still reads through the git-object path (commit set, no flag).
        committed = svc.read_group_blob("grpexpprj", self.GROUP, "README.md")["data"]
        assert committed["commit"] and not committed.get("untracked")

        # A truly missing path is still a 404 (fallback never invents a file).
        with pytest.raises(svc.GitServiceError) as exc:
            svc.read_group_blob("grpexpprj", self.GROUP, "nope/missing.txt")
        assert exc.value.status == 404


    def test_changes_carry_per_file_line_counts(self, grpexp_origin):
        """The final-approval sidebar summarizes how big a group's change is,
        so /changes now carries +/- per file. --name-status alone could not answer it.
        Tracked edits get their counts from `git diff --numstat`; an untracked file has no
        diff entry at all, so its added-lines count is read off disk."""
        from modules.flow_gate.services import git_service as svc

        wt = self._provision(svc)
        # README.md starts as a single line; rewrite it as three so the edit is +3/-1.
        (wt / "README.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
        (wt / "brand_new.txt").write_text("a\nb\n", encoding="utf-8")
        # No trailing newline: the last partial line still counts as a line.
        (wt / "no_eol.txt").write_text("only", encoding="utf-8")
        (wt / "logo.bin").write_bytes(b"\x89PNG\x00\x01\x02")

        by_path = {
            c["path"]: c
            for c in svc.read_group_changes("grpexpprj", self.GROUP)["data"]["changes"]
        }

        readme = by_path["README.md"]
        assert readme["status"] == "M"
        assert readme["insertions"] == 3 and readme["deletions"] == 1

        # Untracked: counted from disk, and a never-added file deletes nothing.
        assert by_path["brand_new.txt"]["insertions"] == 2
        assert by_path["brand_new.txt"]["deletions"] == 0
        assert by_path["no_eol.txt"]["insertions"] == 1

        # Binary is unknown (None), NOT 0 — the client must be able to tell the two apart
        # so it never renders a made-up "+0".
        assert by_path["logo.bin"]["insertions"] is None


    def test_file_diff_returns_old_new_for_tracked_and_untracked(self, grpexp_origin):
        """The "Open changes" action must read through the SAME
        old/new-content contract the file explorer uses — the merge-base blob is the old side, the live worktree file is the
        new side, and the client derives its own line diff from the two bodies. Binary
        is flagged on the side that actually is binary, never guessed from the pair.
        """
        from modules.flow_gate.services import git_service as svc

        wt = self._provision(svc)
        # write_bytes so no platform newline translation muddies the content compare.
        (wt / "README.md").write_bytes(b"one\ntwo\nthree\n")
        (wt / "brand_new.txt").write_bytes(b"a\nb\n")
        (wt / "logo.bin").write_bytes(b"\x89PNG\x00\x01\x02")

        tracked = svc.read_group_file_diff("grpexpprj", self.GROUP, "README.md")["data"]
        assert tracked["status"] == "M" and tracked["base_branch"] == "main"
        assert tracked["old"]["exists"] and tracked["old"]["binary"] is False
        assert tracked["new"]["content"] == "one\ntwo\nthree\n"
        assert tracked["new"]["binary"] is False and tracked["new"]["truncated"] is False

        fresh = svc.read_group_file_diff("grpexpprj", self.GROUP, "brand_new.txt")["data"]
        assert fresh["status"] == "A"
        assert fresh["old"]["exists"] is False and fresh["old"]["content"] is None
        assert fresh["new"]["content"] == "a\nb\n"

        binary = svc.read_group_file_diff("grpexpprj", self.GROUP, "logo.bin")["data"]
        assert binary["status"] == "A"
        assert binary["new"]["binary"] is True and binary["new"]["content"] is None


    def test_file_diff_guards_paths(self, grpexp_origin):
        """The diff reader carries the blob reader's guards: no traversal, no hidden path,
        and a 404 only when NEITHER side has the file. An unchanged tracked file is not
        a 404 under the 0326 contract — its old/new content simply reads identical, which
        is the client's cue to show "no changes", not the server's to hide the path."""
        from modules.flow_gate.services import git_service as svc

        wt = self._provision(svc)
        (wt / ".hidden_new").write_bytes(b"x\n")
        # The class shares one persistent worktree, so restore README.md to its committed
        # content (byte-exact, via git itself): this case needs a TRACKED, UNCHANGED file.
        _git(["checkout", "--", "README.md"], cwd=wt)

        for bad in ("", "../outside.txt", "/etc/passwd"):
            with pytest.raises(svc.GitServiceError):
                svc.read_group_file_diff("grpexpprj", self.GROUP, bad)

        # Exposure-filtered and absent paths are 404; an unchanged tracked file is not.
        for missing in (".hidden_new", "nope/missing.txt"):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.read_group_file_diff("grpexpprj", self.GROUP, missing)
            assert exc.value.status == 404

        unchanged = svc.read_group_file_diff("grpexpprj", self.GROUP, "README.md")["data"]
        assert unchanged["status"] == "M"


