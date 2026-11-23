"""(A1): a review loop waits for the questions its AI asked inside the loop.

R 0051.0001 A: "검수 반복 중 수정 AI가 질문을 남기면, 다음 검수나 수정을 시작하지 않는다 … 답이 달리면 그 답을 근거로
이어서 수정한다". The v02test.0032 exception ("the loop does not wait") is gone.

Part 1 drives the REAL worker hop loop (``worker._worker``) with the REAL loop checkpoint
(``review._checkpoint_document_review_loop``), the real park (``question_loop_wait.park``) and the real wait table
(migration 200) on one SQLite connection, as ``test_ai_invoke_document_review_loop_0417`` does; only the provider, the
Graph ledger row and the question read are stand-ins. Part 2 drives the real question wait (``question_wait``) and the
real resume (``question_loop_wait.resume``, the wait table's CAS) with the Graph ledger / fence / run state as
recorders: dispatch to the card's responder, the answers (a person's, the responder AI's, a chat AI's), exactly one
resume (repeated events, a restart), pause/stop, a new-attempt answer, several questions, a responder that could not be
assigned, a gone attempt, the successor's hand-off. Part 3: the guards (owner evidence, run end, verdict, card badge).

r2 (rework after review): Part 1 also covers the hold — a failed question read, a wait that cannot be stored or
no wait store never let the next hop start (the loop stops with a ``question_wait_unconfirmed`` detail, the run carries
the diagnosis); Part 2 the answer that comes while the parked run is still finishing (never a cancellation; resumed
once after the run's end). Part 4 is the end-to-end path on the REAL Graph ledger (migrations 151/152/153a/154), the
real fence and Flow-guard transaction, the real ``execution.launch`` / ``execution.finish`` / ``terminal.cleanup``
(execution end and group lease release), ``execution.replay`` -> ``legacy_adapter.start_review_loop`` ->
``execution.request_loop`` -> ``scheduling.admit`` -> ``execution.loop_state`` -> the real loop-row insert and
hand-off, and a real successor ``worker._worker``: park -> run end and lease release -> the review AI's answer -> the
stored stage runs once, under repeated answers, run reads, duplicate replays, a restart and a lost CAS.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from flowgate.card_settings import question_loop_wait as qlw
from flowgate.card_settings import question_wait as wait
from modules.flow_gate.db import question_loop_waits as db_waits

MIGRATIONS = Path(__file__).resolve().parents[2] / "sql" / "migrations"
FLOW = "flowgate.v02test.0051"
DOC = "flowgate.v02test.0051.0005-T"
RUN = "aiv2_loop_run"
EXEC = "exe_loop_1"
ATTEMPT = "att_1"
GRAPH_RUN = "grun_1"
NODE = {"node_key": "card", "node_type": "document", "doc_type": "T"}


class Store:
    """The SQLite connection behind every module the loop path touches (as the 0417 suite builds it)."""

    def __init__(self, conn):
        self.conn = conn

    def _execute(self, sql, values=()):
        cursor = self.conn.execute(sql, values)
        self.conn.commit()
        return cursor

    def _execute_affected(self, sql, values=()):
        return self.conn.execute(sql, values).rowcount

    def _fetch_one(self, sql, values=()):
        row = self.conn.execute(sql, values).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, values=()):
        return [dict(row) for row in self.conn.execute(sql, values).fetchall()]

    def table_exists(self, name):
        return self._fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name]) is not None

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


def _wait_table(conn):
    conn.executescript((MIGRATIONS / "sqlite" / "203_question_loop_waits.sql").read_text(encoding="utf-8"))


# ── Part 1: the real worker hop loop and checkpoint ─────────────────────────────

LOOP = {
    "review_count": 2, "reviewer_provider_id": "reviewer", "review_criteria": "document_type_default",
    "rework_provider_id": "reworker", "rework_timeout_sec": 1800, "rework_message": "fix every finding",
    "failure_restart_max_attempts": 0, "total_timeout_sec": 3600, "review_baseline_id": 0,
    "baseline_revision_no": 3, "starts_with_rework": False, "round_no": 1, "current_stage": "review",
    "attempts_used": 0,
}


@pytest.fixture
def world(monkeypatch):
    from flowgate.graph_run import execution_store as ledger, fencing
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.services import ai_invoke_service as service
    from modules.flow_gate.services import graph_execution_hooks
    from modules.flow_gate.services.ai_invoke import finalize
    from modules.flow_gate.services.ai_invoke import worker as worker_module

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT UNIQUE NOT NULL, project_id TEXT,
            group_id TEXT, revision_no INTEGER NOT NULL, doc_review_status TEXT NOT NULL, meta TEXT,
            rejection_reason TEXT, rejection_history TEXT, updated_at TEXT);
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
        CREATE TABLE document_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL, reviewer_id TEXT NOT NULL, verdict TEXT NOT NULL, findings TEXT NOT NULL,
            comment TEXT, reviewed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    for name in ("091_ai_invoke_document_review_loops.sql", "092_ai_invoke_document_review_loop_live_run.sql"):
        conn.executescript((MIGRATIONS / "sqlite" / name).read_text(encoding="utf-8"))
    _wait_table(conn)
    conn.execute("INSERT INTO groups VALUES (?)", (FLOW,))
    conn.execute("INSERT INTO documents(doc_id,project_id,group_id,revision_no,doc_review_status,updated_at) "
                 "VALUES (?, 'flowgate', ?, 3, 'pending_review', '2026-11-19T00:00:00+00:00')", (DOC, FLOW))
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",), ("reworker",)])
    conn.commit()
    store = Store(conn)
    from modules.flow_gate.db import review_count_exclusions as db_exclusions_store
    # (A2): the exclusion store reads this connection too (no migration 201 here: nothing is marked)
    for module in (db_loops, service, service.db_docs, db_reviews, db_waits, db_exclusions_store):
        monkeypatch.setattr(module, "get_store", lambda: store)

    state = {"asked": {}, "hops": [], "hooks": [], "tokens": 0, "rejects": 0, "act": {}}
    monkeypatch.setattr(finalize, "open_questions_raised_by_run",
                        lambda group_id, run_id: [dict(i) for i in state["asked"].get(run_id, [])])
    monkeypatch.setattr(ledger, "get", lambda exec_id: {
        "exec_id": exec_id, "attempt_id": ATTEMPT, "run_id": GRAPH_RUN, "kind": "review", "status": "running",
        "assignment_stage": "result_review", "assignment_seq": 1, "request_json": "{}"})
    monkeypatch.setattr(fencing, "attempt_row", lambda attempt_id: {
        "attempt_id": attempt_id, "node_key": "card", "attempt_status": "active", "run_status": "running"})
    monkeypatch.setattr(graph_execution_hooks, "call", lambda name, *a, **k: state["hooks"].append(name))

    def execute(run, chain, prompt):
        stage = db_loops.get(run["run_id"])["current_stage"]
        state["hops"].append(stage)
        stamp = f"2026-11-19T00:00:0{len(state['hops'])}+00:00"
        act = state["act"].get(len(state["hops"]), {})
        if stage == "rework" and act.get("revise", True):
            conn.execute("UPDATE documents SET revision_no = revision_no + 1 WHERE doc_id = ?", (DOC,))
        if stage == "review" and act.get("verdict"):
            revision = conn.execute("SELECT revision_no FROM documents WHERE doc_id = ?", (DOC,)).fetchone()[0]
            conn.execute("INSERT INTO document_reviews(doc_id,revision_no,reviewer_id,verdict,findings,comment,"
                         "reviewed_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (DOC, revision, "reviewer", act["verdict"],
                          json.dumps(["F1"] if act["verdict"] == "issues" else []), None, stamp, stamp, stamp))
        conn.commit()
        if act.get("ask"):
            state["asked"].setdefault(run["run_id"], []).append(
                {"id": act["ask"], "asker_kind": "ai", "asker_ai_run_id": run["run_id"], "anchor_doc_id": DOC,
                 "asker_actual_provider_id": run.get("provider_id")})
        return True

    def auto_reject(slot, review_row, ctx):
        state["rejects"] += 1
        conn.execute("UPDATE documents SET doc_review_status = 'rejected' WHERE doc_id = ?", (DOC,))
        conn.commit()
        return {"ok": True}

    def prepare(run):
        state["tokens"] += 1
        return {"mention": "token"}

    monkeypatch.setattr(service, "_execute_provider_chain", execute)
    monkeypatch.setattr(service, "_auto_reject", auto_reject)
    monkeypatch.setattr(service, "_classify_end_reason", lambda item, ok: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_judge_hop", lambda item: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_prepare_retry_token", prepare)
    monkeypatch.setattr(service, "_reset_attempt_state", lambda item: None)
    monkeypatch.setattr(worker_module, "_start_post_process_recovery", lambda item: (None, None))
    monkeypatch.setattr(worker_module, "_stop_post_process_recovery", lambda *a, **k: None)
    monkeypatch.setattr(service, "_broadcast", lambda run, event_type, payload: None)
    monkeypatch.setattr(service, "_finalize_run", lambda item: item.update(status="finished"))
    monkeypatch.setattr(service.ai_settings_service, "resolve_effective", lambda project: {
        "providers": [{"id": "reviewer", "name": "Reviewer"}, {"id": "reworker", "name": "Reworker"}]})

    def start(stage="review", graph=True, **loop_fields):
        started = datetime.now(timezone.utc)
        loop = {**LOOP, **loop_fields, "run_id": RUN, "group_id": FLOW, "doc_ref": DOC, "current_stage": stage,
                "starts_with_rework": stage == "rework", "started_at": started.isoformat(),
                "deadline_at": (started + timedelta(hours=1)).isoformat(), "stop_reason": None, "stop_detail": None}
        if stage == "rework":
            conn.execute("UPDATE documents SET doc_review_status = 'rejected' WHERE doc_id = ?", (DOC,))
            conn.commit()
        run = {"run_id": RUN, "project_id": "flowgate", "issued_to": "owner",
               "api_base_url": "http://127.0.0.1/flowgate/api/v1", "group_id": FLOW, "doc_ref": DOC,
               "mode": "single", "document_review_loop": db_loops.insert(loop), "started_at": loop["started_at"],
               "docs_target": 0, "chain_id": "loop", "chain_docs_target": 0, "chain_docs_reached": 0,
               "attempts_used": 0, "fallback_history": [], "action_scope": "edit" if stage == "rework" else "review",
               "hop_kind": stage, "cancel_event": threading.Event()}
        if graph:
            run["graph_exec_id"] = EXEC
        provider = "reworker" if stage == "rework" else "reviewer"
        run["provider_id"] = provider
        service._worker(run, [{"id": provider, "name": provider.title()}], "go")
        return run

    yield {"state": state, "start": start, "loop": lambda: db_loops.get(RUN), "conn": conn,
           "waits": lambda: [db_waits._decode(r) for r in store._fetch_all("SELECT * FROM question_loop_waits")]}
    conn.close()


def test_a_rework_ai_that_asks_does_not_start_the_next_review_and_parks(world):
    world["state"]["act"] = {1: {"ask": 901}}                      # the rework hop revises and asks
    run = world["start"]("rework")
    assert world["state"]["hops"] == ["rework"]                      # no review hop started
    assert "transition" not in world["state"]["hooks"]               # no next Graph execution
    assert world["state"]["tokens"] == 0                             # no next token
    loop = world["loop"]()
    assert (loop["current_stage"], loop["stop_reason"], loop["round_no"]) == ("review", None, 1)
    [row] = world["waits"]()
    assert row["wait_id"] == run["question_loop_wait_id"]
    assert (row["status"], row["resume_stage"], row["resume_round_no"], row["asked_hop_kind"]) == \
        ("waiting", "review", 1, "rework")
    assert (row["graph_exec_id"], row["graph_attempt_id"], row["graph_run_id"], row["node_key"]) == \
        (EXEC, ATTEMPT, GRAPH_RUN, "card")
    assert (row["assignment_stage"], row["assignment_seq"]) == ("result_review", 1)
    assert row["question_item_ids"] == [901] and row["hop_outcome"] == "succeeded" and row["waited_at"]
    assert row["answered_at"] is None and row["ended_at"] is None


def test_a_hop_that_only_asked_is_a_question_wait_never_a_failed_attempt(world):
    # failure_restart_max_attempts 0: before, a rework that wrote nothing (it only asked) stopped the loop as
    # retry_exhausted; now the same stage is reserved without spending the retry budget, and the loop parks.
    world["state"]["act"] = {1: {"ask": 902, "revise": False}}
    world["start"]("rework")
    assert world["state"]["hops"] == ["rework"]
    loop = world["loop"]()
    assert (loop["current_stage"], loop["stop_reason"], loop["attempts_used"]) == ("rework", None, 0)
    assert loop["last_hop_outcome"] == "failed"
    [row] = world["waits"]()
    assert (row["resume_stage"], row["hop_outcome"], row["resume_attempts_used"]) == ("rework", "failed", 0)


def test_a_reviewer_that_rejects_and_asks_does_not_start_the_next_rework(world):
    world["state"]["act"] = {1: {"verdict": "issues", "ask": 903}}
    world["start"]("review")
    assert world["state"]["hops"] == ["review"] and world["state"]["rejects"] == 1   # the real rejection stands
    assert world["state"]["tokens"] == 0 and "transition" not in world["state"]["hooks"]
    loop = world["loop"]()
    assert (loop["current_stage"], loop["round_no"]) == ("rework", 2)
    [row] = world["waits"]()
    assert (row["resume_stage"], row["resume_round_no"], row["asked_hop_kind"], row["asked_round_no"]) == \
        ("rework", 2, "review", 1)


def test_an_until_passed_loop_parks_the_same_way(world):
    world["state"]["act"] = {1: {"verdict": "issues"}, 2: {"ask": 904}}
    world["start"]("review", review_count=-1)
    assert world["state"]["hops"] == ["review", "rework"]            # the question came on the rework hop
    assert world["state"]["hooks"].count("transition") == 1          # review -> rework only
    [row] = world["waits"]()
    assert (row["resume_stage"], row["asked_hop_kind"]) == ("review", "rework")


def test_a_loop_without_questions_goes_on_as_before(world):
    world["state"]["act"] = {1: {}, 2: {"verdict": "pass"}}
    world["start"]("rework")
    assert world["state"]["hops"] == ["rework", "review"]
    assert world["state"]["hooks"].count("transition") == 1 and world["state"]["tokens"] == 1
    assert world["loop"]()["stop_reason"] == "review_passed"
    assert world["waits"]() == []


def test_a_loop_with_no_graph_execution_keeps_its_earlier_behaviour(world):
    # only a Graph loop can be resumed (its execution ledger); any other loop is not parked
    world["state"]["act"] = {1: {"ask": 905}, 2: {"verdict": "pass"}}
    world["start"]("rework", graph=False)
    assert world["state"]["hops"] == ["rework", "review"]
    assert world["waits"]() == []


def _held_loop(world, reason):
    loop = world["loop"]()
    assert (loop["current_stage"], loop["stop_reason"]) == ("stopped", "retry_exhausted")
    assert loop["stop_detail"] == "question_wait_unconfirmed: " + reason
    return loop


def test_a_failed_question_read_holds_the_next_hop_with_a_diagnosis(world, monkeypatch):
    # "nothing open" is only ever a successful read: an unreadable question store never lets the loop go on
    from modules.flow_gate.services.ai_invoke import finalize
    monkeypatch.setattr(qlw, "_RETRY_DELAYS", (0, 0))
    reads = []

    def unreadable(group_id, run_id):
        reads.append(run_id)
        raise RuntimeError("question store unreadable")

    monkeypatch.setattr(finalize, "open_questions_raised_by_run", unreadable)
    world["state"]["act"] = {1: {"ask": 906}, 2: {"verdict": "pass"}}
    run = world["start"]("rework")
    assert len(reads) == 3                                           # read again twice before holding
    assert world["state"]["hops"] == ["rework"]                      # the next review never started
    assert "transition" not in world["state"]["hooks"] and world["state"]["tokens"] == 0
    assert world["waits"]() == []
    _held_loop(world, "question_read_failed")
    hold = run["question_loop_wait_hold"]
    assert (hold["status"], hold["reason"], hold["stage"], hold["loop_stopped"]) == \
        ("held", "question_read_failed", "review", True)
    assert (run["end_reason"], run["outcome"]) == ("question_wait_unconfirmed", "none")
    assert "question_read_failed" in run["last_message"]


def test_open_questions_whose_wait_cannot_be_stored_hold_the_next_hop(world, monkeypatch):
    monkeypatch.setattr(qlw, "_RETRY_DELAYS", (0, 0))
    inserts = []

    def broken(row):
        inserts.append(row["loop_run_id"])
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db_waits, "insert", broken)
    world["state"]["act"] = {1: {"verdict": "issues", "ask": 907}, 2: {}}
    run = world["start"]("review")
    assert len(inserts) == 3
    assert world["state"]["hops"] == ["review"] and world["state"]["rejects"] == 1   # no rework hop after it
    assert "transition" not in world["state"]["hooks"] and world["state"]["tokens"] == 0
    _held_loop(world, "wait_store_failed")
    assert run["question_loop_wait_hold"]["question_item_ids"] == [907]
    assert run["question_loop_wait_hold"]["stage"] == "rework"


def test_open_questions_with_no_wait_store_hold_and_no_question_still_goes_on(world, monkeypatch):
    monkeypatch.setattr(db_waits, "available", lambda: False)
    world["state"]["act"] = {1: {"ask": 908}, 2: {"verdict": "pass"}}
    run = world["start"]("rework")
    assert world["state"]["hops"] == ["rework"] and world["state"]["tokens"] == 0
    _held_loop(world, "wait_store_unavailable")
    assert run["question_loop_wait_hold"]["question_item_ids"] == [908]


def test_without_a_wait_store_a_loop_that_asked_nothing_goes_on(world, monkeypatch):
    monkeypatch.setattr(db_waits, "available", lambda: False)
    world["state"]["act"] = {1: {}, 2: {"verdict": "pass"}}
    world["start"]("rework")
    assert world["state"]["hops"] == ["rework", "review"]            # a confirmed "none open" needs no store
    assert world["loop"]()["stop_reason"] == "review_passed"


def test_a_transient_failure_is_tried_again_and_a_stored_wait_is_never_doubled(world, monkeypatch):
    from modules.flow_gate.services.ai_invoke import finalize
    monkeypatch.setattr(qlw, "_RETRY_DELAYS", (0, 0))
    read = finalize.open_questions_raised_by_run
    failures = {"read": 1, "insert": 1}

    def flaky_read(group_id, run_id):
        if failures["read"]:
            failures["read"] -= 1
            raise RuntimeError("connection reset")
        return read(group_id, run_id)

    insert = db_waits.insert

    def insert_then_lose_the_reply(row):
        stored = insert(row)
        if failures["insert"]:
            failures["insert"] -= 1
            raise RuntimeError("reply lost after the commit")
        return stored

    monkeypatch.setattr(finalize, "open_questions_raised_by_run", flaky_read)
    monkeypatch.setattr(db_waits, "insert", insert_then_lose_the_reply)
    world["state"]["act"] = {1: {"ask": 909}}
    run = world["start"]("rework")
    assert world["state"]["hops"] == ["rework"] and "question_loop_wait_hold" not in run
    [row] = world["waits"]()                                         # the retry took the stored row, no second one
    assert row["wait_id"] == run["question_loop_wait_id"] and row["status"] == "waiting"
    assert world["loop"]()["stop_reason"] is None                    # parked, not held


# ── Part 2: the question wait, the responder and the resume ────────────────────

class Ledger:
    """The Graph execution ledger: the parked loop's ended execution and every request made after it."""

    def __init__(self):
        self.rows = {EXEC: {"exec_id": EXEC, "attempt_id": ATTEMPT, "run_id": GRAPH_RUN, "kind": "rework",
                            "status": "ended", "ai_run_id": RUN, "assignment_stage": "result_review",
                            "assignment_seq": 1,
                            "request_json": json.dumps({"type": "loop", "flow_id": FLOW, "doc_id": DOC,
                                                        "review_run_ids": ["aiv2_earlier"],
                                                        "assignment_ref": {"stage": "result_review", "seq": 1},
                                                        "invocation_origin": {"origin_kind": "card_assigned"}})}}
        self.requests = []

    def get(self, exec_id):
        return dict(self.rows[exec_id]) if exec_id in self.rows else None

    def request(self, attempt_id, run_id, kind, payload, assignment=None):
        exec_id = f"exe_resume_{len(self.requests) + 1}"
        self.rows[exec_id] = {"exec_id": exec_id, "attempt_id": attempt_id, "run_id": run_id, "kind": kind,
                              "status": "requested", "ai_run_id": None, "request_json": json.dumps(payload)}
        self.requests.append((attempt_id, run_id, kind, payload, assignment))
        return exec_id


