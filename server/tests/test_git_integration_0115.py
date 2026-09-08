"""Git integration (flowgate.default.0115) — db + service test suite.

Covers:
  - branch name generation / sanitization                      → L0006 §2.1
  - secret masking + AES-GCM encrypt/decrypt roundtrip          → L0006 §2.3
  - config CRUD: masking, keep/clear secret protocol, URL/enum
    validation, delete                                          → P0005 §1·§2
  - effective source-root resolution: fallback-first            → L0006 §2.2·§4.1
  - project git lock: acquire / busy / release / transfer       → L0006 §2.8
  - finalize guards (non-integrated group → 409)                → L0006 §4.2
  - finalize action contract: additive commit_push/commit_only,
    untouched UI choice lists, dirty `push` refusal            → 0331 NR0005 §2·§3
  - REAL git end-to-end (skipped when git is absent): clone +
    worktree provisioning (idempotent), finalize merge/push/wait,
    conflict session → resolve/abort                            → L0006 §2.4~§2.7

Environment: TESTING=1, temporary SQLite with the real sqlite migrations
(mirrors test_project_test_commands.py), temporary FLOWGATE_STORAGE_DIR, and a
fixed FLOWGATE_GIT_ENCRYPT_KEY.
"""
from __future__ import annotations

import base64
import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = base64.b64encode(b"K" * 32).decode()

_TMP_STORAGE = tempfile.mkdtemp(prefix="fg-git-test-storage-")
os.environ["FLOWGATE_STORAGE_DIR"] = _TMP_STORAGE

import sys

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

_GIT = shutil.which("git") is not None


def _git_can_clone_local_file_url() -> bool:
    """Probe whether this platform's git can clone a local ``file://`` origin.

    The real-git E2E suite provisions bare origins and hands their ``file://``
    URL to the service's ``git clone`` (a native subprocess). On POSIX this is
    the normal same-host mirror path. git-for-Windows, however, cannot clone a
    ``file:///DRIVE:/...`` URL through a native subprocess — it strips the
    scheme to ``/C:/...`` and reports "does not appear to be a git repository".
    That is an environment capability gap, not a defect in the code under test,
    so the E2E classes must *skip* (never *fail*) where the capability is
    absent. On the Linux deployment the probe passes and the suite runs in full.
    """
    if not _GIT:
        return False
    probe = Path(tempfile.mkdtemp(prefix="fg-git-probe-"))
    ident = ["-c", "user.name=Probe", "-c", "user.email=probe@probe"]

    def _run(args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True,
        )
    try:
        bare = probe / "o.git"
        work = probe / "w"
        if _run(["init", "--bare", "-b", "main", str(bare)]).returncode:
            return False
        if _run(["init", "-b", "main", str(work)]).returncode:
            return False
        (work / "f.txt").write_text("probe\n", encoding="utf-8")
        if _run([*ident, "add", "-A"], cwd=work).returncode:
            return False
        if _run([*ident, "commit", "-m", "probe"], cwd=work).returncode:
            return False
        _run(["remote", "add", "origin", str(bare)], cwd=work)
        if _run([*ident, "push", "origin", "main"], cwd=work).returncode:
            return False
        return _run(["clone", bare.as_uri(), str(probe / "c")]).returncode == 0
    except Exception:
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)


_FILE_CLONE = _git_can_clone_local_file_url()
needs_git = pytest.mark.skipif(
    not (_GIT and _FILE_CLONE),
    reason="git binary or local file:// clone capability unavailable "
           "(e.g. git-for-Windows cannot clone file:///DRIVE:/... via subprocess)",
)


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def seed(tmp_db):
    from modules.flow_gate.db import projects

    projects.create({"project_id": "gitprj", "project_name": "GitProj"})
    projects.create({"project_id": "plainprj", "project_name": "PlainProj"})
    yield


def _migration_applied(tmp_db) -> bool:
    mock_db, _ = tmp_db
    row = mock_db.fetch_one(
        "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name='project_git_config'"
    )
    return row is not None


# ── migration 056 sanity ──────────────────────────────────────────────────────

class TestMigration:
    def test_tables_exist(self, tmp_db):
        assert _migration_applied(tmp_db), "056_git_integration.sql did not apply"

    def test_grant_group_id_column(self, tmp_db):
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(remote_tool_grant)")]
        assert "group_id" in cols

    def test_merge_session_finalize_action_column(self, tmp_db):
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(git_merge_session)")]
        assert "finalize_action" in cols

    def test_project_git_config_author_columns(self, tmp_db):
        # 065_git_author.sql (0237) — configurable commit author
        mock_db, _ = tmp_db
        cols = [r["name"] for r in mock_db.fetch_all("PRAGMA table_info(project_git_config)")]
        assert "author_name" in cols and "author_email" in cols


# ── branch naming (L0006 §2.1) ───────────────────────────────────────────────

class TestBranchName:
    def test_standard(self):
        from modules.flow_gate.services import git_service as svc

        assert (
            svc.worktree_branch_name("flowgate", "default", "flowgate.default.0115")
            == "flowgate_default_0115"
        )

    def test_sanitize_rules(self):
        from modules.flow_gate.services import git_service as svc

        assert svc.sanitize_branch("My Proj//módule.x") == "my-proj-m-dule.x"
        assert svc.sanitize_branch("-..--UPPER--..-") == "upper"
        assert len(svc.sanitize_branch("a" * 300)) == svc.BRANCH_MAX_LEN

    def test_invalid_raises(self):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError):
            svc.sanitize_branch("///")
        with pytest.raises(svc.GitServiceError):
            svc.sanitize_branch("a..b")  # git refname escape sequences stay banned



