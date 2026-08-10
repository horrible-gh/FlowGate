"""flowgate.default.0403 T0005 — NR0004 가 재현 조건까지 적어 둔 결함들의 회귀 테스트.

NR0004 §5 의 우선순위 순서를 그대로 따라간다.

  1. F1  저장의 파일 쓰기와 DB 리비전이 한 덩어리인가            (§3 F1 재현 조건)
  2. F2  부어 넣은 계획이 낡았을 때 저장이 멈추는가              (§3 F2 재현 조건)
  3. F3  부어 저장한 사실이 적용 이력에 남는가                    (§3 F3)
  4. F4  워크플로가 없어도 계획으로 첫 시퀀스를 만들 수 있는가    (§3 F4)
  5. F6  제안이 화면과 서버 가운데 하나의 리비전만 기준으로 삼는가 (§3 F6)
  6. F7  편집 잠금 판정이 서버 한 곳에서 나오는가                 (§3 F7)
  7. F8  제목 길이 오류가 문서 번호를 소비하지 않는가             (§3 F8)

앞의 네 개는 서비스 계층을, 뒤의 세 개는 HTTP 라우트를 직접 두드린다.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "wp403"
os.environ["FLOWGATE_TOKEN_PEPPER_wp403"] = "test-pepper-value-0403"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import work_plan_apply_service as wpa  # noqa: E402
from modules.flow_gate.services import work_plan_sequence_service as wpseq  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wds  # noqa: E402

PROJECT = "wp403prj"
GROUP = "wp403prj-__ALL__-0403"
ROOT_DOC = "wp403prj-__ALL__-0403-R0001"

# 서비스 계층 시험이 쓰는 이름들 (DB 없이 monkeypatch 로 배선한다).
OWNER_DOC_ID = "flowgate.default.0403.0001-B"
WP_DOC_ID = "flowgate.default.0403.0009-WP"
SEQUENCE = {"id": 403, "doc_id": OWNER_DOC_ID, "head_advanced_at": None}


# ══ 서비스 계층 — F2 · F3 · F4 ═══════════════════════════════════════════════

def _item(item_seq, type_, label, status, result=None):
    return {
        "id": item_seq, "item_seq": item_seq, "type": type_, "label": label,
        "status": status, "result_doc_id": result, "sort_order": item_seq,
        "note": "", "source_doc_id": None, "source_revision_no": None,
        "doc_class": "R",
    }


class _FakeStore:
    @contextmanager
    def transaction(self):
        yield


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """edit_workflow_pending 을 DB 대신 기록으로 돌린다. 시퀀스가 이미 있는 그룹."""
    inserted: list[dict] = []
    stored = [
        _item(1, "R", "요건정의", "done", result=OWNER_DOC_ID),
        _item(2, "WP", "작업계획", "done", result=WP_DOC_ID),
        _item(5, "P", "프로토콜설계", "pending"),
    ]
    plan_doc = {
        "doc_id": WP_DOC_ID, "type_code": "WP", "revision_no": 3,
        "project_id": "flowgate", "group_id": "flowgate.default.0403",
        "module": "default", "branch": "main",
        "doc_review_status": "approved",
    }

    def _current_items(seq_id):
        """저장 전에는 지금 있는 줄, 저장 뒤에는 방금 쓴 줄까지 — 적용 이력에 적히는
        '저장 뒤 지문'과 '이 계획이 만든 줄 번호'가 진짜 값이 되도록."""
        if not inserted:
            return [dict(r) for r in stored]
        locked = [dict(r) for r in stored if r["result_doc_id"] is not None]
        made = [{
            "id": k["item_seq"], "item_seq": k["item_seq"], "type": k["type_"],
            "label": k["label"], "status": "pending", "result_doc_id": None,
            "sort_order": k["sort_order"], "note": k["note"],
            "source_doc_id": k.get("source_doc_id"),
            "source_revision_no": k.get("source_revision_no"),
            "doc_class": k["doc_class"],
        } for k in inserted]
        return locked + made

    monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda doc_id: SEQUENCE)
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_items", _current_items)
    monkeypatch.setattr(wds.db_wfseq, "get_max_item_seq", lambda seq_id: 5)
    monkeypatch.setattr(wds.db_wfseq, "delete_pending_items", lambda seq_id: None)
    monkeypatch.setattr(wds.db_wfseq, "insert_sequence_item",
                        lambda **kwargs: inserted.append(kwargs))
    monkeypatch.setattr(wds, "get_store", lambda: _FakeStore())
    monkeypatch.setattr(wds.db_documents, "update", lambda *a, **k: None)
    monkeypatch.setattr(wds.db_documents, "list_documents", lambda **kwargs: [])

    def _get_by_id(doc_id):
        if doc_id == WP_DOC_ID:
            return dict(plan_doc)
        return {
            "doc_id": doc_id, "type_code": "B", "doc_review_status": "wf_in_progress",
            "project_id": "flowgate", "group_id": "flowgate.default.0403",
        }

    monkeypatch.setattr(wds.db_documents, "get_by_id", _get_by_id)

    # 적용 이력은 진짜 파일로 남긴다 — 남았는지를 파일에서 읽어 확인하기 위해서다.
    plan_path = tmp_path / "0009-WP_document.json"
    plan_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "modules.flow_gate.services.work_plan_service.plan_path_for_doc",
        lambda doc: plan_path,
    )
    return {"inserted": inserted, "stored": stored, "plan_doc": plan_doc,
            "plan_path": plan_path}


def test_f2_a_plan_that_moved_since_the_dialog_opened_stops_the_save(wired):
    """NR0004 F2 재현 조건: 워크플로 지문은 그대로인데 계획만 리비전이 올랐다."""
    with pytest.raises(wds.PlanRevisionChanged) as exc:
        wds.edit_workflow_pending(
            OWNER_DOC_ID,
            [{"type": "P", "label": "프로토콜설계", "note": "계획에서 온 줄",
              "source_doc_id": WP_DOC_ID, "source_revision_no": 2}],
            expected_plan={"wp_doc_id": WP_DOC_ID, "wp_revision_no": 2},
        )
    assert exc.value.expected == 2
    assert exc.value.current == 3
    # 한 줄도 쓰이지 않았다.
    assert wired["inserted"] == []


def test_f2_the_same_plan_revision_lets_the_save_through(wired):
    wds.edit_workflow_pending(
        OWNER_DOC_ID,
        [{"type": "P", "label": "프로토콜설계", "note": "계획에서 온 줄",
          "source_doc_id": WP_DOC_ID, "source_revision_no": 3}],
        expected_plan={"wp_doc_id": WP_DOC_ID, "wp_revision_no": 3, "mode": "append"},
    )
    assert [row["type_"] for row in wired["inserted"]] == ["P"]


def test_f2_an_ordinary_edit_carrying_an_old_source_is_not_judged_by_it(wired):
    """이미 부어 저장된 줄은 그 출처를 계속 달고 다닌다. 그 낡은 번호로 평범한
    [시퀀스 수정]을 막으면, 한 번 부은 그룹은 두 번 다시 시퀀스를 못 고친다."""
    wds.edit_workflow_pending(
        OWNER_DOC_ID,
        [{"type": "P", "label": "프로토콜설계", "note": "예전에 부어 둔 줄",
          "source_doc_id": WP_DOC_ID, "source_revision_no": 1}],
    )
    assert [row["type_"] for row in wired["inserted"]] == ["P"]


def test_f3_a_poured_save_is_written_into_the_plans_application_journal(wired):
    result = wds.edit_workflow_pending(
        OWNER_DOC_ID,
        [{"type": "T", "label": "작업지시", "note": "테스트 포함",
          "source_doc_id": WP_DOC_ID, "source_revision_no": 3}],
        expected_plan={"wp_doc_id": WP_DOC_ID, "wp_revision_no": 3, "mode": "replace_after"},
        applied_by="usr_403",
    )
    assert result["application_recorded"] is True

    journal = wpa.read_applications(wired["plan_path"], WP_DOC_ID)
    assert journal["total"] == 1
    row = journal["items"][0]
    assert row["applied_by"] == "usr_403"
    assert row["wp_revision_no"] == 3
    assert row["via"] == "sequence_edit"
    assert row["mode"] == "replace_after"
    assert row["workflow_doc_id"] == OWNER_DOC_ID
    assert row["workflow_tag_before"] != row["workflow_tag_after"]
    # 이 계획이 만든 줄이 어느 자리인지까지 남는다.
    assert row["poured_item_seqs"] == [6]


def test_f3_an_ordinary_edit_writes_no_application_row(wired):
    result = wds.edit_workflow_pending(OWNER_DOC_ID, [{"type": "P", "label": "프로토콜설계"}])
    assert "application_recorded" not in result
    assert wpa.read_applications(wired["plan_path"], WP_DOC_ID)["total"] == 0


@pytest.fixture
def wired_without_sequence(wired, monkeypatch):
    """같은 배선인데 이 그룹에는 아직 워크플로가 없다 (NR0004 F4)."""
    created: list[str] = []
    sequence: dict = {}

    def _get_sequence(doc_id):
        return SEQUENCE if sequence.get("made") else None

    def _insert_sequence(doc_id):
        created.append(doc_id)
        sequence["made"] = True

    def _items(seq_id):
        return [{
            "id": k["item_seq"], "item_seq": k["item_seq"], "type": k["type_"],
            "label": k["label"], "status": "pending", "result_doc_id": None,
            "sort_order": k["sort_order"], "note": k["note"],
            "source_doc_id": k.get("source_doc_id"),
            "source_revision_no": k.get("source_revision_no"),
            "doc_class": k["doc_class"],
        } for k in wired["inserted"]]

    monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", _get_sequence)
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_items", _items)
    monkeypatch.setattr(wds.db_wfseq, "insert_sequence", _insert_sequence)
    wired["created"] = created
    return wired


def test_f4_an_undecided_workflow_still_refuses_an_ordinary_edit(wired_without_sequence):
    with pytest.raises(ValueError) as exc:
        wds.edit_workflow_pending(OWNER_DOC_ID, [{"type": "P", "label": "프로토콜설계"}])
    assert str(exc.value).startswith("sequence_not_decided:")
    assert wired_without_sequence["created"] == []


def test_f4_pouring_a_plan_builds_the_first_sequence(wired_without_sequence):
    """NR0004 F4: 후보 생성기는 시퀀스가 없어도 계획 행을 만들어 주는데, 저장하는 쪽이
    곧바로 거절해서 "계획을 세워 두고 그걸로 워크플로를 만든다"가 막혀 있었다."""
    result = wds.edit_workflow_pending(
        OWNER_DOC_ID,
        [{"type": "N", "label": "조사지시", "note": "먼저 조사",
          "source_doc_id": WP_DOC_ID, "source_revision_no": 3}],
        expected_workflow_tag="none",          # 시퀀스가 없을 때의 지문
        expected_plan={"wp_doc_id": WP_DOC_ID, "wp_revision_no": 3, "mode": "append"},
        applied_by="usr_403",
    )
    assert result["sequence_created"] is True
    assert wired_without_sequence["created"] == [OWNER_DOC_ID]
    # 지시 줄에는 서버가 레포트 줄을 붙인다 — 결정 경로와 같다.
    assert [row["type_"] for row in wired_without_sequence["inserted"]] == ["N", "NR"]
    # 그리고 그 사실이 계획의 적용 이력에도 남는다.
    journal = wpa.read_applications(wired_without_sequence["plan_path"], WP_DOC_ID)
    assert journal["items"][0]["sequence_created"] is True


def test_f4_an_empty_pour_does_not_create_a_zombie_sequence(wired_without_sequence):
    with pytest.raises(ValueError) as exc:
        wds.edit_workflow_pending(
            OWNER_DOC_ID, [],
            expected_plan={"wp_doc_id": WP_DOC_ID, "wp_revision_no": 3},
        )
    assert str(exc.value).startswith("invalid_sequence_empty:")
    assert wired_without_sequence["created"] == []


def test_f2_the_candidate_response_carries_the_plan_revision(monkeypatch):
    """저장할 때 되돌려 보낼 값이므로, 후보 응답에 실려 있어야 한다."""
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id", lambda doc_id: None)
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_items", lambda seq_id: [])
    monkeypatch.setattr(wpseq.db_wfseq, "get_item_by_result_doc_id", lambda doc_id: None)
    out = wpseq.build_candidates(
        doc={"doc_id": WP_DOC_ID, "target_id": OWNER_DOC_ID, "revision_no": 7},
        plan={"steps": [{"key": "D#1", "type": "D", "note": "설계"}]},
        mode="append",
    )
    assert out["wp_revision_no"] == 7
    assert out["workflow_tag"] == "none"


# ══ HTTP 라우트 — F1 · F6 · F7 · F8 ══════════════════════════════════════════

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
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        db_path = fh.name
    mock = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock._conn.commit()
    yield mock
    mock.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    """get_store 가 아니라 STORE 객체를 갈아끼운다 — 함수를 패치하면 뒤에 import 되는
    모듈로 새어 "혼자 돌리면 통과, 같이 돌리면 실패"가 된다."""
    from modules.flow_gate.db import connection as conn_mod

    original = conn_mod.STORE
    prev_pepper = os.environ.get("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID")
    os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "wp403"

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = tmp_db
            self._sq = None

        def _sql(self, key: str) -> str:
            raise NotImplementedError

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original
    if prev_pepper is None:
        os.environ.pop("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", None)
    else:
        os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = prev_pepper


@pytest.fixture(scope="module", autouse=True)
def storage_root(tmp_path_factory, patch_store):
    root = tmp_path_factory.mktemp("wp403_storage")
    prev = os.environ.get("FLOWGATE_STORAGE_DIR")
    os.environ["FLOWGATE_STORAGE_DIR"] = str(root)
    yield root
    if prev is None:
        os.environ.pop("FLOWGATE_STORAGE_DIR", None)
    else:
        os.environ["FLOWGATE_STORAGE_DIR"] = prev


@pytest.fixture(scope="module")
def seed(tmp_db, patch_store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.rbac import permission_service

    projects.create({"project_id": PROJECT, "project_name": "WP 0403 Test"})
    users.create({
        "user_id": "usr_wp_403", "username": "wp403user",
        "email": "wp403@example.com", "password": "hashed",
    })
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_wp403", "WP 0403 Worker", now, now],
    )
    for perm in (
        "document.create", "document.read", "document.update",
        "perm_document_create", "perm_document_read", "perm_document_update",
    ):
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
            [perm, perm, now],
        )
        store._execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
            ["role_wp403", perm],
        )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        ["usr_wp_403", PROJECT, "role_wp403", now],
    )
    permission_service.clear_all_cache()
    db_groups.create({
        "group_id": GROUP, "project_id": PROJECT, "module": "__ALL__", "title": "WP 0403 Group",
    })
    db_docs.create({
        "doc_id": ROOT_DOC, "project_id": PROJECT, "type_code": "R", "seq": 1,
        "title": "Root Requirement", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_403",
    })
    yield


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers.work_plan import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_wp_403"}
    return TestClient(app, raise_server_exceptions=False)


def _create_plan(client, doc_code: str, title: str = "0403 작업계획"):
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value=doc_code,
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": title,
            "counted_types": ["D", "T"],
            "provider_candidates": ["aip_opus"],
        })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_f1_the_request_that_loses_the_revision_never_writes_the_canonical_file(
    seed, storage_root, monkeypatch,
):
    """NR0004 F1 재현 조건 — 두 요청이 같은 리비전을 읽고 초기 검사를 나란히 통과한다.

    저장 A 가 끝난 뒤에도, 저장 B 는 자기가 창을 열 때 읽어 둔 revision_no=0 을 들고 온다.
    고치기 전에는 B 가 그 낡은 값으로 검사를 통과해 정본 파일을 덮어쓴 다음에야 DB 경쟁에서
    져서 409 를 받았다 — 화면에는 "저장 실패", 디스크에는 B 의 본문.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import work_plan as route
    from modules.flow_gate.services import work_plan_service as wp

    client = _client()
    created = _create_plan(client, "0002-WP")
    doc_id = created["doc_id"]
    stored = created["stored_path"]

    body_a = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()["body"]
    body_a["defaults"]["note"] = "A 가 저장한 본문"
    saved = client.put(f"/api/v1/documents/{doc_id}/work-plan",
                       json={"base_revision_no": 0, "body": body_a})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision_no"] == 1
    on_disk_after_a = json.loads((storage_root / stored).read_text(encoding="utf-8"))
    assert on_disk_after_a["defaults"]["note"] == "A 가 저장한 본문"

    # B 의 요청. 첫 조회만 "창을 열 때 본 값"(리비전 0)을 돌려준다.
    real_get_by_id = db_docs.get_by_id
    seen: dict = {"first": True}

    def _stale_first(target_id):
        row = real_get_by_id(target_id)
        if row is not None and target_id == doc_id and seen["first"]:
            seen["first"] = False
            row = dict(row)
            row["revision_no"] = 0
        return row

    monkeypatch.setattr(route.db_docs, "get_by_id", _stale_first)

    writes: list = []
    real_write = wp.write_body_atomically

    def _recording_write(path, plan_body):
        writes.append(plan_body)
        real_write(path, plan_body)

    monkeypatch.setattr(route.wp, "write_body_atomically", _recording_write)

    body_b = json.loads(json.dumps(body_a))
    body_b["defaults"]["note"] = "B 가 덮어쓰려던 본문"
    conflict = client.put(f"/api/v1/documents/{doc_id}/work-plan",
                          json={"base_revision_no": 0, "body": body_b})

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "wp_revision_conflict"
    assert conflict.json()["current_revision_no"] == 1
    # 진 요청은 파일에 손대지 않는다 — 이것이 이 결함의 본체다.
    assert writes == []
    assert json.loads((storage_root / stored).read_text(encoding="utf-8")) == on_disk_after_a
    assert db_docs.get_by_id(doc_id)["revision_no"] == 1


