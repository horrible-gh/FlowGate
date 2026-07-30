"""Conversation-turn search + merge into document search (group 0351, T4, L0004 §2-15).

Full-schema harness (mirrors test_content_search_0123.py): all sqlite migrations
applied to a real temp DB, so both `documents` (title/status/project_id/…) and
`conversation_turns`/`conversation_docs` exist together for the JOIN this feature adds.
"""
from __future__ import annotations

import hashlib
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

_GROUP = "testprj-__ALL__-0351search"
_TURN_MARKER = "zturnmarkerxyz"
_STALE_FILE_MARKER = "zstalefilemarkerxyz"
_FAILED_FILE_MARKER = "zfailedfilemarkerxyz"


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
    root = tempfile.mkdtemp(prefix="fg_conv_search_")
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


def _seed_turns(doc_id: str, bodies: list[str], start: int = 1) -> None:
    from modules.flow_gate.db import conversation_turns as turn_store

    for offset, body in enumerate(bodies):
        seq = start + offset
        key = f"search-seed:{doc_id}:{seq}"
        turn_store.insert_migrated_turn(
            doc_id=doc_id, seq=seq, speaker="user" if seq % 2 else "ai",
            participant_key="user:u1" if seq % 2 else "provider:legacy:x",
            display_name=None if seq % 2 else "Claude",
            locale="ko" if seq % 2 else None,
            body=body, body_hash=hashlib.sha256(body.encode()).hexdigest(),
            based_on_seq=seq - 1, idempotency_key=key,
            idempotency_hash=hashlib.sha256(key.encode()).hexdigest(),
            created_at=f"2026-07-29T10:{seq:02d}:00+09:00",
        )


