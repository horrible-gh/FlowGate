"""Work plan (WP) — flowgate.default.0395 T0011.

Covers the three things this task set promised: the SAME validator on both paths, a
`.json` canonical body with its change summary, and a WP that does not auto-complete.

  1. expansion + canonical form            (L0010 §2.1, P0009 §2.6)
  2. validation verdicts                    (L0010 §2.3 layers, P0009 §4.7)
  3. human API create → read → save         (P0009 §4.2 · §4.4 · §4.6 · §4.8)
  4. AI inbox create                        (P0009 §5.1 · §5.2 · §5.3)
  5. type registration and the audited sets (D0007 §7)
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
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "wp395"
os.environ["FLOWGATE_TOKEN_PEPPER_wp395"] = "test-pepper-value-0395"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

PROJECT = "wpprj"
GROUP = "wpprj-__ALL__-0402"
ROOT_DOC = "wpprj-__ALL__-0402-R0001"


# ── Part 1: expansion and canonical form (no DB) ─────────────────────────────

def _plan(**over):
    from modules.flow_gate.services import work_plan_service as wp

    counted = over.pop("counted_types", ["DS", "D", "P", "L", "DB", "N", "T", "TS"])
    counts = over.pop("counts", {"DS": 1, "D": 2, "P": 1, "L": 1, "DB": 0, "N": 1, "T": 3, "TS": 1})
    quantities = {
        code: {"unit": wp.WORK_PLAN_TYPE_UNITS[code], "count": counts[code]}
        for code in counted
    }
    body = {
        "wp_version": 1,
        "binding": "advisory",
        "counted_types": counted,
        "quantities": quantities,
        "provider_candidates": [
            {"provider_id": "aip_opus", "display_name": "Claude Opus", "group_label": "Claude · CLI"},
            {"provider_id": "aip_sonnet", "display_name": "Claude Sonnet", "group_label": "Claude · CLI"},
        ],
        "defaults": {"provider_id": None, "note": ""},
        "steps": wp.expand_steps(counted, quantities),
    }
    body.update(over)
    return body


def test_expand_steps_matches_the_designed_order():
    """P0009 §2.6: DS · D · D · P · L · N · NR · T · TR ×3 · TS · TSR — 15 steps."""
    from modules.flow_gate.services import work_plan_service as wp

    steps = _plan()["steps"]
    assert [s["key"] for s in steps] == [
        "DS#1", "D#1", "D#2", "P#1", "L#1",
        "N#1", "NR#1",
        "T#1", "TR#1", "T#2", "TR#2", "T#3", "TR#3",
        "TS#1", "TSR#1",
    ]
    # A count of 0 expands to nothing but keeps its quantity box (P0009 §2.2).
    assert all(not s["key"].startswith("DB#") for s in steps)
    assert wp.totals(_plan())["steps"] == 15


def test_set_steps_are_paired_and_tsr_is_locked():
    steps = {s["key"]: s for s in _plan()["steps"]}
    assert steps["T#2"]["pair_key"] == "TR#2"
    assert steps["T#2"]["pair_role"] == "instruction"
    assert steps["TR#2"]["pair_key"] == "T#2"
    assert steps["TR#2"]["pair_role"] == "result"
    assert steps["DS#1"]["pair_key"] is None
    assert steps["DS#1"]["pair_role"] == "single"
    # DS0006 §2-7: the server assembles TSR, so it can never carry an assignment.
    assert steps["TSR#1"]["locked"] is True
    assert steps["TSR#1"]["locked_reason"] == "server_assembled"
    assert steps["TSR#1"]["origin"] == "system"
    assert steps["TS#1"]["locked"] is False


def test_canonical_dump_is_stable():
    """P0009 §2.6 결정 3·4: fixed key order, 2-space indent, one trailing newline."""
    from modules.flow_gate.services import work_plan_service as wp

    text = wp.dumps(wp.validate(_plan()))
    assert text.endswith("}\n")
    assert not text.endswith("}\n\n")
    parsed = json.loads(text)
    assert list(parsed) == [
        "wp_version", "binding", "counted_types", "quantities",
        "provider_candidates", "defaults", "steps",
    ]
    assert list(parsed["steps"][0]) == list(wp.STEP_FIELD_ORDER)
    assert '\n  "binding": "advisory"' in text
    # Same input, same bytes — a revision diff must only show real changes.
    assert wp.dumps(wp.validate(json.loads(text))) == text


def test_future_x_fields_are_preserved_and_typos_are_not():
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan()
    body["x_experiment"] = {"a": 1}
    body["steps"][0]["x_hint"] = "keep me"
    saved = wp.validate(body)
    assert saved["x_experiment"] == {"a": 1}
    assert saved["steps"][0]["x_hint"] == "keep me"

    body = _plan()
    body["steps"][0]["provider_ids"] = ["aip_opus"]
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert [e["code"] for e in wp.render_errors(exc.value.errors, "ko")] == ["unknown_field"]


# ── Part 2: validation verdicts ──────────────────────────────────────────────

def _codes(exc):
    from modules.flow_gate.services import work_plan_service as wp

    return [e["code"] for e in wp.render_errors(exc.errors, "ko")]


def test_locked_step_rejects_provider_and_note():
    """P0009 §4.7: the screen locks the row, and the server refuses it too."""
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan()
    tsr = [s for s in body["steps"] if s["key"] == "TSR#1"][0]
    tsr["provider_id"] = "aip_opus"
    tsr["provider_display_name"] = "Claude Opus"
    tsr["note"] = "레포트 정리"
    tsr["origin"] = "human"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    codes = _codes(exc.value)
    assert "provider_not_allowed" in codes
    assert "note_not_allowed" in codes
    assert "origin_not_allowed" in codes
    keys = {e["key"] for e in wp.render_errors(exc.value.errors, "ko")}
    assert keys == {"TSR#1"}


def test_provider_must_be_a_candidate():
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan()
    body["steps"][1]["provider_id"] = "aip_gemini"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc.value) == ["provider_not_candidate"]


def test_duplicate_key_stops_before_the_quantity_comparison():
    """L0010 §2.3 결정 5: one mistake must not be reported as two."""
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan()
    body["steps"].append(dict(body["steps"][1]))
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc.value) == ["duplicate_key"]
    assert "steps_quantity_mismatch" not in _codes(exc.value)


@pytest.mark.parametrize(
    "count,expected",
    [(0, None), (20, None), (21, "count_out_of_range"), (-1, "count_out_of_range"),
     ("2", "count_not_integer"), (2.0, "count_not_integer")],
)
def test_count_boundaries(count, expected):
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan(counted_types=["D"], counts={"D": 1})
    body["quantities"]["D"]["count"] = count
    if expected is None:
        body["steps"] = wp.expand_steps(["D"], body["quantities"])
        assert len(wp.validate(body)["steps"]) == count
        return
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc.value) == [expected]


def test_note_limits():
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan(counted_types=["D"], counts={"D": 1})
    body["steps"][0]["note"] = "가" * wp.NOTE_MAX_CHARS
    wp.validate(body)  # exactly at the limit is fine

    body["steps"][0]["note"] = "가" * (wp.NOTE_MAX_CHARS + 1)
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc.value) == ["note_too_long"]

    # A newline is refused, not silently swallowed — an AI must not believe its
    # body was stored verbatim when it was not (L0010 §1.5).
    body["steps"][0]["note"] = "가\n나"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc.value) == ["note_has_control_char"]


def test_binding_and_version_gates():
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan()
    body["binding"] = "mandatory"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert "binding_not_allowed" in _codes(exc.value)

    body = _plan()
    body["wp_version"] = 2
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc.value) == ["wp_version_unsupported"]


def test_steps_must_match_quantities():
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan(counted_types=["T"], counts={"T": 2})
    body["steps"] = body["steps"][:2]
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    assert _codes(exc.value) == ["steps_quantity_mismatch"]


def test_error_messages_follow_the_locale():
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan()
    body["steps"][1]["provider_id"] = "aip_gemini"
    with pytest.raises(wp.WorkPlanValidationError) as exc:
        wp.validate(body)
    for locale, needle in (("ko", "후보"), ("en", "candidates"), ("ja", "候補")):
        payload = wp.error_response(exc.value, locale)
        assert payload["code"] == "wp_validation_failed"
        assert needle in payload["errors"][0]["msg"]


def test_inbox_message_says_where_the_json_broke():
    """P0009 §5.2: "잘못된 형식입니다" alone makes an unattended worker repeat itself."""
    from modules.flow_gate.services import work_plan_service as wp

    raw = "# 작업계획\n\n| 타입 | 장수 |\n"
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        message = wp.inbox_not_json_message(raw, exc, "ko")
    assert "JSON" in message
    assert "1행 1열" in message
    assert "'#'" in message
    assert "design_template/WP" in message


# ── Part 3: derived views and change summary ─────────────────────────────────

def test_assignment_summary_uses_the_current_provider_name():
    from modules.flow_gate.services import work_plan_service as wp

    body = _plan()
    body["steps"][1]["provider_id"] = "aip_sonnet"
    body["steps"][1]["provider_display_name"] = "Claude Sonnet"
    providers = [{"id": "aip_sonnet", "name": "Claude Sonnet 5", "kind": "claude", "exec_type": "cli"}]
    summary = wp.assignment_summary(body, providers)
    assert summary == [{"provider_id": "aip_sonnet", "display_name": "Claude Sonnet 5", "step_count": 1}]

    status = {s["provider_id"]: s for s in wp.provider_status(body, providers)}
    assert status["aip_sonnet"]["name_changed"] is True
    assert status["aip_opus"]["registered"] is False


def test_change_summary_reports_what_changed():
    from modules.flow_gate.services import work_plan_service as wp

    before = wp.validate(_plan())
    after_body = _plan(counts={"DS": 1, "D": 2, "P": 1, "L": 1, "DB": 0, "N": 1, "T": 4, "TS": 1})
    after_body["steps"][1]["provider_id"] = "aip_opus"
    after_body["steps"][1]["provider_display_name"] = "Claude Opus"
    after = wp.validate(after_body)

    created = wp.change_summary(after)
    assert created["kind"] == "work_plan"
    assert "T 4세트" in created["quantities"]
    assert created["steps"] == 17
    # P0009 §5.1 · §5.4: 배정 + 미배정 = 단계 수. 잠긴 TSR 을 어느 쪽에도 넣지
    # 않으면 무인 작업자가 자기가 보낸 것이 다 저장됐는지 셈으로 확인할 수 없다.
    assert created["assigned_steps"] + created["unassigned_steps"] == created["steps"]
    assert created["unassigned_steps"] == wp.unassigned_step_count(after)

    edited = wp.change_summary(after, before)
    assert "quantities.T.count 3 → 4" in edited["changed"]
    assert "steps 15 → 17" in edited["changed"]
    assert any("D#1].provider_id" in line for line in edited["changed"])


def test_template_is_json_and_valid():
    """P0009 §6: an AI can send the template back unchanged and be accepted."""
    from modules.flow_gate.services import work_plan_service as wp

    payload = wp.template_payload("ko")
    assert payload["body_format"] == "json"
    body = json.loads(payload["body"])
    assert wp.validate(body)["binding"] == "advisory"
    assert any("advisory" in rule for rule in payload["rules"])


# ── Part 4: type registration and the audited type sets (D0007 §7) ───────────

def test_work_plan_is_not_auto_complete():
    from modules.flow_gate.documents.constants import (
        AUTO_COMPLETE_TYPES,
        FILELESS_APPROVABLE_TYPES,
        HEAD_TYPE_GUARD_EXEMPT_TYPES,
        NON_SLOT_WORKFLOW_TYPES,
        WORK_PLAN_TYPE,
    )

    # 결정 4: it is created pending_review and reviewed like any other document.
    assert WORK_PLAN_TYPE not in AUTO_COMPLETE_TYPES
    # It has a real body file, so it is not a file-less approval either.
    assert WORK_PLAN_TYPE not in FILELESS_APPROVABLE_TYPES
    # 결정 5: several plans per group, at any point — so not pinned to the head type.
    assert WORK_PLAN_TYPE in HEAD_TYPE_GUARD_EXEMPT_TYPES
    # D0007 §7 은 작업계획이 워크플로 칸을 차지하는지 "명시"하라고 했다. 같은 문서가
    # 그것을 "요건정의 다음에 오는 일반 칸"으로 정했으므로 칸을 차지한다 — 머리 타입
    # 검사에서 빠지는 것과 칸을 갖지 않는 것은 다른 이야기다.
    assert WORK_PLAN_TYPE not in NON_SLOT_WORKFLOW_TYPES


def test_work_plan_reaches_the_review_and_linter_type_sets():
    from modules.flow_gate import linter, process_service

    assert "WP" in linter.VALID_TYPES
    assert "WP" in process_service.REVIEW_REQUEST_TYPES
    assert "WP" in process_service.APPROVABLE_TYPES
    assert process_service.TYPE_ACTIONS["WP"] == ["review_request", "approve", "reject"]


def test_type_annotation_marks_countable_types():
    from modules.flow_gate.services import work_plan_service as wp

    rows = wp.annotate_types([
        {"type_code": "D", "series": "design"},
        {"type_code": "T", "series": "instruction", "type_name": "작업지시"},
        {"type_code": "L", "series": "general"},   # the LOG type, not the logic sheet
        {"type_code": "WP", "series": "general"},
        {"type_code": "TR", "series": "work", "type_name": "작업레포트"},
    ])
    by_code = {r["type_code"]: r for r in rows}
    assert by_code["D"]["countable"] is True and by_code["D"]["unit"] == "sheet"
    assert by_code["T"]["countable"] is True and by_code["T"]["unit"] == "set"
    assert by_code["T"]["pair_code"] == "TR"
    # P0009 §4.1: 짝의 이름은 같은 응답 안의 행에서 가져오므로 로케일이 저절로 맞는다.
    assert by_code["T"]["pair_name"] == "작업레포트"
    # 짝 행이 목록에 없으면 항목 자체를 만들지 않는다 — 코드를 이름인 척 보이지 않는다.
    lone = wp.annotate_types([{"type_code": "N", "series": "instruction"}])[0]
    assert lone["pair_code"] == "NR" and "pair_name" not in lone
    assert by_code["L"]["countable"] is False   # same letter, different type
    assert by_code["WP"]["countable"] is False  # a plan never counts itself
    assert by_code["TR"]["countable"] is False  # the result half is not counted


def test_all_three_dialects_seed_the_type():
    """D0007 §7: fixing one branch and not the others leaves a DB without the type."""
    roots = _SERVER_DIR / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        # Not named `path`: the repo-scratch guard (0382) tracks names transitively
        # derived from __file__ across the whole file, so reusing that name here would
        # make an unrelated `path.unlink()` on a tmp file below read as a repo write.
        sql_path = roots / dialect / "078b_seed_work_plan_doctype.sql"
        assert sql_path.is_file(), f"missing {dialect} migration"
        sql = sql_path.read_text(encoding="utf-8")
        assert "'WP'" in sql and "작업계획" in sql
        assert "'general'" in sql


# ── Part 5: DB-backed paths ──────────────────────────────────────────────────

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
    """Swap the STORE object, not get_store — patching the function leaks into modules
    imported later in the same session (a "passes alone, fails together" trap)."""
    from modules.flow_gate.db import connection as conn_mod

    original = conn_mod.STORE
    prev_pepper = os.environ.get("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID")
    os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "wp395"

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
    root = tmp_path_factory.mktemp("wp_storage")
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

    projects.create({"project_id": PROJECT, "project_name": "WP Test"})
    users.create({
        "user_id": "usr_wp_001", "username": "wpuser",
        "email": "wp@example.com", "password": "hashed",
    })
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_wp", "WP Worker", now, now],
    )
    # The inbox gate asks for the `perm_*` ids, so those are the ones granted here and
    # the gate is left to run for real. Mocking has_permission instead only worked while
    # this file was the first to import inbox_routes — once another module imported it
    # earlier, the patched name was no longer the one the route had bound, and the mock
    # silently stopped covering the very check it was hiding.
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
            ["role_wp", perm],
        )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        ["usr_wp_001", PROJECT, "role_wp", now],
    )
    # The permission set is cached for 30 minutes per (user, project); drop it so this
    # module's first check reads the rows just inserted rather than an older empty set.
    from modules.flow_gate.rbac import permission_service

    permission_service.clear_all_cache()
    db_groups.create({
        "group_id": GROUP, "project_id": PROJECT, "module": "__ALL__", "title": "WP Group",
    })
    db_docs.create({
        "doc_id": ROOT_DOC, "project_id": PROJECT, "type_code": "R", "seq": 1,
        "title": "Root Requirement", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001",
    })
    yield


def _client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers.work_plan import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_wp_001"}
    return TestClient(app, raise_server_exceptions=False)


def test_human_create_read_save_roundtrip(seed, storage_root):
    from modules.flow_gate.db import documents as db_docs

    client = _client()
    parent_before = dict(db_docs.get_by_id(ROOT_DOC))
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0002-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0402 작업계획",
            "counted_types": ["DS", "D", "P", "L", "N", "T", "TS"],
            "provider_candidates": ["aip_opus"],
            # flowgate.default.0423 T0005 item 15: this roundtrip test needs real steps
            # to exercise save/conflict/reject below, so it sends explicit quantities —
            # a request that omits them now defaults to 0, not 1 (see
            # test_human_create_legacy_request_defaults_unspecified_types_to_zero).
            "quantities": {code: 1 for code in ["DS", "D", "P", "L", "N", "T", "TS"]},
        })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    doc_id = created["doc_id"]

    # 결정 2: an explicit quantity in the request is honored as-is.
    assert created["body"]["quantities"]["D"]["count"] == 1
    assert created["body"]["binding"] == "advisory"
    # 결정 2 (P0009 §2.6): the canonical body is a .json file, never .md.
    stored = created["stored_path"]
    assert stored.endswith("_document.json"), stored
    assert (storage_root / stored).is_file()
    assert json.loads((storage_root / stored).read_text(encoding="utf-8"))["wp_version"] == 1

    # 결정 4: pending review, and the parent is left exactly as it was.
    row = db_docs.get_by_id(doc_id)
    assert row["doc_review_status"] == "pending_review"
    # §4.2: the parent's state is left exactly as it was — creating a plan is not
    # progress on the requirement it hangs from.
    parent_after = dict(db_docs.get_by_id(ROOT_DOC))
    assert parent_after["status"] == parent_before["status"]
    assert parent_after["doc_review_status"] == parent_before["doc_review_status"]

    read = client.get(f"/api/v1/documents/{doc_id}/work-plan")
    assert read.status_code == 200, read.text
    view = read.json()
    assert view["revision_no"] == 0
    assert view["origin"] == "human"
    assert view["totals"]["steps"] == len(view["body"]["steps"])
    assert view["unassigned_step_count"] == view["totals"]["steps"] - 1  # TSR is locked

    # Save: assign one provider, then check the revision moved and the file changed.
    body = view["body"]
    body["steps"][0]["provider_id"] = "aip_opus"
    body["steps"][0]["provider_display_name"] = "Claude Opus"
    saved = client.put(f"/api/v1/documents/{doc_id}/work-plan",
                       json={"base_revision_no": 0, "body": body})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision_no"] == 1
    assert saved.json()["assignment_summary"][0]["step_count"] == 1
    # §4.6: saving does not touch the review state.
    assert db_docs.get_by_id(doc_id)["doc_review_status"] == "pending_review"
    on_disk = json.loads((storage_root / stored).read_text(encoding="utf-8"))
    assert on_disk["steps"][0]["provider_id"] == "aip_opus"

    # §4.8: a stale base revision is refused, and nothing is overwritten.
    body["steps"][0]["note"] = "덮어쓰기 시도"
    conflict = client.put(f"/api/v1/documents/{doc_id}/work-plan",
                          json={"base_revision_no": 0, "body": body})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "wp_revision_conflict"
    assert conflict.json()["current_revision_no"] == 1
    assert "body" not in conflict.json()          # 결정 7: no merge, so no rival body
    assert json.loads((storage_root / stored).read_text(encoding="utf-8")) == on_disk

    # 결정 6: an invalid body is refused BEFORE the revision check, so the user is
    # never told to discard their edits only to find the values were wrong anyway.
    bad = json.loads(json.dumps(body))
    tsr = [s for s in bad["steps"] if s["key"] == "TSR#1"][0]
    tsr["provider_id"] = "aip_opus"
    rejected = client.put(f"/api/v1/documents/{doc_id}/work-plan",
                          json={"base_revision_no": 0, "body": bad})
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "wp_validation_failed"
    assert rejected.json()["errors"][0]["code"] == "provider_not_allowed"
    assert rejected.json()["errors"][0]["key"] == "TSR#1"


def test_human_create_applies_quantities_defaults_and_type_providers(seed):
    client = _client()
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0991-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "채워진 작업계획",
            "counted_types": ["D", "T", "TS"],
            "provider_candidates": ["aip_opus"],
            "quantities": {"D": 2, "T": 2, "TS": 1},
            "defaults": {"provider_id": "aip_opus", "note": "공통 멘트"},
            "type_providers": {"T": "aip_opus", "TS": "aip_opus"},
        })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    plan = created["body"]
    assert created["title"] == "채워진 작업계획"
    assert plan["quantities"] == {
        "D": {"unit": "sheet", "count": 2},
        "T": {"unit": "set", "count": 2},
        "TS": {"unit": "set", "count": 1},
    }
    assert plan["defaults"] == {"provider_id": "aip_opus", "note": "공통 멘트"}
    assert sum(1 for step in plan["steps"] if not step["locked"] and not step["provider_id"]) == 0
    assert all(
        step["provider_id"] == "aip_opus" and step["origin"] == "human"
        for step in plan["steps"]
        if not step["locked"]
    )


def test_human_create_assigns_work_provider_to_report_pair_but_not_locked_tsr(seed):
    client = _client()
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0992-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "counted_types": ["T", "TS"],
            "provider_candidates": ["aip_opus"],
            # flowgate.default.0423 T0005 item 15: an omitted quantity now defaults to 0,
            # so this test (about provider assignment, not the default) states its own.
            "quantities": {"T": 1, "TS": 1},
            "type_providers": {"T": "aip_opus", "TS": "aip_opus"},
        })
    assert resp.status_code == 201, resp.text
    by_key = {step["key"]: step for step in resp.json()["body"]["steps"]}
    assert by_key["T#1"]["provider_id"] == "aip_opus"
    assert by_key["TR#1"]["provider_id"] == "aip_opus"
    assert by_key["TS#1"]["provider_id"] == "aip_opus"
    assert by_key["TSR#1"]["provider_id"] is None
    assert by_key["TSR#1"]["locked"] is True
    assert by_key["TSR#1"]["origin"] == "system"


def test_human_create_rejects_assignment_outside_provider_candidates(seed):
    client = _client()
    resp = client.post("/api/v1/documents/work-plan", json={
        "parent_doc_id": ROOT_DOC,
        "counted_types": ["D"],
        "provider_candidates": ["aip_opus"],
        # flowgate.default.0423 T0005 item 15: a D step must actually exist for the
        # provider-candidate rejection below to have anything to reject.
        "quantities": {"D": 1},
        "type_providers": {"D": "aip_not_a_candidate"},
    })
    assert resp.status_code == 422, resp.text
    assert any(error["code"] == "provider_not_candidate" for error in resp.json()["errors"])


def test_save_accepts_registered_provider_outside_candidates_but_rejects_unknown(seed):
    """0411 T0004: manual assignment is candidate ∪ currently registered, never arbitrary."""
    client = _client()
    effective = {
        "providers": [
            {"id": "aip_opus", "name": "Claude Opus", "kind": "claude", "exec_type": "cli"},
            {"id": "aip_sonnet", "name": "Claude Sonnet", "kind": "claude", "exec_type": "cli"},
        ],
    }
    with (
        patch(
            "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
            return_value="0994-WP",
        ),
        patch(
            "modules.flow_gate.settings.ai_settings_service.resolve_effective",
            return_value=effective,
        ),
    ):
        created = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "counted_types": ["D"],
            "provider_candidates": ["aip_opus"],
            # flowgate.default.0423 T0005 item 15: the save-time provider check below
            # needs an actual D step; an omitted quantity now defaults to 0.
            "quantities": {"D": 1},
        })
        assert created.status_code == 201, created.text
        doc_id = created.json()["doc_id"]

        view = client.get(f"/api/v1/documents/{doc_id}/work-plan")
        assert view.status_code == 200, view.text
        assert [provider["id"] for provider in view.json()["registered_providers"]] == [
            "aip_opus", "aip_sonnet",
        ]
        body = view.json()["body"]
        assert [item["provider_id"] for item in body["provider_candidates"]] == ["aip_opus"]

        body["steps"][0]["provider_id"] = "aip_sonnet"
        body["steps"][0]["provider_display_name"] = "Claude Sonnet"
        saved = client.put(
            f"/api/v1/documents/{doc_id}/work-plan",
            json={"base_revision_no": 0, "body": body},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision_no"] == 1

        saved_view = client.get(f"/api/v1/documents/{doc_id}/work-plan")
        assert saved_view.status_code == 200, saved_view.text
        saved_body = saved_view.json()["body"]
        assert saved_body["steps"][0]["provider_id"] == "aip_sonnet"
        assert [item["provider_id"] for item in saved_body["provider_candidates"]] == [
            "aip_opus",
        ]

        invalid_body = saved_body
        invalid_body["steps"][0]["provider_id"] = "aip_never_registered"
        invalid = client.put(
            f"/api/v1/documents/{doc_id}/work-plan",
            json={"base_revision_no": 1, "body": invalid_body},
        )
        assert invalid.status_code == 422, invalid.text
        assert any(
            error["code"] == "provider_not_candidate" for error in invalid.json()["errors"]
        )


def test_human_create_legacy_request_defaults_unspecified_types_to_zero(seed):
    """flowgate.default.0423 T0005 item 5/15: a legacy request that omits ``quantities``
    entirely used to fill every counted type with 1 (NR0003's all-1 defect). It now
    falls back to the group's workflow_type_counts derivation, or 0 when the group has
    no sequence yet (this fixture's ROOT_DOC) — never a guessed 1."""
    client = _client()
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0993-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "counted_types": ["D", "T"],
            "provider_candidates": ["aip_opus"],
        })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    plan = created["body"]
    assert created["title"] == created["doc_id"]
    assert {code: item["count"] for code, item in plan["quantities"].items()} == {"D": 0, "T": 0}
    assert plan["defaults"] == {"provider_id": None, "note": ""}
    assert plan["steps"] == []


