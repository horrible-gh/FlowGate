"""Group 0452 T0005 — the per-user finished-card retention setting.

Three things are pinned here, in the order the work was specified:

1. reading and saving the value, in both directions (L0003 §2-1/§2-8),
2. the endpoint that carries it, including the shape of a refusal,
3. the table it lives in, including the constraints DB0004 deliberately did NOT put in
   the schema.

The recurring theme is the asymmetry L0003 §2-8 asked for: a value *arriving* in a save
is refused, while a value already *sitting* in storage is repaired on the way out and the
call proceeds — a setting must never be able to stop finished cards from working.

The trap the parametrized cases exist for: ``-1`` is a member of the domain, not a lower
bound. A range clamp passes every other case in this file and silently turns "never
expires" into 30 minutes.
"""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.api.v1 import ui_settings_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import user_ui_settings as store
from modules.flow_gate.services import ui_settings_service as uss

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "sql" / "migrations"
MIGRATION_NAME = "089_user_ui_settings.sql"
ORDINAL = MIGRATION_NAME.split("_", 1)[0]
DIALECTS = ("sqlite", "mysql", "postgres")
FIELD = uss.RETENTION_FIELD


# ── fakes ─────────────────────────────────────────────────────────────────────

class _FakeStore:
    """Stands in for FlowGateStore so the SQL can be read without a database."""

    def __init__(self, row=None):
        self.row = row
        self.fetched: list[tuple] = []
        self.executed: list[tuple] = []

    def _fetch_one(self, sql, params=None):
        self.fetched.append((sql, params))
        return self.row

    def _execute(self, sql, params=None):
        self.executed.append((sql, params))


@pytest.fixture
def fake_store(monkeypatch):
    fake = _FakeStore()
    monkeypatch.setattr(store, "get_store", lambda: fake)
    return fake


class _MemoryStore:
    """A one-row settings store with the same surface the service calls."""

    def __init__(self, row=None):
        self.row = row
        self.writes: list[dict] = []

    def get(self, user_id):
        return self.row

    def upsert(self, user_id, *, columns, unset_columns, updated_at):
        self.writes.append(dict(columns))
        base = self.row or {"user_id": user_id, **unset_columns, "created_at": updated_at}
        base = {k: v for k, v in base.items() if k != "updated_at"}
        base.update(columns)
        base["updated_at"] = updated_at
        self.row = base


@pytest.fixture
def memory_store(monkeypatch):
    mem = _MemoryStore()
    monkeypatch.setattr(uss, "ui_settings_store", mem)
    return mem


# ── 1. reading the setting ────────────────────────────────────────────────────

class TestResolveSettings:
    def test_never_saved_is_thirty_minutes_and_says_so(self, memory_store):
        settings, is_default = uss.resolve_ui_settings("u1")
        assert is_default is True
        assert settings == {FIELD: 30, "updated_at": None}

    def test_unknown_user_is_treated_like_someone_who_never_saved(self, memory_store):
        assert uss.resolve_ui_settings(None) == (uss.defaults(), True)

    def test_a_saved_row_comes_back_as_saved(self, memory_store):
        memory_store.row = {
            "user_id": "u1", FIELD: 1440,
            "created_at": "x", "updated_at": "2026-08-23T18:22:41+09:00",
        }
        settings, is_default = uss.resolve_ui_settings("u1")
        assert is_default is False
        assert settings[FIELD] == 1440
        assert settings["updated_at"] == "2026-08-23T18:22:41+09:00"

    @pytest.mark.parametrize("stored", list(uss.RETENTION_DOMAIN_MINUTES))
    def test_every_domain_member_survives_a_round_trip(self, memory_store, stored):
        # -1 among them: the membership rule has to keep it, and a range check would not.
        memory_store.row = {"user_id": "u1", FIELD: stored, "created_at": "x", "updated_at": "t"}
        settings, _ = uss.resolve_ui_settings("u1")
        assert settings[FIELD] == stored

    @pytest.mark.parametrize(
        "stored",
        [None, True, False, "30", "abc", 45, -2, -60, 0.5, 100_000],
    )
    def test_values_outside_the_domain_read_back_as_the_default(self, memory_store, stored):
        memory_store.row = {"user_id": "u1", FIELD: stored, "created_at": "x", "updated_at": "t"}
        settings, is_default = uss.resolve_ui_settings("u1")
        assert settings[FIELD] == 30
        # Still "saved": the row exists, it just holds something unusable.
        assert is_default is False

    def test_reading_never_writes(self, memory_store):
        memory_store.row = {"user_id": "u1", FIELD: -2, "created_at": "x", "updated_at": "t"}
        uss.resolve_ui_settings("u1")
        # A repair written back would erase the value that explains the problem.
        assert memory_store.writes == []
        assert memory_store.row[FIELD] == -2

    def test_response_carries_the_domain_so_no_screen_holds_its_own_copy(self, memory_store):
        body = uss.settings_response("u1")
        assert body["domain"] == {FIELD: [-1, 0, 30, 60, 120, 180, 360, 720, 1440]}
        assert body["defaults"] == {FIELD: 30}
        assert "updated_at" not in body["defaults"]
        assert body["settings"] == {FIELD: 30, "updated_at": None}
        assert body["is_default"] is True

    def test_an_unreachable_store_answers_defaults_instead_of_failing(self, monkeypatch):
        def _boom(_user_id):
            raise RuntimeError("ui settings store is down")

        monkeypatch.setattr(
            uss, "ui_settings_store", type("X", (), {"get": staticmethod(_boom)})
        )
        assert uss.resolve_ui_settings_safe("u1") == uss.defaults()
        body = uss.settings_response_safe("u1")
        assert body["ok"] is True
        assert body["settings"][FIELD] == 30
        assert body["domain"][FIELD] == list(uss.RETENTION_DOMAIN_MINUTES)