@pytest.fixture
def parked(monkeypatch):
    """A Graph card whose review loop parked after its rework AI asked question 901 (the wait row is real)."""
    from flowgate.card_settings import legacy_adapter as legacy, stage_assignment as sa
    from flowgate.graph_run import execution_store as ledger_module, fencing
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _wait_table(conn)
    store = Store(conn)
    monkeypatch.setattr(db_waits, "get_store", lambda: store)
    ledger = Ledger()
    state = {"open": [{"id": 901, "anchor_doc_id": DOC, "asker_ai_run_id": RUN, "answer_count": 0,
                       "asker_actual_provider_id": "reworker", "responder_state": None}],
             "run_status": ("running", None), "attempt": "active", "dispatched": [], "failed": [], "settled": 0,
             "handed_on": [], "requirements": [], "loop_stage": "review", "assignment": {"seq": 1}}
    monkeypatch.setattr(ledger_module, "get", ledger.get)
    monkeypatch.setattr(ledger_module, "request", ledger.request)
    monkeypatch.setattr(fencing, "attempt_row", lambda attempt_id: {
        "attempt_id": attempt_id, "node_key": "card", "attempt_status": state["attempt"],
        "run_status": state["run_status"][0]})
    monkeypatch.setattr(fencing, "check", lambda *a, **k: None)
    monkeypatch.setattr(fencing, "guarded", lambda flow_id, body: body())
    monkeypatch.setattr(sa, "current_review_assignment", lambda attempt_id, stage: {
        "seq": state["assignment"]["seq"], "actor": sa.AI, "stage_state": sa.ACTIVE, "verdict": None})
    monkeypatch.setattr(db_loops, "get", lambda run_id: {
        **LOOP, "run_id": run_id, "current_stage": state["loop_stage"], "stop_reason": None, "round_no": 1})
    monkeypatch.setattr(db_loops, "stop_for_question_wait_handoff",
                        lambda run_id, successor_run_id: state["handed_on"].append((run_id, successor_run_id)) or True)
    # the card and its Graph run
    loop_run = {"run_id": RUN, "group_id": FLOW, "doc_ref": DOC, "graph_exec_id": EXEC, "provider_id": "reworker",
                "issued_to": "owner", "invocations": [{"inv_role": "execution", "actual_ai_id": "reworker",
                                                       "launch_no": 2, "inv_id": "inv_rework"}],
                "document_review_loop": {"current_stage": state["loop_stage"], "stop_reason": None}}
    monkeypatch.setattr(legacy, "ai_run_record", lambda run_id: dict(loop_run) if run_id == RUN else None)
    monkeypatch.setattr(legacy, "instruction_node_of_document", lambda fid, doc: None)
    monkeypatch.setattr(legacy, "node_of_result", lambda fid, doc: NODE)
    monkeypatch.setattr(legacy, "review_loop_active", lambda doc: True)          # its row stays unstopped
    monkeypatch.setattr(legacy, "open_questions_of_ai_run", lambda fid, run_id: [
        dict(i) for i in state["open"] if i["asker_ai_run_id"] == run_id and not i["answer_count"]])
    monkeypatch.setattr(legacy, "open_ai_questions", lambda fid: [dict(i) for i in state["open"]
                                                                  if not i["answer_count"]])
    monkeypatch.setattr(legacy, "flow_ai_busy", lambda fid: False)
    monkeypatch.setattr(legacy, "active_run_id", lambda fid: GRAPH_RUN)
    monkeypatch.setattr(legacy, "graph_run_state", lambda fid: state["run_status"])

    def wake(fid):
        status, idle_reason = state["run_status"]
        if status == "running":
            return True
        if status == "idle" and idle_reason != "stopped":
            state["run_status"] = ("running", None)
            return True
        return False

    monkeypatch.setattr(legacy, "wake_graph_run", wake)
    monkeypatch.setattr(legacy, "settle_graph_run", lambda fid: state.__setitem__("settled", state["settled"] + 1))
    monkeypatch.setattr(legacy, "answered_requirements", lambda run_id: list(state["requirements"]))
    monkeypatch.setattr(legacy, "attempt_of_ai_run", lambda run: ATTEMPT)
    monkeypatch.setattr(legacy, "active_attempt_id", lambda run_id, key: ATTEMPT)
    monkeypatch.setattr(legacy, "record_action_required", lambda *a: state.setdefault("held", []).append(a))
    monkeypatch.setattr(legacy, "mark_question_responder_failed", lambda item_id, issued_to, reason: (
        state["failed"].append((item_id, reason)), _fail(state, item_id, reason))[0] is None)
    from flowgate.ai_roles import qa_adapter
    state["candidates"] = [("reviewer", "card_review_role")]
    monkeypatch.setattr(qa_adapter, "candidates", lambda fid, node, asker, role=None: (
        list(state["candidates"]), None if state["candidates"] else "no_independent_responder", True, "qa_new"))
    monkeypatch.setattr(qa_adapter, "origin", lambda *a: {"origin_kind": "card_qa"})

    def ask(doc_id, item_id, issued_to, provider, source, **kw):
        state["dispatched"].append((item_id, provider, source))
        next(i for i in state["open"] if i["id"] == item_id).update(responder_state="dispatched",
                                                                   responder_run_id="air_responder")
        return {"run_id": "air_responder"}

    monkeypatch.setattr(legacy, "ask_responder", ask)
    monkeypatch.setattr(legacy, "askers_answered_by", lambda run_id: set(state.get("answered_by", {}).get(run_id, ())))
    monkeypatch.setattr(legacy, "askers_answered_as_author",
                        lambda run_id: set(state.get("answered_as_author", {}).get(run_id, ())))
    wait_row = db_waits.insert({
        "group_id": FLOW, "doc_ref": DOC, "loop_run_id": RUN, "graph_exec_id": EXEC, "graph_attempt_id": ATTEMPT,
        "graph_run_id": GRAPH_RUN, "node_key": "card", "assignment_stage": "result_review", "assignment_seq": 1,
        "asked_hop_kind": "rework", "asker_inv_id": "inv_rework", "asked_round_no": 1, "hop_outcome": "succeeded",
        "resume_stage": "review", "resume_round_no": 1, "resume_attempts_used": 0, "question_item_ids": [901]})
    yield {"state": state, "ledger": ledger, "wait": lambda: db_waits.get(wait_row["wait_id"]), "store": store,
           "run": loop_run}
    conn.close()


