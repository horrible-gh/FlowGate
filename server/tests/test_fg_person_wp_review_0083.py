"""flowgate.v02.0083 T#4 (WP-4) — a work plan (WP) card a person writes: the card's AI review of every submission, or with
review 'none' its automatic approval. A new path, kept apart from T#1~3 (its own commit and this file).

The person's result is bound by the result binder, which hands it to the card before its settlement
(``card_settings_ports.person_result`` -> ``review_stage.person_result``: the node is marked as worked on) and runs the
card's step after its commit (``follow_person_result``):

- review count 1 or more, or until it passes (the same here): one AI review of the submission — a loop of its own,
  ``review_count`` 1, no rework AI (``rework_provider_id`` None) — requested as a person's explicit review request
  (Q439), so an idle run takes it and goes back to running. A pass is approved by the existing review stage
  (``_after_review``, ``legacy.node_of_result`` now takes a person's reviewed plan node) and the next card opens.
  ``issues`` rejects the document and the engine ends the loop (no rework hop: ``review_count_exhausted`` with
  ``stop_detail`` ``awaiting_author_revision`` — the stop_reason CHECK of migration 107 takes no new value, and the
  schema is not changed); the node stays in review and its author submits again (``mark_revised`` or a new revision),
  which starts a new loop whose rounds start from 0 (``compute_review_baseline``);
- review 'none': ``legacy.approve`` with the run issuer's own authority (``_get_user_permissions``, the transition rules,
  the TR hook) — never the author's; without it nothing is tried and a person approves the card.

Covered (T#4 §5):
1. a person's WP on an idle run -> running -> passed -> approved -> the next card opens (its AI job);
2. review count 1 (and until it passes): rejected (document rejected, no AI rework, node in review) -> resubmitted by
   ``mark_revised``, by a new revision, or by the real HTTP ``POST /inbox`` ``action=edit`` of the same document (its
   ``revision_no`` raised, its contact ``inbox_edited``) -> reviewed again once -> passed -> approved;
3. duplicates: the same bind told twice, a submission while the loop works, a start tried on a rejected document ->
   one loop at most; and the submission made while the loop worked (a new revision, or the same document edited in
   place) is reviewed exactly once when that loop ends, then approved — never by the old loop's pass — and is not
   taken over under a Time Machine operation;
4. Stop: a Stop during the review ends it and approves nothing (nor takes anything over); a submission on a stopped run
   (or under a Time Machine operation) starts no review; after a Resume the new attempt's submission is reviewed; a
   Time Machine operation, an attempt invalidation or a Stop committed between the bind's job and its step refuses that
   step (approval / review start) and records it — the step is judged under the Stop gate's shared hold;
5. attempt invalidation: the old attempt's review is refused (its verdict, its late pass, its stale start: recorded,
   nothing approved); after a T#2 card recovery the old loop's result is refused too;
6. review 'none': approved and on with the issuer's ``document.approve``, awaiting a person without it (whatever the
   author holds); a Stop's exclusive hold asked while that approval is in flight waits until it is over;
7. the engine: a loop with its rework AI keeps its stages for count 1 / 2 / -1 (``review_count_exhausted``), hold and
   stalled stay as they are and a rejected document's AI loop starts with its rework; the loop with no rework AI
   stops on ``issues`` (the real worker: one hop, the rejection, no rework hop) and is refused at admission in rework;
8. a lost contact (L0009 E-27): a submission the run read's or a Resume's reconciliation binds takes the card's step —
   its review, or with 'none' its automatic approval — once, however often it is recovered.

On the card settings suites' real SQLite DB, routes and fixtures (``test_fg_card_settings``,
``test_fg_card_settings_review``, ``test_fg_card_approval_0056``, ``test_fg_wp_ai_run_0083``): the result binder, the
Graph execution journal (``graph_attempt_runs``), the fencing, the Stop / Resume / recovery routes, the loop row, the
checkpoint transaction (``_auto_reject``, the gate) and the v0.1 approval are the real ones. Only the AI invoke admission
is a recorder (what the card asks it to start); its own step for a loop — reading the Graph's journal loop and storing
the row (``admit_author_revision_loop``) — is made by ``_admitted`` with the engine's own function.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _h, _sql, _start, db_path, decided, env, setting,
)
from test_fg_card_settings_review import _admin_and_reviewer, _node, _review_status
from test_fg_card_approval_0056 import (  # noqa: F401 — card_approval_port: registered as startup does
    LEE, _approve, _current, _node_run, _resume, _run_id, _stop, card_approval_port,
)
from test_fg_wp_ai_run_0083 import OPUS, _cards, _opus, _run_status, _tokens, _wp_result

REVIEWER = "aip_review"
API = "http://127.0.0.1:8089/flowgate/api/v1"
AUTHOR_STOP = ("stopped", "review_count_exhausted", "awaiting_author_revision")


# ── fixtures and helpers ─────────────────────────────────────────────────────

@pytest.fixture()
def reviews(monkeypatch):
    """The AI invoke admission replaced by a recorder: what the card asks it to start, one run id per start. A
    requested execution the run's settlement redelivers (``execution.replay``) is recorded once, as the env fixture's
    job recorder does (a worker's execution lease admits it once)."""
    from modules.flow_gate.services import ai_invoke_service

    calls = []

    def start_run(**kw):
        known = next((i for i, c in enumerate(calls) if kw.get("graph_exec_id") and
                      c.get("graph_exec_id") == kw["graph_exec_id"]), None)
        if known is None:
            calls.append(kw)
            known = len(calls) - 1
        return {"run_id": f"air_person_review_{known + 1}", "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
    monkeypatch.setattr(ai_invoke_service, "_broadcast", lambda *args, **kwargs: None)
    return calls


def _person_wp(env, review_count=1, card=("WP", LEE)):
    """R -> a card a person (u_lee) writes -> an AI T card, confirmed and started: (its key, the T key). The run waits
    for the person (idle, waiting_approval: 0083 WP-2)."""
    _admin_and_reviewer(env)
    settings = (("review_count", review_count), ("review_ai", REVIEWER)) if review_count else ()
    key, t2 = _cards(env, [card, ("T", AI)], {0: settings} if settings else None)
    _start(env)
    assert env["jobs"] == [] and _node(env, key) == ("open", None)
    assert _run_status(env) == ("idle", "waiting_approval")
    return key, t2


def _token(env, key):
    return _tokens(env, key)[-1]["token_id"]                    # the node's dispatch token of its latest attempt


def _submit(env, key, seq):
    """The person registers a WP with the node's token, as the inbox does (its Graph contact)."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    doc = _wp_result(seq)
    v01_contact.after_inbox_registered(FA, doc, "WP", _token(env, key))
    return doc


def _revise(env, key, old, seq):
    """The person registers a new revision of the WP (its supersede chain), as an edit registration does."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    new = _wp_result(seq)
    db_documents.set_superseded_by(old, new)
    v01_contact.after_inbox_registered(FA, new, "WP", _token(env, key))
    return new


def _mark_revised(doc):
    """The author puts the rejected WP back in review: the v0.1 ``review_transitions/mark_revised`` route and its
    contact."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    transition_document_review(doc_id=doc, action="mark_revised", actor_user_id="u_lee",
                               user_permissions={"document.update"})
    v01_contact.after_review_decided(FA, doc, "mark_revised")


def _admitted(call, index):
    """What the AI invoke admission makes of a recorded loop start: the Graph journal's loop (``execution.loop_state``,
    as admission reads it), ``admit_author_revision_loop`` on it, the durable loop row, and the run the worker
    checkpoints with."""
    from flowgate.graph_run import execution
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.services.ai_invoke import review

    run_id = f"air_person_review_{index}"
    loop = dict(execution.loop_state(call["graph_exec_id"])["loop"])
    author = review.admit_author_revision_loop(loop, loop["current_stage"])
    started = datetime.now(timezone.utc)
    loop.update(round_no=1, stop_reason=None, stop_detail=None, attempts_used=0, started_at=started.isoformat(),
                deadline_at=(started + timedelta(seconds=int(loop["total_timeout_sec"]))).isoformat())
    db_loops.insert({**loop, "run_id": run_id, "group_id": FA, "doc_ref": call["doc_ref"]})
    return {"run_id": run_id, "group_id": FA, "doc_ref": call["doc_ref"], "action_scope": "review", "mode": "single",
            "issued_to": call["issued_to"], "graph_exec_id": call["graph_exec_id"], "outcome": "complete",
            "end_reason": "exited", "api_base_url": API, "review_loop_author_revision": author,
            "document_review_loop": db_loops.get(run_id)}


def _verdict(run, verdict, findings=(), revision=None):
    """The review hop registers its verdict on the document's current revision (owned by the loop's run); ``revision``:
    the revision it reviewed, when the document was revised in place since."""
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.db import documents as db_documents

    if revision is None:
        revision = int(db_documents.get_by_id(run["doc_ref"]).get("revision_no") or 0)
    stamp = datetime.now(timezone.utc).isoformat()
    return db_reviews.insert_review(run["doc_ref"], revision, REVIEWER, verdict,
                                    json.dumps([{"locus": "steps", "note": n} for n in findings]), None, stamp,
                                    review_run_id=run["run_id"], attempt_no=1)


def _hop_ends(run):
    """The worker's checkpoint of the hop that ended: the real transaction (the automatic rejection, the gate)."""
    from modules.flow_gate.services.ai_invoke import review

    latest = review._checkpoint_document_review_loop(run)
    run["document_review_loop"] = latest
    return latest


