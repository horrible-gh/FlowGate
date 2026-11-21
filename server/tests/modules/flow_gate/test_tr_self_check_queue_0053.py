"""1-B: the Self-check queue -- queued requests, snapshot runs, no G.

Real pieces: a fully migrated SQLite store (migration 201), a throwaway git repository standing
in for the managed worktree (committed, modified, untracked and ignored files), the real
``_admit`` -> ``tr_self_check_queue.enqueue`` path (git probe, snapshot copy), the real queue
claim (capacity lock, reservation, project alternation) and the real executor / process-tree
proof in ``_finish``. Replaced: the project switch, the worktree resolver, program resolution
(the test's own Python), the instance identity and the SSE broadcast. Time rules are driven by
stored stamps and a monkeypatched limit; no test waits for them.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import tr_self_check_runs as runs_db
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services import tr_self_check_queue as queue
from modules.flow_gate.services import tr_self_check_service as service

P, P2 = "p_0053q", "p_0053q2"
G, G2 = "g_0053q_1", "g_0053q2_1"
DOCS = {P: ("d_0053q_a", "d_0053q_b", "d_0053q_c"), P2: ("d_0053q2_a",)}
GROUP_OF = {P: G, P2: G2}
HOST = "node-0053q"
_STORE_MODULES = (connection, runs_db)
_MIGRATIONS = Path(__file__).resolve().parents[3] / "sql" / "migrations"


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_tr_self_check_queue_0053.db")


def _seed(store) -> None:
    now = datetime.now(timezone.utc).isoformat()
    store._execute("DELETE FROM tr_self_check_runs")
    for project, docs in DOCS.items():
        gid = GROUP_OF[project]
        store._execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                       "VALUES (?, ?, 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
                       [project, "P " + project, now, now])
        store._execute("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
                       "VALUES (?, ?, 'default', 'G', 'OPEN', ?, ?) ON CONFLICT(group_id) DO NOTHING",
                       [gid, project, now, now])
        for seq, doc in enumerate(docs, 1):
            store._execute("INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title,"
                           " status, branch, revision_no, rejection_history, created_at, updated_at) VALUES"
                           " (?, ?, ?, 'default', 'TR', ?, 't', 'draft', 'main', 1, '[]', ?, ?)"
                           " ON CONFLICT(doc_id) DO NOTHING", [doc, project, gid, seq, now, now])


@pytest.fixture
def store(db_path, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper

    real = object.__new__(connection.FlowGateStore)
    real._db, real._sq = SQLiteWrapper(db_path), None
    for module in _STORE_MODULES:
        monkeypatch.setattr(module, "get_store", lambda real=real: real)
    _seed(real)
    try:
        yield real
    finally:
        try:
            real._db.close()
        except Exception:
            pass


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def worktree(tmp_path):
    root = tmp_path / "worktree"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text("build/\nnode_modules/\n", encoding="utf-8")
    (root / "value.txt").write_text("committed\n", encoding="utf-8")
    (root / "gone.txt").write_text("tracked, deleted later\n", encoding="utf-8")
    (root / "check.py").write_text(
        "import pathlib\nprint('value=' + pathlib.Path('value.txt').read_text().strip())\n"
        "print('extra=' + pathlib.Path('extra.txt').read_text().strip())\n", encoding="utf-8")
    (root / "slow.py").write_text("import time\nprint('slow-started', flush=True)\ntime.sleep(60)\n",
                                  encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    (root / "value.txt").write_text("uncommitted\n", encoding="utf-8")      # modified, not committed
    (root / "extra.txt").write_text("untracked\n", encoding="utf-8")       # untracked, not ignored
    (root / "gone.txt").unlink()                                           # deleted, not committed
    (root / "build").mkdir()
    (root / "build" / "out.bin").write_text("ignored build output\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    return root


@pytest.fixture
def env(store, worktree, tmp_path, monkeypatch):
    """The admission and queue around the real store and repository."""
    events, acquired = [], []
    monkeypatch.setenv(queue.SNAPSHOT_DIR_ENV, str(tmp_path / "snapshots"))
    monkeypatch.setattr(service.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(service, "_worktree", lambda _target: worktree)
    monkeypatch.setattr(service.policy, "resolve_command", lambda program, args, root, path: policy.ResolvedCommand(
        sys.executable, (), "python", "host_path", path))
    monkeypatch.setattr(service.policy, "host_capacity", lambda host=None: (2, 0))
    monkeypatch.setattr(service, "_emit", lambda row: events.append(dict(row)) if row else None)
    # The queue never takes the Group lock or an attempt W: any acquire is recorded and refused.
    monkeypatch.setattr(service.lock_manager, "acquire",
                        lambda *a, **k: acquired.append((a, k)) or SimpleNamespace(ok=False, kind="busy"))
    monkeypatch.setattr(service.lock_manager, "acquire_group",
                        lambda *a, **k: acquired.append((a, k)) or (_ for _ in ()).throw(AssertionError("G")))
    from modules.flow_gate.services.git import instance_registry
    monkeypatch.setattr(instance_registry, "node_key", lambda: HOST)
    monkeypatch.setattr(instance_registry, "current_instance_id", lambda: "inst_0053q")

    def admit(doc, project=P, program="python", args=("check.py",), **extra):
        target = {"project_id": project, "group_id": GROUP_OF[project], "tr_doc_id": doc}
        return service._admit(target, {"program": program, "args": list(args), **extra}, None)

    return SimpleNamespace(admit=admit, events=events, acquired=acquired, root=worktree, store=store)


@pytest.fixture
def thread_stores(store, db_path, monkeypatch):
    """One SQLite connection per thread (as the live backend opens one per call), so concurrent
    requests race on the real store's locks instead of sharing one connection."""
    from sqloader.sqlite3 import SQLiteWrapper

    local, opened = threading.local(), []

    def _thread_store():
        current = getattr(local, "store", None)
        if current is None:
            current = object.__new__(connection.FlowGateStore)
            current._db, current._sq = SQLiteWrapper(db_path), None
            local.store = current
            opened.append(current)
        return current

    for module in _STORE_MODULES:
        monkeypatch.setattr(module, "get_store", _thread_store)
    yield store
    for current in opened:
        try:
            current._db.close()
        except Exception:
            pass


