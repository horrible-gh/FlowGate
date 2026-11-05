"""L#2 §2.15.1/§2.15.2: structured TS commands (cases/brief) parsed, validated and built.

Request shape checks are DB-free; build runs inside the revise boundary on the fresh body.
Rules are reused from test_spec_service; no second grammar and no client-rendered body.
"""
from .errors import GraphApiError, invalid
from .structured_identity import INPUT_TS_BRIEF, INPUT_TS_SPEC, StructuredCommand

TS_BRIEF_MAX_CHARS = 20_000
SPEC_FIELDS = frozenset({"expected_doc_id", "expected_revision", "idempotency_key",
                         "cases", "title", "intro", "locale"})
BRIEF_FIELDS = frozenset({"expected_doc_id", "expected_revision", "idempotency_key", "text"})
_SHARED = ("expected_doc_id", "expected_revision", "idempotency_key")


def parse_structured_body(kind, body):
    """Strict whitelist: raw content/actor/origin/force etc. are unknown_field."""
    if not isinstance(body, dict):
        raise invalid("invalid_request", "body", "invalid_format")
    allowed = SPEC_FIELDS if kind == INPUT_TS_SPEC else BRIEF_FIELDS
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise GraphApiError(422, "invalid_request", {"errors": [
            {"field": k, "reason": "unknown_field"} for k in unknown]})
    return body


def command_of(kind, body):
    """Validated StructuredCommand holding only the fields present in the request."""
    if kind == INPUT_TS_SPEC:
        cases = body.get("cases")
        if "cases" not in body or cases is None:
            raise invalid("invalid_request", "cases", "required")
        if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
            raise invalid("invalid_request", "cases", "invalid_type")
        payload = {"cases": cases}
        for name in ("title", "intro", "locale"):
            if name in body:
                if body[name] is not None and not isinstance(body[name], str):
                    raise invalid("invalid_request", name, "invalid_type")
                payload[name] = body[name]
        return StructuredCommand(kind, payload)
    text = body.get("text")
    if text is None:
        raise invalid("invalid_request", "text", "required")
    if not isinstance(text, str):
        raise invalid("invalid_request", "text", "invalid_type")
    if len(text) > TS_BRIEF_MAX_CHARS:
        raise invalid("invalid_content", "text", "invalid_format",
                      validation={"reason": "too_long", "max": TS_BRIEF_MAX_CHARS})
    return StructuredCommand(kind, {"text": text})


def _fail(current, validation):
    raise invalid("invalid_content", "content", "invalid_format", current, validation=validation)


def build_text(command, current, body):
    """Next full body text for a TS document; raises P#1 invalid_content on any rule failure."""
    from modules.flow_gate.services import test_run_service as runs, test_spec_service as spec
    if str(current.get("type_code") or "").upper() != "TS":
        _fail(current, {"error": "not_ts"})
    text = body.text
    if command.input_kind == INPUT_TS_BRIEF:
        return spec.replace_brief(text, command.payload["text"])
    if spec.detect_contract_version(text) == spec.CONTRACT_LEGACY:
        try:
            runs.parse_test_cases(text)
        except runs.TestCaseParseError:
            pass
        else:
            _fail(current, {"error": "legacy_executable_ts"})
    cases, errors = spec.validate_cases_payload(command.payload["cases"])
    if not errors:
        errors = spec.unrunnable_case_errors(cases, spec.parse_spec(text)["cases"])
    if errors:
        _fail(current, {"error": "invalid_spec", "errors": errors,
                        "message": "; ".join(e["message"] for e in errors[:10])})
    locale = command.payload.get("locale")
    if locale not in ("ko", "en", "ja"):
        locale = "ko"
        if command.payload.get("locale") is None:
            headings = dict(zip(spec.SPEC_SECTION_NAMES, ("ko", "en", "ja")))
            _f, _b, rest = spec.split_frontmatter(text)
            for line in rest.splitlines():
                if line.strip().startswith("## ") and line.strip()[3:] in headings:
                    locale = headings[line.strip()[3:]]
                    break
    title = (command.payload.get("title") or current.get("title") or current["doc_id"]).strip()
    out = spec.replace_spec_cases(text, cases, title=title, locale=locale)
    if command.payload.get("intro") is not None:
        out = spec.replace_brief(out, command.payload["intro"])
    return out


def projection(kind, snapshot_body):
    """Server-side projection of the same snapshot (§2.15.3); null when unreadable."""
    from modules.flow_gate.services import test_spec_service as spec
    if snapshot_body.get("content") is None:
        return None
    content = snapshot_body["content"]
    if kind == INPUT_TS_BRIEF:
        return {"text": spec.brief_parts(content)[1]}
    parsed = spec.parse_spec(content)
    return {"contract_version": parsed["contract_version"], "title": parsed["title"],
            "cases": parsed["cases"], "errors": parsed["errors"], "intro": spec.spec_intro(content)}
