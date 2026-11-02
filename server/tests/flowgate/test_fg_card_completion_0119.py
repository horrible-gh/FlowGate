"""flowgate.v02.0119 T#2 (T-1) — the v0.2 card completion: the completion record is a Graph node's only source of done.

On the same real SQLite DB and fixtures as ``test_fg_card_settings`` / ``test_fg_run_buttons_0053`` (every migration, the
card documents and card settings registered as ``startup`` does, plus ``flowgate.card_completion``). The cards are person
cards whose result is bound when it is registered with the node's token (``pre_feature_attempts``), so each test drives
exactly one node's completion. Fault injection happens here only (fixtures), never on an operating run:

- a v0.1 approval of the result (the review screen's hidden person's approval) completes nothing;
- a completion is applied once: done, attempt ended, the next card opened once, the record ``applied``, the node event
  carrying its ``completion_id``;
- a repeated pass / a retried job (the same key twice) writes one record and opens the next card once;
- a record committed and its application lost (① then a failure) is applied by the next reconciliation, once;
- the same ``review_approved`` applied twice is ignored the second time (frontier E-20 / E-36);
- an old verdict (another attempt, a result revised in place) is ``superseded`` and moves nothing;
- a Time Machine rewind after the completion marks the record ``revoked``; the rework's new attempt has its own record;
- a completion and a [전체 실행] / [다음 단계] command racing open the next card once;
- the automatic completion (``card_settings.legacy_adapter.approve``) takes no v0.1 path (no review transition, no TR
  approval hook, no Graph contact) and attributes the act: ``no_review_submitted`` by the system, ``ai_review_passed``
  by the reviewing AI provider, the issuer as ``requested_by`` only;
- an existing run (started before the service) reads its approved result as complete;
- the read views name the AI of an existing review / rejection / response row without rewriting it.

Rework of review rej_01M6D564RSF8JQE7 (all with the completion registered as the server's start registers it):

- an approved result is judged like any other before its completion (reviewed revision, reviewed submission);
- an older verdict superseded, the newer submission of the same attempt completes (the key's next generation); a
  superseded / revoked record is never reported as a completion;
- an existing run's approved result never revives a revoked / superseded / stale record, nor completes a rework's new
  attempt;
- a TS / TR2 completion runs the document's own effect with no v0.1 approval: the TS admission and the record commit
  together (a failure in between rolls both back), the TR2 apply runs under the system and records in its finalize; a
  TR2 applied whose record was lost is recovered without applying again;
- a run read and a command settle stale records with no explicit apply (never ``applied`` for another attempt);
- with no registered handler a document approval moves no node, and a failed registration fails the start;
- actors: the person who wrote the submission, the run's issuer (``started_by``) as ``requested_by``, and only the
  provider a review actually ran on.
"""
from __future__ import annotations

import json
import threading

import pytest

from test_fg_card_settings import (  # noqa: F401 — pytest fixtures and helpers of the card settings tests
    legacy_cards, passive_person_results, pre_feature_attempts,
    FA, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft,
)

# flowgate.v02.0119 T#4 rework (rej_01M6DQW6YF0SCQ5D P1): the person cards here are passive vehicles (``passive_person_results``).
pytestmark = pytest.mark.usefixtures("legacy_cards", "pre_feature_attempts", "completions", "passive_person_results")

LEE = {"kind": "human", "user_id": "u_lee", "provider_id": None}


@pytest.fixture()
def completions(env):
    """``flowgate.card_completion`` registered as ``startup`` does (its ports and its install mark)."""
    from flowgate.card_completion import integration

    integration.register()
    yield
    integration.unregister()


def _confirmed(env, n=2):
    nodes = [node("tmp_r", "R")] + [node(f"tmp_{i}", "T", assignee=LEE) for i in range(n)]
    refs = ["tmp_r"] + [f"tmp_{i}" for i in range(n)]
    body = put_draft(env, FA, 0, nodes, [edge(f"tmp_e{i}", a, b) for i, (a, b) in enumerate(zip(refs, refs[1:]))])
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    return [body["key_map"][f"tmp_{i}"] for i in range(n)]


def _command(env, mode="full"):
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=_h())
    assert res.status_code in (200, 201), res.text
    return res.json()["run"]


def _current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _states(env):
    return {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}


def _token(env, key):
    return _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid DESC",
                (key,))[0]["token_id"]