def test_f6_a_suggestion_asked_against_a_stale_revision_is_refused(seed):
    """NR0004 F6: 제안 라우트는 base_revision_no 를 받아 놓고 한 번도 보지 않았다."""
    client = _client()
    created = _create_plan(client, "0003-WP")
    doc_id = created["doc_id"]

    ok = client.post(f"/api/v1/documents/{doc_id}/work-plan/suggest",
                     json={"base_revision_no": 0, "scope": {"step_keys": [], "provider_ids": []}})
    assert ok.status_code == 200, ok.text

    stale = client.post(f"/api/v1/documents/{doc_id}/work-plan/suggest",
                        json={"base_revision_no": 9, "scope": {"step_keys": []}})
    assert stale.status_code == 409
    assert stale.json()["code"] == "wp_revision_conflict"
    assert stale.json()["current_revision_no"] == 0

    # 리비전을 말하지 않은 호출은 예전처럼 그대로 통과한다.
    silent = client.post(f"/api/v1/documents/{doc_id}/work-plan/suggest", json={})
    assert silent.status_code == 200, silent.text


def test_f7_the_read_view_states_whether_the_plan_can_be_edited(seed):
    """NR0004 F7: 화면이 승인 상태만 보고 혼자 잠그던 것을 서버 판정으로 바꿨다."""
    from modules.flow_gate.db import documents as db_docs

    client = _client()
    created = _create_plan(client, "0004-WP")
    doc_id = created["doc_id"]

    view = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()
    assert view["editable"] is True
    assert view["edit_locked_reason"] is None
    # 승인만으로는 잠기지 않는다 — 서버가 저장을 받아 주는 상태와 화면이 같아야 한다.
    db_docs.update(doc_id, {"doc_review_status": "approved"})
    approved = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()
    assert approved["doc_review_status"] == "approved"
    assert approved["editable"] is True

    # 그룹 최종 승인이 끝나면 잠긴다 — 저장 라우트가 거절하는 바로 그 조건이다.
    db_docs.update(ROOT_DOC, {"doc_review_status": "wf_done"})
    try:
        locked = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()
        assert locked["editable"] is False
        assert locked["edit_locked_reason"] == "final_approved"
        body = locked["body"]
        refused = client.put(f"/api/v1/documents/{doc_id}/work-plan",
                             json={"base_revision_no": locked["revision_no"], "body": body})
        assert refused.status_code == 422
    finally:
        db_docs.update(ROOT_DOC, {"doc_review_status": None})


