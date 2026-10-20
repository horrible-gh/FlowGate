"""flowgate.v02.0074 T#5 — the client's two deploy paths end to end on the server API (D0006 §3.7 · §3.6.2 · §6.1 · §6.2).

On the real SQLite DB, routes and fixtures of ``test_fg_instruction_stage_0056`` (card documents, card settings, the
card approval port and the Graph run as ``startup`` registers them), with the v0.1 work plan router (the window's
``PUT …/work-plan``) and the deploy routes mounted beside them, called in the order the client calls them:

- 「지시 생략」: the WP PUT stores the step's pre-instruction 「승인 되었습니다」 → status (no deployed card: first) →
  preview at the saved revision → the execution with that preview's baseline → the card's instruction line (the
  「설계 지시」 DS line of a design card) holds the text → the Graph confirmed and run: no instruction AI job, the
  instruction is approved by the run (approve_instruction) and the result stage starts with it as its reference — for
  N, T and the design cards D / P / L / DB;
- 「정리 포함 재전개」 with every quantity at 0: a deploy of four cards, two of them touched (a body a person wrote, an
  attachment on the report line), the plan saved back to 0 through the PUT → the status offers only cleanup → its
  preview lists the two untouched cards → the execution with that baseline removes them and re-links the chain;
  the touched ones are ``kept_orphan`` with their reason.

The WP card is a person's card (it waits in a run) and a branch from R has no run rule yet, so before the run the
draft is re-linked as R -> the deployed card -> WP (the card's node, lines, settings and ``wp_source`` stay as the
deploy left them).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_fg_card_settings import AI, FA, _h, _sql, _start, edge, put_draft
from test_fg_card_settings_review import _admin_and_reviewer, _review_status
from test_fg_instruction_stage_0056 import (  # noqa: F401 — fixtures used by name
    _one_job, _row, card_approval_port, db_path, decided, env,
)
from test_work_plan_deploy_0074 import (
    _attach, _chain, _doc_row, _draft, _edge_input, _file, _flow, _has_body, _line_doc, _node_input, _source,
    _write_plan,
)

OMIT = "승인 되었습니다"
RESULT_TYPE = {"N": "NR", "T": "TR", "D": "D", "P": "P", "L": "L", "DB": "DB"}


@pytest.fixture()
def api(env):
    """The routes the client calls: the WP read / save (v0.1 router, a signed-in admin) and the deploy (user JWT)."""
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import work_plan as wp_routes

    from flowgate.work_plan_deploy import routes as deploy_routes

    app = FastAPI()
    app.include_router(wp_routes.router, prefix="/api/v1")
    app.include_router(deploy_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u_kim", "is_admin": True}
    env["api"] = TestClient(app)
    _admin_and_reviewer(env)
    return env


# ── the client's calls ───────────────────────────────────────────────────────

def _read(env, wp_doc):
    res = env["api"].get(f"/api/v1/documents/{wp_doc}/work-plan", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def _save(env, wp_doc, change):
    """The window's one PUT: the body as read, changed by ``change``, on its base revision."""
    view = _read(env, wp_doc)
    body = view["body"]
    change(body)
    res = env["api"].put(f"/api/v1/documents/{wp_doc}/work-plan",
                         json={"base_revision_no": view["revision_no"], "body": body}, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def _omit(env, wp_doc, *step_keys):
    """「지시 생략」's draft change (the fixed text, no attachment, a person's edit) saved once."""
    def change(body):
        for step in body["steps"]:
            if step["key"] in step_keys:
                assert not step.get("pre_instruction_text") and not step.get("pre_instruction_attachment")
                step.update(pre_instruction_text=OMIT, pre_instruction_attachment=None, origin="human")
    return _save(env, wp_doc, change)


def _status(env, wp_doc):
    res = env["api"].get(f"/api/v1/documents/{wp_doc}/work-plan/deploy", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def _preview(env, wp_doc, mode, revision):
    res = env["api"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy/preview",
                          json={"mode": mode, "wp_revision_no": revision}, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def _execute(env, wp_doc, mode, baseline):
    """The execution with the preview's baseline as it came."""
    res = env["api"].post(f"/api/v1/documents/{wp_doc}/work-plan/deploy", json={"mode": mode, **baseline},
                          headers=_h())
    assert res.status_code == 200, res.text
    return res.json()


def _card_entry(answer, card_id):
    [entry] = [c for c in answer["cards"] if c["card_id"] == card_id]
    return entry


def _confirm_and_run_after_the_root(env, root_key, card_key):
    """The deployed card right after R, the WP card after it (the WP card waits for a person in a run, and a branch
    from R has no run rule yet): R -> card -> WP, an AI on the card; confirm and start the run."""
    nodes, _edges, version = _draft(env)
    wp_key = next(n["node_key"] for n in nodes if n["doc_type"] == "WP")
    put_draft(env, FA, version,
              [{**_node_input(n), "assignee": AI if n["node_key"] == card_key else None} for n in nodes],
              [edge("tmp_root_card", root_key, card_key), edge("tmp_card_wp", card_key, wp_key)])
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": version + 1},
                                   headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    _start(env)


# ── 「지시 생략」 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc_type", ["N", "T", "D", "P", "L", "DB"])
def test_an_omitted_instruction_saved_and_deployed_is_approved_by_the_run_without_an_instruction_ai(
        api, decided, doc_type):
    _wp_key, wp_doc, km = _flow(api)
    _write_plan(wp_doc, {doc_type: 1})                                   # the card's plan as first written (rev 0)
    step_key = f"{doc_type}#1"

    saved = _omit(api, wp_doc, step_key)                                 # the window's one PUT
    assert saved["revision_no"] == 1
    [step] = [s for s in saved["body"]["steps"] if s["key"] == step_key]
    assert (step["pre_instruction_text"], step["pre_instruction_attachment"]) == (OMIT, None)
    card_id = step["card_id"]

    status = _status(api, wp_doc)                                        # no deployed card: first
    assert (status["deployable"], status["deployed_count"], status["wp_revision_no"]) == (True, 0, 1)
    assert status["modes"]["first"] is True
    preview = _preview(api, wp_doc, "first", saved["revision_no"])       # at the saved revision
    assert (preview["executable"], preview["blockers"], preview["no_change"]) == (True, [], False)
    assert _card_entry(preview, card_id)["instruction"] == "instruction_write"
    assert preview["baseline"]["wp_revision_no"] == 1
    answer = _execute(api, wp_doc, "first", preview["baseline"])
    entry = _card_entry(answer, card_id)
    assert (entry["result"], entry["instruction"]) == ("create", "instruction_write")
    card_key = answer["key_map"][card_id]

    instruction = _line_doc(api, card_key)                               # N / T: the instruction; design: the DS
    assert _doc_row(instruction)["type_code"] == ("DS" if doc_type in ("D", "P", "L", "DB") else doc_type)
    assert OMIT in _file(instruction).read_text(encoding="utf-8")
    assert _source(api, card_key)["instruction_doc_id"] == instruction
    assert _review_status(instruction) is None                           # the deploy approves nothing itself

    _confirm_and_run_after_the_root(api, km["tmp_r"], card_key)
    job = _one_job(api)                                                  # the result stage's only: no instruction AI
    assert (job["stage"], job["ref_doc_ids"], job["result_doc_type"]) == ("result", [instruction], RESULT_TYPE[doc_type])
    assert _review_status(instruction) == "approved"                     # approved by the run (approve_instruction)
    row = _row(api, card_key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", instruction)


def test_a_second_omission_deploys_with_add_and_writes_only_its_own_card(api):
    _wp_key, wp_doc, _km = _flow(api)
    _write_plan(wp_doc, {"T": 2})
    first = _omit(api, wp_doc, "T#1")
    preview = _preview(api, wp_doc, "first", first["revision_no"])
    deployed = _execute(api, wp_doc, "first", preview["baseline"])
    keys = deployed["created_node_keys"]
    assert len(keys) == 2
    assert (_card_entry(deployed, "T#1")["instruction"], _card_entry(deployed, "T#2")["instruction"]) == (
        "instruction_write", None)
    t2_line = _line_doc(api, keys[1])
    assert not _has_body(t2_line)

    second = _omit(api, wp_doc, "T#2")                                   # the client: deployed cards -> add
    status = _status(api, wp_doc)
    assert (status["deployed_count"], status["modes"]["add"]) == (2, True)
    preview = _preview(api, wp_doc, "add", second["revision_no"])
    entry = _card_entry(preview, "T#2")
    assert (entry["result"], entry["instruction"]) == ("already_deployed", "instruction_write")
    answer = _execute(api, wp_doc, "add", preview["baseline"])
    assert answer["created_node_keys"] == [] and answer["removed_node_keys"] == []
    assert OMIT in _file(t2_line).read_text(encoding="utf-8")
    # T#1's line is the first omission's and stays as it was written (protected: kept, not written again).
    assert _card_entry(answer, "T#1")["instruction"] in (None, "instruction_kept")
    assert OMIT in _file(_line_doc(api, keys[0])).read_text(encoding="utf-8")


# ── 「정리 포함 재전개」 with every quantity at 0 ────────────────────────────────

def test_a_plan_saved_back_to_zero_cleans_up_the_untouched_cards_and_keeps_the_touched_ones(api):
    wp_key, wp_doc, _km = _flow(api)
    _write_plan(wp_doc, {"T": 4})

    def texts(body):
        for step in body["steps"]:
            if step["key"] == "T#1":
                step["pre_instruction_text"] = OMIT
            if step["key"] == "T#3":
                step["pre_instruction_text"] = "사람이 고칠 지시"
    saved = _save(api, wp_doc, texts)
    preview = _preview(api, wp_doc, "first", saved["revision_no"])
    t1, t2, t3, t4 = _execute(api, wp_doc, "first", preview["baseline"])["created_node_keys"]
    assert _chain(api, wp_key) == [t1, t2, t3, t4]
    edited = _file(_line_doc(api, t3))                                    # a person's edit of the deploy's body
    edited.write_text(edited.read_text(encoding="utf-8") + "\n사람이 덧붙임\n", encoding="utf-8")
    _attach(api, _line_doc(api, t4, "report"))                            # an attachment on another line
    removed_docs = [r["doc_id"] for r in _sql(api["db"], "SELECT doc_id FROM graph_node_documents "
                                                         "WHERE node_key IN (?, ?)", (t1, t2))]

    def to_zero(body):                                                    # the window's PUT with T at 0
        body["quantities"]["T"]["count"] = 0
        body["steps"] = [s for s in body["steps"] if s["type"] not in ("T", "TR")]
    zero = _save(api, wp_doc, to_zero)

    status = _status(api, wp_doc)
    assert (status["target_count"], status["deployed_count"], status["orphan_count"]) == (0, 4, 4)
    assert status["modes"] == {"first": False, "add": False, "cleanup": True}
    preview = _preview(api, wp_doc, "cleanup", zero["revision_no"])
    assert preview["blockers"] == [] and preview["executable"] is True
    assert sorted(preview["baseline"]["remove_node_keys"]) == sorted([t1, t2])
    assert {c["node_key"]: c["reason"] for c in preview["cards"] if c["result"] == "kept_orphan"} == {
        t3: "has_body", t4: "has_attachment"}

    answer = _execute(api, wp_doc, "cleanup", preview["baseline"])
    assert sorted(answer["removed_node_keys"]) == sorted([t1, t2])
    assert {c["node_key"]: c["reason"] for c in answer["cards"] if c["result"] == "kept_orphan"} == {
        t3: "has_body", t4: "has_attachment"}
    assert _chain(api, wp_key) == [t3, t4]                                # removed and re-linked
    nodes = {n["node_key"] for n in _draft(api)[0]}
    assert t1 not in nodes and t2 not in nodes and {t3, t4} <= nodes
    assert all(_doc_row(d) is None for d in removed_docs)                 # their own empty drafts go with them
    assert _doc_row(_line_doc(api, t3)) is not None
    after = _status(api, wp_doc)
    assert (after["deployed_count"], after["orphan_count"], after["modes"]["cleanup"]) == (2, 2, True)