def _stage(row):
    return row["current_stage"], row.get("stop_reason"), row.get("stop_detail")


def _loop_ended(run):
    """The review run's process ended (its own execution's ledger end, as ``_executions_ended`` ends one: the recorder
    never launched it) and released its lease: the run-end port follows it with the loop row as it stands, then the
    finalize's settlement (``execution.settle``)."""
    from flowgate.graph_run import execution
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.services import ai_run_end_hooks

    row = ledger.get(run["graph_exec_id"]) or {}
    if row.get("status") == "requested":
        ledger.cancel(row["exec_id"], None, "requested")
    elif row.get("status") == "running":
        ledger.finish(row["exec_id"], row["lease_token"], "ended")
    ended = {**run, "document_review_loop": db_loops.get(run["run_id"])}
    ai_run_end_hooks.run_ending(ended)
    ai_run_end_hooks.run_ended(ended)
    execution.settle(FA)


def _passes(run):
    _verdict(run, "pass")
    assert _stage(_hop_ends(run)) == ("stopped", "review_passed", None)
    _loop_ended(run)


def _ledger(env):
    return _sql(env["db"], "SELECT kind, status, attempt_id FROM graph_attempt_runs ORDER BY created_at, exec_id")


def _active_attempt(env, key):
    rows = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'", (key,))
    return rows[0]["attempt_id"] if rows else None


def _refusals(env):
    rows = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE cause = 'attempt_inactive' ORDER BY event_seq")
    return [(p.get("operation"), p.get("reason")) for p in (json.loads(r["payload_json"]) for r in rows)]


def _run_causes(env, after=0):
    return [r["cause"] for r in _sql(env["db"], "SELECT cause FROM graph_run_events WHERE node_key IS NULL AND "
                                                "event_seq > ? ORDER BY event_seq", (after,))]


def _seq(env):
    return _sql(env["db"], "SELECT last_event_seq FROM graph_runs WHERE group_id = ?", (FA,))[0]["last_event_seq"]


def _grant(user, *perms):
    """``user`` holds ``perms`` in the project (the inbox's own RBAC check, run for real as in the T#1 suite)."""
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.rbac import permission_service

    store, now, role = get_store(), now_iso(), f"role_{user}_0083_wp4"
    store._execute("INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
                   [role, role, now, now])
    for perm in perms:
        store._execute("INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
                       [perm, perm, now])
        store._execute("INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)", [role, perm])
    store._execute("INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) "
                   "VALUES (?,?,?,?)", [user, "flowgate", role, now])
    permission_service.clear_all_cache()


def _wp_body(note):
    """A valid WP body (the T#1 suite's, its default note changed: what the author revised)."""
    from modules.flow_gate.services import work_plan_service as wp

    counted = ["T"]
    quantities = {"T": {"unit": wp.WORK_PLAN_TYPE_UNITS["T"], "count": 1}}
    body = {"wp_version": 1, "binding": "advisory", "counted_types": counted, "quantities": quantities,
            "provider_candidates": [{"provider_id": OPUS, "display_name": "Claude Opus 5.5",
                                     "group_label": "Claude · CLI"}],
            "defaults": {"provider_id": None, "note": note}, "steps": wp.expand_steps(counted, quantities)}
    return wp.dumps(wp.validate(body))


def _author_edits(env):
    """The author (u_lee) may edit the Flow's documents through the inbox (its RBAC, run for real)."""
    _opus(env)
    _grant("u_lee", "perm_document_read", "perm_document_create", "perm_document_update")


