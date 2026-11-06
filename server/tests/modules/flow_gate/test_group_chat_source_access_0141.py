"""T-2 narrow self-check: settings/capability A->B->A and B-only edit_once consumption.

No application DB, real token scratch, network or worker is used. SQLite is in memory.
Run directly with Python/unittest, or select this file in the later formal test stage.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from contextlib import ExitStack, contextmanager
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
SERVER = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVER))

from modules.flow_gate.db import documents, user_chat_source_access as access
from modules.flow_gate.services import chat_settings_service as settings, token_service
from modules.flow_gate.services import graph_execution_hooks
from modules.flow_gate.api.v1 import chat_settings_routes as routes

PROJECT, USER = "t2-synthetic", "t2-user"
A, B = PROJECT + ".v02.A", PROJECT + ".v02.B"


class MemoryStore:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE users (user_id TEXT PRIMARY KEY);
            CREATE TABLE groups (group_id TEXT PRIMARY KEY);
            CREATE TABLE tokens (token_id TEXT PRIMARY KEY, source_access TEXT);
        """)
        migration = SERVER / "sql/migrations/sqlite"
        self.conn.executescript((migration / "108_user_chat_source_access.sql").read_text(encoding="utf-8"))
        self.conn.executescript((migration / "177_group_chat_source_access.sql").read_text(encoding="utf-8"))
        self.conn.execute("INSERT INTO users VALUES (?)", [USER])
        self.conn.executemany("INSERT INTO groups VALUES (?)", [(A,), (B,)])
        self.conn.commit()

    def _execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def _execute_affected(self, sql, params):
        return self.conn.execute(sql, params).rowcount

    def _fetch_one(self, sql, params):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    @contextmanager
    def transaction(self):
        with self.conn:
            yield self


class GroupSourceAccessT2(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.store = MemoryStore()
        self.addCleanup(self.store.conn.close)
        def replace(obj, name, value):
            return self.stack.enter_context(patch.object(obj, name, value))
        replace(access, "get_store", lambda: self.store)
        replace(settings, "get_store", lambda: self.store)
        replace(token_service, "get_store", lambda: self.store)
        empty = SimpleNamespace(get=lambda user: None)
        replace(settings, "chat_settings_store", empty)
        replace(settings, "command_policy_store", empty)
        adapter = routes.legacy_adapter
        replace(adapter, "has_permission", lambda user, perm, project: project == PROJECT)
        replace(adapter, "get_project", lambda project: {"project_id": PROJECT} if project == PROJECT else None)
        replace(adapter, "get_group", lambda group: {"project_id": PROJECT, "group_id": group} if group in (A, B) else None)
        replace(documents, "get_by_id", lambda doc: {"type_code": "CH", "project_id": PROJECT, "group_id": doc.rsplit(".", 1)[0]})
        replace(token_service, "_active_pepper", lambda: ("test", "synthetic-pepper"))
        ids = count(1)
        replace(token_service, "_next_token_id", lambda: f"t2-token-{next(ids)}")
        replace(token_service, "_scratch_dir", lambda *args: Path(os.environ.get("FLOWGATE_T2_SCRATCH", ".")) / args[1])
        replace(token_service, "to_storage_relative", lambda *args: "synthetic/scratch")
        for name in ("create", "sweep_on_issue"):
            replace(token_service.token_scratch, name, lambda *args, **kwargs: None)
        replace(token_service.token_scratch, "delete_owned", lambda *args, **kwargs: True)
        replace(token_service, "_record_self_check_anchor", lambda *args: None)
        replace(graph_execution_hooks, "call", lambda *args: None)
        replace(token_service.db_events, "create", lambda *args: None)
        self.tokens = {}
        def create_token(data):
            self.tokens[data["token_id"]] = dict(data)
            self.store._execute("INSERT INTO tokens VALUES (?, ?)", [data["token_id"], data["source_access"]])
        def set_capability(token, mode):
            self.tokens[token]["source_access"] = mode
            self.store._execute("UPDATE tokens SET source_access=? WHERE token_id=?", [mode, token])
        replace(token_service.db_tokens, "create", create_token)
        replace(token_service.db_tokens, "set_source_access", set_capability)
        replace(token_service.db_tokens, "get_by_id", lambda token: self.tokens.get(token))

    def save(self, group, mode):
        return routes.patch_my_chat_settings(routes.ChatSettingsPatch(source_access_mode=mode),
            user={"user_id": USER}, project_id=PROJECT, group_id=group)

    def get(self, group):
        return routes.get_my_chat_settings(user={"user_id": USER}, project_id=PROJECT, group_id=group)

    def issue(self, group):
        result = token_service.issue(project=PROJECT, group_id=group, action_scope="chat",
            doc_ref=group + ".0001-CH", issued_to=USER)
        self.assertEqual(self.tokens[result["token_id"]]["source_access"], result["source_access"])
        return result

    def test_a_b_a_settings_match_token_source_access(self):
        self.save(A, "read_only")
        self.save(B, "edit")
        observed = []
        for group, mode, capability in ((A, "read_only", "read"), (B, "edit", "read_write"), (A, "read_only", "read")):
            response = self.get(group)
            self.assertEqual(response["source_access_scope"], {"project_id": PROJECT, "group_id": group})
            self.assertEqual(response["settings"]["source_access_mode"], mode)
            result = self.issue(group)
            self.assertEqual(result["source_access"], capability)
            observed.append((group, mode, capability))
        print("A->B->A settings/capability:", observed)

    def test_b_edit_once_consumption_leaves_a_row_unchanged(self):
        self.save(A, "read_only")
        before = access.get(USER, PROJECT, A)
        self.save(B, "edit_once")
        result = self.issue(B)
        self.assertTrue(result["one_shot_claimed"])
        self.assertEqual(result["source_access"], "read_write")
        self.assertEqual(access.get(USER, PROJECT, A), before)
        self.assertEqual(access.commit(USER, PROJECT, B, result["token_id"], "consumed"), "COMMITTED")
        row = access.get(USER, PROJECT, B)
        self.assertEqual((row["source_access_mode"], row["one_shot_token_id"], row["one_shot_claimed_at"]), ("read_only", None, None))
        self.assertEqual(access.get(USER, PROJECT, A), before)
        self.assertEqual(self.get(A)["settings"]["source_access_mode"], "read_only")
        print("B edit_once claim/commit: read_only + empty marker; complete A row unchanged")


if __name__ == "__main__":
    unittest.main(verbosity=2)