def test_f7_the_read_view_reports_where_a_plan_was_last_applied(seed, storage_root):
    """F3 의 화면 쪽 절반: 이력이 있으면 마지막 한 건을 읽기 응답에 실어 준다."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import work_plan_service as wp

    client = _client()
    created = _create_plan(client, "0005-WP")
    doc_id = created["doc_id"]

    assert client.get(f"/api/v1/documents/{doc_id}/work-plan").json()["last_application"] is None

    wpa.append_application(
        wp.plan_path_for_doc(db_docs.get_by_id(doc_id)), doc_id,
        {"applied_at": "2026-08-10T18:00:00+09:00", "applied_by": "usr_wp_403",
         "wp_revision_no": 0, "via": "sequence_edit"},
    )
    view = client.get(f"/api/v1/documents/{doc_id}/work-plan").json()
    assert view["last_application"]["applied_by"] == "usr_wp_403"
    assert view["last_application"]["via"] == "sequence_edit"


def test_f8_a_title_that_is_too_long_does_not_consume_a_document_number(seed):
    """NR0004 F8: 번호를 먼저 예약하고 제목을 나중에 검사하면 번호에 구멍이 남는다."""
    client = _client()
    calls: list = []

    def _reserve(**kwargs):
        calls.append(kwargs)
        return "0006-WP"

    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        side_effect=_reserve,
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "가" * 101,
            "counted_types": ["D"],
            "provider_candidates": ["aip_opus"],
        })
    assert resp.status_code == 422
    assert "100 characters" in resp.text
    assert calls == []
