"""flowgate.v02.0114 T#4: D 0006 9.2-7 (g) through the real run path.

Several Groups and two server instances of one host (one SQLite connection per thread, the
same ``node_key``) admit and end Self-check runs at random and at the same time. Every request
goes through the real ``tr_self_check_service._admit`` (the real ``create_pending`` reservation
transaction, the real runtime folder and slot pool) and every admitted run through the real
worker ``_finish``: ``executor.spawn(cpu_limit=R)`` with its named Job, real processes -- a
``server/tests`` pytest with xdist, a pytest that nests another pytest, a general script with busy
children -- and the real tree proof before the terminal write. Only what needs a live server is
replaced: the project switch, the worktree path, the Git probe, the Group lock manager and the
event emitter.

Checked at every moment by a watcher, and per run afterwards:

- the host's sum of active and protected reservations never exceeds the cap;
- each running tree's processor limit (its Job's affinity, read from the OS) equals the
  reservation stored with its row, and every process that ran a test or a busy loop saw exactly
  that many processors;
- each run's xdist worker processes (outer and nested together) never exceed its reservation;
- every refusal is a 409 that left no row, no Group lock and no runtime folder; every admitted
  run ends terminal with its lock and runtime folder released.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import tr_self_check_runs as runs_db
from modules.flow_gate.services import process_runner
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_result as result_block
from modules.flow_gate.services import tr_self_check_service as service

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the tree's limit is read from its Windows Job")
pytest.importorskip("xdist")

P = "p_0114g"
ACTORS = 5                       # five runs of share >= 1 cannot all fit a cap of 4: refusals happen
GROUPS_PER_ACTOR = 3
RUNS_PER_ACTOR = 2
WINDOW_SECONDS = 150
HOST = "node-g"
CAP = (4, 1)
_TESTS_DIR = Path(__file__).resolve().parents[2]
_STORE_MODULES = (connection, runs_db)

_RECORD = textwrap.dedent('''
    import json, os, random, time
    from pathlib import Path


    def _cpus():
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                             ctypes.POINTER(ctypes.c_size_t)]
        mine, system = ctypes.c_size_t(), ctypes.c_size_t()
        k.GetProcessAffinityMask(k.GetCurrentProcess(), ctypes.byref(mine), ctypes.byref(system))
        return bin(mine.value).count("1")


    def test_record():
        out = Path(os.environ["INV_OUT"]) / os.environ["FG_SELFCHECK_RUN_ID"]
        out.mkdir(parents=True, exist_ok=True)
        begin = time.time()
        time.sleep(random.uniform(0.2, 0.8))
        (out / f"{os.getpid()}-{__name__}.json").write_text(json.dumps({
            "pid": os.getpid(), "worker": os.environ.get("PYTEST_XDIST_WORKER"), "cpus": _cpus(),
            "tree": os.environ.get("INV_TREE", "outer"), "begin": begin, "end": time.time()}))
''')

_NESTED = textwrap.dedent('''
    import json, os, subprocess, sys
    from pathlib import Path


    def test_nested_pytest():
        env = dict(os.environ, INV_TREE="nested")
        for key in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT"):
            env.pop(key, None)
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "xdist.plugin",
                               "-n", "4", "--dist", "loadfile", "tests"], cwd=os.environ["INV_INNER_SERVER"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=240)
        out = Path(os.environ["INV_OUT"]) / os.environ["FG_SELFCHECK_RUN_ID"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "nested.json").write_text(json.dumps({"returncode": proc.returncode, "stdout": proc.stdout}))
        assert proc.returncode == 0, proc.stdout[-2000:]
''')

_BUSY = textwrap.dedent('''
    import ctypes, json, os, subprocess, sys, time
    from ctypes import wintypes
    from pathlib import Path

    out = Path(os.environ["INV_OUT"]) / os.environ["FG_SELFCHECK_RUN_ID"]
    if sys.argv[1] == "child":
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                             ctypes.POINTER(ctypes.c_size_t)]
        mine, system = ctypes.c_size_t(), ctypes.c_size_t()
        k.GetProcessAffinityMask(k.GetCurrentProcess(), ctypes.byref(mine), ctypes.byref(system))
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{os.getpid()}-busy.json").write_text(json.dumps({
            "pid": os.getpid(), "worker": None, "cpus": bin(mine.value).count("1"), "tree": "script",
            "begin": time.time(), "end": time.time()}))
        end = time.monotonic() + float(sys.argv[2])
        while time.monotonic() < end:
            pass
        sys.exit(0)
    children = [subprocess.Popen([sys.executable, __file__, "child", sys.argv[1]]) for _ in range(3)]
    sys.exit(max(child.wait() for child in children))
''')


def _repo(root: Path, files: dict) -> Path:
    """A throwaway repository whose ``server/tests/conftest.py`` is the real one; returns ``server``."""
    tests = root / "server" / "tests"
    tests.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(".test-tmp-*/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (tests / "conftest.py").write_text((_TESTS_DIR / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
    for name, text in files.items():
        (tests / name).write_text(text, encoding="utf-8")
    return root / "server"


def _doc(group_id: str) -> str:
    return "d_" + group_id


GROUPS = [f"g_0114g_{a}_{i}" for a in range(ACTORS) for i in range(GROUPS_PER_ACTOR)]


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_tr_self_check_invariant_0114.db")


@pytest.fixture
def stores(db_path, monkeypatch):
    """One connection per thread: actors of two instances, the workers and the watcher."""
    from sqloader.sqlite3 import SQLiteWrapper

    local, opened, guard = threading.local(), [], threading.Lock()

    def _thread_store():
        current = getattr(local, "store", None)
        if current is None:
            current = object.__new__(connection.FlowGateStore)
            current._db, current._sq = SQLiteWrapper(db_path), None
            local.store = current
            with guard:
                opened.append(current)
        return current

    for module in _STORE_MODULES:
        monkeypatch.setattr(module, "get_store", _thread_store)
    store = _thread_store()
    now = datetime.now(timezone.utc).isoformat()
    store._execute("DELETE FROM tr_self_check_runs WHERE project_id = ?", [P])
    store._execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                   "VALUES (?, 'P 0114g', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING", [P, now, now])
    for seq, gid in enumerate(GROUPS, 1):
        store._execute("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
                       "VALUES (?, ?, 'default', 'G', 'OPEN', ?, ?) ON CONFLICT(group_id) DO NOTHING",
                       [gid, P, now, now])
        store._execute("INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status,"
                       " branch, revision_no, rejection_history, created_at, updated_at) VALUES (?, ?, ?, 'default',"
                       " 'TR', ?, 't', 'draft', 'main', 1, '[]', ?, ?) ON CONFLICT(doc_id) DO NOTHING",
                       [_doc(gid), P, gid, seq, now, now])
    yield store
    for current in opened:
        try:
            current._db.close()
        except Exception:
            pass


def _job_affinity_bits(run_id: str):
    """Processors of the run's named Job, from the OS; None when the Job is gone."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenJobObjectW.restype = wintypes.HANDLE
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
    k.QueryInformationJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    _c, _w, info_type = process_runner._JobBasicLimitInformation.types()
    handle = k.OpenJobObjectW(0x0004, False, proof.job_name(run_id))      # JOB_OBJECT_QUERY
    if not handle:
        return None
    try:
        info = info_type()
        if not k.QueryInformationJobObject(handle, 2, ctypes.byref(info.BasicLimitInformation),
                                           ctypes.sizeof(info.BasicLimitInformation), None):
            return None
        return bin(info.BasicLimitInformation.Affinity).count("1")
    finally:
        k.CloseHandle(handle)


