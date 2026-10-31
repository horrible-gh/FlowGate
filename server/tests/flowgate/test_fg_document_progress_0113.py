"""flowgate.v02.0113 0038-T T#3 — the next stage by the card's documents, the review a satisfied card still takes, the
wait reinterpreted by the next command, and [전체 실행]'s graph progress (0030-M §3 / §3.1 / §3.2, 0002-D 개정 §3.5.1 /
§3.6-0 / §3.7 / §3.12).

The harness of ``test_fg_ai_roles_l2_assignment`` (real routes, Graph engine, card settings / documents / approval
ports, a separate SQLite DB; every AI write start is the ``env["jobs"]`` recorder and every review loop the ``starts``
recorder: nothing calls an AI). The test policy opens a node once all its predecessors are done and completes the run
once every node is (a join waits for all its branches).

- A (§3.6-0): the six rows, a person's (every role 'not used') and an AI's (instruction and execution AI X): a written
  report asks no writer and goes to its review; "지시서 없음 · 레포트 있음" writes the instruction first and never takes
  the report of before it (the attempt's baseline, kept unchanged) — only a report saved after it completes the card;
- §3.7: a satisfied card with a review AI is reviewed (no write AI, one loop of the §3.7 rounds), a rejection goes no
  further; a review 'not used' approves without a review whatever its count; a review AI with a stored 0 is one round;
- B (§3.5.1-8): a person's manual write wait whose role changed is resolved again by the next command (a new attempt,
  the old tokens revoked, the AI asked once — the value change alone asks nothing); unchanged, it waits on; the
  instruction an AI wrote before the wait is kept (no instruction AI again), and so is the report baseline;
- C (§3.12): [전체 실행] goes as far as the documents allow, branch by branch — a linear Flow stops at its first
  unwritten card, parallel branches wait at once while a written one finishes, a join waits, a written manual Flow
  completes with no AI call.
"""
from __future__ import annotations

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    A, B, FA, X, Y, _h, _review_status, _sql, _write_written, ai_writes_result, assignments, command, db_path, decided,
    edge, env, line_doc, node, node_run, one, put_draft, record_starts, roles, save_instruction, start, starts,
    stored, tokens, wired, write_line,
)
from test_fg_ai_roles_l2_review import _ai_writes_instruction, _review_end
from test_fg_card_settings_review import WORK_RUN, _run_ended

INSTRUCTION_BODY = "## 작업\n\n미리 써 둔 지시서\n"
REPORT_BODY = "## 결과\n\n미리 써 둔 레포트\n"
NEW_REPORT = "## 결과\n\n지시서 이후 새로 쓴 레포트\n"


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fork_join(decided):
    """The production branch and join rules (``flowgate.graph_policies``: every out-edge, unbounded parallelism, the
    AND join) for the validator and the runs started while it is used, over ``decided``'s other slots."""
    from flowgate.graph_policies import fork, join
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    slots = fork.SLOTS + join.SLOTS
    validators = dict(policies._registered)
    old = {}
    for key, value, version, impl in slots:
        run_policies.register(key, value, version, impl)
        old[key] = policies.set_system_default(key, value, version)
    saved = policies._registered
    policies._registered = {**validators, **{key: impl for key, _v, _ver, impl in slots}}
    yield
    policies._registered = saved
    for key, (value, version) in old.items():
        policies.set_system_default(key, value, version)


def graph(env, cards, links, omit=(), settings=()):
    """A confirmed Flow FA: the root R, the cards ``{ref: doc_type}`` and the edges ``[(source ref, target ref)]``
    (``"r"`` is the root). ``omit``: the cards whose instruction is not used. -> {ref: node key}."""
    nodes = [node("tmp_r", "R")] + [node("tmp_" + ref, typ) for ref, typ in cards.items()]
    edges = [edge(f"tmp_e{i}", "tmp_" + s, "tmp_" + t) for i, (s, t) in enumerate(links)]
    keys = put_draft(env, FA, 0, nodes, edges)["key_map"]
    out = {ref: keys["tmp_" + ref] for ref in cards}
    for ref in omit:
        stored(out[ref], "instruction", "omit")
    for ref, field, value in settings:
        stored(out[ref], field, value)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    return out


def prewrite(env, key, slot, body):
    """The card's ``slot`` line holds a document with ``body`` before the run (a person wrote it in the Designer)."""
    doc_id = line_doc(env, key, slot)
    if doc_id is None:
        return write_line(env, key, slot, body)["doc_id"]
    _write_written(doc_id, body)
    return doc_id


def all_roles(keys, instruction="not_used", execution="not_used", review="not_used"):
    for key in keys.values():
        roles(key, instruction, execution, review)


def status(env, key):
    return node_run(env, key)["status"]


def run_row(env):
    return _sql(env["db"], "SELECT run_id, status, idle_reason FROM graph_runs")[0]


