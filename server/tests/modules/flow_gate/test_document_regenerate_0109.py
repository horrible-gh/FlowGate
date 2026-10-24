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

0064 T-B1d (L0005 2.12): recovery now runs inside the document's guard and reads its evidence
(revision backups, events, other rows using the target) from the DB, so the document lives in
the real SQLite DB of ``test_fg_card_documents.db_path`` instead of mocked service calls. A file
already at the target that no row uses is no longer a 409: it is kept beside as ``.orphan-*``
and the recovery writes next to it; a readable body is the 409 ``file_exists``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fastapi import HTTPException  # noqa: E402

from test_fg_card_documents import db_path  # noqa: E402,F401 — the real SQLite DB fixture
from modules.flow_gate.documents.routers import documents as docs_mod  # noqa: E402
from modules.flow_gate import process_service  # noqa: E402
from modules.flow_gate.db import documents as db_docs  # noqa: E402
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
    "seq": 1,
    "title": "재생성 대상",
    "target_id": "",
    "status": "closed",  # NR0003 §5: recovery must work even for a closed doc
    "file_path": "",     # the missing-file symptom — empty column
}


@pytest.fixture
def storage(tmp_path, monkeypatch, db_path):  # noqa: F811
    from modules.flow_gate.db import _SqliteTxnAdapter
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    # The affected-row count the Flow guard bump reads (as test_fg_card_documents.env sets it up).
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    # Recovery must not depend on group/edit gates.
    monkeypatch.setattr(process_service, "is_group_disposed", lambda gid: False)
    monkeypatch.setattr(docs_mod, "_broadcast_document_refresh", lambda doc: None)
    db_projects.create({"project_id": "testprj", "project_name": "testprj"})
    db_groups.create({"group_id": _DOC["group_id"], "project_id": "testprj", "module": "default",
                      "title": "0109", "status": "OPEN"})
    db_docs.create(dict(_DOC))
    return tmp_path


def _expected_target(tmp_path: Path) -> Path:
    return (
        tmp_path / "documents" / "testprj" / "main" / "default" / "0109"
        / "0001-R_document.md"
    )


def test_regenerate_from_metadata_when_no_backup(storage, monkeypatch):
    tmp_path = storage
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
    expected = "documents/testprj/main/default/0109/0001-R_document.md"
    assert db_docs.get_by_id(_DOC["doc_id"])["file_path"] == expected
    assert out["data"]["status"] == "closed"


def test_regenerate_restores_latest_revision_backup(storage, monkeypatch):
    tmp_path = storage

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
    tmp_path = storage
    monkeypatch.setattr(db_rev, "list_by_doc", lambda did: [])

    target = _expected_target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("# precious existing content", encoding="utf-8")
    db_docs.update(_DOC["doc_id"], {"file_path": storage_paths.to_storage_relative(target, "testprj")})

    out = docs_mod.regenerate_document_file(_DOC["doc_id"], _USER)
    assert out.status_code == 409
    assert out.body == (b'{"detail":"Document file already exists; nothing to regenerate.",'
                        b'"error":"file_exists"}')
    # Untouched.
    assert target.read_text(encoding="utf-8") == "# precious existing content"


def test_regenerate_keeps_an_unrecorded_file_aside(storage, monkeypatch):
    """0064 T-B1d: a file no row uses is moved aside, never overwritten or deleted."""
    tmp_path = storage
    monkeypatch.setattr(db_rev, "list_by_doc", lambda did: [])

    target = _expected_target(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text("# precious existing content", encoding="utf-8")

    out = docs_mod.regenerate_document_file(_DOC["doc_id"], _USER)
    assert out["restored_from"] == "metadata"
    [aside] = [p for p in target.parent.iterdir() if p.name.startswith(target.name + ".orphan-")]
    assert aside.read_text(encoding="utf-8") == "# precious existing content"


def test_regenerate_unknown_doc_returns_404(storage, monkeypatch):
    with pytest.raises(HTTPException) as ei:
        docs_mod.regenerate_document_file("testprj.default.0109.9999-R", _USER)
    assert ei.value.status_code == 404