def _fail(state, item_id, reason):
    next(i for i in state["open"] if i["id"] == item_id).update(responder_state="failed", responder_error_code=reason,
                                                               responder_attempts=1)


def _answer(parked, how, item_id=901):
    """Question ``item_id`` answered: a person's answer, the responder AI's run end, or a chat AI's answer."""
    state = parked["state"]
    next(i for i in state["open"] if i["id"] == item_id)["answer_count"] = 1
    if how == "human":
        wait.on_question_answered({"author_kind": "human", "group_id": FLOW, "asker_ai_run_id": RUN})
    elif how == "ai":
        state.setdefault("answered_by", {})["air_responder"] = {RUN}
        wait.follow({"run_id": "air_responder", "group_id": FLOW})
    else:
        wait.on_question_answered({"author_kind": "ai", "group_id": FLOW, "asker_ai_run_id": RUN})   # skipped
        state.setdefault("answered_as_author", {})["aiv2_chat"] = {RUN}
        wait.follow({"run_id": "aiv2_chat", "group_id": FLOW, "action_scope": "chat"})


@pytest.fixture(autouse=True)
def graph_owned(monkeypatch):
    from flowgate.card_settings import legacy_adapter as legacy, repository
    monkeypatch.setattr(repository, "available", lambda: True)
    monkeypatch.setattr(legacy, "graph_owned", lambda fid: True)


def test_the_parked_loop_is_a_loop_wait_step_and_its_question_goes_to_the_card_review_ai(parked):
    assert wait._waiting(FLOW, parked["run"])[0] == "loop_wait"
    wait.follow({"run_id": RUN, "group_id": FLOW})                   # the parked run's own end
    assert parked["state"]["dispatched"] == [(901, "reviewer", "card_review_role")]
    assert parked["ledger"].requests == []                           # nothing goes on while it is open
    wait.follow({"run_id": RUN, "group_id": FLOW})                   # a later run end: the claimed one is not redone
    assert len(parked["state"]["dispatched"]) == 1


@pytest.mark.parametrize("how", ["human", "ai", "chat"])
def test_answers_resume_the_loop_from_its_stored_stage_exactly_once(parked, how):
    wait.follow({"run_id": RUN, "group_id": FLOW})
    _answer(parked, how)
    [(attempt, graph_run, kind, payload, assignment)] = parked["ledger"].requests
    assert (attempt, graph_run, kind, assignment) == (ATTEMPT, GRAPH_RUN, "review", ("result_review", 1))
    assert payload["loop"]["current_stage"] == "review"
    assert payload["review_run_ids"] == ["aiv2_earlier", RUN]        # its rounds go on counting
    assert payload["invocation_origin"]["purpose"] == "result_review_round"
    row = parked["wait"]()
    assert (row["status"], row["resume_exec_id"]) == ("resuming", "exe_resume_1")
    assert row["answered_at"] and row["resume_requested_at"]
    assert parked["state"]["settled"] >= 1                           # the Graph replays the request
    # the same answers told again — a person's and the responder's, the run read — nothing more
    _answer(parked, "human")
    _answer(parked, "ai")
    wait.reconcile(FLOW)
    assert len(parked["ledger"].requests) == 1


def test_a_restart_resumes_an_answered_wait_once_from_the_stored_row(parked):
    parked["state"]["open"][0]["answer_count"] = 1                   # answered while the server was down
    wait.reconcile(FLOW)                                             # the next run read
    wait.reconcile(FLOW)
    assert len(parked["ledger"].requests) == 1
    assert parked["wait"]()["status"] == "resuming"


def test_a_resumed_request_lost_before_any_run_took_it_is_requested_again_once(parked):
    parked["state"]["open"][0]["answer_count"] = 1
    wait.reconcile(FLOW)
    parked["ledger"].rows["exe_resume_1"]["status"] = "cancelled"     # e.g. a restart closed the unowned request
    assert qlw.owns(RUN) is True                                      # still this wait's loop
    wait.reconcile(FLOW)
    wait.reconcile(FLOW)
    assert [r[2] for r in parked["ledger"].requests] == ["review", "review"]
    assert parked["wait"]()["resume_exec_id"] == "exe_resume_2"


@pytest.mark.parametrize("status", [("paused", None), ("stopping", None), ("idle", "stopped")])
def test_a_paused_or_stopped_run_is_not_woken_and_the_resume_waits(parked, status):
    parked["state"]["run_status"] = status
    _answer(parked, "human")
    assert parked["ledger"].requests == [] and parked["wait"]()["status"] == "waiting"
    parked["state"]["run_status"] = ("running", None)                 # Resume: the run read takes it
    wait.reconcile(FLOW)
    assert len(parked["ledger"].requests) == 1


def test_an_idle_run_is_woken_for_the_resume(parked):
    parked["state"]["run_status"] = ("idle", "waiting_answer")
    _answer(parked, "human")
    assert parked["state"]["run_status"] == ("running", None) and len(parked["ledger"].requests) == 1


def test_new_attempt_answer_falls_back_to_loop_when_replacement_unavailable(parked, monkeypatch):
    # 0071 T6: a parked review has a result, so it cannot be replaced. Resume
    # its ordinary review path instead of waiting for a person's Stop command.
    from flowgate.card_settings import action_guard
    monkeypatch.setattr(action_guard, "_automatic_retry", lambda *a: False)
    parked["state"]["requirements"] = ["new_attempt"]
    _answer(parked, "human")
    assert len(parked["ledger"].requests) == 1
    assert parked["wait"]()["status"] == "resuming"


def test_several_questions_are_answered_one_by_one_before_the_loop_goes_on(parked):
    parked["state"]["open"].append({"id": 902, "anchor_doc_id": DOC, "asker_ai_run_id": RUN, "answer_count": 0,
                                    "asker_actual_provider_id": "reworker", "responder_state": None})
    wait.follow({"run_id": RUN, "group_id": FLOW})
    assert [d[0] for d in parked["state"]["dispatched"]] == [901]
    _answer(parked, "ai", 901)
    assert [d[0] for d in parked["state"]["dispatched"]] == [901, 902]
    assert parked["ledger"].requests == []
    _answer(parked, "ai", 902)
    assert len(parked["ledger"].requests) == 1


def test_no_responder_marks_the_question_failed_with_its_reason_and_the_loop_stays_held(parked, monkeypatch):
    from modules.flow_gate.graph_workflow.services import run_service
    parked["state"]["candidates"] = []
    wait.follow({"run_id": RUN, "group_id": FLOW})
    assert parked["state"]["failed"] == [(901, "no_independent_responder")]
    for _ in range(3):                                               # never re-dispatched, never resumed
        wait.follow({"run_id": RUN, "group_id": FLOW})
        wait.reconcile(FLOW)
    assert parked["state"]["failed"] == [(901, "no_independent_responder")] and parked["ledger"].requests == []
    assert parked["wait"]()["status"] == "waiting" and qlw.owns(RUN) is True
    # the card's Q badge carries the reason and the wait phase
    from modules.flow_gate.db import question_items
    from modules.flow_gate.graph_workflow.store import assignment_store
    from modules.flow_gate.services.ai_invoke import finalize
    item = dict(parked["state"]["open"][0], seq=1, responder_error_message="No independent enabled responder")
    monkeypatch.setattr(assignment_store, "root_binding", lambda fid: {})
    monkeypatch.setattr(question_items, "list_by_doc", lambda doc_id: [])
    monkeypatch.setattr(run_service.run_store, "ai_runs_of_node", lambda run_id, key: [RUN])
    monkeypatch.setattr(finalize, "open_questions_raised_by_run", lambda fid, run_id: [item])
    [detail] = run_service.question_wait_details(FLOW, {"run_id": GRAPH_RUN, "node_key": "card"})
    assert detail["state"] == "failed" and detail["code"] == "no_independent_responder"
    assert detail["message"] == "No independent enabled responder"
    assert (detail["phase"], detail["wait_status"], detail["resume_stage"]) == ("loop_wait", "waiting", "review")
    # a person answers it at last: the loop goes on
    _answer(parked, "human")
    assert len(parked["ledger"].requests) == 1


def test_an_answer_while_the_parked_run_still_finishes_waits_for_its_end_then_resumes_once(parked):
    # the park is stored, the run's finalize has not ended its execution yet: never a cancellation
    parked["ledger"].rows[EXEC]["status"] = "running"
    _answer(parked, "human")
    assert parked["ledger"].requests == []
    row = parked["wait"]()
    assert (row["status"], row["end_reason"]) == ("waiting", None) and qlw.owns(RUN) is True
    wait.reconcile(FLOW)                                             # a run read meanwhile changes nothing either
    assert parked["ledger"].requests == [] and parked["wait"]()["status"] == "waiting"
    parked["ledger"].rows[EXEC]["status"] = "ended"                  # the run's end
    from flowgate.card_settings import review_stage
    review_stage.on_run_ended(dict(parked["run"]))
    review_stage.on_run_ended(dict(parked["run"]))                   # told twice: still once
    assert len(parked["ledger"].requests) == 1 and parked["wait"]()["status"] == "resuming"


def test_a_gone_attempt_cancels_the_wait_and_no_longer_owns_the_loop(parked):
    parked["state"]["attempt"] = "invalidated"
    assert qlw.owns(RUN) is False                                    # the orphan sweep may close its row
    _answer(parked, "human")
    assert parked["ledger"].requests == []
    row = parked["wait"]()
    assert (row["status"], row["end_reason"]) == ("cancelled", "attempt_invalidated") and row["ended_at"]


