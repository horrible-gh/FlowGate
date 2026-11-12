"""flowgate.v02test.0025: chat AI settings.

1. Each chat keeps its own AI pick (per user), validated against what the user may select.
2. The [checkpoint] context mode: the chat's checkpoint (from this message / from the next message) decides the
   window of BOTH context paths (the CLI/Agent mention and the API model read); without one the last 5 turns go;
   [recent] / [all] ignore a stored checkpoint.
3. One display order over system AI and my AI (local and remote mixed). It orders the selection
   list; for an ordinary user the fallback chain stays the system order, for an administrator the
   system AI's relative order is saved as the fallback order in the same transaction.

Runs on a full-chain migrated SQLite DB (migrations 185 and 186 included).
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[3]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.auth.middleware import get_current_user  # noqa: E402
from modules.flow_gate.services import chat_doc_settings_service as cds  # noqa: E402
from modules.flow_gate.services import chat_settings_service as css  # noqa: E402
from modules.flow_gate.services import invoke_mention_service as ims  # noqa: E402
from modules.flow_gate.settings import ai_settings_service as ais  # noqa: E402

DOC_A = "flowgate.v02test.0025.0002-CH"
DOC_B = "flowgate.v02test.0025.0003-CH"
PROJECT = "proj_0025"

_STORE_MODULES = (
    "modules.flow_gate.db.connection",
    "modules.flow_gate.db.system_settings",
    "modules.flow_gate.db.projects",
    "modules.flow_gate.db.ai_providers",
    "modules.flow_gate.db.user_ai_defaults",
    "modules.flow_gate.db.user_ai_order",
    "modules.flow_gate.db.user_conversation_settings",
)


class _Store:
    """SQLite store with real BEGIN/COMMIT/ROLLBACK so the linked save's atomicity is observable."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._in_tx = False

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        if self._in_tx:
            yield self
            return
        self._in_tx = True
        self._conn.execute("BEGIN")
        try:
            yield self
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")
        finally:
            self._in_tx = False


_SEED = f"""
INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
    VALUES('{PROJECT}','P0025',1,datetime('now'),datetime('now'));
INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
    VALUES('u1','u1','u1@test.com','x',1,0,0,datetime('now'),datetime('now')),
          ('u2','u2','u2@test.com','x',1,0,0,datetime('now'),datetime('now')),
          ('adm','adm','adm@test.com','x',1,1,0,datetime('now'),datetime('now'));
"""


def _provider(store, pid, *, owner="system", user=None, location="local", agent=None, order=0, enabled=1):
    store._execute(
        "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, enabled, cli_command, "
        "sort_order, created_at, updated_at, owner_scope, owner_user_id, project_access, exec_location, target_agent_id) "
        "VALUES (?, NULL, ?, 'cli', 'claude', ?, 'claude -p', ?, 't', 't', ?, ?, 'all', ?, ?)",
        [pid, pid, enabled, order, owner, user, location, agent],
    )


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    path = migrated_sqlite_db("chat_ai_settings_0025.db", seed_sql=_SEED)
    s = _Store(str(path))
    for name in _STORE_MODULES:
        monkeypatch.setattr(importlib.import_module(name), "get_store", lambda s=s: s, raising=False)
    from modules.flow_gate.settings import workspace_service
    monkeypatch.setattr(workspace_service, "allowed_resource_ids", lambda *_a, **_k: None)
    # Mixed list: system local, system remote, u1's remote (runnable), u1's local (not runnable).
    _provider(s, "sys_local", order=0)
    _provider(s, "sys_remote", location="remote", order=1)
    _provider(s, "sys_off", order=2, enabled=0)
    _provider(s, "my_remote", owner="user", user="u1", location="remote", agent="agt_1", order=0)
    _provider(s, "my_local", owner="user", user="u1", order=1)
    _provider(s, "other_remote", owner="user", user="u2", location="remote", agent="agt_2", order=0)
    return s


def _system_order(store) -> list[str]:
    return [r["provider_id"] for r in ais._ai_db.list_scope(None)]


# ── 2. context checkpoint ─────────────────────────────────────────────────────