def _edit(doc, note, reason="rejected", user="u_lee"):
    """The author registers a new revision of ``doc`` itself through the real HTTP ``POST /inbox`` ``action=edit``, with
    an edit token for the document: (the response, the token)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import token_service

    token = token_service.issue(project="flowgate", group_id=FA, action_scope="edit", doc_ref=doc, issued_to=user)
    body = {"project": "flowgate", "module": "v02", "group_name": FA, "action": "edit", "doc_id": doc,
            "edit_reason": reason, "content": _wp_body(note)}
    if reason == "rejected":
        body["rejection_response"] = "step 2 names its owner now"
    app = FastAPI()
    app.include_router(inbox_routes.router)
    res = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/inbox", headers={"Authorization": f"Bearer {token['raw_token']}"}, json=body)
    return res, token


def _revision(doc):
    from modules.flow_gate.db import documents as db_documents

    return int(db_documents.get_by_id(doc).get("revision_no") or 0)


# ── 1. submitted on an idle run -> running -> passed -> approved -> next ─────

def test_1_a_person_s_wp_on_an_idle_run_is_reviewed_running_passed_approved_and_the_next_card_opens(
        env, decided, reviews):
    wp, t2 = _person_wp(env)
    seq = _seq(env)
    doc = _submit(env, wp, 81)
    [call] = reviews                                                    # the card's review, once
    assert (call["doc_ref"], call["action_scope"], call["mode"]) == (doc, "review", "single")
    assert call["issued_to"] == "u_kim"                                 # the run's issuer, not the author (u_lee)
    loop = call["document_review_loop"]
    assert (loop["review_count"], loop["reviewer_provider_id"], loop["rework_provider_id"]) == (1, REVIEWER, None)
    attempt = _active_attempt(env, wp)
    assert _ledger(env) == [{"kind": "review", "status": "requested", "attempt_id": attempt}]
    assert _run_status(env) == ("running", None)                         # the explicit request took the idle run back
    assert "work_resumed" in _run_causes(env, seq)
    assert _node(env, wp) == ("review", doc) and _node_run(env, wp)["awaiting_approval"] is False
    run = _admitted(call, 1)
    assert run["review_loop_author_revision"] is True
    assert run["document_review_loop"]["rework_provider_id"] == REVIEWER   # the row's NOT NULL column, never used
    _passes(run)
    assert (_node(env, wp), _review_status(doc)) == (("done", doc), "approved")   # no person approved it
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t2]   # and its AI starts
    assert len(reviews) == 1 and _run_status(env)[0] == "running"


# ── 2. review count 1: rejected -> resubmitted -> reviewed again -> approved ──

@pytest.mark.parametrize("count", [1, -1], ids=["count_1", "until_pass"])
@pytest.mark.parametrize("resubmit", ["mark_revised", "revision"])
def test_2_a_rejected_wp_is_resubmitted_reviewed_again_and_approved_with_no_ai_rework(
        env, decided, reviews, count, resubmit):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history

    wp, t2 = _person_wp(env, count)
    doc = _submit(env, wp, 81)
    run1 = _admitted(reviews[0], 1)
    first = _verdict(run1, "issues", ["step 2 names no owner"])
    assert _stage(_hop_ends(run1)) == AUTHOR_STOP                       # no rework hop: the loop ends
    assert _review_status(doc) == "rejected"                            # the review's rejection stands
    history = parse_rejection_history(db_documents.get_by_id(doc).get("rejection_history"))
    assert [h.get("review_id") for h in history] == [first["id"]]
    _loop_ended(run1)
    assert len(reviews) == 1                                             # no AI rework job
    assert [r["kind"] for r in _ledger(env)] == ["review"]               # no rework execution requested
    assert _node(env, wp) == ("review", doc)                             # a rejection is no node event
    assert _node_run(env, wp)["awaiting_approval"] is True              # a person may approve it meanwhile
    assert _node(env, t2)[0] == "pending"
    if resubmit == "mark_revised":
        _mark_revised(doc)
        target = doc
    else:
        target = _revise(env, wp, doc, 82)
    assert len(reviews) == 2                                             # a new loop for the new submission
    call2 = reviews[1]
    assert call2["doc_ref"] == target and call2["document_review_loop"]["review_count"] == 1
    assert call2["document_review_loop"]["rework_provider_id"] is None
    run2 = _admitted(call2, 2)
    baseline = run2["document_review_loop"]["review_baseline_id"]
    assert baseline == (first["id"] if resubmit == "mark_revised" else 0)   # its rounds start from 0
    _passes(run2)
    assert (_node(env, wp), _review_status(target)) == (("done", target), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t2]


@pytest.mark.parametrize("count", [1, -1], ids=["count_1", "until_pass"])
def test_2_a_rejected_wp_revised_through_the_real_inbox_edit_is_reviewed_once_and_approved(env, decided, reviews, count):
    """The author's resubmission of the same document through the real HTTP ``POST /inbox`` ``action=edit`` (its
    ``revision_no`` raised, the rejected -> revised submit): its Graph contact (``inbox_edited``) hands it to the card —
    one review of the new revision, a pass approves it and the next card opens. The contact told again, before and after
    the review's admission, starts nothing more."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    _author_edits(env)
    wp, t2 = _person_wp(env, count)
    doc = _submit(env, wp, 81)
    run1 = _admitted(reviews[0], 1)
    first = _verdict(run1, "issues", ["step 2 names no owner"])
    assert _stage(_hop_ends(run1)) == AUTHOR_STOP
    _loop_ended(run1)
    assert (_review_status(doc), _node(env, wp), len(reviews)) == ("rejected", ("review", doc), 1)
    edited, token = _edit(doc, "step 2: u_lee owns it")
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision_no"] == 1 == _revision(doc)            # the same document, its new revision
    assert _review_status(doc) != "rejected"                              # submitted again (rejected -> revised)
    assert len(reviews) == 2 and _node(env, wp) == ("review", doc)        # one review of the new revision
    call2 = reviews[1]
    assert call2["doc_ref"] == doc and call2["issued_to"] == "u_kim"
    assert (call2["document_review_loop"]["review_count"], call2["document_review_loop"]["rework_provider_id"]) == (1, None)
    v01_contact.after_inbox_edited(FA, doc, 1, token["token_id"])         # the contact told again
    assert len(reviews) == 2 and [r["kind"] for r in _ledger(env)] == ["review", "review"]
    run2 = _admitted(call2, 2)
    assert run2["document_review_loop"]["review_baseline_id"] == first["id"]   # its rounds start from 0
    v01_contact.after_inbox_edited(FA, doc, 1, token["token_id"])         # and once its review works
    assert len(reviews) == 2 and len(_ledger(env)) == 2
    _passes(run2)
    assert (_node(env, wp), _review_status(doc)) == (("done", doc), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t2]
    assert len(reviews) == 2


