"""0127 T#5: structured TS command identity (L#2 §2.15.2) and its build/judge contract (§2.15.1).

Pure checks: the request fingerprint, the strict request whitelist, the build over the fresh body
and the idempotency decision where raw and structured requests never share a fingerprint.
"""
import pytest

from flowgate.card_document_edit import idempotency, structured_ts_adapter as adapter
from flowgate.card_document_edit.contracts import (Actor, Body, Origin, ReviseRequest, STRUCTURED_FP_ALG,
                                                   FINGERPRINT_ALG, EVENT_SCHEMA)
from flowgate.card_document_edit.errors import GraphApiError
from flowgate.card_document_edit.structured_identity import (INPUT_TS_BRIEF, INPUT_TS_SPEC, StructuredCommand,
                                                             binding_fields, canonical_json)
from modules.flow_gate.services import test_spec_service as spec

CASE = {"case_id": "TC-001", "title": "login", "category": "normal", "requirement": "R-1",
        "execution_mode": "manual", "required": True, "precondition": "-", "input": "id/pw",
        "procedure": "sign in", "expected": "dashboard", "check_points": "title"}
DOC = {"doc_id": "flowgate.v02.0127.0030-TS", "project_id": "flowgate", "group_id": "flowgate.v02.0127",
       "type_code": "TS", "title": "TS title", "revision_no": 3}


def _ts_text(cases=(CASE,)):
    return spec.render_spec_document(list(cases), title="TS title", frontmatter_block="", locale="ko")


def _err(call):
    with pytest.raises(GraphApiError) as raised:
        call()
    return raised.value


def test_canonical_json_is_order_free_and_keeps_unicode_and_newlines():
    a = canonical_json({"b": [1, 2], "a": "한글\n끝"})
    b = canonical_json({"a": "한글\n끝", "b": [1, 2]})
    assert a == b == '{"a":"한글\\n끝","b":[1,2]}'.replace("\\n", "\\n")
    assert "한글" in a and " " not in a.replace("한글", "")


def test_fingerprint_depends_on_kind_cases_order_and_presence_not_on_rendering():
    one = StructuredCommand(INPUT_TS_SPEC, {"cases": [CASE, {**CASE, "case_id": "TC-002"}]})
    swapped = StructuredCommand(INPUT_TS_SPEC, {"cases": [{**CASE, "case_id": "TC-002"}, CASE]})
    same = StructuredCommand(INPUT_TS_SPEC, {"cases": [dict(reversed(list(CASE.items()))), {**CASE, "case_id": "TC-002"}]})
    assert one.fingerprint == same.fingerprint            # key order is not meaning
    assert one.fingerprint != swapped.fingerprint         # array order is
    assert len(one.fingerprint) == 64
    assert StructuredCommand(INPUT_TS_SPEC, {"cases": []}).fingerprint != StructuredCommand(INPUT_TS_BRIEF, {"text": ""}).fingerprint
    null_title = StructuredCommand(INPUT_TS_SPEC, {"cases": [], "title": None})
    absent_title = StructuredCommand(INPUT_TS_SPEC, {"cases": []})
    assert null_title.fingerprint != absent_title.fingerprint  # null and absent differ


def test_lone_surrogate_cannot_be_fingerprinted():
    with pytest.raises(UnicodeError):
        StructuredCommand(INPUT_TS_BRIEF, {"text": "\ud800"}).fingerprint


def test_request_whitelist_rejects_raw_content_actor_origin_and_force():
    for field in ("content", "expected_content", "actor", "origin", "force"):
        error = _err(lambda f=field: adapter.parse_structured_body(INPUT_TS_SPEC, {"cases": [], f: "x"}))
        assert error.status == 422 and error.code == "invalid_request"
    assert adapter.parse_structured_body(INPUT_TS_BRIEF, {"text": "t", "expected_revision": 1})


def test_command_shape_and_brief_limit():
    assert _err(lambda: adapter.command_of(INPUT_TS_SPEC, {})).code == "invalid_request"
    assert _err(lambda: adapter.command_of(INPUT_TS_SPEC, {"cases": "x"})).code == "invalid_request"
    assert _err(lambda: adapter.command_of(INPUT_TS_SPEC, {"cases": [1]})).code == "invalid_request"
    assert _err(lambda: adapter.command_of(INPUT_TS_SPEC, {"cases": [], "title": 3})).code == "invalid_request"
    assert _err(lambda: adapter.command_of(INPUT_TS_BRIEF, {"text": 3})).code == "invalid_request"
    too_long = _err(lambda: adapter.command_of(INPUT_TS_BRIEF, {"text": "x" * 20001}))
    assert too_long.code == "invalid_content"
    assert adapter.command_of(INPUT_TS_BRIEF, {"text": "x" * 20000}).payload == {"text": "x" * 20000}
    command = adapter.command_of(INPUT_TS_SPEC, {"cases": [CASE], "intro": None})
    assert command.payload == {"cases": [CASE], "intro": None}  # present-null survives


