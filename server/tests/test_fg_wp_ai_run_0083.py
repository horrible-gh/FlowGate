"""flowgate.v02.0083 T#1 (WP-1) — a work plan (WP) card whose work AI is set runs on that AI, and its result takes the
run cards' path: review -> automatic approval -> the next card.

0072 T#3 개정 (revised): the plan card's run block was values only (``test_a_work_plan_node_hands_nothing_to_a_run``).
Now ``settings.uses_ai`` judges a plan card's writer — an explicit person assignee, else the card's work AI
(``run_ai``), else an explicit AI assignee, else a person — ``fields.run_plan`` hands the AI writer its work AI, time
limit and restarts, and ``fields.after_result`` gives its AI result the review / approval of a run card. The review
engine and the Graph run state (``graph_run/*``) are not changed: the review, its rework, the approval and the
propagation are the existing ones (``review_stage._after_work`` / ``_after_review``, ``legacy_adapter.approve``).

The reported case (flowgate.v02.0082 / gn_5kjgdqmwjpq7): a WP whose work AI was aip_ozi33p (Claude Opus 5.5) was handed
to a person — open / active, no job. Covered here:

(a) a WP with its work AI: the job is created, its provider is that AI and the node's tokens are issued to it;
(b) the writer order: person assignee + work AI -> the person; work AI only -> it; AI assignee only -> that AI;
    AI assignee and another work AI -> the work AI; nothing -> a person;
(c) review count 1: passed -> approved at once -> the next card opens; review 'none' (0): approved at once -> the
    next card opens;
(d) the Graph node's AI token registers the WP JSON through the real HTTP ``POST /inbox``: 201 when valid, 400 when
    malformed, 409 (``graph_attempt_inactive``) at a wrong stage (its run idle) or for a duplicate (the card's WP
    already accepted and approved);
(e) the reported state (a person's token, open / active, no job, the work AI stored) is reassigned to the AI by one
    resume (``go_on_command`` -> ``needs_ai_dispatch``);

and the regressions: run / test cards still approve by themselves, person-assigned run cards keep a person's
writing and approval (0083 WP-4 개정: a person's WP takes its card's review / approval, ``test_fg_person_wp_review_0083``),
and a step command keeps the next card pending.

On the card settings suites' real SQLite DB, routes and fixtures (``test_fg_card_settings``,
``test_fg_card_settings_review``, ``test_fg_card_approval_0056``); the AI invoke admission is a recorder, as there.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _doc, _h, _sql, _start, db_path, decided, env, setting,
)
from test_fg_card_settings_review import (
    REVIEW_RUN, _admin_and_reviewer, _executions_ended, _loop_row, _node, _recorded_start_run, _result,
    _review_status, _run_ended,
)
from test_fg_card_approval_0056 import (  # noqa: F401 — card_approval_port: registered as startup does
    LEE, _chain, _current, _human_result, _node_run, _run_id, card_approval_port,
)

OPUS = "aip_ozi33p"                     # the reported case's work AI (Claude Opus 5.5)
WP_RUN = "air_wp_work"
ROOT = f"{FA}.0001-R"


# ── helpers ──────────────────────────────────────────────────────────────────

def _opus(env):
    _sql(env["db"], "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                    "VALUES (?, 'Claude Opus 5.5', 'cli', 'claude', 't', 't')", (OPUS,))


def _cards(env, cards, settings=None):
    """R -> the cards (``(doc_type, assignee)``) confirmed; ``settings``: ``{index: ((field, value), ...)}``."""
    keys = _chain(env, cards)
    for index, values in (settings or {}).items():
        for field, value in values:
            setting(env, FA, keys[index], field, value)
    return keys


def _tokens(env, key):
    return _sql(env["db"], "SELECT t.token_id, t.provider_id, t.issued_to, t.ai_run_id, t.consumed_at FROM tokens t "
                           "JOIN graph_run_token_map m ON m.token_id = t.token_id WHERE m.node_key = ? ORDER BY m.rowid",
                (key,))


def _attempt(env, key):
    rows = _sql(env["db"], "SELECT status FROM graph_node_attempts WHERE node_key = ? ORDER BY created_at", (key,))
    return [r["status"] for r in rows]


def _run_status(env):
    run = _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE group_id = ?", (FA,))[0]
    return run["status"], run["idle_reason"]


def _start_ai_run(monkeypatch, job, run_id=WP_RUN, tokens=1):
    """The node's AI start as the engine runs it (``ai_node_starter``), the AI invoke admission recorded: the start's
    own issuer mints the run's token(s) and maps them to the node. -> (mention head types, the start call, issued)."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service, mention_service

    heads, calls, issued = [], [], []
    monkeypatch.setattr(mention_service, "build_mention_from_token_rec",
                        lambda **kw: heads.append(kw["head_type"]) or "mention")

    def fake_start_run(**kw):
        calls.append(kw)
        for _ in range(tokens):                     # a retry's reissue mints another token of the same run
            issued.append(kw["issue_builder"](ai_run_id=run_id))
        return {"run_id": run_id, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", fake_start_run)
    ai_node_starter.start_ai_invoke(job)
    [call] = calls
    return heads, call, issued


def _wp_json():
    """A valid WP body in its canonical form (the same validator the inbox runs)."""
    from modules.flow_gate.services import work_plan_service as wp

    counted = ["T"]
    quantities = {"T": {"unit": wp.WORK_PLAN_TYPE_UNITS["T"], "count": 1}}
    body = {"wp_version": 1, "binding": "advisory", "counted_types": counted, "quantities": quantities,
            "provider_candidates": [{"provider_id": OPUS, "display_name": "Claude Opus 5.5",
                                     "group_label": "Claude · CLI"}],
            "defaults": {"provider_id": None, "note": ""}, "steps": wp.expand_steps(counted, quantities)}
    return wp.dumps(wp.validate(body))


def _wp_result(seq):
    """A registered WP (its canonical JSON body) waiting for review."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    doc_id = _doc(FA, seq, "WP")
    rel = f"docs/{doc_id}_document.json"
    path = get_storage_root("flowgate", create=True) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_wp_json(), encoding="utf-8")
    db_documents.update(doc_id, {"file_path": rel, "doc_review_status": "pending_review"})
    return doc_id


def _register(token_id, doc_id, typ):
    """The AI registers its result as the inbox does: the Graph contact, then its token's consume."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    v01_contact.after_inbox_registered(FA, doc_id, typ, token_id)
    token_service.consume(token_id, "flowgate", doc_id)


def _ai_wp_in_review(env, monkeypatch, cards, settings):
    """The first card (an AI WP) runs, its AI registers a WP and its process ends (the ledger end, as ``_in_review``).
    -> (card keys, the WP)."""
    _opus(env)
    _admin_and_reviewer(env)
    keys = _cards(env, cards, settings)
    _start(env)
    [job] = env["jobs"]          # kept: the recorder records each execution once (a requested one is redelivered)
    _heads, _call, [issued] = _start_ai_run(monkeypatch, job)
    result = _wp_result(81)
    _register(issued["token_id"], result, "WP")
    assert _node(env, keys[0]) == ("review", result)
    _executions_ended()
    return keys, result


def _grant_document_create():
    """u_kim registers documents in the project (the inbox's own RBAC check, run for real as in the WP suite)."""
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.rbac import permission_service

    store, now = get_store(), now_iso()
    store._execute("INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
                   ["role_wp_0083", "WP writer", now, now])
    for perm in ("perm_document_create", "perm_document_read"):
        store._execute("INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
                       [perm, perm, now])
        store._execute("INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
                       ["role_wp_0083", perm])
    store._execute("INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) "
                   "VALUES (?,?,?,?)", ["u_kim", "flowgate", "role_wp_0083", now])
    permission_service.clear_all_cache()


def _inbox(raw_token, content):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/inbox", headers={"Authorization": f"Bearer {raw_token}"},
        json={"project": "flowgate", "module": "v02", "group_name": FA, "action": "new", "prev_doc_id": ROOT,
              "doc_type": "WP", "title": "작업계획 — AI", "content": content})


