"""Group 0362 T0012 — per-user chat settings and the recent-turn context window.

Three things are pinned here, in the order the work was specified:

1. the window arithmetic and the mention text it produces (L0010 §2-3/§2-4, P0009 시나리오 11~15),
2. the settings the window is read from, in both directions (L0010 §2-1/§2-2),
3. the table those settings live in, including the constraints DB0011 deliberately did
   NOT put in the schema.

The recurring theme is that a setting must never be able to break a conversation: an
unknown value arriving in a save is refused, but an unknown value already in storage is
repaired on the way out and the call proceeds.
"""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.api.v1 import chat_settings_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import user_chat_settings as store
from modules.flow_gate.services import chat_settings_service as css
from modules.flow_gate.services import conversation_query_service
from modules.flow_gate.services import invoke_mention_service as ims

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "sql" / "migrations"
MIGRATION_NAME = "076_user_chat_settings.sql"
DIALECTS = ("sqlite", "mysql", "postgres")


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
    monkeypatch.setattr(css, "chat_settings_store", mem)
    return mem


# ── 1. the window ─────────────────────────────────────────────────────────────

class TestContextWindow:
    def test_copy_path_on_a_long_conversation_folds_the_earlier_turns(self):
        # P0009 시나리오 11: head 30, nothing read, range 20.
        assert css.resolve_context_window(
            last_read=0, head_seq=30, mode="recent", turns=20
        ) == (10, 10)

    def test_short_conversation_folds_nothing(self):
        # 시나리오 12: the subtraction goes negative and the clamp catches it. A worker
        # told "0 turns are folded" would be reading filler on almost every call.
        assert css.resolve_context_window(
            last_read=0, head_seq=8, mode="recent", turns=20
        ) == (0, 0)

    def test_partly_read_provider_folds_only_what_it_has_not_read(self):
        # 시나리오 14: after_seq 10, but 1..4 were read long ago, so 6 turns fold.
        assert css.resolve_context_window(
            last_read=4, head_seq=30, mode="recent", turns=20
        ) == (10, 6)

    def test_a_caught_up_provider_is_never_dragged_backwards(self):
        # 시나리오 15: re-reading 17 turns would look like the conversation jumped back.
        assert css.resolve_context_window(
            last_read=27, head_seq=30, mode="recent", turns=20
        ) == (27, 0)

    def test_all_starts_where_the_reader_stopped_and_ignores_the_head(self):
        # 시나리오 13. head_seq arrives as 0 precisely because [전체] does not ask for it.
        assert css.resolve_context_window(
            last_read=12, head_seq=0, mode="all", turns=20
        ) == (12, 0)

    def test_empty_conversation(self):
        assert css.resolve_context_window(
            last_read=0, head_seq=0, mode="recent", turns=20
        ) == (0, 0)

    def test_cursor_past_the_end_of_the_conversation_is_pulled_back_to_the_head(self):
        # Turns cannot be deleted, so this only happens if storage was edited by hand.
        # There is nothing new to hand over, and nothing is reported as folded.
        assert css.resolve_context_window(
            last_read=40, head_seq=30, mode="recent", turns=20
        ) == (30, 0)


# ── 2. the mention ────────────────────────────────────────────────────────────

