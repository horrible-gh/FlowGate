"""flowgate.default.0624 T0004: connected FK regression for snapshot audit actors.

`test_snapshot_materialization_0517.py` mocks `workflow_events.create` with a plain
list append (see its `snapshot_env` fixture), so a snapshot actor that is a free-text
label instead of a real `users.user_id` never touches the real `workflow_events`
table and its `actor_user_id ... REFERENCES users(user_id)` FK. That is exactly how
`snapshot-recovery` / `snapshot-ttl-cleanup` / `snapshot-run-cleanup` /
`snapshot-group-cleanup` / `group-close:<id>` / `ai-run:<run-id>` reached production
unnoticed (flowgate.default.0624 NR0003).

These tests run the real `modules.flow_gate.db.workflow_events.create()` and the real
`modules.flow_gate.db.snapshot_requests` functions against a fully migrated sqlite
database with `PRAGMA foreign_keys = ON`, a real `projects`/`groups` row pair, and two
real `users` rows — plus the reserved system user migration 038 already seeds
(`u-system`). Every assertion below would fail with a `sqlite3.IntegrityError` FK
violation if a bare label ever reached `actor_user_id` again.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from modules.flow_gate.services import snapshot_materialization_service as materialize
from modules.flow_gate.services import snapshot_access_service

PROJECT = "proj0624"
GROUP = f"{PROJECT}.default.0001"
OWNER = "usr_owner_0624"
READER = "usr_reader_0624"

SEED_SQL = f"""
INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at)
VALUES ('{PROJECT}', 'Test Project 0624', 1, '2026-09-26T00:00:00+00:00', '2026-09-26T00:00:00+00:00');

INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
VALUES ('{GROUP}', '{PROJECT}', 'default', 'Test Group 0624', 'OPEN',
        '2026-09-26T00:00:00+00:00', '2026-09-26T00:00:00+00:00');

INSERT INTO users(user_id, username, email, password, is_active, is_admin,
                  first_login_required, created_at, updated_at)
VALUES
    ('{OWNER}', 'owner0624', 'owner0624@test.local', 'hashed', 1, 0, 0,
     '2026-09-26T00:00:00+00:00', '2026-09-26T00:00:00+00:00'),
    ('{READER}', 'reader0624', 'reader0624@test.local', 'hashed', 1, 0, 0,
     '2026-09-26T00:00:00+00:00', '2026-09-26T00:00:00+00:00');
"""


class _NullTxn:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ConnectedStore:
    """Thin `get_store()` shim over a real sqlite3 connection with FK enforcement on."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def transaction(self):
        return _NullTxn()

    def _execute(self, sql, params=()):
        self.conn.execute(sql, params)
        self.conn.commit()

    def _execute_affected(self, sql, params=()):
        cursor = self.conn.execute(sql, params)
        self.conn.commit()
        return cursor.rowcount

    def _fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=()):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]


@pytest.fixture
def connected_env(tmp_path, monkeypatch, migrated_sqlite_db):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").mkdir()
    (worktree / "one.txt").write_text("one", encoding="utf-8")

    scratch = tmp_path / "token-scratch"
    scratch.mkdir()

    db_path = migrated_sqlite_db("snapshot_actor_fk_0624.sqlite", seed_sql=SEED_SQL)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    store = _ConnectedStore(conn)

    # Real `workflow_events.create()` and real `snapshot_requests` db functions run
    # unmocked; only their `get_store()` is redirected to this migrated, FK-enforcing
    # database. Group/worktree/token-scratch resolution stays mocked, exactly like
    # `test_snapshot_materialization_0517.py`'s `snapshot_env` fixture, since none of
    # that is what this regression is about.
    monkeypatch.setattr(materialize, "get_store", lambda: store)
    monkeypatch.setattr(materialize.db, "get_store", lambda: store)
    monkeypatch.setattr(materialize.workflow_events, "get_store", lambda: store)
    monkeypatch.setattr(
        materialize.db_groups, "get_by_id",
        lambda group_id: {"group_id": group_id, "project_id": PROJECT},
    )
    monkeypatch.setattr(
        materialize.git_service, "effective_src_root_ex",
        lambda project_id, group_id: (worktree, "registered"),
    )
    monkeypatch.setattr(materialize.git_service, "_worktree_link_ok", lambda root: True)
    monkeypatch.setattr(
        materialize.token_service, "scratch_dir_path",
        lambda project_id, token_id: str(scratch),
    )

    def fake_git(args, cwd):
        if args[:2] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(materialize.git_service, "_run_git", fake_git)

    def make_request(snapshot_id, *, run_id="run0624", token_id="tok0624", chain_id=None):
        return materialize.db.create({
            "snapshot_id": snapshot_id, "project_id": PROJECT, "group_id": GROUP,
            "run_id": run_id, "chain_id": chain_id, "token_id": token_id,
            "provider_id": "provider", "reason": "connected FK regression",
            "scope": "single_file", "requested_paths": ["one.txt"],
            "purpose": "0624 actor FK regression", "source_kind": "current_worktree",
        })

    def approve(snapshot_id, actor=OWNER):
        materialize.db.transition(snapshot_id, "approved", actor)

    def latest_event():
        rows = store._fetch_all(
            "SELECT * FROM workflow_events ORDER BY id DESC LIMIT 1", [],
        )
        return rows[0]

    env = SimpleNamespace(
        worktree=worktree, scratch=scratch, conn=conn, store=store,
        make_request=make_request, approve=approve, latest_event=latest_event,
        final=lambda sid: scratch / materialize.SNAPSHOT_NAMESPACE / sid,
    )
    yield env
    conn.close()


