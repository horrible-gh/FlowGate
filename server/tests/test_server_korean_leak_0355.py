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


# ── Generalized local error-helper sink discovery (T0004 작업 9 / NR0003 권고 6) ──────
# The scan above (_localized_literals) only ever looks INSIDE recognized locale-branch
# shapes (an en/ja-keyed dict, or an if/elif on locale). It has no opinion about a file's
# OTHER functions, so a locally-defined error-response helper with no locale parameter at
# all (NR0003 발견 6: ai_invoke_service.py's _http_error) is invisible to it — nothing about
# that call is a "locale branch" to find. This second scanner starts the other end: find
# every function that structurally assembles a user-facing error response (whatever it is
# named), then check ALL of its call sites for a raw, unbranched Korean literal — one that
# reaches the sink without ever passing through a locale copy-dict or an if/elif on locale.


def _dict_literal_has_false_ok_key(node: ast.AST) -> bool:
    if not isinstance(node, ast.Dict):
        return False
    for key, value in zip(node.keys, node.values):
        if (
            isinstance(key, ast.Constant) and key.value == "ok"
            and isinstance(value, ast.Constant) and value.value is False
        ):
            return True
    return False


def _call_target_name(call: ast.Call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _constructs_error_response(func_node) -> bool:
    """Structural test, not a name test: does this function's body assemble an
    HTTPException, or a JSONResponse-style envelope carrying an "ok": False shape?
    Matches both inbox_routes._fail (JSONResponse) and ai_invoke_service._http_error
    (HTTPException) without depending on either spelling."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        name = _call_target_name(node)
        if name == "HTTPException":
            return True
        if name == "JSONResponse":
            for kw in node.keywords:
                if kw.arg in ("content", None) and _dict_literal_has_false_ok_key(kw.value):
                    return True
            for arg in node.args:
                if _dict_literal_has_false_ok_key(arg):
                    return True
    return False


def _local_error_helpers(tree) -> set[str]:
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    helpers = {name for name, node in functions.items() if _constructs_error_response(node)}
    # Fixed point: a thin wrapper that only forwards to an already-known helper is a
    # helper too (e.g. a per-router _reject(msg) that returns _fail(422, msg)).
    changed = True
    while changed:
        changed = False
        for name, node in functions.items():
            if name in helpers:
                continue
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and _call_target_name(call) in helpers:
                    helpers.add(name)
                    changed = True
                    break
    return helpers


def _build_parent_map(tree: ast.AST) -> dict:
    parents: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _inside_locale_branch(node: ast.AST, parents: dict, source: str) -> bool:
    """True if some ancestor If/IfExp test already conditions on locale — the same
    per-branch shape _localized_literals already recognizes (e.g. a plain
    'if locale == "ko": return _fail(422, "...")' branch). A literal reached that way
    is routed by locale even though it never passes through a dict."""
    current = node
    while current in parents:
        parent = parents[current]
        test = None
        if isinstance(parent, ast.If):
            test = parent.test
        elif isinstance(parent, ast.IfExp):
            test = parent.test
        if test is not None:
            test_source = ast.get_source_segment(source, test) or ""
            if re.search(r"\blocale\b|continuation_locale|x_locale", test_source, re.I):
                return True
        current = parent
    return False


def _unbranched_korean_call_args(tree, helper_names: set[str], source: str):
    """Every call to a discovered helper (or directly to HTTPException) whose argument
    subtree contains a raw Korean string literal that was not routed through a locale
    copy-dict (those never appear as an inline Constant at the call site — the Hangul
    lives in the dict definition, scanned separately by _localized_literals) and is not
    already inside an if/elif branch conditioned on locale."""
    sinks = helper_names | {"HTTPException"}
    parents = _build_parent_map(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_target_name(node) not in sinks:
            continue
        if _inside_locale_branch(node, parents, source):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            for literal in _constant_strings(arg):
                if _HANGUL.search(literal.value):
                    yield node.lineno, literal.value


def test_static_error_sinks_reject_unbranched_korean_regardless_of_helper_name():
    """0355 T0023's original scanner only ever recognized locale-branch SHAPES (an
    en/ja dict, or an if/elif on locale) — it never asked "does this file have its own
    error-response helper, and is every call site of THAT helper locale-safe?". NR0003
    발견 6 slipped through exactly that gap: ai_invoke_service._http_error has no locale
    parameter and no en/ja dict nearby, so nothing about it looked like a locale branch
    to find. This walks every discovered locale-aware file, finds its own local
    error-response helpers structurally (whatever they are named), and fails on any call
    site whose message argument is a raw, unbranched Korean literal."""
    offenders = []
    for path in _discover_locale_files():
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        helpers = _local_error_helpers(tree)
        for lineno, value in _unbranched_korean_call_args(tree, helpers, source):
            offenders.append(f"{path.relative_to(_MODULE_ROOT).as_posix()}:{lineno}: {value!r}")
    assert not offenders, (
        "Unbranched Korean literal(s) reaching a local error-response sink:\n"
        + "\n".join(offenders[:30])
    )


def test_local_error_helper_detection_generalizes_beyond_fail_and_http_error():
    """AST fixture (T0004 작업 9): a helper named neither _fail nor _http_error must
    still be found structurally, and an unbranched Korean literal reaching it must
    fail the scan — while the same wording routed through a locale map must pass."""
    bad_source = (
        "from fastapi import HTTPException\n\n\n"
        "def _reject_oddly_named(status_code, message, **extra):\n"
        '    return HTTPException(status_code=status_code, detail={"message": message, **extra})\n\n\n'
        "def handler():\n"
        '    raise _reject_oddly_named(409, "이 메시지는 로케일 분기 없이 그대로 나갑니다")\n'
    )
    good_source = (
        "from fastapi import HTTPException\n\n\n"
        '_COPY = {"ko": "이 메시지는 로케일 맵을 통해서만 나갑니다", "en": "routed"}\n\n\n'
        "def _reject_oddly_named(status_code, message, **extra):\n"
        '    return HTTPException(status_code=status_code, detail={"message": message, **extra})\n\n\n'
        "def handler(locale):\n"
        "    raise _reject_oddly_named(409, _COPY[locale])\n"
    )

    bad_tree = ast.parse(bad_source)
    helpers = _local_error_helpers(bad_tree)
    assert "_reject_oddly_named" in helpers
    bad_hits = list(_unbranched_korean_call_args(bad_tree, helpers, bad_source))
    assert bad_hits, "the fixture's unbranched Korean literal must be caught"

    good_tree = ast.parse(good_source)
    good_helpers = _local_error_helpers(good_tree)
    assert "_reject_oddly_named" in good_helpers
    good_hits = list(_unbranched_korean_call_args(good_tree, good_helpers, good_source))
    assert not good_hits, "a message routed through a locale map must not be flagged"


def test_local_error_helper_scan_does_not_flag_ko_branches_or_docstrings():
    """False-positive fixture (T0004 작업 9): the generalized scan must stay quiet on the
    two legitimate shapes NR0003 권고 6 calls out — a message chosen by an explicit ko
    branch, and Korean prose that never reaches the sink (a docstring or comment sitting
    in the same function). Only literals that actually flow into the error response
    unbranched may fail the scan."""
    source = (
        "from fastapi import HTTPException\n\n\n"
        "def _reject_oddly_named(status_code, message, **extra):\n"
        '    return HTTPException(status_code=status_code, detail={"message": message, **extra})\n\n\n'
        "def handler(locale):\n"
        '    """이 독스트링은 한국어지만 응답으로 직렬화되지 않는다."""\n'
        "    # 이 주석도 마찬가지로 sink 로 흐르지 않는다.\n"
        '    if locale == "ko":\n'
        '        raise _reject_oddly_named(409, "명시적 ko 분기로 선택된 문구입니다")\n'
        '    raise _reject_oddly_named(409, "routed elsewhere")\n'
    )

    tree = ast.parse(source)
    helpers = _local_error_helpers(tree)
    assert "_reject_oddly_named" in helpers
    hits = list(_unbranched_korean_call_args(tree, helpers, source))
    assert not hits, f"legitimate ko branch / non-sink prose was flagged: {hits}"


def test_ai_invoke_service_worktree_unavailable_is_localized():
    """Regression pin for NR0003 발견 6 (T0004 작업 6): ai_invoke_service._http_error's
    worktree_unavailable 409 used to be a fixed Korean f-string that the old narrowed
    0355 scanner never saw (it matched none of the four fixed sink spellings). The
    structural helper-detector must find _http_error on its own, and the call site's
    message argument in ai_invoke_service.py must carry zero raw Korean now that T0004
    routes it through _WORKTREE_UNAVAILABLE_COPY."""
    path = _MODULE_ROOT / "services" / "ai_invoke_service.py"
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    helpers = _local_error_helpers(tree)
    assert "_http_error" in helpers
    hits = list(_unbranched_korean_call_args(tree, helpers, source))
    assert not hits, f"worktree_unavailable regressed to an unbranched Korean literal: {hits}"