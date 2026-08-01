"""Tests for the design-document template body provision feature (group 0024).

Covers: migrations 043/044 schema, the L0013 resolution algorithm (5 ranks +
ko fallback + path graceful-degrade), type-default skeletons, P0011 CRUD (E4/E5/E6)
including the ko-fallback delete guard and idempotent PUT, and P0012 provision meta.

flowgate.default.0024.0015-T
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


# ─────────────────────────────────────────────────────────────────────────────
# Pure-logic tests (no DB)
# ─────────────────────────────────────────────────────────────────────────────
from modules.flow_gate import template_provision as tp  # noqa: E402


class TestPureLogic:
    def test_normalize_locale(self):
        assert tp.normalize_locale("ja") == "ja"
        assert tp.normalize_locale("ko") == "ko"
        assert tp.normalize_locale("en") == "en"
        assert tp.normalize_locale("zh") == "ko"      # unsupported → ko
        assert tp.normalize_locale("") == "ko"         # blank → ko
        assert tp.normalize_locale(None) == "ko"       # absent → ko
        assert tp.normalize_locale("  ja ") == "ja"    # trimmed

    def test_contains_path(self):
        assert tp.contains_path(r"C:\workspace\templates\x.md")
        assert tp.contains_path("see _rule/templates/template_design.md")
        assert tp.contains_path("legacy _old/templates/x")
        assert tp.contains_path("Template: /etc/foo")
        assert not tp.contains_path("# 기본설계\n## 1. 배경")
        assert not tp.contains_path("")

    def test_build_type_default_known(self):
        for tc in ("D", "P", "L", "DB"):
            sk = tp.build_type_default(tc)
            assert sk.startswith("#")
            assert not tp.contains_path(sk)

    def test_build_type_default_unknown_falls_back(self):
        sk = tp.build_type_default("ZZ")          # not in skeleton map
        assert "ZZ" in sk
        assert not tp.contains_path(sk)

    def test_validate_locale(self):
        tp.validate_locale("ko")
        with pytest.raises(tp.TemplateValidationError):
            tp.validate_locale("zh")

    def test_validate_content(self):
        tp.validate_content("# ok")
        with pytest.raises(tp.TemplateValidationError):
            tp.validate_content("")
        with pytest.raises(tp.TemplateValidationError):
            tp.validate_content("   \n  ")
        with pytest.raises(tp.TemplateValidationError):
            tp.validate_content("x" * (tp.MAX_CONTENT_BYTES + 1))


# ─────────────────────────────────────────────────────────────────────────────
# DB-integration tests
# ─────────────────────────────────────────────────────────────────────────────
class _TestStore:
    """Minimal FlowGateStore stand-in over a real migrated sqlite DB."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        cur = self._conn.execute(sql, params or [])
        row = cur.fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        cur = self._conn.execute(sql, params or [])
        return [dict(r) for r in cur.fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "tpl.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    for f in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    # 045 seeds global standard bodies for D/P/L/DB. The rank-resolution unit tests
    # below set up their own registry state from an EMPTY baseline, so neutralise the
    # seed here. The seed itself is covered separately by TestSeededGlobalTemplates
    # (seeded_store fixture, which keeps it).
    conn.execute("DELETE FROM document_type_template_contents")
    conn.execute("DELETE FROM document_type_templates")
    conn.executescript(
        """
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('flowgate','FlowGate',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,
                                    first_login_required,created_at,updated_at)
            VALUES('u1','admin','a@t.com','pw',1,1,0,datetime('now'),datetime('now'));
        """
    )
    conn.commit()
    conn.close()

    s = _TestStore(db_path)
    with patch("modules.flow_gate.db.templates.get_store", return_value=s), \
         patch("modules.flow_gate.settings.project_settings_service.get_store", return_value=s):
        yield s


@pytest.fixture
def seeded_store(tmp_path):
    """Like `store` but KEEPS the 045 global-template seed — the as-deployed default
    state. Verifies that a fresh DB resolves real standard bodies out of the box,
    with no manual E4/E5 registration (TR0016 rev3 — 'are you going to keep it from ever showing up' fix)."""
    db_path = str(tmp_path / "tpl_seeded.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    for f in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        conn.executescript(f.read_text(encoding="utf-8"))
    conn.executescript(
        """
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('flowgate','FlowGate',1,datetime('now'),datetime('now'));
        """
    )
    conn.commit()
    conn.close()

    s = _TestStore(db_path)
    with patch("modules.flow_gate.db.templates.get_store", return_value=s), \
         patch("modules.flow_gate.settings.project_settings_service.get_store", return_value=s):
        yield s


# import after patch target exists
from modules.flow_gate.db import templates as tdb            # noqa: E402
from modules.flow_gate.settings import project_settings_service as svc  # noqa: E402


class TestSchema:
    def test_contents_table_and_fk_cascade(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        assert tdb.upsert_content(tid, "ko", "# body", "u1") is True
        assert tdb.content_for(tid, "ko") == "# body"
        # CASCADE: deleting the registry row removes its bodies
        store._execute("DELETE FROM document_type_templates WHERE id = ?", [tid])
        assert tdb.content_for(tid, "ko") is None

    def test_is_design_type(self, store):
        assert tdb.is_design_type("D")
        assert tdb.is_design_type("DB")
        assert not tdb.is_design_type("ZZ")


class TestResolution:
    def _activate(self, store, project_id, type_code):
        store._execute(
            "UPDATE document_type_templates SET is_active = 1 "
            "WHERE project_id IS ? AND type_code = ?",
            [project_id, type_code],
        )

    def test_rank1_project_exact(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        tdb.upsert_content(tid, "ja", "# 基本設計", "u1")
        self._activate(store, "flowgate", "D")
        r = tp.resolve_active_template("flowgate", "D", "ja")
        assert r["resolution"] == "exact"
        assert r["scope"] == "project"
        assert r["resolved_locale"] == "ja"
        assert r["content"] == "# 基本設計"
        assert r["is_active"] == 1

    def test_rank2_project_ko_fallback(self, store):
        tid = tdb.ensure_registry("flowgate", "P", "u1")
        tdb.upsert_content(tid, "ko", "# 프로토콜", "u1")
        self._activate(store, "flowgate", "P")
        r = tp.resolve_active_template("flowgate", "P", "ja")  # no ja → ko
        assert r["resolution"] == "fallback-ko"
        assert r["resolved_locale"] == "ko"
        assert r["scope"] == "project"

    def test_rank4_global_fallback(self, store):
        # global row only (project_id NULL), ko body, activated
        store._execute(
            "INSERT INTO document_type_templates(project_id,type_code,template_path,"
            "is_active,uploaded_by,uploaded_at) VALUES(NULL,'L',NULL,1,'u1',datetime('now'))",
        )
        gid = store._fetch_one(
            "SELECT id FROM document_type_templates WHERE project_id IS NULL AND type_code='L'"
        )["id"]
        tdb.upsert_content(gid, "ko", "# 전역 로직", "u1")
        r = tp.resolve_active_template("flowgate", "L", "ja")
        assert r["resolution"] == "global-fallback-ko"
        assert r["scope"] == "global"
        assert r["content"] == "# 전역 로직"

    def test_rank5_type_default_for_db(self, store):
        # DB has no template at all → type-default skeleton, never blocks
        r = tp.resolve_active_template("flowgate", "DB", "ko")
        assert r["resolution"] == "type-default"
        assert r["scope"] is None
        assert r["resolved_locale"] is None
        assert r["is_active"] is None
        assert r["content"].startswith("# DB설계")

    def test_inactive_not_served(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")   # is_active=0
        tdb.upsert_content(tid, "ko", "# inactive", "u1")
        r = tp.resolve_active_template("flowgate", "D", "ko")
        assert r["resolution"] == "type-default"            # not exposed until activated

    def test_path_polluted_body_falls_through(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        tdb.upsert_content(tid, "ko", r"Template: C:\x\template_design.md", "u1")
        self._activate(store, "flowgate", "D")
        r = tp.resolve_active_template("flowgate", "D", "ko")
        # polluted body is skipped → type-default (graceful degrade, never blocks)
        assert r["resolution"] == "type-default"

    def test_unknown_type_raises(self, store):
        with pytest.raises(tp.UnknownDesignType):
            tp.resolve_active_template("flowgate", "ZZ", "ko")

    def test_active_empty_falls_through_to_global(self, store):
        # project active row but NO body → must fall through to global
        ptid = tdb.ensure_registry("flowgate", "L", "u1")
        self._activate(store, "flowgate", "L")
        store._execute(
            "INSERT INTO document_type_templates(project_id,type_code,template_path,"
            "is_active,uploaded_by,uploaded_at) VALUES(NULL,'L',NULL,1,'u1',datetime('now'))",
        )
        gid = store._fetch_one(
            "SELECT id FROM document_type_templates WHERE project_id IS NULL AND type_code='L'"
        )["id"]
        tdb.upsert_content(gid, "ko", "# 전역", "u1")
        r = tp.resolve_active_template("flowgate", "L", "ko")
        assert r["scope"] == "global"
        assert r["resolved_template_id"] == gid


class TestCrud:
    def test_e4_register_then_e1_summary(self, store):
        out = svc.register_template_content("flowgate", "DB", "ko", "# DB body", "u1")
        assert out["type_code"] == "DB"
        assert out["is_active"] == 0           # new registry is inactive (review gate)
        assert out["locales"] == ["ko"]

    def test_e4_invalid_locale_and_empty(self, store):
        with pytest.raises(tp.TemplateValidationError):
            svc.register_template_content("flowgate", "DB", "zh", "# x", "u1")
        with pytest.raises(tp.TemplateValidationError):
            svc.register_template_content("flowgate", "DB", "ko", "  ", "u1")

    def test_e5_put_created_then_idempotent(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        r1 = svc.put_template_content("flowgate", tid, "en", "# EN", "u1")
        assert r1 is not None and r1["created"] is True
        r2 = svc.put_template_content("flowgate", tid, "en", "# EN v2", "u1")
        assert r2["created"] is False           # update, not insert
        assert tdb.content_for(tid, "en") == "# EN v2"

    def test_e5_not_owned_returns_none(self, store):
        # template_id that doesn't belong to flowgate
        assert svc.put_template_content("flowgate", 99999, "ko", "# x", "u1") is None

    def test_e6_ko_fallback_guard(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        tdb.upsert_content(tid, "ko", "# ko", "u1")
        tdb.upsert_content(tid, "ja", "# ja", "u1")
        # deleting ko while ja exists is blocked
        with pytest.raises(svc.KoFallbackProtected):
            svc.delete_template_content("flowgate", tid, "ko")
        # deleting ja is fine
        assert svc.delete_template_content("flowgate", tid, "ja") is True
        # now ko is the only locale → deletable
        assert svc.delete_template_content("flowgate", tid, "ko") is True

    def test_e2_e3_content_access(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        tdb.upsert_content(tid, "ko", "# 한국어 본문", "u1")
        meta = svc.list_template_contents("flowgate", tid)
        assert meta is not None and meta[0]["locale"] == "ko"
        assert meta[0]["bytes"] == len("# 한국어 본문".encode("utf-8"))
        row = svc.get_template_content("flowgate", tid, "ko")
        assert row["content"] == "# 한국어 본문"


class TestProvisionMeta:
    def test_available_locales_enumerated_on_resolved_row(self, store):
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        tdb.upsert_content(tid, "ko", "# ko", "u1")
        tdb.upsert_content(tid, "ja", "# ja", "u1")
        store._execute(
            "UPDATE document_type_templates SET is_active=1 WHERE id=?", [tid]
        )
        meta = tp.resolve_active_meta("flowgate", "D", "ja")
        assert meta["available_locales"] == ["ko", "ja"]   # SUPPORTED_LOCALES order
        assert meta["resolved_locale"] == "ja"
        assert "content" not in meta

    def test_type_default_meta_empty_locales(self, store):
        meta = tp.resolve_active_meta("flowgate", "DB", "ko")
        assert meta["available_locales"] == []
        assert meta["resolution"] == "type-default"


class TestHttpRoutes:
    """End-to-end route wiring (admin bypasses permission gates)."""

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from modules.flow_gate.settings.routers.project_settings import router
        from modules.flow_gate.auth.middleware import get_current_user

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "is_admin": 1}
        return TestClient(app)

    def test_g1_active_literal_disambiguated_from_template_id(self, store):
        # `active` must route to G1, not the integer {template_id} routes.
        c = self._client()
        resp = c.get("/api/v1/projects/flowgate/templates/active/DB", headers={"x-locale": "ko"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"] == "type-default"      # DB unregistered → skeleton
        assert "content" in body
        assert "C:\\" not in body["content"]              # AC-1: no path

    def test_g1_unknown_type_404(self, store):
        c = self._client()
        resp = c.get("/api/v1/projects/flowgate/templates/active/ZZ")
        assert resp.status_code == 404

    def test_e4_e5_e6_g1_full_cycle(self, store):
        c = self._client()
        # E4: register DB ko body (registry created inactive)
        r = c.post("/api/v1/projects/flowgate/templates",
                   json={"type_code": "DB", "locale": "ko", "content": "# DB ko"})
        assert r.status_code == 201
        assert r.json()["locales"] == ["ko"]
        tid = r.json()["id"]
        # E7: activate
        assert c.patch(f"/api/v1/projects/flowgate/templates/{tid}",
                       json={"is_active": 1}).status_code == 200
        # E5: add ja body
        r5 = c.put(f"/api/v1/projects/flowgate/templates/{tid}/contents/ja",
                   json={"content": "# DB ja"})
        assert r5.status_code == 200 and r5.json()["created"] is True
        # G1: ja now served exact
        g = c.get("/api/v1/projects/flowgate/templates/active/DB", headers={"x-locale": "ja"})
        assert g.json()["resolution"] == "exact" and g.json()["content"] == "# DB ja"
        # G2 meta: both locales, no body
        m = c.get("/api/v1/projects/flowgate/templates/active/DB/meta", headers={"x-locale": "ja"})
        assert m.json()["available_locales"] == ["ko", "ja"]
        assert "content" not in m.json()
        # E6: deleting ko while ja exists → 409
        assert c.delete(f"/api/v1/projects/flowgate/templates/{tid}/contents/ko").status_code == 409

    def test_e5_invalid_locale_422(self, store):
        c = self._client()
        tid = tdb.ensure_registry("flowgate", "D", "u1")
        r = c.put(f"/api/v1/projects/flowgate/templates/{tid}/contents/zh",
                  json={"content": "# x"})
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Worker-facing template pointer (group 0372 set 2)
#
# The complete template body is fetched from the authenticated help item. Mentions
# and AC/RJ self-contained sections keep only one actionable pointer line.
# ─────────────────────────────────────────────────────────────────────────────
from modules.flow_gate.services.mention_service import build_mention  # noqa: E402
from modules.flow_gate import process_service as _ps                  # noqa: E402


def _mention_new(head_type, project="flowgate"):
    return build_mention(
        project=project, module="default", group="0024",
        parent_type="R", parent_doc_number="R0001", parent_title="root",
        parent_doc_id="R0001",
        head_type=head_type, head_status="pending",
        scratch_dir="/s", raw_token="tok",
        api_base_url="http://localhost:8000/flowgate/api/v1",
        action_scope="new",
    )


class TestWorkerMentionEmbed:
    def test_is_design_type_reexport(self, store):
        assert tp.is_design_type("D") is True
        assert tp.is_design_type("DB") is True
        assert tp.is_design_type("ZZ") is False

    def test_new_handoff_points_to_help_without_inline_skeleton(self, store):
        m = _mention_new("D")
        assert "## Document template" in m
        assert "GET http://localhost:8000/flowgate/api/v1/help/items/design_template/D" in m
        assert "## 다음 문서 템플릿" not in m
        assert "## 1. 배경" not in m
        assert "Template:" not in m

    def test_registered_body_is_not_duplicated_in_the_mention(self, store):
        store._execute(
            "INSERT INTO document_type_templates(project_id,type_code,template_path,"
            "is_active,uploaded_by,uploaded_at) VALUES(NULL,'D',NULL,1,'u1',datetime('now'))",
        )
        gid = store._fetch_one(
            "SELECT id FROM document_type_templates WHERE project_id IS NULL AND type_code='D'"
        )["id"]
        tdb.upsert_content(gid, "ko", "# 전역 기본설계 본문", "u1")
        m = _mention_new("D")
        assert "design_template/D" in m
        assert "# 전역 기본설계 본문" not in m
        assert "출처: 전역 표준 템플릿" not in m

    def test_edit_mention_points_to_the_design_parent_type(self, store):
        m = build_mention(
            project="flowgate", module="default", group="0024",
            parent_type="D", parent_doc_number="D0004", parent_title="design",
            parent_doc_id="D0004", parent_canonical_doc_id="flowgate.default.0024.0004-D",
            head_type="", head_status="",
            scratch_dir="/s", raw_token="tok",
            api_base_url="http://localhost:8000/flowgate/api/v1",
            action_scope="edit",
        )
        assert "## Document template" in m
        assert "/help/items/design_template/D" in m
        assert "## 다음 문서 템플릿" not in m

    def test_no_template_section_for_non_design(self, store):
        m = _mention_new("TR")
        assert "## Document template" not in m
        assert "design_template/TR" not in m

    def test_self_contained_path_uses_the_same_help_pointer(self, store):
        pointer = tp.render_help_pointer("D", "ko")
        assert _ps._render_design_template_section("D", "ko") == ["", pointer]
        assert pointer.endswith("/help/items/design_template/D로 받으세요.")


# ─────────────────────────────────────────────────────────────────────────────
# Migration 045 — seeded global standard templates (TR0016 rev3)
#
# The rev2 rejection ("so you'll just keep it from ever showing up?"): with 043/044 creating only
# EMPTY tables, resolution forever fell to the type-default skeleton (the "표준 템플릿
# 미등록" / "standard template not registered" badge) because nobody manually runs E4/E5 per project. 045 seeds a GLOBAL standard
# body for every design type, so a freshly migrated DB resolves real bodies out of the
# box — no manual registration, no redeploy. Project E4/E5 overrides still win.
# ─────────────────────────────────────────────────────────────────────────────
class TestSeededGlobalTemplates:
    def test_every_design_type_resolves_global_exact_out_of_the_box(self, seeded_store):
        # No manual registration whatsoever — just the migrated (seeded) DB.
        for tc in ("D", "P", "L", "DB"):
            r = tp.resolve_active_template("anyproject", tc, "ko")
            assert r["resolution"] == "global-exact", tc
            assert r["scope"] == "global", tc
            assert r["resolved_locale"] == "ko", tc
            assert r["is_active"] == 1, tc
            assert "미등록" not in r["content"], tc          # not the skeleton badge
            assert not tp.contains_path(r["content"]), tc    # AC-1

    def test_seeded_mention_still_keeps_the_body_behind_help(self, seeded_store):
        m = _mention_new("D", project="anyproject")
        assert "/help/items/design_template/D" in m
        assert "## 다음 문서 템플릿" not in m
        assert "출처: 전역 표준 템플릿" not in m
        assert "전역 표준 기본설계 템플릿" not in m
        assert "Template:" not in m and "C:\\" not in m

    def test_seeded_outlines_are_machine_validatable_in_every_locale(self, seeded_store):
        for tc in ("D", "P", "L", "DB"):
            for loc in ("ko", "ja", "en"):
                resolved = tp.resolve_active_template("anyproject", tc, loc)
                headings = tp.required_document_headings(resolved["content"])
                assert len(headings) >= 3, (tc, loc, headings)
                authored_headings = [
                    "[normal1] Example"
                    if any(marker in heading.casefold() for marker in ("scenario", "시나리오", "シナリオ"))
                    else heading
                    for heading in headings
                ]
                document = "# authored\n\n" + "\n\n".join(
                    f"## {heading}\nfilled" for heading in authored_headings
                )
                result = tp.validate_design_document_structure(
                    "anyproject", tc, loc, document
                )
                assert result["valid"] is True, (tc, loc, result)
                assert result["locale"] == loc, (tc, loc, result)

    def test_every_supported_locale_resolves_directly(self, seeded_store):
        # R0001 i18n: ko/ja/en are each seeded, so a ja/en request resolves to that
        # locale DIRECTLY — NOT a ko fallback. (rev4 rejection: 'you only did the locale for Korean')
        for tc in ("D", "P", "L", "DB"):
            for loc in ("ko", "ja", "en"):
                r = tp.resolve_active_template("anyproject", tc, loc)
                assert r["resolution"] == "global-exact", (tc, loc)
                assert r["resolved_locale"] == loc, (tc, loc)      # direct hit, no ko fold
                assert not tp.contains_path(r["content"]), (tc, loc)

    def test_available_locales_lists_all_supported(self, seeded_store):
        m = tp.resolve_active_meta("anyproject", "D", "en")
        assert m["available_locales"] == ["ko", "ja", "en"]   # SUPPORTED_LOCALES order

    def test_seed_follows_legacy_template_format_not_skeleton(self, seeded_store):
        # rev4 rejection: 'the format doesn't even match either'. The seeded bodies must reproduce the
        # absorbed legacy template format (judgment-criteria table + per-section writing criteria + checklist),
        # not the old flat 5-heading skeleton.
        d = tp.resolve_active_template("anyproject", "D", "ko")["content"]
        assert "## 판단 기준 — D에 쓸 수 있는 것 / 없는 것" in d
        assert "## 섹션별 작성 기준" in d
        assert "## 체크리스트 (작성 완료 전 확인)" in d
        l = tp.resolve_active_template("anyproject", "L", "ko")["content"]
        assert "### 1. 파라미터 정의" in l and "## 체크리스트" in l
        p = tp.resolve_active_template("anyproject", "P", "ko")["content"]
        assert "S: 서버가 클라이언트에게 보내는 데이터" in p   # S/C notation absorbed

    def test_unsupported_raw_locale_folds_to_seeded_ko(self, seeded_store):
        # A locale outside SUPPORTED_LOCALES that reaches resolve directly still folds
        # to the seeded ko body — graceful, never blocks. The fold now happens at the
        # resolve entry itself (normalize_locale, landed after 2026-07-04 with the
        # i18n hardening), so the seeded ko row is an EXACT hit for the folded
        # request rather than a per-row fallback (pre-existing stale expectation
        # fixed alongside flowgate.default.0226 — behavior itself is unchanged).
        r = tp.resolve_active_template("anyproject", "P", "fr")
        assert r["resolution"] == "global-exact"
        assert r["resolved_locale"] == "ko"

    def test_project_override_still_wins_over_seed(self, seeded_store):
        # E4/E5 project registration must take precedence over the global seed.
        tid = tdb.ensure_registry("flowgate", "D", None)
        tdb.upsert_content(tid, "ko", "# 프로젝트 전용 본문", None)
        seeded_store._execute(
            "UPDATE document_type_templates SET is_active=1 WHERE id=?", [tid]
        )
        r = tp.resolve_active_template("flowgate", "D", "ko")
        assert r["resolution"] == "exact"
        assert r["scope"] == "project"
        assert r["content"] == "# 프로젝트 전용 본문"

    def test_seed_is_idempotent_on_reapply(self, seeded_store):
        # Re-applying 045 (sqloader re-run / manual) must not duplicate rows.
        sql = (_MIGRATIONS_DIR / "045_seed_global_design_templates.sql").read_text(
            encoding="utf-8"
        )
        seeded_store._conn.executescript(sql)
        seeded_store._conn.commit()
        n_reg = seeded_store._fetch_one(
            "SELECT COUNT(*) c FROM document_type_templates WHERE project_id IS NULL"
        )["c"]
        n_body = seeded_store._fetch_one(
            "SELECT COUNT(*) c FROM document_type_template_contents"
        )["c"]
        assert n_reg == 4
        assert n_body == 12   # 4 design types × 3 locales (ko/ja/en)
