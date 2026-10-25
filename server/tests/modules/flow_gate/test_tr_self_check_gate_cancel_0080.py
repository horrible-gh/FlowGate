"""0080 T#2: the Self-check G reclaim gate, cancel without a control, and who holds the slot.

The card's core checks (NR0007 §5 items 9, 3-a, 1):

1. a Self-check-owned G whose owner lookup fails, or whose heartbeat expired while the
   owner lives, is deleted neither by the sweeper (``job_sweeper.step_stale_locks``) nor by
   another holder's G acquire (``source_mutation`` as the representative) -- both pass the
   one gate in ``lock_manager.reclaim_stale_row``; with the owner proved dead the same gate
   reclaims an empty tree (the run ended first) and protects an unproved one;
2. a cancel of an active run that has no control in this process ends nothing while the
   owner or the tree is UNKNOWN: 409 ``selfcheck_cancel_unconfirmed``, the run keeps its
   state and ``active_key``, no live process is signalled;
3. while a live run holds the Group's slot a second start stays ``already_running``, and
   the refusal carries the holder's summary, readable through the Group-scoped read.

Rework (review of rev 0): a gate pass that fails after its ``recovering`` CAS -- the finish
write, the incomplete write, or the read back after the CAS -- gives the fence back, so the
pass after the store recovers proves the row again; a fence whose undo is lost too (the CAS
committed, its answer lost) is proved again under the same fence once it is left behind,
and never while its owner is unproved.

Fixtures: ``flow`` of tests.e2e.self_check.test_tr_self_check_draft_e2e_0638 and ``world`` of
test_tr_self_check_recover_proof_0080 (run rows, owner server rows and G written directly,
real short python children). Injected, as in T#1: the identity lookup failure
(``executor.probe_start_identity``). Not covered here: the full path x injection matrix of
NR §6 item 6, the cancel poll of a worker in another server, a failing TerminateJobObject.
"""
from __future__ import annotations

import pytest

from tests.e2e.self_check.test_tr_self_check_draft_e2e_0638 import DRAFT_RUNS, G, P, RAW, _as, _row, _wait, flow  # noqa: F401 -- fixture
from tests.regression.self_check.test_tr_self_check_recover_proof_0080 import _assert_not_released, _g, _lookup_fails, world  # noqa: F401 -- fixture

from modules.flow_gate.api.v1 import self_check_routes as routes
from modules.flow_gate.db import tr_self_check_runs as db_runs
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import help_catalog
from modules.flow_gate.services import tr_self_check_gate as gate
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services.git import instance_registry as registry
from modules.flow_gate.services.git import job_sweeper
from modules.flow_gate.services.git import lock_manager


def _take_g(path: str):
    """The sweeper pass, or another holder's G acquire; True when G changed hands."""
    if path == "sweeper":
        return job_sweeper.step_stale_locks(full=True) > 0, None
    ctx = lock_manager.new_context("req")
    outcome = lock_manager.acquire("G", P, group_id=G, holder_kind="source_mutation", mode="job", ctx=ctx)
    if outcome.ok:
        lock_manager.release(ctx, outcome.lock_key)
    return outcome.ok, outcome


@pytest.mark.parametrize("path", ["sweeper", "acquire"])
@pytest.mark.parametrize("hint", ["owner_lookup_fails", "heartbeat_expired"])
def test_a_selfcheck_g_survives_stale_reclaim_without_proof(world, monkeypatch, path, hint):
    process, store = world["process"], world["store"]
    owner, target = process(alive=True), process(alive=True)
    rid, epoch = world["run"](owner, target)
    if hint == "owner_lookup_fails":
        # What mark_prior_same_node_dead writes after a failed lookup: a DEAD hint, no proof.
        _lookup_fails(monkeypatch, owner[0].pid)
        store._execute("UPDATE server_instance SET status = 'dead' WHERE pid = ?", [owner[0].pid])
    else:
        # The owner lives, but its heartbeat stopped long ago (lost after a DB outage).
        store._execute("UPDATE resource_lock SET heartbeat_until = ? WHERE lock_key = ?",
                       [registry.add_seconds(now_iso(), -3600), lock_manager.lock_key("G", P, G)])
    assert lock_manager.is_stale(_g(store)) == lock_manager.STALE_RECLAIMABLE   # the old path would delete

    taken, outcome = _take_g(path)

    assert taken is False
    if outcome is not None:
        assert outcome.kind == lock_manager.BUSY, outcome
    row, lock = _assert_not_released(store, rid, epoch)
    assert (row["recovery_state"], int(lock["protected"])) == ("none", 0)    # KEEP: nothing written
    assert owner[0].poll() is None and target[0].poll() is None


