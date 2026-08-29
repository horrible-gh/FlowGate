from __future__ import annotations

import json
import os
from contextlib import nullcontext
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta, timezone

import pytest

from modules.flow_gate.api.v1 import ai_invoke_routes as routes
from modules.flow_gate.services import ai_invoke_service as service


BASE = {
    "review_count": 2,
    "reviewer_provider_id": "reviewer",
    "review_criteria": "document_type_default",
    "rework_provider_id": "reworker",
    "rework_timeout_sec": 1800,
    "rework_message": "fix every finding",
    "failure_restart_max_attempts": 0,
    "total_timeout_sec": 3600,
    "review_baseline_id": 10,
    "baseline_revision_no": 3,
    "starts_with_rework": False,
    "round_no": 1,
    "current_stage": "review",
    "attempts_used": 0,
}


def gate(**updates):
    return service.resolve_document_review_loop_gate({**BASE, **updates})


def test_review_pass_stops_immediately_and_does_not_schedule_another_hop():
    timeline = []
    state = gate(reviews=[])
    timeline.append((state["round_no"], state["current_stage"]))
    state = gate(reviews=[{"id": 11, "verdict": "pass", "revision_no": 3}])
    timeline.append((state["round_no"], state["current_stage"], state["stop_reason"]))
    assert timeline == [(1, "review"), (1, "stopped", "review_passed")]
    assert state["stop_detail"] is None
    assert "approve" not in json.dumps(state).lower()


def test_review_reject_reworks_then_exhausts_finite_review_count():
    first = gate(reviews=[{"id": 11, "verdict": "issues", "revision_no": 3}])
    assert (first["current_stage"], first["round_no"]) == ("rework", 2)
    second = gate(reviews=[{"id": 11, "verdict": "issues"}, {"id": 12, "verdict": "hold"}])
    assert (second["current_stage"], second["round_no"]) == ("rework", 3)
    exhausted = gate(
        reviews=[{"id": 11, "verdict": "issues"}, {"id": 12, "verdict": "hold"}],
        last_hop_kind="rework", last_hop_outcome="succeeded", doc={"revision_no": 4},
    )
    assert exhausted["current_stage"] == "stopped"
    assert exhausted["stop_reason"] == "review_count_exhausted"


def test_retry_exhausted_and_total_timeout_are_terminal():
    retry = gate(last_hop_outcome="failed", attempts_used=1, failure_detail="worker failed")
    assert (retry["current_stage"], retry["stop_reason"]) == ("stopped", "retry_exhausted")
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    timeout = gate(now=now, deadline_at=now - timedelta(seconds=1))
    assert (timeout["current_stage"], timeout["stop_reason"]) == ("stopped", "total_timeout")


def test_standalone_document_loop_and_restart_use_persisted_bundle_values():
    # No workflow sequence/item_seq exists in this bundle. A fresh process receives only
    # this durable bundle and reaches the same next stage without recomputing its baseline.
    persisted = {**BASE, "round_no": 2, "current_stage": "review", "attempts_used": 1}
    before = service.resolve_document_review_loop_gate({**persisted, "reviews": []})
    restored = service.resolve_document_review_loop_gate({**dict(persisted), "reviews": []})
    assert restored == before
    assert restored["round_no"] == 2
    assert restored["current_stage"] == "review"


@pytest.mark.parametrize("override", [
    {"action_scope": "edit"},
    {"mode": "continuous"},
    {"provider_id": "forbidden"},
    {"provider_pinned": True},
    {"continuation_review_count_overrides": {"1": 1}},
    {"continuation_reviewer_overrides": {"1": "p"}},
])
def test_loop_contract_conflicts_return_422_before_database(monkeypatch, override):
    body = {
        "project": "flowgate", "module": "default", "group": "0417",
        "doc_ref": "flowgate.default.0417.0011-T", "action_scope": "review", "mode": "single",
        "document_review_loop": {key: value for key, value in BASE.items() if key in {
            "review_count", "reviewer_provider_id", "review_criteria", "rework_provider_id",
            "rework_timeout_sec", "rework_message", "failure_restart_max_attempts", "total_timeout_sec"
        }},
    }
    body.update(override)
    monkeypatch.setattr(routes, "_require_user", lambda request: {"issued_to": "u", "_is_user_jwt": True})
    response = routes.start_ai_invoke(routes.AiInvokeStartRequest(**body), object())
    assert response.status_code == 422
    assert json.loads(response.body)["code"] == "validation_failed"


