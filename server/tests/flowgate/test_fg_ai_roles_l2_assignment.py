"""flowgate.v02.0113 T#3 — L-2 stage assignments: the opening, the start of each stage, atomicity and the node token.

On the real SQLite DB and routes of ``test_fg_card_settings`` with the stage assignment feature on (the shared ``env``
fixture applies every migration, so every suite runs its new attempts on the assigned path; ``STAGE_ASSIGNMENTS`` only
marks these suites). The AI start is the suite's
recorder (``env["jobs"]``), the AI invoke admission a recorder; nothing calls a real AI. Test names carry the L-2 §4.8 /
§4.9 row ids (G-n, K-n) they prove; the helpers here are shared by the other ``test_fg_ai_roles_l2_*`` / ``p1`` suites.
"""
from __future__ import annotations

import json

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft,
)
from test_fg_card_settings_review import (  # noqa: F401
    INSTRUCTION_RUN, REVIEW_RUN, WORK_RUN, _executions_ended, _loop_row, _node, _result, _review_status, _run_ended,
    _start_ai, _write_written,
)

STAGE_ASSIGNMENTS = True
X, B, A, Y = "aip_x", "aip_b", "aip_a", "aip_y"


@pytest.fixture(autouse=True)
def wired(env, monkeypatch):
    """The card approval port and the card documents routes, as ``startup`` / ``main`` wire them (their RBAC answer:
    every permission)."""
    from flowgate.card_approval import integration as approval
    from flowgate.card_documents import legacy_adapter as documents
    from flowgate.card_documents import routes as document_routes

    monkeypatch.setattr(documents, "has_permission", lambda u, p, perm: True)
    approval.register()
    env["client"].app.include_router(document_routes.router)
    _sql(env["db"], "UPDATE users SET is_admin = 1 WHERE user_id = 'u_kim'")
    for pid in (X, B, A, Y):
        _sql(env["db"], "INSERT OR IGNORE INTO ai_providers (provider_id, name, exec_type, kind, created_at, updated_at) "
                        "VALUES (?, ?, 'cli', 'claude', 't', 't')", (pid, "AI " + pid))
    yield
    approval.unregister()


@pytest.fixture()
def starts(monkeypatch):
    """The AI invoke admission as a recorder (review loops and AI starts): never a real AI. The recorder never acquires
    the execution, so the engine's scheduler replays that same requested execution (harness, as in 0105
    ``_review_starts``): only the starts that are no such replay are recorded. ``record_starts`` puts it back after a
    helper that patched the admission itself (``_start_ai``)."""
    calls = []
    record_starts(monkeypatch, calls)
    return calls


def record_starts(monkeypatch, calls):
    import traceback

    from modules.flow_gate.services import ai_invoke_service

    def start_run(**kw):
        if not any(frame.name == "replay" for frame in traceback.extract_stack()):
            calls.append(kw)
        return {"run_id": REVIEW_RUN if kw.get("document_review_loop") else WORK_RUN, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)


# ── shared helpers ───────────────────────────────────────────────────────────

def flow(env, doc_type="T", assignee=None, extra_settings=()):
    """A confirmed R -> card Flow (FA); returns the card's node key (its run not started)."""
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", doc_type, assignee=assignee)],
                      [edge("tmp_e", "tmp_r", "tmp_c")])
    key = first["key_map"]["tmp_c"]
    for field, value in extra_settings:
        stored(key, field, value)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    return key


def stored(key, field, value):
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    with_flow_guard(FA, lambda: repository.put(FA, key, field, value, "u_kim"))


