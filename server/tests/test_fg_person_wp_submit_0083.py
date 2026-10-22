"""flowgate.v02.0083 T#3 (0015-WP T#3, 0014-CH turn 8 D-2 / D-3) — a work plan (WP) card a person writes, through the
routes its work plan window calls: the plan is written and saved in its editor (``PUT /documents/{doc}/work-plan``), then
[제출] is the card line write of its plan line (``POST /flows/{flow}/nodes/{key}/slots/plan/document``, no body): the
card's own draft on the line — no new document — is registered with the node's dispatch token (the token claimed, the
document submitted) and the Graph told, so the result binder binds it and the T#4 step follows (the card's review, or
with review 'none' its automatic approval) and the next card opens. After a rejection the author saves the plan again
and [제출] is the v0.1 ``review_transitions/mark_revised`` route, which hands it to a new review.

Nothing here calls ``v01_contact`` itself: every step is a real HTTP route (the T#4 suite told the binder directly and
missed that no route reached it — 0014-CH turn 8 §1).

Covered (0020-T 검증 2·3·4·5 and 8, on the server):
1. saved -> submitted -> reviewed once (or review 'none') -> approved -> the next card opens; the line keeps its one plan;
2. another card's AI working in the same Flow neither refuses the save nor the submission (no group lease is held);
   an AI run holding the Flow's group lease still refuses the save (423, the mutation policy is not changed);
3. while the card's review loop works, the submission is refused (409 ``node_ai_working``);
4. rejected -> saved again -> mark_revised -> reviewed again -> approved -> the next card opens;
5. a plan of no work set is refused (422 ``empty_body``) and changes nothing; a second submission registers nothing
   more; a plan line takes no text (422 ``not_for_this_slot``).

On the T#4 suite's real SQLite DB, routes and fixtures (the card settings suites' env, the recorder of the AI invoke
admission ``reviews``, ``_admitted`` / ``_passes`` / ``_verdict`` / ``_hop_ends`` / ``_loop_ended``).
"""
from __future__ import annotations

import json

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _h, _sql, _start, db_path, decided, edge, env, node, put_draft,
)
from test_fg_card_settings_review import _admin_and_reviewer, _node, _review_status
from test_fg_card_approval_0056 import LEE, _lease, _node_run, _run_id, card_approval_port  # noqa: F401
from test_fg_wp_ai_run_0083 import _opus, _plan_line, _run_status, _start_ai_run, _tokens
from test_fg_person_wp_review_0083 import (  # noqa: F401 — reviews: the recorder fixture
    AUTHOR_STOP, REVIEWER, _admitted, _hop_ends, _loop_ended, _passes, _person_wp, _stage, _verdict, _wp_body, reviews,
)

AUTHOR = {"Authorization": "Bearer user:u_lee", "x-canvas-client-id": "tab_wp"}


@pytest.fixture(autouse=True)
def window_routes(env, monkeypatch):
    """The routes the work plan window calls, mounted as ``main`` mounts them: the plan read and save, the card line
    write and the review transition. The author (u_lee) is the signed-in person; the line write's RBAC answers yes."""
    from fastapi.testclient import TestClient

    from flowgate.card_documents import legacy_adapter as documents
    from flowgate.card_documents import routes as document_routes
    from modules.flow_gate.auth import middleware
    from modules.flow_gate.documents.routers import work_plan
    from modules.flow_gate.services.mutation_policy import MutationPolicyError, mutation_error_response
    from modules.flow_gate.workflow.routers import workflow

    app = env["client"].app
    app.add_exception_handler(MutationPolicyError, lambda _request, exc: mutation_error_response(exc))
    app.include_router(document_routes.router)
    app.include_router(work_plan.router, prefix="/api/v1")
    app.include_router(workflow.router)
    app.dependency_overrides[middleware.get_current_user] = lambda: {"user_id": "u_lee", "is_admin": False}
    monkeypatch.setattr(documents, "has_permission", lambda u, p, perm: True)
    _opus(env)
    env["api"] = TestClient(app, raise_server_exceptions=False)
    yield
    app.dependency_overrides.pop(middleware.get_current_user, None)


# ── helpers ──────────────────────────────────────────────────────────────────

def _read(env, doc):
    res = env["api"].get(f"/api/v1/documents/{doc}/work-plan")
    assert res.status_code == 200, res.text
    return res.json()


def _save(env, doc, note="step 1: u_lee owns it", body=None):
    """The window's [저장]: the whole plan with its base revision (PUT …/work-plan)."""
    base = _read(env, doc)["revision_no"]
    plan = json.loads(_wp_body(note)) if body is None else body
    for step in plan.get("steps") or []:
        step.pop("card_id", None)                                        # a new card: the server names it
    return env["api"].put(f"/api/v1/documents/{doc}/work-plan", json={"base_revision_no": base, "body": plan})