@pytest.mark.parametrize("field,value", [
    ("review_count", 0), ("review_criteria", "anything"), ("rework_timeout_sec", 1),
    ("failure_restart_max_attempts", 3), ("total_timeout_sec", 1),
])
def test_loop_range_errors_return_422(monkeypatch, field, value):
    config = {key: val for key, val in BASE.items() if key in {
        "review_count", "reviewer_provider_id", "review_criteria", "rework_provider_id",
        "rework_timeout_sec", "rework_message", "failure_restart_max_attempts", "total_timeout_sec"
    }}
    config[field] = value
    body = routes.AiInvokeStartRequest(project="flowgate", module="default", group="0417",
        doc_ref="flowgate.default.0417.0011-T", action_scope="review", mode="single",
        document_review_loop=config)
    monkeypatch.setattr(routes, "_require_user", lambda request: {"issued_to": "u", "_is_user_jwt": True})
    response = routes.start_ai_invoke(body, object())
    assert response.status_code == 422
    assert field in json.loads(response.body)["errors"][0]["loc"]


def test_stage_provider_is_fixed_and_unknown_stage_fails():
    assert service.resolve_loop_provider(BASE, "review") == "reviewer"
    assert service.resolve_loop_provider(BASE, "rework") == "reworker"
    with pytest.raises(ValueError):
        service.resolve_loop_provider(BASE, "queued")


def test_successful_rework_advances_to_review_instead_of_repeating_rework():
    state = gate(
        starts_with_rework=True,
        current_stage="rework",
        last_hop_kind="rework",
        last_hop_outcome="succeeded",
        doc={"revision_no": 4},
        reviews=[{"id": 11, "verdict": "issues", "revision_no": 3}],
    )
    assert (state["round_no"], state["current_stage"]) == (1, "review")


def test_second_review_cannot_reuse_first_round_verdict():
    reviews = [{"id": 11, "verdict": "issues", "revision_no": 3}]
    assert not service.check_expected_progress(
        {**BASE, "round_no": 2, "last_hop_kind": "review"},
        {"revision_no": 4},
        reviews,
    )
    assert service.check_expected_progress(
        {**BASE, "round_no": 2, "last_hop_kind": "review"},
        {"revision_no": 4},
        reviews + [{"id": 12, "verdict": "pass", "revision_no": 4}],
    )


def test_later_rework_must_advance_past_latest_review_revision():
    reviews = [
        {"id": 11, "verdict": "issues", "revision_no": 3},
        {"id": 12, "verdict": "hold", "revision_no": 4},
    ]
    bundle = {**BASE, "round_no": 3, "last_hop_kind": "rework"}
    assert not service.check_expected_progress(bundle, {"revision_no": 4}, reviews)
    assert service.check_expected_progress(bundle, {"revision_no": 5}, reviews)