def test_a_newer_review_assignment_cancels_the_wait(parked):
    parked["state"]["assignment"] = {"seq": 2}
    _answer(parked, "human")
    assert parked["ledger"].requests == [] and parked["wait"]()["status"] == "cancelled"


def test_the_successor_admission_hands_the_loop_on_and_the_parked_row_is_never_a_verdict(parked):
    from flowgate.card_settings import assigned_review, stage_assignment as sa
    _answer(parked, "human")
    assert qlw.on_successor_admitted({"run_id": "aiv2_successor", "graph_exec_id": "exe_resume_1"}) is True
    row = parked["wait"]()
    assert (row["status"], row["resumed_run_id"]) == ("resumed", "aiv2_successor") and row["resumed_at"]
    assert parked["state"]["handed_on"] == [(RUN, "aiv2_successor")]
    assert qlw.on_successor_admitted({"run_id": "aiv2_successor", "graph_exec_id": "exe_resume_1"}) is False
    assert qlw.owns(RUN) is False
    assert wait._waiting(FLOW, parked["run"]) is None                # neither loop_wait nor the loop-end review
    # the closed row (retry_exhausted + question_wait_handed_on) is never consumed as the loop's verdict
    claims = []
    loop = {"run_id": RUN, "current_stage": "stopped", "stop_reason": "retry_exhausted",
            "stop_detail": "question_wait_handed_on: aiv2_successor"}
    assert qlw.handed_on(loop)
    original = assigned_review._claim
    try:
        assigned_review._claim = lambda *a, **k: claims.append(a) or True
        assigned_review.consume_loop_verdict(FLOW, {"verdict": None, "run_id": GRAPH_RUN, "node_key": "card",
                                                    "attempt_id": ATTEMPT, "stage": sa.RESULT_REVIEW, "seq": 1},
                                             loop, "auto", [])
    finally:
        assigned_review._claim = original
    assert claims == []


# ── Part 3: the guards that must not misread a parked loop ──────────────────────

def test_the_parked_loop_has_owner_evidence_so_no_sweep_closes_it(parked, monkeypatch):
    from modules.flow_gate.db import ai_run_identity
    from modules.flow_gate.services.ai_invoke import admission
    monkeypatch.setattr(ai_run_identity, "foreign_run_id", lambda run_id: False)
    monkeypatch.setattr(admission._svc(), "is_run_live", lambda run_id: False)
    row = {"run_id": RUN, "group_id": FLOW, "updated_at": "2026-01-01T00:00:00+00:00"}
    assert admission.review_loop_owner_evidence(row, held=frozenset(), before=None) == "question_wait"


def test_the_parked_run_end_judges_no_loop_end_and_follows_its_questions(monkeypatch):
    from flowgate.card_settings import review_stage
    calls = []
    monkeypatch.setattr(qlw, "parked", lambda run: True)
    monkeypatch.setattr(review_stage, "_after_review", lambda *a, **k: calls.append("after_review"))
    monkeypatch.setattr(wait, "follow", lambda run: calls.append("follow"))
    monkeypatch.setattr(wait, "follow_parked_end", lambda run: calls.append("parked_end"))
    review_stage.on_run_ended({"run_id": RUN, "group_id": FLOW, "document_review_loop": {"current_stage": "review"}})
    assert calls == ["follow", "parked_end"]
    monkeypatch.setattr(qlw, "parked", lambda run: False)             # an ended loop is judged as before
    review_stage.on_run_ended({"run_id": RUN, "group_id": FLOW, "document_review_loop": {"current_stage": "stopped"}})
    assert calls == ["follow", "parked_end", "after_review", "follow", "parked_end"]


def test_the_wait_table_exists_in_every_dialect_with_the_fields_a2_reads():
    names = {d: (MIGRATIONS / d / "203_question_loop_waits.sql").read_text(encoding="utf-8")
             for d in ("sqlite", "mysql", "postgres")}
    for text in names.values():
        for column in ("loop_run_id", "graph_exec_id", "assignment_stage", "assignment_seq", "asker_inv_id",
                       "asked_hop_kind", "asked_round_no", "resume_stage", "question_item_ids", "waited_at",
                       "answered_at", "ended_at", "status"):
            assert column in text
    conn = sqlite3.connect(":memory:")
    _wait_table(conn)
    _wait_table(conn)                                                # IF NOT EXISTS: re-applying is harmless
    conn.close()


# ── Part 4: end to end on the real Graph ledger, fence, finish and lease release ───

