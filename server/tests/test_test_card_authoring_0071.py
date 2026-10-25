"""T0015: editor persistence must not erase surrounding document content."""
import pytest
from modules.flow_gate.services import test_spec_service as spec

CASE = dict(case_id="TC-1", title="One", category="normal", requirement="R1",
            execution_mode="manual", required=True, procedure="Look", expected="Visible", check_points="Visible")

@pytest.mark.parametrize("prefix", ["Text without H1  \n\n", "before title\n# Original\n\n## Context\nkeep  \n\n", "# Title\n\nintro\n\n"])
@pytest.mark.parametrize("section", spec.SPEC_SECTION_NAMES)
def test_structured_save_replaces_only_spec_section(prefix, section):
    suffix = "## Notes\nkeep trailing spaces  \n\n## Evidence\nunchanged\n"
    source = "---\ncustom: keep\ntest_contract_version: 2\n---\n" + prefix + "## " + section + "\nold cases\n" + suffix
    saved = spec.replace_spec_cases(source, [CASE], title="Ignored existing title")
    assert saved.startswith("---\ncustom: keep\ntest_contract_version: 2\n---\n" + prefix)
    assert saved.endswith(suffix)
    assert spec.parse_spec(saved)["cases"][0]["title"] == "One"

def test_initial_case_save_retains_body_without_title():
    source = "Instructions without a title\n\n## References\nretain\n"
    saved = spec.replace_spec_cases(source, [CASE], title="New")
    assert source in saved
    assert len(spec.parse_spec(saved)["cases"]) == 1

@pytest.mark.parametrize("section", ["## 시험 사양", "## 테스트 케이스"])
def test_brief_round_trip_preserves_frontmatter_title_and_all_case_sections(section):
    suffix = section + "\n### TC-1: original\n- cmd: keep\n\n## Notes\nkeep  \n"
    source = "---\ncustom: keep\n---\n\n# Original\n\nold brief\n\n" + suffix
    assert spec.brief_parts(source)[1] == "old brief"
    saved = spec.replace_brief(source, "new brief")
    assert saved.startswith("---\ncustom: keep\n---\n\n# Original\n")
    assert saved.endswith(suffix)
    assert spec.brief_parts(saved)[1] == "new brief"
    assert spec.detect_contract_version(saved) == spec.detect_contract_version(source)


@pytest.fixture
def brief_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.db import users
    state = {"content": "---\ntest_contract_version: 2\n---\n# Cases\n\nOriginal\n\n## 시험 사양\ncase source\n## Notes\nkeep  \n", "writes": 0}
    monkeypatch.setattr(users, "get_by_id", lambda _id: {"user_id": "editor", "is_admin": 1})
    monkeypatch.setattr(routes.document_service, "get_document", lambda _id: {"doc_id": "ts", "type_code": "TS"})
    monkeypatch.setattr(test_run_service, "_read_doc_content_or_empty", lambda _doc: state["content"])
    def persist(doc_id, body, user, request=None):
        state["content"] = body.content
        state["writes"] += 1
        return {"ok": True}
    monkeypatch.setattr(routes, "update_document_content", persist)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_current_user] = lambda: {"user_id": "editor"}
    with TestClient(app) as client:
        yield client, state, routes.router.prefix


def test_brief_api_save_reopen_conflict_and_length(brief_client):
    client, state, prefix = brief_client
    url = prefix + "/ts/test-brief"
    first = client.get(url)
    assert first.status_code == 200, first.text
    original = first.json()
    assert original["text"] == "Original"
    saved = client.put(url, json={"text": "New instruction", "expected_content": original["content"]})
    assert saved.status_code == 200, saved.text
    assert client.get(url).json()["text"] == "New instruction"
    assert state["content"].endswith("## 시험 사양\ncase source\n## Notes\nkeep  \n")
    conflict = client.put(url, json={"text": "Lost update", "expected_content": original["content"]})
    assert conflict.status_code == 409
    assert state["writes"] == 1 and "Lost update" not in state["content"]
    too_long = client.put(url, json={"text": "x" * 20001, "expected_content": state["content"]})
    assert too_long.status_code == 422 and state["writes"] == 1


# TR0016 rev1: the three v9 verification modes and the case editor's save contract.

@pytest.mark.parametrize("mode", ["automated", "ai", "manual"])
def test_each_v9_verification_mode_saves_and_rereads(mode):
    cases, errors = spec.validate_cases_payload([{**CASE, "execution_mode": mode}])
    assert errors == [] and cases[0]["execution_mode"] == mode
    saved = spec.replace_spec_cases("# T\n\n## 시험 사양\nold\n", cases, title="T")
    assert f"- execution_mode: {mode}\n" in saved
    assert spec.parse_spec(saved)["cases"][0]["execution_mode"] == mode


def test_absent_required_flag_means_required_as_the_editor_and_client_read_it():
    body = ("---\ntest_contract_version: 2\n---\n# T\n\n## 시험 사양\n\n### TC-1: One\n- category: normal\n"
            "- requirement: R1\n- execution_mode: ai\n- procedure: Look\n- expected: Visible\n- check_points: Visible\n")
    parsed = spec.parse_spec(body)
    assert parsed["errors"] == [] and parsed["cases"][0]["required"] is True
    payload = {key: value for key, value in CASE.items() if key != "required"}
    cases, errors = spec.validate_cases_payload([payload])
    assert errors == [] and cases[0]["required"] is True
    assert "- required: true\n" in spec.render_spec_body([payload], title="T")


