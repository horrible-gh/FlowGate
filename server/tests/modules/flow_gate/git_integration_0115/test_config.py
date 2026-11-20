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

from test_git_integration_0115 import TestConfig as _Scenario


class TestConfig(_Scenario):
    def test_unconfigured_view(self, seed):
        from modules.flow_gate.services import git_service as svc

        view = svc.get_config_view("plainprj")
        assert view == {"ok": True, "configured": False, "config": None}


    def test_save_and_mask(self, seed):
        from modules.flow_gate.services import git_service as svc

        out = svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "provider": "github",
            "username": "bot",
            "secret": "ghp_AbCdEfGh1234567890IjKlMnOpQrStWXYZ",
            "base_branch": "main",
            "default_finalize_action": "merge",
            "enabled": True,
        })
        cfg = out["config"]
        assert out["configured"] is True
        assert cfg["has_secret"] is True
        assert cfg["secret_masked"].startswith("ghp_")
        assert "ghp_AbCdEfGh" not in str(cfg)  # plaintext never leaves the service


    def test_secret_keep_and_clear(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        before = db_git.get_config("gitprj")["secret_enc"]
        out = svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "secret": None,  # keep
            "enabled": True,
        })
        assert db_git.get_config("gitprj")["secret_enc"] == before
        assert out["config"]["has_secret"] is True

        svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "secret": "",  # clear
            "enabled": True,
        })
        assert db_git.get_config("gitprj")["secret_enc"] is None


    def test_author_unset_by_default(self, seed):
        from modules.flow_gate.services import git_service as svc

        out = svc.save_config("gitprj", {"repo_url": "https://example.com/team/repo.git"})
        assert out["config"]["author_name"] is None
        assert out["config"]["author_email"] is None
        # no override → the built-in FlowGate identity, i.e. no GIT_AUTHOR_* env
        assert svc._author_env_for("gitprj") is None


    def test_author_save_keep_and_clear(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        out = svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "author_name": "  Shin  ",          # trimmed
            "author_email": " shin@example.com ",
        })
        assert out["config"]["author_name"] == "Shin"
        assert out["config"]["author_email"] == "shin@example.com"
        assert svc._author_env_for("gitprj") == {
            "GIT_AUTHOR_NAME": "Shin", "GIT_AUTHOR_EMAIL": "shin@example.com",
        }

        # omitted → keep (same protocol as secret/translate_url)
        svc.save_config("gitprj", {"repo_url": "https://example.com/team/repo.git"})
        assert db_git.get_config("gitprj")["author_name"] == "Shin"

        # "" on both → clear, back to the FlowGate default
        out = svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "author_name": "", "author_email": "",
        })
        assert out["config"]["author_name"] is None
        assert db_git.get_config("gitprj")["author_email"] is None
        assert svc._author_env_for("gitprj") is None


    def test_author_must_be_set_as_a_pair(self, seed):
        from modules.flow_gate.services import git_service as svc

        for half in ({"author_name": "Shin"}, {"author_email": "shin@example.com"}):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.save_config("gitprj", {
                    "repo_url": "https://example.com/team/repo.git", **half,
                })
            assert exc.value.status == 422


    def test_author_value_validation(self, seed):
        from modules.flow_gate.services import git_service as svc

        bad = [
            {"author_name": "Bad <hack>", "author_email": "a@b.com"},   # git would mangle
            {"author_name": "Bad\nName", "author_email": "a@b.com"},
            {"author_name": "Shin", "author_email": "not-an-email"},    # no @
            {"author_name": "Shin", "author_email": "a b@c.com"},       # space
            {"author_name": "x" * 101, "author_email": "a@b.com"},
        ]
        for case in bad:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.save_config("gitprj", {
                    "repo_url": "https://example.com/team/repo.git", **case,
                })
            assert exc.value.status == 422
        # a bad value never reaches storage
        assert svc._author_env_for("gitprj") is None


    def test_author_partial_db_row_falls_back(self, seed):
        """A hand-edited/legacy half-set row must still commit, not crash."""
        from modules.flow_gate.services import git_service as svc

        assert svc._author_env_from_cfg({"author_name": "Shin"}) is None
        assert svc._author_env_from_cfg({"author_email": "shin@example.com"}) is None
        assert svc._author_env_from_cfg({}) is None
        assert svc._author_env_from_cfg(None) is None


    def test_url_validation(self, seed):
        from modules.flow_gate.services import git_service as svc

        for bad in ("not-a-url", "ftp://x/y", "https://user:pw@host/repo.git"):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.save_config("gitprj", {"repo_url": bad})
            assert exc.value.status == 422
        # ssh scp-like form is allowed
        out = svc.save_config("gitprj", {"repo_url": "git@github.com:org/repo.git"})
        assert out["configured"] is True


    def test_enum_validation(self, seed):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError):
            svc.save_config("gitprj", {"repo_url": "https://x/y.git", "provider": "svn"})
        with pytest.raises(svc.GitServiceError):
            svc.save_config("gitprj", {
                "repo_url": "https://x/y.git", "default_finalize_action": "rebase",
            })


    def test_delete(self, seed):
        from modules.flow_gate.services import git_service as svc

        assert svc.delete_config("gitprj")["deleted"] is True
        assert svc.delete_config("gitprj")["deleted"] is False
        assert svc.get_config_view("gitprj")["configured"] is False


