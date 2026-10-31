"""flowgate.v02.0114 T#4: the host-wide Self-check resource cap (D 0006 3.5 (1)(4)(5)(6), 8-5,
9.2-7 (a)(b)(c)(d)(g)(h)).

Real pieces: a fully migrated SQLite store (migration 164: ``cpu_reservation``,
``cpu_share_requested``, ``tr_self_check_capacity_lock``), the real ``create_pending``
transaction, and ``tr_self_check_service._admit`` with only the worktree, Git probe, Group lock
and worker thread replaced. The numbers are the D 9.2-7 examples: cap 4, serial reserve 1.
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import tr_self_check_runs as runs_db
from modules.flow_gate.services import api_server_tools as tools
from modules.flow_gate.services import help_catalog
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services import tr_self_check_service as service

P = "p_0114c"
GROUPS = [f"g_0114c_{i}" for i in range(12)]
HOST, OTHER_HOST = "node-a", "node-b"
CAP = (4, 1)
_MIGRATIONS = Path(__file__).resolve().parents[3] / "sql" / "migrations"
_STORE_MODULES = (connection, runs_db)


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_tr_self_check_capacity_0114.db")


def _seed(store) -> None:
    now = datetime.now(timezone.utc).isoformat()
    store._execute("DELETE FROM tr_self_check_runs WHERE project_id = ?", [P])
    store._execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                   "VALUES (?, 'P 0114c', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING", [P, now, now])
    for seq, gid in enumerate(GROUPS, 1):
        store._execute("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
                       "VALUES (?, ?, 'default', 'G', 'OPEN', ?, ?) ON CONFLICT(group_id) DO NOTHING",
                       [gid, P, now, now])
        store._execute("INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status,"
                       " branch, revision_no, rejection_history, created_at, updated_at) VALUES (?, ?, ?, 'default',"
                       " 'TR', ?, 't', 'draft', 'main', 1, '[]', ?, ?) ON CONFLICT(doc_id) DO NOTHING",
                       [_doc(gid), P, gid, seq, now, now])


def _doc(group_id: str) -> str:
    return "d_" + group_id


@pytest.fixture
def store(db_path):
    from sqloader.sqlite3 import SQLiteWrapper

    real = object.__new__(connection.FlowGateStore)
    real._db, real._sq = SQLiteWrapper(db_path), None
    previous = [module.get_store for module in _STORE_MODULES]
    for module in _STORE_MODULES:
        module.get_store = lambda real=real: real
    _seed(real)
    try:
        yield real
    finally:
        for module, value in zip(_STORE_MODULES, previous):
            module.get_store = value
        try:
            real._db.close()
        except Exception:
            pass


@pytest.fixture
def thread_stores(store, db_path, monkeypatch):
    """One SQLite connection per thread (as the live backend opens one per call): two of them
    stand for two server instances of one host sharing the store."""
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


def _pending(group_id, *, share=1, host=HOST, capacity=CAP, program="pytest", instance="inst_a"):
    return runs_db.create_pending(P, group_id, _doc(group_id), None, policy.POLICY_VERSION, program, ["-q"],
                                  f"/bin/{program}", program, "host_path", ".", 60, ["PATH"],
                                  owner_instance_id=instance, host_node_key=host,
                                  host_capacity=capacity, cpu_share_requested=share)


def _finish(run_id):
    assert runs_db.finish_completed(run_id, exit_code=0, timed_out=False, stdout_tail="", stderr_tail="")


def _sum(host=HOST, limit=CAP[0]):
    return runs_db.host_reservations(host, limit)


def _rows(store, group_id):
    return store._fetch_one("SELECT COUNT(*) AS n FROM tr_self_check_runs WHERE group_id = ?", [group_id])["n"]


# ── decision rules (D 3.5 (4)) ───────────────────────────────────────────────

@pytest.mark.parametrize("total,desired,expected", [
    (0, 1, 1), (0, 4, 3), (0, 2, 2), (1, 4, 2), (2, 4, 1), (3, 4, 1), (3, 1, 1), (4, 1, None), (5, 1, None),
])
def test_reservation_is_one_plus_what_the_serial_reserve_leaves(total, desired, expected):
    assert runs_db.decide_reservation(4, 1, total, desired) == expected


def test_policy_cap_is_the_processor_count_at_most_and_the_share_is_validated(monkeypatch):
    monkeypatch.setattr(policy, "processor_ids", lambda: list(range(16)))
    assert policy.host_capacity({}) == (4, 1)                                  # default min(4, processors)
    assert policy.host_capacity({policy.HOST_LIMIT_ENV: "64"}) == (16, 1)      # above processors -> processors
    assert policy.host_capacity({policy.HOST_LIMIT_ENV: "1"}) == (1, 0)        # reserve stays inside the cap
    assert policy.host_capacity({policy.SERIAL_RESERVE_ENV: "0"}) == (4, 0)
    monkeypatch.setattr(policy, "processor_ids", lambda: [0, 1])
    assert policy.host_capacity({}) == (2, 1)
    assert policy.validate_share(None) == 1
    for good in (1, 2, policy.SHARE_MAX):
        assert policy.validate_share(good) == good
    for bad in (0, -1, policy.SHARE_MAX + 1, True, 2.0, "2"):
        with pytest.raises(policy.PolicyError, match="selfcheck_invalid_share"):
            policy.validate_share(bad)
    assert policy.POLICY_VERSION == "tr-selfcheck-v2"


def test_the_slot_pool_lives_in_the_runtime_folder_with_one_file_per_slot(tmp_path):
    env = policy.slot_pool_env(tmp_path, 3)
    assert env == {policy.SLOT_DIR_ENV: str(tmp_path / "slots"), policy.SLOTS_ENV: "3"}
    assert sorted(p.name for p in (tmp_path / "slots").iterdir()) == ["slot-0", "slot-1", "slot-2"]


# ── 9.2-7 (a): a share-1 run is inside the cap too ───────────────────────────

def test_7a_serial_and_non_pytest_runs_reserve_and_are_refused_when_the_host_is_full(store):
    x = _pending(GROUPS[0], share=4)
    assert (x["cpu_reservation"], x["cpu_share_requested"]) == (3, 4)          # 4 wished, 3 granted
    assert _sum() == (3, 0)
    y = _pending(GROUPS[1])
    assert y["cpu_reservation"] == 1 and _sum() == (4, 0)
    for group, program in ((GROUPS[2], "pytest"), (GROUPS[3], "mypy"), (GROUPS[4], "python")):
        with pytest.raises(runs_db.SelfCheckHostCapacityError) as caught:
            _pending(group, program=program)
        refused = caught.value
        assert (refused.limit, refused.reserved_total, refused.reserved_protected, refused.requested) == (4, 4, 0, 1)
        assert _rows(store, group) == 0                                        # no record
        assert _sum() == (4, 0)                                                # never above the cap
    _finish(x["self_check_run_id"])
    z = _pending(GROUPS[2])
    assert z["cpu_reservation"] == 1 and _sum() == (2, 0)


def test_7a_the_same_share_gets_the_same_reservation_whatever_the_program(store):
    granted = []
    for group, program in zip(GROUPS, ("pytest", "python", "mypy")):
        row = _pending(group, share=2, capacity=(8, 1))
        granted.append(row["cpu_reservation"])
        _finish(row["self_check_run_id"])
    assert granted == [2, 2, 2]


@pytest.fixture
def admission(store, monkeypatch, tmp_path):
    """``_admit`` on the real store: only the worktree, probe, Group lock and worker are fakes."""
    released, runtimes, started = [], [], []
    monkeypatch.setattr(service.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(service, "_worktree", lambda _t: tmp_path)
    monkeypatch.setattr(service, "_probe", lambda _root: {"head": "h", "branch": "b", "refs_hash": "0" * 64,
                                                          "index_hash": "0" * 64, "status_hash": "0" * 64,
                                                          "index_lock": False})
    monkeypatch.setattr(service.policy, "controlled_path", lambda root, host: ("PATH", None))
    monkeypatch.setattr(service.policy, "resolve_command", lambda *a: SimpleNamespace(
        executable="pytest", name="pytest", origin="host_path", argv_prefix=[]))
    monkeypatch.setattr(service.policy, "validate_cwd", lambda root, rel: tmp_path)
    monkeypatch.setattr(service.policy, "host_capacity", lambda: CAP)
    real_mkdtemp = service.tempfile.mkdtemp
    monkeypatch.setattr(service.tempfile, "mkdtemp",
                        lambda prefix: runtimes.append(Path(real_mkdtemp(prefix=prefix))) or str(runtimes[-1]))
    monkeypatch.setattr(service.lock_manager, "new_context",
                        lambda *_: SimpleNamespace(ctx_id="ctx_0114c", on_lock_lost=None))
    monkeypatch.setattr(service.lock_manager, "acquire", lambda *a, **k: SimpleNamespace(ok=True, lock_key="G:" + k["group_id"]))
    monkeypatch.setattr(service.lock_manager, "release", lambda ctx, key: released.append(key))
    monkeypatch.setattr(service, "_emit", lambda row: None)
    from modules.flow_gate.services.git import instance_registry
    monkeypatch.setattr(instance_registry, "node_key", lambda: HOST)
    monkeypatch.setattr(instance_registry, "current_instance_id", lambda: "inst_a")

    class _Worker:
        def __init__(self, *, target, args, daemon, name):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(service.threading, "Thread", _Worker)

    def admit(group_id, request):
        target = {"project_id": P, "group_id": group_id, "tr_doc_id": _doc(group_id)}
        return service._admit(target, request, None)

    return SimpleNamespace(admit=admit, released=released, runtimes=runtimes, started=started)


def test_7a_a_refused_admission_is_409_with_the_numbers_and_leaves_no_row_lock_or_folder(store, admission):
    x = admission.admit(GROUPS[0], {"program": "pytest", "parallel_share": 4})
    assert x["reservation"] == {"requested": 4, "granted": 3, "limit": 4, "serial_reserve": 1, "cpu_limit": 3,
                                "lowered": "host_capacity"}
    assert x["cpu_reservation"] == 3 and x["policy_version"] == "tr-selfcheck-v2"
    env = admission.started[0][9]
    assert env[policy.SLOTS_ENV] == "3" and Path(env[policy.SLOT_DIR_ENV]).parent == admission.runtimes[0]
    admission.admit(GROUPS[1], {"program": "pytest"})
    for group, program in ((GROUPS[2], "pytest"), (GROUPS[3], "mypy")):
        with pytest.raises(service.SelfCheckError) as caught:
            admission.admit(group, {"program": program})
        assert (caught.value.status, caught.value.code) == (409, "selfcheck_host_capacity_exhausted")
        assert caught.value.details == {"limit": 4, "reserved_total": 4, "reserved_protected": 0, "requested": 1,
                                        "desired_share": 1, "retry": "retry_later"}
        assert _rows(store, group) == 0
        assert admission.released[-1] == "G:" + group                        # the Group lock is let go
        assert not admission.runtimes[-1].exists()                           # and the runtime folder
    assert len(admission.started) == 2                                       # no worker for a refusal
    # (h) the same Group's second request stays "already running", a different code
    with pytest.raises(service.SelfCheckError) as caught:
        admission.admit(GROUPS[0], {"program": "pytest"})
    assert (caught.value.status, caught.value.code) == (409, "selfcheck_already_running")
    _finish(x["self_check_run_id"])
    again = admission.admit(GROUPS[2], {"program": "pytest"})
    assert again["cpu_reservation"] == 1


def test_a_slot_pool_that_cannot_be_prepared_ends_the_committed_run_and_frees_everything(store, admission, monkeypatch):
    """The row is committed before the pool exists and no worker is there yet to end it: a pool
    that cannot be made (permission / disk error) fails the run in ``_admit`` itself -- terminal
    record (the reservation leaves the host's sum), keeper stopped, G released, runtime removed."""
    real_pool = policy.slot_pool_env

    def broken(run_root, slots):
        raise PermissionError("injected: slot pool cannot be created")

    stopped = []
    real_stop = service._LockKeeper.stop
    monkeypatch.setattr(service._LockKeeper, "stop", lambda self: stopped.append(self.run_id) or real_stop(self))
    monkeypatch.setattr(service.policy, "slot_pool_env", broken)
    with pytest.raises(service.SelfCheckError) as caught:
        admission.admit(GROUPS[0], {"program": "pytest", "parallel_share": 2})
    assert (caught.value.status, caught.value.code) == (500, "selfcheck_internal_error")
    rows = store._fetch_all("SELECT * FROM tr_self_check_runs WHERE group_id = ?", [GROUPS[0]])
    assert len(rows) == 1
    run = rows[0]
    assert (run["status"], run["error_code"], run["active_key"]) == ("failed", "selfcheck_internal_error", None)
    assert run["cpu_reservation"] == 2 and _sum() == (0, 0)                  # the reservation is free again
    assert stopped == [run["self_check_run_id"]]                             # keeper
    assert admission.released == ["G:" + GROUPS[0]]                          # G
    assert not admission.runtimes[-1].exists()                               # runtime folder
    assert admission.started == []                                           # nothing ran
    monkeypatch.setattr(service.policy, "slot_pool_env", real_pool)
    again = admission.admit(GROUPS[0], {"program": "pytest", "parallel_share": 4})
    assert again["cpu_reservation"] == 3                                     # the Group and the host take the next one


def test_7h_already_running_comes_first_and_stays_distinct_inside_the_transaction(store):
    first = _pending(GROUPS[0], share=4)
    _pending(GROUPS[1])                                                      # host now full
    with pytest.raises(runs_db.SelfCheckAlreadyRunningError) as caught:
        _pending(GROUPS[0])
    assert caught.value.existing_run_id == first["self_check_run_id"]
    with pytest.raises(runs_db.SelfCheckHostCapacityError):
        _pending(GROUPS[2])


def test_a_share_is_part_of_the_request_contract(store, admission):
    with pytest.raises(service.SelfCheckError) as caught:
        admission.admit(GROUPS[0], {"program": "pytest", "parallel_share": 0})
    assert (caught.value.status, caught.value.code) == (422, "selfcheck_invalid_share")
    with pytest.raises(service.SelfCheckError) as caught:
        service._validate_request({"program": "pytest", "share": 2})
    assert caught.value.code == "selfcheck_invalid_request"
    big = admission.admit(GROUPS[0], {"program": "pytest", "parallel_share": 9})
    assert big["reservation"]["granted"] == 3 and big["cpu_share_requested"] == 9


# ── 9.2-7 (b): the last reservation is taken once ────────────────────────────

def test_7b_two_requests_for_the_last_reservation_admit_exactly_one(thread_stores):
    holder = _pending(GROUPS[0], share=3, capacity=(4, 0))
    assert holder["cpu_reservation"] == 3
    for attempt in range(5):
        barrier, outcomes = threading.Barrier(2), []

        def request(group, instance):
            barrier.wait()
            try:
                outcomes.append(("ok", _pending(group, capacity=(4, 0), instance=instance)))
            except runs_db.SelfCheckHostCapacityError as exc:
                outcomes.append(("refused", exc))

        groups = GROUPS[1 + 2 * attempt:3 + 2 * attempt]
        threads = [threading.Thread(target=request, args=(g, inst)) for g, inst in zip(groups, ("inst_a", "inst_b"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert sorted(kind for kind, _ in outcomes) == ["ok", "refused"], outcomes
        assert runs_db.host_reservations(HOST, 4) == (4, 0)
        _finish(next(row for kind, row in outcomes if kind == "ok")["self_check_run_id"])


# ── 9.2-7 (c): restart, protected runs ───────────────────────────────────────

def test_7c_a_protected_run_of_a_previous_instance_keeps_its_reservation_until_recovery_ends_it(store):
    a = _pending(GROUPS[0], share=3, instance="inst_before_restart")
    assert a["cpu_reservation"] == 3
    assert runs_db.mark_recovering(a["self_check_run_id"])
    assert runs_db.mark_recovery_incomplete(a["self_check_run_id"], "tree_unproven")
    b = _pending(GROUPS[1], share=4, instance="inst_after_restart")       # A's 3 is in the sum
    assert b["cpu_reservation"] == 1 and _sum() == (4, 3)
    with pytest.raises(runs_db.SelfCheckHostCapacityError) as caught:
        _pending(GROUPS[2], instance="inst_after_restart")
    assert (caught.value.reserved_total, caught.value.reserved_protected) == (4, 3)
    # the existing recovery path writes the terminal state; that alone frees the reservation
    assert runs_db.retry_recovery_incomplete(a["self_check_run_id"])
    assert runs_db.finish_recovered_interrupted(a["self_check_run_id"])
    assert _pending(GROUPS[2], instance="inst_after_restart")["cpu_reservation"] == 1


# ── 9.2-7 (d): what a host is ────────────────────────────────────────────────

def test_7d_other_hosts_do_not_count_unknown_hosts_and_unreserved_runs_count_in_full(store):
    other = _pending(GROUPS[0], share=4, host=OTHER_HOST)
    assert other["cpu_reservation"] == 3 and _sum() == (0, 0)
    no_host = _pending(GROUPS[1], host=None, capacity=None)               # host unknown, no reservation
    assert no_host["cpu_reservation"] is None and no_host["host_node_key"] is None
    assert _sum() == (4, 0)                                                # counted here, as the whole cap
    with pytest.raises(runs_db.SelfCheckHostCapacityError):
        _pending(GROUPS[2])
    _finish(no_host["self_check_run_id"])
    known = _pending(GROUPS[2])
    store._execute("UPDATE tr_self_check_runs SET cpu_reservation = NULL WHERE self_check_run_id = ?",
                   [known["self_check_run_id"]])                           # a run from before step 4
    assert _sum() == (4, 0)
    _finish(known["self_check_run_id"])
    # the run row keeps its own copy of the host: no instance registration row is read
    kept = _pending(GROUPS[3], instance="inst_never_registered")
    assert store._fetch_one("SELECT host_node_key FROM tr_self_check_runs WHERE self_check_run_id = ?",
                            [kept["self_check_run_id"]])["host_node_key"] == HOST
    assert _sum() == (1, 0)
    assert runs_db.host_reservations(None, 4)[0] >= 1 + 3                  # unknown requester: every run counts


def test_the_capacity_lock_row_is_required(store):
    store._execute("DELETE FROM tr_self_check_capacity_lock")
    try:
        with pytest.raises(RuntimeError, match="capacity_lock"):
            _pending(GROUPS[0])
        assert _rows(store, GROUPS[0]) == 0
    finally:
        store._execute("INSERT INTO tr_self_check_capacity_lock (lock_id, updated_at) VALUES (?, NULL)",
                       [runs_db.CAPACITY_LOCK_ID])


@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
def test_migration_164_exists_for_every_dialect(dialect):
    text = (_MIGRATIONS / dialect / "164_tr_self_check_host_capacity.sql").read_text(encoding="utf-8")
    for needle in ("ADD COLUMN cpu_reservation", "ADD COLUMN cpu_share_requested",
                   "CREATE TABLE tr_self_check_capacity_lock", "'host_capacity'"):
        assert needle in text


# ── 9.2-7 (g): the invariant under random concurrent admissions and ends ─────

def test_7g_random_concurrent_admissions_and_ends_never_exceed_the_cap(thread_stores):
    rng = random.Random(1140)
    stop, violations, refusals, admitted = threading.Event(), [], [], []
    lock = threading.Lock()

    def monitor():
        while not stop.is_set():
            total, _protected = runs_db.host_reservations(HOST, 4)
            if total > 4:
                violations.append(total)
            time.sleep(0.005)

    def actor(index, instance):
        local = random.Random(rng.random())
        for _ in range(25):
            group = GROUPS[local.randrange(len(GROUPS))]
            try:
                row = _pending(group, share=local.choice((1, 2, 4, 4)), instance=instance,
                               program=local.choice(("pytest", "python", "mypy")))
            except runs_db.SelfCheckAlreadyRunningError:
                continue
            except runs_db.SelfCheckHostCapacityError as exc:
                with lock:
                    refusals.append(exc)
                continue
            with lock:
                admitted.append(row)
            assert 1 <= row["cpu_reservation"] <= 4
            time.sleep(local.random() * 0.03)
            _finish(row["self_check_run_id"])

    watcher = threading.Thread(target=monitor)
    watcher.start()
    actors = [threading.Thread(target=actor, args=(i, ("inst_a", "inst_b")[i % 2])) for i in range(4)]
    for thread in actors:
        thread.start()
    for thread in actors:
        thread.join(120)
    stop.set()
    watcher.join(10)
    assert not violations
    assert admitted
    ids = {row["self_check_run_id"] for row in admitted}
    stored = thread_stores._fetch_all("SELECT self_check_run_id FROM tr_self_check_runs WHERE project_id = ?", [P])
    assert {r["self_check_run_id"] for r in stored} == ids                  # refusals wrote nothing
    assert runs_db.host_reservations(HOST, 4) == (0, 0)


# ── request contract, help and tool schema (D 2.3, 7.2) ──────────────────────

def test_the_tool_schema_help_and_service_share_the_request_fields():
    schema = tools.SCHEMAS["run_self_check"]
    assert schema["properties"]["parallel_share"] == {"type": "integer", "minimum": 1}
    assert tuple(help_catalog.SELF_CHECK_REQUEST_FIELDS) == tuple(schema["properties"])
    assert service.REQUEST_FIELDS == frozenset(help_catalog.SELF_CHECK_REQUEST_FIELDS)
    assert "selfcheck_host_capacity_exhausted" in tools.DESCRIPTIONS["run_self_check"]


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_help_explains_the_cap_the_share_and_the_retry(locale):
    ctx = {"locale": locale, "action_scope": "new", "principal_kind": "worker", "token_rec": None}
    content = help_catalog._content_tr_self_check(ctx)
    parallel = content["parallel"]
    assert parallel["request_field"] == "parallel_share"
    assert parallel["refusal_code"] == "selfcheck_host_capacity_exhausted"
    assert set(parallel) >= {"reservation", "guarantee", "pytest", "serial_only", "refusal", "verification"}
    assert "--dist loadfile" in parallel["pytest"] and "409" in parallel["refusal"]
    codes = {row["code"] for row in content["errors"]}
    assert {"selfcheck_host_capacity_exhausted", "selfcheck_invalid_share", "selfcheck_cpu_limit_violation"} <= codes
    field = next(f for f in content["request"]["fields"] if f["name"] == "parallel_share")
    assert (field["type"], field["default"], field["required"]) == ("integer", 1, False)


# ── the executor gets the stored reservation; a POSIX limit violation is a failure ──

def test_the_worker_spawns_with_the_stored_reservation_and_records_a_limit_violation_as_failed(store, monkeypatch):
    import tempfile
    from modules.flow_gate.services import tr_self_check_executor as executor

    row = _pending(GROUPS[0], share=2)
    assert row["cpu_reservation"] == 2
    seen = {}

    class _Control:
        proc = SimpleNamespace(poll=lambda: 0, wait=lambda timeout=None: 0)

        def cancel(self):
            return True

        def close(self):
            pass

    def spawn(argv, cwd, env, timeout, run_id, **kwargs):
        seen.update(kwargs)
        return _Control(), {"process_owner_kind": "posix_supervisor"}

    probe = {"head": "h", "branch": "b", "refs_hash": "0" * 64, "index_hash": "0" * 64, "status_hash": "0" * 64,
             "index_lock": False}
    monkeypatch.setattr(service.executor, "spawn", spawn)
    monkeypatch.setattr(service.executor, "wait", lambda control, timeout, cancelled: executor.ExecutionResult(
        -9, False, False, "out", "err", cpu_limit_violated=True))
    monkeypatch.setattr(service, "_settle_tree", lambda run_id, row, ownership: True)
    monkeypatch.setattr(service, "_probe", lambda root: dict(probe))
    monkeypatch.setattr(service, "_watch_cancel", lambda run_id, control, cancelled: threading.Event())
    monkeypatch.setattr(service, "_emit", lambda row: None)
    ctx = SimpleNamespace(ctx_id="ctx_0114c", lock_lost=False, find_held=lambda key: None)
    command = SimpleNamespace(executable="pytest", argv_prefix=())
    runtime = Path(tempfile.mkdtemp(prefix="flowgate-0114c-"))
    service._finish(row["self_check_run_id"], ctx, "G:x", runtime, probe, runtime, command, [], runtime, {}, 60)
    assert seen["cpu_limit"] == 2
    final = runs_db.get_run(row["self_check_run_id"])
    assert (final["status"], final["error_code"]) == ("failed", "selfcheck_cpu_limit_violation")
    assert final["active_key"] is None and _sum() == (0, 0)                # released by the terminal write