class TxStore:
    """One SQLite connection in autocommit mode with real, re-entrant transactions (savepoints inside): a guarded body
    that raises rolls back every write it made, as the Flow guard's transaction does (``fencing.guarded``)."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        self.depth = 0

    def table_exists(self, name):
        return self._fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name]) is not None

    def _fetch_one(self, sql, values=()):
        with self.lock:
            row = self.conn.execute(sql, values).fetchone()
            return dict(row) if row else None

    def _fetch_all(self, sql, values=()):
        with self.lock:
            return [dict(row) for row in self.conn.execute(sql, values).fetchall()]

    def _execute(self, sql, values=()):
        with self.lock:
            return self.conn.execute(sql, values)

    def _execute_affected(self, sql, values=()):
        with self.lock:
            return self.conn.execute(sql, values).rowcount

    @contextmanager
    def transaction(self):
        with self.lock:
            name = f"sp{self.depth}"
            self.conn.execute("BEGIN IMMEDIATE" if self.depth == 0 else f"SAVEPOINT {name}")
            self.depth += 1
            try:
                yield self
            except BaseException:
                self.depth -= 1
                self.conn.execute("ROLLBACK" if self.depth == 0 else f"ROLLBACK TO {name}")
                if self.depth:
                    self.conn.execute(f"RELEASE {name}")
                raise
            self.depth -= 1
            self.conn.execute("COMMIT" if self.depth == 0 else f"RELEASE {name}")


@pytest.fixture
def graph_world(monkeypatch):
    """A Graph card (run ``grun_1``, node ``card`` in review, its attempt active) whose result review loop execution is
    requested in the real ledger. Real: the ledger, the fence and Flow-guard transaction, ``execution.replay`` /
    ``legacy_adapter.start_review_loop`` / ``execution.request_loop`` / ``scheduling.admit`` / ``execution.loop_state``,
    the loop rows and their checkpoint, the worker, ``execution.launch`` / ``finish``, ``terminal.cleanup`` (the group
    lease release), ``review_stage.on_run_ended``, ``question_wait`` and ``question_loop_wait``. Stand-ins: the provider
    (it revises, reviews or asks), the question items, the token mint, the stage assignment read, the card's responder
    start (``legacy.ask_responder``) and the loop-end judgement (``review_stage._after_review``, recorded)."""
    from flowgate.ai_roles import qa_adapter
    from flowgate.card_settings import legacy_adapter as legacy, review_stage, stage_assignment as sa
    from flowgate.graph_run import execution, execution_store as ledger, fencing, scheduling, settle as graph_settle
    from flowgate.graph_time_machine import operation as time_machine
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import ai_run_identity, connection, document_reviews as db_reviews, group_ai_leases
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services import ai_invoke_service as service
    from modules.flow_gate.services import graph_execution_hooks, snapshot_materialization_service
    from modules.flow_gate.services import tr_self_check_progress
    from modules.flow_gate.services.ai_invoke import finalize, remote_supervisor, review, terminal
    from modules.flow_gate.services.ai_invoke import worker as worker_module

    store = TxStore()
    store.conn.executescript("""
        CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT UNIQUE NOT NULL, project_id TEXT,
            group_id TEXT, revision_no INTEGER NOT NULL, doc_review_status TEXT NOT NULL, meta TEXT,
            rejection_reason TEXT, rejection_history TEXT, updated_at TEXT);
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
        CREATE TABLE document_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL, reviewer_id TEXT NOT NULL, verdict TEXT NOT NULL, findings TEXT NOT NULL,
            comment TEXT, reviewed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE graph_runs(run_id TEXT PRIMARY KEY, group_id TEXT, status TEXT,
                                last_event_seq INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE graph_run_token_map(token_id TEXT PRIMARY KEY, run_id TEXT, node_key TEXT,
                                         stage TEXT, mapped_at TEXT);
        CREATE TABLE graph_node_runs(run_id TEXT, node_key TEXT, status TEXT, result_doc_id TEXT,
            opened_at TEXT, finished_at TEXT, dispatch_token_issued INTEGER, dispatch_issued_to TEXT,
            dispatch_mention_id TEXT, dispatch_error_code TEXT, stage TEXT, instruction_doc_id TEXT,
            result_event_floor INTEGER, updated_at TEXT);
    """)
    for name in ("091_ai_invoke_document_review_loops.sql", "092_ai_invoke_document_review_loop_live_run.sql",
                 "151_graph_run_state_attempts.sql", "152_graph_attempt_runs.sql",
                 "154_graph_run_token_map_kind.sql"):
        store.conn.executescript((MIGRATIONS / "sqlite" / name).read_text(encoding="utf-8"))
    store.conn.execute("CREATE TABLE tr_commit_ledger(id INTEGER PRIMARY KEY)")
    store.conn.executescript((MIGRATIONS / "sqlite" / "153a_graph_attempt_results.sql").read_text(encoding="utf-8"))
    columns = {r["name"] for r in store._fetch_all("PRAGMA table_info(graph_attempt_runs)")}
    for column, kind in (("assignment_stage", "TEXT"), ("assignment_seq", "INTEGER")):   # migration 164's columns
        if column not in columns:
            store.conn.execute(f"ALTER TABLE graph_attempt_runs ADD COLUMN {column} {kind}")
    _wait_table(store.conn)
    store._execute("INSERT INTO groups VALUES (?)", [FLOW])
    store._execute("INSERT INTO documents(doc_id,project_id,group_id,revision_no,doc_review_status,updated_at) "
                   "VALUES (?, 'flowgate', ?, 3, 'rejected', '2026-11-19T00:00:00+00:00')", [DOC, FLOW])
    store._execute("INSERT INTO ai_providers VALUES ('reviewer'), ('reworker')")
    store._execute("INSERT INTO graph_runs(run_id,group_id,status,run_mode,step_node_keys) "
                   "VALUES (?, ?, 'running', 'step', '[\"card\"]')", [GRAPH_RUN, FLOW])
    store._execute("INSERT INTO graph_node_runs(run_id,node_key,status,result_doc_id) VALUES (?, 'card', 'review', ?)",
                   [GRAPH_RUN, DOC])

    monkeypatch.setattr(connection, "STORE", store)
    for module in (db_loops, service, service.db_docs, db_reviews, db_waits, run_store, execution):
        monkeypatch.setattr(module, "get_store", lambda: store)
    monkeypatch.setattr(run_store, "run_by_id", lambda rid: run_store._normalize(
        store._fetch_one("SELECT * FROM graph_runs WHERE run_id=?", [rid])))
    monkeypatch.setattr(run_store, "node_runs", lambda rid:
                        store._fetch_all("SELECT * FROM graph_node_runs WHERE run_id=?", [rid]))

    def guard(_flow, body):
        with store.transaction():
            return body()

    for module in (execution, fencing, scheduling):
        monkeypatch.setattr(module, "with_flow_guard", guard)
    monkeypatch.setattr(ledger, "_assignment_columns", lambda: True)
    monkeypatch.setattr(execution.workspace, "verify_execution", lambda _eid: None)
    monkeypatch.setattr(execution.workspace, "cleanup", lambda _aid: None)
    monkeypatch.setattr(execution.workspace, "cleanup_finished", lambda: None)
    monkeypatch.setattr(execution.scheduling, "acquire", lambda eid, rid: ledger.acquire(eid, rid))
    monkeypatch.setattr(scheduling, "limit", lambda flow_id: 4)
    monkeypatch.setattr(execution.result_merge, "recover", lambda: None)
    monkeypatch.setattr(execution.result_merge, "in_progress", lambda _rid: False)
    monkeypatch.setattr(execution.stop, "recover", lambda: None)
    monkeypatch.setattr(time_machine, "busy", lambda _flow: False)
    monkeypatch.setattr(graph_settle, "_gate_reserved", lambda _ctx: {})
    monkeypatch.setattr(remote_supervisor, "available", lambda *a, **k: False)
    monkeypatch.setattr(graph_execution_hooks, "_handler", None)
    execution.register()
    attempt = run_store.open_attempt(GRAPH_RUN, "card", 1)

    state = {"questions": [], "hops": [], "act": {}, "runs": {}, "order": [], "tokens": 0, "dispatched": [],
             "after_review": [], "settled": 0, "answered_by": {}, "before_cleanup": None, "replays": []}

    # ── the provider (one hop per call), the tokens and the question items ──
    def execute(run, chain, prompt):
        assert execution.launch(run)                 # the run's Graph execution lease (real acquire / renew)
        stage = db_loops.get(run["run_id"])["current_stage"]
        state["hops"].append((run["run_id"], stage))
        act = state["act"].get(len(state["hops"]), {})
        stamp = f"2026-11-19T00:00:{len(state['hops']):02d}+00:00"
        if stage == "rework" and act.get("revise", True):
            store._execute("UPDATE documents SET revision_no = revision_no + 1 WHERE doc_id = ?", [DOC])
        if stage == "review" and act.get("verdict"):
            revision = store._fetch_one("SELECT revision_no FROM documents WHERE doc_id = ?", [DOC])["revision_no"]
            store._execute("INSERT INTO document_reviews(doc_id,revision_no,reviewer_id,verdict,findings,comment,"
                           "reviewed_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           [DOC, revision, "reviewer", act["verdict"],
                            json.dumps(["F1"] if act["verdict"] == "issues" else []), None, stamp, stamp, stamp])
        if act.get("ask"):
            state["questions"].append({"id": act["ask"], "anchor_doc_id": DOC, "asker_kind": "ai",
                                       "asker_ai_run_id": run["run_id"], "asker_actual_provider_id": run["provider_id"],
                                       "answer_count": 0, "responder_state": None})
        return True

    def open_of(run_id):
        return [dict(q) for q in state["questions"] if q["asker_ai_run_id"] == run_id and not q["answer_count"]]

    def prepare(run):
        state["tokens"] += 1
        return {"mention": "token"}

    monkeypatch.setattr(finalize, "open_questions_raised_by_run", lambda group_id, run_id: open_of(run_id))
    monkeypatch.setattr(service, "_execute_provider_chain", execute)
    monkeypatch.setattr(service, "_auto_reject", lambda slot, review_row, ctx: {"ok": True})
    monkeypatch.setattr(service, "_classify_end_reason", lambda item, ok: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_judge_hop", lambda item: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_prepare_retry_token", prepare)
    monkeypatch.setattr(service, "_reset_attempt_state", lambda item: None)
    monkeypatch.setattr(service, "_broadcast", lambda run, event_type, payload: None)
    monkeypatch.setattr(service, "_persist_run_record", lambda run: True, raising=False)
    monkeypatch.setattr(service, "finished_payload", lambda run: {}, raising=False)
    monkeypatch.setattr(service.ai_settings_service, "resolve_effective", lambda project: {
        "providers": [{"id": "reviewer", "name": "Reviewer"}, {"id": "reworker", "name": "Reworker"}]})
    monkeypatch.setattr(worker_module, "_start_post_process_recovery", lambda item: (None, None))
    monkeypatch.setattr(worker_module, "_stop_post_process_recovery", lambda *a, **k: None)
    monkeypatch.setattr(ai_run_identity, "assert_owner", lambda run, **kw: None)
    monkeypatch.setattr(tr_self_check_progress, "end_owned", lambda run, **kw: None)
    monkeypatch.setattr(snapshot_materialization_service, "cleanup_for_run", lambda run_id, actor=None: {})

    # ── the run's finalize: the real terminal cleanup (execution end, lease release), then the run-end hook ──
    def finalize_run(run):
        if state["before_cleanup"]:
            state["before_cleanup"](run)
        terminal.cleanup(run)
        review_stage.on_run_ended(run)

    monkeypatch.setattr(service, "_finalize_run", finalize_run)

    # ── the AI invoke admission the replayed loop start reaches (``ai_invoke_service.start_run``) ──
    def start_run(**kw):
        exec_id = kw["graph_exec_id"]
        run_id = f"aiv2_loop_{len(state['order']) + 1}"
        replayed = graph_execution_hooks.call("loop_state", exec_id)          # the stored loop of the request
        scheduling.admit(exec_id, run_id, FLOW)                              # the Graph's admission claim (CAS)
        lease = group_ai_leases.acquire(group_id=FLOW, project_id="flowgate", run_id=run_id, chain_id=None,
                                        action_scope="review", worker_identity=None)
        assert lease and lease["run_id"] == run_id, "the Flow's group lease is still held"
        loop = dict(replayed["loop"])
        stage = loop["current_stage"]
        provider = "reworker" if stage == "rework" else "reviewer"
        run = {"run_id": run_id, "project_id": "flowgate", "issued_to": "owner",
               "api_base_url": "http://127.0.0.1/flowgate/api/v1", "group_id": FLOW, "doc_ref": DOC,
               "mode": "single", "document_review_loop": loop, "started_at": loop["started_at"],
               "docs_target": 0, "chain_id": "loop", "chain_docs_target": 0, "chain_docs_reached": 0,
               "attempts_used": 0, "fallback_history": [], "action_scope": "review", "hop_kind": stage,
               "cancel_event": threading.Event(), "graph_exec_id": exec_id, "provider_id": provider,
               "graph_review_run_ids": list(replayed["review_run_ids"]),
               "invocations": [{"inv_role": "execution" if stage == "rework" else "review", "actual_ai_id": provider,
                                "launch_no": 1, "inv_id": "inv_" + run_id}]}
        review._insert_document_review_loop(run)                             # the successor takes the loop over
        run["document_review_loop"] = db_loops.get(run_id)
        state["runs"][run_id] = run
        state["order"].append(run_id)
        service._worker(run, [{"id": provider, "name": provider.title()}], "go")
        return run

    monkeypatch.setattr(service, "start_run", start_run)
    monkeypatch.setattr(legacy, "submit_for_card_review", lambda doc_id, issued_to: None)
    monkeypatch.setattr(legacy, "last_rejection", lambda doc_id: None)
    monkeypatch.setattr(ai_node_starter, "api_base_url", lambda user_id=None: "http://127.0.0.1/flowgate/api/v1")

    # ── the Graph's settlement: requested executions no launcher claimed are replayed (``queue_requests``) ──
    def settle_graph_run(flow_id):
        state["settled"] += 1
        graph = run_store.run_by_id(GRAPH_RUN)
        if graph["status"] not in ("running", "paused"):
            return
        for row in scheduling.requests(GRAPH_RUN):
            if not row.get("ai_run_id"):
                state["replays"].append(row["exec_id"])
                execution.replay(row)

    def graph_status(flow_id):
        return (run_store.run_by_id(GRAPH_RUN)["status"], None)

    monkeypatch.setattr(legacy, "settle_graph_run", settle_graph_run)
    monkeypatch.setattr(legacy, "graph_run_state", graph_status)
    monkeypatch.setattr(legacy, "wake_graph_run", lambda flow_id: graph_status(flow_id)[0] == "running")
    monkeypatch.setattr(legacy, "ai_run_record", lambda run_id: dict(state["runs"][run_id])
                        if run_id in state["runs"] else None)
    monkeypatch.setattr(legacy, "instruction_node_of_document", lambda fid, doc: None)
    monkeypatch.setattr(legacy, "node_of_result", lambda fid, doc: NODE)
    monkeypatch.setattr(legacy, "review_loop_active", lambda doc: True)
    monkeypatch.setattr(legacy, "open_questions_of_ai_run", lambda fid, run_id: open_of(run_id))
    monkeypatch.setattr(legacy, "open_ai_questions", lambda fid: [dict(q) for q in state["questions"]
                                                                  if not q["answer_count"]])
    monkeypatch.setattr(legacy, "flow_ai_busy", lambda fid: group_ai_leases.get(fid) is not None)
    monkeypatch.setattr(legacy, "active_run_id", lambda fid: GRAPH_RUN)
    monkeypatch.setattr(legacy, "answered_requirements", lambda run_id: [])
    monkeypatch.setattr(legacy, "attempt_of_ai_run", lambda run: attempt)
    monkeypatch.setattr(legacy, "active_attempt_id", lambda run_id, key: attempt)
    monkeypatch.setattr(legacy, "askers_answered_by", lambda run_id: set(state["answered_by"].get(run_id, ())))
    monkeypatch.setattr(legacy, "askers_answered_as_author", lambda run_id: set())
    monkeypatch.setattr(sa, "current_review_assignment", lambda attempt_id, stage: {
        "seq": 1, "actor": sa.AI, "stage_state": sa.ACTIVE, "verdict": None})
    monkeypatch.setattr(qa_adapter, "candidates", lambda fid, node, asker, role=None: (
        [("reviewer", "card_review_role")], None, True, "qa_new"))
    monkeypatch.setattr(qa_adapter, "origin", lambda *a: {"origin_kind": "card_qa"})

    def ask(doc_id, item_id, issued_to, provider, source, **kw):
        state["dispatched"].append((item_id, provider, source))
        next(q for q in state["questions"] if q["id"] == item_id).update(responder_state="dispatched",
                                                                       responder_run_id="air_responder")
        return {"run_id": "air_responder"}

    monkeypatch.setattr(legacy, "ask_responder", ask)
    monkeypatch.setattr(review_stage, "_after_review", lambda flow_id, run, answered=False: state["after_review"]
                        .append(run["run_id"]))

    started = datetime.now(timezone.utc)
    loop = {**LOOP, "run_id": "aiv2_admission", "group_id": FLOW, "doc_ref": DOC, "current_stage": "rework",
            "starts_with_rework": True, "started_at": started.isoformat(),
            "deadline_at": (started + timedelta(hours=1)).isoformat()}
    first = ledger.request(attempt, GRAPH_RUN, "rework", {
        "type": "loop", "project_id": "flowgate", "flow_id": FLOW, "doc_id": DOC, "issued_to": "owner",
        "loop": loop, "basis": "the card's review basis", "node_key": "card",
        "assignment_ref": {"stage": "result_review", "seq": 1},
        "invocation_origin": {"origin_kind": "card_assigned"}}, assignment=("result_review", 1))

    def answer(item_id, by="ai"):
        """The card's review AI (or a person) answers question ``item_id``."""
        next(q for q in state["questions"] if q["id"] == item_id)["answer_count"] = 1
        if by == "ai":
            state["answered_by"]["air_responder"] = {q["asker_ai_run_id"] for q in state["questions"]
                                                     if q["id"] == item_id}
            wait.follow({"run_id": "air_responder", "group_id": FLOW})
        else:
            asker = next(q for q in state["questions"] if q["id"] == item_id)["asker_ai_run_id"]
            wait.on_question_answered({"author_kind": "human", "group_id": FLOW, "asker_ai_run_id": asker})

    yield {"state": state, "store": store, "attempt": attempt, "first": first, "answer": answer,
           "start": lambda: execution.replay(ledger.get(first)),
           "executions": lambda: store._fetch_all("SELECT * FROM graph_attempt_runs ORDER BY created_at, exec_id"),
           "loop": db_loops.get, "lease": lambda: group_ai_leases.get(FLOW),
           "waits": lambda: [db_waits._decode(r) for r in store._fetch_all("SELECT * FROM question_loop_waits")]}
    store.conn.close()


def test_e2e_park_releases_the_lease_and_the_review_ai_answer_runs_the_stored_stage_once(graph_world):
    from flowgate.graph_run import execution, execution_store as ledger
    g, state = graph_world, graph_world["state"]
    state["act"] = {1: {"ask": 911}, 2: {"verdict": "pass"}}         # the rework AI revises and asks
    g["start"]()
    [parked_run] = state["order"]
    # parked: no review hop, the run ended (its execution ended, the group lease released), the card still locked
    assert state["hops"] == [(parked_run, "rework")] and state["tokens"] == 0
    [ended] = g["executions"]()
    assert (ended["exec_id"], ended["status"], ended["ai_run_id"]) == (g["first"], "ended", parked_run)
    assert g["lease"]() is None
    loop = g["loop"](parked_run)
    assert (loop["current_stage"], loop["stop_reason"]) == ("review", None)
    [row] = g["waits"]()
    assert (row["status"], row["loop_run_id"], row["graph_exec_id"], row["graph_attempt_id"], row["graph_run_id"]) \
        == ("waiting", parked_run, g["first"], g["attempt"], GRAPH_RUN)
    assert (row["assignment_stage"], row["assignment_seq"], row["resume_stage"]) == ("result_review", 1, "review")
    assert qlw.owns(parked_run) is True and state["after_review"] == []          # nothing judged at the park
    assert state["dispatched"] == [(911, "reviewer", "card_review_role")]        # put to the card's review AI

    g["answer"](911)                                                  # the review AI's answer (its run's end)
    first, resumed = g["executions"]()
    successor = state["order"][1]
    assert (resumed["kind"], resumed["status"], resumed["ai_run_id"]) == ("review", "ended", successor)
    assert (resumed["attempt_id"], resumed["assignment_stage"], resumed["assignment_seq"]) == \
        (g["attempt"], "result_review", 1)
    request = json.loads(resumed["request_json"])
    assert request["review_run_ids"] == [parked_run] and request["question_wait_id"] == row["wait_id"]
    assert request["invocation_origin"]["purpose"] == "result_review_round"
    # the successor worker ran the stored stage, from the stored round, and the loop ended on its verdict
    assert state["hops"] == [(parked_run, "rework"), (successor, "review")]
    assert state["runs"][successor]["graph_review_run_ids"] == [parked_run]
    done = g["loop"](successor)
    assert (done["stop_reason"], done["round_no"]) == ("review_passed", 1)
    handed = g["loop"](parked_run)
    assert (handed["stop_reason"], handed["stop_detail"]) == ("retry_exhausted",
                                                              f"question_wait_handed_on: {successor}")
    [row] = g["waits"]()
    assert (row["status"], row["resume_exec_id"], row["resumed_run_id"]) == ("resumed", resumed["exec_id"], successor)
    assert row["answered_at"] and row["resumed_at"] and row["ended_at"]
    assert state["after_review"] == [successor] and g["lease"]() is None
    # told again in every way — the person's event, the responder's end, run reads, a duplicate replay — nothing more
    g["answer"](911, by="human")
    wait.follow({"run_id": "air_responder", "group_id": FLOW})
    wait.reconcile(FLOW)
    qlw.resume_answered(FLOW)
    execution.replay(ledger.get(resumed["exec_id"]))
    assert len(g["executions"]()) == 2 and state["order"] == [parked_run, successor]
    assert len(state["hops"]) == 2 and state["after_review"] == [successor]


def test_e2e_an_answer_before_the_parked_run_ended_is_resumed_once_after_its_real_end(graph_world):
    g, state = graph_world, graph_world["state"]
    state["act"] = {1: {"ask": 912}, 2: {"verdict": "pass"}}
    seen = {}

    def answer_while_finishing(run):
        if run["run_id"] != "aiv2_loop_1" or seen:
            return
        g["answer"](912, by="human")                                 # the park is stored; its finalize not run yet
        [execution_row] = g["executions"]()
        [row] = g["waits"]()
        seen.update(execution=execution_row["status"], wait=row["status"], end_reason=row["end_reason"],
                    owns=qlw.owns(run["run_id"]))

    state["before_cleanup"] = answer_while_finishing
    g["start"]()
    assert seen == {"execution": "running", "wait": "waiting", "end_reason": None, "owns": True}   # never cancelled
    parked_run, successor = state["order"]
    assert state["hops"] == [(parked_run, "rework"), (successor, "review")]
    first, resumed = g["executions"]()
    assert (first["status"], resumed["status"], resumed["ai_run_id"]) == ("ended", "ended", successor)
    [row] = g["waits"]()
    assert (row["status"], row["resumed_run_id"]) == ("resumed", successor)
    assert g["loop"](successor)["stop_reason"] == "review_passed" and g["lease"]() is None


def test_e2e_a_restart_resumes_the_answered_wait_once_and_a_lost_cas_rolls_its_request_back(graph_world,
                                                                                           monkeypatch):
    g, state = graph_world, graph_world["state"]
    state["act"] = {1: {"ask": 913}, 2: {"verdict": "pass"}}
    g["start"]()
    [parked_run] = state["order"]
    [row] = g["waits"]()
    # another process claimed the wait first: the request made in the same Flow-guard transaction is rolled back
    with monkeypatch.context() as scoped:
        scoped.setattr(db_waits, "claim_resume", lambda wait_id, exec_id, **kw: False)
        state["questions"][0]["answer_count"] = 1
        assert qlw.resume(FLOW, row) is None
    assert len(g["executions"]()) == 1 and g["waits"]()[0]["status"] == "waiting"
    # answered while the server was down (no event): the run reads after the restart resume it once
    wait.reconcile(FLOW)
    wait.reconcile(FLOW)
    assert state["order"] == [parked_run, "aiv2_loop_2"] and len(g["executions"]()) == 2
    assert qlw.resume(FLOW, g["waits"]()[0]) is None                  # the stored row is not resumable again
    assert g["waits"]()[0]["status"] == "resumed" and len(state["hops"]) == 2


# ── Part 5: question-caused verdicts and the question wait are left out of the loop's budget ─────────────
#
# R 0051.0001 A-3: "답이 달리면 그 답을 근거로 이어서 수정한다. 답을 기다린 시간과 질문 때문에 생긴 반려는 검수 횟수에
# 넣지 않는다." The same real worker hop loop and checkpoint as Part 1, with the review rows' run provenance
# (``review_run_id`` / ``attempt_no``, so the Graph predecessor ownership of ``_document_loop_review_view`` is the real
# one), the wait table (migration 200) and the exclusion table (migration 201). A loop is carried across its wait by a
# successor run exactly as the Graph replay hands it on: the parked loop row's state, the predecessor run ids, the
# real ``review._insert_document_review_loop`` -> ``question_loop_wait.on_successor_admitted`` hand-off.

from modules.flow_gate.db import review_count_exclusions as db_exclusions  # noqa: E402

RUN2, RUN3 = "aiv2_loop_run_2", "aiv2_loop_run_3"


def _exclusion_table(conn):
    conn.executescript((MIGRATIONS / "sqlite" / "205_review_count_exclusions.sql").read_text(encoding="utf-8"))


def _iso(moment):
    return moment.isoformat()


@pytest.fixture
def budget(monkeypatch):
    from flowgate.graph_run import execution_store as ledger, fencing
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.db import question_items as db_question_items
    from modules.flow_gate.services import ai_invoke_service as service
    from modules.flow_gate.services import graph_execution_hooks
    from modules.flow_gate.services.ai_invoke import finalize, review
    from modules.flow_gate.services.ai_invoke import worker as worker_module

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT UNIQUE NOT NULL, project_id TEXT,
            group_id TEXT, revision_no INTEGER NOT NULL, doc_review_status TEXT NOT NULL, meta TEXT,
            rejection_reason TEXT, rejection_history TEXT, updated_at TEXT);
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
        CREATE TABLE document_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL, reviewer_id TEXT NOT NULL, verdict TEXT NOT NULL, findings TEXT NOT NULL,
            comment TEXT, reviewed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            review_run_id TEXT, attempt_no INTEGER);
    """)
    for name in ("091_ai_invoke_document_review_loops.sql", "092_ai_invoke_document_review_loop_live_run.sql"):
        conn.executescript((MIGRATIONS / "sqlite" / name).read_text(encoding="utf-8"))
    _wait_table(conn)
    _exclusion_table(conn)
    conn.execute("INSERT INTO groups VALUES (?)", (FLOW,))
    conn.execute("INSERT INTO documents(doc_id,project_id,group_id,revision_no,doc_review_status,updated_at) "
                 "VALUES (?, 'flowgate', ?, 3, 'pending_review', '2026-11-19T00:00:00+00:00')", (DOC, FLOW))
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",), ("reworker",)])
    conn.commit()
    store = Store(conn)
    for module in (db_loops, service, service.db_docs, db_reviews, db_waits, db_exclusions):
        monkeypatch.setattr(module, "get_store", lambda: store)

    state = {"asked": {}, "items": {}, "hops": [], "timeouts": [], "rejects": 0, "act": {}}
    monkeypatch.setattr(finalize, "open_questions_raised_by_run",
                        lambda group_id, run_id: [dict(i) for i in state["asked"].get(run_id, [])])
    monkeypatch.setattr(db_question_items, "get_by_pk", lambda pk: state["items"].get(int(pk)))
    monkeypatch.setattr(ledger, "get", lambda exec_id: {
        "exec_id": exec_id, "attempt_id": ATTEMPT, "run_id": GRAPH_RUN, "kind": "review", "status": "running",
        "assignment_stage": "result_review", "assignment_seq": 1, "request_json": "{}"})
    monkeypatch.setattr(fencing, "attempt_row", lambda attempt_id: {
        "attempt_id": attempt_id, "node_key": "card", "attempt_status": "active", "run_status": "running"})
    monkeypatch.setattr(graph_execution_hooks, "call", lambda name, *a, **k: None)

    def execute(run, chain, prompt):
        stage = db_loops.get(run["run_id"])["current_stage"]
        state["hops"].append((run["run_id"], stage))
        state["timeouts"].append(run.get("timeout_sec"))
        stamp = _iso(datetime.now(timezone.utc))
        act = state["act"].get(len(state["hops"]), {})
        if stage == "rework" and act.get("revise", True):
            conn.execute("UPDATE documents SET revision_no = revision_no + 1, doc_review_status = 'revised' "
                         "WHERE doc_id = ?", (DOC,))
        if stage == "review" and act.get("verdict"):
            revision = conn.execute("SELECT revision_no FROM documents WHERE doc_id = ?", (DOC,)).fetchone()[0]
            findings = act.get("findings", ["a real defect"] if act["verdict"] == "issues" else [])
            conn.execute("INSERT INTO document_reviews(doc_id,revision_no,reviewer_id,verdict,findings,comment,"
                         "reviewed_at,created_at,updated_at,review_run_id,attempt_no) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                         (DOC, revision, "reviewer", act["verdict"], json.dumps(findings), None, stamp, stamp, stamp,
                          run["run_id"]))
        conn.commit()
        if act.get("ask"):
            item_id, seq = act["ask"]
            item = {"id": item_id, "seq": seq, "asker_kind": "ai", "asker_ai_run_id": run["run_id"],
                    "anchor_doc_id": DOC, "asker_actual_provider_id": run.get("provider_id")}
            state["items"][item_id] = item
            state["asked"].setdefault(run["run_id"], []).append(item)
        return True

    def auto_reject(slot, review_row, ctx):
        state["rejects"] += 1
        conn.execute("UPDATE documents SET doc_review_status = 'rejected' WHERE doc_id = ?", (DOC,))
        conn.commit()
        return {"ok": True}

    monkeypatch.setattr(service, "_execute_provider_chain", execute)
    monkeypatch.setattr(service, "_auto_reject", auto_reject)
    monkeypatch.setattr(service, "_classify_end_reason", lambda item, ok: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_judge_hop", lambda item: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_prepare_retry_token", lambda run: {"mention": "token"})
    monkeypatch.setattr(service, "_reset_attempt_state", lambda item: None)
    monkeypatch.setattr(worker_module, "_start_post_process_recovery", lambda item: (None, None))
    monkeypatch.setattr(worker_module, "_stop_post_process_recovery", lambda *a, **k: None)
    monkeypatch.setattr(service, "_broadcast", lambda run, event_type, payload: None)
    monkeypatch.setattr(service, "_finalize_run", lambda item: item.update(status="finished"))
    monkeypatch.setattr(service.ai_settings_service, "resolve_effective", lambda project: {
        "providers": [{"id": "reviewer", "name": "Reviewer"}, {"id": "reworker", "name": "Reworker"}]})

    def _run(run_id, loop_row, exec_id, predecessors):
        stage = loop_row["current_stage"]
        provider = "reworker" if stage == "rework" else "reviewer"
        return {"run_id": run_id, "project_id": "flowgate", "issued_to": "owner",
                "api_base_url": "http://127.0.0.1/flowgate/api/v1", "group_id": FLOW, "doc_ref": DOC,
                "mode": "single", "document_review_loop": loop_row, "started_at": loop_row["started_at"],
                "docs_target": 0, "chain_id": "loop", "chain_docs_target": 0, "chain_docs_reached": 0,
                "attempts_used": 0, "fallback_history": [], "action_scope": "edit" if stage == "rework" else "review",
                "hop_kind": stage, "cancel_event": threading.Event(), "graph_exec_id": exec_id,
                "graph_review_run_ids": list(predecessors), "provider_id": provider}

    def _work(run):
        provider = run["provider_id"]
        service._worker(run, [{"id": provider, "name": provider.title()}], "go")
        return run

    def start(stage="review", started=None, **loop_fields):
        started = started or datetime.now(timezone.utc)
        loop = {**LOOP, **loop_fields, "run_id": RUN, "group_id": FLOW, "doc_ref": DOC, "current_stage": stage,
                "starts_with_rework": stage == "rework", "started_at": _iso(started),
                "deadline_at": _iso(started + timedelta(seconds=int(loop_fields.get("total_timeout_sec", 3600)))),
                "stop_reason": None, "stop_detail": None}
        if stage == "rework":
            conn.execute("UPDATE documents SET doc_review_status = 'rejected' WHERE doc_id = ?", (DOC,))
            conn.commit()
        return _work(_run(RUN, db_loops.insert(loop), EXEC, []))

    def resume(parked_run_id, run_id, predecessors, *, waited=None, exec_id=None):
        """Answer the parked run's questions and carry the loop on as the Graph replay does (A1's resume ->
        ``execution.loop_state`` -> admission -> ``review._insert_document_review_loop``)."""
        wait_row = db_waits.get_by_loop_run(parked_run_id)
        assert wait_row["status"] == "waiting"
        if waited is not None:                         # the wait started that long ago
            conn.execute("UPDATE question_loop_waits SET waited_at = ? WHERE wait_id = ?",
                         (_iso(datetime.now(timezone.utc) - waited), wait_row["wait_id"]))
            conn.commit()
        state["asked"].pop(parked_run_id, None)        # every question answered
        exec_id = exec_id or "exe_" + run_id
        assert db_waits.claim_resume(wait_row["wait_id"], exec_id)
        parked_loop = db_loops.get(parked_run_id)
        carried = {k: v for k, v in parked_loop.items() if k not in ("created_at", "updated_at")}
        carried["run_id"] = run_id
        run = _run(run_id, carried, exec_id, predecessors)
        review._insert_document_review_loop(run)       # admission: loop row + hand-off (wait resumed)
        assert db_waits.get(wait_row["wait_id"])["status"] == "resumed"
        assert db_loops.get(parked_run_id)["stop_detail"].startswith("question_wait_handed_on")
        run["document_review_loop"] = db_loops.get(run_id)
        return _work(run)

    yield {"state": state, "start": start, "resume": resume, "conn": conn, "loop": db_loops.get,
           "exclusions": lambda: db_exclusions.list_by_doc(DOC),
           "stages": lambda run_id: [s for r, s in state["hops"] if r == run_id]}
    conn.close()


def test_a_question_caused_rejection_is_not_counted_and_the_limit_reads_the_real_rejections(budget):
    # review_count 2: the reviewer rejects only because of its question (its one finding cites it and declares
    # "cause": "question"), then two real
    # rejections. The loop stops after the SECOND real rejection's rework, never after the first one.
    budget["state"]["act"] = {
        1: {"verdict": "issues", "ask": (701, 7),
            "findings": [{"locus": "Q7 (item 701)", "note": "cannot judge before the answer", "cause": "question"}]},
        3: {"verdict": "issues", "findings": [{"locus": "section 2", "note": "real defect A"}]},
        5: {"verdict": "issues", "findings": [{"locus": "section 3", "note": "real defect B"}]},
    }
    budget["start"]("review")
    assert budget["stages"](RUN) == ["review"]                                  # parked before its rework
    [mark] = budget["exclusions"]()
    assert (mark["cause"], mark["basis"], mark["review_run_id"], mark["question_item_ids"]) == \
        ("question_wait", "asked_with_verdict", RUN, [701])
    assert mark["wait_id"] == db_waits.get_by_loop_run(RUN)["wait_id"]
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["stages"](RUN2) == ["rework", "review", "rework", "review", "rework"]
    loop = budget["loop"](RUN2)
    assert (loop["current_stage"], loop["stop_reason"]) == ("stopped", "review_count_exhausted")
    assert loop["stop_detail"] == "review count 2 exhausted (1 question-caused review(s) not counted)"
    # the verdict is kept: three review rows, three rejections (the question-caused one included)
    assert budget["conn"].execute("SELECT COUNT(*) FROM document_reviews").fetchone()[0] == 3
    assert budget["state"]["rejects"] == 3


def test_a_rejection_with_a_real_defect_and_a_question_is_counted(budget):
    budget["state"]["act"] = {
        1: {"verdict": "issues", "ask": (702, 8),
            "findings": [{"locus": "section 1", "note": "real defect"}, {"locus": "Q8", "note": "please answer"}]},
        3: {"verdict": "issues", "findings": [{"locus": "section 2", "note": "real defect A"}]},
    }
    budget["start"]("review")
    assert budget["exclusions"]() == []                                         # no marker: it counts
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["stages"](RUN2) == ["rework", "review", "rework"]
    loop = budget["loop"](RUN2)
    assert (loop["stop_reason"], loop["stop_detail"]) == ("review_count_exhausted", "review count 2 exhausted")


def test_a_reviewer_that_asks_and_rejects_with_no_finding_is_counted(budget):
    # no finding declares anything: there is no explicit basis, so the rejection counts as before
    budget["state"]["act"] = {1: {"verdict": "issues", "findings": [], "ask": (703, 9)}}
    budget["start"]("review", review_count=1)
    assert budget["exclusions"]() == []


# r1 rejection: citing the question is a reference, not the cause. A real defect that names the wait's question
# in the same finding -- or every defect finding naming it -- is counted.
_DEFECT_NAMING_Q7 = {"locus": "server/api.py:42 (Q7, item 701)",
                     "note": "질문 답변과 무관한 실제 결함: 권한 검사가 없어 다른 사용자의 데이터를 반환한다. "
                             "Q7의 배포 여부도 확인 필요하다."}


@pytest.mark.parametrize("findings", [
    [_DEFECT_NAMING_Q7],                                                        # one finding: defect + question
    [{"locus": "Q7 (item 701)", "note": "the endpoint skips the permission check"},
     {"locus": "section 2, see item 701", "note": "the migration drops a column still read"}],  # every one cites
], ids=["defect_and_question_in_one_finding", "every_defect_finding_cites_the_question"])
def test_a_real_defect_that_names_the_reviewers_question_is_counted(budget, findings):
    budget["state"]["act"] = {
        1: {"verdict": "issues", "findings": findings, "ask": (701, 7)},
        3: {"verdict": "issues", "findings": [{"locus": "section 3", "note": "real defect A"}]},
    }
    budget["start"]("review")
    assert budget["exclusions"]() == []                                         # no marker: it counts
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["stages"](RUN2) == ["rework", "review", "rework"]
    loop = budget["loop"](RUN2)
    assert (loop["stop_reason"], loop["stop_detail"]) == ("review_count_exhausted", "review count 2 exhausted")


@pytest.mark.parametrize("findings", [
    [{"locus": "server/api.py:42 (Q11, item 801)",
      "note": "a real defect unrelated to the answer: no permission check. Q11 is also worth a look."}],
    [{"locus": "Q11", "note": "the endpoint skips the permission check"},
     {"locus": "item 801", "note": "the migration drops a column still read"}],
], ids=["defect_and_question_in_one_finding", "every_defect_finding_cites_the_question"])
def test_a_review_after_the_answer_whose_defects_name_the_question_is_counted(budget, findings):
    budget["state"]["act"] = {1: {"ask": (801, 11)}, 2: {"verdict": "issues", "findings": findings}}
    budget["start"]("rework", review_count=1)
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["exclusions"]() == []
    assert budget["stages"](RUN2) == ["review", "rework"]
    assert budget["loop"](RUN2)["stop_detail"] == "review count 1 exhausted"


def test_a_review_after_the_answer_that_rejects_on_the_answered_question_only_is_not_counted(budget):
    # the rework AI submitted while asking; the review after the answer rejects because the document contradicts the
    # answer (every finding cites the question and declares "cause": "question"): that rejection is question-caused.
    # review_count 1.
    budget["state"]["act"] = {
        1: {"ask": (801, 11)},                                                  # rework revises and asks
        2: {"verdict": "issues",
            "findings": [{"locus": "Q11 (item 801)", "note": "the answer says B, not A", "cause": "question"}]},
        4: {"verdict": "issues", "findings": [{"locus": "section 4", "note": "real defect"}]},
    }
    budget["start"]("rework", review_count=1)
    assert budget["stages"](RUN) == ["rework"] and budget["exclusions"]() == []
    budget["resume"](RUN, RUN2, [RUN])
    [mark] = budget["exclusions"]()
    assert (mark["basis"], mark["review_run_id"], mark["question_item_ids"]) == ("answer_contradicted", RUN2, [801])
    assert budget["stages"](RUN2) == ["review", "rework", "review", "rework"]
    loop = budget["loop"](RUN2)
    assert loop["stop_detail"] == "review count 1 exhausted (1 question-caused review(s) not counted)"


def test_a_review_after_the_answer_with_a_defect_of_its_own_is_counted(budget):
    budget["state"]["act"] = {
        1: {"ask": (802, 12)},
        2: {"verdict": "issues", "findings": [{"locus": "item 802", "note": "contradicts the answer", "cause": "question"},
                                              {"locus": "section 5", "note": "real defect"}]},
    }
    budget["start"]("rework", review_count=1)
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["exclusions"]() == []
    assert budget["stages"](RUN2) == ["review", "rework"]
    assert budget["loop"](RUN2)["stop_detail"] == "review count 1 exhausted"


def test_only_the_first_review_after_the_answer_can_be_question_caused(budget):
    budget["state"]["act"] = {
        1: {"ask": (803, 13)},
        2: {"verdict": "issues", "findings": [{"locus": "section 6", "note": "real defect"}]},
        4: {"verdict": "issues",
            "findings": [{"locus": "item 803", "note": "still contradicts the answer", "cause": "question"}]},
    }
    budget["start"]("rework", review_count=2)
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["exclusions"]() == []                                         # the later one is a real round
    assert budget["stages"](RUN2) == ["review", "rework", "review", "rework"]
    assert budget["loop"](RUN2)["stop_detail"] == "review count 2 exhausted"


def test_a_hop_that_only_asked_spends_no_retry_and_the_resumed_loop_goes_on(budget):
    # failure_restart_max_attempts 0: a rework that only asked would have ended the loop as retry_exhausted
    budget["state"]["act"] = {1: {"ask": (804, 14), "revise": False}, 3: {"verdict": "pass"}}
    budget["start"]("rework")
    loop = budget["loop"](RUN)
    assert (loop["current_stage"], loop["attempts_used"], loop["stop_reason"]) == ("rework", 0, None)
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["stages"](RUN2) == ["rework", "review"]
    assert budget["loop"](RUN2)["stop_reason"] == "review_passed"
    assert budget["exclusions"]() == []


def _push_back(budget, run_id, *, deadline_ago):
    """The parked loop's total deadline passed ``deadline_ago`` ago (it was parked long enough)."""
    budget["conn"].execute("UPDATE ai_invoke_document_review_loops SET deadline_at = ? WHERE run_id = ?",
                           (_iso(datetime.now(timezone.utc) - deadline_ago), run_id))
    budget["conn"].commit()


