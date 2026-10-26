"""0064 T-B4d — the AI's first naming of a card (0062 D0005 set 3, L0006 2.3~2.6, P0006 §4.2.5 / §4.2.6, DB0007 §2.2).

On the real SQLite DB, routes and fixtures of ``test_fg_card_settings`` (a confirmed R -> AI card Flow whose AI start is
recorded as a job; ``ai_invoke_service.start_run`` replaced so the start's issue builder runs as the engine runs it):
the first AI start of a card showing its default name claims the title opportunity in the mapping guard and the prompt
the worker hands a provider with its token ends with the title request, worded for that provider's kind (whatever the
job named: the project default chain, a fallback); every later start (retry, reissue, the result stage, a new run) asks
nothing and its prompt is its mention byte for byte. The registration of the claiming token (the inbox order: the Graph contact, the consume, then the
title hook) applies a valid candidate once, rechecked in the guard; any other candidate or registration closes the
opportunity without touching the registration. Every token map row carries its kind (``map_kind``).

Verification ids: DB0007 M-1 ~ M-8, L0005 V-B4d-1 ~ 3, 0062 L0006 V-01 ~ V-12, V-14, V-15, V-18 (person x AI),
V-19 ~ V-23, V-27, V-28, P0006 V-P-22 ~ V-P-24, V-P-26.
"""
from __future__ import annotations

import ast
import os
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

import pytest

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

import config  # noqa: E402,F401 — its import-time env export happens at collection, not in a test

import _flowgate_test_imports  # noqa: F401
from test_fg_card_settings import (  # noqa: E402,F401 — fixtures used by name
    AI, FA, FB, _doc, _h, _sql, _start, db_path, decided, edge, env, node, put_draft,
)
from test_fg_card_settings_review import _admin_and_reviewer  # noqa: E402
from tests.regression.graph_attempt.test_graph_attempt_results_0061 import result_db  # noqa: E402,F401 — the execution ledger (M-3 / M-4)
from test_graph_attempt_runs_0061 import db  # noqa: E402,F401 — used by result_db
from test_node_title_0064 import canvas_node, put_title, title_events, title_rows  # noqa: E402

_SERVER_DIR = Path(__file__).resolve().parents[3]
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
SEP = "\n\n"
NODE_MENTION = "NODE MENTION"
ABSENT = object()
API_AI = {"kind": "ai", "user_id": None, "provider_id": "aip_api"}
# P0006 §4.2.6, pinned here as the protocol fixes them.
CLI_REQUEST = {
    "ko": "이 카드는 아직 기본 제목입니다. 작업 내용을 한눈에 알 수 있는 제목을 20~30자로 짓되, 카드 종류 명칭(예: 조사, 요구사항)을 그대로 반복하지 마세요. 그 제목을 문서를 제출하는 inbox 요청 본문의 `node_title` 항목에 넣어 주세요. 제목은 선택 사항이며 문서 제출에는 영향을 주지 않습니다.",  # noqa: E501
    "en": "This card still has its default title. Write a title of 20 to 30 characters that tells at a glance what this work is, without simply repeating the card kind name (for example \"Research\" or \"Requirement\"), and put it in the `node_title` field of the body of your inbox submission. The title is optional and does not affect the document submission.",  # noqa: E501
    "ja": "このカードはまだ既定のタイトルです。作業内容がひと目で分かるタイトルを20〜30文字で考えてください。カード種類の名称（例：調査、要件）をそのまま繰り返さないでください。そのタイトルを、文書を提出する inbox リクエスト本文の `node_title` 項目に入れてください。タイトルは任意で、文書の提出には影響しません。",  # noqa: E501
}
API_REQUEST = {
    "ko": "이 카드는 아직 기본 제목입니다. 작업 내용을 한눈에 알 수 있는 제목을 20~30자로 짓되, 카드 종류 명칭(예: 조사, 요구사항)을 그대로 반복하지 마세요. 그 제목을 문서를 등록하는 `register_document` 도구 입력의 `node_title` 항목에 넣어 주세요. 제목은 선택 사항이며 문서 등록에는 영향을 주지 않습니다.",  # noqa: E501
    "en": "This card still has its default title. Write a title of 20 to 30 characters that tells at a glance what this work is, without simply repeating the card kind name (for example \"Research\" or \"Requirement\"), and put it in the `node_title` input of your `register_document` tool call. The title is optional and does not affect the document registration.",  # noqa: E501
    "ja": "このカードはまだ既定のタイトルです。作業内容がひと目で分かるタイトルを20〜30文字で考えてください。カード種類の名称（例：調査、要件）をそのまま繰り返さないでください。そのタイトルを、文書を登録する `register_document` ツール入力の `node_title` 項目に入れてください。タイトルは任意で、文書の登録には影響しません。",  # noqa: E501
}
NO_REPEAT = {"ko": "카드 종류 명칭(예: 조사, 요구사항)을 그대로 반복하지 마세요",
             "en": "without simply repeating the card kind name", "ja": "カード種類の名称（例：調査、要件）をそのまま繰り返さないでください"}
TOOL_DESCRIPTION = (
    "Register the document for this step. Optional node_title (new and edit scopes only; other scopes reject it): "
    "a 20-30 character card title that tells at a glance what this work is; do not simply repeat the card kind name "
    "(for example \"Research\" or \"Requirement\"). Only offered when your prompt asks for it. It never affects "
    "whether the document registers.")
GOOD = "OAuth 제공자 비교 조사"


# ── helpers ──────────────────────────────────────────────────────────────────

def card(ref, typ, label, assignee=None, node_type="document", palette=None):
    row = node(ref, typ, node_type=node_type, assignee=assignee)
    row.update(label=label, palette_item_id=palette)
    return row


def _ai_card(env, label="작업", typ="T", assignee=AI, node_type="document", palette=None):
    """A confirmed R -> AI card Flow (FA) started: (the card's key, its AI start job)."""
    _admin_and_reviewer(env)
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), card("tmp_c", typ, label, assignee, node_type, palette)],
                      [edge("tmp_e", "tmp_r", "tmp_c")])
    key = first["key_map"]["tmp_c"]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    return key, job


