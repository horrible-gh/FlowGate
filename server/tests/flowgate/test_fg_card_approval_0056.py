"""flowgate.v02.0056 T#1 — the card approval flow (0002-CH turn 5~10) on the Graph run.

On the same real SQLite DB, routes and fixtures as ``test_fg_card_settings`` (card documents and card settings
registered as ``startup`` does, the card approval route mounted as ``routers/main.py`` mounts it):

- every node run tells whether its card awaits a person's approval (``awaiting_approval``): a node in review
  whose result was not approved automatically and on which no AI works any more — neither the node's own AI
  run (it still holds the Flow's group AI lease) nor the card's review loop;
- ``POST /runs/{run_id}/nodes/{node_key}/approval`` approves the card's documents that exist and are not
  approved yet through the v0.1 approval, instruction first and the node's result last; only the result's
  approval is a node event (one ``review_approved``, one propagation) — an instruction's binds nothing;
- a card that is not awaiting approval is refused with 409 and its reason, a gate card included;
- a card is approved as a whole (T0004 §4 역방향): a result approved before its card's instruction — a
  person's v0.1 approval of the result first, or the card's automatic approval — is no node event; the node
  waits in review until the last document is approved, through the card approval, a person's v0.1 approval
  (the contact) or the reconciliation of a read (``card_approval_ports.pending_documents``);
- a document a person wrote in the editor and never submitted (no review status, a body) is approved with
  the card; only the card's untouched empty draft is skipped;
- the card approval needs ``document.approve`` only: an approver without the author's edit permission
  approves a rejected result and a written draft as well;
- 0056 T#1 개정: a rejected bound root is input; its card is done and the next card opens,
  while its document stays rejected and card approval is refused with 409 ``already_approved``;
- an AI run that bound its result and then did not end on its own (cancelled, timeout, paused, providers
  exhausted, a provider failure) approves nothing and starts no review: the card awaits its approval;
- a result approved while its card waits for its other documents and then revised is followed by the
  reconciliation of a read even when the revision's contact was lost;
- a rejection is no node event (a person's through the v0.1 contact, or the card review's when its count is
  spent): the node waits for the card approval, which approves a rejected result too;
- [다음 단계] stop is not "awaiting approval": an approval while the run is stopped — a person's card approval
  or the card's automatic approval — makes the node done at once and only its propagation waits.

0083 WP-3 (0061 T#2 개정, revised): a person's approval on an idle run propagates at once again (``frontier.defers``,
``test_fg_idle_approval_0083``), and a run's states are 0061 T#2's (migration 151): a done run is ``idle`` with the
idle reason ``completed`` and keeps its Flow. Two tests read the old ``completed`` run status and were revised to it
(``..._revised_0061_t2``); what they check of the card approval is unchanged.

The v0.1 AI invoke admission is replaced by a recorder; the Flow's group AI lease is stubbed where a test says
an AI run holds it. Auth, RBAC and SSE delivery are stubbed as in the card settings tests.

flowgate.v02.0119 T#3 (T-2): no person approves a card any more. A card in review that nothing completes is
``needs_intervention`` (or a ``system_error``, ``node_approval.intervention``), ``awaiting_approval`` is always false and
the card approval route refuses it (409 with that kind; T-3 removes the route). The cases whose premise was a person's
card approval moving a node on were removed here (the person's approval of a result, the approval's document targets
and order, the approval permissions, another AI run refusing it); the cases on what still holds were kept or rewritten
to the new contract: the judgement by state, AI still working, the refusal codes, a gate decided by its own confirm, a
rejection and a stopped AI run that move nothing (the card needs intervention), a result approved before its card's
other documents. The T-2 contract itself is ``test_fg_card_intervention_0119`` / ``test_fg_auto_approval_0105``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, pre_feature_attempts,
    AI, FA, _doc, _h, _run_one_card, _sql, _start, db_path, decided, edge, env, node, put_draft, setting,
)
from test_fg_card_settings_review import (
    REVIEW_RUN, WORK_RUN, _admin_and_reviewer, _in_review, _loop_row, _node, _recorded_start_run, _result,
    _review_status, _work_run_ended,
)
# flowgate.v02.0113 T#3 (T §6.4): this suite's cards are cards made before the AI role feature (their legacy fields:
# assignee, run_ai, review_count / review_ai — L-1 §2.4.5); the stage assignment feature stays on, so each new attempt
# runs the assigned path (L-2) resolved from those fields. Pre-feature attempts are made explicitly
# (``pre_feature_attempts``) by the tests of the pre-feature contract.
pytestmark = pytest.mark.usefixtures("legacy_cards")

_SERVER_DIR = Path(__file__).resolve().parents[2]
LEE = {"kind": "human", "user_id": "u_lee", "provider_id": None}
APPROVER_ONLY = {"document.read", "document.approve"}      # an approver without the author's edit permission


@pytest.fixture(autouse=True)
def card_approval_port():
    """The card approval's Graph port, registered as ``startup`` registers it."""
    from flowgate.card_approval import integration

    integration.register()
    yield
    integration.unregister()


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_id(env):
    return _sql(env["db"], "SELECT run_id FROM graph_runs WHERE group_id = ?", (FA,))[0]["run_id"]