def test_a_startup_recovery_uses_real_fk_and_tags_source(connected_env):
    """Reproduces the exact NR log: startup recovery of a published-but-uncommitted
    snapshot must not write `actor_user_id='snapshot-recovery'`."""
    snapshot_id = "snap_recovery_0624"
    connected_env.make_request(snapshot_id)
    connected_env.approve(snapshot_id)
    created = materialize.materialize(snapshot_id, OWNER)
    assert created["status"] == "created"

    # Simulate a crash between publishing the tree and committing the DB update: the
    # directory is real, but the request row reverted to "approved".
    connected_env.store._execute(
        "UPDATE snapshot_requests SET status='approved' WHERE snapshot_id=?", [snapshot_id],
    )

    materialize.cleanup_orphans(startup=True)
    row = materialize.db.get(snapshot_id)
    assert row["status"] == "created"

    event = connected_env.latest_event()
    assert event["event_type"] == "snapshot_created"
    assert event["actor_user_id"] == materialize.SYSTEM_ACTOR_USER_ID
    metadata = json.loads(event["metadata"])
    assert metadata["source"] == "snapshot_recovery"
    assert metadata["recovery"] == "published_before_db_update"


def test_b_ttl_cleanup_uses_system_actor_and_tags_trigger(connected_env):
    snapshot_id = "snap_ttl_0624"
    connected_env.make_request(snapshot_id)
    connected_env.approve(snapshot_id)
    materialize.materialize(snapshot_id, OWNER)

    past = (materialize._utcnow() - materialize.timedelta(hours=1)).isoformat()
    connected_env.store._execute(
        "UPDATE snapshot_requests SET expires_at=? WHERE snapshot_id=?", [past, snapshot_id],
    )

    summary = materialize.sweep_expired()
    assert summary["deleted"] == 1
    row = materialize.db.get(snapshot_id)
    assert row["status"] == "deleted"

    event = connected_env.latest_event()
    assert event["event_type"] == "snapshot_deleted"
    assert event["actor_user_id"] == materialize.SYSTEM_ACTOR_USER_ID
    metadata = json.loads(event["metadata"])
    assert metadata["trigger"] == "ttl_expired"
    assert metadata["source"] == "snapshot_ttl_cleanup"


