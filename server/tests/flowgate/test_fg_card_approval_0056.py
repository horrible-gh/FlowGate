"""flowgate.v02.0056 T#1 — the card approval flow (0002-CH turn 5~10) on the Graph run.

On the same real SQLite DB, routes and fixtures as ``test_fg_card_settings`` (card documents and card settings
registered as ``startup`` does):

- every node run tells whether its card awaits a person's approval (``awaiting_approval``): a node in review
  whose result was not approved automatically and on which no AI works any more — neither the node's own AI
  run (it still holds the Flow's group AI lease) nor the card's review loop;
- a rejected bound root is input; its card is done and the next card opens, while its document stays rejected;
- an AI run that bound its result and then did not end on its own (cancelled, timeout, paused, providers
  exhausted, a provider failure) approves nothing and starts no review;
- a rejection is no node event (a person's through the v0.1 contact, or the card review's when its count is spent);
- [다음 단계] stop is not "awaiting approval": the card's automatic approval while the run is stopped makes the node
  done at once and only its propagation waits.

0083 WP-3 (0061 T#2 개정, revised): a person's approval on an idle run propagates at once again (``frontier.defers``,
``test_fg_idle_approval_0083``), and a run's states are 0061 T#2's (migration 151): a done run is ``idle`` with the
idle reason ``completed`` and keeps its Flow.

The v0.1 AI invoke admission is replaced by a recorder; the Flow's group AI lease is stubbed where a test says
an AI run holds it. Auth, RBAC and SSE delivery are stubbed as in the card settings tests.

flowgate.v02.0119 T#3 (T-2): no person approves a card any more. A card in review that nothing completes is
``needs_intervention`` (or a ``system_error``, ``node_approval.intervention``) and ``awaiting_approval`` is always false.
The T-2 contract itself is ``test_fg_card_intervention_0119`` / ``test_fg_auto_approval_0105``.

flowgate.v02.0119 T#4 (T-3, 0004-CH seq 9 T-3 · seq 11 교체 3): the card approval is removed — its package
(``flowgate.card_approval``), its Graph port (``card_approval_ports``) and its route (``POST /runs/{run}/nodes/{node}/approval``,
now 404). What was not a person's approval moved to the v0.2 completion (``flowgate.card_completion``):

- a card's completion looks at its required outputs only (``card_completion_ports.pending_outputs``): the result's
  completion and, when the card uses an instruction, that instruction approved. A line document beside them — the
  instruction line of a card that omits its instruction, written or submitted by a person — never holds the node;
- the instruction's automatic approval is the completion's own (``card_completion.instruction``): written by the system,
  no v0.1 review transition, no TR approval hook, no Graph contact, no node event;
- the card's lines and the stale lines of an ended attempt (0061 T#7) are read there (``outputs.card_lines_of``).

The cases whose premise was a person's card approval (its route, its refusal codes, its document targets and order) were
removed or rewritten to the new contract; the cases on what still holds were kept.
"""
from __future__ import annotations

import ast
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


@pytest.fixture(autouse=True)
def card_outputs_port():
    """flowgate.v02.0119 T#4 (T-3): the card's required outputs port (``card_completion_ports.pending_outputs``),
    registered as ``startup`` registers it (``card_completion.integration``) — alone, so the engine suites' completion
    stand-in (``tests/conftest.py``) keeps answering ``completion_of``. It replaced the removed card approval port."""
    from flowgate.card_completion import outputs
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    ports.register(ports.PENDING_OUTPUTS, outputs.pending_outputs)
    yield
    ports.unregister(ports.PENDING_OUTPUTS)


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


def _approval_route_gone(env, key, run_id=None):
    """flowgate.v02.0119 T#4 (T-3): the card approval route is unmounted — no person approves a card (404)."""
    res = env["client"].post(f"/api/v1/runs/{run_id or _run_id(env)}/nodes/{key}/approval", json=None,
                             headers={**_h(), "Authorization": "Bearer user:u_kim"})
    assert res.status_code == 404, res.text


def _events(env, key):
    return [r["cause"] for r in _sql(env["db"], "SELECT cause FROM graph_run_events WHERE node_key = ? "
                                                "ORDER BY event_seq", (key,))]


