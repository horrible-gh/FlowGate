"""0146: effective events, exact ownership and streaming before target exit."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services.ai_invoke import provider_cli, finalize, worker
from modules.flow_gate.services import process_runner
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_progress as progress
from modules.flow_gate.services import tr_self_check_service as service
from modules.flow_gate.db import tr_self_check_runs as db
from modules.flow_gate.db import connection


class Clock:
    now = 1000.
    def __call__(self):
        return self.now


class TickEvent:
    def __init__(self, clock, tick, limit=100):
        self.clock, self.tick, self.limit = clock, tick, limit
        self.ticks = 0
        self.flag = False
    def wait(self, interval):
        if self.flag or self.ticks >= self.limit:
            return True
        self.ticks += 1
        self.clock.now += interval
        self.tick(self.ticks)
        return False
    def is_set(self):
        return self.flag


class Proc:
    returncode = None
    def poll(self):
        return self.returncode


@pytest.fixture
def harness(monkeypatch):
    clock = Clock()
    kills = []
    monkeypatch.setattr(svc, "_now_mono", clock)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda _: 4)
    # Continuous CPU/child churn is weak activity, never effective progress.
    monkeypatch.setattr(process_runner, "process_tree_snapshot", lambda _: clock.now)
    monkeypatch.setattr(process_runner, "kill_process_tree", lambda p: kills.append(clock.now))
    run = {"run_id": "aiv_0146", "project_id": "p", "group_id": "g", "token_id": "t",
           "timeout_sec": 10, "started_mono": clock.now, "stall_anchor_mono": clock.now,
           "baseline_seq": 4, "cancel_event": threading.Event(), "self_check_watermark": 0}
    sample = {"seq": 0, "last": None}
    monkeypatch.setattr(progress, "observe", lambda _: dict(sample))
    return clock, kills, run, sample


def event(sample, kind, seq, run_id="scr_owned", status="running", count=0):
    sample.update(seq=seq, last={"self_check_run_id": run_id, "status": status,
                               "event_kind": kind, "progress_seq": seq, "output_bytes": count})


def run_loop(harness, tick, limit=100):
    clock, kills, run, sample = harness
    timer = TickEvent(clock, tick, limit)
    verdict = svc._progress_watchdog_loop(run, Proc(), timer, 1)
    return verdict, clock.now - run["started_mono"], len(kills)


def test_intermittent_events_extend_then_stall_from_last_event(harness):
    _, _, run, sample = harness
    def tick(n):
        if n in (3, 9, 17, 23):
            event(sample, {3:"created", 9:"state:running", 17:"output", 23:"state:completed"}[n],
                  n, status="completed" if n == 23 else "running", count=100000 if n >= 17 else 0)
    assert run_loop(harness, tick) == ("no_progress", 33, 1)
    assert run["stall_anchor_mono"] == 1023
    assert run["progress_observations"] == 4
    assert run["watchdog_kill"]["progress_evidence"]["self_check"]["status"] == "completed"


def test_normal_exit_after_long_test_is_preserved(harness):
    clock, kills, run, sample = harness
    proc = Proc()
    def tick(n):
        if n in (4, 12, 20):
            event(sample, "output", n)
        if n == 24:
            proc.returncode = 0
            run["outcome"] = "complete"
    verdict = svc._progress_watchdog_loop(run, proc, TickEvent(clock, tick), 1)
    assert verdict is None and not kills
    assert run["outcome"] == "complete" and proc.returncode == 0


@pytest.mark.parametrize("mode", ["none", "same_snapshot", "heartbeat", "historical"])
def test_no_event_is_never_replayed(harness, mode):
    _, _, run, sample = harness
    if mode != "none":
        event(sample, "state:completed", 7, status="completed")
        # A first snapshot with no known engine baseline only establishes a watermark.
        run.pop("self_check_watermark")
    def tick(n):
        if mode == "heartbeat":
            sample["last"]["updated_at"] = str(n)
    assert run_loop(harness, tick) == ("no_progress", 10, 1)
    assert not run.get("progress_observations")


def test_rotating_tail_same_length_counts_new_bytes_once(harness):
    _, _, run, sample = harness
    def tick(n):
        if n in (2, 8, 15):
            event(sample, "output", n, count=65536 * n)
    assert run_loop(harness, tick) == ("no_progress", 25, 1)
    assert run["progress_observations"] == 3


def test_read_failure_recovery_keeps_watermark_and_document_guard(harness, monkeypatch):
    clock, _, run, sample = harness
    def observe(_):
        if 4 <= clock.now - 1000 <= 7:
            raise OSError("unavailable")
        return sample
    monkeypatch.setattr(progress, "observe", observe)
    def tick(n):
        if n == 2:
            event(sample, "output", 1)
    assert run_loop(harness, tick) == ("no_progress", 12, 1)
    assert run["progress_observations"] == 1
    assert run["self_check_observation"] == "ok"


def test_permanent_observer_error_does_not_disable_document_progress(harness, monkeypatch):
    clock, _, run, _ = harness
    monkeypatch.setattr(progress, "observe", lambda _: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda _: 5 if clock.now >= 1008 else 4)
    assert run_loop(harness, lambda n: None) == ("no_progress", 18, 1)
    assert run["self_check_observation"] == "unavailable"
    assert run["last_document_progress"]["kind"] == "document"


@pytest.mark.parametrize("new_runs", [False, True])
def test_every_tick_progress_cannot_move_parent_absolute_cap(harness, monkeypatch, new_runs):
    _, _, run, sample = harness
    monkeypatch.setattr(provider_cli, "_absolute_cap_sec", lambda: 30)
    def tick(n):
        event(sample, "created" if new_runs else "output", n,
              run_id="scr_" + str(n) if new_runs else "scr_one")
    assert run_loop(harness, tick) == ("absolute_cap", 30, 1)
    assert run["started_mono"] == 1000


def test_user_cancel_and_natural_exit_win_kill_race(harness):
    clock, kills, run, _ = harness
    run["cancel_event"].set()
    assert not svc._claim_watchdog_kill(run, Proc(), TickEvent(clock, lambda n: None), "no_progress", 1010)
    assert not kills and not run.get("watchdog_kill")
    run["cancel_event"].clear()
    proc = Proc(); proc.returncode = 0
    assert not svc._claim_watchdog_kill(run, proc, TickEvent(clock, lambda n: None), "no_progress", 1010)
    assert not kills


def test_scope_binding_rejects_other_attempt_group_hop_and_independent_token():
    run = {"run_id":"a", "project_id":"p", "group_id":"g", "token_id":"token-one"}
    token = {"token_id":"token-one", "project":"p", "group_id":"g"}
    progress.open_attempt(run)
    try:
        first = progress.owner_for(token, "p", "g")
        assert first and first["ai_run_id"] == "a"
        assert progress.owner_for({**token, "group_id":"other"}, "p", "g") is None
        assert progress.owner_for({**token, "token_id":"independent"}, "p", "g") is None
        progress.bind_token(run, "token-two")
        assert progress.owner_for(token, "p", "g") is None
        second = progress.owner_for({**token,"token_id":"token-two"}, "p", "g")
        assert second["scope_id"] != first["scope_id"]
        assert second["provider_attempt_id"] == first["provider_attempt_id"]
        progress.open_attempt(run)
        third = progress.owner_for(token, "p", "g")
        assert third["provider_attempt_id"] != first["provider_attempt_id"]
        assert third["hop_id"] == first["hop_id"]  # retry stays in the logical hop
        run["submission_hop"] = 1
        progress.open_attempt(run)
        next_hop = progress.owner_for(token, "p", "g")
        assert next_hop["hop_id"] != first["hop_id"]
    finally:
        progress.close_attempt(run)
    assert progress.owner_for(token, "p", "g") is None


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper
    path = migrated_sqlite_db("progress0146.db")
    real = object.__new__(connection.FlowGateStore)
    real._db, real._sq = SQLiteWrapper(path), None
    monkeypatch.setattr(db, "get_store", lambda: real)
    real._execute("INSERT INTO projects(project_id,project_name,created_at,updated_at) VALUES ('p','P','now','now')")
    real._execute("INSERT INTO groups(group_id,project_id,module,title,status,created_at,updated_at) VALUES ('g','p','v02','G','OPEN','now','now')")
    real._execute("INSERT INTO documents(doc_id,project_id,group_id,module,type_code,seq,title,status,branch,created_at,updated_at) VALUES ('doc','p','g','v02','TR',1,'TR','draft','main','now','now')")
    try:
        yield real
    finally:
        real._db.close()


def pending(owner):
    return db.create_pending("p", "g", "doc", None, "1", "pytest", [], "pytest", "pytest", "host", ".", 10, [], progress_owner=owner)


def test_db_events_are_monotonic_and_scope_isolation_survives_linking(store):
    parent = {"run_id":"a", "project_id":"p", "group_id":"g", "token_id":"t"}
    token = {"token_id":"t", "project":"p", "group_id":"g"}
    progress.open_attempt(parent)
    try:
        owner = progress.owner_for(token, "p", "g")
        row = pending(owner)
        rid = row["self_check_run_id"]
        assert progress.observe(parent)["seq"] == 1
        db.record_effective_progress(rid, status="running")
        db.record_effective_progress(rid, output_bytes=65536)
        db.record_effective_progress(rid, output_bytes=131072)
        assert progress.observe(parent)["seq"] == 4
        for _ in range(3):
            db.record_effective_progress(rid, status="pending")  # delayed admission SSE
            db.record_effective_progress(rid, status="running", output_bytes=131072)
        # Document linkage changes neither ownership nor seq (same row / token).
        store._execute("UPDATE tr_self_check_runs SET linked_at = 'linked' WHERE self_check_run_id = ?", [rid])
        assert progress.observe(parent)["seq"] == 4
        db.record_effective_progress(rid, status="completed")
        db.record_effective_progress(rid, status="completed")
        assert progress.observe(parent)["seq"] == 5
        db.finish_completed(rid, exit_code=0, timed_out=False, stdout_tail="", stderr_tail="")
        progress.open_attempt(parent)
        assert progress.observe(parent) == {"seq":0, "last":None}
        # Old-attempt output cannot extend the replacement, even with the same token/TR.
        db.record_effective_progress(rid, output_bytes=200000)
        assert progress.observe(parent)["seq"] == 0
        other = pending(None)
        db.record_effective_progress(other["self_check_run_id"], status="running", output_bytes=9)
        assert progress.observe(parent)["seq"] == 0
    finally:
        progress.close_attempt(parent)


def test_owner_insert_rolls_back_on_invalid_group(store):
    owner = dict(scope_id="s", ai_run_id="a", hop_id="h", provider_attempt_id="try",
                 owner_token_id="t", project_id="p", group_id="wrong")
    with pytest.raises(ValueError):
        pending(owner)
    assert db.get_active("p", "g") is None


def test_short_flushed_output_is_observed_while_target_is_still_alive(tmp_path):
    release = tmp_path / "release"
    code = ("import pathlib,time; print('x',flush=True); "
            "p=pathlib.Path(" + repr(str(release)) + "); "
            "deadline=time.monotonic()+5\n"
            "while not p.exists() and time.monotonic()<deadline: time.sleep(.02)\n")
    control, _ = executor.spawn([sys.executable,"-c",code],tmp_path,dict(os.environ),8,"scr_stream0146")
    seen = []
    def arrived(count):
        seen.append((count, control.proc.poll()))
        release.write_text("ok")
    control.progress = arrived
    try:
        result = executor.wait(control,8,threading.Event())
        assert result.exit_code == 0 and not result.timed_out
        assert seen and seen[0][0] >= 2 and seen[0][1] is None
        assert release.exists() and result.stdout_tail.strip() == "x"
    finally:
        control.close()


@pytest.mark.parametrize("noisy", [False, True])
def test_hung_selfcheck_hits_own_timeout_even_when_output_continues(tmp_path, noisy):
    code = "import time\nwhile True: " + ("print('tick',flush=True); " if noisy else "") + "time.sleep(.02)"
    control, _ = executor.spawn([sys.executable,"-c",code],tmp_path,dict(os.environ),1,"scr_hung0146")
    counts=[]; control.progress=counts.append
    try:
        result=executor.wait(control,1,threading.Event())
        assert result.timed_out and control.proc.poll() is not None
        assert bool(counts) == noisy
    finally:
        control.close()


def test_posix_protocol_progress_frames_are_consumed_before_result():
    class ProtocolProc:
        def __init__(self):
            self.stdout=io.BytesIO(b'{"event":"progress","output_bytes":2}\n'
                                 b'{"event":"progress","output_bytes":65538}\n'
                                 b'{"event":"result","exit_code":0,"stdout_tail":"ok"}\n')
            self.returncode=None
        def wait(self,timeout):
            self.returncode=0
        def poll(self):
            return self.returncode
    proc=ProtocolProc()
    control=executor.ProcessControl(proc)
    seen=[]; control.progress=lambda count: seen.append((count,proc.poll()))
    result=executor.wait(control,5,threading.Event())
    assert seen[0] == (2,None) and seen[-1] == (65538,None)
    assert result.exit_code == 0 and result.stdout_tail == "ok"


def test_direct_reader_uses_short_reads_and_counts_beyond_bounded_tail():
    output=bytearray(); counts=[]
    executor._read_tail(io.BytesIO(b"x"*140000),output,executor.OutputProgress(counts.append))
    assert len(output)==65536
    # The final forced flush is the waiter's responsibility.
    reporter=executor.OutputProgress(counts.append)
    executor._read_tail(io.BytesIO(b"x"*140000),bytearray(),reporter); reporter.flush()
    assert counts[-1]==140000


def test_diagnosis_preserves_separate_progress_and_legacy_unmarked_data(harness):
    _, _, run, sample=harness
    def tick(n):
        if n == 3:
            event(sample,"output",1,count=2)
    assert run_loop(harness,tick) == ("no_progress",13,1)
    run["duration_ms"]=13000
    kind,line=finalize._resolve_timeout_diagnostics(run)
    assert kind == "no_progress" and "10 sec of 13 sec total" in line
    assert '"self_check"' in line and '"document": null' in line
    assert '"stall_basis": "self_check"' in line and '"output_bytes": 2' in line
    assert finalize._resolve_timeout_diagnostics({"duration_ms":100}) == (None,None)


def test_verified_service_admission_captures_current_scope(monkeypatch, store):
    from contextlib import nullcontext
    from modules.flow_gate.services import graph_source_hooks
    token = {"token_id":"t", "project":"p", "group_id":"g"}
    parent = {"run_id":"a", "project_id":"p", "group_id":"g", "token_id":"t"}
    target = {"project_id":"p", "group_id":"g", "tr_doc_id":"doc", "caller_token":token}
    progress.open_attempt(parent)
    monkeypatch.setattr(graph_source_hooks,"write_session",nullcontext)
    monkeypatch.setattr(service,"_attempt_target",lambda *a: {})
    monkeypatch.setattr(service,"_admit",lambda target,*a: target)
    try:
        admitted=service._start(target,{},None)
        assert admitted["progress_owner"]["scope_id"] == parent["self_check_scope"]["scope_id"]
        independent={**target,"caller_token":{**token,"token_id":"independent"}}
        assert service._start(independent,{},None)["progress_owner"] is None
    finally:
        progress.close_attempt(parent)


def test_draft_document_link_preserves_exact_child_and_watermark(store):
    store._execute("INSERT INTO users(user_id,username,email,password,created_at,updated_at) VALUES ('u','u0146','u0146@t','x','now','now')")
    store._execute("INSERT INTO tokens(token_id,hash,pepper_id,project,group_id,doc_ref,action_scope,issued_to,created_at,expires_at) VALUES ('t','hash0146','pep','p','g','doc','new','u','now','9999-12-31T00:00:00+00:00')")
    parent={"run_id":"a", "project_id":"p", "group_id":"g", "token_id":"t"}
    progress.open_attempt(parent)
    try:
        owner=progress.owner_for({"token_id":"t","project":"p","group_id":"g"},"p","g")
        row=db.create_pending("p","g",None,None,"1","pytest",[],"pytest","pytest","host",".",10,[],
                              owner_token_id="t",draft_doc_ref="doc",progress_owner=owner)
        assert progress.consume(parent,"first")
        before=progress.observe(parent)
        assert db.link_owner_runs("t","doc","p","g") == 1
        assert progress.observe(parent)==before
        assert not progress.consume(parent,"linked")
        db.record_effective_progress(row["self_check_run_id"],status="running")
        assert progress.consume(parent,"running")
        assert not progress.consume(parent,"again")
    finally:
        progress.close_attempt(parent)


def test_real_db_events_drive_watchdog_to_last_event_plus_threshold(store,harness,monkeypatch):
    _,_,parent,_=harness
    progress.open_attempt(parent)
    scope=parent["self_check_scope"]["scope_id"]
    monkeypatch.setattr(progress,"poll",progress.consume)
    monkeypatch.setattr(progress,"observe",lambda _:db.observe_effective_progress(scope))
    try:
        owner=progress.owner_for({"token_id":"t","project":"p","group_id":"g"},"p","g")
        rid=pending(owner)["self_check_run_id"]
        def tick(n):
            if n==3: db.record_effective_progress(rid,status="running")
            if n==9: db.record_effective_progress(rid,output_bytes=2)
            if n==17: db.record_effective_progress(rid,status="completed")
        assert run_loop(harness,tick)==("no_progress",27,1)
        assert parent["progress_observations"]==4  # create at tick 1, then 3/9/17
        assert parent["stall_anchor_mono"]==1017
    finally:
        progress.close_attempt(parent)


def test_posix_supervisor_sends_short_output_before_terminal_frame(monkeypatch,tmp_path):
    from modules.flow_gate.services import tr_self_check_supervisor as supervisor
    payload={"argv":["test"],"cwd":str(tmp_path),"env":{},"timeout_seconds":5}
    output=io.StringIO()
    monkeypatch.setattr(supervisor,"sys",SimpleNamespace(
        stdin=SimpleNamespace(buffer=io.BytesIO((json.dumps(payload)+"\n").encode())),stdout=output))
    proc=SimpleNamespace(pid=123,stdout=io.BytesIO(b"x\n"),stderr=io.BytesIO(b"warn\n"),returncode=0)
    monkeypatch.setattr(supervisor.subprocess,"Popen",lambda *a,**k:proc)
    monkeypatch.setattr(supervisor.executor,"start_identity",lambda _:"identity")
    monkeypatch.setattr(supervisor,"_limit",lambda _:None)
    monkeypatch.setattr(supervisor,"_terminate",lambda *a:None)
    monkeypatch.setattr(supervisor,"_progress_frame",lambda count:
                        output.write(json.dumps({"event":"progress","output_bytes":count})+"\n"))
    polls=[]
    def exited(_):
        polls.append(1)
        return len(polls)>2
    monkeypatch.setattr(supervisor,"_leader_exited",exited)
    class Selector:
        def register(self,*a):pass
        def select(self,*a):return []
        def close(self):pass
    monkeypatch.setattr(supervisor.selectors,"DefaultSelector",Selector)
    supervisor.main()
    frames=[json.loads(line) for line in output.getvalue().splitlines()]
    assert frames[0]["event"]=="started" and frames[-1]["event"]=="result"
    updates=[f for f in frames if f["event"]=="progress"]
    assert updates and updates[-1]=={"event":"progress","output_bytes":7}
    assert frames[-1]["stdout_tail"]=="x\n" and frames[-1]["stderr_tail"]=="warn\n"


def test_token_adoption_cannot_race_a_successful_old_scope_read(monkeypatch):
    parent={"run_id":"a", "project_id":"p", "group_id":"g", "token_id":"t"}
    entered,release,adopted=threading.Event(),threading.Event(),threading.Event()
    progress.open_attempt(parent)
    def observe(_):
        entered.set()
        assert release.wait(3)
        return {"seq":1,"last":{"self_check_run_id":"old","event_kind":"output"}}
    monkeypatch.setattr(progress,"observe",observe)
    reader=threading.Thread(target=progress.consume,args=(parent,"old-time"))
    def adopt():
        progress.bind_token(parent,"next")
        adopted.set()
    writer=threading.Thread(target=adopt)
    try:
        reader.start(); assert entered.wait(3)
        writer.start()
        assert not adopted.wait(.02)
        release.set(); reader.join(3); writer.join(3)
        assert not reader.is_alive() and not writer.is_alive() and adopted.is_set()
        assert parent["last_self_check_progress"] is None
        assert parent["self_check_watermark"]==0
    finally:
        release.set(); reader.join(3)
        if writer.ident:writer.join(3)
        progress.close_attempt(parent)


def test_blocked_optional_query_is_bounded_and_does_not_disable_watchdog(harness,monkeypatch):
    _,_,parent,_=harness
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def blocked(scope):
        calls.append(scope); entered.set()
        assert release.wait(3)
        return {"seq":99,"last":{"self_check_run_id":"late"}}
    monkeypatch.setattr(db,"observe_effective_progress",blocked)
    progress.open_attempt(parent)
    thread=None
    try:
        assert not progress.poll(parent,"begin")
        assert entered.wait(3)
        thread=progress._probe["thread"]
        assert run_loop(harness,lambda n:None)==("no_progress",10,1)
        assert len(calls)==1 and parent["self_check_observation"]=="unavailable"
        # Close before the hung read returns: its stale event must never enter a later hop.
        progress.close_attempt(parent)
        release.set(); thread.join(3)
        assert not thread.is_alive() and progress._probe is None
        assert not parent.get("last_self_check_progress")
    finally:
        release.set()
        if thread: thread.join(3)
        progress.close_attempt(parent)


def test_async_probe_success_error_and_recovery_never_replay_watermark(monkeypatch):
    parent={"run_id":"a", "project_id":"p", "group_id":"g", "token_id":"t"}
    value=[1]; broken=[False]
    def query(_):
        if broken[0]: raise OSError("probe unavailable")
        return {"seq":value[0],"last":{"self_check_run_id":"scr_current","progress_seq":value[0]}}
    monkeypatch.setattr(db,"observe_effective_progress",query)
    progress.open_attempt(parent)
    def settle():
        with progress._lock:
            job=progress._probe
        if job:
            job["thread"].join(3)
            assert not job["thread"].is_alive()
    try:
        assert not progress.poll(parent,"first"); settle()
        assert progress.poll(parent,"created"); settle()
        assert not progress.poll(parent,"repeat"); settle()
        value[0]=2
        assert not progress.poll(parent,"pending"); settle()
        assert progress.poll(parent,"output"); settle()
        broken[0]=True
        assert not progress.poll(parent,"before-error"); settle()
        broken[0]=False
        assert not progress.poll(parent,"error")
        assert parent["self_check_observation"]=="unavailable"
        settle()
        assert not progress.poll(parent,"recovered-same-seq"); settle()
        assert parent["self_check_watermark"]==2
    finally:
        settle(); progress.close_attempt(parent)


@pytest.mark.parametrize("full",[False,True])
def test_posix_progress_pipe_backpressure_never_blocks_timeout_loop(monkeypatch,full):
    from modules.flow_gate.services import tr_self_check_supervisor as supervisor
    modes=[]; frames=[]
    def write(fd,frame):
        assert fd==7
        frames.append(json.loads(frame))
        if full:raise BlockingIOError()
        return len(frame)
    monkeypatch.setattr(supervisor,"sys",SimpleNamespace(stdout=SimpleNamespace(fileno=lambda:7)))
    monkeypatch.setattr(supervisor,"os",SimpleNamespace(get_blocking=lambda fd:True,
                        set_blocking=lambda fd,mode:modes.append(mode),write=write))
    assert supervisor._progress_frame(65538) is (not full)
    assert modes==[False,True]
    assert frames==[{"event":"progress","output_bytes":65538}]