def _wp_documents(env):
    """The Flow's WP documents (the card's own plan line draft included)."""
    return {r["doc_id"] for r in _sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ? AND type_code = 'WP'",
                                      (FA,))}


def _consumed(env, token_id):
    return _sql(env["db"], "SELECT consumed_at FROM tokens WHERE token_id = ?", (token_id,))[0]["consumed_at"]


def _plan_line(env, key):
    return _sql(env["db"], "SELECT doc_id FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = 'plan'",
                (FA, key))[0]["doc_id"]


# ── (a) the work AI runs the WP ──────────────────────────────────────────────

def test_a_wp_with_its_work_ai_creates_the_job_on_that_ai_and_issues_its_tokens_to_it(env, decided, monkeypatch):
    """0072 T#3 개정: the reported case's card (no assignee, work AI aip_ozi33p) is an AI node — not a person's."""
    _opus(env)
    [wp] = _cards(env, [("WP", None)], {0: (("run_ai", OPUS), ("time_limit", 90), ("restart", 1))})
    _start(env)
    [job] = env["jobs"]
    assert (job["node_key"], job["provider_id"], job["stage"], job["doc_type"], job["result_doc_type"]) == (
        wp, OPUS, "result", "WP", "WP")                                  # no instruction stage: the WP itself
    assert (job["step_timeout_sec"], job["restart_max"], job["instruction"]) == (5400, 1, None)
    [dispatch] = _tokens(env, wp)
    assert (dispatch["provider_id"], dispatch["issued_to"]) == (OPUS, "u_kim")
    assert _run_status(env)[0] == "running" and _node(env, wp) == ("open", None) and _attempt(env, wp) == ["active"]
    ledger = _sql(env["db"], "SELECT kind, status FROM graph_attempt_runs")
    assert ledger == [{"kind": "write", "status": "requested"}]          # the write is requested for the attempt
    heads, call, [issued] = _start_ai_run(monkeypatch, job)
    assert heads == ["WP"]                                              # the AI is asked for a WP
    assert {k: call[k] for k in ("provider_id", "action_scope", "mode", "continuation_step_timeout_sec",
                                 "node_restart_max_attempts")} == {
        "provider_id": OPUS, "action_scope": "new", "mode": "single", "continuation_step_timeout_sec": 5400,
        "node_restart_max_attempts": 1}
    ai_token = next(t for t in _tokens(env, wp) if t["token_id"] == issued["token_id"])
    assert (ai_token["provider_id"], ai_token["ai_run_id"]) == (OPUS, WP_RUN)   # the AI's own token