def _current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _node_run(env, key):
    run = _current(env)
    return next(n for n in run["node_runs"] if n["node_key"] == key)


def _approve(env, key, expect=200, body=None, user="u_kim", run_id=None):
    res = env["client"].post(f"/api/v1/runs/{run_id or _run_id(env)}/nodes/{key}/approval", json=body,
                             headers={**_h(), "Authorization": f"Bearer user:{user}"})
    assert res.status_code == expect, res.text
    return res.json()


def _events(env, key):
    return [r["cause"] for r in _sql(env["db"], "SELECT cause FROM graph_run_events WHERE node_key = ? "
                                                "ORDER BY event_seq", (key,))]


def _lease(monkeypatch, run_id):
    """An AI run ``run_id`` holds the Flow's group AI lease (read only and active reads); None: released."""
    from modules.flow_gate.db import group_ai_leases

    row = {"group_id": FA, "run_id": run_id, "state": "active", "action_scope": "new",
           "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(timespec="seconds")}
    held = run_id is not None
    monkeypatch.setattr(group_ai_leases, "get", lambda group_id: dict(row) if held and group_id == FA else None)
    monkeypatch.setattr(group_ai_leases, "get_active", lambda group_id: dict(row) if held and group_id == FA else None)


def _line_doc(env, key, slot):
    return _sql(env["db"], "SELECT doc_id FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
                (FA, key, slot))[0]["doc_id"]


def _submit_written(doc_id):
    """A person wrote and submitted a card line's document: a body, waiting for review."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    path = get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {doc_id}\n\nwritten by a person\n", encoding="utf-8")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.md", "status": "open",
                                 "doc_review_status": "pending_review"})


def _write_body(doc_id, body):
    """The editor's save (PATCH /documents/content): the file is written, only ``file_path`` changes — the
    document keeps its state and no review status (``document_service`` / ``documents`` route)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    path = get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: T\ntitle: {doc_id}\n---\n{body}", encoding="utf-8")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.md"})


