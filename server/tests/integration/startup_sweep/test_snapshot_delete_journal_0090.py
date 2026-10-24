"""flowgate.v02.0090 T#3 — snapshot deletes through the shared delete + journal (NR0003 §6.2).

Short adapter assertions on the T#1 state fixtures (token ``owned``) and the
existing snapshot fixture (``snapshot_env``); no new Git or file-lock experiment.

(a) A published snapshot is recorded at G/snapshot/<project-key>/<snapshot-id>.json
    (token parent, owner evidence) before the delete; the row turns deleted only
    after the target is gone, and the record is dropped last.
(b) A partial delete that already took README.md and source (or snapshot.json)
    is resumed by the resume-only checks, not blocked by _load_manifest; the
    existing retry count/backoff still gates a created row.
(c) A token whose snapshot record is pending keeps its row; once the parent
    token scratch took the snapshot, the resume verifies the absence, finishes
    the row and drops the record, and only then the row is purged.
(d) Review regressions: an absent recorded target never finishes a row that now
    names another token's live snapshot, and a record whose eligibility basis
    was lost or is unknown authorizes neither a delete nor a record drop.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest

from modules.flow_gate.services import snapshot_materialization_service as materialize
from modules.flow_gate.services import token_scratch
from modules.flow_gate.services.snapshot_request_service import SnapshotRequestError
from modules.flow_gate.storage import filesystem as storage_fs

from test_snapshot_materialization_0517 import snapshot_env  # noqa: F401
from test_token_scratch_delete_journal_0090 import PROJECT, _row, _scratch, owned  # noqa: F401

PAST = "2000-01-01T00:00:00+00:00"


def _publish(env, *, status="created"):
    """A published snapshot tree with its manifest, README.md and source payload."""
    row = env.row
    row.update(status=status, created_at="2026-09-20T00:02:00+00:00",
               expires_at="2026-09-21T00:02:00+00:00")
    final = Path(materialize.token_service.scratch_dir_path(row["project_id"], row["token_id"])) \
        / materialize.SNAPSHOT_NAMESPACE / row["snapshot_id"]
    (final / "source").mkdir(parents=True)
    (final / "source" / "one.txt").write_text("one", encoding="utf-8")
    (final / "README.md").write_text("readme", encoding="utf-8")
    manifest = {
        "schema": materialize.SNAPSHOT_SCHEMA, "owner": materialize.SNAPSHOT_OWNER,
        "snapshot_id": row["snapshot_id"], "project_id": row["project_id"],
        "group_id": row["group_id"], "run_id": row["run_id"], "token_id": row["token_id"],
        "source_kind": "current_worktree", "created_at": row["created_at"],
    }
    (final / "snapshot.json").write_text(json.dumps(manifest), encoding="utf-8")
    return final


def _record(env):
    return (env.storage / ".flowgate-gc" / "snapshot"
            / storage_fs.project_key(env.row["project_id"]) / f"{env.row['snapshot_id']}.json")


def _partial_then_held(monkeypatch, final, *names):
    """Remove *names* from the snapshot, then stop the delete like a held file."""
    real_rmtree = shutil.rmtree

    def partial(target, *args, **kwargs):
        if Path(target).resolve() == final.resolve():
            for name in names:
                path = final / name
                real_rmtree(path) if path.is_dir() else path.unlink()
            raise PermissionError(13, "simulated held file", str(final / "held"))
        return real_rmtree(target, *args, **kwargs)

    monkeypatch.setattr(storage_fs.shutil, "rmtree", partial)


def _restart(monkeypatch):
    monkeypatch.setattr(materialize, "_operations", {})
    monkeypatch.setattr(storage_fs, "_held", set())


def test_published_delete_is_recorded_first_and_finished_after_absence(snapshot_env, monkeypatch):
    final = _publish(snapshot_env)
    record = _record(snapshot_env)
    seen = {}
    real_remove, real_mark = storage_fs.remove_managed_tree, snapshot_env.state.mark_deleted

    def journaled_first(target, root, **kwargs):
        data = json.loads(record.read_text(encoding="utf-8"))
        seen["record"] = data
        seen["status_at_delete"] = snapshot_env.row["status"]
        return real_remove(target, root, **kwargs)

    def mark_after_absence(*args):
        seen["final_at_mark"] = final.exists()
        seen["record_at_mark"] = record.is_file()
        return real_mark(*args)

    monkeypatch.setattr(storage_fs, "remove_managed_tree", journaled_first)
    monkeypatch.setattr(materialize.db, "mark_deleted", mark_after_absence)
    result = materialize.cleanup("snap_test", "human", trigger="explicit")

    assert result["status"] == "deleted"
    data = seen["record"]
    assert (data["owner"], data["kind"], data["record_id"]) == ("snapshot", "delete", "snap_test")
    assert data["parent"] == {"owner": "token", "id": "token"}
    assert Path(data["managed_root"]) == final.parent.resolve()
    assert data["manifest"]["snapshot_id"] == "snap_test"
    assert data["eligibility"]["basis"] == "created_cleanup"
    assert "manifest_owner" in data["eligibility"]["checks"]
    assert seen["status_at_delete"] == "created"
    assert seen["final_at_mark"] is False and seen["record_at_mark"] is True
    assert not final.exists() and not record.exists()
    assert snapshot_env.state.events[-1]["event_type"] == "snapshot_deleted"


def test_partial_delete_resumes_without_payload_and_keeps_retry_policy(
        snapshot_env, monkeypatch, caplog):
    final = _publish(snapshot_env)
    record = _record(snapshot_env)
    with monkeypatch.context() as patch:
        _partial_then_held(patch, final, "README.md", "source")
        first = materialize.cleanup("snap_test", "human")
    assert first["status"] == "created" and first["cleanup_attempts"] == 1
    assert final.is_dir() and not (final / "README.md").exists() and not (final / "source").exists()
    assert record.is_file()
    with pytest.raises(SnapshotRequestError):  # the payload check alone would block forever
        materialize._load_manifest(final, snapshot_env.row)
    _restart(monkeypatch)

    # The existing retry count stops it on purpose; the startup pass leaves it alone.
    snapshot_env.row.update(cleanup_attempts=materialize.SNAPSHOT_CLEANUP_MAX_ATTEMPTS,
                            cleanup_next_at=PAST, expires_at=PAST)
    with caplog.at_level(logging.WARNING, logger=materialize.__name__):
        assert materialize.sweep_expired()["skipped"] == 1
    assert "stop=intentional code=retry_limit_reached" in caplog.text
    assert final.is_dir() and record.is_file() and snapshot_env.row["status"] == "created"

    # Lost owner evidence is a different, unintentional stop: nothing is deleted.
    caplog.clear()
    with monkeypatch.context() as patch, caplog.at_level(logging.WARNING, logger=materialize.__name__):
        patch.setattr(materialize.db, "get", lambda _sid: dict(snapshot_env.row, token_id="other"))
        assert materialize.cleanup("snap_test", "human")["cleanup_failed"] is True
    assert "stop=unintentional code=owner_evidence_lost" in caplog.text
    assert final.is_dir() and record.is_file()

    # Within the policy the TTL sweep resumes from the record alone.
    snapshot_env.row.update(cleanup_attempts=1, cleanup_next_at=PAST)
    summary = materialize.sweep_expired()
    assert summary["deleted"] == 1 and snapshot_env.row["status"] == "deleted"
    assert not final.exists() and not record.exists()
    metadata = json.loads(snapshot_env.state.events[-1]["metadata"])
    assert metadata["recovery"] == "delete_record_resumed"


def test_terminal_leftover_resumes_in_the_orphan_pass_without_manifest(snapshot_env, monkeypatch):
    final = _publish(snapshot_env, status="failed")
    record = _record(snapshot_env)
    with monkeypatch.context() as patch:
        _partial_then_held(patch, final, "snapshot.json", "README.md")
        result = materialize.cleanup("snap_test", "human")
    assert result["status"] == "failed" and "cleanup_warning" in result
    assert final.is_dir() and not (final / "snapshot.json").exists() and record.is_file()
    data = json.loads(record.read_text(encoding="utf-8"))
    assert data["eligibility"]["basis"] == "terminal_orphan" and data["manifest"] is not None
    _restart(monkeypatch)

    materialize.cleanup_orphans(startup=True)
    assert not final.exists() and not record.exists()
    assert snapshot_env.row["status"] == "failed"  # a terminal row's state is not changed


def test_pending_snapshot_record_holds_the_token_row_purge(owned, snapshot_env, monkeypatch):
    storage, rows, purged = owned
    token_id, token_path = _scratch(storage, 30)
    rows[token_id] = _row(token_id, token_path)
    snapshot_env.row.update(project_id=PROJECT, token_id=token_id)
    monkeypatch.setattr(materialize.token_service, "scratch_dir_path",
                        lambda _pid, _tid: str(token_path))
    final = _publish(snapshot_env)
    record = _record(snapshot_env)
    with monkeypatch.context() as patch:
        _partial_then_held(patch, final, "README.md")
        assert materialize.cleanup("snap_test", "human")["cleanup_failed"] is True
    assert json.loads(record.read_text(encoding="utf-8"))["parent"] == {"owner": "token", "id": token_id}

    # The parent token scratch goes first and takes the snapshot with it.
    assert token_scratch.sweep(PROJECT) == 1
    assert not token_path.exists() and record.is_file()
    assert token_scratch.purge_expired_rows() == 0 and token_id in rows
    _restart(monkeypatch)

    # The orphan pass verifies the real absence, finishes the row, drops the record.
    materialize.cleanup_orphans()
    assert snapshot_env.row["status"] == "deleted" and not record.exists()
    assert token_scratch.purge_expired_rows() == 1 and purged == [token_id]


def test_absent_recorded_target_does_not_finish_a_row_of_another_token(
        snapshot_env, monkeypatch, caplog, tmp_path):
    work = tmp_path / "work"
    monkeypatch.setattr(materialize.token_service, "scratch_dir_path",
                        lambda _pid, token_id: str(work / token_id))
    recorded = _publish(snapshot_env)
    record = _record(snapshot_env)
    with monkeypatch.context() as patch:
        _partial_then_held(patch, recorded, "README.md")
        assert materialize.cleanup("snap_test", "human")["cleanup_failed"] is True
    assert json.loads(record.read_text(encoding="utf-8"))["parent"] == {"owner": "token", "id": "token"}
    _restart(monkeypatch)

    # The recorded token scratch is gone; the row now names another token's live snapshot.
    shutil.rmtree(work / "token")
    snapshot_env.row.update(token_id="other")
    live = _publish(snapshot_env)
    with caplog.at_level(logging.WARNING, logger=materialize.__name__):
        assert materialize.cleanup("snap_test", "human")["status"] == "created"
        materialize.cleanup_orphans()
    assert caplog.text.count("stop=unintentional code=owner_evidence_lost") == 2
    assert snapshot_env.row["status"] == "created" and snapshot_env.row["cleanup_failed"] is True
    assert (live / "source" / "one.txt").is_file() and record.is_file()
    assert not any(event["event_type"] == "snapshot_deleted" for event in snapshot_env.state.events)


@pytest.mark.parametrize(("damage", "code"), [
    (lambda eligibility: {}, "eligibility_missing"),
    (lambda eligibility: dict(eligibility, basis="unknown_basis"), "basis_unknown"),
])
def test_terminal_resume_refuses_a_lost_or_unknown_basis(
        snapshot_env, monkeypatch, caplog, damage, code):
    final = _publish(snapshot_env, status="failed")
    record = _record(snapshot_env)
    with monkeypatch.context() as patch:
        _partial_then_held(patch, final, "snapshot.json")
        materialize.cleanup("snap_test", "human")
    data = json.loads(record.read_text(encoding="utf-8"))
    data["eligibility"] = damage(data["eligibility"])
    record.write_text(json.dumps(data), encoding="utf-8")
    _restart(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=materialize.__name__):
        materialize.cleanup_orphans(startup=True)
    assert f"stop=unintentional code={code}" in caplog.text
    assert (final / "README.md").is_file() and record.is_file()

    # Also once the target is gone: the damaged record is kept, not dropped.
    shutil.rmtree(final)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=materialize.__name__):
        materialize.cleanup_orphans(startup=True)
    assert f"stop=unintentional code={code}" in caplog.text
    assert record.is_file() and snapshot_env.row["status"] == "failed"