def test_2_every_review_count_reviews_each_submission_once(env, decided, reviews):
    """A count of 2 asks one review per submission too (the budget is the submission)."""
    wp, _t2 = _person_wp(env, 2)
    _submit(env, wp, 81)
    [call] = reviews
    assert (call["document_review_loop"]["review_count"], call["document_review_loop"]["rework_provider_id"]) == (1, None)


# ── 3. duplicates: one loop at most ──────────────────────────────────────────

def test_3_the_same_bind_told_twice_starts_one_loop(env, decided, reviews):
    from flowgate.card_settings import review_stage
    from modules.flow_gate.graph_workflow.api import v01_contact

    wp, _t2 = _person_wp(env)
    doc = _submit(env, wp, 81)
    v01_contact.after_inbox_registered(FA, doc, "WP", _token(env, wp))    # the same contact again
    assert len(reviews) == 1 and len(_ledger(env)) == 1
    review_stage.follow_person_result(FA, _run_id(env), wp, doc, _active_attempt(env, wp))   # the step itself again,
    assert len(reviews) == 1 and len(_ledger(env)) == 1                  # before admission: the journal has its one
    _admitted(reviews[0], 1)
    review_stage.follow_person_result(FA, _run_id(env), wp, doc, _active_attempt(env, wp))   # and once it works
    assert len(reviews) == 1 and len(_ledger(env)) == 1
    assert _node(env, wp) == ("review", doc)


@pytest.mark.parametrize("old_verdict", ["pass", "issues"])
def test_3_a_submission_while_the_loop_works_is_reviewed_once_when_that_loop_ends(env, decided, reviews, old_verdict):
    """One loop at a time: a new revision submitted while the review works starts no second loop. When that loop ends —
    its pass or its ``issues`` of the old revision — the node's current submission takes its own review, exactly once,
    and its pass approves it (the old loop approves nothing)."""
    wp, t2 = _person_wp(env)
    doc = _submit(env, wp, 81)
    run1 = _admitted(reviews[0], 1)
    new = _revise(env, wp, doc, 82)                                      # a new revision while the review works
    assert _node(env, wp) == ("review", new)
    assert len(reviews) == 1                                             # the attempt has its execution already
    assert [(r["kind"], r["status"]) for r in _ledger(env)] == [("review", "requested")]
    _verdict(run1, old_verdict, ["step 2 names no owner"] if old_verdict == "issues" else ())
    _hop_ends(run1)
    _loop_ended(run1)                                                    # the old loop ends
    assert _review_status(doc) != "approved" and _node(env, t2)[0] == "pending"
    assert len(reviews) == 2 and reviews[1]["doc_ref"] == new            # the new submission's own review
    assert reviews[1]["document_review_loop"]["rework_provider_id"] is None
    assert [(r["kind"], r["status"]) for r in _ledger(env)][-1] == ("review", "requested")
    assert _node(env, wp) == ("review", new) and _review_status(new) == "pending_review"
    _passes(_admitted(reviews[1], 2))
    assert (_node(env, wp), _review_status(new)) == (("done", new), "approved") and _node(env, t2)[0] == "open"
    assert len(reviews) == 2 and [j["node_key"] for j in env["jobs"]] == [t2]


def test_3_the_same_wp_edited_in_place_while_its_loop_works_is_reviewed_again_not_approved_by_the_old_pass(
        env, decided, reviews):
    """The author edits the WP under review through the real inbox (the same document, revision 1) while the review of
    revision 0 works: no second loop then; that review's pass answers revision 0 only, so it approves nothing and the
    current revision takes its own review, whose pass approves it."""
    _author_edits(env)
    wp, t2 = _person_wp(env)
    doc = _submit(env, wp, 81)
    run1 = _admitted(reviews[0], 1)
    edited, _token_rec = _edit(doc, "a later step", reason="user_comment")
    assert edited.status_code == 200, edited.text
    assert _revision(doc) == 1 and len(reviews) == 1                     # its review works: one loop at a time
    _verdict(run1, "pass", revision=0)                                   # the pass of the revision it reviewed
    _hop_ends(run1)
    _loop_ended(run1)
    assert _review_status(doc) != "approved" and _node(env, wp) == ("review", doc) and _node(env, t2)[0] == "pending"
    assert len(reviews) == 2 and reviews[1]["doc_ref"] == doc            # revision 1's own review
    _passes(_admitted(reviews[1], 2))
    assert (_node(env, wp), _review_status(doc)) == (("done", doc), "approved") and _node(env, t2)[0] == "open"
    assert len(reviews) == 2


def test_3_a_submission_made_while_the_loop_worked_is_not_taken_over_under_a_time_machine_operation(
        env, decided, reviews, monkeypatch):
    from flowgate.graph_time_machine import operation

    wp, t2 = _person_wp(env)
    doc = _submit(env, wp, 81)
    run1 = _admitted(reviews[0], 1)
    new = _revise(env, wp, doc, 82)
    monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm_in_progress"}])
    _passes(run1)                                                        # the old loop ends under the operation
    assert len(reviews) == 1 and [r["kind"] for r in _ledger(env)] == ["review"]
    assert _node(env, wp) == ("review", new) and _review_status(new) == "pending_review"
    assert _review_status(doc) != "approved" and _node(env, t2)[0] == "pending"


def test_3_a_start_on_a_rejected_wp_is_refused_until_it_is_submitted_again(env, decided, reviews):
    from fastapi import HTTPException

    from flowgate.card_settings import review_stage
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services.ai_invoke import review

    wp, _t2 = _person_wp(env)
    doc = _submit(env, wp, 81)
    run1 = _admitted(reviews[0], 1)
    _verdict(run1, "issues", ["missing step"])
    _hop_ends(run1)
    _loop_ended(run1)
    assert _review_status(doc) == "rejected"
    review_stage.follow_person_result(FA, _run_id(env), wp, doc, _active_attempt(env, wp))   # a late or repeated step
    v01_contact.after_inbox_registered(FA, doc, "WP", _token(env, wp))                     # a repeated bind
    assert len(reviews) == 1 and len(_ledger(env)) == 1
    with pytest.raises(HTTPException) as refused:                       # the engine's own start guard
        review.admit_author_revision_loop({**reviews[0]["document_review_loop"]}, "rework")
    assert (refused.value.status_code, refused.value.detail["code"]) == (409, review.AUTHOR_REVISION_REWORK_START_CODE)
    _mark_revised(doc)                                                   # submitted again: one new loop
    assert len(reviews) == 2


# ── 4. Stop ──────────────────────────────────────────────────────────────────

