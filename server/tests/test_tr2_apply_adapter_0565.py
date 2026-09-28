import json

import pytest

from modules.flow_gate.documents import tr2_apply_adapter as apply
from modules.flow_gate.documents import tr2_precheck as precheck
from modules.flow_gate.documents import tr2_service as tr2


def _edit(ident, path, old, new):
    return {"id": ident, "kind": "edit", "file": path,
            "anchor_old": old, "replacement_new": new,
            "rationale": "test edit", "confidence": "high"}


def _create(ident, path, content):
    return {"id": ident, "kind": "create_file", "file": path, "content": content,
            "rationale": "test create", "confidence": "high"}


def _spec(*edits):
    return {"termination": "ready_to_apply", "edits": list(edits),
            "deferred": [], "gate": {"apply": False, "commands": []}}


def test_backup_apply_restore_preserves_bytes_and_created_cleanup(tmp_path):
    root, backup = tmp_path / "src", tmp_path / "backups"
    root.mkdir()
    old = b"\xef\xbb\xbfalpha\r\nbeta\r\n"
    (root / "a.txt").write_bytes(old)
    spec = _spec(_edit("e", "a.txt", "alpha", "gamma"),
                 _create("c", "new.txt", "created\n"))
    baseline = tr2.target_fingerprint(spec, root)
    assert apply.adapter.evaluate(spec, root, baseline=baseline)["ready"]
    bundle = apply.adapter.create_backup(spec, root, backup, baseline=baseline)
    result = apply.adapter.apply_all(spec, root, backup, bundle)
    assert result["written"] == ["a.txt", "new.txt"]
    assert (root / "a.txt").read_bytes() == b"\xef\xbb\xbfgamma\r\nbeta\r\n"
    assert (root / "new.txt").read_bytes() == b"created\n"
    assert apply.Tr2ApplyAdapter().recover(backup, bundle, source_root=root)["ok"]
    assert (root / "a.txt").read_bytes() == old
    assert not (root / "new.txt").exists()
    assert apply.Tr2ApplyAdapter().recover(backup, bundle, source_root=root)["ok"]
    assert tr2.target_fingerprint(spec, root) == baseline


@pytest.mark.parametrize("content,status", [("other", "anchor_missing"),
                                              ("old old", "anchor_ambiguous")])
def test_anchor_mismatch_fails_closed(tmp_path, content, status):
    (tmp_path / "a.txt").write_text(content)
    spec = _spec(_edit("e", "a.txt", "old", "new"))
    result = apply.adapter.evaluate(spec, tmp_path)
    assert result["ready"] is False
    assert result["edits"][0]["status"] == status
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_edit_not_applicable"):
        apply.adapter.create_backup(spec, tmp_path, tmp_path.parent / "backup-0565")
    assert (tmp_path / "a.txt").read_text() == content


def test_create_collision_and_fingerprint_drift(tmp_path):
    spec = _spec(_create("c", "new.txt", "new"))
    baseline = tr2.target_fingerprint(spec, tmp_path)
    (tmp_path / "new.txt").write_text("foreign")
    result = apply.adapter.evaluate(spec, tmp_path, baseline=baseline)
    assert result["drift"] and result["code"] == "tr2_source_drift"
    assert result["edits"][0]["status"] == "file_exists"
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_source_drift"):
        apply.adapter.create_backup(spec, tmp_path, tmp_path.parent / "backup-0565",
                                    baseline=baseline)
    assert (tmp_path / "new.txt").read_text() == "foreign"


def test_same_file_order_is_composed_and_broken_anchor_rejected(tmp_path):
    (tmp_path / "a.txt").write_text("first second")
    good = _spec(_edit("1", "a.txt", "first", "third"),
                 _edit("2", "a.txt", "second", "fourth"))
    assert apply.adapter.evaluate(good, tmp_path)["ready"]
    bad = _spec(_edit("1", "a.txt", "first", "third"),
                _edit("2", "a.txt", "first", "fourth"))
    assert apply.adapter.evaluate(bad, tmp_path)["edits"][1]["status"] == "anchor_missing"


