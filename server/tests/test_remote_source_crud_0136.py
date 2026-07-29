"""Remote source CRUD guidance in copied worker mentions (group 0136).

0349 TR-2 (D0004 D-4/D-8 2단계) moved the request formats and JSON examples out of the
mention and behind GET /help/tools. What the mention still owes the worker is unchanged in
kind — which tools this step may use, and the token to use them with — so these tests keep
asserting that, against the shrunk text. The example-shape guarantee this file has carried
since B0001 moved with the examples, and is asserted at its new home below.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services import tool_registry  # noqa: E402


def _mention(**overrides) -> str:
    params = {
        "project": "flowgate",
        "module": "default",
        "group": "0136",
        "parent_type": "T",
        "parent_doc_number": "T0004",
        "parent_title": "작업지시 승인",
        "parent_doc_id": "R0001",
        "head_type": "TR",
        "head_status": "pending",
        "scratch_dir": "",
        "raw_token": "raw-token-0136",
        "api_base_url": "http://localhost:8089/flowgate/api/v1",
    }
    params.update(overrides)
    return mention_service.build_mention(**params)


def _tool_section(text: str) -> str:
    start = text.index("## Remote project source CRUD")
    end = text.find("\n\n## ", start)
    return text[start:end if end != -1 else len(text)]


def test_task_report_mention_names_the_full_tool_set():
    section = _tool_section(_mention())

    assert "도구: read, grep, glob, write, remove" in section
    assert "Authorization: Bearer raw-token-0136" in section
    # The one thing the worker must know before it can look anything up.
    assert "GET http://localhost:8089/flowgate/api/v1/help/tools" in section


def test_investigation_report_mention_keeps_remote_source_read_only():
    section = _tool_section(_mention(parent_type="N", parent_doc_number="N0002", head_type="NR"))

    assert "도구: read, grep, glob" in section
    assert "write" not in section
    assert "remove" not in section


def test_mention_no_longer_carries_request_formats():
    """The shrink itself (R0001): one block per tool is what made the mention unreadable.

    Endpoint paths, request bodies and field descriptions now come from /help/tools, so a
    new tool must not be able to grow this section again.
    """
    text = _mention()

    assert "/remote/read" not in text
    assert "/remote/write" not in text
    assert "max_bytes" not in text
    assert len(_tool_section(text).splitlines()) == 7  # header + '---' + 5 lines


def test_help_tool_examples_omit_path_field():
    """grep/glob path is optional-when-omitted (project root default); a copied example
    with `path: ""` fails the API's own safety check (B0001 / NR0003), so the examples
    must omit the key rather than send an empty string. The examples now ship in the tool
    registry (0349 TR-1) instead of the mention, so the guarantee is asserted there."""
    grep_body = tool_registry.EXAMPLE_BODIES["grep"]
    glob_body = tool_registry.EXAMPLE_BODIES["glob"]

    assert "path" not in grep_body
    assert "path" not in glob_body
    assert grep_body["pattern"] == "TODO"
    assert glob_body["pattern"] == "**/*.py"
