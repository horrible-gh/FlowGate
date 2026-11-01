"""flowgate.v02.0117 — a Graph card's instruction stage writes into its line's EMPTY document.

Its start is an edit (``node_dispatch._instruction_job``: ``action_scope`` edit on the line's document), but the
document has no body: it is a first write. The edit mention used to tell the worker to revise it after reading
"the review feedback below" (there is none) while the T/N authoring guide told it to copy the header from an
'Instruction to include next document header' section that only a new-document mention carries. Workers either
guessed the header or stopped and registered a question. Now:

- ``first_write`` turns Section 2 into 'Writing instructions' (no revision, no review feedback, keep the
  document's own header); every other mention is byte-identical;
- the graph AI start passes ``first_write`` for the instruction stage's edit only;
- the N/T authoring guide footer names both cases (new: copy the header section; edit: keep the existing header).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import mention_service  # noqa: E402

_NEXT_HEADER = "## Instruction to include next document header"


def _build(**over) -> str:
    params = {
        "project": "flowgate", "module": "v02", "group": "0117",
        "parent_type": "T", "parent_doc_number": "T0002", "parent_title": "t",
        "parent_doc_id": "T0002", "parent_canonical_doc_id": "flowgate.v02.0117.0002-T",
        "head_type": "T", "head_status": "pending",
        "scratch_dir": "S", "raw_token": "RAW", "api_base_url": "http://h/flowgate/api/v1",
    }
    params.update(over)
    return mention_service.build_mention(**params)


def _section(text: str, header: str) -> str:
    start = text.index(header)
    end = text.find("\n\n## ", start)
    return text[start:end if end != -1 else len(text)]


def test_first_write_edit_says_it_is_a_first_write_with_no_review_feedback():
    text = _build(action_scope="edit", first_write=True)
    assert "## Revision instructions" not in text
    assert _NEXT_HEADER not in text
    section = _section(text, "## Writing instructions")
    assert "flowgate.v02.0117.0002-T" in section
    assert "first write" in section and "no review feedback" in section
    assert "review feedback below" not in section
    assert "existing header (front matter)" in section
    # still an in-place edit of that document
    assert '"action": "edit"' in text and '"doc_id": "flowgate.v02.0117.0002-T"' in text


def test_a_normal_edit_is_unchanged():
    text = _build(action_scope="edit", parent_revision_no=1)
    assert _build(action_scope="edit", parent_revision_no=1, first_write=False) == text
    section = _section(text, "## Revision instructions")
    assert "Read the target document and the review feedback below before editing." in section
    assert "## Writing instructions" not in text


def test_first_write_is_ignored_by_a_new_mention():
    assert _build(first_write=True) == _build()
    assert _NEXT_HEADER in _build(first_write=True)


def test_token_rec_builder_forwards_first_write():
    parent = {"doc_id": "flowgate.v02.0117.0002-T", "type_code": "T", "seq": 2, "title": "t", "revision_no": 0}
    rec = {"project": "flowgate", "group_id": "flowgate.v02.0117", "scratch_dir": "S"}
    kw = dict(token_rec=rec, head_type="T", head_status="pending", parent_doc=parent,
              api_base_url="http://h/flowgate/api/v1", raw_token="RAW", action_scope="edit")
    assert "## Writing instructions" in mention_service.build_mention_from_token_rec(**kw, first_write=True)
    assert "## Revision instructions" in mention_service.build_mention_from_token_rec(**kw)


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
@pytest.mark.parametrize("type_code", ["N", "T"])
def test_authoring_footer_covers_the_edit_case(locale, type_code):
    guide = mention_service._nt_authoring_section(type_code, locale)
    assert "Instruction to include next document header" in guide
    assert "front matter" in guide and "(edit)" in guide


def _start(monkeypatch, job):
    """``ai_node_starter.start_ai_invoke`` with its collaborators replaced; returns the mention builder's kwargs."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter

    seen: list = []
    monkeypatch.setattr(ai_node_starter.db_documents, "get_by_id", lambda doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(ai_node_starter.token_service, "issue",
                        lambda **_k: {"token_id": "tok", "raw_token": "RAW", "scratch_dir": "S"})
    monkeypatch.setattr(ai_node_starter, "_map_if_open", lambda *_a, **_k: False)
    monkeypatch.setattr(ai_node_starter.mention_service, "build_mention_from_token_rec",
                        lambda **kw: seen.append(kw) or "work")
    monkeypatch.setattr(ai_node_starter.ai_start_precheck, "node_mention", lambda mention, *_a, **_k: mention)
    monkeypatch.setattr(ai_node_starter.card_memo_hooks, "prepend_memo_section", lambda mention, *_a, **_k: mention)
    monkeypatch.setattr(ai_node_starter, "api_base_url", lambda: "http://h/flowgate/api/v1")
    monkeypatch.setattr(ai_node_starter.ai_invoke_service, "start_run",
                        lambda **kw: kw["issue_builder"](ai_run_id="air") and {"run_id": "air"})
    ai_node_starter.start_ai_invoke(job)
    [kwargs] = seen
    return kwargs


_JOB = {"flow_id": "flowgate.v02.0117", "run_id": "run", "node_key": "n1", "issued_to": "u", "doc_type": "T",
        "doc_ref": "flowgate.v02.0117.0001-R", "project_id": "flowgate"}


def test_the_instruction_stage_edit_start_is_a_first_write(monkeypatch):
    kwargs = _start(monkeypatch, {**_JOB, "stage": "instruction", "action_scope": "edit",
                                  "target_doc_id": "flowgate.v02.0117.0002-T", "result_doc_type": "T"})
    assert kwargs["action_scope"] == "edit" and kwargs["first_write"] is True


@pytest.mark.parametrize("stage, scope", [("instruction", "new"), ("result", "new")])
def test_other_starts_are_not_first_writes(monkeypatch, stage, scope):
    kwargs = _start(monkeypatch, {**_JOB, "stage": stage, "action_scope": scope, "result_doc_type": "TR"})
    assert "first_write" not in kwargs
