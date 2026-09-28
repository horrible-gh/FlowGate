"""Evaluation schema, fingerprint, ordering and execution boundary regression tests."""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))


def _schema():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE projects(project_id TEXT PRIMARY KEY);
        CREATE TABLE users(user_id TEXT PRIMARY KEY);
        CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
        INSERT INTO projects VALUES ('p');
        INSERT INTO users VALUES ('u');
        INSERT INTO ai_invoke_runs VALUES ('old');
    """)
    sql = (SERVER / "sql/migrations/sqlite/124_ai_evaluations_and_groups.sql").read_text()
    db.executescript(sql)
    return db


def test_three_dialect_schema_contract():
    db = _schema()
    assert db.execute("SELECT requested_ai_group_id FROM ai_invoke_runs WHERE run_id='old'").fetchone() == (None,)
    for dialect in ("sqlite", "mysql", "postgres"):
        sql = (SERVER / f"sql/migrations/{dialect}/124_ai_evaluations_and_groups.sql").read_text()
        for required in ("ai_evaluation_runs", "ai_groups", "requested_ai_group_id",
                         "project_run_seq", "active_dedupe_key", "name_key_hash"):
            assert required in sql
        assert "UNIQUE" in sql.upper()
    mysql = (SERVER / "sql/migrations/mysql/124_ai_evaluations_and_groups.sql").read_text()
    assert "CHAR(64) CHARACTER SET ascii COLLATE ascii_bin" in mysql
    assert "BIGINT UNSIGNED" in mysql
    db.close()


def test_sqlite_sequence_dedupe_and_cascade_constraints():
    db = _schema()
    base = ["e1", "p", 1, "provider", "a" * 64, "b" * 64, "pending", "t", "t"]
    insert = """INSERT INTO ai_evaluation_runs
        (evaluation_id, project_id, project_run_seq, provider_id, subject_fingerprint,
         active_dedupe_key, lifecycle, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    db.execute(insert, base)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ["e2", "p", 2, "provider", "a" * 64, "b" * 64, "pending", "t", "t"])
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ["e2", "p", 1, "provider", "a" * 64, "c" * 64, "pending", "t", "t"])
    db.execute("UPDATE ai_evaluation_runs SET active_dedupe_key=NULL WHERE evaluation_id='e1'")
    db.execute(insert, ["e2", "p", 2, "provider", "a" * 64, "b" * 64, "pending", "t", "t"])
    assert db.execute("SELECT evaluation_id FROM ai_evaluation_runs ORDER BY project_run_seq DESC").fetchall() == [("e2",), ("e1",)]
    db.execute("DELETE FROM projects WHERE project_id='p'")
    assert db.execute("SELECT COUNT(*) FROM ai_evaluation_runs").fetchone() == (0,)
    db.close()


def test_fingerprint_stability_and_key_rotation(monkeypatch, tmp_path):
    from modules.flow_gate.services import ai_evaluation_fingerprint as fp
    monkeypatch.setattr(fp, "_root", lambda: tmp_path)
    monkeypatch.setattr(fp, "_history_exists", lambda: False)
    monkeypatch.setenv(fp.KEY_ENV, base64.b64encode(b"A" * 32).decode())
    provider = {"exec_type": "api", "kind": "openai", "api_base_url": "https://example.test/v1", "api_model": "m"}
    kwargs = dict(project_id="p", provider_id="a", scope="project", provider=provider, credential="secret")
    first = fp.subject_fingerprint(**kwargs)
    assert fp.subject_fingerprint(**kwargs) == first
    assert fp.subject_fingerprint(**{**kwargs, "credential": "changed"}) != first
    assert fp.subject_fingerprint(**{**kwargs, "provider": {**provider, "name": "renamed"}}) == first
    monkeypatch.setenv(fp.KEY_ENV, base64.b64encode(b"B" * 32).decode())
    with pytest.raises(fp.FingerprintKeyError):
        fp.subject_fingerprint(**kwargs)


def test_subject_is_immutable_and_diagnostic_is_redacted():
    from modules.flow_gate.services import ai_evaluation_service as service
    subject = service.EvaluationExecutionSubject("p", "a", "project", "name", "api", "openai",
        None, "https://example.test/v1", "m", "secret-value", "a" * 64, 1)
    with pytest.raises(FrozenInstanceError):
        subject.api_model = "changed"
    text = service._safe_summary("Authorization: Bearer xyz API key: abc secret-value " + "x" * 2500,
                                 "secret-value")
    assert len(text) == 2000
    assert "xyz" not in text and "abc" not in text and "secret-value" not in text