def test_completed_hop_checkpoints_durable_loop_and_updates_live_payload(monkeypatch):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews as db_reviews

    persisted = {
        **BASE,
        "run_id": "aiv_test",
        "group_id": "flowgate.default.0417",
        "doc_ref": "flowgate.default.0417.0011-T",
        "updated_at": "2026-08-29T00:00:00+00:00",
        "deadline_at": "2026-08-29T02:00:00+00:00",
        "rework_message": "fix every finding",
    }
    monkeypatch.setattr(db_loops, "get", lambda run_id: dict(persisted))
    monkeypatch.setattr(
        service, "get_store",
        lambda: type("Store", (), {"transaction": lambda self: nullcontext(self)})(),
    )
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda doc_id: {"revision_no": 3})
    monkeypatch.setattr(db_reviews, "list_by_doc", lambda doc_id: [
        {"id": 11, "verdict": "pass", "revision_no": 3}
    ])

    captured = {}
    def checkpoint(run_id, **kwargs):
        captured.update(kwargs)
        latest = {**persisted, **{k: v for k, v in kwargs.items() if not k.startswith("expected_")}}
        return True, latest
    monkeypatch.setattr(db_loops, "checkpoint", checkpoint)

    run = {"run_id": "aiv_test", "outcome": "complete", "document_review_loop": dict(persisted)}
    latest = service._checkpoint_document_review_loop(run)
    assert captured["current_stage"] == "stopped"
    assert captured["stop_reason"] == "review_passed"
    payload = service.document_review_loop_payload(run)
    assert {key: payload[key] for key in ("round_no", "current_stage", "stop_reason", "stop_detail")} == {
        "round_no": 1, "current_stage": "stopped",
        "stop_reason": "review_passed", "stop_detail": None,
    }
    # 0417 T0013 item 8: the round table rides along, rebuilt from the canonical review row
    # (id 11 > baseline 10), so a card that connects only now still gets round 1.
    assert payload["history"] == [{
        "round_no": 1, "stage": "review", "result": "passed", "verdict": "pass",
        "finding_count": 0, "revision_no": 3, "at": None,
    }]
    assert latest == run["document_review_loop"]


def test_history_orders_a_rework_first_run_and_falls_back_to_revisions(monkeypatch):
    """A run that opened on an unanswered rejection lists its rework first (deck screen 6)."""
    from modules.flow_gate.db import document_revisions as db_revisions
    monkeypatch.setattr(service.db_reviews, "list_by_doc", lambda doc_id: [
        {"id": 21, "verdict": "issues", "revision_no": 4,
         "findings": '[{"note": "x"}]', "reviewed_at": "t2"},
        {"id": 22, "verdict": "pass", "revision_no": 5, "findings": "[]", "reviewed_at": "t4"},
    ])
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda doc_id: {
        "rejection_history": json.dumps([
            {"rejection_id": "rej_pre", "responded_at": "t1", "response_revision_no": 4},
        ])
    })
    monkeypatch.setattr(db_revisions, "list_by_doc", lambda doc_id: [
        {"revision_no": 3, "created_at": "t1"}, {"revision_no": 4, "created_at": "t3"},
    ])
    rows = service.build_document_review_loop_history({
        **BASE, "doc_ref": "d", "review_baseline_id": 20,
        "baseline_revision_no": 3, "starts_with_rework": 1,
    })
    assert [(row["round_no"], row["stage"], row["result"]) for row in rows] == [
        (1, "rework", "complete"), (1, "review", "issues"),
        (2, "rework", "complete"), (2, "review", "passed"),
    ]
    assert rows[1]["finding_count"] == 1
    # Round 2's rework recorded no response text, so document_revisions is the backstop
    # that keeps the row on the table instead of dropping the round silently.
    assert rows[2]["rejection_id"] is None
    assert rows[2]["revision_no"] == 5


def test_history_survives_a_broken_revision_backstop(monkeypatch):
    from modules.flow_gate.db import document_revisions as db_revisions

    def boom(doc_id):
        raise RuntimeError("document_revisions unavailable")

    monkeypatch.setattr(service.db_reviews, "list_by_doc", lambda doc_id: [
        {"id": 5, "verdict": "issues", "revision_no": 3, "findings": "[]", "reviewed_at": "t1"},
    ])
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda doc_id: {})
    monkeypatch.setattr(db_revisions, "list_by_doc", boom)
    rows = service.build_document_review_loop_history({
        **BASE, "doc_ref": "d", "review_baseline_id": 0, "baseline_revision_no": 3,
    })
    assert [(row["round_no"], row["stage"], row["result"]) for row in rows] == [
        (1, "review", "issues"),
    ]