class TestFoldedMention:
    DOC = "flowgate.default.0362.0006-CH"

    @staticmethod
    def _build(monkeypatch, *, head_seq, last_read=0, settings=None, **overrides):
        monkeypatch.setattr(ims, "_chat_lookup_sections", lambda **_kwargs: [])
        monkeypatch.setattr(
            ims.conversation_turns, "current_head_seq", lambda doc_id: head_seq
        )
        monkeypatch.setattr(
            ims.conversation_turns, "get_last_read_seq", lambda *_a: last_read
        )
        resolved = css.defaults()
        resolved.update(settings or {})
        monkeypatch.setattr(
            ims.chat_settings_service, "resolve_chat_settings_safe", lambda _u: resolved
        )
        args = {
            "doc_id": TestFoldedMention.DOC,
            "project": "flowgate",
            "module": "default",
            "group_name": "flowgate.default.0362",
            "raw_token": "RAW",
            "token_id": "tok_20260731_091844",
            "api_base_url": "http://h:1/api/v1",
            "user_id": "usr_admin",
        }
        args.update(overrides)
        return ims.build_conversation_mention(**args)

    def test_fold_notice_reproduces_the_agreed_wording(self, monkeypatch):
        text = self._build(monkeypatch, head_seq=30)
        assert (
            "The 10 turns before that point are folded, not deleted. Read them when you need the\n"
            "earlier context:\n"
            f"GET http://h:1/api/v1/conversation/{self.DOC}/turns?before_seq=11\n"
            "Authorization: Bearer RAW\n"
            "If `prev_before_seq` is not null, call again with that value to keep going further back.\n"
            "Paging back does not consume this token either."
        ) in text

    def test_start_paragraph_stops_claiming_the_position_is_the_readers_own(self, monkeypatch):
        text = self._build(monkeypatch, head_seq=30)
        assert "The after_seq above is where the server wants you to start" in text
        assert "moved forward to the recent-conversation range this user chose" in text
        # The original sentence is false once the start point has moved, and leaving it
        # in would tell the worker it had already read the folded turns.
        assert "The after_seq above is YOUR last read position" not in text

    def test_notice_sits_between_the_start_paragraph_and_the_reply_instructions(self, monkeypatch):
        text = self._build(monkeypatch, head_seq=30)
        assert (
            text.index("where the server wants you to start")
            < text.index("folded, not deleted")
            < text.index("To reply, append ONE turn")
        )

    def test_before_seq_is_one_past_the_start_so_the_boundary_turn_is_included(self, monkeypatch):
        # 시나리오 14, the invoke path. The cursor only exists when a provider is pinned:
        # the copy path has nobody to have read anything yet, so it is always 0.
        text = self._build(
            monkeypatch, head_seq=30, last_read=4, provider_id="prov_claude_opus_5"
        )
        assert f"?after_seq=10&include_head=1" in text
        # Backward paging is seq < before_seq: before_seq=10 would silently drop turn 10.
        assert f"turns?before_seq=11" in text
        assert "The 6 turns before that point are folded" in text

    def test_a_single_folded_turn_reads_as_one_turn(self, monkeypatch):
        text = self._build(monkeypatch, head_seq=21)
        assert "The 1 turn before that point is folded, not deleted. Read it when you need the" in text
        assert "turns are folded" not in text

    def test_short_conversation_keeps_the_original_paragraph_and_adds_nothing(self, monkeypatch):
        text = self._build(monkeypatch, head_seq=8)
        assert "The after_seq above is YOUR last read position" in text
        assert "folded" not in text
        assert "?after_seq=0&include_head=1" in text

    def test_all_produces_the_pre_feature_mention_without_asking_for_the_head(self, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(ims, "_chat_lookup_sections", lambda **_kwargs: [])
        monkeypatch.setattr(
            ims.conversation_turns,
            "current_head_seq",
            lambda doc_id: seen.append(doc_id) or 30,
        )
        chosen = css.defaults()
        chosen["context_mode"] = "all"
        monkeypatch.setattr(
            ims.chat_settings_service, "resolve_chat_settings_safe", lambda _u: chosen
        )
        text = ims.build_conversation_mention(
            doc_id=self.DOC, project="flowgate", module="default",
            group_name="flowgate.default.0362", raw_token="RAW",
            token_id="tok_20260731_092155", api_base_url="http://h:1/api/v1",
            user_id="usr_admin",
        )
        # Costing exactly what it cost before is what makes "put it back on [전체] and
        # compare" a usable way to find out whether this feature is the problem.
        assert seen == []
        assert "?after_seq=0&include_head=1" in text
        assert "The after_seq above is YOUR last read position" in text
        assert "folded" not in text

    def test_an_unreachable_settings_store_still_produces_a_mention(self, monkeypatch):
        monkeypatch.setattr(ims, "_chat_lookup_sections", lambda **_kwargs: [])
        monkeypatch.setattr(ims.conversation_turns, "current_head_seq", lambda doc_id: 8)

        def _boom(_user_id):
            raise RuntimeError("settings store is down")

        monkeypatch.setattr(css, "chat_settings_store", type("X", (), {"get": staticmethod(_boom)}))
        text = ims.build_conversation_mention(
            doc_id=self.DOC, project="flowgate", module="default",
            group_name="flowgate.default.0362", raw_token="RAW",
            token_id="tok", api_base_url="http://h:1/api/v1", user_id="usr_admin",
        )
        assert "?after_seq=0&include_head=1" in text


# ── 3. reading the settings ───────────────────────────────────────────────────

class TestResolveSettings:
    def test_maximum_is_derived_from_the_read_page_cap_not_typed_twice(self):
        assert css.CONTEXT_TURNS_MAX == conversation_query_service.TURN_LIMIT_MAX

    def test_never_saved_is_the_defaults_and_says_so(self, memory_store):
        settings, is_default = css.resolve_chat_settings("u1")
        assert is_default is True
        assert settings == {
            "send_action": "none", "context_mode": "recent",
            "context_turns": 20, "updated_at": None,
        }

    def test_unknown_user_is_treated_like_someone_who_never_saved(self, memory_store):
        assert css.resolve_chat_settings(None) == (css.defaults(), True)

    def test_a_saved_row_comes_back_as_saved(self, memory_store):
        memory_store.row = {
            "user_id": "u1", "send_action": "invoke_ai", "context_mode": "recent",
            "context_turns": 30, "created_at": "x", "updated_at": "2026-07-30T18:22:41+09:00",
        }
        settings, is_default = css.resolve_chat_settings("u1")
        assert is_default is False
        assert settings["context_turns"] == 30
        assert settings["updated_at"] == "2026-07-30T18:22:41+09:00"

    def test_only_the_broken_field_is_reverted(self, memory_store):
        # P0009 시나리오 17: one unusable value must not take a good one down with it.
        memory_store.row = {
            "user_id": "u1", "send_action": "invoke_ai", "context_mode": "last_week",
            "context_turns": 20, "created_at": "x", "updated_at": "t",
        }
        settings, _ = css.resolve_chat_settings("u1")
        assert settings["context_mode"] == "recent"
        assert settings["send_action"] == "invoke_ai"

    def test_reading_never_writes(self, memory_store):
        memory_store.row = {
            "user_id": "u1", "send_action": "nonsense", "context_mode": "nonsense",
            "context_turns": -3, "created_at": "x", "updated_at": "t",
        }
        css.resolve_chat_settings("u1")
        # A repair written back would erase the value that explains the problem.
        assert memory_store.writes == []
        assert memory_store.row["send_action"] == "nonsense"

    @pytest.mark.parametrize(
        "stored,expected",
        [
            (10, 10),
            (css.CONTEXT_TURNS_MAX + 50, css.CONTEXT_TURNS_MAX),  # a lowered cap: keep as much as possible
            (0, css.CONTEXT_TURNS_DEFAULT),                        # not a shrunken domain, a wrong value
            (-5, css.CONTEXT_TURNS_DEFAULT),
            ("abc", css.CONTEXT_TURNS_DEFAULT),                    # SQLite affinity lets this past the CHECK
            (True, css.CONTEXT_TURNS_DEFAULT),
            (None, css.CONTEXT_TURNS_DEFAULT),
        ],
    )
    def test_turn_count_repairs(self, memory_store, stored, expected):
        memory_store.row = {
            "user_id": "u1", "send_action": "none", "context_mode": "recent",
            "context_turns": stored, "created_at": "x", "updated_at": "t",
        }
        settings, _ = css.resolve_chat_settings("u1")
        assert settings["context_turns"] == expected

    def test_response_carries_the_limits_so_no_screen_holds_its_own_copy(self, memory_store):
        body = css.settings_response("u1")
        assert body["domain"] == {
            "send_action": ["copy_mention", "invoke_ai", "none"],
            "context_mode": ["recent", "all"],
            "context_turns_presets": [5, 10, 15, 20, 30],
            "context_turns_min": 1,
            "context_turns_max": css.CONTEXT_TURNS_MAX,
        }
        assert "updated_at" not in body["defaults"]


# ── 4. saving the settings ────────────────────────────────────────────────────

class TestSaveSettings:
    def test_only_the_sent_field_is_written(self, memory_store):
        css.save_chat_settings("u1", {"send_action": "copy_mention"})
        assert memory_store.writes == [{"send_action": "copy_mention"}]

    def test_switching_to_all_leaves_the_turn_count_where_it_was(self, memory_store):
        css.save_chat_settings("u1", {"context_mode": "recent", "context_turns": 45})
        css.save_chat_settings("u1", {"context_mode": "all"})
        assert memory_store.row["context_turns"] == 45
        assert memory_store.row["context_mode"] == "all"

    def test_saving_flips_is_default_and_answers_with_a_fresh_read(self, memory_store):
        body = css.save_chat_settings("u1", {"context_turns": 45})
        assert body["is_default"] is False
        assert body["settings"]["context_turns"] == 45
        assert body["settings"]["updated_at"]

    def test_an_empty_patch_creates_no_row(self, memory_store):
        body = css.save_chat_settings("u1", {})
        # A defaults row here would flip is_default to false and block the hand-over
        # of the [전송 시] value still sitting in some other browser, forever.
        assert memory_store.writes == []
        assert memory_store.row is None
        assert body["is_default"] is True

    @pytest.mark.parametrize(
        "patch,field,message",
        [
            ({"send_action": "auto_copy"}, "send_action",
             "send_action must be one of copy_mention, invoke_ai, none."),
            ({"context_mode": "last_week"}, "context_mode",
             "context_mode must be one of recent, all."),
            ({"context_turns": 0}, "context_turns",
             f"context_turns must be between 1 and {css.CONTEXT_TURNS_MAX}."),
            ({"context_turns": css.CONTEXT_TURNS_MAX + 1}, "context_turns",
             f"context_turns must be between 1 and {css.CONTEXT_TURNS_MAX}."),
            ({"context_turns": "20"}, "context_turns", "context_turns must be an integer."),
            ({"context_turns": True}, "context_turns", "context_turns must be an integer."),
            ({"send_action": None}, "send_action", "send_action must not be null."),
            ({"auto_copy": True}, "auto_copy", "unknown field: auto_copy."),
        ],
    )
    def test_rejections(self, memory_store, patch, field, message):
        with pytest.raises(css.ChatSettingsError) as exc:
            css.save_chat_settings("u1", patch)
        assert exc.value.field == field
        assert exc.value.message == message
        assert memory_store.writes == []

    def test_the_range_message_quotes_the_live_limits(self, memory_store):
        with pytest.raises(css.ChatSettingsError) as exc:
            css.validate_patch({"context_turns": 0})
        assert str(css.CONTEXT_TURNS_MIN) in exc.value.message
        assert str(css.CONTEXT_TURNS_MAX) in exc.value.message

    def test_several_bad_fields_always_report_the_same_one(self, memory_store):
        with pytest.raises(css.ChatSettingsError) as exc:
            css.validate_patch({"context_turns": 0, "send_action": "auto_copy"})
        assert exc.value.field == "send_action"


# ── 5. the endpoint ───────────────────────────────────────────────────────────

def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "username": "U"}
    return TestClient(app)