class TestResolveWindow:
    @pytest.mark.parametrize("mode,head,turns,cp,include,expected", [
        ("recent", 40, 10, None, True, 30),      # unchanged without a checkpoint
        ("all", 0, 10, None, True, 0),
        ("recent", 40, 10, 35, True, 30),        # [recent] ignores the checkpoint
        ("all", 0, 10, 12, False, 0),            # [all] ignores the checkpoint
        ("checkpoint", 40, 10, 35, True, 34),    # from this message (included)
        ("checkpoint", 40, 10, 35, False, 35),   # from the next message (excluded)
        ("checkpoint", 40, 10, 1, True, 0),      # from the first message = everything
        ("checkpoint", 40, 10, 40, False, 40),   # after the newest message = only what comes next
        ("checkpoint", 40, 10, None, True, 35),  # no checkpoint = the last 5 turns, whatever N is
        ("checkpoint", 3, 10, None, True, 0),
    ])
    def test_window(self, mode, head, turns, cp, include, expected):
        assert css.resolve_context_window(
            head_seq=head, mode=mode, turns=turns, checkpoint_seq=cp, checkpoint_include=include,
        ) == (expected, expected)

    @pytest.mark.parametrize("mode,cp,needs_head,limit", [
        ("recent", None, True, 10),
        ("recent", 7, True, 10),
        ("all", 7, False, None),
        ("checkpoint", 7, False, None),
        ("checkpoint", None, True, 5),
    ])
    def test_head_and_limit(self, mode, cp, needs_head, limit):
        assert css.window_needs_head(mode, cp) is needs_head
        assert css.context_turn_limit(mode, 10, cp) == limit

    def test_checkpoint_is_a_settable_context_mode(self):
        css.validate_patch({"context_mode": "checkpoint"})
        assert "checkpoint" in css.domain()["context_mode"]
        assert css.domain()["context_checkpoint_fallback_turns"] == 5
        with pytest.raises(css.ChatSettingsError):
            css.validate_patch({"context_mode": "since"})


class TestMigration186:
    def test_context_mode_accepts_checkpoint_only_after_widening(self, store):
        store._execute(
            "INSERT INTO user_chat_settings (user_id, send_action, context_mode, context_turns, created_at, updated_at) "
            "VALUES ('u1', 'none', 'checkpoint', 20, 't', 't')"
        )
        assert store._fetch_one("SELECT context_mode FROM user_chat_settings WHERE user_id='u1'")["context_mode"] == "checkpoint"
        with pytest.raises(sqlite3.IntegrityError):
            store._execute(
                "INSERT INTO user_chat_settings (user_id, send_action, context_mode, context_turns, created_at, updated_at) "
                "VALUES ('u2', 'none', 'bogus', 20, 't', 't')"
            )

    def test_a_185_checkpoint_without_kind_reads_as_include(self, store):
        store._execute(
            "INSERT INTO user_conversation_settings (user_id, doc_id, context_checkpoint_seq, updated_at) "
            "VALUES ('u1', ?, 9, 't')", [DOC_A],
        )
        assert cds.get_settings("u1", DOC_A)["context_checkpoint_kind"] == "include"
        assert cds.checkpoint_safe("u1", DOC_A) == (9, True)


class _WindowRoute:
    def __init__(self, head_seq):
        self.head_seq = head_seq
        self.calls = []

    def __call__(self, _doc, _raw, after_seq, before_seq, limit, include_head):
        self.calls.append((after_seq, limit))
        rows = list(range(after_seq + 1, self.head_seq + 1))
        picked = rows[: (limit or 200)]
        more = len(rows) > len(picked)
        body = {"ok": True, "head_seq": self.head_seq, "next_after_seq": picked[-1] if more and picked else None,
                "has_more": more, "truncated_by": None, "turns": [{"seq": n, "body": str(n)} for n in picked]}
        return SimpleNamespace(status_code=200, body=json.dumps(body).encode("utf-8"))


CHECKPOINT = {"context_mode": "checkpoint", "context_turns": 20}