def test_persisted_run_detail_restores_document_review_loop(monkeypatch):
    restored = {**BASE, "current_stage": "rework", "round_no": 2, "doc_ref": "restored-doc",
                "stop_reason": None, "stop_detail": None}
    monkeypatch.setattr(service, "_restore_document_review_loop", lambda run_id: restored)
    monkeypatch.setattr(service.db_reviews, "list_by_doc", lambda doc_id: [
        {"id": 9, "verdict": "issues", "revision_no": 2, "findings": "[]", "reviewed_at": "before"},
        {"id": 11, "verdict": "issues", "revision_no": 3,
         "findings": '[{"note": "a"}, {"note": "b"}]', "reviewed_at": "2026-08-29T12:04:00+09:00"},
    ])
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda doc_id: {"rejection_history": json.dumps([
        {"rejection_id": "rej_old", "responded_at": "2026-08-01T00:00:00+09:00", "response_revision_no": 3},
        {"rejection_id": "rej_1", "responded_at": "2026-08-29T12:19:00+09:00", "response_revision_no": 4},
    ])})
    payload = service._run_detail_from_row({
        "run_id": "aiv_restart", "mode": "single", "group_id": "flowgate.default.0417"
    })
    loop = payload["document_review_loop"]
    assert {key: loop[key] for key in ("round_no", "current_stage", "stop_reason", "stop_detail")} == {
        "round_no": 2, "current_stage": "rework", "stop_reason": None, "stop_detail": None,
    }
    # A fresh process holds no observed transitions at all, yet the completed review and
    # rework rounds come back — and rows that predate the run's baseline stay out.
    assert loop["history"] == [
        {"round_no": 1, "stage": "review", "result": "issues", "verdict": "issues",
         "finding_count": 2, "revision_no": 3, "at": "2026-08-29T12:04:00+09:00"},
        {"round_no": 1, "stage": "rework", "result": "complete", "revision_no": 4,
         "rejection_id": "rej_1", "at": "2026-08-29T12:19:00+09:00"},
    ]


def test_running_status_contains_same_document_review_loop_shape(monkeypatch):
    run = {
        "run_id": "aiv_live", "status": "running", "mode": "single",
        "group_id": "flowgate.default.0417", "docs_target": 0,
        "chain_docs_target": 0, "chain_docs_reached": 0, "chain_docs_accounted": True,
        "provider": {"id": "reviewer"}, "attempt_no": 1,
        "started_at": "2026-08-29T00:00:00+00:00", "started_mono": service.time.monotonic(),
        "timeout_sec": 1800, "deadline_at": "2026-08-29T00:30:00+00:00",
        "attempts_used": 0, "attempts_max": 1,
        "document_review_loop": {**BASE, "round_no": 2, "current_stage": "review",
                                 "stop_reason": None, "stop_detail": None},
    }
    monkeypatch.setattr(service, "get_run_record", lambda run_id: run)
    monkeypatch.setattr(service, "_open_q_doc_ids", lambda group_id: [])
    monkeypatch.setattr(service, "_oracle_new_docs", lambda current: [])
    monkeypatch.setattr(service.db_reviews, "list_by_doc", lambda doc_id: [])
    monkeypatch.setattr(service.db_docs, "get_by_id", lambda doc_id: {})
    payload = service.get_status("aiv_live")
    assert payload["document_review_loop"] == {
        "round_no": 2, "current_stage": "review", "stop_reason": None, "stop_detail": None,
        "history": [],
    }

def test_start_path_inserts_complete_document_review_loop_row(monkeypatch):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    captured = {}
    monkeypatch.setattr(db_loops, "insert", lambda row: captured.update(row) or row)
    run = {
        "run_id": "aiv_insert", "group_id": "flowgate.default.0417",
        "doc_ref": "flowgate.default.0417.0011-T",
        "document_review_loop": {
            **BASE, "rework_message": "fix every finding",
            "started_at": "2026-08-29T00:00:00+00:00",
            "deadline_at": "2026-08-29T01:00:00+00:00",
        },
    }
    service._insert_document_review_loop(run)
    assert captured["run_id"] == "aiv_insert"
    assert captured["doc_ref"] == run["doc_ref"]
    assert captured["review_baseline_id"] == 10
    assert captured["current_stage"] == "review"


