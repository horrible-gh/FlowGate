"""Git integration — db + service test suite.

Covers:
  - branch name generation / sanitization → the earlier implementation
  - secret masking + AES-GCM encrypt/decrypt roundtrip → the earlier implementation
  - config CRUD: masking, keep/clear secret protocol, URL/enum
    validation, delete → the plan·
  - effective source-root resolution: fallback-first → the earlier implementation·
  - project git lock: acquire / busy / release / transfer → the earlier implementation
  - finalize guards (non-integrated group → 409) → the earlier implementation
  - finalize action contract: additive commit_push/commit_only,
    untouched UI choice lists, dirty `push` refusal → the review·
  - REAL git end-to-end (skipped when git is absent): clone +
    worktree provisioning (idempotent), finalize merge/push/wait,
    conflict session → resolve/abort → the earlier implementation~

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

_SERVER_DIR = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from group_lock_stub import hold_lock, held_locks  # noqa: E402
from modules.flow_gate.services.git import lock_manager as lm  # noqa: E402

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


def _node_syntax_checker_available() -> bool:
    """The real-parser review check (``git-review-syntax-check.mjs``) runs against this
    server's own ``client/node_modules``; a source checkout without them cannot run it."""
    from modules.flow_gate.services import git_service as _svc
    return (Path(_svc._FLOWGATE_CLIENT_DIR) / "node_modules" / "typescript").is_dir()         and shutil.which("node") is not None


needs_node_syntax = pytest.mark.skipif(
    not _node_syntax_checker_available(),
    reason="client/node_modules (typescript, vue compiler) or node unavailable in this checkout",
)

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
        # No commit here: a transaction that commits every statement cannot roll
        # anything back, and then an atomic unit of work (the design INV-6: AC
        # approval + root completion + intent consume, all or nothing) silently
        # tests as "each write landed on its own". begin_transaction below owns
        # the commit/rollback.
        self._cur = self._conn.execute(sql, params or [])

    @property
    def cursor(self):
        """The live cursor, so `_execute_affected` can read a real rowcount.

        FlowGateStore._execute_affected reads the affected-row count off the
        transaction's cursor (that is the one portable place all three real
        adapters expose it). A mock that hides its cursor makes every CAS write
        raise "database driver did not expose affected row count" — a harness
        gap, not a product one.
        """
        return self._cur

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        import threading
        self._mutex = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        with self._mutex:
            self._conn.execute(sql, params or [])
            self._conn.commit()

    def fetch_one(self, sql, params=None):
        with self._mutex:
            row = self._conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        with self._mutex:
            return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        # One connection backs this fixture. Serialize its complete transaction,
        # including rollback, so concurrent requests cannot consume each other's
        # cursors or commit a different thread's unit of work. Domain locks and
        # session claims still run through their real database CAS operations.
        with self._mutex:
            txn = _MockTxn(self._conn)
            try:
                yield txn
            except BaseException:
                self._conn.rollback()
                raise
            self._conn.commit()

    def close(self):
        with self._mutex:
            self._conn.close()


@pytest.fixture(scope="module")
def tmp_db(migrated_sqlite_db, tmp_path_factory):
    # Each scenario module owns its storage and DB, even when helper imports
    # happen once per session. Never replay or swallow migrations here.
    previous_pepper = os.environ.get("FLOWGATE_AGENT_PEPPER_ID")
    previous = os.environ.get("FLOWGATE_STORAGE_DIR")
    os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path_factory.mktemp("git-integration"))
    db_path = migrated_sqlite_db("git-integration.db")
    mock_db = _MockDB(db_path)
    try:
        yield mock_db, db_path
    finally:
        mock_db.close()
        os.unlink(db_path)
        if previous_pepper is None:
            os.environ.pop("FLOWGATE_AGENT_PEPPER_ID", None)
        else:
            os.environ["FLOWGATE_AGENT_PEPPER_ID"] = previous_pepper
        if previous is None:
            os.environ.pop("FLOWGATE_STORAGE_DIR", None)
        else:
            os.environ["FLOWGATE_STORAGE_DIR"] = previous


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
    pass





# ── branch naming ───────────────────────────────────────────────

class TestBranchName:
    pass





class TestAutoCommitMessage:
    pass
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






# ── secret handling ─────────────────────────────────────────────

class TestSecrets:
    pass




# ── config CRUD ────────────────────────────────────────────────

class TestConfig:
    pass



    # ── configurable commit author ─────────────────────










# ── effective source-root resolution (· — fallback first) ─────

class TestEffectiveSrcRoot:
    pass



# ── domain locks (replaced by resource_lock in the earlier change) ──────

class TestLock:
    pass



# ── finalize guards ─────────────────────────────────────────────

class TestFinalizeGuards:
    pass




# ── finalize action contract ────────────

class TestFinalizeActionContract0331:
    pass
    """The action vocabulary widened additively: `push` no longer fabricates a
    commit, `commit_push`/`commit_only` carry that behaviour explicitly, and the
    constants the three finalize surfaces read are left alone on purpose so no UI
    starts offering an action it cannot yet render."""

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









# ── REAL git end-to-end ────────────────────────────────────

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
def origin_repo(seed, git_repositories):
    """A local bare origin with an initial commit on main + enabled config."""
    from modules.flow_gate.services import git_service as svc

    tmp = Path(tempfile.mkdtemp(prefix="fg-git-origin-"))
    bare, seedwt = git_repositories("git-integration-0115", tmp)

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


class TestConflictSideDroppedChunkLocal0602:
    pass
    """_conflict_side_dropped must judge each conflict chunk on its own
    resolved text (ours/theirs/both/manual), not on whole-file line membership. Pure-
    function coverage of C1~C4 and C6; the real resolve_conflicts
    boundary — E12 all-or-nothing, review gate, the actual WorkPlanEditor.vue regression
    — is covered against real git in TestGitEndToEnd below, not mocked here.
    """

    @staticmethod
    def _marker_text(ours: str, base: str, theirs: str) -> str:
        return (
            "<<<<<<< ours\n" + ours
            + "||||||| base\n" + base
            + "=======\n" + theirs
            + ">>>>>>> theirs\n"
        )















