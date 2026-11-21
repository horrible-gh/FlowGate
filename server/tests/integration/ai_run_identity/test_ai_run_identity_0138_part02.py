"""Independently selectable scenarios; shared fixtures remain in test_ai_run_identity_0138.py."""
from __future__ import annotations

from tests.integration.ai_run_identity.test_ai_run_identity_0138 import (
    DAY,
    Path,
    SQLiteBackend,
    Transaction,
    _allocate_process,
    _commit_then_crash,
    _legacy_writer,
    _scratch_process,
    _version,
    connection,
    contextmanager,
    datetime,
    hashlib,
    identity,
    identity_db,
    json,
    multiprocessing,
    open_store,
    os,
    owner,
    pytest,
    record,
    records,
    scratch_case,
    sqlite3,
    threading,
    timedelta,
    timezone,
    tree_hash,
    uuid4,
)

def test_duplicate_mkdir_preserves_first_owner(scratch_case):
    from modules.flow_gate.services.ai_invoke import runtime
    row, scratch = scratch_case
    before = tree_hash(scratch)
    with pytest.raises(FileExistsError):
        runtime._create_scratch(row["project_id"], row["run_id"], row["execution_nonce"])
    assert tree_hash(scratch) == before
@pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
def test_record_collision_both_finish_orders(identity_db, order):
    a = owner()
    identity.claim(a)
    b = {**a, "execution_nonce": str(uuid4()), "doc_ref": "other-document", "token_id": "token-B"}
    for name in order:
        if name == "A":
            records.upsert(record(a))
        else:
            with pytest.raises(identity.RunIdentityConflict):
                records.upsert({**record(b), "doc_ref": "other-document", "token_id": "token-B"})
    persisted = records.get(a["run_id"])
    assert persisted["execution_nonce"] == a["execution_nonce"]
    assert persisted["doc_ref"] == "flowgate.v02.0138.0005-TR"
    assert persisted["token_id"] == "token-A"
    records.upsert({**record(a), "outcome": "partial"})
    assert records.get(a["run_id"])["outcome"] == "partial"
    for key, val in (("group_id", "other"), ("project_id", "other"), ("started_at", "later")):
        with pytest.raises(identity.RunIdentityConflict):
            records.upsert({**record(a), key: val})
def test_legacy_rows_readable_but_never_adopted(identity_db):
    legacy = owner()
    legacy.pop("execution_nonce")
    records.upsert(record(legacy))
    before = records.get(legacy["run_id"])
    with pytest.raises(identity.RunIdentityConflict):
        records.upsert({**record(legacy), "token_id": "other"})
    with pytest.raises(identity.RunIdentityConflict):
        identity.claim({**legacy, "execution_nonce": str(uuid4())})
    assert records.get(legacy["run_id"]) == before
def test_persist_failure_never_publishes_success(scratch_case, monkeypatch):
    from modules.flow_gate.services.ai_invoke import terminal
    from modules.flow_gate.services import ai_invoke_service as svc
    row, scratch = scratch_case
    events = []
    monkeypatch.setattr(svc, "_persist_run_record", lambda _: False)
    monkeypatch.setattr(svc, "_broadcast", lambda *a: events.append(a))
    terminal.publish(row)
    assert not events and not row.get("_record_persisted")
    assert scratch.exists()
def test_physical_path_reuse_rejects_delayed_a(scratch_case, identity_db):
    from modules.flow_gate.services.ai_invoke import runtime, finalize
    from modules.flow_gate.storage import filesystem
    a, path = scratch_case
    _, store = identity_db
    assert runtime._delete_owned_scratch(a["project_id"], a["run_id"], path, a["execution_nonce"])[0]
    # Fault injection only in the isolated DB: force reuse that the allocator forbids.
    store._execute("DELETE FROM ai_run_owners WHERE run_id = ?", [a["run_id"]])
    b = {**a, "execution_nonce": str(uuid4())}
    identity.claim(b)
    new_path = runtime._create_scratch(b["project_id"], b["run_id"], b["execution_nonce"])
    (new_path / "B").write_bytes(b"B-owned-data")
    before = tree_hash(new_path), filesystem.path_identity(new_path)
    finalize._finalize_scratch({**a, "scratch_dir": str(path), "outcome": "complete"})
    runtime._cleanup_retained_scratches(b["project_id"])
    assert (tree_hash(new_path), filesystem.path_identity(new_path)) == before
def test_mixed_old_binary_can_bypass_application_guard(identity_db):
    path, _ = identity_db
    a = owner()
    identity.claim(a)
    records.upsert(record(a))
    proc = multiprocessing.get_context("spawn").Process(target=_legacy_writer, args=(path, a["run_id"]))
    try:
        proc.start()
        proc.join(30)
        assert proc.exitcode == 0
        assert records.get(a["run_id"])["doc_ref"] == "legacy-overwrite"
        print("0138 mixed-version residual reproduced: raw legacy writer bypasses application guard; fence required")
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(10)
def test_runtime_allocator_has_no_local_fallback(identity_db, monkeypatch):
    from modules.flow_gate.services.ai_invoke import runtime, provider_cli
    from modules.flow_gate.services import ai_evaluation_service as evaluation
    ids = [runtime._next_run_id(), provider_cli._next_run_id(), evaluation.probe._next_run_id()]
    assert len(set(ids)) == 3
    def unavailable():
        raise OSError("isolated database unavailable")
    monkeypatch.setattr(identity, "get_store", unavailable)
    for allocate in (runtime._next_run_id, provider_cli._next_run_id, evaluation.probe._next_run_id):
        with pytest.raises(OSError):
            allocate()
def test_process_crash_after_commit_burns_id(identity_db, tmp_path):
    path, _ = identity_db
    output = tmp_path / "committed-id.txt"
    proc = multiprocessing.get_context("spawn").Process(target=_commit_then_crash, args=(path, str(output)))
    try:
        proc.start()
        proc.join(30)
        assert proc.exitcode == 73
        committed = output.read_text(encoding="utf-8")
        assert committed == "aiv2_20261106_000001"
        assert identity.allocate(at=DAY) == "aiv2_20261106_000002"
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(10)