# ── (b) who writes a WP ──────────────────────────────────────────────────────

AI_ASSIGNEE = {"kind": "ai", "user_id": None, "provider_id": "aip_assignee"}


@pytest.mark.parametrize("assignee, run_ai, writer", [
    (LEE, "aip_card", "u_lee"),                     # an explicit person assignee wins over the work AI
    (None, "aip_card", "aip_card"),                 # the work AI only
    (AI_ASSIGNEE, None, "aip_assignee"),            # an explicit AI assignee only
    (AI_ASSIGNEE, "aip_card", "aip_card"),          # the AI assignee and another work AI: the work AI
    (None, None, "u_kim"),                          # nothing: a person (the run's starter) writes it
], ids=["person_over_work_ai", "work_ai_only", "ai_assignee_only", "work_ai_over_ai_assignee", "nothing_person"])
def test_b_the_wp_writer_follows_person_then_work_ai_then_ai_assignee_then_person(
        env, decided, assignee, run_ai, writer):
    """0072 T#3 개정: the plan card's writer order (CH seq 5). Run / test cards keep their own judgement."""
    from flowgate.card_settings import settings

    [wp] = _cards(env, [("WP", assignee)], {0: (("run_ai", run_ai),)} if run_ai else None)
    _start(env)
    [dispatch] = _tokens(env, wp)
    if writer.startswith("aip_"):
        [job] = env["jobs"]
        assert (job["node_key"], job["provider_id"], job["stage"]) == (wp, writer, "result")
        assert (dispatch["provider_id"], dispatch["issued_to"]) == (writer, "u_kim")
        assert _run_status(env)[0] == "running"
    else:
        assert env["jobs"] == []                                         # no AI start: the person writes it
        assert (dispatch["provider_id"], dispatch["issued_to"]) == (None, writer)
        assert _node(env, wp) == ("open", None) and _attempt(env, wp) == ["active"]
    row = {"node_key": wp, "node_type": "document", "doc_type": "WP",
           "assignee_kind": (assignee or {}).get("kind")}
    assert settings.uses_ai(FA, row) is writer.startswith("aip_")