def test_4_a_stop_during_the_review_ends_it_approves_nothing_and_the_new_attempt_is_reviewed_after_resume(
        env, decided, reviews):
    wp, t2 = _person_wp(env)
    doc = _submit(env, wp, 81)
    old_attempt = _active_attempt(env, wp)
    run1 = _admitted(reviews[0], 1)
    _stop(env)                                                           # the review's run is running: a Stop takes it
    assert _run_status(env) == ("idle", "stopped")
    assert [(r["kind"], r["status"]) for r in _ledger(env)] == [("review", "cancelled")]   # the loop is ended
    assert _node(env, wp)[0] == "pending"
    _passes(run1)                                                        # its late pass
    assert _review_status(doc) != "approved" and _node(env, wp)[0] == "pending" and _node(env, t2)[0] == "pending"
    assert _run_status(env) == ("idle", "stopped") and env["jobs"] == []
    assert len(reviews) == 1                                             # and nothing is taken over
    _resume(env)                                                         # the next command: a new attempt
    new_attempt = _active_attempt(env, wp)
    assert new_attempt not in (None, old_attempt) and _node(env, wp) == ("open", None)
    doc2 = _submit(env, wp, 82)                                          # with the new attempt's token
    assert len(reviews) == 2 and _ledger(env)[-1] == {"kind": "review", "status": "requested", "attempt_id": new_attempt}
    _passes(_admitted(reviews[1], 2))
    assert (_node(env, wp), _review_status(doc2)) == (("done", doc2), "approved")
    assert _node(env, t2)[0] == "open"


@pytest.mark.parametrize("held", ["stopped", "time_machine"])
def test_4_a_submission_on_a_stopped_run_or_under_a_time_machine_starts_no_review(env, decided, reviews, monkeypatch,
                                                                                    held):
    """As the 0083 WP-2 / WP-3 suites set a stopped run (a Stop takes a running run; the person's card keeps its
    token here), and a Time Machine operation is unfinished (``operation._unfinished``)."""
    from flowgate.graph_time_machine import operation

    wp, _t2 = _person_wp(env)
    if held == "stopped":
        _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'stopped'")
    else:
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm_in_progress"}])
    seq = _seq(env)
    doc = _submit(env, wp, 81)
    assert reviews == [] and _ledger(env) == []                          # no review started
    assert _node(env, wp) == ("review", doc) and _review_status(doc) == "pending_review"
    assert "work_resumed" not in _run_causes(env, seq) and _run_status(env)[0] == "idle"


def _invalidate(env, monkeypatch, how):
    """What may commit on the idle run of a person's card between the bind's job and its step, each as the runtime
    commits it: a Time Machine operation started (its journal: ``operation._unfinished``), the node's attempt invalidated
    under the Stop gate's exclusive hold (a Time Machine rewind's ``time_machine`` invalidation), the run stopped
    (``idle_reason`` stopped, as the WP-2 / WP-3 suites set it)."""
    from flowgate.graph_run import fencing
    from flowgate.graph_time_machine import operation

    if how == "time_machine_started":
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm_in_progress"}])
        return
    with fencing.exclusive(FA):
        if how == "attempt_invalidated":
            _sql(env["db"], "UPDATE graph_node_attempts SET status = 'invalidated', active_node_key = NULL, "
                            "invalidated_reason = 'time_machine' WHERE status = 'active'")
        else:
            _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'stopped'")


@pytest.mark.parametrize("how", ["time_machine_started", "attempt_invalidated", "run_stopped"])
@pytest.mark.parametrize("count", [0, 1], ids=["review_none_approval", "review_start"])
def test_4_what_commits_between_the_job_and_its_step_refuses_the_step_and_records_it(
        env, decided, reviews, monkeypatch, count, how):
    """The bind's post-commit job has read the idle run and its attempt (its own first checks passed); a Time Machine
    operation, an attempt invalidation or a Stop commits before the step itself. The step is judged again under the Stop
    gate's shared hold (``legacy.person_hold``): the '안 함' approval / the review's start is refused and recorded,
    nothing approved or started."""
    from flowgate.card_settings import legacy_adapter

    wp, t2 = _person_wp(env, count)
    real, squeezed = legacy_adapter.person_hold, []

    def person_hold(*args, **kwargs):
        if not squeezed:
            squeezed.append(args[-1])
            _invalidate(env, monkeypatch, how)                           # right before the step's judgement
        return real(*args, **kwargs)

    monkeypatch.setattr(legacy_adapter, "person_hold", person_hold)
    before = len(_refusals(env))
    doc = _submit(env, wp, 81)
    operation = "result_approval" if count == 0 else "review_start"
    assert squeezed == [operation]
    assert reviews == [] and _ledger(env) == []                          # no review started
    assert (_node(env, wp), _review_status(doc)) == (("review", doc), "pending_review")   # nothing approved
    assert _node(env, t2)[0] == "pending" and env["jobs"] == []
    refusals = _refusals(env)[before:]
    assert [op for op, _why in refusals] == [operation]                  # refused and recorded
    assert refusals[0][1] == {"time_machine_started": "time_machine_in_progress", "run_stopped": "run_stopped",
                              "attempt_invalidated": "attempt_invalidated"}[how]


# ── 5. attempt invalidation ──────────────────────────────────────────────────

def _review_token(call, index):
    """The review hop's token as admission mints it for the loop's run (its issuer maps it to the execution)."""
    issued = call["issue_builder"](ai_run_id=f"air_person_review_{index}")
    return issued["token_id"]


def _verdict_refused(env, token_id):
    """The old review's verdict reaches the inbox with its token: the fence's judgement there (``report``)."""
    from modules.flow_gate.services import graph_source_hooks

    token = _sql(env["db"], "SELECT * FROM tokens WHERE token_id = ?", (token_id,))[0]
    with pytest.raises(graph_source_hooks.GraphSourceError) as refused:
        graph_source_hooks.require_writable(token, "inbox_review", report=True)
    return refused.value


def test_5_the_old_attempt_s_review_is_refused_and_recorded_and_approves_nothing(env, decided, reviews):
    from flowgate.card_settings import review_stage

    wp, t2 = _person_wp(env)
    doc1 = _submit(env, wp, 81)
    old_attempt = _active_attempt(env, wp)
    run1 = _admitted(reviews[0], 1)
    old_token = _review_token(reviews[0], 1)
    _stop(env)
    _resume(env)
    doc2 = _submit(env, wp, 82)                                          # the new attempt's submission and review
    new_attempt = _active_attempt(env, wp)
    assert len(reviews) == 2 and new_attempt != old_attempt
    before = len(_refusals(env))
    _verdict_refused(env, old_token)                                     # the old review's verdict: refused,
    assert len(_refusals(env)) == before + 1                             # recorded
    _passes(run1)                                                        # its late pass approves nothing
    assert _review_status(doc1) != "approved" and _node(env, wp) == ("review", doc2) and _node(env, t2)[0] == "pending"
    review_stage.follow_person_result(FA, _run_id(env), wp, doc2, old_attempt)    # a step queued for the old attempt
    assert len(reviews) == 2 and _refusals(env)[-1][0] == "review_start"           # refused and recorded
    _passes(_admitted(reviews[1], 2))                                    # the new attempt's review goes on
    assert (_node(env, wp), _review_status(doc2)) == (("done", doc2), "approved") and _node(env, t2)[0] == "open"


