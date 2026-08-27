"""문서 조회 도구 — 엔드포인트 시험 (group 0370, P0002 시나리오 1~20).

`/outline`·`/section`·`/meta`·`/relations` 네 갈래와, 검색 결과에 붙는 매치 위치, 저장
응답의 변경 요약을 실제 HTTP 로 왕복시켜 잰다. 문서 파일은 임시 저장소에 진짜로 쓰고
읽는다 — 좌표 계산이 파일을 어떻게 읽느냐에 달려 있어서 흉내 낸 본문으로는 증명되지 않는다.

**시험 자료의 핵심은 머리말이다.** `_NR_BODY` 는 8줄짜리 머리말을 이고 있고 검색어는 그
아래 18번째 줄에 있다. 본문 기준으로는 10번째 줄이므로, 본문↔파일 좌표 변환(L0003 §2-1)
을 빠뜨린 구현은 여기서만 10 을 답한다 — 요즘 등록된 머리말 없는 문서로는 절대 드러나지
않는 종류의 버그다.
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

PROJECT = "testprj"
MODULE = "__ALL__"
GROUP = "testprj-__ALL__-0370"
DOC_R = "testprj-__ALL__-0370-R0001"
DOC_NR = "testprj-__ALL__-0370-NR0003"
DOC_T = "testprj-__ALL__-0370-T0004"
DOC_PLAIN = "testprj-__ALL__-0370-N0002"

MARKER = "zqmarkerunique"

# 머리말 8줄 + 본문. 줄 번호를 주석으로 못 박아 둔다 — 기대값이 여기서 나온다.
_NR_BODY = (
    "---\n"                       # 1
    "project: testprj\n"          # 2
    "module: __ALL__\n"           # 3
    "group: 0370\n"               # 4
    "type: NR\n"                  # 5
    "doc_number: 0003-NR\n"       # 6
    "title: 조사 보고서\n"          # 7
    "---\n"                       # 8
    "\n"                          # 9   ← body_line_start
    "## 1. 조사 범위\n"             # 10  s1
    "\n"                          # 11
    "첫 구간 본문입니다.\n"          # 12
    "\n"                          # 13
    "## 2. 액션바 구조 분석\n"       # 14  s2
    "\n"                          # 15
    "### 2.1 현재 구성\n"           # 16  s3
    "\n"                          # 17
    f"여기에 {MARKER} 가 있다.\n"    # 18  ← 검색어. 본문 기준으로는 10번째 줄이다.
    "\n"                          # 19
    "### 2.2 다른 모드\n"           # 20  s4
    "\n"                          # 21
    "두 번째 하위 본문.\n"           # 22
    "\n"                          # 23
    "## 3. 결론\n"                 # 24  s5
    "\n"                          # 25
    "마지막 줄.\n"                  # 26
)

# 제목이 하나도 없는 산문 문서 — 목차가 비어도 200 이어야 한다(P0002 시나리오 2).
_PLAIN_BODY = "짧은 산문 한 줄.\n또 한 줄 있다.\n"

# 같은 이름의 제목이 두 곳에 있는 문서(P0002 시나리오 5).
_T_BODY = (
    "## 변경 파일\n"      # 1  s1
    "\n"                 # 2
    "- a.py\n"           # 3
    "\n"                 # 4
    "## 부록\n"           # 5  s2
    "\n"                 # 6
    "### 변경 파일\n"     # 7  s3
    "\n"                 # 8
    "- b.py\n"           # 9
)


# ── harness (test_content_search_0123 / test_inbox_edit_filepath_repoint_0301) ──

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
    root = tempfile.mkdtemp(prefix="fg_doc_query_0370_")
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
        except Exception:  # noqa: BLE001 — some migrations re-add existing objects
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
            # 실제 store 와 같은 경로로 등록된 SQL 을 읽는다(_sq 가 없으면 파일 폴백).
            # /meta·/relations 는 답 개수·검토 이력처럼 등록 SQL 을 타는 값을 싣는다.
            return conn_mod.FlowGateStore._sql(self, key)

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


def _write_doc_file(storage_root: Path, rel_path: str, body: str) -> None:
    target = storage_root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def seed_data(tmp_db, storage_root):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.services import content_search_service

    content_search_service.reset_cache()
    projects.create({"project_id": PROJECT, "project_name": "Test Project"})
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
    for perm in ("document.create", "document.read", "document.update"):
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
        ["usr_test_001", PROJECT, "role_worker", now],
    )
    db_groups.create({
        "group_id": GROUP, "project_id": PROJECT, "module": MODULE,
        "title": "문서 조회 도구",
    })

    def _doc(doc_id, type_code, seq, title, rel, body, **extra):
        _write_doc_file(storage_root, rel, body)
        payload = {
            "doc_id": doc_id, "project_id": PROJECT, "type_code": type_code,
            "seq": seq, "title": title, "group_id": GROUP, "module": MODULE,
            "owner_id": "usr_test_001", "status": "open", "file_path": rel,
            "revision_no": 0,
        }
        payload.update(extra)
        db_docs.create(payload)

    _doc(DOC_R, "R", 1, "문서 조회 도구", "documents/testprj/main/0370/0001-R_doc.md",
         "필요한 부분만 가져올 수 있도록 도구를 만든다.\n")
    _doc(DOC_PLAIN, "N", 2, "산문만 있는 문서",
         "documents/testprj/main/0370/0002-N_doc.md", _PLAIN_BODY)
    _doc(DOC_NR, "NR", 3, "조사 보고서", "documents/testprj/main/0370/0003-NR_doc.md",
         _NR_BODY, triggered_by=DOC_R)
    _doc(DOC_T, "T", 4, f"작업지시 {MARKER} 제목에만",
         "documents/testprj/main/0370/0004-T_doc.md", _T_BODY, target_id=DOC_NR)
    yield


def _client():
    from modules.flow_gate.api.v1.document_routes import router as doc_router
    from modules.flow_gate.api.v1.list_routes import router as list_router

    app = FastAPI()
    app.include_router(doc_router)
    app.include_router(list_router)
    return TestClient(app)


def _bearer(tmp_path) -> str:
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        issued = token_service.issue(
            project=PROJECT, group_id=GROUP, action_scope="new",
            doc_ref=None, issued_to="usr_test_001",
        )
    return issued["raw_token"]


def _get(path, raw, client=None):
    client = client or _client()
    return client.get(path, headers={"Authorization": f"Bearer {raw}"})


# ── 시나리오 1·2: 목차 조회 ──────────────────────────────────────────────────────

def test_outline_lists_sections_without_any_body_text(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/outline", _bearer(tmp_path))
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["document_lines"] == 26
    assert data["body_line_start"] == 9, "머리말 8줄 뒤가 본문 시작이다"
    assert data["section_total"] == 5
    assert data["truncated"] is False
    assert data["content_sha256"] and len(data["content_sha256"]) == 64
    assert [(i["section_id"], i["line_start"], i["line_end"]) for i in data["items"]] == [
        ("s1", 10, 13), ("s2", 14, 23), ("s3", 16, 19), ("s4", 20, 23), ("s5", 24, 26),
    ]
    assert data["items"][2]["heading_path"] == ["2. 액션바 구조 분석", "2.1 현재 구성"]
    assert data["items"][1]["has_children"] is True
    assert data["items"][0]["has_children"] is False
    # 목차 응답에 본문 글자가 실려 있으면 "필요한 부분만 가져온다" 는 목적이 무너진다.
    assert MARKER not in resp.text


def test_outline_max_level_filters_items_but_not_section_ids(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/outline?max_level=2", _bearer(tmp_path))
    data = resp.json()
    assert [i["section_id"] for i in data["items"]] == ["s1", "s2", "s5"]
    assert data["section_total"] == 5, "거르기 전 전체 개수를 그대로 말한다"
    assert data["max_level"] == 2


def test_outline_of_a_document_without_headings_is_200_not_404(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_PLAIN}/outline", _bearer(tmp_path))
    assert resp.status_code == 200, "목차가 비었다고 404 를 주면 문서가 없는 줄 안다"
    data = resp.json()
    assert data["items"] == [] and data["section_total"] == 0
    assert data["document_lines"] == 2 and data["body_line_start"] == 1


# ── 시나리오 3~8: 구간 읽기 ──────────────────────────────────────────────────────

def test_section_by_name_returns_the_heading_line_and_its_body(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/section?section=2.1 현재 구성", _bearer(tmp_path))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["resolved_by"] == "section" and data["ambiguous"] is False
    loc = data["locator"]
    assert (loc["line_start"], loc["line_end"]) == (16, 19)
    assert loc["section_id"] == "s3" and loc["level"] == 3
    assert loc["ref"] == f"{DOC_NR}@r0#L16-19"
    assert data["heading"] == "### 2.1 현재 구성"
    assert data["text"].startswith("### 2.1 현재 구성"), "제목 줄 자신도 포함한다"
    assert MARKER in data["text"]
    assert data["lines"] == 4 and data["truncated"] is False and data["next_locator"] is None


def test_section_by_name_id_lines_and_chars_all_agree(tmp_path):
    """이름으로 찾든 줄로 찾든 글자로 찾든 **같은 로케이터**가 나온다(P0002 시나리오 3·4)."""
    raw = _bearer(tmp_path)
    client = _client()
    by_name = _get(f"/api/v1/document/{DOC_NR}/section?section=2.1 현재 구성", raw, client).json()
    loc = by_name["locator"]
    by_id = _get(f"/api/v1/document/{DOC_NR}/section?section_id=s3", raw, client).json()
    by_lines = _get(f"/api/v1/document/{DOC_NR}/section?lines=16-19", raw, client).json()
    by_chars = _get(
        f"/api/v1/document/{DOC_NR}/section?chars={loc['char_start']}-{loc['char_end']}",
        raw, client,
    ).json()

    for other, expected_by in ((by_id, "section_id"), (by_lines, "lines"), (by_chars, "chars")):
        assert other["resolved_by"] == expected_by
        assert other["locator"] == loc, "resolved_by 만 다르고 로케이터는 같아야 한다"
        assert other["text"] == by_name["text"]


def test_section_include_children_false_returns_only_its_own_body(tmp_path):
    raw = _bearer(tmp_path)
    client = _client()
    whole = _get(f"/api/v1/document/{DOC_NR}/section?section_id=s2", raw, client).json()
    own = _get(
        f"/api/v1/document/{DOC_NR}/section?section_id=s2&include_children=false", raw, client
    ).json()
    assert whole["locator"]["line_end"] == 23
    assert own["locator"]["line_end"] == 15
    assert own["include_children"] is False


def test_section_truncation_resumes_exactly_where_it_stopped(tmp_path):
    """자를 때 줄 가운데를 자르지 않고, next_locator 로 이어 읽으면 원문이 복원된다."""
    raw = _bearer(tmp_path)
    client = _client()
    head = _get(
        f"/api/v1/document/{DOC_NR}/section?section_id=s2&max_chars=30", raw, client
    ).json()
    assert head["truncated"] is True
    assert head["text"].endswith("\n"), "줄 가운데를 자르지 않는다"
    nxt = head["next_locator"]
    assert nxt is not None and nxt["line_start"] == head["locator"]["line_end"] + 1
    assert nxt["char_start"] == head["locator"]["char_end"]

    tail = _get(
        f"/api/v1/document/{DOC_NR}/section?chars={nxt['char_start']}-{nxt['char_end']}"
        f"&max_chars=100000", raw, client,
    ).json()
    whole = _get(f"/api/v1/document/{DOC_NR}/section?section_id=s2", raw, client).json()
    assert head["text"] + tail["text"] == whole["text"]


def test_section_reads_the_first_of_two_same_named_headings_and_says_so(tmp_path):
    """무인 작업에서 애매하다고 멈춰 세우면 작업이 죽는다(P0002 시나리오 5)."""
    resp = _get(f"/api/v1/document/{DOC_T}/section?section=변경 파일", _bearer(tmp_path))
    assert resp.status_code == 200
    data = resp.json()
    assert data["ambiguous"] is True
    assert data["locator"]["section_id"] == "s1"
    assert [c["section_id"] for c in data["candidates"]] == ["s1", "s3"]
    assert data["candidates"][1]["heading_path"] == ["부록", "변경 파일"]


def test_section_accepts_the_english_alias(tmp_path):
    """`변경 파일` ↔ `Changed Files` — 별칭은 기존 파서의 상수에서 불러온다."""
    resp = _get(f"/api/v1/document/{DOC_T}/section?section=Changed Files", _bearer(tmp_path))
    assert resp.status_code == 200
    assert resp.json()["locator"]["section_id"] == "s1"


def test_section_not_found_returns_404_with_candidates(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/section?section=검증 결과", _bearer(tmp_path))
    assert resp.status_code == 404
    data = resp.json()
    assert data["ok"] is False and data["section_total"] == 5
    assert data["error_message"].startswith("section not found")
    assert data["candidates"], "빈 404 를 주면 작업자가 목차를 다시 부른다"


def test_two_locators_at_once_is_422(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/section?section_id=s3&lines=16-19", _bearer(tmp_path))
    assert resp.status_code == 422
    assert resp.json()["error_message"] == (
        "specify exactly one of section, section_id, lines, chars"
    )


def test_no_locator_at_all_is_also_422(tmp_path):
    assert _get(f"/api/v1/document/{DOC_NR}/section", _bearer(tmp_path)).status_code == 422


def test_line_start_out_of_range_is_422_but_end_overflow_clamps(tmp_path):
    raw = _bearer(tmp_path)
    client = _client()
    bad = _get(f"/api/v1/document/{DOC_NR}/section?lines=400-450", raw, client)
    assert bad.status_code == 422
    assert bad.json()["document_lines"] == 26

    ok = _get(f"/api/v1/document/{DOC_NR}/section?lines=24-450", raw, client)
    assert ok.status_code == 200 and ok.json()["locator"]["line_end"] == 26


def test_stale_revision_is_409_and_wins_over_a_missing_section(tmp_path):
    """**6번(409)이 8번(404)보다 앞**이라는 L0003 §4-1 의 순서를 못 박는다.

    판이 달라졌다는 사실을 먼저 말하지 않으면, 작업자는 제목이 지워진 줄 알고 목차를 다시
    부르는 대신 엉뚱한 곳을 뒤진다.
    """
    raw = _bearer(tmp_path)
    resp = _get(
        f"/api/v1/document/{DOC_NR}/section?section_id=s99&revision_no=7", raw
    )
    assert resp.status_code == 409
    data = resp.json()
    assert data["requested_revision_no"] == 7 and data["current_revision_no"] == 0
    assert data["content_sha256"], "어느 판을 읽었는지 지문으로 알려 준다"


def test_matching_revision_passes_through(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/section?section_id=s1&revision_no=0", _bearer(tmp_path))
    assert resp.status_code == 200


def test_missing_document_and_missing_token(tmp_path):
    raw = _bearer(tmp_path)
    gone = _get(f"/api/v1/document/testprj-__ALL__-0370-XX9999/outline", raw)
    assert gone.status_code == 404
    assert _client().get(f"/api/v1/document/{DOC_NR}/outline").status_code == 401


# ── 시나리오 12: 본문 뺀 정보만 ──────────────────────────────────────────────────

def test_meta_omits_the_content_key_entirely(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/meta", _bearer(tmp_path))
    assert resp.status_code == 200
    data = resp.json()
    assert "content" not in data, "null 로 두면 '본문이 빈 문서' 와 구별이 안 된다"
    assert MARKER not in resp.text
    body = data["body"]
    assert body["present"] is True
    assert (body["chars"], body["lines"], body["body_line_start"]) == (len(_NR_BODY), 26, 9)
    assert body["section_total"] == 5
    assert body["outline_url"].endswith(f"/document/{DOC_NR}/outline")
    assert data["answers_count"] == 0
    # 기존 조회와 같은 필드·같은 값 — 이름을 바꾸지 않는다.
    assert data["type"] == "NR" and data["title"] == "조사 보고서"
    assert data["triggered_by"] == DOC_R and data["stored_path"]


def test_meta_of_an_unreadable_body_is_still_200(tmp_path):
    from modules.flow_gate.db import documents as db_docs

    doc_id = "testprj-__ALL__-0370-N9999"
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "type_code": "N", "seq": 9999,
        "title": "파일이 없는 문서", "group_id": GROUP, "module": MODULE,
        "owner_id": "usr_test_001", "status": "open", "file_path": "", "revision_no": 0,
    })
    resp = _get(f"/api/v1/document/{doc_id}/meta", _bearer(tmp_path))
    assert resp.status_code == 200
    assert resp.json()["body"] == {
        "present": False, "chars": 0, "lines": 0, "body_line_start": 1,
        "section_total": 0, "content_sha256": None, "outline_url": None,
    }


def test_outline_of_an_unreadable_body_is_404(tmp_path):
    resp = _get("/api/v1/document/testprj-__ALL__-0370-N9999/outline", _bearer(tmp_path))
    assert resp.status_code == 404


# ── 시나리오 13: 관계 조회 ───────────────────────────────────────────────────────

def test_relations_walks_the_group_and_the_pointers(tmp_path):
    resp = _get(f"/api/v1/document/{DOC_NR}/relations", _bearer(tmp_path))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    group = data["group"]
    assert group["group_id"] == GROUP and group["title"] == "문서 조회 도구"
    assert group["seq"] == 3
    assert group["prev_doc"]["doc_id"] == DOC_PLAIN
    assert group["next_doc"]["doc_id"] == DOC_T
    assert data["triggered_by"]["doc_id"] == DOC_R
    assert data["triggered_by"]["type"] == "R"
    assert data["target"] is None
    assert [r["doc_id"] for r in data["referenced_by"]] == [DOC_T], (
        "referenced_by 는 거꾸로 이 문서를 가리키는 문서다"
    )
    assert data["superseded_by"] is None
    assert data["revisions"] == []
    assert data["answers_count"] == 0
    assert data["workflow"]["decided"] is False, "워크플로 결정이 없으면 전부 null 한 벌"


def test_relations_workflow_maps_the_slot_and_its_neighbours(monkeypatch):
    """워크플로 칸 매핑은 SQL 이 아니라 항목 목록 위의 계산이다 — 그 계산만 따로 잰다."""
    from modules.flow_gate.api.v1 import document_routes
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    items = [
        {"item_seq": 1, "type": "N", "label": "조사지시", "status": "done",
         "doc_class": "R", "result_doc_id": "g.0002-N"},
        {"item_seq": 2, "type": "NR", "label": "조사레포트", "status": "done",
         "doc_class": "R", "result_doc_id": DOC_NR},
        {"item_seq": 3, "type": "T", "label": "작업지시", "status": "done",
         "doc_class": "R", "result_doc_id": "g.0004-T"},
    ]
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc",
                        lambda doc_id: {"id": 1, "doc_id": DOC_R})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: items)

    wf = document_routes._relations_workflow(DOC_NR)
    assert wf["decided"] is True and wf["root_doc_id"] == DOC_R
    assert (wf["item_seq"], wf["type"], wf["label"]) == (2, "NR", "조사레포트")
    assert wf["prev_item"]["result_doc_id"] == "g.0002-N"
    assert wf["next_item"]["item_seq"] == 3


# ── 시나리오 9~11: 검색 결과의 위치와 앞뒤 줄 ────────────────────────────────────

def test_search_match_locator_is_in_file_coordinates_not_body_coordinates(tmp_path):
    """머리말 있는 문서에서만 드러나는 버그 — 본문 10번째 줄은 파일 18번째 줄이다."""
    resp = _get(
        f"/api/v1/search/documents/content?q={MARKER}&project={PROJECT}&context_lines=2",
        _bearer(tmp_path),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["context_lines"] == 2 and data["hits_per_doc"] == 3
    hit = next(i for i in data["items"] if i["doc_id"] == DOC_NR)
    assert hit["matched_in"] == "body" and hit["match_total"] == 1
    match = hit["matches"][0]
    loc = match["locator"]
    assert loc["line_start"] == 18, (
        "본문 기준 10 을 답하면 좌표 변환을 빠뜨린 것이다"
    )
    assert loc["unit"] == "line" and loc["ref"] == f"{DOC_NR}@r0#L18-18"
    assert loc["section_id"] == "s3", "매치가 어느 구간 안인지도 같이 알려 준다"
    assert loc["heading_path"] == ["2. 액션바 구조 분석", "2.1 현재 구성"]
    # 매치의 문자 위치는 파일 원문에서 실제로 그 글자를 가리켜야 한다.
    assert _NR_BODY[match["match_char_start"]: match["match_char_end"]] == MARKER
    assert match["text"] == f"여기에 {MARKER} 가 있다."
    assert match["before"] == ["### 2.1 현재 구성", ""], "앞 두 줄을 원문 그대로, 순서대로"
    assert match["after"] == ["", "### 2.2 다른 모드"]


def test_search_context_lines_zero_empties_the_neighbours(tmp_path):
    resp = _get(
        f"/api/v1/search/documents/content?q={MARKER}&project={PROJECT}&context_lines=0",
        _bearer(tmp_path),
    )
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == DOC_NR)
    match = hit["matches"][0]
    assert match["before"] == [] and match["after"] == []
    assert match["text"], "맞은 줄 자신은 그대로 있다"


def test_search_does_not_invent_a_place_for_a_title_only_match(tmp_path):
    """본문에서 맞은 게 아니면 억지로 첫 줄을 가리키지 않는다(P0002 시나리오 10)."""
    resp = _get(
        f"/api/v1/search/documents/content?q={MARKER}&project={PROJECT}", _bearer(tmp_path)
    )
    hit = next(i for i in resp.json()["items"] if i["doc_id"] == DOC_T)
    assert hit["matched_in"] == "title"
    assert hit["matches"] == [] and hit["match_total"] == 0


def test_search_without_matches_is_byte_identical_to_the_old_response(tmp_path):
    """`include_matches=false` 는 두 키를 아예 뺀다 — 지금 화면은 고치지 않아도 된다."""
    raw = _bearer(tmp_path)
    client = _client()
    off = _get(
        f"/api/v1/search/documents/content?q={MARKER}&project={PROJECT}&include_matches=false",
        raw, client,
    ).json()
    on = _get(
        f"/api/v1/search/documents/content?q={MARKER}&project={PROJECT}", raw, client
    ).json()
    for item in off["items"]:
        assert "matches" not in item and "match_total" not in item
    stripped = [
        {k: v for k, v in i.items() if k not in ("matches", "match_total")}
        for i in on["items"]
    ]
    assert stripped == off["items"], "기존 키는 뜻도 값도 그대로다"
    assert (off["total"], off["scope"], off["turn_total"]) == (
        on["total"], on["scope"], on["turn_total"]
    )


def test_search_hits_per_doc_caps_the_returned_places(tmp_path, storage_root):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import content_search_service

    doc_id = "testprj-__ALL__-0370-N0008"
    rel = "documents/testprj/main/0370/0008-N_doc.md"
    needle = "repeatedneedle"
    _write_doc_file(storage_root, rel, "".join(f"{needle} 줄 {i}\n" for i in range(6)))
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "type_code": "N", "seq": 8,
        "title": "반복 문서", "group_id": GROUP, "module": MODULE,
        "owner_id": "usr_test_001", "status": "open", "file_path": rel, "revision_no": 0,
    })
    content_search_service.reset_cache()

    resp = _get(
        f"/api/v1/search/documents/content?q={needle}&project={PROJECT}&hits_per_doc=2",
        _bearer(tmp_path),
    )
    data = resp.json()
    assert data["hits_per_doc"] == 2
    hit = next(i for i in data["items"] if i["doc_id"] == doc_id)
    assert hit["match_total"] == 6, "match_total 은 문서 안에서 찾은 전체 개수다"
    assert len(hit["matches"]) == 2, "matches 는 그중 hits_per_doc 개까지"
    assert [m["locator"]["line_start"] for m in hit["matches"]] == [1, 2]


def test_search_out_of_range_knobs_are_clamped_and_echoed_honestly(tmp_path):
    resp = _get(
        f"/api/v1/search/documents/content?q={MARKER}&project={PROJECT}"
        f"&context_lines=99&hits_per_doc=99", _bearer(tmp_path),
    )
    data = resp.json()
    assert data["context_lines"] == 10 and data["hits_per_doc"] == 10, (
        "서버가 지키지도 않은 숫자를 되울리면 안 된다"
    )


# ── 시나리오 14~16: 저장 후 변경 요약 ────────────────────────────────────────────

def test_inbox_edit_response_carries_the_change_summary(tmp_path, storage_root):
    """진짜 inbox 수정 저장을 한 번 돌려 응답에 요약이 붙는지 본다.

    토큰 검증/소모만 모듈 경계에서 대역을 세운다(기존 0301 시험과 같은 이유 — 여기서
    재는 것은 Step 10 의 응답이지 토큰 표가 아니다).
    """
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import git_service, token_service
    from modules.flow_gate.storage import paths as storage_paths

    doc_id = "testprj-__ALL__-0370-TR0005"
    rel = "documents/testprj/main/0370/0005-TR_doc.md"
    before_body = "## 1. 개요\n\n옛 줄\n\n## 2. 사라질 것\n\n가\n"
    _write_doc_file(storage_root, rel, before_body)
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "type_code": "TR", "seq": 5,
        "title": "작업 레포트", "group_id": GROUP, "module": MODULE,
        "owner_id": "usr_test_001", "status": "open", "file_path": rel, "revision_no": 0,
    })

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    token_rec = {
        "token_id": "tok_test_0370", "project": PROJECT, "action_scope": "edit",
        "doc_ref": doc_id, "issued_to": "usr_test_001", "scratch_dir": str(scratch),
    }
    after_body = "## 1. 개요\n\n새 줄\n한 줄 더\n\n## 3. 새로 생길 것\n\n나\n"

    with patch("modules.flow_gate.rbac.permission_service.has_permission", return_value=True), \
            patch.object(token_service, "verify", return_value=token_rec), \
            patch.object(token_service, "consume", return_value=None), \
            patch.object(git_service, "worktree_untracked_summary", return_value={
                "total_count": 3, "excluded_artifact_count": 2,
                "staged_new_file_count": 1,
            }), \
            patch.object(
                inbox_routes.step_verification_service, "evaluate",
                return_value={"verdict": "pass", "codes": []},
            ):
        app = FastAPI()
        app.include_router(inbox_routes.router)
        resp = TestClient(app).post(
            "/api/v1/inbox",
            json={
                "project": PROJECT, "module": MODULE, "group": "0370",
                "action": "edit", "doc_id": doc_id, "edit_reason": "worker_self",
                "content": after_body,
            },
            headers={"Authorization": "Bearer dummy-token"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 기존 필드는 값까지 그대로다 — change_summary 는 그 옆에 나란히 붙는다.
    assert data["ok"] is True and data["revision_no"] == 1
    assert data["worktree_untracked"] == {
        "total_count": 3, "excluded_artifact_count": 2,
        "staged_new_file_count": 1,
    }
    summary = data["change_summary"]
    assert summary["changed"] is True
    assert summary["before"]["revision_no"] == 0 and summary["after"]["revision_no"] == 1
    assert summary["before"]["lines"] == 7 and summary["after"]["lines"] == 8
    # 옛 7줄 중 4줄이 그대로 남았다 → 3줄이 사라지고 8줄 중 4줄이 새로 생겼다.
    assert summary["lines_added"] == 4 and summary["lines_removed"] == 3
    assert [s["heading_path"] for s in summary["sections_added"]] == [["3. 새로 생길 것"]]
    assert [s["heading_path"] for s in summary["sections_removed"]] == [["2. 사라질 것"]]
    assert [s["heading_path"] for s in summary["sections_changed"]] == [["1. 개요"]]
    changed = summary["sections_changed"][0]
    assert (changed["lines_added"], changed["lines_removed"]) == (2, 1), (
        "구간 안에서만 다시 견주므로 문서 전체 숫자와 다르다"
    )
    assert summary["truncated"] is False
    # 요약은 응답에만 있다 — 표도 컬럼도 늘리지 않았다.
    assert "change_summary" not in (db_docs.get_by_id(doc_id) or {})


def test_inbox_edit_summary_failure_does_not_fail_the_save(tmp_path, storage_root):
    """요약이 죽어도 저장은 끝난 것이다 — 아니면 작업자가 같은 문서를 또 올린다."""
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import change_summary_service, token_service

    doc_id = "testprj-__ALL__-0370-TR0006"
    rel = "documents/testprj/main/0370/0006-TR_doc.md"
    _write_doc_file(storage_root, rel, "## A\n\n가\n")
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "type_code": "TR", "seq": 6,
        "title": "작업 레포트 2", "group_id": GROUP, "module": MODULE,
        "owner_id": "usr_test_001", "status": "open", "file_path": rel, "revision_no": 0,
    })
    scratch = tmp_path / "scratch2"
    scratch.mkdir(parents=True, exist_ok=True)
    token_rec = {
        "token_id": "tok_test_0370b", "project": PROJECT, "action_scope": "edit",
        "doc_ref": doc_id, "issued_to": "usr_test_001", "scratch_dir": str(scratch),
    }

    def _boom(**kwargs):
        raise RuntimeError("summary exploded")

    with patch("modules.flow_gate.rbac.permission_service.has_permission", return_value=True), \
            patch.object(token_service, "verify", return_value=token_rec), \
            patch.object(token_service, "consume", return_value=None), \
            patch.object(change_summary_service, "build", _boom), \
            patch.object(
                inbox_routes.step_verification_service, "evaluate",
                return_value={"verdict": "pass", "codes": []},
            ):
        app = FastAPI()
        app.include_router(inbox_routes.router)
        resp = TestClient(app).post(
            "/api/v1/inbox",
            json={
                "project": PROJECT, "module": MODULE, "group": "0370",
                "action": "edit", "doc_id": doc_id, "edit_reason": "worker_self",
                "content": "## A\n\n나\n",
            },
            headers={"Authorization": "Bearer dummy-token"},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["revision_no"] == 1, "저장은 성공한다"
    assert data["change_summary"] == {"changed": None, "error": "summary unavailable"}


def test_inbox_new_tr_response_carries_worktree_untracked(tmp_path):
    """action=new has its own response block; prove the positive TR contract there."""
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import git_service, token_service

    scratch = tmp_path / "scratch_new_tr"
    scratch.mkdir(parents=True, exist_ok=True)
    token_rec = {
        "token_id": "tok_test_0370_new_tr", "project": PROJECT, "action_scope": "new",
        "doc_ref": DOC_R, "issued_to": "usr_test_001", "scratch_dir": str(scratch),
    }
    content = "## 작업 결과\n\n신규 TR 응답 검증.\n\n## 변경 파일\n\n없음\n\n## 단계별 확인\n\n없음\n"
    expected = {
        "total_count": 5, "excluded_artifact_count": 3,
        "staged_new_file_count": 2,
    }

    with patch("modules.flow_gate.rbac.permission_service.has_permission", return_value=True), \
            patch.object(token_service, "verify", return_value=token_rec), \
            patch.object(token_service, "consume", return_value=None), \
            patch.object(git_service, "worktree_untracked_summary", return_value=expected):
        app = FastAPI()
        app.include_router(inbox_routes.router)
        resp = TestClient(app).post(
            "/api/v1/inbox",
            json={
                "project": PROJECT, "module": MODULE, "group_name": GROUP,
                "prev_doc_id": DOC_R, "action": "new", "doc_type": "TR",
                "title": "0370 신규 TR 미추적 요약 확인", "content": content,
            },
            headers={"Authorization": "Bearer dummy-token"},
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["worktree_untracked"] == expected

def test_inbox_new_response_carries_the_change_summary(tmp_path, storage_root):
    """진짜 inbox 신규 등록을 한 번 돌려 응답에 요약이 붙는지 본다(P0002 시나리오 15).

    신규 등록은 견줄 옛 판이 없으므로 ``before`` 는 ``null``, 문서 전체가 추가로
    잡혀야 한다. 줄 번호는 **저장된 파일** 기준이므로, 목차 조회가 말할 줄 수와
    같아야 한다 — 보낸 본문 그대로를 세면(머리말이 없으니 이 문서에서는 우연히도
    같은 값이 나오지만) 두 계산이 실제로 같은 함수를 타는지는 이 시험으로 못 박는다.
    """
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import token_service

    scratch = tmp_path / "scratch_new"
    scratch.mkdir(parents=True, exist_ok=True)
    token_rec = {
        "token_id": "tok_test_0370_new", "project": PROJECT, "action_scope": "new",
        "doc_ref": DOC_R, "issued_to": "usr_test_001", "scratch_dir": str(scratch),
    }
    content = "## 1. 새 구간\n\n0370 4세트 변경 요약 신규 등록 확인용 본문.\n\n## 2. 둘째 구간\n\n둘째 줄.\n"

    with patch("modules.flow_gate.rbac.permission_service.has_permission", return_value=True), \
            patch.object(token_service, "verify", return_value=token_rec), \
            patch.object(token_service, "consume", return_value=None):
        app = FastAPI()
        app.include_router(inbox_routes.router)
        resp = TestClient(app).post(
            "/api/v1/inbox",
            json={
                "project": PROJECT, "module": MODULE, "group_name": GROUP,
                "prev_doc_id": DOC_R, "action": "new", "doc_type": "N",
                "title": "0370 4세트 신규 등록 확인",
                "content": content,
            },
            headers={"Authorization": "Bearer dummy-token"},
        )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    summary = data["change_summary"]
    assert summary["changed"] is True and summary["before"] is None
    assert summary["after"]["lines"] == 7 and summary["after"]["section_total"] == 2
    assert summary["lines_added"] == 7 and summary["lines_removed"] == 0
    assert [s["heading_path"] for s in summary["sections_added"]] == [
        ["1. 새 구간"], ["2. 둘째 구간"],
    ]
    assert summary["sections_removed"] == [] and summary["sections_unchanged"] == 0
    assert "worktree_untracked" not in data, "N is not a source-mutating document type"
    assert f"@r0#L" in summary["sections_added"][0]["ref"]
    # 요약은 응답에만 있다 — 표도 컬럼도 늘리지 않았다.
    from modules.flow_gate.db import documents as db_docs
    assert "change_summary" not in (db_docs.get_by_id(data["doc_id"]) or {})