def current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def wire(run, key):
    return next(n for n in run["node_runs"] if n["node_key"] == key)


def write_jobs(env, stage=None):
    return [j for j in env["jobs"] if stage is None or j["stage"] == stage]


def loops(starts):
    return [c for c in starts if c.get("document_review_loop")]


def to_role(key, role, value):
    """The card's ``role`` changed in its AI tab (a slot write; nothing else)."""
    from flowgate.ai_roles import role_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    v = role_store.RoleValue("not_used") if value == "not_used" else role_store.RoleValue("assigned", value, "AI " + value)
    with_flow_guard(FA, lambda: role_store.write_slot(FA, key, role_store.RoleSlot(role, v, "card_explicit", "u_kim",
                                                                                     role_store._now())))


def baseline_rows(env, key):
    return _sql(env["db"], "SELECT * FROM graph_attempt_report_baselines WHERE node_key = ? ORDER BY recorded_at",
                (key,))


def doc_row(env, doc_id):
    return _sql(env["db"], "SELECT doc_id, status, doc_review_status, revision_no FROM documents WHERE doc_id = ?",
                (doc_id,))[0]


# ── A: the six rows of §3.6-0, a person's and an AI's ─────────────────────────

ROWS = {                    # row: (instruction used, instruction written, report written)
    "1_omit_none": (False, False, False),
    "2_omit_report": (False, False, True),
    "3_none_none": (True, False, False),
    "4_ins_none": (True, True, False),
    "5_ins_report": (True, True, True),
    "6_none_report": (True, False, True),
}


def _row_card(env, row, ai):
    used, ins, rep = ROWS[row]
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=() if used else ("c",))
    key = keys["c"]
    roles(key, X if ai else "not_used", X if ai else "not_used", "not_used")
    docs = {}
    if ins:
        docs["instruction"] = prewrite(env, key, "instruction", INSTRUCTION_BODY)
    if rep:
        docs["report"] = prewrite(env, key, "report", REPORT_BODY)
    return key, docs


@pytest.mark.parametrize("row", sorted(ROWS))
def test_a_person_s_card_goes_by_its_documents(env, decided, starts, row):
    """Every role 'not used': a written stage asks nothing and goes on, an unwritten one is the manual write wait."""
    key, docs = _row_card(env, row, ai=False)
    start(env)
    assert write_jobs(env) == [] and starts == []                       # no AI, whatever the row
    if row in ("2_omit_report", "5_ins_report"):                         # 충족: 작성 생략 → 검수(사용하지 않음) → 다음
        assert status(env, key) == "done"
        assert _review_status(docs["report"]) == "approved"
        assert one(env, key, "result_review", 1)["verdict"] == "auto_approved"
        assert run_row(env)["idle_reason"] == "completed"
        return
    wait = wire(current(env), key)
    assert (status(env, key), wait["wait_reason"]["code"]) == ("open", "manual_write_wait")
    stage = node_run(env, key)["stage"]
    assert stage == {"1_omit_none": "result", "3_none_none": "instruction", "4_ins_none": "result",
                     "6_none_report": "instruction"}[row]
    if row == "4_ins_none":
        assert _review_status(docs["instruction"]) == "approved"        # the written instruction went on
    if row == "6_none_report":
        assert baseline_rows(env, key)[0]["doc_id"] == docs["report"]     # the report of before the instruction


def test_a_person_s_report_written_before_the_instruction_never_completes_the_card(env, decided, starts):
    """Row 6 (person): the instruction first, then a new report; the report of before it is kept unchanged and takes no
    part — until a report is saved after the baseline."""
    key, docs = _row_card(env, "6_none_report", ai=False)
    start(env)
    before = doc_row(env, docs["report"])
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved"
    assert (status(env, key), node_run(env, key)["stage"]) == ("open", "result")
    for _ in range(2):                                                  # run reads (RC-8) take nothing
        assert wire(current(env), key)["wait_reason"]["code"] == "manual_write_wait"
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, docs["report"]) == before                       # kept as it was, never registered
    assert one(env, key, "result")["actor"] == "person"

    _write_written(docs["report"], NEW_REPORT)                          # saved after the instruction
    current(env)                                                        # the run read takes the saved report
    assert status(env, key) == "done" and _review_status(docs["report"]) == "approved"
    assert write_jobs(env) == [] and starts == []


