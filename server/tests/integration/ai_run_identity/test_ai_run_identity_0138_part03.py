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

def test_concurrent_mkdir_one_owner(identity_db, tmp_path):
    db_path, _ = identity_db
    row = owner()
    identity.claim(row)
    root = tmp_path / "multi-scratch"
    root.mkdir()
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    outputs = [tmp_path / f"mkdir-{n}.txt" for n in range(2)]
    procs = [ctx.Process(target=_scratch_process, args=(db_path, str(root), row, str(out), barrier)) for out in outputs]
    try:
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(45)
            assert proc.exitcode == 0
        results = [p.read_text(encoding="utf-8") for p in outputs]
        assert results.count("created") == 1
        manifest = json.loads((root / "scratch" / "FlowGate" / row["run_id"] / ".flowgate-ai-scratch.json").read_text(encoding="utf-8"))
        assert manifest["execution_nonce"] == row["execution_nonce"]
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(10)
def test_pre_manifest_legacy_cleanup_requires_terminal_record(scratch_case, identity_db):
    from modules.flow_gate.services.ai_invoke import runtime
    row, current = scratch_case
    legacy_id = "aiv_20260827_000001"
    legacy = current.parent / legacy_id
    legacy.mkdir()
    (legacy / "payload").write_text("legacy", encoding="utf-8")
    old = datetime(2026, 8, 27, tzinfo=timezone.utc)
    os.utime(legacy, (old.timestamp(), old.timestamp()))
    runtime._cleanup_retained_scratches(row["project_id"])
    assert legacy.exists()  # no proven terminal owner yet
    legacy_row = record({**row, "run_id": legacy_id, "started_at": old.isoformat()})
    legacy_row.pop("execution_nonce")
    legacy_row["finished_at"] = (old + timedelta(minutes=1)).isoformat()
    records.upsert(legacy_row)
    runtime._cleanup_retained_scratches(row["project_id"])
    assert not legacy.exists()
    assert current.exists()
def test_admission_rollback_continues_after_token_revoke_failure(scratch_case, monkeypatch):
    from fastapi import HTTPException
    from modules.flow_gate.services import ai_invoke_service as svc
    from modules.flow_gate.services.ai_invoke import runtime
    row, scratch = scratch_case
    released = []
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda _: None)
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **k: released.append(a))
    monkeypatch.setattr(svc, "_runs", {})
    def revoke_failure(*a, **k):
        raise RuntimeError("token DB unavailable")
    monkeypatch.setattr(svc.token_service, "revoke", revoke_failure)
    def fail_after_scratch(**kwargs):
        runtime.admission_identity.set({**row, "token_id": "test-token", "scratch_dir": str(scratch)})
        raise RuntimeError("after scratch")
    monkeypatch.setattr(svc, "_admission_start_run", fail_after_scratch)
    with pytest.raises(HTTPException):
        svc.start_run(project_id=row["project_id"], group_id=row["group_id"])
    assert not scratch.exists()
    assert released == [(row["group_id"], row["run_id"])]
def test_restart_orphan_preserves_committed_execution_identity(identity_db, monkeypatch):
    from modules.flow_gate.services.ai_invoke import admission
    from modules.flow_gate.db import ai_invoke_document_review_loops
    monkeypatch.setattr(ai_invoke_document_review_loops, "stop_for_restart_orphan", lambda *a, **k: None)
    row = owner()
    identity.claim(row)
    lease = {"run_id": row["run_id"], "group_id": row["group_id"], "project_id": row["project_id"],
             "acquired_at": "2026-11-06T00:00:05+00:00", "token_id": None}
    admission._record_orphaned_lease_run(lease, "orphaned_by_restart")
    saved = records.get(row["run_id"])
    assert saved["execution_nonce"] == row["execution_nonce"]
    assert saved["started_at"] == row["started_at"]
    assert saved["end_reason"] == "orphaned_by_restart"
    with pytest.raises(identity.RunIdentityConflict):
        admission._record_orphaned_lease_run({**lease, "group_id": "another"}, "orphaned_by_restart")
    assert records.get(row["run_id"])["group_id"] == row["group_id"]
