"""flowgate.v02test.0056 — the Designer's three dock sizes in /me/ui-settings (R 0056.0001 §2).

Pinned here:

1. reading: no row, a strange stored value or an unreadable store all answer 280 / 300 / 320,
2. saving: a range check per field, only the sent sizes are written,
3. the sizes and the finished-card retention never touch each other's row, so ``is_default``
   keeps meaning "never chose a retention",
4. the table (migration 206) ships for every dialect.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.api.v1 import ui_settings_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import user_designer_layout as store
from modules.flow_gate.services import ui_settings_service as uss

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "sql" / "migrations"
MIGRATION_NAME = "206_user_designer_layout.sql"
LEFT = uss.DESIGNER_LEFT_WIDTH_FIELD
RIGHT = uss.DESIGNER_RIGHT_WIDTH_FIELD
BOTTOM = uss.DESIGNER_BOTTOM_HEIGHT_FIELD
RETENTION = uss.RETENTION_FIELD


class _MemoryStore:
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
def stores(monkeypatch):
    retention, layout = _MemoryStore(), _MemoryStore()
    monkeypatch.setattr(uss, "ui_settings_store", retention)
    monkeypatch.setattr(uss, "designer_layout_store", layout)
    return retention, layout


def _sizes(settings):
    return settings[LEFT], settings[RIGHT], settings[BOTTOM]


class TestReading:
    def test_never_saved_reads_the_defaults(self, stores):
        settings, is_default = uss.resolve_ui_settings("u1")
        assert _sizes(settings) == (280, 300, 320)
        assert is_default is True

    def test_saved_sizes_come_back(self, stores):
        stores[1].row = {"user_id": "u1", "left_width": 410, "right_width": 520, "bottom_height": 233}
        assert _sizes(uss.resolve_ui_settings("u1")[0]) == (410, 520, 233)

    @pytest.mark.parametrize("stored", [None, True, "410", 0, -5, 199, 4001, 12.5])
    def test_a_strange_stored_size_reads_as_its_default_without_writing(self, stores, stored):
        stores[1].row = {"user_id": "u1", "left_width": stored, "right_width": 520, "bottom_height": 5}
        assert _sizes(uss.resolve_ui_settings("u1")[0]) == (280, 520, 320)
        assert stores[1].writes == []
        assert stores[1].row["left_width"] == stored

    def test_the_range_ends_are_kept(self, stores):
        stores[1].row = {"user_id": "u1", "left_width": 200, "right_width": 240, "bottom_height": 4000}
        assert _sizes(uss.resolve_ui_settings("u1")[0]) == (200, 240, 4000)

    def test_an_unreadable_size_table_does_not_stop_the_settings(self, stores, monkeypatch):
        def _boom(_user_id):
            raise RuntimeError("designer layout table is down")

        monkeypatch.setattr(uss, "designer_layout_store", type("X", (), {"get": staticmethod(_boom)}))
        stores[0].row = {"user_id": "u1", RETENTION: 60, "created_at": "x", "updated_at": "t"}
        settings, is_default = uss.resolve_ui_settings("u1")
        assert settings[RETENTION] == 60
        assert _sizes(settings) == (280, 300, 320)
        assert is_default is False

    def test_the_response_carries_defaults_and_ranges(self, stores):
        body = uss.settings_response("u1")
        assert body["defaults"][LEFT] == 280
        assert body["defaults"][RIGHT] == 300
        assert body["defaults"][BOTTOM] == 320
        assert body["domain"][LEFT] == {"min": 200, "max": 4000}
        assert body["domain"][RIGHT] == {"min": 240, "max": 4000}
        assert body["domain"][BOTTOM] == {"min": 160, "max": 4000}


class TestSaving:
    def test_only_the_sent_size_is_written(self, stores):
        body = uss.save_ui_settings("u1", {LEFT: 350})
        assert stores[1].writes == [{"left_width": 350}]
        assert _sizes(body["settings"]) == (350, 300, 320)

    def test_all_three_in_one_save(self, stores):
        body = uss.save_ui_settings("u1", {LEFT: 400, RIGHT: 500, BOTTOM: 200})
        assert stores[1].writes == [{"left_width": 400, "right_width": 500, "bottom_height": 200}]
        assert _sizes(body["settings"]) == (400, 500, 200)

    def test_saving_a_size_does_not_create_the_retention_row(self, stores):
        body = uss.save_ui_settings("u1", {BOTTOM: 400})
        assert stores[0].writes == []
        assert stores[0].row is None
        # Still "never chose a retention".
        assert body["is_default"] is True

    def test_saving_the_retention_writes_no_size(self, stores):
        body = uss.save_ui_settings("u1", {RETENTION: 120})
        assert stores[0].writes == [{RETENTION: 120}]
        assert stores[1].writes == []
        assert _sizes(body["settings"]) == (280, 300, 320)

    def test_both_kinds_in_one_patch_go_to_their_own_rows(self, stores):
        uss.save_ui_settings("u1", {RETENTION: 60, RIGHT: 333})
        assert stores[0].writes == [{RETENTION: 60}]
        assert stores[1].writes == [{"right_width": 333}]

    @pytest.mark.parametrize(
        "patch,field,message",
        [
            ({LEFT: 199}, LEFT, f"{LEFT} must be between 200 and 4000."),
            ({RIGHT: 239}, RIGHT, f"{RIGHT} must be between 240 and 4000."),
            ({BOTTOM: 159}, BOTTOM, f"{BOTTOM} must be between 160 and 4000."),
            ({LEFT: 4001}, LEFT, f"{LEFT} must be between 200 and 4000."),
            ({LEFT: None}, LEFT, f"{LEFT} must not be null."),
            ({LEFT: "300"}, LEFT, f"{LEFT} must be an integer."),
            ({LEFT: 300.5}, LEFT, f"{LEFT} must be an integer."),
            ({LEFT: True}, LEFT, f"{LEFT} must be an integer."),
            ({"designer_top_height": 300}, "designer_top_height", "unknown field: designer_top_height."),
        ],
    )
    def test_rejections_write_nothing(self, stores, patch, field, message):
        with pytest.raises(uss.UiSettingsError) as exc:
            uss.save_ui_settings("u1", patch)
        assert exc.value.field == field
        assert exc.value.message == message
        assert stores[0].writes == [] and stores[1].writes == []

    def test_one_bad_size_stops_the_whole_patch(self, stores):
        with pytest.raises(uss.UiSettingsError):
            uss.save_ui_settings("u1", {LEFT: 300, BOTTOM: 10})
        assert stores[1].writes == []


class TestEndpoint:
    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(ui_settings_routes.router)
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "username": "U"}
        return TestClient(app)

    def test_patch_then_get_round_trip(self, stores):
        client = self._client()
        response = client.patch("/me/ui-settings", json={LEFT: 444, RIGHT: 555, BOTTOM: 222})
        assert response.status_code == 200
        assert _sizes(client.get("/me/ui-settings").json()["settings"]) == (444, 555, 222)

    def test_out_of_range_is_a_422_naming_the_field(self, stores):
        response = self._client().patch("/me/ui-settings", json={BOTTOM: 5})
        assert response.status_code == 422
        assert response.json()["error"]["field"] == BOTTOM


class _FakeStore:
    def __init__(self):
        self.fetched: list[tuple] = []
        self.executed: list[tuple] = []

    def _fetch_one(self, sql, params=None):
        self.fetched.append((sql, params))
        return None

    def _execute(self, sql, params=None):
        self.executed.append((sql, params))


class TestStoreSql:
    def test_upsert_sets_only_the_sent_columns(self, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(store, "get_store", lambda: fake)
        store.upsert(
            "u1",
            columns={"right_width": 500},
            unset_columns={"left_width": 280, "right_width": 300, "bottom_height": 320},
            updated_at="T",
        )
        sql, params = fake.executed[0]
        set_clause = sql.split("DO UPDATE SET")[1]
        assert "right_width = excluded.right_width" in set_clause
        assert "left_width" not in set_clause
        assert "bottom_height" not in set_clause
        assert "created_at" not in set_clause
        assert params == ["u1", 280, 500, 320, "T", "T"]
        assert sql.count("?") == 6

    def test_an_empty_patch_never_reaches_the_table(self, monkeypatch):
        fake = _FakeStore()
        monkeypatch.setattr(store, "get_store", lambda: fake)
        with pytest.raises(ValueError):
            store.upsert("u1", columns={}, unset_columns={}, updated_at="T")
        assert fake.executed == []


class TestMigration:
    def test_it_ships_for_every_dialect_under_one_number(self):
        for dialect in ("sqlite", "mysql", "postgres"):
            assert (MIGRATIONS_DIR / dialect / MIGRATION_NAME).is_file(), dialect
            names = sorted(p.name for p in (MIGRATIONS_DIR / dialect).glob("206_*.sql"))
            assert names == [MIGRATION_NAME], (dialect, names)

    def test_the_table_has_no_defaults_and_no_nullable_column(self, all_migrations_db):
        columns = all_migrations_db.execute("PRAGMA table_info(user_designer_layout)").fetchall()
        assert [c["name"] for c in columns] == [
            "user_id", "left_width", "right_width", "bottom_height", "created_at", "updated_at",
        ]
        for column in columns:
            assert column["dflt_value"] is None, column["name"]
            assert column["notnull"] == 1, column["name"]

    def test_the_retention_table_is_untouched(self, all_migrations_db):
        columns = {
            c["name"] for c in all_migrations_db.execute("PRAGMA table_info(user_ui_settings)").fetchall()
        }
        assert columns == {"user_id", "ai_finished_card_retention_minutes", "created_at", "updated_at"}