def _issue(monkeypatch, job, run_ids=("air_1",), mention=NODE_MENTION):
    """Start the card's AI through ``ai_node_starter`` with the engine admission replaced: its issue builder runs once
    per run id given (one start, then its retries / reissues). -> the issues (mention, token_id, ...)."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service, mention_service

    monkeypatch.setattr(mention_service, "build_mention_from_token_rec", lambda **kw: mention)
    issued, calls = [], []

    def start_run(**kw):
        calls.append(kw)
        for run_id in run_ids:
            issued.append(kw["issue_builder"](ai_run_id=run_id))
        return {"run_id": run_ids[0], "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
    assert ai_node_starter.start_ai_invoke(job) == {"run_id": run_ids[0], "status": "running"}
    assert len(calls) == 1                                       # one AI start; no title-only AI call (V-20)
    return issued


def _base_mention(job, run_id, key):
    """What ``_issue`` built before this set: the precheck section and the memos in front of the mention."""
    from modules.flow_gate.services import ai_start_precheck, card_memo_hooks

    text = ai_start_precheck.node_mention(NODE_MENTION, job, run_id)
    return card_memo_hooks.prepend_memo_section(text, FA, node_key=key)


def _prompt(issued, exec_type="cli"):
    """What the worker hands a provider of ``exec_type`` with this issue's token (its ``_provider_prompt``)."""
    from modules.flow_gate.services.ai_invoke import worker

    return worker._provider_prompt({"action_scope": "new", "raw_token": issued["raw_token"]},
                                   {"exec_type": exec_type}, issued["mention"])


def _asks_nothing(issued):
    """Every kind of provider gets the issue's mention byte for byte."""
    return all(_prompt(issued, kind) == issued["mention"] for kind in ("cli", "api"))