def test_human_create_uses_workflow_type_counts_when_quantities_omitted(seed, storage_root):
    """flowgate.default.0423 T0005 item 6/7/16: an omitted quantity prefers the group's
    workflow_type_counts derivation over the bare 0 fallback, when the group's sequence
    already has decided items to derive from. An explicit request value still wins, and
    a type with neither an explicit value nor a derivation still lands at 0."""
    from modules.flow_gate.db import documents as db_docs

    # A group_id of its own, not the shared GROUP: get_pending_head_by_group scopes by
    # group_id alone, and this sequence deliberately stays pending forever (it never
    # includes "WP"), so sharing GROUP would make it shadow the pending head other
    # GROUP-scoped slot-filling tests expect (module-scoped seed/tmp_db fixtures share
    # one DB across this whole file).
    from modules.flow_gate.db import groups as db_groups

    derivation_group = f"{GROUP}-derivation"
    root = f"{derivation_group}-R0901"
    db_groups.create({
        "group_id": derivation_group, "project_id": PROJECT, "module": "__ALL__",
        "title": "Derivation Group",
    })
    db_docs.create({
        "doc_id": root, "project_id": PROJECT, "type_code": "R", "seq": 901,
        "title": "Derivation Root", "group_id": derivation_group, "module": "__ALL__",
        "owner_id": "usr_wp_001",
    })
    with _sequence_sql():
        _decide_sequence(root, ["D", "D", "T"])

        client = _client()
        with patch(
            "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
            return_value="0995-WP",
        ):
            resp = client.post("/api/v1/documents/work-plan", json={
                "parent_doc_id": root,
                "counted_types": ["D", "T", "P"],
                "provider_candidates": ["aip_opus"],
                "quantities": {"T": 3},
            })
        assert resp.status_code == 201, resp.text
        plan = resp.json()["body"]
        assert {code: item["count"] for code, item in plan["quantities"].items()} == {
            "D": 2,  # derived from the sequence — the request never mentioned D
            "T": 3,  # explicit request value wins over the sequence's derived 1
            "P": 0,  # neither an explicit value nor a derivation exists
        }


