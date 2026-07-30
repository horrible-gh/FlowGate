"""Remote source CRUD guidance in copied worker mentions (group 0136)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import mention_service  # noqa: E402


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


def _json_block(text: str, heading: str) -> dict:
    """Extract and parse the JSON object that follows a `POST ... {endpoint}` heading line."""
    idx = text.index(heading)
    start = text.index("{", idx)
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise AssertionError(f"no closing brace for {heading!r} JSON block")


def test_task_report_mention_includes_remote_source_crud_examples():
    text = _mention()

    assert "## Remote project source CRUD" in text
    assert "POST http://localhost:8089/flowgate/api/v1/remote/read" in text
    assert "POST http://localhost:8089/flowgate/api/v1/remote/write" in text
    assert "POST http://localhost:8089/flowgate/api/v1/remote/remove" in text
    assert "Authorization: Bearer raw-token-0136" in text


def test_investigation_report_mention_keeps_remote_source_read_only():
    text = _mention(parent_type="N", parent_doc_number="N0002", head_type="NR")

    assert "## Remote project source CRUD" in text
    assert "POST http://localhost:8089/flowgate/api/v1/remote/read" in text
    assert "POST http://localhost:8089/flowgate/api/v1/remote/grep" in text
    assert "POST http://localhost:8089/flowgate/api/v1/remote/write" not in text
    assert "POST http://localhost:8089/flowgate/api/v1/remote/remove" not in text
    # Default locale is ko (B0001 rev2 follow-up: this section now follows the
    # worker's requested locale like every other section in the mention).
    assert "읽기/검색만" in text


def test_investigation_report_mention_read_only_note_follows_locale():
    text = _mention(parent_type="N", parent_doc_number="N0002", head_type="NR", locale="en")
    assert "read/search only" in text

    text_ja = _mention(parent_type="N", parent_doc_number="N0002", head_type="NR", locale="ja")
    assert "読み取り/検索のみ" in text_ja


def test_task_report_mention_crud_prose_follows_locale():
    ko = _mention(locale="ko")
    assert "원격 프로젝트의 소스 트리를" in ko
    assert "작업 리포트에 요약" in ko

    en = _mention(locale="en")
    assert "Use these endpoints" in en
    assert "summarize the changed source files" in en

    ja = _mention(locale="ja")
    assert "リモートプロジェクトのソースツリー" in ja
    assert "作業レポートに要約" in ja


def test_remote_source_crud_examples_omit_path_field():
    """grep/glob path is optional-when-omitted (project root default); a copied example
    with `path: ""` fails the API's own safety check (B0001 / NR0003), so the examples
    must omit the key rather than send an empty string."""
    text = _mention()

    grep_body = _json_block(text, "POST http://localhost:8089/flowgate/api/v1/remote/grep")
    glob_body = _json_block(text, "POST http://localhost:8089/flowgate/api/v1/remote/glob")

    assert "path" not in grep_body
    assert "path" not in glob_body
    assert grep_body["pattern"] == "TODO"
    assert glob_body["pattern"] == "**/*.py"