@pytest.mark.parametrize("row", sorted(ROWS))
def test_an_ai_s_card_goes_by_its_documents(env, decided, starts, monkeypatch, row):
    """Instruction and execution AI X: an unwritten stage is X's work (one request), a written one asks nothing."""
    key, docs = _row_card(env, row, ai=True)
    start(env)
    jobs = [(j["stage"], j["provider_id"]) for j in write_jobs(env)]
    expected = {"1_omit_none": [("result", X)], "2_omit_report": [], "3_none_none": [("instruction", X)],
                "4_ins_none": [("result", X)], "5_ins_report": [], "6_none_report": [("instruction", X)]}[row]
    assert jobs == expected
    assert loops(starts) == []
    if not expected:                                                     # 충족: no writer, reviewed (not used) → done
        assert status(env, key) == "done" and _review_status(docs["report"]) == "approved"
        ra = one(env, key, "result")
        assert (ra["actor"], ra["provider_id"], ra["stage_state"], ra["intent_kind"]) == ("ai", X, "done", None)
        assert [l for l in _sql(env["db"], "SELECT kind FROM graph_attempt_runs")] == []   # no execution request
        return
    assert status(env, key) == "open"
    if row == "4_ins_none":
        assert write_jobs(env)[0]["ref_doc_ids"] == [docs["instruction"]]


def test_an_ai_s_report_written_before_the_instruction_is_written_anew_by_its_execution_ai(env, decided, starts,
                                                                                          monkeypatch):
    """Row 6 (AI): X writes the instruction, then X writes a new report — the report of before is not taken."""
    key, docs = _row_card(env, "6_none_report", ai=True)
    start(env)
    before = doc_row(env, docs["report"])
    instruction = _ai_writes_instruction(env, monkeypatch, provider=X)
    assert _review_status(instruction) == "approved"
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"], job["ref_doc_ids"]) == ("result", X, [instruction])
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, docs["report"]) == before
    result = ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    assert status(env, key) == "done" and _review_status(result) == "approved" and result != docs["report"]


def test_an_approved_report_satisfies_its_card_as_a_draft_does(env, decided, starts):
    """§3.6-0-3: a report registered or approved already is written — not refused for being no draft."""
    from modules.flow_gate.db import documents as db_documents

    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", X, "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)
    db_documents.update(report, {"status": "open", "doc_review_status": "approved"})
    start(env)
    assert write_jobs(env) == [] and starts == []
    current(env)
    assert status(env, key) == "done" and node_run(env, key)["result_doc_id"] == report


def test_a_blank_report_is_no_report(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", X, "not_used")
    prewrite(env, key, "report", "   \n\t\n")
    start(env)
    assert [(j["stage"], j["provider_id"]) for j in write_jobs(env)] == [("result", X)]


# ── §3.7: the review of a satisfied card ──────────────────────────────────────

def test_a_satisfied_card_with_a_review_ai_is_reviewed_once_and_a_rejection_goes_no_further(env, decided, starts,
                                                                                            monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")], settings=(("c1", "review_count", 2),))
    roles(keys["c1"], X, X, B)
    roles(keys["c2"], "not_used", "not_used", "not_used")
    prewrite(env, keys["c1"], "instruction", INSTRUCTION_BODY)
    report = prewrite(env, keys["c1"], "report", REPORT_BODY)
    start(env)
    assert write_jobs(env) == []                                         # no write AI: the documents are written
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["author"], rr["review_rounds"]) == ("ai", B, "person", 1)  # §3.7
    [loop] = loops(starts)
    assert (loop["document_review_loop"]["reviewer_provider_id"], loop["doc_ref"]) == (B, report)
    assert loop["document_review_loop"]["rework_provider_id"] is None
    assert status(env, keys["c1"]) == "review" and status(env, keys["c2"]) == "pending"

    db_documents.update(report, {"doc_review_status": "rejected"})
    _review_end(env, rr, "awaiting_author_revision")
    assert one(env, keys["c1"], "result_review", 1)["verdict"] == "issues"
    current(env)
    assert status(env, keys["c1"]) == "review" and status(env, keys["c2"]) == "pending"     # no further
    assert len(loops(starts)) == 1 and write_jobs(env) == []


def test_a_satisfied_card_with_a_passing_review_ai_goes_on(env, decided, starts, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")], omit=("c1",))
    roles(keys["c1"], "not_used", X, B)
    roles(keys["c2"], "not_used", "not_used", "not_used")
    report = prewrite(env, keys["c1"], "report", REPORT_BODY)
    start(env)
    rr = one(env, keys["c1"], "result_review", 1)
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, rr, "review_passed")
    assert _review_status(report) == "approved" and status(env, keys["c1"]) == "done"
    assert status(env, keys["c2"]) == "open" and write_jobs(env) == []


def test_review_not_used_approves_without_a_review_whatever_its_count(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], settings=(("c", "review_count", 2),))
    key = keys["c"]
    roles(key, "not_used", "not_used", "not_used")
    prewrite(env, key, "instruction", INSTRUCTION_BODY)
    report = prewrite(env, key, "report", REPORT_BODY)
    start(env)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["verdict"], rr["review_rounds"]) == ("no_review", "auto_approved", None)
    assert _review_status(report) == "approved" and status(env, key) == "done"
    assert loops(starts) == [] and write_jobs(env) == []
    assert _sql(env["db"], "SELECT value_json FROM graph_node_settings WHERE node_key = ? AND field = 'review_count'",
                (key,))[0]["value_json"] == "2"                         # the stored count stays