def roles(key, instruction="unset", execution="unset", review="unset", init="initialized"):
    """The card's role slots: ``not_used``, a provider id, or ``unset`` (no slot); ``init``: its init state
    (``legacy`` removes the init row: a card made before the role feature)."""
    from flowgate.ai_roles import role_store
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    def body():
        store = get_store()
        store._execute("DELETE FROM graph_node_role_slots WHERE group_id = ? AND node_key = ?", [FA, key])
        store._execute("DELETE FROM graph_node_role_init WHERE group_id = ? AND node_key = ?", [FA, key])
        for role, value in (("instruction", instruction), ("execution", execution), ("review", review)):
            if value == "unset":
                continue
            v = role_store.RoleValue("not_used") if value == "not_used" else role_store.RoleValue("assigned", value,
                                                                                                    "AI " + value)
            role_store.write_slot(FA, key, role_store.RoleSlot(role, v, "card_explicit", "u_kim", role_store._now()))
        if init != "legacy":
            role_store.create_card_init(FA, key, init, "u_kim")
    with_flow_guard(FA, body)


def start(env):
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert res.status_code == 201, res.text
    return res.json()["run"]


def command(env, mode="step"):
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": mode}, headers=_h())
    assert res.status_code in (200, 201), res.text
    return res.json()["run"]


def assignments(env, key=None):
    sql = "SELECT * FROM graph_stage_assignments" + (" WHERE node_key = ?" if key else "") + " ORDER BY decided_at, seq"
    return _sql(env["db"], sql, (key,) if key else ())


def one(env, key, stage, seq=0):
    rows = [a for a in assignments(env, key) if a["stage"] == stage and a["seq"] == seq]
    assert len(rows) <= 1
    return rows[0] if rows else None


def events(env, cause):
    rows = _sql(env["db"], "SELECT reason, payload_json FROM graph_run_events WHERE cause = ? ORDER BY event_seq", (cause,))
    return [(r["reason"], json.loads(r["payload_json"])) for r in rows]


def b1(env):
    return [(p["doc_id"], r, p.get("submission_mark")) for r, p in events(env, "auto_approval_skipped")]


def line_doc(env, key, slot):
    rows = _sql(env["db"], "SELECT doc_id FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
                (FA, key, slot))
    return rows[0]["doc_id"] if rows else None


def node_run(env, key):
    return _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]


def tokens(env, key):
    return _sql(env["db"], "SELECT m.token_id, m.stage, m.map_kind, m.attempt_id, t.provider_id, t.issued_to, "
                           "t.consumed_at FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
                           "WHERE m.node_key = ? ORDER BY m.mapped_at", (key,))


def ledger(env):
    return _sql(env["db"], "SELECT exec_id, attempt_id, kind, status, request_json, assignment_stage, assignment_seq "
                           "FROM graph_attempt_runs ORDER BY created_at")


def save_instruction(env, key, body="## 작업\n\n사람이 쓴 지시서\n"):
    """A person saves the card's instruction line draft (its body), then the save's contact (CONTENT_SAVED)."""
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    doc_id = line_doc(env, key, "instruction")
    _write_written(doc_id, body)
    card_settings_ports.instruction_saved(FA, doc_id)
    return doc_id