# ── (c) review -> automatic approval -> the next card ───────────────────────

def test_c_a_wp_review_that_passes_approves_it_and_opens_the_next_card(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_run_end_hooks

    (wp, t2), result = _ai_wp_in_review(env, monkeypatch, [("WP", None), ("T", AI)],
                                        {0: (("run_ai", OPUS), ("review_count", 1), ("review_ai", "aip_review"))})
    calls = _recorded_start_run(monkeypatch)
    _run_ended(WP_RUN, provider_id=OPUS)                                # the WP's AI run ended on its own
    [call] = calls                                                       # the card's review loop, once
    assert (call["doc_ref"], call["action_scope"]) == (result, "review")
    loop = call["document_review_loop"]
    assert (loop["review_count"], loop["reviewer_provider_id"], loop["rework_provider_id"]) == (1, "aip_review", OPUS)
    assert (_node(env, wp), _review_status(result)) == (("review", result), "pending_review")   # the review decides
    assert _node(env, t2)[0] == "pending"
    _executions_ended()                                                  # the review hop's process ended
    _loop_row(result, "review")
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_passed' WHERE run_id = ?", (REVIEW_RUN,))
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped", "stop_reason": "review_passed"}})
    assert (_node(env, wp), _review_status(result)) == (("done", result), "approved")    # no person approved it
    assert _plan_line(env, wp) == result
    assert _node(env, t2)[0] == "open"                                   # the next card, without a resume
    assert [j["node_key"] for j in env["jobs"]] == [wp, t2]              # and its AI starts


def test_c_a_wp_without_a_review_is_approved_at_once_and_opens_the_next_card(env, decided, monkeypatch):
    (wp, t2), result = _ai_wp_in_review(env, monkeypatch, [("WP", None), ("T", AI)], {0: (("run_ai", OPUS),)})
    calls = _recorded_start_run(monkeypatch)
    _run_ended(WP_RUN, provider_id=OPUS)
    assert calls == []                                                   # review 'none': no review loop
    assert (_node(env, wp), _review_status(result)) == (("done", result), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [wp, t2]
    assert _node_run(env, wp)["awaiting_approval"] is False


def test_c_a_wp_review_that_does_not_pass_leaves_the_card_to_a_person(env, decided, monkeypatch):
    """The review stage's own rule, unchanged: a review that ends any other way waits for the card approval."""
    from modules.flow_gate.services import ai_run_end_hooks

    (wp, t2), result = _ai_wp_in_review(env, monkeypatch, [("WP", None), ("T", AI)],
                                        {0: (("run_ai", OPUS), ("review_count", 1), ("review_ai", "aip_review"))})
    _recorded_start_run(monkeypatch)
    _run_ended(WP_RUN, provider_id=OPUS)
    _executions_ended()
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped",
                                                         "stop_reason": "review_count_exhausted"}})
    assert (_node(env, wp), _review_status(result)) == (("review", result), "pending_review")
    assert _node(env, t2)[0] == "pending"


# ── (d) the real HTTP inbox with the Graph node's AI token ───────────────────

def test_d_the_ai_token_registers_its_wp_json_201_refuses_a_malformed_body_400_and_a_duplicate_409(
        env, decided, monkeypatch):
    _opus(env)
    _admin_and_reviewer(env)
    _grant_document_create()
    [wp] = _cards(env, [("WP", None)], {0: (("run_ai", OPUS),)})
    _start(env)
    [job] = env["jobs"]
    _heads, _call, (first, second) = _start_ai_run(monkeypatch, job, tokens=2)
    drafts = _wp_documents(env)                                          # the card's plan line draft

    for malformed in ("이건 JSON 작업계획이 아니다", "[1, 2, 3]"):
        bad = _inbox(first["raw_token"], malformed)
        assert bad.status_code == 400, bad.text
    assert _wp_documents(env) == drafts and _consumed(env, first["token_id"]) is None   # nothing numbered or consumed
    assert _node(env, wp) == ("open", None)

    ok = _inbox(first["raw_token"], _wp_json())
    assert ok.status_code == 201, ok.text
    doc_id = ok.json()["doc_id"]
    assert _wp_documents(env) == drafts | {doc_id} and _consumed(env, first["token_id"])
    assert _node(env, wp) == ("review", doc_id) and _plan_line(env, wp) == doc_id   # bound, on its plan line
    assert _review_status(doc_id) == "pending_review"

    _executions_ended()
    _run_ended(WP_RUN, provider_id=OPUS)                                # review 'none': approved at once
    assert (_node(env, wp), _review_status(doc_id)) == (("done", doc_id), "approved")

    duplicate = _inbox(second["raw_token"], _wp_json())                 # the same run's other token, once more
    assert duplicate.status_code == 409, duplicate.text
    assert "graph_attempt_inactive" in duplicate.text
    assert _wp_documents(env) == drafts | {doc_id} and _consumed(env, second["token_id"]) is None


