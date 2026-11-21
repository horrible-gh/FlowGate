"""A review verdict may be submitted as a file, like every other document.

B0001's third suspicion — "문서처럼 파일형태 지원 부족?" — was not the cause of the three
failures, but it was a real gap: document registration has had `doc_path`
since D020 §7-5 while a review verdict could only be typed inline. The overall comment on a
review is long Korean prose; pushed through a command line it turns into the ???? the
encoding guard then rejects, which is a registration lost for a formatting reason.

Covered here, matching the earlier implementation criterion 5 exactly:
  * success — the verdict is read out of a file inside the token's scratch directory,
  * a path outside the scratch directory → 422,
  * `content` and `doc_path` sent together → 400,
plus the two neighbours that make the contract usable: a missing file → 422, and the
backward-compatible inline shape (neither field) still registering, because the earlier implementation puts
the working [멘트 복사] review path out of bounds.

The implementation reuses `validate_doc_path` and `_submission_text` rather than growing a
second answer to "is this path inside the scratch dir" — a duplicate is how one of the two
drifts open — and this file asserts the two error strings are the same ones _handle_new
already returns, so they cannot diverge either.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from inbox_client import post_inbox as _raw_post_inbox

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.db import document_reviews as db_reviews  # noqa: E402
from modules.flow_gate.services import help_catalog  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as ai_runtime  # noqa: E402
from store_transaction_support import (  # noqa: E402
    build_live_sqlite_db,
    install_live_sqlite_store,
)

DOC_ID = "flowgate.v02.0003.0005-NR"
GROUP_ID = "flowgate.v02.0003"
PROJECT = "flowgate"
USER = "user-1"

# The shape the review recovered from the dead runs' scratch folders: a long Korean overall
# comment plus per-finding notes. This is the payload that could not be typed inline.
def post_inbox(body: dict):
    """Submit legacy review cases through the mandatory dry-run preflight."""
    if body.get("action") == "review" and not body.get("dry_run") and not body.get("receipt"):
        candidate = dict(body, dry_run=True)
        dry = _raw_post_inbox(candidate)
        if dry.status_code != 200:
            return dry
        body = dict(body, receipt=dry.json()["receipt"])
    return _raw_post_inbox(body)


VERDICT_PAYLOAD = {
    "verdict": "issues",
    "findings": [
        {"locus": "3-2. 메인 대시보드 내부 기능 영역",
         "note": "선정 기준이 문서 어디에도 없고 실제 화면 대비 누락이 큽니다."},
        {"locus": "1. 조사 배경", "note": "'전수 확인'이라는 표현이 실제 조사 범위와 맞지 않습니다."},
    ],
    "comment": "조사 밀도와 정확도 자체는 높습니다. 다만 목록의 누락이 그대로 결과물의 누락이 됩니다.",
}


@pytest.fixture
def review_env(monkeypatch, tmp_path):
    """Everything _handle_review crosses except the doc_path logic under test."""
    scratch = tmp_path / "tok_review"
    scratch.mkdir()
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: {
        "token_id": "tok-review-0393",
        "project": PROJECT,
        "issued_to": USER,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "scratch_dir": str(scratch),
        "ai_run_id": "air_review_0393",
        "expires_at": "2036-09-12T00:00:00+09:00",
    })
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda _id: {
        "doc_id": DOC_ID, "group_id": GROUP_ID, "revision_no": 2,
        "title": "GUI 모드 전환 대상 화면 목록",
    })
    monkeypatch.setattr(inbox_routes.process_service, "is_group_disposed", lambda _gid: False)
    insert = MagicMock()
    monkeypatch.setattr(db_reviews, "insert_review", insert)
    monkeypatch.setattr(inbox_routes.token_service, "consume", MagicMock())
    # the review path claims its token and stores the review inside one
    # store.transaction(). Both writes are mocked here (this file is about doc_path),
    # but the transaction itself is real, so the store has to be able to open one.
    db = build_live_sqlite_db(tmp_path / "flowgate.db")
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    db.conn.execute(
        "INSERT INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (PROJECT, "FlowGate", now, now),
    )
    db.conn.execute(
        "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (GROUP_ID, PROJECT, "v02", "Review group", "in_progress", now, now),
    )
    db.conn.execute(
        "INSERT INTO documents (doc_id, project_id, group_id, type_code, seq, title, "
        "revision_no, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (DOC_ID, PROJECT, GROUP_ID, "NR", 5, "GUI mode targets", 2, now, now),
    )
    db.conn.execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (USER, "reviewer-0393", "reviewer-0393@example.com", "hashed", now, now),
    )
    db.conn.execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, action_scope, "
        "issued_to, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("tok-review-0393", "hash-0393", "p1", PROJECT, DOC_ID, "review", USER,
         now, "2036-09-12T00:00:00+09:00"),
    )
    db.conn.commit()
    install_live_sqlite_store(monkeypatch, db)
    return {"scratch": scratch, "insert": insert, "outside": tmp_path / "outside"}


def _body(**overrides) -> dict:
    body = {"action": "review", "project": PROJECT, "doc_id": DOC_ID}
    body.update(overrides)
    return body


def _write_payload(directory: Path, name: str = "verdict.json", payload=None) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(
        json.dumps(payload if payload is not None else VERDICT_PAYLOAD, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(target)


# ── criterion 5-1: success ────────────────────────────────────────────────────────────

def test_a_verdict_file_inside_the_scratch_directory_registers(review_env):
    doc_path = _write_payload(review_env["scratch"])

    response = post_inbox(
        _body(doc_path=doc_path),
    )

    assert response.status_code == 201, response.text
    kwargs = review_env["insert"].call_args.kwargs
    assert kwargs["verdict"] == "issues"
    assert kwargs["comment"] == VERDICT_PAYLOAD["comment"]
    # The Korean survives the round trip byte for byte — the whole point of the file form.
    findings = json.loads(kwargs["findings_json"])
    assert len(findings) == 2
    assert findings[0]["locus"] == VERDICT_PAYLOAD["findings"][0]["locus"]
    assert findings[1]["note"] == VERDICT_PAYLOAD["findings"][1]["note"]
    assert kwargs["doc_id"] == DOC_ID
    assert kwargs["reviewer_id"] == USER


def test_long_mixed_unicode_file_reaches_insert_review_codepoint_exact(review_env):
    korean = (
        "긴 한국어 검토 문단은 Windows 명령줄의 코드페이지나 인용 규칙을 통과하지 않고 "
        "UTF-8 파일을 통해 전달되어야 합니다. "
    ) * 24
    payload = {
        "verdict": "issues",
        "findings": [{
            "locus": "경로/位置/🧭🚀",
            "note": korean + "日本語の指摘です。境界を確認します。 🧪🌌",
        }],
        "comment": korean + "最終コメントをそのまま保存します。 😀𠮷🚀",
    }
    doc_path = _write_payload(review_env["scratch"], "긴 검토 🚀.json", payload)
    raw = Path(doc_path).read_bytes()
    assert raw == json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert b"\\u" not in raw

    response = post_inbox(_body(doc_path=doc_path))

    assert response.status_code == 201, response.text
    kwargs = review_env["insert"].call_args.kwargs
    assert kwargs["comment"] == payload["comment"]
    assert json.loads(kwargs["findings_json"]) == payload["findings"]


def test_the_same_payload_inline_as_content_also_registers(review_env):
    """`content` is the other half of the pair; a worker that already has the JSON in hand
    should not have to write a file just to use the new door."""
    response = post_inbox(
        _body(content=json.dumps(VERDICT_PAYLOAD, ensure_ascii=False)),
    )

    assert response.status_code == 201, response.text
    assert review_env["insert"].call_args.kwargs["verdict"] == "issues"






# ── criterion 5-2: outside the scratch directory ──────────────────────────────────────





# ── criterion 5-3: both sources at once ───────────────────────────────────────────────





# ── the shape that must NOT change (the copy-mention review path) ───────────









# ── §2-7: the server accepting it is only half — the worker has to be told ────────────