class TestChatSettingsEndpoint:
    def test_handlers_stay_synchronous(self):
        # The store below is synchronous; an async handler would block the loop for
        # every other request (L0010 §5).
        assert not inspect.iscoroutinefunction(chat_settings_routes.get_my_chat_settings)
        assert not inspect.iscoroutinefunction(chat_settings_routes.patch_my_chat_settings)

    def test_get_answers_with_defaults_for_a_new_user(self, memory_store):
        body = _client().get("/me/chat-settings").json()
        assert body["ok"] is True
        assert body["is_default"] is True
        assert body["settings"]["send_action"] == "none"
        assert body["domain"]["context_turns_max"] == css.CONTEXT_TURNS_MAX

    def test_patch_saves_and_returns_the_stored_settings(self, memory_store):
        response = _client().patch("/me/chat-settings", json={"context_turns": 45})
        assert response.status_code == 200
        assert response.json()["settings"]["context_turns"] == 45
        assert memory_store.writes == [{"context_turns": 45}]

    def test_patch_out_of_range_is_a_422_naming_the_field(self, memory_store):
        response = _client().patch("/me/chat-settings", json={"context_turns": 0})
        assert response.status_code == 422
        assert response.json() == {
            "ok": False,
            "error": {
                "code": "invalid_request",
                "field": "context_turns",
                "message": f"context_turns must be between 1 and {css.CONTEXT_TURNS_MAX}.",
            },
        }
        assert memory_store.writes == []

    def test_an_unknown_key_is_refused_rather_than_ignored(self, memory_store):
        response = _client().patch("/me/chat-settings", json={"auto_copy": True})
        assert response.status_code == 422
        assert response.json()["error"]["field"] == "auto_copy"

    def test_a_string_number_is_not_quietly_coerced(self, memory_store):
        response = _client().patch("/me/chat-settings", json={"context_turns": "20"})
        assert response.status_code == 422
        assert response.json()["error"]["message"] == "context_turns must be an integer."

    def test_the_address_names_no_user(self):
        paths = {route.path for route in chat_settings_routes.router.routes}
        assert paths == {"/me/chat-settings"}