def test_suggest_returns_the_documented_shape(seed, storage_root):
    """§4.9: only the changed cells, and a basis the server can actually name."""
    client = _client()
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0006-WP",
    ):
        created = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC, "title": "제안 대상",
            "counted_types": ["D", "T"], "provider_candidates": ["aip_opus"],
        })
    assert created.status_code == 201, created.text
    doc_id = created.json()["doc_id"]

    resp = client.post(f"/api/v1/documents/{doc_id}/work-plan/suggest",
                       json={"base_revision_no": 0})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["basis"] == "not_specified"
    assert isinstance(payload["suggested"]["steps"], list)
    # With no per-doc-type provider rule configured, nothing is invented.
    assert payload["suggested"]["steps"] == []
    # Suggesting never saves — the revision is untouched until [저장].
    assert client.get(f"/api/v1/documents/{doc_id}/work-plan").json()["revision_no"] == 0


def test_a_work_plan_body_satisfies_the_approval_body_guard(seed, storage_root):
    """D0007 §3.6: the plan uses the EXISTING review pipeline, so its JSON body has to
    read as a real body — otherwise every approval would fail with "빈 본문"."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.workflow.pipeline_service import _require_document_body_for_approval

    client = _client()
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0007-WP",
    ):
        created = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC, "title": "승인 대상",
            "counted_types": ["D"], "provider_candidates": ["aip_opus"],
        })
    assert created.status_code == 201, created.text
    doc = db_docs.get_by_id(created.json()["doc_id"])
    _require_document_body_for_approval(doc)  # raises TransitionError when the body is empty


def test_regenerate_recovers_a_work_plan_as_json(seed, storage_root):
    """P0009 §10: a `.json` canonical body is new, so recovery must not write `.md`.

    Generic recovery synthesizes a Markdown frontmatter stub. For a work plan that
    produces a file its own reader can only refuse ("표로 열 수 없습니다"), so the plan
    branch recovers a valid-but-undecided body instead.
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers.documents import router as documents_router
    from modules.flow_gate.documents.routers.work_plan import router as wp_router
    from modules.flow_gate.services import work_plan_service as wp

    app = FastAPI()
    app.include_router(wp_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_wp_001"}
    client = TestClient(app, raise_server_exceptions=False)

    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0008-WP",
    ):
        created = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC, "title": "복구 대상",
            "counted_types": ["D"], "provider_candidates": ["aip_opus"],
        })
    assert created.status_code == 201, created.text
    doc_id = created.json()["doc_id"]
    plan_file = storage_root / created.json()["stored_path"]
    plan_file.unlink()

    resp = client.post(f"/api/v1/documents/{doc_id}/regenerate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["file_path"].endswith("_document.json")
    recovered = storage_root / resp.json()["data"]["file_path"]
    body = json.loads(recovered.read_text(encoding="utf-8"))
    # Valid, openable, and honestly empty rather than invented.
    assert wp.validate(body)["steps"] == []
    assert client.get(f"/api/v1/documents/{doc_id}/work-plan").status_code == 200


def test_help_serves_the_work_plan_template(seed):
    """P0009 §6: the one type whose body an AI cannot guess must have a template."""
    from modules.flow_gate.services import help_catalog
    from modules.flow_gate.services import work_plan_service as wp

    base = {
        "locale": "ko", "action_scope": "new", "principal_kind": "worker_token",
        "project": PROJECT, "base_url": "/flowgate/api/v1",
        "tool_kind": "read_write", "source_mode": "remote",
    }
    assert help_catalog.decide_visibility("design_template", dict(base, doc_type="WP")).visible
    assert help_catalog.decide_visibility("design_template", dict(base, doc_type="D")).visible
    assert not help_catalog.decide_visibility("design_template", dict(base, doc_type="TR")).visible

    wp_children = [c["name"] for c in
                   help_catalog.enumerate_children("design_template", dict(base, doc_type="WP"))]
    assert "WP" in wp_children
    d_children = [c["name"] for c in
                  help_catalog.enumerate_children("design_template", dict(base, doc_type="D"))]
    assert "WP" not in d_children  # a D author has no use for the plan body format

    child = help_catalog.build_child("design_template", "WP", dict(base, doc_type="WP"))
    assert child["content"]["body_format"] == "json"
    # The template is not decoration: sending it back unchanged has to be accepted.
    assert wp.validate(json.loads(child["content"]["body"]))["binding"] == "advisory"

    # The Markdown branch now says its format out loud too — otherwise a worker still
    # has to infer it from whichever body it happened to receive.
    d_child = help_catalog.build_child("design_template", "D", dict(base, doc_type="D"))
    assert d_child["content"]["body_format"] == "markdown"


def test_ai_handoff_points_to_the_work_plan_body_template(seed):
    from modules.flow_gate.services import mention_service

    mention = mention_service.build_mention(
        project=PROJECT, module="default", group="0402",
        parent_type="R", parent_doc_number="R0001", parent_title="root",
        parent_doc_id="R0001", head_type="WP", head_status="pending",
        scratch_dir="/scratch", raw_token="token",
        api_base_url="http://localhost:8000/flowgate/api/v1",
        action_scope="new", locale="ko",
    )
    assert "## Document template" in mention
    assert "GET http://localhost:8000/flowgate/api/v1/help/items/design_template/WP" in mention


@pytest.mark.parametrize(("locale", "needle"), [
    ("ko", "정본 JSON"),
    ("en", "canonical JSON"),
    ("ja", "正規 JSON"),
])
def test_submit_help_explains_the_work_plan_body_contract(seed, locale, needle):
    from modules.flow_gate.services import help_catalog

    payload = help_catalog._content_submit({
        "locale": locale, "action_scope": "new", "doc_type": "WP",
        "project": PROJECT, "group_id": "wpprj.default.0402",
        "doc_id": ROOT_DOC, "base_url": "/flowgate/api/v1",
        "scratch_dir": "/scratch",
    })
    assert payload["body"]["content"] == "<Canonical work-plan JSON>"
    assert payload["content_format"]["format"] == "canonical_json"
    assert payload["content_format"]["template_url"].endswith("design_template/WP")
    assert needle in payload["content_format"]["guidance"]
    assert "title" in payload["content_format"]["guidance"]
    assert "prev_doc_id" in payload["content_format"]["guidance"]


def test_human_create_refuses_an_empty_selection(seed):
    # 0405 T0011 rev2: 후보를 비워 보낸 요청이 거절되는 것은 "고를 수 있는데 비웠을 때"다.
    # 등록된 공급자가 하나도 없는 프로젝트에서는 고를 방법 자체가 없어 빈 후보가 정상이므로
    # (사용자 반려: "AI공급자 선택할게 없으면 ... 1만 선택하고 생성할수 있게"), 이 시험이
    # 말하려는 상황 — 고를 수 있는 프로젝트 — 을 명시한다.
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[{"id": "aip_opus", "name": "Claude Opus", "kind": "claude",
                       "exec_type": "cli", "enabled": True}],
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC, "title": "빈 선택",
            "counted_types": [], "provider_candidates": [],
        })
    assert resp.status_code == 422
    payload = resp.json()
    assert payload["code"] == "wp_validation_failed"
    assert {e["loc"] for e in payload["errors"]} == {"counted_types", "provider_candidates"}