def _submit(env, key, body=None):
    """The window's [제출] on an open card: the plan line's card line write, no text (cardLineWrite(…, 'plan', null))."""
    return env["api"].post(f"/api/v1/flows/{FA}/nodes/{key}/slots/plan/document",
                           json={"content": None} if body is None else body, headers=AUTHOR)


def _mark_revised(env, doc):
    """The window's [제출] on a rejected plan: the v0.1 review transition route."""
    return env["api"].post(f"/api/v1/documents/{doc}/review_transitions/mark_revised", json={})


def _doc(doc):
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc)


def _wp_docs(env):
    return {r["doc_id"] for r in _sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ? AND type_code = 'WP'",
                                      (FA,))}


def _consumed(env, key):
    return [t["token_id"] for t in _tokens(env, key) if t["consumed_at"]]


def _code(res):
    body = res.json()
    return (body.get("error") or {}).get("code") if isinstance(body.get("error"), dict) else body.get("code")


def _reason(res):
    errors = ((res.json().get("error") or {}).get("params") or {}).get("errors") or [{}]
    return errors[0].get("reason")


# ── 1. saved -> submitted -> reviewed / review 'none' -> approved -> next ────

@pytest.mark.parametrize("count", [1, 0], ids=["review_once", "review_none"])
def test_1_a_saved_plan_submitted_over_http_is_the_result_and_the_next_card_opens(env, decided, reviews, count):
    wp, t2 = _person_wp(env, count)
    plan = _plan_line(env, wp)                                          # the card's own draft (작업 0세트)
    before = _wp_docs(env)
    saved = _save(env, plan)
    assert saved.status_code == 200, saved.text
    assert _doc(plan)["status"] == "draft" and _review_status(plan) is None      # a save submits nothing
    assert _node(env, wp) == ("open", None) and reviews == []
    res = _submit(env, wp)
    assert res.status_code == 201, res.text
    answer = res.json()
    assert (answer["doc_id"], answer["doc_type"], answer["bound_as_result"]) == (plan, "WP", True)
    assert _wp_docs(env) == before                                      # no new document
    assert _plan_line(env, wp) == plan and not _doc(plan).get("superseded_by")
    assert len(_consumed(env, wp)) == 1                                 # its dispatch token, once
    if count:
        [call] = reviews                                                # the card's review of the submission
        assert (call["doc_ref"], call["action_scope"]) == (plan, "review")
        assert _node(env, wp) == ("review", plan) and _review_status(plan) == "pending_review"
        assert _node(env, t2)[0] == "pending"
        _passes(_admitted(call, 1))
    else:
        assert reviews == []                                            # '안 함': approved at once
    assert (_node(env, wp), _review_status(plan)) == (("done", plan), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t2]
    assert _run_status(env)[0] == "running"
    canvas = env["client"].get(f"/api/v1/flows/{FA}/canvas", headers=_h()).json()
    assert not [n for n in canvas["nodes"] if str(n.get("node_key", "")).startswith("doc:")]   # no free document node
    shown = next(n for n in canvas["nodes"] if n["node_key"] == wp)
    assert [line["doc_id"] for line in shown["card_documents"] if line["slot"] == "plan"] == [plan]


# ── 2. another card's AI, and the group lease ────────────────────────────────

def test_2_another_card_s_ai_working_neither_locks_the_save_nor_the_submission(env, decided, reviews, monkeypatch):
    """R -> a person's WP and R -> an AI T side by side: the T's AI starts and works while the person saves and submits.
    The branch is confirmed with the fork rule startup registers for the validator (``graph_policies.fork``); the run
    keeps the suite's decided policy."""
    from flowgate.graph_policies import fork
    from modules.flow_gate.graph_workflow.domain import policies

    monkeypatch.setattr(policies, "_registered", dict(policies._registered))
    for key, _value, _version, implementation in fork.SLOTS:
        policies.register(key, implementation)
    _admin_and_reviewer(env)
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_wp", "WP", assignee=LEE), node("tmp_t", "T", assignee=AI)],
                      [edge("tmp_e1", "tmp_r", "tmp_wp"), edge("tmp_e2", "tmp_r", "tmp_t")])
    wp, t = first["key_map"]["tmp_wp"], first["key_map"]["tmp_t"]
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    _start(env)
    [job] = env["jobs"]
    assert job["node_key"] == t
    _start_ai_run(monkeypatch, job, run_id="air_other_card")             # the T's AI works now
    assert _node(env, t)[0] == "open" and _node(env, wp) == ("open", None)
    plan = _plan_line(env, wp)
    saved = _save(env, plan)
    assert saved.status_code == 200, saved.text
    res = _submit(env, wp)
    assert res.status_code == 201, res.text
    assert _node(env, wp) == ("done", plan) and _review_status(plan) == "approved"   # review 'none'