class TestAutoCommitMessage:
    def _create_group(self, group_id: str, title: str, doc_types: list[str]) -> None:
        from modules.flow_gate.db import documents
        from modules.flow_gate.db import groups

        groups.create({
            "group_id": group_id,
            "project_id": "gitprj",
            "module": "default",
            "title": title,
        })
        for seq, doc_type in enumerate(doc_types, start=1):
            documents.create({
                "doc_id": f"{group_id}.{seq:04d}-{doc_type}",
                "project_id": "gitprj",
                "module": "default",
                "group_id": group_id,
                "type_code": doc_type,
                "seq": seq,
                "title": title if seq == 1 else doc_type,
            })

    def test_requirement_with_design_doc_is_feat(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0168"
        self._create_group(group_id, "깃 커밋 메세지", ["R", "D"])

        assert svc.build_auto_commit_message(group_id) == (
            "feat: 깃 커밋 메세지"
        )

    def test_bug_root_is_fix_even_with_design_doc(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0169"
        self._create_group(group_id, "로그인 오류 수정", ["B", "D"])

        assert svc.build_auto_commit_message(group_id) == (
            "fix: 로그인 오류 수정"
        )

    def test_requirement_without_design_doc_is_chore(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0170"
        self._create_group(group_id, "문서 정리", ["R", "TR"])

        assert svc.build_auto_commit_message(group_id) == (
            "chore: 문서 정리"
        )

    def test_missing_metadata_uses_conventional_fallback(self, seed):
        from modules.flow_gate.services import git_service as svc

        assert svc.build_auto_commit_message("gitprj.default.9999") == (
            "chore: finalize workflow changes"
        )

    def test_resolver_ascii_title_omits_group_scope(self, seed):
        from modules.flow_gate.services import git_service as svc

        group_id = "gitprj.default.0171"
        self._create_group(group_id, "Polish git finalize subject", ["B", "TR"])

        assert svc.resolve_commit_message(group_id) == (
            "fix: Polish git finalize subject",
            "auto_title",
        )

    def test_resolver_fallback_omits_group_scope(self, seed):
        from modules.flow_gate.services import git_service as svc

        assert svc.resolve_commit_message("gitprj.default.9998") == (
            "chore: finalize workflow changes",
            "fallback",
        )
# ── secret handling (L0006 §2.3) ─────────────────────────────────────────────

class TestSecrets:
    def test_mask(self):
        from modules.flow_gate.services import git_service as svc

        assert svc.mask_secret("short") == "********"
        masked = svc.mask_secret("ghp_AbCdEfGh1234567890IjKlMnOpQrStWXYZ")
        assert masked.startswith("ghp_") and masked.endswith("WXYZ") and "*" * 12 in masked

    def test_roundtrip(self):
        from modules.flow_gate.services import git_service as svc

        enc = svc.encrypt_secret("token-123")
        assert enc != "token-123"
        assert svc.decrypt_secret(enc) == "token-123"

    def test_wrong_key_unreadable(self):
        from modules.flow_gate.services import git_service as svc

        enc = svc.encrypt_secret("token-456")
        old = os.environ["FLOWGATE_GIT_ENCRYPT_KEY"]
        os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = base64.b64encode(b"X" * 32).decode()
        try:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.decrypt_secret(enc)
            assert exc.value.code == "git_secret_unreadable"
        finally:
            os.environ["FLOWGATE_GIT_ENCRYPT_KEY"] = old


# ── config CRUD (P0005 §1·§2) ────────────────────────────────────────────────

class TestConfig:
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

    # ── configurable commit author (0237 — R0001/NR0003) ─────────────────────

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


# ── effective source-root resolution (L0006 §2.2·§4.1 — fallback first) ─────

class TestEffectiveSrcRoot:
    def test_fallbacks(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        # no group / no config
        assert svc.effective_src_root("gitprj", None) is None
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # config exists but disabled
        svc.save_config("gitprj", {"repo_url": "https://x/y.git", "enabled": False})
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # enabled but no worktree ledger entry
        svc.save_config("gitprj", {"repo_url": "https://x/y.git", "enabled": True})
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # ledger entry without a real directory → still fallback (E7/E13)
        db_git.register_worktree("gitprj.default.0001", "gitprj", "gitprj_default_0001")
        assert svc.effective_src_root("gitprj", "gitprj.default.0001") is None
        # directory appears → worktree wins
        from modules.flow_gate.storage.paths import src_root

        wt = src_root("GitProj", "gitprj_default_0001")
        wt.mkdir(parents=True, exist_ok=True)
        # 0287 NR0004: the `.git` link is what makes it a worktree rather than a
        # leftover directory from an interrupted teardown.
        (wt / ".git").write_text("gitdir: ../main/.git/worktrees/x", encoding="utf-8")
        resolved = svc.effective_src_root("gitprj", "gitprj.default.0001")
        assert resolved is not None and resolved.name == "gitprj_default_0001"
        # cleanup for later git e2e tests
        shutil.rmtree(wt)
        svc.delete_config("gitprj")

    def test_paths_resolver_group_param(self, seed):
        from modules.flow_gate.storage.paths import resolve_project_src_root

        # group-less call keeps the pre-0115 behavior (project branch folder)
        root = resolve_project_src_root("gitprj")
        assert root is not None and root.name == "main"
        # unknown group falls back identically
        with_group = resolve_project_src_root("gitprj", group_id="gitprj.default.9999")
        assert with_group == root


# ── project lock (L0006 §2.8) ────────────────────────────────────────────────

class TestLock:
    def test_acquire_busy_release_transfer(self, seed):
        from modules.flow_gate.db import git_integration as db_git

        assert db_git.try_acquire_lock("gitprj", "op:a") is True
        assert db_git.try_acquire_lock("gitprj", "op:b") is False
        db_git.release_lock("gitprj", "op:b")  # non-holder release is a no-op
        assert db_git.get_lock("gitprj")["holder"] == "op:a"
        db_git.transfer_lock("gitprj", "op:a", "merge:7")
        assert db_git.get_lock("gitprj")["holder"] == "merge:7"
        db_git.release_lock("gitprj", "merge:7")
        assert db_git.get_lock("gitprj") is None


# ── finalize guards (L0006 §4.2) ─────────────────────────────────────────────

class TestFinalizeGuards:
    def test_non_integrated_group(self, seed):
        from modules.flow_gate.services import git_service as svc

        state = svc.get_finalize_state("plainprj.default.0001")
        assert state["state"]["status"] == "none"
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize("plainprj.default.0001", "merge")
        assert exc.value.status == 409

    def test_preview_flag_reflects_preview_ac(self, seed):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitprj.default.0101"
        svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "enabled": True,
        })
        db_git.register_worktree(group, "gitprj", "gitprj_default_0101")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        state = svc.get_finalize_state(group, preview_ac=True)

        assert state["state"]["status"] == "awaiting_choice"
        assert state["state"]["preview"] is True

    def test_preview_ac_read_only_with_wf_in_progress_root(self, seed):
        """Test7 (NR0003 §20): preview_ac shows preliminary awaiting_choice while
        root is wf_in_progress, without DB changes (0197 T0004 §B, pure read).

        Setup: Root = wf_in_progress (not wf_done), Git status = none.
        Call: GET /finalize?context=approval (preview_ac=True).
        Expect: response shows awaiting_choice display-only, but DB status
                remains none. No side effects on group_git_state.
        """
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import groups as db_groups
        from modules.flow_gate.services import git_service as svc

        group = "gitprj.default.0102"
        project_id = "gitprj"

        svc.save_config(project_id, {
            "repo_url": "https://example.com/team/repo.git",
            "enabled": True,
        })

        # Create group and root document with wf_in_progress (NOT wf_done)
        if db_groups.get_by_id(group) is None:
            db_groups.create({
                "group_id": group, "project_id": project_id,
                "module": "default", "title": "test root",
            })

        doc_id = f"{group}.0001-R"
        if db_docs.get_by_id(doc_id) is None:
            db_docs.create({
                "doc_id": doc_id, "project_id": project_id, "module": "default",
                "group_id": group, "type_code": "R", "seq": 1, "title": "root",
                "file_path": f"documents/{group}/0001-R.md",
            })
        # Set root status to wf_in_progress (not wf_done)
        db_docs.update(doc_id, {"doc_review_status": "wf_in_progress"})

        # Register worktree and set git status to none
        db_git.register_worktree(group, project_id, f"{project_id}_default_0102")
        db_git.set_status(group, "none")

        # Verify _group_root_wf_done returns False (precondition)
        from modules.flow_gate.services.git_service import _group_root_wf_done
        assert not _group_root_wf_done(group), "root should not be wf_done"

        # Capture persisted status before preview call
        before_status = db_git.get_state(group)["status"]
        assert before_status == "none"

        # Call get_finalize_state with preview_ac=True
        state = svc.get_finalize_state(group, preview_ac=True)

        # Display status shows preliminary awaiting_choice (display-only)
        assert state["state"]["status"] == "awaiting_choice"
        assert state["state"]["preview"] is True

        # Verify choices are populated (actionable=True for display)
        assert len(state["state"]["choices"]) > 0

        # Verify DB status unchanged (pure read, no side effects)
        after_status = db_git.get_state(group)["status"]
        assert after_status == "none"
        assert after_status == before_status


# ── finalize action contract (flowgate.default.0331 NR0005 §2·§3) ────────────

class TestFinalizeActionContract0331:
    """The action vocabulary widened additively: `push` no longer fabricates a
    commit, `commit_push`/`commit_only` carry that behaviour explicitly, and the
    constants the three finalize surfaces read are left alone on purpose so no UI
    starts offering an action it cannot yet render (TR0007 §2)."""

    GROUP = "gitprj.default.0109"

    @pytest.fixture
    def git_active_group(self, seed):
        """A git-active group whose worktree dir does not exist — enough state to
        get past `_finalize_context` and reach the action validator, and nothing
        more, so no real git is needed."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        svc.save_config("gitprj", {
            "repo_url": "https://example.com/team/repo.git",
            "base_branch": "main",
            "enabled": True,
        })
        db_git.register_worktree(self.GROUP, "gitprj", "gitprj_default_0109")
        _seed_wf_done_root(self.GROUP, project_id=self.GROUP.split(".", 1)[0])
        db_git.set_status(self.GROUP, "awaiting_choice")
        yield self.GROUP

    def test_action_vocabulary_is_additive(self):
        from modules.flow_gate.services import git_service as svc

        assert svc.ACTION_VALUES == (
            "merge", "merge_only", "push", "commit_push", "commit_only", "wait",
        )
        # Deliberately untouched. The axis UI reads `action_axes` (below), so the
        # legacy choice lists stay frozen as the fallback an older client renders
        # (NR0005 §8 "additive"); widening them would change what that client
        # shows without it knowing the new semantics. The project default stays
        # narrow because its DB CHECK still only allows merge/push/wait.
        assert svc.DEFAULT_FINALIZE_ACTION_VALUES == ("merge", "push", "wait")
        assert svc.FINALIZE_MAIN_CHOICES == ("merge", "merge_only", "wait")
        assert svc.FINALIZE_AUX_CHOICES == ("push",)

    def test_finalize_state_publishes_the_axis_matrix(self, git_active_group):
        from modules.flow_gate.services import git_service as svc

        state = svc.get_finalize_state(git_active_group)["state"]
        axes = state["action_axes"]
        # Approved v4 order — 머지 → 커밋 → 대기 — comes from the server, so the
        # three finalize surfaces cannot drift into different orders.
        assert axes["scopes"] == ["merge", "commit", "none"]
        assert axes["matrix"] == {
            "merge": {"push": "merge", "no_push": "merge_only"},
            "commit": {"push": "commit_push", "no_push": "commit_only"},
            "none": {"push": "push", "no_push": "wait"},
        }
        # 3 scopes x push on/off covers the whole vocabulary exactly once: no
        # action is unreachable from the UI and none is offered twice.
        cells = [a for pair in axes["matrix"].values() for a in pair.values()]
        assert sorted(cells) == sorted(svc.ACTION_VALUES)
        # `push` is NOT a committing action any more (it 409s on dirty instead of
        # absorbing), so the panel must not ask for a commit subject there.
        assert set(axes["commit_actions"]) == set(svc.ACTION_VALUES) - {"push", "wait"}
        # Additive: the legacy lists ride along untouched for an older client.
        assert state["choices"] == list(svc.FINALIZE_MAIN_CHOICES)
        assert state["aux_choices"] == list(svc.FINALIZE_AUX_CHOICES)

    def test_axis_matrix_is_absent_when_nothing_is_actionable(self, git_active_group):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        # A terminal slot offers no choices; the axis payload has to disappear
        # with them or the panel would render a live control over a done group.
        db_git.set_status(git_active_group, "merged")
        state = svc.get_finalize_state(git_active_group)["state"]
        assert state["action_axes"] is None
        assert state["choices"] == []

    def test_finalize_validator_accepts_the_new_actions(self, git_active_group):
        from modules.flow_gate.services import git_service as svc

        # An unknown action stops at the 422 gate; the two new ones pass it and
        # only fail later on this group's missing worktree dir (409). That gap is
        # what proves the shared ACTION_VALUES gate let them through.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(git_active_group, "commit_rebase")
        assert (exc.value.status, exc.value.code) == (422, "invalid_request")

        for action in ("commit_push", "commit_only"):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.finalize(git_active_group, action)
            assert (exc.value.status, exc.value.code) == (409, "invalid_state")
            assert "worktree directory is missing" in exc.value.message

    def test_approval_ride_along_shares_the_action_gate(self, git_active_group):
        from modules.flow_gate.services import git_service as svc

        doc = {"type_code": "AC", "group_id": git_active_group}
        for action in ("commit_push", "commit_only"):
            assert svc.precheck_approve_git_action(doc, action) == git_active_group
        with pytest.raises(svc.GitServiceError) as exc:
            svc.precheck_approve_git_action(doc, "commit_rebase")
        assert exc.value.status == 422

    def test_project_default_action_stays_narrow(self, seed):
        from modules.flow_gate.services import git_service as svc

        # save_config validates against DEFAULT_FINALIZE_ACTION_VALUES, so a
        # commit_* project default is still refused (and refused before any write).
        for action in ("commit_push", "commit_only"):
            with pytest.raises(svc.GitServiceError) as exc:
                svc.save_config("gitprj", {
                    "repo_url": "https://example.com/team/repo.git",
                    "default_finalize_action": action,
                })
            assert exc.value.status == 422

    def test_commit_actions_never_reach_the_merge_session_validator(self, seed):
        from modules.flow_gate.db import git_integration as db_git

        # db_git.ACTION_VALUES gates the finalize_action persisted on a conflict
        # session. Only merge/merge_only can open one — commit_push/commit_only
        # return before create_session — so it is intentionally NOT widened. Pin
        # both halves: the narrow list, and its refusal of a commit_* value. If a
        # later change ever routes commit_push through a session this test names
        # the reason the insert blows up.
        assert db_git.ACTION_VALUES == ("merge", "merge_only", "push", "wait")
        for session_capable in ("merge", "merge_only"):
            assert session_capable in db_git.ACTION_VALUES
        with pytest.raises(ValueError):
            db_git.create_session(self.GROUP, ["f.txt"], finalize_action="commit_push")


# ── REAL git end-to-end (L0006 §2.4~§2.7) ────────────────────────────────────

def _git(args, cwd=None, env_extra=None):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=env
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture(scope="class")
def origin_repo(seed):
    """A local bare origin with an initial commit on main + enabled config."""
    from modules.flow_gate.services import git_service as svc

    tmp = Path(tempfile.mkdtemp(prefix="fg-git-origin-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / "shared.py").write_text('"line1"\n', encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("gitprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("gitprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestGitEndToEnd:
    GROUP = "gitprj.default.0100"

    def test_connection_ok(self, origin_repo):
        from modules.flow_gate.services import git_service as svc

        result = svc.test_connection("gitprj", {})
        assert result["reachable"] is True
        assert result["authenticated"] is True
        assert result["base_branch_exists"] is True

    def test_ensure_worktree_and_idempotence(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        assert svc.ensure_worktree("gitprj", "default", self.GROUP) == "ok"
        wt = src_root("GitProj", "gitprj_default_0100")
        assert wt.is_dir() and (wt / "README.md").is_file()
        state = db_git.get_state(self.GROUP)
        assert state["worktree_registered"] == 1
        assert state["branch"] == "gitprj_default_0100"
        # second call: idempotent (created=false path), still ok
        assert svc.ensure_worktree("gitprj", "default", self.GROUP) == "ok"
        # worker CRUD resolution now points at the worktree
        assert svc.effective_src_root("gitprj", self.GROUP) == wt.resolve()

    def test_finalize_wait_then_merge(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        wt = src_root("GitProj", "gitprj_default_0100")
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")

        _seed_wf_done_root(self.GROUP, project_id=self.GROUP.split(".", 1)[0])
        db_git.set_status(self.GROUP, "awaiting_choice")
        out = svc.finalize(self.GROUP, "wait")
        assert out["result"]["status"] == "waiting"

        state = svc.get_finalize_state(self.GROUP)["state"]
        assert state["status"] == "waiting"
        assert state["choices"] == ["merge", "merge_only", "wait"]
        assert state["aux_choices"] == ["push"]

        subject = "fix(git_service): use confirmed merge subject"
        out = svc.finalize(self.GROUP, "merge", commit_message=subject)
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is True
        assert out["result"]["merge_commit"]
        # flowgate.default.0232 B0001: the merge commit no longer reuses the work
        # subject — reusing it stamped two commits of identical title+diff onto
        # origin ("same code committed twice"). The top of origin main is now a
        # conventional Merge commit; the confirmed work subject rides in on its
        # second parent (the absorb commit), a normal work-commit + merge-commit
        # pair. `main^2` also proves the two-parent topology unmerge relies on.
        top_subject = _git(["log", "-1", "--format=%s", "main"], cwd=origin_repo["bare"]).strip()
        assert top_subject.startswith("Merge branch ")
        assert top_subject != subject
        assert _git(["log", "-1", "--format=%s", "main^2"], cwd=origin_repo["bare"]).strip() == subject
        # origin main actually contains the group's work
        files = _git(
            ["ls-tree", "--name-only", "main"], cwd=origin_repo["bare"]
        ).split()
        assert "work.txt" in files
        # B flowgate.default.0172.0001-B: a merge lands the work in default but
        # must NOT publish the intermediate work branch to origin.
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0100" not in heads
        # re-finalize is rejected (already finalized)
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(self.GROUP, "merge")
        assert exc.value.status == 409

    def test_push_action(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0101"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0101")
        (wt / "feature.txt").write_text("branch only\n", encoding="utf-8")
        _git(["add", "-A"], cwd=wt)
        _git(["commit", "-m", "feat: branch only"], cwd=wt)
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "push")
        assert out["result"]["status"] == "pushed"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0101" in heads

    def test_push_action_rejects_dirty_worktree(self, origin_repo):
        # NR flowgate.default.0331.0005 §3: `push` may only send commits that
        # already exist. A dirty worktree used to be silently absorbed into a
        # new commit and pushed — indistinguishable from `commit_push` — which
        # risked publishing worker edits nobody confirmed. It must now be
        # rejected (409) and leave the worktree untouched.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0106"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0106")
        (wt / "feature.txt").write_text("branch only\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group, "push")
        assert exc.value.status == 409
        assert exc.value.code == "dirty_worktree"
        assert exc.value.details["files"] == ["feature.txt"]

        # nothing was committed or pushed; the group is still finalize-able.
        assert _git(["status", "--porcelain"], cwd=wt).strip() == "?? feature.txt"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0106" not in heads
        assert db_git.get_state(group)["status"] == "awaiting_choice"

    def test_commit_push_action_commits_dirty_then_pushes(self, origin_repo):
        # NR flowgate.default.0331.0005 §3: `commit_push` is the action that
        # replaces the old (mis-scoped) `push` behavior of absorbing dirty
        # worktree edits — it commits them, then publishes the branch.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0107"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0107")
        (wt / "feature.txt").write_text("branch only\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "commit_push", commit_message="feat: commit and push")
        assert out["result"]["status"] == "pushed"
        assert out["result"]["action"] == "commit_push"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0107" in heads
        # the group slot was torn down like a bare push (origin keeps the branch).
        assert db_git.get_state(group)["worktree_registered"] == 0

    def test_commit_only_action_keeps_worktree_waiting(self, origin_repo):
        # NR flowgate.default.0331.0005 §3: `commit_only` commits dirty edits
        # locally without pushing or merging, and — unlike merge/push — must NOT
        # tear the slot down, since the group can still be merged/pushed/archived
        # later.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0108"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0108")
        (wt / "feature.txt").write_text("branch only\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "commit_only", commit_message="feat: commit only")
        assert out["result"]["status"] == "waiting"
        assert out["result"]["action"] == "commit_only"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0108" not in heads

        state = db_git.get_state(group)
        assert state["worktree_registered"] == 1
        assert state["status"] == "waiting"
        assert _git(["status", "--porcelain"], cwd=wt).strip() == ""
        assert _git(["log", "-1", "--format=%s"], cwd=wt).strip() == "feat: commit only"

        # still finalize-able afterward (e.g. push it now) — commit_push does not
        # touch the shared base checkout, unlike merge/merge_only.
        pushed = svc.finalize(group, "commit_push")
        assert pushed["result"]["status"] == "pushed"
        heads = _git(["ls-remote", "--heads", str(origin_repo["bare"])])
        assert "refs/heads/gitprj_default_0108" in heads

    def test_merge_only_unpushed_status_and_unmerge(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0105"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0105")
        (wt / "local-only.txt").write_text("not pushed yet\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "merge_only", commit_message="feat: local merge only")
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is False
        merge_commit = out["result"]["merge_commit"]

        assert "local-only.txt" in _git(["ls-tree", "--name-only", "main"], cwd=src_root("GitProj", "main"))
        assert "local-only.txt" not in _git(
            ["ls-tree", "--name-only", "main"], cwd=origin_repo["bare"]
        )

        status = svc.project_git_status("gitprj")["status"]
        unpushed = status["unpushed"]
        assert unpushed["count"] == 1
        assert unpushed["commit_count"] >= unpushed["count"]
        assert unpushed["merges"][0]["group_id"] == group
        assert unpushed["merges"][0]["can_unmerge"] is True

        undo = svc.unmerge(group, merge_commit)["result"]
        assert undo["unmerged"] is True
        assert undo["group_status"] == "awaiting_choice"
        assert undo["reprovisioned"] is True

        state = db_git.get_state(group)
        assert state["status"] == "awaiting_choice"
        assert state["merge_commit"] is None
        assert state["worktree_registered"] == 1
        assert (wt / "local-only.txt").read_text(encoding="utf-8") == "not pushed yet\n"
        assert "local-only.txt" not in _git(["ls-tree", "--name-only", "main"], cwd=src_root("GitProj", "main"))

    def test_conflict_resolve_flow(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0102"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0102")

        # group edits shared.py …
        (wt / "shared.py").write_text('"group version"\n', encoding="utf-8")
        # … while origin main moves the same line via the seed worktree
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)  # catch up (earlier merges landed)
        (seedwt / "shared.py").write_text('"mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        assert out["result"]["conflict_files"] == ["shared.py"]
        # 0205: conflict sessions no longer hold the project mutex; the base is
        # protected by the open-session guard instead.
        assert db_git.get_lock("gitprj") is None
        assert svc.open_merge_session_of_project("gitprj")["merge_id"] == merge_id
        group2 = "gitprj.default.0103"
        assert svc.ensure_worktree("gitprj", "default", group2) == "ok"
        _seed_wf_done_root(group2, project_id=group2.split(".", 1)[0])
        db_git.set_status(group2, "awaiting_choice")
        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group2, "merge")
        assert exc.value.code == "merge_conflict_open"

        conflicts = svc.list_conflicts(group, merge_id)
        assert conflicts["files"][0]["path"] == "shared.py"
        content = conflicts["files"][0]["content"]
        assert "<<<<<<<" in content
        assert "|||||||" in content, "zdiff3 base marker must be present"
        assert conflicts["files"][0]["conflict_count"] == 1

        # Verify base content matches the common ancestor
        # Extract base section from zdiff3 conflict markers
        import re
        marker_pattern = r'^<{7}[^\n]*\n(.*?)^\|{7}[^\n]*\n(.*?)^={7}\n(.*?)^>{7}'
        match = re.search(marker_pattern, content, re.MULTILINE | re.DOTALL)
        assert match, "Should parse zdiff3 conflict with base marker"
        ours_content, base_content, theirs_content = match.groups()
        assert base_content.strip() == '"line1"', "Base marker should separate common ancestor content"
        assert ours_content.strip() == '"mainline version"', "Merge target (main) should be ours"
        assert theirs_content.strip() == '"group version"', "Merge source (group branch) should be theirs"

        # markers left in the submitted content → 422, nothing written
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "shared.py",
                "content": conflicts["files"][0]["content"],
            }], True)
        assert exc.value.code == "conflict_markers_remain"

        # a file outside the session → 422 (E12)
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "README.md", "content": "x\n",
            }], True)
        assert exc.value.status == 422

        # 0481 T0008: a resolved general merge now stops at resolved_pending_review
        # unless auto_authority was recorded (D0006 §3.2/§3.4). This test is about
        # the merge/push mechanics, not the review gate itself (covered separately
        # in test_git_merge_review_gate_0481.py), so it opts into the pre-existing
        # immediate-merge behavior through the officially supported bypass.
        svc.record_auto_authority(group, merge_id, True)
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"group version"\n"mainline version"\n',
        }], True)
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is True
        # flowgate.default.0232 B0001: the conflict-resolution merge commit must
        # ALSO carry a conventional Merge subject, not the reused work subject.
        # This path likewise precedes the merge with an absorb commit, so reusing
        # the finalize subject stamped two commits of identical title+diff onto
        # origin ("same code committed twice"). Guard the fix here too — without
        # this assertion the conflict path could silently regress to the old
        # resolve_commit_message()[0] subject while test_finalize_wait_then_merge
        # (clean path only) still passes.
        top_subject = _git(["log", "-1", "--format=%s", "main"], cwd=origin_repo["bare"]).strip()
        assert top_subject.startswith("Merge branch ")
        absorb_subject = _git(["log", "-1", "--format=%s", "main^2"], cwd=origin_repo["bare"]).strip()
        assert top_subject != absorb_subject  # a normal work+merge pair, not a duplicate
        assert db_git.get_lock("gitprj") is None
        session = db_git.get_session(merge_id)
        assert session["status"] == "done"

    def test_resolve_conflicts_rejects_one_sided_drop(self, origin_repo):
        # 0478 T0012 completion (i): a base-having chunk where BOTH sides changed
        # something over the common ancestor, but the submitted content keeps only
        # one of them (markers all gone) → 422 conflict_side_dropped, nothing written.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0110"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "sidecheck.txt").write_text("base line\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add sidecheck base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0110")
        (wt / "sidecheck.txt").write_text("group change\n", encoding="utf-8")

        (seedwt / "sidecheck.txt").write_text("mainline change\n", encoding="utf-8")
        _git(["commit", "-am", "mainline change to sidecheck"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        conflicts = svc.list_conflicts(group, merge_id)
        entry = next(f for f in conflicts["files"] if f["path"] == "sidecheck.txt")
        assert "|||||||" in entry["content"], "zdiff3 base marker required for this check"
        # a finalize/merge conflict lives in the shared BASE checkout, not the group's own
        # worktree — resolve_conflict_src_root() is the production accessor for that root.
        conflict_root = svc.resolve_conflict_src_root(group, merge_id)
        before_bytes = (conflict_root / "sidecheck.txt").read_bytes()

        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "sidecheck.txt",
                "content": "group change\n",  # drops the mainline ("ours") side entirely
            }], True)
        assert exc.value.code == "conflict_side_dropped"
        assert exc.value.status == 422

        # nothing was written: same bytes on disk, markers still there, session still open
        assert (conflict_root / "sidecheck.txt").read_bytes() == before_bytes
        assert "<<<<<<<" in (conflict_root / "sidecheck.txt").read_text(encoding="utf-8")
        still_open = svc.list_conflicts(group, merge_id)
        still_entry = next(f for f in still_open["files"] if f["path"] == "sidecheck.txt")
        assert still_entry["conflict_count"] == 1

        # abort so this session doesn't block later tests' project-level lock
        svc.abort_merge(group, merge_id)

    def test_base_dirty_belongs_to_the_merge_while_a_conflict_is_open(self, origin_repo):
        """0481 T0010 #1 — why [AI에게 맡기기] kept answering "…시작하지 못했습니다.".

        A stopped merge leaves its unmerged AND its cleanly-merged paths in the base
        checkout's `git status --porcelain`, so `base_dirty` fills up with the merge itself
        and the Git panel offers it as stray-edit cleanup: [AI에게 맡기기], per-file
        [되돌리기], [커밋]. All three are wrong there, and the AI one could never start.
        `base_dirty.merge_in_progress` is the fact the panel needs to say so instead.
        """
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0172"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "ownership.txt").write_text("base line\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add ownership base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0172")
        (wt / "ownership.txt").write_text("group change\n", encoding="utf-8")

        (seedwt / "ownership.txt").write_text("mainline change\n", encoding="utf-8")
        _git(["commit", "-am", "mainline change to ownership"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        # No merge yet: the base checkout is clean and owns nothing.
        assert svc.base_merge_in_progress("gitprj") is None

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        # The conflict really does show up as base-checkout dirt — this is the pile the
        # panel was inviting the operator to "clean up".
        base_dirty = svc.project_git_status("gitprj")["status"]["base_dirty"]
        assert "ownership.txt" in base_dirty["files"]
        assert base_dirty["merge_in_progress"] == {"merge_id": merge_id, "group_id": group}
        assert svc.base_merge_in_progress("gitprj") == {"merge_id": merge_id, "group_id": group}

        svc.abort_merge(group, merge_id)

        # Once the merge is gone the same files are ordinary base dirt again (or gone).
        assert svc.base_merge_in_progress("gitprj") is None
        assert svc.project_git_status("gitprj")["status"]["base_dirty"]["merge_in_progress"] is None

    def test_resolve_conflicts_allows_both_sides_kept(self, origin_repo):
        # 0478 T0012 completion (ii): the ordinary "markers just gone" GREEN path is
        # untouched when both sides' added lines survive — exact line reproduction of
        # the original chunk is not required, only that each side's contribution is
        # present somewhere in the submitted content.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0111"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "sidecheck2.py").write_text("base line\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add sidecheck2 base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0111")
        (wt / "sidecheck2.py").write_text('"group change"\n', encoding="utf-8")

        (seedwt / "sidecheck2.py").write_text('"mainline change"\n', encoding="utf-8")
        _git(["commit", "-am", "mainline change to sidecheck2"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        conflicts = svc.list_conflicts(group, merge_id)
        entry = next(f for f in conflicts["files"] if f["path"] == "sidecheck2.py")
        assert "|||||||" in entry["content"]

        # both sides' lines present, reordered and paraphrased around — not a byte-for-byte
        # reproduction of the original chunk — must still pass.
        # 0481 T0008: opt into the pre-existing immediate-merge behavior (see the
        # note in test_conflict_resolve_flow above) — this test is about the
        # side-drop content check, not the review gate.
        svc.record_auto_authority(group, merge_id, True)
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "sidecheck2.py",
            "content": '"combined:"\n"mainline change"\n"group change"\n',
        }], True)
        assert out["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_resolve_conflicts_skips_baseless_add_add_chunk(self, origin_repo):
        # 0478 T0012 completion (iii): a chunk with NO "|||||||" section at all (T0012's
        # "zdiff3 이전에 만들어진 구세션") must not be checked — base=None means there is no
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
        # must pass exactly like it did before T0012 (markers gone → done).
        # 0481 T0008: opt into the pre-existing immediate-merge behavior (see the
        # note in test_conflict_resolve_flow above) — this test is about the
        # baseless-chunk skip, not the review gate.
        svc.record_auto_authority(group, merge_id, True)
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "onlynew.py",
            "content": '"group-only addition"\n',
        }], True)
        assert out["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_judge_hop_settles_none_when_conflict_side_dropped(self, origin_repo):
        # 0478 T0012 completion (iv): an action_scope=="resolve_conflict" run whose only
        # resolve attempt was rejected by conflict_side_dropped must settle
        # outcome != "complete". ai_invoke_service._judge_hop is called DIRECTLY on a real
        # run dict against a real, still-open conflict session — no mock of _judge_hop,
        # _conflict_resolved, or git_service.list_conflicts anywhere in this test.
        import time

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service as ai_svc
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0113"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "sidecheck3.txt").write_text("base line\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add sidecheck3 base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0113")
        (wt / "sidecheck3.txt").write_text("group change\n", encoding="utf-8")
        (seedwt / "sidecheck3.txt").write_text("mainline change\n", encoding="utf-8")
        _git(["commit", "-am", "mainline change to sidecheck3"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        # the worker's only resolve attempt drops one whole side → rejected, session stays open
        with pytest.raises(svc.GitServiceError) as exc:
            svc.resolve_conflicts(group, merge_id, [{
                "path": "sidecheck3.txt",
                "content": "group change\n",
            }], True)
        assert exc.value.code == "conflict_side_dropped"

        # the minimal real run dict _judge_hop's resolve_conflict branch actually reads
        # (run_id/group_id for the exception-log path, merge_id + action_scope to dispatch).
        run = {
            "run_id": "test-run-0478-t0012-iv",
            "group_id": group,
            "merge_id": merge_id,
            "action_scope": "resolve_conflict",
        }
        started = time.monotonic()
        ai_svc._judge_hop(run)
        elapsed = time.monotonic() - started
        assert elapsed >= ai_svc.ORACLE_SETTLE_SEC, "must really sleep(ORACLE_SETTLE_SEC), not a mock"

        assert run["outcome"] != "complete"
        assert run["outcome"] == "none"
        assert run["docs_reached"] == 0
        assert run["reached_doc_ids"] == []

        # abort so this session doesn't block later tests' project-level lock
        svc.abort_merge(group, merge_id)

    def test_conflict_side_dropped_422_flows_through_resolve_token_and_retries(
        self, origin_repo, monkeypatch,
    ):
        # 0478 T0012 작업 항목 3 (TR0013 rev0 반려 대응): the review demanded proof that
        # `conflict_side_dropped` isn't just raised in-process — it must survive the real
        # POST /groups/{g}/git/merge/{m}/resolve-token route (git_routes.py:490) and be
        # handled by ai_invoke_service._api_execute's conflict_pending branch exactly like
        # every other non-2xx status (tool_result failure + continue), then let a corrected
        # resubmission succeed on retry. Only `verify_bearer` is stubbed (the worker-token
        # auth boundary itself is covered by test_tokens_resolve_conflict_scope_0233.py /
        # test_resolve_conflict_issue_lease_block_0447.py) and `urllib.request.urlopen` is
        # redirected into a real FastAPI TestClient carrying the real router — everything
        # downstream (git_routes.post_merge_resolve_token, git_service.resolve_conflicts,
        # ai_invoke_service._resolve_conflict, ai_invoke_service._api_execute) runs
        # unmocked, against the real conflict session created by finalize() below.
        import io
        import threading
        import time
        import urllib.error
        import urllib.parse
        import urllib.request

        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from modules.flow_gate.api.v1 import git_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service as ai_svc
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git_service import GitServiceError
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0114"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "sidecheck4.py").write_text("base line\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "add sidecheck4 base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0114")
        (wt / "sidecheck4.py").write_text('"group change"\n', encoding="utf-8")
        (seedwt / "sidecheck4.py").write_text('"mainline change"\n', encoding="utf-8")
        _git(["commit", "-am", "mainline change to sidecheck4"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        # 0481 T0008: this test is about the HTTP retry mechanic (422 → corrected
        # resubmission → merged), not the review gate — record_auto_authority is
        # the officially supported bypass a human's [AI 호출] would have set before
        # this same worker token was minted (see test_conflict_resolve_flow above).
        svc.record_auto_authority(group, merge_id, True)

        # real FastAPI app carrying the real resolve-token route, plus the same global
        # GitServiceError envelope handler routers.main installs in production (belt and
        # braces — post_merge_resolve_token already catches GitServiceError itself).
        app = FastAPI()
        app.include_router(git_routes.router)

        @app.exception_handler(GitServiceError)
        async def _handler(request: Request, exc: GitServiceError):  # noqa: ANN202
            error: dict = {"code": exc.code, "message": exc.message}
            if exc.details:
                error["details"] = exc.details
            return JSONResponse(status_code=exc.status, content={"ok": False, "error": error})

        client = TestClient(app, raise_server_exceptions=False)
        monkeypatch.setattr(
            git_routes, "verify_bearer",
            lambda request: {
                "action_scope": "resolve_conflict", "group_id": group,
                "merge_id": merge_id, "token_id": "tok_rc_test_0478", "project": "gitprj",
            },
        )

        raw_calls: list[tuple[int, dict]] = []

        class _FakeHTTPResponse:
            def __init__(self, status, body):
                self.status = status
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        def _fake_urlopen(req, timeout=120):
            parsed = urllib.parse.urlsplit(req.full_url)
            resp = client.post(parsed.path, content=req.data, headers=dict(req.header_items()))
            raw_calls.append((resp.status_code, resp.json()))
            if 200 <= resp.status_code < 300:
                return _FakeHTTPResponse(resp.status_code, resp.content)
            raise urllib.error.HTTPError(
                req.full_url, resp.status_code, "", resp.headers, io.BytesIO(resp.content),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        # turn 1: the worker drops the mainline side entirely (same payload as (i) above,
        # this time submitted through the real HTTP tool the worker actually calls).
        # turn 2: the worker retries with both sides kept, exactly the (ii) shape.
        attempts: list[str] = []

        def fake_model(*args):
            tool_name = args[5]
            attempts.append(tool_name)
            if len(attempts) == 1:
                payload = {
                    "files": [{"path": "sidecheck4.py", "content": '"group change"\n'}],
                    "complete": True,
                }
            else:
                payload = {
                    "files": [{
                        "path": "sidecheck4.py",
                        "content": '"combined:"\n"mainline change"\n"group change"\n',
                    }],
                    "complete": True,
                }
            return "resolving", {"id": f"tc{len(attempts)}", "name": tool_name, "input": payload}, {
                "role": "assistant", "content": "resolving", "tool_calls": [],
            }

        monkeypatch.setattr(ai_svc, "_call_openai", fake_model)
        monkeypatch.setattr(ai_svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")
        tool_results: list[str] = []
        _orig_tool_result_msg = ai_svc._tool_result_msg

        def _spy_tool_result_msg(kind, tool_call, text):
            tool_results.append(text)
            return _orig_tool_result_msg(kind, tool_call, text)

        monkeypatch.setattr(ai_svc, "_tool_result_msg", _spy_tool_result_msg)

        run = {
            "project_id": "gitprj", "chain_source": "system", "run_id": "aiv_conflict_retry_0478",
            "docs_target": 0, "raw_token": "tok_rc_test_0478", "action_scope": "resolve_conflict",
            "mode": "single", "group_id": group, "merge_id": merge_id,
            "api_base_url": "http://fake-host/api/v1",
            "cancel_event": threading.Event(), "started_mono": time.monotonic(), "timeout_sec": 30,
        }
        provider = {
            "id": "aip_api_0478", "exec_type": "api", "kind": "openai",
            "api_base_url": "http://fake-host", "api_model": "test-model",
        }

        result = ai_svc._api_execute(provider, "prompt", run)

        # (a) the real resolve-token HTTP round trip actually produced the new 422.
        assert [status for status, _ in raw_calls] == [422, 200]
        assert raw_calls[0][1]["error"]["code"] == "conflict_side_dropped"
        assert raw_calls[1][1]["result"]["status"] == "merged"

        # (b) _api_execute's conflict_pending branch treated the 422 exactly like any other
        # non-2xx resolve failure — tool_result + continue, no special-cased short-circuit —
        # and the worker's second, corrected attempt was retried and accepted.
        assert attempts == [ai_svc._RESOLVE_TOOL_NAME, ai_svc._RESOLVE_TOOL_NAME]
        assert len(tool_results) == 2
        assert "Conflict resolve failed (HTTP 422)" in tool_results[0]
        assert "conflict_side_dropped" in tool_results[0]
        assert "merged" in tool_results[1]
        assert result == ("started_ok", None)

        # the retry actually merged for real — same production side effect as (ii) above.
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_abort_flow(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0104"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0104")
        (wt / "shared.py").write_text("another group version\n", encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text("mainline again\n", encoding="utf-8")
        _git(["commit", "-am", "mainline again"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        out = svc.abort_merge(group, merge_id)
        assert out["result"]["status"] == "waiting"
        assert db_git.get_lock("gitprj") is None
        assert db_git.get_session(merge_id)["status"] == "aborted"
        # after abort the group can re-choose (wait keeps it re-selectable)
        state = svc.get_finalize_state(group)["state"]
        assert state["status"] == "waiting"

    # ── review gate (flowgate.default.0481 D0006/L0007, T0008) ──────────────

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

    def test_review_gate_auto_authority_commits_without_a_screen(self, origin_repo):
        # D0006 §3.2: [자동] is recorded ONLY by a human call before resolution, and
        # then bypasses the screen for a normal completion — but the safety net
        # (identity check, py_compile, conditional push) still runs underneath it.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0122"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0122")
        (wt / "shared.py").write_text('"auto group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"auto mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "auto mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]

        # the checkbox is recorded by a human call BEFORE resolution — never by a
        # field the resolve submission itself carries.
        svc.record_auto_authority(group, merge_id, True)
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"auto group version"\n"auto mainline version"\n',
        }], True)
        assert out["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_review_gate_worker_token_cannot_grant_itself_auto(self):
        # D0006 §3.2 / L0007 §2.2: the worker-token resolve body model has no `auto`
        # field and forbids extras outright — a token cannot self-approve.
        from pydantic import ValidationError

        from modules.flow_gate.api.v1.git_routes import ResolveBody

        with pytest.raises(ValidationError):
            ResolveBody(files=[], complete=True, auto=True)
        with pytest.raises(ValidationError):
            ResolveBody(files=[], complete=True, approve=True)

    def test_review_gate_reject_restores_conflict_markers(self, origin_repo):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0123"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0123")
        (wt / "shared.py").write_text('"reject group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"reject mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "reject mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        original = svc.list_conflicts(group, merge_id)
        original_content = next(
            f["content"] for f in original["files"] if f["path"] == "shared.py"
        )
        assert "<<<<<<<" in original_content

        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"reject group version"\n"reject mainline version"\n',
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"

        started: dict = {}

        def _fake_start_run(first_message):
            started["msg"] = first_message
            return "aiv_fake_retry"

        rejected = svc.reject_merge_review(
            group, merge_id,
            reason="다시 확인해 주세요 — 이 사유는 충분히 깁니다.",
            provider_id="prov_test", provider_pinned=True,
            start_run=_fake_start_run,
        )
        assert rejected["result"]["status"] == "returned_to_resolver"
        assert rejected["result"]["resolver_run_id"] == "aiv_fake_retry"
        assert started["msg"]

        restored = svc.list_conflicts(group, merge_id)
        restored_content = next(
            f["content"] for f in restored["files"] if f["path"] == "shared.py"
        )
        assert restored_content == original_content
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("review_state") is None
        assert "snapshot_tree" not in context
        assert context.get("auto_authority") is False

        svc.abort_merge(group, merge_id)

    def test_review_gate_py_compile_failure_blocks_approval(self, origin_repo):
        # D0006 §3.5 / L0007 §2.7: a Python syntax error in the frozen candidate
        # blocks approval even though the identity/fingerprint check passed.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0124"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0124")
        (wt / "bad.py").write_text("group addition\n", encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "bad.py").write_text("mainline addition\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline adds bad.py independently"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]

        # keeps a trace of both sides (0478 T0012's conflict_side_dropped check
        # would otherwise 422 on this add/add chunk before the syntax check ever
        # runs) while still being invalid Python.
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.py",
            "content": "group addition\nmainline addition\ndef broken(:\n    pass\n",
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"
        fingerprint = out["result"]["review_fingerprint"]
        before_head = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        rejected = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert rejected["result"]["status"] == "pre_commit_validation_failed"
        assert rejected["result"]["review_state"] == "resolved_pending_review"
        errors = rejected["result"]["errors"]
        assert any(e["path"] == "bad.py" for e in errors)
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == before_head
        assert db_git.get_session(merge_id)["status"] == "open"

        # the human corrects it and tries again from the SAME pending session — a
        # fresh submission through the ordinary resolve endpoint refreezes and the
        # corrected candidate approves cleanly.
        out2 = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.py",
            "content": "def fixed():\n    pass\n",
        }], True)
        assert out2["result"]["status"] == "resolved_pending_review"
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=out2["result"]["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_review_gate_conditional_push_rejects_a_moved_remote(self, origin_repo):
        # D0006 §3.6 / L0007 §2.8: someone else pushed to origin/main in the window
        # between freeze and approve. The local checkout's own HEAD/MERGE_HEAD never
        # moved (so the identity check alone would not catch this), but the
        # conditional push's --force-with-lease is keyed on the REMOTE position
        # recorded at freeze time and must reject, roll the local commit back, and
        # land on a freshly re-frozen re_review rather than silently overwriting the
        # other push.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0125"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0125")
        (wt / "shared.py").write_text('"cas group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"cas mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "cas mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"cas group version"\n"cas mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        # a THIRD party pushes to origin/main without ever touching this project's
        # base checkout — expected_remote_head (frozen above) is now stale.
        (seedwt / "unrelated.txt").write_text("someone else\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "unrelated concurrent push"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)
        moved_remote = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        result = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert result["result"]["status"] == "re_review"
        assert result["result"]["review_state"] == "re_review"
        # the local merge commit was rolled back, not left dangling on the base
        # checkout, and the other party's push on origin was left untouched.
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == moved_remote
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["review_state"] == "re_review"
        assert context["merge_commit"] is None
        assert context["expected_remote_head"] == moved_remote
        new_fingerprint = context["review_fingerprint"]
        assert new_fingerprint != fingerprint

        # re-approving with the STALE fingerprint is refused; the new one succeeds
        # and now correctly targets the moved remote.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.approve_merge_review(
                group, merge_id, attempt_id=str(uuid.uuid4()),
                review_fingerprint=fingerprint, authority="human",
            )
        assert exc.value.code == "stale_review"
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=new_fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_review_gate_conversation_message_appends_ai_turn(self, origin_repo, monkeypatch):
        # D0006 §3.7/§3.9 / L0007 §2.9: a propose-only question during pending
        # review starts a bound run and, once it finishes, its last message is
        # folded into the conversation on the next read — without moving the
        # review off resolved_pending_review or touching the fingerprint.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0126"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0126")
        (wt / "shared.py").write_text('"convo group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"convo mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "convo mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"convo group version"\n"convo mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="ko.ts 쪽은 왜 이렇게 바꿨어?",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_convo",
        )
        assert sent["result"]["status"] == "accepted"
        assert sent["result"]["run_id"] == "aiv_fake_convo"
        # a second message while a run is still active is refused.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.send_review_message(
                group, merge_id, message="still waiting",
                provider_id="prov_test", provider_pinned=True, apply_requested=False,
                start_run=lambda: "aiv_should_not_start",
            )
        assert exc.value.code == "re_instruction_busy"

        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "새로 쓰는 라벨이라 키를 추가했습니다.",
                "provider_id": "prov_test",
            },
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint  # unchanged: propose-only
        roles = [(t["role"], t["message"]) for t in review["conversation"]]
        assert ("human", "ko.ts 쪽은 왜 이렇게 바꿨어?") in roles
        assert ("ai", "새로 쓰는 라벨이라 키를 추가했습니다.") in roles

        # the completed run's slot is cleared — a new message can start another one.
        sent2 = svc.send_review_message(
            group, merge_id, message="고마워요",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_convo_2",
        )
        assert sent2["result"]["status"] == "accepted"
        svc.abort_merge(group, merge_id)

    def test_review_gate_lets_an_unvalidatable_file_type_through_the_merge(self, origin_repo):
        # 0481 TR0010 rev3, human rejection 2026-09-08 10:33 ("머지는 되지도 않음").
        #
        # This test used to assert the opposite: TR0009 rev1 (AI review finding 1)
        # read L0007 §2.7 as covering every changed path in the candidate, so an
        # extension with no registered validator vetoed the approval. §2.7 scopes
        # that rule to the WRITE PLAN's own targets (`syntax_validation_scope` =
        # "변경되거나 생성된 모든 plan 대상 파일"), and applying it to a merge made any
        # merge carrying a `.md`, `.txt`, `.lock` or `.tsbuildinfo` permanently
        # unapprovable — [승인] could only ever answer pre_commit_validation_failed.
        #
        # The merge must complete. The plan-side rule is unchanged and is asserted
        # on the very same tree at the end of this test.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0127"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0127")
        # added independently on both sides — no common ancestor for this path,
        # so the side-drop check (unrelated to this test) is skipped.
        (wt / "notes.rst").write_text('"group notes"\n', encoding="utf-8")
        (seedwt / "notes.rst").write_text('"mainline notes"\n', encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline adds notes.rst independently"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        before_head = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "notes.rst",
            "content": '"group notes"\n"mainline notes"\n',
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"
        fingerprint = out["result"]["review_fingerprint"]

        # The candidate the human approves, captured before the commit exists.
        context = db_git.session_context(db_git.get_session(merge_id))
        base_root = svc._base_root_of("gitprj")

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged", approved
        assert approved["result"]["review_state"] == "completed"
        # It really merged and really pushed — the remote head moved.
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() != before_head
        assert db_git.get_session(merge_id)["status"] != "open"

        # The write-plan rule is untouched: the SAME path, the SAME candidate tree,
        # asked with the plan's mode, is still refused whole.
        plan_errors = svc._validate_review_changed_paths(
            base_root, context, unregistered_extension="reject",
        )
        assert any(
            e["path"] == "notes.rst" and e["validator"] == "unsupported"
            for e in plan_errors
        ), plan_errors

    def test_review_gate_real_typescript_parser_blocks_approval(self, origin_repo):
        # 0481 TR0009 rev1 (AI review finding 2): L0007 §2.7 requires the project
        # TypeScript compiler's no-emit syntactic check for `*.ts`, not a
        # dependency-free delimiter-balance heuristic. `const x: number = ;` has
        # every bracket/quote/comment balanced — the old `_check_balanced_source`
        # would have passed it — but it is not valid TypeScript.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0128"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0128")
        (wt / "bad.ts").write_text('const groupOnly = 1;\n', encoding="utf-8")
        (seedwt / "bad.ts").write_text('const mainlineOnly = 1;\n', encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline adds bad.ts independently"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        # balanced brackets/quotes/comments throughout — the old check would pass this.
        # Keeps both sides' added lines verbatim so the pre-existing side-drop guard
        # (_conflict_side_dropped, unrelated to this review-gate work) does not fire
        # before the resolution ever reaches the real TypeScript parser check.
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.ts",
            "content": "const groupOnly = 1;\nconst mainlineOnly = 1;\nconst x: number = ;\n",
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"
        fingerprint = out["result"]["review_fingerprint"]

        rejected = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert rejected["result"]["status"] == "pre_commit_validation_failed"
        errors = rejected["result"]["errors"]
        assert any(e["path"] == "bad.ts" and e["validator"] == "ecmascript" for e in errors)

        out2 = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.ts",
            "content": "const x: number = 1;\n",
        }], True)
        assert out2["result"]["status"] == "resolved_pending_review"
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=out2["result"]["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_review_gate_vue_validates_both_script_and_script_setup_blocks(self, origin_repo):
        # 0481 TR0009 rev2 (AI review finding 2): a valid SFC may carry BOTH a
        # `<script setup>` and an ordinary `<script>` block — Vue's own compiler
        # merges them at build time. `checkVue` used to pick only ONE via
        # `descriptor.scriptSetup || descriptor.script`, so a syntax error in
        # whichever block lost that `||` reached approval unnoticed. Here the
        # `<script setup>` block is syntactically valid and the ordinary
        # `<script>` block is not — the old code would have approved this.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0135"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0135")
        (wt / "bad.vue").write_text(
            "<script setup>\nconst groupOnly = 1\n</script>\n", encoding="utf-8",
        )
        (seedwt / "bad.vue").write_text(
            "<script setup>\nconst mainlineOnly = 1\n</script>\n", encoding="utf-8",
        )
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline adds bad.vue independently"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        # keeps both sides' added lines verbatim (the side-drop guard, unrelated
        # to this check) while the ordinary <script> block is unclosed — invalid
        # JS the real parser must catch even though <script setup> is fine.
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.vue",
            "content": (
                "<script setup>\nconst groupOnly = 1\nconst mainlineOnly = 1\n"
                "</script>\n<script>\nexport default {\n</script>\n"
            ),
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"
        fingerprint = out["result"]["review_fingerprint"]

        rejected = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert rejected["result"]["status"] == "pre_commit_validation_failed"
        errors = rejected["result"]["errors"]
        assert any(e["path"] == "bad.vue" and e["validator"] == "vue" for e in errors)

        out2 = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.vue",
            "content": (
                "<script setup>\nconst groupOnly = 1\nconst mainlineOnly = 1\n"
                "</script>\n<script>\nexport default { inheritAttrs: false }\n</script>\n"
            ),
        }], True)
        assert out2["result"]["status"] == "resolved_pending_review"
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=out2["result"]["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_review_gate_explicit_apply_applies_anchored_write_plan(self, origin_repo, monkeypatch):
        # 0481 TR0009 rev2 (AI review finding 1): T0008 item 1 and its completion
        # criteria require a human-requested apply to consume an anchored
        # edit/create-file plan, validate it in isolation, atomically apply it,
        # and produce a NEW frozen candidate/fingerprint — not a 409 refusal.
        # This exercises the whole path: [수정 적용] starts a run, the worker
        # submits a plan through the SAME endpoint a real resolve_conflict
        # worker token would call (svc.submit_review_write_plan — its run has no
        # write tool at all), and once the run is observed finished the plan is
        # applied and a NEW review_fingerprint/re_review candidate appears that
        # approves and commits like any other.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0129"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0129")
        (wt / "shared.py").write_text('"apply group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"apply mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "apply mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"apply group version"\n"apply mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_fake_apply",
        )
        assert sent["result"]["status"] == "accepted"
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["pending_conversation_write_requested"] is True
        assert context["pending_conversation_run_id"] == "aiv_fake_apply"

        # the worker reads the candidate's manifest for shared.py's current blob
        # (the anchor/replacement bytes are sliced off the ACTUAL current text —
        # this checkout's git may store LF or CRLF line endings, and an anchor
        # must match the real bytes, never an assumed terminator), then submits
        # an anchored edit — the ONLY channel by which this run can change
        # anything.
        manifest_entry = next(e for e in context["snapshot_manifest"] if e["path"] == "shared.py")
        actual = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]
        split_at = actual.index('"apply mainline')
        anchor_text = actual[:split_at]
        replacement_text = anchor_text.replace("apply group version", "apply group version (fixed)")
        expected_new_content = replacement_text + actual[split_at:]
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1",
                "kind": "edit",
                "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(anchor_text.encode("utf-8")).decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(
                    replacement_text.encode("utf-8")
                ).decode("ascii"),
                "purpose": "fix the group side wording",
            }],
            "held_test_operations": [],
        }
        # 0009-TR rev3 (AI review finding 1): the submission must carry the SAME
        # ai_run_id the worker token would have been issued for this run — a
        # wrong/stale run's token must not be able to attach a plan here (see
        # test_review_gate_write_plan_submission_rejects_a_mismatched_run_id).
        submitted = svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_fake_apply")
        assert submitted["result"]["status"] == "accepted"

        # submitting a SECOND plan for the same run overwrites the first — only
        # the run's own last submission is what the run's own finish attaches.
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": plan,
            },
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "re_review"
        new_fingerprint = review["review_fingerprint"]
        assert new_fingerprint != fingerprint
        roles = [(t["role"], t["status"]) for t in review["conversation"]]
        assert ("ai", "accepted") in roles
        # the pending-turn bookkeeping is cleared, and a NEW message can start
        # another turn afterward.
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("pending_conversation_run_id") is None
        assert context.get("pending_conversation_write_requested") is None

        new_side = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]
        assert new_side["content"] == expected_new_content

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=new_fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_review_gate_write_plan_create_file_operation(self, origin_repo, monkeypatch):
        # 0481 TR0009 rev2: `create_file` adds a path the frozen candidate did
        # not have, using the same isolated-build/atomic-apply engine as `edit`.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0131"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0131")
        (wt / "shared.py").write_text('"create group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"create mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "create mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"create group version"\n"create mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        svc.send_review_message(
            group, merge_id, message="새 파일도 하나 추가해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_fake_create",
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1",
                "kind": "create_file",
                "path": "notes_new.py",
                "absent": True,
                "content_bytes_base64": base64.b64encode(b'"new note"\n').decode("ascii"),
                "mode": "100644",
                "purpose": "add a note the human asked for",
            }],
            "held_test_operations": [],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_fake_create")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "re_review"
        assert any(c["path"] == "notes_new.py" for c in review["changes"])
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=review["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"

    def test_review_gate_write_plan_stale_fingerprint_is_rejected(self, origin_repo, monkeypatch):
        # 0481 TR0009 rev2: apply_write_plan re-checks `base_fingerprint` against
        # the CURRENT review_fingerprint at apply time (L0007 §2.6 `require
        # plan.base_fingerprint == session.review_fingerprint`) — a plan built
        # against a fingerprint that is no longer current must not silently
        # apply, and must leave the session exactly where it was.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0132"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0132")
        (wt / "shared.py").write_text('"stale group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"stale mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "stale mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"stale group version"\n"stale mainline version"\n',
        }], True)

        svc.send_review_message(
            group, merge_id, message="고쳐줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_fake_stale",
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": "0" * 64,  # never the real fingerprint
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "unrelated.py",
                "absent": True, "content_bytes_base64": "eA==", "mode": "100644",
                "purpose": "x",
            }],
            "held_test_operations": [],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_fake_stale")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        context_before = db_git.session_context(db_git.get_session(merge_id))
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == context_before["review_fingerprint"]
        roles = [(t["role"], t["status"]) for t in review["conversation"]]
        assert ("ai", "failed") in roles
        assert not any(c["path"] == "unrelated.py" for c in review["changes"])

        svc.abort_merge(group, merge_id)

    def test_review_gate_write_plan_anchor_mismatch_is_rejected(self, origin_repo, monkeypatch):
        # 0481 TR0009 rev2: an anchor that does not occur exactly
        # `expected_count` times in the CURRENT file must fail apply — this is
        # the whole point of anchoring instead of trusting a line number or
        # trusting the model's own claim about the file's content.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0133"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0133")
        (wt / "shared.py").write_text('"anchor group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"anchor mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "anchor mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"anchor group version"\n"anchor mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]
        context = db_git.session_context(db_git.get_session(merge_id))
        manifest_entry = next(e for e in context["snapshot_manifest"] if e["path"] == "shared.py")
        before_content = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]

        import base64

        svc.send_review_message(
            group, merge_id, message="고쳐줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_fake_anchor",
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1",
                "kind": "edit",
                "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(b"this text does not appear in the file").decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(b"replacement").decode("ascii"),
                "purpose": "x",
            }],
            "held_test_operations": [],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_fake_anchor")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint
        roles = [(t["role"], t["message"], t["status"]) for t in review["conversation"]]
        assert any(role == "ai" and status == "failed" for role, _msg, status in roles)

        # the real worktree file is untouched — validation failed before the one
        # step that would have touched it.
        new_side = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]
        assert new_side["content"] == before_content

        svc.abort_merge(group, merge_id)

    def test_review_gate_write_plan_submission_requires_a_pending_write_turn(self, origin_repo):
        # 0481 TR0009 rev2: the worker-token submission endpoint's server-side
        # entry point (svc.submit_review_write_plan) must refuse a plan when no
        # write turn is waiting for one — a propose-only conversation run (or no
        # run at all) has no write intent recorded on the session.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0134"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0134")
        (wt / "shared.py").write_text('"noreq group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"noreq mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "noreq mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"noreq group version"\n"noreq mainline version"\n',
        }], True)

        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": "0" * 64,
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "unrelated.py",
                "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
            }],
        }
        with pytest.raises(svc.GitServiceError) as exc:
            svc.submit_review_write_plan(group, merge_id, plan=plan)
        assert exc.value.status == 409
        assert exc.value.code == "write_plan_not_requested"

        # a propose-only conversation turn ALSO does not open the write channel.
        svc.send_review_message(
            group, merge_id, message="그냥 질문",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_fake_question",
        )
        with pytest.raises(svc.GitServiceError) as exc2:
            svc.submit_review_write_plan(group, merge_id, plan=plan)
        assert exc2.value.code == "write_plan_not_requested"

        svc.abort_merge(group, merge_id)

    def test_review_gate_write_plan_structure_validation(self, origin_repo):
        # 0481 TR0009 rev2: structural validation (L0007 §2.5, the Q&A-bound
        # schema) rejects a malformed plan and gates test-path operations behind
        # allow_test_edits, independent of any live tree state.
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc._validate_write_plan_structure({"schema_version": "wrong"}, allow_test_edits=False)
        assert exc.value.code == "invalid_write_plan"

        base_plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": "0" * 64,
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "server/tests/new_test.py",
                "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
            }],
        }
        with pytest.raises(svc.GitServiceError) as exc2:
            svc._validate_write_plan_structure(base_plan, allow_test_edits=False)
        assert exc2.value.code == "invalid_write_plan"

        # the SAME test-path operation in held_test_operations (with a benign
        # non-test operation actually in operations[], which must stay
        # non-empty) passes without allow_test_edits; so does
        # allow_test_edits=True with the test-path operation in operations[].
        benign_op = {
            "operation_id": "op2", "kind": "create_file", "path": "server/app.py",
            "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
        }
        svc._validate_write_plan_structure(
            {**base_plan, "operations": [benign_op], "held_test_operations": base_plan["operations"]},
            allow_test_edits=False,
        )
        svc._validate_write_plan_structure(base_plan, allow_test_edits=True)

        # 0009-TR rev3 (AI review finding 2): a plan whose AI proposed ONLY
        # test-path edits is legitimate — operations[] is empty and everything
        # lands in held_test_operations[]. That must pass structural validation
        # (L0007 §2.7 requires the held proposals be SHOWN, not rejected), while
        # a plan with BOTH arrays empty is still meaningless and rejected.
        svc._validate_write_plan_structure(
            {**base_plan, "operations": [], "held_test_operations": base_plan["operations"]},
            allow_test_edits=False,
        )
        with pytest.raises(svc.GitServiceError) as exc3:
            svc._validate_write_plan_structure(
                {**base_plan, "operations": [], "held_test_operations": []}, allow_test_edits=False,
            )
        assert exc3.value.code == "invalid_write_plan"

    def test_review_gate_write_plan_submission_rejects_a_mismatched_run_id(self, origin_repo):
        # 0009-TR rev3 (AI review finding 1): action_scope/group_id/merge_id
        # alone bind a resolve_conflict token to the MERGE, not to the specific
        # pending write TURN — a still-valid token for a stale/different run
        # must not be able to attach a plan to the CURRENT pending write turn.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0135"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0135")
        (wt / "shared.py").write_text('"mismatch group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"mismatch mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "mismatch mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"mismatch group version"\n"mismatch mainline version"\n',
        }], True)
        svc.send_review_message(
            group, merge_id, message="고쳐줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_real_run",
        )
        context_before = db_git.session_context(db_git.get_session(merge_id))
        assert context_before["pending_conversation_run_id"] == "aiv_real_run"

        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": context_before["review_fingerprint"],
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "unrelated.py",
                "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
            }],
            "held_test_operations": [],
        }
        # a stale/different run's own token (its ai_run_id does not match the
        # session's CURRENT pending write turn) is rejected before the plan is
        # ever recorded.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_stale_run")
        assert exc.value.status == 403
        assert exc.value.code == "write_plan_run_mismatch"

        # a token with NO ai_run_id claim at all (should not happen for a real
        # resolve_conflict token, but must not be treated as a wildcard match)
        # is rejected the same way.
        with pytest.raises(svc.GitServiceError) as exc2:
            svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id=None)
        assert exc2.value.code == "write_plan_run_mismatch"

        # the session's pending write turn is untouched by either rejected attempt.
        context_after = db_git.session_context(db_git.get_session(merge_id))
        assert context_after["pending_conversation_run_id"] == "aiv_real_run"

        svc.abort_merge(group, merge_id)

    def test_review_gate_write_plan_submission_overwrites_the_runs_own_prior_plan(self, origin_repo, monkeypatch):
        # 0009-TR rev3: the run's OWN correctly-bound token may resubmit — only
        # the run's last submission is what its finish attaches (this is the
        # run replacing its own plan, not a different run's token overwriting
        # someone else's — that case is the mismatch test above).
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0136"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0136")
        (wt / "shared.py").write_text('"overwrite group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"overwrite mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "overwrite mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"overwrite group version"\n"overwrite mainline version"\n',
        }], True)
        fingerprint = db_git.session_context(db_git.get_session(merge_id))["review_fingerprint"]
        svc.send_review_message(
            group, merge_id, message="고쳐줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_overwrite_run",
        )

        def _plan_for(path: str) -> dict:
            return {
                "schema_version": "flowgate.write-plan.v1",
                "base_fingerprint": fingerprint,
                "operations": [{
                    "operation_id": "op1", "kind": "create_file", "path": path,
                    "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
                }],
                "held_test_operations": [],
            }

        plan_a = _plan_for("overwrite_a.py")
        plan_b = _plan_for("overwrite_b.py")
        svc.submit_review_write_plan(group, merge_id, plan=plan_a, ai_run_id="aiv_overwrite_run")
        svc.submit_review_write_plan(group, merge_id, plan=plan_b, ai_run_id="aiv_overwrite_run")
        # the run's finish carries whatever `write_plan` is now stored against
        # it — the SAME field both submissions wrote to — so this mirrors what
        # a real second submission from the same run's token overwrites.
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan_b},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "re_review"
        changed_paths = {c["path"] for c in review["changes"]}
        assert "overwrite_b.py" in changed_paths
        assert "overwrite_a.py" not in changed_paths

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=review["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"

    def test_review_gate_write_plan_held_only_plan_is_shown_not_applied(self, origin_repo, monkeypatch):
        # 0009-TR rev3 (AI review finding 2): a plan whose AI proposed ONLY
        # test-path edits must be SHOWN (not silently rejected, not silently
        # discarded) and must not touch review_state/review_fingerprint since
        # nothing was actually applied.
        import base64

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0137"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0137")
        (wt / "shared.py").write_text('"heldonly group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"heldonly mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "heldonly mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"heldonly group version"\n"heldonly mainline version"\n',
        }], True)
        fingerprint = db_git.session_context(db_git.get_session(merge_id))["review_fingerprint"]
        svc.send_review_message(
            group, merge_id, message="테스트도 고쳐줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_held_only",
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [],
            "held_test_operations": [{
                "operation_id": "held1", "kind": "create_file", "path": "server/tests/held_case.py",
                "absent": True,
                "content_bytes_base64": base64.b64encode(b'"held test"\n').decode("ascii"),
                "mode": "100644", "purpose": "add a regression case for the fix",
            }],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_held_only")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        # nothing was applied — the pending review is exactly where it was.
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint
        assert not any(c["path"] == "server/tests/held_case.py" for c in review["changes"])
        # the held proposal is SHOWN, not discarded.
        held = review["held_test_operations"]
        assert len(held) == 1
        assert held[0]["path"] == "server/tests/held_case.py"
        assert held[0]["purpose"] == "add a regression case for the fix"
        roles = [(t["role"], t["status"], t["message"]) for t in review["conversation"]]
        assert any(
            role == "ai" and status == "accepted" and "held_case.py" in message
            for role, status, message in roles
        )

        svc.abort_merge(group, merge_id)

    def test_review_gate_write_plan_mixed_plan_preserves_held_operations(self, origin_repo, monkeypatch):
        # 0009-TR rev3 (AI review finding 2): a MIXED plan (some operations
        # applied for real, some test-path proposals held) must not silently
        # drop the held half when the applied half succeeds.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0138"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0138")
        (wt / "shared.py").write_text('"mixed group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"mixed mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "mixed mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"mixed group version"\n"mixed mainline version"\n',
        }], True)
        fingerprint = db_git.session_context(db_git.get_session(merge_id))["review_fingerprint"]
        svc.send_review_message(
            group, merge_id, message="이건 고치고 테스트도 하나 추가해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_mixed_run",
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "mixed_ok.py",
                "absent": True,
                "content_bytes_base64": base64.b64encode(b'"applied"\n').decode("ascii"),
                "mode": "100644", "purpose": "product code the human asked for",
            }],
            "held_test_operations": [{
                "operation_id": "held1", "kind": "create_file", "path": "server/tests/mixed_held.py",
                "absent": True,
                "content_bytes_base64": base64.b64encode(b'"held"\n').decode("ascii"),
                "mode": "100644", "purpose": "regression case, held for a second authorization",
            }],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_mixed_run")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "re_review"
        assert any(c["path"] == "mixed_ok.py" for c in review["changes"])
        assert not any(c["path"] == "server/tests/mixed_held.py" for c in review["changes"])
        held = review["held_test_operations"]
        assert len(held) == 1
        assert held[0]["path"] == "server/tests/mixed_held.py"

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=review["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"

    def test_review_gate_write_plan_allow_test_edits_second_authorization_applies_test_edit(self, origin_repo, monkeypatch):
        # 0009-TR rev3 (AI review finding 2): the human's second explicit action
        # (L0007 §2.7's [테스트 편집 포함 재지시]) starts a run with
        # allow_test_edits=true, and ONLY such a run may put a test-path
        # operation directly in operations[] — where it goes through the exact
        # same anchored-apply/rollback engine as any product-code edit.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0139"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0139")
        (wt / "shared.py").write_text('"testedit group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"testedit mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "testedit mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"testedit group version"\n"testedit mainline version"\n',
        }], True)
        fingerprint = db_git.session_context(db_git.get_session(merge_id))["review_fingerprint"]
        sent = svc.send_review_message(
            group, merge_id, message="이번엔 테스트 편집도 포함해서 다시 해줘",
            provider_id="prov_test", provider_pinned=True,
            apply_requested=True, allow_test_edits=True,
            start_run=lambda: "aiv_test_edit_run",
        )
        assert sent["result"]["status"] == "accepted"
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["pending_conversation_allow_test_edits"] is True

        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "server/tests/allowed_case.py",
                "absent": True,
                "content_bytes_base64": base64.b64encode(b'"now allowed"\n').decode("ascii"),
                "mode": "100644", "purpose": "regression case for the second-authorization path",
            }],
            "held_test_operations": [],
        }
        svc.submit_review_write_plan(group, merge_id, plan=plan, ai_run_id="aiv_test_edit_run")
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {"status": "finished", "succeeded": True, "write_plan": plan},
        )
        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "re_review"
        assert any(c["path"] == "server/tests/allowed_case.py" for c in review["changes"])
        assert not review["held_test_operations"]

        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=review["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"

    def test_review_gate_write_plan_stale_run_discarded_when_approved_while_active(
        self, origin_repo, monkeypatch,
    ):
        # 0481 TR0009 rev4 (AI review finding): L0007 §2.9 requires a
        # re-instruction run's result to be discarded as `stale_run` when the
        # review it was launched against is no longer the one on screen. A human
        # can approve the CURRENTLY pending snapshot while a conversation run
        # (started against that same snapshot) is still in flight — approval
        # does not change review_fingerprint/instruction_generation at all, only
        # review_state, so a guard that only compared fingerprint/generation
        # would miss this race entirely and let the run's late write plan try to
        # apply against an already-merged session.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0140"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0140")
        (wt / "shared.py").write_text('"stale approve group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"stale approve mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "stale approve mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"stale approve group version"\n"stale approve mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_stale_approve_race",
        )
        assert sent["result"]["status"] == "accepted"

        # the human approves the CURRENT (still pending) snapshot while that run
        # is still active — approval never checks pending_conversation_run_id.
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"
        merged_head = _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip()

        # only now does the run finish, with a write plan that (if applied)
        # would edit shared.py again on top of the already-merged commit.
        manifest_entry = next(
            e for e in db_git.session_context(db_git.get_session(merge_id))["snapshot_manifest"]
            if e["path"] == "shared.py"
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1", "kind": "edit", "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(b'"stale approve group version"\n').decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(b'"this must never land"\n').decode("ascii"),
                "purpose": "must be discarded as stale_run, not applied",
            }],
            "held_test_operations": [],
        }
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": plan,
            },
        )

        review = svc.get_merge_review(group, merge_id)["result"]
        assert review["review_state"] == "completed"
        assert review["review_fingerprint"] == fingerprint  # untouched by the discarded run
        roles = [(t["role"], t["status"]) for t in review["conversation"]]
        assert ("ai", "stale_run") in roles
        assert not any(t["role"] == "ai" and t["status"] == "accepted" for t in review["conversation"])
        # 0481 T0010 rev6 (rejection 3): the PLAN is still discarded, but the ANSWER is
        # kept. Before this the operator got only "the approval target changed, instruct
        # again" -- twice in the rejected transcript -- and never saw what the run said.
        stale = next(t for t in review["conversation"] if t["status"] == "stale_run")
        assert "수정했습니다." in stale["message"]
        assert "승인 대기가 끝났습니다" in stale["message"]
        assert "수정안은 적용하지 않았습니다" in stale["message"]

        # the plan never touched the tree: mainline still has exactly the commit
        # approval created, and no new candidate/generation was minted.
        assert _git(["rev-parse", "main"], cwd=origin_repo["bare"]).strip() == merged_head
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("pending_conversation_run_id") is None
        assert int(context.get("instruction_generation") or 0) == 0

    def test_review_gate_conversation_stale_run_discarded_when_rejected_while_active(
        self, origin_repo, monkeypatch,
    ):
        # 0481 TR0009 rev4 (AI review finding): a human can [반려] (reject) the
        # candidate a conversation run is still answering — reject_and_return_
        # to_resolver never checks pending_conversation_run_id. Once such a run
        # later finishes, its answer must be discarded as `stale_run`, never
        # folded in as if it still answered a candidate the session has already
        # abandoned (review_state moved off resolved_pending_review/re_review).
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0141"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0141")
        (wt / "shared.py").write_text('"stale reject group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"stale reject mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "stale reject mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"stale reject group version"\n"stale reject mainline version"\n',
        }], True)

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 왜 이렇게 했어?",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_stale_reject_race",
        )
        assert sent["result"]["status"] == "accepted"

        rejected = svc.reject_merge_review(
            group, merge_id, reason="다시 확인해 주세요 — 이 사유는 충분히 깁니다.",
            provider_id="prov_test", provider_pinned=True,
            start_run=lambda first_message: "aiv_new_resolver_run",
        )
        assert rejected["result"]["status"] == "returned_to_resolver"

        # only now does the OLD (pre-reject) run finish.
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "이 답은 이미 버려진 후보에 대한 것입니다.",
                "provider_id": "prov_test",
            },
        )
        # the session is back to `open` (review_state is None), so the review
        # screen itself is correctly unavailable — but materialization still
        # runs as a side effect of the lookup and must discard the stale answer.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.get_merge_review(group, merge_id)
        assert exc.value.code == "review_not_ready"

        context = db_git.session_context(db_git.get_session(merge_id))
        roles = [(t["role"], t["status"]) for t in context["conversation"]]
        assert ("ai", "stale_run") in roles
        assert not any(t["role"] == "ai" and t["status"] == "accepted" for t in context["conversation"])
        # rev6: kept, under a line that names what moved (here: the reject ended the wait
        # and refroze nothing, so the candidate identity is gone too).
        stale = next(t for t in context["conversation"] if t["status"] == "stale_run")
        assert "이 답은 이미 버려진 후보에 대한 것입니다." in stale["message"]
        assert "stale_run" in stale["message"]
        assert context.get("pending_conversation_run_id") is None

        svc.abort_merge(group, merge_id)

    def test_review_gate_approval_racing_the_materialize_window_is_serialized(
        self, origin_repo, monkeypatch,
    ):
        # 0481 TR0009 rev5 (AI review finding): the stale_run decision must be
        # ATOMIC with what it authorizes. rev4 read review_state/fingerprint/
        # generation with NO lock held and only afterwards applied the plan and
        # appended the turn, so an approval landing in that check-to-materialize
        # window produced exactly what L0007 §2.9 forbids — a write run's result
        # applied/reported against a target nobody re-checked (and reported as a
        # generic `apply_failed`, not `stale_run`). This test drives an approval
        # INTO that window: the racing call fires from inside materialization,
        # at the very moment the pending run is claimed, and must be serialized
        # by the project git lock instead of interleaving.
        import base64
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0142"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0142")
        (wt / "shared.py").write_text('"race apply group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race apply mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race apply mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race apply group version"\n"race apply mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_materialize_race",
        )
        assert sent["result"]["status"] == "accepted"

        actual = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]
        split_at = actual.index('"race apply mainline')
        anchor_text = actual[:split_at]
        replacement_text = anchor_text.replace("race apply group version", "race apply group version (fixed)")
        manifest_entry = next(
            e for e in db_git.session_context(db_git.get_session(merge_id))["snapshot_manifest"]
            if e["path"] == "shared.py"
        )
        plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": fingerprint,
            "operations": [{
                "operation_id": "op1", "kind": "edit", "path": "shared.py",
                "expected_before_blob": manifest_entry["oid"],
                "anchor": {
                    "body_base64": base64.b64encode(anchor_text.encode("utf-8")).decode("ascii"),
                    "expected_count": 1,
                },
                "replacement_bytes_base64": base64.b64encode(
                    replacement_text.encode("utf-8")
                ).decode("ascii"),
                "purpose": "must apply exactly once, against the checked target",
            }],
            "held_test_operations": [],
        }
        assert svc.submit_review_write_plan(
            group, merge_id, plan=plan, ai_run_id="aiv_materialize_race",
        )["result"]["status"] == "accepted"
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": plan,
            },
        )

        # the racer: an approval of the CURRENT (checked, still pending) snapshot
        # fired from inside the check-to-materialize window — the first session
        # write materialization makes is its claim on the pending run.
        monkeypatch.setattr(svc, "LOCK_WAIT_SEC", 0)  # the racer must not block the test
        raced: list = []
        real_set_session_context = db_git.set_session_context

        def racing_set_session_context(mid, ctx):
            real_set_session_context(mid, ctx)
            if raced:
                return
            raced.append("fired")
            try:
                svc.approve_merge_review(
                    group, merge_id, attempt_id=str(uuid.uuid4()),
                    review_fingerprint=fingerprint, authority="human",
                )
                raced.append("interleaved")
            except svc.GitServiceError as exc:
                raced.append(exc.code)

        monkeypatch.setattr(db_git, "set_session_context", racing_set_session_context)

        review = svc.get_merge_review(group, merge_id)["result"]
        # the approval could not interleave: it was refused the project lock the
        # materialization was holding across check + claim + apply + append.
        assert raced == ["fired", "git_busy"]
        # and the run's plan was applied exactly once, to the target the stale
        # check actually looked at.
        assert review["review_state"] == "re_review"
        new_fingerprint = review["review_fingerprint"]
        assert new_fingerprint != fingerprint
        statuses = [(t["role"], t["status"]) for t in review["conversation"]]
        assert statuses.count(("ai", "accepted")) == 1
        assert ("ai", "stale_run") not in statuses
        assert svc.read_merge_review_file_diff(
            group, merge_id, "shared.py",
        )["data"]["new"]["content"].startswith('"race apply group version (fixed)"')

        # the human's approval of the target they had on screen is now correctly
        # refused (it is no longer the candidate), and the new one merges.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.approve_merge_review(
                group, merge_id, attempt_id=str(uuid.uuid4()),
                review_fingerprint=fingerprint, authority="human",
            )
        assert exc.value.code == "stale_review"
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=new_fingerprint, authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"

    def test_review_gate_rejection_racing_the_materialize_window_is_serialized(
        self, origin_repo, monkeypatch,
    ):
        # 0481 TR0009 rev5 (AI review finding), propose-only half: the same
        # window let a plain answer be appended as `accepted` to a session a
        # rejection had already returned to the resolver. A [반려] fired from
        # inside the window must be serialized too — the answer lands on the
        # candidate the check saw, and the rejection is refused the lock.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0143"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0143")
        (wt / "shared.py").write_text('"race ask group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race ask mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race ask mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race ask group version"\n"race ask mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 왜 이렇게 했어?",
            provider_id="prov_test", provider_pinned=True, apply_requested=False,
            start_run=lambda: "aiv_materialize_race_ask",
        )
        assert sent["result"]["status"] == "accepted"
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "이렇게 해결했습니다.", "provider_id": "prov_test",
            },
        )

        monkeypatch.setattr(svc, "LOCK_WAIT_SEC", 0)
        raced: list = []
        real_set_session_context = db_git.set_session_context

        def racing_set_session_context(mid, ctx):
            real_set_session_context(mid, ctx)
            if raced:
                return
            raced.append("fired")
            try:
                svc.reject_merge_review(
                    group, merge_id, reason="이 답이 오기 전에 반려합니다 — 사유는 충분히 깁니다.",
                    provider_id="prov_test", provider_pinned=True,
                    start_run=lambda first_message: "aiv_race_resolver_run",
                )
                raced.append("interleaved")
            except svc.GitServiceError as exc:
                raced.append(exc.code)

        monkeypatch.setattr(db_git, "set_session_context", racing_set_session_context)

        review = svc.get_merge_review(group, merge_id)["result"]
        assert raced == ["fired", "git_busy"]
        assert review["review_state"] == "resolved_pending_review"
        assert review["review_fingerprint"] == fingerprint
        statuses = [(t["role"], t["status"]) for t in review["conversation"]]
        assert statuses.count(("ai", "accepted")) == 1
        assert ("ai", "stale_run") not in statuses
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context.get("pending_conversation_run_id") is None

        # the rejection the racer could not interleave still works afterwards.
        rejected = svc.reject_merge_review(
            group, merge_id, reason="이제 반려합니다 — 이 사유는 충분히 깁니다.",
            provider_id="prov_test", provider_pinned=True,
            start_run=lambda first_message: "aiv_race_resolver_run",
        )
        assert rejected["result"]["status"] == "returned_to_resolver"
        svc.abort_merge(group, merge_id)

    def test_review_gate_message_does_not_overwrite_an_approval_that_won_the_race(
        self, origin_repo,
    ):
        # 0481 TR0009 rev5 (AI review finding, same class): `send_review_message`
        # validated the session, then started the AI run — a slow call — and only
        # then wrote the pending bookkeeping back from the context it had read
        # BEFORE the run started. An approval completing in that window was
        # silently overwritten (review_state/merge_commit reverted to a
        # pre-approval copy). The turn must now be refused instead, and the
        # approval left exactly as it landed.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0144"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0144")
        (wt / "shared.py").write_text('"race send group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race send mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race send mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race send group version"\n"race send mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        message = "실행이 시작되는 사이에 승인이 끝나는 경우"

        def start_run_while_the_human_approves():
            approved = svc.approve_merge_review(
                group, merge_id, attempt_id=str(uuid.uuid4()),
                review_fingerprint=fingerprint, authority="human",
            )
            assert approved["result"]["status"] == "merged"
            return "aiv_orphaned_by_approval"

        with pytest.raises(svc.GitServiceError) as exc:
            svc.send_review_message(
                group, merge_id, message=message,
                provider_id="prov_test", provider_pinned=True, apply_requested=True,
                start_run=start_run_while_the_human_approves,
            )
        assert exc.value.code == "review_not_ready"

        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["review_state"] == "completed"
        assert context.get("merge_commit")
        assert db_git.get_session(merge_id)["status"] == "done"
        assert context.get("pending_conversation_run_id") is None
        assert context.get("pending_conversation_write_requested") is None
        assert not any(t["message"] == message for t in context.get("conversation") or [])

    def test_review_gate_reject_does_not_undo_an_approval_that_won_the_race(
        self, origin_repo, monkeypatch,
    ):
        # 0481 TR0009 rev5 (AI review finding, same class): `reject_merge_review`
        # checked review_state BEFORE taking the project lock and then restored
        # the conflict from that pre-lock context — so an approval that committed
        # while the rejection was waiting for the lock was both undone in the
        # checkout and overwritten in the session. The rejection must now lose
        # the race cleanly. The racer fires from inside `_acquire_lock`, i.e.
        # exactly in the window between the pre-check and the lock being held.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0145"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0145")
        (wt / "shared.py").write_text('"race reject group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"race reject mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "race reject mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"race reject group version"\n"race reject mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        raced: list = []
        real_acquire_lock = svc._acquire_lock

        def racing_acquire_lock(project_id, holder, wait_sec=None):
            if not raced:
                raced.append(holder)
                approved = svc.approve_merge_review(
                    group, merge_id, attempt_id=str(uuid.uuid4()),
                    review_fingerprint=fingerprint, authority="human",
                )
                assert approved["result"]["status"] == "merged"
            if wait_sec is None:
                return real_acquire_lock(project_id, holder)
            return real_acquire_lock(project_id, holder, wait_sec=wait_sec)

        monkeypatch.setattr(svc, "_acquire_lock", racing_acquire_lock)

        with pytest.raises(svc.GitServiceError) as exc:
            svc.reject_merge_review(
                group, merge_id, reason="승인과 경쟁하는 반려 — 이 사유는 충분히 깁니다.",
                provider_id="prov_test", provider_pinned=True,
                start_run=lambda first_message: "aiv_race_reject_resolver",
            )
        assert exc.value.code == "review_not_ready"

        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["review_state"] == "completed"
        assert context.get("merge_commit")
        assert db_git.get_session(merge_id)["status"] == "done"
        # the approved candidate is intact: the rejection did not re-run the
        # merge and did not put conflict markers back.
        assert not any(
            t["status"] == "rejected" for t in context.get("conversation") or []
        )

    def test_review_gate_write_plan_resubmitted_in_the_materialize_window_wins(
        self, origin_repo, monkeypatch,
    ):
        # 0481 TR0009 rev5 (AI review finding, the plan half): the stale check is
        # not the only input that has to be atomic with what it authorizes — so is
        # the plan being applied. `_materialize_pending_conversation_run` reads the
        # run detail BEFORE taking the project lock, and `submit_review_write_plan`
        # (worker token) takes no git lock at all, so a second submission landing in
        # that window was silently dropped: the worker was told `accepted` and the
        # PREVIOUS plan got applied. The run's last submission must win — the same
        # contract `..._submission_overwrites_the_runs_own_prior_plan` states for the
        # sequential case — so the run detail is re-read under the lock too.
        import base64

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0146"
        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0146")
        (wt / "shared.py").write_text('"resubmit group version"\n', encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / "shared.py").write_text('"resubmit mainline version"\n', encoding="utf-8")
        _git(["commit", "-am", "resubmit mainline change"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        merge_id = out["result"]["merge_id"]
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "shared.py",
            "content": '"resubmit group version"\n"resubmit mainline version"\n',
        }], True)
        fingerprint = out["result"]["review_fingerprint"]

        sent = svc.send_review_message(
            group, merge_id, message="이 부분 고쳐서 적용해줘",
            provider_id="prov_test", provider_pinned=True, apply_requested=True,
            start_run=lambda: "aiv_resubmit_race",
        )
        assert sent["result"]["status"] == "accepted"

        actual = svc.read_merge_review_file_diff(group, merge_id, "shared.py")["data"]["new"]["content"]
        anchor_text = actual[:actual.index('"resubmit mainline')]
        manifest_entry = next(
            e for e in db_git.session_context(db_git.get_session(merge_id))["snapshot_manifest"]
            if e["path"] == "shared.py"
        )

        def _plan(tag):
            replacement = anchor_text.replace(
                "resubmit group version", "resubmit group version ({})".format(tag),
            )
            return {
                "schema_version": "flowgate.write-plan.v1",
                "base_fingerprint": fingerprint,
                "operations": [{
                    "operation_id": "op-{}".format(tag), "kind": "edit", "path": "shared.py",
                    "expected_before_blob": manifest_entry["oid"],
                    "anchor": {
                        "body_base64": base64.b64encode(anchor_text.encode("utf-8")).decode("ascii"),
                        "expected_count": 1,
                    },
                    "replacement_bytes_base64": base64.b64encode(
                        replacement.encode("utf-8")
                    ).decode("ascii"),
                    "purpose": "plan {}".format(tag),
                }],
                "held_test_operations": [],
            }

        plan_first, plan_second = _plan("first"), _plan("second")

        # stands in for the runs table: submit WRITES the plan, materialize READS it.
        recorded: dict = {}
        monkeypatch.setattr(
            ai_invoke_service, "record_run_write_plan",
            lambda run_id, plan: recorded.__setitem__(run_id, plan),
        )
        monkeypatch.setattr(
            ai_diagnostics, "get_run_detail",
            lambda run_id: {
                "status": "finished", "succeeded": True,
                "last_message": "수정했습니다.", "provider_id": "prov_test",
                "write_plan": recorded.get(run_id),
            },
        )
        assert svc.submit_review_write_plan(
            group, merge_id, plan=plan_first, ai_run_id="aiv_resubmit_race",
        )["result"]["status"] == "accepted"

        # the racer: the worker's SECOND submission lands after materialization has
        # already read the run detail, but before it takes the lock and claims the run.
        raced: list = []
        real_acquire_lock = svc._acquire_lock

        def racing_acquire_lock(project_id, holder, wait_sec=None):
            if not raced:
                raced.append(holder)
                assert svc.submit_review_write_plan(
                    group, merge_id, plan=plan_second, ai_run_id="aiv_resubmit_race",
                )["result"]["status"] == "accepted"
            if wait_sec is None:
                return real_acquire_lock(project_id, holder)
            return real_acquire_lock(project_id, holder, wait_sec=wait_sec)

        monkeypatch.setattr(svc, "_acquire_lock", racing_acquire_lock)

        review = svc.get_merge_review(group, merge_id)["result"]
        assert raced, "the racing submission never fired"
        # the plan that was applied is the one submitted LAST, not the pre-lock copy.
        assert review["review_state"] == "re_review"
        assert review["review_fingerprint"] != fingerprint
        content = svc.read_merge_review_file_diff(
            group, merge_id, "shared.py",
        )["data"]["new"]["content"]
        assert content.startswith('"resubmit group version (second)"')
        assert "(first)" not in content
        statuses = [(t["role"], t["status"]) for t in review["conversation"]]
        assert statuses.count(("ai", "accepted")) == 1
        assert ("ai", "stale_run") not in statuses
        svc.abort_merge(group, merge_id)


# ── base-slot provisioning: lossless adopt + ledger (flowgate.default.0161) ──

@pytest.fixture(scope="class")
def adopt_origin(seed):
    """A dedicated project whose base slot is OCCUPIED before provisioning
    (the B0001 situation), plus a local bare origin."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "adoptprj", "project_name": "AdoptProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-adopt-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("remote readme\n", encoding="utf-8")
    (seedwt / "shared.txt").write_text("remote version\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    # occupy the slot BEFORE git integration exists
    base = src_root("AdoptProj", "main")
    base.mkdir(parents=True, exist_ok=True)
    (base / "crud.py").write_text("local only\n", encoding="utf-8")
    (base / "shared.txt").write_text("local version\n", encoding="utf-8")

    svc.save_config("adoptprj", {
        "repo_url": bare.as_uri(),
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp, "base": base}
    svc.delete_config("adoptprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestProvision0161:
    def test_judge_occupied_before(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        assert svc._judge_base_slot(adopt_origin["base"], "main") == "occupied"
        view = svc.provision_view("adoptprj")
        assert view["configured"] is True
        assert view["base_path_state"] == "occupied"
        assert view["base_checkout_exists"] is False
        assert view["last_attempt"] is None  # no attempts recorded yet

    def test_adopt_is_lossless_and_records(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        base = adopt_origin["base"]
        out = svc.provision_base("adoptprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "adopt"
        assert out["snapshot_commit"]  # local↔remote difference existed
        # lossless: pre-existing bytes are untouched (local content wins)
        assert (base / "crud.py").read_text(encoding="utf-8") == "local only\n"
        assert (base / "shared.txt").read_text(encoding="utf-8") == "local version\n"
        # remote-only file restored, NOT recorded as a deletion
        assert (base / "README.md").read_text(encoding="utf-8") == "remote readme\n"
        # completion: marker gone, branch ref established
        assert not (base / ".git" / "flowgate_adopt_pending").exists()
        assert svc._judge_base_slot(base, "main") == "checkout"
        # ledger + view
        view = svc.provision_view("adoptprj")
        assert view["base_checkout_exists"] is True
        assert view["last_attempt"]["result"] == "ok"
        assert view["last_attempt"]["trigger"] == "manual"
        assert view["adopt_snapshot"]["commit"] == out["snapshot_commit"]
        # the base checkout is clean → finalize's base_dirty guard passes
        assert svc._dirty(base) is False

    def test_reprovision_is_noop_without_record_update(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        before = svc.provision_view("adoptprj")["last_attempt"]
        out = svc.provision_base("adoptprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "none"
        assert svc.provision_view("adoptprj")["last_attempt"] == before

    def test_worktree_after_adopt_and_finalize_carries_snapshot(self, adopt_origin):
        """B0001 end-to-end: the hook path succeeds on an occupied slot, and the
        adopt snapshot rides along with the finalize push (D0003 §3)."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "adoptprj.default.0001"
        assert svc.ensure_worktree("adoptprj", "default", group) == "ok"
        wt = src_root("AdoptProj", "adoptprj_default_0001")
        # 0296 T0004: the worktree now forks from LOCAL main, which already
        # contains origin/main plus the adopt snapshot. This assertion used to
        # read "remote version" — the group saw stale remote content while the
        # operator's base held the adopted local content, the same invisible-file
        # class as B0001 (NR flowgate.default.0296.0003 §C1/§C2). It pinned the
        # then-current behaviour, not an intent: the snapshot is expected to
        # survive all the way to origin (asserted below), so a worktree that
        # ignores it was only ever a way to lose work.
        assert (wt / "shared.txt").read_text(encoding="utf-8") == "local version\n"
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "merged"
        # origin main now holds the group work AND the adopt snapshot
        files = _git(["ls-tree", "--name-only", "main"], cwd=adopt_origin["bare"]).split()
        assert "work.txt" in files
        assert "crud.py" in files
        # local-content-wins snapshot survived the merge
        assert _git(["show", "main:shared.txt"], cwd=adopt_origin["bare"]) == "local version\n"
        # once the snapshot reached the remote, the view stops advertising it
        assert svc.provision_view("adoptprj")["adopt_snapshot"] is None

    def test_manual_provision_response_shape(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        out = svc.provision_manual("adoptprj")
        assert out["ok"] is True
        assert out["result"]["status"] == "ok"
        assert out["result"]["mode"] == "none"
        assert out["result"]["provision"]["base_checkout_exists"] is True

    def test_manual_not_enabled_409(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.provision_manual("plainprj")
        assert exc.value.status == 409
        assert exc.value.code == "not_enabled"
        # status view for a non-integrated project is the fixed scenario-7 shape
        view = svc.provision_view("plainprj")
        assert view == {"configured": False, "enabled": False, "base_branch": None,
                        "base_path_state": "empty", "base_checkout_exists": False,
                        "adopt_snapshot": None, "last_attempt": None}

    def test_provision_view_unknown_project_404(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.provision_view("ghost")
        assert exc.value.status == 404

    def test_failed_attempt_recorded_then_reentry_completes(self, adopt_origin):
        """Interrupted adopt leaves debris + marker; a re-run resumes and
        finishes (L0005 §2.3 idempotent re-entry, P0004 scenario 5)."""
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        projects.create({"project_id": "retryprj", "project_name": "RetryProj"})
        base = src_root("RetryProj", "main")
        base.mkdir(parents=True, exist_ok=True)
        (base / "keep.txt").write_text("keep me\n", encoding="utf-8")
        # unreachable origin → fetch fails mid-adopt
        svc.save_config("retryprj", {
            "repo_url": (Path(adopt_origin["tmp"]) / "missing.git").as_uri(),
            "base_branch": "main",
            "enabled": True,
        })
        out = svc.provision_base("retryprj", "manual")
        assert out["status"] == "failed"
        assert out["mode"] == "adopt"
        # debris: .git + pending marker stay; the slot still reports occupied
        assert (base / ".git" / "flowgate_adopt_pending").exists()
        assert svc._judge_base_slot(base, "main") == "occupied"
        view = svc.provision_view("retryprj")
        assert view["base_checkout_exists"] is False
        assert view["last_attempt"]["result"] == "failed"
        # fix the config → the re-run resumes from the debris and completes
        svc.save_config("retryprj", {
            "repo_url": adopt_origin["bare"].as_uri(),
            "base_branch": "main",
            "enabled": True,
        })
        out = svc.provision_base("retryprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "adopt"
        assert (base / "keep.txt").read_text(encoding="utf-8") == "keep me\n"
        assert svc._judge_base_slot(base, "main") == "checkout"
        svc.delete_config("retryprj")


# ── flowgate.default.0162: group git actions (status / fetch / push / approve) ─

class TestStatus0162NoGit:
    """Aggregation + precheck paths that need no git binary."""

    def test_disabled_project(self, seed):
        from modules.flow_gate.services import git_service as svc

        out = svc.project_git_status("plainprj")["status"]
        assert out["enabled"] is False
        assert out["pending_count"] == 0
        assert out["slots"] == [] and out["pending"] == []
        assert out["base_branch"] is None

    def test_unknown_project_404(self, seed):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.project_git_status("ghost")
        assert exc.value.status == 404

    def test_precheck_rejections(self, seed):
        from modules.flow_gate.services import git_service as svc

        # bad action value
        with pytest.raises(svc.GitServiceError) as e1:
            svc.precheck_approve_git_action(
                {"type_code": "AC", "group_id": "plainprj.default.0001"}, "frobnicate"
            )
        assert e1.value.status == 422
        # non-AC document
        with pytest.raises(svc.GitServiceError) as e2:
            svc.precheck_approve_git_action(
                {"type_code": "D", "group_id": "plainprj.default.0001"}, "merge"
            )
        assert e2.value.status == 422
        # AC but the group has no git config / worktree
        with pytest.raises(svc.GitServiceError) as e3:
            svc.precheck_approve_git_action(
                {"type_code": "AC", "group_id": "plainprj.default.0001"}, "merge"
            )
        assert e3.value.status == 422

    def test_run_action_never_raises(self, seed):
        from modules.flow_gate.services import git_service as svc

        # finalize on a non-integrated group raises 409 internally; the approve
        # wrapper must swallow it into {ok: false} (D §3.1 — approval stands).
        out = svc.run_approve_git_action("plainprj.default.0001", "merge")
        assert out["ok"] is False
        assert out["error"]["code"]


@pytest.fixture(scope="class")
def act_origin(seed):
    """A dedicated bare origin + enabled project for the 0162 surface."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc

    projects.create({"project_id": "gitactprj", "project_name": "GitActProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0162-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("gitactprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("gitactprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestGitActions0162:
    def test_status_aggregation_and_pending(self, act_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        g_wait = "gitactprj.default.0201"
        g_conf = "gitactprj.default.0202"
        g_done = "gitactprj.default.0203"
        for g in (g_wait, g_conf, g_done):
            assert svc.ensure_worktree("gitactprj", "default", g) == "ok"

        _seed_wf_done_root(g_wait, project_id=g_wait.split(".", 1)[0])
        db_git.set_status(g_wait, "waiting")
        db_git.set_status(g_conf, "conflict", merge_id=None)
        db_git.set_status(g_done, "merged", merge_commit="deadbee")

        out = svc.project_git_status("gitactprj")["status"]
        assert out["enabled"] is True
        assert out["base_branch"] == "main"
        assert out["base_path_state"] == "checkout"
        # fresh clone: base == origin/main
        assert out["ahead_count"] == 0 and out["behind_count"] == 0

        slot_ids = {s["group_id"] for s in out["slots"]}
        pend_ids = {p["group_id"] for p in out["pending"]}
        assert {g_wait, g_conf} <= slot_ids
        assert g_done not in slot_ids            # terminal excluded from slots
        # 0327 T0004 (B0001): every slot reports whether its worktree is really there,
        # so the file explorer can offer create/upload on a working group instead of
        # treating every selected group as read-only. These were provisioned above.
        assert all(s["writable"] is True for s in out["slots"])
        assert pend_ids == {g_wait, g_conf} & pend_ids
        assert g_wait in pend_ids and g_conf in pend_ids
        assert g_done not in pend_ids
        assert out["pending_count"] == len(out["pending"])
        # every pending item carries the project's default action
        assert all(p["default_action"] == "merge" for p in out["pending"])

    def test_manual_fetch_fast_forwards_clean_base(self, act_origin):
        # 0320 B0001/TR0005: manual_fetch used to only move the tracking ref and
        # *report* behind — the clean base checkout never advanced ("영원히 안
        # 가져올건가?"). It now fast-forwards a clean base to origin/{base}, so a
        # single Fetch catches the base up (behind -> 0, advanced=True).
        from modules.flow_gate.services import git_service as svc

        # origin main advances via the seed worktree
        seedwt = act_origin["seedwt"]
        (seedwt / "moved.txt").write_text("x\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "advance"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        out = svc.manual_fetch("gitactprj")["result"]
        assert out["fetched"] is True
        assert out["advanced"] is True
        assert out["base_branch"] == "main"
        assert out["behind_count"] == 0
        assert out["ahead_count"] == 0

    def test_manual_push_allowed_and_rejected(self, act_origin):
        from modules.flow_gate.services import git_service as svc

        # A registered slot branch is an allowed push target (recovery re-push).
        # (Pushing base 'main' here would be a legitimate non-fast-forward after
        # the fetch test advanced origin — slot push avoids that coupling.)
        slot = "gitactprj_default_0201"
        out = svc.manual_push("gitactprj", slot)["result"]
        assert out["pushed"] is True and out["branch"] == slot
        heads = _git(["ls-remote", "--heads", str(act_origin["bare"])])
        assert slot in heads

        # A branch that is neither base nor a slot is rejected before any git.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.manual_push("gitactprj", "release-nope")
        assert exc.value.status == 422
        assert exc.value.code == "invalid_request"

    def test_approve_action_wait_then_merge(self, act_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitactprj.default.0204"
        assert svc.ensure_worktree("gitactprj", "default", group) == "ok"
        from modules.flow_gate.storage.paths import src_root
        wt = src_root("GitActProj", "gitactprj_default_0204")
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        # precheck passes for an AC doc of this git-active group
        gid = svc.precheck_approve_git_action(
            {"type_code": "AC", "group_id": group}, "wait"
        )
        assert gid == group

        waited = svc.run_approve_git_action(group, "wait")
        assert waited["ok"] is True and waited["result"]["status"] == "waiting"

        merged = svc.run_approve_git_action(group, "merge")
        assert merged["ok"] is True and merged["result"]["status"] == "merged"
        files = _git(["ls-tree", "--name-only", "main"], cwd=act_origin["bare"]).split()
        assert "work.txt" in files


# ── flowgate.default.0177: base-checkout commit / revert + E3 409 (L0002) ────

class TestDefaultBaseCommitMessage0177:
    """§2.2 — deterministic default subject; no git needed."""

    def test_plain_join(self):
        from modules.flow_gate.services import git_service as svc

        assert svc.default_base_commit_message(["a.py"]) == "fix: a.py"
        assert svc.default_base_commit_message(["a.py", "b/c.txt"]) == "fix: a.py, b/c.txt"

    def test_overflow_abbreviates(self):
        from modules.flow_gate.services import git_service as svc

        files = [f"dir/deep/path/file_{i:03}.py" for i in range(30)]
        msg = svc.default_base_commit_message(files)
        assert msg == f"fix: {files[0]} and 29 more"
        assert len(msg) <= svc.COMMIT_SUBJECT_MAX

    def test_extreme_path_hard_cut(self):
        from modules.flow_gate.services import git_service as svc

        files = ["x" * 400, "y.py"]
        msg = svc.default_base_commit_message(files)
        assert len(msg) == svc.COMMIT_SUBJECT_MAX
        assert msg.startswith("fix: xxx")


@pytest.fixture(scope="class")
def base_origin(seed):
    """A dedicated bare origin + enabled project with a provisioned base checkout."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "baseprj", "project_name": "BaseProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0177-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / "a.txt").write_text("alpha\n", encoding="utf-8")
    (seedwt / "b.txt").write_text("beta\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("baseprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    out = svc.provision_base("baseprj", "manual")
    assert out["status"] == "ok"
    base = src_root("BaseProj", "main")
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp, "base": base}
    svc.delete_config("baseprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestBaseCommitRevert0177:
    def test_status_clean_and_commit_idempotent(self, base_origin):
        from modules.flow_gate.services import git_service as svc

        out = svc.project_git_status("baseprj")["status"]
        # rev6: ai_run rides alongside (the project AI-cleanup lease as server truth).
        assert out["base_dirty"] == {
            "dirty": False, "files": [], "merge_in_progress": None, "ai_run": None,
        }
        # dirty 0개인데 base-commit → 멱등 성공 (§5 경합 케이스)
        out = svc.base_commit("baseprj", None)["result"]
        assert out["committed"] is False
        assert out["files"] == [] and out["remaining"] == []

    def test_dirty_listed_then_revert_then_commit(self, base_origin):
        from modules.flow_gate.services import git_service as svc

        base = base_origin["base"]
        (base / "a.txt").write_text("edited alpha\n", encoding="utf-8")
        (base / "b.txt").unlink()  # a deletion is a tracked change too
        (base / "junk.tmp").write_text("untracked\n", encoding="utf-8")

        out = svc.project_git_status("baseprj")["status"]
        assert out["base_dirty"]["dirty"] is True
        # untracked artifacts never appear (E3 scope)
        assert sorted(out["base_dirty"]["files"]) == ["a.txt", "b.txt"]

        # per-file revert restores a deletion from HEAD; a.txt stays dirty
        rev = svc.base_revert("baseprj", ["b.txt"])
        assert rev["ok"] is True
        assert rev["result"]["results"] == [{"path": "b.txt", "result": "reverted"}]
        assert rev["result"]["remaining"] == ["a.txt"]
        assert (base / "b.txt").read_text(encoding="utf-8") == "beta\n"

        # blank message → server derives the default subject (§2.2/§2.3)
        out = svc.base_commit("baseprj", "")["result"]
        assert out["committed"] is True
        assert out["subject"] == "fix: a.txt"
        assert out["files"] == ["a.txt"] and out["remaining"] == []
        assert out["commit"]
        # committed but NOT pushed: base is ahead of origin by exactly 1
        st = svc.project_git_status("baseprj")["status"]
        # rev6: ai_run rides alongside (the project AI-cleanup lease as server truth).
        assert st["base_dirty"] == {
            "dirty": False, "files": [], "merge_in_progress": None, "ai_run": None,
        }
        assert st["ahead_count"] == 1 and st["behind_count"] == 0
        log = _git(["log", "-1", "--pretty=%s"], cwd=base)
        assert log.strip() == "fix: a.txt"

    def test_revert_not_dirty_and_validation(self, base_origin):
        from modules.flow_gate.services import git_service as svc

        # not-dirty target is an idempotent success item (§5)
        rev = svc.base_revert("baseprj", ["a.txt"])
        assert rev["ok"] is True
        assert rev["result"]["results"] == [{"path": "a.txt", "result": "not_dirty"}]

        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_revert("baseprj", [])
        assert exc.value.status == 422
        for bad in ["/etc/passwd", "../outside.txt", "a/../../b"]:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.base_revert("baseprj", [bad])
            assert exc.value.status == 422

    def test_commit_message_too_long_422(self, base_origin):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_commit("baseprj", "x" * 201)
        assert exc.value.status == 422

    def test_merge_session_guard(self, base_origin):
        from modules.flow_gate.services import git_service as svc

        marker = base_origin["base"] / ".git" / "MERGE_HEAD"
        marker.write_text("0" * 40 + "\n", encoding="utf-8")
        try:
            with pytest.raises(svc.GitServiceError) as e1:
                svc.base_commit("baseprj", None)
            assert e1.value.status == 409 and e1.value.code == "invalid_state"
            with pytest.raises(svc.GitServiceError) as e2:
                svc.base_revert("baseprj", ["a.txt"])
            assert e2.value.status == 409 and e2.value.code == "invalid_state"
        finally:
            marker.unlink()

    def test_lock_busy_409(self, base_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        assert db_git.try_acquire_lock("baseprj", "op:elsewhere") is True
        try:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.base_commit("baseprj", None)
            assert exc.value.status == 409 and exc.value.code == "git_busy"
        finally:
            db_git.release_lock("baseprj", "op:elsewhere")

    def test_e3_409_then_commit_then_merge(self, base_origin):
        """The confirmed flow (§2.6-c): merge → 409 base_dirty(files) →
        base-commit → retry merge succeeds, and the base-commit rides along."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "baseprj.default.0301"
        assert svc.ensure_worktree("baseprj", "default", group) == "ok"
        wt = src_root("BaseProj", "baseprj_default_0301")
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        base = base_origin["base"]
        (base / "a.txt").write_text("hotfixed alpha\n", encoding="utf-8")

        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group, "merge")
        assert exc.value.status == 409          # was 500 before 0177 (L0002 §2.5)
        assert exc.value.code == "base_dirty"
        assert exc.value.details == {"files": ["a.txt"]}

        out = svc.base_commit("baseprj", None)["result"]
        assert out["committed"] is True and out["remaining"] == []

        # the local base commit does not block the retry (ff-only stays a no-op)
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "merged"
        # origin main now holds the group work AND the explicit base commit
        files = _git(["ls-tree", "--name-only", "main"], cwd=base_origin["bare"]).split()
        assert "work.txt" in files
        assert _git(["show", "main:a.txt"], cwd=base_origin["bare"]) == "hotfixed alpha\n"


# ── flowgate.default.0482 T0011: resolve_base_dirty against a real repo ──────
#
# The automated review on TR0012 rev6 found that `remote_tool_service._exec_
# resolve_base_dirty` called `git_service.base_commit(project_id, commit_paths,
# message)` while `base_commit` is declared `(project_id, message, paths=None)`
# — a real commit decision passed the path list as the commit message and the
# message string as paths. The unit-test regression at
# `test_base_dirty_rework_0482.py` mocked `base_commit` with the SAME reversed
# argument order, so it masked the bug instead of catching it. These tests
# drive the real `git_service.base_commit`/`base_revert` through a real repo
# (no mocking of either) so a reintroduced argument swap fails for real.
#
# Uses its own project ("rbdprj"), not `base_origin`'s "baseprj" — the two
# classes' fixtures both live for the pytest session and `base_origin`'s
# teardown only clears the git config, not the `projects` row, so sharing an
# id would collide.

@pytest.fixture(scope="class")
def resolve_base_dirty_origin(seed):
    """A dedicated bare origin + enabled project with a provisioned base checkout."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "rbdprj", "project_name": "RbdProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0482-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / "a.txt").write_text("alpha\n", encoding="utf-8")
    (seedwt / "b.txt").write_text("beta\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("rbdprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    out = svc.provision_base("rbdprj", "manual")
    assert out["status"] == "ok"
    base = src_root("RbdProj", "main")
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp, "base": base}
    svc.delete_config("rbdprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestResolveBaseDirtyRealRepo0482:
    def test_resolve_base_dirty_commits_selected_paths_with_the_given_message(self, resolve_base_dirty_origin, monkeypatch):
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services import remote_tool_service

        base = resolve_base_dirty_origin["base"]
        (base / "a.txt").write_text("edited alpha\n", encoding="utf-8")
        (base / "b.txt").write_text("edited beta\n", encoding="utf-8")
        status = svc.project_git_status("rbdprj")["status"]
        assert sorted(status["base_dirty"]["files"]) == ["a.txt", "b.txt"]

        monkeypatch.setattr(
            remote_tool_service, "_worker_token_for_grant",
            lambda _grant: {"action_scope": "resolve_base_dirty"},
        )
        result, _ = remote_tool_service._exec_resolve_base_dirty(
            {
                "decisions": [
                    {"path": "a.txt", "action": "commit"},
                    {"path": "b.txt", "action": "discard"},
                ],
                "complete": True,
                "commit_message": "fix: resolve base dirty a.txt",
            },
            {"project": "rbdprj"},
        )
        assert result["status"] == "resolved"
        assert result["remaining"] == []
        assert result["commit"]   # a short hash, not the commit message or a path

        # b.txt was discarded (restored to HEAD), not committed
        assert (base / "b.txt").read_text(encoding="utf-8") == "beta\n"
        subject = _git(["log", "-1", "--format=%s"], cwd=base).strip()
        assert subject == "fix: resolve base dirty a.txt"
        committed_files = _git(["diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], cwd=base).split()
        assert committed_files == ["a.txt"]
        status_after = svc.project_git_status("rbdprj")["status"]
        # rev6: ai_run rides alongside — the project AI-cleanup lease as server truth,
        # so the panel stops latching "already running" in the browser (rejection 2).
        assert status_after["base_dirty"] == {
            "dirty": False, "files": [], "merge_in_progress": None, "ai_run": None,
        }

    def test_resolve_base_dirty_lock_busy_when_project_already_locked(self, resolve_base_dirty_origin, monkeypatch):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services import remote_tool_service

        base = resolve_base_dirty_origin["base"]
        (base / "a.txt").write_text("edited again\n", encoding="utf-8")
        assert svc.project_git_status("rbdprj")["status"]["base_dirty"]["files"] == ["a.txt"]

        monkeypatch.setattr(
            remote_tool_service, "_worker_token_for_grant",
            lambda _grant: {"action_scope": "resolve_base_dirty"},
        )
        assert db_git.try_acquire_lock("rbdprj", "op:elsewhere") is True
        try:
            with pytest.raises(remote_tool_service._OpError) as exc:
                remote_tool_service._exec_resolve_base_dirty(
                    {"decisions": [{"path": "a.txt", "action": "discard"}], "complete": True},
                    {"project": "rbdprj"},
                )
            assert exc.value.status == 409
            assert exc.value.details == {"reason": "git_busy"}
        finally:
            db_git.release_lock("rbdprj", "op:elsewhere")
        # nothing was applied while the project lock was held elsewhere
        assert svc.project_git_status("rbdprj")["status"]["base_dirty"]["files"] == ["a.txt"]


@pytest.fixture(scope="class")
def project_scoped_admission_origin(seed):
    """A dedicated bare origin + enabled project for the 0481 T0010 admission tests.

    Its own project id on purpose: `resolve_base_dirty_origin` above is class-scoped, so
    sharing it would re-run `projects.create("rbdprj")` for this class and error at setup.
    """
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc

    projects.create({"project_id": "psaprj", "project_name": "PsaProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0481-t0010-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("psaprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    assert svc.provision_base("psaprj", "manual")["status"] == "ok"
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("psaprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestBaseDirtyDelegationAdmission0481:
    """0481 T0010 #1 — [AI에게 맡기기] answered "기준 브랜치 AI 정리를 시작하지 못했습니다."
    on every press, forever.

    `resolve_base_dirty` is the one action_scope with NO group of its own: it works in the
    project's BASE checkout, and `start_ai_invoke` synthesizes `<project>.none.0000` only so
    the run/lease rows have a key. Admission then ran BOTH group-worktree gates against that
    phantom group, which by construction can never have a worktree, so every press died with
    409 `worktree_unavailable` before a token was ever minted. 0482's own tests all stubbed
    the surrounding calls, so nothing exercised this pair against a real git-enabled project.
    """

    # What `start_ai_invoke` synthesizes for a group-less resolve_base_dirty press.
    PHANTOM_GROUP = "psaprj.none.0000"

    def test_group_worktree_gate_lets_a_project_scoped_run_through(self, project_scoped_admission_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services.ai_invoke import admission

        # The same arguments start_run passes for a resolve_base_dirty press.
        admission._require_group_worktree(
            "psaprj", "none", self.PHANTOM_GROUP, "main",
            locale="ko", action_scope="resolve_base_dirty",
        )
        # …and it must not have INVENTED the group on the way through. Before this fix the
        # gate's ensure_worktree self-heal happily provisioned a real branch
        # (`psaprj_none_0000`), a checked-out worktree and a group_git_state row for a group
        # that does not exist — junk that outlives the press.
        assert db_git.get_state(self.PHANTOM_GROUP) is None

    def test_initial_source_sync_gate_lets_a_project_scoped_run_through(self, project_scoped_admission_origin):
        from modules.flow_gate.services.ai_invoke import admission

        admission._ensure_initial_source_sync(
            "psaprj", "none", self.PHANTOM_GROUP, "resolve_base_dirty", "psaprj", locale="ko",
        )

    def test_an_ordinary_scope_still_goes_through_the_base_tree_guard(self, project_scoped_admission_origin):
        # The base-tree guard itself (0299 R0001) must not be weakened for the scopes it was
        # written for. An `edit` run on a group with no worktree still engages the gate, and
        # the gate's self-heal provisions the group — the exact work the project-scoped skip
        # above must NOT do for a group that does not exist.
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services.ai_invoke import admission

        assert db_git.get_state("psaprj.default.0001") is None
        admission._require_group_worktree(
            "psaprj", "default", "psaprj.default.0001", "main",
            locale="ko", action_scope="edit",
        )
        state = db_git.get_state("psaprj.default.0001")
        assert state is not None and state["branch"] == "psaprj_default_0001"


# ── flowgate.default.0199 B0001: no-work group auto-discard (no merge/push) ───

@pytest.fixture(scope="class")
def author_origin(seed):
    """Dedicated origin + provisioned base for the configurable-author E2E (0237)."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "authprj", "project_name": "AuthProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0237-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "a.txt").write_text("alpha\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("authprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    assert svc.provision_base("authprj", "manual")["status"] == "ok"
    yield {"bare": bare, "tmp": tmp, "base": src_root("AuthProj", "main")}
    svc.delete_config("authprj")
    shutil.rmtree(tmp, ignore_errors=True)


def _ident_of(base, ref="HEAD"):
    out = _git(["log", "-1", "--format=%an|%ae|%cn|%ce", ref], cwd=base).strip()
    an, ae, cn, ce = out.split("|")
    return {"author": f"{an} <{ae}>", "committer": f"{cn} <{ce}>"}


@needs_git
class TestConfigurableAuthor0237:
    """R0001: server commits must be attributable to a configured person, not
    unconditionally to FlowGate. Author moves; committer stays FlowGate."""

    FLOWGATE = "FlowGate <flowgate@localhost>"

    def _set_author(self, name, email):
        from modules.flow_gate.services import git_service as svc

        cfg = svc.get_config_view("authprj")["config"]
        svc.save_config("authprj", {
            "repo_url": cfg["repo_url"], "base_branch": "main",
            "enabled": True, "author_name": name, "author_email": email,
        })

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
        """The `--author` flag does not exist for `git merge` (NR0003 §4), so the
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


@pytest.fixture(scope="class")
def noop_origin(seed):
    """A dedicated bare origin + enabled project for the 0199 no-work surface."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc

    projects.create({"project_id": "gitnoop", "project_name": "GitNoop"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0199-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("gitnoop", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("gitnoop")
    shutil.rmtree(tmp, ignore_errors=True)


def _seed_wf_done_root(group_id: str, project_id: str = "gitnoop") -> None:
    """Insert an approved (wf_done) R root so _group_root_wf_done(group_id) is True
    (the precondition for the none→awaiting_choice / auto-discard transition). Also
    creates the parent groups row the documents FK requires."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups

    if db_groups.get_by_id(group_id) is None:
        db_groups.create({
            "group_id": group_id, "project_id": project_id,
            "module": "default", "title": "inquiry",
        })
    doc_id = f"{group_id}.0001-R"
    if db_docs.get_by_id(doc_id) is None:
        db_docs.create({
            "doc_id": doc_id, "project_id": project_id, "module": "default",
            "group_id": group_id, "type_code": "R", "seq": 1, "title": "inquiry root",
            "file_path": f"documents/{group_id}/0001-R.md",
        })
    db_docs.update(doc_id, {"doc_review_status": "wf_done"})


@needs_git
class TestNoWorkAutoDiscard0199:
    """flowgate.default.0199 B0001 — a wf_done group that produced NO work (branch
    at base tip, clean worktree) must be auto-discarded: its slot is torn down with
    NO merge and NO push (no empty `--no-ff` commit on base, no leaked branch on
    origin), instead of being parked in the finalize gate."""

    def _origin_heads(self, origin) -> str:
        return _git(["ls-remote", "--heads", str(origin["bare"])])

    def _origin_main_commits(self, origin) -> int:
        out = _git(["log", "--oneline", "main"], cwd=origin["bare"])
        return len([l for l in out.splitlines() if l.strip()])

    def test_realize_transition_discards_no_work_group(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0210"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        assert db_git.get_state(group)["worktree_registered"] == 1
        _seed_wf_done_root(group)

        # Eager approval-time realization: no work → auto-discard, not awaiting_choice.
        svc.realize_wf_done_transition(group)

        state = db_git.get_state(group)
        # Slot torn down: unregistered, DB status left at the neutral "none"
        # ("discarded" is a response label only — the status CHECK has no such value).
        assert state["worktree_registered"] == 0
        assert state["status"] == "none"
        # No finalize gate is surfaced for a discarded group.
        assert svc.get_finalize_state(group)["state"]["status"] == "none"
        # Base got NO empty merge commit; origin got NO leaked work branch.
        assert self._origin_main_commits(noop_origin) == 1        # init only
        assert "gitnoop_default_0210" not in self._origin_heads(noop_origin)

    def test_no_work_group_absent_from_status_lists(self, noop_origin):
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0211"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        _seed_wf_done_root(group)

        # Aggregation-time realization also discards; the group never appears as a
        # pending finalize nor as an active slot.
        out = svc.project_git_status("gitnoop")["status"]
        assert group not in {p["group_id"] for p in out["pending"]}
        assert group not in {s["group_id"] for s in out["slots"]}

    def test_real_work_group_still_gated(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitnoop.default.0212"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        wt = src_root("GitNoop", "gitnoop_default_0212")
        # An uncommitted worktree edit is real, mergeable work (finalize absorbs it).
        (wt / "work.txt").write_text("real work\n", encoding="utf-8")
        _seed_wf_done_root(group)

        svc.realize_wf_done_transition(group)
        state = db_git.get_state(group)
        assert state["status"] == "awaiting_choice"    # real work → keep the gate
        assert state["worktree_registered"] == 1

    def test_finalize_merge_no_change_short_circuits(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0213"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        # Force the gate open on a clean worktree (branch at base, no work) and
        # explicitly pick merge — the no-change guard must still refuse to stamp an
        # empty merge commit / push.
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "merge")["result"]
        assert out["status"] == "discarded"
        assert out["pushed"] is False
        assert out["merge_commit"] is None
        # No empty merge commit on base/origin, no leaked branch, slot cleaned.
        assert self._origin_main_commits(noop_origin) == 1
        assert "gitnoop_default_0213" not in self._origin_heads(noop_origin)
        assert db_git.get_state(group)["worktree_registered"] == 0

    def test_finalize_push_no_change_short_circuits(self, noop_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        group = "gitnoop.default.0214"
        assert svc.ensure_worktree("gitnoop", "default", group) == "ok"
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")

        out = svc.finalize(group, "push")["result"]
        assert out["status"] == "discarded"
        # push of a no-work group must NOT leak an empty branch to origin.
        assert "gitnoop_default_0214" not in self._origin_heads(noop_origin)
        assert db_git.get_state(group)["worktree_registered"] == 0


@pytest.mark.skipif(not _GIT, reason="git binary unavailable")
class TestFirstPushBootstrap0297:
    """0297 B0001 / NR0003 — a freshly created (empty) remote has no
    refs/remotes/origin/{base}, so ahead/behind and the unpushed walk are
    unmeasurable. The payload used to flatten that to commit_count 0, which the
    client could not tell apart from "in sync" — and the only push button in the
    app is gated on that number, so the FIRST push had no entry point at all.

    These tests pin the two facts the client now gates on, and prove the push it
    was blocked from issuing works."""

    def _bootstrap(self, tmp_path):
        """Empty bare origin + a base checkout holding one local snapshot commit
        — exactly the state `_adopt()` leaves behind after provisioning against a
        brand-new repository."""
        bare = tmp_path / "origin.git"
        base = tmp_path / "base"
        _git(["init", "--bare", "-b", "main", str(bare)])
        _git(["init", "-b", "main", str(base)])
        _git(["remote", "add", "origin", str(bare)], cwd=base)
        _git(["fetch", "origin"], cwd=base)
        (base / "README.md").write_text("hello\n", encoding="utf-8")
        _git(["add", "-A"], cwd=base)
        _git(["commit", "-m", "snapshot"], cwd=base)
        return bare, base

    def test_empty_remote_reports_bootstrap_fields(self, tmp_path):
        from modules.flow_gate.services import git_service as svc

        bare, base = self._bootstrap(tmp_path)
        assert not svc._ref_exists(base, "refs/remotes/origin/main")

        unpushed = svc._build_unpushed("gitprj", base, "main", None)
        assert unpushed["measured"] is False
        assert unpushed["commit_count"] == 0          # legacy field, unchanged
        assert unpushed["remote_branch_missing"] is True
        assert unpushed["local_commit_count"] == 1    # …but there IS work to push

        # The push the client used to hide is a plain success.
        _git(["push", "origin", "main"], cwd=base)
        assert "refs/heads/main" in _git(["ls-remote", "--heads", str(bare)])

    def test_measured_payload_keeps_shape(self, tmp_path):
        from modules.flow_gate.services import git_service as svc

        _bare, base = self._bootstrap(tmp_path)
        _git(["push", "origin", "main"], cwd=base)
        (base / "next.txt").write_text("more\n", encoding="utf-8")
        _git(["add", "-A"], cwd=base)
        _git(["commit", "-m", "second"], cwd=base)

        unpushed = svc._build_unpushed("gitprj", base, "main", None)
        assert unpushed["measured"] is True
        assert unpushed["commit_count"] == 1
        # Both bootstrap fields are always present so the client reads them
        # unconditionally; measured means the remote branch exists.
        assert unpushed["remote_branch_missing"] is False
        assert unpushed["local_commit_count"] is None

    def test_missing_checkout_is_not_reported_as_empty_remote(self, tmp_path):
        """Unmeasured has other causes (no checkout, git off). Those must NOT
        read as "empty remote", or the client would offer a first push with
        nothing to push."""
        from modules.flow_gate.services import git_service as svc

        unpushed = svc._build_unpushed("gitprj", tmp_path / "missing", "main", None)
        assert unpushed["measured"] is False
        assert unpushed["remote_branch_missing"] is False
        assert unpushed["local_commit_count"] is None


# ── Untracked base files: listing, explicit commit, merge diagnostics ─────────
# flowgate.default.0296 T0004, implementing NR0003 R1/R3/R5.
#
# The bug behind these tests (B0001): a file created through the FlowGate UI
# lands in the BASE checkout, but every group worktree is `git worktree add`-ed
# from a COMMIT — so the file exists in the tree the operator sees and in none of
# the trees the AI workers read. "Commit it and it appears" was true, yet
# base-commit ran `add -u` and refused to stage anything untracked, leaving no
# way out from inside the product.

def test_untracked_merge_blockers_parsing():
    """R5's stderr parser — pure text, so it needs no git binary.

    None (not []) when the failure was something else: the caller only swaps in
    the dedicated 409 on a positive identification, so unrelated git errors keep
    their honest 500 instead of being mislabelled as an untracked collision.
    """
    from modules.flow_gate.services import git_service as svc

    stderr = (
        "error: The following untracked working tree files would be "
        "overwritten by merge:\n"
        "\tdocs/new.md\n"
        "\tsrc/added.py\n"
        "Please move or remove them before you merge.\n"
        "Aborting\n"
    )
    assert svc._untracked_merge_blockers(stderr) == ["docs/new.md", "src/added.py"]
    # the delete-side wording of the same refusal
    assert svc._untracked_merge_blockers(
        "error: Untracked working tree file would be removed by merge:\n\tx.txt\n"
    ) == ["x.txt"]
    # unrelated failures / empty input → None, never a bogus empty diagnosis
    assert svc._untracked_merge_blockers("fatal: refusing to merge unrelated histories") is None
    assert svc._untracked_merge_blockers("") is None
    assert svc._untracked_merge_blockers(None) is None
    # header recognized but no parsable list → still a positive identification
    assert svc._untracked_merge_blockers(
        "error: The following untracked working tree files would be overwritten by merge:\n"
    ) == []


@pytest.fixture(scope="class")
def untracked_origin(seed):
    """A dedicated bare origin + enabled project, mirroring `base_origin`."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "untrkprj", "project_name": "UntrkProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0296-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / ".gitignore").write_text("*.secret\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("untrkprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    assert svc.provision_base("untrkprj", "manual")["status"] == "ok"
    yield {"bare": bare, "tmp": tmp, "base": src_root("UntrkProj", "main")}
    svc.delete_config("untrkprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestBaseUntrackedCommit0296:
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


# ── flowgate.default.0350 T0004: base_untracked_conflict integrated recovery ──
#
# NR0003's conclusion: the 409 is correct Git behaviour (data-loss prevention),
# but the product only ever implemented half of its own "commit or remove them"
# guidance (commit only) and never exercised the full "revert → untracked
# survives → merge alone → 409 → in-app recovery → retry succeeds" flow
# end-to-end. These fixtures/tests are that missing coverage (NR §7 items 1-6);
# item 7 (the four surfaces' client-side handling) is covered by client vitest.

@pytest.fixture(scope="class")
def untracked_conflict_origin(seed):
    """A dedicated bare origin + enabled project + provisioned base checkout,
    mirroring `base_origin`/`untracked_origin` but kept separate so this class's
    sequential same-path collisions never interact with theirs."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "utcprj", "project_name": "UtcProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0350-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / ".gitignore").write_text("*.secret\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("utcprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    assert svc.provision_base("utcprj", "manual")["status"] == "ok"
    yield {"bare": bare, "tmp": tmp, "base": src_root("UtcProj", "main")}
    svc.delete_config("utcprj")
    shutil.rmtree(tmp, ignore_errors=True)


def _make_untracked_collision(
    svc, db_git, src_root, group, path, content="group version\n",
    project_id="utcprj", project_name="UtcProj",
):
    """Provision a group worktree, stage `path` for its absorb commit (finalize
    commits worker edits before it ever touches the base checkout), and leave the
    caller to drop the SAME path, uncommitted, into the shared base checkout —
    the exact B0001 collision. Returns the worktree Path."""
    module = group.split(".", 2)[1]
    assert svc.ensure_worktree(project_id, module, group) == "ok"
    wt = src_root(project_name, group.replace(".", "_"))
    (wt / path).write_text(content, encoding="utf-8")
    _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
    db_git.set_status(group, "awaiting_choice")
    return wt


@needs_git
class TestBaseUntrackedConflictEndToEnd0350:
    """NR0003 §7 items 1-4: the full collision, both actions, side-effect-free
    failure, the exact B0001 ordering, and the non-conflicting control case."""

    def test_merge_and_merge_only_both_hit_the_same_409(self, untracked_conflict_origin):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_conflict_origin["base"]
        for action, group in [
            ("merge", "utcprj.default.0350a"),
            ("merge_only", "utcprj.default.0350b"),
        ]:
            _make_untracked_collision(svc, db_git, src_root, group, "clash.txt")
            (base / "clash.txt").write_text("base version\n", encoding="utf-8")
            try:
                with pytest.raises(svc.GitServiceError) as exc:
                    svc.finalize(group, action)
                assert exc.value.status == 409
                assert exc.value.code == "base_untracked_conflict"
                assert exc.value.details == {"files": ["clash.txt"]}
            finally:
                (base / "clash.txt").unlink()

    def test_failure_is_side_effect_free(self, untracked_conflict_origin):
        """NR §7 item 2: group stays `waiting`, the base file content and the
        group branch/worktree all survive, and no MERGE_HEAD is left behind —
        git refuses the collision before a merge session ever opens."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_conflict_origin["base"]
        group = "utcprj.default.0350c"
        wt = _make_untracked_collision(svc, db_git, src_root, group, "clash2.txt")
        (base / "clash2.txt").write_text("base pre-existing\n", encoding="utf-8")
        try:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.finalize(group, "merge_only")
            assert exc.value.code == "base_untracked_conflict"

            state = db_git.get_state(group)
            assert state["status"] == "waiting"
            assert (base / "clash2.txt").read_text(encoding="utf-8") == "base pre-existing\n"
            assert wt.is_dir() and (wt / "clash2.txt").is_file()
            branches = _git(["branch", "--list", "utcprj_default_0350c"], cwd=base).strip()
            assert "utcprj_default_0350c" in branches
            assert not (base / ".git" / "MERGE_HEAD").exists()
        finally:
            (base / "clash2.txt").unlink()

    def test_revert_tracked_then_untracked_only_matches_b0001_order(self, untracked_conflict_origin):
        """NR §7 item 3: B0001's actual sequence — a tracked base edit AND an
        untracked collision file both present, revert clears only the tracked
        one, and merge THEN fails on the untracked-only leftover (not the E3
        guard, which the first finalize hit instead)."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_conflict_origin["base"]
        group = "utcprj.default.0350d"
        _make_untracked_collision(svc, db_git, src_root, group, "clash3.txt")
        (base / "README.md").write_text("hotfixed\n", encoding="utf-8")   # tracked
        (base / "clash3.txt").write_text("base pre-existing\n", encoding="utf-8")  # untracked collision
        try:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.finalize(group, "merge_only")
            assert exc.value.code == "base_dirty"          # tracked guard fires first
            assert exc.value.details == {"files": ["README.md"]}

            rev = svc.base_revert("utcprj", ["README.md"])
            assert rev["result"]["remaining"] == []

            with pytest.raises(svc.GitServiceError) as exc2:
                svc.finalize(group, "merge_only")
            assert exc2.value.code == "base_untracked_conflict"
            assert exc2.value.details == {"files": ["clash3.txt"]}
        finally:
            (base / "clash3.txt").unlink()

    def test_noncolliding_untracked_never_blocks_merge(self, untracked_conflict_origin):
        """NR §7 item 4: an untracked base file on a DIFFERENT path is advisory
        only — merge proceeds and the file is simply left behind, proving this
        stayed a path-intersection guard and never widened into "any base
        untracked file blocks everything" (the 0165.0009 regression)."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_conflict_origin["base"]
        group = "utcprj.default.0350e"
        _make_untracked_collision(svc, db_git, src_root, group, "own_work.txt")
        (base / "unrelated_build_artifact.tmp").write_text("junk\n", encoding="utf-8")

        out = svc.finalize(group, "merge_only")
        assert out["result"]["status"] == "merged"
        assert (base / "unrelated_build_artifact.tmp").is_file()
        (base / "unrelated_build_artifact.tmp").unlink()


@pytest.fixture(scope="class")
def untracked_remove_origin(seed):
    """A SEPARATE dedicated bare origin + project from `untracked_conflict_origin`
    — one project id per fixture, one fixture per class (repo convention, see
    `grpexp_origin`): `projects.create` does not tolerate a repeated project id
    across two class-scoped fixture instantiations in the same test module run."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    projects.create({"project_id": "utcrmprj", "project_name": "UtcRmProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0350-rm-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / ".gitignore").write_text("*.secret\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("utcrmprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    assert svc.provision_base("utcrmprj", "manual")["status"] == "ok"
    yield {"bare": bare, "tmp": tmp, "base": src_root("UtcRmProj", "main")}
    svc.delete_config("utcrmprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestBaseRemoveAndRetry0350:
    """NR0003 §7 items 5-6 / §8 R4: the missing `base_remove` API, and both
    recovery paths (delete, and the existing selective commit) unblocking a
    retried finalize with the action contract intact."""

    def test_remove_deletes_and_validates(self, untracked_remove_origin):
        from modules.flow_gate.services import git_service as svc

        base = untracked_remove_origin["base"]
        (base / "gone.txt").write_text("delete me\n", encoding="utf-8")

        # path validation happens before anything reaches git
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", [])
        assert exc.value.status == 422
        for bad in ["/etc/passwd", "../outside.txt", "a/../../b"]:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.base_remove("utcrmprj", [bad])
            assert exc.value.status == 422 and exc.value.code == "invalid_request"

        # a TRACKED file is never a delete target — that is base_revert's job
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", ["README.md"])
        assert exc.value.status == 422 and exc.value.code == "invalid_request"
        assert exc.value.details == {"files": ["README.md"]}
        assert (base / "README.md").is_file()   # untouched by the refusal

        # a gitignore'd path gets its own honest code, never a silent force-clean
        (base / "keys.secret").write_text("shh\n", encoding="utf-8")
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", ["keys.secret"])
        assert exc.value.code == "path_ignored"
        assert exc.value.details == {"files": ["keys.secret"]}
        (base / "keys.secret").unlink()

        # a path git does not currently see as untracked (never existed) is refused
        with pytest.raises(svc.GitServiceError) as exc:
            svc.base_remove("utcrmprj", ["never_existed.txt"])
        assert exc.value.status == 422 and exc.value.code == "invalid_request"
        assert exc.value.details == {"files": ["never_existed.txt"]}

        out = svc.base_remove("utcrmprj", ["gone.txt"])["result"]
        assert out["results"] == [{"path": "gone.txt", "result": "removed"}]
        assert not (base / "gone.txt").exists()
        assert "gone.txt" not in out["remaining_untracked"]

    def test_remove_then_retry_merge_pushes(self, untracked_remove_origin):
        """NR §7 item 5: delete the blocking file → the parked finalize retries
        and `merge` still pushes (the action contract is unaffected by how the
        conflict was cleared)."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_remove_origin["base"]
        group = "utcrmprj.default.0350f"
        _make_untracked_collision(
            svc, db_git, src_root, group, "clash5.txt",
            project_id="utcrmprj", project_name="UtcRmProj",
        )
        (base / "clash5.txt").write_text("base pre-existing\n", encoding="utf-8")

        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group, "merge")
        assert exc.value.code == "base_untracked_conflict"

        rm = svc.base_remove("utcrmprj", ["clash5.txt"])["result"]
        assert rm["results"] == [{"path": "clash5.txt", "result": "removed"}]

        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is True
        files = _git(
            ["ls-tree", "--name-only", "main"], cwd=untracked_remove_origin["bare"]
        ).split()
        assert "clash5.txt" in files   # the group's own copy landed via the merge

    def test_remove_then_retry_merge_only_stays_local(self, untracked_remove_origin):
        """`merge_only` retried the same way must still report pushed=false."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_remove_origin["base"]
        group = "utcrmprj.default.0350g"
        _make_untracked_collision(
            svc, db_git, src_root, group, "clash6.txt",
            project_id="utcrmprj", project_name="UtcRmProj",
        )
        (base / "clash6.txt").write_text("base pre-existing\n", encoding="utf-8")

        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group, "merge_only")
        assert exc.value.code == "base_untracked_conflict"

        svc.base_remove("utcrmprj", ["clash6.txt"])
        out = svc.finalize(group, "merge_only")
        assert out["result"]["status"] == "merged"
        assert out["result"]["pushed"] is False

    def test_selective_commit_then_retry_also_unblocks(self, untracked_remove_origin):
        """NR §7 item 6: GitStatusPanel's existing selective-commit path (base
        commit with explicit `paths`) resolves the same 409 just as well as
        delete — the server side of the client's auto-retry."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_remove_origin["base"]
        group = "utcrmprj.default.0350h"
        _make_untracked_collision(
            svc, db_git, src_root, group, "clash7.txt",
            project_id="utcrmprj", project_name="UtcRmProj",
        )
        (base / "clash7.txt").write_text("base pre-existing, worth keeping\n", encoding="utf-8")

        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(group, "merge_only")
        blocked = exc.value.details["files"]
        assert blocked == ["clash7.txt"]

        committed = svc.base_commit("utcrmprj", "fix: keep the base copy", blocked)["result"]
        assert committed["committed"] is True

        out = svc.finalize(group, "merge_only")
        assert out["result"]["status"] == "conflict"    # both sides now hold real, different content
        # ...which is a normal content conflict — the untracked-collision 409 is
        # gone, proving the commit alone unblocked the merge attempt.
        assert out["result"]["merge_id"] is not None


@pytest.fixture(scope="class")
def grpexp_origin(seed):
    """A bare origin + enabled project dedicated to the group-explorer tests. Uses its
    OWN project id so its on-disk base checkout never collides with another class's
    (each project name maps to one persistent checkout dir across the file)."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc

    projects.create({"project_id": "grpexpprj", "project_name": "GrpExpProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0315-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("grpexpprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "tmp": tmp}
    svc.delete_config("grpexpprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestGroupExplorerUntracked:
    """0315 TR (NR0003 권고 1·2·3): the checkout-free group-branch explorer surfaces
    untracked (never-committed) worktree files. B0001's symptom was that a worker's new
    file stayed invisible in tree / changes / blob reads until finalize, because all three
    read committed git objects only. These pin the untracked channel end-to-end."""

    GROUP = "grpexpprj.default.0150"

    def _provision(self, svc):
        from modules.flow_gate.storage.paths import src_root

        assert svc.ensure_worktree("grpexpprj", "default", self.GROUP) == "ok"
        return src_root("GrpExpProj", "grpexpprj_default_0150")

    def test_tree_and_changes_surface_untracked(self, grpexp_origin):
        from modules.flow_gate.services import git_service as svc

        wt = self._provision(svc)
        # A brand-new file (never git add-ed) plus a tracked edit — the exact asymmetry
        # B0001 reports ("수정은 보이는데 신규만 안 보인다").
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
        """0325 T0006: the final-approval sidebar summarizes how big a group's change is,
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
        """NR0003 (flowgate.default.0329): [변경사항 열기] must read through the SAME
        old/new-content contract the 0326 file explorer uses (flowgate.default.0326
        NR0005 §4) — the merge-base blob is the old side, the live worktree file is the
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


@pytest.fixture(scope="class")
def terminal_reopen_origin(seed):
    """A dedicated bare origin + enabled project for the 0532 T0007 terminal-reopen
    re-provisioning test: a plain bare origin/local-clone pair, kept separate from the
    other classes' shared fixtures so this test can freely advance ``main`` past the
    merge point without disturbing anyone else's timeline."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc

    projects.create({"project_id": "gittermprj", "project_name": "GitTermProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0532-terminal-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "B0"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("gittermprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("gittermprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestTerminalReopenReprovision0532:
    """flowgate.default.0532 T0007 §4 condition 1 — the review's finding: when a
    terminal slot has no retained group branch, reopen used to always re-create the
    worktree at bare C1. If the configured base has since moved on to a later commit
    that already contains C1 (a real merge landed and the base kept moving), the
    recreated branch silently dropped that later base content instead of the T0007 §4
    contract ("re-provision from the current configured/effective base while
    preserving C1"). ``_ensure_worktree_locked`` must fork from the current base tip
    whenever it still contains C1, not from the bare commit."""

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