def _inbox_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app, raise_server_exceptions=False)


def _token(tmp_path):
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        result = token_service.issue(
            project=PROJECT, group_id=GROUP, action_scope="new",
            doc_ref=ROOT_DOC, issued_to="usr_wp_001",
        )
    return result["raw_token"]


def _inbox_post(tmp_path, content, doc_code="0003-WP", doc_type="WP"):
    with patch("modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
               return_value=doc_code):
        return _inbox_client().post(
            "/api/v1/inbox",
            json={
                "project": PROJECT, "module": "__ALL__", "group_name": GROUP,
                "action": "new", "prev_doc_id": ROOT_DOC, "doc_type": doc_type,
                "title": "0402 작업계획 — AI", "content": content,
            },
            headers={"Authorization": f"Bearer {_token(tmp_path)}"},
        )


def test_ai_inbox_creates_a_json_work_plan(seed, storage_root, tmp_path):
    from modules.flow_gate.services import work_plan_service as wp

    raw_plan = _plan()
    for step in raw_plan["steps"]:
        if not step["locked"]:
            step["provider_id"] = "aip_opus"
            step["provider_display_name"] = "Claude Opus"
            step["note"] = f"AI assignment for {step['key']}"
    plan = wp.validate(raw_plan)
    resp = _inbox_post(tmp_path, wp.dumps(plan))
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["stored_path"].endswith("_document.json")
    assert Path(data["stored_path"]).is_file()
    # 결정 10: the work-plan flavour of the summary, not the prose one.
    assert data["change_summary"]["kind"] == "work_plan"
    assert data["change_summary"]["steps"] == 15
    assert "T 3세트" in data["change_summary"]["quantities"]
    assert data["origin"] == "ai"
    # 결정 3 (§2.6): stored in canonical form regardless of how it arrived.
    assert Path(data["stored_path"]).read_text(encoding="utf-8") == wp.dumps(plan)

    # The same response consumed by WorkPlanEditor opens as a structured table, and
    # every assignable row supplied by the AI is assigned (the locked TSR is excluded).
    view_response = _client().get(f"/api/v1/documents/{data['doc_id']}/work-plan")
    assert view_response.status_code == 200, view_response.text
    view = view_response.json()
    assert view["body"]["wp_version"] == 1
    assert len(view["body"]["steps"]) == 15
    assert view["unassigned_step_count"] == 0