def write_line(env, key, slot, content, expect=201):
    res = env["client"].post(f"/api/v1/flows/{FA}/nodes/{key}/slots/{slot}/document", json={"content": content},
                             headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def ai_writes_result(env, monkeypatch, key, job, seq=81, typ="TR", run_id=WORK_RUN, provider=X):
    """The assigned AI's run as the engine runs it: its token registers the result, its executions end, its run ends."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    _heads, token = _start_ai(monkeypatch, job, run_id)
    result = _result(seq, typ)
    v01_contact.after_inbox_registered(FA, result, typ, token)
    token_service.consume(token, "flowgate", result)
    _executions_ended()
    return result


def disable_all_ai(env):
    _sql(env["db"], "UPDATE ai_providers SET enabled = 0")


# ── K-12: the feature off (the store missing) keeps the current dispatch ─────

def test_k12_feature_off_opens_with_the_current_dispatch_and_writes_no_assignment(env, decided, monkeypatch):
    from flowgate.card_settings import stage_assignment

    monkeypatch.setattr(stage_assignment, "enabled", lambda: False)
    key = flow(env, extra_settings=(("instruction", "omit"), ("run_ai", X)))
    start(env)
    assert assignments(env) == []
    [job] = env["jobs"]
    assert job["provider_id"] == X and "assignment_ref" not in job          # the current run AI path
    raw = json.loads(ledger(env)[0]["request_json"])
    assert raw["invocation_origin"]["origin_kind"] == "card_legacy_attempt" and "assignment_ref" not in raw
    assert node_run(env, key)["stage"] == "result"


# ── the opening: the four branches of the instruction stage (L-2 §2.4, §4.2) ─

def test_g1_person_instruction_then_ai_result_once_and_its_review_by_b_reworked_by_x(env, decided, monkeypatch, starts):
    """G-1: person / AI(X) / AI(B). The saved instruction gets NO_REVIEW(no_review_setting), the system approves it and
    X starts once with the instruction attached; the node is not done. X's result: B reviews it, X reworks."""
    key = flow(env)
    roles(key, "not_used", X, B)
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["actor_reason"], ia["stage_state"], ia["plan_instruction_type"], ia["plan_result_type"]) == \
        ("person", "not_used", "waiting", "T", "TR")
    assert env["jobs"] == []                                                       # no AI for a person's stage
    mapped = tokens(env, key)
    assert [(t["stage"], t["map_kind"], t["provider_id"]) for t in mapped] == [(None, "dispatch", None),
                                                                               ("instruction", "dispatch", None)]
    assert [r for r, _p in events(env, "stage_assigned")] == ["person"]
    instruction = save_instruction(env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["actor_reason"], ir["source"], ir["verdict"], ir["author"]) == \
        ("no_review", "no_review_setting", "instruction_review_setting", "auto_approved", "person")
    assert _review_status(instruction) == "approved"
    ra = one(env, key, "result")
    assert (ra["actor"], ra["provider_id"], ra["stage_state"], ra["intent_kind"]) == ("ai", X, "active", "execution")
    [job] = env["jobs"]
    assert (job["provider_id"], job["result_doc_type"], job["ref_doc_ids"], job["stage"]) == \
        (X, "TR", [instruction], "result")
    assert job["assignment_ref"] == {"attempt_id": ra["attempt_id"], "stage": "result", "seq": 0}
    assert node_run(env, key)["status"] == "open"                                  # not done yet
    result = ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["rework_provider_id"], rr["author"], rr["author_provider_id"]) == \
        ("ai", B, X, "ai", X)
    loops = [c for c in starts if c.get("document_review_loop")]
    assert len(loops) == 1
    assert (loops[0]["document_review_loop"]["reviewer_provider_id"],
            loops[0]["document_review_loop"]["rework_provider_id"], loops[0]["doc_ref"]) == (B, X, result)
    assert rr["intent_kind"] == "execution" and rr["intent_exec_id"]


def test_g2_person_person_no_review_completes_without_a_person_s_approval(env, decided, starts):
    """G-2: person / person / not used: the instruction saved -> approved -> the result stage waits for the report (no
    node completion); the report written -> NO_REVIEW -> the system approves it -> the node is done."""
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved"
    ra = one(env, key, "result")
    assert (ra["actor"], ra["stage_state"]) == ("person", "waiting")
    row = node_run(env, key)
    assert (row["status"], row["stage"], row["instruction_doc_id"]) == ("open", "result", instruction)
    assert len([t for t in tokens(env, key) if t["stage"] is None]) == 1          # the node token is still live: no T-3
    assert env["jobs"] == [] and starts == []
    answer = write_line(env, key, "report", "## 결과\n\n사람이 쓴 레포트\n")
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["actor_reason"], rr["verdict"], rr["author"]) == ("no_review", "not_used", "auto_approved",
                                                                              "person")
    assert _review_status(answer["doc_id"]) == "approved"
    assert node_run(env, key)["status"] == "done"
    assert starts == [] and env["jobs"] == []