def _submit(env, key, seq):
    """The card's result registered with its token (the inbox contact): the node goes to review."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    result = _doc(FA, seq, "T", "pending_review")
    v01_contact.after_inbox_registered(FA, result, "T", _token(env, key))
    assert _states(env)[key] == "review"
    return result


def _attempts(env, key):
    return _sql(env["db"], "SELECT attempt_id, status FROM graph_node_attempts WHERE node_key = ? ORDER BY created_at",
                (key,))


def _openings(env, key):
    return [r for r in _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE node_key = ? "
                                       "AND to_status = 'open' ORDER BY event_seq", (key,))]


def _records(env, key=None):
    sql = "SELECT * FROM graph_card_completions" + (" WHERE node_key = ?" if key else "") + " ORDER BY created_at"
    return _sql(env["db"], sql, (key,) if key else ())


def _done_events(env, key):
    rows = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE node_key = ? AND to_status = 'done' "
                           "ORDER BY event_seq", (key,))
    return [json.loads(r["payload_json"]) for r in rows]


def _rev(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return int((db_documents.get_by_id(doc_id) or {}).get("revision_no") or 0)


def _command_any(env, mode):
    """A run button pressed while another step may hold the Flow: whatever it answers, it must not fail the server."""
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=_h())
    assert res.status_code < 500, res.text


def _complete(key_doc, cause="human_submitted", **kw):
    from flowgate.card_completion import completion

    return completion.complete(FA, key_doc, cause=cause, requested_by="u_kim", **kw)


# ── the hidden person's approval is closed; a completion moves the node once ──

def test_a_v01_approval_completes_nothing_and_the_completion_moves_the_node_once(env, decided):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)

    _written_by(env, result, PARK)                                      # the person who wrote the submission
    db_documents.update(result, {"doc_review_status": "approved"})      # the v0.1 review screen's approval
    v01_contact.after_review_decided(FA, result, "approve")
    _current(env)                                                       # a run read reconciles too
    assert _states(env)[t1] == "review" and _states(env)[t2] == "pending"
    assert _records(env) == []

    rec = _complete(result)
    assert rec["status"] == "applied" and rec["created"] is True
    assert _states(env)[t1] == "done" and _states(env)[t2] == "open"
    [row] = _records(env, t1)
    assert (row["cause"], row["actor_kind"], row["actor_id"], row["requested_by"]) == \
        ("human_submitted", "user", PARK, "u_kim")                     # its writer; the run's issuer as requested_by
    assert row["attempt_id"] == _attempts(env, t1)[0]["attempt_id"] and _attempts(env, t1)[0]["status"] == "ended"
    [done] = _done_events(env, t1)
    assert done["completion_id"] == row["completion_id"] and done["cause"] == "review_approved"
    assert len(_openings(env, t2)) == 1 and len(_attempts(env, t2)) == 1


def test_a_repeated_pass_and_a_retried_job_write_one_record_and_open_the_next_card_once(env, decided):
    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)

    first = _complete(result)
    second = _complete(result)                                          # the same pass told again
    third = _complete(result)                                           # the job retried
    assert first["completion_id"] == second["completion_id"] == third["completion_id"]
    assert (second["created"], third["created"]) == (False, False)
    assert len(_records(env)) == 1 and _records(env)[0]["status"] == "applied"
    assert _states(env)[t1] == "done" and len(_openings(env, t2)) == 1 and len(_attempts(env, t2)) == 1


def test_a_record_committed_then_a_lost_application_is_applied_once_by_the_reconciliation(env, decided):
    from flowgate.card_completion import completion

    t1, t2 = _confirmed(env)
    run = _command(env)
    result = _submit(env, t1, 90)
    attempt = _attempts(env, t1)[0]["attempt_id"]

    rec, created = completion.record(FA, run["run_id"], t1, attempt, result, cause="human_submitted",
                                     actor_kind="user", actor_id="u_lee", requested_by="u_kim",
                                     revision_no=_rev(result))
    assert created and rec["status"] == "recorded"                      # ① committed, ② lost (a crash / restart)
    assert _states(env)[t1] == "review"

    _current(env)                                                       # the run read's reconciliation
    assert _states(env)[t1] == "done" and _states(env)[t2] == "open"
    assert _records(env, t1)[0]["status"] == "applied"
    assert completion.apply(FA, rec["completion_id"]) == "applied"      # applied again: nothing moves
    _current(env)
    assert len(_openings(env, t2)) == 1 and len(_attempts(env, t2)) == 1 and len(_done_events(env, t1)) == 1


def test_an_apply_failure_after_the_record_leaves_it_recorded_and_the_next_read_applies_it(env, decided, monkeypatch):
    from flowgate.card_completion import completion, legacy_adapter

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    real = legacy_adapter.reconcile_node
    calls = {"n": 0}

    def failing(run, key, jobs):
        calls["n"] += 1
        raise RuntimeError("injected: the guard fails after the record")

    monkeypatch.setattr(legacy_adapter, "reconcile_node", failing)
    with pytest.raises(RuntimeError):
        _complete(result)
    assert calls["n"] == 1 and _records(env, t1)[0]["status"] == "recorded" and _states(env)[t1] == "review"
    monkeypatch.setattr(legacy_adapter, "reconcile_node", real)

    _current(env)
    assert _states(env)[t1] == "done" and _records(env, t1)[0]["status"] == "applied"
    assert len(_openings(env, t2)) == 1
    assert completion.complete(FA, result, cause="human_submitted", requested_by="u_kim")["created"] is False
    assert len(_openings(env, t2)) == 1


def test_the_same_review_approved_applied_twice_is_ignored_the_second_time(env, decided):
    from modules.flow_gate.graph_workflow.domain.frontier import RunEvent, resolve
    from modules.flow_gate.graph_workflow.domain.run_policies import policies_of
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    _complete(result)

    def again():
        run = run_store.active_run(FA)
        ctx = run_apply.load_context(run, "flowgate", None, [])
        d = resolve(ctx.revision, run_apply.read_run_state(ctx), RunEvent("review_approved", t1, doc_id=result),
                    policies_of(run_store.policy_entries(ctx.run_id)))
        if not d.ignored:
            run_apply.apply_decision(ctx, d)
        return d.ignored

    assert with_flow_guard(FA, again) is True
    assert len(_done_events(env, t1)) == 1 and len(_openings(env, t2)) == 1 and len(_attempts(env, t2)) == 1


def test_an_old_verdict_is_superseded_and_moves_nothing(env, decided):
    from flowgate.card_completion import completion
    from modules.flow_gate.db import documents as db_documents

    t1, t2 = _confirmed(env)
    run = _command(env)
    result = _submit(env, t1, 90)

    rev = _rev(result)
    old_attempt, _ = completion.record(FA, run["run_id"], t1, "att_older", result, cause="human_submitted",
                                       actor_kind="user", actor_id="u_lee", requested_by="u_kim", revision_no=rev)
    assert completion.apply(FA, old_attempt["completion_id"]) == "superseded"
    attempt = _attempts(env, t1)[0]["attempt_id"]
    stale, _ = completion.record(FA, run["run_id"], t1, attempt, result, cause="human_submitted", actor_kind="user",
                                 actor_id="u_lee", requested_by="u_kim", revision_no=rev)
    db_documents.update(result, {"revision_no": rev + 1})                    # the result revised in place meanwhile
    assert completion.apply(FA, stale["completion_id"]) == "superseded"
    _current(env)
    assert _states(env)[t1] == "review" and _states(env)[t2] == "pending" and _openings(env, t2) == []
    assert {r["status_reason"] for r in _records(env, t1)} == {"attempt_changed", "revision_changed"}


def test_a_rewind_after_the_completion_revokes_the_record_and_the_rework_has_its_own(env, decided):
    from flowgate.graph_time_machine import rewind
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    t1, t2 = _confirmed(env)
    _command(env, "step")                                               # the step stops at t1: the run goes idle
    result = _submit(env, t1, 90)
    first = _complete(result)
    assert _states(env)[t1] == "done" and run_store.active_run(FA)["status"] == "idle"

    def step():
        ctx = run_apply.load_context(run_store.active_run(FA), "flowgate", None, [])
        ids = [a["attempt_id"] for a in _attempts(env, t1)]
        return rewind.rewind(ctx, {"node_keys": [t1], "attempt_ids": ids, "documents": []}, [])

    changed = with_flow_guard(FA, step)
    assert changed["reset_node_keys"] == [t1]
    [row] = _records(env, t1)
    assert (row["completion_id"], row["status"], row["status_reason"]) == (first["completion_id"], "revoked",
                                                                           "time_machine")
    _current(env)
    assert _states(env)[t1] == "pending"                                # never done again from the revoked record

    _command(env, "step")                                               # the rework: a new attempt of t1
    rework = _submit(env, t1, 91)
    again = _complete(rework)
    assert again["status"] == "applied" and again["completion_id"] != first["completion_id"]
    assert len(_attempts(env, t1)) == 2 and _states(env)[t1] == "done"


def test_a_completion_racing_a_run_command_opens_the_next_card_once(env, decided):
    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    barrier = threading.Barrier(3)
    errors: list = []

    def run(fn):
        try:
            barrier.wait(timeout=10)
            fn()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(lambda: _complete(result),)),
               threading.Thread(target=run, args=(lambda: _command_any(env, "full"),)),
               threading.Thread(target=run, args=(lambda: _command_any(env, "step"),))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    _current(env)
    assert _states(env)[t1] == "done" and _states(env)[t2] == "open"
    assert len(_records(env, t1)) == 1 and len(_openings(env, t2)) == 1 and len(_attempts(env, t2)) == 1


# ── the automatic completion (card_settings) takes no v0.1 path and attributes the act ──

@pytest.fixture()
def no_v01_path(monkeypatch):
    """Any v0.1 approval step reached by the automatic completion fails the test."""
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import tr_commit_service
    from modules.flow_gate.workflow import pipeline_service

    def refuse(*_a, **_k):
        raise AssertionError("the v0.1 approval path was taken")

    monkeypatch.setattr(pipeline_service, "transition_document_review", refuse)
    monkeypatch.setattr(tr_commit_service, "on_document_approved", refuse)
    monkeypatch.setattr(v01_contact, "after_review_decided", refuse)
    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)   # the body / test gate: no file here


def test_the_automatic_completion_without_review_is_the_systems_and_takes_no_v01_path(env, decided, no_v01_path):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.db import documents as db_documents

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    assert legacy_adapter.approve(FA, result, "u_kim", legacy_adapter.AUTOMATION, node_key=t1) is True
    assert legacy_adapter.approve(FA, result, "u_kim", legacy_adapter.AUTOMATION, node_key=t1) is True   # retried
    [row] = _records(env, t1)
    assert (row["cause"], row["actor_kind"], row["actor_id"], row["requested_by"], row["status"]) == \
        ("no_review_submitted", "system", "system", "u_kim", "applied")
    assert db_documents.get_by_id(result)["doc_review_status"] == "approved"          # the system's status display
    events = _sql(env["db"], "SELECT note FROM events WHERE doc_id = ? AND event_type = 'card_completion_recorded'",
                  (result,))
    assert len(events) == 1 and json.loads(events[0]["note"])["completion_id"] == row["completion_id"]
    assert _states(env)[t1] == "done" and len(_openings(env, t2)) == 1


def test_a_passed_review_is_completed_by_the_reviewing_ai_provider(env, decided, no_v01_path):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.db import document_reviews as db_reviews

    t1, _t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    review = db_reviews.insert_review(doc_id=result, revision_no=_rev(result), reviewer_id="aip_card", verdict="pass",
                                      findings_json="[]", comment=None, reviewed_at="2026-11-02T00:00:00",
                                      requested_provider_id="aip_card", actual_provider_id="aip_card",
                                      actual_provider_name="Card AI")
    assert legacy_adapter.approve(FA, result, "u_kim", legacy_adapter.AUTOMATION, node_key=t1) is True
    [row] = _records(env, t1)
    assert (row["cause"], row["actor_kind"], row["actor_id"], row["requested_by"], row["verdict_id"]) == \
        ("ai_review_passed", "ai", "aip_card", "u_kim", str(review["id"]))


# ── an existing run's approved result ───────────────────────────────────────

def test_an_existing_run_reads_its_approved_result_as_complete(env, decided):
    from modules.flow_gate.db import documents as db_documents

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    db_documents.update(result, {"doc_review_status": "approved"})
    _current(env)
    assert _states(env)[t1] == "review"                                  # a run of the service: no record, no done
    _sql(env["db"], "UPDATE graph_card_completion_meta SET meta_value = '9999-12-31T00:00:00.000+00:00' "
                    "WHERE meta_key = 'installed_at'")                    # the same run, started before the service
    _current(env)
    assert _states(env)[t1] == "done" and _states(env)[t2] == "open" and _records(env) == []


# ── the actors of review / rejection / response rows, read without rewriting them ──

def test_existing_rows_read_their_ai_actor_and_the_issuer_as_requested_by_only():
    from modules.flow_gate.db import document_reviews as db_reviews

    old_review = {"reviewer_id": "u_kim", "actual_provider_id": "aip_rev", "actual_provider_name": "Codex"}
    assert db_reviews.review_actor(old_review) == {"actor_kind": "ai", "actor_id": "aip_rev", "actor_name": "Codex",
                                                   "requested_by": "u_kim"}
    assert db_reviews.review_actor({"reviewer_id": "u_lee"})["actor_kind"] == "user"
    item = {"rejected_by": "u_kim", "ai_response": "fixed", "response_recorded_by": "u_kim",
            "rejection_provider": {"ai_provider_id": "aip_rev", "ai_provider_name": "Codex", "ai_run_id": "r1"},
            "response_provider": {"ai_provider_id": "aip_work", "ai_provider_name": "Claude", "ai_run_id": "r2"}}
    seen = db_reviews.rejection_actors(item)
    assert seen["rejection_actor"] == {"actor_kind": "ai", "actor_id": "aip_rev", "actor_name": "Codex",
                                       "requested_by": "u_kim"}
    assert seen["response_actor"] == {"actor_kind": "ai", "actor_id": "aip_work", "actor_name": "Claude",
                                      "requested_by": "u_kim"}
    assert item.get("rejection_actor") is None                           # the stored item is not rewritten
    person = db_reviews.rejection_actors({"rejected_by": "u_lee", "rejection_provider": None})
    assert person["rejection_actor"]["actor_kind"] == "user" and person["response_actor"] is None


def test_an_automatic_rejection_names_the_reviewing_ai(env):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services.ai_invoke import review

    doc = _doc(FA, 95, "T", "rejected")
    db_documents.update(doc, {"rejection_history": json.dumps([
        {"rejection_id": "rj_1", "reason": "x", "rejected_by": "u_kim", "review_id": 7}])})
    review._attribute_rejection(doc, {"id": 7, "actual_provider_id": "aip_rev"}, "u_kim")
    [item] = json.loads(db_documents.get_by_id(doc)["rejection_history"])
    assert (item["rejected_by"], item["rejected_by_kind"], item["requested_by"]) == ("aip_rev", "ai", "u_kim")
    review._attribute_rejection(doc, {"id": 7}, "u_kim")                  # no provider evidence: unchanged
    assert json.loads(db_documents.get_by_id(doc)["rejection_history"])[0]["rejected_by"] == "aip_rev"


def test_the_rewind_and_the_startup_reach_the_completion_service():
    from pathlib import Path

    server = Path(__file__).resolve().parents[2]
    assert "card_completion.revoke_rewound(run_id, keys)" in \
        (server / "flowgate" / "graph_time_machine" / "rewind.py").read_text(encoding="utf-8")
    startup = (server / "startup.py").read_text(encoding="utf-8")
    assert "start_card_completion_integration()" in startup


# ── flowgate.v02.0119 T#2 rework (review rej_01M6D564RSF8JQE7): the registered completion contract ─────────────────
#
# Each test below runs with ``flowgate.card_completion`` registered as the server's start registers it (``completions``),
# except the unregistered one, which removes every handler (the engine suites' stand-in included) on purpose.

PARK = "u_park"                                                          # the person who writes the submission


def _user(env, uid):
    _sql(env["db"], "INSERT OR IGNORE INTO users (user_id, username, email, password, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'x', 't', 't')", (uid, uid, f"{uid}@x"))


def _written_by(env, doc_id, uid):
    """The submission's own record of its author: the creation event of revision 0 (``doc_created``)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import workflow_events as db_events

    _user(env, uid)
    row = db_documents.get_by_id(doc_id)
    db_events.create({"event_type": "doc_created", "project_id": "flowgate", "group_id": FA,
                      "document_id": row["id"], "actor_user_id": uid, "to_state": row.get("status")})