def _lease(monkeypatch, run_id):
    """An AI run ``run_id`` holds the Flow's group AI lease (read only and active reads); None: released."""
    from datetime import datetime, timedelta, timezone

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
    """A person's v0.1 approval of one document (the ``review_transitions/approve`` route and its contact). On a Graph
    Flow it completes nothing by itself (0119 T-1); here the engine suites' completion stand-in reads it."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    transition_document_review(doc_id=doc_id, action="approve", actor_user_id="u_kim",
                               user_permissions={"document.approve"})
    if contact:
        v01_contact.after_review_decided(FA, doc_id, "approve")


def _causes_after(env, key, seq):
    return [r["cause"] for r in _sql(env["db"], "SELECT cause FROM graph_run_events WHERE event_seq > ? "
                                                "AND node_key = ? ORDER BY event_seq", (seq, key))]


def _human_result(env, key, seq=81, typ="T", state="review"):
    """A person's card (``LEE``) registers its result with the node's token. flowgate.v02.0119 T#4 rework
    (rej_01M6DQW6YF0SCQ5D P1): a pre-feature person card follows its card's review setting — with review 'none' its result
    completes at once (``state`` done), with a review set it stays in review while its review works."""
    from modules.flow_gate.graph_workflow.api import v01_contact

    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid",
                 (key,))[0]["token_id"]
    result = _result(seq, typ)
    v01_contact.after_inbox_registered(FA, result, typ, token)
    assert _node(env, key) == (state, result)
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


def _reopened(doc_id):
    """The card's instruction is back in review (a person put it back / a new submission): not approved any more."""
    from modules.flow_gate.db import documents as db_documents

    db_documents.update(doc_id, {"doc_review_status": "pending_review"})


@pytest.fixture()
def no_v01_path(monkeypatch):
    """Any v0.1 approval step reached by the v0.2 completion fails the test."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import tr_commit_service
    from modules.flow_gate.workflow import pipeline_service

    def refuse(*_a, **_k):
        raise AssertionError("the v0.1 approval path was taken")

    monkeypatch.setattr(pipeline_service, "transition_document_review", refuse)
    monkeypatch.setattr(tr_commit_service, "on_document_approved", refuse)
    monkeypatch.setattr(v01_contact, "after_review_decided", refuse)


# ── awaiting_approval / intervention ─────────────────────────────────────────


def test_an_ai_result_is_in_intervention_only_when_no_ai_works_on_it(env, decided, monkeypatch):
    """0119 T-2 (was ``..._awaits_approval_only_when_...``): the same judgement now tells the card's intervention."""
    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    _lease(monkeypatch, WORK_RUN)                                       # the node's own AI run has not ended
    assert (_node_run(env, key)["awaiting_approval"], _node_run(env, key)["intervention"]) == (False, None)
    _lease(monkeypatch, None)                                            # the run ended (lease released)
    _loop_row(result, "review")                                          # the card's review works on it
    assert _node_run(env, key)["intervention"] is None
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_count_exhausted', stop_detail = 'spent' WHERE run_id = ?", (REVIEW_RUN,))
    shown = _node_run(env, key)                                          # count spent: it needs intervention
    assert (shown["awaiting_approval"], shown["intervention"]["kind"]) == (False, "needs_intervention")
    _approval_route_gone(env, key)                                       # 0119 T-3: no person's approval at all


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


# ── 0119 T-3: the instruction's approval is the completion's own; the required outputs ──


def test_an_instruction_approval_is_the_systems_and_makes_no_node_event(env, decided, monkeypatch, no_v01_path):
    """0119 T-3: ``card_completion.instruction.approve`` writes the instruction's approved display by the system — no
    v0.1 review transition, no TR approval hook, no Graph contact — and binds nothing (no node event)."""
    from flowgate.card_completion import instruction as card_instruction

    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _write_body(instruction, "\n## 작업\n\n사람이 쓴 지시서\n")         # written in the editor, never submitted
    before = _current(env)["last_event_seq"]
    assert card_instruction.approve(instruction, requested_by="u_kim") == card_instruction.WRITTEN
    assert _review_status(instruction) == "approved"
    assert _sql(env["db"], "SELECT status FROM documents WHERE doc_id = ?", (instruction,))[0]["status"] == "open"
    shown = _sql(env["db"], "SELECT note FROM events WHERE doc_id = ? AND event_type = ?",
                 (instruction, "card_instruction_approved"))
    assert len(shown) == 1 and '"actor": "system"' in shown[0]["note"] and '"requested_by": "u_kim"' in shown[0]["note"]
    assert card_instruction.approve(instruction, requested_by="u_kim") == card_instruction.ALREADY   # once
    assert _current(env)["last_event_seq"] == before and _node(env, key) == ("review", result)


def test_an_instruction_approval_refuses_an_empty_body_and_a_moved_revision(env, decided, monkeypatch, no_v01_path):
    from flowgate.card_completion import instruction as card_instruction
    from modules.flow_gate.db import documents as db_documents

    key, _result_doc, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")                     # the card's untouched empty draft
    with pytest.raises(card_instruction.InstructionRefused):
        card_instruction.approve(instruction, requested_by="u_kim")
    _write_body(instruction, "\n## 작업\n\n본문\n")
    revision = int(db_documents.get_by_id(instruction).get("revision_no") or 0)
    with pytest.raises(card_instruction.RevisionMoved):
        card_instruction.approve(instruction, requested_by="u_kim", expected_revision=revision + 1)
    assert _review_status(instruction) != "approved"


