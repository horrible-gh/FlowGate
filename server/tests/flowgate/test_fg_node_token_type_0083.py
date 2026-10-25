"""flowgate.v02.0083 T#2 (0015-WP T#2) — the type a Graph node's token writes, and the inbox refusing another type.

The reported case (symptoms 2 and 4, CH 0014 turn 4 §2 / turn 6 B·C): the two judgements differed.
``card_settings.settings.token_step_type`` answered None when the card changes nothing (``result_type`` None) and the
source tools read that None as "nothing recorded" and took the token's ``doc_ref`` — the Flow's root R. The result
binder (``result_binder.result_type``) reads the same None as the node's own type (``graph_model.run_doc_type``). So a WP
card's AI token was judged an R writer (``design_template/WP`` hidden, 403), its AI registered an R, the inbox took it
(a Graph Flow has no v0.1 sequence, so the Step 5.8 head check never ran), the binder left it unbound (another type) and
the Canvas drew it as a free document node; the WP stayed open with no result.

Covered here, on the card settings suites' real SQLite DB, routes and fixtures:

(a) every card on that None path now answers its node's own type, the binder's — WP (work AI), TS (run AI), an omitted
    D / P / L / DB / T2 instruction, a card run opened before migration 148 (``one_stage``), a person's node (T, WP and a
    Custom card, the last one a path the work instruction did not list) — and the cards whose card changes the type
    keep their value: N / T omitted (NR / TR), a run card in its instruction stage (the instruction type) and in its
    result stage (the report), a Custom card (T / TR); a gate has no type;
(b) the real HTTP ``POST /inbox`` with a node's AI token: another type is refused 409 (the Step 5.8 copy) before
    numbering / storage — no document, no number, the token not consumed — and a dry-run is refused the same; M / CH
    pass; the node's own type registers as before (WP: bound to the card's plan line);
(c) the help: a WP card's AI token reads ``design_template/WP`` (200) and its help context names WP.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _h, _sql, _start, db_path, decided, env, edge, node, put_draft, setting,
)
from test_fg_card_settings_review import _admin_and_reviewer, _node
from test_fg_card_approval_0056 import LEE, card_approval_port  # noqa: F401 — card_approval_port: autouse
from test_fg_wp_ai_run_0083 import (
    OPUS, ROOT, _consumed, _grant_document_create, _opus, _plan_line, _start_ai_run, _wp_json,
)

OMIT = (("instruction", "omit"),)
MISMATCH_KO = "이 그룹의 다음 단계는 {expected}입니다. 받은 타입 {submitted}은 받을 수 없습니다."


# ── helpers ──────────────────────────────────────────────────────────────────

def _card(ref, kind, assignee):
    if kind == "custom":
        return {"node_key": None, "client_key": ref, "node_type": "custom", "doc_type": None,
                "palette_item_id": "custom.1", "label": "커스텀 카드", "assignee": assignee}
    if kind == "gate":
        return node(ref, None, node_type="gate")
    return node(ref, kind, assignee=assignee)


def _flow(env, kind, assignee, settings=()):
    """R -> one card, confirmed as revision 1, its settings stored; -> the card's key."""
    body = put_draft(env, FA, 0, [node("tmp_r", "R"), _card("tmp_c", kind, assignee)],
                     [edge("tmp_e", "tmp_r", "tmp_c")])
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    key = body["key_map"]["tmp_c"]
    for field, value in settings:
        setting(env, FA, key, field, value)
    return key


def _token_rec(env, key):
    """The node's dispatch token as the source tools and the inbox read it (its ``tokens`` row)."""
    token_id = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid",
                    (key,))[0]["token_id"]
    return _sql(env["db"], "SELECT * FROM tokens WHERE token_id = ?", (token_id,))[0]


def _binder_type(key):
    """What the result binder binds a result of the open node as (``result_binder.result_type``), from the run's
    revision node and its node run row (its card run stage)."""
    from modules.flow_gate.graph_workflow.services import result_binder
    from modules.flow_gate.graph_workflow.store import definition_store, run_store

    run = run_store.active_run(FA)
    revision = definition_store.revision(FA, int(run["revision_no"])) or {}
    card = next(n for n in revision["nodes"] if n["node_key"] == key)
    row = next(r for r in run_store.node_runs(run["run_id"]) if r["node_key"] == key)
    return result_binder.result_type(SimpleNamespace(flow_id=FA), card, row)