class TestCheckpointReachesBothContextPaths:
    """What is actually sent: the mention's after_seq (CLI/Agent) and the turns the API model reads."""

    def _mention(self, monkeypatch, user, head, settings):
        monkeypatch.setattr(ims, "_chat_lookup_sections", lambda **_k: [])
        monkeypatch.setattr(ims.conversation_turns, "current_head_seq", lambda _d: head)
        monkeypatch.setattr(ims.chat_settings_service, "resolve_chat_settings_safe", lambda _u: settings)
        text = ims.build_conversation_mention(
            doc_id=DOC_A, project="flowgate", module="v02test", group_name="flowgate.v02test.0025",
            raw_token="RAW", token_id="tok_1", api_base_url="http://h/api/v1", user_id=user,
        )
        return int(text.split(f"/conversation/{DOC_A}/turns?after_seq=")[1].split("&")[0]), text

    def _api(self, monkeypatch, user, head, settings):
        from modules.flow_gate.api.v1 import conversation_routes
        from modules.flow_gate.services.ai_invoke import worker

        route = _WindowRoute(head)
        monkeypatch.setattr(conversation_routes, "_authenticate", lambda *_a: ({}, None))
        monkeypatch.setattr(conversation_routes, "_list_authenticated", route)
        monkeypatch.setattr(worker.conversation_turns, "current_head_seq", lambda _d: head)
        monkeypatch.setattr(worker.chat_settings_service, "resolve_chat_settings_safe", lambda _u: settings)
        status, payload = worker._conversation_context({"doc_ref": DOC_A, "issued_to": user}, "raw")
        assert status == 200
        return payload, route

    def _set(self, monkeypatch, body, user="u1", doc=DOC_A):
        monkeypatch.setattr(cds.conversation_turns, "current_head_seq", lambda _d: 30)
        return cds.save_settings(user, doc, body, project_id=PROJECT)

    def test_from_this_message_includes_it(self, store, monkeypatch):
        self._set(monkeypatch, {"context_checkpoint_seq": 25, "context_checkpoint_kind": "include"})

        after, text = self._mention(monkeypatch, "u1", 30, CHECKPOINT)
        payload, route = self._api(monkeypatch, "u1", 30, CHECKPOINT)

        assert after == 24
        assert "context checkpoint at seq 25" in text
        assert "before_seq=25" in text  # earlier turns folded, still readable
        assert payload["context_window"]["after_seq"] == 24
        assert (payload["context_window"]["checkpoint_seq"], payload["context_window"]["checkpoint_kind"]) == (25, "include")
        assert [t["seq"] for t in payload["turns"]] == list(range(25, 31))
        assert route.calls[0] == (24, None)

    def test_from_the_next_message_excludes_it(self, store, monkeypatch):
        self._set(monkeypatch, {"context_checkpoint_seq": 25, "context_checkpoint_kind": "exclude"})

        after, text = self._mention(monkeypatch, "u1", 30, CHECKPOINT)
        payload, route = self._api(monkeypatch, "u1", 30, CHECKPOINT)

        assert after == 25
        assert "context checkpoint after seq 25" in text and "seq 25 itself is excluded" in text
        assert "before_seq=26" in text
        assert payload["context_window"]["checkpoint_kind"] == "exclude"
        assert [t["seq"] for t in payload["turns"]] == list(range(26, 31))
        assert route.calls[0] == (25, None)

    def test_a_long_tail_after_the_checkpoint_is_sent_whole(self, store, monkeypatch):
        """Not capped at N: everything after the checkpoint goes, across pages."""
        monkeypatch.setattr(cds.conversation_turns, "current_head_seq", lambda _d: 500)
        cds.save_settings("u1", DOC_A, {"context_checkpoint_seq": 3}, project_id=PROJECT)
        payload, route = self._api(monkeypatch, "u1", 500, CHECKPOINT)
        assert [t["seq"] for t in payload["turns"]] == list(range(3, 501))
        assert len(route.calls) == 3

    def test_no_checkpoint_sends_the_last_5_turns(self, store, monkeypatch):
        after, text = self._mention(monkeypatch, "u1", 30, CHECKPOINT)
        payload, route = self._api(monkeypatch, "u1", 30, CHECKPOINT)
        assert after == 25 and "checkpoint" not in text
        assert [t["seq"] for t in payload["turns"]] == list(range(26, 31))
        assert route.calls[0] == (25, 5)
        assert "checkpoint_seq" not in payload["context_window"]

    @pytest.mark.parametrize("settings,expected_after,expected_limit", [
        ({"context_mode": "recent", "context_turns": 20}, 10, 20),
        ({"context_mode": "all", "context_turns": 20}, 0, None),
    ])
    def test_other_modes_ignore_a_stored_checkpoint(self, store, monkeypatch, settings, expected_after, expected_limit):
        self._set(monkeypatch, {"context_checkpoint_seq": 25, "context_checkpoint_kind": "exclude"})

        after, text = self._mention(monkeypatch, "u1", 30, settings)
        payload, route = self._api(monkeypatch, "u1", 30, settings)

        assert after == expected_after and "checkpoint" not in text
        assert payload["context_window"] == {
            "mode": settings["context_mode"], "context_turns": 20, "after_seq": expected_after, "folded": expected_after,
        }
        assert route.calls[0] == (expected_after, expected_limit)
        # Kept, not cleared: going back to [checkpoint] applies it again.
        assert self._mention(monkeypatch, "u1", 30, CHECKPOINT)[0] == 25

    def test_another_user_and_another_chat_are_untouched(self, store, monkeypatch):
        self._set(monkeypatch, {"context_checkpoint_seq": 25})

        after_u2, text_u2 = self._mention(monkeypatch, "u2", 30, CHECKPOINT)
        payload_u2, _ = self._api(monkeypatch, "u2", 30, CHECKPOINT)
        assert after_u2 == 25 and "checkpoint" not in text_u2  # u2 has none: the last 5
        assert payload_u2["context_window"]["after_seq"] == 25
        assert cds.checkpoint_safe("u1", DOC_B) is None

    def test_clearing_the_checkpoint_falls_back_to_5_turns(self, store, monkeypatch):
        self._set(monkeypatch, {"context_checkpoint_seq": 12, "context_checkpoint_kind": "exclude"})
        settings = self._set(monkeypatch, {"context_checkpoint_seq": None})
        assert (settings["context_checkpoint_seq"], settings["context_checkpoint_kind"]) == (None, None)
        after, _ = self._mention(monkeypatch, "u1", 30, CHECKPOINT)
        assert after == 25

    def test_a_broken_read_never_stops_the_chat(self, monkeypatch):
        def boom(*_a):
            raise RuntimeError("no table")
        monkeypatch.setattr(cds.store, "get", boom)
        assert cds.checkpoint_safe("u1", DOC_A) is None
        assert cds.checkpoint_seq_safe("u1", DOC_A) is None