def test_a_complete_result_waits_for_the_instruction_its_card_uses_and_follows_it_once(env, decided, monkeypatch):
    """0119 T-3 (필수 산출물): with its instruction used, a complete result (here the engine suites' stand-in reads the
    approved result as complete) waits in review while the instruction is not approved; once the instruction is
    approved — by the completion's own approval, which tells the Graph nothing — the next read's reconciliation makes
    the node done once (no contact is needed)."""
    from flowgate.card_completion import instruction as card_instruction

    key, result, _token = _in_review(env, monkeypatch, (), "TR")          # through its instruction stage
    instruction = _line_doc(env, key, "instruction")
    assert _review_status(instruction) == "approved"                     # the stage approved it (the system)
    _reopened(instruction)
    seq = _current(env)["last_event_seq"]
    _v01_approve(result)                                                 # the result complete (stand-in)
    node_run = _node_run(env, key)                                       # the read reconciles: its instruction waits
    assert (node_run["status"], node_run["intervention"]["kind"]) == ("review", "needs_intervention")
    assert _causes_after(env, key, seq) == []
    assert card_instruction.approve(instruction, requested_by="u_kim") == card_instruction.WRITTEN
    assert _current(env) is not None and _node(env, key) == ("done", result)
    assert _causes_after(env, key, seq) == ["review_approved"]
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_propagations WHERE node_key = ?", (key,))[0]["n"] == 1


def test_a_written_instruction_line_of_a_card_that_omits_its_instruction_never_holds_its_result(
        env, decided, monkeypatch):
    """0119 T-3: a line document beside the required outputs (a person wrote and submitted the instruction line of a
    card that omits its instruction) is history, not an output: the result's automatic completion moves the node on and
    the line document is left as it is."""
    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _submit_written(instruction)                                         # a person wrote the instruction line
    _work_run_ended()                                                    # no review: the result is complete
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"
    assert _review_status(instruction) == "pending_review"               # kept as it is, never approved here
    assert _node_run(env, key)["intervention"] is None


def test_a_complete_result_revised_while_it_waits_for_its_instruction_is_followed_after_a_lost_contact(
        env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    key, old, _token = _in_review(env, monkeypatch, (), "TR")
    instruction = _line_doc(env, key, "instruction")
    _reopened(instruction)
    seq = _current(env)["last_event_seq"]
    _v01_approve(old)                                                    # the TR complete: it waits for its T
    assert _node(env, key) == ("review", old)
    new = _result(91, "TR")                                              # the TR is revised and its
    db_documents.set_superseded_by(old, new)                             # registration's contact is lost
    run = _current(env)                                                  # the read reconciles: the revision first
    node_run = next(n for n in run["node_runs"] if n["node_key"] == key)
    assert (node_run["status"], node_run["result_doc_id"], node_run["intervention"]["kind"]) == (
        "review", new, "needs_intervention")                             # 0119 T-2: no person's approval follows
    assert _causes_after(env, key, seq) == ["revised"]


def test_the_reconciliation_follows_a_live_revision_before_a_complete_result_s_outputs(monkeypatch):
    from types import SimpleNamespace

    from modules.flow_gate.graph_workflow.domain.frontier import RunEvent
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    from modules.flow_gate.graph_workflow.services import result_binder as rb
    from modules.flow_gate.services import graph_execution_hooks

    docs = {"tr_old": {"doc_id": "tr_old", "doc_review_status": "approved", "superseded_by": "tr_new",
                       "group_id": "f", "status": "open"},
            "tr_new": {"doc_id": "tr_new", "doc_review_status": "pending_review", "group_id": "f", "status": "open"}}
    ctx = SimpleNamespace(flow_id="f", run={"run_id": "r"}, run_id="r", node_rows={"gn_t": {"node_key": "gn_t"}},
                          docs=lambda: docs)
    s = SimpleNamespace(node=lambda key: SimpleNamespace(status="review", result_doc_id="tr_old"))
    monkeypatch.setattr(ports, "completion_of", lambda flow_id, run, key, doc: {"completion_id": "c", "legacy": False})
    monkeypatch.setattr(card_settings_ports, "result_review_pending", lambda *a: False)
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *a, **k: None)
    monkeypatch.setattr(ports, "pending_outputs", lambda flow_id, node, result_doc_id: ["t1"])   # its T waits
    assert rb.next_missing_event(ctx, s, "gn_t") == RunEvent("revised", "gn_t", doc_id="tr_new", replaces="tr_old")
    docs["tr_old"]["superseded_by"] = None                               # no revision: the instruction still waits
    assert rb.next_missing_event(ctx, s, "gn_t") is None
    monkeypatch.setattr(ports, "pending_outputs", lambda flow_id, node, result_doc_id: [])        # outputs complete
    assert rb.next_missing_event(ctx, s, "gn_t") == RunEvent("review_approved", "gn_t", doc_id="tr_old")