def test_the_wait_is_moved_out_of_the_total_deadline_and_the_stage_timeout(budget):
    # 30 minutes of the hour were left when the rework parked; it waited two hours for its answer. Measured on the
    # stored deadline the loop would be 90 minutes over it (total_timeout at the next checkpoint, a 1-second stage).
    budget["state"]["act"] = {1: {"ask": (805, 15)}, 2: {"verdict": "issues"}, 4: {"verdict": "pass"}}
    budget["start"]("rework", review_count=3)
    _push_back(budget, RUN, deadline_ago=timedelta(minutes=90))
    budget["resume"](RUN, RUN2, [RUN], waited=timedelta(hours=2))
    assert budget["stages"](RUN2) == ["review", "rework", "review"]
    assert budget["loop"](RUN2)["stop_reason"] == "review_passed"
    rework_budget = budget["state"]["timeouts"][2]                              # the stage switch inside RUN2
    assert 1500 < rework_budget <= 1800                                         # the 30 minutes that were left


def test_time_outside_the_wait_still_reaches_the_deadline(budget):
    # the same overdue loop with a 10-minute wait: the time it did not wait is loop time as before
    budget["state"]["act"] = {1: {"ask": (806, 16)}, 2: {"verdict": "issues"}}
    budget["start"]("rework", review_count=3)
    _push_back(budget, RUN, deadline_ago=timedelta(minutes=90))
    budget["resume"](RUN, RUN2, [RUN], waited=timedelta(minutes=10))
    assert budget["stages"](RUN2) == ["review"]
    loop = budget["loop"](RUN2)
    assert (loop["stop_reason"], loop["stop_detail"]) == ("total_timeout", "document review loop deadline reached")


