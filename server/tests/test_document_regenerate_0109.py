"""0109 TR0005 — regenerate the .md file for a document whose stored file is missing.

R0001: "파일을 찾을 수 없는 문서에 대해 파일을 재 생성하는 기능". NR0003 designed
POST /api/v1/documents/{doc_id}/regenerate as a *recovery* op (not a content edit):

  - missing file + no backup  → frontmatter stub from DB metadata (body_lost=True)
  - missing file + backup     → restore the last-saved body (restored_from="revision")
  - file already present       → 409 (never clobber good data, NR0003 §7-3)
  - unknown doc_id            → 404
  - recovery ignores the edit/final-approval gate (NR0003 §5) — closed docs recover too

These call the router function directly (mirrors test_predecessor_endpoint_0061), with a
real temp storage root so path resolution / persistence is exercised end to end.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402

from modules.flow_gate.documents.routers import documents as docs_mod  # noqa: E402
from modules.flow_gate.documents import document_service  # noqa: E402
from modules.flow_gate import process_service  # noqa: E402
from modules.flow_gate.db import document_revisions as db_rev  # noqa: E402
from modules.flow_gate.storage import paths as storage_paths  # noqa: E402

_USER = {"user_id": "tester"}

_DOC = {
    "doc_id": "testprj.default.0109.0001-R",
    "group_id": "testprj.default.0109",
    "project_id": "testprj",
    "module": "default",
    "branch": "main",
    "type_code": "R",
    "title": "재생성 대상",
    "target_id": "",
    "status": "closed",  # NR0003 §5: recovery must work even for a closed doc
    "file_path": "",     # the missing-file symptom — empty column
}


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    # Recovery must not depend on group/edit gates.
    monkeypatch.setattr(process_service, "is_group_disposed", lambda gid: False)
    captured: dict = {}

    def _update(doc_id, fields, actor_user_id=None):
        captured["file_path"] = fields.get("file_path")
        return {**_DOC, **fields, "doc_id": doc_id}

    monkeypatch.setattr(document_service, "update_document", _update)
    return tmp_path, captured


def _expected_target(tmp_path: Path) -> Path:
    return (
        tmp_path / "documents" / "testprj" / "main" / "default" / "0109"
        / "0001-R_document.md"
    )


def test_regenerate_from_metadata_when_no_backup(storage, monkeypatch):
    tmp_path, captured = storage
    monkeypatch.setattr(document_service, "get_document", lambda did: dict(_DOC))
    monkeypatch.setattr(db_rev, "list_by_doc", lambda did: [])

    out = docs_mod.regenerate_document_file(_DOC["doc_id"], _USER)

    assert out["restored_from"] == "metadata"
    assert out["body_lost"] is True
    target = _expected_target(tmp_path)
    assert target.is_file()
    body = target.read_text(encoding="utf-8")
    # Frontmatter synthesized from DB metadata.
    assert "type: R" in body
    assert "doc_number: 0001-R" in body
    assert "title: 재생성 대상" in body
    # Persisted path is storage-relative (B0001 guard), not absolute.
    assert captured["file_path"] == "documents/testprj/main/default/0109/0001-R_document.md"


def test_regenerate_restores_latest_revision_backup(storage, monkeypatch):
    tmp_path, _ = storage
    monkeypatch.setattr(document_service, "get_document", lambda did: dict(_DOC))

    # Two backups; newest (revision_no DESC → first) must win.
    rev_dir = tmp_path / "documents" / "testprj" / "main" / "default" / "0109" / "revisions"
    rev_dir.mkdir(parents=True)
    new_backup = rev_dir / "0001-R.r2.md"
    old_backup = rev_dir / "0001-R.r1.md"
    new_backup.write_text("# restored newest body", encoding="utf-8")
    old_backup.write_text("# stale older body", encoding="utf-8")

    def _rel(p: Path) -> str:
        return storage_paths.to_storage_relative(p, "testprj")

    monkeypatch.setattr(db_rev, "list_by_doc", lambda did: [
        {"revision_no": 2, "backup_path": _rel(new_backup)},
        {"revision_no": 1, "backup_path": _rel(old_backup)},
    ])

    out = docs_mod.regenerate_document_file(_DOC["doc_id"], _USER)

    assert out["restored_from"] == "revision"
    assert out["body_lost"] is False
    assert _expected_target(tmp_path).read_text(encoding="utf-8") == "# restored newest body"


def test_regenerate_refuses_to_clobber_existing_file(storage, monkeypatch):
    tmp_path, _ = storage
    monkeypatch.setattr(document_service, "get_document", lambda did: dict(_DOC))
    monkeypatch.setattr(db_rev, "list_by_doc", lambda did: [])

    target = _expected_target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("# precious existing content", encoding="utf-8")

    with pytest.raises(HTTPException) as ei:
        docs_mod.regenerate_document_file(_DOC["doc_id"], _USER)
    assert ei.value.status_code == 409
    # Untouched.
    assert target.read_text(encoding="utf-8") == "# precious existing content"


def test_regenerate_unknown_doc_returns_404(storage, monkeypatch):
    monkeypatch.setattr(document_service, "get_document", lambda did: None)
    with pytest.raises(HTTPException) as ei:
        docs_mod.regenerate_document_file("testprj.default.0109.9999-R", _USER)
    assert ei.value.status_code == 404
