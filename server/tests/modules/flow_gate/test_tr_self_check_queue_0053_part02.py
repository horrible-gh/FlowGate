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

def test_a_worker_resuming_after_its_lease_was_handed_over_neither_spawns_nor_writes(env, monkeypatch):
    """rev2: worker A claims, stalls before spawning, its lease lapses and the run goes back to
    the queue; worker B claims it again. A's claim token no longer matches: its spawn stamp,
    process records, running / terminal / recovery writes and lease renewal all match nothing,
    and A resuming ends without spawning or touching B's row. B then runs the request."""
    rid = env.admit(DOCS[P][0])["self_check_run_id"]
    row_a = runs_db.claim_next_queued("inst_0053q", "2000-01-01T00:00:00+09:00", HOST, (2, 0))
    assert row_a["self_check_run_id"] == rid
    assert queue.maintain()["released"] == 1                               # A's lease lapsed
    row_b = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    assert row_b["self_check_run_id"] == rid
    claim_a, claim_b = row_a["queue_lease_owner"], row_b["queue_lease_owner"]
    assert claim_a != claim_b and claim_a.startswith("inst_0053q/")        # one token per claim
    # Every write A could still make is fenced to its (lost) claim.
    assert runs_db.claim_held(rid, claim_a) is False and runs_db.claim_held(rid, claim_b) is True
    assert runs_db.renew_lease(rid, claim_a, queue._stamp(60)) is False
    assert runs_db.mark_spawn_started(rid, "posix_supervisor", claim=claim_a) is False
    assert runs_db.record_spawn_ownership(rid, target_pid=4242, claim=claim_a) is False
    assert runs_db.mark_running(rid, claim=claim_a) is None
    assert runs_db.finish_failed(rid, error_code="x", claim=claim_a) is None
    assert runs_db.finish_completed(rid, exit_code=0, claim=claim_a) is None
    assert runs_db.finish_cancelled(rid, claim=claim_a) is None
    assert runs_db.mark_recovering(rid, claim=claim_a) is None
    keeper = queue._LeaseKeeper(rid, claim_a)
    assert keeper.renew() is False and keeper.lost.is_set()               # A's keeper sees the loss
    # B is in the middle of its spawn (stamp written, no process yet) when A resumes. A's own
    # check read its claim just before the hand-over (a stale True once), so A goes as far as its
    # spawn: the fenced stamp refuses it before any Popen, and A leaves without writing.
    assert runs_db.mark_spawn_started(rid, "posix_supervisor", claim=claim_b) is True
    assert runs_db.mark_spawn_started(rid, "posix_supervisor", claim=claim_b) is False   # stamped once
    real_held, real_renew, calls, popen = runs_db.claim_held, queue._LeaseKeeper.renew, [], []
    real_popen = service.executor.subprocess.Popen
    monkeypatch.setattr(runs_db, "claim_held",
                        lambda r, c: True if not calls and not calls.append(1) else real_held(r, c))
    monkeypatch.setattr(queue._LeaseKeeper, "renew", lambda self: True)   # A noticed nothing yet
    monkeypatch.setattr(service.executor.subprocess, "Popen",
                        lambda *a, **k: popen.append(a) or real_popen(*a, **k))
    try:
        queue.execute(row_a, "inst_0053q")
    finally:
        monkeypatch.setattr(service.executor.subprocess, "Popen", real_popen)
        monkeypatch.setattr(queue._LeaseKeeper, "renew", real_renew)
        monkeypatch.setattr(runs_db, "claim_held", real_held)
    assert len(calls) == 1                                                 # the stale read was used
    assert popen == []                                                     # A spawned nothing
    after_a = _row(rid)
    assert (after_a["status"], after_a["queue_lease_owner"], after_a["recovery_state"]) == ("pending", claim_b, "none")
    assert after_a["process_owner_kind"] == "posix_supervisor"             # B's stamp is intact
    assert after_a["target_pid"] is None and after_a["finished_at"] is None
    assert Path(after_a["snapshot_path"]).is_dir()                         # B's snapshot is kept
    # B goes on: its stamp is taken back for this test's spawn, and B runs the request.
    assert runs_db.clear_spawn_started(rid, claim=claim_a) is False        # A cannot clear B's stamp
    assert runs_db.clear_spawn_started(rid, claim=claim_b) is True
def test_the_worker_holding_the_claim_runs_it_after_a_hand_over(env):
    rid = env.admit(DOCS[P][0])["self_check_run_id"]
    row_a = runs_db.claim_next_queued("inst_0053q", "2000-01-01T00:00:00+09:00", HOST, (2, 0))
    assert queue.maintain()["released"] == 1
    row_b = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    assert queue.execute(row_a, "inst_0053q")["status"] == "pending"      # A: nothing done
    assert _row(rid)["queue_lease_owner"] == row_b["queue_lease_owner"]
    done = queue.execute(row_b, "inst_0053q")
    assert (done["status"], done["exit_code"]) == ("completed", 0), done
    assert "value=uncommitted" in done["stdout_tail"]
    assert not Path(done["snapshot_path"]).exists()
    assert runs_db.host_reservations(HOST, 2) == (0, 0)