def test_cli_launch_rejects_another_execution_before_spawn(scratch_case):
    from modules.flow_gate.services.ai_invoke import provider_cli
    row, scratch = scratch_case
    run = {**row, "scratch_dir": str(scratch)}
    provider = {"kind": "custom"}
    assert provider_cli._resolve_cli_launch(provider, run, "worker")[1] == "valid"
    before = tree_hash(scratch)
    decision, reason = provider_cli._resolve_cli_launch(provider, {**run, "execution_nonce": str(uuid4())}, "worker")
    assert decision is None and reason == "scratch_owner_mismatch"
    assert tree_hash(scratch) == before
@pytest.mark.parametrize("start_version", [0, 1])
def test_first_allocation_activates_aiv2_namespace_without_fence_0139(identity_db, start_version):
    """No operator step and no stopped v0.1: version 0 (never cut over) or 1 (0138
    contract) activates on the first allocation, floored only by earlier aiv2_ IDs."""
    _, store = identity_db
    store._execute("DELETE FROM ai_run_id_counters")
    store._execute("UPDATE ai_run_id_contract SET version = ?", [start_version])
    # v0.1 keeps writing its own aiv_ rows into the same tables; they never floor v0.2.
    for serial in (3670, 3675):
        v01_row = record(owner(f"aiv_20261106_{serial:06d}"))
        v01_row.pop("execution_nonce")
        records.upsert(v01_row)
    store._execute("INSERT INTO ai_run_owners (run_id, execution_nonce, project_id, group_id, started_at) "
                   "VALUES (?, ?, ?, ?, ?)",
                   ["aiv2_20261106_000041", str(uuid4()), "flowgate", "flowgate.v02.0138", DAY.isoformat()])
    assert identity.allocate(at=DAY) == "aiv2_20261106_000042"
    assert _version(store) == identity.VERSION == 2
    assert identity.allocate(at=DAY) == "aiv2_20261106_000043"   # activation runs once
def test_v01_namespace_is_never_claimed_or_floored_0139(identity_db):
    """v0.1 floors its in-memory counter with ``LIKE 'aiv_<date>_%'``; aiv2_ IDs never
    match it, and v0.2's restart recovery treats aiv_ IDs as v0.1's."""
    _, store = identity_db
    issued = identity.allocate(at=DAY)
    row = owner(issued)
    identity.claim(row)
    records.upsert(record(row))
    v01_floor = store._fetch_all("SELECT run_id FROM ai_invoke_runs WHERE run_id LIKE ?", ["aiv_20261106_%"])
    assert issued.startswith("aiv2_") and v01_floor == []
    assert identity.RUN_ID.fullmatch("aiv_20261106_003675") and identity.RUN_ID.fullmatch(issued)
    assert identity.ISSUED_RUN_ID.fullmatch(issued)
    assert identity.foreign_run_id("aiv_20261106_003675") and not identity.foreign_run_id(issued)
def test_old_0138_binary_stays_refused_after_activation_0139(identity_db):
    """A 0138 binary checks ``version == 1``; activation moves the contract to 2 so it can
    never resume minting aiv_ IDs inside v0.1's namespace."""
    _, store = identity_db
    identity.allocate(at=DAY)
    assert _version(store) == 2 != 1
def test_runtime_allocators_issue_aiv2_0139(identity_db):
    from modules.flow_gate.services.ai_invoke import runtime
    _, store = identity_db
    store._execute("UPDATE ai_run_id_contract SET version = 0")
    run_id = runtime._next_run_id()
    assert identity.ISSUED_RUN_ID.fullmatch(run_id) and runtime._RUN_ID_RE.fullmatch(run_id)
    assert runtime._RUN_ID_RE.fullmatch("aiv_20261106_003675")  # legacy rows stay readable
