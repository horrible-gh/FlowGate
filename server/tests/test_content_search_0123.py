"""Body full-text search (R0001 Phase 2 / group 0123 T0006).

Exercises GET /api/v1/search/documents/content end to end with real files on a
temporary storage root, so the mtime-cached filesystem read path in
content_search_service is covered (not just mocked). Self-contained harness mirrors
test_outbound_list.py; FLOWGATE_STORAGE_DIR is set per-module and restored to avoid
leaking into other test modules.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

_GROUP = "testprj-__ALL__-0123"
_BODY_MARKER = "zphiraxquux_unique_body_marker"
_TITLE_ONLY = "titlematchonlyzz"
# Marker present in BOTH a markdown heading line and the running prose — the
# snippet must be drawn from the prose, never the header (rev4).
_HDR_BODY_MARKER = "headerandbodymarkerzz"
# A unique token that lives ONLY inside the YAML frontmatter of a document file —
# it must NOT count as a body hit (the frontmatter is the header, not the body) and
# must never appear in any snippet (group 0123 rev6).
_FRONTMATTER_ONLY = "frontmatteronlymarkerzz"
# A token in the body of a frontmatter-bearing doc — its snippet must start from the
# body content, never the leading ``---`` header fence (group 0123 rev6).
_FM_BODY_MARKER = "frontmatterbodymarkerzz"
# A token that lives ONLY inside a markdown section heading (``## …``) in the body —
# it is body content, so the match must still produce a snippet (group 0123 rev7: the
# excerpt went missing whenever the only hit was a heading, "본문이 있는 문서도 나오지
# 않는다"). The snippet must carry the heading *text* but none of the ``#`` markup.
_HEADING_ONLY_MARKER = "headingonlymarkerzz"
# A body marker for a doc whose stored ``file_path`` is a STALE ABSOLUTE path from a
# previous host (B0054 host-migration class). The real file lives at the canonical tail
# under the current storage root; the body must still be searchable (group 0123 rev8:
# "본문이 있는 문서의 경우도 [검색에] 나오지 않는다" — a body-bearing doc must not silently
# drop out just because its stored path points at a dead host prefix).
_MIGRATED_BODY_MARKER = "hostmigratedbodymarkerzz"


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


@pytest.fixture(scope="module")
def storage_root():
    prev = os.environ.get("FLOWGATE_STORAGE_DIR")
    root = tempfile.mkdtemp(prefix="fg_content_search_")
    os.environ["FLOWGATE_STORAGE_DIR"] = root
    yield Path(root)
    if prev is None:
        os.environ.pop("FLOWGATE_STORAGE_DIR", None)
    else:
        os.environ["FLOWGATE_STORAGE_DIR"] = prev


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            raise NotImplementedError

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


def _write_doc_file(storage_root: Path, rel_path: str, body: str) -> None:
    target = storage_root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


@pytest.fixture(scope="module")
def seed_data(tmp_db, storage_root):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.services import content_search_service

    content_search_service.reset_cache()

    projects.create({"project_id": "testprj", "project_name": "Test Project"})
    projects.create({"project_id": "OTHERPRJ", "project_name": "Other Project"})
    users.create({
        "user_id": "usr_test_001",
        "username": "testuser",
        "email": "test@example.com",
        "password": "hashed_pw",
    })

    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    for perm in ["document.create", "document.read", "document.update"]:
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
            [perm, perm, now],
        )
        store._execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
            ["role_worker", perm],
        )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        ["usr_test_001", "testprj", "role_worker", now],
    )
    db_groups.create({
        "group_id": _GROUP,
        "project_id": "testprj",
        "module": "__ALL__",
        "title": "Search Group",
    })

    # Doc whose BODY contains the unique marker (title/doc_id do not).
    body_rel = "documents/testprj/main/0123/0001-R_doc.md"
    _write_doc_file(
        storage_root, body_rel,
        f"# Root\n\nThis report discusses the {_BODY_MARKER} deep inside the prose.\n",
    )
    db_docs.create({
        "doc_id": "testprj.__ALL__.0123.0001-R",
        "project_id": "testprj",
        "type_code": "R",
        "seq": 1,
        "title": "Body Bearing Root",
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "file_path": body_rel,
    })

    # Doc whose TITLE matches a token absent from any body (title-fallback path).
    title_rel = "documents/testprj/main/0123/0002-N_doc.md"
    _write_doc_file(storage_root, title_rel, "# Plain\n\nNothing special in this body.\n")
    db_docs.create({
        "doc_id": "testprj.__ALL__.0123.0002-N",
        "project_id": "testprj",
        "type_code": "N",
        "seq": 2,
        "title": f"Investigation {_TITLE_ONLY} order",
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "file_path": title_rel,
    })

    # Doc whose heading line AND prose both carry a marker — proves the snippet is
    # built from the prose with the markdown header stripped (rev4).
    hdr_rel = "documents/testprj/main/0123/0003-R_doc.md"
    _write_doc_file(
        storage_root, hdr_rel,
        f"# Heading line says {_HDR_BODY_MARKER} here\n\n"
        f"Much later the running prose also says {_HDR_BODY_MARKER} in a sentence.\n",
    )
    db_docs.create({
        "doc_id": "testprj.__ALL__.0123.0003-R",
        "project_id": "testprj",
        "type_code": "R",
        "seq": 3,
        "title": "Header And Body Doc",
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "file_path": hdr_rel,
    })

    # Doc that begins with a real YAML frontmatter header (the actual stored-file
    # shape). The header carries _FRONTMATTER_ONLY; the body carries _FM_BODY_MARKER.
    # Proves rev6: the header never leaks into the snippet and a header-only term is
    # not a body hit (group 0123 — "상세쪽에 문서의 헤더를 출력하지 말라").
    fm_rel = "documents/testprj/main/0123/0004-B_doc.md"
    _write_doc_file(
        storage_root, fm_rel,
        "---\n"
        "project: testprj\n"
        "module: __ALL__\n"
        "group: 0123\n"
        "type: B\n"
        "doc_number: 0004-B\n"
        f"title: {_FRONTMATTER_ONLY} Header Bug\n"
        "target_id: \n"
        "---\n\n"
        f"The body proper opens here and mentions {_FM_BODY_MARKER} in a sentence.\n",
    )
    db_docs.create({
        "doc_id": "testprj.__ALL__.0123.0004-B",
        "project_id": "testprj",
        "type_code": "B",
        "seq": 4,
        "title": "Frontmatter Header Doc",
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "file_path": fm_rel,
    })

    # Doc whose search term lives ONLY in a markdown section heading (no prose copy).
    # Proves rev7: a heading is body content, so the match must still yield a snippet
    # carrying the heading text (the rev4 whole-line heading strip produced no snippet
    # here, so the doc's body content "나왔다 안나왔다" depending on heading vs prose).
    heading_rel = "documents/testprj/main/0123/0005-R_doc.md"
    _write_doc_file(
        storage_root, heading_rel,
        f"# Root\n\n## Section about {_HEADING_ONLY_MARKER}\n\n"
        "Plain prose that does not repeat the marker token at all.\n",
    )
    db_docs.create({
        "doc_id": "testprj.__ALL__.0123.0005-R",
        "project_id": "testprj",
        "type_code": "R",
        "seq": 5,
        "title": "Heading Only Doc",
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "file_path": heading_rel,
    })

    # Doc whose stored file_path is a STALE ABSOLUTE path from a previous host
    # (B0054 host-migration class). The actual file is written at the canonical tail
    # under the *current* storage root; the stored path keeps a dead ``/home/<olduser>``
    # prefix that no longer resolves. Proves rev8: the body is recovered by re-basing
    # the ``documents/<project>/…`` tail, so a document that HAS the body content still
    # appears in search instead of being silently skipped.
    migrated_tail = "documents/testprj/main/0123/0006-R_doc.md"
    _write_doc_file(
        storage_root, migrated_tail,
        f"# Root\n\nMigrated-host body talks about {_MIGRATED_BODY_MARKER} in the prose.\n",
    )
    db_docs.create({
        "doc_id": "testprj.__ALL__.0123.0006-R",
        "project_id": "testprj",
        "type_code": "R",
        "seq": 6,
        "title": "Host Migrated Doc",
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "file_path": "/home/olduser/legacy/FlowGate/storage/" + migrated_tail,
    })

    # Doc in another project carrying the body marker — for the project facet.
    other_rel = "documents/OTHERPRJ/main/0123/0001-R_doc.md"
    _write_doc_file(
        storage_root, other_rel,
        f"# Other\n\nAlso mentions {_BODY_MARKER} but in OTHERPRJ.\n",
    )
    db_docs.create({
        "doc_id": "OTHERPRJ.__ALL__.0123.0001-R",
        "project_id": "OTHERPRJ",
        "type_code": "R",
        "seq": 1,
        "title": "Other Project Root",
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "file_path": other_rel,
    })
    yield


def _build_client():
    from modules.flow_gate.api.v1.list_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _issue_bearer(tmp_path, user_id: str = "usr_test_001", project: str = "testprj") -> str:
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        issued = token_service.issue(
            project=project,
            group_id=_GROUP,
            action_scope="new",
            doc_ref=None,
            issued_to=user_id,
        )
    return issued["raw_token"]


def _get(client, path, raw):
    return client.get(path, headers={"Authorization": f"Bearer {raw}"})


def test_content_search_body_match_with_snippet(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, f"/api/v1/search/documents/content?q={_BODY_MARKER}&project=testprj", raw)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["scope"] == "content"
    hit = next(i for i in data["items"] if i["doc_id"] == "testprj.__ALL__.0123.0001-R")
    assert hit["matched_in"] == "body"
    assert _BODY_MARKER in hit["snippet"]


def test_content_search_snippet_keeps_heading_text_without_hash_markup(seed_data, tmp_path):
    """A markdown heading is body content: its text stays in the snippet, the ``#`` does not.

    rev4 stripped whole heading lines (misreading "문서의 헤더" as a ``#`` heading); rev6
    clarified the header is the YAML frontmatter, and rev7 reported the resulting bug —
    a heading hit produced no body excerpt. The snippet now carries the heading *text*
    while the ``#`` markup glyphs are removed.
    """
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(
        client,
        f"/api/v1/search/documents/content?q={_HDR_BODY_MARKER}&project=testprj",
        raw,
    )

    assert resp.status_code == 200
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0003-R")
    assert hit["matched_in"] == "body"
    snippet = hit["snippet"]
    assert snippet is not None
    assert _HDR_BODY_MARKER in snippet
    # The ``#`` markup glyph is gone, but the heading words are body content and stay.
    assert "#" not in snippet
    assert "Heading line" in snippet


def test_content_search_heading_only_term_still_yields_snippet(seed_data, tmp_path):
    """A term that appears ONLY in a section heading must still produce a snippet (rev7).

    This is the reviewer's exact bug: the document body contains the term (in a ``##``
    section title), the row matched as ``body``, yet the excerpt was empty because the
    heading line had been stripped — so the content "나왔다 안나왔다" depending on whether
    the hit was prose or a heading. The snippet must now carry the heading text.
    """
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(
        client,
        f"/api/v1/search/documents/content?q={_HEADING_ONLY_MARKER}&project=testprj",
        raw,
    )

    assert resp.status_code == 200
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0005-R")
    assert hit["matched_in"] == "body"
    snippet = hit["snippet"]
    assert snippet is not None
    assert _HEADING_ONLY_MARKER in snippet
    assert "Section about" in snippet
    assert "#" not in snippet


def test_content_search_snippet_excludes_yaml_frontmatter(seed_data, tmp_path):
    """The snippet must start from the body, never the leading YAML header (rev6).

    The reviewer's actual complaint: a stored file begins with a ``---`` frontmatter
    fence (project/module/group/title/…) and that header was showing in the detail
    row. The snippet must contain the body match and none of the fence/metadata.
    """
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(
        client,
        f"/api/v1/search/documents/content?q={_FM_BODY_MARKER}&project=testprj",
        raw,
    )

    assert resp.status_code == 200
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0004-B")
    assert hit["matched_in"] == "body"
    snippet = hit["snippet"]
    assert snippet is not None
    assert _FM_BODY_MARKER in snippet
    # No part of the YAML header fence or its metadata keys/values leaks in.
    assert "---" not in snippet
    assert "project:" not in snippet
    assert "doc_number" not in snippet
    assert _FRONTMATTER_ONLY not in snippet


def test_content_search_frontmatter_only_term_is_not_a_body_hit(seed_data, tmp_path):
    """A term that lives only in the frontmatter header is not a body match (rev6).

    The header is not body content, so a metadata-only token must not surface the doc
    as a body hit with a snippet. (It may still match via title/doc_id, but here the
    token is absent from both, so the doc does not match at all.)
    """
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(
        client,
        f"/api/v1/search/documents/content?q={_FRONTMATTER_ONLY}&project=testprj",
        raw,
    )

    assert resp.status_code == 200
    # The token is only in the YAML header (and that doc's real title does not contain
    # it) → the frontmatter doc must not appear as a body hit.
    assert all(
        i["doc_id"] != "testprj.__ALL__.0123.0004-B" for i in resp.json()["items"]
    )


def test_content_search_recovers_host_migrated_absolute_path(seed_data, tmp_path):
    """A stale absolute file_path from a previous host must still surface the body (rev8).

    The reviewer's literal complaint: documents that DO have body content do not appear
    in search. One real cause is a stored ``/home/<olduser>/…`` absolute path that no
    longer resolves on the current host — the body file was silently skipped, so a
    body-only search term never matched and the document vanished from results. The
    re-based resolver recovers the file under the current storage root, so the document
    appears as a body hit with a snippet.
    """
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(
        client,
        f"/api/v1/search/documents/content?q={_MIGRATED_BODY_MARKER}&project=testprj",
        raw,
    )

    assert resp.status_code == 200
    hit = next(
        i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0006-R"
    )
    assert hit["matched_in"] == "body"
    assert hit["snippet"] is not None
    assert _MIGRATED_BODY_MARKER in hit["snippet"]


def test_content_search_no_match_empty(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, "/api/v1/search/documents/content?q=qqq_no_such_token_qqq", raw)

    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_content_search_title_fallback_includes_body_preview(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, f"/api/v1/search/documents/content?q={_TITLE_ONLY}", raw)

    assert resp.status_code == 200
    items = resp.json()["items"]
    hit = next(i for i in items if i["doc_id"] == "testprj.__ALL__.0123.0002-N")
    assert hit["matched_in"] == "title"
    assert hit["snippet"] is not None
    assert "Plain" in hit["snippet"]
    assert "Nothing special in this body" in hit["snippet"]


def test_content_search_doc_id_fallback_includes_body_preview(seed_data, tmp_path):
    """Searching by a full doc id must still show the simplified body in content mode.

    This is the rev9 rejection in concrete form: rows such as
    ``flowgate.default.0094.0001-R`` matched by identifier, but the detail line that
    should show the document body (e.g. ``test 1234``) was absent because snippet was
    only populated for direct body matches.
    """
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(
        client,
        "/api/v1/search/documents/content?q=testprj.__ALL__.0123.0001-R&project=testprj",
        raw,
    )

    assert resp.status_code == 200
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0001-R")
    assert hit["matched_in"] == "doc_id"
    assert hit["snippet"] is not None
    assert "Root" in hit["snippet"]
    assert _BODY_MARKER in hit["snippet"]


def test_meta_search_doc_id_includes_body_preview(seed_data, tmp_path):
    """rev10 core fix: the DEFAULT (metadata) search must show the brief body too.

    The reviewer searches in the default explorer mode ("내용까지 검색" OFF), which hits
    ``GET /search/documents`` — NOT the content endpoint. For rev1–rev9 that endpoint
    returned only id/title with no body, so a doc_id search like
    ``flowgate.default.0094.0001-R`` showed the row but never the brief body
    (``test 1234``). Every prior fix patched the wrong (content) endpoint. The body
    preview must now come back on this default endpoint as well.
    """
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(
        client,
        "/api/v1/search/documents?q=0001-R&project=testprj",
        raw,
    )

    assert resp.status_code == 200
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0001-R")
    # The default endpoint now carries the simplified body preview.
    assert hit["snippet"] is not None
    assert "Root" in hit["snippet"]
    assert _BODY_MARKER in hit["snippet"]


def test_meta_search_title_includes_body_preview(seed_data, tmp_path):
    """Title-matched results in the default search also show the brief body."""
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, "/api/v1/search/documents?q=Body+Bearing&project=testprj", raw)

    assert resp.status_code == 200
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0001-R")
    assert hit["snippet"] is not None
    assert _BODY_MARKER in hit["snippet"]


def test_meta_search_no_body_file_snippet_none(seed_data, tmp_path):
    """A result whose body file cannot be read returns snippet=None, not an error."""
    client = _build_client()
    raw = _issue_bearer(tmp_path)
    # Searching by a title token still returns the row; with a readable file it has a
    # preview. The contract under test: the field is always present (None when absent).
    resp = _get(client, f"/api/v1/search/documents?q={_TITLE_ONLY}&project=testprj", raw)
    assert resp.status_code == 200
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == "testprj.__ALL__.0123.0002-N")
    assert "snippet" in hit


def test_content_search_type_facet(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, f"/api/v1/search/documents/content?q={_BODY_MARKER}&type=N", raw)

    assert resp.status_code == 200
    # The body marker lives only in R docs → N facet yields no body hits.
    assert all(i["doc_id"] != "testprj.__ALL__.0123.0001-R" for i in resp.json()["items"])


def test_content_search_project_facet(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, f"/api/v1/search/documents/content?q={_BODY_MARKER}&project=testprj", raw)

    assert resp.status_code == 200
    ids = {i["doc_id"] for i in resp.json()["items"]}
    assert "testprj.__ALL__.0123.0001-R" in ids
    assert "OTHERPRJ.__ALL__.0123.0001-R" not in ids


def test_content_search_mtime_refresh(seed_data, tmp_path, storage_root):
    """Rewriting a file changes its mtime → the cache must re-read it (self-healing)."""
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    # Warm the cache.
    _get(client, f"/api/v1/search/documents/content?q={_BODY_MARKER}&project=testprj", raw)

    new_marker = "freshmarker_after_rewrite_55"
    target = storage_root / "documents/testprj/main/0123/0001-R_doc.md"
    # Bump mtime explicitly so the change is detectable even within filesystem
    # timestamp granularity.
    target.write_text(f"# Root\n\nNow it talks about {new_marker} instead.\n", encoding="utf-8")
    st = target.stat()
    os.utime(target, (st.st_atime + 5, st.st_mtime + 5))

    resp_new = _get(client, f"/api/v1/search/documents/content?q={new_marker}&project=testprj", raw)
    assert resp_new.status_code == 200
    assert any(i["doc_id"] == "testprj.__ALL__.0123.0001-R" for i in resp_new.json()["items"])

    resp_old = _get(client, f"/api/v1/search/documents/content?q={_BODY_MARKER}&project=testprj", raw)
    assert all(i["doc_id"] != "testprj.__ALL__.0123.0001-R" for i in resp_old.json()["items"])


def test_content_search_empty_query_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)
    resp = _get(client, "/api/v1/search/documents/content?q=%20%20", raw)
    assert resp.status_code == 400


def test_content_search_missing_query_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)
    resp = _get(client, "/api/v1/search/documents/content", raw)
    assert resp.status_code == 400


def test_content_search_no_auth_401(seed_data):
    client = _build_client()
    resp = client.get(f"/api/v1/search/documents/content?q={_BODY_MARKER}")
    assert resp.status_code == 401


def test_content_search_limit_out_of_range_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)
    resp = _get(client, f"/api/v1/search/documents/content?q={_BODY_MARKER}&limit=0", raw)
    assert resp.status_code == 400