def test_a_review_ai_with_a_stored_zero_is_one_round(env, decided, starts, monkeypatch):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",), settings=(("c", "review_count", 0),))
    key = keys["c"]
    roles(key, "not_used", X, B)
    start(env)
    [job] = write_jobs(env)
    ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["author"], rr["review_rounds"], rr["rework_provider_id"]) == \
        ("ai", B, "ai", 1, X)
    assert len(loops(starts)) == 1


# ── C: [전체 실행] graph progress (§3.12) ──────────────────────────────────────

def _written(env, key):
    prewrite(env, key, "instruction", INSTRUCTION_BODY)
    prewrite(env, key, "report", REPORT_BODY)


def test_full_on_a_linear_flow_stops_at_its_first_unwritten_card_and_never_opens_the_next(env, decided, starts):
    keys = graph(env, {"c1": "T", "c2": "T", "c3": "T", "c4": "T"},
                 [("r", "c1"), ("c1", "c2"), ("c2", "c3"), ("c3", "c4")])
    all_roles(keys)
    for ref in ("c1", "c2", "c4"):
        _written(env, keys[ref])
    start(env)                                                           # [전체 실행]
    assert [status(env, keys[r]) for r in ("c1", "c2", "c3", "c4")] == ["done", "done", "open", "pending"]
    run = current(env)
    assert wire(run, keys["c3"])["wait_reason"]["code"] == "manual_write_wait"
    assert (run["status"], run["idle_reason"]) == ("idle", "waiting_approval")
    assert write_jobs(env) == [] and starts == []


def test_full_on_parallel_branches_waits_at_every_unwritten_card_at_once_and_the_join_waits(env, fork_join, starts):
    cards = {r: "T" for r in ("c1", "c21", "c22", "c23", "c31", "c32", "c33", "c4")}
    links = [("r", "c1"), ("c1", "c21"), ("c1", "c22"), ("c1", "c23"), ("c21", "c31"), ("c22", "c32"),
             ("c23", "c33"), ("c31", "c4"), ("c32", "c4"), ("c33", "c4")]
    keys = graph(env, cards, links)
    all_roles(keys)
    for ref in ("c1", "c21", "c22", "c23", "c32", "c4"):
        _written(env, keys[ref])
    start(env)
    got = {ref: status(env, keys[ref]) for ref in cards}
    assert got == {"c1": "done", "c21": "done", "c22": "done", "c23": "done", "c31": "open", "c32": "done",
                   "c33": "open", "c4": "pending"}
    run = current(env)
    assert {wire(run, keys[r])["wait_reason"]["code"] for r in ("c31", "c33")} == {"manual_write_wait"}
    assert run["idle_reason"] == "waiting_approval"                     # not complete: two branches wait
    assert write_jobs(env) == [] and starts == []

    save_instruction(env, keys["c31"])                                   # one branch written: the join still waits
    write_line(env, keys["c31"], "report", NEW_REPORT)
    assert status(env, keys["c31"]) == "done" and status(env, keys["c4"]) == "pending"


def test_full_on_a_written_manual_flow_completes_with_no_ai_call(env, decided, starts):
    keys = graph(env, {"c1": "T", "c2": "T", "c3": "T"}, [("r", "c1"), ("c1", "c2"), ("c2", "c3")])
    all_roles(keys)
    for key in keys.values():
        _written(env, key)
    start(env)
    assert [status(env, keys[r]) for r in ("c1", "c2", "c3")] == ["done", "done", "done"]
    assert (run_row(env)["status"], run_row(env)["idle_reason"]) == ("idle", "completed")
    assert write_jobs(env) == [] and starts == [] and _sql(env["db"], "SELECT * FROM graph_attempt_runs") == []


def test_full_on_a_written_flow_with_a_review_ai_reviews_each_card_then_completes(env, decided, starts, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    keys = graph(env, {"c1": "T", "c2": "T", "c3": "T"}, [("r", "c1"), ("c1", "c2"), ("c2", "c3")])
    all_roles(keys, review=B)
    for key in keys.values():
        _written(env, key)
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    start(env)
    for i, ref in enumerate(("c1", "c2", "c3"), 1):
        rr = one(env, keys[ref], "result_review", 1)
        assert (rr["actor"], rr["provider_id"]) == ("ai", B) and len(loops(starts)) == i
        assert status(env, keys[ref]) == "review"
        _review_end(env, rr, "review_passed", f"air_review_{ref}")
        assert status(env, keys[ref]) == "done"
    assert run_row(env)["idle_reason"] == "completed" and write_jobs(env) == []


def test_step_keeps_its_targets_on_a_written_flow(env, decided, starts):
    """step's contract is kept (no wider ``in_command``): its written target is done with no AI, the successor waits
    pending for the next command, which takes it the same way."""
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")])
    all_roles(keys)
    for key in keys.values():
        _written(env, key)
    command(env, "step")                                                 # the run starts with [다음 단계]
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "pending")
    assert run_row(env)["idle_reason"] != "completed"
    command(env, "step")
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "done")
    assert run_row(env)["idle_reason"] == "completed" and write_jobs(env) == [] and starts == []


