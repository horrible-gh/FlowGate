"""flowgate.v02.0074 T#4 — the work plan deploy: a WP card's plan becomes Designer cards (D0006 §3.1 ~ §3.6, §3.8).

On the real SQLite DB, routes and fixtures of ``test_fg_card_settings`` (card documents and card settings registered
as ``startup`` registers them) with the deploy routes (``flowgate.work_plan_deploy.routes``) mounted beside them:

- first deploy: one card per WP card id (N + NR -> N, T + TR -> T, TS + TSR -> TS, D / P / L / DB), chained after the
  WP card in plan order; DS / T2 make none; the plan is never written; ``wp_source`` names the card id and its type;
- a step's pre-instruction becomes its card's instruction line body (N / T, design: DS) — doc id and front matter
  kept, written, saved as ``work_plan_deploy``; each design card takes only its own step's text (D 2 · P 1);
- protection: a line with a body, an attachment, a past review / rejection, not the card's own, or a card that
  started is never written (``instruction_kept`` + reason); an unreadable fact protects (``protection_unknown``);
- answer order: a resend / the second of two requests is 200 with nothing written; mode, graph baseline, blockers,
  stale cleanup; WP revision / digest; locks (run, group AI, disposal, final approval, not a card plan, ownership);
- add / cleanup: new card ids after the chain's tail (before an outside next card), removal of untouched cards only
  (what only the deploy wrote counts as untouched), the plan saved back to zero, retyped / duplicated cards kept;
- failure: a failed file replacement or commit leaves every byte, identity, row, setting and draft as it was, with no
  kept or temporary file and no event; a retry then writes them all; a target changed after the judgement is 409;
  an original that cannot be put back is reported;
- the WP save waits for a deploy holding the WP save lock; the v0.1 final approval expansion and apply leave a card
  plan / Graph Flow alone.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, _doc, _h, _sql, db_path, decided, edge, env, get_settings, node, put_draft, setting,
)


# ── fixtures and helpers ─────────────────────────────────────────────────────

@pytest.fixture()
def dep(env):
    from flowgate.work_plan_deploy import routes

    app = FastAPI()
    app.include_router(routes.router)
    env["deploy"] = TestClient(app)
    return env


def _flow(env, extra_nodes=(), extra_edges=()):
    """R -> WP in a new draft of FA: (WP node key, WP doc id, key map)."""
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_wp", "WP"), *extra_nodes],
                      [edge("tmp_e", "tmp_r", "tmp_wp"), *extra_edges])
    km = first["key_map"]
    wp_doc = next(line["doc_id"] for line in first["card_documents"][km["tmp_wp"]] if line["slot"] == "plan")
    return km["tmp_wp"], wp_doc, km


def _doc_row(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc_id)


def _write_plan(wp_doc, quantities, texts=None, step_values=None, bump=False):
    """The plan of ``wp_doc``: ``quantities`` (type -> count), pre-instruction ``texts`` and other values by step key.
    ``bump``: a save happened (the revision goes up)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import work_plan_service as wp

    doc = _doc_row(wp_doc)
    body = wp.initial_body(list(quantities), [], "flowgate", quantities=quantities)   # card id = the card's key
    for step in body["steps"]:
        if step["key"] in (texts or {}):
            step["pre_instruction_text"] = texts[step["key"]]
        step.update((step_values or {}).get(step["key"], {}))
    plan = wp.validate(body, project_id="flowgate", doc_id=wp_doc, action="save", enforce_provider_scope=False)
    wp.write_body_atomically(wp.plan_path_for_doc(doc), plan)
    if bump:
        db_documents.update(wp_doc, {"revision_no": int(doc.get("revision_no") or 0) + 1})
    return plan


def _plan_bytes(wp_doc):
    from modules.flow_gate.services import work_plan_service as wp

    return wp.plan_path_for_doc(_doc_row(wp_doc)).read_bytes()


