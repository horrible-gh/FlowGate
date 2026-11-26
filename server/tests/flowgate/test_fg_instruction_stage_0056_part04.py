"""Independently selectable scenarios; shared fixtures remain in test_fg_instruction_stage_0056.py."""
from __future__ import annotations

from test_fg_instruction_stage_0056 import (
    AI,
    FA,
    INSTRUCTION_RUN,
    Path,
    ROOT,
    WORK_RUN,
    _SERVER_DIR,
    _admin_and_reviewer,
    _before_migration_148,
    _blank,
    _card,
    _count,
    _current,
    _doc,
    _events,
    _h,
    _line,
    _new_instruction_start,
    _node,
    _node_run,
    _one_job,
    _record_start,
    _recorded_start_run,
    _register,
    _register_as_the_inbox,
    _result,
    _resume,
    _review_status,
    _row,
    _run_ended,
    _run_id,
    _run_one_card,
    _source_scopes,
    _sql,
    _start,
    _start_ai,
    _stop,
    _through_instruction,
    _token,
    _token_stage,
    _work_run_ended,
    _write_written,
    card_outputs_port,
    db_path,
    decided,
    edge,
    env,
    get_settings,
    json,
    legacy_cards,
    legacy_setting,
    node,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    setting,
    sqlite3,
)

def test_the_node_run_tells_its_stage_and_its_instruction(env, decided, monkeypatch):
    _admin_and_reviewer(env)
    key = _card(env, "T")
    _start(env)
    instruction = _line(env, key, "instruction")
    shown = _node_run(env, key)
    assert (shown["status"], shown["stage"], shown["instruction_doc_id"], shown["awaiting_approval"]) == (
        "open", "instruction", instruction, False)
    _through_instruction(env, monkeypatch)
    shown = _node_run(env, key)
    assert (shown["stage"], shown["instruction_doc_id"]) == ("result", instruction)
    canvas = env["client"].get(f"/api/v1/flows/{FA}/canvas", headers=_h()).json()
    card = next(n for n in canvas["nodes"] if n["node_key"] == key)
    assert (card["node_run"]["stage"], card["node_run"]["instruction_doc_id"]) == ("result", instruction)
def test_the_instruction_line_is_written_by_the_instruction_ai_or_a_person(env):
    first = put_draft(env, FA, 0, [node("tmp_t", "T"), node("tmp_s", "TS"), node("tmp_x", None, node_type="custom")])
    km, lines = first["key_map"], first["card_documents"]
    assert [(x["slot"], x["author"]) for x in lines[km["tmp_t"]]] == [
        ("instruction", "instruction_ai_or_human"), ("report", "run")]
    assert [(x["slot"], x["author"]) for x in lines[km["tmp_x"]]] == [
        ("instruction", "instruction_ai_or_human"), ("report", "run")]
    assert [(x["slot"], x["author"]) for x in lines[km["tmp_s"]]] == [
        ("scenario", "run"), ("report", "run")]            # TS: a person's
    from flowgate.card_documents import slots

    assert (slots.author_of("instruction", "T"), slots.author_of("instruction", "DS"),
            slots.author_of("instruction", "TS"), slots.author_of("requirement", "R"), slots.author_of("report", "TR")) == (
        "instruction_ai_or_human", "instruction_ai_or_human", "human", "human", "run")
    # a node without a document of its own shows its instruction first, as it showed a person's line
    from modules.flow_gate.graph_workflow.projection.graph_projector import first_line_document

    by_id = {"d_i": {"doc_id": "d_i"}, "d_r": {"doc_id": "d_r"}}
    assert first_line_document([{"doc_id": "d_r", "author": "run"}, {"doc_id": "d_i",
                                "author": "instruction_ai_or_human"}], by_id) == {"doc_id": "d_i"}
def test_the_source_tools_judge_a_node_token_by_its_stage(env, decided, monkeypatch):
    """The instruction stage writes the instruction (T: read only); the result stage writes the report (TR: the
    source write scopes), for the dispatch token as well as the run's own."""
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(git_service, "ensure_worktree", lambda *_a, **_k: "ok")
    _admin_and_reviewer(env)
    _card(env, "T")
    _start(env)
    job = _one_job(env)
    dispatch_raw = job["token"]["raw_token"]
    _mention, _start_kw, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
    for raw in (dispatch_raw, issue["raw_token"]):
        assert "write" not in _source_scopes(raw)
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    result_job = _one_job(env)
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    for raw in (dispatch_raw, issue["raw_token"]):
        assert {"write", "remove"} <= _source_scopes(raw)
def test_the_body_judgement_is_one_function_the_v02_cards_share(env):
    from flowgate.card_completion import legacy_adapter, outputs
    from flowgate.card_documents import lines

    first = put_draft(env, FA, 0, [node("tmp_t", "T")])
    instruction = first["card_documents"][first["key_map"]["tmp_t"]][0]["doc_id"]
    from modules.flow_gate.db import documents as db_documents

    doc = lambda: db_documents.get_by_id(instruction)                     # noqa: E731
    assert lines.has_body(None) is False
    assert lines.has_body(doc()) is False                                # the card's draft: front matter only
    _blank(instruction, "\n \t \n")
    assert lines.has_body(doc()) is False                                # blanks only
    _blank(instruction, "\n# 지시\n\n본문\n")
    assert lines.has_body(doc()) is True
    db_documents.update(instruction, {"file_path": None})
    assert lines.has_body(doc()) is False                                # no readable file
    # the completion's output lines ask the same function (no judgement of its own; 0119 T-3: moved from the removed
    # card approval)
    assert not hasattr(legacy_adapter, "has_body") and not hasattr(outputs, "has_body")
    src = (_SERVER_DIR / "flowgate" / "card_completion" / "outputs.py").read_text(encoding="utf-8")
    assert "card_lines.has_body(" in src
    settings_src = (_SERVER_DIR / "flowgate" / "card_settings" / "settings.py").read_text(encoding="utf-8")
    assert "card_lines.has_body(" in settings_src
def test_the_stage_ports_are_registered_and_answer_nothing_without_a_handler():
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    from flowgate.card_settings import integration

    integration.unregister()
    try:
        assert ports.instruction_document("f", {"node_key": "k"}) == {}
        assert ports.approve_instruction("f", {"node_key": "k"}, "d", "u") is False
        integration.register()
        assert ports.registered(ports.INSTRUCTION_DOCUMENT) and ports.registered(ports.APPROVE_INSTRUCTION)
    finally:
        integration.unregister()
def test_migration_148_adds_the_stage_columns_in_every_dialect():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "148_graph_node_run_stage.sql").read_text(encoding="utf-8")
        assert "ALTER TABLE graph_node_runs ADD COLUMN stage" in sql
        assert "ALTER TABLE graph_node_runs ADD COLUMN instruction_doc_id" in sql
        assert "ALTER TABLE graph_node_runs ADD COLUMN result_event_floor" in sql
        assert "ALTER TABLE graph_run_token_map ADD COLUMN stage" in sql