# ── B: a wait reinterpreted by the next command (§3.5.1-8) ────────────────────

def test_a_manual_result_wait_whose_execution_ai_changed_is_the_ai_s_at_the_next_command(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    first = one(env, key, "result")
    assert (first["actor"], status(env, key)) == ("person", "open")
    old_tokens = [t["token_id"] for t in tokens(env, key)]

    command(env, "step")                                                 # unchanged: the wait stays
    assert one(env, key, "result") == first and write_jobs(env) == []
    assert [a["attempt_id"] for a in assignments(env, key)] == [first["attempt_id"]]

    to_role(key, "execution", X)
    assert write_jobs(env) == [] and starts == []                        # the change alone calls nothing
    run = command(env, "step")                                           # the next command reinterprets it
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("result", X)
    active = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                  (key,))[0]["attempt_id"]
    assert active != first["attempt_id"]
    ra = [a for a in assignments(env, key) if a["attempt_id"] == active and a["stage"] == "result"][0]
    assert (ra["actor"], ra["provider_id"]) == ("ai", X)
    old = _sql(env["db"], "SELECT status, invalidated_reason FROM graph_node_attempts WHERE attempt_id = ?",
               (first["attempt_id"],))[0]
    assert (old["status"], old["invalidated_reason"]) == ("invalidated", "stage_reinterpret")
    revoked = _sql(env["db"], "SELECT token_id, revoked_at FROM tokens WHERE token_id IN (%s)"
                   % ",".join("?" * len(old_tokens)), tuple(old_tokens))
    assert old_tokens and all(r["revoked_at"] for r in revoked)          # the person's write permission withdrawn
    assert run["status"] == "running"                                    # the AI's work: the run runs
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": "step"}, headers=_h())
    assert res.status_code == 409 and res.json()["error"]["code"] == "run_already_active"
    assert len(write_jobs(env)) == 1


def test_a_manual_instruction_wait_whose_instruction_ai_changed_is_the_ai_s_at_the_next_full(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    assert one(env, key, "instruction")["actor"] == "person"
    to_role(key, "execution", Y)                                         # another role than the waiting stage's
    command(env, "full")
    assert write_jobs(env) == [] and one(env, key, "instruction")["actor"] == "person"
    to_role(key, "instruction", X)
    command(env, "full")
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("instruction", X)


def test_a_person_stage_holding_its_document_keeps_its_assignment(env, decided, starts):
    """A person's stage whose document is registered keeps its assignment until that document is judged: the report
    bound and rejected by its review (the person's revision awaited), the execution role changed — the command
    reinterprets nothing and asks no AI."""
    from modules.flow_gate.db import documents as db_documents

    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", "not_used", B)
    start(env)
    first = one(env, key, "result")
    report = write_line(env, key, "report", NEW_REPORT)["doc_id"]       # registered: the review takes it
    rr = one(env, key, "result_review", 1)
    db_documents.update(report, {"doc_review_status": "rejected"})
    _review_end(env, rr, "awaiting_author_revision")
    assert current(env)["status"] == "idle"
    to_role(key, "execution", X)
    command(env, "step")
    ra = one(env, key, "result")
    assert (ra["attempt_id"], ra["actor"], ra["stage_state"]) == (first["attempt_id"], "person", "done")
    assert write_jobs(env) == []
    assert status(env, key) == "review" and node_run(env, key)["result_doc_id"] == report
    assert [a["attempt_id"] for a in assignments(env, key)] == [first["attempt_id"]] * len(assignments(env, key))


def test_a_result_wait_reinterpreted_keeps_the_instruction_an_ai_wrote_and_the_report_baseline(env, decided, starts,
                                                                                              monkeypatch):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, X, "not_used", "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)                   # row 6: the report of before
    start(env)
    instruction = _ai_writes_instruction(env, monkeypatch, provider=X)
    assert _review_status(instruction) == "approved"
    first = one(env, key, "result")
    assert first["actor"] == "person" and write_jobs(env) == []
    assert current(env)["status"] == "idle"                              # the manual write wait

    to_role(key, "execution", Y)
    command(env, "step")
    jobs = [(j["stage"], j["provider_id"]) for j in write_jobs(env)]
    assert jobs == [("result", Y)]                                       # no instruction AI again, the report not taken
    active = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                  (key,))[0]["attempt_id"]
    ia = [a for a in assignments(env, key) if a["attempt_id"] == active and a["stage"] == "instruction"][0]
    assert ia["reused_doc_id"] == instruction
    rows = baseline_rows(env, key)
    assert [(r["doc_id"], r["carried_from"]) for r in rows] == [(report, None), (report, first["attempt_id"])]
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None