# ── 1. per-chat AI + settings route ──────────────────────────────────────────

def _client(monkeypatch, user_id="u1"):
    from modules.flow_gate.documents import document_service
    from modules.flow_gate.documents.routers import conversation_turns as router_module

    monkeypatch.setattr(document_service, "get_document",
                        lambda doc_id: {"doc_id": doc_id, "type_code": "CH", "project_id": PROJECT})
    monkeypatch.setattr(cds.conversation_turns, "current_head_seq", lambda _d: 30)
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}
    return TestClient(app)


class TestChatAiSelection:
    def test_each_chat_keeps_its_own_ai(self, store, monkeypatch):
        client = _client(monkeypatch)
        assert client.patch(f"/documents/{DOC_A}/conversation/settings", json={"provider_id": "sys_remote"}).status_code == 200
        assert client.patch(f"/documents/{DOC_B}/conversation/settings", json={"provider_id": "my_remote"}).status_code == 200

        a = client.get(f"/documents/{DOC_A}/conversation/settings").json()["settings"]
        b = client.get(f"/documents/{DOC_B}/conversation/settings").json()["settings"]
        assert (a["provider_id"], b["provider_id"]) == ("sys_remote", "my_remote")

    def test_a_pick_is_per_user(self, store, monkeypatch):
        _client(monkeypatch, "u1").patch(f"/documents/{DOC_A}/conversation/settings", json={"provider_id": "my_remote"})
        other = _client(monkeypatch, "u2").get(f"/documents/{DOC_A}/conversation/settings").json()["settings"]
        assert other["provider_id"] is None

    @pytest.mark.parametrize("pid", ["my_local", "other_remote", "sys_off", "nope"])
    def test_an_ai_the_user_cannot_select_is_refused(self, store, monkeypatch, pid):
        response = _client(monkeypatch).patch(f"/documents/{DOC_A}/conversation/settings", json={"provider_id": pid})
        assert response.status_code == 422
        assert response.json()["field"] == "provider_id"
        assert cds.get_settings("u1", DOC_A)["provider_id"] is None

    def test_fields_are_saved_independently(self, store, monkeypatch):
        client = _client(monkeypatch)
        client.patch(f"/documents/{DOC_A}/conversation/settings", json={"provider_id": "sys_local"})
        client.patch(f"/documents/{DOC_A}/conversation/settings", json={"context_checkpoint_seq": 7})
        settings = client.get(f"/documents/{DOC_A}/conversation/settings").json()["settings"]
        assert (settings["provider_id"], settings["context_checkpoint_seq"]) == ("sys_local", 7)
        client.patch(f"/documents/{DOC_A}/conversation/settings", json={"provider_id": None})
        settings = client.get(f"/documents/{DOC_A}/conversation/settings").json()["settings"]
        assert (settings["provider_id"], settings["context_checkpoint_seq"]) == (None, 7)

    def test_checkpoint_kind_round_trip(self, store, monkeypatch):
        client = _client(monkeypatch)
        url = f"/documents/{DOC_A}/conversation/settings"
        saved = client.patch(url, json={"context_checkpoint_seq": 7}).json()["settings"]
        assert (saved["context_checkpoint_seq"], saved["context_checkpoint_kind"]) == (7, "include")
        saved = client.patch(url, json={"context_checkpoint_seq": 9, "context_checkpoint_kind": "exclude"}).json()["settings"]
        assert (saved["context_checkpoint_seq"], saved["context_checkpoint_kind"]) == (9, "exclude")
        saved = client.patch(url, json={"context_checkpoint_kind": "include"}).json()["settings"]
        assert (saved["context_checkpoint_seq"], saved["context_checkpoint_kind"]) == (9, "include")
        saved = client.patch(url, json={"context_checkpoint_seq": None}).json()["settings"]
        assert (saved["context_checkpoint_seq"], saved["context_checkpoint_kind"]) == (None, None)
        refused = client.patch(url, json={"context_checkpoint_kind": "exclude"})
        assert refused.status_code == 422 and refused.json()["reason"] == "no_checkpoint"

    @pytest.mark.parametrize("body,field", [
        ({"context_checkpoint_seq": 3, "context_checkpoint_kind": "after"}, "context_checkpoint_kind"),
        ({"context_checkpoint_seq": 3, "context_checkpoint_kind": None}, "context_checkpoint_kind"),
        ({"context_checkpoint_seq": 0}, "context_checkpoint_seq"),
        ({"context_checkpoint_seq": 31}, "context_checkpoint_seq"),
        ({"context_checkpoint_seq": "3"}, "context_checkpoint_seq"),
        ({"context_checkpoint_seq": True}, "context_checkpoint_seq"),
        ({"auto": 1}, "auto"),
    ])
    def test_bad_values_are_a_422_naming_the_field(self, store, monkeypatch, body, field):
        response = _client(monkeypatch).patch(f"/documents/{DOC_A}/conversation/settings", json=body)
        assert response.status_code == 422
        assert response.json()["field"] == field

    def test_a_deleted_ai_reads_as_no_pick(self, store, monkeypatch):
        _client(monkeypatch).patch(f"/documents/{DOC_A}/conversation/settings", json={"provider_id": "sys_remote"})
        store._execute("UPDATE ai_providers SET deleted_at = 't2' WHERE provider_id = 'sys_remote'")
        assert cds.get_settings("u1", DOC_A)["provider_id"] is None