def test_g3_no_selectable_ai_is_a_person_stage_that_stays_one_when_an_ai_is_added(env, decided):
    """G-3: unset roles and no selectable AI for the run starter -> person(no_ai); an AI added later changes nothing of
    the assignment, and nothing is written to the account or the card."""
    key = flow(env)
    disable_all_ai(env)
    before = _sql(env["db"], "SELECT * FROM graph_node_role_slots WHERE node_key = ?", (key,))
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["actor_reason"], ia["source"]) == ("person", "no_ai", "no_ai")
    _sql(env["db"], "UPDATE ai_providers SET enabled = 1")
    assert one(env, key, "instruction") == ia
    assert _sql(env["db"], "SELECT * FROM graph_node_role_slots WHERE node_key = ?", (key,)) == before
    assert _sql(env["db"], "SELECT * FROM user_ai_role_defaults") == []


@pytest.mark.parametrize("cause, setup", [
    ("deleted", lambda env: _sql(env["db"], "DELETE FROM ai_providers WHERE provider_id = ?", (X,))),
    ("inactive", lambda env: _sql(env["db"], "UPDATE ai_providers SET enabled = 0 WHERE provider_id = ?", (X,))),
    ("personal_not_runnable", lambda env: _sql(env["db"], "UPDATE ai_providers SET owner_scope = 'user', "
                                                          "owner_user_id = 'u_kim' WHERE provider_id = ?", (X,))),
])
def test_g7_an_unavailable_assigned_ai_waits_with_its_cause_and_calls_nothing(env, decided, starts, cause, setup):
    """G-7 / 폴백 금지: an assigned AI that cannot run (the last AI deleted included) is WAIT_UNAVAILABLE(cause) with its
    original id, the node records ``ai_unavailable:<cause>``, no request, no call, no other AI."""
    key = flow(env)
    roles(key, X, X, "not_used")
    setup(env)
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["cause"], ia["provider_id"], ia["stage_state"]) == ("wait_unavailable", cause, X, "waiting")
    assert node_run(env, key)["dispatch_error_code"] == "ai_unavailable:" + cause
    assert env["jobs"] == [] and starts == [] and ledger(env) == []
    [(reason, payload)] = events(env, "stage_assigned")
    assert (reason, payload["cause_code"], payload["provider_id"]) == ("wait_unavailable", cause, X)


def test_registry_unreadable_and_store_error_wait_for_setup_and_never_read_as_no_ai(env, decided, monkeypatch):
    """L-2 §2.3 / E-05 / E-07: a broken stored value waits for setup even with no AI; an unreadable registry is no
    'no AI'."""
    from flowgate.ai_roles import availability
    from flowgate.card_settings import repository

    key = flow(env, extra_settings=(("instruction", "omit"),))
    disable_all_ai(env)
    real = repository.values
    monkeypatch.setattr(repository, "values", lambda f, k: (_ for _ in ()).throw(RuntimeError("store down"))
                        if k == key else real(f, k))
    start(env)
    ia = one(env, key, "instruction")                               # an unreadable setting plans with the defaults
    assert (ia["actor"], ia["source"], ia["plan_instruction_type"], ia["plan_result_type"]) ==         ("wait_setup", "store_error", "T", "TR")
    monkeypatch.setattr(availability, "selectable_ais", lambda u: (_ for _ in ()).throw(RuntimeError("registry")))
    from flowgate.card_settings import stage_assignment
    assert stage_assignment.no_ai("u_kim") == "unknown"


def test_a_pending_init_failure_rolls_back_to_its_savepoint_and_the_stage_is_still_assigned(env, decided, monkeypatch):
    """T §4.1: the one pending fill fails under its own savepoint — pending_init, slots and version stay — and the stage
    is resolved on the slots left."""
    from flowgate.ai_roles import card_init

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, init="pending_init")
    version = _sql(env["db"], "SELECT * FROM graph_node_role_init WHERE node_key = ?", (key,))

    def broken(flow_id, node_key, kind):
        from modules.flow_gate.db.connection import get_store
        get_store()._execute("UPDATE graph_node_role_init SET state = 'initialized' WHERE group_id = ? AND node_key = ?",
                             [flow_id, node_key])
        raise RuntimeError("slot write failed")

    monkeypatch.setattr(card_init, "initialize_pending_card", broken)
    start(env)
    ra = one(env, key, "result")
    assert (ra["actor"], ra["provider_id"]) == ("ai", X)
    assert _sql(env["db"], "SELECT * FROM graph_node_role_init WHERE node_key = ?", (key,)) == version