# ── rework (rej_01M692YYF9MR6F2S #1): a report approved before is reviewed by this attempt ──────────────────

def _approved_report_card(env, execution, review, count=None):
    """c1 (instruction not used) → c2: c1's report is approved before the run (an earlier judgement)."""
    from modules.flow_gate.db import documents as db_documents

    settings = (("c1", "review_count", count),) if count is not None else ()
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")], omit=("c1",), settings=settings)
    roles(keys["c1"], "not_used", execution, review)
    roles(keys["c2"], "not_used", "not_used", "not_used")
    report = prewrite(env, keys["c1"], "report", REPORT_BODY)
    db_documents.update(report, {"status": "open", "doc_review_status": "approved"})
    return keys, report


def _events(env, cause):
    import json

    rows = _sql(env["db"], "SELECT reason, payload_json FROM graph_run_events WHERE cause = ? ORDER BY event_seq",
                (cause,))
    return [(r["reason"], json.loads(r["payload_json"])) for r in rows]


def taken_judged_events(env, key):
    return [p for _r, p in _events(env, "result_taken_judged") if p["node_key"] == key]


@pytest.mark.parametrize("execution", ["not_used", X])
def test_an_approved_report_taken_by_a_new_attempt_is_reviewed_by_its_review_ai_and_goes_on_only_once_passed(
        env, decided, starts, monkeypatch, execution):
    """A-3 / A-4: the old approval is no review of this attempt — the review AI B reviews it (one loop, no write AI),
    run reads never complete the node on the old approval, and only B's pass opens the next card. Both the person's
    result stage (execution 'not used') and the AI's (execution X) take it."""
    from flowgate.card_settings import legacy_adapter

    keys, report = _approved_report_card(env, execution, B)
    start(env)
    assert write_jobs(env) == []                                         # written: no write AI
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["basis_doc_id"], rr["verdict"]) == ("ai", B, report, None)
    [loop] = loops(starts)
    assert (loop["document_review_loop"]["reviewer_provider_id"], loop["doc_ref"]) == (B, report)
    assert [p["doc_id"] for p in taken_judged_events(env, keys["c1"])] == [report]
    for _ in range(2):                                                   # run reads: the old approval completes nothing
        current(env)
        assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("review", "pending")
    assert len(loops(starts)) == 1 and one(env, keys["c1"], "result_review", 2) is None   # no second review

    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, rr, "review_passed")
    assert one(env, keys["c1"], "result_review", 1)["verdict"] == "passed"
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "open")
    assert write_jobs(env) == [] and len(loops(starts)) == 1


def test_an_approved_report_taken_whose_review_does_not_pass_goes_no_further(env, decided, starts):
    keys, report = _approved_report_card(env, "not_used", B)
    start(env)
    rr = one(env, keys["c1"], "result_review", 1)
    _review_end(env, rr, "awaiting_author_revision")
    assert one(env, keys["c1"], "result_review", 1)["verdict"] == "issues"
    current(env)
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("review", "pending")
    assert write_jobs(env) == [] and len(loops(starts)) == 1


@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
@pytest.mark.parametrize("execution", ["not_used", X])
def test_an_approved_report_taken_under_a_review_wait_waits_and_never_completes(env, decided, starts, wait, execution):
    """A-4: the review assignment is made and waits (setup needed / AI unavailable): no review, no automatic approval,
    the node stays in review and the next card is not opened, read after read."""
    review = "unset" if wait == "wait_setup" else B
    keys, report = _approved_report_card(env, execution, review)
    if wait == "wait_unavailable":
        _sql(env["db"], "UPDATE ai_providers SET enabled = 0 WHERE provider_id = ?", (B,))
    start(env)
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["basis_doc_id"], rr["verdict"]) == (wait, report, None)
    for _ in range(2):
        run = current(env)
        assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("review", "pending")
    assert run["idle_reason"] != "completed"
    assert write_jobs(env) == [] and starts == []


def test_an_approved_report_taken_with_review_not_used_is_approved_by_this_attempt_then_goes_on(env, decided, starts):
    """A-4: review 'not used' (count 2): this attempt's automatic approval — recorded on its own review assignment —
    is what moves the node, not the report's old approval."""
    keys, report = _approved_report_card(env, "not_used", "not_used", count=2)
    start(env)
    current(env)
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["verdict"], rr["basis_doc_id"]) == ("no_review", "auto_approved", report)
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "open")
    assert write_jobs(env) == [] and starts == []


# ── rework #2: a setup / unavailable write wait keeps its attempt until its role changes ─────────────────────

def _write_wait(env, wait):
    """The role value of a stage that waits for its setup (unset) or for an unavailable AI (A, disabled)."""
    if wait == "wait_unavailable":
        _sql(env["db"], "UPDATE ai_providers SET enabled = 0 WHERE provider_id = ?", (A,))
        return A
    return "unset"


