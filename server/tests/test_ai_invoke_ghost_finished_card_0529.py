"""flowgate.default.0529 B0001 — "제거 눌러도 안 사라지잖아": the immortal finished card.

The reported card was `aiv_20260830_000075` (group `flowgate.default.0481`, doc
`flowgate.default.0481.0004-NR`, `stop_code=group_lease_denied`), still on screen on
2026-09-06 for a run that ended on 2026-08-31. It was NOT a paused card: at the time of
the report `ai_invoke_paused_chains` held zero rows, so `isNonResumableSystemStop()` and
the whole `DELETE /paused/{group_id}` path could never reach it. It was a FINISHED card,
counted in the header's "1 완료" band, and it came from the one other place `active_all`
builds cards from: `list_review_loops_by_user`, the join over
`ai_invoke_document_review_loops`.

That query had neither of the two bounds a bootstrap restore needs, which is what this
suite pins down:

  * **Removal did not stick.** The browser's [목록에서 제거] on a finished card was a
    local delete; nothing told the server, and the next `/ai-invoke/active-all` rebuilt
    the identical card. `card_dismissed_at` (migration 102) plus
    `dismiss_review_loop_card` are the durable answer, and — FlowGate being a time
    machine — they mark the card, never delete the loop's history.
  * **Nothing aged it out.** The restore answered with every loop row the user ever
    owned, forever. `_review_loop_card_expired` bounds it by the finished-card retention
    that user already chose (0452 L0003 §1-1) — the same number the browser sweeps its
    own copy of this very card with.

No database except where a real SQLite is the point (the migration and the CAS update):
everything else is a dict-backed double, as elsewhere in the ai-invoke suites.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.flow_gate.api.v1 import ai_invoke_routes as routes  # noqa: E402
from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops  # noqa: E402
from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import mutation_policy  # noqa: E402
from modules.flow_gate.services import ui_settings_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import chain  # noqa: E402
from modules.flow_gate.services.ai_invoke import diagnostics  # noqa: E402

MIGRATIONS = _SERVER_DIR / "sql" / "migrations"

# The reported card, field for field (see the module docstring).
GHOST_RUN = "aiv_20260830_000075"
GHOST_GROUP = "flowgate.default.0481"
GHOST_DOC = "flowgate.default.0481.0004-NR"
OWNER = "4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39"
OTHER = "usr_other"
ADMIN = "usr_admin"

LOOP_ROW = {
    "run_id": GHOST_RUN,
    "group_id": GHOST_GROUP,
    "doc_ref": GHOST_DOC,
    "review_count": 1,
    "reviewer_provider_id": "reviewer",
    "review_criteria": "document_type_default",
    "rework_provider_id": "reworker",
    "rework_timeout_sec": 3600,
    "failure_restart_max_attempts": 1,
    "total_timeout_sec": 7200,
    "review_baseline_id": 425,
    "baseline_revision_no": 1,
    "starts_with_rework": True,
    "started_at": "2026-08-31T07:57:01+09:00",
    "deadline_at": "2026-08-31T09:57:01+09:00",
    "current_stage": "review",
}


def _iso_ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


# ══════════════════════════════════════════════════════════════════════════════════════
# Migration 102 — the column that makes a removal durable
# ══════════════════════════════════════════════════════════════════════════════════════

class TestMigration102:
    @pytest.mark.parametrize("dialect", ["sqlite", "postgres", "mysql"])
    def test_every_dialect_adds_the_column_additively(self, dialect):
        text = (MIGRATIONS / dialect
                / "102_ai_invoke_review_loop_card_dismissed.sql").read_text(encoding="utf-8")
        statement = [line for line in text.splitlines()
                     if line.strip().upper().startswith("ALTER TABLE")][0]
        assert statement.startswith("ALTER TABLE ai_invoke_document_review_loops ADD COLUMN")
        assert "card_dismissed_at" in statement
        # Nullable with no default: a card that predates the migration must keep showing
        # until somebody actually removes it.
        assert "NOT NULL" not in text
        assert "DEFAULT" not in text
        # And never a drop -- the loop's history is not what the user asked to get rid of.
        assert "DROP" not in text.upper()

    def test_mysql_does_not_use_if_not_exists_on_add_column(self):
        text = (MIGRATIONS / "mysql"
                / "102_ai_invoke_review_loop_card_dismissed.sql").read_text(encoding="utf-8")
        statement = [line for line in text.splitlines()
                     if line.strip().upper().startswith("ALTER TABLE")][0]
        assert "IF NOT EXISTS" not in statement.upper()


# ══════════════════════════════════════════════════════════════════════════════════════
# DB layer, against a real SQLite built from the real migrations
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def loop_db(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "loops.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE ai_invoke_runs(
            run_id TEXT PRIMARY KEY,
            issued_to TEXT,
            group_id TEXT,
            started_at TEXT,
            finished_at TEXT
        );
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(doc_id TEXT PRIMARY KEY);
        CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY);
    """)
    for name in ("091_ai_invoke_document_review_loops.sql",
                 "092_ai_invoke_document_review_loop_live_run.sql",
                 "102_ai_invoke_review_loop_card_dismissed.sql"):
        conn.executescript((MIGRATIONS / "sqlite" / name).read_text(encoding="utf-8"))
    conn.execute("INSERT INTO groups VALUES (?)", [GHOST_GROUP])
    conn.execute("INSERT INTO documents VALUES (?)", [GHOST_DOC])
    conn.executemany("INSERT INTO ai_providers VALUES (?)", [("reviewer",), ("reworker",)])
    conn.execute(
        "INSERT INTO ai_invoke_runs(run_id, issued_to, group_id, started_at, finished_at) "
        "VALUES (?,?,?,?,?)",
        [GHOST_RUN, OWNER, GHOST_GROUP, "2026-08-31T07:57:01+09:00",
         "2026-08-31T08:14:33+09:00"],
    )
    conn.commit()

    class Store:
        def _execute(self, sql, values=()):
            conn.execute(sql, values)
            conn.commit()

        def _execute_affected(self, sql, values=()):
            cursor = conn.execute(sql, values)
            conn.commit()
            return cursor.rowcount

        def _fetch_one(self, sql, values=()):
            row = conn.execute(sql, values).fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, values=()):
            return [dict(row) for row in conn.execute(sql, values).fetchall()]

    monkeypatch.setattr(db_loops, "get_store", Store)
    monkeypatch.setattr(db_runs, "get_store", Store)
    monkeypatch.setattr(db_runs, "_row_to_payload", lambda row: dict(row))
    db_loops.insert(dict(LOOP_ROW))
    # The reported loop was `stopped/retry_exhausted`. `insert` never writes those two
    # columns (091 CHECKs them against current_stage), so the row reaches that state the
    # way production does -- afterwards.
    conn.execute(
        "UPDATE ai_invoke_document_review_loops "
        "SET current_stage='stopped', stop_reason='retry_exhausted', stop_detail=? "
        "WHERE run_id=?",
        ["the rework hop failed twice", GHOST_RUN],
    )
    conn.commit()
    yield conn
    conn.close()