def _before_this_fix(token_rec):
    """``settings.token_step_type`` as it was (its last return ``result_type`` alone)."""
    from flowgate.card_settings import fields, repository, settings
    from flowgate.card_settings import legacy_adapter as legacy

    flow_id, card = legacy.open_node_of_token(token_rec)
    if card.get("run_stage") == fields.INSTRUCTION_STAGE and settings.uses_ai(flow_id, card):
        written = fields.instruction_stage(card.get("node_type"), card.get("doc_type"),
                                           repository.values(flow_id, card["node_key"]))
        if written:
            return written
    return settings.result_type(flow_id, card)


def _stage(env, key, stage):
    """The node run's card run stage: ``result`` as the move to the result stage records it, None as a card run opened
    before migration 148 (no stage) reads."""
    _sql(env["db"], "UPDATE graph_node_runs SET stage = ? WHERE node_key = ?", (stage, key))


# ── (a) the type each card's token writes ────────────────────────────────────

# (id, card kind, assignee, card settings, stage set after the start ("keep": as dispatched), expected, None before)
CARDS = [
    ("wp_work_ai", "WP", None, (("run_ai", OPUS),), "keep", "WP", True),
    ("ts_run_ai", "TS", None, (("run_ai", "aip_card"),), "keep", "TS", True),
    ("d_omitted", "D", AI, OMIT, "keep", "D", True),
    ("p_omitted", "P", AI, OMIT, "keep", "P", True),
    ("l_omitted", "L", AI, OMIT, "keep", "L", True),
    ("db_omitted", "DB", AI, OMIT, "keep", "DB", True),
    ("t_one_stage_before_148", "T", AI, (), None, "T", True),
    ("person_t", "T", LEE, (), "keep", "T", True),
    ("person_wp", "WP", None, (), "keep", "WP", True),
    ("person_custom", "custom", LEE, (), "keep", "T", True),
    # unchanged: the card changes the type
    ("n_omitted", "N", AI, OMIT, "keep", "NR", False),
    ("t_omitted", "T", AI, OMIT, "keep", "TR", False),
    ("n_instruction_stage", "N", AI, (), "keep", "N", False),
    ("t_instruction_stage", "T", AI, (), "keep", "T", False),
    ("d_instruction_stage", "D", AI, (), "keep", "DS", False),
    ("t_result_stage", "T", AI, (), "result", "TR", False),
    ("d_result_stage", "D", AI, (), "result", "D", False),
    ("custom_ai_instruction_stage", "custom", AI, (), "keep", "T", False),
    ("custom_ai_result_stage", "custom", AI, (), "result", "TR", False),
    ("custom_ai_omitted", "custom", AI, OMIT, "keep", "TR", False),
]


@pytest.mark.parametrize("kind, assignee, values, stage, expected, was_none", [c[1:] for c in CARDS],
                         ids=[c[0] for c in CARDS])
def test_a_node_token_writes_what_the_result_binder_binds(env, decided, kind, assignee, values, stage, expected,
                                                          was_none):
    from flowgate.card_settings import fields, settings
    from modules.flow_gate.services import worker_step_type_hooks

    _opus(env)
    key = _flow(env, kind, assignee, values)
    _start(env)
    assert _node(env, key) == ("open", None)
    if stage != "keep":
        _stage(env, key, stage)
    rec = _token_rec(env, key)
    judged = settings.token_step_type(rec)
    assert judged == expected
    assert worker_step_type_hooks.token_step_type(rec) == expected       # the registered port answers the same
    row_stage = _sql(env["db"], "SELECT stage FROM graph_node_runs WHERE node_key = ?", (key,))[0]["stage"]
    if row_stage == fields.INSTRUCTION_STAGE:
        assert judged != _binder_type(key)                                # the instruction stage writes its instruction
    else:
        assert judged == _binder_type(key)                                # what the binder binds
    before = _before_this_fix(rec)
    assert (before is None) is was_none                                    # the None path the source tools misread
    if not was_none:
        assert before == judged                                            # unchanged


