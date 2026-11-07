"""flowgate.v02.0143 T#1 (W1, R11; NR flowgate.v02.0143.0003 §4.2) — the run read tells whether an automatic Q&A responder
answers a question of the run's card now (``qa_responding`` / ``qa_responding_node_keys``).

The existing read model has the card's ``question`` (a question nobody answered yet) only: it tells a person's wait
(responder ``failed`` / ``user_decision`` / none) and an automatic answer going on (``dispatched``) alike, so the header
cannot use it. ``qa_responding`` is true only when all hold: an open node of this run, an AI run of its live attempt
(``run_store.ai_runs_of_node``) asked the question (``finalize.open_questions_raised_by_run``), the item's
``responder_state`` is ``dispatched`` and its ``responder_run_id`` is an AI run still live (``ai_invoke.runtime.is_run_live``).
Anything else, and a failed read, is false (never locks).

1. ``run_service.qa_responding_nodes`` over stand-in reads (the table of T §3 R11);
2. the real run read and Canvas read on the real Graph engine / question wait of ``test_fg_question_pending_0111`` (an AI
   card whose instruction AI asked and ended): true while its responder is live, false once it failed, handed the
   question to a person or the asker's question was answered.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import FA, _h  # noqa: F401
from test_fg_question_pending_0111 import (  # noqa: F401 — fixtures used by name
    INSTRUCTION_RUN, RESPONDER_RUN, _asked_instruction, _current, card_outputs_port, db_path, decided, env,
    legacy_cards, questions,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")

ASKER, OTHER, RESPONDER = "air_asker", "air_other", "air_responder"


@pytest.fixture(autouse=True)
def document_write_port_0138(monkeypatch):
    """This older harness seeds documents before any app lifespan registers the document write gate (flowgate.v02.0127):
    the current real gate is registered explicitly, as test_fg_provider_failure_retry_0121 does, and the review harness's
    fixture writes of earlier documents bypass it (not an agent's card edit)."""
    import test_fg_card_settings_review as review_harness
    from flowgate.card_document_edit import write_gate
    from modules.flow_gate.db import documents
    from modules.flow_gate.graph_workflow.services import card_document_ports

    monkeypatch.setitem(card_document_ports._handlers, card_document_ports.DOCUMENT_WRITE_GATE, write_gate.dispatch)
    real_update = documents.update

    def seed_update(doc_id, updates):
        if set(updates) <= {"file_path", "doc_review_status"} and updates.get("file_path") == f"docs/{doc_id}.md":
            return documents._update_document(doc_id, updates)
        return real_update(doc_id, updates)

    def seeded(function):
        def call(*args, **kwargs):
            with monkeypatch.context() as m:
                m.setattr(documents, "update", seed_update)
                return function(*args, **kwargs)
        return call

    monkeypatch.setattr(review_harness, "_write_written", seeded(review_harness._write_written))


# ── 1. qa_responding_nodes over stand-in reads ──────────────────────────────────────────────────────────────

@pytest.fixture()
def reads(monkeypatch):
    """The reads ``qa_responding_nodes`` makes, as stand-ins: the run's node runs, each node's live-attempt AI runs, the
    unanswered questions each AI run asked and the live AI runs."""
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services.ai_invoke import finalize
    from modules.flow_gate.services.ai_invoke import runtime as ai_runtime

    state = {
        "rows": [{"run_id": "run_1", "node_key": "gn_1", "status": "open"}],
        "ai_runs": {"gn_1": [ASKER]},
        "asked": {ASKER: [{"id": 1, "asker_kind": "ai", "asker_ai_run_id": ASKER, "responder_state": "dispatched",
                           "responder_run_id": RESPONDER, "answer_count": 0}]},
        "live": {RESPONDER},
        "calls": [],
    }
    monkeypatch.setattr(run_store, "node_runs", lambda run_id: [dict(r) for r in state["rows"]])
    monkeypatch.setattr(run_store, "ai_runs_of_node", lambda run_id, key: list(state["ai_runs"].get(key, [])))

    def asked(flow_id, ai_run_id):
        state["calls"].append((flow_id, ai_run_id))
        if isinstance(state["asked"], Exception):
            raise state["asked"]
        return [dict(i) for i in state["asked"].get(ai_run_id, [])]

    monkeypatch.setattr(finalize, "open_questions_raised_by_run", asked)
    monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: run_id in state["live"])
    return state