def _register(doc_id, token_id, candidate=ABSENT, action="edit", typ="T"):
    """The inbox registration in the inbox's own order: the Graph contact, the consume, then the card title hook with
    the request's ``node_title`` as sent."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import node_title_hooks, token_service

    v01_contact.after_inbox_registered(FA, doc_id, typ, token_id)
    token_service.consume(token_id, "flowgate", doc_id)
    node_title_hooks.after_registration(FA, doc_id, token_id, None if candidate is ABSENT else candidate, action)


def _state(env, key):
    rows = [r for r in title_rows(env, FA) if r["node_key"] == key]
    return rows[0] if rows else None


def _kinds(env):
    return {r["token_id"]: r["map_kind"] for r in _sql(
        env["db"], "SELECT m.token_id, m.map_kind FROM graph_run_token_map m JOIN graph_runs r ON r.run_id = m.run_id "
                   "WHERE r.group_id = ?", (FA,))}


def _consumed(env, token_id):
    return [r for r in _sql(env["db"], "SELECT metadata FROM workflow_events WHERE event_type = 'token_consumed'")
            if f'"token_id": "{token_id}"' in (r["metadata"] or "") or f'"token_id":"{token_id}"' in (r["metadata"] or "")]


# ── the claim at the first AI start, the candidate, the request (V-01, V-B4d-1, M-1, M-2, M-7, V-P-22/24) ──

def test_the_first_ai_start_of_a_default_card_claims_asks_and_takes_the_ai_title(env, decided, monkeypatch):
    key, job = _ai_card(env)
    dispatch = job["token"]["token_id"]
    [issued] = _issue(monkeypatch, job)
    token = issued["token_id"]
    st = _state(env, key)
    assert (st["opportunity_state"], st["claimed_token_id"], st["claimed_ai_run_id"], st["version"], st["title"],
            st["title_source"], st["human_edited"], st["close_reason"]) == (
        "claimed", token, "air_1", 1, None, "none", 0, None)
    assert issued["mention"] == _base_mention(job, "air_1", key)                                 # the mention as ever
    assert _prompt(issued) == issued["mention"] + SEP + CLI_REQUEST["ko"]                        # at the very end
    assert _prompt(issued, "api") == issued["mention"] + SEP + API_REQUEST["ko"]
    assert title_events(env) == []                                                               # a claim tells nothing
    assert _kinds(env) == {dispatch: "dispatch", token: "ai_start"}                               # M-1, M-2, M-7
    assert canvas_node(env, FA, key)["title_state"] == {"title": None, "title_source": None, "version": 1}

    _register(job["target_doc_id"], token, f"  {GOOD}  ")                                        # normalized
    st = _state(env, key)
    assert (st["title"], st["title_source"], st["opportunity_state"], st["close_reason"], st["version"],
            st["human_edited"]) == (GOOD, "ai", "closed", "applied", 2, 0)
    [event] = title_events(env)
    assert event == {"flow_id": FA, "reason": "title", "actor_user_id": None, "origin_client_id": None,
                     "draft_version": None, "revision_no": None, "node_keys": [key]}
    shown = canvas_node(env, FA, key)
    assert (shown["title"], shown["title_state"]) == (GOOD, {"title": GOOD, "title_source": "ai", "version": 2})
    assert _consumed(env, token)                                                                  # registered as ever


def test_every_later_start_asks_nothing_and_keeps_its_mention(env, decided, monkeypatch):
    """V-02 / V-03: a reissue of the claiming run (no-output retry, Pause / Resume, Stop / Resume) and any start after
    the opportunity closed (the result stage, a new run, a restart: the state is the database's) ask nothing."""
    key, job = _ai_card(env)
    first, retry = _issue(monkeypatch, job, run_ids=("air_1", "air_1"))
    assert first["mention"] == retry["mention"] == _base_mention(job, "air_1", key)
    assert _prompt(first) == _prompt(retry) + SEP + CLI_REQUEST["ko"]
    assert _asks_nothing(retry)                                                  # byte for byte, any provider
    st = _state(env, key)
    assert (st["opportunity_state"], st["close_reason"], st["claimed_token_id"], st["version"]) == (
        "closed", "superseded_start", first["token_id"], 2)
    _register(job["target_doc_id"], first["token_id"], GOOD)                     # the claim's late candidate
    assert _state(env, key) == st and title_events(env) == []
    later_job = {**job, "stage": "result"}
    [later] = _issue(monkeypatch, later_job, run_ids=("air_2",))
    assert later["mention"] == _base_mention(later_job, "air_2", key) and _state(env, key) == st   # X: no write
    assert _asks_nothing(later)
    assert set(_kinds(env).values()) == {"dispatch", "ai_start"}


def test_a_failed_claim_starts_the_ai_without_a_request_and_the_mapping_is_the_evidence(env, decided, monkeypatch):
    """V-04 / L0006 5: the claim savepoint fails, the token is mapped (ai_start) and the AI starts with its mention;
    every later start finds that mapping (E1) and closes the opportunity as prior_evidence."""
    from modules.flow_gate.graph_workflow.store import title_store

    key, job = _ai_card(env)
    real_claim = title_store.claim
    monkeypatch.setattr(title_store, "claim", lambda *a, **k: 1 / 0)
    [issued] = _issue(monkeypatch, job)
    assert issued["mention"] == _base_mention(job, "air_1", key) and _asks_nothing(issued) and _state(env, key) is None
    assert _kinds(env)[issued["token_id"]] == "ai_start"
    monkeypatch.setattr(title_store, "claim", real_claim)
    [again] = _issue(monkeypatch, job, run_ids=("air_2",))
    st = _state(env, key)
    assert _asks_nothing(again) and (st["opportunity_state"], st["close_reason"]) == (
        "closed", "prior_evidence")


def test_a_lost_state_row_never_reopens_the_opportunity(env, decided, monkeypatch):
    """V-05: no state row (a failure, a feature turned on later) and an earlier AI start mapping: prior_evidence."""
    key, job = _ai_card(env)
    [first] = _issue(monkeypatch, job)
    _sql(env["db"], "DELETE FROM graph_node_title_states WHERE group_id = ?", (FA,))
    [second] = _issue(monkeypatch, job, run_ids=("air_2",))
    assert _asks_nothing(second)
    assert (_state(env, key)["opportunity_state"], _state(env, key)["close_reason"]) == ("closed", "prior_evidence")


@pytest.mark.parametrize("refusal", ["result_registered", "mapping_failed"])
def test_a_refused_mapping_rolls_the_claim_back(env, decided, monkeypatch, refusal):
    """V-11: a result already registered refuses the start before the claim; a mapping that fails after the claim
    rolls the guard back with the claim in it. No AI start, the token revoked, the opportunity untouched."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.graph_workflow.store import run_store

    key, job = _ai_card(env)
    if refusal == "result_registered":
        monkeypatch.setattr(ai_node_starter, "_result_registered", lambda run, j: True)
    else:
        real = run_store.map_token
        monkeypatch.setattr(run_store, "map_token", lambda *a, **k: real(*a, **k) or 1 / 0)
    with pytest.raises(Exception):
        _issue(monkeypatch, job)
    assert _state(env, key) is None and set(_kinds(env).values()) == {"dispatch"}
    revoked = _sql(env["db"], "SELECT COUNT(*) AS n FROM tokens WHERE revoked_at IS NOT NULL AND token_id <> ?",
                   (job["token"]["token_id"],))[0]["n"]
    assert revoked == 1


def _walk(monkeypatch, issued, chain):
    """The worker's provider chain walk (``_execute_provider_chain``) with this issue's token over ``chain`` (kinds;
    ``!`` = fails to start, the walk falls through) -> (kind, prompt) each provider was handed."""
    from modules.flow_gate.services import ai_invoke_service as svc, graph_execution_hooks

    handed = []

    def run_provider(provider, prompt, run):
        handed.append((provider["exec_type"], prompt))
        if not provider["fails"]:
            return "started_ok", None
        return ("api_error" if provider["exec_type"] == "api" else "spawn_failed"), None

    real_call = graph_execution_hooks.call
    monkeypatch.setattr(svc, "_api_execute", run_provider)
    monkeypatch.setattr(svc, "_cli_execute", run_provider)
    monkeypatch.setattr(svc, "_broadcast", lambda *a, **k: None)
    monkeypatch.setattr(graph_execution_hooks, "call",
                        lambda name, *a, **k: None if name == "launch" else real_call(name, *a, **k))
    providers = [{"id": f"aip_{i}", "name": f"p{i}", "exec_type": kind.rstrip("!"), "kind": "claude",
                  "fails": kind.endswith("!")} for i, kind in enumerate(chain)]
    run = {"run_id": "air_1", "group_id": FA, "action_scope": "new", "raw_token": issued["raw_token"],
           "token_id": issued["token_id"], "cancel_event": threading.Event(), "fallback_history": [],
           "provider": None, "provider_id": None}
    assert svc._execute_provider_chain(run, providers, issued["mention"]) is True
    return handed


@pytest.mark.parametrize("assignee", ["default", "api"])
@pytest.mark.parametrize("chain,asked", [
    (["api"], ["api"]),
    (["cli"], ["cli"]),
    (["api!", "cli"], ["api", "cli"]),                # the API AI fails to start: the CLI fallback gets its own wording
    (["cli!", "api"], ["cli", "api"]),
])
def test_the_request_is_worded_for_the_provider_the_prompt_is_handed_to(env, decided, monkeypatch, assignee, chain,
                                                                        asked):
    """P0006 §4.2.6 / V-P-24: a default card's job names no provider (the project default chain picks the AI at
    admission), another names an API one; either way the provider each attempt launches decides the wording, and a
    reissued token's prompt stays its mention for every provider."""
    _sql(env["db"], "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                    "VALUES ('aip_api', 'aip_api', 'api', 'claude', 't', 't')")
    key, job = _ai_card(env, assignee=None if assignee == "default" else API_AI)
    assert job.get("provider_id") == (None if assignee == "default" else "aip_api")
    first, reissued = _issue(monkeypatch, job, run_ids=("air_1", "air_1"))
    assert first["mention"] == reissued["mention"] == _base_mention(job, "air_1", key)
    wording = {"api": API_REQUEST["ko"], "cli": CLI_REQUEST["ko"]}
    assert _walk(monkeypatch, first, chain) == [(kind, first["mention"] + SEP + wording[kind]) for kind in asked]
    assert _walk(monkeypatch, reissued, chain) == [(kind, reissued["mention"]) for kind in asked]


def test_a_failing_title_request_wording_starts_the_ai_with_the_mention(env, decided, monkeypatch):
    from modules.flow_gate.services import node_title_hooks

    key, job = _ai_card(env)
    monkeypatch.setattr(node_title_hooks, "title_request", lambda *a, **k: 1 / 0)
    [issued] = _issue(monkeypatch, job)
    assert issued["mention"] == _base_mention(job, "air_1", key) and _asks_nothing(issued)
    assert node_title_hooks.title_requested(issued["raw_token"])
    assert _state(env, key)["opportunity_state"] == "claimed"                      # claimed all the same


# ── cards the naming is not for (V-08, V-B4d-2/3, V-12, V-P-25 / V-P-26 (g)) ──

@pytest.mark.parametrize("label,typ,node_type,reason", [
    ("보안 점검", None, "custom", "not_target_node"),            # a Custom card
    ("로그인 개선", "T", "document", "not_target_display"),       # a label a person gave
    ("작업 2", "T", "document", "not_target_display"),           # no default name: exact match only
])
def test_a_card_the_naming_is_not_for_closes_without_a_request(env, decided, monkeypatch, label, typ, node_type,
                                                               reason):
    key, job = _ai_card(env, label=label, typ=typ, node_type=node_type)
    [issued] = _issue(monkeypatch, job)
    st = _state(env, key)
    assert (st["opportunity_state"], st["close_reason"], st["claimed_token_id"], st["title"]) == (
        "closed", reason, None, None)
    assert issued["mention"] == _base_mention(job, "air_1", key) and _asks_nothing(issued)
    _register(job["target_doc_id"] or job["doc_ref"], issued["token_id"], GOOD)       # (g): a candidate applies nothing
    assert _state(env, key) == st and title_events(env) == []


def test_a_person_s_title_before_the_first_start_wins(env, decided, monkeypatch):
    """V-12 (a): a person saved (A -> B -> A) before the run; the AI start asks nothing and writes nothing."""
    _admin_and_reviewer(env)
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), card("tmp_c", "T", "작업", AI)], [edge("tmp_e", "tmp_r", "tmp_c")])
    key = first["key_map"]["tmp_c"]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    for text in ("A 제목", "B 제목", "A 제목"):
        put_title(env, FA, key, {"title": text})
    before = _state(env, key)
    _start(env)
    [job] = env["jobs"]
    [issued] = _issue(monkeypatch, job)
    assert _state(env, key) == before and _asks_nothing(issued)
    _register(job["target_doc_id"], issued["token_id"], GOOD)
    assert _state(env, key) == before and canvas_node(env, FA, key)["title"] == "A 제목"