def test_c_run_cleanup_prefers_real_actor_and_falls_back_to_system(connected_env):
    snapshot_id = "snap_run_0624"
    connected_env.make_request(snapshot_id, run_id="run_c")
    connected_env.approve(snapshot_id)
    materialize.materialize(snapshot_id, OWNER)

    result = materialize.cleanup_for_run("run_c", actor=READER)
    assert result["deleted"] == 1

    event = connected_env.latest_event()
    assert event["event_type"] == "snapshot_deleted"
    assert event["actor_user_id"] == READER
    metadata = json.loads(event["metadata"])
    assert metadata["trigger"] == "run_finished"
    assert metadata["source"] == "snapshot_run_cleanup"

    # A run with no resolvable real user (terminal.py's fallback path) must still
    # satisfy the FK via the reserved system user, never the bare run id/label.
    snapshot_id2 = "snap_run_0624_default"
    connected_env.make_request(snapshot_id2, run_id="run_c_default")
    connected_env.approve(snapshot_id2)
    materialize.materialize(snapshot_id2, OWNER)
    result2 = materialize.cleanup_for_run("run_c_default")
    assert result2["deleted"] == 1
    event2 = connected_env.latest_event()
    assert event2["actor_user_id"] == materialize.SYSTEM_ACTOR_USER_ID


def test_d_group_close_uses_real_actor_and_group_finished_trigger(connected_env):
    snapshot_id = "snap_group_0624"
    connected_env.make_request(snapshot_id, run_id="run_d")
    connected_env.approve(snapshot_id)
    materialize.materialize(snapshot_id, OWNER)

    result = materialize.cleanup_for_group(GROUP, actor=OWNER)
    assert result["deleted"] == 1

    event = connected_env.latest_event()
    assert event["event_type"] == "snapshot_deleted"
    assert event["actor_user_id"] == OWNER
    metadata = json.loads(event["metadata"])
    assert metadata["trigger"] == "group_finished"
    assert metadata["source"] == "snapshot_group_cleanup"


def test_e_ai_run_freshness_uses_issued_to_and_falls_back_to_system(connected_env):
    snapshot_id = "snap_access_0624"
    connected_env.make_request(snapshot_id, run_id="run_e")
    connected_env.approve(snapshot_id)
    materialize.materialize(snapshot_id, OWNER)

    # Disguise the in-scope file so refresh_stale must actually transition to stale
    # (and therefore actually write a workflow_events row) rather than short-circuit.
    (connected_env.worktree / "one.txt").write_text("changed", encoding="utf-8")

    run = {
        "project_id": PROJECT, "group_id": GROUP, "run_id": "run_e",
        "token_id": "tok0624", "issued_to": READER,
    }
    meta = snapshot_access_service.status_metadata(run, snapshot_id)
    assert meta["status"] == "stale"

    event = connected_env.latest_event()
    assert event["event_type"] == "snapshot_stale"
    assert event["actor_user_id"] == READER
    metadata = json.loads(event["metadata"])
    assert metadata["source"] == "ai_run"

    # A run with no resolvable `issued_to` still satisfies the FK via the system user.
    connected_env.store._execute(
        "UPDATE snapshot_requests SET stale=0, stale_detected_at=NULL WHERE snapshot_id=?",
        [snapshot_id],
    )
    (connected_env.worktree / "one.txt").write_text("changed again", encoding="utf-8")
    anon_run = {
        "project_id": PROJECT, "group_id": GROUP, "run_id": "run_e", "token_id": "tok0624",
    }
    meta2 = snapshot_access_service.status_metadata(anon_run, snapshot_id)
    assert meta2["status"] == "stale"
    event2 = connected_env.latest_event()
    assert event2["actor_user_id"] == materialize.SYSTEM_ACTOR_USER_ID


def test_f_audit_direct_call_resolves_actor_both_ways(connected_env):
    """`snapshot_access_service._audit()` is the one place that still wrote
    `actor_user_id` inline instead of going through materialize's `_record_event`."""
    snapshot_id = "snap_audit_0624"
    connected_env.make_request(snapshot_id, run_id="run_f")
    connected_env.approve(snapshot_id)
    row = materialize.materialize(snapshot_id, OWNER)

    run_with_user = {
        "project_id": PROJECT, "group_id": GROUP, "run_id": "run_f",
        "token_id": "tok0624", "issued_to": READER,
    }
    snapshot_access_service._audit("snapshot_execution_reported", row, run_with_user, task_kind="build")
    event = connected_env.latest_event()
    assert event["actor_user_id"] == READER

    anon_run = {"project_id": PROJECT, "group_id": GROUP, "run_id": "run_f", "token_id": "tok0624"}
    snapshot_access_service._audit("snapshot_execution_reported", row, anon_run, task_kind="build")
    event2 = connected_env.latest_event()
    assert event2["actor_user_id"] == materialize.SYSTEM_ACTOR_USER_ID