def test_g4_a_value_changed_before_the_result_stage_applies_and_one_changed_after_it_does_not(env, decided, starts):
    """G-4: the result stage is resolved when it starts; a person's result assignment stays a person's when the role is
    changed to AI(Y) afterwards, and a value change itself calls nothing."""
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    from flowgate.ai_roles import role_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    def to_ai(pid):
        with_flow_guard(FA, lambda: role_store.write_slot(FA, key, role_store.RoleSlot(
            "execution", role_store.RoleValue("assigned", pid), "card_explicit", "u_kim", role_store._now())))

    to_ai(Y)                                                                        # before the result stage
    assert env["jobs"] == [] and starts == []                                       # a change alone calls nothing
    save_instruction(env, key)
    ra = one(env, key, "result")
    assert (ra["actor"], ra["provider_id"]) == ("ai", Y)
    [job] = env["jobs"]
    assert job["provider_id"] == Y


def test_g4_a_person_result_stage_keeps_its_assignment_after_the_role_turns_to_ai(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    save_instruction(env, key)
    from flowgate.ai_roles import role_store
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    with_flow_guard(FA, lambda: role_store.write_slot(FA, key, role_store.RoleSlot(
        "execution", role_store.RoleValue("assigned", Y), "card_explicit", "u_kim", role_store._now())))
    answer = write_line(env, key, "report", "## 결과\n\n본문\n")
    assert one(env, key, "result")["actor"] == "person"
    assert _review_status(answer["doc_id"]) == "approved" and node_run(env, key)["status"] == "done"
    assert env["jobs"] == [] and starts == []


# ── K-13: the node token (T-1), the instruction stage token (T-2), the result supplement (T-3) ──

def _failing_issue(monkeypatch, fail_calls):
    from modules.flow_gate.services import token_service

    real, count = token_service.issue, {"n": 0}

    def issue(**kw):
        count["n"] += 1
        if count["n"] in fail_calls:
            raise RuntimeError("token store down")
        return real(**kw)

    monkeypatch.setattr(token_service, "issue", issue)
    return count


def test_k13_t1_failure_fails_the_first_assignment_and_requests_nothing(env, decided, monkeypatch, starts):
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    _failing_issue(monkeypatch, {1})
    start(env)
    ra = one(env, key, "result")
    assert (ra["stage_state"], ra["fail_cause"]) == ("failed", "token_issue_failed")
    row = node_run(env, key)
    assert (row["dispatch_token_issued"], row["dispatch_error_code"]) == (0, "token_issue_failed")
    assert tokens(env, key) == [] and env["jobs"] == [] and ledger(env) == []
    assert [r for r, _p in events(env, "stage_failed")] == ["token_issue_failed"]
    assert [(r, p["token_kind"]) for r, p in events(env, "stage_token_issue_failed")] == [("node", "node")]


def test_k13_t2_failure_keeps_the_person_instruction_stage_and_the_node_token_registers(env, decided, monkeypatch):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    _failing_issue(monkeypatch, {2})
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["stage_state"]) == ("person", "waiting")
    assert [t["stage"] for t in tokens(env, key)] == [None]
    assert [(r, p["token_kind"], p["stage"]) for r, p in events(env, "stage_token_issue_failed")] == \
        [("instruction_stage", "instruction_stage", "instruction")]
    instruction = save_instruction(env, key)                                        # E-15: a draft save still judges
    assert _review_status(instruction) == "approved"