def test_path_escape_and_parent_symlink_fail_closed(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        apply.adapter.evaluate(_spec(_create("c", "../outside/x", "x")), root)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        apply.adapter.evaluate(_spec(_create("c", ".git/config", "x")), root)
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_path_unsafe"):
        apply.adapter.evaluate(_spec(_create("c", "link/x", "x")), root)
    assert not (outside / "x").exists()


def test_partial_write_failure_restores_modified_and_created(tmp_path, monkeypatch):
    root, backup = tmp_path / "src", tmp_path / "backups"
    root.mkdir()
    (root / "a.txt").write_bytes(b"old")
    (root / "b.txt").write_bytes(b"old")
    spec = _spec(_edit("a", "a.txt", "old", "new"),
                 _create("c", "new.txt", "created"),
                 _edit("b", "b.txt", "old", "new"))
    bundle = apply.adapter.create_backup(spec, root, backup)
    original = apply._atomic_bytes
    failed = False

    def fail_second(path, data):
        nonlocal failed
        if path == root / "b.txt" and not failed:
            failed = True
            raise OSError("injected write failure")
        return original(path, data)

    monkeypatch.setattr(apply, "_atomic_bytes", fail_second)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_apply_failed"):
        apply.adapter.apply_all(spec, root, backup, bundle)
    assert (root / "a.txt").read_bytes() == b"old"
    assert (root / "b.txt").read_bytes() == b"old"
    assert not (root / "new.txt").exists()
    assert json.loads((backup / bundle / "manifest.json").read_text())["phase"] == "restored"


def test_restore_failure_keeps_rollback_phase_for_restart(tmp_path, monkeypatch):
    root, backup = tmp_path / "src", tmp_path / "backups"
    root.mkdir()
    (root / "a.txt").write_text("old")
    spec = _spec(_edit("e", "a.txt", "old", "new"))
    bundle = apply.adapter.create_backup(spec, root, backup)
    apply.adapter.apply_all(spec, root, backup, bundle)
    snapshot = backup / bundle / "files" / "a.txt"
    snapshot.write_text("tampered")
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_recovery_required"):
        apply.adapter.recover(backup, bundle, source_root=root)
    assert json.loads((backup / bundle / "manifest.json").read_text())["phase"] == "rollback"
    assert (root / "a.txt").read_text() == "new"


def test_restart_before_first_write_checks_source_without_overwriting(tmp_path):
    root, backup = tmp_path / "src", tmp_path / "backups"
    root.mkdir()
    (root / "a.txt").write_text("old")
    spec = _spec(_edit("e", "a.txt", "old", "new"))
    bundle = apply.adapter.create_backup(spec, root, backup)
    (root / "a.txt").write_text("foreign")
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_recovery_required"):
        apply.Tr2ApplyAdapter().recover(backup, bundle, source_root=root)
    assert (root / "a.txt").read_text() == "foreign"
    (root / "a.txt").write_text("old")
    assert apply.Tr2ApplyAdapter().recover(backup, bundle, source_root=root)["recovered_no_write"]


def test_restore_after_validation_changes_created_file(tmp_path):
    root, backup = tmp_path / "src", tmp_path / "backups"
    root.mkdir()
    spec = _spec(_create("c", "new.txt", "created"))
    bundle = apply.adapter.create_backup(spec, root, backup)
    apply.adapter.apply_all(spec, root, backup, bundle)
    (root / "new.txt").write_text("validation changed this attempt's file")
    assert apply.Tr2ApplyAdapter().recover(backup, bundle, source_root=root)["ok"]
    assert not (root / "new.txt").exists()


def test_write_time_anchor_drift_rolls_back_every_target(tmp_path, monkeypatch):
    root, backup = tmp_path / "src", tmp_path / "backups"
    root.mkdir()
    (root / "a.txt").write_text("old")
    (root / "b.txt").write_text("old")
    spec = _spec(_edit("a", "a.txt", "old", "new"),
                 _edit("b", "b.txt", "old", "new"))
    bundle = apply.adapter.create_backup(spec, root, backup)
    original = apply._safe_target
    seen_b = 0

    def drift_before_second_write(source_root, rel, kind):
        nonlocal seen_b
        target = original(source_root, rel, kind)
        if rel == "b.txt":
            seen_b += 1
            if seen_b == 2:
                target.write_text("drift")
        return target

    monkeypatch.setattr(apply, "_safe_target", drift_before_second_write)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_apply_failed"):
        apply.adapter.apply_all(spec, root, backup, bundle)
    assert (root / "a.txt").read_text() == "old"
    assert (root / "b.txt").read_text() == "old"


def test_duplicate_request_key_never_reuses_lock_holder(tmp_path, monkeypatch):
    holders = []

    def acquire(_project_id, holder):
        holders.append(holder)
        return True

    monkeypatch.setattr(precheck.git_service, "_acquire_lock", acquire)
    monkeypatch.setattr(precheck.db_git, "release_lock", lambda *_: None)
    monkeypatch.setattr(precheck, "_approval_root", lambda *_: tmp_path)
    with precheck.source_lock("p", "g", request_key="same"):
        pass
    with precheck.source_lock("p", "g", request_key="same"):
        pass
    assert len(holders) == 2 and holders[0] != holders[1]


def test_dirty_worktree_and_lock_denial(tmp_path, monkeypatch):
    monkeypatch.setattr(precheck.git_service, "probe_worktree_pending_changes", lambda _: True)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_worktree_dirty"):
        precheck._clean(tmp_path)
    monkeypatch.setattr(precheck.git_service, "_acquire_lock", lambda *args: False)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_source_locked"):
        with precheck.source_lock("p", "g"):
            pass


def test_revision_and_spec_identity_inside_authoritative_precheck(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("old")
    spec = _spec(_edit("e", "a.txt", "old", "new"))
    body = {"tr2_version": 1, "source_t2_doc_id": "flowgate.default.0565.0001-T2",
            "edit_spec": spec, "baseline_fingerprint": tr2.target_fingerprint(spec, tmp_path)}
    doc = {"doc_id": "flowgate.default.0565.0002-TR2", "type_code": "TR2",
           "project_id": "p", "group_id": "g", "doc_review_status": "pending_review",
           "revision_no": 2}
    monkeypatch.setattr(precheck.db_docs, "get_by_id", lambda _: doc)
    monkeypatch.setattr(precheck.db_wfseq, "get_pending_head_by_group",
                        lambda *_: {"type": "TR2", "result_doc_id": doc["doc_id"]})
    monkeypatch.setattr(tr2, "canonical_path_for_doc", lambda _: tmp_path / "document.json")
    monkeypatch.setattr(tr2, "load_body", lambda _: body)
    monkeypatch.setattr(tr2, "verify_pair", lambda *_: None)
    monkeypatch.setattr(precheck, "_clean", lambda _: None)
    monkeypatch.setattr(precheck.db_git, "get_lock", lambda _: {"holder": "holder"})
    monkeypatch.setattr(precheck, "_approval_root", lambda *_: tmp_path)
    locked = precheck.LockedSource("p", "g", tmp_path, "holder")
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_changed"):
        precheck.authoritative_precheck(doc["doc_id"], locked, expected_revision=1)
    with pytest.raises(tr2.Tr2ValidationError, match="tr2_spec_changed"):
        precheck.authoritative_precheck(doc["doc_id"], locked, expected_revision=2,
                                        expected_spec_fingerprint="wrong")
    assert precheck.authoritative_precheck(doc["doc_id"], locked, expected_revision=2,
                                           expected_spec_fingerprint=tr2.spec_fingerprint(spec)
                                           )["evaluation"]["ready"]
    applied = precheck.apply_locked(
        doc["doc_id"], locked, tmp_path.parent / "backup-connected-0565",
        expected_revision=2, expected_spec_fingerprint=tr2.spec_fingerprint(spec))
    assert (tmp_path / "a.txt").read_text() == "new"
    assert precheck.restore_locked(
        locked, applied["spec"], applied["evaluation"]["baseline_fingerprint"],
        tmp_path.parent / "backup-connected-0565", applied["backup_bundle_id"])["ok"]
    assert (tmp_path / "a.txt").read_text() == "old"