def test_a_long_queue_of_one_project_does_not_hide_another_project(env):
    """rev2: the alternation looks at every project with a waiting request, however deep the
    queue is. Project P was served before and has 600 requests waiting ahead; project P2 has
    never been served and has one request at the very end: P2 goes next, then P again."""
    served = _queued_row(P, DOCS[P][0], 0)
    claimed = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (8, 0))
    assert claimed["self_check_run_id"] == served["self_check_run_id"]
    assert runs_db.finish_completed(served["self_check_run_id"], exit_code=0)
    p_rows = [_queued_row(P, DOCS[P][n % 3], n) for n in range(1, 601)]
    late = _queued_row(P2, DOCS[P2][0], 1)
    assert runs_db.queue_position(late["self_check_run_id"]) == 601
    first = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (8, 0))
    assert first["self_check_run_id"] == late["self_check_run_id"]          # not hidden behind P
    assert runs_db.finish_completed(first["self_check_run_id"], exit_code=0)
    second = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (8, 0))
    assert second["self_check_run_id"] == p_rows[0]["self_check_run_id"]    # P's oldest, in order
def test_a_full_host_makes_requests_wait_instead_of_refusing_them(env):
    runs = [env.admit(DOCS[P][i]) for i in range(3)]
    first = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    second = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    assert first and second
    assert runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0)) is None
    assert _row(runs[2]["self_check_run_id"])["status"] == "queued"        # waits, no 409
    assert runs_db.finish_completed(first["self_check_run_id"], exit_code=0)
    third = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    assert third["self_check_run_id"] == runs[2]["self_check_run_id"]
def test_a_snapshot_that_cannot_be_taken_fails_only_that_request(env, monkeypatch):
    real = queue.take_snapshot

    def broken(root, run_id):
        raise queue.QueueError(queue.SNAPSHOT_FAILED, "injected: no space left on device")

    monkeypatch.setattr(queue, "take_snapshot", broken)
    failed = env.admit(DOCS[P][0])
    assert (failed["status"], failed["error_code"]) == ("failed", queue.SNAPSHOT_FAILED)
    assert "no space left" in _row(failed["self_check_run_id"])["stderr_tail"]
    monkeypatch.setattr(queue, "take_snapshot", real)
    ok = env.admit(DOCS[P][1])
    assert ok["status"] == "queued"
def test_a_snapshot_that_cannot_be_removed_is_left_pending_and_retried(env, monkeypatch):
    row = _row(env.admit(DOCS[P][0])["self_check_run_id"])
    real = queue.remove_snapshot_dir
    monkeypatch.setattr(queue, "remove_snapshot_dir", lambda path: (_ for _ in ()).throw(OSError("busy")))
    queue.cancel_waiting(row["self_check_run_id"])
    pending = _row(row["self_check_run_id"])
    assert pending["status"] == "cancelled" and pending["cleanup_pending"] is True
    assert "snapshot_cleanup_failed" in pending["cleanup_error"]
    monkeypatch.setattr(queue, "remove_snapshot_dir", real)
    assert queue.maintain()["cleaned"] == 1
    assert _row(row["self_check_run_id"])["cleanup_pending"] is False
    assert not Path(row["snapshot_path"]).exists()
def test_an_unproved_queue_run_keeps_only_its_slot_never_the_group(env, monkeypatch):
    row = env.admit(DOCS[P][0])
    claimed = runs_db.claim_next_queued("inst_0053q", queue._stamp(60), HOST, (2, 0))
    monkeypatch.setattr(service.lock_manager, "selfcheck_ensure_protected",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no G protection for a queue run")))
    current = runs_db.mark_recovering(claimed["self_check_run_id"])
    service._hold_recovery_incomplete(current, service.proof.TREE_UNPROVEN, set())
    held = _row(row["self_check_run_id"])
    assert held["recovery_state"] == "incomplete"
    assert runs_db.has_group_recovery_incomplete(P, G) is False            # the Group stays open
    assert runs_db.host_reservations(HOST, 2) == (1, 1)                    # its one slot stays taken
def test_card_status_cancel_waiting_and_force_stop(env):
    key = queue.card_key(tr_doc_id=DOCS[P][0])
    assert queue.card_status(key)["state"] == "idle"
    row = env.admit(DOCS[P][0], args=("slow.py",))
    status = queue.card_status(key)
    assert status["state"] == "queued" and status["queued"][0]["position"] == 1
    worker = threading.Thread(target=queue.dispatch_once, daemon=True)
    worker.start()
    for _ in range(200):
        if _row(row["self_check_run_id"])["status"] == "running":
            break
        time.sleep(0.05)
    running = queue.card_status(key)
    assert running["state"] == "running" and running["running"][0]["started_at"]
    stopped = queue.force_stop(row["self_check_run_id"])
    worker.join(timeout=30)
    assert (stopped["status"], stopped["error_code"]) == ("failed", queue.FORCE_STOPPED)
    assert runs_db.host_reservations(HOST, 2) == (0, 0)                    # the slot is free again
    ended = queue.card_status(key)
    assert ended["state"] == "ended" and ended["last_ended"]["error_code"] == queue.FORCE_STOPPED
    waiting = env.admit(DOCS[P][1])
    assert queue.cancel_waiting(waiting["self_check_run_id"])["status"] == "cancelled"
    assert queue.cancel_waiting(waiting["self_check_run_id"]) is None      # not waiting any more
    forced_wait = env.admit(DOCS[P][2])
    assert queue.force_stop(forced_wait["self_check_run_id"])["status"] == "cancelled"
def test_the_cancel_route_ends_a_waiting_request_at_once(env):
    row = env.admit(DOCS[P][0])
    result = service._cancel(row["self_check_run_id"], lambda: runs_db.get_run(row["self_check_run_id"]))
    assert result["status"] == "cancelled"
    assert result["cancel"] == {"accepted": True, "stopped": True, "state": "stopped", "reason": None}