def test_an_omitted_t2_card_answers_t2_as_its_binder_binds(env, monkeypatch):
    """T2 is no Palette type yet (a Graph with it is not confirmed: ``rule_undecided/non_palette_doc_type``), so no
    run opens one today. Its card is judged on the open node row the source tools would read."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import settings
    from modules.flow_gate.graph_workflow.services import result_binder

    body = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", "T2", assignee=AI)],
                     [edge("tmp_e", "tmp_r", "tmp_c")])
    key = body["key_map"]["tmp_c"]
    setting(env, FA, key, "instruction", "omit")
    card = {"node_key": key, "node_type": "document", "doc_type": "T2", "assignee_kind": "ai"}
    monkeypatch.setattr(legacy, "open_node_of_token", lambda _rec: (FA, {**card, "run_stage": "result"}))
    assert settings.result_type(FA, {**card, "run_stage": "result"}) is None          # the None path, before
    assert settings.token_step_type({"token_id": "tok_t2", "group_id": FA}) == "T2"
    assert result_binder.result_type(SimpleNamespace(flow_id=FA), card, {"stage": "result"}) == "T2"


def test_a_gate_token_and_a_token_of_no_open_node_have_no_type(env, decided):
    """A gate hands nothing out (``run_doc_type`` None); a token no open node of the active run holds is unrecorded."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import settings

    assert legacy.run_doc_type("gate", None) is None
    key = _flow(env, "T", LEE)
    _start(env)
    rec = _token_rec(env, key)
    assert settings.token_step_type({**rec, "token_id": "tok_not_mapped"}) is None
    assert settings.token_step_type({**rec, "group_id": "flowgate.v02.0092"}) is None


def test_the_source_tools_judge_a_wp_card_token_a_wp_writer_not_its_root(env, decided):
    """``_worker_token_step_type_result``: the recorded type before the token's ``doc_ref`` (the root R)."""
    from modules.flow_gate.services import remote_tool_service

    _opus(env)
    key = _flow(env, "WP", None, (("run_ai", OPUS),))
    _start(env)
    rec = _token_rec(env, key)
    assert rec["doc_ref"] == ROOT
    assert remote_tool_service._worker_token_step_type_result(rec) == ("WP", False)


# ── (b) the inbox refuses another type from a node token ─────────────────────

def _submit(raw_token, doc_type, content, dry_run=False):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    body = {"project": "flowgate", "module": "v02", "group_name": FA, "action": "new", "prev_doc_id": ROOT,
            "doc_type": doc_type, "title": f"{doc_type} — 노드 토큰", "content": content}
    if dry_run:
        body["dry_run"] = True
    return TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/inbox", headers={"Authorization": f"Bearer {raw_token}"}, json=body)