def test_the_pending_outputs_port_names_only_the_instruction_the_card_uses(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    key, result, _token = _in_review(env, monkeypatch, (), "TR")          # its instruction used (T)
    instruction = _line_doc(env, key, "instruction")
    t_card = {"node_key": key, "node_type": "document", "doc_type": "T"}
    assert ports.pending_outputs(FA, t_card, result) == []               # approved by its stage
    _reopened(instruction)
    assert ports.pending_outputs(FA, t_card, result) == [instruction]
    assert ports.pending_outputs(FA, t_card, instruction) == []          # never the result itself
    _v01_approve(instruction, contact=False)
    assert ports.pending_outputs(FA, t_card, result) == []
    assert ports.pending_outputs(FA, {"node_key": key, "node_type": "gate"}, None) == []


def test_a_card_that_omits_its_instruction_has_no_pending_output(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    t_card = {"node_key": key, "node_type": "document", "doc_type": "T"}
    _write_body(instruction, "written")
    assert ports.pending_outputs(FA, t_card, result) == []
    _submit_written(instruction)
    assert ports.pending_outputs(FA, t_card, result) == []


def test_the_instruction_a_card_requires_by_its_kind_and_its_stored_use(monkeypatch):
    """``card_settings.settings.required_instruction``: an assigned attempt's plan names it; a pre-feature attempt reads
    the card's stored instruction use (a run card's instruction stage type, a test card's TS unless omitted)."""
    from flowgate.card_settings import repository, settings

    stored = {}
    plan = {"value": None}
    monkeypatch.setattr(settings, "_assigned", lambda flow_id, n: ("att", plan["value"]) if plan["value"] else None)
    monkeypatch.setattr(repository, "available", lambda: True)
    monkeypatch.setattr(repository, "values", lambda flow_id, key: dict(stored))

    def required(node_type, doc_type):
        return settings.required_instruction("f", {"node_key": "gn_x", "node_type": node_type, "doc_type": doc_type})

    assert [required("document", t) for t in ("N", "T", "T2", "D")] == ["N", "T", "T2", "DS"]
    assert required("custom", None) == "T" and required("document", "TS") == "TS"
    assert [required("document", t) for t in ("WP", "R", "B")] == [None, None, None] and required("gate", None) is None
    stored["instruction"] = "omit"
    assert required("document", "T") is None and required("document", "TS") is None
    plan["value"] = {"instruction_type": "DS"}                           # an assigned attempt: its plan decides
    assert required("document", "T") == "DS"
    plan["value"] = {"instruction_type": None, "result_type": "WP"}
    assert required("document", "WP") is None


def test_the_output_lines_moved_from_the_card_approval_read_written_and_stale_documents():
    from flowgate.card_completion.outputs import is_target

    assert is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": "pending_review"})
    assert not is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": None})            # a draft
    assert not is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": None, "written": False})
    assert is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": None, "written": True})
    assert not is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": "pending_review",
                          "stale": True})                                # 0061 T#7: an ended attempt's document
    assert not is_target({"slot": "instruction", "doc_id": None, "doc_review_status": "approved"})


# ── 0119 T-3: the route is gone; a person's result completes its card (rework: rej_01M6DQW6YF0SCQ5D P1) ──

# 0113 T#3 (T §6.4) pre-feature contract: a person card's dispatch token registers its result directly; an assigned person RUN card writes its instruction first (L-2 §2.1/§2.7) — test_fg_ai_roles_l2_assignment.py::test_g2_*
def test_the_card_approval_route_is_gone_and_a_person_s_result_completes_its_card(pre_feature_attempts, env, decided):
    """0119 T-3 rework (rej_01M6DQW6YF0SCQ5D P1, was ``..._is_the_card_s_intervention``): no route takes a person's
    approval, and a pre-feature person card whose card reviews nothing completes its result as ``human_submitted``."""
    _admin_and_reviewer(env)
    t1, t2 = _chain(env, [("T", LEE), ("T", LEE)])
    _start(env)
    for key in (t1, t2, "gn_nothing"):
        _approval_route_gone(env, key)
    _approval_route_gone(env, t1, run_id="run_missing")
    result = _human_result(env, t1, state="done")                       # review 'none': completed as submitted
    _approval_route_gone(env, t1)
    shown = _node_run(env, t1)
    assert (shown["status"], shown["awaiting_approval"], shown["intervention"]) == ("done", False, None)
    assert "viewer" not in _current(env)                                 # 0119 T-3: no viewer.can_approve
    assert _review_status(result) == "approved" and _node(env, t2)[0] == "open"
    [done] = _sql(env["db"], "SELECT cause, actor_kind FROM graph_card_completions WHERE node_key = ?", (t1,))
    assert (done["cause"], done["actor_kind"]) == ("human_submitted", "user")


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
        run_policies.register(key, "card_outputs_gate", "1", gate)
        old[key] = policies.set_system_default(key, "card_outputs_gate", "1")
    yield
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