def _preview(env, wp_doc, mode, expect=200, revision=None):
    rev = int(_doc_row(wp_doc).get("revision_no") or 0) if revision is None else revision
    res = env["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy/preview",
                             json={"mode": mode, "wp_revision_no": rev}, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _execute(env, wp_doc, mode, expect=200, baseline=None):
    if baseline is None:
        baseline = _preview(env, wp_doc, mode)["baseline"]
    res = env["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy", json={"mode": mode, **baseline},
                             headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _status(env, wp_doc, expect=200):
    res = env["deploy"].get(f"/api/v1/documents/{wp_doc}/work-plan/deploy", headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _draft(env):
    nodes = _sql(env["db"], "SELECT node_key, node_type, doc_type, label, palette_item_id FROM graph_draft_nodes "
                            "WHERE group_id = ? ORDER BY ordinal", (FA,))
    edges = _sql(env["db"], "SELECT edge_id, source_node_key AS source, target_node_key AS target, condition_json "
                            "FROM graph_draft_edges WHERE group_id = ? ORDER BY ordinal", (FA,))
    version = _sql(env["db"], "SELECT draft_version FROM graph_drafts WHERE group_id = ?", (FA,))
    return nodes, edges, (version[0]["draft_version"] if version else 0)


def _chain(env, start):
    _nodes, edges, _v = _draft(env)
    out = {}
    for e in edges:
        out.setdefault(e["source"], []).append(e["target"])
    path, cur = [], start
    while len(out.get(cur, [])) == 1:
        cur = out[cur][0]
        path.append(cur)
    return path


def _source(env, key):
    import json

    rows = _sql(env["db"], "SELECT value_json FROM graph_node_settings WHERE group_id = ? AND node_key = ? "
                           "AND field = 'wp_source'", (FA, key))
    return json.loads(rows[0]["value_json"]) if rows else None


def _line_doc(env, key, slot="instruction"):
    rows = _sql(env["db"], "SELECT doc_id FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
                (FA, key, slot))
    return rows[0]["doc_id"] if rows else None


def _file(doc_id):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    return legacy.file_of(_doc_row(doc_id))


def _text(doc_id):
    return _file(doc_id).read_text(encoding="utf-8")


def _identity(doc_id):
    from modules.flow_gate.storage.files import file_identity

    return file_identity(_file(doc_id))


def _body_saves(env, doc_id):
    import json

    rows = _sql(env["db"], "SELECT note FROM events WHERE doc_id = ? AND event_type = 'doc_body_saved'", (doc_id,))
    return [json.loads(r["note"])["source"] for r in rows]


def _cards(answer, result=None):
    return [c for c in answer["cards"] if result is None or c["result"] == result]


def _key_of(answer, card_id):
    return next(c["node_key"] for c in answer["cards"] if c["card_id"] == card_id and c["node_key"])


def _has_body(doc_id):
    from flowgate.card_documents import lines

    return lines.has_body(_doc_row(doc_id))


# ── first deploy ─────────────────────────────────────────────────────────────

def test_the_first_deploy_makes_one_card_per_card_id_in_plan_order_after_the_wp_card(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"DS": 1, "D": 1, "P": 1, "N": 1, "T": 1, "T2": 1, "TS": 1})
    before = _plan_bytes(wp_doc)
    status = _status(dep, wp_doc)
    assert (status["deployable"], status["deployed_count"], status["target_count"]) == (True, 0, 5)
    assert status["modes"] == {"first": True, "add": False, "cleanup": False}

    answer = _execute(dep, wp_doc, "first")
    created = _cards(answer, "create")
    assert [c["doc_type"] for c in created] == ["D", "P", "N", "T", "TS"]
    assert {c["card_id"] for c in _cards(answer, "skipped_type")} == {"DS#1", "T2#1"}
    chain = _chain(dep, wp_key)
    assert chain == answer["created_node_keys"] and len(chain) == 5
    nodes = {n["node_key"]: n for n in _draft(dep)[0]}
    assert [nodes[k]["doc_type"] for k in chain] == ["D", "P", "N", "T", "TS"]
    assert [nodes[k]["palette_item_id"] for k in chain] == ["basic_design", "protocol", "research", "task", "test"]
    for key, card in zip(chain, created):
        assert _source(dep, key) == {"wp_doc_id": wp_doc, "card_id": card["card_id"], "node_type": "document",
                                     "doc_type": card["doc_type"], "wp_revision_no": 0, "instruction_doc_id": None,
                                     "instruction_digest": None}
    # each card has its own lines (design: DS + design document; TS: its scenario lines)
    assert [r["doc_type"] for r in _sql(dep["db"], "SELECT doc_type FROM graph_node_documents WHERE group_id = ? "
                                                   "AND node_key = ? ORDER BY ordinal", (FA, chain[0]))] == ["DS", "D"]
    assert _plan_bytes(wp_doc) == before                                  # the plan (DS / T2 kept) is never written
    after = _status(dep, wp_doc)
    assert (after["deployed_count"], after["missing_count"], after["modes"]) == (
        5, 0, {"first": False, "add": True, "cleanup": True})


def test_an_unassigned_flow_stays_unassigned_and_gets_no_v01_sequence(dep):
    from modules.flow_gate.graph_workflow.services.engine_router import read_ownership

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    _execute(dep, wp_doc, "first")
    assert read_ownership(FA).state == "unassigned"
    assert read_ownership(FA).sequence is None                              # no v0.1 sequence


def test_a_graph_flow_without_a_draft_deploys_into_a_new_draft_of_its_latest_revision(dep):
    from modules.flow_gate.graph_workflow.services.engine_router import read_ownership

    wp_key, wp_doc, _km = _flow(dep)
    assert dep["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    assert read_ownership(FA).state == "graph" and _draft(dep)[2] == 0
    _write_plan(wp_doc, {"D": 1})
    preview = _preview(dep, wp_doc, "first")
    assert (preview["baseline"]["draft_version"], preview["baseline"]["base_revision_no"]) == (0, 1)
    answer = _execute(dep, wp_doc, "first", baseline=preview["baseline"])
    assert answer["draft_version"] == 1 and _chain(dep, wp_key) == answer["created_node_keys"]
    base = _sql(dep["db"], "SELECT base_revision_no FROM graph_drafts WHERE group_id = ?", (FA,))[0]
    assert base["base_revision_no"] == 1 and read_ownership(FA).state == "graph"


# ── the instruction bodies ───────────────────────────────────────────────────

def test_mixed_design_cards_each_take_their_own_steps_text_as_their_design_instruction(dep):
    wp_key, wp_doc, _km = _flow(dep)
    plan = _write_plan(wp_doc, {"D": 2, "P": 1, "T": 1, "TS": 1},
                       texts={"D#1": "첫 기본설계 지시", "D#2": "둘째 기본설계 지시", "P#1": "프로토콜 지시",
                              "T#1": "작업 지시", "TS#1": "시험 지시"},
                       step_values={"D#2": {"note": "메모는 지시가 아니다"}})
    answer = _execute(dep, wp_doc, "first")
    by_card = {c["card_id"]: c for c in _cards(answer, "create")}
    assert {k: c["instruction"] for k, c in by_card.items()} == {
        "D#1": "instruction_write", "D#2": "instruction_write", "P#1": "instruction_write",
        "T#1": "instruction_write", "TS#1": None}
    expected = {"D#1": "첫 기본설계 지시", "D#2": "둘째 기본설계 지시", "P#1": "프로토콜 지시", "T#1": "작업 지시"}
    for card_id, text in expected.items():
        key = _key_of(answer, card_id)
        doc_id = _line_doc(dep, key)
        content = _text(doc_id)
        assert content.startswith("---\n") and content.rstrip().endswith(text)
        assert all(other not in content for other in expected.values() if other != text)
        assert "메모는 지시가 아니다" not in content
        assert _has_body(doc_id) and _body_saves(dep, doc_id) == ["work_plan_deploy"]
        source = _source(dep, key)
        assert (source["instruction_doc_id"], source["card_id"]) == (doc_id, card_id)
        assert _doc_row(doc_id)["status"] == "draft"                         # nothing is approved here
    ds = _line_doc(dep, _key_of(answer, "D#1"))
    assert _doc_row(ds)["type_code"] == "DS"
    assert not _has_body(_line_doc(dep, _key_of(answer, "TS#1")))         # TS: no pre-instruction transfer
    # the card ids, not the order, link the cards: D#2 moved first keeps its card
    del plan


def test_an_instruction_keeps_the_doc_id_and_front_matter_of_the_cards_empty_draft(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    first = _execute(dep, wp_doc, "first")
    key = first["created_node_keys"][0]
    doc_id = _line_doc(dep, key)
    empty = _text(doc_id)
    assert not _has_body(doc_id)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "승인 되었습니다"}, bump=True)
    answer = _execute(dep, wp_doc, "add")
    assert [(c["result"], c["instruction"]) for c in answer["cards"]] == [("already_deployed", "instruction_write")]
    assert _line_doc(dep, key) == doc_id
    content = _text(doc_id)
    front = empty.split("\n---", 1)[0]
    assert content.startswith(front) and content.rstrip().endswith("승인 되었습니다")
    assert _has_body(doc_id) and _body_saves(dep, doc_id) == ["work_plan_deploy"]
    assert answer["created_node_keys"] == []


def test_the_instruction_composition_is_the_v01_rule_file_first_then_the_text():
    from modules.flow_gate.documents.routers import documents as v01
    from modules.flow_gate.services import work_plan_instruction_text as text

    document = text.instruction_document("추가로 할 일", {"doc_id": "x"}, "---\ntitle: f\n---\n# 파일 지시\n\n본문\n")
    assert text.instruction_body("제목", document, "ko") == "# 파일 지시\n\n본문\n\n## 추가 지시\n\n추가로 할 일\n"
    assert v01._work_plan_instruction_body("제목", document, "ko") == text.instruction_body("제목", document, "ko")
    assert text.instruction_body("제목", text.instruction_document("텍스트만", None, None), "ko") == "# 제목\n\n텍스트만\n"
    assert text.instruction_document("  ", None, None) is None             # nothing: no body is written


# ── protection of a written line ─────────────────────────────────────────────

def _deployed_t(dep, count=1):
    """A first deploy of ``count`` T cards with no pre-instruction; returns (wp doc, node keys)."""
    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": count})
    keys = _execute(dep, wp_doc, "first")["created_node_keys"]
    _write_plan(wp_doc, {"T": count}, texts={f"T#{i}": f"지시 {i}" for i in range(1, count + 1)}, bump=True)
    return wp_doc, keys


def _attach(env, doc_id):
    _sql(env["db"], "INSERT INTO attachments (doc_id, original_filename, filename, file_path, size, content_sha256, "
                    "uploaded_at, created_at) VALUES (?, 'a.txt', 'a.txt', ?, 1, ?, 't', 't')",
         (doc_id, f"att/{doc_id}/a.txt", "0" * 64))


@pytest.mark.parametrize("touch, reason", [
    ("body", "has_body"), ("attachment", "has_attachment"), ("review", "has_history"),
    ("rejection", "has_history"), ("not_owned", "not_owned"), ("started", "started"),
    ("unreadable", "protection_unknown"),
])
def test_a_touched_instruction_line_is_never_overwritten(dep, monkeypatch, touch, reason):
    from flowgate.work_plan_deploy import legacy_adapter as legacy
    from modules.flow_gate.db import documents as db_documents

    wp_doc, [key] = _deployed_t(dep)
    doc_id = _line_doc(dep, key)
    if touch == "body":
        path = _file(doc_id)
        path.write_text(path.read_text(encoding="utf-8") + "\n사람이 쓴 지시\n", encoding="utf-8")
    elif touch == "attachment":
        _attach(dep, doc_id)
    elif touch == "review":
        _sql(dep["db"], "INSERT INTO document_reviews (doc_id, reviewer_id, reviewed_at) VALUES (?, 'ai', 't')",
             (doc_id,))
    elif touch == "rejection":
        db_documents.update(doc_id, {"rejection_history": '[{"reason": "다시", "at": "t"}]'})
    elif touch == "not_owned":
        _sql(dep["db"], "UPDATE graph_node_documents SET owned = 0 WHERE node_key = ? AND slot = 'instruction'",
             (key,))
    elif touch == "started":
        monkeypatch.setattr(legacy, "node_started", lambda flow_id, node_key: node_key == key)
    elif touch == "unreadable":
        def broken(_doc_id):
            raise RuntimeError("document_reviews unreadable")
        monkeypatch.setattr(legacy, "review_count", broken)
    before = _text(doc_id)
    answer = _execute(dep, wp_doc, "add")
    assert [(c["instruction"], c["instruction_reason"]) for c in answer["cards"]] == [("instruction_kept", reason)]
    assert answer["no_change"] is True and _text(doc_id) == before and _body_saves(dep, doc_id) == []


# ── answer order: resend, concurrency, mode, baseline ───────────────────────

def test_a_resent_first_deploy_is_200_with_nothing_written(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"N": 1, "T": 1}, texts={"N#1": "조사", "T#1": "작업"})
    preview = _preview(dep, wp_doc, "first")
    first = _execute(dep, wp_doc, "first", baseline=preview["baseline"])
    assert first["no_change"] is False
    events = len(dep["events"])
    again = _execute(dep, wp_doc, "first", baseline=preview["baseline"])     # same request, stale baseline
    assert again["no_change"] is True and again["created_node_keys"] == []
    assert {c["result"] for c in again["cards"]} == {"already_deployed"}
    assert {c["instruction"] for c in again["cards"]} == {"instruction_kept"}
    assert len(_chain(dep, wp_key)) == 2 and len(dep["events"]) == events


def test_two_concurrent_deploys_make_the_cards_once(dep):
    from flowgate.work_plan_deploy import service

    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    baseline = _preview(dep, wp_doc, "first")["baseline"]
    results, errors = [], []

    def run():
        try:
            results.append(service.execute(wp_doc, {"mode": "first", **baseline}, "u_kim", "ko"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert sorted(r["no_change"] for r in results) == [False, True]
    assert len(_chain(dep, wp_key)) == 2


def test_the_mode_must_fit_the_deployed_state(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    refused = _execute(dep, wp_doc, "add", expect=409)
    assert refused["code"] == "deploy_not_deployed"
    assert _execute(dep, wp_doc, "cleanup", expect=409)["code"] == "deploy_not_deployed"
    first = _execute(dep, wp_doc, "first")
    nodes, edges, version = _draft(dep)
    gone = first["created_node_keys"][1]
    put_draft(dep, FA, version, [{**_node_input(n)} for n in nodes if n["node_key"] != gone],
              [_edge_input(e) for e in edges if gone not in (e["source"], e["target"])])
    assert _execute(dep, wp_doc, "first", expect=409)["code"] == "deploy_already_deployed"
    readded = _execute(dep, wp_doc, "add")                                 # the deleted card's id is made again
    assert [c["result"] for c in readded["cards"]] == ["already_deployed", "create"]


def _node_input(n):
    return {"node_key": n["node_key"], "client_key": None, "node_type": n["node_type"], "doc_type": n["doc_type"],
            "palette_item_id": n.get("palette_item_id"), "label": n["label"], "assignee": None}


def _edge_input(e, condition=None):
    return {"edge_id": e["edge_id"], "client_key": None, "source": e["source"], "target": e["target"],
            "condition": condition}


def test_a_graph_saved_after_the_preview_refuses_the_execution_and_writes_nothing(dep):
    wp_key, wp_doc, km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    preview = _preview(dep, wp_doc, "first")
    nodes, edges, version = _draft(dep)
    put_draft(dep, FA, version, [_node_input(n) for n in nodes], [_edge_input(e) for e in edges])
    refused = _execute(dep, wp_doc, "first", expect=409, baseline=preview["baseline"])
    assert refused["code"] == "draft_version_conflict"
    assert _chain(dep, wp_key) == [] and _draft(dep)[2] == version + 1


def test_a_plan_changed_after_the_preview_is_refused(dep):
    from modules.flow_gate.db import documents as db_documents

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    preview = _preview(dep, wp_doc, "first")
    _write_plan(wp_doc, {"T": 2})                                         # the file changed, the revision did not
    changed = _execute(dep, wp_doc, "first", expect=409, baseline=preview["baseline"])
    assert (changed["code"], changed.get("reason")) == ("wp_changed", "plan_digest")
    db_documents.update(wp_doc, {"revision_no": 3})
    assert _execute(dep, wp_doc, "first", expect=409, baseline=preview["baseline"])["code"] == "wp_changed"
    assert _preview(dep, wp_doc, "first", expect=409, revision=0)["code"] == "wp_changed"
    assert _draft(dep)[0] and len(_draft(dep)[0]) == 2                     # R and WP only


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state, code", [("v01", "flow_owned_by_v01"), ("conflict", "flow_ownership_conflict")])
def test_a_flow_the_graph_may_not_edit_refuses_the_deploy(dep, monkeypatch, state, code):
    from flowgate.work_plan_deploy import legacy_adapter as legacy
    from modules.flow_gate.graph_workflow.services.engine_router import Ownership

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    baseline = _preview(dep, wp_doc, "first")["baseline"]
    monkeypatch.setattr(legacy, "read_ownership", lambda flow_id: Ownership(state, {"doc_id": f"{FA}.0001-R"}, None))
    assert _status(dep, wp_doc)["reason"] == code
    preview = _preview(dep, wp_doc, "first")
    assert preview["executable"] is False and preview["blockers"][0]["code"] == code
    assert _execute(dep, wp_doc, "first", expect=409, baseline=baseline)["code"] == code


def test_locks_and_states_refuse_the_deploy(dep, monkeypatch):
    from modules.flow_gate import process_service
    from modules.flow_gate.documents import document_service
    from modules.flow_gate.services import mutation_policy

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    baseline = _preview(dep, wp_doc, "first")["baseline"]
    with monkeypatch.context() as m:
        m.setattr(mutation_policy.db_leases, "get_active",
                  lambda gid: {"group_id": gid, "run_id": "air_x", "token_id": "t", "action_scope": "new"})
        assert _execute(dep, wp_doc, "first", expect=423, baseline=baseline)["code"] == "GROUP_AI_RUN_LOCKED"
    with monkeypatch.context() as m:
        m.setattr(process_service, "is_group_disposed", lambda gid: True)
        assert _execute(dep, wp_doc, "first", expect=409, baseline=baseline)["code"] == "flow_disposed"
    with monkeypatch.context() as m:
        m.setattr(document_service, "is_final_approved", lambda doc: True)
        refused = _execute(dep, wp_doc, "first", expect=422, baseline=baseline)
        assert (refused["code"], refused["reason"]) == ("wp_not_editable", "final_approved")
    loose = _doc(FA, 50, "WP")
    res = dep["deploy"].post(f"/api/v1/documents/{loose}/work-plan/deploy",
                             json={"mode": "first", **baseline}, headers=_h())
    assert res.status_code == 409 and res.json()["code"] == "wp_not_on_card"
    root = dep["deploy"].get(f"/api/v1/documents/{FA}.0001-R/work-plan/deploy", headers=_h())
    assert root.status_code == 422 and root.json()["code"] == "not_a_work_plan"
    bad = dep["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy", json={"mode": "all"}, headers=_h())
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_request"
    assert _chain(dep, _km["tmp_wp"]) == []


def test_a_running_flow_refuses_the_deploy(dep, decided):
    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    assert dep["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    baseline = _preview(dep, wp_doc, "first")["baseline"]
    started = dep["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    _sql(dep["db"], "UPDATE graph_runs SET status = 'running' WHERE group_id = ?", (FA,))   # its engine at work
    assert _execute(dep, wp_doc, "first", expect=409, baseline=baseline)["code"] == "graph_run_locked"


def test_the_source_setting_is_hidden_and_not_writable(dep):
    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    key = _execute(dep, wp_doc, "first")["created_node_keys"][0]
    assert "wp_source" not in get_settings(dep, FA, key)["fields"]
    refused = setting(dep, FA, key, "wp_source", {"wp_doc_id": "x", "card_id": "y"}, expect=422)
    assert refused["error"]["code"] == "invalid_request"
    assert _source(dep, key)["wp_doc_id"] == wp_doc


def test_a_new_card_starts_with_the_steps_ai_and_review_values(dep):
    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1, "D": 1}, step_values={
        "T#1": {"provider_id": "aip_card"}, "TR#1": {"provider_id": "aip_assignee", "review_count": 1,
                                                     "reviewer_provider_id": "aip_card"},
        "D#1": {"provider_id": "aip_assignee"}})
    answer = _execute(dep, wp_doc, "first")
    t, d = _key_of(answer, "T#1"), _key_of(answer, "D#1")
    shown_t = {k: f["value"] for k, f in get_settings(dep, FA, t)["fields"].items()}
    assert (shown_t["instruction_ai"], shown_t["run_ai"], shown_t["review_ai"], shown_t["review_count"],
            shown_t["instruction"]) == ("aip_card", "aip_assignee", "aip_card", 1, "use")
    assert {k: f["value"] for k, f in get_settings(dep, FA, d)["fields"].items()}["run_ai"] == "aip_assignee"


# ── add and cleanup ──────────────────────────────────────────────────────────

def test_add_makes_only_new_card_ids_between_the_tail_and_the_outside_next_card(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    [t1] = _execute(dep, wp_doc, "first")["created_node_keys"]
    nodes, edges, version = _draft(dep)
    saved = put_draft(dep, FA, version, [_node_input(n) for n in nodes] + [node("tmp_x", "N")],
                      [_edge_input(e) for e in edges] + [edge("tmp_ex", t1, "tmp_x")])
    x = saved["key_map"]["tmp_x"]
    tail_edge = next(e["edge_id"] for e in _draft(dep)[1] if e["target"] == x)
    _write_plan(wp_doc, {"T": 2}, bump=True)
    answer = _execute(dep, wp_doc, "add")
    [t2] = answer["created_node_keys"]
    assert _chain(dep, wp_key) == [t1, t2, x]
    moved = next(e for e in _draft(dep)[1] if e["target"] == x)
    assert (moved["edge_id"], moved["source"]) == (tail_edge, t2)          # the tail's edge now leaves the new card


def test_a_broken_chain_blocks_new_cards_but_not_filling_instructions(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    t1, t2 = _execute(dep, wp_doc, "first")["created_node_keys"]
    nodes, edges, version = _draft(dep)
    put_draft(dep, FA, version, [_node_input(n) for n in nodes],
              [_edge_input(e) for e in edges if (e["source"], e["target"]) != (t1, t2)])
    _write_plan(wp_doc, {"T": 3}, texts={"T#1": "채울 지시"}, bump=True)
    preview = _preview(dep, wp_doc, "add")
    assert preview["blockers"][0]["code"] == "deploy_chain_nonlinear" and t2 in preview["blockers"][0]["node_keys"]
    blocked = _execute(dep, wp_doc, "add", expect=409, baseline=preview["baseline"])
    assert blocked["code"] == "deploy_blocked" and not _has_body(_line_doc(dep, t1))
    _write_plan(wp_doc, {"T": 2}, texts={"T#1": "채울 지시"}, bump=True)
    filled = _execute(dep, wp_doc, "add")                                  # no new card: no chain edit needed
    assert filled["created_node_keys"] == [] and _has_body(_line_doc(dep, t1))


def test_a_branch_from_a_deployed_card_blocks_and_stays(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    [t1] = _execute(dep, wp_doc, "first")["created_node_keys"]
    nodes, edges, version = _draft(dep)
    put_draft(dep, FA, version, [_node_input(n) for n in nodes] + [node("tmp_y", "N"), node("tmp_z", "N")],
              [_edge_input(e) for e in edges] + [edge("tmp_ey", t1, "tmp_y"), edge("tmp_ez", t1, "tmp_z")])
    before = _draft(dep)[1]
    _write_plan(wp_doc, {"T": 2}, bump=True)
    assert _execute(dep, wp_doc, "add", expect=409)["code"] == "deploy_blocked"
    assert _draft(dep)[1] == before


def test_the_first_deploy_is_blocked_when_the_wp_card_already_has_a_next_card(dep):
    wp_key, wp_doc, km = _flow(dep, [node("tmp_u", "N")], [edge("tmp_eu", "tmp_wp", "tmp_u")])
    _write_plan(wp_doc, {"T": 1})
    preview = _preview(dep, wp_doc, "first")
    assert preview["blockers"][0]["code"] == "wp_card_has_successor"
    assert _execute(dep, wp_doc, "first", expect=409, baseline=preview["baseline"])["code"] == "deploy_blocked"


def test_cleanup_removes_untouched_cards_bridges_the_chain_and_keeps_protected_ones(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 4}, texts={"T#2": "전개가 쓴 지시", "T#3": "사람이 고칠 지시"})
    t1, t2, t3, t4 = _execute(dep, wp_doc, "first")["created_node_keys"]
    edited = _file(_line_doc(dep, t3))
    edited.write_text(edited.read_text(encoding="utf-8") + "\n사람이 덧붙임\n", encoding="utf-8")
    _attach(dep, _line_doc(dep, t4, "report"))                              # a protected document on another line
    _write_plan(wp_doc, {"T": 0}, bump=True)                                 # the plan saved back to zero
    status = _status(dep, wp_doc)
    assert (status["target_count"], status["orphan_count"], status["modes"]["cleanup"],
            status["modes"]["add"]) == (0, 4, True, False)
    preview = _preview(dep, wp_doc, "cleanup")
    assert sorted(preview["baseline"]["remove_node_keys"]) == sorted([t1, t2])
    t1_docs = [r["doc_id"] for r in _sql(dep["db"], "SELECT doc_id FROM graph_node_documents WHERE node_key = ?",
                                         (t1,))]
    answer = _execute(dep, wp_doc, "cleanup", baseline=preview["baseline"])
    kept = {c["node_key"]: c["reason"] for c in _cards(answer, "kept_orphan")}
    assert kept == {t3: "has_body", t4: "has_attachment"}
    assert sorted(answer["removed_node_keys"]) == sorted([t1, t2])
    assert _chain(dep, wp_key) == [t3, t4]
    assert all(_doc_row(d) is None for d in t1_docs)                       # the removed card's own drafts go
    assert _doc_row(_line_doc(dep, t3)) is not None


def test_a_cleanup_that_would_remove_more_than_the_preview_showed_is_refused(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    t1, t2 = _execute(dep, wp_doc, "first")["created_node_keys"]
    _attach(dep, _line_doc(dep, t2))
    _write_plan(wp_doc, {"T": 0}, bump=True)
    preview = _preview(dep, wp_doc, "cleanup")
    assert preview["baseline"]["remove_node_keys"] == [t1]
    _sql(dep["db"], "DELETE FROM attachments")                              # t2 became removable meanwhile
    assert _execute(dep, wp_doc, "cleanup", expect=409, baseline=preview["baseline"])["code"] == "deploy_preview_stale"
    # the other way round is not asked again: a card protected after the preview stays
    preview = _preview(dep, wp_doc, "cleanup")
    _attach(dep, _line_doc(dep, t1))
    answer = _execute(dep, wp_doc, "cleanup", baseline=preview["baseline"])
    assert {c["node_key"]: c["reason"] for c in _cards(answer, "kept_orphan")} == {t1: "has_attachment"}
    assert answer["removed_node_keys"] == [t2]


def test_a_retyped_or_duplicated_card_is_kept_and_never_written(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"D": 1, "T": 1})
    answer = _execute(dep, wp_doc, "first")
    d, t = _key_of(answer, "D#1"), _key_of(answer, "T#1")
    nodes, edges, version = _draft(dep)
    put_draft(dep, FA, version, [{**_node_input(n), "doc_type": "P"} if n["node_key"] == d else _node_input(n)
                                 for n in nodes], [_edge_input(e) for e in edges])
    _write_plan(wp_doc, {"D": 1, "T": 1}, texts={"D#1": "D의 지시"}, bump=True)
    again = _execute(dep, wp_doc, "add")
    by_key = {c["node_key"]: c for c in again["cards"]}
    assert by_key[d]["result"] == "kept_type_mismatch" and again["no_change"] is True
    assert not _has_body(_line_doc(dep, d)) and again["created_node_keys"] == []
    from modules.flow_gate.db.connection import get_store

    value = get_store()._fetch_one("SELECT value_json FROM graph_node_settings WHERE node_key = ? AND field = "
                                   "'wp_source'", [t])["value_json"]
    _sql(dep["db"], "UPDATE graph_node_settings SET value_json = ? WHERE node_key = ? AND field = 'wp_source'",
         (value, d))                                                          # d now claims T#1 as well
    dup = _execute(dep, wp_doc, "cleanup")
    assert {c["node_key"]: c["result"] for c in dup["cards"] if c["card_id"] == "T#1"} == {
        d: "kept_duplicate", t: "kept_duplicate"}
    # D#1 has no card of its own now: it is made again; the T#1 duplicates are neither written nor removed
    assert [c["card_id"] for c in _cards(dup, "create")] == ["D#1"] and dup["removed_node_keys"] == []


# ── failure atomicity ────────────────────────────────────────────────────────

def _snapshot(env, keys):
    docs = {k: _line_doc(env, k) for k in keys}
    return {
        "bytes": {k: _file(d).read_bytes() for k, d in docs.items()},
        "identity": {k: _identity(d) for k, d in docs.items()},
        "rows": {k: {f: _doc_row(d).get(f) for f in ("file_path", "status", "updated_at", "revision_no")}
                 for k, d in docs.items()},
        "saves": {k: len(_body_saves(env, d)) for k, d in docs.items()},
        "sources": {k: _source(env, k) for k in keys},
        "draft": _draft(env),
        "documents": _sql(env["db"], "SELECT COUNT(*) AS n FROM documents WHERE group_id = ?", (FA,))[0]["n"],
        "side_files": sorted(str(p) for k, d in docs.items() for p in _file(d).parent.iterdir()
                             if ".keep-" in p.name or ".tmp-" in p.name),
    }


def test_a_failed_file_replacement_leaves_everything_as_it_was_and_a_retry_writes_all(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    wp_doc, keys = _deployed_t(dep, 3)
    before, events = _snapshot(dep, keys), len(dep["events"])
    real, calls = legacy.replace_file, []

    def failing(path, data, identity):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(path, data, identity)

    monkeypatch.setattr(legacy, "replace_file", failing)
    res = dep["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy",
                             json={"mode": "add", **_preview(dep, wp_doc, "add")["baseline"]}, headers=_h())
    assert res.status_code == 500 and len(calls) == 2
    assert _snapshot(dep, keys) == before and len(dep["events"]) == events
    monkeypatch.setattr(legacy, "replace_file", real)
    answer = _execute(dep, wp_doc, "add")
    assert [c["instruction"] for c in answer["cards"]] == ["instruction_write"] * 3
    assert all(_has_body(_line_doc(dep, k)) for k in keys)


def test_a_failure_after_the_last_write_is_rolled_back_with_the_files(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    wp_doc, keys = _deployed_t(dep, 3)
    before, events = _snapshot(dep, keys), len(dep["events"])

    def fail():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(legacy, "before_commit", fail)
    res = dep["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy",
                             json={"mode": "add", **_preview(dep, wp_doc, "add")["baseline"]}, headers=_h())
    assert res.status_code == 500
    assert _snapshot(dep, keys) == before and len(dep["events"]) == events


def test_a_failure_while_making_new_cards_leaves_no_document_file_or_node(dep, monkeypatch):
    from flowgate.work_plan_deploy import service

    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2, "D": 1}, texts={"T#1": "지시"})
    count = _sql(dep["db"], "SELECT COUNT(*) AS n FROM documents WHERE group_id = ?", (FA,))[0]["n"]
    files = sorted(str(p) for p in dep_storage(dep).rglob("*") if p.is_file())
    draft = _draft(dep)

    def broken(*_a, **_k):
        raise RuntimeError("settings store down")

    monkeypatch.setattr(service.card_settings, "put", broken)
    res = dep["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy",
                             json={"mode": "first", **_preview(dep, wp_doc, "first")["baseline"]}, headers=_h())
    assert res.status_code == 500
    assert _sql(dep["db"], "SELECT COUNT(*) AS n FROM documents WHERE group_id = ?", (FA,))[0]["n"] == count
    assert sorted(str(p) for p in dep_storage(dep).rglob("*") if p.is_file()) == files
    assert _draft(dep) == draft


def dep_storage(env):
    from modules.flow_gate.storage.paths import get_storage_root

    return get_storage_root("flowgate", create=True)


def test_a_line_changed_between_the_judgement_and_its_replacement_is_409(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    wp_doc, keys = _deployed_t(dep, 2)
    real = legacy.replace_file
    first_doc = _line_doc(dep, keys[0])

    def meddling(path, data, identity):
        target = _file(first_doc)
        if path == target:
            tmp = target.with_name(target.name + ".other")
            tmp.write_bytes(target.read_bytes())
            tmp.replace(target)                                               # same bytes, another file
        return real(path, data, identity)

    monkeypatch.setattr(legacy, "replace_file", meddling)
    before = {k: len(_body_saves(dep, _line_doc(dep, k))) for k in keys}
    refused = _execute(dep, wp_doc, "add", expect=409)
    assert refused["code"] == "instruction_target_changed"
    assert {k: len(_body_saves(dep, _line_doc(dep, k))) for k in keys} == before
    assert not any(_has_body(_line_doc(dep, k)) for k in keys)


def test_an_original_that_cannot_be_put_back_is_kept_and_reported(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    wp_doc, keys = _deployed_t(dep, 2)
    real, calls = legacy.replace_file, []
    first_doc = _line_doc(dep, keys[0])

    def failing(path, data, identity):
        calls.append(path)
        if len(calls) == 2:
            target = _file(first_doc)
            tmp = target.with_name(target.name + ".other")
            tmp.write_bytes(b"someone else")
            tmp.replace(target)                                               # the first replacement is gone
            raise OSError("disk full")
        return real(path, data, identity)

    monkeypatch.setattr(legacy, "replace_file", failing)
    res = dep["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy",
                             json={"mode": "add", **_preview(dep, wp_doc, "add")["baseline"]}, headers=_h())
    assert res.status_code == 500 and res.json()["code"] == "deploy_restore_incomplete"
    assert res.json()["doc_ids"] == [first_doc]
    kept = [p for p in _file(first_doc).parent.iterdir() if ".keep-" in p.name]
    assert len(kept) == 1


# ── the WP save and the v0.1 paths ───────────────────────────────────────────

def test_a_wp_save_waits_for_a_deploy_holding_the_plan(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy
    from flowgate.work_plan_deploy import service
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import work_plan as wp_routes

    wp_key, wp_doc, _km = _flow(dep)
    plan = _write_plan(wp_doc, {"T": 1}, texts={"T#1": "리비전 0의 지시"})
    baseline = _preview(dep, wp_doc, "first")["baseline"]
    inside, release, done = threading.Event(), threading.Event(), {}

    def hold():
        inside.set()
        release.wait(30)

    monkeypatch.setattr(legacy, "before_commit", hold)
    app = FastAPI()
    app.include_router(wp_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u_kim", "is_admin": True}
    wp_client = TestClient(app)
    changed = dict(plan, steps=[dict(s, pre_instruction_text="리비전 1의 지시") if s["key"] == "T#1" else s
                                for s in plan["steps"]])

    def deploy():
        done["deploy"] = service.execute(wp_doc, {"mode": "first", **baseline}, "u_kim", "ko")

    def save():
        done["save"] = wp_client.put(f"/api/v1/documents/{wp_doc}/work-plan",
                                     json={"base_revision_no": 0, "body": changed})

    first = threading.Thread(target=deploy)
    first.start()
    assert inside.wait(30)
    second = threading.Thread(target=save)
    second.start()
    time.sleep(0.5)
    assert "save" not in done                                              # the save waits for the deploy
    release.set()
    first.join(60)
    second.join(60)
    assert done["save"].status_code == 200, done["save"].text
    assert done["save"].json()["revision_no"] == 1
    key = done["deploy"]["created_node_keys"][0]
    assert _source(dep, key)["wp_revision_no"] == 0
    assert _text(_line_doc(dep, key)).rstrip().endswith("리비전 0의 지시")


def test_approving_a_card_plan_never_expands_it_into_a_v01_sequence(dep):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
    from modules.flow_gate.services import work_plan_sequence_service as wpseq

    _wp_key, wp_doc, _km = _flow(dep)
    plan = _write_plan(wp_doc, {"T": 1})
    db_documents.update(wp_doc, {"doc_review_status": "approved", "target_id": f"{FA}.0001-R",
                                 "triggered_by": f"{FA}.0001-R"})
    result = wpseq.expand_final_work_plan(doc=_doc_row(wp_doc), plan=plan)
    assert (result["status"], result["reason"]) == ("skipped", "graph_flow")
    assert read_ownership(FA).state == "unassigned"
    assert read_ownership(FA).sequence is None                              # no v0.1 sequence
    # a plan of a Graph-owned Flow that is not a card's plan is not expanded either
    assert dep["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    loose = _doc(FA, 60, "WP", "approved")
    db_documents.update(loose, {"target_id": f"{FA}.0001-R"})
    assert wpseq.expand_final_work_plan(doc=_doc_row(loose), plan=plan)["reason"] == "graph_flow"
    assert read_ownership(FA).state == "graph"


def test_the_v01_apply_is_refused_on_a_graph_flow(dep):
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.graph_workflow.services.engine_router import read_ownership
    from modules.flow_gate.documents.routers import work_plan as wp_routes

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    assert dep["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    app = FastAPI()
    app.include_router(wp_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u_kim", "is_admin": True}
    res = TestClient(app).post(f"/api/v1/documents/{wp_doc}/work-plan/apply", json={
        "instruction_mode": "auto_approved", "change_workflow": False, "workflow_tag": "x", "wp_revision_no": 0})
    assert res.status_code == 409 and res.json()["error"] == "flow_owned_by_graph"
    assert read_ownership(FA).sequence is None                              # no v0.1 sequence


def test_the_deploy_lives_in_the_v02_package_and_main_mounts_it():
    from pathlib import Path

    server = Path(__file__).resolve().parents[1]
    main = (server / "routers" / "main.py").read_text(encoding="utf-8")
    assert "from flowgate.work_plan_deploy.routes import router as _work_plan_deploy_router" in main
    assert "app.include_router(_work_plan_deploy_router" in main
    for path in (server / "modules").rglob("*.py"):                     # modules.flow_gate never imports it
        text = path.read_text(encoding="utf-8")
        assert "from flowgate.work_plan_deploy" not in text and "import flowgate.work_plan_deploy" not in text, path
    wp_router = (server / "modules" / "flow_gate" / "documents" / "routers" / "work_plan.py").read_text(
        encoding="utf-8")
    assert "from modules.flow_gate.services.work_plan_locks import plan_save_lock as _plan_save_lock" in wp_router
    assert "_save_locks: dict" not in wp_router


# ── more cases: pre-instruction files, cleanup protection, chain edges ──────

_REF = {"doc_id": None, "filename": "__wp_pre_instruction__a.md", "original_filename": "a.md",
        "content_sha256": "a" * 64}


def test_a_pre_instruction_file_comes_first_and_its_text_follows_in_the_instruction(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "덧붙인 지시"},
                step_values={"T#1": {"pre_instruction_attachment": dict(_REF, doc_id=wp_doc)}})
    seen = []
    monkeypatch.setattr(legacy, "attachment_text",
                        lambda doc_id, ref: seen.append((doc_id, ref["filename"])) or
                        "---\ntitle: 파일\n---\n# 파일 지시\n\n파일 본문\n")
    key = _execute(dep, wp_doc, "first")["created_node_keys"][0]
    body = _text(_line_doc(dep, key)).split("\n---", 1)[1]
    assert body.strip().splitlines() == ["# 파일 지시", "", "파일 본문", "", "## 추가 지시", "", "덧붙인 지시"]
    assert seen and seen[0] == (wp_doc, _REF["filename"])


def test_an_unreadable_pre_instruction_file_refuses_the_deploy_and_writes_nothing(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, step_values={"T#1": {"pre_instruction_attachment": dict(_REF, doc_id=wp_doc)}})
    refused = _preview(dep, wp_doc, "first", expect=409)
    assert (refused["code"], refused["card_id"]) == ("wp_attachment_unreadable", "T#1")
    assert refused["reason"] == "pre_instruction_attachment_registry_missing"
    assert _chain(dep, wp_key) == []


@pytest.mark.parametrize("touch, reason", [
    ("review", "has_history"), ("rejection", "has_history"), ("started", "started"),
    ("unreadable", "protection_unknown"),
])
def test_cleanup_keeps_a_card_whose_documents_or_run_say_it_was_touched(dep, monkeypatch, touch, reason):
    from flowgate.work_plan_deploy import legacy_adapter as legacy
    from modules.flow_gate.db import documents as db_documents

    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    t1, t2 = _execute(dep, wp_doc, "first")["created_node_keys"]
    report = _line_doc(dep, t2, "report")
    if touch == "review":
        _sql(dep["db"], "INSERT INTO document_reviews (doc_id, reviewer_id, reviewed_at) VALUES (?, 'ai', 't')",
             (report,))
    elif touch == "rejection":
        db_documents.update(report, {"rejection_history": '[{"reason": "다시", "at": "t"}]'})
    elif touch == "started":
        monkeypatch.setattr(legacy, "node_started", lambda flow_id, node_key: node_key == t2)
    else:
        real = legacy.attachment_count
        def broken(doc_id):
            if doc_id == report:
                raise RuntimeError("registry unreadable")
            return real(doc_id)
        monkeypatch.setattr(legacy, "attachment_count", broken)
    _write_plan(wp_doc, {"T": 0}, bump=True)
    answer = _execute(dep, wp_doc, "cleanup")
    assert {c["node_key"]: c["reason"] for c in _cards(answer, "kept_orphan")} == {t2: reason}
    assert answer["removed_node_keys"] == [t1] and _chain(dep, wp_key) == [t2]
    assert _doc_row(report) is not None


def test_new_cards_are_blocked_when_the_outside_next_card_is_protected_by_the_run(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    [t1] = _execute(dep, wp_doc, "first")["created_node_keys"]
    nodes, edges, version = _draft(dep)
    x = put_draft(dep, FA, version, [_node_input(n) for n in nodes] + [node("tmp_x", "N")],
                  [_edge_input(e) for e in edges] + [edge("tmp_ex", t1, "tmp_x")])["key_map"]["tmp_x"]
    monkeypatch.setattr(legacy, "protected_nodes", lambda flow_id: {x})
    _write_plan(wp_doc, {"T": 2}, bump=True)
    preview = _preview(dep, wp_doc, "add")
    assert preview["blockers"] == [{"code": "deploy_position_blocked", "node_key": x}]
    assert _execute(dep, wp_doc, "add", expect=409, baseline=preview["baseline"])["code"] == "deploy_blocked"
    assert _chain(dep, wp_key) == [t1, x]


def test_an_edge_into_a_deployed_card_from_outside_blocks_new_cards_and_stays(dep):
    wp_key, wp_doc, km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    [t1] = _execute(dep, wp_doc, "first")["created_node_keys"]
    nodes, edges, version = _draft(dep)
    put_draft(dep, FA, version, [_node_input(n) for n in nodes] + [node("tmp_y", "N")],
              [_edge_input(e) for e in edges] + [edge("tmp_ey", km["tmp_r"], "tmp_y"), edge("tmp_eyt", "tmp_y", t1)])
    before = _draft(dep)[1]
    _write_plan(wp_doc, {"T": 2}, bump=True)
    preview = _preview(dep, wp_doc, "add")
    assert preview["blockers"][0]["code"] == "deploy_chain_nonlinear"
    assert _execute(dep, wp_doc, "add", expect=409, baseline=preview["baseline"])["code"] == "deploy_blocked"
    assert _draft(dep)[1] == before


# ── the final chain, missing lines, save evidence and the draft check (review rej_01M5B8KXP6Z51KKX) ──────────

def _assert_one_chain(env, wp_key, expected):
    """Every edge ends on a node of the draft, and ``expected`` is the one chain after the WP card."""
    nodes, edges, _v = _draft(env)
    keys = {n["node_key"] for n in nodes}
    assert all(e["source"] in keys and e["target"] in keys for e in edges), edges
    assert _chain(env, wp_key) == expected
    path = [wp_key] + expected
    for a, b in zip(path, path[1:]):
        assert [e["target"] for e in edges if e["source"] == a] == [b]
        assert [e["source"] for e in edges if e["target"] == b] == [a]


def _group_docs(env):
    return {r["doc_id"] for r in _sql(env["db"], "SELECT doc_id FROM documents WHERE group_id = ?", (FA,))}


def _storage_files(env):
    return sorted(str(p) for p in dep_storage(env).rglob("*") if p.is_file())


def _all_saves(env):
    return _sql(env["db"], "SELECT COUNT(*) AS n FROM events WHERE event_type = 'doc_body_saved'")[0]["n"]


def _linked(env):
    return {r["doc_id"] for r in _sql(env["db"], "SELECT doc_id FROM graph_node_documents WHERE group_id = ?",
                                      (FA,))}


def test_a_cleanup_that_removes_the_tail_and_adds_chains_the_new_card_after_the_card_that_stays(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    t1, t2 = _execute(dep, wp_doc, "first")["created_node_keys"]
    _write_plan(wp_doc, {"T": 1, "N": 1}, bump=True)                         # T#2 goes, N#1 comes
    answer = _execute(dep, wp_doc, "cleanup")
    [n1] = answer["created_node_keys"]
    assert answer["removed_node_keys"] == [t2]
    _assert_one_chain(dep, wp_key, [t1, n1])


def test_a_cleanup_that_removes_every_card_and_adds_chains_the_new_cards_after_the_wp_card(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    [t1] = _execute(dep, wp_doc, "first")["created_node_keys"]
    _write_plan(wp_doc, {"D": 1, "P": 1}, bump=True)                         # T=0: every card goes
    answer = _execute(dep, wp_doc, "cleanup")
    assert answer["removed_node_keys"] == [t1] and len(answer["created_node_keys"]) == 2
    _assert_one_chain(dep, wp_key, answer["created_node_keys"])            # plan order, after the WP card


def test_a_cleanup_with_an_outside_next_card_moves_its_edge_to_the_new_last_card(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 2})
    t1, t2 = _execute(dep, wp_doc, "first")["created_node_keys"]
    nodes, edges, version = _draft(dep)
    x = put_draft(dep, FA, version, [_node_input(n) for n in nodes] + [node("tmp_x", "N")],
                  [_edge_input(e) for e in edges] + [edge("tmp_ex", t2, "tmp_x")])["key_map"]["tmp_x"]
    tail_edge = next(e["edge_id"] for e in _draft(dep)[1] if e["target"] == x)
    _write_plan(wp_doc, {"T": 1, "N": 1}, bump=True)                         # the tail goes, a new card comes
    answer = _execute(dep, wp_doc, "cleanup")
    [n1] = answer["created_node_keys"]
    assert answer["removed_node_keys"] == [t2]
    _assert_one_chain(dep, wp_key, [t1, n1, x])
    assert next(e for e in _draft(dep)[1] if e["target"] == x) == {
        "edge_id": tail_edge, "source": n1, "target": x, "condition_json": None}
    _write_plan(wp_doc, {"P": 1}, bump=True)                                 # every card goes, a new one comes
    again = _execute(dep, wp_doc, "cleanup")
    [p1] = again["created_node_keys"]
    assert sorted(again["removed_node_keys"]) == sorted([t1, n1])
    _assert_one_chain(dep, wp_key, [p1, x])
    assert next(e["edge_id"] for e in _draft(dep)[1] if e["target"] == x) == tail_edge


def test_filling_a_card_whose_instruction_line_has_no_row_writes_the_empty_draft_the_save_made(dep):
    import hashlib

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1})
    [key] = _execute(dep, wp_doc, "first")["created_node_keys"]
    _sql(dep["db"], "DELETE FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = 'instruction'",
         (FA, key))
    docs, files = _group_docs(dep), _storage_files(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "줄이 없던 카드의 지시"}, bump=True)
    preview = _preview(dep, wp_doc, "add")
    assert [(c["result"], c["instruction"]) for c in preview["cards"]] == [("already_deployed", "instruction_write")]
    answer = _execute(dep, wp_doc, "add", baseline=preview["baseline"])
    assert [c["instruction"] for c in answer["cards"]] == ["instruction_write"]
    line = _line_doc(dep, key)
    assert _group_docs(dep) - docs == {line} and line in _linked(dep)      # one document, the line's own
    assert len(set(_storage_files(dep)) - set(files)) == 1                  # one file, no stray empty draft
    assert _text(line).startswith("---\n") and _text(line).rstrip().endswith("줄이 없던 카드의 지시")
    assert _body_saves(dep, line) == ["work_plan_deploy"]
    source = _source(dep, key)
    assert source["instruction_doc_id"] == line
    assert source["instruction_digest"] == hashlib.sha256(_file(line).read_bytes()).hexdigest()


def _no_instruction_drafts(monkeypatch):
    """The draft save leaves every instruction line without a document (the deploy's own creation then runs)."""
    from flowgate.card_documents import lines
    from flowgate.card_documents import slots as cs

    real = lines._line_document

    def skip(group, node_, slot, made, registered, user_id, locale=None):     # 0088: the save's locale goes on
        if slot.slot == cs.INSTRUCTION:
            return None, False
        return real(group, node_, slot, made, registered, user_id, locale)

    monkeypatch.setattr(lines, "_line_document", skip)


def _count_creations(monkeypatch, made):
    from flowgate.work_plan_deploy import service

    real = service._create_line

    def counted(*args, **kwargs):
        made.append(args[1])
        return real(*args, **kwargs)

    monkeypatch.setattr(service, "_create_line", counted)


def test_a_document_the_deploy_makes_carries_its_save_evidence_and_cleanup_takes_it_back(dep, monkeypatch):
    import hashlib

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "새로 만든 지시 문서"})
    docs, made = _group_docs(dep), []
    with monkeypatch.context() as m:
        _no_instruction_drafts(m)
        _count_creations(m, made)
        answer = _execute(dep, wp_doc, "first")
    [key] = answer["created_node_keys"]
    assert made == [key] and [c["instruction"] for c in answer["cards"]] == ["instruction_write"]
    line = _line_doc(dep, key)
    assert line in _group_docs(dep) - docs and _group_docs(dep) - docs <= _linked(dep)   # nothing left unlinked
    assert _has_body(line) and _body_saves(dep, line) == ["work_plan_deploy"]
    source = _source(dep, key)
    assert source["instruction_doc_id"] == line
    assert source["instruction_digest"] == hashlib.sha256(_file(line).read_bytes()).hexdigest()
    _write_plan(wp_doc, {"T": 0}, bump=True)                                 # the plan saved back to zero
    preview = _preview(dep, wp_doc, "cleanup")
    assert preview["baseline"]["remove_node_keys"] == [key]
    removed = _execute(dep, wp_doc, "cleanup", baseline=preview["baseline"])
    assert removed["removed_node_keys"] == [key]
    assert _doc_row(line) is None and _line_doc(dep, key) is None and _body_saves(dep, line) == []


def test_a_failed_deploy_leaves_no_row_file_or_evidence_of_a_document_it_made(dep, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "롤백될 지시"})
    baseline = _preview(dep, wp_doc, "first")["baseline"]
    before = (_group_docs(dep), _storage_files(dep), _draft(dep), _all_saves(dep), len(dep["events"]))
    made = []
    _no_instruction_drafts(monkeypatch)
    _count_creations(monkeypatch, made)

    def fail():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(legacy, "before_commit", fail)
    res = dep["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy", json={"mode": "first", **baseline},
                             headers=_h())
    assert res.status_code == 500 and len(made) == 1                        # made, then rolled back
    assert (_group_docs(dep), _storage_files(dep), _draft(dep), _all_saves(dep), len(dep["events"])) == before


def test_cleanup_before_a_confirmation_deletes_the_deploys_drafts_with_their_evidence(dep):
    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "확정 전 지시"})
    [key] = _execute(dep, wp_doc, "first")["created_node_keys"]
    line = _line_doc(dep, key)
    assert _body_saves(dep, line) == ["work_plan_deploy"]
    _write_plan(wp_doc, {"T": 0}, bump=True)
    answer = _execute(dep, wp_doc, "cleanup")
    assert answer["removed_node_keys"] == [key] and _chain(dep, wp_key) == []
    assert _doc_row(line) is None and _line_doc(dep, key) is None and _body_saves(dep, line) == []


def test_cleanup_after_a_confirmation_keeps_the_revisions_documents_lines_and_save_history(dep):
    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "확정된 지시"})
    [key] = _execute(dep, wp_doc, "first")["created_node_keys"]
    line, version = _line_doc(dep, key), _draft(dep)[2]
    text = _text(line)
    confirmed = dep["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": version},
                                   headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    lines_sql = ("SELECT slot, doc_type, doc_id, owned FROM graph_node_documents WHERE group_id = ? AND node_key = ? "
                 "ORDER BY ordinal")
    rows = _sql(dep["db"], lines_sql, (FA, key))
    _write_plan(wp_doc, {"T": 0}, bump=True)
    answer = _execute(dep, wp_doc, "cleanup")
    assert answer["removed_node_keys"] == [key]
    assert key not in {n["node_key"] for n in _draft(dep)[0]}              # gone from the draft only
    assert _sql(dep["db"], lines_sql, (FA, key)) == rows
    assert _doc_row(line) is not None and _text(line) == text
    assert _body_saves(dep, line) == ["work_plan_deploy"]                   # the revision's document keeps its history


def _gates(count):
    return [node(f"tmp_g{i}", None, node_type="gate") for i in range(count)]


def test_the_final_graph_must_fit_the_draft_saves_node_limit_in_first_add_and_cleanup(dep):
    from modules.flow_gate.graph_workflow.domain.graph_model import DRAFT_NODES_MAX

    too_many = {"code": "deploy_draft_invalid", "errors": [{"field": "nodes", "reason": "too_many_items"}]}
    wp_key, wp_doc, _km = _flow(dep, _gates(DRAFT_NODES_MAX - 3))          # R + WP + gates: one below the limit
    assert len(_draft(dep)[0]) == DRAFT_NODES_MAX - 1

    def refused(mode):
        before = (_draft(dep), _group_docs(dep), _storage_files(dep), _all_saves(dep))
        preview = _preview(dep, wp_doc, mode)
        assert preview["executable"] is False and preview["blockers"] == [too_many]
        answer = _execute(dep, wp_doc, mode, expect=409, baseline=preview["baseline"])
        assert answer["code"] == "deploy_blocked" and answer["blockers"] == [too_many]
        assert (_draft(dep), _group_docs(dep), _storage_files(dep), _all_saves(dep)) == before

    _write_plan(wp_doc, {"T": 2}, texts={"T#1": "넘치면 쓰지 않는다"})
    refused("first")                                                        # 301 nodes
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "한도 안"}, bump=True)
    [t1] = _execute(dep, wp_doc, "first")["created_node_keys"]               # 300: the limit itself
    assert len(_draft(dep)[0]) == DRAFT_NODES_MAX
    _write_plan(wp_doc, {"T": 1, "N": 1}, bump=True)
    refused("add")
    refused("cleanup")                                                      # nothing to remove, one more card
    _write_plan(wp_doc, {"N": 1}, bump=True)
    answer = _execute(dep, wp_doc, "cleanup")                               # T#1 goes, N#1 comes: 300 again
    assert answer["removed_node_keys"] == [t1] and len(answer["created_node_keys"]) == 1
    assert len(_draft(dep)[0]) == DRAFT_NODES_MAX
    _assert_one_chain(dep, wp_key, answer["created_node_keys"])


def test_a_final_graph_with_an_edge_to_a_missing_node_is_refused_before_any_write(dep, monkeypatch):
    from flowgate.work_plan_deploy import service

    wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "쓰이면 안 되는 지시"})
    real = service.deploy_chain.rebuild

    def broken(edges, chain, removed, new_keys):
        return real(edges, chain, removed, new_keys) + [
            {"edge_id": None, "client_key": "tmp_wpd_bad", "source": new_keys[-1], "target": "n_gone"}]

    monkeypatch.setattr(service.deploy_chain, "rebuild", broken)
    before = (_draft(dep), _group_docs(dep), _storage_files(dep), _all_saves(dep))
    preview = _preview(dep, wp_doc, "first")
    [blocker] = preview["blockers"]
    assert blocker["code"] == "deploy_draft_invalid" and preview["executable"] is False
    assert any(e["field"].endswith(".target") and e["reason"] == "unknown_node_reference" for e in blocker["errors"])
    assert _execute(dep, wp_doc, "first", expect=409, baseline=preview["baseline"])["code"] == "deploy_blocked"
    assert (_draft(dep), _group_docs(dep), _storage_files(dep), _all_saves(dep)) == before
    assert _chain(dep, wp_key) == []



