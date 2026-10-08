"""FAP request-key retries after conflict review hand-off (0676)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection, git_concurrency, operation_job  # noqa: E402
from modules.flow_gate.services.git import job_store  # noqa: E402


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_fap_handoff_retry_0676.db")


@pytest.fixture
def store(db_path, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper

    s = object.__new__(connection.FlowGateStore)
    s._db = SQLiteWrapper(db_path)
    s._sq = None
    for module in (connection, git_concurrency, operation_job, job_store):
        monkeypatch.setattr(module, "get_store", lambda s=s: s)
    now = connection.now_iso()
    with s.transaction():
        s._execute("DELETE FROM operation_job")
        s._execute("DELETE FROM server_instance")
        s._execute(
            "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
            "VALUES ('p_0676', 'P 0676', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
            [now, now],
        )
    instance_id = "inst_" + "a" * 32
    git_concurrency.insert_instance(instance_id, "node-0676", 1, "marker-0676", now)
    monkeypatch.setattr(job_store, "_instance_id", lambda: instance_id)
    return s


def _request(doc_id="p_0676.default.0676.0002-AC"):
    return {
        "doc_id": doc_id,
        "doc_revision_no": 0,
        "git_action": "merge",
        "target_branch": "main",
        "commit_title": "approve",
        "group_id": "p_0676.default.0676",
    }


def _create(req):
    result = job_store.create_or_get_job(
        "final_approval_publish", "p_0676", req, group_id=req["group_id"], wake=False,
    )
    assert result.ok, result
    return result


def _finish(store, result, status, value):
    store._execute(
        "UPDATE operation_job SET status = ?, phase = 'done', result = ?, "
        "lease_owner = NULL, lease_token = NULL, lease_until = NULL WHERE job_id = ?",
        [status, value, result.job["job_id"]],
    )


def test_two_conflict_handoffs_create_fresh_jobs_and_advance_sequence(store):
    req = _request()
    first = _create(req)
    assert first.outcome == job_store.CREATED
    assert first.job["request_key"].endswith(":r0:0")
    assert _create(req).outcome == job_store.EXISTING

    _finish(store, first, "succeeded", "handed_off_to_conflict_review")
    second = _create(req)
    assert second.outcome == job_store.CREATED
    assert second.job["job_id"] != first.job["job_id"]
    assert second.job["request_key"].endswith(":r0:1")

    _finish(store, second, "succeeded", "handed_off_to_conflict_review")
    third = _create(req)
    assert third.outcome == job_store.CREATED
    assert third.job["job_id"] not in (first.job["job_id"], second.job["job_id"])
    assert third.job["request_key"].endswith(":r0:2")


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_or_cancelled_job_still_advances_sequence(store, status):
    req = _request()
    first = _create(req)
    _finish(store, first, status, status)
    retry = _create(req)
    assert retry.outcome == job_store.CREATED
    assert retry.job["request_key"].endswith(":r0:1")


def test_completed_publish_is_reused_without_second_job(store):
    req = _request()
    first = _create(req)
    _finish(store, first, "succeeded", "published")
    again = _create(req)
    assert again.outcome == job_store.EXISTING
    assert again.job["job_id"] == first.job["job_id"]
    assert store._fetch_one(
        "SELECT COUNT(*) AS n FROM operation_job WHERE project_id = 'p_0676'"
    )["n"] == 1


def test_doc_prefix_escapes_sql_like_characters(store):
    req = _request("p_0676.default.0676.0002%-AC")
    other = _create(_request("p_0676.default.0676.00020-AC"))
    _finish(store, other, "failed", "failed")
    first = _create(req)
    assert first.job["request_key"].endswith(":r0:0")