def test_real_sqlite_accepts_loop_before_finished_run(monkeypatch, tmp_path):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    conn = sqlite3.connect(tmp_path / "loop.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(doc_id TEXT PRIMARY KEY);
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
    """)
    migrations = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite"
    for name in ("091_ai_invoke_document_review_loops.sql",
                 "092_ai_invoke_document_review_loop_live_run.sql"):
        conn.executescript((migrations / name).read_text(encoding="utf-8"))
    conn.execute("INSERT INTO groups VALUES ('flowgate.default.0417')")
    conn.execute("INSERT INTO documents VALUES ('flowgate.default.0417.0011-T')")
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",), ("reworker",)])
    conn.commit()

    class Store:
        def _execute(self, sql, values):
            conn.execute(sql, values)
            conn.commit()
        def _fetch_one(self, sql, values):
            row = conn.execute(sql, values).fetchone()
            return dict(row) if row else None

    monkeypatch.setattr(db_loops, "get_store", Store)
    inserted = db_loops.insert({
        **BASE, "run_id": "aiv_live", "group_id": "flowgate.default.0417",
        "doc_ref": "flowgate.default.0417.0011-T",
        "started_at": "2026-08-29T00:00:00+00:00",
        "deadline_at": "2026-08-29T01:00:00+00:00",
    })
    assert inserted["run_id"] == "aiv_live"
    assert conn.execute("SELECT 1 FROM ai_invoke_runs").fetchone() is None
    targets = {row[2] for row in conn.execute(
        "PRAGMA foreign_key_list(ai_invoke_document_review_loops)"
    )}
    assert "ai_invoke_runs" not in targets
    assert {"groups", "documents", "ai_providers"} <= targets
    conn.close()


def test_persisted_review_loop_discovery_is_scoped_to_issuer(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    seen = {}
    class Store:
        def _fetch_all(self, sql, values):
            seen["sql"] = sql
            seen["values"] = values
            return [{"run_id": "aiv_saved", "group_id": "flowgate.default.0417",
                     "project_id": "flowgate", "doc_ref": "standalone", "mode": "single"}]

    monkeypatch.setattr(db_runs, "get_store", Store)
    rows = db_runs.list_review_loops_by_user("review-owner", limit=7)
    assert [row["run_id"] for row in rows] == ["aiv_saved"]
    assert "INNER JOIN ai_invoke_document_review_loops" in seen["sql"]
    assert "r.issued_to = ?" in seen["sql"]
    assert seen["values"] == ["review-owner", 7]


def test_real_worker_standalone_loop_restart_restore_and_never_approves(monkeypatch, tmp_path):
    """Drive real review rejection, rework, worker checkpoint, and restart paths."""
    from contextlib import contextmanager
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import ai_invoke_runs as db_runs
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.workflow.routers import workflow as workflow_router

    conn = sqlite3.connect(tmp_path / "worker-loop.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT UNIQUE NOT NULL,
            project_id TEXT,
            group_id TEXT,
            revision_no INTEGER NOT NULL,
            doc_review_status TEXT NOT NULL,
            meta TEXT,
            rejection_reason TEXT,
            rejection_history TEXT,
            updated_at TEXT
        );
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
        CREATE TABLE document_reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            reviewer_id TEXT NOT NULL,
            verdict TEXT NOT NULL,
            findings TEXT NOT NULL,
            comment TEXT,
            reviewed_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    migrations = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite"
    for name in ("091_ai_invoke_document_review_loops.sql",
                 "092_ai_invoke_document_review_loop_live_run.sql"):
        conn.executescript((migrations / name).read_text(encoding="utf-8"))
    conn.execute("INSERT INTO groups VALUES ('flowgate.default.0417')")
    conn.execute(
        "INSERT INTO documents(doc_id,project_id,group_id,revision_no,doc_review_status,updated_at) "
        "VALUES (?, 'flowgate', 'flowgate.default.0417', 3, 'pending_review', ?)",
        ("standalone", "2026-08-29T00:00:00+00:00"),
    )
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",), ("reworker",)])
    conn.commit()

    class Store:
        def _execute(self, sql, values=()):
            cursor = conn.execute(sql, values)
            conn.commit()
            return cursor
        def _execute_affected(self, sql, values=()):
            cursor = conn.execute(sql, values)
            return cursor.rowcount
        def _fetch_one(self, sql, values=()):
            row = conn.execute(sql, values).fetchone()
            return dict(row) if row else None
        def _fetch_all(self, sql, values=()):
            return [dict(row) for row in conn.execute(sql, values).fetchall()]
        @contextmanager
        def transaction(self):
            try:
                yield self
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    store = Store()
    monkeypatch.setattr(db_loops, "get_store", lambda: store)
    monkeypatch.setattr(service, "get_store", lambda: store)
    monkeypatch.setattr(service.db_docs, "get_store", lambda: store)
    monkeypatch.setattr(db_reviews, "get_store", lambda: store)
    monkeypatch.setattr(db_users, "get_by_id", lambda user_id: {
        "user_id": user_id, "is_admin": 1,
    })
    monkeypatch.setattr(
        workflow_router, "_get_user_permissions",
        lambda actor: {"document.reject", "document.update"},
    )
    monkeypatch.setattr(pipeline_service, "log_state_changed", lambda **kwargs: None)

    # The total-timeout gate compares the stored deadline against the REAL clock, so a
    # hard-coded 2026-08-29T01:00Z deadline turned this into a time bomb: run it after that
    # instant and the first hop stopped with total_timeout instead of driving the loop.
    # Anchor the window on now so the run under test is always inside its budget.
    started = datetime.now(timezone.utc)
    loop = {
        **BASE, "review_baseline_id": 0, "run_id": "aiv_e2e",
        "group_id": "flowgate.default.0417",
        "doc_ref": "standalone", "rework_message": "fix it",
        "started_at": started.isoformat(),
        "deadline_at": (started + timedelta(hours=1)).isoformat(),
        "stop_reason": None, "stop_detail": None,
    }
    persisted = db_loops.insert(loop)
    run = {
        "run_id": "aiv_e2e", "project_id": "flowgate", "issued_to": "review-owner",
        "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1",
        "group_id": "flowgate.default.0417", "doc_ref": "standalone",
        "mode": "single", "document_review_loop": persisted,
        "started_at": loop["started_at"], "docs_target": 0,
        "chain_id": "loop", "chain_docs_target": 0, "chain_docs_reached": 0,
        "attempts_used": 0, "fallback_history": [],
    }
    hops = []
    def execute(_run, _chain, _prompt):
        stage = db_loops.get("aiv_e2e")["current_stage"]
        hops.append(stage)
        stamp = f"2026-08-29T00:00:0{len(hops)}+00:00"
        if stage == "rework":
            rejected = service.db_docs.get_by_id("standalone")
            assert rejected["doc_review_status"] == "rejected"
            assert "Automated review rejection" in rejected["rejection_reason"]
            assert "first finding" in rejected["rejection_reason"]
            pipeline_service.transition_document_review(
                doc_id="standalone", action="mark_revised", actor_user_id="review-owner",
                user_permissions={"document.update"},
            )
            conn.execute("UPDATE documents SET revision_no = 4 WHERE doc_id = 'standalone'")
            pipeline_service.record_rejection_response(
                doc_id="standalone", response_text="Addressed first finding.",
                recorded_by="review-owner", revision_no=4,
            )
        else:
            verdict = "issues" if len(hops) == 1 else "pass"
            conn.execute(
                "INSERT INTO document_reviews(doc_id,revision_no,reviewer_id,verdict,findings,comment,reviewed_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("standalone", 3 if verdict == "issues" else 4, "reviewer", verdict,
                 json.dumps([{"locus": "body", "note": "first finding"}])
                 if verdict == "issues" else "[]", None, stamp, stamp, stamp),
            )
        conn.commit()
        return True

    monkeypatch.setattr(service, "_execute_provider_chain", execute)
    monkeypatch.setattr(service, "_classify_end_reason", lambda item, ok: item.update(outcome="complete"))
    monkeypatch.setattr(service, "_judge_hop", lambda item: None)
    monkeypatch.setattr(service, "_prepare_retry_token", lambda item: {"mention": "token"})
    monkeypatch.setattr(service, "_reset_attempt_state", lambda item: None)
    monkeypatch.setattr(service, "_broadcast", lambda *args: None)
    monkeypatch.setattr(service, "_finalize_run", lambda item: item.update(status="finished"))
    monkeypatch.setattr(service.ai_settings_service, "resolve_effective", lambda project: {
        "providers": [{"id": "reviewer", "name": "Reviewer"},
                      {"id": "reworker", "name": "Reworker"}]
    })

    service._worker(run, [{"id": "reviewer", "name": "Reviewer"}], "review")
    assert hops == ["review", "rework", "review"]
    assert db_loops.get("aiv_e2e")["stop_reason"] == "review_passed"

    # Simulate a fresh server process: no live registry survives. The bootstrap endpoint
    # must discover the durable run itself; a caller does not already know its run_id.
    monkeypatch.setattr(service, "_runs", {})
    stored_run = {
        "run_id": "aiv_e2e", "mode": "single", "group_id": "flowgate.default.0417",
        "project_id": "flowgate", "doc_ref": "standalone", "issued_to": "review-owner",
        "started_at": loop["started_at"], "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(db_runs, "list_review_loops_by_user", lambda user_id: (
        [stored_run] if user_id == "review-owner" else []
    ))
    bootstrap = service.active_all("review-owner")
    assert [item["run_id"] for item in bootstrap["runs"]] == ["aiv_e2e"]
    restored = bootstrap["runs"][0]
    assert restored["persisted"] is True
    assert {key: restored["document_review_loop"][key] for key in
            ("round_no", "current_stage", "stop_reason", "stop_detail")} == {
        "round_no": 2, "current_stage": "stopped",
        "stop_reason": "review_passed", "stop_detail": None,
    }
    # 0417 T0013 items 7-8 / TR0018 rejection: the fresh process observed no transition at
    # all, yet deck screen 6's whole table comes back — the two completed reviews with the
    # server's own finding counts and the rework round in between — because it is rebuilt
    # from document_reviews and the answered rejection_history entry, not from a browser.
    history = restored["document_review_loop"]["history"]
    assert [(row["round_no"], row["stage"], row["result"]) for row in history] == [
        (1, "review", "issues"), (1, "rework", "complete"), (2, "review", "passed"),
    ]
    assert [row.get("finding_count") for row in history] == [1, None, 0]
    assert history[0]["at"] == "2026-08-29T00:00:01+00:00"
    assert history[1]["revision_no"] == 4
    # Rebuilding is stable through the same discovery endpoint, not a direct detail helper.
    second_bootstrap = service.active_all("review-owner")
    assert second_bootstrap["runs"][0]["document_review_loop"]["history"] == history
    doc = conn.execute(
        "SELECT revision_no, doc_review_status, rejection_reason, rejection_history "
        "FROM documents WHERE doc_id = 'standalone'"
    ).fetchone()
    assert doc["revision_no"] == 4
    assert doc["doc_review_status"] == "pending_review"
    assert "first finding" in doc["rejection_reason"]
    history = json.loads(doc["rejection_history"])
    assert len(history) == 1
    assert history[0]["ai_response"] == "Addressed first finding."
    assert history[0]["response_revision_no"] == 4
    conn.close()