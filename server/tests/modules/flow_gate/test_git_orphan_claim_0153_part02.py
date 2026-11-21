"""Independently selectable scenarios; shared fixtures remain in test_git_orphan_claim_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_orphan_claim_0153 import (
    AT,
    B,
    CLAIM_LOG,
    EVIDENCE_ALONE,
    EVIDENCE_NAME,
    FUTURE,
    H,
    INSTANCES,
    INTENT,
    INTENT_CTX,
    LATER,
    M,
    OLD,
    ORPHAN_VERDICTS,
    OTHER_NODE,
    PAST,
    PROJECT,
    PUSH_RESULTS,
    Path,
    ProcessDeath,
    READ_FAILURES,
    RESUMABLE,
    RESUME_FACTS,
    SERVER,
    X,
    _age,
    _columns,
    _die_mid_reconcile,
    _disk_only_evidence_session,
    _facts,
    _fresh_claim_thread_state,
    _hours_ago,
    _only,
    _take_over,
    approval_intent,
    approved,
    assert_consistent,
    assert_lease_only,
    assert_untouched_by_guard,
    assert_writes_guarded,
    ast,
    branch_merge_publish,
    claim_row,
    cleanup,
    clock,
    common,
    conflict_session,
    count,
    db_claim,
    env,
    gc,
    get_store,
    git,
    git_rc,
    gs,
    inspect,
    legacy_incident_session,
    locks,
    logging,
    merge_aborts,
    merge_commit,
    names,
    og,
    on,
    open_session,
    origin_main,
    patch_store,
    plain_orphan_session,
    plant_claim,
    preserve,
    preserve_refs,
    project,
    push_rejected,
    push_timeout,
    pushes,
    pytest,
    pytestmark,
    re,
    ref_value,
    register_instances,
    registry,
    remote_commit,
    repo,
    saved,
    session_claim,
    subprocess,
    t4,
    tmp_db,
    to_conflict,
    unknown_session,
    unmerged,
)

@pytest.mark.parametrize('case', ['lease_expired', 'owner_dead', 'owner_alive'])
def test_U2_stale_reclaim(clock, case, caplog):
    mid = clock['mid']
    lease = registry.add_seconds(AT, -120) if case == 'lease_expired' else registry.add_seconds(AT, 600)
    plant_claim(mid, 'inst_A', claimed_at=registry.add_seconds(AT, -720), lease_until=lease)
    if case == 'owner_dead':
        gc.mark_instance_dead('inst_A')
    clock['instance'] = 'inst_B'
    caplog.set_level(logging.INFO, logger=CLAIM_LOG)
    claim = session_claim.acquire(mid, 'sweep')
    if case == 'owner_alive':
        assert claim is None
        assert claim_row(mid)['claim_token'] == 'planted-token'
        return
    assert claim is not None and claim.reclaimed_from == 'inst_A'
    row = claim_row(mid)
    assert row['owner_instance_id'] == 'inst_B' and row['claim_token'] == claim.token
    assert (row['last_owner_instance_id'], row['last_purpose'], row['last_outcome']) == (
        'inst_A', 'reconcile', 'reclaimed_from:inst_A')
    assert any('reclaimed' in r.getMessage() and 'inst_A' in r.getMessage() and 'planted-token' in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    session_claim.release(claim, 'hold')
    assert claim_row(mid)['last_outcome'] == 'hold/reclaimed_from:inst_A'
def test_U2_the_same_observed_token_wins_only_once(clock):
    mid = clock['mid']
    db_claim.ensure_row(mid)
    db_claim.ensure_row(mid)          # idempotent
    first = db_claim.take(mid, None, token='t1', owner='inst_A', purpose='sweep', claimed_at=AT, lease_until=LATER)
    second = db_claim.take(mid, None, token='t2', owner='inst_B', purpose='sweep', claimed_at=AT, lease_until=LATER)
    assert (first, second) == (1, 0) and claim_row(mid)['claim_token'] == 't1'
    third = db_claim.take(mid, 't1', token='t3', owner='inst_B', purpose='sweep', claimed_at=AT, lease_until=LATER)
    fourth = db_claim.take(mid, 't1', token='t4', owner='inst_C', purpose='sweep', claimed_at=AT, lease_until=LATER)
    assert (third, fourth) == (1, 0) and claim_row(mid)['claim_token'] == 't3'
    assert db_claim.release(mid, 't1', owner='inst_A', purpose='sweep', outcome='x', released_at=AT) == 0
def test_U2_no_instance_id_fails_closed(clock, monkeypatch):
    mid = clock['mid']
    clock['instance'] = None
    lazily = []
    monkeypatch.setattr(locks, '_register_lazily', lambda: lazily.append(1))
    assert session_claim.acquire(mid, 'sweep') is None
    assert lazily == [1]
    row = claim_row(mid)
    assert row is None or row['claim_token'] is None
def test_U2_a_lost_claim_refuses_every_session_write_and_audited_git_of_its_thread(clock):
    """Review rej_01M6ZWFPF1E6JE2M: the fence is not limited to the named call sites — every
    session-row write (context, close) and audited Git command of a session this thread
    processes under its claim is refused once the DB no longer carries its token."""
    mid = clock['mid']
    gs.db_git.set_session_context(mid, {'x': 1})               # no claim held: a plain write
    claim = session_claim.acquire(mid, 'reconcile')
    gs.db_git.set_session_context(mid, {'x': 2})               # valid claim: renewed, fenced, written
    assert saved(mid) == {'x': 2}
    get_store()._execute('UPDATE git_merge_session_claim SET claim_token = ? WHERE merge_id = ?', ['other', mid])
    with pytest.raises(session_claim.ClaimLost) as lost:
        gs.db_git.set_session_context(mid, {'x': 3})
    assert lost.value.merge_id == mid and lost.value.step == 'session_set_context' and claim.lost
    with pytest.raises(session_claim.ClaimLost):
        gs.db_git.close_session(mid, 'aborted')
    with pytest.raises(session_claim.ClaimLost) as git_lost:
        gs._run_session_git(mid, {}, 'update-ref', 'probe', ['--version'])
    assert git_lost.value.step == 'git_probe'
    assert saved(mid) == {'x': 2} and gs.db_git.get_session(mid)['status'] == 'open'
    assert issubclass(session_claim.ClaimLost, BaseException)
    assert not issubclass(session_claim.ClaimLost, Exception)    # no `except Exception` swallows it
    session_claim.release(claim)
    assert claim_row(mid)['claim_token'] == 'other'             # the release never clobbers it
    gs.db_git.set_session_context(mid, {'x': 4})               # no claim held any more
    assert saved(mid) == {'x': 4}
def test_U2_held_ends_a_lost_claim_quietly_and_only_for_its_own_merge(clock):
    mid = clock['mid']
    with session_claim.held(mid, 'sweep') as claim:
        get_store()._execute('UPDATE git_merge_session_claim SET claim_token = ? WHERE merge_id = ?',
                             ['other', mid])
        gs.db_git.set_session_context(mid, {'never': True})
    assert claim.outcome == 'claim_lost' and 'never' not in saved(mid)
    assert claim_row(mid)['claim_token'] == 'other' and session_claim.current(mid) is None
    with pytest.raises(session_claim.ClaimLost):
        with session_claim.held(mid, 'sweep') as busy:           # 'other' (inst_A, lease valid): busy
            assert busy is None
            raise session_claim.ClaimLost(mid + 1, 'elsewhere')
def test_U2_lease_parameter_is_clamped(monkeypatch):
    monkeypatch.delenv(session_claim.LEASE_ENV, raising=False)
    assert session_claim.merge_session_claim_lease_sec() == 600
    for raw, expected in (('50', 120), ('99999', 3600), ('900', 900), ('junk', 600)):
        monkeypatch.setenv(session_claim.LEASE_ENV, raw)
        assert session_claim.merge_session_claim_lease_sec() == expected
def test_U3_migration_181_applies_and_the_three_dialects_agree(tmp_db):
    mock_db, _ = tmp_db
    rows = mock_db._conn.execute('PRAGMA table_info(git_merge_session_claim)').fetchall()
    assert [r[1] for r in rows] == list(db_claim.COLUMNS)
    sqlite_cols = _columns('sqlite')
    assert list(sqlite_cols) == list(db_claim.COLUMNS)
    assert _columns('mysql') == sqlite_cols == _columns('postgres')
    assert [name for name, not_null in sqlite_cols.items() if not_null] == ['merge_id']
    for dialect in ('sqlite', 'mysql', 'postgres'):
        text = (SERVER / 'sql' / 'migrations' / dialect / '181_git_merge_session_claim.sql').read_text(encoding='utf-8')
        assert 'Rollback = DROP TABLE git_merge_session_claim' in text
        assert 'ON DELETE CASCADE' in text and 'REFERENCES git_merge_session(merge_id)' in text
        assert 'server_instance' not in text.split('CREATE TABLE')[1]   # no FK to instances (158 precedent)
        for line in text.splitlines():               # naive ';' splitters must not cut a comment
            if line.lstrip().startswith('--'):
                assert ';' not in line, (dialect, line)
