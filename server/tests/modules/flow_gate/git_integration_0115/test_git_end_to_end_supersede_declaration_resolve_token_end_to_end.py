"""Git integration 0115: independently selectable scenario module."""
from __future__ import annotations

from test_git_integration_0115 import (
    TestConflictSupersede0604,
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
    def test_supersede_declaration_resolve_token_end_to_end(self, origin_repo, monkeypatch):
        """0604 T0008 §11.1~§11.7: a real zdiff3 session through the resolve-token route."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git_service import GitServiceError
        from modules.flow_gate.storage.paths import src_root

        sup = TestConflictSupersede0604
        group = "gitprj.default.0605"
        texts = {
            "sup.py": (sup.BASE_0395, sup.BASE_0395 + sup.OURS_0395, sup.BASE_0395 + sup.THEIRS_0395),
            "other.py": ("value = 'base'\n", "value = 'ours'\n", "value = 'theirs'\n"),
            "clean.py": ("flag = 'base'\n", "flag = 'ours'\n", "flag = 'theirs'\n"),
        }
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        for path, (base, _ours, _theirs) in texts.items():
            (seedwt / path).write_text(base, encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add supersede bases"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0605")
        for path, (_base, ours, theirs) in texts.items():
            (wt / path).write_text(theirs, encoding="utf-8")
            (seedwt / path).write_text(ours, encoding="utf-8")
        _git(["commit", "-am", "change supersede files on main"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        finalized = svc.finalize(group, "merge")
        assert finalized["result"]["status"] == "conflict"
        merge_id = finalized["result"]["merge_id"]
        assert sorted(f["path"] for f in svc.list_conflicts(group, merge_id)["files"]) == sorted(texts)
        root = svc.resolve_conflict_src_root(group, merge_id)
        before = {path: (root / path).read_bytes() for path in texts}
        post = self._resolve_token_client_0604(monkeypatch, group, merge_id)

        def unchanged():
            return all((root / path).read_bytes() == raw for path, raw in before.items())

        ours = {path: t[1] for path, t in texts.items()}
        theirs = {path: t[2] for path, t in texts.items()}
        merged = {"other.py": "value = 'ours+theirs'\n", "clean.py": "flag = 'ours+theirs'\n"}
        decl = {"side": "theirs", "reason": "theirs already carries every test ours added"}

        # §11.1 + §11.7: two violating files in ONE 422, with the supersede hint; nothing written.
        response = post([
            {"path": "sup.py", "content": theirs["sup.py"]},
            {"path": "other.py", "content": ours["other.py"]},
            {"path": "clean.py", "content": merged["clean.py"]},
        ])
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "conflict_side_dropped"
        assert [row["path"] for row in error["details"]["files"]] == ["sup.py", "other.py"]
        first = error["details"]["files"][0]["chunks"][0]
        assert (first["chunk"], first["side"], first["dropped_side"]) == (0, "theirs", "ours")
        assert first["start_line"] >= 1 and first["end_line"] > first["start_line"]
        assert "supersede" in error["message"] and "supersede" in error["details"]["hint"]
        assert unchanged()

        # §11.5 side mismatch, §11.3 dropped lines removed, §11.6 no violation.
        cases = [
            ([{"path": "sup.py", "content": theirs["sup.py"], "supersede": {**decl, "side": "ours"}}],
             "side_mismatch"),
            ([{"path": "sup.py", "content": ours["sup.py"], "supersede": {**decl, "side": "ours"}}],
             "dropped_lines_removed"),
            ([{"path": "other.py", "content": theirs["other.py"], "supersede": decl}],
             "insufficient_preserved"),
            ([{"path": "clean.py", "content": merged["clean.py"], "supersede": decl}],
             "no_violation"),
        ]
        for files, condition in cases:
            response = post(files)
            assert response.status_code == 422, response.text
            error = response.json()["error"]
            assert error["code"] == "conflict_supersede_invalid"
            assert [row["condition"] for row in error["details"]["files"]] == [condition]
            assert error["details"]["files"][0]["path"] == files[0]["path"]
            assert unchanged()

        # §11.2: a valid theirs declaration passes, is recorded, and blocks auto-approval.
        svc.record_auto_authority(group, merge_id, True)
        accepted = post([
            {"path": "sup.py", "content": theirs["sup.py"], "supersede": decl},
            {"path": "other.py", "content": merged["other.py"]},
            {"path": "clean.py", "content": merged["clean.py"]},
        ])
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["result"]["status"] == "resolved_pending_review"
        assert (root / "sup.py").read_text(encoding="utf-8") == theirs["sup.py"]
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["auto_authority"] is True
        records = context["conflict_supersedes"]
        assert [(r["path"], r["side"], r["reason"]) for r in records] == [("sup.py", "theirs", decl["reason"])]
        chunk = records[0]["chunks"][0]
        assert (len(chunk["preserved_lines"]), len(chunk["changed_lines"])) == (5, 3)
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["conflict_supersedes"] == records
        assert [o["selection"] for o in review["conflict_origins"] if o["path"] == "sup.py"] == ["theirs"]
        with pytest.raises(GitServiceError) as denied:
            svc.approve_merge_review(
                group, merge_id, attempt_id="auto-0605",
                review_fingerprint=review["review_fingerprint"], authority="automatic",
            )
        assert denied.value.code == "human_authority_required"

        aborted = svc.abort_merge(group, merge_id)
        assert aborted["result"]["status"] == "waiting"


