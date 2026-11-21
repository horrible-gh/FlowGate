"""Group 0061 card 1 -- size baseline of every AI-facing prompt and response.

This suite *measures* and pins; it changes no prompt and no response. It is the frame the
later cards of the group (help split, API response slimming, block registry, selection table,
final limits) shrink against:

  * every prompt builder (requirement P1-P10 plus the six extra builders of design 0004-D
    section 2.1 scope and the auto-title prompt) is built from representative inputs and its
    delivered length (builder text + the "what to do now" block the provider appends) is
    compared with its limit;
  * AI-facing responses (turn list, /help, /help/tools, every help item, per token persona)
    are measured the same way;
  * a scan of the server source fails when a prompt-building function exists that no case
    covers, so a new builder cannot appear without being measured.

Limits live in prompt_size_baseline_0061.json (measured size + 10 %). Lowering a limit after a
reduction card is a one-line diff there; *raising* one shows up in review as the same diff.
A measurement prints one ``SIZE|<key>|<chars>|<limit>`` line per entry (run with -s).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate import template_provision
from modules.flow_gate.api.v1 import help_routes
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import events as db_events
from modules.flow_gate.db import templates as db_templates
from modules.flow_gate.services import (
    invoke_mention_service as chat,
    mention_service,
    message_image_delivery,
    prompt_selection,
    question_policy,
    remote_tool_service,
    terminal_prompt,
    test_command_service,
)
from modules.flow_gate.settings import source_mode_service
from prompt_size_probe_0061 import discover_builders, load_baseline
from test_conversation_query_0351 import DOC_ID as TURN_DOC_ID, _seed as _seed_turns, doc, store  # noqa: F401
from test_prompt_selection_0049 import hooks, mention_context  # noqa: F401

BASE = "http://example.invalid/flowgate/api/v1"
LOC = "ko"
DOC = {"doc_id": "p.m.0001.0002-TR", "project_id": "p", "group_id": "p.m.0001", "type_code": "TR",
       "seq": 2, "title": "Result", "module": "m"}
TOKEN = {"project": "p", "group_id": "p.m.0001", "scratch_dir": "scratch"}
# A fixed stand-in for the user's own question policy: its size is a constant of every
# measurement and must not move with whatever a developer's database holds.
POLICY_BODY = "\n".join(f"- policy sample line {i:02d}: reversible work is decided by the AI" for i in range(14))

M = "server/modules/flow_gate/"
K_MENTION = M + "services/mention_service.py::"
K_TOKEN = M + "api/token_routes.py::"
K_INVOKE = M + "services/invoke_mention_service.py::"
K_ROUTES = M + "api/v1/ai_invoke_routes.py::"
K_ANSWER = M + "services/q_answer_invoke_service.py::"
K_ORIGIN = M + "services/failure_origin_review_service.py::"
K_RUN = M + "services/test_run_service.py::"
K_REPAIR = M + "services/engine_recipe_service.py::"
K_COPY = M + "workflow/prompt_copy_service.py::"
K_TITLE = "server/flowgate/conversation/auto_title.py::"


class Env:
    """The stubbed world one prompt case builds in (copied from test_terminal_prompt_0049)."""

    def __init__(self, mp, ctx):
        self.mp = mp
        self.ctx = ctx

    def remote(self, flag: bool) -> None:
        self.mp.setattr(mention_service, "_include_remote_source_crud", lambda project: flag)


@pytest.fixture
def env(mention_context, hooks, monkeypatch):  # noqa: F811
    from modules.flow_gate.api import token_routes
    from modules.flow_gate.services import engine_recipe_service as repair
    from modules.flow_gate.services import failure_origin_review_service as origin
    from modules.flow_gate.services import q_answer_invoke_service as qa
    from modules.flow_gate.services import test_run_service as runs

    monkeypatch.setenv("ALLOWED_ORIGIN", "http://localhost")
    monkeypatch.setenv("CONTEXT", "/flowgate")
    monkeypatch.setenv("DB_TYPE", "sqlite")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-testing-only-32c")
    monkeypatch.setattr(question_policy, "text", lambda *a, **kw: POLICY_BODY)
    monkeypatch.setattr(chat.chat_settings_service, "resolve_chat_settings_safe", lambda user: {})
    monkeypatch.setattr(chat.chat_doc_settings_service, "context_window", lambda *a, **kw: {"after_seq": 0, "folded": 0})
    monkeypatch.setattr(message_image_delivery, "image_count", lambda *a, **kw: 0)
    monkeypatch.setattr(qa.db_groups, "get_by_id", lambda group: None)
    monkeypatch.setattr(mention_service.document_service, "get_document", lambda doc_id: None)
    monkeypatch.setattr(token_routes.git_service, "merge_session_owner_args", lambda *a: ("p.m.0001", None))
    monkeypatch.setattr(token_routes.git_service, "review_conversation_brief", lambda *a, **kw: {
        "conversation": [{"role": "human", "message": "why was this line changed?"}]})
    monkeypatch.setattr(token_routes.git_service, "list_conflicts", lambda *a, **kw: {"files": []})
    monkeypatch.setattr(token_routes.git_service, "merge_route_prefix", lambda *a: "/groups/p.m.0001/git/merges/1")
    monkeypatch.setattr(runs, "_read_doc_content_or_empty", lambda doc: "")
    monkeypatch.setattr(runs.test_spec_service, "parse_spec", lambda body: {"cases": []})
    del repair, origin  # imported only so a broken import fails here, not inside a case
    return Env(monkeypatch, mention_context)


# ── prompt cases ─────────────────────────────────────────────────────────────

def _mention(env, *, remote=True, **over):
    env.remote(remote)
    args = dict(env.ctx, locale=LOC)
    args.update(over)
    return mention_service.build_mention(**args)


_NEW = dict(action_scope="new", parent_type="T", head_type="TR", head_status="pending")
_REWORK = dict(
    edit_reason="rejected",
    current_review={"id": 11, "revision_no": 3, "verdict": "issues", "findings": [{"locus": "section 2", "note": "fix the table"}]},
    rejection_target={"rejection_id": "r1", "reason": "the report does not list the changed files"},
)


def _common(env, remote=True):
    env.remote(remote)
    return dict(token_rec=TOKEN, target_doc=DOC, api_base_url=BASE, raw_token="test-token", locale=LOC)


def _chat(env, *, unanswered=False, current_run=False, can_write=False, access="read",
          command_access=None, copy=False):
    env.remote(True)
    env.mp.setattr(prompt_selection, "chat_situation",
                   lambda *a, **kw: prompt_selection.ChatSituation(can_write, unanswered, current_run))
    return chat.build_conversation_mention(
        doc_id="p.m.0001.0003-CH", project="p", module="m", group_name="p.m.0001", raw_token="test-token",
        token_id="test-id", api_base_url=BASE, source_access=access, command_access=command_access,
        command_execution=not copy, locale=LOC)


def _token_path(env):
    from modules.flow_gate.api import token_routes as tr
    parent = {"doc_id": DOC["doc_id"], "type_code": "TR", "seq": 2, "title": "Result", "module": "m",
              "project_id": "p", "group_id": "p.m.0001", "revision_no": 1}
    env.mp.setattr(tr.db_documents, "get_by_id", lambda doc_id: dict(parent))
    env.mp.setattr(tr.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: None)
    env.mp.setattr(tr.db_documents, "get_group_max_seq", lambda group: 5)
    env.mp.setattr(tr.db_documents, "fetch_recent_group_docs", lambda **kw: [
        {"doc_id": f"p.m.0001.000{i}-D", "doc_type": "D", "seq": i, "title": f"doc {i}", "status": "open"}
        for i in (4, 3, 2)])
    env.remote(True)
    return tr._build_mention_for_token(
        doc_ref=DOC["doc_id"], group_id="p.m.0001", project_id="p", scratch_dir="scratch",
        raw_token="test-token", api_base_url=BASE, action_scope="new", locale=LOC)


def _from_token_rec(env):
    env.remote(True)
    parent = {"doc_id": DOC["doc_id"], "type_code": "TR", "seq": 2, "title": "Result", "module": "m"}
    return mention_service.build_mention_from_token_rec(
        token_rec=TOKEN, head_type="N", head_status="pending", parent_doc=parent, api_base_url=BASE,
        raw_token="test-token", action_scope="new", locale=LOC)


def _rework_base(env):
    return _mention(env, **_REWORK)


def _rework_invoke(env):
    from modules.flow_gate.api import token_routes
    base = _rework_base(env)
    env.mp.setattr(db_documents, "get_by_id", lambda doc_id: {
        "rejection_history": [{"rejection_id": "r1", "reason": "the report does not list the changed files"}],
        "rejection_reason": "the report does not list the changed files"})
    env.mp.setattr(token_routes, "_build_mention_for_token", lambda **kw: base)
    env.mp.setattr(chat, "_resolve_rework_pre_instruction", lambda doc_id: None)
    return chat.build_rework_mention(
        doc_id=DOC["doc_id"], group_id="p.m.0001", project_id="p", scratch_dir="scratch",
        raw_token="test-token", api_base_url=BASE, locale=LOC)


def _rework_route(env):
    from modules.flow_gate.api.v1 import ai_invoke_routes as routes
    base = _rework_base(env)
    env.mp.setattr(routes.db_docs, "get_by_id", lambda doc_id: {"rejection_history": [], "rejection_reason": ""})
    env.mp.setattr(routes.ai_invoke_service, "previous_timeout_handoff", lambda group, doc: None)
    return routes.build_rework_mention(
        base=base, doc_ref=DOC["doc_id"], group_id="p.m.0001",
        reject_reason="the report does not list the changed files", locale=LOC)


def _answer(env, options=False):
    from modules.flow_gate.services import q_answer_invoke_service as qa
    env.remote(True)
    item = {"id": 42, "seq": 1, "title": "Which option?", "body": "Which of the two should we pick?\nGive a reason.",
            "options": [{"id": "o1", "label": "A"}, {"id": "o2", "label": "B"}] if options else []}
    return qa.build_answer_mention(doc=DOC, item=item, raw_token="test-token", scratch_dir="scratch", api_base_url=BASE)


def _work_plan(env):
    return mention_service.build_work_plan_fill_mention(
        **_common(env), body={"steps": [], "quantities": {}}, scope={})


def _sequence(env):
    items = [{"type": "D", "label": "design", "status": "done"},
             {"type": "T", "label": "instruction", "status": "in_progress"},
             {"type": "TR", "label": "report", "status": "pending"}]
    return mention_service.build_sequence_edit_mention(**_common(env), sequence_items=items)


def _failure_origin(env):
    from modules.flow_gate.services import failure_origin_review_service as origin
    items = [{"result": "fail", "case_no": 1, "case_title": "case", "assert_mode": "exit", "expect": "0",
              "actual": "1", "exit_code": 1, "output_tail": "traceback line\n" * 20}]
    return origin.build_failure_origin_mention(
        doc=DOC, run={"run_id": "r", "revision_no": 2}, items=items, api_base_url=BASE, raw_token="test-token")


def _next_step(env):
    from modules.flow_gate.workflow import prompt_copy_service as copy_service
    related = [dict(DOC, doc_id=f"p.m.0001.000{i}-D", type_code="D", seq=i, status="open") for i in (3, 4)]
    env.mp.setattr(copy_service.db_docs, "get_by_id", lambda doc_id: DOC)
    env.mp.setattr(copy_service.db_docs, "list_documents", lambda **kw: [DOC] + related)
    env.mp.setattr(copy_service.db_groups, "get_by_id", lambda group_id: None)
    env.mp.setattr(copy_service, "_get_v_report_path", lambda doc_id: None)
    env.mp.setattr(copy_service, "_append_qa_block", lambda *a, **kw: None)
    env.mp.setattr(copy_service, "log_prompt_copied", lambda **kw: None)
    return copy_service.build_prompt(doc_id=DOC["doc_id"], actor_user_id="test-user", locale=LOC)["prompt_text"]


def _conflict(env, with_chunk=False):
    from modules.flow_gate.api import token_routes
    if with_chunk:
        content = "line\n<<<<<<< ours\nA\n||||||| base\nO\n=======\nB\n>>>>>>> theirs\nline\n"
        env.mp.setattr(token_routes.git_service, "list_conflicts", lambda *a, **kw: {
            "files": [{"path": "a.py", "conflict_count": 1, "content": content}]})
    return token_routes._build_conflict_mention(
        group_id="p.m.0001", project_id="p", merge_id=1, scratch_dir="scratch", raw_token="test-token",
        api_base_url=BASE, locale=LOC)


def _merge_review(env):
    from modules.flow_gate.api import token_routes
    return token_routes._build_review_conversation_mention(
        group_id="p.m.0001", project_id="p", merge_id=1, raw_token="test-token", api_base_url=BASE, locale=LOC)


def _repair(env):
    from modules.flow_gate.services import engine_recipe_service as repair
    return repair._build_repair_mention(DOC, {"run_id": "r", "error": "setup_failed"}, [], 1, "test-token", None)


def _test_run(env, continuous=False, specification=False):
    from modules.flow_gate.services import test_run_service as runs
    return runs._build_test_run_mention(doc=DOC, api_base_url=BASE, raw_token="test-token", locale=LOC,
                                        continuous=continuous, specification=specification)


def _auto_title(env):
    from flowgate.conversation import auto_title
    return auto_title.build_prompt("please summarize the open questions of this flow " * 3)


# (case id, builders covered, representative for the P1-P10 sum, builder of the text)
CASES = [
    ("P1.new_remote", (K_MENTION + "build_mention",), True, lambda e: _mention(e, **_NEW)),
    ("P1.new_local", (), False, lambda e: _mention(e, remote=False, **_NEW)),
    ("P1.edit_remote", (), False, lambda e: _mention(e)),
    ("P1.edit_local", (), False, lambda e: _mention(e, remote=False)),
    ("P1.first_write_remote", (), False, lambda e: _mention(e, first_write=True)),
    ("P1.rework_remote", (), False, _rework_base),
    ("P1.preflight_remote", (), False, lambda e: _mention(e, **dict(_NEW, head_type="D", continuous=True,
                                                                     continuous_review_mode=True))),
    ("P1.from_token_rec", (K_MENTION + "build_mention_from_token_rec",), False, _from_token_rec),
    ("P1.token_path", (K_TOKEN + "_build_mention_for_token", K_ROUTES + "_standard_mention"), False, _token_path),
    ("P2.review_remote", (K_MENTION + "build_review_mention",), True,
     lambda e: mention_service.build_review_mention(**_common(e))),
    ("P2.review_local", (), False, lambda e: mention_service.build_review_mention(**_common(e, remote=False))),
    ("P3.rework_invoke", (K_INVOKE + "build_rework_mention",), True, _rework_invoke),
    ("P3.rework_route", (K_ROUTES + "build_rework_mention",), False, _rework_route),
    ("P4.answer", (K_ANSWER + "build_answer_mention",), True, _answer),
    ("P4.answer_options", (), False, lambda e: _answer(e, options=True)),
    ("P5.decide_remote", (K_MENTION + "build_workflow_decision_mention",), True,
     lambda e: mention_service.build_workflow_decision_mention(**_common(e))),
    ("P5.decide_local", (), False, lambda e: mention_service.build_workflow_decision_mention(**_common(e, remote=False))),
    ("P5.decide_continuous", (), False,
     lambda e: mention_service.build_workflow_decision_mention(**_common(e), continuous=True)),
    ("P5.decide_continuous_review", (), False,
     lambda e: mention_service.build_workflow_decision_mention(**_common(e), continuous=True,
                                                               continuous_review_mode=True)),
    ("P6.sequence_edit", (K_MENTION + "build_sequence_edit_mention",), True, _sequence),
    ("P7.work_plan_fill", (K_MENTION + "build_work_plan_fill_mention",), True, _work_plan),
    ("P8.failure_origin", (K_ORIGIN + "build_failure_origin_mention",), True, _failure_origin),
    ("P9.chat_read", (K_INVOKE + "build_conversation_mention",), True, lambda e: _chat(e)),
    ("P9.chat_read_unanswered_q", (), False, lambda e: _chat(e, unanswered=True, current_run=True)),
    ("P9.chat_read_copy", (), False, lambda e: _chat(e, copy=True)),
    ("P9.chat_execute", (), False, lambda e: _chat(e, command_access="execute")),
    ("P9.chat_edit", (), False, lambda e: _chat(e, can_write=True, access="read_write")),
    ("P9.chat_edit_unanswered_q", (), False,
     lambda e: _chat(e, can_write=True, access="read_write", unanswered=True, current_run=True)),
    ("P10.next_step_copy", (K_COPY + "build_prompt",), True, _next_step),
    ("X.merge_review_conversation", (K_TOKEN + "_build_review_conversation_mention",), False, _merge_review),
    ("X.conflict_empty", (K_TOKEN + "_build_conflict_mention",), False, _conflict),
    ("X.conflict_one_chunk", (), False, lambda e: _conflict(e, with_chunk=True)),
    ("X.repair", (K_REPAIR + "_build_repair_mention",), False, _repair),
    ("X.test_run", (K_RUN + "_build_test_run_mention",), False, _test_run),
    ("X.test_run_continuous", (), False, lambda e: _test_run(e, continuous=True)),
    ("X.spec_result", (K_RUN + "_build_spec_result_mention",), False, lambda e: _test_run(e, specification=True)),
    ("X.auto_title", (K_TITLE + "build_prompt",), False, _auto_title),
]
CASE_IDS = [case[0] for case in CASES]
REGISTRY = {builder: case[0] for case in CASES for builder in case[1]}


def delivered_chars(text: str) -> int:
    """Length the provider receives: the builder text plus its final 'what to do now' block."""
    _body, spec = terminal_prompt.split(text)
    return len(terminal_prompt.finalize(text)) if spec is not None else len(text)


def _report(key: str, measured: int, limit) -> None:
    print(f"SIZE|{key}|{measured}|{limit}")


def _check(section: str, key: str, measured: int) -> None:
    limit = load_baseline().get(section, {}).get(key)
    _report(key, measured, limit)
    assert limit is not None, f"no baseline limit for {key!r} ({measured} chars); add it to prompt_size_baseline_0061.json"
    assert measured <= limit, f"{key}: {measured} chars exceeds the limit {limit}"


# ── prompt tests ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case_id", CASE_IDS)
def test_prompt_size_within_baseline(env, case_id):
    build = next(case[3] for case in CASES if case[0] == case_id)
    text = build(env)
    assert isinstance(text, str) and text, f"{case_id} produced no prompt"
    _check("prompts", case_id, delivered_chars(text))


def test_representative_sum_within_baseline(env):
    total = 0
    for case_id, _builders, representative, build in CASES:
        if representative:
            total += delivered_chars(build(env))
    _check("prompts", "SUM.P1-P10", total)


def test_every_prompt_builder_is_measured():
    found = discover_builders()
    unmeasured = sorted(found - set(REGISTRY))
    assert not unmeasured, (
        "prompt builders without a measured case (add a case to CASES in "
        f"test_prompt_size_baseline_0061.py): {unmeasured}")
    stale = sorted(set(REGISTRY) - found)
    assert not stale, f"measured builders that no longer exist in the source (update CASES): {stale}"


def test_every_case_belongs_to_a_known_family():
    assert len(set(CASE_IDS)) == len(CASE_IDS)
    for prefix in [f"P{n}." for n in range(1, 11)]:
        assert any(case_id.startswith(prefix) for case_id in CASE_IDS), prefix
    assert sum(1 for case in CASES if case[2]) == 10


# ── response tests ───────────────────────────────────────────────────────────

_TYPE_ROWS = [
    {"type_code": "R", "type_name": "R", "series": "requirement", "description": "d", "is_active": 1},
    {"type_code": "D", "type_name": "D", "series": "design", "description": "d", "is_active": 1},
    {"type_code": "TR", "type_name": "TR", "series": "task", "description": "d", "is_active": 1},
]
_GROUP_DOCS = [{"doc_id": "p.m.0001.0001-R", "type_code": "R", "title": "r", "status": "open", "seq": 1}]

# persona -> (token fields, workflow step type)
HELP_PERSONAS = {
    "chat_read": (dict(action_scope="chat", source_access="read"), "CH"),
    "chat_edit": (dict(action_scope="chat", source_access="read_write"), "CH"),
    "edit_TR": (dict(action_scope="edit"), "TR"),
    "review_TR": (dict(action_scope="review"), "TR"),
    "new_T": (dict(action_scope="new"), "T"),
}


def _help_client(monkeypatch, token_rec, step_type):
    monkeypatch.setattr(help_routes, "verify_bearer", lambda _request: token_rec)
    monkeypatch.setattr(help_routes, "_record_help_tools_event", lambda *a, **kw: None)
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _p: "remote")
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _rec: (step_type, False))
    monkeypatch.setattr(db_templates, "list_document_types", lambda project_id=None, series=None, locale="ko": [
        row for row in _TYPE_ROWS if series is None or row["series"] == series])
    monkeypatch.setattr(db_documents, "get_documents_by_group_id", lambda _gid: list(_GROUP_DOCS))
    monkeypatch.setattr(db_documents, "get_by_id", lambda doc_id: {
        "doc_id": doc_id, "type_code": step_type, "target_id": "p.m.0001.0001-R"})
    monkeypatch.setattr(template_provision, "is_design_type", lambda code: code in ("D", "P", "L", "DB"))
    monkeypatch.setattr(test_command_service, "build_verified_commands_block", lambda _project: "")
    monkeypatch.setattr(db_events, "insert_event", lambda **kw: None)
    monkeypatch.setattr(question_policy, "text", lambda *a, **kw: POLICY_BODY)
    # tr_self_check (TR author personas) reads the card's remaining Self-check allowance.
    from modules.flow_gate.services import tr_self_check_quota as quota
    monkeypatch.setattr(quota, "resolve", lambda token, target=None: {
        "stage_key": "k", "max_runs": 10, "run_timeout_sec": 60})
    monkeypatch.setattr(quota.runs, "verify_used", lambda key: 0)
    app = FastAPI()
    app.include_router(help_routes.router)
    return TestClient(app, headers={"Authorization": "Bearer help-token-0061"})


def _measure_help(monkeypatch, persona: str) -> dict[str, int]:
    fields, step_type = HELP_PERSONAS[persona]
    token = {"project": "p", "group_id": "p.m.0001", "doc_ref": "p.m.0001.0004-TR",
             "scratch_dir": "scratch", **fields}
    client = _help_client(monkeypatch, token, step_type)
    sizes: dict[str, int] = {}
    index = client.get("/api/v1/help")
    assert index.status_code == 200, index.text[:300]
    sizes[f"help_index:{persona}"] = len(index.text)
    tools = client.get("/api/v1/help/tools")
    assert tools.status_code == 200, tools.text[:300]
    sizes[f"help_tools:{persona}"] = len(tools.text)
    for item in index.json()["items"]:
        name = item["name"]
        response = client.get(f"/api/v1/help/items/{name}")
        assert response.status_code == 200, (name, response.text[:300])
        sizes[f"help_item:{persona}:{name}"] = len(response.text)
        if response.json().get("form") == "children":
            for child in response.json().get("children", []):
                leaf = client.get(f"/api/v1/help/items/{name}/{child['name']}")
                assert leaf.status_code == 200, (name, child["name"], leaf.text[:300])
                sizes[f"help_item:{persona}:{name}/{child['name']}"] = len(leaf.text)
    return sizes


@pytest.mark.parametrize("persona", sorted(HELP_PERSONAS))
def test_help_response_sizes_within_baseline(monkeypatch, persona):
    sizes = _measure_help(monkeypatch, persona)
    problems = []
    for key, measured in sorted(sizes.items()):
        limit = load_baseline().get("responses", {}).get(key)
        _report(key, measured, limit)
        if limit is None:
            problems.append(f"no baseline limit for {key} ({measured})")
        elif measured > limit:
            problems.append(f"{key}: {measured} > {limit}")
    assert not problems, "\n".join(problems)
    gone = [key for key in load_baseline().get("responses", {})
            if key.split(":")[1] == persona and key.startswith("help") and key not in sizes]
    assert not gone, f"baseline entries without a measured response: {gone}"


def _worker_token():
    return {"token_id": "tok_1", "provider_id": "cx_opus", "project": "flowgate"}


def _wire(page: dict) -> str:
    return json.dumps(page, ensure_ascii=False, separators=(",", ":"))


def test_turn_list_response_size_within_baseline(store, doc):  # noqa: F811
    from modules.flow_gate.services import conversation_query_service as query
    from modules.flow_gate.services import conversation_turn_service as append_service

    _seed_turns(6, body="hello, this is a sample turn body")
    actor = {"kind": "worker", "token": _worker_token()}
    with patch.object(append_service, "_provider_row", return_value={"name": "Claude Opus 5"}):
        first = query.list_turns(doc_id=TURN_DOC_ID, actor=actor, after_seq=0, include_head=True, limit=3)
        nxt = query.list_turns(doc_id=TURN_DOC_ID, actor=actor, after_seq=3, limit=3)
    measured = {}
    for key, page in (("turns:worker_first_page", first), ("turns:worker_next_page", nxt)):
        total = len(_wire(page))
        measured[key] = total
        measured[key + ":body_excluded"] = total - sum(len(turn.get("body") or "") for turn in page["turns"])
    limits = load_baseline().get("responses", {})
    for key, value in measured.items():
        _report(key, value, limits.get(key))
    for key, value in measured.items():
        if key in limits:
            assert value <= limits[key], f"{key}: {value} chars exceeds the limit {limits[key]}"
    # A page that is not the entry call carries no head, participants or read boundary.
    assert measured["turns:worker_next_page:body_excluded"] < measured["turns:worker_first_page:body_excluded"]
    missing = [key for key in measured if key not in limits and not key.startswith("turns:worker_next_page")]
    assert not missing, f"no baseline limit for {missing}"