def test_waits_of_every_predecessor_are_summed_and_a_question_caused_round_of_each_is_left_out(budget):
    # two waits in one loop: RUN parks (reviewer asked, question-only rejection), RUN2 parks again (its rework asked
    # and submitted), RUN3 carries on. Both waits are moved out of the deadline; both question-caused verdicts out of
    # the count.
    budget["state"]["act"] = {
        1: {"verdict": "issues", "findings": [{"locus": "item 901", "note": "need the answer", "cause": "question"}],
            "ask": (901, 21)},
        2: {"ask": (902, 22)},                                                  # RUN2's rework asks and submits
        3: {"verdict": "issues", "findings": [{"locus": "Q22", "note": "contradicts the answer", "cause": "question"}]},
        5: {"verdict": "pass"},
    }
    budget["start"]("review", review_count=1)
    budget["resume"](RUN, RUN2, [RUN], waited=timedelta(minutes=40))
    assert budget["stages"](RUN2) == ["rework"]
    _push_back(budget, RUN2, deadline_ago=timedelta(minutes=30))
    budget["resume"](RUN2, RUN3, [RUN, RUN2], waited=timedelta(minutes=20))
    assert budget["stages"](RUN3) == ["review", "rework", "review"]            # never review_count_exhausted
    assert budget["loop"](RUN3)["stop_reason"] == "review_passed"
    assert sorted(m["basis"] for m in budget["exclusions"]()) == ["answer_contradicted", "asked_with_verdict"]