def test_brief_build_replaces_only_the_intro_and_keeps_cases():
    current = _ts_text()
    built = adapter.build_text(StructuredCommand(INPUT_TS_BRIEF, {"text": "new brief"}), DOC, Body("readable", text=current))
    assert "new brief" in built
    assert spec.parse_spec(built)["cases"][0]["case_id"] == "TC-001"


def test_spec_build_renders_server_side_and_validates_with_the_existing_rules():
    current = _ts_text()
    changed = {**CASE, "title": "login v2"}
    built = adapter.build_text(StructuredCommand(INPUT_TS_SPEC, {"cases": [changed]}), DOC, Body("readable", text=current))
    assert spec.parse_spec(built)["cases"][0]["title"] == "login v2"
    bad = _err(lambda: adapter.build_text(StructuredCommand(INPUT_TS_SPEC, {"cases": [{"case_id": "bad id"}]}), DOC,
                                          Body("readable", text=current)))
    assert bad.status == 422 and bad.code == "invalid_content"
    assert bad.params["errors"][0]["field"] == "content" and bad.params["errors"][0]["reason"] == "invalid_format"
    assert bad.params["errors"][0]["validation"]["error"] == "invalid_spec"
    not_ts = _err(lambda: adapter.build_text(StructuredCommand(INPUT_TS_BRIEF, {"text": "x"}), {**DOC, "type_code": "N"},
                                             Body("readable", text=current)))
    assert not_ts.code == "invalid_content"


def _req(structured=None, content="", key="k-1", revision=3):
    return ReviseRequest("flowgate.v02.0127", "ts", "instruction", DOC["doc_id"], revision, content,
                         Actor("u1"), Origin(), idempotency_key=key, structured=structured)


def _event(binding, revision_no=4):
    return {"actor_user_id": "u1", "parsed": {**{k: v for k, v in binding.items() if k not in {"project_id", "actor_user_id"}},
            "schema": EVENT_SCHEMA, "revision_no": revision_no, "updated_at": "2026-11-04T00:00:00"}}


def test_binding_uses_command_identity_and_records_kind_and_algorithm():
    command = StructuredCommand(INPUT_TS_SPEC, {"cases": [CASE]})
    binding = idempotency.make_binding(_req(command), DOC)
    assert binding["content_sha256"] == command.fingerprint == binding["request_fingerprint"]
    assert binding["fp_alg"] == binding["fingerprint_algorithm"] == STRUCTURED_FP_ALG != FINGERPRINT_ALG
    assert binding["input_kind"] == INPUT_TS_SPEC
    raw = idempotency.make_binding(_req(content="body"), DOC)
    assert "input_kind" not in raw and raw["fp_alg"] == FINGERPRINT_ALG
    assert binding_fields(command)["input_kind"] == INPUT_TS_SPEC


def test_same_command_replays_without_a_second_build_and_other_commands_reuse_the_key():
    command = StructuredCommand(INPUT_TS_SPEC, {"cases": [CASE]})
    saved = idempotency.make_binding(_req(command), DOC)
    current = {**DOC, "revision_no": 4}
    again = idempotency.make_binding(_req(command), DOC)
    assert idempotency.judge([_event(saved)], again, current).kind == idempotency.REPLAY
    other = idempotency.make_binding(_req(StructuredCommand(INPUT_TS_SPEC, {"cases": [{**CASE, "title": "x"}]})), DOC)
    assert idempotency.judge([_event(saved)], other, current).kind == idempotency.REUSED
    brief = idempotency.make_binding(_req(StructuredCommand(INPUT_TS_BRIEF, {"text": "t"})), DOC)
    assert idempotency.judge([_event(saved)], brief, current).kind == idempotency.REUSED
    raw = idempotency.make_binding(_req(content="x"), DOC)
    assert idempotency.judge([_event(saved)], raw, current).kind == idempotency.REUSED  # raw and structured never share a key
    moved = {**DOC, "revision_no": 5}
    assert idempotency.judge([_event(saved)], again, moved).kind == idempotency.STALE


def test_raw_record_never_replays_for_a_structured_request():
    raw = idempotency.make_binding(_req(content="x"), DOC)
    structured = idempotency.make_binding(_req(StructuredCommand(INPUT_TS_BRIEF, {"text": "t"})), DOC)
    assert idempotency.judge([_event(raw)], structured, {**DOC, "revision_no": 4}).kind == idempotency.REUSED
