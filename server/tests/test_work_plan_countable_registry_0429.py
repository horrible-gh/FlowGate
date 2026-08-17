"""Work plan (WP) countable-type registry — flowgate.default.0429 T0004.

NR0003 확정 원인: 클라이언트는 document_types 를 `series, sort_order, type_code` 원본
순서(design 이 instruction 보다 앞이라 D · P · L · DB · DS · N · T · TS)로 읽고, 서버
`work_plan_service.list_countable_types()`는 DS 를 설계 앞으로 옮기는 별도 순위
(DS · D · P · L · DB · N · T · TS)를 썼다. 이 파일은 T0004 가 만든 단일 정본 —
서버의 `work_plan_countable_types` API 필드 + `list_countable_types(project_id)` +
`expand_steps()`/`type_order()` — 이 프로젝트 오버라이드(신규 비countable 코드,
기존 코드의 중복·비활성·재정렬)가 있어도 항상 같은 코드당-한-항목 · 결정적 순서를
내는지 고정한다. `load_body(path, project_id=...)` 전파도 함께 고정한다.
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest

from tests.test_work_plan_0395 import (  # noqa: F401 — module fixtures are used by name
    GROUP,
    PROJECT,
    ROOT_DOC,
    _client,
    _sequence_sql,
    patch_store,
    seed,
    storage_root,
    tmp_db,
)

CANONICAL_ORDER = ["DS", "D", "P", "L", "DB", "N", "T", "TS"]
RAW_DB_ORDER = ["D", "P", "L", "DB", "DS", "N", "T", "TS"]  # series,sort_order,type_code

PROJECT2 = "wpprj2"
GROUP2 = "wpprj2-__ALL__-0429"
ROOT_DOC2 = "wpprj2-__ALL__-0429-R0001"


# ── Part 1: the global default seed — raw order vs the work-plan registry ────

def test_raw_document_types_order_differs_from_the_work_plan_registry(seed):
    """The bug's exact shape (NR0003): the two orders are NOT the same list."""
    from modules.flow_gate.db import templates as db_templates
    from modules.flow_gate.services import work_plan_service as wp

    raw_rows = db_templates.list_document_types(project_id=None, locale="ko")
    raw_countable = [
        str(r["type_code"]).upper() for r in raw_rows
        if str(r["type_code"]).upper() in wp.WORK_PLAN_TYPE_UNITS
    ]
    assert raw_countable == RAW_DB_ORDER

    registry = wp.list_countable_types()
    assert [e["code"] for e in registry] == CANONICAL_ORDER
    assert raw_countable != CANONICAL_ORDER


def test_expand_steps_through_the_registry_matches_canonical_order(seed):
    """steps built from the *registry's own* order (not a hand-written list) — the
    exact round trip client typeOrder()/expandSteps() and the server both must agree on.
    """
    from modules.flow_gate.services import work_plan_service as wp

    ordered_codes = [e["code"] for e in wp.list_countable_types()]
    assert ordered_codes == CANONICAL_ORDER
    quantities = {code: {"unit": wp.WORK_PLAN_TYPE_UNITS[code], "count": 1} for code in ordered_codes}
    steps = wp.expand_steps(ordered_codes, quantities)
    step_types = []
    for step in steps:
        if step["type"] not in step_types:
            step_types.append(step["type"])
    # N/T/TS pairs expand to instruction+result; collapse to the leading half per set.
    collapsed = [t for t in step_types if t not in ("NR", "TR", "TSR")]
    assert collapsed == CANONICAL_ORDER


def test_document_types_api_exposes_both_orders(seed):
    """0429 T0004 작업 2: `data` keeps the settings-screen order; the additive
    `work_plan_countable_types` array is the sole work-plan sort/dedup registry."""
    from modules.flow_gate.settings.routers import project_settings as ps_router

    resp = ps_router.list_doc_types(PROJECT, locale="ko", user=None)
    data_countable = [
        row["code"] for row in resp["data"]
        if row.get("countable")
    ]
    assert data_countable == RAW_DB_ORDER
    assert [row["code"] for row in resp["work_plan_countable_types"]] == CANONICAL_ORDER
    for row in resp["work_plan_countable_types"]:
        assert set(row) >= {"code", "label", "unit"}


# ── Part 2: project overrides — dedup, priority, reordering, deactivation ────

def _insert_project_doctype(store, now, *, project_id, code, name, series, is_active, sort_order):
    store._execute(
        "INSERT INTO document_types "
        "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
        [project_id, code, name, series, is_active, sort_order, now, now],
    )
    row = store._fetch_one(
        "SELECT id FROM document_types WHERE project_id = ? AND type_code = ? AND series = ?",
        [project_id, code, series],
    )
    store._execute(
        "INSERT INTO document_type_names (document_type_id, locale, type_name) VALUES (?, 'ko', ?)",
        [row["id"], name],
    )