# ── flowgate.v02.0088 T#1: the instruction documents a deploy makes are titled in its request's language ────────

INSTRUCTION_NAMES = {"ko": "지시서", "ja": "指示書", "en": "Instruction"}


def _execute_in(env, wp_doc, mode, locale):
    baseline = _preview(env, wp_doc, mode)["baseline"]
    res = env["deploy"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy", json={"mode": mode, **baseline},
                             headers={**_h(), "x-locale": locale})
    assert res.status_code == 200, res.text
    return res.json()


def _front_title(doc_id):
    head = _text(doc_id).split("\n---", 1)[0]
    return next(line[len("title: "):] for line in head.splitlines() if line.startswith("title: "))


def _heading(doc_id):
    body = _text(doc_id).split("\n---", 1)[1]
    return next(line[2:] for line in body.splitlines() if line.startswith("# "))


def _canvas_title(env, key, slot="instruction"):
    res = env["client"].get(f"/api/v1/flows/{FA}/canvas", headers=_h())
    assert res.status_code == 200, res.text
    shown = next(n for n in res.json()["nodes"] if n["node_key"] == key)
    return next(line["title"] for line in shown["card_documents"] if line["slot"] == slot)


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_a_deploy_s_graph_save_titles_the_new_cards_documents_in_its_language(dep, locale):
    """The deploy's draft save (the route's own X-Locale down the Graph port): heading = row = front matter = line."""
    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "작업 지시"})
    answer = _execute_in(dep, wp_doc, "first", locale)
    key = _key_of(answer, "T#1")
    doc_id = _line_doc(dep, key)
    title = _doc_row(doc_id)["title"]
    assert title.endswith(" · " + INSTRUCTION_NAMES[locale]) or title == INSTRUCTION_NAMES[locale]
    assert _heading(doc_id) == _front_title(doc_id) == _canvas_title(dep, key) == title
    report = _line_doc(dep, key, "report")
    assert _doc_row(report)["title"].endswith({"ko": "레포트", "ja": "レポート", "en": "Report"}[locale])
    assert _text(doc_id).rstrip().endswith("작업 지시") and _body_saves(dep, doc_id) == ["work_plan_deploy"]


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_a_deploy_s_new_instruction_line_is_titled_once_for_its_heading_row_and_front_matter(dep, monkeypatch, locale):
    """The direct branch (``_create_line``, reached as above: the draft save leaves the instruction line without a
    document): the title is decided once in the request's locale and is the heading, the row, the front matter and
    the line's title."""
    _wp_key, wp_doc, _km = _flow(dep)
    _write_plan(wp_doc, {"T": 1}, texts={"T#1": "새로 만든 지시 문서"})
    made = []
    with monkeypatch.context() as m:
        _no_instruction_drafts(m)
        _count_creations(m, made)
        answer = _execute_in(dep, wp_doc, "first", locale)
    [key] = answer["created_node_keys"]
    assert made == [key] and [c["instruction"] for c in answer["cards"]] == ["instruction_write"]
    doc_id = _line_doc(dep, key)
    title = _doc_row(doc_id)["title"]
    assert title == INSTRUCTION_NAMES[locale]                                    # the deploy's node has no label
    assert _heading(doc_id) == _front_title(doc_id) == _canvas_title(dep, key) == title
    assert _text(doc_id).rstrip().endswith("새로 만든 지시 문서") and _body_saves(dep, doc_id) == ["work_plan_deploy"]
    assert _source(dep, key)["instruction_doc_id"] == doc_id