# ── 2. saving the setting ─────────────────────────────────────────────────────

class TestSaveSettings:
    def test_saving_writes_the_field_and_answers_with_a_fresh_read(self, memory_store):
        body = uss.save_ui_settings("u1", {FIELD: 720})
        assert memory_store.writes == [{FIELD: 720}]
        assert body["is_default"] is False
        assert body["settings"][FIELD] == 720
        assert body["settings"]["updated_at"]

    def test_never_expires_is_saved_not_clamped(self, memory_store):
        body = uss.save_ui_settings("u1", {FIELD: -1})
        assert memory_store.writes == [{FIELD: -1}]
        assert body["settings"][FIELD] == -1

    def test_immediately_is_saved_not_treated_as_missing(self, memory_store):
        body = uss.save_ui_settings("u1", {FIELD: 0})
        assert memory_store.writes == [{FIELD: 0}]
        assert body["settings"][FIELD] == 0

    def test_an_empty_patch_creates_no_row(self, memory_store):
        body = uss.save_ui_settings("u1", {})
        # A defaults row here would flip "has this person ever saved" to false for
        # somebody who chose nothing (DB0004 §2-6, §3-4).
        assert memory_store.writes == []
        assert memory_store.row is None
        assert body["is_default"] is True

    def test_a_repaired_value_shows_up_immediately(self, memory_store):
        # The response is re-read, not echoed: whatever the table now holds is what the
        # screen sees, even when the server had to substitute the default.
        memory_store.row = {"user_id": "u1", FIELD: 45, "created_at": "x", "updated_at": "t"}
        body = uss.settings_response("u1")
        assert body["settings"][FIELD] == 30

    @pytest.mark.parametrize(
        "patch,field,message",
        [
            ({FIELD: None}, FIELD, f"{FIELD} must not be null."),
            ({FIELD: True}, FIELD, f"{FIELD} must be an integer."),
            ({FIELD: "30"}, FIELD, f"{FIELD} must be an integer."),
            ({FIELD: 30.0}, FIELD, f"{FIELD} must be an integer."),
            ({FIELD: 45}, FIELD,
             f"{FIELD} must be one of -1, 0, 30, 60, 120, 180, 360, 720, 1440."),
            ({FIELD: -2}, FIELD,
             f"{FIELD} must be one of -1, 0, 30, 60, 120, 180, 360, 720, 1440."),
            ({"retention_minutes": 30}, "retention_minutes",
             "unknown field: retention_minutes."),
        ],
    )
    def test_rejections(self, memory_store, patch, field, message):
        with pytest.raises(uss.UiSettingsError) as exc:
            uss.save_ui_settings("u1", patch)
        assert exc.value.field == field
        assert exc.value.message == message
        assert memory_store.writes == []

    def test_the_message_quotes_the_live_domain(self, memory_store):
        with pytest.raises(uss.UiSettingsError) as exc:
            uss.validate_patch({FIELD: 45})
        for value in uss.RETENTION_DOMAIN_MINUTES:
            assert str(value) in exc.value.message

    def test_an_unknown_key_is_reported_before_a_bad_known_one(self, memory_store):
        with pytest.raises(uss.UiSettingsError) as exc:
            uss.validate_patch({"nope": 1, FIELD: 45})
        assert exc.value.field == "nope"