class TestConflictSideDroppedAdjacentGroups0604:
    pass
    """only genuinely synthesized nearby conflict groups are exempt."""

    @staticmethod
    def _marker(ours: str, base: Optional[str], theirs: str) -> str:
        text = "<<<<<<< ours\n" + ours
        if base is not None:
            text += "||||||| base\n" + base
        return text + "=======\n" + theirs + ">>>>>>> theirs\n"

    @staticmethod
    def _gap(count: int, prefix: str = "gap") -> str:
        return "".join(f"{prefix}-{index}\n" for index in range(count))

    @classmethod
    def _two_chunk_original(cls, gap: int) -> str:
        return (
            "head\n"
            + cls._marker("first ours\n", "first base\n", "first theirs\n")
            + cls._gap(gap)
            + cls._marker("second ours\n", "second base\n", "second theirs\n")
            + "tail\n"
        )

    @classmethod
    def _two_chunk_submission(cls, first: str, gap: int, second: str) -> str:
        return "head\n" + first + cls._gap(gap) + second + "tail\n"









    @classmethod
    def _work_plan_0599_reduction(cls) -> tuple[str, dict[str, str]]:
        """Reduced from real zdiff3 chunks 0..3; common gaps remain exactly 1, 2, 2."""
        chunks = [
            ("ever exposing\n", "def _revisions_brief(doc_id)\n", "without exposing\ndoc_id = doc['doc_id']\n"),
            ("rows = list_by_doc(doc.get('doc_id'))\n", "rows = list_by_doc(doc_id)\nexcept old\n", "rows = list_by_doc(doc_id)\n"),
            ("result = []\nrevision_no = row.get('revision_no')\n", "return legacy list\n", "result: list[dict] = []\nrevision_no = int(row['revision_no'])\n"),
            ("restorable = reason is None\n", "legacy result\n", "restorable = selected is not None and reason is None\nif len(result) >= limit:\n    break\n"),
        ]
        gaps = [
            "    try:\n",
            "    except Exception:  # history failure\n        return []\n",
            "            created_at = row.get('created_at')\n            created_by = row.get('created_by')\n",
        ]
        original = "head\n"
        for index, (ours, base, theirs) in enumerate(chunks):
            original += cls._marker(ours, base, theirs)
            if index < len(gaps):
                original += gaps[index]
        original += "    return result\n"
        submissions = {
            "merged": (
                "head\nresolved helper synthesis\n" + gaps[0]
                + "resolved guarded history lookup\n" + gaps[1]
                + "resolved de-duplicated result\n" + gaps[2]
                + chunks[3][2] + "    return result\n"
            ),
            "ours": "head\n" + chunks[0][0] + gaps[0] + chunks[1][0] + gaps[1]
                    + chunks[2][0] + gaps[2] + chunks[3][0] + "    return result\n",
            "theirs": "head\n" + chunks[0][2] + gaps[0] + chunks[1][2] + gaps[1]
                      + chunks[2][2] + gaps[2] + chunks[3][2] + "    return result\n",
        }
        return original, submissions




class TestConflictSupersede0604:
    pass
    """the `supersede` declaration is checked chunk by chunk
    against real evidence; it can only excuse a chunk the checker already rejects."""

    _marker = staticmethod(TestConflictSideDroppedAdjacentGroups0604._marker)

    # Reduced from the real 0599 test_work_plan_0395.py chunk: theirs keeps every line
    # ours added, rewrites three of them in place (wp_version 2→3, == 1 →
    # WP_VERSION_SUPPORTED, the table row) and appends its own new test.
    OURS_0395 = (
        "def test_restore_rejects_future_version(client, wp_client):\n"
        "    future_raw = '{\"wp_version\":2,\"binding\":\"advisory\"}\\n'\n"
        "    restored = wp_client.post('/restore', data=future_raw)\n"
        "    assert restored.status_code == 422\n"
        "    assert restored.headers['x-wp'] == 'v'\n"
        "    assert restored.json()[\"body\"][\"wp_version\"] == 1\n"
        "    assert restored.json()[\"error\"] == 'wp_version_unsupported'\n"
        "    (11, bad_dir / \"future.json\", '{\"wp_version\":2}\\n', \"wp_version_unsupported\"),\n"
    )
    THEIRS_0395 = (
        "from modules.flow_gate.services import work_plan_service as wp\n"
        "def test_restore_rejects_future_version(client, wp_client):\n"
        "    future_raw = '{\"wp_version\":3,\"binding\":\"advisory\"}\\n'\n"
        "    restored = wp_client.post('/restore', data=future_raw)\n"
        "    assert restored.status_code == 422\n"
        "    assert restored.headers['x-wp'] == 'v'\n"
        "    assert restored.json()[\"body\"][\"wp_version\"] == wp.WP_VERSION_SUPPORTED\n"
        "    assert restored.json()[\"error\"] == 'wp_version_unsupported'\n"
        "    (11, bad_dir / \"future.json\", '{\"wp_version\":3}\\n', \"wp_version_unsupported\"),\n"
        "\n"
        "def test_connected_recovery_added_by_theirs():\n"
        "    assert wp.WP_VERSION_SUPPORTED == 2\n"
    )
    BASE_0395 = "import pytest\n"

    @classmethod
    def _original(cls, ours: str, theirs: str, base: Optional[str] = None) -> str:
        base = cls.BASE_0395 if base is None else base
        return "head\n" + cls._marker(base + ours, base, base + theirs) + "tail\n"

    @staticmethod
    def _submitted(side_text: str, base: str = "import pytest\n") -> str:
        return "head\n" + base + side_text + "tail\n"

    @staticmethod
    def _check(path: str, original: str, submitted: str, supersede):
        from modules.flow_gate.services.git.conflict import (
            _conflict_side_violations,
            _supersede_findings,
        )

        return _supersede_findings(path, supersede, _conflict_side_violations(original, submitted))