def test_naming_targets_and_default_displays():
    """L0006 2.4 step 6 with L0005 2.17-1 (V-B4d-1/2): palette / R / B document cards; never Custom or a gate."""
    from modules.flow_gate.graph_workflow.services.node_title_ai import naming_target

    def row(**kw):
        return {"node_key": "n", "node_type": "document", "doc_type": "T", "label": "작업", "palette_item_id": None, **kw}

    assert naming_target(row()) and naming_target(row(doc_type="N", label="조사", palette_item_id="research"))
    assert naming_target(row(doc_type="R", label="요구사항")) and naming_target(row(doc_type="B", label="버그"))
    assert not naming_target(row(palette_item_id="custom.1")) and not naming_target(row(node_type="custom"))
    assert not naming_target(row(node_type="gate", doc_type=None, palette_item_id="confirm"))
    assert not naming_target(row(doc_type="TR"))                                   # a type with no palette name


def _views(monkeypatch, views, by_id):
    from modules.flow_gate.graph_workflow.services import node_title_ai

    monkeypatch.setattr(node_title_ai, "title_views", lambda flow_id: (views, by_id))


def test_the_shown_title_is_read_as_the_card_draws_it(monkeypatch):
    """L0006 2.5 (V-08 / V-10, V-B4d-1/3): every view must show a default name from the label; a document title never
    is one (``로그인 개선 · 요구사항``, or even ``요구사항``) unless it is the claiming AI's own document."""
    from modules.flow_gate.graph_workflow.projection.graph_projector import GraphView
    from modules.flow_gate.graph_workflow.services.node_title_ai import display_is_default

    r_card = {"node_key": "n", "node_type": "document", "doc_type": "R", "label": "요구사항", "palette_item_id": None}
    lines = {"n": [{"doc_id": "d_line", "author": "human"}]}
    by_id = {"d_line": {"doc_id": "d_line", "title": "요구사항", "group_id": FA, "status": "open"},
             "d_old": {"doc_id": "d_old", "title": "로그인 개선 · 요구사항", "group_id": FA, "status": "open"},
             "d_ai": {"doc_id": "d_ai", "title": "AI 결과", "group_id": FA, "status": "open"}}
    draft = GraphView(mode="draft", nodes=[r_card], card_docs=lines)              # a line document is no title source
    _views(monkeypatch, [draft], by_id)
    assert display_is_default(FA, "n", None) is True                                  # V-B4d-1: the new R card
    bug = GraphView(mode="draft", nodes=[{**r_card, "doc_type": "B", "label": "버그"}])
    _views(monkeypatch, [bug], by_id)
    assert display_is_default(FA, "n", None) is True                                  # V-B4d-2
    bound = GraphView(mode="revision", nodes=[r_card], bound_node_key="n", bound_doc_id="d_old")
    _views(monkeypatch, [draft, bound], by_id)
    assert display_is_default(FA, "n", None) is False                                 # V-B4d-3 / V-08: any view counts
    bound_default = GraphView(mode="revision", nodes=[r_card], bound_node_key="n", bound_doc_id="d_line")
    _views(monkeypatch, [bound_default], by_id)
    assert display_is_default(FA, "n", None) is False                                 # a document title, never a name
    run = GraphView(mode="revision", nodes=[r_card], node_runs={"n": {"result_doc_id": "d_ai"}})
    _views(monkeypatch, [run], by_id)
    assert display_is_default(FA, "n", None) is False                                 # someone's result is shown
    assert display_is_default(FA, "n", None, {"d_ai"}) is True                        # V-10: the claim's own result
    _views(monkeypatch, [], by_id)
    assert display_is_default(FA, "n", r_card) is True and display_is_default(FA, "n", None) is False