class TestDismissCardIsDurableAndNonDestructive:
    def test_the_bootstrap_listing_returns_the_card_until_it_is_dismissed(self, loop_db):
        assert [row["run_id"] for row in db_runs.list_review_loops_by_user(OWNER)] == [GHOST_RUN]

        assert db_loops.dismiss_card(GHOST_RUN) is True

        # The exact symptom this bug is about: the next bootstrap must not rebuild it.
        assert db_runs.list_review_loops_by_user(OWNER) == []

    def test_dismissing_marks_the_row_and_never_deletes_the_history(self, loop_db):
        db_loops.dismiss_card(GHOST_RUN)

        row = db_loops.get(GHOST_RUN)
        assert row is not None, "FlowGate is a time machine — the loop row must survive"
        assert row["card_dismissed_at"]
        assert row["stop_reason"] == "retry_exhausted"
        assert row["stop_detail"] == "the rework hop failed twice"
        assert loop_db.execute(
            "SELECT COUNT(*) FROM ai_invoke_document_review_loops").fetchone()[0] == 1

    def test_a_replay_is_reported_as_a_replay_not_as_a_second_removal(self, loop_db):
        assert db_loops.dismiss_card(GHOST_RUN) is True
        # Compare-and-swap on `card_dismissed_at IS NULL`: the second click must not be
        # able to claim it removed the card, or the surface would say "done" twice and
        # overwrite the moment the card actually went.
        first_stamp = db_loops.get(GHOST_RUN)["card_dismissed_at"]
        assert db_loops.dismiss_card(GHOST_RUN, at="2099-01-01T00:00:00+00:00") is False
        assert db_loops.get(GHOST_RUN)["card_dismissed_at"] == first_stamp

    def test_an_unknown_run_reports_no_removal(self, loop_db):
        assert db_loops.dismiss_card("aiv_does_not_exist") is False

    def test_another_users_card_is_untouched_by_the_listing_filter(self, loop_db):
        assert db_runs.list_review_loops_by_user(OTHER) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# Service layer — dismiss_review_loop_card
