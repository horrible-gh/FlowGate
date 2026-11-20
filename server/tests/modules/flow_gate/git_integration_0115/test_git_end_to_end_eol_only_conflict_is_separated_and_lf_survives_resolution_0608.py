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
    def test_eol_only_conflict_is_separated_and_lf_survives_resolution_0608(self, origin_repo, monkeypatch):
        """0608 T0005, connected: a real finalize whose main side flipped one file to CRLF
        (the 6132cf58 shape) opens a session in which that file is already merged and
        marked resolved; the mention lists only the real conflict, by location; and the
        LF file resolved through the resolve-token route stays LF."""
        monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
        monkeypatch.setenv("CONTEXT", "/flowgate")
        monkeypatch.setenv("DB_TYPE", "sqlite")
        import json

        from modules.flow_gate.api import token_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0608"
        lines = [f"msg_{i}: 'text {i}',\n" for i in range(40)]
        base = "".join(lines).encode()
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "i18n_en.ts").write_bytes(base)
        (seedwt / "real.py").write_bytes(b"x = 1\n")
        # byte-exact under this host's autocrlf=true, as in the real repository
        (seedwt / ".gitattributes").write_text("i18n_en.ts -text\nreal.py -text\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "0608 base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0608")
        theirs = lines[:]
        theirs[5] = "msg_5: 'text 5 (group)',\n"
        (wt / "i18n_en.ts").write_bytes("".join(theirs).encode())
        (wt / "real.py").write_bytes(b"x = 2\n")
        (seedwt / "i18n_en.ts").write_bytes(base.replace(b"\n", b"\r\n"))   # EOL flip only
        (seedwt / "real.py").write_bytes(b"x = 3\n")
        _git(["commit", "-am", "main flips i18n to CRLF"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        finalized = svc.finalize(group, "merge")
        assert finalized["result"]["status"] == "conflict"
        assert finalized["result"]["conflict_files"] == ["i18n_en.ts", "real.py"]
        merge_id = finalized["result"]["merge_id"]
        root = svc.resolve_conflict_src_root(group, merge_id)

        # the EOL-only file: merged on normalised text, in ours' (CRLF) line ending, staged
        assert db_git.remaining_conflicts(merge_id) == ["real.py"]
        listed = svc.list_conflicts(group, merge_id)
        assert listed["eol_only_paths"] == ["i18n_en.ts"]
        assert {f["path"]: f["conflict_count"] for f in listed["files"]} == {"i18n_en.ts": 0, "real.py": 1}
        assert (root / "i18n_en.ts").read_bytes() == "".join(theirs).encode().replace(b"\n", b"\r\n")
        unmerged = _git(["diff", "--name-only", "--diff-filter=U"], cwd=root).split()
        assert unmerged == ["real.py"]

        mention = token_routes._build_conflict_mention(
            group_id=group, project_id="gitprj", merge_id=merge_id,
            scratch_dir="/tmp/scratch", raw_token="tok", api_base_url="http://x/api/v1",
        )
        session = json.loads(mention.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        assert [f["path"] for f in session["files"]] == ["real.py"]
        assert session["resolved_files"] == [{"path": "i18n_en.ts", "reason": "eol_only"}]
        assert "POST http://x/api/v1/remote/read" in mention
        assert "msg_5" not in mention                       # the EOL-only file is not re-sent

        post = self._resolve_token_client_0604(monkeypatch, group, merge_id)
        accepted = post([{"path": "real.py", "content": "x = 23\n"}])
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["result"]["status"] == "resolved_pending_review"
        assert (root / "real.py").read_bytes() == b"x = 23\n"   # LF kept (was CRLF on Windows)

        aborted = svc.abort_merge(group, merge_id)
        assert aborted["result"]["status"] == "waiting"