def test_the_decision_order_of_a_claim(monkeypatch):
    """L0006 2.4 / 4.1: closed -> nothing; claimed -> superseded (same token: nothing); evidence; person; title; kind;
    display; else claim."""
    from modules.flow_gate.graph_workflow.services import node_title_ai as ai

    job = {"flow_id": FA, "node_key": "n", "node": {"node_type": "document", "doc_type": "T", "label": "작업"}}
    monkeypatch.setattr(ai, "prior_ai_start", lambda *a: None)
    monkeypatch.setattr(ai, "display_is_default", lambda *a, **k: True)
    st = {"opportunity_state": "unused", "claimed_token_id": None, "human_edited": 0, "title": None}
    assert ai._decide(job, "t1", {**st, "opportunity_state": "closed"}) == (False, None)
    assert ai._decide(job, "t1", {**st, "opportunity_state": "claimed", "claimed_token_id": "t1"}) == (False, None)
    assert ai._decide(job, "t2", {**st, "opportunity_state": "claimed", "claimed_token_id": "t1"}) == (
        False, "superseded_start")
    assert ai._decide(job, "t1", {**st, "human_edited": 1}) == (False, "human_edited")
    assert ai._decide(job, "t1", {**st, "title": "AI"}) == (False, "not_target_title")
    assert ai._decide(job, "t1", None) == (True, None)
    monkeypatch.setattr(ai, "prior_ai_start", lambda *a: "L6")
    assert ai._decide(job, "t1", {**st, "human_edited": 1}) == (False, "prior_evidence_legacy")   # before the person
    monkeypatch.setattr(ai, "prior_ai_start", lambda *a: "E1")
    assert ai._decide(job, "t1", None) == (False, "prior_evidence")


# ── persistent evidence (L0006 2.3, V-06, V-07, V-21 ~ V-23, question #441 o1) ──

class _Tokens:
    """The tokens / ai_invoke_runs rows ``_classify`` reads."""

    def __init__(self, tokens, runs):
        self.tokens, self.runs = tokens, runs

    def _fetch_one(self, sql, params):
        if "FROM tokens" in sql:
            return {"ai_run_id": self.tokens[params[0]]} if params[0] in self.tokens else None
        return {"run_id": "r"} if (params[0], params[1]) in self.runs else None

    def table_exists(self, name):
        return name == "ai_invoke_runs"


def _mapping(token, kind=None, at="2026-10-17T00:00:00.000Z", run="run_1", revision=1):
    return {"run_id": run, "revision_no": revision, "node_key": "n", "token_id": token, "mapped_at": at, "map_kind": kind}


@pytest.mark.parametrize("case,rows,tokens,runs,assignee,rule", [
    ("E1", [_mapping("t", "ai_start")], {}, set(), "ai", "E1"),
    ("E0 x4 + attempt", [_mapping(f"d{i}", "dispatch", f"2026-10-17T00:0{i}") for i in range(4)]
     + [_mapping("rv", "attempt", "2026-10-17T00:09")], {}, set(), "ai", None),
    ("a L2", [_mapping("d", None, "1"), _mapping("x", None, "2")], {"x": "air_9"}, set(), "ai", "L2"),
    ("b L3", [_mapping("d", None, "1"), _mapping("x", None, "2")], {}, {("x", FA)}, "ai", "L3"),
    ("c L4", [_mapping("d", None, "1"), _mapping("x", None, "2")], {"x": None}, set(), "ai", None),
    ("d L5", [_mapping("x", None, "1")], {}, set(), "ai", None),
    ("e L1", [_mapping("d", None, "1"), _mapping("x", None, "2"), _mapping("y", None, "3")], {}, set(), "human", None),
    ("f L6", [_mapping("d", None, "1"), _mapping("x", None, "2")], {}, set(), "ai", "L6"),
    ("f L6 then E1 elsewhere", [_mapping("d", None, "1"), _mapping("x", None, "2"),
                                _mapping("t", "ai_start", "3", run="run_2")], {}, set(), "ai", "E1"),
    ("the start itself", [_mapping("me", "ai_start")], {}, set(), "ai", None),
])
def test_legacy_mappings_are_judged_by_l1_to_l6(monkeypatch, case, rows, tokens, runs, assignee, rule):
    from modules.flow_gate.graph_workflow.services import node_title_ai as ai

    monkeypatch.setattr(ai, "_mappings", lambda flow_id, node_key: [dict(r) for r in rows])
    monkeypatch.setattr(ai, "get_store", lambda: _Tokens(tokens, runs))
    monkeypatch.setattr(ai.definition_store, "revision", lambda flow, number: {
        "nodes": [{"node_key": "n", "assignee_kind": assignee}]})
    assert ai.prior_ai_start(FA, "n", "me") == rule, case
    if rule:
        assert ai.REASON_OF_RULE[rule] == ("prior_evidence_legacy" if rule == "L6" else "prior_evidence")


# ── candidates (V-14, V-P-23, V-P-26 (c)(d)(d2)) ─────────────────────────────

