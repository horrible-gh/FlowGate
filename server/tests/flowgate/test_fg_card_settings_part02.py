"""Independently selectable scenarios; shared fixtures remain in test_fg_card_settings.py."""
from __future__ import annotations

from test_fg_card_settings import (
    AI,
    FA,
    FB,
    FastAPI,
    JSONResponse,
    Path,
    RUN_FIELDS,
    TEST_VALUE,
    TEST_VERSION,
    TestClient,
    _GIT,
    _MIGRATIONS_DIR,
    _POLICY,
    _SERVER_DIR,
    _TARGET_VIEW_KEYS_0148,
    _TestPolicy,
    _card_lines,
    _doc,
    _fake_ai_invoke,
    _gate_key,
    _get_refreshed,
    _git_cmd,
    _h,
    _put_raw,
    _run_one_card,
    _seed,
    _sql,
    _start,
    _start_job,
    base64,
    db_path,
    decided,
    edge,
    env,
    get_settings,
    git_flow,
    json,
    legacy_cards,
    legacy_setting,
    node,
    os,
    passive_person_results,
    patch,
    placed,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    re,
    setting,
    shown,
    shutil,
    sqlite3,
    stored_rows,
    subprocess,
    sys,
)

def test_without_the_port_handler_a_node_ignores_its_card(env, decided):
    from flowgate.card_settings import integration

    key = _run_one_card(env, AI)
    legacy_setting(env, FA, key, "run_ai", "aip_card")
    integration.unregister()
    _start(env)
    [job] = env["jobs"]
    assert (job["provider_id"], job["step_timeout_sec"], job["restart_max"]) == ("aip_assignee", None, None)
def test_a_human_node_does_not_read_its_card(env, decided):
    key = _run_one_card(env, {"kind": "human", "user_id": "u_lee", "provider_id": None})
    legacy_setting(env, FA, key, "run_ai", "aip_card")
    _start(env)
    assert env["jobs"] == []
    token = _sql(env["db"], "SELECT t.provider_id, t.issued_to FROM tokens t JOIN graph_run_token_map m "
                            "ON m.token_id = t.token_id WHERE m.node_key = ?", (key,))[0]
    assert (token["provider_id"], token["issued_to"]) == (None, "u_lee")
def test_turning_ai_delegation_on_and_off_adds_and_removes_its_line(env):
    from flowgate.card_documents import repository, slots, legacy_adapter
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import groups
    key = put_draft(env, FB, 0, [node("tmp_r", "R")])["key_map"]["tmp_r"]
    legacy_setting(env, FB, key, "delegation", "on")
    # A historical draft line must survive settings refusals and R/B retyping.
    group = groups.get_by_id(FB)
    spec = slots.card_slots("document", "R", ai_delegation=True)[1]
    old_node = {"node_key": key, "node_type": "document", "doc_type": "R", "label": "R"}
    doc_id = legacy_adapter.create_document(group, old_node, spec, "u_kim")
    repository.insert(FB, key, "delegation", "M", doc_id, True, 1, "u_kim")
    before = db_documents.get_by_id(doc_id)
    for value in ("on", "off", None):
        setting(env, FB, key, "delegation", value, expect=422)
    for version, kind in ((1, "R"), (2, "B")):
        saved = put_draft(env, FB, version, [node(key, kind)])
        assert any(r["doc_id"] == doc_id for r in saved["card_documents"][key])
        assert db_documents.get_by_id(doc_id) == before
    assert stored_rows(env, FB)[(key, "delegation")] == '\"on\"'
def test_after_review_is_no_setting_and_a_stored_value_is_not_read(env):
    from flowgate.card_settings import fields as f

    key = placed(env, FB, "T")
    refused = setting(env, FB, key, "after_review", "human", expect=422)
    assert refused["error"]["params"]["errors"] == [
        {"field": "field", "reason": "not_a_setting_of_this_card", "card_kind": "run"}]
    _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                    "VALUES (?, ?, 'after_review', '\"human\"', 't')", (FB, key))
    body = get_settings(env, FB, key)                                     # a row left from before: not read
    assert "after_review" not in body["fields"] and list(body["fields"]) == RUN_FIELDS + ["hold"]
    assert f.run_plan(f.RUN, {"after_review": "human"}) == {"settings": {}, "step_timeout_sec": 14400, "restart_max": 3}
    # what follows a result is the former follow, whatever was stored
    assert [f.after_result(kind, {"after_review": "human"}) for kind in (f.RUN, f.TEST)] == [f.APPROVE, f.APPROVE]
    assert all(f.field_of(kind, "after_review") is None for kind in f.CARD_KINDS)
