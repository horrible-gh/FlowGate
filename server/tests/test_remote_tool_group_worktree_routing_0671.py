"""Two REAL managed git worktrees, mutated through the real routing API
(flowgate.default.0671 T0002; this TR's own last rejection, finding 2).

The previous revision's ``env`` fixture gave each group its own ``git init``'d directory and
set ``group_git_state.worktree_registered = 1`` by a raw SQL insert, with
``git_service.ensure_worktree`` replaced by a no-op stub. Those two directories were never
git worktrees of one shared repository, so the "two real git worktrees" claim did not hold.

This file provisions both groups' worktrees for real: one project, one real local bare
origin, one real base checkout, and ``git_service.ensure_worktree`` run UNREPLACED for each
group — the exact same call ``test_worktree_resolution_e2e_0280.py`` and
``test_merge_timeout_recovery_0607.py`` already prove creates a real ``git worktree add``
linked to the shared ``.git``. ``group_git_state.worktree_registered`` becomes 1 because that
real call set it, not because a fixture poked the row. Concurrent mutation then goes through
``remote_tool_service.handle("write", ...)`` exactly as a worker token's ``/remote/write`` call
does.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from test_git_integration_0115 import _git as _git_run  # noqa: E402 -- real git helper

PROJECT_ID = "p_0671rt"
PROJECT_NAME = "P0671Rt"
GROUPS = ("g_rt1", "g_rt2")

needs_git = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git binary unavailable",
)


def _migrations() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """One project, one real bare origin, one real base checkout, two groups each
    provisioned through the real ``ensure_worktree`` -- real managed git worktrees of the
    SAME repository, not two independently-``git init``'d directories."""
    storage_root = tmp_path / "storage"
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))

    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    for f in _migrations():
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    from sqloader.sqlite3 import SQLiteWrapper
    from modules.flow_gate.db import connection as conn_mod
    store = object.__new__(conn_mod.FlowGateStore)
    # SQLiteWrapper (not a hand-rolled single-connection mock) is what the existing
    # concurrent lock tests (test_git_parallel_0669.py, test_git_selfcheck_overlap_0669.py)
    # already prove safe under real multi-threaded acquire/release; this test drives the
    # same kind of concurrency through remote_tool_service.handle, so it needs the same
    # thread-safe backend rather than a single raw sqlite3.Connection shared across threads.
    store._db = SQLiteWrapper(db_path)
    store._sq = None
    conn_mod.STORE = store

    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db.connection import now_iso
    now = now_iso()
    store._execute(
        "INSERT INTO projects(project_id,project_name,is_active,created_at,updated_at) "
        "VALUES (?,?,1,?,?)",
        [PROJECT_ID, PROJECT_NAME, now, now],
    )
    store._execute(
        "INSERT INTO users(user_id,username,email,password,is_active,is_admin,created_at,updated_at) "
        "VALUES (?,?,?,?,1,0,?,?)",
        ["worker-user", "worker-user", "worker@example.test", "x", now, now],
    )
    db_projects.upsert_settings(PROJECT_ID, {"branch": "main"})

    # A REAL local bare origin + a real pushed base, exactly like 0280/0555/0607's harness --
    # this is what lets ensure_worktree's real "fetched" phase (git fetch origin) and
    # "registered" phase (git worktree add --no-checkout) run unmocked below.
    origin_tmp = tmp_path / "origin_src"
    bare = origin_tmp / "origin.git"
    seedwt = origin_tmp / "seedwt"
    _git_run(["init", "--bare", "-b", "main", str(bare)])
    _git_run(["init", "-b", "main", str(seedwt)])
    (seedwt / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git_run(["add", "-A"], cwd=seedwt)
    _git_run(["commit", "-m", "seed"], cwd=seedwt)
    _git_run(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git_run(["push", "origin", "main"], cwd=seedwt)

    from modules.flow_gate.services import git_service
    git_service.save_config(PROJECT_ID, {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "wait",
        "enabled": True,
    })

    for g in GROUPS:
        store._execute(
            "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, ?, 'default', ?, 'OPEN', ?, ?)",
            [g, PROJECT_ID, g, now, now],
        )

    roots: dict[str, Path] = {}
    from modules.flow_gate.storage.paths import src_root
    from modules.flow_gate.services.git.worktree import worktree_branch_name
    for g in GROUPS:
        # The real provisioning path: G -> intent, R -> fetch, M -> worktree add --no-checkout,
        # G -> reset --hard (worktree_provision.provision, run synchronously by ensure_worktree
        # itself per L 2.28). Never stubbed -- this is the exact call 0280/0555/0607 already
        # prove creates a real linked git worktree of the shared base .git.
        assert git_service.ensure_worktree(PROJECT_ID, "default", g) == "ok"
        branch = worktree_branch_name(PROJECT_ID, "default", g)
        root = src_root(PROJECT_NAME, branch)
        assert root.is_dir(), f"ensure_worktree did not materialize a real worktree for {g}"
        assert (root / ".git").exists(), f"{root} is not a git worktree"
        roots[g] = root

    from modules.flow_gate.services import remote_tool_service
    monkeypatch.setattr(
        remote_tool_service, "_worker_token_step_type_result", lambda _rec: ("TR", False)
    )

    class Ctx:
        def __init__(self):
            self.store = store
            self.roots = roots
            self.db_path = db_path
            self.origin_bare = bare

        def make_worker_token(self, token: str, group_id: str, *, token_id: str) -> str:
            from datetime import datetime, timezone, timedelta
            from modules.flow_gate.services import token_service
            _pid, pepper = token_service._active_pepper()
            token_hash = token_service._hash_token(token, pepper)
            now_dt = datetime.now(timezone.utc)
            self.store._execute(
                "INSERT INTO tokens "
                "(token_id, hash, pepper_id, project, group_id, doc_ref, action_scope, "
                "issued_to, created_at, expires_at, scratch_dir) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    token_id, token_hash, _pid, PROJECT_ID, group_id, None, "edit",
                    "worker-user", now_dt.isoformat(timespec="seconds"),
                    (now_dt + timedelta(hours=1)).isoformat(timespec="seconds"), None,
                ],
            )
            return token_id

    yield Ctx()

    conn_mod.STORE = None
    os.unlink(db_path)


def _handle(operation, token, body):
    from modules.flow_gate.services import remote_tool_service
    return remote_tool_service.handle(operation, token, body)


@needs_git
def test_write_routes_through_resolve_root_for_mutation_to_the_right_group_worktree(env):
    """A single write for one group must land in THAT group's REAL managed worktree,
    resolved by the real ``remote_tool_service.handle`` -> ``_resolve_root_for_mutation`` ->
    ``git_service.effective_src_root`` chain, not a path this test picked itself."""
    token = "raw-rt-token-g1"
    env.make_worker_token(token, "g_rt1", token_id="tok_rt_g1")

    status, payload = _handle(
        "write", token, {"path": "from_g1.txt", "content": "hello from g1", "mode": "create"}
    )

    assert status == 200, payload
    assert payload["ok"] is True
    assert (env.roots["g_rt1"] / "from_g1.txt").read_text(encoding="utf-8") == "hello from g1"
    assert not (env.roots["g_rt2"] / "from_g1.txt").exists()


@needs_git
def test_two_groups_mutate_their_own_real_worktrees_concurrently_through_the_routing_api(env):
    """Both groups' REAL managed worktrees (linked worktrees of the SAME base .git, both
    created by the real ``ensure_worktree``) are written through ``handle("write", ...)`` at
    the same time. The server's own G-domain lock (``tr2_file_policy.general_source_mutation``,
    exercised inside ``handle()``) must keep each group's batch serialized against itself
    while never blocking the other group, and release everything once every write returns."""
    tokens = {g: f"raw-rt-token-{g}" for g in GROUPS}
    for g in GROUPS:
        env.make_worker_token(tokens[g], g, token_id=f"tok_rt_{g}")
    barrier = threading.Barrier(len(GROUPS), timeout=20)
    errors: list[BaseException] = []

    def worker(g: str):
        def run():
            try:
                barrier.wait()
                for i in range(15):
                    status, payload = _handle(
                        "write", tokens[g],
                        {"path": f"{g}_{i}.txt", "content": f"{g}-{i}", "mode": "create"},
                    )
                    assert status == 200, payload
            except BaseException as exc:  # surfaced after join
                errors.append(exc)
        return run

    threads = [threading.Thread(target=worker(g), daemon=True) for g in GROUPS]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "worker thread hung"
    assert not errors, errors

    for g, other in (("g_rt1", "g_rt2"), ("g_rt2", "g_rt1")):
        names = {p.name for p in env.roots[g].glob(f"{g}_*.txt")}
        assert names == {f"{g}_{i}.txt" for i in range(15)}
        assert list(env.roots[g].glob(f"{other}_*.txt")) == []

    # the real G lock (acquired/released inside handle() via general_source_mutation)
    # must leave nothing held once every write has returned.
    from modules.flow_gate.db import git_concurrency as gc
    assert gc.list_locks_in_scope(PROJECT_ID) == []


@needs_git
def test_write_is_rejected_not_silently_redirected_to_base_when_worktree_is_unregistered(
    env, monkeypatch,
):
    """Mirrors ``_resolve_root_for_mutation``'s own contract (0205 L §2.3): once a group's
    worktree is unregistered, ``handle("write", ...)`` must 409 through the real routing
    path, never silently fall back to the base checkout.

    The real resolver's first reaction to an unregistered worktree is NOT to reject -- it
    is ``_resolve_root_for_mutation``'s own documented "one synchronous self-heal
    ensure_worktree retry" (0205 L section 2.3). The directory this test's ``env`` fixture
    really provisioned is still healthy on disk, so merely flipping
    ``group_git_state.worktree_registered`` to 0 lets that real self-heal silently
    re-register it and the write would still land -- which was confirmed by actually running
    this exact test body first: it got 200, not 409 (self-check run
    scr_61b5f2f1b3d647f7b19ddd20). To make the self-heal retry itself genuinely fail (the
    only way ``_resolve_root_for_mutation`` really reaches its 409 branch), git is made
    briefly unavailable to ``ensure_worktree`` -- the same real E1 path ``worktree.py``'s
    ``ensure_worktree`` already fails closed on when the git binary cannot be found."""
    env.store._execute(
        "UPDATE group_git_state SET worktree_registered = 0 WHERE group_id = ?", ["g_rt1"]
    )
    from modules.flow_gate.services import git_service
    monkeypatch.setattr(git_service, "git_available", lambda: False)
    token = "raw-rt-token-unreg"
    env.make_worker_token(token, "g_rt1", token_id="tok_rt_unreg")

    status, payload = _handle(
        "write", token, {"path": "should_not_land.txt", "content": "x", "mode": "create"}
    )

    assert status == 409, payload
    # the failed self-heal retry really records its own provision_error (git_unavailable),
    # so the real resolver's cause is "provision_failed", not a bare "worktree_missing" --
    # confirmed by actually reading the raised _OpError.details rather than assumed.
    assert payload["error"]["details"]["cause"] == "provision_failed", payload
    assert not (env.roots["g_rt1"] / "should_not_land.txt").exists()