# 0113 T#3 (T §6.4) pre-feature contract: same person-card scene (no instruction stage before the result)
def test_a_gate_card_keeps_its_own_confirm(pre_feature_attempts, env, gate_decided, monkeypatch):
    _admin_and_reviewer(env)
    t1, gate = _chain(env, [("T", LEE), ("gate", None)])
    _start(env)
    # 0119 T-2 / T-3 rework: t1 completes automatically as submitted (review 'none'), no person's card approval
    _human_result(env, t1, state="done")
    assert _node(env, gate)[0] == "open"
    _approval_route_gone(env, gate)
    assert _node_run(env, gate)["awaiting_approval"] is False
    decided_run = env["client"].post(f"/api/v1/runs/{_run_id(env)}/nodes/{gate}/gate-decision",
                                     json={"decision": "confirm"}, headers=_h())
    assert decided_run.status_code == 200 and _node(env, gate)[0] == "done"   # the gate keeps its own decision


# ── a rejection is no node event ─────────────────────────────────────────────

# 0113 T#3 (T §6.4) pre-feature contract: same person-card scene; an assigned person result is judged and approved by the system (G-2)
def test_a_person_s_rejection_is_no_node_event(pre_feature_attempts, env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    from test_fg_card_settings import legacy_setting

    _admin_and_reviewer(env)
    (t1,) = _chain(env, [("T", LEE)])
    for field, value in (("review_count", 1), ("review_ai", "aip_review")):   # its card reviews it (0119 T-3 rework)
        legacy_setting(env, FA, t1, field, value)
    calls = _recorded_start_run(monkeypatch)
    _start(env)
    result = _human_result(env, t1)                                      # in review while its review works
    assert len({c.get("graph_exec_id") for c in calls}) == 1           # its one review (a redelivery counted once)
    before = _current(env)["last_event_seq"]
    transition_document_review(doc_id=result, action="reject", actor_user_id="u_kim",
                               user_permissions={"document.reject"}, comment="다시")
    v01_contact.after_review_decided(FA, result, "reject")             # the v0.1 reject route's contact
    run = _current(env)                                                  # reconciliation ran before the read
    node_run = next(n for n in run["node_runs"] if n["node_key"] == t1)
    assert (node_run["status"], run["last_event_seq"]) == ("review", before)     # no node event, no approval
    assert _review_status(result) == "rejected" and len({c.get("graph_exec_id") for c in calls}) == 1
    _approval_route_gone(env, t1)
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
    _approval_route_gone(env, key)                                       # 0119 T-3: no person's approval
    assert (_node(env, key)[0], _review_status(result)) == ("review", "rejected")


def test_0056_T1_개정_rejected_root_is_input_and_its_card_is_done(env, decided):
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
    _approval_route_gone(env, r_key)
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
    _approval_route_gone(env, key)
    assert _node(env, key) == ("review", result)


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
    _approval_route_gone(env, key)
    assert _resume(env)["status"] == "completed"


# ── wiring: the card approval is removed (0119 T-3) ──────────────────────────

def _imports(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")      # a BOM is no import
    try:
        tree = ast.parse(text)
    except SyntaxError:                                                  # not Python 3 source: its import lines
        return {part for line in text.splitlines() if line.lstrip().startswith(("import ", "from "))
                for part in line.replace(",", " ").split()[1:2]}
    out = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    out |= {f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
            for a in n.names}
    out |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    return out


def test_the_card_approval_package_port_and_route_are_removed():
    """0119 T-3 ③ / ④: nothing imports ``flowgate.card_approval`` or ``card_approval_ports`` (the server's sources and
    its tests); the package, the port and the route mount are gone, and ``startup`` no longer registers it."""
    removed = ("flowgate.card_approval", "modules.flow_gate.graph_workflow.services.card_approval_ports")
    offenders = []
    for path in _SERVER_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        found = [m for m in _imports(path) if m.startswith(removed) or m.endswith(".card_approval_ports")]
        if found:
            offenders.append((str(path.relative_to(_SERVER_DIR)), found))
    assert offenders == []
    assert not (_SERVER_DIR / "flowgate" / "card_approval").joinpath("__init__.py").exists()
    assert not list((_SERVER_DIR / "flowgate" / "card_approval").glob("*.py"))
    assert not (_SERVER_DIR / "modules" / "flow_gate" / "graph_workflow" / "services" / "card_approval_ports.py").exists()
    main = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    assert "card_approval" not in main
    startup_src = (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    assert "def start_card_approval_integration" not in startup_src
    assert "start_card_approval_integration()" not in startup_src


def test_startup_registers_the_pending_outputs_port_and_the_port_is_stdlib_only():
    import startup
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    from flowgate.card_completion import integration

    integration.unregister()
    assert not ports.registered(ports.PENDING_OUTPUTS)
    assert ports.pending_outputs(FA, {"node_key": "gn_x"}, "d") == []    # unregistered: the result alone
    startup.start_card_completion_integration()
    try:
        assert ports.registered(ports.PENDING_OUTPUTS)
    finally:
        integration.unregister()
    path = _SERVER_DIR / "modules" / "flow_gate" / "graph_workflow" / "services" / "card_completion_ports.py"
    imported = {m.split(".")[0] for m in _imports(path)}
    assert imported <= {"__future__", "logging", "threading", "typing"}


# ── 0119 T-3 rework (rej_01M6DQW6YF0SCQ5D): a TS instruction's own effect, a required instruction missing ──

TS_AUTOMATED = (
    "---\nproject: flowgate\ntype: TS\ntest_contract_version: 2\n---\n# 시험 사양\n\n범위: 카드 완료.\n\n## 시험 사양\n\n"
    "### TC-001: 자동 Case\n- category: normal\n- requirement: AC-1\n- execution_mode: automated\n- required: true\n"
    "- procedure: 1. 실행한다\n- expected: 통과\n- check_points: 결과\n- automation_ref: tests/test_x.py::test_tc_1\n\n"
)
# Two Cases with one id: the specification does not validate (``test_spec_service.parse_spec`` ``duplicate_case_id``).
TS_INVALID = TS_AUTOMATED + "### TC-001: 같은 id\n- category: normal\n- execution_mode: manual\n- required: true\n" \
                            "- procedure: 본다\n- expected: 보인다\n- check_points: UI\n\n"


@pytest.fixture()
def ts_admission(monkeypatch, tmp_path):
    """The real TS admission (``spec_execution_service.admit_on_approval``: its run row queued, its asset check) on the
    suite's real DB; the Group worktree is ``tmp_path`` (``source_root``), the report it opens and the run's start notice
    are recorded instead of written / broadcast."""
    from modules.flow_gate.services import test_basis_service
    from modules.flow_gate.services import test_run_service as runner

    opened, told = [], []
    monkeypatch.setattr(test_basis_service, "source_root", lambda doc: tmp_path)
    monkeypatch.setattr(runner, "open_execution_tsr",
                        lambda doc, cases, run, locale="ko": opened.append(run["run_id"]) or f"tsr_of_{doc['doc_id']}")
    monkeypatch.setattr(runner, "_emit_started", lambda doc, run: told.append(run["run_id"]))
    monkeypatch.setattr(runner, "_broadcast", lambda *a, **k: None)
    return {"root": tmp_path, "opened": opened, "told": told}


def _ts_instruction(seq, body, asset_root=None):
    """A TS a test card uses as its instruction, written and submitted (``body``: its specification); ``asset_root``: its
    automated Case's test file exists there."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    doc_id = _result(seq, "TS")
    (get_storage_root("flowgate", create=True) / "docs" / f"{doc_id}.md").write_text(body, encoding="utf-8")
    db_documents.update(doc_id, {"doc_review_status": "pending_review"})
    if asset_root is not None:
        (asset_root / "tests").mkdir(parents=True, exist_ok=True)
        (asset_root / "tests" / "test_x.py").write_text("def test_tc_1():\n    assert True\n", encoding="utf-8")
    return doc_id


def _ts_runs(env, doc_id):
    return _sql(env["db"], "SELECT run_id, runner_id, status FROM test_runs WHERE doc_id = ?", (doc_id,))


def _doc_events(env, doc_id, event_type):
    return [r["note"] for r in _sql(env["db"], "SELECT note FROM events WHERE doc_id = ? AND event_type = ?",
                                    (doc_id, event_type))]


def test_a_ts_instruction_s_approval_admits_its_test_run_once_with_its_approved_display(
        env, decided, monkeypatch, no_v01_path, ts_admission):
    """rej_01M6DQW6YF0SCQ5D P1: a test card's TS instruction approved by the v0.2 (``card_completion.instruction``) runs the
    TS's own effect the v0.1 approval carried — its specification validated, its automated Case's asset checked, its
    run queued and its TSR opened (``admit_on_approval``) with the admission event — in the approval's transaction, by
    the system with the run's issuer as ``requested_by`` / the run's runner. Told again it admits nothing more."""
    from flowgate.card_completion import instruction as card_instruction

    _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")       # a run with its issuer
    ts = _ts_instruction(95, TS_AUTOMATED, ts_admission["root"])
    assert card_instruction.approve(ts, requested_by="u_kim") == card_instruction.WRITTEN
    assert _review_status(ts) == "approved"
    [run] = _ts_runs(env, ts)                                            # one run, queued for the issuer
    assert run["runner_id"] == "u_kim" and ts_admission["opened"] == [run["run_id"]]
    [admitted] = _doc_events(env, ts, "test_spec_execution_admitted")
    assert '"actor": "system"' in admitted and '"requested_by": "u_kim"' in admitted and run["run_id"] in admitted
    assert len(_doc_events(env, ts, "card_instruction_approved")) == 1
    assert ts_admission["told"] == [run["run_id"]]                       # told after the commit
    assert card_instruction.approve(ts, requested_by="u_kim") == card_instruction.ALREADY     # told again
    assert len(_ts_runs(env, ts)) == 1 and len(_doc_events(env, ts, "test_spec_execution_admitted")) == 1
    assert ts_admission["opened"] == [run["run_id"]]


@pytest.mark.parametrize("case", ["invalid_specification", "missing_test_asset", "run_in_progress"])
def test_a_ts_instruction_that_does_not_pass_its_gate_is_not_approved_and_starts_no_result_stage(
        env, decided, monkeypatch, no_v01_path, ts_admission, case):
    """rej_01M6DQW6YF0SCQ5D P1: a TS whose specification does not validate, whose automated Case's test file is missing,
    or whose earlier run is still in progress is refused — not approved, no run, no admission event, no approval event
    — and the instruction stage's caller (``review_stage._reconciled_result_stage``, as every caller of
    ``approve_instruction``) starts no result stage: a system error recorded (``approval_refused``), retried."""
    from contextlib import contextmanager

    from flowgate.card_completion import instruction as card_instruction
    from flowgate.card_settings import legacy_adapter, review_stage
    from modules.flow_gate.services import spec_execution_service

    key, _result_doc, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    body = TS_INVALID if case == "invalid_specification" else TS_AUTOMATED
    ts = _ts_instruction(96, body, None if case == "missing_test_asset" else ts_admission["root"])
    if case == "run_in_progress":
        monkeypatch.setattr(spec_execution_service, "admission_blocker",
                            lambda doc_id: {"error": "run_in_progress", "run_id": "tr_running"})
    with pytest.raises(card_instruction.InstructionRefused) as refused:
        card_instruction.approve(ts, requested_by="u_kim")
    assert refused.value.code == {"invalid_specification": "test_gate_refused", "missing_test_asset": "ts_automation_assets",
                                  "run_in_progress": "ts_run_blocked"}[case]
    started = []
    monkeypatch.setattr(legacy_adapter, "start_result_stage", lambda *a, **k: started.append(a))

    @contextmanager
    def owned():
        yield True

    run_id = _run_id(env)
    node_row = {"node_key": key, "node_type": "document", "doc_type": "T"}
    review_stage._reconciled_result_stage(FA, run_id, key, node_row, ts, "u_kim", None, hold=owned())
    assert started == []                                                 # no result stage
    assert _review_status(ts) == "pending_review" and _ts_runs(env, ts) == []
    assert _doc_events(env, ts, "test_spec_execution_admitted") == [] == _doc_events(env, ts, "card_instruction_approved")
    assert ts_admission["opened"] == [] and "approval_refused" in legacy_adapter.approval_waits(run_id, key, ts)


def test_a_valid_ts_instruction_s_approval_starts_its_result_stage_once(env, decided, monkeypatch, no_v01_path,
                                                                       ts_admission):
    from contextlib import contextmanager

    from flowgate.card_settings import legacy_adapter, review_stage

    key, _result_doc, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    ts = _ts_instruction(97, TS_AUTOMATED, ts_admission["root"])
    started = []
    monkeypatch.setattr(legacy_adapter, "start_result_stage", lambda *a, **k: started.append(a))

    @contextmanager
    def owned():
        yield True

    node_row = {"node_key": key, "node_type": "document", "doc_type": "T"}
    review_stage._reconciled_result_stage(FA, _run_id(env), key, node_row, ts, "u_kim", None, hold=owned())
    assert [a[3] for a in started] == [ts] and _review_status(ts) == "approved"     # approved, then its result stage
    assert review_stage.approve_instruction(FA, node_row, ts, "u_kim") is True       # told again: approved already
    assert len(_ts_runs(env, ts)) == 1 and len(_doc_events(env, ts, "test_spec_execution_admitted")) == 1


def test_the_required_instruction_judgement_names_a_missing_empty_or_stale_instruction(monkeypatch):
    """rej_01M6DQW6YF0SCQ5D P2 (``outputs.pending_outputs``): with its instruction used, a card whose instruction has no live
    document — no line document, its untouched empty draft, an ended attempt's document — is not complete
    (``missing:<type>``); a live one not approved is named; approved, the card's outputs are complete. Exempt only: no
    instruction used, a one-stage node (its result of the instruction's type, a node run with no stage)."""
    from flowgate.card_completion import legacy_adapter, outputs
    from flowgate.card_documents import decision_repository

    # 0126 T#2: the output judgement also reads the human-decision ledger; this stubbed test has no store, so the
    # projection sees no decisions and the document's own review status decides (the ledger: test_fg_human_*_0126).
    monkeypatch.setattr(decision_repository, "available", lambda: False)
    lines, used, row, result_type = [], {"type": "T"}, {"stage": "result"}, {"type": "TR"}
    monkeypatch.setattr(outputs, "card_lines_of", lambda flow_id, node: [dict(line) for line in lines])
    monkeypatch.setattr(outputs, "instruction_type", lambda flow_id, node: used["type"])
    monkeypatch.setattr(legacy_adapter, "document", lambda doc_id: {"doc_id": doc_id, "type_code": result_type["type"]})
    monkeypatch.setattr(legacy_adapter, "active_run", lambda flow_id: {"run_id": "r"})
    monkeypatch.setattr(legacy_adapter, "node_run", lambda run_id, key: dict(row) if row is not None else None)
    card = {"node_key": "gn_t", "node_type": "document", "doc_type": "T"}

    def pending():
        return outputs.pending_outputs("f", card, "tr_1")

    assert pending() == ["missing:T"]                                    # no instruction line document
    lines[:] = [{"slot": "instruction", "doc_type": "T", "doc_id": "t_1", "doc_review_status": None, "written": False}]
    assert pending() == ["missing:T"]                                    # its untouched empty draft
    lines[0].update(doc_review_status="pending_review", stale=True)
    assert pending() == ["missing:T"]                                    # an ended attempt's document
    lines[0].pop("stale")
    assert pending() == ["t_1"]                                          # live, not approved
    lines[0]["doc_review_status"] = "approved"
    assert pending() == []                                               # approved: complete
    lines[:] = [{"slot": "report", "doc_type": "T", "doc_id": "t_9", "doc_review_status": "pending_review"}]
    assert pending() == ["missing:T"]                                    # another line's document is no instruction
    used["type"] = None
    assert pending() == []                                               # the instruction omitted
    used["type"], lines[:] = "T", []
    row["stage"] = None
    assert pending() == []                                               # a node run with no stage: one stage
    row["stage"], result_type["type"] = "result", "T"
    assert pending() == []                                               # its result is the instruction's type
    assert outputs.pending_outputs("f", {"node_key": "gn_g", "node_type": "gate"}, None) == []


@pytest.mark.parametrize("case", ["missing", "empty", "stale"])
def test_a_complete_result_stays_in_review_while_its_required_instruction_is_missing_empty_or_stale(
        env, decided, monkeypatch, case):
    """rej_01M6DQW6YF0SCQ5D P2: a complete result (the engine suites' stand-in reads the approved result as complete) of a
    card that uses its instruction stays in review while that instruction has no live document; once a valid
    instruction is approved, the next read's reconciliation makes the node done once (one propagation)."""
    from flowgate.card_completion import instruction as card_instruction
    from flowgate.card_completion import legacy_adapter as completion_records
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    key, result, _token = _in_review(env, monkeypatch, (), "TR")          # through its instruction stage (T)
    instruction = _line_doc(env, key, "instruction")
    t_card = {"node_key": key, "node_type": "document", "doc_type": "T"}
    kept = _sql(env["db"], "SELECT * FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
                (FA, key, "instruction"))
    if case == "missing":
        _sql(env["db"], "DELETE FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
             (FA, key, "instruction"))
    elif case == "empty":
        _write_body(instruction, "")
        _sql(env["db"], "UPDATE documents SET doc_review_status = NULL WHERE doc_id = ?", (instruction,))
    else:
        _reopened(instruction)
        monkeypatch.setattr(completion_records, "stale_documents", lambda flow_id, node_key: {instruction})
    assert ports.pending_outputs(FA, t_card, result) == ["missing:T"]
    seq = _current(env)["last_event_seq"]
    _v01_approve(result)                                                 # the result complete (stand-in)
    shown = _node_run(env, key)
    assert (shown["status"], shown["intervention"]["kind"]) == ("review", "needs_intervention")
    assert _causes_after(env, key, seq) == []
    if case == "missing":
        row = dict(kept[0])
        columns = ", ".join(row)
        _sql(env["db"], f"INSERT INTO graph_node_documents ({columns}) VALUES ({', '.join('?' for _ in row)})",
             tuple(row.values()))
    elif case == "empty":
        _write_body(instruction, "\n## 작업\n\n다시 쓴 지시서\n")
        assert card_instruction.approve(instruction, requested_by="u_kim") == card_instruction.WRITTEN
    else:
        monkeypatch.setattr(completion_records, "stale_documents", lambda flow_id, node_key: set())
        assert card_instruction.approve(instruction, requested_by="u_kim") == card_instruction.WRITTEN
    assert ports.pending_outputs(FA, t_card, result) == []
    assert _current(env) is not None and _node(env, key) == ("done", result)
    _current(env)                                                        # read again: nothing more
    assert _causes_after(env, key, seq) == ["review_approved"]
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_propagations WHERE node_key = ?", (key,))[0]["n"] == 1
