"""Remote source CRUD guidance in copied worker mentions (group 0136)."""
from __future__ import annotations

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
    assert "read/search only" in text
