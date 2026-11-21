"""Independently selectable scenarios; shared fixtures remain in test_tr_self_check_queue_0053.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_tr_self_check_queue_0053 import (
    DOCS,
    G,
    G2,
    GROUP_OF,
    HOST,
    P,
    P2,
    Path,
    SimpleNamespace,
    _MIGRATIONS,
    _STORE_MODULES,
    _git,
    _locked,
    _queued_row,
    _row,
    _seed,
    _stamp,
    connection,
    datetime,
    db_path,
    env,
    policy,
    pytest,
    queue,
    runs_db,
    service,
    store,
    subprocess,
    sys,
    thread_stores,
    threading,
    time,
    timezone,
    worktree,
)

def test_the_group_lists_its_open_queue_runs(env):
    a = env.admit(DOCS[P][0])
    b = env.admit(DOCS[P][1])
    listed = service.group_active_runs(P, G)
    assert [r["self_check_run_id"] for r in listed] == [a["self_check_run_id"], b["self_check_run_id"]]
    assert {r["status"] for r in listed} == {"queued"}
@pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
def test_migration_201_exists_for_every_dialect(dialect):
    text = (_MIGRATIONS / dialect / "201_tr_self_check_queue.sql").read_text(encoding="utf-8")
    for column in ("enqueued_at", "snapshot_path", "queue_key", "queue_lease_owner", "queue_lease_until",
                   "queue_requeue_count", "queue_order", "queue_claimed_at"):
        assert column in text, (dialect, column)
    assert "'queued', 'pending', 'running', 'completed', 'failed', 'cancelled'" in text
def test_quota_ten_starts_then_refusal_and_timeout_clamp(env):
    from modules.flow_gate.services import tr_self_check_quota as quota
    for remaining in range(9, -1, -1):
        row = env.admit(DOCS[P][0], timeout_seconds=3600)
        assert row["timeout_seconds"] == 60                    # clamped to the card default of 1 minute
        assert row["verification"]["remaining"] == remaining + 1
        assert queue.dispatch_once()
        final = service.public(_row(row["self_check_run_id"]))
        assert final["status"] == "completed"
        assert final["verification"]["remaining"] == remaining
        assert final["verification"]["notice"].startswith("⚠ ") == (remaining <= 2)
    with pytest.raises(service.SelfCheckError) as error:
        env.admit(DOCS[P][0])
    assert error.value.code == "selfcheck_quota_exhausted"
    # Another work stage has its own budget; rereading the first retains its zero.
    assert env.admit(DOCS[P][1])["verification"]["remaining"] == 10
    assert service.public(_row(row["self_check_run_id"]))["verification"]["remaining"] == 0
def test_quota_waiting_superseded_cancelled_and_invalid_cost_nothing(env):
    first = env.admit(DOCS[P][0])
    second = env.admit(DOCS[P][0])
    assert _row(first["self_check_run_id"])["status"] == "cancelled"
    queue.cancel_waiting(second["self_check_run_id"])
    with pytest.raises(service.SelfCheckError):
        env.admit(DOCS[P][0], timeout_seconds=0)
    third = env.admit(DOCS[P][0], timeout_seconds=10)
    assert third["verification"]["remaining"] == 10 and third["timeout_seconds"] == 10
    assert third["verification"]["run_timeout_sec"] == 60
def test_quota_failed_and_timed_out_runs_count(env):
    failed = env.admit(DOCS[P][0], args=("missing.py",))
    assert queue.dispatch_once()
    final = service.public(_row(failed["self_check_run_id"]))
    assert final["exit_code"] != 0 and final["verification"]["remaining"] == 9
    timed = env.admit(DOCS[P][0], args=("slow.py",), timeout_seconds=1)
    assert queue.dispatch_once()
    final = service.public(_row(timed["self_check_run_id"]))
    assert final["timed_out"] and final["verification"]["remaining"] == 8
def test_quota_no_process_spawn_failure_refunds_once(env, monkeypatch):
    row = env.admit(DOCS[P][0])
    def no_process(*args, **kwargs):
        kwargs["record"]("spawning", {"process_owner_kind": "posix_supervisor"})
        raise service.executor.OwnershipError("no process", created=False)
    monkeypatch.setattr(service.executor, "spawn", no_process)
    assert queue.dispatch_once()
    result = _row(row["self_check_run_id"])
    assert result["status"] == "failed" and result["verify_started_count"] == 0
    assert not runs_db.clear_spawn_started(row["self_check_run_id"])
def test_quota_rechecked_at_spawn_after_two_requests_were_admitted(env, monkeypatch):
    from modules.flow_gate.services import tr_self_check_quota as quota
    original = quota.resolve
    monkeypatch.setattr(quota, "resolve", lambda token, target=None: dict(original(token, target), max_runs=1))
    first = env.admit(DOCS[P][0])
    first_claim = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    second = env.admit(DOCS[P][0])
    second_claim = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    assert first_claim and second_claim
    queue.execute(first_claim, "inst_0053q")
    queue.execute(second_claim, "inst_0053q")
    assert _row(first["self_check_run_id"])["status"] == "completed"
    last = _row(second["self_check_run_id"])
    assert last["status"] == "failed" and last["error_code"] == "selfcheck_quota_exhausted"
    assert last["verify_started_count"] == 0 and last["target_pid"] is None
def test_quota_atomic_last_start_across_connections(env, thread_stores, monkeypatch):
    from modules.flow_gate.services import tr_self_check_quota as quota
    original = quota.resolve
    monkeypatch.setattr(quota, "resolve", lambda token, target=None: dict(original(token, target), max_runs=1))
    one = env.admit(DOCS[P][0])
    a = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    two = env.admit(DOCS[P][0])
    b = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    barrier, results = threading.Barrier(2), []
    def charge(row):
        barrier.wait()
        try:
            results.append(runs_db.mark_spawn_started(row["self_check_run_id"], "posix_supervisor", row["queue_lease_owner"]))
        except runs_db.SelfCheckQuotaExhausted:
            results.append("exhausted")
    threads = [threading.Thread(target=charge, args=(r,)) for r in (a, b)]
    for t in threads: t.start()
    for t in threads: t.join(10)
    assert sorted(map(str, results)) == ["True", "exhausted"]
    assert sum(_row(r["self_check_run_id"])["verify_started_count"] for r in (one, two)) == 1
@pytest.mark.parametrize("locale, expected", [("ko", "1회당 3분 제한, 3회 남음"),
    ("en", "3 min per run, 3 runs remaining"), ("ja", "1回あたり3分制限、残り3回")])
def test_quota_localized_single_line(locale, expected):
    from modules.flow_gate.services import tr_self_check_quota as quota
    assert quota.notice({"run_timeout_sec": 180}, 3, locale) == "Self-check: " + expected
    assert quota.notice({"run_timeout_sec": 180}, 2, locale).startswith("⚠ Self-check:")
def test_quota_stage_origin_survives_new_token_and_hop_but_rework_resets(monkeypatch):
    from modules.flow_gate.services import tr_self_check_quota as quota
    origin = {"target_attempt_id": "a", "target_stage": "result", "target_seq": 0,
              "inv_role": "execution", "round_no": None}
    class Store:
        def table_exists(self, name): return True
        def _fetch_one(self, sql, params):
            if "graph_run_token_map" in sql:
                return {"flow_id": "f", "node_key": "n", "attempt_id": "a", "stage": "result", "run_id": "r"}
            return dict(origin)
    monkeypatch.setattr(quota.runs, "get_store", Store)
    monkeypatch.setattr(quota, "resolve_verify_limits", lambda f, n: SimpleNamespace(max_runs=5, run_timeout_sec=420))
    initial = quota.resolve({"token_id": "one", "ai_run_id": "run1"})
    assert quota.resolve({"token_id": "two", "ai_run_id": "run2"}) == initial
    assert initial["max_runs"] == 5 and initial["run_timeout_sec"] == 420
    origin.update(target_stage="result_review", target_seq=1, inv_role="rework", round_no=1)
    rework = quota.resolve({"token_id": "three", "ai_run_id": "run3"})
    assert rework["stage_key"] != initial["stage_key"]
    origin["round_no"] = 2
    assert quota.resolve({"token_id": "four", "ai_run_id": "run4"})["stage_key"] != rework["stage_key"]
