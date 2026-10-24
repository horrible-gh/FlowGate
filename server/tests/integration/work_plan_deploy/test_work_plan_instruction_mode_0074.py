"""T0017: saved instruction omission, real API deploy and report-only Graph execution."""
from copy import deepcopy

import pytest

from test_work_plan_omit_0074 import (
    api, env, db_path, card_approval_port, decided,  # noqa: F401
    _read, _save, _preview, _execute, _confirm_and_run_after_the_root,
    _flow, _write_plan, _line_doc, _file, _one_job, _row, _sql, _h, FA,
)


@pytest.fixture(autouse=True)
def card_start_port():
    """The deploy's card start port (WP read and saves), registered as ``startup`` registers it."""
    from flowgate.work_plan_deploy import integration

    integration.register()
    yield
    integration.unregister()


def _put(env, doc, body, revision):
    return env["api"].put(f"/api/v1/documents/{doc}/work-plan",
                          json={"base_revision_no": revision, "body": body}, headers=_h())


def _stop_run(env):
    [run] = _sql(env["db"], "SELECT run_id FROM graph_runs WHERE group_id = ?", (FA,))
    res = env["client"].post(f"/api/v1/runs/{run['run_id']}/stop", json={}, headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _started(view):
    return {row["card_id"]: row["started"] for row in view["step_execution_status"]}


def _mode(env, doc, mode):
    def change(body):
        for step in body["steps"]:
            if step["pair_role"] == "instruction":
                step["instruction_mode"] = mode
    return _save(env, doc, change)


@pytest.mark.parametrize("doc_type,result_type", [("N", "NR"), ("T", "TR")])
def test_omission_saved_reopened_deployed_and_run_only_as_report(api, decided, doc_type, result_type):
    from flowgate.card_settings import repository
    from modules.flow_gate.services import work_plan_service as wp

    _, doc, keys = _flow(api)
    _write_plan(doc, {doc_type: 1})
    before = deepcopy(_read(api, doc)["body"])
    saved = _mode(api, doc, "omit")
    reopened = _read(api, doc)
    assert reopened["body"] == saved["body"]
    assert len(reopened["body"]["steps"]) == 2
    assert wp.totals(reopened["body"])["steps"] == 1
    assert wp.unassigned_step_count(reopened["body"]) == 1
    original_instruction, original_report = before["steps"]
    assert reopened["body"]["steps"] == [{**original_instruction, "instruction_mode": "omit"}, original_report]

    preview = _preview(api, doc, "first", saved["revision_no"])
    deployed = _execute(api, doc, "first", preview["baseline"])
    [key] = deployed["created_node_keys"]
    assert repository.values(FA, key)["instruction"] == "omit"
    instruction = _line_doc(api, key)
    report = _line_doc(api, key, "report")
    assert instruction != report

    _confirm_and_run_after_the_root(api, keys["tmp_r"], key)
    job = _one_job(api)
    assert job["stage"] == "result"
    assert job["result_doc_type"] == result_type
    assert instruction not in (job["ref_doc_ids"] or [])
    assert _row(api, key)["instruction_doc_id"] is None


@pytest.mark.parametrize("doc_type", ["N", "T"])
def test_mode_changes_on_an_existing_card_preserve_both_documents_and_instruction_body(api, doc_type):
    from flowgate.card_settings import repository

    _, doc, _ = _flow(api)
    _write_plan(doc, {doc_type: 1})
    def content(body):
        body["steps"][0]["pre_instruction_text"] = "Keep this instruction"
    first = _save(api, doc, content)
    preview = _preview(api, doc, "first", first["revision_no"])
    [key] = _execute(api, doc, "first", preview["baseline"])["created_node_keys"]
    ids = [_line_doc(api, key), _line_doc(api, key, "report")]
    before = [_file(doc_id).read_bytes() for doc_id in ids]

    for mode in ("omit", "use", "omit"):
        saved = _mode(api, doc, mode)
        preview = _preview(api, doc, "add", saved["revision_no"])
        assert preview["executable"] and not preview["no_change"]
        result = _execute(api, doc, "add", preview["baseline"])
        assert result["created_node_keys"] == result["removed_node_keys"] == []
        assert repository.values(FA, key)["instruction"] == mode
        assert [_line_doc(api, key), _line_doc(api, key, "report")] == ids
        assert [_file(doc_id).read_bytes() for doc_id in ids] == before
        assert _read(api, doc)["body"]["steps"][0]["pre_instruction_text"] == "Keep this instruction"
        again = _preview(api, doc, "add", saved["revision_no"])
        assert again["no_change"]


def test_omitted_preinstruction_is_preserved_without_materializing_or_reading_its_attachment(api, monkeypatch):
    from flowgate.work_plan_deploy import plan
    from modules.flow_gate.services import work_plan_service as wp
    _, doc, _ = _flow(api)
    _write_plan(doc, {"T": 1})
    body = _read(api, doc)["body"]
    body["steps"][0].update(instruction_mode="omit", pre_instruction_text="Keep", pre_instruction_attachment={"filename": "reserved"})
    [target], _ = plan.targets(body, wp.card_type_of_step)
    assert plan.pre_instruction(target) == (None, None)
    assert body["steps"][0]["pre_instruction_attachment"] == {"filename": "reserved"}


@pytest.mark.parametrize("mode", [True, 1, "skip", [], {}])
def test_invalid_modes_are_rejected_without_saving(api, mode):
    _, doc, _ = _flow(api)
    _write_plan(doc, {"T": 1})
    view = _read(api, doc)
    before = deepcopy(view["body"])
    view["body"]["steps"][0]["instruction_mode"] = mode
    response = api["api"].put(f"/api/v1/documents/{doc}/work-plan",
        json={"base_revision_no": view["revision_no"], "body": view["body"]}, headers=_h())
    assert response.status_code == 422, response.text
    assert _read(api, doc)["body"] == before


def test_mode_on_a_report_is_rejected(api):
    _, doc, _ = _flow(api)
    _write_plan(doc, {"T": 1})
    view = _read(api, doc)
    view["body"]["steps"][1]["instruction_mode"] = "omit"
    response = api["api"].put(f"/api/v1/documents/{doc}/work-plan",
        json={"base_revision_no": view["revision_no"], "body": view["body"]}, headers=_h())
    assert response.status_code == 422


def test_started_or_unreadable_execution_history_blocks_reconfiguration(api, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter
    from flowgate.card_settings import repository
    _, doc, _ = _flow(api)
    _write_plan(doc, {"T": 1})
    first = _preview(api, doc, "first", 0)
    [key] = _execute(api, doc, "first", first["baseline"])["created_node_keys"]
    saved = _mode(api, doc, "omit")
    monkeypatch.setattr(legacy_adapter, "node_started", lambda *_: True)
    preview = _preview(api, doc, "add", saved["revision_no"])
    assert not preview["executable"]
    assert {"code": "instruction_mode_locked", "node_key": key} in preview["blockers"]
    assert repository.values(FA, key).get("instruction", "use") == "use"



def test_mode_changes_compare_cards_and_count_no_field_as_use():
    from modules.flow_gate.services import work_plan_service as wp

    def step(card, role="instruction", mode=None, kind="T"):
        out = {"key": card, "card_id": card, "type": kind if role == "instruction" else kind + "R", "pair_role": role}
        if mode is not None:
            out["instruction_mode"] = mode
        return out

    previous = {"steps": [step("a"), step("a", "result"), step("b", mode="omit"), step("c", mode="use")]}
    plan = {"steps": [step("a", mode="use"), step("a", "result"), step("b", mode="use"), step("c"),
                      step("new", mode="omit")]}
    assert [(i, s["card_id"]) for i, s in wp.instruction_mode_changes(previous, plan)] == [(2, "b")]
    assert wp.instruction_mode_changes(None, plan) == []
    [error] = wp.instruction_mode_started_errors(wp.instruction_mode_changes(previous, plan), {"b"})
    assert (error["code"], error["loc"], error["params"]["value"]) == (
        "instruction_mode_started", "steps[2].instruction_mode", "b")
    assert wp.instruction_mode_started_errors(wp.instruction_mode_changes(previous, plan), {"a"}) == []


def test_a_card_that_ran_in_the_graph_is_started_after_the_run_lock_and_keeps_its_mode(api, decided):
    """Review rej_01M5BJZ01320745F: a real Graph run of the deployed card, stopped (no run lock any more):
    read (started), change (refused), save (the rest goes through), deploy again (the card setting is kept)."""
    from flowgate.card_settings import repository

    _, doc, keys = _flow(api)
    _write_plan(doc, {"T": 1})
    first = _preview(api, doc, "first", 0)
    [key] = _execute(api, doc, "first", first["baseline"])["created_node_keys"]
    view = _read(api, doc)
    card = view["body"]["steps"][0]["card_id"]
    assert _started(view) == {card: False}

    _confirm_and_run_after_the_root(api, keys["tmp_r"], key)
    assert _one_job(api)                                                 # the card ran (its AI job started)
    assert _stop_run(api)["status"] not in ("running", "paused", "stopping")   # the run lock is released

    view = _read(api, doc)
    assert _started(view) == {card: True}                                # from the Graph's run history

    # Changing the started card's choice is refused, nothing written.
    before = deepcopy(view["body"])
    body = deepcopy(view["body"])
    body["steps"][0]["instruction_mode"] = "omit"
    refused = _put(api, doc, body, view["revision_no"])
    assert refused.status_code == 422, refused.text
    [error] = refused.json()["errors"]
    assert (error["code"], error["loc"], error["key"]) == (
        "instruction_mode_started", "steps[0].instruction_mode", body["steps"][0]["key"])
    again = _read(api, doc)
    assert (again["revision_no"], again["body"]) == (view["revision_no"], before)

    # A save that keeps the choice goes through; the deploy then has no locked change and keeps the setting.
    saved = _save(api, doc, lambda body: body["steps"][0].update(pre_instruction_text="Later note"))
    assert saved["body"]["steps"][0].get("instruction_mode") is None
    preview = _preview(api, doc, "add", saved["revision_no"])
    assert not [b for b in preview["blockers"] if b["code"] == "instruction_mode_locked"]
    assert repository.values(FA, key).get("instruction", "use") == "use"
    assert _started(_read(api, doc)) == {card: True}


def test_unreadable_graph_run_facts_mark_the_card_started_and_refuse_its_mode_change(api, monkeypatch):
    from flowgate.work_plan_deploy import legacy_adapter

    _, doc, _ = _flow(api)
    _write_plan(doc, {"N": 1})
    first = _preview(api, doc, "first", 0)
    _execute(api, doc, "first", first["baseline"])

    def unreadable(*_):
        raise RuntimeError("run facts unreadable")

    monkeypatch.setattr(legacy_adapter, "node_started", unreadable)
    view = _read(api, doc)
    card = view["body"]["steps"][0]["card_id"]
    assert _started(view) == {card: True}
    body = deepcopy(view["body"])
    body["steps"][0]["instruction_mode"] = "omit"
    refused = _put(api, doc, body, view["revision_no"])
    assert refused.status_code == 422, refused.text
    assert [e["code"] for e in refused.json()["errors"]] == ["instruction_mode_started"]
    # A save that leaves the choice alone does not read the run facts and goes through.
    assert _put(api, doc, view["body"], view["revision_no"]).status_code == 200


def _mode_of(env, doc, card_id, mode):
    def change(body):
        for step in body["steps"]:
            if step["pair_role"] == "instruction" and step["card_id"] == card_id:
                step["instruction_mode"] = mode
    return _save(env, doc, change)


# ── T0017 rework (rej_01M5BQWTKTAAZS2A): the design cards' 지시서 생략 ─────────────────────────────

DESIGN = ("D", "P", "L", "DB")


def _design_mode(env, doc, mode):
    def change(body):
        for step in body["steps"]:
            if step["pair_role"] == "single" and step["type"] in DESIGN:
                step["instruction_mode"] = mode
    return _save(env, doc, change)


@pytest.mark.parametrize("doc_type", DESIGN)
def test_design_omission_saved_reopened_deployed_and_run_only_as_the_design(api, decided, doc_type):
    from flowgate.card_settings import repository
    from modules.flow_gate.services import work_plan_service as wp

    _, doc, keys = _flow(api)
    _write_plan(doc, {doc_type: 1})
    before = deepcopy(_read(api, doc)["body"])
    saved = _design_mode(api, doc, "omit")
    reopened = _read(api, doc)
    assert reopened["body"] == saved["body"]
    assert reopened["body"]["steps"] == [{**step, "instruction_mode": "omit"} for step in before["steps"]]
    # The design step is the card's 설계서: it still counts as a step and still wants its AI.
    assert wp.totals(reopened["body"]) == wp.totals(before)
    assert wp.unassigned_step_count(reopened["body"]) == wp.unassigned_step_count(before)

    preview = _preview(api, doc, "first", saved["revision_no"])
    deployed = _execute(api, doc, "first", preview["baseline"])
    [key] = deployed["created_node_keys"]
    assert repository.values(FA, key)["instruction"] == "omit"
    instruction = _line_doc(api, key)

    _confirm_and_run_after_the_root(api, keys["tmp_r"], key)
    job = _one_job(api)                                                  # no 설계 지시 stage: the design runs first
    assert (job["stage"], job["result_doc_type"]) == ("result", doc_type)
    assert instruction is None or instruction not in (job["ref_doc_ids"] or [])
    assert _row(api, key)["instruction_doc_id"] is None


@pytest.mark.parametrize("doc_type", ["D", "DB"])
def test_design_mode_changes_on_an_existing_card_keep_its_documents_and_design_instruction(api, doc_type):
    from flowgate.card_settings import repository

    _, doc, _ = _flow(api)
    _write_plan(doc, {doc_type: 1})
    first = _save(api, doc, lambda body: body["steps"][0].update(pre_instruction_text="Keep this design instruction"))
    preview = _preview(api, doc, "first", first["revision_no"])
    [key] = _execute(api, doc, "first", preview["baseline"])["created_node_keys"]
    ids = [_line_doc(api, key), _line_doc(api, key, "report")]
    assert ids[0] is not None                                            # the 설계 지시 (DS) line holds the text
    before = [_file(doc_id).read_bytes() for doc_id in ids if doc_id]

    for mode in ("omit", "use", "omit"):
        saved = _design_mode(api, doc, mode)
        preview = _preview(api, doc, "add", saved["revision_no"])
        assert preview["executable"] and not preview["no_change"]
        result = _execute(api, doc, "add", preview["baseline"])
        assert result["created_node_keys"] == result["removed_node_keys"] == []
        assert repository.values(FA, key)["instruction"] == mode
        assert [_line_doc(api, key), _line_doc(api, key, "report")] == ids
        assert [_file(doc_id).read_bytes() for doc_id in ids if doc_id] == before
        assert _read(api, doc)["body"]["steps"][0]["pre_instruction_text"] == "Keep this design instruction"
        assert _preview(api, doc, "add", saved["revision_no"])["no_change"]


def test_a_design_card_that_ran_in_the_graph_keeps_its_mode(api, decided):
    """A real Graph run of a deployed design card, stopped: read (started), change (refused), deploy (kept)."""
    from flowgate.card_settings import repository

    _, doc, keys = _flow(api)
    _write_plan(doc, {"D": 1})
    first = _preview(api, doc, "first", 0)
    [key] = _execute(api, doc, "first", first["baseline"])["created_node_keys"]
    view = _read(api, doc)
    card = view["body"]["steps"][0]["card_id"]
    assert _started(view) == {card: False}

    _confirm_and_run_after_the_root(api, keys["tmp_r"], key)
    assert _one_job(api)
    assert _stop_run(api)["status"] not in ("running", "paused", "stopping")

    view = _read(api, doc)
    assert _started(view) == {card: True}
    before = deepcopy(view["body"])
    body = deepcopy(view["body"])
    body["steps"][0]["instruction_mode"] = "omit"
    refused = _put(api, doc, body, view["revision_no"])
    assert refused.status_code == 422, refused.text
    [error] = refused.json()["errors"]
    assert (error["code"], error["loc"]) == ("instruction_mode_started", "steps[0].instruction_mode")
    assert (_read(api, doc)["revision_no"], _read(api, doc)["body"]) == (view["revision_no"], before)
    preview = _preview(api, doc, "add", view["revision_no"])
    assert not [b for b in preview["blockers"] if b["code"] == "instruction_mode_locked"]
    assert repository.values(FA, key).get("instruction", "use") == "use"


def test_the_choice_belongs_to_n_t_instructions_and_design_steps_only():
    from flowgate.work_plan_deploy import plan
    from modules.flow_gate.services import work_plan_service as wp

    def step(kind, role, card="c", mode=None):
        out = {"key": f"{kind}#1", "card_id": card, "type": kind, "pair_role": role}
        if mode is not None:
            out["instruction_mode"] = mode
        return out

    allowed = [step(k, "single") for k in DESIGN] + [step("N", "instruction"), step("T", "instruction")]
    refused = [step("DS", "single"), step("TS", "instruction"), step("T2", "instruction"), step("TR", "result"),
               step("D", "instruction")]
    assert all(wp.instruction_mode_allowed(s) for s in allowed)
    assert not any(wp.instruction_mode_allowed(s) for s in refused)
    # An omitted design step is still the card's 설계서: counted, and not an omitted instruction.
    assert not wp.instruction_omitted(step("D", "single", mode="omit"))
    assert wp.totals({"steps": [step("D", "single", mode="omit")]})["steps"] == 1

    previous = {"steps": [step("D", "single", "d"), step("P", "single", "p", "omit")]}
    current = {"steps": [step("D", "single", "d", "omit"), step("P", "single", "p", "omit")]}
    assert [(i, s["card_id"]) for i, s in wp.instruction_mode_changes(previous, current)] == [(0, "d")]

    [target], _ = plan.targets({"steps": [step("L", "single", "l", "omit")]}, lambda s: s["type"])
    assert plan.instruction_mode(target) == "omit"
    assert ("instruction", "omit") in plan.initial_settings(target)
    assert plan.pre_instruction(target) == (None, None)


def test_mode_on_a_hidden_ds_step_is_rejected(api):
    _, doc, _ = _flow(api)
    _write_plan(doc, {"DS": 1, "D": 1})
    view = _read(api, doc)
    before = deepcopy(view["body"])
    [ds] = [s for s in view["body"]["steps"] if s["type"] == "DS"]
    ds["instruction_mode"] = "omit"
    response = _put(api, doc, view["body"], view["revision_no"])
    assert response.status_code == 422, response.text
    assert _read(api, doc)["body"] == before


# ── T0017 rework (rej_01M5BT6J13Z9GCWX): a dropped choice, and the revision restore ──────────────────────────

def _restore(env, doc, revision, base):
    return env["api"].post(f"/api/v1/documents/{doc}/work-plan/revisions/{revision}/restore",
                           json={"base_revision_no": base}, headers=_h())


def _choice(body):
    """The step carrying the card's instruction choice: the N/T instruction step, the design step."""
    return next(s for s in body["steps"] if s["pair_role"] in ("instruction", "single"))


def _stored(env, doc):
    """What a refused write leaves as it was: the plan file, its revision backups and their rows."""
    from test_work_plan_deploy_0074 import _doc_row
    from modules.flow_gate.services import work_plan_service as wp

    path = wp.plan_path_for_doc(_doc_row(doc))
    backups = path.parent / "revisions"
    names = sorted(p.name for p in backups.iterdir()) if backups.is_dir() else []
    rows = _sql(env["db"], "SELECT revision_no, backup_path FROM document_revisions WHERE doc_id = ? ORDER BY id",
                (doc,))
    return path.read_bytes(), names, [dict(row) for row in rows]


@pytest.mark.parametrize("dropped", ["absent", "null"])
@pytest.mark.parametrize("doc_type", ["T", "D"])
def test_a_choice_dropped_after_an_omit_deploy_is_saved_deployed_and_run_as_use(api, decided, doc_type, dropped):
    """Review rej_01M5BT6J13Z9GCWX: omit saved and deployed, then a JSON body on the same card id without the field
    (or null): stored as use → read the same, counted as use → the deploy sets use → the run starts the instruction."""
    from flowgate.card_settings import repository
    from modules.flow_gate.services import work_plan_service as wp

    _, doc, keys = _flow(api)
    _write_plan(doc, {doc_type: 1})
    use_totals = wp.totals(_read(api, doc)["body"])
    saved = _save(api, doc, lambda body: _choice(body).update(instruction_mode="omit"))
    preview = _preview(api, doc, "first", saved["revision_no"])
    [key] = _execute(api, doc, "first", preview["baseline"])["created_node_keys"]
    assert repository.values(FA, key)["instruction"] == "omit"

    view = _read(api, doc)
    body = deepcopy(view["body"])
    if dropped == "absent":
        del _choice(body)["instruction_mode"]
    else:
        _choice(body)["instruction_mode"] = None
    response = _put(api, doc, body, view["revision_no"])
    assert response.status_code == 200, response.text
    expected = deepcopy(view["body"])
    _choice(expected)["instruction_mode"] = "use"
    reopened = _read(api, doc)
    assert response.json()["body"] == reopened["body"] == expected
    assert list(_choice(reopened["body"]))[-1] == "instruction_mode"          # canonical field order
    assert wp.totals(reopened["body"]) == use_totals

    preview = _preview(api, doc, "add", reopened["revision_no"])
    assert preview["executable"] and not preview["no_change"]
    result = _execute(api, doc, "add", preview["baseline"])
    assert result["created_node_keys"] == result["removed_node_keys"] == []
    assert repository.values(FA, key)["instruction"] == "use"
    assert _preview(api, doc, "add", reopened["revision_no"])["no_change"]

    _confirm_and_run_after_the_root(api, keys["tmp_r"], key)
    assert _one_job(api)["stage"] == "instruction"                         # the instruction is not omitted any more
    assert _row(api, key)["stage"] == "instruction"


def test_only_a_card_the_plan_chose_for_gets_use_written():
    """A dropped choice becomes ``use`` (in canonical field order); a legacy plan and a new card keep no field."""
    from modules.flow_gate.services import work_plan_service as wp

    def step(card, mode="absent", **extra):
        out = {"key": f"T#{card}", "card_id": card, "type": "T", "pair_role": "instruction"}
        if mode != "absent":
            out["instruction_mode"] = mode
        return {**out, **extra}

    previous = {"steps": [step("a", "omit"), step("b"), step("c", "use")]}
    plan = {"steps": [step("a", x_note=1), step("b"), step("c", None), step("n")]}
    out = wp.instruction_mode_defaults(previous, plan)
    assert [s.get("instruction_mode", "-") for s in out["steps"]] == ["use", "-", "use", "-"]
    assert list(out["steps"][0]) == ["key", "card_id", "type", "pair_role", "instruction_mode", "x_note"]
    assert "instruction_mode" not in plan["steps"][0]                       # the submitted body is not mutated
    assert wp.instruction_mode_defaults({"steps": [step("a")]}, plan) is plan
    assert wp.instruction_mode_defaults(None, plan) is plan


def test_restoring_a_revision_without_the_choice_over_an_omit_deploy_restores_use(api):
    """An older revision that never chose, restored over a deployed omit (the card not started): stored as use, and the
    deploy sets use, so the restored plan is what the card runs."""
    from flowgate.card_settings import repository

    _, doc, _ = _flow(api)
    _write_plan(doc, {"T": 1})
    plain = _save(api, doc, lambda body: _choice(body).update(pre_instruction_text="Plain revision"))
    omitted = _mode(api, doc, "omit")
    preview = _preview(api, doc, "first", omitted["revision_no"])
    [key] = _execute(api, doc, "first", preview["baseline"])["created_node_keys"]
    assert repository.values(FA, key)["instruction"] == "omit"

    response = _restore(api, doc, plain["revision_no"], omitted["revision_no"])
    assert response.status_code == 200, response.text
    reopened = _read(api, doc)
    assert reopened["revision_no"] == omitted["revision_no"] + 1
    assert response.json()["body"] == reopened["body"]
    assert _choice(reopened["body"])["instruction_mode"] == "use"
    assert _choice(reopened["body"])["pre_instruction_text"] == "Plain revision"
    preview = _preview(api, doc, "add", reopened["revision_no"])
    assert preview["executable"] and not preview["no_change"]
    _execute(api, doc, "add", preview["baseline"])
    assert repository.values(FA, key)["instruction"] == "use"


@pytest.mark.parametrize("doc_type", ["T", "D"])
def test_a_restore_that_changes_a_started_cards_choice_is_refused_and_writes_nothing(api, decided, doc_type):
    """Review rej_01M5BT6J13Z9GCWX: use saved, then omit saved, deployed and run in the Graph, the run stopped; the
    use revision's restore is refused before anything is written (revision, plan file, backups and their rows, the
    card setting). A restore that keeps the started card's choice still goes through."""
    from flowgate.card_settings import repository

    _, doc, keys = _flow(api)
    _write_plan(doc, {doc_type: 1})
    use = _save(api, doc, lambda body: _choice(body).update(instruction_mode="use"))
    omitted = _save(api, doc, lambda body: _choice(body).update(instruction_mode="omit"))
    preview = _preview(api, doc, "first", omitted["revision_no"])
    [key] = _execute(api, doc, "first", preview["baseline"])["created_node_keys"]
    _confirm_and_run_after_the_root(api, keys["tmp_r"], key)
    assert _one_job(api)["stage"] == "result"                               # omitted: no instruction stage
    assert _stop_run(api)["status"] not in ("running", "paused", "stopping")

    view = _read(api, doc)
    card = _choice(view["body"])["card_id"]
    assert _started(view) == {card: True}
    before = _stored(api, doc)
    refused = _restore(api, doc, use["revision_no"], view["revision_no"])
    assert refused.status_code == 422, refused.text
    answer = refused.json()
    assert (answer["code"], answer["reason"]) == ("wp_revision_not_restorable", "instruction_mode_started")
    assert [error["code"] for error in answer["errors"]] == ["instruction_mode_started"]
    assert card in answer["message"]
    again = _read(api, doc)
    assert (again["revision_no"], again["body"]) == (view["revision_no"], view["body"])
    assert _stored(api, doc) == before
    assert repository.values(FA, key)["instruction"] == "omit"
    assert _started(again) == {card: True}

    # The omit revision itself (the started card's choice kept) still restores.
    later = _save(api, doc, lambda body: _choice(body).update(note="later"))
    restored = _restore(api, doc, omitted["revision_no"], later["revision_no"])
    assert restored.status_code == 200, restored.text
    assert _choice(_read(api, doc)["body"])["instruction_mode"] == "omit"
    assert repository.values(FA, key)["instruction"] == "omit"