def test_5_after_a_t2_card_recovery_the_old_loop_s_result_is_refused(env, decided, reviews):
    wp, t2 = _person_wp(env)
    doc1 = _submit(env, wp, 81)
    run1 = _admitted(reviews[0], 1)
    old_token = _review_token(reviews[0], 1)
    _stop(env)
    _resume(env)                                                         # open again, the person's card, idle
    assert _node(env, wp) == ("open", None) and _run_status(env)[0] == "idle"
    recovered = env["client"].post(f"/api/v1/runs/{_run_id(env)}/nodes/{wp}/recover", json={}, headers=_h())
    assert recovered.status_code == 200, recovered.text                   # 0083 WP-2: back to pending
    assert _node(env, wp) == ("pending", None)
    _resume(env)
    _verdict_refused(env, old_token)                                     # the old loop's verdict: refused
    _passes(run1)                                                        # its late pass: nothing approved
    assert _review_status(doc1) != "approved" and _node(env, wp) == ("open", None) and _node(env, t2)[0] == "pending"
    doc2 = _submit(env, wp, 82)
    assert len(reviews) == 2
    _passes(_admitted(reviews[1], 2))
    assert (_node(env, wp), _review_status(doc2)) == (("done", doc2), "approved") and _node(env, t2)[0] == "open"


# ── 6. review 'none': the issuer's authority ─────────────────────────────────

def test_6_review_none_is_approved_with_the_issuer_s_permission_and_the_next_card_opens(env, decided, reviews):
    wp, t2 = _person_wp(env, 0)
    seq = _seq(env)
    doc = _submit(env, wp, 81)
    assert reviews == []                                                 # no review
    assert (_node(env, wp), _review_status(doc)) == (("done", doc), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t2]
    assert "work_resumed" in _run_causes(env, seq)                       # the idle run went on (0083 WP-3)


def test_6_review_none_waits_for_a_person_when_the_issuer_lacks_document_approve(env, decided, reviews, monkeypatch):
    """The issuer (u_kim, the run's starter) lacks ``document.approve``; the author (u_lee) holds it and is never used."""
    from modules.flow_gate.workflow.pipeline_service import transition_document_review as real_transition
    from modules.flow_gate.workflow.routers import workflow

    wp, t2 = _person_wp(env, 0)
    real = workflow._get_user_permissions
    monkeypatch.setattr(workflow, "_get_user_permissions", lambda u: (
        {"document.read"} if (u or {}).get("user_id") == "u_kim" else
        {"document.read", "document.approve", "document.update"} if (u or {}).get("user_id") == "u_lee" else real(u)))
    transitions = []
    from modules.flow_gate.workflow import pipeline_service
    monkeypatch.setattr(pipeline_service, "transition_document_review",
                        lambda **kw: transitions.append(kw) or real_transition(**kw))
    doc = _submit(env, wp, 81)
    assert transitions == []                                             # nothing tried
    assert (_node(env, wp), _review_status(doc)) == (("review", doc), "pending_review")
    assert _node_run(env, wp)["awaiting_approval"] is True and _node(env, t2)[0] == "pending" and env["jobs"] == []
    assert _run_status(env) == ("idle", "waiting_approval")
    _approve(env, wp, user="u_lee")                                      # a person's approval of the card
    assert _node(env, wp) == ("done", doc) and _node(env, t2)[0] == "open"


def test_6_a_stop_waits_for_the_review_none_approval_in_flight_on_an_idle_run(env, decided, reviews, monkeypatch):
    """The '안 함' approval on the idle run holds the Stop gate shared until its transition, TR hook and Graph contact are
    over: a Stop's exclusive hold asked meanwhile waits for it, so the Stop commits after the approval, never across it."""
    from flowgate.card_settings import legacy_adapter
    from flowgate.graph_run import fencing
    from modules.flow_gate.graph_workflow.api import v01_contact

    wp, t2 = _person_wp(env, 0)
    doc, token = _wp_result(81), _token(env, wp)
    inside, release, approved = threading.Event(), threading.Event(), threading.Event()
    real = legacy_adapter.approve

    def approve(*args, **kwargs):
        inside.set()
        release.wait(10)
        try:
            return real(*args, **kwargs)
        finally:
            approved.set()

    monkeypatch.setattr(legacy_adapter, "approve", approve)
    errors, seen = [], []

    def submit():
        try:
            v01_contact.after_inbox_registered(FA, doc, "WP", token)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def stop_gate():
        try:
            with fencing.exclusive(FA):                                  # what a Stop's ``stopping`` takes
                seen.append(approved.is_set())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    submitter = threading.Thread(target=submit)
    submitter.start()
    assert inside.wait(10)                                               # the approval is in flight
    stopper = threading.Thread(target=stop_gate)
    stopper.start()
    stopper.join(0.5)
    assert stopper.is_alive() and seen == []                             # the Stop waits for it
    release.set()
    submitter.join(10)
    stopper.join(10)
    assert errors == [] and seen == [True]                               # and takes the gate after it
    assert (_node(env, wp), _review_status(doc)) == (("done", doc), "approved") and _node(env, t2)[0] == "open"


def test_6_a_passed_review_waits_for_a_person_when_the_issuer_lacks_document_approve(env, decided, reviews, monkeypatch):
    from modules.flow_gate.workflow.routers import workflow

    wp, t2 = _person_wp(env)
    doc = _submit(env, wp, 81)
    run = _admitted(reviews[0], 1)
    _verdict(run, "pass")
    _hop_ends(run)
    real = workflow._get_user_permissions
    monkeypatch.setattr(workflow, "_get_user_permissions",
                        lambda u: {"document.read"} if (u or {}).get("user_id") == "u_kim" else real(u))
    _loop_ended(run)
    assert (_node(env, wp), _review_status(doc)) == (("review", doc), "pending_review")
    assert _node_run(env, wp)["awaiting_approval"] is True and _node(env, t2)[0] == "pending"