@pytest.fixture
def harness(stores, monkeypatch, tmp_path):
    work, out = tmp_path / "work", tmp_path / "out"
    _repo(work / "pytest", {f"test_part_{i}.py": _RECORD for i in range(4)})
    _repo(work / "nested", {"test_outer_record.py": _RECORD, "test_outer_nested.py": _NESTED})
    inner = _repo(work / "inner", {"test_inner_a.py": _RECORD, "test_inner_b.py": _RECORD})
    (work / "script").mkdir(parents=True)
    (work / "script" / "busy.py").write_text(_BUSY, encoding="utf-8")

    state = SimpleNamespace(lock=threading.Lock(), held={}, spawned={}, runtimes={}, ctx_by_thread={},
                            protected=[], local=threading.local())

    monkeypatch.setattr(service.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(service, "_worktree", lambda _t: work)
    probe = {"head": "h", "branch": "b", "refs_hash": "0" * 64, "index_hash": "0" * 64, "status_hash": "0" * 64,
             "index_lock": False}
    monkeypatch.setattr(service, "_probe", lambda _root: dict(probe))
    monkeypatch.setattr(service, "_emit", lambda row: None)
    monkeypatch.setattr(service.policy, "controlled_path", lambda root, host: (os.environ.get("PATH", ""), None))
    monkeypatch.setattr(service.policy, "resolve_command", lambda program, args, root, path_value: SimpleNamespace(
        executable=sys.executable, name=program, origin="host_path",
        argv_prefix=("-m", "pytest") if program == "pytest" else ()))
    monkeypatch.setattr(service.policy, "validate_cwd", lambda root, rel: Path(root) / rel)
    monkeypatch.setattr(service.policy, "host_capacity", lambda: CAP)
    real_scrubbed = policy.scrubbed_env

    def scrubbed(path_value, run_root, host=None):
        env = real_scrubbed(path_value, run_root, host)
        env.update(PYTHONPATH=str(_TESTS_DIR), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONUTF8="1", INV_OUT=str(out), INV_INNER_SERVER=str(inner))
        return env

    monkeypatch.setattr(service.policy, "scrubbed_env", scrubbed)
    real_mkdtemp = tempfile.mkdtemp

    def mkdtemp(*args, **kwargs):
        made = real_mkdtemp(*args, **kwargs)
        if str(kwargs.get("prefix", "")).startswith("flowgate-scr_"):
            with state.lock:
                state.runtimes.setdefault(threading.get_ident(), []).append(Path(made))
        return made

    monkeypatch.setattr(service.tempfile, "mkdtemp", mkdtemp)

    def new_context(*_args):
        held = set()
        ctx = SimpleNamespace(ctx_id=f"ctx_{uuid.uuid4().hex[:12]}", on_lock_lost=None,
                              lock_lost=False, find_held=lambda key: key if key in held else None)
        with state.lock:
            state.held[ctx.ctx_id] = held
            state.ctx_by_thread[threading.get_ident()] = ctx
        return ctx

    def acquire(kind, project_id, *, group_id, ctx, **_kw):
        key = f"{kind}:{project_id}:{group_id}"
        with state.lock:
            state.held[ctx.ctx_id].add(key)
        return SimpleNamespace(ok=True, lock_key=key, kind=None)

    def release(ctx, key):
        with state.lock:
            state.held[ctx.ctx_id].discard(key)

    def protect(ctx, key, reason):
        with state.lock:
            state.protected.append((ctx.ctx_id, key, reason))

    monkeypatch.setattr(service.lock_manager, "new_context", new_context)
    monkeypatch.setattr(service.lock_manager, "acquire", acquire)
    monkeypatch.setattr(service.lock_manager, "release", release)
    monkeypatch.setattr(service.lock_manager, "protect", protect)
    from modules.flow_gate.services.git import instance_registry
    monkeypatch.setattr(instance_registry, "node_key", lambda: HOST)
    monkeypatch.setattr(instance_registry, "current_instance_id",
                        lambda: getattr(state.local, "instance", "inst_a"))
    real_spawn = executor.spawn

    def spawn(argv, cwd, env, timeout, run_id, **kwargs):
        control, ownership = real_spawn(argv, cwd, env, timeout, run_id, **kwargs)
        bits = _job_affinity_bits(run_id)                                    # the tree's limit, from the OS
        with state.lock:
            state.spawned[run_id] = {"cpu_limit": kwargs.get("cpu_limit"), "job_bits": bits}
        return control, ownership

    monkeypatch.setattr(executor, "spawn", spawn)
    return SimpleNamespace(work=work, out=out, state=state)


def _request(kind: str, share: int, rng: random.Random) -> dict:
    if kind == "pytest":
        workers = rng.choice(("2", "4", "auto"))
        return {"program": "pytest", "parallel_share": share, "cwd": "pytest/server", "timeout_seconds": 240,
                "args": ["-q", "-p", "no:cacheprovider", "-p", "xdist.plugin", "-n", workers, "--dist", "loadfile",
                         "tests"]}
    if kind == "nested":
        return {"program": "pytest", "parallel_share": share, "cwd": "nested/server", "timeout_seconds": 240,
                "args": ["-q", "-p", "no:cacheprovider", "-p", "xdist.plugin", "-n", "2", "--dist", "loadfile",
                         "tests"]}
    seconds = rng.choice(("1.5", "3"))
    timeout = 2 if seconds == "3" and rng.random() < 0.5 else 120               # some end by timeout
    return {"program": "python", "parallel_share": share, "cwd": "script", "timeout_seconds": timeout,
            "args": ["busy.py", seconds]}


def _xdist_peak(records: list[dict]) -> int:
    """Most xdist worker processes of one run seen at once (from each test's own interval)."""
    spans = {}
    for record in records:
        if record.get("worker"):
            begin, end = spans.get(record["pid"], (record["begin"], record["end"]))
            spans[record["pid"]] = (min(begin, record["begin"]), max(end, record["end"]))
    edges = sorted([(b, 1) for b, _e in spans.values()] + [(e, -1) for _b, e in spans.values()],
                   key=lambda edge: (edge[0], edge[1]))
    peak = current = 0
    for _at, step in edges:
        current += step
        peak = max(peak, current)
    return peak


def test_7g_random_concurrent_real_runs_keep_every_invariant(harness, stores):
    state = harness.state
    rng = random.Random(11407)
    stop, sums, limits, outcomes = threading.Event(), [], [], []
    guard = threading.Lock()

    def watch():
        while not stop.is_set():
            total, _protected = runs_db.host_reservations(HOST, CAP[0])
            rows = runs_db.get_store()._fetch_all(
                "SELECT self_check_run_id, cpu_reservation FROM tr_self_check_runs"
                " WHERE project_id = ? AND status = 'running'", [P]) or []
            seen = [(row["self_check_run_id"], row["cpu_reservation"], _job_affinity_bits(row["self_check_run_id"]))
                    for row in rows]
            with guard:
                sums.append(total)
                limits.extend(seen)
            time.sleep(0.02)

    def actor(index: int):
        state.local.instance = ("inst_a", "inst_b")[index % 2]                 # two instances, one host
        local = random.Random(rng.random())
        mine = GROUPS[index * GROUPS_PER_ACTOR:(index + 1) * GROUPS_PER_ACTOR]
        barrier.wait()
        # actor 0 opens with a share-4 request on the empty host; the others join at random.
        # Each actor goes on until it had RUNS_PER_ACTOR runs admitted (or the window closes), its
        # work kind turning pytest -> nested pytest -> script with every admission.
        time.sleep(0 if index == 0 else local.uniform(0.3, 3.0))
        deadline, count = time.monotonic() + WINDOW_SECONDS, 0
        while count < RUNS_PER_ACTOR and time.monotonic() < deadline:
            free = [g for g in mine if runs_db.get_active(P, g) is None]
            if not free:
                time.sleep(0.5)
                continue
            group = local.choice(free)
            kind = ("pytest", "nested", "script")[(index + count) % 3]
            share = 4 if (index, count) == (0, 0) else local.choice((1, 2, 4))
            request = _request(kind, share, local)
            rows_before = runs_db.get_store()._fetch_one(
                "SELECT COUNT(*) AS n FROM tr_self_check_runs WHERE group_id = ?", [group])["n"]
            made_before = len(state.runtimes.get(threading.get_ident(), []))
            try:
                admitted = service._admit({"project_id": P, "group_id": group, "tr_doc_id": _doc(group)},
                                          request, None)
            except service.SelfCheckError as exc:
                ctx = state.ctx_by_thread.get(threading.get_ident())
                rows_after = runs_db.get_store()._fetch_one(
                    "SELECT COUNT(*) AS n FROM tr_self_check_runs WHERE group_id = ?", [group])["n"]
                made = state.runtimes.get(threading.get_ident(), [])[made_before:]
                with guard:
                    outcomes.append({"kind": "refused", "code": exc.code, "status": exc.status, "group": group,
                                     "details": exc.details, "new_rows": rows_after - rows_before,
                                     "locks_left": sorted(state.held.get(ctx.ctx_id, ())) if ctx else None,
                                     "folders_left": [str(p) for p in made if p.exists()]})
            else:
                count += 1
                with guard:
                    outcomes.append({"kind": "admitted", "run_id": admitted["self_check_run_id"], "work": kind,
                                     "share": share, "reservation": admitted["cpu_reservation"],
                                     "instance": state.local.instance, "request": request})
            time.sleep(local.uniform(0.5, 2.0))

    barrier = threading.Barrier(ACTORS)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    actors = [threading.Thread(target=actor, args=(i,)) for i in range(ACTORS)]
    for thread in actors:
        thread.start()
    for thread in actors:
        thread.join(600)
    admitted = [o for o in outcomes if o["kind"] == "admitted"]
    deadline = time.monotonic() + 600
    for outcome in admitted:                                                   # every worker ends its run
        for worker in [t for t in threading.enumerate() if t.name == f"selfcheck-{outcome['run_id']}"]:
            worker.join(max(1, deadline - time.monotonic()))
    stop.set()
    watcher.join(30)

    refused = [o for o in outcomes if o["kind"] == "refused"]
    summary_line = {"admitted": len(admitted), "refused": len(refused), "max_sum": max(sums or [0]),
                    "samples": len(sums), "kinds": sorted({o["work"] for o in admitted}),
                    "instances": sorted({o["instance"] for o in admitted}),
                    "reservations": sorted(o["reservation"] for o in admitted)}
    print("invariant 9.2-7(g):", json.dumps(summary_line))

    # the host's sum, at every sampled moment
    assert sums and max(sums) <= CAP[0], summary_line
    # refusals: 409 host capacity, nothing left behind
    assert refused, summary_line                                               # the cap was actually reached
    for outcome in refused:
        assert (outcome["status"], outcome["code"]) == (409, "selfcheck_host_capacity_exhausted"), outcome
        assert outcome["new_rows"] == 0 and outcome["locks_left"] == [] and outcome["folders_left"] == [], outcome
    assert {"pytest", "nested", "script"} <= {o["work"] for o in admitted}, summary_line
    assert {"inst_a", "inst_b"} <= {o["instance"] for o in admitted}, summary_line
    assert max(o["reservation"] for o in admitted) >= 2, summary_line          # parallel trees took part
    # the tree's limit equals the stored reservation: at spawn, and whenever the watcher saw it running
    for run_id, reserved, bits in limits:
        assert bits is None or bits == reserved, (run_id, reserved, bits)
    for outcome in admitted:
        run_id, reserved = outcome["run_id"], outcome["reservation"]
        row = runs_db.get_run(run_id)
        assert row["cpu_reservation"] == reserved and 1 <= reserved <= CAP[0]
        assert state.spawned[run_id] == {"cpu_limit": reserved, "job_bits": reserved}, (run_id, state.spawned[run_id])
        assert row["status"] == "completed" and row["active_key"] is None, (outcome, row["status"], row["error_code"])
        records = [json.loads(p.read_text()) for p in (harness.out / run_id).glob("*-*.json")]
        assert all(r["cpus"] == reserved for r in records), (outcome, records)
        if outcome["work"] == "script":
            assert row["timed_out"] or row["exit_code"] == 0, (outcome, row["exit_code"])
            if not row["timed_out"]:
                assert len(records) == 3
            continue
        assert row["exit_code"] == 0, (outcome, row["stdout_tail"][-2500:])
        assert records, outcome
        # per run, outer and nested together: xdist worker processes <= the reservation
        assert len({r["pid"] for r in records if r["worker"]}) <= reserved, (outcome, records)
        assert _xdist_peak(records) <= reserved, (outcome, records)
        block, environment = result_block.summarize(row["stdout_tail"], row["exit_code"])
        assert block["parallel"]["pool_size"] == reserved and environment["workers"]["cpu_limit"] == reserved
        assert environment["workers"]["applied"] <= reserved
        if outcome["work"] == "nested":
            nested = json.loads((harness.out / run_id / "nested.json").read_text())
            inner_block, inner_env = result_block.summarize(nested["stdout"], nested["returncode"])
            assert environment["workers"]["applied"] + inner_env["workers"]["applied"] <= reserved, outcome
            assert inner_block["parallel"]["pool_size"] == reserved
    # every lock released, no runtime folder left, no protection, the host is empty again
    assert all(not keys for keys in state.held.values()), state.held
    assert not state.protected
    assert not [p for made in state.runtimes.values() for p in made if p.exists()]
    assert runs_db.host_reservations(HOST, CAP[0]) == (0, 0)