def _active_attempt(env, key):
    return _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                (key,))[0]["attempt_id"]


def _token_state(env, key):
    return _sql(env["db"], "SELECT t.token_id, t.revoked_at, t.consumed_at FROM graph_run_token_map m JOIN tokens t "
                           "ON t.token_id = m.token_id WHERE m.node_key = ? ORDER BY m.mapped_at, t.token_id", (key,))


@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
def test_a_setup_or_unavailable_result_wait_keeps_its_attempt_until_its_role_changes_then_is_reinterpreted_once(
        env, decided, starts, wait):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", _write_wait(env, wait), "not_used")
    start(env)
    first = one(env, key, "result")
    assert (first["actor"], first["stage_state"]) == (wait, "waiting")
    tokens_before = _token_state(env, key)
    assert tokens_before and not any(t["revoked_at"] for t in tokens_before)

    for mode in ("step", "full", "step"):                               # unchanged: the wait stays as it is
        command(env, mode)
        assert _active_attempt(env, key) == first["attempt_id"]
        assert assignments(env, key) == [first] and _token_state(env, key) == tokens_before
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_node_attempts WHERE node_key = ?", (key,))[0]["n"] == 1
    assert write_jobs(env) == [] and starts == []

    to_role(key, "execution", Y)
    assert write_jobs(env) == []                                         # the change alone calls nothing
    command(env, "step")                                                 # the first command after it reinterprets
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("result", Y)
    active = _active_attempt(env, key)
    assert active != first["attempt_id"]
    old = _sql(env["db"], "SELECT status, invalidated_reason FROM graph_node_attempts WHERE attempt_id = ?",
               (first["attempt_id"],))[0]
    assert (old["status"], old["invalidated_reason"]) == ("invalidated", "stage_reinterpret")
    revoked = {t["token_id"]: t["revoked_at"] for t in _token_state(env, key)}
    assert all(revoked[t["token_id"]] for t in tokens_before)            # the old assignment's permission withdrawn
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": "step"}, headers=_h())
    assert res.status_code == 409                                        # Y works: no second reinterpretation
    assert len(write_jobs(env)) == 1


@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
def test_a_setup_or_unavailable_result_wait_reinterpreted_keeps_the_instruction_and_the_report_baseline(
        env, decided, starts, monkeypatch, wait):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, X, _write_wait(env, wait), "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)                   # row 6: the report of before
    start(env)
    instruction = _ai_writes_instruction(env, monkeypatch, provider=X)
    assert _review_status(instruction) == "approved"
    first = one(env, key, "result")
    assert first["actor"] == wait and write_jobs(env) == []
    assert current(env)["status"] == "idle"                              # the wait
    command(env, "step")                                                 # unchanged: kept
    assert _active_attempt(env, key) == first["attempt_id"] and write_jobs(env) == []

    to_role(key, "execution", Y)
    command(env, "step")
    assert [(j["stage"], j["provider_id"]) for j in write_jobs(env)] == [("result", Y)]   # no instruction AI again
    active = _active_attempt(env, key)
    ia = [a for a in assignments(env, key) if a["attempt_id"] == active and a["stage"] == "instruction"][0]
    assert ia["reused_doc_id"] == instruction
    assert [(r["doc_id"], r["carried_from"]) for r in baseline_rows(env, key)] == \
        [(report, None), (report, first["attempt_id"])]
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, report)["doc_review_status"] is None             # the report of before: kept, never taken


@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
def test_a_setup_or_unavailable_instruction_wait_keeps_its_attempt_until_its_role_changes(env, decided, starts, wait):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, _write_wait(env, wait), "not_used", "not_used")
    start(env)
    first = one(env, key, "instruction")
    assert first["actor"] == wait
    tokens_before = _token_state(env, key)
    to_role(key, "execution", Y)                                         # another role than the waiting stage's
    for mode in ("step", "full"):
        command(env, mode)
        assert _active_attempt(env, key) == first["attempt_id"] and _token_state(env, key) == tokens_before
    to_role(key, "instruction", X)
    command(env, "full")
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("instruction", X)
    assert _active_attempt(env, key) != first["attempt_id"]


# ── rework #3: a report baseline that cannot be recorded or read never lets the old report through ───────────

class _BrokenBaselineStore:
    """The store as ``report_baseline`` sees it with one fault on its table: ``insert`` / ``read`` fail, ``absent``
    says the table (migration 167) is missing. ``fault`` None: the store works again."""

    def __init__(self, real, faults):
        self.real, self.faults = real, faults

    @property
    def fault(self):
        return self.faults.get("fault")

    def __getattr__(self, name):
        return getattr(self.real, name)

    def table_exists(self, table):
        if self.fault == "absent" and table == "graph_attempt_report_baselines":
            return False
        return self.real.table_exists(table)

    def insert_isolated(self, sql, params, savepoint):
        if self.fault == "insert" and "graph_attempt_report_baselines" in sql:
            raise RuntimeError("baseline insert failed")
        return self.real.insert_isolated(sql, params, savepoint)

    def _fetch_one(self, sql, params=None):
        if self.fault == "read" and "graph_attempt_report_baselines" in sql:
            raise RuntimeError("baseline read failed")
        return self.real._fetch_one(sql, params)