def _row(run_id):
    return runs_db.get_run(run_id)


def _stamp(store, run_id, column, value):
    store._execute(f"UPDATE tr_self_check_runs SET {column} = ? WHERE self_check_run_id = ?", [value, run_id])


# ── completion criteria 1 and 2: two requests of one Group queue and run in order, no G ──

def test_two_requests_of_one_group_both_queue_without_409_and_run_in_order(env):
    first = env.admit(DOCS[P][0])
    second = env.admit(DOCS[P][1])
    assert (first["status"], second["status"]) == ("queued", "queued")
    assert first["queue"]["position"] == 1 and second["queue"]["position"] == 2
    assert first["enqueued_at"] and first["started_at"] is None
    assert runs_db.get_active(P, G) is None                                # no Group slot is taken
    assert queue.dispatch_once() is True
    done_first = _row(first["self_check_run_id"])
    assert done_first["status"] == "completed" and _row(second["self_check_run_id"])["status"] == "queued"
    assert queue.dispatch_once() is True
    done_second = _row(second["self_check_run_id"])
    assert done_second["status"] == "completed"
    assert done_first["queue_claimed_at"] < done_second["queue_claimed_at"]
    assert queue.dispatch_once() is False                                  # nothing left
    assert env.acquired == []                                              # never a G / W acquire
    assert done_first["source_lock_holder"] is None


def test_the_run_tests_the_snapshot_taken_at_enqueue_not_the_later_worktree(env):
    row = _row(env.admit(DOCS[P][0])["self_check_run_id"])
    tree = Path(row["snapshot_path"]) / queue.TREE_DIR
    assert (tree / "value.txt").read_text().strip() == "uncommitted"      # uncommitted change kept
    assert (tree / "extra.txt").is_file()                                 # untracked file kept
    assert not (tree / "gone.txt").exists()                               # deleted file left out
    assert not (tree / "build").exists() and not (tree / ".git").exists()  # ignored output, no .git
    assert (tree / "node_modules").is_symlink()                           # dependency folder linked
    (env.root / "value.txt").write_text("written after the request\n", encoding="utf-8")
    assert queue.dispatch_once()
    done = _row(row["self_check_run_id"])
    assert (done["status"], done["exit_code"]) == ("completed", 0), done
    assert "value=uncommitted" in done["stdout_tail"] and "extra=untracked" in done["stdout_tail"]
    assert done["source_changed_during_run"] is False and done["worktree_state_changed"] is False
    assert not Path(row["snapshot_path"]).exists()                        # removed once ended
    assert done["cpu_reservation"] == 1 and runs_db.host_reservations(HOST, 2) == (0, 0)