@pytest.mark.parametrize("candidate,reason,title", [
    (ABSENT, "no_candidate", None), (None, "no_candidate", None), (12345, "invalid_candidate", None),
    ({"title": GOOD}, "invalid_candidate", None), (["x"], "invalid_candidate", None), ("", "invalid_candidate", None),
    (" \t\n ", "invalid_candidate", None), ("가" * 31, "invalid_candidate", None),
    ("가" * 201, "invalid_candidate", None), ("가" * 30, "applied", "가" * 30),
    (unicodedata.normalize("NFD", "한글 제목"), "applied", "한글 제목"), ("조사", "applied", "조사"),
])
def test_each_candidate_closes_the_opportunity_and_the_registration_stands(env, decided, monkeypatch, candidate,
                                                                            reason, title):
    key, job = _ai_card(env)
    [issued] = _issue(monkeypatch, job)
    _register(job["target_doc_id"], issued["token_id"], candidate)
    st = _state(env, key)
    assert (st["opportunity_state"], st["close_reason"], st["title"], st["version"]) == ("closed", reason, title, 2)
    assert st["title_source"] == ("ai" if title else "none")
    assert len(title_events(env)) == (1 if title else 0)                              # V-P-22
    assert _consumed(env, issued["token_id"])                                          # the document is registered


def test_another_token_s_registration_closes_and_the_late_candidate_is_ignored(env, decided, monkeypatch):
    """V-09 / L0006 4.4: a person registers the result with the dispatch token while the AI holds the claim."""
    key, job = _ai_card(env)
    [issued] = _issue(monkeypatch, job)
    person = _doc(FA, 80, "T")
    _register(person, job["token"]["token_id"], GOOD, action="new")
    assert (_state(env, key)["close_reason"], _state(env, key)["title"]) == ("other_registration", None)
    _register(job["target_doc_id"], issued["token_id"], GOOD)
    assert _state(env, key)["close_reason"] == "other_registration" and title_events(env) == []


def test_a_registration_of_no_graph_token_or_in_a_transaction_does_nothing(env, decided, monkeypatch):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services import node_title_ai

    key, job = _ai_card(env)
    [issued] = _issue(monkeypatch, job)
    node_title_ai.after_registration_title(FA, "doc", "tok_not_mapped", GOOD, "new")        # Inspector AI, review
    with get_store().transaction():
        node_title_ai.after_registration_title(FA, "doc", issued["token_id"], GOOD, "edit")   # V-19 guard
    assert _state(env, key)["opportunity_state"] == "claimed"


@pytest.mark.parametrize("change,reason", [("dispose", "flow_disposed"), ("gone", "node_gone"),
                                           ("shown", "source_changed"), ("person", "human_edited")])
def test_the_apply_rechecks_the_card_in_the_guard(env, decided, monkeypatch, change, reason):
    """L0006 2.6 steps 4~7 (V-09, V-28): what changed between the claim and the candidate closes it, title unchanged."""
    from modules.flow_gate.graph_workflow.services import node_title_ai

    key, job = _ai_card(env)
    [issued] = _issue(monkeypatch, job)
    if change == "dispose":
        _doc(FA, 99, "DC")
    elif change == "gone":
        monkeypatch.setattr(node_title_ai, "_definition_keys", lambda flow_id: set())
    elif change == "shown":
        monkeypatch.setattr(node_title_ai, "display_is_default", lambda *a, **k: False)
    else:
        _sql(env["db"], "UPDATE graph_node_title_states SET human_edited = 1, title = 'P', title_source = 'human' "
                        "WHERE node_key = ?", (key,))
    _register(job["target_doc_id"], issued["token_id"], GOOD)
    st = _state(env, key)
    assert (st["opportunity_state"], st["close_reason"]) == ("closed", reason) and st["title"] != GOOD
    assert title_events(env) == []


@pytest.mark.parametrize("where", ["disposal_read", "apply_write", "guard"])
def test_a_failing_apply_keeps_the_claim_and_the_registration(env, decided, monkeypatch, where):
    """V-15 / V-27: an exception anywhere in the apply is logged only; the opportunity stays claimed (used)."""
    from modules.flow_gate.graph_workflow.services import node_title_ai
    from modules.flow_gate.graph_workflow.store import title_store

    key, job = _ai_card(env)
    [issued] = _issue(monkeypatch, job)
    target = {"disposal_read": (node_title_ai, "flow_disposed_strict"), "apply_write": (title_store, "apply_ai"),
              "guard": (node_title_ai, "with_flow_guard")}[where]
    monkeypatch.setattr(*target, lambda *a, **k: 1 / 0)
    _register(job["target_doc_id"], issued["token_id"], GOOD)                      # never raises
    assert _state(env, key)["opportunity_state"] == "claimed" and _consumed(env, issued["token_id"])