def test_ai_inbox_refuses_markdown_and_rule_breaks(seed, tmp_path):
    from modules.flow_gate.services import work_plan_service as wp

    markdown = _inbox_post(tmp_path, "# 작업계획\n\n| 타입 | 장수 |\n|---|---|\n| D | 2 |\n")
    assert markdown.status_code == 400
    assert "JSON" in markdown.json()["error_message"]
    assert markdown.json()["help_url"].endswith("design_template/WP")

    body = _plan()
    [s for s in body["steps"] if s["key"] == "TSR#1"][0]["provider_id"] = "aip_opus"
    broken = _inbox_post(tmp_path, json.dumps(body, ensure_ascii=False))
    assert broken.status_code == 400
    # Same verdict as the human path, flattened into the inbox envelope (§5.3).
    assert "TSR#1" in broken.json()["error_message"]

    # 결정 9: the title lives in the request, so a body that repeats it is refused
    # rather than silently ignored.
    titled = _plan()
    titled["title"] = "본문에 적은 제목"
    named = _inbox_post(tmp_path, json.dumps(titled, ensure_ascii=False))
    assert named.status_code == 400
    assert "title" in named.json()["error_message"]


# ── Part 6: 0395 T0021 — 작업계획이 워크플로 시퀀스의 한 칸일 때 ────────────────