@pytest.fixture(scope="module")
def seed_data(tmp_db, storage_root):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import conversation_turns as turn_store
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.services import content_search_service

    content_search_service.reset_cache()

    projects.create({"project_id": "testprj", "project_name": "Test Project"})
    users.create({
        "user_id": "usr_test_001", "username": "testuser",
        "email": "test@example.com", "password": "hashed_pw",
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
        "group_id": _GROUP, "project_id": "testprj", "module": "__ALL__", "title": "Search Group",
    })

    # CH #1: migrated — its stale file body must be EXCLUDED from doc-body search
    # (T4 §5), and its turns must be found by turn search instead.
    migrated_rel = "documents/testprj/main/0351search/0001-CH_doc.md"
    _write_doc_file(
        storage_root, migrated_rel,
        f"---\ntitle: chat\n---\n\nThis stale frozen file still says {_STALE_FILE_MARKER}.\n",
    )
    db_docs.create({
        "doc_id": "testprj.__ALL__.0351search.0001-CH", "project_id": "testprj",
        "type_code": "CH", "seq": 1, "title": "Migrated Chat", "group_id": _GROUP,
        "module": "__ALL__", "owner_id": "usr_test_001", "status": "open",
        "file_path": migrated_rel,
    })
    turn_store.ensure_migration_row("testprj.__ALL__.0351search.0001-CH")
    store._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated' WHERE doc_id = ?",
        ["testprj.__ALL__.0351search.0001-CH"],
    )
    _seed_turns(
        "testprj.__ALL__.0351search.0001-CH",
        [f"hello {_TURN_MARKER} one", f"hello {_TURN_MARKER} two",
         f"hello {_TURN_MARKER} three", f"hello {_TURN_MARKER} four"],
    )
    # Also plant the stale marker in a turn so we can prove doc-body search never
    # matches it via the file (it would only ever match via the turn path).
    _seed_turns(
        "testprj.__ALL__.0351search.0001-CH", [f"turn says {_STALE_FILE_MARKER} too"], start=5,
    )

    # CH #2: migration FAILED — its file stays the record of truth and must still be
    # searchable via the ordinary document-body path.
    failed_rel = "documents/testprj/main/0351search/0002-CH_doc.md"
    _write_doc_file(
        storage_root, failed_rel,
        f"---\ntitle: chat\n---\n\nThis LEGACY body still says {_FAILED_FILE_MARKER}.\n",
    )
    db_docs.create({
        "doc_id": "testprj.__ALL__.0351search.0002-CH", "project_id": "testprj",
        "type_code": "CH", "seq": 2, "title": "Failed Chat", "group_id": _GROUP,
        "module": "__ALL__", "owner_id": "usr_test_001", "status": "open",
        "file_path": failed_rel,
    })
    turn_store.ensure_migration_row("testprj.__ALL__.0351search.0002-CH")
    store._execute(
        "UPDATE conversation_docs SET migration_state = 'failed' WHERE doc_id = ?",
        ["testprj.__ALL__.0351search.0002-CH"],
    )

    # A non-CH document — used to prove the `type` facet suppresses turn results.
    other_rel = "documents/testprj/main/0351search/0003-R_doc.md"
    _write_doc_file(storage_root, other_rel, "# Root\n\nUnrelated body.\n")
    db_docs.create({
        "doc_id": "testprj.__ALL__.0351search.0003-R", "project_id": "testprj",
        "type_code": "R", "seq": 3, "title": "Other Root", "group_id": _GROUP,
        "module": "__ALL__", "owner_id": "usr_test_001", "status": "open",
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
            project=project, group_id=_GROUP, action_scope="new", doc_ref=None, issued_to=user_id,
        )
    return issued["raw_token"]


def _get(client, path, raw):
    return client.get(path, headers={"Authorization": f"Bearer {raw}"})


# ── content_search_service.search_conversation_turns() 단위 ──────────────────

def test_turn_search_finds_matches_across_turns(seed_data):
    from modules.flow_gate.services import content_search_service

    # Override the production per-doc cap so all four are visible; L0004 §2-15 orders
    # newest turns first, with descending seq as the deterministic tie-breaker.
    items = content_search_service.search_conversation_turns(
        q=_TURN_MARKER, project="testprj", per_doc=10,
    )
    assert len(items) == 4
    assert all(i["match_kind"] == "conversation_turn" for i in items)
    assert all(i["doc_id"] == "testprj.__ALL__.0351search.0001-CH" for i in items)
    assert all(_TURN_MARKER in i["snippet"] for i in items)
    assert [i["seq"] for i in items] == [4, 3, 2, 1]


def test_turn_search_caps_results_per_document(seed_data):
    from modules.flow_gate.services import content_search_service

    items = content_search_service.search_conversation_turns(
        q=_TURN_MARKER, project="testprj", per_doc=2,
    )
    assert len(items) == 2
    assert [i["seq"] for i in items] == [4, 3]


def test_turn_search_percent_and_underscore_are_literal_not_wildcards(seed_data):
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.db import conversation_turns as turn_store
    from modules.flow_gate.services import content_search_service

    literal_body = "50%_off coupon zwildcardlitmarker"
    turn_store.insert_migrated_turn(
        doc_id="testprj.__ALL__.0351search.0001-CH", seq=50, speaker="user",
        participant_key="user:u1", display_name=None, locale="ko", body=literal_body,
        body_hash=hashlib.sha256(literal_body.encode()).hexdigest(), based_on_seq=49,
        idempotency_key="wildcard-lit-1",
        idempotency_hash=hashlib.sha256(b"wildcard-lit-1").hexdigest(),
        created_at=now_iso(),
    )
    hits = content_search_service.search_conversation_turns(q="50%_off", project="testprj")
    assert any("zwildcardlitmarker" in i["snippet"] for i in hits)
    # A query with the SAME characters but not actually present in any turn must not
    # match everything via an unescaped wildcard interpretation.
    no_hits = content_search_service.search_conversation_turns(q="zzz%zzz_zzz", project="testprj")
    assert no_hits == []


def test_turn_search_is_case_insensitive(seed_data):
    from modules.flow_gate.services import content_search_service

    items = content_search_service.search_conversation_turns(q=_TURN_MARKER.upper(), project="testprj")
    assert len(items) > 0


def test_turn_search_snippet_is_at_most_120_characters(seed_data):
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.db import conversation_turns as turn_store
    from modules.flow_gate.services import content_search_service

    marker = "zsnippetlengthmarker"
    body = "a" * 80 + marker + "b" * 200
    turn_store.insert_migrated_turn(
        doc_id="testprj.__ALL__.0351search.0001-CH", seq=51, speaker="user",
        participant_key="user:u1", display_name=None, locale="ko", body=body,
        body_hash=hashlib.sha256(body.encode()).hexdigest(), based_on_seq=50,
        idempotency_key="snippet-limit-1",
        idempotency_hash=hashlib.sha256(b"snippet-limit-1").hexdigest(),
        created_at=now_iso(),
    )
    hits = content_search_service.search_conversation_turns(q=marker, project="testprj")
    assert len(hits) == 1
    assert hits[0]["snippet"].startswith("…") and hits[0]["snippet"].endswith("…")
    assert len(hits[0]["snippet"].strip("…")) <= content_search_service.SEARCH_SNIPPET_CHARS


def test_turn_search_hides_partial_rows_while_migration_is_in_progress(seed_data):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import content_search_service

    doc_id = "testprj.__ALL__.0351search.0001-CH"
    store = get_store()
    store._execute(
        "UPDATE conversation_docs SET migration_state = 'in_progress' WHERE doc_id = ?", [doc_id],
    )
    try:
        assert content_search_service.search_conversation_turns(
            q=_TURN_MARKER, project="testprj",
        ) == []
    finally:
        store._execute(
            "UPDATE conversation_docs SET migration_state = 'migrated' WHERE doc_id = ?", [doc_id],
        )


# ── 문서 본문 검색과의 중복 제거 (T4 §5) ───────────────────────────────────────

def test_migrated_ch_stale_file_body_is_excluded_from_document_body_search(seed_data):
    from modules.flow_gate.services import content_search_service

    page, total = content_search_service.search_document_bodies(q=_STALE_FILE_MARKER, project="testprj")
    assert total == 0
    assert page == []


def test_failed_ch_file_body_is_still_searchable(seed_data):
    from modules.flow_gate.services import content_search_service

    page, total = content_search_service.search_document_bodies(q=_FAILED_FILE_MARKER, project="testprj")
    assert total == 1
    assert page[0]["doc_id"] == "testprj.__ALL__.0351search.0002-CH"
    assert page[0]["match_kind"] == "document_body"
    assert page[0]["matched_in"] == "body"


# ── 병합 HTTP 엔드포인트 ───────────────────────────────────────────────────────

def test_merged_endpoint_carries_both_match_kinds(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, f"/api/v1/search/documents/content?q={_TURN_MARKER}&project=testprj", raw)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    kinds = {i["match_kind"] for i in data["items"]}
    assert kinds == {"conversation_turn"}
    assert data["turn_total"] == len(data["items"])
    # total/offset/limit keep their pre-existing document-body-only meaning.
    assert data["total"] == 0


def test_merged_endpoint_type_facet_excludes_turn_results(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, f"/api/v1/search/documents/content?q={_TURN_MARKER}&project=testprj&type=R", raw)
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["turn_total"] == 0


def test_merged_endpoint_document_body_and_turn_hits_coexist(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = _get(client, f"/api/v1/search/documents/content?q={_FAILED_FILE_MARKER}&project=testprj", raw)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    body_hits = [i for i in data["items"] if i["match_kind"] == "document_body"]
    assert len(body_hits) == 1
    assert body_hits[0]["doc_id"] == "testprj.__ALL__.0351search.0002-CH"