def test_2_an_ai_run_holding_the_group_lease_still_refuses_the_save_423(env, decided, reviews, monkeypatch):
    """The mutation policy is not changed (0020-T): a non-Graph AI run holding the Flow's group lease refuses the save."""
    wp, _t2 = _person_wp(env, 0)
    plan = _plan_line(env, wp)
    _lease(monkeypatch, "air_single_other_doc")
    refused = _save(env, plan)
    assert refused.status_code == 423, refused.text
    _lease(monkeypatch, None)
    assert _save(env, plan).status_code == 200


# ── 3. the card's review loop works: refused ─────────────────────────────────

def test_3_a_submission_while_the_card_s_review_loop_works_is_refused_node_ai_working(env, decided, reviews):
    wp, t2 = _person_wp(env, 1)
    plan = _plan_line(env, wp)
    assert _save(env, plan).status_code == 200
    assert _submit(env, wp).status_code == 201
    run = _admitted(reviews[0], 1)                                      # the review loop works on the plan
    again = _submit(env, wp)
    assert again.status_code == 409, again.text
    assert _code(again) == "node_ai_working"
    assert len(reviews) == 1 and len(_consumed(env, wp)) == 1 and _node(env, wp) == ("review", plan)
    _passes(run)
    assert _node(env, wp) == ("done", plan) and _node(env, t2)[0] == "open"


# ── 4. rejected -> saved again -> mark_revised -> reviewed -> approved ───────

def test_4_a_rejected_plan_saved_again_and_resubmitted_by_mark_revised_is_reviewed_and_approved(env, decided, reviews):
    wp, t2 = _person_wp(env, 1)
    plan = _plan_line(env, wp)
    assert _save(env, plan).status_code == 200
    assert _submit(env, wp).status_code == 201
    run1 = _admitted(reviews[0], 1)
    _verdict(run1, "issues", ["step 1 names no owner"])
    assert _stage(_hop_ends(run1)) == AUTHOR_STOP
    _loop_ended(run1)
    assert _review_status(plan) == "rejected" and _node(env, wp) == ("review", plan) and len(reviews) == 1
    assert _read(env, plan)["editable"] is True                          # rejected: still the author's to edit
    resaved = _save(env, plan, "step 1: u_lee owns it now")
    assert resaved.status_code == 200, resaved.text
    assert _review_status(plan) == "rejected"                            # a save changes no review state
    revised = _mark_revised(env, plan)
    assert revised.status_code == 200, revised.text
    assert _review_status(plan) == "pending_review"
    assert len(reviews) == 2 and reviews[1]["doc_ref"] == plan           # a new review of the revised plan
    _passes(_admitted(reviews[1], 2))
    assert (_node(env, wp), _review_status(plan)) == (("done", plan), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t2]


# ── 5. refusals: empty, twice, text ──────────────────────────────────────────

def test_5_a_plan_of_no_work_set_is_refused_422_and_changes_nothing(env, decided, reviews):
    wp, _t2 = _person_wp(env, 1)
    plan = _plan_line(env, wp)
    before = _wp_docs(env)
    res = _submit(env, wp)                                              # the untouched draft: 작업 0세트
    assert res.status_code == 422, res.text
    assert _reason(res) == "empty_body"
    from modules.flow_gate.services import work_plan_service as wp_service

    no_work = json.loads(_wp_body("design only"))
    no_work["quantities"]["T"]["count"] = 0
    no_work["steps"] = []
    saved = _save(env, plan, body=no_work)
    assert saved.status_code == 200, saved.text
    assert wp_service.totals(_read(env, plan)["body"])["work_sets"] == 0
    again = _submit(env, wp)
    assert again.status_code == 422 and _reason(again) == "empty_body"
    assert _wp_docs(env) == before and _doc(plan)["status"] == "draft" and _review_status(plan) is None
    assert _node(env, wp) == ("open", None) and _consumed(env, wp) == [] and reviews == []


def test_5_a_second_submission_registers_nothing_more(env, decided, reviews):
    wp, t2 = _person_wp(env, 0)
    plan = _plan_line(env, wp)
    assert _save(env, plan).status_code == 200
    assert _submit(env, wp).status_code == 201
    revision = _doc(plan)["revision_no"]
    again = _submit(env, wp)
    assert again.status_code == 409, again.text
    assert _code(again) == "slot_has_document"
    assert len(_consumed(env, wp)) == 1 and _doc(plan)["revision_no"] == revision
    assert _node(env, wp) == ("done", plan) and _node(env, t2)[0] == "open" and len(env["jobs"]) == 1


def test_5_a_plan_line_takes_no_text(env, decided, reviews):
    wp, _t2 = _person_wp(env, 1)
    res = _submit(env, wp, body={"content": "# a plan as text"})
    assert res.status_code == 422 and _reason(res) == "not_for_this_slot"
    assert _node(env, wp) == ("open", None) and _consumed(env, wp) == []