def _break_baseline(monkeypatch, fault):
    """The fault on, until the returned switch's ``fault`` is set to None (the store repaired)."""
    from flowgate.card_settings import report_baseline
    from modules.flow_gate.db.connection import get_store

    faults = {"fault": fault}
    monkeypatch.setattr(report_baseline, "get_store", lambda: _BrokenBaselineStore(get_store(), faults))
    return faults


def _row6_person_card(env):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, "not_used", "not_used", "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)
    return key, report


@pytest.mark.parametrize("fault", ["insert", "read", "absent"])
def test_a_baseline_that_cannot_be_recorded_fails_the_opening_and_the_old_report_is_never_taken(env, decided, starts,
                                                                                              monkeypatch, fault):
    """A-2: the opening that needs a baseline (instruction not written, a report there) fails as a whole when the
    baseline cannot be recorded (an insert or read failure, migration 167 missing): the report is kept as it was,
    unregistered, the card not done. Once the store works the next command opens it with its baseline, and only a
    report saved after it completes the card."""
    key, report = _row6_person_card(env)
    before = doc_row(env, report)
    faults = _break_baseline(monkeypatch, fault)
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert (res.status_code, res.json()["error"]["code"]) == (500, "internal_error")   # rolled back as a whole
    assert _sql(env["db"], "SELECT * FROM graph_node_attempts WHERE node_key = ?", (key,)) == []   # nothing opened
    assert _sql(env["db"], "SELECT * FROM graph_attempt_report_baselines") == []
    assert doc_row(env, report) == before and assignments(env, key) == []

    faults["fault"] = None                                               # the store works again
    command(env, "full")
    assert baseline_rows(env, key)[0]["doc_id"] == report
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved"
    current(env)
    assert (status(env, key), node_run(env, key)["stage"], node_run(env, key)["result_doc_id"]) == \
        ("open", "result", None)
    assert doc_row(env, report) == before                                # still the report of before: not taken
    _write_written(report, NEW_REPORT)                                   # saved after the baseline
    current(env)
    assert status(env, key) == "done" and _review_status(report) == "approved"
    assert write_jobs(env) == [] and starts == []


@pytest.mark.parametrize("fault", ["read", "absent"])
def test_a_baseline_that_cannot_be_read_takes_no_report_until_it_can(env, decided, starts, monkeypatch, fault):
    """A-2: after the opening, a baseline that cannot be read (a read failure, the table gone) is no "no baseline":
    the result stage takes no report — neither the old one nor one saved meanwhile — and nothing is completed; once
    it can be read again only a report saved after the baseline completes the card."""
    key, report = _row6_person_card(env)
    start(env)
    before = doc_row(env, report)
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved"
    assert (status(env, key), node_run(env, key)["stage"]) == ("open", "result")

    faults = _break_baseline(monkeypatch, fault)
    for _ in range(2):                                                   # run reads (RC-8) and a command take nothing
        current(env)
    command(env, "step")
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, report) == before
    _write_written(report, NEW_REPORT)                                   # even a new save waits while it is unknown
    current(env)
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None

    faults["fault"] = None
    current(env)                                                         # known again: the report saved after it
    assert status(env, key) == "done" and _review_status(report) == "approved"
    assert write_jobs(env) == [] and starts == []


# ── migration 167 ─────────────────────────────────────────────────────────────

def test_migration_167_is_the_same_table_in_three_dialects_and_runs_twice():
    import sqlite3
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (root / dialect / "167_attempt_report_baselines.sql").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS graph_attempt_report_baselines" in sql
        for column in ("attempt_id", "run_id", "group_id", "node_key", "doc_id", "revision_no", "body_sha256",
                       "body_event_id", "carried_from", "recorded_at"):
            assert column in sql, (dialect, column)
        ddl = sql[sql.index("CREATE TABLE"):].split("-- ── DOWN")[0]
        assert "REFERENCES" not in ddl and "FOREIGN KEY" not in ddl          # a history row
    conn = sqlite3.connect(":memory:")
    script = (root / "sqlite" / "167_attempt_report_baselines.sql").read_text(encoding="utf-8")
    conn.executescript(script)
    conn.execute("INSERT INTO graph_attempt_report_baselines (attempt_id, run_id, group_id, node_key, recorded_at) "
                 "VALUES ('a', 'r', 'g', 'n', 't')")
    conn.executescript(script)                                           # again: the row stays
    assert conn.execute("SELECT COUNT(*) FROM graph_attempt_report_baselines").fetchone()[0] == 1