def test_d_the_ai_token_at_a_wrong_stage_is_refused_409_before_anything_is_stored(env, decided, monkeypatch):
    _opus(env)
    _admin_and_reviewer(env)
    _grant_document_create()
    [wp] = _cards(env, [("WP", None)], {0: (("run_ai", OPUS),)})
    _start(env)
    [job] = env["jobs"]
    _heads, _call, [issued] = _start_ai_run(monkeypatch, job)
    drafts = _wp_documents(env)
    _executions_ended()                                                  # its run is over, nothing registered
    _current(env)                                                        # the read settles the run: idle
    assert _run_status(env)[0] == "idle"
    late = _inbox(issued["raw_token"], _wp_json())
    assert late.status_code == 409, late.text
    assert "graph_attempt_inactive" in late.text
    assert _wp_documents(env) == drafts and _consumed(env, issued["token_id"]) is None
    assert _node(env, wp) == ("open", None)


# ── (e) the reported state is reassigned by one resume ───────────────────────

def test_e_the_reported_open_wp_of_a_person_goes_to_its_work_ai_on_one_resume(env, decided, monkeypatch):
    """The state the reported case left (flowgate.v02.0082 / gn_5kjgdqmwjpq7): the WP opened for a person before this
    fix (0072 T#3: a plan card never used AI) — a person's token, open / active, no job, the work AI stored. With the
    fixed judgement one resume reassigns it (``go_on_command`` -> ``needs_ai_dispatch``), no data repair."""
    from flowgate.card_settings import fields, settings
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    from modules.flow_gate.graph_workflow.services import node_dispatch, run_service

    _opus(env)
    [wp] = _cards(env, [("WP", None)], {0: (("run_ai", OPUS),)})

    def before_this_fix(_flow_id, node):
        if node.get("assignee_kind") is not None:
            return node["assignee_kind"] == "ai"
        return fields.card_kind(node.get("node_type"), node.get("doc_type")) in (fields.RUN, fields.TEST)

    ports.register(ports.USES_AI, before_this_fix)
    _start(env)
    ports.register(ports.USES_AI, settings.uses_ai)                      # the fixed server takes the same rows over
    [person] = _tokens(env, wp)
    assert env["jobs"] == [] and (person["provider_id"], person["issued_to"]) == (None, "u_kim")
    assert _node(env, wp) == ("open", None) and _attempt(env, wp) == ["active"]
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_attempt_runs")[0]["n"] == 0
    assert _current(env)["status"] == "idle"

    commands, dispatched = [], []
    real_go_on, real_needs = run_service.go_on_command, node_dispatch.needs_ai_dispatch
    monkeypatch.setattr(run_service, "go_on_command",
                        lambda ctx, command, policies: commands.append(command) or real_go_on(ctx, command, policies))
    monkeypatch.setattr(node_dispatch, "needs_ai_dispatch",
                        lambda ctx, row: dispatched.append((row["node_key"], real_needs(ctx, row))) or dispatched[-1][1])
    resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text
    assert commands and (wp, True) in dispatched                         # the command reassigns the open WP
    [job] = env["jobs"]
    assert (job["node_key"], job["provider_id"], job["stage"]) == (wp, OPUS, "result")
    tokens = _tokens(env, wp)
    assert [t["provider_id"] for t in tokens] == [None, OPUS]            # the person's token stays, the AI's is new
    assert _run_status(env)[0] == "running" and _node(env, wp) == ("open", None)


