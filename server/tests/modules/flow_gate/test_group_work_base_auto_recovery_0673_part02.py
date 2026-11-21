"""Independently selectable scenarios; shared fixtures remain in test_group_work_base_auto_recovery_0673.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_group_work_base_auto_recovery_0673 import (
    _forget_floor,
    _scope,
    _state,
    json,
    patch_store,
    proj,
    pytest,
    storage_dir,
)

def test_corrupt_ledger_remains_explicit_error_without_source_change(proj, monkeypatch):
    from modules.flow_gate.db import tr_commit_ledger
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(64, work_base="v0.2")
    _forget_floor(gid)
    before = (proj.wt_git(gid, "rev-parse", "HEAD"),
              proj.wt_git(gid, "status", "--porcelain=v1"))
    monkeypatch.setattr(tr_commit_ledger, "group_commit_evidence",
                        lambda _gid: (_ for _ in ()).throw(RuntimeError("ledger unavailable")))
    out = svc.collect_scope_changes(proj.pid, gid)
    assert out["available"] is False
    assert out["work_base_error"]["code"] == "group_work_base_unverified"
    assert out["work_base_error"]["details"]["reason"] == "ledger_unavailable"
    assert _state(gid)["work_base_state"] is None
    assert before == (proj.wt_git(gid, "rev-parse", "HEAD"),
                      proj.wt_git(gid, "status", "--porcelain=v1"))
def test_tr_review_and_final_recheck_continue_after_legacy_recovery(proj, monkeypatch):
    import io
    import zipfile
    from modules.flow_gate.services import review_package_service as rps
    from modules.flow_gate.services import tr_scope_service as trs

    tr_gid = proj.group(65, work_base="v0.2")
    proj.commit(tr_gid, "tr.txt", "tr\n")
    _forget_floor(tr_gid)
    monkeypatch.setattr(trs, "resolve_stage", lambda _pid: trs.STAGE_ENFORCE)
    verdict = trs.evaluate(proj.pid, tr_gid, "## 변경 파일\n\n- tr.txt\n")
    assert verdict["verdict"] == trs.VERDICT_PASS
    assert verdict["detected"] == ["tr.txt"]

    review_gid = proj.group(66, work_base="v0.2")
    proj.commit(review_gid, "review.txt", "review\n")
    _forget_floor(review_gid)
    monkeypatch.setattr(rps.tr_scope_service, "group_declared_paths",
                        lambda group_id, exclude_doc_id=None: [])
    package = rps.build_review_package(proj.pid, review_gid)
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
    assert metadata["changed_files"] == ["review.txt"]

    final_gid = proj.group(67, work_base="v0.2")
    proj.commit(final_gid, "final.txt", "final\n")
    _forget_floor(final_gid)
    recheck = trs.evaluate_group_unreported(proj.pid, final_gid)
    assert recheck["checked"] is True
    assert {row["path"] for row in recheck["unreported"]} == {"final.txt"}
