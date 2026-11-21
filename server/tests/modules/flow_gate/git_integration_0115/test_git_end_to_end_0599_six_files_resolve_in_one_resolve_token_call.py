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
    def test_0599_six_files_resolve_in_one_resolve_token_call(self, origin_repo, monkeypatch):
        """Completion condition: the real 0599 merge, reproduced by zdiff3 in a
        throwaway repo, resolves all six files in ONE resolve-token call; all-ours and
        all-theirs stay rejected with or without declarations."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base, ours, theirs, resolved = self._merge_0599_inputs()
        paths = self._MERGE_0599["paths"]
        group = "gitprj.default.0606"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        for path in paths:
            (seedwt / path).parent.mkdir(parents=True, exist_ok=True)
            (seedwt / path).write_bytes(base[path])
        # The real repository runs with core.autocrlf=false. git-for-Windows' system
        # config turns it on, which renormalizes WorkPlanEditor.vue's mixed line endings
        # and changes git's chunk layout (7 chunks instead of the real 8). `-text` keeps
        # these six paths byte-exact under any autocrlf, as in the real repository.
        (seedwt / ".gitattributes").write_text(
            "".join(f"{path} -text\n" for path in paths), encoding="utf-8",
        )
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "0599 merge base (c1949e69)"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0606")
        for path in paths:
            (wt / path).write_bytes(theirs[path])
            (seedwt / path).write_bytes(ours[path])
        _git(["commit", "-am", "0599 main side (691fbc7)"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        finalized = svc.finalize(group, "merge")
        assert finalized["result"]["status"] == "conflict"
        merge_id = finalized["result"]["merge_id"]
        conflicts = {f["path"]: f for f in svc.list_conflicts(group, merge_id)["files"]}
        assert sorted(conflicts) == sorted(paths)
        # Same chunk layout as the live session 97, except WorkPlanEditor.vue:
        # its ours side carries 23 stray LF lines in a CRLF file, and since the line-ending fix
        # the finalize merge re-merges such a file on normalised line endings, which
        # drops the one chunk that was only that line-ending difference (8 → 7).
        # work_plan.py (two stray CRLF lines) is re-merged too, keeping its 5 chunks.
        assert [conflicts[p]["conflict_count"] for p in paths] == [1, 1, 1, 7, 5, 1]
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["eol_renormalized_paths"] == [
            "client/src/main/components/WorkPlanEditor.vue",
            "server/modules/flow_gate/documents/routers/work_plan.py",
        ]
        assert context["eol_only_paths"] == []
        root = svc.resolve_conflict_src_root(group, merge_id)
        before = {path: (root / path).read_bytes() for path in paths}
        post = self._resolve_token_client_0604(monkeypatch, group, merge_id)
        decl_0395 = {
            "side": "theirs",
            "reason": "theirs keeps every ours test and only moves wp_version expectations "
                      "to WP_VERSION_SUPPORTED=2 of the merged tree",
        }

        def text(raw: bytes) -> str:
            return raw.decode("utf-8")

        def pick(raw: bytes, side: str) -> str:
            """Resolve every chunk of the real marker file to one side (EOLs kept)."""
            out, state = [], "common"
            for line in raw.decode("utf-8").splitlines(keepends=True):
                bare = line.rstrip("\r\n")
                if state == "common" and bare.startswith("<<<<<<<"):
                    state = "ours"
                elif state == "ours" and bare.startswith("|||||||"):
                    state = "base"
                elif state in ("ours", "base") and bare == "=======":
                    state = "theirs"
                elif state == "theirs" and bare.startswith(">>>>>>>"):
                    state = "common"
                elif state in ("common", side):
                    out.append(line)
            return "".join(out)

        # Completion condition 3: all-ours and all-theirs are rejected with or
        # without declarations, and nothing is written. (a) one side picked in EVERY
        # chunk of the real markers: every file is a violation, listed in one 422.
        for label in ("ours", "theirs"):
            for declare in (False, True):
                files = [{"path": p, "content": pick(before[p], label)} for p in paths]
                if declare:
                    for row in files:
                        row["supersede"] = {"side": label, "reason": f"all {label}"}
                response = post(files)
                assert response.status_code == 422, (label, declare, response.text)
                error = response.json()["error"]
                listed = [row["path"] for row in error["details"]["files"]]
                if declare:
                    assert error["code"] == "conflict_supersede_invalid"
                    assert {
                        "client/src/main/components/WorkPlanEditor.vue",
                        "server/modules/flow_gate/documents/routers/work_plan.py",
                    } <= set(listed)
                else:
                    assert error["code"] == "conflict_side_dropped"
                    assert listed == paths
                assert all((root / p).read_bytes() == before[p] for p in paths)
        # (b) whole side blobs (`git checkout --ours/--theirs`): also refused as a request.
        for label, side_blobs in (("ours", ours), ("theirs", theirs)):
            for declare in (False, True):
                files = [{"path": p, "content": text(side_blobs[p])} for p in paths]
                if declare:
                    for row in files:
                        row["supersede"] = {"side": label, "reason": f"all {label}"}
                response = post(files)
                assert response.status_code == 422, (label, declare, response.text)
                assert response.json()["error"]["code"] in (
                    "conflict_side_dropped", "conflict_supersede_invalid",
                )
                assert all((root / p).read_bytes() == before[p] for p in paths)

        # The 0599 resolution without the declaration: only 0395 is still rejected.
        files = [{"path": p, "content": text(resolved[p])} for p in paths]
        response = post(files)
        assert response.status_code == 422, response.text
        assert [row["path"] for row in response.json()["error"]["details"]["files"]] == [
            "server/tests/test_work_plan_0395.py",
        ]

        # ONE call: six files, 0395 carrying its theirs declaration → resolved_pending_review.
        svc.record_auto_authority(group, merge_id, True)
        files[-1]["supersede"] = decl_0395
        accepted = post(files)
        assert accepted.status_code == 200, accepted.text
        result = accepted.json()["result"]
        assert result["status"] == "resolved_pending_review"
        assert result["remaining_conflicts"] == []
        assert all(row["resolved"] for row in db_git.session_files(merge_id))
        # Byte-exact, in each file's own line ending (the base branch's).
        # Until then this compared CR-insensitively because resolve_conflicts wrote in
        # text mode — which is how the real 0599 merge (6132cf58) turned the three LF
        # i18n files, work_plan.py and test_work_plan_0395.py CRLF, and WorkPlanEditor.vue
        # "\r\r\n", on the Windows server.
        for p in paths:
            want = resolved[p].replace(b"\r\n", b"\n")
            if ours[p].count(b"\r\n") * 2 > ours[p].count(b"\n"):
                want = want.replace(b"\n", b"\r\n")
            got = (root / p).read_bytes()
            assert got == want, p
            assert b"\r\r\n" not in got, p
        for p in paths[:3] + paths[4:]:
            assert b"\r" not in (root / p).read_bytes(), p
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == svc.REVIEW_STATE_PENDING
        (record,) = review["conflict_supersedes"]
        assert (record["path"], record["side"]) == ("server/tests/test_work_plan_0395.py", "theirs")
        (chunk,) = record["chunks"]
        assert (len(chunk["preserved_lines"]), len(chunk["changed_lines"])) == (151, 3)
        assert [
            o["selection"] for o in review["conflict_origins"]
            if o["path"] == "server/modules/flow_gate/documents/routers/work_plan.py"
        ][:4] == ["manual", "manual", "manual", "theirs"]

        aborted = svc.abort_merge(group, merge_id)
        assert aborted["result"]["status"] == "waiting"