def _nodes():
    from modules.flow_gate.graph_workflow.services import run_service
    return run_service.qa_responding_nodes("run_1", FA)


def test_a_dispatched_responder_still_live_for_the_live_attempt_s_question_is_responding(reads):
    assert _nodes() == frozenset({"gn_1"})
    assert reads["calls"] == [(FA, ASKER)]


@pytest.mark.parametrize("state", ["failed", "user_decision", "answered", None])
def test_a_responder_that_failed_handed_over_or_answered_or_never_started_is_not(reads, state):
    reads["asked"][ASKER][0]["responder_state"] = state
    assert _nodes() == frozenset()


def test_a_dispatched_item_whose_responder_run_is_no_longer_live_is_not(reads):
    reads["live"] = set()                                   # finished (or lost to a restart: the startup sweep's case)
    assert _nodes() == frozenset()
    reads["live"] = {RESPONDER}
    reads["asked"][ASKER][0]["responder_run_id"] = None
    assert _nodes() == frozenset()


def test_a_question_of_another_run_or_of_a_replaced_attempt_is_not(reads):
    reads["asked"] = {OTHER: reads["asked"][ASKER]}          # asked by an AI run that is not this node's
    assert _nodes() == frozenset()
    reads["asked"] = {ASKER: [dict(i, responder_state="dispatched") for i in reads["asked"][OTHER]]}
    reads["ai_runs"] = {"gn_1": []}                          # an invalidated attempt's runs are not the node's
    assert _nodes() == frozenset()


def test_a_node_that_is_not_open_is_not(reads):
    for status in ("done", "review", "pending", "rejected"):
        reads["rows"] = [{"run_id": "run_1", "node_key": "gn_1", "status": status}]
        assert _nodes() == frozenset()


def test_a_failed_read_is_not_responding(reads):
    reads["asked"] = RuntimeError("db down")
    assert _nodes() == frozenset()


def test_only_the_nodes_whose_own_question_is_answered_now(reads):
    reads["rows"] = [{"run_id": "run_1", "node_key": key, "status": "open"} for key in ("gn_1", "gn_2", "gn_3")]
    reads["ai_runs"] = {"gn_1": [ASKER], "gn_2": [OTHER], "gn_3": []}
    reads["asked"][OTHER] = [{"id": 2, "asker_kind": "ai", "asker_ai_run_id": OTHER, "responder_state": "failed",
                              "responder_run_id": RESPONDER, "answer_count": 0}]
    assert _nodes() == frozenset({"gn_1"})


# ── 2. the real run read / Canvas read ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def live(monkeypatch):
    """The AI runs this process tracks as live (``ai_invoke.runtime.is_run_live``)."""
    from modules.flow_gate.services.ai_invoke import runtime as ai_runtime

    runs: set[str] = set()
    monkeypatch.setattr(ai_runtime, "is_run_live", lambda run_id: run_id in runs)
    return runs


def _canvas_run(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/canvas", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def test_the_run_read_and_the_canvas_read_tell_a_live_responder_and_stop_telling_it(env, decided, monkeypatch,
                                                                                   questions, live):
    key, _doc, _exec = _asked_instruction(env, monkeypatch, questions)
    [item] = questions["asked"][INSTRUCTION_RUN]

    run = _current(env)                                      # asked, its responder not started yet
    assert run["status"] == "idle"
    assert (run["qa_responding"], run["qa_responding_node_keys"]) == (False, [])

    item.update(responder_state="dispatched", responder_run_id=RESPONDER_RUN)    # dispatch_answer_run's claim
    live.add(RESPONDER_RUN)
    run = _current(env)
    assert run["status"] == "idle" and run["idle_reason"] != "error"
    assert (run["qa_responding"], run["qa_responding_node_keys"]) == (True, [key])
    canvas = _canvas_run(env)
    assert (canvas["qa_responding"], canvas["qa_responding_node_keys"]) == (True, [key])

    live.discard(RESPONDER_RUN)                              # the responder's run ended (ai_invoke_finished)
    assert _current(env)["qa_responding"] is False
    live.add(RESPONDER_RUN)
    for state in ("failed", "user_decision"):                 # a person answers now
        item.update(responder_state=state)
        run = _current(env)
        assert (run["qa_responding"], run["status"]) == (False, "idle")
    item.update(responder_state="dispatched")
    assert _current(env)["qa_responding"] is True
    questions["asked"][INSTRUCTION_RUN] = []                  # answered: no unanswered question of the asker any more
    assert _current(env)["qa_responding"] is False