def test_a_person_and_the_ai_racing_end_on_the_person_s_title(env, decided, monkeypatch):
    """V-12 (b) / V-18: the lock released (a person may save) while the AI's candidate lands: whichever commits
    first, the person's title stays and the version only grows."""
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    key, job = _ai_card(env)
    [issued] = _issue(monkeypatch, job)
    assert put_title(env, FA, key, {"title": "잠금 중"}, expect=409)["error"]["code"] == "node_title_locked"   # V-16
    monkeypatch.setattr(card_settings_ports, "node_running", lambda flow_id, node_key: False)
    barrier = threading.Barrier(2)
    errors = []

    def person():
        barrier.wait()
        try:
            put_title(env, FA, key, {"title": "사람 제목"})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def ai():
        barrier.wait()
        _register(job["target_doc_id"], issued["token_id"], GOOD)

    threads = [threading.Thread(target=person), threading.Thread(target=ai)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    st = _state(env, key)
    assert errors == [] and (st["title"], st["title_source"], st["human_edited"]) == ("사람 제목", "human", 1)
    assert st["version"] in (2, 3) and st["close_reason"] in ("human_edited", "applied")
    [_issue_after] = _issue(monkeypatch, job, run_ids=("air_2",))
    assert _asks_nothing(_issue_after) and _state(env, key)["title"] == "사람 제목"


# ── the token map kind (DB0007 §2.2, M-3 ~ M-6, M-8) ────────────────────────

def test_map_token_needs_its_kind(env):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.store import run_store

    with pytest.raises(TypeError):
        run_store.map_token("tok_x", "run_x", "n")                                     # M-5: no default
    with pytest.raises(ValueError):
        with get_store().transaction():
            run_store.map_token("tok_x", "run_x", "n", map_kind="legacy")
    assert _sql(env["db"], "SELECT * FROM graph_run_token_map WHERE token_id = 'tok_x'") == []
    assert run_store.MAP_KINDS == ("dispatch", "ai_start", "attempt")


def test_one_insert_and_three_callers_write_the_token_map():
    """M-6: a new mapping path fails here until it names its kind (DB0007 §2.2.1)."""
    inserts, callers = [], {}
    for root in (_SERVER_DIR / "modules", _SERVER_DIR / "flowgate"):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(_SERVER_DIR).as_posix()
            if "INSERT INTO graph_run_token_map" in text:
                inserts.append(rel)
            for line in text.splitlines():
                if "run_store.map_token(" in line:
                    callers.setdefault(rel, []).append(line.strip())
    assert inserts == ["modules/flow_gate/graph_workflow/store/run_store.py"]
    assert {rel: [re.search(r"map_kind=run_store\.(\w+)", c).group(1) for c in calls]
            for rel, calls in callers.items()} == {
        "modules/flow_gate/graph_workflow/services/node_dispatch.py": ["MAP_DISPATCH"],
        "modules/flow_gate/graph_workflow/services/ai_node_starter.py": ["MAP_AI_START"],
        "flowgate/graph_run/execution.py": ["MAP_ATTEMPT"],
    }
    src = (_SERVER_DIR / "modules" / "flow_gate" / "graph_workflow" / "store" / "run_store.py").read_text(
        encoding="utf-8")
    mark = src[src.index("def mark_token_stage("):src.index("def authoring_token_map(")]
    assert "UPDATE graph_run_token_map SET stage = ?" in mark and "map_kind" not in mark          # never rewritten


def test_migration_154_keeps_legacy_rows_and_closes_the_values():
    """M-8 and the three dialects (DB0007 §3.1): additive, nullable, no backfill, no IF NOT EXISTS."""
    texts = {d: (_MIGRATIONS / d / "154_graph_run_token_map_kind.sql").read_text(encoding="utf-8")
             for d in ("sqlite", "postgres", "mysql")}
    for dialect, sql in texts.items():
        body = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
        assert "ALTER TABLE graph_run_token_map ADD COLUMN map_kind" in body and "IF NOT EXISTS" not in body
        assert "CHECK (map_kind IS NULL OR map_kind IN ('dispatch', 'ai_start', 'attempt'))" in body
        assert not re.search(r"\b(UPDATE|DROP|DELETE)\b", body), dialect
    assert "VARCHAR(16)" in texts["mysql"]
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE graph_run_token_map (token_id TEXT PRIMARY KEY, run_id TEXT, node_key TEXT, stage TEXT, "
                 "attempt_id TEXT, mapped_at TEXT)")
    conn.execute("INSERT INTO graph_run_token_map VALUES ('old', 'run', 'n', 'result', NULL, 't')")
    conn.executescript(texts["sqlite"])
    assert conn.execute("SELECT token_id, map_kind FROM graph_run_token_map").fetchall() == [("old", None)]
    conn.execute("INSERT INTO graph_run_token_map VALUES ('new', 'run', 'n', NULL, NULL, 't', 'ai_start')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO graph_run_token_map VALUES ('bad', 'run', 'n', NULL, NULL, 't', 'legacy')")


@pytest.fixture
def kept_source_hooks(monkeypatch):
    """The attempt fixture registers the execution hooks; restore them after the test."""
    from modules.flow_gate.services import graph_source_hooks

    for attr in ("_handler", "_mutation_handler", "_fence_handler"):
        monkeypatch.setattr(graph_source_hooks, attr, getattr(graph_source_hooks, attr))


def test_an_attempt_token_is_mapped_as_attempt(kept_source_hooks, result_db):
    """M-3 / M-4: the review / rework / reissue tokens of an execution attempt reach the map through the unchanged
    hook ``graph_execution_hooks.call("map_token", exec_id, token_id)``; their stage is computed as before."""
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.services import graph_execution_hooks

    eid = ledger.request(result_db.attempt, "run", "review", {"type": "review", "job": {}})
    graph_execution_hooks.call("map_token", eid, "tok_review")
    [row] = result_db._fetch_all(
        "SELECT token_id, node_key, stage, map_kind FROM graph_run_token_map WHERE token_id = 'tok_review'")
    assert row == {"token_id": "tok_review", "node_key": "node", "stage": "result", "map_kind": "attempt"}


# ── the register tool, the worker, the inbox (V-P-26, V-19) ─────────────────

def test_the_register_tool_offers_a_lenient_node_title_to_new_and_edit_only():
    from modules.flow_gate.services import api_server_tools as tools

    for scope, required in (("new", ["content", "doc_type"]), ("edit", ["content", "edit_reason"])):
        schema = tools.REGISTER_SCHEMAS[scope]
        assert schema["properties"]["node_title"] == {"type": "string", "x-flowgate-lenient": True}
        assert schema["required"] == required and schema["additionalProperties"] is False      # (f)
        for value in (GOOD, 12345, {"a": 1}, None, "", "가" * 300, ["x"]):
            base = {"content": "# x", "doc_type": "N"} if scope == "new" else {"content": "# x", "edit_reason": "worker_self"}
            tools.validate(schema, {**base, "node_title": value})                            # (d)(d2): no 422
    for scope in ("review", "test_run"):
        assert "node_title" not in tools.REGISTER_SCHEMAS[scope]["properties"]
    with pytest.raises(tools.ToolError) as refused:
        tools.validate(tools.REGISTER_SCHEMAS["review"], {"verdict": "pass", "node_title": "검수 제목"})
    assert (refused.value.status, refused.value.reason) == (422, "schema_validation_failed")         # (e)
    with pytest.raises(tools.ToolError):
        tools.validate(tools.REGISTER_SCHEMAS["new"], {"content": 1, "doc_type": "N"})             # others still checked
    assert tools.DESCRIPTIONS["register_document"] == TOOL_DESCRIPTION


def test_the_worker_copies_node_title_as_it_came_for_new_and_edit_only():
    from modules.flow_gate.services.ai_invoke import worker

    context = {"project": "flowgate", "group": FA, "doc": f"{FA}.0001-R"}
    run = {"module": "v02"}
    for value in (GOOD, 12345, None, {"a": 1}):
        new = worker._register_envelope({**context, "action": "new"}, run,
                                         {"doc_type": "N", "title": "t", "content": "c", "node_title": value})
        edit = worker._register_envelope({**context, "action": "edit"}, run,
                                          {"content": "c", "edit_reason": "worker_self", "node_title": value})
        assert new["node_title"] == value and edit["node_title"] == value          # (a)(b)(d2): no reshaping
    assert "node_title" not in worker._register_envelope({**context, "action": "new"}, run,
                                                         {"doc_type": "N", "content": "c"})        # (c)
    for action, payload in (("review", {"verdict": "pass", "node_title": "x"}), ("test_run", {"node_title": "x"})):
        assert "node_title" not in worker._register_envelope({**context, "action": action}, run, payload)
    assert worker._REGISTER_MODEL_FIELDS["new"][-1] == worker._REGISTER_MODEL_FIELDS["edit"][-1] == "node_title"


def _handler_calls(name):
    tree = ast.parse((_SERVER_DIR / "modules" / "flow_gate" / "api" / "inbox_routes.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    consume = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "consume"]
    hooks = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "after_registration"]
    nested = [w for w in ast.walk(fn) if isinstance(w, (ast.With, ast.Try))
              for h in hooks if w.lineno < h.lineno <= (w.end_lineno or 0)]
    return consume, hooks, nested


@pytest.mark.parametrize("name,action", [("_handle_new", "new"), ("_handle_edit", "edit")])
def test_the_inbox_hands_the_candidate_over_after_the_consume_outside_any_transaction(name, action):
    """L0006 2.6 (V-19): after Step 8, outside any transaction or try block, its value never used; ``node_title`` is
    read apart from the document (``body.get``), so it cannot fail the registration."""
    consume, hooks, nested = _handler_calls(name)
    [hook] = hooks
    assert hook.lineno > max(consume) and nested == []
    src = ast.unparse(hook)
    assert src.startswith("node_title_hooks.after_registration(") and "body.get('node_title')" in src
    assert ast.unparse(hook.args[-1]) == repr(action)


def test_the_hook_port_never_raises_and_startup_wires_it(env):
    from modules.flow_gate.services import node_title_hooks

    from flowgate.card_settings import integration

    assert node_title_hooks.registered(node_title_hooks.AFTER_REGISTRATION)           # the fixture's startup wiring
    calls = []
    node_title_hooks.register(node_title_hooks.AFTER_REGISTRATION, lambda *a: calls.append(a) or 1 / 0)
    assert node_title_hooks.after_registration(FA, "d", "t", None, "new") is None and calls == [(FA, "d", "t", None,
                                                                                                    "new")]
    node_title_hooks.after_registration(None, "d", "t", GOOD, "new")                   # no Flow: not called
    assert len(calls) == 1
    integration.unregister()
    assert not node_title_hooks.registered(node_title_hooks.AFTER_REGISTRATION)
    node_title_hooks.after_registration(FA, "d", "t", GOOD, "new")
    integration.register()


# ── the title request wording (P0006 §4.2.6, V-P-24) ────────────────────────

def test_the_six_title_requests_and_their_choice(env):
    from modules.flow_gate.services import node_title_hooks as hooks

    assert hooks.TITLE_REQUEST_CLI == CLI_REQUEST and hooks.TITLE_REQUEST_API == API_REQUEST
    for loc in ("ko", "en", "ja"):
        assert NO_REPEAT[loc] in CLI_REQUEST[loc] and NO_REPEAT[loc] in API_REQUEST[loc]
        assert "inbox" in CLI_REQUEST[loc] and "`register_document`" in API_REQUEST[loc]
        assert hooks.title_request("cli", loc) == CLI_REQUEST[loc]
        assert hooks.title_request("api", loc) == API_REQUEST[loc]
    assert "do not simply repeat the card kind name" in TOOL_DESCRIPTION
    assert hooks.title_request("API", "en-US") == API_REQUEST["en"]
    assert hooks.title_request(None, None) == hooks.title_request("missing", "xx") == CLI_REQUEST["ko"]
    assert hooks.append_title_request("M", "cli") == "M" + SEP + CLI_REQUEST["ko"]
    assert hooks.append_title_request("", None) == CLI_REQUEST["ko"]
    # The request rides the claiming token (kept as its digest) to the provider boundary; nothing else is touched.
    hooks.request_title("raw-ja", "ja")
    assert hooks.provider_prompt("M", "raw-ja", "api") == "M" + SEP + API_REQUEST["ja"]
    assert hooks.provider_prompt("M", "raw-ja", "cli") == "M" + SEP + CLI_REQUEST["ja"]
    for raw in ("raw-other", None, "", 7):
        assert hooks.provider_prompt("M", raw, "api") == "M" and not hooks.title_requested(raw)
    assert "raw-ja" not in repr(hooks._requests)
    for i in range(hooks.REQUESTS_KEPT):
        hooks.request_title(f"raw-{i}")
    assert not hooks.title_requested("raw-ja") and len(hooks._requests) == hooks.REQUESTS_KEPT
