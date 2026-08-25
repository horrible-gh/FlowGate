"""Group 0460 T0004 task 2.5 — the generated 'Instruction to include next
document header' block must be fenced so downstream Markdown viewers (and
anything else that renders this mention as Markdown) preserve its 7 lines
instead of collapsing them into one run-together paragraph (the group 0458
display bug R0001 reported; NR0003 traced the direct cause to the renderer,
not the stored text — this fences the *source* of that text too, so the
same soft-line-break collapse cannot recur wherever else this block ends up
being rendered as Markdown).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import mention_service  # noqa: E402

_HEADER = "## Instruction to include next document header"


def _build(**over) -> str:
    params = {
        "project": "flowgate",
        "module": "default",
        "group": "0460",
        "parent_type": "R",
        "parent_doc_number": "R0001",
        "parent_title": "t",
        "parent_doc_id": "flowgate.default.0460.0001-R",
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


def test_new_mention_fences_the_seven_field_block():
    section = _section(_build())
    assert "```text\n" in section
    assert section.rstrip().endswith("```")
    # The 7 fields themselves are still LF-separated inside the fence, unchanged.
    inner = section.split("```text\n", 1)[1].rsplit("```", 1)[0]
    lines = inner.strip("\n").split("\n")
    keys = [ln.split(":", 1)[0] for ln in lines]
    assert keys == [
        "next_type", "next_type_detail", "project", "module", "group",
        "title", "target_id",
    ]
    assert lines[2] == "project: flowgate"
    assert lines[4] == "group: 0460"
    assert lines[6] == "target_id: flowgate.default.0460.0001-R"


def test_edit_mention_is_unaffected(monkeypatch):
    """The edit-mode branch uses a different section entirely ('Revision
    instructions' / 'Revise the existing document...' — no bare multi-key
    block) and must stay untouched."""
    text = _build(action_scope="edit", parent_revision_no=1)
    assert _HEADER not in text
    assert "## Revision instructions" in text
    revision_section = text[text.index("## Revision instructions"):]
    revision_section = revision_section[:revision_section.find("\n\n## ")]
    assert "```text" not in revision_section
    assert "Revise the existing document" in revision_section


def test_review_continuous_mention_is_unaffected():
    """The review-phase branch (a different section header entirely — 'Step
    under review') is explicitly out of scope for task 2.5 and must not gain
    a fence it never had."""
    text = _build(continuous=True, continuous_review_mode=True)
    assert _HEADER not in text
    assert "## Step under review (do NOT create it yet)" in text
    review_section = text[text.index("## Step under review"):]
    review_section = review_section[:review_section.find("\n\n## ")]
    assert "```text" not in review_section