def _v01_approve(doc_id, contact=True):
    """A person's v0.1 approval of one document (the ``review_transitions/approve`` route and its contact)."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    transition_document_review(doc_id=doc_id, action="approve", actor_user_id="u_kim",
                               user_permissions={"document.approve"})
    if contact:
        v01_contact.after_review_decided(FA, doc_id, "approve")


def _causes_after(env, key, seq):
    return [r["cause"] for r in _sql(env["db"], "SELECT cause FROM graph_run_events WHERE event_seq > ? "
                                                "AND node_key = ? ORDER BY event_seq", (seq, key))]


def _approver_only(monkeypatch, user="u_lee"):
    """``user`` holds ``document.approve`` but not ``document.update`` / ``own.draft`` (a real RBAC role the
    approve button's stub cannot express)."""
    from modules.flow_gate.workflow.routers import workflow

    real = workflow._get_user_permissions
    monkeypatch.setattr(workflow, "_get_user_permissions",
                        lambda u: set(APPROVER_ONLY) if (u or {}).get("user_id") == user else real(u))


def _human_result(env, key, seq=81, typ="T"):
    """A person's card (``LEE``) registers its result with the node's token."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid",
                 (key,))[0]["token_id"]
    result = _result(seq, typ)
    v01_contact.after_inbox_registered(FA, result, typ, token)
    assert _node(env, key) == ("review", result)
    return result


def _chain(env, cards):
    """R -> card 1 -> card 2 … confirmed as revision 1 (``cards``: (doc_type or 'gate', assignee))."""
    nodes = [node("tmp_r", "R")]
    for i, (kind, assignee) in enumerate(cards):
        nodes.append(node(f"tmp_{i}", None, node_type="gate") if kind == "gate" else node(f"tmp_{i}", kind,
                                                                                           assignee=assignee))
    refs = ["tmp_r"] + [f"tmp_{i}" for i in range(len(cards))]
    body = put_draft(env, FA, 0, nodes, [edge(f"tmp_e{i}", a, b) for i, (a, b) in enumerate(zip(refs, refs[1:]))])
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    return [body["key_map"][f"tmp_{i}"] for i in range(len(cards))]


def _stop(env):
    res = env["client"].post(f"/api/v1/runs/{_run_id(env)}/stop", json={}, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _resume(env):
    res = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={}, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


# ── awaiting_approval ────────────────────────────────────────────────────────


def test_an_ai_result_is_in_intervention_only_when_no_ai_works_on_it(env, decided, monkeypatch):
    """0119 T-2 (was ``..._awaits_approval_only_when_...``): the same judgement now tells the card's intervention."""
    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    _lease(monkeypatch, WORK_RUN)                                       # the node's own AI run has not ended
    assert (_node_run(env, key)["awaiting_approval"], _node_run(env, key)["intervention"]) == (False, None)
    assert _approve(env, key, 409)["error"]["code"] == "ai_running"
    _lease(monkeypatch, None)                                            # the run ended (lease released)
    _loop_row(result, "review")                                          # the card's review works on it
    assert _node_run(env, key)["intervention"] is None
    assert _approve(env, key, 409)["error"]["code"] == "ai_running"
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_count_exhausted', stop_detail = 'spent' WHERE run_id = ?", (REVIEW_RUN,))
    shown = _node_run(env, key)                                          # count spent: it needs intervention
    assert (shown["awaiting_approval"], shown["intervention"]["kind"]) == (False, "needs_intervention")
    assert _approve(env, key, 409)["error"]["code"] == "needs_intervention"


def test_the_node_approval_judgement_by_state():
    from modules.flow_gate.graph_workflow.services import node_approval as na

    review = {"node_key": "gn_x", "status": "review", "result_doc_id": "d"}
    # 0119 T-2: a card in review nothing completes is in intervention, never awaiting a person's approval
    assert na.refusal("f", review) == "needs_intervention" and not na.awaiting_approval("f", review)
    assert na.intervention("f", review) == {"kind": "needs_intervention", "reason": None}
    assert [na.refusal("f", {"node_key": "gn_x", "status": s, "result_doc_id": None})
            for s in ("pending", "open", "done", "rejected", "closed")] == [
        "node_not_started", "no_result", "already_approved", "node_not_in_review", "node_not_in_review"]
    assert na.refusal("f", {**review, "result_doc_id": None}) == "no_result"
    assert na.refusal("f", {"node_key": "gn_g", "status": "open"}, "gate") == "node_is_gate"


# ── the card approval: documents, order, one node event ──────────────────────


def test_an_instruction_approval_makes_no_node_event(env, decided, monkeypatch):
    from flowgate.card_approval import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _submit_written(instruction)
    before = _current(env)["last_event_seq"]
    legacy_adapter.approve_document(FA, legacy_adapter.document(instruction), "u_kim",
                                    legacy_adapter.permissions_of("u_kim"), "ko")
    assert _review_status(instruction) == "approved"
    assert _current(env)["last_event_seq"] == before and _node(env, key) == ("review", result)


def test_a_result_approved_before_its_instruction_waits_until_the_card_is_whole(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _submit_written(instruction)
    seq = _current(env)["last_event_seq"]
    _v01_approve(result)                                                 # v0.1: the TR first
    node_run = _node_run(env, key)                                       # the read reconciles: nothing missing
    assert (node_run["status"], node_run["intervention"]["kind"]) == ("review", "needs_intervention")    # 0119 T-2
    assert _causes_after(env, key, seq) == [] and _review_status(instruction) == "pending_review"
    _v01_approve(instruction)                                            # then the T: the card is whole
    assert _node(env, key) == ("done", result)
    assert _causes_after(env, key, seq) == ["review_approved"]
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_propagations WHERE node_key = ?", (key,))[0]["n"] == 1


def test_a_lost_contact_of_the_last_document_is_reconciled_by_the_next_read(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _submit_written(instruction)
    seq = _current(env)["last_event_seq"]
    _v01_approve(result)
    _v01_approve(instruction, contact=False)                             # the contact was lost
    assert _node(env, key) == ("review", result)
    assert _current(env) is None                                         # the read reconciles: R -> T completes
    assert _node(env, key) == ("done", result) and _causes_after(env, key, seq) == ["review_approved"]


def test_an_automatic_approval_of_the_result_leaves_a_written_instruction_to_the_card(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _submit_written(instruction)                                         # a person wrote the instruction
    _work_run_ended()                                                    # no review: the result is approved
    assert _review_status(result) == "approved" and _node(env, key) == ("review", result)
    assert _node_run(env, key)["intervention"]["kind"] == "needs_intervention"           # 0119 T-2: no person's approval
    assert _approve(env, key, 409)["error"]["code"] == "needs_intervention" and _node(env, key) == ("review", result)


def test_an_approved_result_revised_while_its_card_waits_is_followed_after_a_lost_contact(env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    key, old, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _submit_written(instruction)
    seq = _current(env)["last_event_seq"]
    _v01_approve(old)                                                    # the TR first: the card waits for its T
    assert _node(env, key) == ("review", old)
    new = _result(91, "TR")                                              # the TR is revised and its
    db_documents.set_superseded_by(old, new)                             # registration's contact is lost
    run = _current(env)                                                  # the read reconciles: the revision first
    node_run = next(n for n in run["node_runs"] if n["node_key"] == key)
    assert (node_run["status"], node_run["result_doc_id"], node_run["intervention"]["kind"]) == (
        "review", new, "needs_intervention")                             # 0119 T-2: no person's approval follows
    assert _causes_after(env, key, seq) == ["revised"]


def test_the_reconciliation_follows_a_live_revision_before_an_approved_result_s_card(monkeypatch):
    from types import SimpleNamespace

    from modules.flow_gate.graph_workflow.domain.frontier import RunEvent
    from modules.flow_gate.graph_workflow.services import card_approval_ports as ports
    from modules.flow_gate.graph_workflow.services import result_binder as rb

    docs = {"tr_old": {"doc_id": "tr_old", "doc_review_status": "approved", "superseded_by": "tr_new",
                       "group_id": "f", "status": "open"},
            "tr_new": {"doc_id": "tr_new", "doc_review_status": "pending_review", "group_id": "f", "status": "open"}}
    ctx = SimpleNamespace(flow_id="f", node_rows={"gn_t": {"node_key": "gn_t"}}, docs=lambda: docs)
    s = SimpleNamespace(node=lambda key: SimpleNamespace(status="review", result_doc_id="tr_old"))
    monkeypatch.setattr(ports, "pending_documents", lambda flow_id, node, result_doc_id: ["t1"])   # the card waits
    assert rb.next_missing_event(ctx, s, "gn_t") == RunEvent("revised", "gn_t", doc_id="tr_new", replaces="tr_old")
    docs["tr_old"]["superseded_by"] = None                               # no revision: the card still waits
    assert rb.next_missing_event(ctx, s, "gn_t") is None
    monkeypatch.setattr(ports, "pending_documents", lambda flow_id, node, result_doc_id: [])        # the card is whole
    assert rb.next_missing_event(ctx, s, "gn_t") == RunEvent("review_approved", "gn_t", doc_id="tr_old")


def test_the_pending_documents_port_names_the_card_documents_not_approved_yet(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_approval_ports as ports

    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    t_card = {"node_key": key, "node_type": "document", "doc_type": "T"}
    assert ports.pending_documents(FA, t_card, result) == []             # the empty draft is no document
    _write_body(instruction, "written")
    assert ports.pending_documents(FA, t_card, result) == [instruction]
    _submit_written(instruction)
    assert ports.pending_documents(FA, t_card, result) == [instruction]
    _v01_approve(instruction, contact=False)
    assert ports.pending_documents(FA, t_card, result) == []
    assert ports.pending_documents(FA, {"node_key": key, "node_type": "gate"}, None) == []


def test_the_approval_targets_of_each_card_kind():
    from flowgate.card_approval.approval import targets
    from flowgate.card_documents import slots

    def kinds(node_type, doc_type):
        return [(s.slot, s.doc_type) for s in slots.approval_slots(node_type, doc_type)]

    assert kinds("document", "N") == [("instruction", "N"), ("report", "NR")]
    assert kinds("document", "T") == kinds("custom", None) == [("instruction", "T"), ("report", "TR")]
    assert kinds("document", "T2") == [("instruction", "T2"), ("report", "TR2")]
    assert [kinds("document", t) for t in ("D", "P", "L", "DB")] == [
        [("instruction", "DS"), ("report", t)] for t in ("D", "P", "L", "DB")]
    assert kinds("document", "TS") == [("instruction", "TS"), ("report", "TSR")]   # the scenario shares the TS
    assert kinds("document", "R") == [("requirement", "R")] and kinds("document", "B") == [("requirement", "B")]
    assert kinds("document", "WP") == [("plan", "WP")] and kinds("gate", None) == []
    lines = [{"slot": "instruction", "doc_id": "t", "doc_review_status": "pending_review"},
             {"slot": "report", "doc_id": "tr", "doc_review_status": "pending_review"}]
    t_card = {"node_type": "document", "doc_type": "T"}
    assert targets(t_card, lines, "tr") == ["t", "tr"]
    assert targets(t_card, lines, "t") == ["tr", "t"]                    # the node's result is always last
    assert targets(t_card, [{**lines[0], "doc_review_status": None}, lines[1]], "tr") == ["tr"]   # a draft
    assert targets(t_card, [{**lines[0], "doc_review_status": None, "written": False}, lines[1]], "tr") == ["tr"]
    assert targets(t_card, [{**lines[0], "doc_review_status": None, "written": True}, lines[1]], "tr") == [
        "t", "tr"]                                                       # written in the editor, never submitted
    assert targets(t_card, [], "tr") == ["tr"]
    r_card = {"node_type": "document", "doc_type": "R"}
    assert targets(r_card, [{"slot": "requirement", "doc_id": "r", "doc_review_status": "pending_review"}], "r") == ["r"]


# ── 409 / 403 / 404 ──────────────────────────────────────────────────────────

# 0113 T#3 (T §6.4) pre-feature contract: a person card's dispatch token registers its result directly; an assigned person RUN card writes its instruction first (L-2 §2.1/§2.7) — test_fg_ai_roles_l2_assignment.py::test_g2_*
def test_a_card_that_is_not_awaiting_approval_is_refused_with_its_reason_revised_0061_t2(pre_feature_attempts, env, decided):
    _admin_and_reviewer(env)
    t1, t2 = _chain(env, [("T", LEE), ("T", LEE)])
    _start(env)
    for key, code in ((t1, "no_result"), (t2, "node_not_started")):
        refused = _approve(env, key, 409)["error"]
        assert refused["code"] == code and refused["params"]["node_key"] == key and refused["message"]
    assert _approve(env, "gn_nothing", 404)["error"]["code"] == "node_not_found"
    assert _approve(env, t1, 404, run_id="run_missing")["error"]["code"] == "run_not_found"
    result = _human_result(env, t1)
    # 0119 T-2: a person's result in review is no approval's any more — refused with the card's intervention, an admin
    # too, before any other check; nothing is written
    for user in ("u_kim", "u_lee"):
        refused = _approve(env, t1, 409, user=user, body={"result_doc_id": result})["error"]
        assert (refused["code"], refused["params"]["status"]) == ("needs_intervention", "review")
    bad = env["client"].post(f"/api/v1/runs/{_run_id(env)}/nodes/{t1}/approval", json={"x": 1}, headers=_h())
    assert bad.status_code == 422 and bad.json()["error"]["params"]["errors"] == [
        {"field": "x", "reason": "unknown_field"}]
    assert _review_status(result) == "pending_review" and _node(env, t1) == ("review", result)


class _GatePolicy:
    """Decided test rules with a gate slot (the card settings test double plus ``judge``)."""

    def select(self, *args):
        return args[0].start_keys() if len(args) == 2 else args[2]

    def admit(self, candidates, _open):
        return candidates

    def openable(self, _target, states):
        return "open" if all(s["state"] == "completed" for s in states) else "wait"

    def route(self, *_args):
        return [], []

    def is_complete(self, revision, state):
        return all(state.node(k) is not None and state.node(k).status == "done" for k in revision.node_keys())

    def judge(self, _node_key, decision, _comment):
        return "confirmed" if decision == "confirm" else "rejected"


@pytest.fixture()
def gate_decided(monkeypatch):
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    monkeypatch.setitem(policies._registered, "gate_node", object())
    gate = _GatePolicy()
    old = {}
    for key in policies.POLICY_KEYS:
        run_policies.register(key, "card_approval_gate", "1", gate)
        old[key] = policies.set_system_default(key, "card_approval_gate", "1")
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


# 0113 T#3 (T §6.4) pre-feature contract: same person-card scene (no instruction stage before the result)
def test_a_gate_card_is_not_approved_here(pre_feature_attempts, env, gate_decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    _admin_and_reviewer(env)
    t1, gate = _chain(env, [("T", LEE), ("gate", None)])
    _start(env)
    result = _human_result(env, t1)
    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)      # no file is written here
    # 0119 T-2: t1 completes automatically (T-1), no person's card approval
    assert legacy_adapter.approve(FA, result, "u_kim", legacy_adapter.AUTOMATION, node_key=t1) is True
    assert _node(env, gate)[0] == "open"
    refused = _approve(env, gate, 409)["error"]
    assert (refused["code"], refused["params"]["status"]) == ("node_is_gate", "open")
    assert _node_run(env, gate)["awaiting_approval"] is False
    decided_run = env["client"].post(f"/api/v1/runs/{_run_id(env)}/nodes/{gate}/gate-decision",
                                     json={"decision": "confirm"}, headers=_h())
    assert decided_run.status_code == 200 and _node(env, gate)[0] == "done"   # the gate keeps its own decision


# ── a rejection is no node event; the card approval approves a rejected result ─

# 0113 T#3 (T §6.4) pre-feature contract: same person-card scene; an assigned person result is judged and approved by the system (G-2)
def test_a_person_s_rejection_is_no_node_event(pre_feature_attempts, env, decided):
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    _admin_and_reviewer(env)
    (t1,) = _chain(env, [("T", LEE)])
    _start(env)
    result = _human_result(env, t1)
    before = _current(env)["last_event_seq"]
    transition_document_review(doc_id=result, action="reject", actor_user_id="u_kim",
                               user_permissions={"document.reject"}, comment="다시")
    v01_contact.after_review_decided(FA, result, "reject")             # the v0.1 reject route's contact
    run = _current(env)                                                  # reconciliation ran before the read
    node_run = next(n for n in run["node_runs"] if n["node_key"] == t1)
    assert (node_run["status"], node_run["intervention"]["kind"], run["last_event_seq"]) == (
        "review", "needs_intervention", before)                          # 0119 T-2: never a person's approval
    assert _review_status(result) == "rejected"
    assert _approve(env, t1, 409)["error"]["code"] == "needs_intervention"
    assert (_node(env, t1)[0], _review_status(result)) == ("review", "rejected")


def test_a_review_that_spent_its_count_leaves_the_card_in_intervention(env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 1), ("review_ai", "aip_review")))
    _loop_row(result, "review")
    db_documents.update(result, {"doc_review_status": "rejected"})       # the last review hop rejected it
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_count_exhausted', stop_detail = 'spent' WHERE run_id = ?", (REVIEW_RUN,))
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped"}})
    assert _node(env, key) == ("review", result)
    assert _node_run(env, key)["intervention"] == {"kind": "needs_intervention",
                                                   "reason": "review_not_passed:review_count_exhausted"}
    assert _approve(env, key, 409)["error"]["code"] == "needs_intervention"       # 0119 T-2: no person's approval
    assert (_node(env, key)[0], _review_status(result)) == ("review", "rejected")


def test_0056_T1_개정_rejected_root_is_input_and_its_card_approval_is_refused(env, decided):
    """0056 T#1 개정: Run accepts rejected root input; its document stays rejected."""
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    (t1,) = _chain(env, [("T", LEE)])
    root = f"{FA}.0001-R"
    _write_body(root, "\n# 요구사항\n\n반려된 요구사항\n")
    db_documents.update(root, {"doc_review_status": "rejected"})        # a person rejected the root (v0.1)
    _start(env)
    run = _current(env)
    root_run = next(n for n in run["node_runs"] if n["root_bound"])
    r_key = root_run["node_key"]
    assert (root_run["status"], root_run["result_doc_id"], root_run["awaiting_approval"]) == ("done", root, False)
    assert "review_rejected" not in _events(env, r_key) and run["open_node_keys"] == [t1]
    assert _sql(env["db"], "SELECT outcome FROM graph_propagations WHERE node_key = ?", (r_key,)) == [
        {"outcome": "approved"}]
    refused = _approve(env, r_key, 409, body={"result_doc_id": root})["error"]
    assert (refused["code"], refused["params"]["status"]) == ("already_approved", "done")
    assert _review_status(root) == "rejected"                            # nothing written
    run = _current(env)                                                  # the first work card follows the root input
    nodes = {n["node_key"]: n for n in run["node_runs"]}
    assert (nodes[r_key]["status"], nodes[t1]["status"], run["open_node_keys"]) == ("done", "open", [t1])


# ── an AI run that did not end on its own approves nothing ──────────────────

_STOPPED_AI_RUNS = [("cancelled", "cancelled"), ("timeout", "timeout"), ("user_paused", "user_paused"),
                    ("all_providers_failed", "providers_exhausted"), ("exited", "provider_failed"),
                    ("worker_error", None)]


@pytest.mark.parametrize("review_count", [0, 2])
@pytest.mark.parametrize("end_reason, stop_code", _STOPPED_AI_RUNS)
def test_an_ai_run_stopped_after_binding_its_result_leaves_the_card_in_intervention(
        env, decided, monkeypatch, end_reason, stop_code, review_count):
    settings = (("review_count", review_count), ("review_ai", "aip_review")) if review_count else ()
    key, result, _token = _in_review(env, monkeypatch, settings)          # the result is bound (outcome complete)
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended(end_reason=end_reason, stop_code=stop_code)
    assert calls == []                                                   # no review loop
    assert (_node(env, key), _review_status(result)) == (("review", result), "pending_review")   # no approval
    shown = _node_run(env, key)                                          # 0119 T-2: the AI's abnormal end
    assert (shown["awaiting_approval"], shown["intervention"]["kind"]) == (False, "needs_intervention")
    assert shown["intervention"]["reason"].startswith("run_not_clean:")
    assert _approve(env, key, 409)["error"]["code"] == "needs_intervention" and _node(env, key) == ("review", result)


def test_only_a_run_that_ended_on_its_own_is_a_clean_end():
    from flowgate.card_settings.review_stage import ended_cleanly

    assert ended_cleanly({"end_reason": "exited", "stop_code": None, "outcome": "complete"})
    assert ended_cleanly({"end_reason": "exited"})
    assert not any(ended_cleanly({"end_reason": e, "stop_code": c, "outcome": "complete"}) for e, c in _STOPPED_AI_RUNS)
    assert not ended_cleanly({"end_reason": "exited", "stop_code": "question_pending", "outcome": "complete"})
    assert not ended_cleanly({"outcome": "complete"})                    # no end reason: not judged clean


# ── [다음 단계] stop is not awaiting approval ────────────────────────────────────


def test_an_automatically_approved_card_never_goes_back_to_awaiting_approval_while_stopped(
        env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch)                   # an AI card, no review
    _stop(env)
    _work_run_ended()                                                    # the card approves it at once
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"
    for _ in range(2):                                                   # reads reconcile: nothing goes back
        run = _current(env)
        node_run = next(n for n in run["node_runs"] if n["node_key"] == key)
        assert (run["status"], node_run["status"], node_run["awaiting_approval"]) == ("stopped", "done", False)
        assert (run["pending_propagation_count"], run["pending_node_keys"]) == (1, [key])
    assert _approve(env, key, 409)["error"]["code"] == "already_approved"
    assert _resume(env)["status"] == "completed"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_main_mounts_the_card_approval_route_and_the_package_is_v02():
    import ast

    main = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    assert "from flowgate.card_approval.routes import router as _card_approval_router" in main
    assert 'app.include_router(_card_approval_router, prefix=f"{CONTEXT}", tags=["CardApproval"])' in main
    pkg = _SERVER_DIR / "flowgate" / "card_approval"
    assert {p.stem for p in pkg.glob("*.py")} == {"__init__", "approval", "routes", "legacy_adapter", "integration"}
    for path in pkg.glob("*.py"):                 # modules.flow_gate only through the adapter (and the port registration)
        if path.stem in ("legacy_adapter", "integration"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any(m.startswith("modules.") for m in imported), path.name


def test_the_route_follows_the_run_api_path_rule():
    from flowgate.card_approval.routes import router

    assert [(r.path, sorted(r.methods)) for r in router.routes] == [
        ("/api/v1/runs/{run_id}/nodes/{node_key}/approval", ["POST"])]


def test_startup_registers_the_card_approval_port_and_the_port_is_stdlib_only():
    import ast

    import startup
    from modules.flow_gate.graph_workflow.services import card_approval_ports as ports

    from flowgate.card_approval import integration

    integration.unregister()
    assert not ports.registered(ports.PENDING_DOCUMENTS)
    assert ports.pending_documents(FA, {"node_key": "gn_x"}, "d") == []   # unregistered: as before the port
    startup.start_card_approval_integration()
    assert ports.registered(ports.PENDING_DOCUMENTS)
    assert "start_card_approval_integration()" in (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    path = _SERVER_DIR / "modules" / "flow_gate" / "graph_workflow" / "services" / "card_approval_ports.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "threading", "typing"}