def test_current_prefers_newer_active_run_even_with_inverted_id(monkeypatch):
    from modules.flow_gate.services import ai_evaluation_service as service
    current = service.EvaluationExecutionSubject("p", "provider", "project", "name", "cli", "codex",
        "codex exec", None, None, None, "a" * 64, 1)
    older = dict(evaluation_id="z", project_id="p", provider_id="provider", project_run_seq=1,
                 subject_fingerprint="a" * 64, subject_fingerprint_version=1, lifecycle="completed",
                 verdict="suitable", provider_name_snapshot="name")
    newer = dict(older, evaluation_id="a", project_run_seq=2, lifecycle="pending", verdict=None)
    monkeypatch.setattr(service, "_current_subject", lambda *_: current)
    monkeypatch.setattr(service.repo, "latest", lambda _p, _id, *_: newer)
    result = service.current("p", "provider")
    assert result["current"]["evaluation_id"] == "a"
    assert result["current"]["lifecycle"] == "pending"


def test_safety_recheck_uses_identity_only(monkeypatch):
    from modules.flow_gate.services import ai_evaluation_service as service
    subject = service.EvaluationExecutionSubject("p", "a", "project", "name", "api", "openai",
        None, "https://old.example/v1", "old-model", "accepted-secret", "a" * 64, 1)
    monkeypatch.setattr(service, "_effective", lambda _: {"source": "project", "providers": [
        {"id": "a", "api_model": "new-model", "api_base_url": "https://new.example/v1"}]})
    assert service._safety_available(subject)
    assert subject.api_model == "old-model" and subject.credential == "accepted-secret"
    monkeypatch.setattr(service, "_effective", lambda _: {"source": "project", "providers": []})
    assert not service._safety_available(subject)


class _SqliteStore:
    """Small adapter exercising the real repository against the migration schema."""
    def __init__(self):
        self.db = _schema()
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()

    def _fetch_one(self, sql, params=None):
        with self.lock:
            row = self.db.execute(sql, params or []).fetchone()
            return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params or []).fetchall()]

    def _execute(self, sql, params=None):
        with self.lock:
            self.db.execute(sql, params or [])

    def _execute_affected(self, sql, params=None):
        with self.lock:
            cursor = self.db.execute(sql, params or [])
            self.db.commit()
            return cursor.rowcount


def test_repository_concurrent_creation_and_active_dedupe(monkeypatch):
    from modules.flow_gate.db import ai_evaluation_runs as repo
    store = _SqliteStore()
    monkeypatch.setattr(repo, "get_store", lambda: store)
    monkeypatch.setattr(repo, "now_iso", lambda: "same-timestamp")
    def create(fingerprint):
        try:
            return repo.create_pending(project_id="p", provider_id="provider", provider_name="name",
                fingerprint=fingerprint, version=1, created_by="u")
        except repo.AlreadyRunning as exc:
            return exc
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(create, "a" * 64), pool.submit(create, "a" * 64),
                   pool.submit(create, "c" * 64)]
        results = [future.result() for future in futures]
    rows = [result for result in results if isinstance(result, dict)]
    duplicates = [result for result in results if isinstance(result, repo.AlreadyRunning)]
    assert len(rows) == 2 and len(duplicates) == 1
    assert duplicates[0].evaluation_id in {row["evaluation_id"] for row in rows}
    assert {row["project_run_seq"] for row in rows} == {1, 2}
    assert repo.latest("p", "provider")["project_run_seq"] == 2
    store.db.close()


def test_repository_terminal_cancel_and_orphan_release(monkeypatch):
    from modules.flow_gate.db import ai_evaluation_runs as repo
    store = _SqliteStore()
    monkeypatch.setattr(repo, "get_store", lambda: store)
    row = repo.create_pending(project_id="p", provider_id="provider", provider_name="name",
        fingerprint="a" * 64, version=1, created_by="u")
    assert repo.mark_running("p", row["evaluation_id"])
    assert repo.finish("p", row["evaluation_id"], lifecycle="cancelled", reason_code="cancelled_by_user")
    assert not repo.finish("p", row["evaluation_id"], lifecycle="failed", reason_code="internal_error")
    assert repo.get("p", row["evaluation_id"])["active_dedupe_key"] is None
    second = repo.create_pending(project_id="p", provider_id="provider", provider_name="name",
        fingerprint="a" * 64, version=1, created_by="u")
    assert repo.recover_orphans() == 1
    assert repo.get("p", second["evaluation_id"])["lifecycle"] == "failed"
    assert repo.get("p", second["evaluation_id"])["active_dedupe_key"] is None
    store.db.close()
