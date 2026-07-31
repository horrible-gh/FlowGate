"""Server-wide Hangul leak guard for non-Korean output (0355 T0023).

The suite combines three independent boundaries:
* AST discovery of every locale-aware server module and all en/ja literals;
* runtime probes of assembled instructions, errors, and stored type descriptions;
* recursive inspection of real HTTP response bodies for the affected routers.
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MODULE_ROOT = _SERVER_DIR / "modules" / "flow_gate"
sys.path.insert(0, str(_SERVER_DIR))

_HANGUL = re.compile(r"[가-힣]")
_LOCALE_MARKER = re.compile(
    r"continuation_locale|x_locale|normalize_locale|locale\s*==|SUPPORTED_LOCALES"
)
_REQUIRED_DISCOVERY = {
    "api/inbox_routes.py",
    "api/token_routes.py",
    "api/v1/ai_invoke_routes.py",
    "api/v1/help_routes.py",
    "api/v1/qa_routes.py",
    "db/tokens.py",
    "documents/routers/documents.py",
    "process_service.py",
    "services/ai_invoke_service.py",
    "services/engine_recipe_service.py",
    "services/invoke_mention_service.py",
    "services/mention_service.py",
    "services/q_answer_invoke_service.py",
    "services/remote_tool_service.py",
    "services/test_run_service.py",
    "services/token_service.py",
    "services/workflow_decision_service.py",
    "settings/routers/project_settings.py",
    "template_provision.py",
}


def _walk_strings(payload, path: str = "$"):
    if isinstance(payload, str):
        yield path, payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str):
                yield f"{path}.<key>", key
            yield from _walk_strings(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple, set)):
        for index, value in enumerate(payload):
            yield from _walk_strings(value, f"{path}[{index}]")


def assert_no_korean_leak(payload) -> None:
    leaks = [
        f"{path}: {value!r}"
        for path, value in _walk_strings(payload)
        if _HANGUL.search(value)
    ]
    assert not leaks, "Korean syllable leak(s):\n" + "\n".join(leaks[:20])


def _is_locale_source(source: str) -> bool:
    explicit = _LOCALE_MARKER.search(source)
    localized_map = re.search(r"\blocale\b", source, re.I) and '"en"' in source and '"ja"' in source
    return bool(explicit or localized_map)


def _discover_locale_files() -> list[Path]:
    return [
        path
        for path in sorted(_MODULE_ROOT.rglob("*.py"))
        if _is_locale_source(path.read_text(encoding="utf-8-sig"))
    ]


def _constant_strings(node: ast.AST):
    for part in ast.walk(node):
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            yield part


def _localized_literals(path: Path):
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    seen: set[tuple[int, str, str]] = set()

    # Locale maps may be nested arbitrarily. Once an en/ja key is reached, every
    # string under that value belongs to that non-Korean output branch.
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in {"en", "ja"}:
                    for literal in _constant_strings(value):
                        item = (literal.lineno, key.value, literal.value)
                        if item not in seen:
                            seen.add(item)
                            yield item

    # Also cover direct branches such as `if locale == "en": return "..."`.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.IfExp)):
            continue
        test_source = ast.get_source_segment(source, node.test) or ""
        if not re.search(r"\blocale\b|continuation_locale|x_locale", test_source, re.I):
            continue
        locales = set(re.findall(r"[\"'](en|ja)[\"']", test_source))
        branch = node.body if isinstance(node, ast.If) else [node.body]
        for locale in locales:
            for statement in branch:
                for literal in _constant_strings(statement):
                    item = (literal.lineno, locale, literal.value)
                    if item not in seen:
                        seen.add(item)
                        yield item


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(_SERVER_DIR).with_suffix("").parts)


def _localized_mapping_values(value):
    if not isinstance(value, dict):
        return
    for locale in ("en", "ja"):
        if locale in value:
            yield locale, value[locale]


def test_static_locale_branch_scan_has_zero_korean():
    files = _discover_locale_files()
    relative = {path.relative_to(_MODULE_ROOT).as_posix() for path in files}
    assert _REQUIRED_DISCOVERY <= relative
    assert len(files) >= 20, "locale discovery unexpectedly narrowed"

    scanned = []
    for path in files:
        for line, locale, value in _localized_literals(path):
            scanned.append((path, line, locale, value))
            assert_no_korean_leak(value)

    assert len(scanned) >= 100, "AST guard did not inspect enough localized literals"
    print(f"locale guard discovered {len(files)} files and scanned {len(scanned)} en/ja literals")
    for path in files:
        print(path.relative_to(_MODULE_ROOT).as_posix())
    assert scanned


def test_runtime_all_discovered_locale_maps_have_zero_korean():
    checked = 0
    for path in _discover_locale_files():
        module = importlib.import_module(_module_name(path))
        for name, value in vars(module).items():
            if name.startswith("__"):
                continue
            for locale, branch in _localized_mapping_values(value) or ():
                checked += 1
                assert_no_korean_leak(branch)
    assert checked >= 20


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_runtime_generated_instructions_and_errors_have_zero_korean(locale, monkeypatch):
    from modules.flow_gate import process_service, template_provision
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.services import (
        invoke_mention_service,
        mention_service,
        remote_tool_service,
        test_run_service,
        tr_scope_service,
    )

    outputs = [
        *(template_provision.build_type_default(code, locale) for code in ("D", "P", "L", "DB", "ZZ")),
        process_service._build_bug_template_body(locale),
        documents._auto_approved_title("Label", locale),
        documents._auto_approved_body("Label", locale),
        mention_service._ts_authoring_section(locale),
        mention_service._nt_authoring_section("N", locale),
        mention_service._nt_authoring_section("T", locale),
        invoke_mention_service.prepend_messages_section("base", ["message"], locale),
        invoke_mention_service.build_reject_context("Doc", "Reason", locale),
        invoke_mention_service.build_design_handoff_context(
            types=["D"], mode="batch", doc_ref="R0001", locale=locale
        ),
        tr_scope_service.tr_section_guide(locale),
        tr_scope_service.tr_section_placeholder(locale),
        remote_tool_service._continuation({"report_doc_id": "TR0001"}, locale),
        remote_tool_service._continuation({}, locale),
    ]

    monkeypatch.setattr(invoke_mention_service, "_chat_lookup_sections", lambda **_kwargs: [])
    # 0362 T0012: the builder now sizes the recent-turn window from the head of the
    # conversation, and this check runs without a database. A head of 0 is the short
    # conversation that folds nothing — the plain mention this guard is here to read.
    monkeypatch.setattr(
        invoke_mention_service.conversation_turns, "current_head_seq", lambda doc_id: 0
    )
    outputs.append(invoke_mention_service.build_conversation_mention(
        doc_id="sample.none.0001.0001-CH",
        project="sample",
        module="none",
        group_name="sample.none.0001",
        raw_token="token",
        token_id="tok_20260731_000000",
        api_base_url="http://example.test/api/v1",
    ))

    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", lambda _token_id: None)
    dry_response = inbox_routes._maybe_dry_run(
        {"dry_run": True},
        {"token_id": "tok", "dry_run_count": 0, "continuation_locale": locale},
        {"doc_type": "TR"},
    )
    outputs.append(json.loads(dry_response.body))

    parser_failures = []
    for content in (
        "",
        "## Test Cases\n### TC-1: title\n- cmd: true\n",
        "## Setup\n- unknown: value\n## Test Cases\n### TC-1: title\n- cmd: true\n- expect: pass\n",
        "## Test Cases\n### TC-1: title\n- cmd: true\n- expect: pass\n## Teardown\n- start: service\n",
    ):
        with pytest.raises(test_run_service.TestCaseParseError) as exc_info:
            test_run_service.parse_test_plan(content)
        parser_failures.append(exc_info.value.detail)
    outputs.extend(parser_failures)

    assert_no_korean_leak(outputs)


def _description_rows(connection, locale: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT dt.type_code,
               COALESCE((SELECT dtn.type_name FROM document_type_names dtn
                         WHERE dtn.document_type_id=dt.id AND dtn.locale=?), dt.type_code) AS type_name,
               dt.series,
               dtd.description,
               dt.is_active
        FROM document_types dt
        JOIN document_type_descriptions dtd ON dtd.document_type_id=dt.id AND dtd.locale=?
        WHERE dt.project_id IS NULL
        ORDER BY dt.series, dt.sort_order, dt.type_code
        """,
        (locale, locale),
    ).fetchall()
    return [dict(row) for row in rows]


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_stored_document_type_descriptions_have_zero_korean(all_migrations_db, locale):
    rows = _description_rows(all_migrations_db, locale)
    expected = all_migrations_db.execute(
        "SELECT COUNT(*) FROM document_types WHERE project_id IS NULL"
    ).fetchone()[0]
    assert len(rows) == expected
    assert_no_korean_leak(rows)