def test_the_group_lock_stays_free_while_a_queue_run_executes(env):
    from modules.flow_gate.services.git import lock_manager
    row = env.admit(DOCS[P][0], args=("slow.py",))
    worker = threading.Thread(target=queue.dispatch_once, daemon=True)
    worker.start()
    try:
        for _ in range(200):
            if _row(row["self_check_run_id"])["status"] == "running":
                break
            time.sleep(0.05)
        assert _row(row["self_check_run_id"])["status"] == "running"
        # While it runs: no G holder, nothing that closes the Group, and another request queues.
        assert env.acquired == []
        assert lock_manager.group_recovery_check(P, G) is False
        assert runs_db.get_active(P, G) is None
        other = env.admit(DOCS[P][1])
        assert other["status"] == "queued"
    finally:
        queue.force_stop(row["self_check_run_id"])
        worker.join(timeout=30)
    assert not worker.is_alive()


# ── queue rules ──────────────────────────────────────────────────────────────

def test_a_new_request_of_the_same_card_cancels_its_waiting_one_but_not_a_started_one(env):
    old = _row(env.admit(DOCS[P][0])["self_check_run_id"])
    new = env.admit(DOCS[P][0])
    gone = _row(old["self_check_run_id"])
    assert (gone["status"], gone["error_code"]) == ("cancelled", queue.SUPERSEDED)
    assert not Path(old["snapshot_path"]).exists()
    claimed = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    assert claimed["self_check_run_id"] == new["self_check_run_id"] and claimed["status"] == "pending"
    newest = env.admit(DOCS[P][0])
    assert _row(new["self_check_run_id"])["status"] == "pending"           # started: left to run
    assert _row(newest["self_check_run_id"])["status"] == "queued"
    other_card = env.admit(DOCS[P][1])
    assert _row(newest["self_check_run_id"])["status"] == "queued"         # another card: untouched
    assert other_card["status"] == "queued"


def test_the_wait_limit_requeues_once_then_fails(env, monkeypatch):
    monkeypatch.setattr(queue, "wait_limit_sec", lambda: 5)
    rid = env.admit(DOCS[P][0])["self_check_run_id"]
    row = _row(rid)
    _stamp(env.store, rid, "enqueued_at", queue._stamp(-60))
    assert queue.maintain()["requeued"] == 1
    again = _row(rid)
    assert (again["status"], again["queue_requeue_count"]) == ("queued", 1)
    assert again["enqueued_at"] > queue._stamp(-5) and again["queue_order"] == row["queue_order"]
    assert queue.maintain()["requeued"] == 0                               # its clock restarted
    _stamp(env.store, rid, "enqueued_at", queue._stamp(-60))
    assert queue.maintain()["expired"] == 1
    failed = _row(rid)
    assert (failed["status"], failed["error_code"]) == ("failed", queue.WAIT_EXPIRED)
    assert not Path(row["snapshot_path"]).exists()


def test_queued_requests_survive_a_restart_and_an_interrupted_run_is_queued_again(env, monkeypatch):
    waiting = env.admit(DOCS[P][0])
    interrupted = _row(env.admit(DOCS[P][1])["self_check_run_id"])
    # The "previous process" claimed and started the second one, then died.
    store = env.store
    claimed = runs_db.claim_next_queued("inst_dead", queue._stamp(60), HOST, (2, 0))
    assert claimed["self_check_run_id"] == waiting["self_check_run_id"]
    store._execute("UPDATE tr_self_check_runs SET status = 'queued', queue_lease_owner = NULL,"
                   " owner_instance_id = NULL, cpu_reservation = NULL, started_at = NULL"
                   " WHERE self_check_run_id = ?", [waiting["self_check_run_id"]])
    store._execute("UPDATE tr_self_check_runs SET status = 'running', owner_instance_id = 'inst_dead',"
                   " queue_lease_owner = 'inst_dead', cpu_reservation = 1, started_at = created_at"
                   " WHERE self_check_run_id = ?", [interrupted["self_check_run_id"]])
    # Restart: this server proves the old owner dead and the tree empty (the existing rules).
    monkeypatch.setattr(service.proof, "owner_server_state",
                        lambda _owner: SimpleNamespace(gone=True, reason=None, state="dead", detail={}))
    monkeypatch.setattr(service.proof, "release_verdict",
                        lambda _row, terminate=True: SimpleNamespace(decision=service.proof.RELEASE, reason=None))
    service.recover()
    back = _row(interrupted["self_check_run_id"])
    assert (back["status"], back["recovery_state"], back["owner_instance_id"]) == ("queued", "none", None)
    assert back["snapshot_path"] == interrupted["snapshot_path"] and Path(back["snapshot_path"]).is_dir()
    assert _row(waiting["self_check_run_id"])["status"] == "queued"        # the waiting one stayed
    assert queue.dispatch_once() and queue.dispatch_once()
    for run in (waiting, interrupted):
        done = _row(run["self_check_run_id"])
        assert (done["status"], done["exit_code"]) == ("completed", 0), done
        assert "value=uncommitted" in done["stdout_tail"]


