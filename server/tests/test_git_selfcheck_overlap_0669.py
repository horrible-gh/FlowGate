"""Self-check and approval/finalize running at the same time (flowgate.default.0669 T0003).

Real code, real SQLite store, real threads and real child processes:

* ``tr_self_check_service.start`` spawns a real (slow) python child for a Group and keeps that
  Group's G (holder_kind=selfcheck) for the whole run.
* ``finalize_publish.request`` and ``approval_publish.start`` / the Runner (``job_runner``)
  are the real job paths, with the real ``job_store``, ``lock_manager`` and ``operation_job``.

Stubbed (named here so the limits are visible): the Git bodies ``git_service.finalize`` and
``git_service.run_approve_git_action`` (they sleep and record which locks the job holds),
approval F1~F4 git work (``run_freeze`` writes the same F5 DB state), ``commit_final_approval``
and the SSE emit. Each test prints ``OBS`` lines with the observed holder / wait / outcome.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.db import git_concurrency as gc  # noqa: E402
from modules.flow_gate.services import tr_self_check_service as sc  # noqa: E402
from modules.flow_gate.services.git import approval_freeze as af  # noqa: E402
from modules.flow_gate.services.git import approval_publish as ap  # noqa: E402
from modules.flow_gate.services.git import finalize_publish as fp  # noqa: E402
from modules.flow_gate.services.git import instance_registry as reg  # noqa: E402
from modules.flow_gate.services.git import job_runner as runner  # noqa: E402
from modules.flow_gate.services.git import job_store as jobs  # noqa: E402
from modules.flow_gate.services.git import lock_manager as lm  # noqa: E402
from modules.flow_gate.services.git import merge_target  # noqa: E402
from modules.flow_gate.services.git.credentials import GitServiceError  # noqa: E402

NOW = "2026-10-05T12:00:00+09:00"
P = "p_0669sc"
GROUPS = ("g_a", "g_b", "g_c")
SLEEP = 2.5          # self-check child lifetime
BODY = 0.6           # stubbed Git body of a publish


def obs(line: str) -> None:
    print("OBS " + line, flush=True)


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_git_selfcheck_overlap_0669.db")


@pytest.fixture
def env(db_path, tmp_path, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper

    s = object.__new__(connection.FlowGateStore)
    s._db = SQLiteWrapper(db_path)
    s._sq = None
    from modules.flow_gate.db import operation_job, tr_history_recovery  # noqa: F401
    for name, module in list(sys.modules.items()):
        if name.startswith("modules.flow_gate.") and hasattr(module, "get_store"):
            monkeypatch.setattr(module, "get_store", lambda s=s: s)
    with s.transaction():
        for table in ("resource_lock", "tr_self_check_runs", "operation_job", "server_instance"):
            s._execute(f"DELETE FROM {table}")
        s._execute("DELETE FROM group_git_state WHERE project_id = ?", [P])
        s._execute("DELETE FROM documents WHERE project_id = ?", [P])
        s._execute("DELETE FROM groups WHERE project_id = ?", [P])
        s._execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                   "VALUES (?, 'P 0669 sc', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING", [P, NOW, NOW])
        for i, g in enumerate(GROUPS, 1):
            s._execute("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
                       "VALUES (?, ?, 'default', ?, 'OPEN', ?, ?)", [g, P, g, NOW, NOW])
            s._execute("INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, "
                       "branch, revision_no, rejection_history, created_at, updated_at) "
                       "VALUES (?, ?, ?, 'default', 'TR', 1, 'TR', 'open', 'main', 1, '[]', ?, ?)",
                       [f"{g}.0001-TR", P, g, NOW, NOW])
            s._execute("INSERT INTO group_git_state (group_id, project_id, branch, worktree_registered, status, "
                       "created_at, updated_at) VALUES (?, ?, ?, 1, 'none', ?, ?)",
                       [g, P, f"wf/{g}", NOW, NOW])
    roots = {}
    for g in GROUPS:
        root = tmp_path / g
        root.mkdir()
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "t")
        (root / "slow.py").write_text(f"import time\nprint('child {g} started', flush=True)\ntime.sleep({SLEEP})\n"
                                      f"print('child {g} done', flush=True)\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "init")
        roots[g] = root
    monkeypatch.setattr(sc.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(sc.git_service, "effective_src_root_ex",
                        lambda _p, group_id: (str(roots[group_id]), "worktree"))
    monkeypatch.setattr(sc, "_emit", lambda row: None)
    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-a")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.02)
    budget = {"interactive": 3.0}
    monkeypatch.setattr(lm, "wait_budget", lambda domain, mode: 0.0 if mode in (lm.NO_WAIT, "job") else (
        5.0 if mode == "selfcheck_start" else budget["interactive"]))
    monkeypatch.setattr(gc, "_current_instance_id", None)
    reg.register()

    # job-layer stubs (Git bodies and unrelated services only)
    from modules.flow_gate.services import git_service as gs
    held_log = []

    def snapshot(tag):
        rows = gc.list_locks_in_scope(P)
        held_log.append((tag, sorted((r["domain"], r.get("group_id") or "-", r["holder_kind"], r["holder_ctx_id"][:14])
                                     for r in rows)))
        return held_log[-1][1]

    def fake_finalize(group_id, action, message=None, **kw):
        snapshot(f"finalize:{group_id}")
        time.sleep(BODY)
        return {"ok": True, "result": {"action": action, "status": "merged", "merge_commit": "c" * 40,
                                       "pushed": True, "merge_id": None, "conflict_files": []}}

    def fake_approve(group_id, action, *a, **kw):
        snapshot(f"approval:{group_id}")
        time.sleep(BODY)
        return {"ok": True, "terminal": True, "result": {"action": action, "status": "merged",
                                                         "merge_commit": "d" * 40, "pushed": True}}

    monkeypatch.setattr(gs, "finalize", fake_finalize)
    monkeypatch.setattr(gs, "run_approve_git_action", fake_approve)
    monkeypatch.setattr(gs, "_project_of_group", lambda g: P)
    monkeypatch.setattr(gs, "complete_approve_git_action", lambda *a, **k: None)
    monkeypatch.setattr(gs, "realize_wf_done_transition", lambda *a, **k: None)
    monkeypatch.setattr(gs.db_git, "get_config", lambda _p: {})
    monkeypatch.setattr(merge_target, "plan_finalize_target",
                        lambda *a, **k: SimpleNamespace(is_project_base=True, target_branch="main"))

    def fake_freeze(ctx, origin="request"):
        with s.transaction():
            af.set_freeze_claim(ctx, "publish_wait")
            enc = jobs.fenced_write(ctx, dict(
                phase="publish_pending", freeze_completed=1, frozen_sha="a" * 40, frozen_tree="b" * 40,
                pin_ref=af.pin_ref_name(ctx.job_id), attempt_count=0,
                status="running" if origin == af.REQUEST else "pending"),
                expected=af._FREEZING, allow_freeze=True, update_local=False)
        ctx.job.update(enc)
        return af.FROZEN

    monkeypatch.setattr(af, "run_freeze", fake_freeze)
    from modules.flow_gate.workflow import pipeline_service

    def fake_commit(doc_id, actor_user_id, user_permissions, locale, consume_hook):
        with s.transaction():
            assert consume_hook(None, None) is not False
        return {"document": {"doc_id": doc_id}}

    monkeypatch.setattr(pipeline_service, "commit_final_approval", fake_commit)
    monkeypatch.setattr(ap.approval_intent, "find_clean_retry", lambda g: (None, None))
    ap.install()
    yield SimpleNamespace(store=s, roots=roots, budget=budget, held=held_log, snapshot=snapshot)
    gc.set_current_instance_id(None)


def _doc(g):
    return f"{g}.0001-TR"


def _start_check(g):
    t0 = time.monotonic()
    run = sc.start(_doc(g), {"program": "python", "args": ["slow.py"], "timeout_seconds": 30})
    return run, time.monotonic() - t0


def _wait_check(g, run_id, limit=30):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        row = sc.read(_doc(g), run_id)
        if row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError("self-check did not finish")


def _g_rows(group_id):
    return [(r["holder_kind"], r["holder_ctx_id"]) for r in gc.list_locks_in_scope(P)
            if r["domain"] == "G" and r.get("group_id") == group_id]


def _approval(g):
    t0 = time.monotonic()
    doc = {"doc_id": _doc(g), "revision_no": 1}
    out = ap.start(doc=doc, group_id=g, git_action="merge", target_branch=None,
                   actor_user_id="u", locale="ko")
    return out, time.monotonic() - t0


# ── 1. a running Self-check and another Group's work ─────────────────────────

def test_selfcheck_does_not_block_other_groups_but_blocks_its_own(env):
    run_a, took_a = _start_check("g_a")
    run_b, took_b = _start_check("g_b")
    obs(f"selfcheck start g_a={took_a:.2f}s g_b={took_b:.2f}s (both admitted while the other runs)")
    assert _g_rows("g_a")[0][0] == "selfcheck" and _g_rows("g_b")[0][0] == "selfcheck"
    obs(f"selfcheck holders while running: g_a={_g_rows('g_a')[0]} g_b={_g_rows('g_b')[0]}")

    t0 = time.monotonic()
    ctx = lm.new_context()
    other = lm.acquire("G", P, group_id="g_c", holder_kind="source_mutation", ctx=ctx, mode=lm.NO_WAIT)
    waited = time.monotonic() - t0
    assert other.ok
    lm.release(ctx, other.lock_key)
    obs(f"g_c source_mutation while selfcheck g_a+g_b run: {other.kind} in {waited:.3f}s")

    ctx2 = lm.new_context()
    same = lm.acquire("G", P, group_id="g_a", holder_kind="source_mutation", ctx=ctx2, mode=lm.NO_WAIT)
    assert same.kind == lm.BUSY and same.blocker["holder_kind"] == "selfcheck"
    obs(f"g_a source_mutation during its own selfcheck: busy, blocker holder_kind={same.blocker['holder_kind']}")

    with pytest.raises(sc.SelfCheckError) as dup:
        _start_check("g_a")
    obs(f"second selfcheck on g_a: {dup.value.code}")

    row_a = _wait_check("g_a", run_a["self_check_run_id"])
    row_b = _wait_check("g_b", run_b["self_check_run_id"])
    for g, row in (("g_a", row_a), ("g_b", row_b)):
        assert row["status"] == "completed" and row["exit_code"] == 0
        assert not row["source_changed_during_run"]
        obs(f"selfcheck {g}: status={row['status']} exit={row['exit_code']} "
            f"source_changed={bool(row['source_changed_during_run'])} stdout={row['stdout_tail'].strip()!r}")
    assert gc.list_locks_in_scope(P) == []
    ctx3 = lm.new_context()
    again = lm.acquire("G", P, group_id="g_a", holder_kind="source_mutation", ctx=ctx3, mode=lm.NO_WAIT)
    assert again.ok
    lm.release(ctx3, again.lock_key)
    obs("after completion: no locks left, g_a source_mutation acquirable")


# ── 2. real finalize job while a Self-check runs ─────────────────────────────

def test_finalize_job_of_another_group_runs_during_selfcheck(env):
    run_a, _ = _start_check("g_a")
    t0 = time.monotonic()
    out = fp.request(P, "g_b", "merge", None, None)
    took = time.monotonic() - t0
    assert out["ok"] and out["result"]["status"] == "merged"
    job = jobs.get_job(out["result"]["job_id"])
    assert job["status"] == "succeeded"
    obs(f"finalize g_b during selfcheck g_a: succeeded in {took:.2f}s (body {BODY}s), no git_busy; "
        f"locks held by the job = {env.held[-1][1]}")
    assert sc.read(_doc("g_a"), run_a["self_check_run_id"])["status"] in ("pending", "running")
    _wait_check("g_a", run_a["self_check_run_id"])


def test_finalize_job_on_selfcheck_group_waits_then_runs(env):
    env.budget["interactive"] = 6.0       # longer than the self-check child, so the wait path is what runs
    run_a, _ = _start_check("g_a")
    holder = _g_rows("g_a")[0]
    t0 = time.monotonic()
    out = fp.request(P, "g_a", "merge", None, None)       # interactive wait (budget 6s > selfcheck left)
    took = time.monotonic() - t0
    assert out["result"]["status"] == "merged"
    obs(f"finalize g_a on the selfcheck Group: waited {took:.2f}s for holder={holder[0]} then succeeded; "
        f"locks held by the job = {env.held[-1][1]}")
    assert took >= SLEEP - 1.0
    row = _wait_check("g_a", run_a["self_check_run_id"])
    assert row["status"] == "completed" and row["exit_code"] == 0


def test_finalize_job_queues_when_budget_is_short_then_runner_finishes_it(env):
    env.budget["interactive"] = 0.2
    run_a, _ = _start_check("g_a")
    t0 = time.monotonic()
    with pytest.raises(GitServiceError) as busy:
        fp.request(P, "g_a", "merge", None, None)
    took = time.monotonic() - t0
    d = busy.value.details
    assert busy.value.status == 409 and d["queued"] and d["blocker"]["domain"] == "G"
    obs(f"finalize g_a (budget 0.2s): 409 {busy.value.code} after {took:.2f}s, queued job={d['job_id']} "
        f"blocker={d['blocker']}")
    _wait_check("g_a", run_a["self_check_run_id"])
    t1 = time.monotonic()
    cr = runner.try_claim(d["job_id"])
    assert cr is not None
    runner.execute(cr)
    job = jobs.get_job(d["job_id"])
    assert job["status"] == "succeeded"
    obs(f"runner then ran the queued finalize: status={job['status']} in {time.monotonic() - t1:.2f}s; "
        f"locks held = {env.held[-1][1]}")
    assert gc.list_locks_in_scope(P) == []


# ── 3. real approval job under concurrent Group activity ─────────────────────

def test_approval_during_selfcheck_and_parallel_finalize(env):
    run_a, _ = _start_check("g_a")
    results = {}

    def approve():
        results["approval"] = _approval("g_b")

    def finalize():
        t0 = time.monotonic()
        results["finalize"] = (fp.request(P, "g_c", "merge", None, None), time.monotonic() - t0)

    threads = [threading.Thread(target=approve), threading.Thread(target=finalize)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads)
    wall = time.monotonic() - start
    out, took_ap = results["approval"]
    fin, took_fi = results["finalize"]
    assert out.state == ap.EXECUTED, out
    assert fin["result"]["status"] == "merged"
    job = jobs.get_job(out.job["job_id"])
    assert job["status"] == "succeeded"
    obs(f"approval g_b {took_ap:.2f}s state={out.state}; finalize g_c {took_fi:.2f}s merged; "
        f"wall {wall:.2f}s (serial would be >= {2 * BODY:.1f}s, both need B+R of the same project)")
    for tag, rows in env.held:
        obs(f"locks held by {tag}: {rows}")
    slower = max(took_ap, took_fi)
    assert slower >= BODY            # the later one really waited for B/R
    assert slower < 3.0              # and did not hit the interactive budget
    assert gc.list_locks_in_scope(P) == [] or all(r["holder_kind"] == "selfcheck" for r in gc.list_locks_in_scope(P))
    _wait_check("g_a", run_a["self_check_run_id"])
    assert gc.list_locks_in_scope(P) == []


def test_approval_queues_behind_another_approval_and_runner_finishes_it(env):
    env.budget["interactive"] = 0.1
    results = {}

    def first():
        results["first"] = _approval("g_b")

    t = threading.Thread(target=first)
    t.start()
    time.sleep(0.25)                  # g_b is inside its publish (B+R held)
    holder = [(r["domain"], r["holder_kind"]) for r in gc.list_locks_in_scope(P)]
    out, took = _approval("g_c")
    t.join(30)
    assert out.state != ap.EXECUTED
    job = jobs.get_job(out.job["job_id"])
    assert job["status"] == "blocked" and job["blocked_domain"] in ("B", "R")
    obs(f"approval g_c while g_b publishes ({holder}): not executed after {took:.2f}s, status={job['status']} "
        f"blocked_domain={job['blocked_domain']} blocked_operation={job['blocked_operation']} "
        f"blocked_holder={job['blocked_holder']}")
    assert results["first"][0].state == ap.EXECUTED
    t1 = time.monotonic()
    cr = runner.try_claim(job["job_id"])
    assert cr is not None
    runner.execute(cr)
    done = jobs.get_job(job["job_id"])
    assert done["status"] == "succeeded"
    obs(f"runner finished the queued approval: status={done['status']} in {time.monotonic() - t1:.2f}s "
        f"(wait_started_at={job.get('wait_started_at')})")
    assert gc.list_locks_in_scope(P) == []