def test_k13_t3_failure_fails_the_result_assignment_with_no_registration_and_no_wait(env, decided, monkeypatch):
    """T-3: a person's result stage whose node token was spent on the instruction, and the supplement fails."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    [node_token] = [t["token_id"] for t in tokens(env, key) if t["stage"] is None]
    instruction = _doc(FA, 70, "T")
    _write_written(instruction)
    _failing_issue(monkeypatch, {1})
    v01_contact.after_inbox_registered(FA, instruction, "T", node_token)       # E-16: registered with the node token
    token_service.consume(node_token, "flowgate", instruction)
    ra = one(env, key, "result")
    assert (ra["actor"], ra["stage_state"], ra["fail_cause"]) == ("person", "failed", "token_issue_failed")
    assert node_run(env, key)["dispatch_error_code"] == "token_issue_failed"
    assert [p["token_kind"] for _r, p in events(env, "stage_token_issue_failed")] == ["result_supplement"]


def test_t3_issues_one_node_token_when_the_instruction_spent_it(env, decided):
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    [node_token] = [t["token_id"] for t in tokens(env, key) if t["stage"] is None]
    instruction = _doc(FA, 70, "T")
    _write_written(instruction)
    v01_contact.after_inbox_registered(FA, instruction, "T", node_token)
    token_service.consume(node_token, "flowgate", instruction)
    assert node_run(env, key)["instruction_doc_id"] == instruction
    assert _review_status(instruction) == "approved"
    live = [t for t in tokens(env, key) if t["stage"] is None and t["consumed_at"] is None]
    assert len(live) == 1 and live[0]["token_id"] != node_token                   # T-3: one, to the same person
    assert live[0]["issued_to"] == "u_kim"


# ── atomicity of an assignment and its intent (L-2 §2.2.2) ───────────────────

def test_a_refused_review_request_rolls_back_to_its_savepoint_and_keeps_the_failed_assignment(env, decided, monkeypatch,
                                                                                            starts):
    """E-42: the review loop's locked request refused -> the request is rolled back, the assignment stays
    ``failed(review_not_started)`` and the B-1 of this submission is recorded once after the commit."""
    from flowgate.graph_run import execution

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used", review=B)
    start(env)
    monkeypatch.setattr(execution, "request_loop_locked", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fenced")))
    answer = write_line(env, key, "report", "## 결과\n\n본문\n")
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["stage_state"], rr["fail_cause"], rr["intent_kind"]) == \
        ("ai", "failed", "review_not_started", None)
    assert [l for l in ledger(env) if l["kind"] in ("review", "rework")] == []
    assert [(d, r) for d, r, _m in b1(env)] == [(answer["doc_id"], "review_not_started")]
    assert b1(env)[0][2] == {"doc_id": answer["doc_id"], "revision_no": 0, "rejection_count": 0}
    assert _review_status(answer["doc_id"]) != "approved" and starts == []


def test_an_ai_review_assignment_and_its_ledger_request_commit_together_with_the_ref(env, decided, starts):
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used", review=B)
    start(env)
    answer = write_line(env, key, "report", "## 결과\n\n본문\n")
    rr = one(env, key, "result_review", 1)
    reqs = [l for l in ledger(env) if l["kind"] in ("review", "rework")]
    assert len(reqs) == 1, [(l["kind"], l["status"], l["assignment_stage"], l["request_json"][:300]) for l in reqs]
    [req] = reqs
    assert (rr["intent_exec_id"], req["assignment_stage"], req["assignment_seq"]) == (req["exec_id"], "result_review", 1)
    raw = json.loads(req["request_json"])
    assert raw["assignment_ref"] == {"attempt_id": rr["attempt_id"], "stage": "result_review", "seq": 1}
    origin = raw["invocation_origin"]
    assert (origin["origin_kind"], origin["purpose"], origin["role"], origin["requested_ai_id"], origin["round_no"]) == \
        ("card_assigned", "result_review_round", "review", B, 1)
    assert origin["target"]["doc_id"] == answer["doc_id"]
    assert (rr["review_rounds"], rr["rework_provider_id"]) == (1, None)            # a person's result: 1 round, no rework
    [loop] = [c for c in starts if c.get("document_review_loop")]
    assert loop["graph_exec_id"] == req["exec_id"] and loop["invocation_origin"]["purpose"] == "result_review_round"