def test_6_a_person_s_run_card_still_waits_for_a_person_s_approval(env, decided, reviews):
    from modules.flow_gate.graph_workflow.api import v01_contact
    from test_fg_card_settings_review import _result

    key, t2 = _person_wp(env, 0, card=("T", LEE))
    doc = _result(81, "T")
    v01_contact.after_inbox_registered(FA, doc, "T", _token(env, key))
    assert reviews == [] and (_node(env, key), _review_status(doc)) == (("review", doc), "pending_review")
    assert _node_run(env, key)["awaiting_approval"] is True and _node(env, t2)[0] == "pending"


# ── 7. the engine: loops with a rework AI unchanged; no rework AI: stop ──────

BASE = {
    "review_count": 2, "reviewer_provider_id": "reviewer", "review_criteria": "document_type_default",
    "rework_provider_id": "reworker", "rework_timeout_sec": 1800, "rework_message": "", "failure_restart_max_attempts": 0,
    "total_timeout_sec": 3600, "review_baseline_id": 10, "baseline_revision_no": 3, "starts_with_rework": False,
    "round_no": 1, "current_stage": "review", "attempts_used": 0, "review_run_id": "loop-run",
    "doc": {"revision_no": 3, "doc_review_status": "pending_review"},
}
SAME = '[{"locus":"x","note":"same"}]'


def _row(rid, verdict, revision, findings="[]"):
    return {"id": rid, "verdict": verdict, "revision_no": revision, "review_run_id": "loop-run", "attempt_no": 1,
            "findings": findings}


def _gate(**updates):
    from modules.flow_gate.services.ai_invoke import review

    return review.resolve_document_review_loop_gate({**BASE, **updates})


@pytest.mark.parametrize("count", [1, 2, -1])
def test_7_a_loop_with_its_rework_ai_keeps_its_stages_for_every_count(count):
    one = [_row(11, "issues", 3)]
    two = [_row(11, "issues", 3), _row(12, "issues", 4)]
    assert (_gate(review_count=count, reviews=one)["current_stage"], _gate(review_count=count, reviews=one)["round_no"]) \
        == ("rework", 2)                                                 # issues -> its rework, whatever the count
    landed = _gate(review_count=count, reviews=one, last_hop_kind="rework", last_hop_outcome="succeeded",
                   doc={"revision_no": 4})
    second = _gate(review_count=count, reviews=two, last_hop_kind="rework", last_hop_outcome="succeeded",
                   doc={"revision_no": 5})
    if count == 1:
        assert (landed["current_stage"], landed["stop_reason"]) == ("stopped", "review_count_exhausted")
        assert landed["stop_detail"] == "review count 1 exhausted"
    else:
        assert landed["current_stage"] == "review"
    if count == 2:
        assert (second["current_stage"], second["stop_reason"]) == ("stopped", "review_count_exhausted")
    elif count == -1:
        assert second["current_stage"] == "review"                        # until it passes: no ceiling


@pytest.mark.parametrize("author_revision", [False, True], ids=["rework_ai", "no_rework_ai"])
def test_7_hold_stalled_and_pass_stay_as_they_are(author_revision):
    flag = {"author_revision": author_revision}
    held = _gate(reviews=[_row(11, "hold", 3)], **flag)
    assert (held["current_stage"], held["stop_reason"]) == ("stopped", "review_verdict_hold")
    passed = _gate(reviews=[_row(11, "pass", 3)], **flag)
    assert (passed["current_stage"], passed["stop_reason"]) == ("stopped", "review_passed")
    stalled = _gate(reviews=[_row(11, "issues", 3, SAME), _row(12, "issues", 4, SAME)], doc={"revision_no": 4}, **flag)
    assert (stalled["current_stage"], stalled["stop_reason"]) == ("stopped", "review_stalled")


def test_7_a_rejected_document_s_ai_loop_starts_with_its_rework_and_one_with_no_rework_ai_is_refused():
    from fastapi import HTTPException

    from modules.flow_gate.services.ai_invoke import review

    first = _gate(starts_with_rework=True, reviews=[])
    assert (first["current_stage"], first["round_no"]) == ("rework", 1)  # as before
    with_ai = {k: v for k, v in BASE.items() if k != "doc"}
    assert review.admit_author_revision_loop(with_ai, "rework") is False and with_ai["rework_provider_id"] == "reworker"
    assert review.resolve_loop_provider(with_ai, "rework") == "reworker"
    author = {**with_ai, "rework_provider_id": None}
    with pytest.raises(HTTPException) as refused:
        review.admit_author_revision_loop(dict(author), "rework")
    assert refused.value.status_code == 409
    assert review.admit_author_revision_loop(author, "review") is True   # admission takes it in review
    assert author["rework_provider_id"] == "reviewer" and review.resolve_loop_provider(author, "review") == "reviewer"


def test_7_issues_without_a_rework_ai_end_the_loop_and_with_one_go_to_its_rework():
    issues = [_row(11, "issues", 3, SAME)]
    with_ai = _gate(review_count=1, reviews=issues)
    assert (with_ai["current_stage"], with_ai["round_no"]) == ("rework", 2)
    author = _gate(review_count=1, reviews=issues, author_revision=True)
    assert (author["current_stage"], author["stop_reason"], author["stop_detail"]) == AUTHOR_STOP