def test_all_optional_cases_save_and_the_last_case_can_be_deleted():
    _cases, errors = spec.validate_cases_payload([{**CASE, "required": False}])
    assert errors == [] and _cases[0]["required"] is False
    assert spec.validate_cases_payload([]) == ([], [])
    source = "---\ntest_contract_version: 2\n---\n# T\n\n## 시험 사양\n\n### TC-1: One\n- required: true\n\n## Notes\nkeep  \n"
    emptied = spec.replace_spec_cases(source, [], title="T")
    assert emptied.endswith("## Notes\nkeep  \n") and "### TC-1" not in emptied
    # Approval and result intake still refuse an empty specification.
    assert {error["code"] for error in spec.parse_spec(emptied)["errors"]} == {"no_cases"}


def test_ai_cases_are_recorded_by_the_ai_worker_not_by_the_server_run(monkeypatch):
    from modules.flow_gate.services import spec_execution_service, test_basis_service, test_run_service
    cases = [{**CASE, "case_id": "TC-1", "execution_mode": "automated", "automation_ref": "tests/test_x.py::test_a"},
             {**CASE, "case_id": "TC-2", "title": "AI", "execution_mode": "ai"},
             {**CASE, "case_id": "TC-3", "execution_mode": "manual", "required": False}]
    assert test_run_service.result_entry_case_ids(cases) == ["TC-2", "TC-3"]
    assert test_basis_service.locator(cases[1]) == {"capability": "ai"}
    assert all(case["execution_mode"] == "automated" for case in spec_execution_service.automated_selection(cases))
    results = spec.normalize_results([{"case_id": "TC-1", "status": "PASS"},
                                      {"case_id": "TC-2", "status": "FAIL", "execution_mode": "ai", "actual": "seen"}],
                                     submitted_by="worker", now="2026-10-25T00:00:00+09:00")
    kept, refused = test_run_service.refuse_server_run_results(cases, results)
    assert [item["case_id"] for item in kept] == ["TC-2"]
    assert [item["case_id"] for item in refused] == ["TC-1"]
    rows = spec.map_results(cases, kept)["cases"]
    assert {row["case_id"]: row["status"] for row in rows}["TC-2"] == "FAIL"
    content = "---\ntest_contract_version: 2\n---\n" + spec.render_spec_body(cases, title="T")
    monkeypatch.setattr(test_run_service, "_read_doc_content_or_empty", lambda _doc: content)
    mention = test_run_service._build_spec_result_mention(
        doc={"doc_id": "ts", "project_id": "p", "group_id": "g", "title": "T"}, api_base_url="http://x", raw_token="t")
    record, server_run = mention.split("Cases you record:\n", 1)[1].split("Automated Cases", 1)
    assert "- TC-2 (ai, required): AI" in record and "TC-1" not in record
    assert "TC-1" in server_run and "`ai` Cases (AI check) are verified by you" in mention


def test_structured_save_api_stores_ai_mode_and_all_optional_cases(brief_client):
    client, state, prefix = brief_client
    url = prefix + "/ts/test-spec"
    saved = client.put(url, json={"cases": [{**CASE, "execution_mode": "ai"},
                                            {**CASE, "case_id": "TC-2", "required": False}]})
    assert saved.status_code == 200, saved.text
    reread = spec.parse_spec(state["content"])
    assert reread["errors"] == []
    assert [(c["case_id"], c["execution_mode"], c["required"]) for c in reread["cases"]] == [
        ("TC-1", "ai", True), ("TC-2", "manual", False)]
    assert state["content"].endswith("## Notes\nkeep  \n")
    refused = client.put(url, json={"cases": [{**CASE, "required": False}]})
    assert refused.status_code == 200, refused.text
    assert spec.parse_spec(state["content"])["cases"][0]["required"] is False
    assert state["writes"] == 2
    emptied = client.put(url, json={"cases": []})
    assert emptied.status_code == 200, emptied.text
    assert spec.parse_spec(state["content"])["cases"] == [] and state["content"].endswith("## Notes\nkeep  \n")


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_0094_automated_authoring_binds_real_code_and_its_command(locale):
    from modules.flow_gate.services import mention_service, tool_registry
    from modules.flow_gate.services.test_basis_service import locator
    guide = mention_service._TS_SPEC_AUTHORING_TEXT[locale]
    # No framework is the premise: one command, in any language, test file or not (B0005).
    assert '- run_command: <' in guide and '- automation_ref: <' not in guide and '- run_cwd: <' not in guide
    assert 'assertion' in guide and '{junit}' in guide and '{target}' not in guide and '{node}' not in guide
    for example in ('node --test tests/login.test.mjs', 'npm test', 'go test ./...'):
        assert example in guide
        assert locator({'execution_mode': 'automated', 'run_command': example})['capability'] == 'case_selectable', example
    assert 'tests/test_x.py::test_tc_001' not in guide and '.py' not in guide
    assert tool_registry.kind_for_step('edit', 'TS')[0] == 'read_write'
    automated = {'execution_mode': 'automated'}
    assert locator({**automated, 'automation_ref': '6', 'run_command': 'npm test'})['capability'] == 'unbound'
    assert locator({**automated, 'run_command': 'bash run.sh'})['capability'] == 'command_invalid'
    assert locator({**automated, 'automation_ref': 'client_gui/tests/a.spec.ts'})['capability'] == 'command_missing'
    assert locator({**automated, 'automation_ref': 'client_gui/tests/a.spec.ts', 'run_command': 'npm test -- {target}',
                    'run_cwd': 'client_gui'})['capability'] == 'case_selectable'