@contextmanager
def _sequence_sql():
    """Let the patched store resolve the workflow_sequences queries for a moment.

    The module store deliberately refuses ``_sql`` so a test cannot lean on the query
    files by accident. These two tests are about a real sequence in a real table, so
    they borrow the base implementation (which falls back to the sqlite query files)
    for the duration of the case and hand it back afterwards.
    """
    from modules.flow_gate.db.connection import FlowGateStore, get_store

    store_cls = type(get_store())
    with patch.object(store_cls, "_sql", FlowGateStore._sql):
        yield


def _decide_sequence(root_doc_id: str, types: list[str]) -> int:
    """Write a real decided sequence for ``root_doc_id`` and return its id.

    No mock of get_pending_head_by_group here on purpose: the point of the test is
    that the route reads the head the database actually holds.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    db_wfseq.insert_sequence(root_doc_id)
    seq = db_wfseq.get_sequence_by_doc_id(root_doc_id)
    for idx, type_ in enumerate(types, start=1):
        db_wfseq.insert_sequence_item(
            sequence_id=seq["id"], item_seq=idx, type_=type_,
            label=type_, doc_class="R", sort_order=idx,
        )
    return seq["id"]


def test_human_create_fills_a_pending_WP_slot(seed, storage_root):
    """T0021: 작업계획을 시퀀스 칸으로 놓을 수 있게 되었으니 사람 경로도 그 칸을 채운다.

    D0007 §7 puts WP in a workflow slot (it is deliberately absent from
    NON_SLOT_WORKFLOW_TYPES) and the AI path already fills the head at inbox step 7.5.
    Until this change the human dialog did not, so a group whose sequence held a WP
    step stayed pending forever and the created plan came back as an orphaned member.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    root = f"{GROUP}-R0900"
    db_docs.create({
        "doc_id": root, "project_id": PROJECT, "type_code": "R", "seq": 900,
        "title": "Slot Root", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001",
    })
    with _sequence_sql():
        seq_id = _decide_sequence(root, ["WP", "T"])

        client = _client()
        with patch(
            "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
            return_value="0901-WP",
        ):
            resp = client.post("/api/v1/documents/work-plan", json={
                "parent_doc_id": root,
                "title": "시퀀스 칸을 채우는 작업계획",
                "counted_types": ["T"],
                "provider_candidates": ["aip_opus"],
            })
        assert resp.status_code == 201, resp.text
        doc_id = resp.json()["doc_id"]

        items = db_wfseq.get_sequence_items(seq_id)
        assert items[0]["type"] == "WP"
        assert items[0]["result_doc_id"] == doc_id
        # The next step becomes the head — the sequence actually moved on.
        head = db_wfseq.get_pending_head_by_group(GROUP, PROJECT)
        assert head is not None and head["type"] == "T"
        # And the plan is no longer an orphan the recovery screen has to ask about.
        assert db_wfseq.is_orphaned_workflow_member(doc_id) is False