def test_a_claim_whose_lease_lapsed_before_any_spawn_goes_back_to_the_queue(env):
    row = env.admit(DOCS[P][0])
    claimed = runs_db.claim_next_queued("inst_gone", "2000-01-01T00:00:00+09:00", HOST, (2, 0))
    assert claimed["status"] == "pending" and runs_db.host_reservations(HOST, 2) == (1, 0)
    assert queue.maintain()["released"] == 1
    back = _row(row["self_check_run_id"])
    assert (back["status"], back["queue_lease_owner"], back["cpu_reservation"]) == ("queued", None, None)
    assert queue.dispatch_once() and _row(row["self_check_run_id"])["status"] == "completed"


def test_projects_alternate_and_the_oldest_request_goes_first(env):
    order = []
    for project, doc in ((P, DOCS[P][0]), (P, DOCS[P][1]), (P2, DOCS[P2][0]), (P, DOCS[P][2])):
        order.append(env.admit(doc, project=project)["self_check_run_id"])
    a1, a2, b1, a3 = order
    claimed = []
    while True:
        row = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (8, 0))
        if row is None:
            break
        claimed.append(row["self_check_run_id"])
        assert runs_db.finish_completed(row["self_check_run_id"], exit_code=0)
        time.sleep(0.002)
    assert claimed == [a1, b1, a2, a3]


def test_concurrent_requests_of_one_card_leave_exactly_the_newest_one_waiting(env, thread_stores, monkeypatch):
    """rev2: the insert and the supersede of one card are one transaction on the capacity row,
    and only requests older than the new one are cancelled. Requests of one card released
    together at the insert (the interleaving where each used to cancel the other) end with
    exactly one waiting -- the one stored last -- and every other one superseded."""
    real_create, real_probe = runs_db.create_queued, service._probe
    probe_lock = threading.Lock()
    monkeypatch.setattr(service, "_probe", lambda root: _locked(probe_lock, real_probe, root))
    previous = []
    for _round in range(3):
        barrier, outcomes, errors = threading.Barrier(4), [], []

        def at_insert(*a, **k):
            barrier.wait(timeout=30)                   # every request reaches its insert together
            return real_create(*a, **k)

        monkeypatch.setattr(runs_db, "create_queued", at_insert)

        def request():
            try:
                outcomes.append(env.admit(DOCS[P][0])["self_check_run_id"])
            except Exception as exc:                   # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=request) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert errors == [] and len(outcomes) == 4
        rows = [_row(rid) for rid in outcomes]
        waiting = [r for r in rows if r["status"] == "queued"]
        assert len(waiting) == 1, [(r["status"], r["error_code"]) for r in rows]
        survivor = waiting[0]
        assert survivor["queue_order"] == max(r["queue_order"] for r in rows)    # the newest one
        for r in rows:
            if r is not survivor:
                assert (r["status"], r["error_code"]) == ("cancelled", queue.SUPERSEDED)
                assert r["queue_order"] < survivor["queue_order"]                # only older ones
                assert not Path(r["snapshot_path"]).exists()
        for rid in previous:                                                     # earlier rounds too
            assert (_row(rid)["status"], _row(rid)["error_code"]) == ("cancelled", queue.SUPERSEDED)
        previous = outcomes
    monkeypatch.setattr(runs_db, "create_queued", real_create)
    assert len(runs_db.queued_of_card(queue.card_key(tr_doc_id=DOCS[P][0]))) == 1


def _locked(lock, fn, *args):
    with lock:
        return fn(*args)






def _queued_row(project, doc, n):
    rid = f"scr_0053fair_{project}_{n:04d}"
    return runs_db.create_queued(
        project, GROUP_OF[project], doc, None, policy.POLICY_VERSION, "python", ["check.py"],
        sys.executable, "python", "host_path", ".", 60, ["PATH"], run_id=rid,
        snapshot_path=str(Path("unused") / rid), queue_key=f"tr:{doc}:{n}")












# ── server functions for 1-C / 1-D ───────────────────────────────────────────









# quotas apply at spawn, not admission/claim, and survive reconnects.