def test_7_the_real_worker_runs_one_review_hop_rejects_and_never_reworks_a_loop_with_no_rework_ai(
        monkeypatch, tmp_path):
    """The real worker loop, gate and checkpoint transaction (``test_ai_invoke_document_review_loop_0417``'s real
    worker harness): a loop admitted with no rework AI gets ``issues``: the rejection is made once, the loop stops with
    the author's revision, and no rework hop runs."""
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.services import ai_invoke_service as service
    from modules.flow_gate.services.ai_invoke import review
    from modules.flow_gate.services.ai_invoke import worker as worker_module

    conn = sqlite3.connect(tmp_path / "worker-author.db")
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
    migrations = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite"
    for name in ("091_ai_invoke_document_review_loops.sql", "092_ai_invoke_document_review_loop_live_run.sql",
                 "102_ai_invoke_review_loop_card_dismissed.sql", "105_ai_invoke_document_review_loop_hold_stop_reason.sql",
                 "106_ai_invoke_document_review_loop_restart_orphaned.sql",
                 "107_ai_invoke_document_review_loop_review_stalled.sql"):
        conn.executescript((migrations / name).read_text(encoding="utf-8"))
    conn.execute("INSERT INTO groups VALUES ('flowgate.v02.0083')")
    conn.execute("INSERT INTO documents(doc_id,project_id,group_id,revision_no,doc_review_status,updated_at) "
                 "VALUES ('person-wp', 'flowgate', 'flowgate.v02.0083', 3, 'pending_review', 't')")
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",)])
    conn.commit()

    class Store:
        def _execute(self, sql, values=()):
            cursor = conn.execute(sql, values)
            conn.commit()
            return cursor

        def _execute_affected(self, sql, values=()):
            return conn.execute(sql, values).rowcount

        def _fetch_one(self, sql, values=()):
            row = conn.execute(sql, values).fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, values=()):
            return [dict(row) for row in conn.execute(sql, values).fetchall()]

        @contextmanager
        def transaction(self):
            try:
                yield self
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    store = Store()
    for module in (db_loops, service, service.db_docs, db_reviews):
        monkeypatch.setattr(module, "get_store", lambda: store)
    asked = {k: v for k, v in BASE.items() if k not in ("doc", "review_run_id", "round_no", "attempts_used")}
    asked.update(review_count=1, rework_provider_id=None, review_baseline_id=0)
    author = review.admit_author_revision_loop(asked, "review")          # what admission stores and carries
    started = datetime.now(timezone.utc)
    persisted = db_loops.insert({**asked, "run_id": "aiv_author", "group_id": "flowgate.v02.0083",
                                 "doc_ref": "person-wp", "started_at": started.isoformat(),
                                 "deadline_at": (started + timedelta(hours=1)).isoformat()})
    run = {"run_id": "aiv_author", "project_id": "flowgate", "issued_to": "u_kim", "api_base_url": API,
           "group_id": "flowgate.v02.0083", "doc_ref": "person-wp", "mode": "single", "document_review_loop": persisted,
           "review_loop_author_revision": author, "started_at": started.isoformat(), "docs_target": 0,
           "chain_id": "loop-author", "chain_docs_target": 0, "chain_docs_reached": 0, "attempts_used": 0,
           "fallback_history": []}
    rejected = []

    def auto_reject(slot, latest, bundle):
        rejected.append(latest["id"])
        conn.execute("UPDATE documents SET doc_review_status = 'rejected' WHERE doc_id = 'person-wp'")
        return {"ok": True}

    monkeypatch.setattr(service, "_auto_reject", auto_reject)
    hops = []

    def execute(_run, _chain, _prompt):
        hops.append(db_loops.get("aiv_author")["current_stage"])
        conn.execute("INSERT INTO document_reviews(doc_id,revision_no,reviewer_id,verdict,findings,comment,reviewed_at,"
                     "created_at,updated_at) VALUES ('person-wp', 3, 'reviewer', 'issues', ?, NULL, 't', 't', 't')",
                     ('[{"locus":"x","note":"step 2"}]',))
        conn.commit()
        return True

    monkeypatch.setattr(service, "_execute_provider_chain", execute)
    monkeypatch.setattr(service, "_classify_end_reason", lambda item, ok: item.update(end_reason="exited"))
    monkeypatch.setattr(service, "_judge_hop", lambda item: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_prepare_retry_token", lambda item: {"mention": "token"})
    monkeypatch.setattr(service, "_reset_attempt_state", lambda item: None)
    monkeypatch.setattr(worker_module, "_start_post_process_recovery", lambda item: (None, None))
    monkeypatch.setattr(worker_module, "_stop_post_process_recovery", lambda *a, **k: None)
    monkeypatch.setattr(service, "_broadcast", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_finalize_run", lambda item: item.update(status="finished"))
    monkeypatch.setattr(service.ai_settings_service, "resolve_effective",
                        lambda project: {"providers": [{"id": "reviewer", "name": "Reviewer"}]})

    service._worker(run, [{"id": "reviewer", "name": "Reviewer"}], "review")

    assert hops == ["review"]                                            # one review hop, no rework hop
    assert len(rejected) == 1                                            # the rejection, once
    assert _stage(db_loops.get("aiv_author")) == AUTHOR_STOP
    doc = conn.execute("SELECT revision_no, doc_review_status FROM documents WHERE doc_id = 'person-wp'").fetchone()
    assert (doc["revision_no"], doc["doc_review_status"]) == (3, "rejected")     # nobody reworked it
    conn.close()


# ── 8. a lost contact: the reconciliation hands the submission on ────────────

@pytest.mark.parametrize("recovery", ["read", "resume"])
@pytest.mark.parametrize("count", [1, 0], ids=["review", "review_none"])
def test_8_a_submission_bound_by_the_reconciliation_after_a_lost_contact_takes_its_card_s_step_once(
        env, decided, reviews, recovery, count):
    """L0009 E-27: the person's WP was registered and its token consumed, but the Graph contact was lost. The run read's
    reconciliation (``reconcile_if_needed``) or a Resume's (``reconcile_links``) binds it and hands it to the card as the
    contact would have, into the same post-commit jobs: one review, or with 'none' the automatic approval. Recovering
    again starts nothing more."""
    from modules.flow_gate.services import token_service

    wp, t2 = _person_wp(env, count)
    doc = _wp_result(81)
    token_service.consume(_token(env, wp), "flowgate", doc)              # stored and consumed: no contact
    assert _node(env, wp) == ("open", None) and reviews == []
    if recovery == "read":
        _current(env)
    else:
        _resume(env)
    if count:
        assert len(reviews) == 1 and reviews[0]["doc_ref"] == doc and _node(env, wp) == ("review", doc)
        assert reviews[0]["document_review_loop"]["rework_provider_id"] is None
        assert _run_status(env)[0] == "running"
        _current(env)                                                    # recovered again
        assert len(reviews) == 1 and len(_ledger(env)) == 1
        _passes(_admitted(reviews[0], 1))
    else:
        assert reviews == []
    assert (_node(env, wp), _review_status(doc)) == (("done", doc), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t2]
    _current(env)                                                        # once more: nothing more
    assert len(reviews) == count and [j["node_key"] for j in env["jobs"]] == [t2]


# ── wiring ───────────────────────────────────────────────────────────────────

def test_the_card_registers_the_person_result_port_and_without_it_the_graph_hands_nothing(env, decided, reviews):
    from flowgate.card_settings import review_stage
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    assert ports.registered(ports.PERSON_RESULT) and ports.PERSON_RESULT in ports.PORT_NAMES
    ports.unregister(ports.PERSON_RESULT)
    try:
        wp, _t2 = _person_wp(env)
        doc = _submit(env, wp, 81)
        assert reviews == [] and _node(env, wp) == ("review", doc)       # as before WP-4: a person approves
        assert _node_run(env, wp)["awaiting_approval"] is True
    finally:
        ports.register(ports.PERSON_RESULT, review_stage.person_result)
