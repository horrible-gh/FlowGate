"""Independently selectable scenarios; shared fixtures remain in test_group_work_base_scope_0665.py."""
from __future__ import annotations

from tests.regression.group_git.test_group_work_base_scope_0665 import (
    Path,
    Proj,
    V02_ONLY,
    _COUNTER,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _fail_audit_log,
    _forget_floor,
    _git,
    _ledger_commit,
    _scope,
    _seed_wf_done_root,
    _set_marker,
    _state,
    base64,
    contextmanager,
    inspect,
    itertools,
    json,
    os,
    patch_store,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    tempfile,
    threading,
)

def test_SB2_SB3_update_history_is_unverified_and_a_manual_same_subject_merge_cannot_be_confirmed(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(15, work_base="v0.2")
    fork = _state(gid)["work_base_sha"]
    proj.commit(gid, "own.txt", "o\n")
    # A feature branch off the fork (NOT in v0.2), merged with the FlowGate subject.
    proj.wt_git(gid, "branch", "feature", fork)
    proj.wt_git(gid, "checkout", "-q", "feature")
    proj.commit(gid, "feature.txt", "f\n")
    proj.wt_git(gid, "checkout", "-q", gid.replace(".", "_"))
    proj.wt_git(gid, "-c", "user.name=FlowGate", "-c", "user.email=flowgate@localhost",
                "merge", "--no-ff", "feature", "-m", f"Merge base 'v0.2' into '{gid.replace('.', '_')}'")
    feature_sha = proj.wt_git(gid, "rev-parse", "feature")
    _forget_floor(gid)

    report = svc.backfill_group_work_base(proj.pid, apply=True)
    row = next(r for r in report["groups"] if r["group_id"] == gid)
    assert row["verdict"] == "unverified" and row["reason"] == "update_history_unproven"
    assert row["candidates"][0]["second_parent"] == feature_sha
    result = svc.collect_scope_changes(proj.pid, gid)
    assert result["reason"] == "group_work_base_unverified"
    assert result["work_base_error"]["details"]["candidates"]
    # Nothing to absorb (HEAD contains v0.2): no_change without touching the record.
    noop = svc.update_from_base(gid)["result"]
    assert noop["status"] == "no_change" and noop["floor_unusable"] == "group_work_base_unverified"
    assert _state(gid)["work_base_state"] == "unverified"
    proj.origin_commit("v0.2", "later.txt", "l\n")
    with pytest.raises(GitServiceError) as upd:
        svc.update_from_base(gid)                      # something to absorb: refused before confirmation
    assert upd.value.code == "group_work_base_unverified"

    # SB-3: the feature tip is not v0.2 history -> rule 7 refuses it.
    with pytest.raises(GitServiceError) as bad:
        svc.confirm_group_work_base(proj.pid, gid, work_base_sha=fork,
                                    work_base_sync_sha=feature_sha, actor="admin")
    assert bad.value.status == 422 and bad.value.details["reason"] == "outside_work_base_history"
    assert _state(gid)["work_base_state"] == "unverified"

    # SB-6: floor = fork is always allowed (over-inclusive, never drops work).
    ok = svc.confirm_group_work_base(proj.pid, gid, work_base_sha=fork,
                                     work_base_sync_sha=None, actor="admin", basis="checked")
    assert ok["work_base_state"] == "confirmed"
    assert _scope(proj.pid, gid) == {"own.txt", "feature.txt"}
    from modules.flow_gate.db import git_integration as db_git
    assert db_git.list_work_base_log(gid)[-1]["kind"] == "manual_confirm"
def test_SB5_confirm_rejects_a_floor_containing_group_work(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(16, work_base="v0.2")
    c1 = proj.commit(gid, "tr.txt", "t\n")
    _ledger_commit(gid, proj.pid, 2, c1)
    before = dict(_state(gid))
    with pytest.raises(GitServiceError) as exc:
        svc.confirm_group_work_base(proj.pid, gid, work_base_sha=c1,
                                    work_base_sync_sha=None, actor="admin")
    assert exc.value.status == 422
    assert exc.value.details["reason"] in ("outside_work_base_history", "floor_contains_group_work")
    after = _state(gid)
    assert after["work_base_sha"] == before["work_base_sha"]
    assert after["work_base_state"] == before["work_base_state"]
def test_legacy_group_auto_recovers_during_final_approval_recheck(proj):
    from modules.flow_gate.services import tr_scope_service as trs
    gid = proj.group(17, work_base="v0.2")
    _forget_floor(gid)
    out = trs.evaluate_group_unreported(proj.pid, gid)
    assert out["checked"] is True
    assert _state(gid)["work_base_state"] == "verified"
def test_E_clean_update_audit_failure_keeps_the_previous_floor(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git_service import GitServiceError
    gid = proj.group(30, work_base="v0.2")
    proj.commit(gid, "mine.txt", "m\n")
    fork = _state(gid)["work_base_sha"]
    tip = proj.origin_commit("v0.2", "u1.txt", "1\n")
    head = proj.wt_git(gid, "rev-parse", "HEAD")

    with monkeypatch.context() as m:
        _fail_audit_log(m)
        with pytest.raises(GitServiceError) as exc:
            svc.update_from_base(gid)
    assert exc.value.code == "group_work_base_record_failed"
    state = _state(gid)
    assert state["work_base_sync_sha"] is None and state["work_base_sha"] == fork
    assert proj.wt_git(gid, "rev-parse", "HEAD") == head              # merge rolled back
    assert _scope(proj.pid, gid) == {"mine.txt"}
    assert [row["kind"] for row in db_git.list_work_base_log(gid)] == ["fork"]

    # The kept floor is still usable: a retry absorbs the same source.
    assert svc.update_from_base(gid)["result"]["source_sha"] == tip
    assert _state(gid)["work_base_sync_sha"] == tip

    # A recorded sync value is restored too, not only a NULL one.
    tip2 = proj.origin_commit("v0.2", "u2.txt", "2\n")
    head = proj.wt_git(gid, "rev-parse", "HEAD")
    with monkeypatch.context() as m:
        _fail_audit_log(m)
        with pytest.raises(GitServiceError):
            svc.update_from_base(gid)
    assert _state(gid)["work_base_sync_sha"] == tip
    assert proj.wt_git(gid, "rev-parse", "HEAD") == head
    assert svc.update_from_base(gid)["result"]["source_sha"] == tip2
    assert _state(gid)["work_base_sync_sha"] == tip2
    assert _scope(proj.pid, gid) == {"mine.txt"}
