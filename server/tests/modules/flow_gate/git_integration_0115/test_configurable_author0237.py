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

from test_git_integration_0115 import TestConfigurableAuthor0237 as _Scenario


class TestConfigurableAuthor0237(_Scenario):
    def test_default_commits_as_flowgate(self, author_origin):
        """Unconfigured author = the pre-0237 behavior, unchanged."""
        from modules.flow_gate.services import git_service as svc

        base = author_origin["base"]
        (base / "a.txt").write_text("edit 1\n", encoding="utf-8")
        assert svc.base_commit("authprj", "fix: a.txt")["result"]["committed"] is True

        ident = _ident_of(base)
        assert ident["author"] == self.FLOWGATE
        assert ident["committer"] == self.FLOWGATE


    def test_configured_author_on_commit(self, author_origin):
        from modules.flow_gate.services import git_service as svc

        base = author_origin["base"]
        self._set_author("Shin", "shin@example.com")
        (base / "a.txt").write_text("edit 2\n", encoding="utf-8")
        assert svc.base_commit("authprj", "fix: a.txt again")["result"]["committed"] is True

        ident = _ident_of(base)
        assert ident["author"] == "Shin <shin@example.com>"
        # the server really made this commit — the committer stays honest
        assert ident["committer"] == self.FLOWGATE


    def test_configured_author_on_finalize_merge_commit(self, author_origin):
        """The `--author` flag does not exist for `git merge`, so the
        merge commit is the regression-prone one: pin it explicitly."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        self._set_author("Shin", "shin@example.com")
        group = "authprj.default.0300"
        assert svc.ensure_worktree("authprj", "default", group) == "ok"
        wt = src_root("AuthProj", "authprj_default_0300")
        (wt / "feature.txt").write_text("work\n", encoding="utf-8")

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "merged"

        base = author_origin["base"]
        # the merge commit itself …
        merge_ident = _ident_of(base)
        assert merge_ident["author"] == "Shin <shin@example.com>"
        assert merge_ident["committer"] == self.FLOWGATE
        # … and the absorb commit it merged in (the work commit on the branch)
        absorb_ident = _ident_of(base, "HEAD^2")
        assert absorb_ident["author"] == "Shin <shin@example.com>"
        assert absorb_ident["committer"] == self.FLOWGATE


    def test_cleared_author_reverts_to_flowgate(self, author_origin):
        from modules.flow_gate.services import git_service as svc

        base = author_origin["base"]
        self._set_author("", "")
        (base / "a.txt").write_text("edit 3\n", encoding="utf-8")
        assert svc.base_commit("authprj", "fix: back to default")["result"]["committed"] is True
        assert _ident_of(base)["author"] == self.FLOWGATE


    def test_ambient_author_env_is_not_inherited(self, author_origin, monkeypatch):
        """An operator's GIT_AUTHOR_* in the server env must never leak into a commit."""
        from modules.flow_gate.services import git_service as svc

        base = author_origin["base"]
        monkeypatch.setenv("GIT_AUTHOR_NAME", "Ambient")
        monkeypatch.setenv("GIT_AUTHOR_EMAIL", "ambient@leak")
        (base / "a.txt").write_text("edit 4\n", encoding="utf-8")
        assert svc.base_commit("authprj", "fix: no ambient leak")["result"]["committed"] is True
        assert _ident_of(base)["author"] == self.FLOWGATE