# unit level: the counted view, the classification and the wait arithmetic on their own

def _reviews(*rows):
    return [{"id": i, "revision_no": rev, "verdict": v, "findings": "[]", "review_run_id": RUN, "attempt_no": 1}
            for i, rev, v in rows]


def test_the_count_leaves_out_only_marked_rounds_and_nothing_else_moves():
    from modules.flow_gate.services.ai_invoke import review
    reviews = _reviews((3, 6, "issues"), (2, 5, "issues"), (1, 4, "issues"))
    bundle = {"review_count": 2, "review_run_id": RUN, "reviews": reviews, "doc": {"revision_no": 7},
              "last_hop_kind": "rework", "last_hop_outcome": "succeeded", "round_no": 3, "baseline_revision_no": 3}
    current, _ = review._document_loop_review_view(bundle, reviews)
    assert review._document_loop_counted_rounds(current, set()) == 3
    assert review._document_loop_counted_rounds(current, {2}) == 2
    assert review._document_loop_counted_rounds(current, {99}) == 3            # not one of the loop's rounds
    unmarked = review.resolve_document_review_loop_gate(bundle)                 # legacy / no marker: as before
    assert (unmarked["stop_reason"], unmarked["stop_detail"]) == ("review_count_exhausted", "review count 2 exhausted")
    marked = review.resolve_document_review_loop_gate({**bundle, "count_excluded_review_ids": {1, 2}})
    assert (marked["current_stage"], marked["round_no"]) == ("review", 3)      # one counted round: review again
    assert review.check_expected_progress({**bundle, "last_hop_kind": "review", "round_no": 3}, {}, reviews)


_ASKED = {"cause": "question"}


@pytest.mark.parametrize("findings, expected", [
    ([{"locus": "Q7", "note": "x", **_ASKED}], [701]),
    ([{"locus": "s1", "note": "see item 701", **_ASKED}], [701]),
    ([{"locus": "Q 7", "note": "x", "cause": " Question "}], [701]),            # the declaration, loosely spelled
    ([{"locus": "Q7", **_ASKED}, {"locus": "item 701", "note": "y", **_ASKED}], [701]),
    # citing is not enough: no declaration, no exclusion (TR r1 rejection)
    ([{"locus": "Q7", "note": "x"}], None),
    ([_DEFECT_NAMING_Q7], None),                                                # a defect naming the question
    ([{"locus": "Q7", "note": "defect A"}, {"locus": "item 701", "note": "defect B"}], None),  # every one cites
    (["Q 7: unanswered"], None),                                                # a bare string declares nothing
    ([{"locus": "Q7", **_ASKED}, {"locus": "s2", "note": "a real defect"}], None),
    ([{"locus": "Q7", **_ASKED}, {"locus": "Q7", "note": "a real defect naming Q7"}], None),
    ([{"locus": "Q7", "cause": "defect"}], None),                               # another cause
    # the declaration has to be tied to the wait's own question
    ([{"locus": "s1", "note": "no question named", **_ASKED}], None),
    ([{"locus": "Q8", "note": "another question", **_ASKED}], None),
    ([{"locus": "item 7010", **_ASKED}], None),                                 # a different number
    ([], None),                                                                 # nothing explicit to rest on
], ids=["q_seq", "item_id", "loose_spelling", "two_declared", "cited_only", "defect_naming_question",
        "every_defect_cites", "bare_string", "declared_plus_defect", "declared_plus_defect_naming_question",
        "other_cause", "declared_without_citation", "other_question", "other_number", "no_findings"])
def test_a_verdict_rests_on_its_questions_only_when_every_finding_declares_and_cites_one(findings, expected):
    items = [{"id": 701, "seq": 7}]
    review_row = {"verdict": "issues", "findings": json.dumps(findings, ensure_ascii=False)}
    assert qlw.question_only_verdict(review_row, items) == expected


def test_only_an_issues_verdict_with_readable_findings_can_be_question_caused():
    items = [{"id": 701, "seq": 7}]
    declared = json.dumps([{"locus": "Q7", **_ASKED}])
    assert qlw.question_only_verdict({"verdict": "issues", "findings": declared}, items) == [701]
    assert qlw.question_only_verdict({"verdict": "pass", "findings": declared}, items) is None
    assert qlw.question_only_verdict({"verdict": "hold", "findings": declared}, items) is None
    assert qlw.question_only_verdict({"verdict": "issues", "findings": "{bad"}, items) is None
    assert qlw.question_only_verdict({"verdict": "issues", "findings": declared}, []) is None


def test_the_review_prompts_tell_the_reviewer_when_a_finding_is_question_only():
    from modules.flow_gate.services.ai_invoke import review
    clause = review._answered_questions_clause(
        [{"id": 701, "seq": 7, "anchor_doc_id": DOC, "body": "A or B?", "answer_body": "B"}], "review")
    assert '"cause": "question"' in clause and 'without "cause"' in clause
    assert '"cause"' not in review._answered_questions_clause(
        [{"id": 701, "seq": 7, "anchor_doc_id": DOC, "body": "A or B?", "answer_body": "B"}], "rework")


def test_the_wait_seconds_span_waited_to_resumed_and_skip_cancelled_waits(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _wait_table(conn)
    store = Store(conn)
    monkeypatch.setattr(db_waits, "get_store", lambda: store)
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    base = {"group_id": FLOW, "doc_ref": DOC, "asked_hop_kind": "rework", "asked_round_no": 1, "resume_stage": "review",
            "resume_round_no": 1, "question_item_ids": [1]}
    db_waits.insert({**base, "loop_run_id": "r1", "waited_at": _iso(now - timedelta(hours=3))})
    conn.execute("UPDATE question_loop_waits SET status='resumed', resumed_at=? WHERE loop_run_id='r1'",
                 (_iso(now - timedelta(hours=2)),))                             # 1 hour
    db_waits.insert({**base, "loop_run_id": "r2", "waited_at": _iso(now - timedelta(minutes=30))})
    conn.execute("UPDATE question_loop_waits SET status='resuming' WHERE loop_run_id='r2'")   # 30 min, up to now
    db_waits.insert({**base, "loop_run_id": "r3", "waited_at": _iso(now - timedelta(hours=5))})
    conn.execute("UPDATE question_loop_waits SET status='cancelled' WHERE loop_run_id='r3'")  # never excluded
    conn.commit()
    assert qlw.excluded_wait_seconds(["r1"], now) == 3600
    assert qlw.excluded_wait_seconds(["r1", "r2", "r3", "unknown"], now) == 5400
    assert qlw.excluded_wait_seconds([], now) == 0
    monkeypatch.setattr(db_waits, "list_by_loop_runs", lambda ids: (_ for _ in ()).throw(RuntimeError("down")))
    assert qlw.excluded_wait_seconds(["r1"], now) == 0                          # unreadable: counted as before


def test_the_stage_timeout_moves_by_the_wait_and_keeps_its_floor():
    from modules.flow_gate.services.ai_invoke import review
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    loop = {"rework_timeout_sec": 1800, "deadline_at": _iso(now - timedelta(minutes=10))}
    assert review.loop_stage_timeout_sec(loop, "rework", now) == 1                       # as before
    assert review.loop_stage_timeout_sec(loop, "rework", now, question_wait_sec=1200) == 600
    assert review.loop_stage_timeout_sec(loop, "rework", now, question_wait_sec=7200) == 1800
    assert review.loop_stage_timeout_sec({"rework_timeout_sec": 1800}, "rework", now, question_wait_sec=99) == 1800


def test_the_exclusion_table_exists_in_every_dialect_and_is_insert_only(monkeypatch):
    names = {d: (MIGRATIONS / d / "205_review_count_exclusions.sql").read_text(encoding="utf-8")
             for d in ("sqlite", "mysql", "postgres")}
    for text in names.values():
        for column in ("review_id", "doc_ref", "cause", "wait_id", "basis", "question_item_ids"):
            assert column in text
        assert "review_count_exclusions" in text
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _exclusion_table(conn)
    _exclusion_table(conn)                                                      # re-applied: idempotent
    monkeypatch.setattr(db_exclusions, "get_store", lambda: Store(conn))
    first = db_exclusions.insert(review_id=5, doc_ref=DOC, wait_id="qlw_a", basis="asked_with_verdict",
                                 question_item_ids=[3, 3, 1])
    again = db_exclusions.insert(review_id=5, doc_ref=DOC, wait_id="qlw_b", basis="answer_contradicted",
                                 question_item_ids=[9])
    assert again == first and first["question_item_ids"] == [1, 3] and first["cause"] == "question_wait"
    assert db_exclusions.review_ids_by_doc(DOC) == {5}
    with pytest.raises(ValueError):
        db_exclusions.insert(review_id=6, doc_ref=DOC, wait_id="qlw_c", basis="guessed", question_item_ids=[])


def test_a_loop_without_the_exclusion_store_counts_every_round(budget, monkeypatch):
    # a server whose migration 201 is not applied yet (or a failed read): the marker is not written, every verdict
    # counts and the loop stops exactly where it stopped before A2
    monkeypatch.setattr(db_exclusions, "available", lambda: False)
    budget["state"]["act"] = {
        1: {"verdict": "issues", "findings": [{"locus": "Q7", "cause": "question"}], "ask": (707, 7)},
        3: {"verdict": "issues", "findings": [{"locus": "section 2", "note": "real defect A"}]},
    }
    budget["start"]("review")
    budget["resume"](RUN, RUN2, [RUN])
    assert budget["stages"](RUN2) == ["rework", "review", "rework"]
    assert budget["loop"](RUN2)["stop_detail"] == "review count 2 exhausted"
