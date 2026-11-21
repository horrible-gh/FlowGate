"""Independently selectable scenarios; shared fixtures remain in test_git_approval_recovery_extended_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_approval_recovery_extended_0153 import (
    ThreadPoolExecutor,
    approval_intent,
    approve,
    bindings,
    bmp,
    copy,
    env,
    fa,
    fail_push,
    git,
    git_rc,
    gs,
    json,
    names,
    parents_of,
    patch_store,
    pending,
    project,
    pytest,
    ready,
    real,
    recovery,
    ref_value,
    remote_commit,
    repo,
    request,
    saved,
    session_claim,
    subprocess,
    terminal,
    threading,
    tmp_db,
    uuid,
)

def test_P1_uuid_is_bound_across_sessions_and_payloads(real):
    mid, fp = ready(real)
    key = str(uuid.uuid4())
    a = {'group_id': real.group, 'merge_id': mid, 'review_fingerprint': fp}
    b = {**a, 'merge_id': mid + 1}
    assert bindings.bind('approval', key, a)
    assert bindings.bind('approval', key, a)
    assert not bindings.bind('approval', key, b)
    assert bindings.matches('approval', key, b) is False
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        approve(real, mid, key, 'other candidate')
    assert exc.value.code == 'approval_attempt_reused' and not real.commands
def test_R1_explicit_retry_grants_one_bounded_budget_and_keeps_total_history(real):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    before = saved(mid)
    _, body = request(real, mid)
    for _ in range(4):
        gs.reconcile_push_session(mid, trigger='server_startup')
    exhausted = saved(mid)
    assert exhausted['reconcile_cycle_count'] == 5
    assert exhausted['reconciliation_kind'] == gs.RECONCILE_KIND_RETRY_EXHAUSTED
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, body=body)
    assert exc.value.code == gs.RECONCILE_KIND_RETRY_EXHAUSTED
    assert not real.commands and saved(mid)['reconcile_cycle_count'] == 5
    request(real, mid)
    after = saved(mid)
    assert after['reconcile_cycle_count'] == 1
    assert after['reconcile_attempt_count'] == exhausted['reconcile_attempt_count'] + 1
    assert len(after['recovery_retry_history']) == 2
    assert after['approval_attempt_id'] == attempt and after['merge_commit'] == before['merge_commit']
    pending(real, mid)
def test_binding_migration_is_idempotent_and_identical_portable_sql():
    import sqlite3
    from pathlib import Path
    root = Path(__file__).resolve().parents[3] / 'sql' / 'migrations'
    scripts = [(root / engine / '182_git_merge_request_binding.sql').read_text(encoding='utf-8')
               for engine in ('sqlite', 'mysql', 'postgres')]
    assert len(set(scripts)) == 1
    with sqlite3.connect(':memory:') as conn:
        for script in scripts:
            conn.executescript(script)
        conn.execute('INSERT INTO git_merge_request_binding VALUES (?, ?, ?, ?, ?, ?)',
                     ['approval', 'id', 'digest', 1, 'group', 'now'])
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO git_merge_request_binding VALUES (?, ?, ?, ?, ?, ?)',
                         ['approval', 'id', 'changed', 2, 'other', 'now'])