def test_human_create_leaves_a_non_WP_head_alone(seed, storage_root):
    """자문형이라는 성질은 그대로다: 머리가 다른 타입이면 그 칸을 가로채지 않는다."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    root = f"{GROUP}-R0910"
    db_docs.create({
        "doc_id": root, "project_id": PROJECT, "type_code": "R", "seq": 910,
        "title": "Busy Root", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001",
    })
    with _sequence_sql():
        seq_id = _decide_sequence(root, ["DS", "T"])

        client = _client()
        with patch(
            "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
            return_value="0911-WP",
        ):
            resp = client.post("/api/v1/documents/work-plan", json={
                "parent_doc_id": root,
                "title": "진행 중인 그룹의 작업계획",
                "counted_types": ["T"],
                "provider_candidates": ["aip_opus"],
            })
        assert resp.status_code == 201, resp.text

        items = db_wfseq.get_sequence_items(seq_id)
        assert [it["result_doc_id"] for it in items] == [None, None]


def test_ai_inbox_is_not_blocked_by_a_mismatched_workflow_head(seed, tmp_path):
    """A plan is advisory: it must be writable while the group sits on another step.

    The workflow head is stubbed to DS so the guard is genuinely in play. The TR control
    in the same test proves the guard still bites for a step document — otherwise this
    would pass just as well with the guard removed entirely.
    """
    from modules.flow_gate.services import work_plan_service as wp

    head = {"id": 1, "type": "DS", "label": "설계지시", "status": "pending"}
    with patch("modules.flow_gate.api.inbox_routes.db_wfseq.get_pending_head_by_group",
               return_value=head):
        resp = _inbox_post(tmp_path, wp.dumps(wp.validate(_plan())), doc_code="0004-WP")
        assert resp.status_code == 201, resp.text

        control = _inbox_post(
            tmp_path, "# 작업 레포트\n\n## 변경 파일\n\n없음\n",
            doc_code="0005-TR", doc_type="TR",
        )
        assert control.status_code == 409
        assert "DS" in control.json()["error_message"]


# ── Part 7: 0395 T0026 재작업 — 생성 대화상자를 거치지 않고 만들어진 작업계획 ────
#
# 사용자가 실제로 겪은 일: 워크플로 머리 칸이 WP 인 그룹에서 [빈 문서 만들기]로 작업계획을
# 만들었더니, 문서를 열 때마다
#     이 작업계획을 표로 열 수 없습니다. 원문 보기로 확인해 주세요.
#     Expecting value: line 1 column 1 (char 0)
# 만 나왔다. 그 길이 마크다운 머리말 뼈대를 정본 자리에 써 버렸기 때문이다.


def _seed_providers():
    """진짜 공급자 두 대와 프로젝트 기본값, 그리고 D 타입 배정 규칙 한 줄.

    auto_plan_body 가 무엇을 읽는지가 이 시험의 핵심이므로 설정 서비스를 가짜로 바꾸지
    않는다 — 실제 표에 넣고 실제 해석기를 통과시킨다.
    """
    from modules.flow_gate.db.connection import get_store, now_iso

    store = get_store()
    now = now_iso()
    for provider_id, name, order in (
        ("aip_seed_a", "Claude Opus", 1),
        ("aip_seed_b", "GPT", 2),
    ):
        store._execute(
            "INSERT OR IGNORE INTO ai_providers "
            "(provider_id, project_id, name, exec_type, kind, enabled, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [provider_id, PROJECT, name, "cli", "claude", 1, order, now, now],
        )
    store._execute(
        "INSERT INTO project_settings (project_id, ai_mode, ai_default_provider_id, updated_at) "
        "VALUES (?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
        "ai_mode = excluded.ai_mode, ai_default_provider_id = excluded.ai_default_provider_id",
        [PROJECT, "custom", "aip_seed_a", now],
    )
    store._execute(
        "DELETE FROM ai_provider_doctype_map WHERE project_id = ? AND doc_type = ?",
        [PROJECT, "D"],
    )
    store._execute(
        "INSERT INTO ai_provider_doctype_map (project_id, doc_type, provider_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        [PROJECT, "D", "aip_seed_b", now, now],
    )


def _full_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers.documents import router as documents_router
    from modules.flow_gate.documents.routers.work_plan import router as wp_router

    app = FastAPI()
    app.include_router(wp_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_wp_001"}
    return TestClient(app, raise_server_exceptions=False)


def test_workflow_type_counts_folds_the_report_half_into_its_set():
    """짝 레포트 칸은 새 세트가 아니다. 지시 칸이 사라진 시퀀스에서도 세트 수는 유지된다."""
    from modules.flow_gate.services import work_plan_service as wp

    items = [{"type": t} for t in ("WP", "D", "D", "T", "TR", "TS", "TSR", "AC")]
    assert wp.workflow_type_counts(items) == {"D": 2, "T": 1, "TS": 1}
    assert wp.workflow_type_counts([{"type": "TR"}, {"type": "TR"}]) == {"T": 2}
    assert wp.workflow_type_counts([]) == {}


def test_unwritten_plan_is_only_the_never_written_file():
    """되살리기 대상은 '계획이 들어 있던 적 없는 파일'뿐이다. 내용이 있으면 손대지 않는다."""
    from modules.flow_gate.services import work_plan_service as wp

    assert wp.is_unwritten_plan("") is True
    assert wp.is_unwritten_plan("---\ntype: WP\ntitle: ww\n---\n") is True
    assert wp.is_unwritten_plan("---\ntype: WP\n---\n\n계획을 여기 적었다\n") is False
    assert wp.is_unwritten_plan('{"wp_version": 1,') is False


def test_next_empty_creates_a_plan_that_opens_as_a_table(seed, storage_root):
    """[빈 문서 만들기]로 만든 작업계획이 표로 열리고, 수량·공급자가 채워져 있다.

    사용자가 신고한 증상(Expecting value: line 1 column 1)이 여기서 나던 것이다. 수량은
    이 그룹의 워크플로 시퀀스에서, 공급자는 프로젝트 실행 체인과 문서종류 배정표에서 온다.
    """
    from modules.flow_gate.db import documents as db_docs

    _seed_providers()
    root = f"{GROUP}-R0920"
    db_docs.create({
        "doc_id": root, "project_id": PROJECT, "type_code": "R", "seq": 920,
        "title": "Empty-Doc Root", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001",
    })
    client = _full_client()
    with _sequence_sql():
        _decide_sequence(root, ["WP", "D", "D", "T", "TR", "TS", "TSR"])
        with patch(
            "modules.flow_gate.documents.routers.documents.numbering_service.reserve_document",
            return_value="0921-WP",
        ):
            created = client.post("/api/v1/documents/next-empty", json={
                "project_id": PROJECT, "group_id": GROUP, "prev_doc_id": root,
                "type_code": "WP", "title": "ww", "module": "__ALL__",
            })
        assert created.status_code == 201, created.text
        doc_id = created.json()["doc_id"]

    # 1. 정본은 마크다운이 아니라 JSON 이다.
    stored = created.json()["data"]["file_path"]
    assert stored.endswith("_document.json"), stored
    on_disk = json.loads((storage_root / stored).read_text(encoding="utf-8"))
    assert on_disk["wp_version"] == 1

    # 2. 문서를 열면 표가 나온다 — 409 "표로 열 수 없습니다" 가 아니다.
    view = client.get(f"/api/v1/documents/{doc_id}/work-plan")
    assert view.status_code == 200, view.text
    payload = view.json()

    # 3. 수량은 시퀀스에서 왔고, 미지정 칸이 없다.
    assert payload["totals"] == {"design_sheets": 2, "work_sets": 2, "steps": 6}
    assert payload["unassigned_step_count"] == 0
    by_key = {step["key"]: step for step in payload["body"]["steps"]}
    assert by_key["D#1"]["provider_id"] == "aip_seed_b"   # 문서종류 배정표가 이긴다
    assert by_key["T#1"]["provider_id"] == "aip_seed_a"   # 규칙이 없으면 기본 공급자
    assert by_key["TR#1"]["provider_id"] == "aip_seed_a"  # 짝 레포트도 같은 공급자
    assert by_key["TSR#1"]["locked"] is True and by_key["TSR#1"]["provider_id"] is None

    # 4. 제목은 사용자가 적은 것 그대로다 (문서번호가 아니다).
    assert payload["title"] == "ww"


def test_next_empty_still_writes_markdown_for_other_types(seed, storage_root):
    """작업계획만 다르다. 나머지 타입의 빈 문서는 지금까지와 똑같은 마크다운 뼈대다."""
    from modules.flow_gate.db import documents as db_docs

    root = f"{GROUP}-R0930"
    db_docs.create({
        "doc_id": root, "project_id": PROJECT, "type_code": "R", "seq": 930,
        "title": "Markdown Root", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001",
    })
    client = _full_client()
    with _sequence_sql():
        _decide_sequence(root, ["N", "NR"])
        with patch(
            "modules.flow_gate.documents.routers.documents.numbering_service.reserve_document",
            return_value="0931-N",
        ):
            created = client.post("/api/v1/documents/next-empty", json={
                "project_id": PROJECT, "group_id": GROUP, "prev_doc_id": root,
                "type_code": "N", "title": "보통 문서", "module": "__ALL__",
            })
    assert created.status_code == 201, created.text
    stored = created.json()["data"]["file_path"]
    assert stored.endswith("_document.md"), stored
    text = (storage_root / stored).read_text(encoding="utf-8")
    assert text.startswith("---") and "type: N" in text


def test_an_already_broken_plan_heals_the_first_time_it_is_opened(seed, storage_root):
    """이미 만들어져 버린 마크다운 작업계획도 열면 되살아난다.

    생성 경로만 고치면 사용자가 이미 갖고 있는 문서는 영원히 못 연다. 계획이 들어 있던 적
    없는 파일에 한해, 여는 그 자리에서 정본을 만들고 문서가 그 파일을 가리키게 한다.
    지난 마크다운 파일은 지우지 않는다.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.storage import paths as storage_paths

    _seed_providers()
    root = f"{GROUP}-R0940"
    db_docs.create({
        "doc_id": root, "project_id": PROJECT, "type_code": "R", "seq": 940,
        "title": "Legacy Root", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001",
    })
    doc_id = f"{GROUP}.0941-WP"
    stub_path = storage_paths.document_path(
        project_id=PROJECT, group_code=GROUP, doc_code="0941-WP",
        filename="document.md", module="__ALL__", branch="main",
    )
    stub_path.parent.mkdir(parents=True, exist_ok=True)
    stub_path.write_text(
        "---\nproject: wpprj\ntype: WP\ndoc_number: 0941-WP\ntitle: ww\n---\n",
        encoding="utf-8",
    )
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "type_code": "WP", "seq": 941,
        "title": "ww", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001", "target_id": root,
        "file_path": storage_paths.to_storage_relative(stub_path, PROJECT),
    })

    client = _full_client()
    view = client.get(f"/api/v1/documents/{doc_id}/work-plan")
    assert view.status_code == 200, view.text
    assert view.json()["unassigned_step_count"] == 0

    healed = db_docs.get_by_id(doc_id)["file_path"]
    assert healed.endswith("_document.json"), healed
    assert json.loads((storage_root / healed).read_text(encoding="utf-8"))["wp_version"] == 1
    assert stub_path.is_file()  # 되살리기는 지우는 일이 아니다

    # 두 번째로 열어도 같은 표가 나온다(되살리기는 한 번으로 끝난다).
    assert client.get(f"/api/v1/documents/{doc_id}/work-plan").status_code == 200