# ── 3. the endpoint ───────────────────────────────────────────────────────────

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ui_settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "username": "U"}
    return TestClient(app)


class TestUiSettingsEndpoint:
    def test_handlers_stay_synchronous(self):
        # The store below is synchronous; an async handler would block the loop for
        # every other request (tests/test_event_loop_blocking_0279.py).
        assert not inspect.iscoroutinefunction(ui_settings_routes.get_my_ui_settings)
        assert not inspect.iscoroutinefunction(ui_settings_routes.patch_my_ui_settings)

    def test_the_address_names_no_user(self):
        paths = {route.path for route in ui_settings_routes.router.routes}
        assert paths == {"/me/ui-settings"}

    def test_get_answers_defaults_for_a_new_user_without_writing(self, memory_store):
        body = _client().get("/me/ui-settings").json()
        assert body["ok"] is True
        assert body["is_default"] is True
        assert body["settings"][FIELD] == 30
        assert body["domain"][FIELD] == [-1, 0, 30, 60, 120, 180, 360, 720, 1440]
        assert memory_store.writes == []
        assert memory_store.row is None

    def test_patch_saves_and_returns_the_stored_settings(self, memory_store):
        response = _client().patch("/me/ui-settings", json={FIELD: 120})
        assert response.status_code == 200
        assert response.json()["settings"][FIELD] == 120
        assert memory_store.writes == [{FIELD: 120}]

    def test_an_empty_patch_is_a_success_that_writes_nothing(self, memory_store):
        response = _client().patch("/me/ui-settings", json={})
        assert response.status_code == 200
        assert response.json()["is_default"] is True
        assert memory_store.writes == []

    def test_patch_out_of_domain_is_a_422_naming_the_field(self, memory_store):
        response = _client().patch("/me/ui-settings", json={FIELD: 45})
        assert response.status_code == 422
        assert response.json() == {
            "ok": False,
            "error": {
                "code": "invalid_request",
                "field": FIELD,
                "message": (
                    f"{FIELD} must be one of -1, 0, 30, 60, 120, 180, 360, 720, 1440."
                ),
            },
        }
        assert memory_store.writes == []

    def test_a_string_number_is_not_quietly_coerced(self, memory_store):
        response = _client().patch("/me/ui-settings", json={FIELD: "30"})
        assert response.status_code == 422
        assert response.json()["error"]["message"] == f"{FIELD} must be an integer."

    def test_a_boolean_is_not_an_integer_here(self, memory_store):
        response = _client().patch("/me/ui-settings", json={FIELD: True})
        assert response.status_code == 422
        assert response.json()["error"]["message"] == f"{FIELD} must be an integer."

    def test_null_is_refused(self, memory_store):
        response = _client().patch("/me/ui-settings", json={FIELD: None})
        assert response.status_code == 422
        assert response.json()["error"]["message"] == f"{FIELD} must not be null."

    def test_an_unknown_key_is_refused_rather_than_ignored(self, memory_store):
        response = _client().patch("/me/ui-settings", json={"retention": 30})
        assert response.status_code == 422
        assert response.json()["error"]["field"] == "retention"

    def test_never_expires_survives_the_endpoint(self, memory_store):
        response = _client().patch("/me/ui-settings", json={FIELD: -1})
        assert response.status_code == 200
        assert response.json()["settings"][FIELD] == -1

    def test_a_broken_store_does_not_take_the_get_down(self, monkeypatch):
        def _boom(_user_id):
            raise RuntimeError("ui settings store is down")

        monkeypatch.setattr(
            uss, "ui_settings_store", type("X", (), {"get": staticmethod(_boom)})
        )
        response = _client().get("/me/ui-settings")
        assert response.status_code == 200
        assert response.json()["settings"][FIELD] == 30