def _revise(env, doc_id, uid):
    """A person's resubmission in place (the same attempt): the next revision, its edit event naming its author."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import workflow_events as db_events

    _user(env, uid)
    rev = _rev(doc_id) + 1
    db_documents.update(doc_id, {"revision_no": rev, "doc_review_status": "pending_review"})
    db_events.create({"event_type": "doc_edited", "project_id": "flowgate", "group_id": FA, "document_id": None,
                      "actor_user_id": uid, "metadata": json.dumps({"doc_id": doc_id, "revision_no": rev})})
    return rev


def _approve(doc_id, key, issuer="u_lee", **kw):
    from flowgate.card_settings import legacy_adapter

    return legacy_adapter.approve(FA, doc_id, issuer, legacy_adapter.AUTOMATION, node_key=key, **kw)


# 1) an approved result is judged like any other: the revision reviewed and the submission reviewed (assigned_review)

class _Hold:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return True

    def __exit__(self, *exc):
        return False


def _assigned(author, revision):
    from flowgate.card_settings import stage_assignment as sa

    return {"stage": sa.RESULT_REVIEW, "run_id": "run_x", "node_key": "nk_x", "attempt_id": "att_x", "author": author,
            "basis_doc_id": "doc_x", "basis_revision_no": revision, "basis_rejection_count": 0}


@pytest.mark.parametrize("approved", [True, False])
def test_an_approved_result_moved_since_its_review_is_handed_over_not_completed(monkeypatch, approved):
    from flowgate.card_settings import assigned_review, legacy_adapter

    calls: list = []
    current = {"doc_id": "doc_x", "revision_no": 2, "doc_review_status": "approved" if approved else "pending_review"}
    monkeypatch.setattr(assigned_review, "_context", lambda *a: ({"run_id": "run_x", "started_by": "u_kim"},
                                                                 {"node_key": "nk_x"}))
    monkeypatch.setattr(assigned_review, "hold_of", _Hold)
    monkeypatch.setattr(assigned_review, "_doc", lambda doc_id: dict(current))
    monkeypatch.setattr(legacy_adapter, "rejection_count", lambda doc: 0, raising=False)
    monkeypatch.setattr(legacy_adapter, "approve", lambda *a, **k: calls.append((a, k)) or True)
    # the pass answered revision 1; the document (approved before or not) is at revision 2 now
    assert assigned_review.approve_for("flow_x", _assigned("ai", 1), dict(current), assigned_review.AUTO, 1) \
        == assigned_review.MOVED
    # a person's submission newer than the assignment's basis is handed over too
    assert assigned_review.approve_for("flow_x", _assigned("person", 1), dict(current), assigned_review.AUTO, 2) \
        == assigned_review.MOVED
    assert calls == []


def test_an_approved_report_handed_over_is_completed_at_the_reviewed_revision(monkeypatch):
    from flowgate.card_settings import assigned_review, legacy_adapter

    calls: list = []
    current = {"doc_id": "doc_x", "revision_no": 2, "doc_review_status": "approved"}
    monkeypatch.setattr(assigned_review, "_context", lambda *a: ({"run_id": "run_x", "started_by": "u_kim"},
                                                                 {"node_key": "nk_x"}))
    monkeypatch.setattr(assigned_review, "hold_of", _Hold)
    monkeypatch.setattr(assigned_review, "_doc", lambda doc_id: dict(current))
    monkeypatch.setattr(legacy_adapter, "approve", lambda *a, **k: calls.append(k) or True)
    assert assigned_review.approve_for("flow_x", _assigned("ai", 2), dict(current), assigned_review.AUTO, 2) is True
    [kw] = calls
    assert (kw["expected_revision"], kw["author"], kw["node_key"]) == (2, "ai", "nk_x")
    # the completion found the revision moved inside its own transaction: MOVED (the newer submission's own review)
    monkeypatch.setattr(legacy_adapter, "approve", lambda *a, **k: legacy_adapter.REVISION_MOVED)
    assert assigned_review.approve_for("flow_x", _assigned("ai", 2), dict(current), assigned_review.AUTO, 2) \
        == assigned_review.MOVED


# 2) an older verdict superseded, then the newer submission of the same attempt actually completes

def test_a_resubmission_in_the_same_attempt_completes_after_its_older_record_is_superseded(env, decided, no_v01_path):
    from flowgate.card_completion import completion

    t1, t2 = _confirmed(env)
    run = _command(env)
    result = _submit(env, t1, 90)
    attempt = _attempts(env, t1)[0]["attempt_id"]
    old, _ = completion.record(FA, run["run_id"], t1, attempt, result, cause="human_submitted", actor_kind="user",
                               actor_id="u_lee", requested_by="u_kim", revision_no=_rev(result))
    _revise(env, result, PARK)                                           # resubmitted in place: revision 1
    assert completion.apply(FA, old["completion_id"]) == "superseded"
    # the newer submission (same document, same attempt) completes as its key's next generation
    assert _approve(result, t1, author="person") is True
    rows = _records(env, t1)
    assert [(r["generation"], r["revision_no"], r["status"]) for r in rows] == [(0, 0, "superseded"), (1, 1, "applied")]
    assert (rows[1]["cause"], rows[1]["actor_kind"], rows[1]["actor_id"]) == ("human_submitted", "user", PARK)
    assert _states(env)[t1] == "done" and len(_openings(env, t2)) == 1
    assert _approve(result, t1, author="person") is True                 # a repeated pass: the same record
    assert len(_records(env, t1)) == 2


def test_a_recorded_older_revision_is_superseded_by_the_newer_submissions_record(env, decided, no_v01_path):
    from flowgate.card_completion import completion

    t1, _t2 = _confirmed(env)
    run = _command(env)
    result = _submit(env, t1, 90)
    attempt = _attempts(env, t1)[0]["attempt_id"]
    completion.record(FA, run["run_id"], t1, attempt, result, cause="human_submitted", actor_kind="user",
                      actor_id="u_lee", requested_by="u_kim", revision_no=_rev(result))   # ① only (② lost)
    _revise(env, result, PARK)
    assert _approve(result, t1, author="person") is True
    assert [(r["status"], r["status_reason"]) for r in _records(env, t1)] == [("superseded", "revision_changed"),
                                                                              ("applied", None)]
    assert _states(env)[t1] == "done"


def test_a_superseded_or_revoked_record_is_never_reported_as_a_completion(env, decided, no_v01_path, monkeypatch):
    from flowgate.card_completion import completion

    t1, _t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    monkeypatch.setattr(completion, "apply", lambda flow_id, cid: completion.SUPERSEDED)
    assert _approve(result, t1, author="person") is False                # apply superseded it: no completion
    [row] = _records(env, t1)
    _sql(env["db"], "UPDATE graph_card_completions SET status = 'revoked' WHERE completion_id = ?",
         (row["completion_id"],))
    assert _approve(result, t1, author="person") is False                # its key's row is revoked: not renewed
    assert len(_records(env, t1)) == 1 and _states(env)[t1] == "review"


# 3) an existing run's fallback never reads past a record of the node, nor a rework's new attempt

def _legacy(env):
    _sql(env["db"], "UPDATE graph_card_completion_meta SET meta_value = '9999-01-01T00:00:00.000+00:00' "
                    "WHERE meta_key = 'installed_at'")                    # the run started before the service


@pytest.mark.parametrize("state", ["revoked", "superseded", "stale"])
def test_an_existing_runs_approved_result_does_not_revive_a_record_of_the_node(env, decided, state):
    from flowgate.card_completion import completion
    from modules.flow_gate.db import documents as db_documents

    t1, t2 = _confirmed(env)
    run = _command(env)
    result = _submit(env, t1, 90)
    attempt = _attempts(env, t1)[0]["attempt_id"]
    rec, _ = completion.record(FA, run["run_id"], t1, attempt, result, cause="human_submitted", actor_kind="user",
                               actor_id="u_lee", requested_by="u_kim", revision_no=_rev(result))
    if state == "stale":
        db_documents.update(result, {"revision_no": _rev(result) + 1})   # recorded, its revision moved
    else:
        _sql(env["db"], "UPDATE graph_card_completions SET status = ? WHERE completion_id = ?",
             (state, rec["completion_id"]))
    db_documents.update(result, {"doc_review_status": "approved"})
    _legacy(env)
    _current(env)
    assert _states(env)[t1] == "review" and _states(env)[t2] == "pending"


def test_an_existing_runs_rework_attempt_is_completed_by_its_record_only(env, decided):
    from modules.flow_gate.db import documents as db_documents

    t1, _t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    _legacy(env)
    _sql(env["db"], "UPDATE graph_node_attempts SET created_at = '9999-06-01T00:00:00.000+00:00' WHERE node_key = ?",
         (t1,))                                                           # the attempt opened after the installation
    db_documents.update(result, {"doc_review_status": "approved"})
    _current(env)
    assert _states(env)[t1] == "review" and _records(env) == []
    _sql(env["db"], "UPDATE graph_node_attempts SET created_at = '2000-01-01T00:00:00.000+00:00' WHERE node_key = ?",
         (t1,))                                                           # an attempt of before: the old rows' rule
    _current(env)
    assert _states(env)[t1] == "done" and _records(env) == []


# 4) TS / TR2: their own effect run by the v0.2 completion, no v0.1 approval, recoverable

def _as_type(env, doc_id, typ):
    _sql(env["db"], "UPDATE documents SET type_code = ? WHERE doc_id = ?", (typ, doc_id))


def test_a_ts_completion_admits_its_run_with_its_record_atomically_and_takes_no_v01_path(env, decided, no_v01_path,
                                                                                        monkeypatch):
    from flowgate.card_completion import own_effects
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import events as db_events
    from modules.flow_gate.services import spec_execution_service

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    _as_type(env, result, "TS")
    tries = {"n": 0}

    def admit(doc, parsed, selected, runner_id):
        tries["n"] += 1
        db_events.insert_event(doc["doc_id"], "fake_ts_run_admitted", note=runner_id)       # the effect's own write
        if tries["n"] == 1:
            raise RuntimeError("injected: the process fails after the admission, before the commit")
        return {"run": {"run_id": "tr_1"}, "tsr_doc_id": "tsr_1", "selected_case_ids": ["c1"]}

    monkeypatch.setattr(own_effects, "_ts_plan", lambda doc: ({"cases": []}, ["c1"]))
    monkeypatch.setattr(own_effects, "_admit", admit)
    monkeypatch.setattr(own_effects, "_announce", lambda doc, admitted: None)
    monkeypatch.setattr(spec_execution_service, "admission_blocker", lambda doc_id: None)

    def admitted():
        return _sql(env["db"], "SELECT note FROM events WHERE doc_id = ? AND event_type = 'fake_ts_run_admitted'",
                    (result,))

    assert _approve(result, t1, issuer="u_kim") is False                  # rolled back together: no run, no record
    assert admitted() == [] and _records(env) == [] and db_documents.get_by_id(result)["doc_review_status"] != "approved"
    assert _approve(result, t1, issuer="u_kim") is True                   # the retry: the admission and the record
    assert len(admitted()) == 1 and admitted()[0]["note"] == "u_kim"
    [row] = _records(env, t1)
    assert row["status"] == "applied" and _states(env)[t1] == "done" and len(_openings(env, t2)) == 1
    assert _approve(result, t1, issuer="u_kim") is True and len(admitted()) == 1 and tries["n"] == 2


def test_a_tr2_completion_applies_under_the_system_and_records_in_its_finalize(env, decided, no_v01_path, monkeypatch):
    from flowgate.card_completion import own_effects
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.documents import tr2_approval_service

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    _as_type(env, result, "TR2")
    seen: list = []

    def tr2_apply(**kw):                                                  # the strong TR2 apply, its finalize only
        seen.append(kw)
        db_documents.update(result, {"doc_review_status": "approved"})
        kw["completion_hook"](db_documents.get_by_id(result))
        return db_documents.get_by_id(result)

    monkeypatch.setattr(tr2_approval_service, "approve", tr2_apply)
    monkeypatch.setattr(own_effects, "_tr2_succeeded", lambda doc_id, rev: False)
    assert _approve(result, t1, issuer="u_lee") is True
    [kw] = seen
    assert kw["actor_user_id"] == "system" and kw["request_key"].startswith("card:") and kw["expected_revision"] == 0
    [row] = _records(env, t1)
    assert (row["status"], row["requested_by"]) == ("applied", "u_kim") and _states(env)[t1] == "done"


def test_a_tr2_applied_whose_record_was_lost_is_recovered_without_applying_again(env, decided, no_v01_path,
                                                                                monkeypatch):
    from flowgate.card_completion import own_effects
    from modules.flow_gate.db import documents as db_documents

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    _as_type(env, result, "TR2")
    applied = {"n": 0}

    def crashed(flow_id, doc_id, revision, issuer, permissions, request_key, hook):
        applied["n"] += 1                                                 # applied and committed, then a crash before
        db_documents.update(result, {"doc_review_status": "approved"})   # its completion was written
        raise RuntimeError("injected: the server stops after the TR2 commit")

    monkeypatch.setattr(own_effects, "_tr2_apply", crashed)
    monkeypatch.setattr(own_effects, "_retryable_tr2", lambda exc: False)
    assert _approve(result, t1) is False and _records(env) == [] and _states(env)[t1] == "review"
    monkeypatch.setattr(own_effects, "_tr2_succeeded", lambda doc_id, rev: True)   # its attempt succeeded
    assert _approve(result, t1) is True                                   # the restart's retry: recovery
    assert applied["n"] == 1
    [row] = _records(env, t1)
    assert row["status"] == "applied" and _states(env)[t1] == "done" and len(_openings(env, t2)) == 1


# 5) the reconciliation settles stale records with no explicit apply (run read / command)

def test_a_run_read_settles_a_recorded_older_revision_and_an_older_attempt(env, decided):
    from flowgate.card_completion import completion
    from modules.flow_gate.db import documents as db_documents

    t1, t2 = _confirmed(env)
    run = _command(env)
    result = _submit(env, t1, 90)
    attempt = _attempts(env, t1)[0]["attempt_id"]
    stale, _ = completion.record(FA, run["run_id"], t1, attempt, result, cause="human_submitted", actor_kind="user",
                                 actor_id="u_lee", requested_by="u_kim", revision_no=_rev(result))
    older, _ = completion.record(FA, run["run_id"], t1, "att_older", result, cause="human_submitted",
                                 actor_kind="user", actor_id="u_lee", requested_by="u_kim", revision_no=_rev(result))
    db_documents.update(result, {"revision_no": _rev(result) + 1})       # ① then a crash, and the result revised
    _current(env)                                                         # no apply: the run read's reconciliation
    rows = {r["completion_id"]: r for r in _records(env, t1)}
    assert (rows[stale["completion_id"]]["status"], rows[stale["completion_id"]]["status_reason"]) == \
        ("superseded", "revision_changed")
    assert (rows[older["completion_id"]]["status"], rows[older["completion_id"]]["status_reason"]) == \
        ("superseded", "attempt_changed")
    assert _states(env)[t1] == "review" and _openings(env, t2) == []


def test_a_command_settles_an_other_attempts_record_of_a_done_node_without_applying_it(env, decided):
    from flowgate.card_completion import completion

    t1, _t2 = _confirmed(env)
    run = _command(env, "step")
    result = _submit(env, t1, 90)
    done = _complete(result)
    assert done["status"] == "applied"
    other, _ = completion.record(FA, run["run_id"], t1, "att_other", result, cause="human_submitted",
                                 actor_kind="user", actor_id="u_lee", requested_by="u_kim", revision_no=_rev(result))
    _command(env, "step")                                                 # the command's reconciliation
    row = next(r for r in _records(env, t1) if r["completion_id"] == other["completion_id"])
    assert (row["status"], row["status_reason"]) == ("superseded", "attempt_changed")   # never "applied"


# 6) unregistered: nothing completes a node; a failed registration fails the start

def test_with_no_registered_handler_a_document_approval_moves_no_node(env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    t1, t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    for name in ports.PORT_NAMES:                                         # the real handlers and the suites' stand-in
        ports.unregister(name)
    db_documents.update(result, {"doc_review_status": "approved"})
    v01_contact.after_review_decided(FA, result, "approve")
    _current(env)
    _command_any(env, "full")
    assert _states(env)[t1] == "review" and _states(env)[t2] == "pending" and _records(env) == []


def test_a_failed_completion_registration_fails_the_start(monkeypatch):
    import startup
    from flowgate.card_completion import integration

    def broken():
        raise RuntimeError("injected: the completion service cannot register")

    monkeypatch.setattr(integration, "register", broken)
    with pytest.raises(RuntimeError):
        startup.start_card_completion_integration()


# 7) actors: the person who wrote it, the run's issuer, the provider the review actually ran on

def test_a_persons_result_completed_under_the_systems_authority_names_its_author_and_the_runs_issuer(
        env, decided, no_v01_path):
    t1, _t2 = _confirmed(env)
    _command(env)                                                         # started by u_kim (the run's issuer)
    result = _submit(env, t1, 90)
    _written_by(env, result, PARK)                                        # written by u_park
    assert _approve(result, t1, issuer="u_lee", author="person") is True  # the token's recipient: u_lee (assignee)
    [row] = _records(env, t1)
    assert (row["cause"], row["actor_kind"], row["actor_id"], row["requested_by"]) == \
        ("human_submitted", "user", PARK, "u_kim")


@pytest.mark.parametrize("evidence,expected", [
    ({"requested_provider_id": "aip_assignee", "actual_provider_id": "aip_card"}, "aip_card"),
    ({"requested_provider_id": "aip_assignee"}, None),
    ({"requested_provider_id": "aip_assignee", "review_run_id": "rv_1"}, "aip_card"),
])
def test_a_passed_review_names_only_the_provider_it_actually_ran_on(env, decided, no_v01_path, monkeypatch,
                                                                   evidence, expected):
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.services.ai_invoke import runtime

    monkeypatch.setattr(runtime, "get_run_record", lambda run_id: {"provider_id": "aip_card"} if run_id == "rv_1"
                        else None)
    t1, _t2 = _confirmed(env)
    _command(env)
    result = _submit(env, t1, 90)
    db_reviews.insert_review(doc_id=result, revision_no=_rev(result), reviewer_id="u_kim", verdict="pass",
                             findings_json="[]", comment=None, reviewed_at="2026-11-02T00:00:00", **evidence)
    assert _approve(result, t1, issuer="u_kim", author="ai") is True
    [row] = _records(env, t1)
    assert (row["cause"], row["actor_kind"], row["actor_id"]) == ("ai_review_passed", "ai", expected)