# ── 3. display order over system AI and my AI ────────────────────────────────

def _runtime_ids(user_id):
    from modules.flow_gate.services.ai_invoke import admission
    return [p["id"] for p in admission.list_runtime_providers(PROJECT, user_id)["providers"]]


class TestDisplayOrder:
    def test_default_order_is_system_then_mine(self, store):
        assert ais.get_user_ai_order("u1")["order"] == ["sys_local", "sys_remote", "sys_off", "my_remote", "my_local"]
        assert _runtime_ids("u1") == ["sys_local", "sys_remote", "my_remote"]

    def test_my_remote_ai_goes_above_system_local_ai(self, store):
        order = ["my_remote", "sys_remote", "my_local", "sys_local", "sys_off"]
        saved = ais.save_user_ai_order("u1", order)
        assert saved["order"] == order
        assert saved["system_order_changed"] is False
        assert ais.get_user_ai_order("u1")["order"] == order          # kept on re-read
        assert _runtime_ids("u1") == ["my_remote", "sys_remote", "sys_local"]  # selection list follows
        # Display only for an ordinary user: fallback chain and default untouched.
        assert _system_order(store) == ["sys_local", "sys_remote", "sys_off"]
        assert [p["id"] for p in ais.resolve_global("u1")["providers"]] == ["sys_local", "sys_remote"]
        assert _runtime_ids("u2") == ["sys_local", "sys_remote", "other_remote"]  # another user unaffected

    def test_new_ai_is_appended_and_deleted_ai_drops_out(self, store):
        ais.save_user_ai_order("u1", ["my_remote", "sys_remote", "sys_local", "sys_off", "my_local"])
        _provider(store, "sys_new", order=3)
        store._execute("UPDATE ai_providers SET deleted_at = 't2' WHERE provider_id = 'sys_remote'")
        assert ais.get_user_ai_order("u1")["order"] == ["my_remote", "sys_local", "sys_off", "my_local", "sys_new"]

    @pytest.mark.parametrize("order,reason", [
        (["sys_local", "other_remote"], "unknown_provider"),
        (["sys_local", "sys_local"], "duplicate_id"),
        ("sys_local", "not_a_list"),
    ])
    def test_invalid_orders_are_refused(self, store, order, reason):
        with pytest.raises(ais.AiSettingsValidationError) as caught:
            ais.save_user_ai_order("u1", order)
        assert caught.value.errors[0]["reason"] == reason

    def test_admin_reorder_is_linked_to_the_fallback_order(self, store):
        _provider(store, "adm_remote", owner="user", user="adm", location="remote", agent="agt_3", order=0)
        order = ["sys_remote", "adm_remote", "sys_off", "sys_local"]
        saved = ais.save_user_ai_order("adm", order, link_system_order=True)
        assert saved["system_order_changed"] is True
        assert saved["order"] == order
        assert _system_order(store) == ["sys_remote", "sys_off", "sys_local"]
        assert [p["id"] for p in ais.resolve_global()["providers"]] == ["sys_remote", "sys_local"]
        # A user without an order of their own sees the new system order too.
        assert _runtime_ids("u1") == ["sys_remote", "sys_local", "my_remote"]

    def test_admin_move_that_keeps_system_relative_order_leaves_fallback_alone(self, store):
        _provider(store, "adm_remote", owner="user", user="adm", location="remote", agent="agt_3", order=0)
        saved = ais.save_user_ai_order("adm", ["sys_local", "adm_remote", "sys_remote", "sys_off"], link_system_order=True)
        assert saved["system_order_changed"] is False
        assert _system_order(store) == ["sys_local", "sys_remote", "sys_off"]

    def test_admin_display_always_shows_the_current_fallback_order(self, store):
        _provider(store, "adm_remote", owner="user", user="adm", location="remote", agent="agt_3", order=0)
        ais.save_user_ai_order("adm", ["adm_remote", "sys_local", "sys_remote", "sys_off"], link_system_order=True)
        # Another administrator (or the system settings save) reorders the system AI afterwards.
        store._execute("UPDATE ai_providers SET sort_order = 0 WHERE provider_id = 'sys_off'")
        store._execute("UPDATE ai_providers SET sort_order = 1 WHERE provider_id = 'sys_local'")
        store._execute("UPDATE ai_providers SET sort_order = 2 WHERE provider_id = 'sys_remote'")
        read = ais.get_user_ai_order("adm", link_system_order=True)["order"]
        assert read == ["adm_remote", "sys_off", "sys_local", "sys_remote"]  # my slots, fallback order inside

    def test_linked_save_is_one_transaction(self, store, monkeypatch):
        def fail(*_a):
            raise RuntimeError("system write failed")
        monkeypatch.setattr(ais._ai_db, "reorder_system", fail)
        with pytest.raises(RuntimeError):
            ais.save_user_ai_order("adm", ["sys_remote", "sys_local", "sys_off"], link_system_order=True)
        assert ais.get_user_ai_order("adm")["customized"] is False  # display order rolled back too
        assert _system_order(store) == ["sys_local", "sys_remote", "sys_off"]

    def test_route_links_only_for_administrators(self, store, monkeypatch):
        from modules.flow_gate.settings.routers import ai_settings as router_module

        def client(user):
            app = FastAPI()
            app.include_router(router_module.router, prefix="/api/v1")
            app.dependency_overrides[get_current_user] = lambda: user
            return TestClient(app)

        user = client({"user_id": "u1", "is_admin": False})
        monkeypatch.setattr("modules.flow_gate.rbac.decorators._permission_service.has_permission", lambda *_a: False)
        body = user.put("/api/v1/me/ai-order", json={"order": ["sys_remote", "sys_local"]}).json()
        assert body["system_order_changed"] is False
        assert _system_order(store) == ["sys_local", "sys_remote", "sys_off"]

        admin = client({"user_id": "adm", "is_admin": True})
        body = admin.put("/api/v1/me/ai-order", json={"order": ["sys_remote", "sys_local"]}).json()
        assert body["system_order_changed"] is True
        assert _system_order(store) == ["sys_remote", "sys_local", "sys_off"]
        assert admin.get("/api/v1/me/ai-order").json()["linked_to_fallback"] is True
        assert user.put("/api/v1/me/ai-order", json={"order": ["zzz"]}).status_code == 422