# ── 4. the mutation inventory ─────────────────────────────────────────────────

def test_the_patch_is_classified_as_personal_state():
    # Otherwise the group/project gate would judge a per-user preference, and the 0378
    # inventory guard would have to invent a group for a URL that names none.
    from modules.flow_gate.services import mutation_policy as policy

    resource, reason = policy.classify_mutation_route(
        "/flowgate/api/v1/me/ui-settings", {"PATCH"}
    )
    assert (resource, reason) == ("personal", "per_user_state")


def test_the_router_is_mounted_under_the_v1_context():
    from routers.main import app

    paths = {route.path for route in app.routes if getattr(route, "path", "").endswith("ui-settings")}
    assert paths == {"/flowgate/api/v1/me/ui-settings"}


# ── 5. the store's SQL ────────────────────────────────────────────────────────

class TestStoreSql:
    def test_reading_is_a_single_keyed_lookup(self, fake_store):
        store.get("u1")
        sql, params = fake_store.fetched[0]
        assert "WHERE user_id = ?" in sql
        assert params == ["u1"]
        assert fake_store.executed == []

    def test_upsert_updates_the_sent_column_and_leaves_created_at_alone(self, fake_store):
        store.upsert("u1", columns={FIELD: -1}, unset_columns=uss.defaults(), updated_at="T")
        sql, params = fake_store.executed[0]
        set_clause = sql.split("DO UPDATE SET")[1]
        assert f"{FIELD} = excluded.{FIELD}" in set_clause
        assert "updated_at = excluded.updated_at" in set_clause
        # created_at in the SET list would reset "when this row first appeared" on every
        # save and leave the column meaning nothing.
        assert "created_at" not in set_clause
        assert params == ["u1", -1, "T", "T"]

    def test_the_sql_stays_in_the_dialect_normal_form(self, fake_store):
        # `?` placeholders and `excluded.` are what db/dialect.py rewrites for MySQL and
        # passes through for PostgreSQL. Hand-written per-dialect SQL bypasses that.
        store.upsert("u1", columns={FIELD: 30}, unset_columns=uss.defaults(), updated_at="T")
        sql, _params = fake_store.executed[0]
        assert sql.count("?") == 4
        assert "ON CONFLICT (user_id) DO UPDATE SET" in sql
        assert "%s" not in sql

    def test_an_empty_patch_never_reaches_the_table(self, fake_store):
        with pytest.raises(ValueError):
            store.upsert("u1", columns={}, unset_columns=uss.defaults(), updated_at="T")
        assert fake_store.executed == []


# ── 6. the table ──────────────────────────────────────────────────────────────

@pytest.fixture
def ui_settings_db(all_migrations_db):
    conn = all_migrations_db
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,"
        "first_login_required,created_at,updated_at) VALUES "
        "('usr_0452','ui0452','ui0452@test','pw',1,0,0,datetime('now'),datetime('now'))"
    )
    conn.commit()
    yield conn
    conn.execute("DELETE FROM user_ui_settings WHERE user_id = 'usr_0452'")
    conn.execute("DELETE FROM users WHERE user_id = 'usr_0452'")
    conn.commit()


def _insert(conn, **overrides):
    row = {"user_id": "usr_0452", FIELD: 30, "created_at": "T", "updated_at": "T"}
    row.update(overrides)
    conn.execute(
        f"INSERT INTO user_ui_settings (user_id, {FIELD}, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        [row["user_id"], row[FIELD], row["created_at"], row["updated_at"]],
    )