@pytest.mark.parametrize("locale", ["en", "ja"])
def test_http_router_responses_recursively_have_zero_korean(
    all_migrations_db, locale, monkeypatch
):
    from modules.flow_gate.api.v1 import ai_invoke_routes, help_routes, qa_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents import document_service
    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.settings.routers import project_settings

    user = {"user_id": "u1", "issued_to": "u1", "is_admin": 1, "_is_user_jwt": True}
    responses = []

    help_app = FastAPI()
    help_app.include_router(help_routes.router)
    rows = _description_rows(all_migrations_db, locale)
    monkeypatch.setattr(help_routes, "verify_bearer", lambda _request: {"continuation_locale": locale})
    monkeypatch.setattr(help_routes.db_templates, "list_document_types", lambda **_kwargs: rows)
    with TestClient(help_app) as client:
        responses.append(client.get("/api/v1/help/doc_type").json())
        responses.append(client.get("/api/v1/help/question").json())

    docs_app = FastAPI()
    docs_app.include_router(documents.router)
    docs_app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(document_service, "get_document", lambda _doc_id: None)
    with TestClient(docs_app) as client:
        response = client.get("/documents/missing", headers={"x-locale": locale})
        assert response.status_code == 404
        responses.append(response.json())

    qa_app = FastAPI()
    qa_app.include_router(qa_routes.router)
    qa_app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(process_service := importlib.import_module("modules.flow_gate.process_service"),
                        "get_answer_form_data", lambda _doc_pk: None)
    with TestClient(qa_app) as client:
        response = client.get("/api/v1/qa/999/form", headers={"x-locale": locale})
        assert response.status_code == 404
        responses.append(response.json())

    ai_app = FastAPI()
    ai_app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(ai_invoke_routes, "verify_bearer", lambda _request: user)
    with TestClient(ai_app) as client:
        response = client.post(
            "/api/v1/ai-invoke/start",
            headers={"x-locale": locale},
            json={"project": "sample", "group": "0001", "mode": "invalid"},
        )
        assert response.status_code == 422
        responses.append(response.json())

    settings_app = FastAPI()
    settings_app.include_router(project_settings.router, prefix="/api/v1")
    settings_app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(
        project_settings._tp,
        "resolve_active_template",
        lambda _project, code, requested: {
            "content": project_settings._tp.build_type_default(code, requested),
            "resolution": "type-default",
            "scope": None,
            "resolved_locale": requested,
            "is_active": None,
            "bytes": 1,
        },
    )
    with TestClient(settings_app) as client:
        response = client.get(
            "/api/v1/projects/sample/templates/active/D",
            headers={"x-locale": locale},
        )
        assert response.status_code == 200
        responses.append(response.json())

    assert_no_korean_leak(responses)


def test_guard_rejects_one_korean_syllable():
    with pytest.raises(AssertionError, match="Korean syllable leak"):
        assert_no_korean_leak({"message": "English text 끝"})