@pytest.fixture(scope="module", autouse=True)
def project2_overrides(tmp_db, patch_store, seed):
    """A second project whose own document_types rows exercise every override shape
    T0004 작업 5 lists: a project-only non-countable code, a duplicate active override
    of an existing countable code with a different sort_order (재정렬, no duplicate
    entry), and an inactive override that turns an active global type off."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": PROJECT2, "project_name": "WP Test 2"})
    store = get_store()
    now = now_iso()

    _insert_project_doctype(
        store, now, project_id=PROJECT2, code="ZZZ", name="프로젝트전용",
        series="general", is_active=1, sort_order=999,
    )
    _insert_project_doctype(
        store, now, project_id=PROJECT2, code="D", name="기본설계(재정렬)",
        series="design", is_active=1, sort_order=999,
    )
    _insert_project_doctype(
        store, now, project_id=PROJECT2, code="DB", name="데이터베이스(비활성)",
        series="design", is_active=0, sort_order=40,
    )

    db_groups.create({
        "group_id": GROUP2, "project_id": PROJECT2, "module": "__ALL__", "title": "WP2 Group",
    })
    db_docs.create({
        "doc_id": ROOT_DOC2, "project_id": PROJECT2, "type_code": "R", "seq": 1,
        "title": "Root2", "group_id": GROUP2, "module": "__ALL__", "owner_id": "usr_wp_001",
    })
    yield


def test_project_only_noncountable_code_never_joins_the_registry():
    from modules.flow_gate.db import templates as db_templates
    from modules.flow_gate.services import work_plan_service as wp

    raw = db_templates.list_document_types(project_id=PROJECT2, locale="ko")
    assert any(str(r["type_code"]).upper() == "ZZZ" for r in raw)
    registry_codes = {e["code"] for e in wp.list_countable_types(PROJECT2)}
    assert "ZZZ" not in registry_codes


def test_project_override_wins_over_global_without_duplicating_the_code():
    from modules.flow_gate.services import work_plan_service as wp

    registry = wp.list_countable_types(PROJECT2)
    d_entries = [e for e in registry if e["code"] == "D"]
    assert len(d_entries) == 1, "the project row must replace, not duplicate, the global row"
    assert d_entries[0]["name"] == "기본설계(재정렬)"

    # The override's sort_order (999) pushes D behind P/L (still global, sort_order
    # 20/30) inside this project's sheet ordering — a visible reorder, not a no-op.
    codes = [e["code"] for e in registry]
    assert codes.index("D") > codes.index("P") > codes.index("DS")

    # The global registry (no project_id) is completely unaffected by PROJECT2's rows.
    global_codes = [e["code"] for e in wp.list_countable_types()]
    assert global_codes == CANONICAL_ORDER


def test_inactive_project_override_removes_the_code_but_not_globally():
    from modules.flow_gate.services import work_plan_service as wp

    project_codes = {e["code"] for e in wp.list_countable_types(PROJECT2)}
    assert "DB" not in project_codes
    global_codes = {e["code"] for e in wp.list_countable_types()}
    assert "DB" in global_codes


def test_type_order_falls_back_deterministically_for_a_deactivated_code():
    """0429 T0004 작업 1: a plan can still name a code the active registry dropped
    (DB, inactive for PROJECT2). type_order() must place it by the same
    WORK_PLAN_COUNTABLE_ORDER rank list_countable_types uses on a DB outage — not
    alphabetically, and not wobbling between calls."""
    from modules.flow_gate.services import work_plan_service as wp

    result = wp.type_order(["DS", "DB", "D"], PROJECT2)
    assert result == ["DS", "D", "DB"]
    # Deterministic: repeated calls (e.g. a save followed by a read) agree exactly.
    assert wp.type_order(["DS", "DB", "D"], PROJECT2) == result


def test_stored_plan_with_a_now_inactive_type_reads_and_saves_deterministically(storage_root):
    from modules.flow_gate.services import work_plan_service as wp

    counted = ["DS", "D", "DB"]
    quantities = {code: {"unit": wp.WORK_PLAN_TYPE_UNITS[code], "count": 1} for code in counted}
    body = {
        "wp_version": 1,
        "binding": "advisory",
        "counted_types": counted,
        "quantities": quantities,
        "provider_candidates": [],
        "defaults": {"provider_id": None, "note": ""},
        "steps": wp.expand_steps(counted, quantities, PROJECT2),
    }
    assert [s["key"].split("#")[0] for s in body["steps"]] == ["DS", "D", "DB"]

    validated = wp.validate(body, project_id=PROJECT2, enforce_provider_scope=False)
    assert [s["key"].split("#")[0] for s in validated["steps"]] == ["DS", "D", "DB"]

    path = storage_root / "wp_inactive_type_plan.json"
    wp.write_body_atomically(path, validated)
    reloaded = wp.load_body(path, project_id=PROJECT2)
    assert [s["key"].split("#")[0] for s in reloaded["steps"]] == ["DS", "D", "DB"]


# ── Part 3: load_body(path, project_id=...) reaches every read path ─────────

def _create_plan(client, parent_doc_id: str, doc_code: str) -> str:
    with patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value=doc_code,
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": parent_doc_id,
            "title": f"registry propagation {doc_code}",
            "counted_types": ["DS", "D"],
            "provider_candidates": [],
            "quantities": {"DS": 1, "D": 1},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 201, resp.text
    return resp.json()["doc_id"]


def test_get_suggest_and_sequence_candidates_pass_the_document_project_id(seed, storage_root):
    from modules.flow_gate.documents.routers import work_plan as wp_router

    client = _client()
    doc_id = _create_plan(client, ROOT_DOC, "0090-WP")

    seen: list[Optional[str]] = []
    real_load_body = wp_router.wp.load_body

    def _spy(path, project_id=None):
        seen.append(project_id)
        return real_load_body(path, project_id=project_id)

    # sequence-candidates reads workflow_sequences through the dialect query files
    # (_sql), which the module store deliberately refuses outside this borrow window —
    # see test_work_plan_0395._sequence_sql. The doc has no sequence row, which is
    # exactly what this test wants: build_candidates should read an empty sequence,
    # not crash, while load_body still receives this document's project_id.
    with patch.object(wp_router.wp, "load_body", side_effect=_spy), _sequence_sql():
        assert client.get(f"/api/v1/documents/{doc_id}/work-plan").status_code == 200
        assert client.post(f"/api/v1/documents/{doc_id}/work-plan/suggest", json={}).status_code == 200
        seq_resp = client.post(
            f"/api/v1/documents/{doc_id}/work-plan/sequence-candidates", json={"mode": "append"},
        )
        assert seq_resp.status_code == 200

    assert len(seen) == 3
    assert all(project_id == PROJECT for project_id in seen)


def test_apply_preview_and_apply_pass_the_document_project_id(seed, storage_root):
    """0429 T0004 작업 4: _preview_sync / _apply_sync also forward project_id — mock
    the apply-service calls themselves (their own contract is out of this task's scope)
    and only observe what load_body() received."""
    from modules.flow_gate.documents.routers import work_plan as wp_router

    client = _client()
    doc_id = _create_plan(client, ROOT_DOC, "0091-WP")

    seen: list[Optional[str]] = []
    real_load_body = wp_router.wp.load_body

    def _spy(path, project_id=None):
        seen.append(project_id)
        return real_load_body(path, project_id=project_id)

    with patch.object(wp_router.wp, "load_body", side_effect=_spy), \
         patch.object(wp_router.wpa, "preview", return_value={"ok": True}), \
         patch.object(wp_router.wpa, "apply", return_value={"ok": True}):
        preview_resp = client.post(f"/api/v1/documents/{doc_id}/work-plan/apply/preview", json={})
        assert preview_resp.status_code == 200
        apply_resp = client.post(f"/api/v1/documents/{doc_id}/work-plan/apply", json={
            "instruction_mode": "auto_approved",
            "change_workflow": False,
            "workflow_tag": "x",
            "wp_revision_no": 0,
        })
        assert apply_resp.status_code == 200

    assert len(seen) == 2
    assert all(project_id == PROJECT for project_id in seen)


def test_fill_token_issuance_passes_the_document_project_id(seed, storage_root, monkeypatch):
    """The sixth load_body() call site — request_work_plan_fill's own internal read."""
    from modules.flow_gate.services import mention_service, token_service
    from modules.flow_gate.services import work_plan_service as wp

    client = _client()
    doc_id = _create_plan(client, ROOT_DOC, "0092-WP")

    seen: list[Optional[str]] = []
    real_load_body = wp.load_body

    def _spy(path, project_id=None):
        seen.append(project_id)
        return real_load_body(path, project_id=project_id)

    monkeypatch.setattr(wp, "load_body", _spy)
    monkeypatch.setattr(token_service, "issue", lambda **kwargs: {
        "raw_token": "tok", "token_id": "tid", "expires_at": "later",
        "scratch_dir": "C:/scratch/tok_1",
    })
    monkeypatch.setattr(mention_service, "build_work_plan_fill_mention", lambda **kwargs: "mention body")

    result = wp.request_work_plan_fill(doc_id, "worker-1", "http://localhost:8000", scope={})
    assert result["mention"] == "mention body"
    assert seen == [PROJECT]