def test_migration_147_deletes_only_the_after_review_rows(env):
    key = placed(env, FB, "T")
    setting(env, FB, key, "restart", 1)
    _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                    "VALUES (?, ?, 'after_review', '\"human\"', 't')", (FB, key))
    sql = (_MIGRATIONS_DIR / "147_graph_node_settings_after_review.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(env["db"])
    try:
        conn.executescript(sql)
    finally:
        conn.close()
    assert stored_rows(env, FB) == {(key, "restart"): "1"}
def test_migration_147_is_the_same_delete_in_three_dialects():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "147_graph_node_settings_after_review.sql").read_text(
            encoding="utf-8")
        statements = [line.strip() for line in sql.splitlines()
                      if line.strip() and not line.startswith("--") and line.strip() not in ("BEGIN;", "COMMIT;")]
        assert statements == ["DELETE FROM graph_node_settings WHERE field = 'after_review';"], dialect
def test_migration_146_table_trigger_and_flow_delete_cleanup(env):
    tables = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'table'")}
    triggers = {r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert "graph_node_settings" in tables and "trg_graph_node_settings_group_deleted" in triggers
    setting(env, FB, placed(env, FB, "T"), "restart", 1)
    setting(env, FA, placed(env, FA, "N"), "restart", 1)
    conn = sqlite3.connect(env["db"])
    try:
        conn.execute("DELETE FROM groups WHERE group_id = ?", (FB,))
        conn.commit()
    finally:
        conn.close()
    assert stored_rows(env, FB) == {} and len(stored_rows(env, FA)) == 1
def test_migration_146_has_the_same_table_in_three_dialects():
    found = {}
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (_SERVER_DIR / "sql" / "migrations" / dialect / "146_graph_node_settings.sql").read_text(encoding="utf-8")
        found[dialect] = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
        assert ("CREATE TRIGGER" in sql) == (dialect == "sqlite"), dialect
        assert not re.search(r"\b(ALTER|DROP|UPDATE)\b", sql), dialect
        assert "PRIMARY KEY (group_id, node_key, field)" in sql, dialect
    assert found["sqlite"] == found["postgres"] == found["mysql"] == ["graph_node_settings"]
def test_startup_registers_the_port_and_main_mounts_the_routes():
    import startup
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    from flowgate.card_settings import integration

    integration.unregister()
    try:
        assert not ports.registered(ports.RUN_SETTINGS)
        startup.start_card_settings_integration()
        assert ports.registered(ports.RUN_SETTINGS)
        assert "start_card_settings_integration()" in (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    finally:
        integration.unregister()
    main = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    assert "from flowgate.card_settings.routes import router as _card_settings_router" in main
    assert 'app.include_router(_card_settings_router, prefix=f"{CONTEXT}", tags=["CardSettings"])' in main
def test_the_feature_is_in_the_v02_package_and_the_graph_holds_only_the_port():
    import ast

    pkg = _SERVER_DIR / "flowgate" / "card_settings"
    assert {p.stem for p in pkg.glob("*.py")} == {
        "__init__", "fields", "settings", "review_stage", "routes", "repository", "legacy_adapter", "integration",
        "assigned_review", "context", "manual_stage", "question_wait", "report_baseline",
        "stage_assignment", "stage_view",
        "result_recovery", "review_exhaustion",      # their own files already (the list lagged behind)
        "verify_limits",                             # flowgate.v02test.0053 0003-T: the one 검증 limits function
        "action_guard"}                     # flowgate.v02gui.0015 T3: the answered step's common guard
    graph = _SERVER_DIR / "modules" / "flow_gate" / "graph_workflow"
    callers = sorted(p.relative_to(graph).as_posix() for p in graph.rglob("*.py")
                     if "card_settings_ports" in p.read_text(encoding="utf-8") and p.name != "card_settings_ports.py")
    # ai_node_starter / root_start are callers added by later cards (the list lagged behind, like the package files above).
    assert callers == ["services/ai_node_starter.py", "services/graph_service.py", "services/node_approval.py",
                       "services/node_dispatch.py", "services/node_title.py", "services/result_binder.py",
                       "services/root_start.py", "services/run_service.py"]
    tree = ast.parse((graph / "services" / "card_settings_ports.py").read_text(encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "logging", "threading", "typing"}
@pytest.mark.parametrize("locale, name", [("ko", "AI 위임"), ("ja", "AI委任"), ("en", "AI delegation")])
def test_turning_ai_delegation_on_titles_its_new_document_in_the_request_s_language(env, locale, name):
    key = put_draft(env, FB, 0, [node("tmp_r", "R")])["key_map"]["tmp_r"]
    legacy_setting(env, FB, key, "delegation", "on")
    res = env["client"].patch(f"/api/v1/flows/{FB}/nodes/{key}/settings",
        json={"field": "delegation", "value": "on"}, headers={**_h(), "x-locale": locale})
    assert res.status_code == 422
    put_draft(env, FB, 1, [node(key, "R")])
    assert _card_lines(env, FB, key)[0] == [("requirement", "R")]
def test_test_card_confirmation_settings_round_trip_and_stay_out_of_ai_plan(env):
    from flowgate.card_settings import fields as f
    first = put_draft(env, FB, 0, [node("tmp_ts", "TS"), node("tmp_t", "T")])
    ts_key, task_key = first["key_map"]["tmp_ts"], first["key_map"]["tmp_t"]
    initial = get_settings(env, FB, ts_key)
    assert shown(initial)["result_cause"] == "ai"
    assert shown(initial)["follow_scope"] == "summary"
    assert initial["fields"]["result_cause"]["choices"] == ["ai", "human"]
    assert initial["fields"]["follow_scope"]["choices"] == ["summary", "full"]
    for name, value in (("result_cause", "human"), ("follow_scope", "full")):
        setting(env, FB, ts_key, name, value)
        assert shown(get_settings(env, FB, ts_key))[name] == value
        setting(env, FB, ts_key, name, "unsupported", expect=422)
        assert shown(get_settings(env, FB, ts_key))[name] == value
        setting(env, FB, task_key, name, value, expect=422)
    assert f.run_plan(f.TEST, {"run_ai": "aip_card", "result_cause": "human", "follow_scope": "full"}) == {
        "settings": {"run_ai": "aip_card"}, "provider_id": "aip_card"}
def test_local_branch_checks_the_value_alone_and_never_rewrites_it():
    from flowgate.card_settings import fields as f

    field = f.field_of(f.CHECK, "merge_target_branch")
    assert (field.kind, field.default, f.choices_of(field, ())) == (f.LOCAL_BRANCH, None, None)
    assert f.field_of(f.CHECK, "merge_target").choices == ("origin",)          # the remote keeps its meaning
    assert f.field_of(f.RUN, "merge_target_branch") is None                    # a check card setting only
    kw = {"repeat_choices": (), "team_exists": lambda _: False, "ai_exists": lambda _: False}
    # Git's own name rules and the branch itself are the save's repository check, not the value check
    for name in ("main", "feature/v0.2.0", "a..b", "release/v0.2", "한글", "x" * 300):
        assert f.check_value(field, name, **kw) == name
    for bad, reason in (("", "not_a_text"), (1, "not_a_text"), (True, "not_a_text"), (["main"], "not_a_text"),
                        (" main", "surrounding_whitespace"), ("main\n", "surrounding_whitespace"),
                        ("   ", "surrounding_whitespace"), ("a\tb", "control_character"),
                        ("a\x7fb", "control_character")):
        with pytest.raises(f.InvalidValue) as caught:
            f.check_value(field, bad, **kw)
        assert caught.value.reason == reason, bad