# ── 6. the store's SQL ────────────────────────────────────────────────────────

class TestStoreSql:
    def test_reading_is_a_single_keyed_lookup(self, fake_store):
        store.get("u1")
        sql, params = fake_store.fetched[0]
        assert "WHERE user_id = ?" in sql
        assert params == ["u1"]
        assert fake_store.executed == []

    def test_upsert_updates_only_the_sent_column(self, fake_store):
        store.upsert(
            "u1",
            columns={"send_action": "copy_mention"},
            unset_columns=css.defaults(),
            updated_at="T",
        )
        sql, params = fake_store.executed[0]
        set_clause = sql.split("DO UPDATE SET")[1]
        assert "send_action = excluded.send_action" in set_clause
        assert "updated_at = excluded.updated_at" in set_clause
        assert "context_mode" not in set_clause
        assert "context_turns" not in set_clause
        # created_at in the SET list would reset "when this row first appeared" on
        # every save and leave the column meaning nothing.
        assert "created_at" not in set_clause
        # All six values still travel, because the INSERT branch has NOT NULL columns
        # the request did not mention.
        assert params == ["u1", "copy_mention", "recent", 20, "T", "T"]

    def test_an_empty_patch_never_reaches_the_table(self, fake_store):
        with pytest.raises(ValueError):
            store.upsert("u1", columns={}, unset_columns=css.defaults(), updated_at="T")
        assert fake_store.executed == []