class TestSupersedeTransport0604:
    pass
    """— `supersede` reaches resolve_conflicts from both
    routes and the API worker, and the resolver is told how (and when) to use it."""

    DECL = {"side": "theirs", "reason": "theirs contains ours"}

    def _client(self, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from modules.flow_gate.api.v1 import git_routes
        from modules.flow_gate.auth.middleware import get_current_user

        captured: list = []

        def fake_resolve(group_id, merge_id, files, complete, **kwargs):
            captured.append({"files": files, "complete": complete})
            return {"ok": True, "result": {"status": "conflict", "remaining_conflicts": ["x"]}}

        monkeypatch.setattr(git_routes.git_service, "resolve_conflicts", fake_resolve)
        monkeypatch.setattr(git_routes, "_has_permission", lambda *a, **k: True)
        monkeypatch.setattr(
            git_routes, "verify_bearer",
            lambda request: {
                "action_scope": "resolve_conflict", "group_id": "gitprj.default.0690",
                "merge_id": 7, "token_id": "tok_transport_0604", "project": "gitprj",
            },
        )
        app = FastAPI()
        app.include_router(git_routes.router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin"}
        return TestClient(app, raise_server_exceptions=False), captured






@needs_git
class TestGitEndToEnd:
    pass
    GROUP = "gitprj.default.0100"


    @staticmethod
    def _resolve_token_client_0604(monkeypatch, group: str, merge_id: int):
        """Real git_routes router + GitServiceError envelope; only verify_bearer stubbed."""
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        from modules.flow_gate.api.v1 import git_routes
        from modules.flow_gate.services.git_service import GitServiceError

        app = FastAPI()
        app.include_router(git_routes.router)

        @app.exception_handler(GitServiceError)
        async def _handler(request: Request, exc: GitServiceError):  # noqa: ANN202
            error = {"code": exc.code, "message": exc.message}
            if exc.details:
                error["details"] = exc.details
            return JSONResponse(status_code=exc.status, content={"ok": False, "error": error})

        monkeypatch.setattr(
            git_routes, "verify_bearer",
            lambda request: {
                "action_scope": "resolve_conflict", "group_id": group,
                "merge_id": merge_id, "token_id": f"tok_{group}", "project": "gitprj",
            },
        )
        client = TestClient(app, raise_server_exceptions=False)
        endpoint = f"/api/v1/groups/{group}/git/merge/{merge_id}/resolve-token"

        def post(files, complete=True):
            return client.post(endpoint, json={"files": files, "complete": complete},
                               headers={"Authorization": "Bearer test"})
        return post


    _MERGE_0599 = {
        "base": "c1949e69c86f99ddf77b3f2fc946e4ce4b88e493",
        "ours": "691fbc7",
        "theirs": "d1318024",
        "paths": [
            "client/shared/i18n/en.ts",
            "client/shared/i18n/ja.ts",
            "client/shared/i18n/ko.ts",
            "client/src/main/components/WorkPlanEditor.vue",
            "server/modules/flow_gate/documents/routers/work_plan.py",
            "server/tests/test_work_plan_0395.py",
        ],
    }

    @classmethod
    def _merge_0599_inputs(cls) -> tuple[dict, dict, dict, dict]:
        """(base, ours, theirs, resolved) bytes per path for the real 0599 merge.

        base/ours/theirs are read from this repository's own history (691fbc7 = main at
        the time, d1318024 = the 0599 group head). The resolution is theirs + the 0599
        resolver's patch (fixtures/merge_0599_resolution.patch); test_work_plan_0395.py is
        theirs verbatim — the superset case the declaration exists for.
        """
        repo = Path(__file__).resolve().parents[4]
        spec = cls._MERGE_0599
        blobs: dict[str, dict[str, bytes]] = {}
        for label in ("base", "ours", "theirs"):
            probe = subprocess.run(["git", "cat-file", "-e", f"{spec[label]}^{{commit}}"],
                                   cwd=repo, capture_output=True)
            if probe.returncode != 0:
                pytest.fail(f"0599 E2E needs commit {spec[label]} in {repo} (full clone required)")
            blobs[label] = {
                path: subprocess.run(["git", "show", f"{spec[label]}:{path}"], cwd=repo,
                                     capture_output=True, check=True).stdout
                for path in spec["paths"]
            }
        scratch = Path(tempfile.mkdtemp(prefix="fg-0599-resolution-"))
        try:
            _git(["init", "-b", "main", str(scratch)])
            for path, raw in blobs["theirs"].items():
                (scratch / path).parent.mkdir(parents=True, exist_ok=True)
                (scratch / path).write_bytes(raw)
            patch = Path(__file__).resolve().parents[2] / "fixtures" / "merge_0599_resolution.patch"
            _git(["apply", "--whitespace=nowarn", str(patch)], cwd=scratch)
            resolved = {path: (scratch / path).read_bytes() for path in spec["paths"]}
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        assert resolved["server/tests/test_work_plan_0395.py"] == blobs["theirs"]["server/tests/test_work_plan_0395.py"]
        return blobs["base"], blobs["ours"], blobs["theirs"], resolved

























    # ── review gate ─────────────────────────────────────────────────────────































# ── base-slot provisioning: lossless adopt + ledger ──────────────────────

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
    pass









# ── the earlier change: group git actions (status / fetch / push / approve) ─

class TestStatus0162NoGit:
    pass
    """Aggregation + precheck paths that need no git binary."""






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
    pass





# ── the earlier change: base-checkout commit / revert + E3 409 ────

class TestDefaultBaseCommitMessage0177:
    pass
    """§2.2 — deterministic default subject; no git needed."""





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
    pass








# ── the earlier change the earlier implementation: resolve_base_dirty against a real repo ──────
#
# The automated review on the earlier implementation found that `remote_tool_service._exec_
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
    pass



@pytest.fixture(scope="class")
def project_scoped_admission_origin(seed):
    """A dedicated bare origin + enabled project for the the earlier implementation admission tests.

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
    pass
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





# ── the earlier change the regression: no-work group auto-discard (no merge/push) ───

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
    pass
    """server commits must be attributable to a configured person, not
    unconditionally to FlowGate. Author moves; committer stays FlowGate."""

    FLOWGATE = "FlowGate <flowgate@localhost>"

    def _set_author(self, name, email):
        from modules.flow_gate.services import git_service as svc

        cfg = svc.get_config_view("authprj")["config"]
        svc.save_config("authprj", {
            "repo_url": cfg["repo_url"], "base_branch": "main",
            "enabled": True, "author_name": name, "author_email": email,
        })







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
    pass
    """a wf_done group that produced NO work (branch
    at base tip, clean worktree) must be auto-discarded: its slot is torn down with
    NO merge and NO push (no empty `--no-ff` commit on base, no leaked branch on
    origin), instead of being parked in the finalize gate."""

    def _origin_heads(self, origin) -> str:
        return _git(["ls-remote", "--heads", str(origin["bare"])])

    def _origin_main_commits(self, origin) -> int:
        out = _git(["log", "--oneline", "main"], cwd=origin["bare"])
        return len([l for l in out.splitlines() if l.strip()])







@pytest.fixture(scope="class")
def nowork_surface_origin(seed):
    """A dedicated bare origin + enabled project for the 0548 no-work SURFACE tests.

    Deliberately not shared with `noop_origin`: these tests provision and tear down
    their own slots, and a class-scoped git origin fixture belongs to exactly one
    class (a second consumer re-runs it and collides on the project row)."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc

    projects.create({"project_id": "gitnowork", "project_name": "GitNoWork"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0548-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config("gitnowork", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("gitnowork")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestNoWorkFinalizeSurfaces0548:
    pass
    """stopped the no-work group from being MERGED;
    this stops it from being SHOWN anything about merging.

    The reviewer's rejection of the earlier implementation walked the real product path on a group
    where they did no work at all (R → T → TR → AC in half a minute):

        머지할게 없는데... 경고 토스트가 그대로 뜬다 / 깃 다이얼로그도 그대로 뜬다
        머지 다이얼로그가 그대로 뜬다 / document merge section이 그대로 뜬다

    All four fall out of one chain, reproduced here on a real origin + real worktree
    + the real DB: the AC approval dialog faked a finalize gate for a slot with
    nothing in it, that stale choice rode along into the approval, and by the time
    it ran, the approval's own 0199 auto-discard had already unregistered the slot —
    so finalize could only fail with "Git integration is not active for group …",
    which the client turned into a warning toast AND an auto-opened Git panel."""

    def _origin_main_commits(self, origin) -> int:
        out = _git(["log", "--oneline", "main"], cwd=origin["bare"])
        return len([l for l in out.splitlines() if l.strip()])





@pytest.mark.skipif(not _GIT, reason="git binary unavailable")
class TestFirstPushBootstrap0297:
    pass
    """a freshly created (empty) remote has no
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





# ── Untracked base files: listing, explicit commit, merge diagnostics ─────────
# implementing the review R1/R3/R5.
#
# The bug behind these tests: a file created through the FlowGate UI
# lands in the BASE checkout, but every group worktree is `git worktree add`-ed
# from a COMMIT — so the file exists in the tree the operator sees and in none of
# the trees the AI workers read. "Commit it and it appears" was true, yet
# base-commit ran `add -u` and refused to stage anything untracked, leaving no
# way out from inside the product.



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
    pass






# ── the earlier change the earlier implementation: base_untracked_conflict integrated recovery ──
#
# 's conclusion: the 409 is correct Git behaviour (data-loss prevention),
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
    the exact the regression collision. Returns the worktree Path."""
    module = group.split(".", 2)[1]
    assert svc.ensure_worktree(project_id, module, group) == "ok"
    wt = src_root(project_name, group.replace(".", "_"))
    (wt / path).write_text(content, encoding="utf-8")
    _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
    db_git.set_status(group, "awaiting_choice")
    return wt


@needs_git
class TestBaseUntrackedConflictEndToEnd0350:
    pass
    """items 1-4: the full collision, both actions, side-effect-free
    failure, the exact the regression ordering, and the non-conflicting control case."""






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
    pass
    """items 5-6 / R4: the missing `base_remove` API, and both
    recovery paths (delete, and the existing selective commit) unblocking a
    retried finalize with the action contract intact."""






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
    pass
    """TR: the checkout-free group-branch explorer surfaces
    untracked (never-committed) worktree files. the regression's symptom was that a worker's new
    file stayed invisible in tree / changes / blob reads until finalize, because all three
    read committed git objects only. These pin the untracked channel end-to-end."""

    GROUP = "grpexpprj.default.0150"

    def _provision(self, svc):
        from modules.flow_gate.storage.paths import src_root

        assert svc.ensure_worktree("grpexpprj", "default", self.GROUP) == "ok"
        return src_root("GrpExpProj", "grpexpprj_default_0150")







@pytest.fixture(scope="class")
def terminal_reopen_origin(seed):
    """A dedicated bare origin + enabled project for the the earlier implementation terminal-reopen
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
    pass
    """condition 1 — the review's finding: when a
    terminal slot has no retained group branch, reopen used to always re-create the
    worktree at bare C1. If the configured base has since moved on to a later commit
    that already contains C1 (a real merge landed and the base kept moving), the
    recreated branch silently dropped that later base content instead of the the earlier implementation
    contract ("re-provision from the current configured/effective base while
    preserving C1"). ``_ensure_worktree_locked`` must fork from the current base tip
    whenever it still contains C1, not from the bare commit."""




# ── the earlier implementation: the API provider's tool-driven conflict loop, connected ─────────────
#
# stopped putting chunk text into a large conflict's mention; these runs prove an
# API provider can still resolve one. Everything between the model and git is real: the
# finalize that opens the session (with its EOL separation), the mention, `_api_execute`'s
# tool loop, `api_server_tools` -> `remote_tool_service.handle` for every read (only the
# token lookup, the grant row, the history log and the root lookup are stubbed -- the
# root is the real `resolve_conflict_src_root`), and the resolve-token route into
# `resolve_conflicts`. Only the model is scripted, and it can only learn a chunk's text by
# reading it through the tools.

_API_0594_PATHS = [
    "client/shared/i18n/en.ts",
    "client/shared/i18n/ja.ts",
    "client/shared/i18n/ko.ts",
    "client/src/main/components/ReviewActionBar.vue",
    "server/modules/flow_gate/api/inbox_routes.py",
    "server/modules/flow_gate/services/git/conflict.py",
    "server/modules/flow_gate/services/git/finalize.py",
    "server/modules/flow_gate/services/git_service.py",
    "server/modules/flow_gate/workflow/routers/workflow.py",
    "server/tests/test_git_facade_seam_scope_0550.py",
]
_API_0594_OURS = "e40a3ae0"        # main when 0594's last session (103) opened
_API_0594_THEIRS = "1a968388"      # flowgate_default_0594 head


def _checkout_0608() -> Path:
    """This repository, for its history only (read-only)."""
    return Path(__file__).resolve().parents[4]


def _history_blob_0608(rev: str, path: str) -> bytes:
    proc = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=str(_checkout_0608()),
                          capture_output=True)
    assert proc.returncode == 0, (rev, path, proc.stderr)
    return proc.stdout


def _session_json_0608(text: str) -> dict:
    import json
    return json.loads(text.split("```json\n", 1)[1].rsplit("\n```", 1)[0])


def _chunk_sides_0608(block: str) -> tuple[list[str], list[str]]:
    """ours / theirs lines of one whole `<<<<<<<` .. `>>>>>>>` block as read back."""
    lines = block.splitlines()
    assert lines[0].startswith("<<<<<<<") and lines[-1].startswith(">>>>>>>"), (lines[:1], lines[-1:])
    ours, theirs, state = [], [], "ours"
    for line in lines[1:-1]:
        if state == "ours" and line.startswith("|||||||"):
            state = "base"
        elif state in ("ours", "base") and line == "=======":
            state = "theirs"
        elif state == "ours":
            ours.append(line)
        elif state == "theirs":
            theirs.append(line)
    return ours, theirs


class _ScriptedConflictModel0608:
    """An API model stand-in that resolves every chunk by keeping both sides.

    It sees exactly what a real model sees -- the first user message (guidance +
    mention) and the tool results -- and nothing else. With `chunk_text: omitted` it
    cannot know a chunk until it has read the block's full range; a first piece that is
    deliberately short, and any `result_too_large` refusal, make it read again. It
    submits one file per call as `chunks`, `complete` only on the last file.
    """

    def __init__(self, *, first_piece_lines: int = 3, probe_ref: bool = True):
        import json
        self._json = json
        self.first_piece_lines = first_piece_lines
        self.probe_ref = probe_ref
        self.turns: list[list[str]] = []
        self.offered: Optional[list[str]] = None
        self.max_tokens: list = []
        self.prompt = ""
        self.session: dict = {}
        self.files: list[dict] = []
        self.file_idx = 0
        self.pending: dict[str, tuple] = {}
        self.todo: list[tuple] = []
        self.pieces: dict[tuple, dict] = {}
        self.covered: dict[tuple, list] = {}
        self.need: dict[tuple, tuple] = {}
        self.reads: list[dict] = []
        self.read_failures: list[str] = []
        self.submits: list[dict] = []
        self.submit_results: list[str] = []
        self.help_result: Optional[dict] = None
        self.ref_result: Optional[dict] = None
        self.stopped = False
        self._seq = 0

    # the provider adapter's signature (`_call_anthropic`)
    def __call__(self, _url, _model, _key, conversation, _timeout, tool_specs, _desc, _schema,
                 _force=False, max_tokens=None):
        self.max_tokens.append(max_tokens)
        if self.offered is None:
            self.offered = [spec["name"] for spec in tool_specs]
            self.prompt = conversation[1]["content"]
            self.session = _session_json_0608(self.prompt)
            self.files = self.session["files"]
            self._queue_file()
        self._absorb(conversation)
        calls = [] if self.stopped else self._next_calls()
        self.turns.append([call["name"] for call in calls])
        content = [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]} for c in calls]
        return None, calls, {"role": "assistant", "content": content}

    def _call(self, name: str, tool_input: dict, request: tuple) -> dict:
        self._seq += 1
        call_id = f"tu_{self._seq}"
        self.pending[call_id] = request
        return {"id": call_id, "name": name, "input": tool_input}

    def _queue_file(self) -> None:
        entry = self.files[self.file_idx]
        for index, chunk in enumerate(entry["chunks"]):
            start, end = chunk["start_line"], chunk["end_line"]
            self.need[(entry["path"], chunk["chunk"])] = (start, end)
            stop = end
            if self.file_idx == 0 and index == 0 and self.first_piece_lines:
                stop = min(end, start + self.first_piece_lines - 1)
            self.todo.append((entry["path"], chunk["chunk"], start, stop, end))

    def _absorb(self, conversation) -> None:
        for msg in conversation:
            content = msg.get("content")
            if msg.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                request = self.pending.pop(block.get("tool_use_id"), None)
                if request is not None:
                    self._take(request, block.get("content") or "")

    def _take(self, request: tuple, text: str) -> None:
        kind = request[0]
        if kind == "submit":
            self.submit_results.append(text)
            head = text.split("\n", 1)[0]
            if text.startswith("Conflict resolve failed") or '"ok": false' in head:
                self.stopped = True
                return
            status = self._json.loads(head)["result"]["status"]
            if status == "conflict":
                self.file_idx += 1
                if self.file_idx < len(self.files):
                    self._queue_file()
            else:
                self.stopped = True
            return
        result = self._json.loads(text)
        if kind == "help":
            self.help_result = result
            return
        if kind == "ref":
            self.ref_result = result
            return
        _kind, path, number, start, stop, end = request
        if not result.get("ok"):
            error = result.get("error") or {}
            reason = (error.get("details") or {}).get("reason") or error.get("code")
            if reason == "result_too_large" and stop > start:
                middle = (start + stop) // 2
                self.todo += [(path, number, start, middle, end), (path, number, middle + 1, stop, end)]
                return
            # any other failed read: nothing is guessed -- the model stops here.
            self.read_failures.append(reason)
            self.stopped = True
            return
        self.pieces.setdefault((path, number), {})[start] = result["content"]
        self.covered.setdefault((path, number), []).append((start, result["returned_end_line"]))
        in_flight = (
            any(req[0] == "read" and req[1:3] == (path, number) for req in self.pending.values())
            or any(item[:2] == (path, number) for item in self.todo)
        )
        if not in_flight:
            chunk_start, chunk_end = self.need[(path, number)]
            position = chunk_start
            for got_start, got_end in sorted(self.covered[(path, number)]):
                if got_start > position:
                    break
                position = max(position, got_end + 1)
            if position <= chunk_end:
                self.todo.append((path, number, position, chunk_end, chunk_end))

    def _chunk_text(self, path: str, number: int) -> Optional[str]:
        pieces = self.pieces.get((path, number)) or {}
        text = "".join(pieces[key] for key in sorted(pieces))
        lines = text.splitlines()
        if not lines or not lines[-1].startswith(">>>>>>>"):
            return None
        return text

    def _next_calls(self) -> list[dict]:
        calls: list[dict] = []
        if len(self.turns) == 0:
            calls.append(self._call("read_help", {}, ("help",)))
            refs = self.session.get("refs") or {}
            if self.probe_ref and refs.get("ours"):
                calls.append(self._call("read_source_file", {
                    "path": self.files[0]["path"], "ref": refs["ours"], "start_line": 1, "end_line": 3,
                }, ("ref",)))
        if self.pending:
            return calls
        if self.todo:
            batch, self.todo = self.todo, []
            for path, number, start, stop, end in batch:
                self.reads.append({"path": path, "chunk": number, "start_line": start, "end_line": stop})
                calls.append(self._call("read_source_file", {
                    "path": path, "start_line": start, "end_line": stop,
                }, ("read", path, number, start, stop, end)))
            return calls
        entry = self.files[self.file_idx]
        resolved = []
        for chunk in entry["chunks"]:
            text = self._chunk_text(entry["path"], chunk["chunk"])
            assert text is not None, (entry["path"], chunk["chunk"], "read incomplete")
            ours, theirs = _chunk_sides_0608(text)
            both = ours + theirs
            resolved.append({"chunk": chunk["chunk"], "content": "\n".join(both) + ("\n" if both else "")})
        payload = {"files": [{"path": entry["path"], "chunks": resolved}],
                   "complete": self.file_idx == len(self.files) - 1}
        self.submits.append(payload)
        calls.append(self._call("resolve_git_conflict", payload, ("submit",)))
        return calls


def _resolve_token_over_urlopen_0608(monkeypatch, group: str, merge_id: int) -> list:
    """`urllib.request.urlopen` -> the real resolve-token route (verify_bearer stubbed)."""
    import io
    import urllib.error
    import urllib.parse
    import urllib.request

    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.services.git_service import GitServiceError

    app = FastAPI()
    app.include_router(git_routes.router)

    @app.exception_handler(GitServiceError)
    async def _handler(request: Request, exc: GitServiceError):  # noqa: ANN202
        error: dict = {"code": exc.code, "message": exc.message}
        if exc.details:
            error["details"] = exc.details
        return JSONResponse(status_code=exc.status, content={"ok": False, "error": error})

    client = TestClient(app, raise_server_exceptions=False)
    monkeypatch.setattr(git_routes, "verify_bearer", lambda request: {
        "action_scope": "resolve_conflict", "group_id": group, "merge_id": merge_id,
        "token_id": f"tok_{group}", "project": "gitapiprj",
    })
    calls: list = []

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def fake_urlopen(req, timeout=120):
        import json
        parsed = urllib.parse.urlsplit(req.full_url)
        assert parsed.path.endswith(f"/git/merge/{merge_id}/resolve-token"), parsed.path
        resp = client.post(parsed.path, content=req.data, headers=dict(req.header_items()))
        calls.append((resp.status_code, json.loads(req.data), resp.json()))
        if 200 <= resp.status_code < 300:
            return _Resp(resp.status_code, resp.content)
        raise urllib.error.HTTPError(req.full_url, resp.status_code, "", resp.headers, io.BytesIO(resp.content))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _real_reads_for_conflict_0608(monkeypatch, group: str, merge_id: int) -> list:
    """Wire the run's read tools to the real remote pipeline, bound to the session root.

    Stubbed: token -> grant lookup, the grant's scope row (filled from the real
    tool_registry -> remote scope mapping for resolve_conflict), the history log and the
    token -> root lookup (answered by the real `resolve_conflict_src_root`)."""
    from modules.flow_gate.services import api_server_tools
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services import remote_tool_service as remote
    from modules.flow_gate.services import tool_registry

    kind, _ = tool_registry.kind_for_step("resolve_conflict")
    grant = {"grant_id": 608, "project": "gitapiprj", "module": "default", "group_id": group}
    ops: list = []
    real_handle = remote.handle

    def handle(op, raw, body):
        ops.append((op, dict(body or {})))
        return real_handle(op, raw, body)

    monkeypatch.setattr(remote, "_authenticate", lambda raw: grant)
    monkeypatch.setattr(remote.db_grants, "get_scopes", lambda grant_id: set(remote._SCOPES_BY_KIND[kind]))
    monkeypatch.setattr(remote, "_resolve_src_root", lambda g, op="read": svc.resolve_conflict_src_root(group, merge_id))
    monkeypatch.setattr(remote, "_log", lambda *a, **kw: None)
    monkeypatch.setattr(remote, "_locale_for_grant", lambda g: "en")
    monkeypatch.setattr(remote, "handle", handle)
    monkeypatch.setattr(api_server_tools.token_service, "verify", lambda raw: {
        "action_scope": "resolve_conflict", "project": "gitapiprj", "group_id": group,
        "merge_id": merge_id, "continuation_locale": "en",
    })
    return ops


def _api_conflict_run_0608(group: str, merge_id: int, run_id: str) -> tuple[dict, dict]:
    import threading
    import time
    run = {
        "project_id": "gitapiprj", "chain_source": "system", "run_id": run_id,
        "docs_target": 0, "raw_token": f"tok_{run_id}", "action_scope": "resolve_conflict",
        "mode": "single", "group_id": group, "merge_id": merge_id,
        "api_base_url": "http://fake-host/api/v1",
        "cancel_event": threading.Event(), "started_mono": time.monotonic(), "timeout_sec": 300,
    }
    provider = {"id": "aip_api_0608", "exec_type": "api", "kind": "claude",
                "api_base_url": "http://fake-host", "api_model": "test-model"}
    return run, provider


def _unmerged_0608(root: Path) -> list[str]:
    return sorted(_git(["diff", "--name-only", "--diff-filter=U"], cwd=root).split())


def _index_conflict_stages_0608(root: Path) -> str:
    return _git(["ls-files", "-u"], cwd=root)


@pytest.fixture(scope="class")
def api_conflict_origin_0608(seed):
    """A dedicated project + bare origin (the terminal_reopen_origin shape): the shared
    `gitprj` base checkout still points at the first class's origin, so a second
    `origin_repo` for that project cannot provision a worktree."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.services import git_service as svc

    projects.create({"project_id": "gitapiprj", "project_name": "GitApiProj"})
    tmp = Path(tempfile.mkdtemp(prefix="fg-git-0608-api-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)
    svc.save_config("gitapiprj", {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config("gitapiprj")
    shutil.rmtree(tmp, ignore_errors=True)


@needs_git
class TestApiConflictToolLoop0608:
    pass
    """Cases A~F of the work order, against a real finalize session."""

    def _open_session(self, api_conflict_origin_0608, group: str, files: dict, attrs: str = "") -> tuple[int, Path]:
        """``files``: path -> (base, ours=main, theirs=group) bytes; finalize conflicts."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        seedwt = api_conflict_origin_0608["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)

        def put(root: Path, which: int) -> None:
            for path, sides in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(sides[which])

        # byte-exact under this host's autocrlf=true, as in the real repository
        (seedwt / ".gitattributes").write_text(
            attrs or "".join(f"{path} -text\n" for path in files), encoding="utf-8")
        put(seedwt, 0)
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", f"{group} base"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)
        assert svc.ensure_worktree("gitapiprj", "default", group) == "ok"
        put(src_root("GitApiProj", group.replace(".", "_")), 2)
        put(seedwt, 1)
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", f"{group} main side"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)
        _seed_wf_done_root(group, project_id="gitapiprj")
        db_git.set_status(group, "awaiting_choice")
        finalized = svc.finalize(group, "merge")
        assert finalized["result"]["status"] == "conflict", finalized
        merge_id = finalized["result"]["merge_id"]
        return merge_id, svc.resolve_conflict_src_root(group, merge_id)


    def _resolve_0594(self, monkeypatch, group, merge_id, root, files):
        import json

        from modules.flow_gate.api import token_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service as ai_svc
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git import conflict as git_conflict

        # Case E: the four EOL-only files were merged and marked resolved by the server.
        eol_only = sorted(svc.list_conflicts(group, merge_id)["eol_only_paths"])
        assert eol_only == sorted(_API_0594_PATHS[:4])
        open_paths = sorted(_API_0594_PATHS[4:])
        assert _unmerged_0608(root) == open_paths
        assert sorted(db_git.remaining_conflicts(merge_id)) == open_paths

        # Case A: the real mention -- small, location only, read-tool driven.
        mention = token_routes._build_conflict_mention(
            group_id=group, project_id="gitapiprj", merge_id=merge_id,
            scratch_dir="/scratch/x", raw_token="tok_raw", api_base_url="http://fake-host/api/v1",
        )
        assert len(mention) <= 10_000, len(mention)
        session = _session_json_0608(mention)
        assert session["chunk_text"] == "omitted"
        assert sum(len(f["chunks"]) for f in session["files"]) == 24
        assert [f["path"] for f in session["files"]] == open_paths
        assert sorted(r["path"] for r in session["resolved_files"] if r["reason"] == "eol_only") == eol_only

        remote_ops = _real_reads_for_conflict_0608(monkeypatch, group, merge_id)
        resolve_calls = _resolve_token_over_urlopen_0608(monkeypatch, group, merge_id)
        model = _ScriptedConflictModel0608()
        monkeypatch.setattr(ai_svc, "_call_anthropic", model)
        monkeypatch.setattr(ai_svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")
        run, provider = _api_conflict_run_0608(group, merge_id, "aiv_conflict_0594_0608")

        result = ai_svc._api_execute(provider, mention, run)
        assert result == ("started_ok", None)

        # tool surface: the mention's read tools + read_help + the bound submit, nothing that writes
        assert model.offered == [
            "read_help", "read_source_file", "search_source", "glob_source", "stat_source",
            "diff_source", "log_source", "show_commit_source", "merge_preview_source",
            "resolve_git_conflict",
        ]
        assert "`read_source_file`" in model.prompt and "GET http://fake-host/api/v1/help" not in model.prompt
        assert len(model.prompt) <= 10_000, len(model.prompt)     # guidance + mention, as the API model gets it
        assert run["api_turn_budget"] == 4 + 2 * 24 + 6 == 58
        from modules.flow_gate.services.ai_invoke.runtime import API_CONFLICT_MAX_TOKENS
        assert set(model.max_tokens) == {API_CONFLICT_MAX_TOKENS}

        # the reads really happened, through the real pipeline, before any submit
        assert model.help_result and model.help_result["ok"] is True
        assert model.ref_result["ok"] is True
        assert model.ref_result["content"].encode() == b"".join(
            _history_blob_0608(_API_0594_OURS, session["files"][0]["path"]).splitlines(keepends=True)[:3])
        read_ops = [body for op, body in remote_ops if op == "read"]
        assert all(body["path"] in open_paths for body in read_ops)        # never an EOL-only file
        assert not [op for op, _ in remote_ops if op in ("write", "patch", "remove")]
        first_submit_turn = next(i for i, names in enumerate(model.turns) if "resolve_git_conflict" in names)
        assert "read_source_file" in model.turns[0] and first_submit_turn > 0
        # Case B: the first chunk took more than one read (a short first piece, then the rest),
        # and every chunk was read over its full marker range before it was resolved.
        first = session["files"][0]
        first_reads = [r for r in model.reads if (r["path"], r["chunk"]) == (first["path"], 1)]
        assert len(first_reads) >= 2
        assert not model.read_failures
        for entry in session["files"]:
            for chunk in entry["chunks"]:
                covered = sorted((r["start_line"], r["end_line"]) for r in model.reads
                                 if (r["path"], r["chunk"]) == (entry["path"], chunk["chunk"]))
                assert covered[0][0] == chunk["start_line"] and max(e for _, e in covered) == chunk["end_line"]

        # Case C: one file per submit -- five accepted partials, then the complete one
        assert [status for status, _body, _resp in resolve_calls] == [200] * 6
        statuses = [resp["result"]["status"] for _s, _b, resp in resolve_calls]
        assert statuses == ["conflict"] * 5 + ["resolved_pending_review"]
        remaining_after = [sorted(resp["result"]["remaining_conflicts"]) for _s, _b, resp in resolve_calls]
        assert remaining_after == [open_paths[index + 1:] for index in range(5)] + [[]]
        assert [len(body["files"]) for _s, body, _r in resolve_calls] == [1] * 6
        assert all("chunks" in body["files"][0] and "content" not in body["files"][0]
                   for _s, body, _r in resolve_calls)
        assert [body["complete"] for _s, body, _r in resolve_calls] == [False] * 5 + [True]
        assert run["conflict_partial_submits"] == 5
        assert db_git.remaining_conflicts(merge_id) == []
        assert _unmerged_0608(root) == []
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["review_state"] == svc.REVIEW_STATE_PENDING
        # the largest reply (finalize.py's 10 chunks, both sides kept) vs. the whole file
        biggest = max(len(json.dumps(body)) for _s, body, _r in resolve_calls)
        assert biggest < len(files["server/modules/flow_gate/services/git/finalize.py"][1]) // 3

        # every resolved file: markers gone (these sources quote marker strings, so only a
        # whole marker line counts), written in ours' line ending
        for path in open_paths:
            data = (root / path).read_bytes()
            assert not svc.has_conflict_markers(data.decode("utf-8")), path
            assert b"\r\r\n" not in data
            assert git_conflict._majority_eol(data) == git_conflict._majority_eol(files[path][1])
        for path in eol_only:
            assert not svc.has_conflict_markers((root / path).read_bytes().decode("utf-8")), path


    def _resolve_small(self, monkeypatch, group, merge_id, root):
        from modules.flow_gate.api import token_routes
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import ai_invoke_service as ai_svc

        mention = token_routes._build_conflict_mention(
            group_id=group, project_id="gitapiprj", merge_id=merge_id,
            scratch_dir="/scratch/x", raw_token="tok_raw", api_base_url="http://fake-host/api/v1",
        )
        session = _session_json_0608(mention)
        assert session["chunk_text"] == "inline"
        chunks = session["files"][0]["chunks"]
        assert [c["ours"] for c in chunks] == [["first = 'main'"], ["second = 'main'"]]
        assert [c["theirs"] for c in chunks] == [["first = 'group'"], ["second = 'group'"]]

        both = [{"chunk": 1, "content": "first = 'main'\nfirst = 'group'\n"},
                {"chunk": 2, "content": "second = 'main'\nsecond = 'group'\n"}]
        attempts = [
            # drops the group side of chunk 1
            [{"path": "small.py", "chunks": [{"chunk": 1, "content": "first = 'main'\n"}, both[1]]}],
            # chunk 2 not sent: its marker block stays in the rebuilt file
            [{"path": "small.py", "chunks": [both[0]]}],
            # a supersede declaration with no dropped side to excuse
            [{"path": "small.py", "chunks": both, "supersede": {"side": "ours", "reason": "main wins"}}],
            [{"path": "small.py", "chunks": both}],
        ]
        before = ((root / "small.py").read_bytes(), _index_conflict_stages_0608(root))
        snapshots = []
        offered = []

        def model(_u, _m, _k, conversation, _t, tool_specs, _d, _s, _f=False, max_tokens=None):
            offered.append([spec["name"] for spec in tool_specs])
            if len(offered) > 1:
                snapshots.append(((root / "small.py").read_bytes(), _index_conflict_stages_0608(root),
                                  db_git.remaining_conflicts(merge_id)))
            files = attempts[len(offered) - 1]
            call = {"id": f"tu{len(offered)}", "name": "resolve_git_conflict",
                    "input": {"files": files, "complete": True}}
            return None, [call], {"role": "assistant", "content": [
                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["input"]}]}

        resolve_calls = _resolve_token_over_urlopen_0608(monkeypatch, group, merge_id)
        remote_ops = _real_reads_for_conflict_0608(monkeypatch, group, merge_id)
        monkeypatch.setattr(ai_svc, "_call_anthropic", model)
        monkeypatch.setattr(ai_svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")
        run, provider = _api_conflict_run_0608(group, merge_id, "aiv_conflict_small_0608")

        assert ai_svc._api_execute(provider, mention, run) == ("started_ok", None)

        assert run["api_turn_budget"] == 4 + 2 * 2 + 1
        assert remote_ops == []                          # nothing forced a read
        codes = [resp.get("error", {}).get("code") for _s, _b, resp in resolve_calls[:3]]
        assert [s for s, _b, _r in resolve_calls] == [422, 422, 422, 200]
        assert codes == ["conflict_side_dropped", "conflict_markers_remain", "conflict_supersede_invalid"]
        # each refusal left the conflicted file, the index stages and the session as they were
        for data, stages, remaining in snapshots[:3]:
            assert (data, stages) == before
            assert remaining == ["small.py"]
        assert resolve_calls[3][2]["result"]["status"] == "resolved_pending_review"
        assert (root / "small.py").read_bytes() == (
            b"head = 0\nfirst = 'main'\nfirst = 'group'\nmid_a = 1\nmid_b = 2\nmid_c = 3\nmid_d = 4\n"
            b"second = 'main'\nsecond = 'group'\ntail = 9\n")
        assert db_git.remaining_conflicts(merge_id) == []