@pytest.mark.parametrize("tree", ["empty", "unproved"])
def test_a_proved_dead_owner_lets_the_gate_reclaim_an_empty_tree_and_protect_the_rest(world, monkeypatch, tree):
    process, store = world["process"], world["store"]
    target = process(alive=tree == "unproved")
    rid, epoch = world["run"](process(alive=False), target)
    if tree == "unproved":
        _lookup_fails(monkeypatch, target[0].pid)
    # A dead owner's heartbeat lapses: that nominates the row (on Windows the exited owner's
    # still-open handle keeps instance_state ALIVE, so DEAD alone would not).
    store._execute("UPDATE resource_lock SET heartbeat_until = ? WHERE lock_key = ?",
                   [registry.add_seconds(now_iso(), -3600), lock_manager.lock_key("G", P, G)])

    taken, outcome = _take_g("acquire")

    row = _row(store, rid)
    if tree == "empty":
        assert taken is True                                     # same gate, proof given: RECLAIM
        assert (row["status"], row["error_code"], row["active_key"]) == ("failed", "interrupted_by_restart", None)
    else:
        assert taken is False and outcome.kind == lock_manager.RECOVERY_REQUIRED, outcome
        row, lock = _assert_not_released(store, rid, epoch)
        assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
        assert int(lock["protected"]) == 1
        assert target[0].poll() is None


def _expire_g_heartbeat(store):
    store._execute("UPDATE resource_lock SET heartbeat_until = ? WHERE lock_key = ?",
                   [registry.add_seconds(now_iso(), -3600), lock_manager.lock_key("G", P, G)])


def _fails_once(monkeypatch, name, *, after_write=False):
    """``db_runs.<name>`` raises once, as a transient store error would; with ``after_write``
    its write commits first and only the answer is lost."""
    real, calls = getattr(db_runs, name), []

    def flaky(*args, **kwargs):
        if not calls:
            calls.append(name)
            if after_write:
                real(*args, **kwargs)
            raise RuntimeError(f"transient store error in {name}")
        return real(*args, **kwargs)

    monkeypatch.setattr(db_runs, name, flaky)


def _read_back_fails_once(monkeypatch):
    """The read right after the gate's CAS fails once (the old mark_recovering's UPDATE
    landed and its get_run failed)."""
    real_claim, real_get, state = db_runs.claim_recovering, db_runs.get_run, {"armed": False, "fired": False}

    def claim(rid):
        stamp = real_claim(rid)
        state["armed"] = stamp is not None and not state["fired"]
        return stamp

    def get(rid):
        if state["armed"]:
            state["armed"], state["fired"] = False, True
            raise RuntimeError("transient store error reading the fenced run")
        return real_get(rid)

    monkeypatch.setattr(db_runs, "claim_recovering", claim)
    monkeypatch.setattr(db_runs, "get_run", get)


@pytest.mark.parametrize("failing", ["finish", "incomplete", "read_back"])
def test_a_gate_pass_failing_after_its_fence_gives_it_back_and_the_next_pass_proves_again(world, monkeypatch, failing):
    process, store = world["process"], world["store"]
    target = process(alive=failing == "incomplete")
    rid, epoch = world["run"](process(alive=False), target)
    if failing == "incomplete":
        _lookup_fails(monkeypatch, target[0].pid)
        _fails_once(monkeypatch, "mark_recovery_incomplete")
    elif failing == "finish":
        _fails_once(monkeypatch, "finish_recovered_interrupted")
    else:
        _read_back_fails_once(monkeypatch)
    _expire_g_heartbeat(store)

    taken, outcome = _take_g("acquire")

    assert taken is False and outcome.kind == lock_manager.BUSY, outcome      # KEEP: nothing ended
    row, lock = _assert_not_released(store, rid, epoch)
    assert (row["recovery_state"], int(lock["protected"])) == ("none", 0)     # the fence was given back

    taken, outcome = _take_g("acquire")                                        # the store is back

    if failing == "incomplete":
        assert taken is False and outcome.kind == lock_manager.RECOVERY_REQUIRED, outcome
        row, lock = _assert_not_released(store, rid, epoch)
        assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
        assert int(lock["protected"]) == 1 and target[0].poll() is None
    else:
        assert taken is True, outcome
        row = _row(store, rid)
        assert (row["status"], row["error_code"], row["active_key"]) == ("failed", "interrupted_by_restart", None)


@pytest.mark.parametrize("tree", ["empty", "unproved"])
def test_a_recovering_row_left_behind_is_proved_again_under_its_fence_once_stale(world, monkeypatch, tree):
    process, store = world["process"], world["store"]
    target = process(alive=tree == "unproved")
    rid, epoch = world["run"](process(alive=False), target)
    if tree == "unproved":
        _lookup_fails(monkeypatch, target[0].pid)
    _fails_once(monkeypatch, "claim_recovering", after_write=True)           # nothing to undo with
    _expire_g_heartbeat(store)

    first, _ = _take_g("acquire")
    second, outcome = _take_g("acquire")             # inside the window a pass may still be proving it

    assert first is False and second is False and outcome.kind == lock_manager.BUSY, outcome
    row, lock = _assert_not_released(store, rid, epoch)
    assert (row["recovery_state"], int(lock["protected"])) == ("recovering", 0)

    store._execute("UPDATE tr_self_check_runs SET updated_at = ? WHERE self_check_run_id = ?",
                   [registry.add_seconds(now_iso(), -int(gate._recovering_stale_sec()) - 1), rid])
    taken, outcome = _take_g("acquire")

    if tree == "empty":
        assert taken is True, outcome
        row = _row(store, rid)
        assert (row["status"], row["error_code"], row["active_key"]) == ("failed", "interrupted_by_restart", None)
    else:
        assert taken is False and outcome.kind == lock_manager.RECOVERY_REQUIRED, outcome
        row, lock = _assert_not_released(store, rid, epoch)
        assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
        assert int(lock["protected"]) == 1 and target[0].poll() is None