# ── 7. the table ──────────────────────────────────────────────────────────────

@pytest.fixture
def chat_settings_db(all_migrations_db):
    conn = all_migrations_db
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,"
        "first_login_required,created_at,updated_at) VALUES "
        "('usr_0362','chat0362','chat0362@test','pw',1,0,0,datetime('now'),datetime('now'))"
    )
    conn.commit()
    yield conn
    conn.execute("DELETE FROM user_chat_settings WHERE user_id = 'usr_0362'")
    conn.execute("DELETE FROM users WHERE user_id = 'usr_0362'")
    conn.commit()


def _insert(conn, **overrides):
    row = {
        "user_id": "usr_0362", "send_action": "none", "context_mode": "recent",
        "context_turns": 20, "created_at": "T", "updated_at": "T",
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO user_chat_settings "
        "(user_id, send_action, context_mode, context_turns, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [row["user_id"], row["send_action"], row["context_mode"],
         row["context_turns"], row["created_at"], row["updated_at"]],
    )


class TestMigration:
    def test_it_ships_for_every_dialect(self):
        # check_db_ready compares the DB_TYPE directory against the migrations table, so
        # a single-dialect migration leaves the other engines "ready" without the table.
        for dialect in DIALECTS:
            assert (MIGRATIONS_DIR / dialect / MIGRATION_NAME).is_file(), dialect

    def test_the_number_is_not_shared_with_another_file(self):
        # The loader reads *.sql in sort order; a duplicate number has hidden a file before.
        for dialect in DIALECTS:
            names = sorted(p.name for p in (MIGRATIONS_DIR / dialect).glob("076_*.sql"))
            assert names == [MIGRATION_NAME], (dialect, names)

    def test_it_only_adds(self):
        for dialect in DIALECTS:
            body = (MIGRATIONS_DIR / dialect / MIGRATION_NAME).read_text(encoding="utf-8")
            assert "ALTER TABLE" not in body
            assert "DROP TABLE" not in body

    def test_one_row_per_user(self, chat_settings_db):
        _insert(chat_settings_db)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(chat_settings_db)
        chat_settings_db.rollback()

    def test_the_two_closed_domains_are_enforced(self, chat_settings_db):
        for column, value in (("send_action", "auto_copy"), ("context_mode", "last_week")):
            with pytest.raises(sqlite3.IntegrityError):
                _insert(chat_settings_db, **{column: value})
            chat_settings_db.rollback()

    def test_zero_turns_is_refused(self, chat_settings_db):
        # n = 0 means the invoked AI does not even receive the message it must answer.
        with pytest.raises(sqlite3.IntegrityError):
            _insert(chat_settings_db, context_turns=0)
        chat_settings_db.rollback()

    def test_the_upper_bound_is_left_to_the_code(self, chat_settings_db):
        # The cap is derived from TURN_LIMIT_MAX and moves. A CHECK here would turn
        # lowering that constant into a table-rebuild migration that fails on old rows.
        _insert(chat_settings_db, context_turns=100_000)
        chat_settings_db.rollback()

    def test_no_column_carries_a_default(self, chat_settings_db):
        # The one source of truth for defaults is the service; a second copy in the
        # schema is a copy that eventually disagrees.
        columns = chat_settings_db.execute(
            "PRAGMA table_info(user_chat_settings)"
        ).fetchall()
        assert columns, "user_chat_settings is missing"
        for column in columns:
            assert column["dflt_value"] is None, column["name"]
            assert column["notnull"] == 1, column["name"]

    def test_deleting_the_user_takes_the_settings_with_them(self, chat_settings_db):
        _insert(chat_settings_db)
        chat_settings_db.commit()
        chat_settings_db.execute("DELETE FROM users WHERE user_id = 'usr_0362'")
        chat_settings_db.commit()
        left = chat_settings_db.execute(
            "SELECT COUNT(*) AS n FROM user_chat_settings WHERE user_id = 'usr_0362'"
        ).fetchone()
        assert left["n"] == 0
