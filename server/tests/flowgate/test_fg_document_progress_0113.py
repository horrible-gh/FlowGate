"""the next stage by the card's documents, the review a satisfied card still takes, the
wait reinterpreted by the next command, and [전체 실행]'s graph progress (0030-M §3 / §3.1 / §3.2, 0002-D revised §3.5.1 /
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
    if row in ("2_omit_report", "5_ins_report"):                         # Satisfied: omit writing → review (not used) → next
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


# ── the earlier implementation rework: a report saved while the instruction waits is no report of after it (write order) ──────────

EDITED_REPORT = "## 결과\n\n지시서 대기 중에 고친 레포트\n"


def _waits_for_its_report(env, key, report=None):
    """The card took no report after its instruction: open on its result stage, the manual write wait, no result bound
    — through run reads and a [다음 단계] — and the report (when given) still unregistered."""
    assert _review_status(line_doc(env, key, "instruction")) == "approved"
    for _ in range(2):                                                  # run reads (RC-8) take nothing
        assert wire(current(env), key)["wait_reason"]["code"] == "manual_write_wait"
    command(env, "step")
    assert (status(env, key), node_run(env, key)["stage"], node_run(env, key)["result_doc_id"]) == \
        ("open", "result", None)
    assert wire(current(env), key)["wait_reason"]["code"] == "manual_write_wait"
    if report is not None:
        assert doc_row(env, report)["doc_review_status"] is None          # kept as saved, never registered


def test_a_report_edited_while_the_instruction_waits_never_completes_the_card_after_the_instruction(env, decided,
                                                                                                     starts):
    """Row 6 (person), the order of the review's reproduction (test3.test.0040): the run opens on the instruction wait,
    the existing report is edited and saved while the instruction is still blank, then the instruction is saved. The
    report was saved before the instruction: the card stays on its result stage's manual write wait, and only a report
    saved after the instruction completes it."""
    key, docs = _row_card(env, "6_none_report", ai=False)
    command(env, "full")
    assert (status(env, key), node_run(env, key)["stage"]) == ("open", "instruction")
    _write_written(docs["report"], EDITED_REPORT)                       # saved while the instruction is blank
    current(env)
    assert (status(env, key), node_run(env, key)["stage"]) == ("open", "instruction")
    save_instruction(env, key)
    _waits_for_its_report(env, key, docs["report"])
    [row] = baseline_rows(env, key)
    assert row["doc_id"] == docs["report"]                              # the baseline moved to the instruction's moment

    _write_written(docs["report"], NEW_REPORT)                          # saved after the instruction
    current(env)
    assert status(env, key) == "done" and _review_status(docs["report"]) == "approved"
    assert run_row(env)["idle_reason"] == "completed"
    assert write_jobs(env) == [] and starts == []


def test_a_report_first_written_while_the_instruction_waits_never_completes_the_card_after_the_instruction(
        env, decided, starts):
    """Row 3 (person) whose report gets its first body during the instruction wait (a blank report at the opening):
    the report was written before the instruction and is no report of after it — the card waits for a report saved
    after it."""
    key, _docs = _row_card(env, "3_none_none", ai=False)
    command(env, "full")
    assert (status(env, key), node_run(env, key)["stage"]) == ("open", "instruction")
    report = prewrite(env, key, "report", EDITED_REPORT)                # written while the instruction is blank
    save_instruction(env, key)
    _waits_for_its_report(env, key, report)
    assert [r["doc_id"] for r in baseline_rows(env, key)] == [report]

    _write_written(report, NEW_REPORT)
    current(env)
    assert status(env, key) == "done" and _review_status(report) == "approved"
    assert write_jobs(env) == [] and starts == []


def test_a_report_edited_while_an_ai_writes_the_instruction_is_written_anew_by_its_execution_ai(env, decided, starts,
                                                                                               monkeypatch):
    """Row 6 (AI): a person edits the report while X still writes the instruction; once the instruction is written X
    is asked for a new report — the edited one, saved before the instruction, is not taken."""
    key, docs = _row_card(env, "6_none_report", ai=True)
    start(env)
    _write_written(docs["report"], EDITED_REPORT)
    instruction = _ai_writes_instruction(env, monkeypatch, provider=X)
    assert _review_status(instruction) == "approved"
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"], job["ref_doc_ids"]) == ("result", X, [instruction])
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, docs["report"])["doc_review_status"] is None


def test_a_card_written_from_the_start_still_asks_no_writer(env, decided, starts):
    """Row 5 kept (the review's condition): instruction and report written before the run — no write is asked, no
    baseline is kept, the card completes at once."""
    key, docs = _row_card(env, "5_ins_report", ai=False)
    command(env, "full")
    assert status(env, key) == "done" and _review_status(docs["report"]) == "approved"
    assert baseline_rows(env, key) == []
    assert write_jobs(env) == [] and starts == []










# ── §3.7: the review of a satisfied card ──────────────────────────────────────









# ── C: [전체 실행] graph progress (§3.12) ──────────────────────────────────────

def _written(env, key):
    prewrite(env, key, "instruction", INSTRUCTION_BODY)
    prewrite(env, key, "report", REPORT_BODY)












# ── B: a wait reinterpreted by the next command (§3.5.1-8) ────────────────────









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








def _saved(doc_id, body):
    """A body save as the service records it: the body, and its ``doc_body_saved`` event (the order of the saves)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.db.events import insert_event

    _write_written(doc_id, body)
    with get_store().transaction():
        insert_event(doc_id, "doc_body_saved", note='{"source": "test_save"}')




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






# ── migration 167 ─────────────────────────────────────────────────────────────
