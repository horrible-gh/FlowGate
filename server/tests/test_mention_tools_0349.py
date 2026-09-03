"""Mention tool section after the 0349 shrink — TR-2 (D0004 D-3/D-4/D-5/D-6, P0005 [참고]).

TR-1 built the registry and /help/tools without touching a single mention. This is the
other half: every mention now asks the registry which tools to name, names only those, and
sends the worker to help for the rest.

The contract asserted here is the one that made the whole change worth doing — what the
mention advertises must equal what the server will allow. Both sides now call
tool_registry.kind_for_step / kind_for_token, and the last test pins them together so a
future change to one cannot silently diverge from the other.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service  # noqa: E402
from modules.flow_gate.services import remote_tool_service  # noqa: E402
from modules.flow_gate.services import tool_registry  # noqa: E402

_HEADER = "## Remote project source CRUD"
_TARGET_DOC = {"doc_id": "p.default.0349.0009-TR", "type_code": "TR", "seq": 9, "title": "t"}
_TOKEN_REC = {"project": "p", "group_id": "p.default.0349", "scratch_dir": "S"}


@pytest.fixture(autouse=True)
def _remote_mode(monkeypatch):
    """Source mode gates advertising only; pin it on so each test states its own subject."""
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: True)


def _build(**over) -> str:
    params = {
        "project": "p",
        "module": "default",
        "group": "0349",
        "parent_type": "T",
        "parent_doc_number": "T0008",
        "parent_title": "t",
        "parent_doc_id": "R0001",
        "head_type": "TR",
        "head_status": "pending",
        "scratch_dir": "S",
        "raw_token": "RAW",
        "api_base_url": "http://h/flowgate/api/v1",
    }
    params.update(over)
    return mention_service.build_mention(**params)


def _section(text: str) -> str:
    start = text.index(_HEADER)
    end = text.find("\n\n## ", start)
    return text[start:end if end != -1 else len(text)]


def _headers(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## ")]


# ── D-4: what stays inline ───────────────────────────────────────────────────

def test_section_is_five_lines_and_carries_the_fallback_facts():
    """A worker that never calls help still learns the four things it cannot guess."""
    body = _section(_build()).splitlines()[2:]

    assert len(body) == 5
    assert body[0] == "첫 행동으로 GET http://h/flowgate/api/v1/help/tools 를 호출해 각 도구의 사용법을 확인하세요."
    assert body[1].startswith("디스크의 프로젝트 소스를 직접 편집하지 마세요")
    # 0482 T0011: default head_type="TR" is a MUTATING_STEP_TYPES read_write kind, and
    # `resolve_base_dirty` joined DISPLAY_ORDER/WRITE_TOOLS as a tenth catalog tool — every
    # read_write mention's tool line grows by one, exactly like the /help catalog's count.
    assert body[2] == "도구: read, grep, glob, stat, diff, log, write, patch, remove, resolve_base_dirty"
    assert body[3] == "Authorization: Bearer RAW"
    assert body[4] == "도구별 상세: GET http://h/flowgate/api/v1/help/tools/{name}"


def test_no_disk_edit_line_sits_next_to_the_tool_names_not_inside_help():
    """D-4: the accident this line prevents is a worker that never opened help."""
    body = _section(_build()).splitlines()
    assert body.index("도구: read, grep, glob, stat, diff, log, write, patch, remove, resolve_base_dirty") - 1 == next(
        i for i, line in enumerate(body) if line.startswith("디스크의")
    )


@pytest.mark.parametrize("locale,tools_line", [
    ("ko", "도구: read, grep, glob, stat, diff, log, write, patch, remove"),
    ("ja", "ツール: read, grep, glob, stat, diff, log, write, patch, remove"),
    ("en", "Tools: read, grep, glob, stat, diff, log, write, patch, remove"),
    ("zh", "도구: read, grep, glob, stat, diff, log, write, patch, remove"),  # unsupported → ko, never a 400
])
def test_section_follows_the_worker_locale(locale, tools_line):
    assert tools_line in _section(_build(locale=locale))


# ── D-3 / D-1: advertised set per step and scope ─────────────────────────────

@pytest.mark.parametrize("head_type,expected", [
    # T (0427 T0004): write/patch/remove recalled -- investigation-only now.
    ("T", "read, grep, glob, stat"),
    ("TR", "read, grep, glob, stat, diff, log, write, patch, remove"),
    # TSR: the test-report step really does edit test code, and TR-1 already gave its
    # token write scope. TS (group 0390 R0001): the test-scenario step now writes too, so
    # the bug a scenario surfaces gets fixed instead of just the test being rewritten.
    ("TSR", "read, grep, glob, stat, diff, log, write, patch, remove"),
    ("NR", "read, grep, glob, stat"),
    ("D", "read, grep, glob, stat"),
    ("TS", "read, grep, glob, stat, diff, log, write, patch, remove"),
])
def test_tool_list_matches_the_step_type(head_type, expected):
    assert f"도구: {expected}" in _section(_build(head_type=head_type))


def test_edit_mention_judges_by_the_document_being_revised():
    text = _build(action_scope="edit", parent_type="NR", head_type="TR")
    assert "도구: read, grep, glob, stat" in _section(text)
    assert "write" not in _section(text)


def test_local_source_mode_drops_the_section_entirely(monkeypatch):
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: False)
    assert _HEADER not in _build()


def test_review_mention_now_advertises_the_read_tools():
    text = mention_service.build_review_mention(
        token_rec=_TOKEN_REC, target_doc=_TARGET_DOC,
        api_base_url="http://h/flowgate/api/v1", raw_token="RAW",
    )
    assert "도구: read, grep, glob, stat" in _section(text)
    assert "write" not in _section(text)


def test_workflow_decision_mention_now_advertises_the_read_tools():
    text = mention_service.build_workflow_decision_mention(
        token_rec=_TOKEN_REC,
        target_doc={"doc_id": "p.default.0349.0001-R", "type_code": "R", "seq": 1, "title": "t"},
        api_base_url="http://h/flowgate/api/v1", raw_token="RAW",
    )
    assert "도구: read, grep, glob, stat" in _section(text)


def test_sequence_edit_mention_still_advertises_nothing():
    """The sequence-edit token gets no scopes at all — the current mention was right."""
    text = mention_service.build_sequence_edit_mention(
        token_rec=_TOKEN_REC,
        target_doc={"doc_id": "p.default.0349.0001-R", "type_code": "R", "seq": 1, "title": "t"},
        api_base_url="http://h/flowgate/api/v1", raw_token="RAW",
        sequence_items=[{"type": "T", "label": "작업", "status": "pending"}],
    )
    assert _HEADER not in text
    assert "help/tools" not in text


def test_answer_mention_follows_the_registry_instead_of_a_fixed_kind(monkeypatch):
    """D-3: the answer token is edit-scoped, so its tools depend on the step it lands on."""
    monkeypatch.setattr(
        tool_registry, "kind_for_token", lambda token_rec: ("read_write", None)
    )
    text = q_answer_invoke_service.build_answer_mention(
        doc={"doc_id": "p.default.0349.0009-TR", "project_id": "p", "title": "t", "group_id": ""},
        item={"id": 1, "seq": 1, "title": "q", "body": "b"},
        raw_token="RAW", scratch_dir="S", api_base_url="http://h/flowgate/api/v1",
    )
    assert "[소스 도구]" in text
    assert "도구: read, grep, glob, stat, diff, log, write, patch, remove" in text
    assert text.index("[소스 도구]") < text.index("[질의]")


def test_answer_mention_degrades_to_no_block_when_judgement_fails(monkeypatch):
    def _boom(_token_rec):
        raise RuntimeError("registry down")

    monkeypatch.setattr(tool_registry, "kind_for_token", _boom)
    text = q_answer_invoke_service.build_answer_mention(
        doc={"doc_id": "d", "project_id": "p", "title": "t", "group_id": ""},
        item={"id": 1, "seq": 1, "title": "q", "body": "b"},
        raw_token="RAW", scratch_dir="S", api_base_url="http://h/flowgate/api/v1",
    )
    assert "[소스 도구]" not in text
    assert "[답변 등록 방법]" in text  # the hand-off itself survives


# ── D-5: position ────────────────────────────────────────────────────────────

def test_section_sits_below_identity_and_above_the_instruction_sections():
    # group 0372 set 3 (L-0005 §2-10): the central help block ("## 도움말") takes the
    # slot right below the identity + guide blocks, so the tool section now sits
    # fourth — still above every instruction section.
    headers = _headers(_build(continuous=True))
    assert headers[:4] == [
        "## Document information",
        "## Continuous work",
        "## 도움말",
        _HEADER,
    ]
    assert headers.index(_HEADER) < headers.index("## Instruction to include next document header")


def test_section_precedes_the_scope_guard_it_used_to_follow():
    headers = _headers(_build(head_type="NR"))
    assert headers.index(_HEADER) < headers.index("## Work scope")


# ── D-2: one judge for advertising and for permission ────────────────────────

@pytest.mark.parametrize("action_scope,step_type,expected_scopes", [
    ("edit", "TR", ["read", "write", "grep", "remove"]),
    ("edit", "TSR", ["read", "write", "grep", "remove"]),
    ("new", "NR", ["read", "grep"]),
    ("review", None, ["read", "grep"]),
    ("workflow_decide", None, ["read", "grep"]),
    ("workflow_sequence_edit", None, []),
    ("test_run", None, []),
    # 0392 B0001/NR0003 added "chat" to this parametrize list so the CH mention could
    # not default to "new" and advertise tools the token could not use. 0431 T0004
    # flips the settled chat=none policy to chat=read: the chat worker token now gets
    # real read/grep/glob/stat access, matching kind_for_step's read-only early return.
    ("chat", "CH", ["read", "grep"]),
    # 0478 T0004: resolve_conflict was falling through to the "none" branch of
    # kind_for_step (action_scope not in {new, edit}), leaving the conflict-resolution
    # worker with zero remote tools. It now shares the read-only early return with
    # review/workflow_decide/chat.
    ("resolve_conflict", None, ["read", "grep"]),
])
def test_advertised_tools_equal_granted_scopes(monkeypatch, action_scope, step_type, expected_scopes):
    """The point of the change: no step may be told about a tool the server refuses."""
    monkeypatch.setattr(
        remote_tool_service, "_worker_token_step_type_result", lambda rec: (step_type, False)
    )
    granted = remote_tool_service._scopes_for_worker_token(
        {"action_scope": action_scope, "doc_ref": "d"}
    )
    assert granted == expected_scopes

    kind, _reason = tool_registry.kind_for_step(action_scope, step_type)
    advertised = tool_registry.tool_names(kind)
    assert {remote_tool_service.OP_SCOPE[name] for name in advertised} == set(granted)


def test_failed_step_lookup_is_demoted_to_read_never_raised_to_write():
    kind, reason = tool_registry.kind_for_step("edit", None, lookup_failed=True)

    assert kind == "read"
    assert reason == "step_lookup_failed"
    assert tool_registry.tool_names(kind) == ["read", "grep", "glob", "stat", "diff", "log"]