# ══════════════════════════════════════════════════════════════════════════════════════

class _Loops:
    """Dict-backed stand-in for ai_invoke_document_review_loops."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.dismissed = []

    def get(self, run_id):
        return self.rows.get(run_id)

    def dismiss_card(self, run_id, *, at=None):
        row = self.rows.get(run_id)
        if row is None or row.get("card_dismissed_at"):
            return False
        row["card_dismissed_at"] = at or "2026-09-06T11:00:00+09:00"
        self.dismissed.append(run_id)
        return True


@pytest.fixture
def service_doubles(monkeypatch):
    loops = _Loops({GHOST_RUN: dict(LOOP_ROW)})
    runs = {GHOST_RUN: {"run_id": GHOST_RUN, "group_id": GHOST_GROUP,
                        "issued_to": OWNER, "status": "finished",
                        "finished_at": "2026-08-31T08:14:33+09:00"}}
    monkeypatch.setattr(db_loops, "get", loops.get)
    monkeypatch.setattr(db_loops, "dismiss_card", loops.dismiss_card)
    monkeypatch.setattr(db_runs, "get", lambda run_id: runs.get(run_id))
    monkeypatch.setattr(svc, "get_run_record", lambda run_id: None)
    return loops, runs


class TestDismissReviewLoopCardService:
    def test_the_owner_removes_the_card_and_the_answer_says_so(self, service_doubles):
        loops, _ = service_doubles

        result = svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=OWNER)

        assert result == {"ok": True, "run_id": GHOST_RUN, "group_id": GHOST_GROUP,
                          "dismissed": True, "already_dismissed": False}
        assert loops.dismissed == [GHOST_RUN]

    def test_a_replay_is_idempotent_200_never_404(self, service_doubles):
        svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=OWNER)

        result = svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=OWNER)

        assert result["dismissed"] is False
        assert result["already_dismissed"] is True

    def test_a_run_with_no_loop_row_is_also_idempotent_200(self, service_doubles):
        loops, _ = service_doubles
        loops.rows.clear()

        result = svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=OWNER)

        assert result["dismissed"] is False
        assert result["already_dismissed"] is True

    def test_a_third_party_is_refused_403_and_the_card_stays(self, service_doubles):
        loops, _ = service_doubles

        with pytest.raises(Exception) as exc:
            svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=OTHER)

        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "run_card_forbidden"
        assert loops.dismissed == []
        assert loops.rows[GHOST_RUN].get("card_dismissed_at") is None

    def test_an_admin_may_remove_someone_elses_card(self, service_doubles):
        loops, _ = service_doubles

        result = svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=ADMIN, is_admin=True)

        assert result["dismissed"] is True
        assert loops.dismissed == [GHOST_RUN]

    def test_an_unknown_run_is_404(self, service_doubles):
        with pytest.raises(Exception) as exc:
            svc.dismiss_review_loop_card(run_id="aiv_nope", user_id=OWNER)
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "run_not_found"

    def test_a_live_run_is_refused_409_and_the_card_stays(self, service_doubles, monkeypatch):
        loops, _ = service_doubles
        monkeypatch.setattr(svc, "get_run_record",
                            lambda run_id: {"run_id": run_id, "status": "running"})

        with pytest.raises(Exception) as exc:
            svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=OWNER)

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "run_still_active"
        assert loops.dismissed == []

    def test_a_run_that_finished_in_this_process_may_still_be_removed(
            self, service_doubles, monkeypatch):
        loops, _ = service_doubles
        monkeypatch.setattr(svc, "get_run_record",
                            lambda run_id: {"run_id": run_id, "status": "finished"})

        assert svc.dismiss_review_loop_card(run_id=GHOST_RUN, user_id=OWNER)["dismissed"] is True


# ══════════════════════════════════════════════════════════════════════════════════════
# Service layer — the retention bound on the bootstrap restore
# ══════════════════════════════════════════════════════════════════════════════════════

class TestReviewLoopCardExpiry:
    def test_never_expires_keeps_the_card_at_any_age(self):
        row = {"finished_at": "2020-01-01T00:00:00+09:00"}
        assert chain._review_loop_card_expired(row, ui_settings_service.RETENTION_NEVER) is False

    def test_disappears_immediately_restores_no_finished_card_at_all(self):
        row = {"finished_at": _iso_ago(seconds=1)}
        assert chain._review_loop_card_expired(
            row, ui_settings_service.RETENTION_IMMEDIATE) is True

    def test_a_card_younger_than_the_retention_survives(self):
        assert chain._review_loop_card_expired({"finished_at": _iso_ago(minutes=5)}, 30) is False

    def test_a_card_older_than_the_retention_is_dropped(self):
        # The reported card: six days old against a 30-minute retention.
        assert chain._review_loop_card_expired({"finished_at": _iso_ago(days=6)}, 30) is True

    @pytest.mark.parametrize("stamp", [None, "", "not-a-timestamp"])
    def test_an_unreadable_finish_time_is_never_treated_as_old(self, stamp):
        # Guessing "old" here would silently delete a card nobody asked to lose.
        assert chain._review_loop_card_expired({"finished_at": stamp}, 30) is False

    def test_a_naive_stamp_is_read_in_local_time_not_as_utc(self):
        naive = (datetime.now() - timedelta(minutes=5)).isoformat()
        assert chain._review_loop_card_expired({"finished_at": naive}, 30) is False

    def test_the_retention_comes_from_the_users_own_setting(self, monkeypatch):
        seen = {}

        def _resolve(user_id):
            seen["user_id"] = user_id
            return {ui_settings_service.RETENTION_FIELD: 120}, False
        monkeypatch.setattr(ui_settings_service, "resolve_ui_settings", _resolve)

        assert chain._finished_card_retention_minutes(OWNER) == 120
        assert seen["user_id"] == OWNER

    def test_a_failing_settings_read_can_never_blank_a_card(self, monkeypatch):
        def _boom(_user_id):
            raise RuntimeError("settings store down")
        monkeypatch.setattr(ui_settings_service, "resolve_ui_settings", _boom)

        assert chain._finished_card_retention_minutes(OWNER) == ui_settings_service.RETENTION_NEVER


# ══════════════════════════════════════════════════════════════════════════════════════
# active_all — the bootstrap that used to hand the ghost back
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def bootstrap_doubles(monkeypatch):
    state = {"loop_rows": [], "retention": 30}

    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(db_paused, "list_by_user", lambda user_id: [])
    monkeypatch.setattr(db_runs, "list_review_loops_by_user",
                        lambda user_id, *a, **kw: list(state["loop_rows"]))
    monkeypatch.setattr(
        ui_settings_service, "resolve_ui_settings",
        lambda user_id: ({ui_settings_service.RETENTION_FIELD: state["retention"]}, False),
    )
    monkeypatch.setattr(diagnostics, "_run_detail_from_row",
                        lambda row: {"run_id": row["run_id"], "status": "finished",
                                     "group_id": row["group_id"]})
    return state


class TestActiveAllStopsResurrectingTheGhost:
    def test_a_fresh_review_loop_card_is_still_restored_after_a_restart(self, bootstrap_doubles):
        # The restore exists for exactly this: `_runs` is empty because the process
        # restarted, and the card has to come back.
        bootstrap_doubles["loop_rows"] = [
            {"run_id": GHOST_RUN, "group_id": GHOST_GROUP, "finished_at": _iso_ago(minutes=2)},
        ]

        payload = svc.active_all(OWNER)

        assert [run["run_id"] for run in payload["runs"]] == [GHOST_RUN]
        assert payload["runs"][0]["persisted"] is True

    def test_the_six_day_old_card_is_not_restored_at_the_default_retention(
            self, bootstrap_doubles):
        bootstrap_doubles["loop_rows"] = [
            {"run_id": GHOST_RUN, "group_id": GHOST_GROUP,
             "finished_at": "2026-08-31T08:14:33+09:00"},
        ]

        assert svc.active_all(OWNER)["runs"] == []

    def test_never_expires_keeps_restoring_it_until_it_is_removed(self, bootstrap_doubles):
        bootstrap_doubles["retention"] = ui_settings_service.RETENTION_NEVER
        bootstrap_doubles["loop_rows"] = [
            {"run_id": GHOST_RUN, "group_id": GHOST_GROUP, "finished_at": _iso_ago(days=6)},
        ]

        assert [run["run_id"] for run in svc.active_all(OWNER)["runs"]] == [GHOST_RUN]

        # ...and "until it is removed" is the listing filter, which the query owns.
        bootstrap_doubles["loop_rows"] = []
        assert svc.active_all(OWNER)["runs"] == []

    def test_disappears_immediately_restores_nothing(self, bootstrap_doubles):
        bootstrap_doubles["retention"] = ui_settings_service.RETENTION_IMMEDIATE
        bootstrap_doubles["loop_rows"] = [
            {"run_id": GHOST_RUN, "group_id": GHOST_GROUP, "finished_at": _iso_ago(seconds=1)},
        ]

        assert svc.active_all(OWNER)["runs"] == []


# ══════════════════════════════════════════════════════════════════════════════════════
# Route layer: DELETE /api/v1/ai-invoke/runs/{run_id}/card
# ══════════════════════════════════════════════════════════════════════════════════════

class TestDismissCardRoute:
    @pytest.fixture
    def app_client(self, monkeypatch):
        app = FastAPI()
        app.include_router(routes.router)
        monkeypatch.setattr(
            routes, "verify_bearer",
            lambda request: {"_is_user_jwt": True, "issued_to": OWNER, "is_admin": False},
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_the_route_calls_the_service_and_returns_200(self, app_client, monkeypatch):
        captured = {}

        def _fake(**kw):
            captured.update(kw)
            return {"ok": True, "run_id": GHOST_RUN, "group_id": GHOST_GROUP,
                    "dismissed": True, "already_dismissed": False}
        monkeypatch.setattr(svc, "dismiss_review_loop_card", _fake)

        resp = app_client.delete(f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card",
                                 headers={"Authorization": "Bearer tok"})

        assert resp.status_code == 200
        assert resp.json()["dismissed"] is True
        assert captured == {"run_id": GHOST_RUN, "user_id": OWNER, "is_admin": False}

    def test_the_service_error_envelope_passes_through_verbatim(self, app_client, monkeypatch):
        def _forbid(**kw):
            raise svc._http_error(403, "run_card_forbidden", "not yours", run_id=GHOST_RUN)
        monkeypatch.setattr(svc, "dismiss_review_loop_card", _forbid)

        resp = app_client.delete(f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card",
                                 headers={"Authorization": "Bearer tok"})

        assert resp.status_code == 403
        assert resp.json()["code"] == "run_card_forbidden"
        assert resp.json()["run_id"] == GHOST_RUN

    def test_no_user_session_403(self, app_client, monkeypatch):
        monkeypatch.setattr(routes, "verify_bearer", lambda request: {"_is_user_jwt": False})
        resp = app_client.delete(f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card",
                                 headers={"Authorization": "Bearer tok"})
        assert resp.status_code == 403
        assert resp.json()["code"] == "user_session_required"

    def test_runs_is_never_read_as_a_run_id(self, app_client, monkeypatch):
        """The declaration order guard GET /runs and GET /leases already rely on."""
        called = {}
        monkeypatch.setattr(svc, "dismiss_review_loop_card",
                            lambda **kw: called.setdefault("run_id", kw["run_id"]) or
                            {"ok": True, "run_id": kw["run_id"], "group_id": None,
                             "dismissed": False, "already_dismissed": True})

        app_client.delete("/api/v1/ai-invoke/runs/aiv_x/card",
                          headers={"Authorization": "Bearer tok"})

        assert called["run_id"] == "aiv_x"

    def test_the_path_is_exempt_from_the_mutation_policy(self):
        # A group under a mutation lease must still be able to clear its own ghost card.
        assert mutation_policy.is_policy_control_path(
            f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card") is True


# ══════════════════════════════════════════════════════════════════════════════════════
# The round trip, over the real routes and a real SQLite carrying EVERY migration
# ══════════════════════════════════════════════════════════════════════════════════════
#
# Everything above judges one link at a time. This is the report itself: the card is
# there, the user removes it, and the next bootstrap does not bring it back. Nothing is
# stubbed except the bearer-token transport -- the route, the service, the SQL and the
# schema (all 100-odd migrations, applied in name order by the shared builder) are the
# production ones.

class _SqliteStore:
    """The store surface the db modules use, over one sqlite3 connection."""

    def __init__(self, conn):
        self._conn = conn

    def _execute(self, sql, values=()):
        self._conn.execute(sql, values)
        self._conn.commit()

    def _execute_affected(self, sql, values=()):
        cursor = self._conn.execute(sql, values)
        self._conn.commit()
        return cursor.rowcount

    def _fetch_one(self, sql, values=()):
        row = self._conn.execute(sql, values).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, values=()):
        return [dict(row) for row in self._conn.execute(sql, values).fetchall()]


class TestGhostCardRoundTripOverRealRoutes:
    @pytest.fixture
    def client(self, migrated_sqlite_db, monkeypatch):
        import importlib
        import pkgutil

        from modules.flow_gate import db as db_pkg

        # TestClient runs the route on its own thread, so the connection must not be
        # pinned to the one that seeded it.
        conn = sqlite3.connect(migrated_sqlite_db("ghost-card-0529.db"),
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        store = _SqliteStore(conn)

        # Every db module holds its own `get_store` reference (a from-import), so the
        # redirect has to be applied per module rather than once on `connection`.
        for info in pkgutil.iter_modules(db_pkg.__path__):
            module = importlib.import_module(f"modules.flow_gate.db.{info.name}")
            if hasattr(module, "get_store"):
                monkeypatch.setattr(module, "get_store", lambda _s=store: _s)

        conn.execute("INSERT INTO users(user_id, username, email, password, created_at, "
                     "updated_at) VALUES (?,?,?,?,?,?)",
                     [OWNER, "owner", "owner@example.test", "x",
                      "2026-08-01", "2026-08-01"])
        conn.execute("INSERT INTO projects(project_id, project_name, created_at, "
                     "updated_at) VALUES ('flowgate','FlowGate','2026-08-01','2026-08-01')")
        conn.execute("INSERT INTO groups(group_id, project_id, module, title, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     [GHOST_GROUP, "flowgate", "default", "0481 ghost card",
                      "2026-08-01", "2026-08-01"])
        conn.execute("INSERT INTO documents(doc_id, project_id, group_id, type_code, "
                     "seq, title, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     [GHOST_DOC, "flowgate", GHOST_GROUP, "NR", 4,
                      "깃 충돌 해결 승인 관문(R0001) 현황 조사 및 Before/After 시안 등록",
                      "2026-08-01", "2026-08-01"])
        conn.executemany(
            "INSERT INTO ai_providers(provider_id, name, exec_type, kind, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?)",
            [("reviewer", "Reviewer", "cli", "cli", "2026-08-01", "2026-08-01"),
             ("reworker", "Reworker", "cli", "cli", "2026-08-01", "2026-08-01")],
        )
        conn.execute(
            "INSERT INTO ai_invoke_runs(run_id, group_id, project_id, doc_ref, mode, "
            "status, outcome, end_reason, stop_code, resumable, issued_to, started_at, "
            "finished_at, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [GHOST_RUN, GHOST_GROUP, "flowgate", GHOST_DOC, "single", "finished",
             "none", "exited", "group_lease_denied", 0, OWNER,
             "2026-08-31T07:57:01+09:00", _iso_ago(minutes=3),
             "2026-08-31T08:14:33+09:00", "2026-08-31T08:14:33+09:00"],
        )
        conn.commit()
        db_loops.insert(dict(LOOP_ROW))

        monkeypatch.setattr(svc, "_runs", {})
        app = FastAPI()
        app.include_router(routes.router)
        monkeypatch.setattr(
            routes, "verify_bearer",
            lambda request: {"_is_user_jwt": True, "issued_to": OWNER, "is_admin": False},
        )
        yield TestClient(app, raise_server_exceptions=False), conn
        conn.close()

    def test_the_card_appears_is_removed_and_does_not_come_back(self, client):
        http, conn = client
        headers = {"Authorization": "Bearer tok"}

        first = http.get("/api/v1/ai-invoke/active-all", headers=headers)
        assert first.status_code == 200
        assert [run["run_id"] for run in first.json()["runs"]] == [GHOST_RUN], \
            "the restore itself must keep working -- this is what survives a restart"
        assert first.json()["runs"][0]["persisted"] is True

        removed = http.delete(f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card", headers=headers)
        assert removed.status_code == 200
        assert removed.json()["dismissed"] is True

        # 언제까지 나오게 할건데 -- this is the answer: it does not come back.
        again = http.get("/api/v1/ai-invoke/active-all", headers=headers)
        assert again.json()["runs"] == []

        # ...and it comes back on no later bootstrap either, because the durable row
        # says so rather than any per-session memory.
        assert http.get("/api/v1/ai-invoke/active-all", headers=headers).json()["runs"] == []

        # Nothing was destroyed to achieve that.
        row = conn.execute(
            "SELECT stop_reason, card_dismissed_at FROM ai_invoke_document_review_loops "
            "WHERE run_id = ?", [GHOST_RUN]).fetchone()
        assert row["card_dismissed_at"]
        assert conn.execute("SELECT COUNT(*) c FROM ai_invoke_runs "
                            "WHERE run_id = ?", [GHOST_RUN]).fetchone()["c"] == 1

    def test_a_second_removal_is_an_idempotent_success(self, client):
        http, _ = client
        headers = {"Authorization": "Bearer tok"}

        http.delete(f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card", headers=headers)
        replay = http.delete(f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card", headers=headers)

        assert replay.status_code == 200
        assert replay.json()["dismissed"] is False
        assert replay.json()["already_dismissed"] is True

    def test_a_third_party_cannot_remove_it_and_it_stays_on_the_owners_bootstrap(
            self, client, monkeypatch):
        http, _ = client
        monkeypatch.setattr(
            routes, "verify_bearer",
            lambda request: {"_is_user_jwt": True, "issued_to": OTHER, "is_admin": False},
        )

        refused = http.delete(f"/api/v1/ai-invoke/runs/{GHOST_RUN}/card",
                              headers={"Authorization": "Bearer tok"})

        assert refused.status_code == 403
        assert refused.json()["code"] == "run_card_forbidden"
        monkeypatch.setattr(
            routes, "verify_bearer",
            lambda request: {"_is_user_jwt": True, "issued_to": OWNER, "is_admin": False},
        )
        still = http.get("/api/v1/ai-invoke/active-all",
                         headers={"Authorization": "Bearer tok"})
        assert [run["run_id"] for run in still.json()["runs"]] == [GHOST_RUN]