# ── regressions ──────────────────────────────────────────────────────────────

def test_r_run_cards_still_approve_by_themselves_and_open_the_next_card(env, decided, monkeypatch):
    _admin_and_reviewer(env)
    t1, t2 = _cards(env, [("T", AI), ("T", AI)], {0: (("instruction", "omit"),)})
    _start(env)
    [job] = env["jobs"]
    _heads, _call, [issued] = _start_ai_run(monkeypatch, job, run_id="air_t1")
    result = _result(81, "TR")
    _register(issued["token_id"], result, "TR")
    _executions_ended()
    _run_ended("air_t1")
    assert (_node(env, t1), _review_status(result)) == (("done", result), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t1, t2]


def test_r_run_and_test_cards_keep_their_judgement(env):
    """RUN / TEST: unassigned -> AI (the project default when the card names none); requirement / check: none."""
    from flowgate.card_settings import fields as f
    from flowgate.card_settings import settings

    def row(doc_type, kind=None, node_type="document"):
        return {"node_key": "gn_none", "node_type": node_type, "doc_type": doc_type, "assignee_kind": kind}

    assert [settings.uses_ai(FA, row(t)) for t in ("T", "N", "TS", "D")] == [True, True, True, True]
    assert settings.uses_ai(FA, row(None, node_type="custom")) is True
    assert [settings.uses_ai(FA, row(t)) for t in ("R", "B", "WP")] == [False, False, False]
    assert settings.uses_ai(FA, row(None, node_type="gate")) is False
    assert settings.uses_ai(FA, row("T", "human")) is False and settings.uses_ai(FA, row("T", "ai")) is True
    assert [f.after_result(k, {}) for k in (f.RUN, f.TEST, f.PLAN)] == [f.APPROVE] * 3
    assert [f.after_result(k, {"review_count": 1}) for k in (f.REQUIREMENT, f.CHECK, None)] == [None] * 3
    assert f.run_plan(f.TEST, {"time_limit": 120, "restart": 1}) == {"settings": {}}     # TEST: as before


@pytest.mark.parametrize("doc_type, typ", [("T", "T")])
def test_r_a_person_assigned_run_card_keeps_a_person_s_writing_and_approval_revised_0083_wp4(env, decided, doc_type, typ):
    """0083 WP-4 개정 (revised): the WP case is gone from here. A work plan a person writes now takes its card's review
    or, review 'none' (this card), its automatic approval (``test_fg_person_wp_review_0083``); a person's run card still
    waits for a person's approval."""
    _admin_and_reviewer(env)
    [key] = _cards(env, [(doc_type, LEE)], {0: (("run_ai", "aip_card"),)})
    _start(env)
    assert env["jobs"] == []
    [token] = _tokens(env, key)
    assert (token["provider_id"], token["issued_to"]) == (None, "u_lee")
    result = _human_result(env, key, 81, typ)
    assert _node_run(env, key)["awaiting_approval"] is True              # nothing approves a person's result here
    assert _review_status(result) == "pending_review"


def test_r_a_step_command_keeps_the_next_card_pending_after_the_wp_is_approved(env, decided, monkeypatch):
    _opus(env)
    _admin_and_reviewer(env)
    wp, t2 = _cards(env, [("WP", None), ("T", AI)], {0: (("run_ai", OPUS),)})
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": "step"}, headers=_h())
    assert started.status_code == 201, started.text
    [job] = env["jobs"]
    _heads, _call, [issued] = _start_ai_run(monkeypatch, job)
    result = _wp_result(81)
    _register(issued["token_id"], result, "WP")
    _executions_ended()
    _run_ended(WP_RUN, provider_id=OPUS)
    assert (_node(env, wp), _review_status(result)) == (("done", result), "approved")
    assert _node(env, t2)[0] == "pending"                                # the step is over: the next one waits
    assert [j["node_key"] for j in env["jobs"]] == [wp]                  # no AI start for it
    assert _sql(env["db"], "SELECT status FROM graph_propagations WHERE node_key = ?", (wp,)) == [{"status": "pending"}]