def test_a_recovering_row_left_behind_by_an_unproved_owner_is_kept(world, monkeypatch):
    process, store = world["process"], world["store"]
    owner, target = process(alive=True), process(alive=True)
    rid, epoch = world["run"](owner, target)
    _lookup_fails(monkeypatch, owner[0].pid)
    store._execute("UPDATE server_instance SET status = 'dead' WHERE pid = ?", [owner[0].pid])
    store._execute("UPDATE tr_self_check_runs SET recovery_state = 'recovering', updated_at = ?"
                   " WHERE self_check_run_id = ?", [registry.add_seconds(now_iso(), -3600), rid])

    swept, _ = _take_g("sweeper")
    taken, outcome = _take_g("acquire")

    assert swept is False and taken is False and outcome.kind == lock_manager.BUSY, outcome
    row, lock = _assert_not_released(store, rid, epoch)
    assert (row["recovery_state"], int(lock["protected"])) == ("recovering", 0)
    assert owner[0].poll() is None and target[0].poll() is None


@pytest.mark.parametrize("unknown", ["owner_server", "target"])
def test_a_cancel_with_no_control_here_ends_nothing_unproved(world, monkeypatch, unknown):
    process, store, client = world["process"], world["store"], world["client"]
    if unknown == "owner_server":
        owner, target = process(alive=True), process(alive=True)       # owner alive, unreadable
        _lookup_fails(monkeypatch, owner[0].pid)
    else:
        owner, target = process(alive=False), process(alive=True)      # owner dead, target unreadable
        _lookup_fails(monkeypatch, target[0].pid)
    rid, epoch = world["run"](owner, target)

    response = client.post(f"{DRAFT_RUNS}/{rid}/cancel", headers=_as(RAW["new"]))

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "selfcheck_cancel_unconfirmed"
    assert error["details"]["cancel"] == {"accepted": True, "stopped": False, "state": "unconfirmed",
                                          "reason": proof.OWNER_SERVER_UNPROVEN if unknown == "owner_server"
                                          else proof.TREE_UNPROVEN}
    row, lock = _assert_not_released(store, rid, epoch)
    assert int(row["cancel_requested"]) == 1
    assert target[0].poll() is None, "a process whose identity could not be read was signalled"
    if unknown == "owner_server":
        assert (row["recovery_state"], int(lock["protected"])) == ("none", 0)
    else:
        # Fenced by the CAS and unproved: held like recover() holds it.
        assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
        assert int(lock["protected"]) == 1


def test_a_second_start_stays_already_running_and_names_the_holder(flow):
    client = flow["client"]
    client.app.include_router(routes.group_router)
    new = _as(RAW["new"])
    slow = client.post(DRAFT_RUNS, headers=new, json={"program": "python", "args": ["check_slow.py"]})
    assert slow.status_code == 202, slow.text
    rid = slow.json()["self_check_run_id"]
    _wait(client, f"{DRAFT_RUNS}/{rid}", new, statuses={"running"})

    busy = client.post(DRAFT_RUNS, headers=new, json={"program": "python", "args": ["check_ok.py"]})

    assert busy.status_code == 409
    error = busy.json()["error"]
    assert error["code"] == "selfcheck_already_running"
    assert error["details"]["self_check_run_id"] == rid
    holder = error["details"]["run"]
    assert {k: holder[k] for k in ("self_check_run_id", "group_id", "tr_doc_id", "draft", "owner_kind",
                                   "status", "same_owner")} == {
        "self_check_run_id": rid, "group_id": G, "tr_doc_id": None, "draft": True, "owner_kind": "draft",
        "status": "running", "same_owner": True}
    assert holder["created_at"] and "stdout_tail" not in holder and "args" not in holder
    # The summary names a read any worker token of the Group can follow; another Group's cannot.
    assert holder["group_read_path"] == f"/self-check/group/runs/{rid}"
    read = client.get("/api/v1" + holder["group_read_path"], headers=new)
    assert read.status_code == 200 and read.json()["status"] == "running"
    assert [r["self_check_run_id"] for r in client.get("/api/v1/self-check/group/runs", headers=new).json()["runs"]] == [rid]
    assert client.get("/api/v1" + holder["group_read_path"], headers=_as(RAW["new_other"])).status_code == 404
    # The help's Group operations are the registered routes.
    listed = {(m, "/api/v1" + p) for _n, m, p, _s in help_catalog.SELF_CHECK_GROUP_HTTP_OPERATIONS}
    assert listed == {(m, r.path) for r in routes.group_router.routes for m in r.methods if m != "HEAD"}