def test_a_deploy_in_another_language_fills_an_existing_instruction_under_its_own_title(dep):
    """``_instruction_file``: the card's empty draft keeps its doc id, front matter and title (made in ko)."""
    wp_doc, [key] = _deployed_t(dep)
    doc_id = _line_doc(dep, key)
    title = _doc_row(doc_id)["title"]
    assert title.endswith("지시서")
    _execute_in(dep, wp_doc, "add", "ja")
    assert _line_doc(dep, key) == doc_id and _doc_row(doc_id)["title"] == title
    assert _heading(doc_id) == _front_title(doc_id) == title and _text(doc_id).rstrip().endswith("지시 1")


def test_a_work_plan_card_made_in_japanese_has_a_japanese_title_and_the_same_plan_contract(dep):
    first = dep["client"].put(f"/api/v1/flows/{FA}/graph/draft", headers={**_h(), "x-locale": "ja"},
                              json={"draft_version": 0, "nodes": [node("tmp_r", "R"), node("tmp_wp", "WP")],
                                    "edges": [edge("tmp_e", "tmp_r", "tmp_wp")]})
    assert first.status_code == 200, first.text
    body = first.json()
    wp_lines = body["card_documents"][body["key_map"]["tmp_wp"]]
    wp_doc = next(line["doc_id"] for line in wp_lines if line["slot"] == "plan")
    assert _doc_row(wp_doc)["title"].startswith("作業計画 — ")
    _write_plan(wp_doc, {"T": 1})                                                # the plan reads, saves and deploys
    assert _status(dep, wp_doc)["deployable"] is True
    assert _execute(dep, wp_doc, "first")["created_node_keys"]