class TestMigration:
    def test_it_ships_for_every_dialect(self):
        # check_db_ready compares the DB_TYPE directory against the migrations table, so
        # a single-dialect migration leaves the other engines "ready" without the table.
        for dialect in DIALECTS:
            assert (MIGRATIONS_DIR / dialect / MIGRATION_NAME).is_file(), dialect

    def test_the_number_is_not_shared_with_another_file(self):
        # The loader reads *.sql in sort order; a duplicate number has hidden a file
        # before. DB0004 §3-1: the later arrival renumbers, so if this fails, this file
        # is the one that moves.
        for dialect in DIALECTS:
            names = sorted(p.name for p in (MIGRATIONS_DIR / dialect).glob(f"{ORDINAL}_*.sql"))
            assert names == [MIGRATION_NAME], (dialect, names)

    def test_it_only_adds(self):
        for dialect in DIALECTS:
            body = (MIGRATIONS_DIR / dialect / MIGRATION_NAME).read_text(encoding="utf-8")
            statements = "\n".join(
                line for line in body.splitlines() if not line.lstrip().startswith("--")
            )
            assert "ALTER TABLE" not in statements.upper()
            assert "DROP TABLE" not in statements.upper()
            assert "user_chat_settings" not in statements

    def test_the_dialects_differ_only_where_DB0004_says_they_may(self):
        bodies = {
            d: (MIGRATIONS_DIR / d / MIGRATION_NAME).read_text(encoding="utf-8")
            for d in DIALECTS
        }
        assert "VARCHAR(191)" in bodies["mysql"]
        assert "VARCHAR(191)" not in bodies["sqlite"]
        assert "VARCHAR(191)" not in bodies["postgres"]
        assert "BEGIN;" in bodies["sqlite"]
        assert "BEGIN;" not in bodies["mysql"]
        assert "BEGIN;" not in bodies["postgres"]

    def test_one_row_per_user(self, ui_settings_db):
        _insert(ui_settings_db)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(ui_settings_db)
        ui_settings_db.rollback()

    @pytest.mark.parametrize("value", list(uss.RETENTION_DOMAIN_MINUTES))
    def test_every_domain_member_is_accepted(self, ui_settings_db, value):
        _insert(ui_settings_db, **{FIELD: value})
        ui_settings_db.rollback()

    @pytest.mark.parametrize("value", [45, -2, 29, 1441, 100_000])
    def test_values_outside_the_domain_are_refused(self, ui_settings_db, value):
        with pytest.raises(sqlite3.IntegrityError):
            _insert(ui_settings_db, **{FIELD: value})
        ui_settings_db.rollback()

    def test_no_column_carries_a_default_and_none_is_nullable(self, ui_settings_db):
        # The one source of truth for the default is the service; a second copy in the
        # schema is a copy that eventually disagrees (DB0004 §0-2).
        columns = ui_settings_db.execute("PRAGMA table_info(user_ui_settings)").fetchall()
        assert columns, "user_ui_settings is missing"
        for column in columns:
            assert column["dflt_value"] is None, column["name"]
            assert column["notnull"] == 1, column["name"]

    def test_the_primary_key_is_user_id_alone(self, ui_settings_db):
        columns = ui_settings_db.execute("PRAGMA table_info(user_ui_settings)").fetchall()
        keyed = [c["name"] for c in columns if c["pk"]]
        assert keyed == ["user_id"]

    def test_deleting_the_user_takes_the_settings_with_them(self, ui_settings_db):
        _insert(ui_settings_db)
        ui_settings_db.commit()
        ui_settings_db.execute("DELETE FROM users WHERE user_id = 'usr_0452'")
        ui_settings_db.commit()
        left = ui_settings_db.execute(
            "SELECT COUNT(*) AS n FROM user_ui_settings WHERE user_id = 'usr_0452'"
        ).fetchone()
        assert left["n"] == 0

    def test_a_second_save_keeps_created_at_and_moves_updated_at(self, ui_settings_db):
        _insert(ui_settings_db, created_at="first", updated_at="first")
        ui_settings_db.execute(
            f"INSERT INTO user_ui_settings (user_id, {FIELD}, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET "
            f"{FIELD} = excluded.{FIELD}, updated_at = excluded.updated_at",
            ["usr_0452", -1, "second", "second"],
        )
        row = ui_settings_db.execute(
            "SELECT * FROM user_ui_settings WHERE user_id = 'usr_0452'"
        ).fetchone()
        assert row["created_at"] == "first"
        assert row["updated_at"] == "second"
        assert row[FIELD] == -1
        ui_settings_db.rollback()

    def test_the_chat_settings_table_is_untouched(self, ui_settings_db):
        # DB0004 §0-1: the whole reason for a second table is that 0362's row invariant
        # must keep meaning what it meant.
        columns = {
            c["name"] for c in ui_settings_db.execute(
                "PRAGMA table_info(user_chat_settings)"
            ).fetchall()
        }
        assert columns == {
            "user_id", "send_action", "context_mode", "context_turns",
            "created_at", "updated_at",
        }
