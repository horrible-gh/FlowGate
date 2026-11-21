"""the card approval flow (CH ~10) on the Graph run.

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

WP-3 (revised): a person's approval on an idle run propagates at once again (``frontier.defers``,
``test_fg_idle_approval_0083``), and a run's states are the earlier implementation's (migration 151): a done run is ``idle`` with the
idle reason ``completed`` and keeps its Flow.

The v0.1 AI invoke admission is replaced by a recorder; the Flow's group AI lease is stubbed where a test says
an AI run holds it. Auth, RBAC and SSE delivery are stubbed as in the card settings tests.

(T-2): no person approves a card any more. A card in review that nothing completes is
``needs_intervention`` (or a ``system_error``, ``node_approval.intervention``) and ``awaiting_approval`` is always false.
The T-2 contract itself is ``test_fg_card_intervention_0119`` / ``test_fg_auto_approval_0105``.

(T-3, the discussion seq 9 T-3 · seq 11 replacement 3): the card approval is removed — its package
(``flowgate.card_approval``), its Graph port (``card_approval_ports``) and its route (``POST /runs/{run}/nodes/{node}/approval``,
now 404). What was not a person's approval moved to the v0.2 completion (``flowgate.card_completion``):

- a card's completion looks at its required outputs only (``card_completion_ports.pending_outputs``): the result's
  completion and, when the card uses an instruction, that instruction approved. A line document beside them — the
  instruction line of a card that omits its instruction, written or submitted by a person — never holds the node;
- the instruction's automatic approval is the completion's own (``card_completion.instruction``): written by the system,
  no v0.1 review transition, no TR approval hook, no Graph contact, no node event;
- the card's lines and the stale lines of an ended attempt are read there (``outputs.card_lines_of``).

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
# (T): this suite's cards are cards made before the AI role feature (their legacy fields:
# assignee, run_ai, review_count / review_ai — L-1 §2.4.5); the stage assignment feature stays on, so each new attempt
# runs the assigned path (L-2) resolved from those fields. Pre-feature attempts are made explicitly
# (``pre_feature_attempts``) by the tests of the pre-feature contract.
pytestmark = pytest.mark.usefixtures("legacy_cards")

_SERVER_DIR = Path(__file__).resolve().parents[2]
LEE = {"kind": "human", "user_id": "u_lee", "provider_id": None}


@pytest.fixture(autouse=True)
def card_outputs_port():
    """(T-3): the card's required outputs port (``card_completion_ports.pending_outputs``),
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
    """(T-3): the card approval route is unmounted — no person approves a card ."""
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
    """A person's card (``LEE``) registers its result with the node's token. the earlier change the earlier implementation rework
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
    """T-2 (was ``..._awaits_approval_only_when_...``): the same judgement now tells the card's intervention."""
    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review"), ("after_review_exhausted", "stop")))
    _lease(monkeypatch, WORK_RUN)                                       # the node's own AI run has not ended
    assert (_node_run(env, key)["awaiting_approval"], _node_run(env, key)["intervention"]) == (False, None)
    _lease(monkeypatch, None)                                            # the run ended (lease released)
    _loop_row(result, "review")                                          # the card's review works on it
    assert _node_run(env, key)["intervention"] is None
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_count_exhausted', stop_detail = 'spent' WHERE run_id = ?", (REVIEW_RUN,))
    shown = _node_run(env, key)                                          # count spent: it needs intervention
    assert (shown["awaiting_approval"], shown["intervention"]["kind"]) == (False, "needs_intervention")
    _approval_route_gone(env, key)                                       # T-3: no person's approval at all


def test_the_node_approval_judgement_by_state():
    from modules.flow_gate.graph_workflow.services import node_approval as na

    review = {"node_key": "gn_x", "status": "review", "result_doc_id": "d"}
    # T-2: a card in review nothing completes is in intervention, never awaiting a person's approval
    assert na.refusal("f", review) == "needs_intervention" and not na.awaiting_approval("f", review)
    assert na.intervention("f", review) == {"kind": "needs_intervention", "reason": None}
    assert [na.refusal("f", {"node_key": "gn_x", "status": s, "result_doc_id": None})
            for s in ("pending", "open", "done", "rejected", "closed")] == [
        "node_not_started", "no_result", "already_approved", "node_not_in_review", "node_not_in_review"]
    assert na.refusal("f", {**review, "result_doc_id": None}) == "no_result"
    assert na.refusal("f", {"node_key": "gn_g", "status": "open"}, "gate") == "node_is_gate"


# ── 0119 T-3: the instruction's approval is the completion's own; the required outputs ──


def test_an_instruction_approval_is_the_systems_and_makes_no_node_event(env, decided, monkeypatch, no_v01_path):
    """T-3: ``card_completion.instruction.approve`` writes the instruction's approved display by the system — no
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
    """T-3 (required artifacts): with its instruction used, a complete result (here the engine suites' stand-in reads the
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
    """T-3: a line document beside the required outputs (a person wrote and submitted the instruction line of a
    card that omits its instruction) is history, not an output: the result's automatic completion moves the node on and
    the line document is left as it is."""
    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    _submit_written(instruction)                                         # a person wrote the instruction line
    _work_run_ended()                                                    # no review: the result is complete
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"
    assert _review_status(instruction) == "pending_review"               # kept as it is, never approved here
    assert _node_run(env, key)["intervention"] is None














# ── 0119 T-3: the route is gone; a person's result completes its card (rework: rej_01M6DQW6YF0SCQ5D P1) ──

# (T) pre-feature contract: a person card's dispatch token registers its result directly; an assigned person RUN card writes its instruction first (L-2) — test_fg_ai_roles_l2_assignment.py::test_g2_*


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


# (T) pre-feature contract: same person-card scene (no instruction stage before the result)


# ── a rejection is no node event ─────────────────────────────────────────────

# (T) pre-feature contract: same person-card scene; an assigned person result is judged and approved by the system (G-2)






# ── an AI run that did not end on its own approves nothing ──────────────────

_STOPPED_AI_RUNS = [("cancelled", "cancelled"), ("timeout", "timeout"), ("user_paused", "user_paused"),
                    ("all_providers_failed", "providers_exhausted"), ("exited", "provider_failed"),
                    ("worker_error", None)]






# ── [다음 단계] stop is not awaiting approval ────────────────────────────────────




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