def test_a_broken_plan_with_content_is_never_overwritten(seed, storage_root):
    """반쯤 쓰다 깨진 계획은 서버가 덮어쓰지 않는다 — 원문 보기로 넘긴다."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.storage import paths as storage_paths

    doc_id = f"{GROUP}.0951-WP"
    path = storage_paths.document_path(
        project_id=PROJECT, group_code=GROUP, doc_code="0951-WP",
        filename="document.json", module="__ALL__", branch="main",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    broken = '{"wp_version": 1, "steps": [  '
    path.write_text(broken, encoding="utf-8")
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "type_code": "WP", "seq": 951,
        "title": "깨진 계획", "group_id": GROUP, "module": "__ALL__",
        "owner_id": "usr_wp_001",
        "file_path": storage_paths.to_storage_relative(path, PROJECT),
    })

    client = _full_client()
    resp = client.get(f"/api/v1/documents/{doc_id}/work-plan")
    assert resp.status_code == 409
    assert resp.json()["code"] == "wp_unreadable"
    assert path.read_text(encoding="utf-8") == broken


def test_template_includes_real_candidates_and_note_rules(monkeypatch):
    from modules.flow_gate.services import work_plan_service as wp

    monkeypatch.setattr(wp, "_effective_chain", lambda _project: {
        "providers": [{
            "id": "prov-a",
            "name": "Provider A",
            "kind": "openai",
            "exec_type": "api",
        }]
    })
    body = json.loads(wp.template_body("wpprj"))
    assert body["provider_candidates"] == [{
        "provider_id": "prov-a",
        "display_name": "Provider A",
        "group_label": "Openai · API",
    }]
    # flowgate.default.0423 T0005 item 4/16: the template example is a format sample,
    # never the all-1 shape NR0003 flagged — every countable type renders at 0.
    assert {code: item["count"] for code, item in body["quantities"].items()} == {
        code: 0 for code in body["counted_types"]
    }
    for locale in ("ko", "en", "ja"):
        rules = "\n".join(wp.TEMPLATE_RULES[locale])
        assert "steps[].note" in rules
        assert "defaults.note" in rules
        assert "provider_candidates" in rules
        # flowgate.default.0423 T0005 item 3/16: a quantity-basis rule that points to
        # workflow_type_counts as evidence (and forbids guessing 1) must be present.
        assert "workflow_type_counts" in rules


def test_work_plan_fill_mention_contains_three_scopes_and_note_instruction(monkeypatch):
    from modules.flow_gate.services import mention_service
    from modules.flow_gate.services import work_plan_service as wp

    monkeypatch.setattr(
        mention_service,
        "get_type_name",
        lambda code, locale: {"D": "기본설계"}.get(code, code),
    )
    body = wp.initial_body(
        ["D"],
        [{"provider_id": "prov-a", "display_name": "Provider A", "group_label": "Openai · API"}],
        "wpprj",
    )
    body["quantities"]["D"]["count"] = 1
    body["steps"] = wp.expand_steps(body["counted_types"], body["quantities"])
    scope = {
        "quantity_type_codes": ["D"],
        "step_keys": ["D#1"],
        "provider_ids": ["prov-a"],
    }
    mention = mention_service.build_work_plan_fill_mention(
        token_rec={"project": "wpprj", "group_id": "wpprj.default.0402", "scratch_dir": "x"},
        target_doc={"doc_id": "wpprj.default.0402.0002-WP"},
        body=body,
        scope=scope,
        api_base_url="http://localhost:8000",
        raw_token="secret",
        locale="ko",
    )
    assert "D#1 · 기본설계 1장" in mention
    assert "D#1 · D" not in mention
    assert "prov-a · Provider A" in mention
    assert "scratch_dir: x" in mention
    assert "범위 밖 값은 지금 값 그대로" in mention
    assert "note를 반드시 채우십시오" in mention
    assert "design_template/WP" in mention
    assert '"wp_version"' in mention


def test_suggest_scope_validates_and_echoes_the_exact_boundary(seed, storage_root):
    client = _client()
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0012-WP",
    ):
        created = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "범위 제안",
            "counted_types": ["D", "T"],
            "provider_candidates": ["aip_opus"],
            "quantities": {"D": 1, "T": 1},
        })
    assert created.status_code == 201, created.text
    doc_id = created.json()["doc_id"]

    invalid = client.post(f"/api/v1/documents/{doc_id}/work-plan/suggest", json={
        "scope": {
            "quantity_type_codes": ["UNKNOWN"],
            "step_keys": ["missing#1"],
            "provider_ids": ["outside"],
        }
    })
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "wp_scope_invalid"

    scope = {
        "quantity_type_codes": ["D"],
        "step_keys": [],
        "provider_ids": ["aip_opus"],
    }
    scoped = client.post(
        f"/api/v1/documents/{doc_id}/work-plan/suggest",
        json={"base_revision_no": 0, "scope": scope},
    )
    assert scoped.status_code == 200, scoped.text
    payload = scoped.json()
    assert payload["scope_echo"] == scope
    assert payload["suggested"]["steps"] == []
    assert set(payload["suggested"]["quantities"]).issubset({"D"})
    assert payload["basis"] == "project_type_provider_map"
