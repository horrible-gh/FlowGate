"""flowgate.default.0443 T0002 (R0001): ContinuousWorkDialog's 기본 설정 탭 gained a 재시작
횟수 select (-1/0/1/2/3, default 1) — how many times a no-output hop retries on the SAME
step-assigned provider (never a different one) before giving up. This replaces the fixed
NO_OUTPUT_MAX_ATTEMPTS(=2, i.e. "exactly one retry") with a per-run pick:

  * -1 ("될 때까지") — unlimited retries, same provider only
  *  0 ("재실행 안 함") — no retry at all (first attempt is the only attempt)
  *  1 (default) — exactly one retry, reproducing the pre-existing fixed behavior byte-for-byte
  *  2, 3 — that many retries

Covers:
  * _resolve_restart_max_attempts: dialog restart-count -> total-attempts conversion, -1 stays
    -1, an unset/unrecognized value falls back to the default (1 restart == 2 attempts, the
    old NO_OUTPUT_MAX_ATTEMPTS behavior).
  * _retry_eligible / _retry_provider_chain: the cap is now run["attempts_max"] (falling back
    to NO_OUTPUT_MAX_ATTEMPTS for a run/row with no pick, e.g. pre-migration), -1 never caps,
    and every retry still resolves to the SAME selected provider — no fallback tail.
  * POST /api/v1/ai-invoke/start: an out-of-choice value is rejected 422, a valid value reaches
    ai_invoke_service.start_run unchanged, and omitting the field keeps working exactly as
    before (backward compatible default).
  * _maybe_auto_resume_hop / _spawn_auto_resume: the pick rides the run forward across a hop
    re-spawn, mirroring the 0400 step-timeout coverage this file's shape is copied from.

Migration 086's column presence is covered here too (mirrors 0400's migration 079a test).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GROUP = "flowgate.default.0443"
ROOT_DOC = "flowgate.default.0443.0001-R"


# ── _resolve_restart_max_attempts (unit) ──────────────────────────────────────

class TestResolveRestartMaxAttempts:
    def test_default_is_one_restart_two_total_attempts(self):
        assert svc._resolve_restart_max_attempts(None) == 2

    def test_minus_one_stays_minus_one(self):
        assert svc._resolve_restart_max_attempts(-1) == -1

    @pytest.mark.parametrize("restart_count, expected_total", [(0, 1), (1, 2), (2, 3), (3, 4)])
    def test_every_dialog_option_converts_to_total_attempts(self, restart_count, expected_total):
        assert svc._resolve_restart_max_attempts(restart_count) == expected_total

    @pytest.mark.parametrize("bogus", [4, -2, 99, "1"])
    def test_unrecognized_value_falls_back_to_default(self, bogus):
        assert svc._resolve_restart_max_attempts(bogus) == 2


# ── _retry_eligible / _retry_provider_chain with a configurable cap ──────────

def _judged_run(**over):
    run = {
        "mode": "continuous", "cancel_event": threading.Event(), "end_reason": "exited",
        "pause_requested": False, "completion_oracle": None, "action_scope": "new",
        "docs_target": 1, "docs_reached": 0, "attempts_used": 1, "group_id": GROUP,
        "started_mono": time.monotonic(), "timeout_sec": 3600, "outcome": "none",
    }
    run.update(over)
    return run


class TestRetryEligibilityConfigurableCap:
    def test_no_attempts_max_falls_back_to_the_fixed_constant(self):
        # A run/row from before this feature (or the resume path for one) has no
        # attempts_max at all — must behave exactly like the old fixed constant.
        assert svc._retry_eligible(_judged_run(attempts_used=1)) is True
        assert svc._retry_eligible(_judged_run(attempts_used=2)) is False

    def test_zero_restart_max_attempts_blocks_even_the_first_retry(self):
        # "재실행 안 함" (0) — attempts_max resolves to 1 total attempt, so attempt 1
        # already used up the whole budget.
        assert svc._retry_eligible(_judged_run(attempts_used=1, attempts_max=1)) is False

    def test_higher_restart_count_allows_more_attempts(self):
        # restart count 3 -> attempts_max 4: eligible through attempt 3, blocked at 4.
        assert svc._retry_eligible(_judged_run(attempts_used=3, attempts_max=4)) is True
        assert svc._retry_eligible(_judged_run(attempts_used=4, attempts_max=4)) is False

    def test_minus_one_never_caps(self):
        # "될 때까지" (-1) — no attempts_used value should ever trip the cap.
        for used in (1, 2, 10, 1000):
            assert svc._retry_eligible(_judged_run(attempts_used=used, attempts_max=-1)) is True


class TestRetryProviderChainConfigurableCap:
    def test_no_attempts_max_falls_back_to_the_fixed_constant(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x", "attempts_used": 1,
               "continuation_selected_provider_id": "aip_2"}
        assert [p["id"] for p in svc._retry_provider_chain(run)] == ["aip_2"]
        run["attempts_used"] = 2
        assert svc._retry_provider_chain(run) == []

    def test_zero_restart_max_attempts_returns_no_retry_chain(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x", "attempts_used": 1,
               "attempts_max": 1, "continuation_selected_provider_id": "aip_2"}
        assert svc._retry_provider_chain(run) == []

    def test_higher_restart_count_keeps_returning_the_SAME_provider_only(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x", "attempts_used": 1,
               "attempts_max": 4, "continuation_selected_provider_id": "aip_2"}
        for used in (1, 2, 3):
            run["attempts_used"] = used
            assert [p["id"] for p in svc._retry_provider_chain(run)] == ["aip_2"]
        run["attempts_used"] = 4
        assert svc._retry_provider_chain(run) == []

    def test_minus_one_keeps_retrying_the_same_provider_indefinitely(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x",
               "attempts_max": -1, "continuation_selected_provider_id": "aip_2"}
        for used in (1, 5, 50):
            run["attempts_used"] = used
            assert [p["id"] for p in svc._retry_provider_chain(run)] == ["aip_2"]

    def test_still_does_not_substitute_when_selected_provider_is_inactive(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x", "attempts_used": 1,
               "attempts_max": 4, "continuation_selected_provider_id": "aip_removed"}
        assert svc._retry_provider_chain(run) == []


@pytest.fixture
def env(monkeypatch):
    chain = [
        {"id": "aip_1", "name": "P1", "exec_type": "cli", "kind": "claude",
         "enabled": True, "cli_command": "noop", "api_base_url": None,
         "api_model": None, "api_key_set": False, "api_key_hint": None},
        {"id": "aip_2", "name": "P2", "exec_type": "cli", "kind": "claude",
         "enabled": True, "cli_command": "noop", "api_base_url": None,
         "api_model": None, "api_key_set": False, "api_key_hint": None},
    ]
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective",
        lambda pid: {"providers": chain, "source": "system"},
    )
    return {"chain": chain}


# ── POST /api/v1/ai-invoke/start (route-level validation) ─────────────────────

_ITEMS = [
    {"id": 1, "item_seq": 1, "type": "T", "label": "작업지시",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 2, "item_seq": 2, "type": "TR", "label": "작업레포트",
     "result_doc_id": None, "result_doc_review_status": None},
]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from modules.flow_gate.api.v1 import ai_invoke_routes
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: list(_ITEMS))
    return TestClient(app)


def _route_body(**over):
    body = {
        "project": "flowgate", "module": "default", "group": "0443",
        "doc_ref": ROOT_DOC, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": 2,
    }
    body.update(over)
    return body


def _post_start(client, **over):
    return client.post(
        "/api/v1/ai-invoke/start",
        json=_route_body(**over),
        headers={"Authorization": "Bearer tok"},
    )


class TestStartRouteValidation:
    @pytest.mark.parametrize("choice", [-1, 0, 1, 2, 3])
    def test_valid_choices_reach_start_run(self, client, monkeypatch, choice):
        from modules.flow_gate.api.v1 import ai_invoke_routes

        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"run_id": "aiv_0443", "status": "running"}

        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

        resp = _post_start(client, continuation_restart_max_attempts=choice)

        assert resp.status_code == 200, resp.text
        assert captured["continuation_restart_max_attempts"] == choice

    def test_omitted_field_defaults_to_none_and_still_starts(self, client, monkeypatch):
        from modules.flow_gate.api.v1 import ai_invoke_routes

        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"run_id": "aiv_0443", "status": "running"}

        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

        resp = _post_start(client)

        assert resp.status_code == 200, resp.text
        assert captured["continuation_restart_max_attempts"] is None

    @pytest.mark.parametrize("bogus", [4, -2, 99])
    def test_out_of_choice_value_is_rejected_422_by_the_real_route(
        self, client, monkeypatch, bogus,
    ):
        from modules.flow_gate.api.v1 import ai_invoke_routes

        monkeypatch.setattr(
            ai_invoke_routes.ai_invoke_service,
            "start_run",
            lambda **kw: pytest.fail("must not reach start_run"),
        )

        resp = _post_start(client, continuation_restart_max_attempts=bogus)

        assert resp.status_code == 422
        assert resp.json()["errors"][0]["loc"] == "continuation_restart_max_attempts"


# ── auto-resume handoff (mirrors test_ai_invoke_step_timeout_0400's shape) ────

class TestRestartCountRunDictAndAutoResume:
    @pytest.fixture(autouse=True)
    def _clean_registries(self):
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()
        yield
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()

    def test_maybe_resume_carries_restart_max_attempts_forward(self, monkeypatch):
        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc.request_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        run = {
            "group_id": GROUP, "end_reason": "exited", "cancel_event": None,
            "continuation_provider_overrides": None,
            "continuation_note_overrides": None,
            "continuation_default_note": None,
            "continuation_step_timeout_sec": None,
            "continuation_restart_max_attempts": 3,
        }
        svc._maybe_auto_resume_hop(run)
        assert len(calls) == 1
        _g, pending = calls[0]
        assert pending["restart_max_attempts"] == 3

    def test_spawn_passes_restart_max_attempts_to_start_run(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
            "restart_max_attempts": 3,
        })
        assert captured["continuation_restart_max_attempts"] == 3

    def test_spawn_without_a_pick_forwards_none(self, monkeypatch):
        # A pending hop queued before this feature (or with no pick made) must not crash
        # _spawn_auto_resume — pending.get() degrades cleanly to None (-> engine default).
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        assert captured["continuation_restart_max_attempts"] is None


# ── migration 086 (schema) ─────────────────────────────────────────────────────

def test_migration_086_adds_restart_max_attempts_column(test_db):
    cols = {
        row["name"]: row
        for row in test_db.execute("PRAGMA table_info(ai_invoke_paused_chains)").fetchall()
    }
    assert "continuation_restart_max_attempts" in cols
    assert cols["continuation_restart_max_attempts"]["notnull"] == 0


# ── TR0005 review: a -1 ("될 때까지") run must actually reach ai_invoke_runs ───
#
# Before this migration's CHECK widening, run["attempts_max"] == -1 reached
# _persist_run_record -> db_runs.upsert() -> a real INSERT whose CHECK only allowed
# NULL or >= 1. The IntegrityError was swallowed by _persist_run_record's own
# try/except (L0007 §5), so the hop finished normally but left no durable row at
# all. A fake/dict-backed store (as used elsewhere in this repo's ai-invoke tests)
# has no CHECK constraint and would stay green through that regression -- this test
# goes through the real migrated schema instead, exactly as
# test_continuous_handoff_durable_0406.py's audit round-trip does.

def test_persist_run_record_with_unlimited_attempts_max_reaches_the_real_table(test_db):
    from modules.flow_gate.db import ai_invoke_runs as db_runs
    from modules.flow_gate.db import connection as db_connection

    test_db.execute(
        "INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('flowgate', 'flowgate', 1, datetime('now'), datetime('now'))"
    )
    test_db.execute(
        "INSERT OR IGNORE INTO groups(group_id, project_id, module, title, created_at, updated_at) "
        f"VALUES ('{GROUP}', 'flowgate', 'default', '{GROUP}', datetime('now'), datetime('now'))"
    )
    test_db.commit()

    class Store:
        def _execute(self, sql, params=None):
            test_db.execute(sql, params or [])
            test_db.commit()

        def _fetch_one(self, sql, params=None):
            row = test_db.execute(sql, params or []).fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params=None):
            return [dict(r) for r in test_db.execute(sql, params or []).fetchall()]

    previous = db_connection.STORE
    db_connection.STORE = Store()
    try:
        run = {
            "run_id": "aiv_0443_unlimited", "group_id": GROUP, "project_id": "flowgate",
            "doc_ref": ROOT_DOC, "mode": "continuous", "outcome": "complete",
            "end_reason": "exited", "attempts_used": 12, "attempts_max": -1,
            "started_at": "2026-08-19T00:00:00+09:00",
            "finished_at": "2026-08-19T00:00:05+09:00",
        }
        svc._persist_run_record(run)

        got = db_runs.get("aiv_0443_unlimited")
        assert got is not None, "the -1 run's durable row was silently lost (CHECK violation swallowed)"
        assert got["attempts_max"] == -1
        assert got["attempts_used"] == 12
    finally:
        db_connection.STORE = previous