def _group_state(env):
    docs = {r["doc_id"] for r in _sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ?", (FA,))}
    counters = _sql(env["db"], "SELECT * FROM id_counter")
    return docs, counters


R_BODY = "# 요구사항 — WP 카드 토큰이 쓴 R\n\nWP 카드의 AI가 R로 판정되어 쓴 문서.\n"
N_BODY = "# 조사지시\n\n노드 타입이 아닌 문서.\n"


def _ai_token(env, monkeypatch, kind, values):
    _opus(env)
    _admin_and_reviewer(env)
    _grant_document_create()
    key = _flow(env, kind, None if kind == "WP" else AI, values)
    _start(env)
    [job] = env["jobs"]
    _heads, _call, [issued] = _start_ai_run(monkeypatch, job)
    return key, issued


@pytest.mark.parametrize("submitted, content", [("R", R_BODY), ("N", N_BODY)], ids=["R", "N"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry_run"])
def test_a_wp_card_token_registering_another_type_is_refused_409_before_anything_is_numbered(
        env, decided, monkeypatch, submitted, content, dry_run):
    key, issued = _ai_token(env, monkeypatch, "WP", (("run_ai", OPUS),))
    before = _group_state(env)
    refused = _submit(issued["raw_token"], submitted, content, dry_run=dry_run)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error_message"].startswith(MISMATCH_KO.format(expected="WP", submitted=submitted))
    assert _group_state(env) == before                                    # no document, no number
    assert _consumed(env, issued["token_id"]) is None                    # the token is not spent
    assert _node(env, key) == ("open", None)
    dry_runs = _sql(env["db"], "SELECT dry_run_count FROM tokens WHERE token_id = ?", (issued["token_id"],))
    assert dry_runs[0]["dry_run_count"] in (0, None)                     # a refused dry-run is not counted


def test_after_the_refusal_the_node_s_own_wp_registers_and_binds_as_before(env, decided, monkeypatch):
    key, issued = _ai_token(env, monkeypatch, "WP", (("run_ai", OPUS),))
    docs, _counters = _group_state(env)
    assert _submit(issued["raw_token"], "R", R_BODY).status_code == 409
    preview = _submit(issued["raw_token"], "WP", _wp_json(), dry_run=True)
    assert preview.status_code == 200, preview.text
    assert "node_token_type" in preview.json()["would_register"]["checks_passed"]
    ok = _submit(issued["raw_token"], "WP", _wp_json())
    assert ok.status_code == 201, ok.text
    doc_id = ok.json()["doc_id"]
    seq = _sql(env["db"], "SELECT seq FROM documents WHERE doc_id = ?", (doc_id,))[0]["seq"]
    assert seq == max(int(d.rsplit(".", 1)[1].split("-")[0]) for d in docs) + 1   # the refusals took no number
    assert _node(env, key) == ("review", doc_id) and _plan_line(env, key) == doc_id
    roots = _sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ? AND type_code = 'R'", (FA,))
    assert [r["doc_id"] for r in roots] == [ROOT]                        # no second R (no free document node)


@pytest.mark.parametrize("auto", ["M", "CH"])
def test_a_memo_and_a_chat_pass_the_node_token_check(env, decided, monkeypatch, auto):
    """AUTO_COMPLETE_TYPES (M / CH) only: a dry-run passes every check, the node type check not among them."""
    _key, issued = _ai_token(env, monkeypatch, "WP", (("run_ai", OPUS),))
    preview = _submit(issued["raw_token"], auto, f"# {auto}\n\n노드 토큰의 {auto}\n", dry_run=True)
    assert preview.status_code == 200, preview.text
    assert "node_token_type" not in preview.json()["would_register"]["checks_passed"]


def test_an_omitted_work_card_token_registers_its_report_and_not_the_instruction(env, decided, monkeypatch):
    """A card whose card changes the type (T omitted -> TR) is refused the node's own type T."""
    key, issued = _ai_token(env, monkeypatch, "T", OMIT)
    before = _group_state(env)
    refused = _submit(issued["raw_token"], "T", "# 작업지시\n\n생략된 지시서\n")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error_message"].startswith(MISMATCH_KO.format(expected="TR", submitted="T"))
    assert _group_state(env) == before and _consumed(env, issued["token_id"]) is None
    assert _node(env, key) == ("open", None)


def test_a_token_mapped_to_no_open_node_is_not_checked(env, decided, monkeypatch):
    """Not a node's token any more (its run settled idle): the node type check gives way to the existing ones."""
    from modules.flow_gate.services import worker_step_type_hooks

    key, issued = _ai_token(env, monkeypatch, "WP", (("run_ai", OPUS),))
    rec = _sql(env["db"], "SELECT * FROM tokens WHERE token_id = ?", (issued["token_id"],))[0]
    assert worker_step_type_hooks.token_step_type(rec) == "WP"
    _sql(env["db"], "UPDATE graph_node_runs SET status = 'done' WHERE node_key = ?", (key,))
    assert worker_step_type_hooks.token_step_type(rec) is None


# ── (c) the help of a WP card's token ────────────────────────────────────────

def test_a_wp_card_token_reads_the_wp_template_and_its_help_context_is_wp(env, decided, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import help_routes

    _key, issued = _ai_token(env, monkeypatch, "WP", (("run_ai", OPUS),))
    app = FastAPI()
    app.include_router(help_routes.router)
    client = TestClient(app, raise_server_exceptions=False,
                        headers={"Authorization": f"Bearer {issued['raw_token']}"})
    index = client.get("/api/v1/help")
    assert index.status_code == 200, index.text
    assert index.json()["context"]["doc_type"] == "WP"
    assert "design_template" in [item["name"] for item in index.json()["items"]]
    template = client.get("/api/v1/help/items/design_template/WP")
    assert template.status_code == 200, template.text
