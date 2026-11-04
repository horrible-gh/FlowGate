"""0127 T#3: window contracts, recovery, JOIN lineage and failure atomicity."""
import json
from dataclasses import replace
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from test_card_document_edit_0127 import env, D, F, HEADER, RuleCall, business_state
from modules.flow_gate.db import documents, document_revisions
from modules.flow_gate.db.connection import in_transaction
from modules.flow_gate.graph_workflow.services import card_document_ports as ports, flow_guard
from modules.flow_gate.documents.routers import work_plan as wp_route, tr2 as tr2_route
from modules.flow_gate.documents import tr2_service as tr2, body_recovery
from modules.flow_gate.storage import paths, files
from flowgate.card_document_edit import (integration, dedicated_bridge, dedicated_windows as windows,
    recovery_bridge, deployment_bridge, store, boundary, contacts)
from flowgate.card_document_edit.contracts import Actor, Built, DedicatedRequest, Origin


@pytest.fixture(autouse=True)
def registered(monkeypatch):
    monkeypatch.setattr(ports, "_handlers", dict(ports._handlers))
    integration.register()


def request():
    return Request({"type": "http", "method": "PUT", "path": "/", "headers": []})


def as_wp(env, monkeypatch):
    from modules.flow_gate.services import work_plan_service as wp
    documents.update(D, {"type_code": "WP"})
    plan = wp.initial_body(["N"], [], "flowgate", quantities={"N": 0})
    env.path.write_bytes(wp.dumps(plan).encode())
    monkeypatch.setattr(windows, "editable", lambda doc: None)
    monkeypatch.setattr(wp_route, "_load_doc", lambda doc_id: documents.get_by_id(doc_id))
    monkeypatch.setattr(wp_route, "_plan_path", lambda doc: env.path)
    monkeypatch.setattr(wp_route, "_providers", lambda p: [])
    return wp_route.WorkPlanSave(base_revision_no=0, body=plan)


def test_wp_preserves_fields_stale_409_and_one_notification(env, monkeypatch):
    submitted = as_wp(env, monkeypatch)
    result = wp_route.save_work_plan(request(), D, submitted, {"user_id": "user"})
    assert result["revision_no"] == 1
    assert set(result) == {"ok", "doc_id", "revision_no", "updated_at", "updated_by", "doc_review_status", "title",
                           "body", "unassigned_step_count", "assignment_summary", "totals", "warnings"}
    before = business_state(env)
    stale = wp_route.save_work_plan(request(), D, submitted, {"user_id": "user"})
    assert stale.status_code == 409 and json.loads(stale.body)["code"] == "wp_revision_conflict"
    assert business_state(env) == before
    assert [name for name, _ in env.events].count("graph") == 1
    assert [e.event_type for name, e in env.events if name == "document"].count(contacts.EventType.GROUP_VIEW_REFRESH) == 1


def test_missing_dedicated_ports_return_existing_500_shapes_without_any_write(env, monkeypatch):
    ports.unregister(ports.DEDICATED_DOCUMENT_WRITE)
    before = business_state(env)
    with pytest.raises(HTTPException) as error:
        wp_route.save_work_plan(request(), D, SimpleNamespace(), {"user_id": "user"})
    assert error.value.status_code == 500
    response = tr2_route._run(D, lambda: tr2.save(D, {}, actor="user", expected_revision=0))
    assert response.status_code == 500
    assert json.loads(response.body)["code"] == "tr2_internal_error"
    assert business_state(env) == before


def test_wp_validation_precedes_revision_and_capability_acks(env, monkeypatch):
    submitted = as_wp(env, monkeypatch)
    submitted.base_revision_no = 999
    submitted.body = {"bad": "shape"}
    response = windows.wp_save(request(), D, submitted, {"user_id": "user"})
    assert response.status_code == 422
    assert documents.get_by_id(D)["revision_no"] == 0


def tr2_env(env, monkeypatch):
    documents.update(D, {"type_code": "TR2"})
    monkeypatch.setattr(tr2, "_assert_mutable", lambda doc: None)
    monkeypatch.setattr(tr2, "validate", lambda body, **kw: body)
    monkeypatch.setattr(tr2, "verify_pair", lambda *a: None)
    monkeypatch.setattr(tr2, "canonicalize", lambda body: dict(body))
    monkeypatch.setattr(tr2, "target_set", lambda *a: [])
    monkeypatch.setattr(tr2, "resolve_source_root", lambda *a: env.root)
    monkeypatch.setattr(tr2, "target_fingerprint", lambda *a: "sha256:" + "b" * 64)
    monkeypatch.setattr(tr2, "derived_files", lambda *a: [])
    monkeypatch.setattr(tr2, "spec_fingerprint", lambda *a: "spec")
    monkeypatch.setattr(tr2, "canonical_path_for_doc", lambda doc: env.path)
    monkeypatch.setattr(tr2, "dumps", json.dumps)
    monkeypatch.setattr(tr2, "parse", json.loads)
    return {"edit_spec": {}, "tr2_version": 1, "source_t2_doc_id": "t2"}


def test_tr2_expected_revision_and_build_current_are_protected(env, monkeypatch):
    raw = tr2_env(env, monkeypatch)
    result = tr2.save(D, raw, actor="user", expected_revision=0)
    assert result["new_revision"] == 1 and result["operation"] == "save"
    calls = []
    def build(current):
        assert in_transaction() and flow_guard.held_guard() == ("flow", F)
        calls.append(current)
        return current
    with pytest.raises(tr2.Tr2ValidationError) as exc:
        tr2._save(D, build, actor="user", expected_revision=0)
    assert exc.value.code == "tr2_spec_changed" and not calls
    result = tr2._save(D, build, actor="user", expected_revision=1)
    assert result["new_revision"] == 2 and calls[0]["baseline_fingerprint"] == "sha256:" + "b" * 64
    assert sorted(r["revision_no"] for r in document_revisions.list_by_doc(D)) == [1, 2]


@pytest.mark.parametrize("rollback", [False, True])
def test_tr2_create_contacts_follow_outer_commit(env, monkeypatch, rollback):
    raw = tr2_env(env, monkeypatch)
    env.path.unlink()
    with pytest.raises(RuntimeError) if rollback else __import__('contextlib').nullcontext():
        with env.db.transaction():
            result = tr2.create(D, raw, actor="user")
            assert result["new_revision"] == 1 and not env.events
            if rollback:
                raise RuntimeError("outer failure")
    assert bool(env.events) != rollback
    assert env.path.exists() != rollback
    assert documents.get_by_id(D)["revision_no"] == (0 if rollback else 1)


def test_join_failure_rolls_back_all_revisions_and_files(env):
    before = business_state(env)
    req = DedicatedRequest(D, 0, Actor("user"), RuleCall(env.path, "PREVIOUS"), Origin("wp_deploy"))
    pending = None
    def fail():
        nonlocal pending
        _, pending = dedicated_bridge.dedicated_join(req)
        assert env.path.read_bytes() == b"new bytes"
        raise RuntimeError("mark failed")
    with pytest.raises(RuntimeError, match="mark failed"):
        flow_guard.with_flow_guard(F, fail)
    assert pending.state == "DISCARDED"
    assert business_state(env)[:4] == before[:4]
    assert not env.events and not list(env.path.parent.glob("*.keep-*"))


def test_card_recovery_advances_revision_and_does_not_invent_empty_backup(env, monkeypatch):
    env.path.unlink()
    monkeypatch.setattr(body_recovery, "recover_write_checks", lambda doc: None)
    monkeypatch.setattr(body_recovery, "diagnose_doc", lambda *a: {"method": body_recovery.REGENERATE_STUB,
        "body_lost": "true", "candidate": None})
    monkeypatch.setattr(body_recovery, "recorded_or_standard", lambda doc: env.path)
    monkeypatch.setattr(body_recovery, "stub_bytes", lambda doc: b"recovered")
    result = body_recovery.recover_one(D, body_recovery.REGENERATE_STUB, "user")
    assert result["data"]["revision_no"] == 1 and result["content"] == "recovered"
    assert not document_revisions.list_by_doc(D)
    metadata = json.loads(env.db._fetch_one("SELECT metadata FROM workflow_events WHERE event_type='doc_edited'")["metadata"])
    assert metadata["before_image"] == "absent" and metadata["origin"] == "recovery"
    assert [n for n, _ in env.events] == ["document", "canvas", "graph"]
    with pytest.raises(body_recovery.RecoveryRefused) as exc:
        body_recovery.recover_one(D, body_recovery.REGENERATE_STUB, "user")
    assert exc.value.error == "file_exists"


def test_recovery_raw_sidecar_preserves_r0_and_failure_rolls_back(env):
    class Rule(RuleCall):
        def build(self, fresh, body):
            return Built(b"first", snapshot_policy="RECOVERY_BEFORE")
    req = DedicatedRequest(D, 0, Actor("user"), Rule(env.path), Origin("recovery"))
    dedicated_bridge.dedicated_save(req)
    original = document_revisions.list_by_doc(D)[0]
    backup = paths.resolve_storage_path(original["backup_path"], "flowgate")
    raw = backup.read_bytes()
    documents.update(D, {"revision_no": 0})
    env.path.write_bytes(b"broken but readable")
    dedicated_bridge.dedicated_save(req)
    assert backup.read_bytes() == raw
    assert len(document_revisions.list_by_doc(D)) == 1
    sidecar = next(env.path.parent.glob("revisions/*unreadable-before*"))
    assert sidecar.read_bytes() == b"broken but readable"


def test_deployment_lineage_requires_every_revision_and_exact_owner(env):
    mark = {"wp_doc_id": "wp", "instruction_revision_no": 1}
    class Rule(RuleCall):
        def build(self, fresh, body):
            return Built(b"deploy", event_metadata={"node_key": "research", "slot": "instruction",
                "wp_doc_id": "wp", "body_saved_source": "work_plan_deploy"})
    req = DedicatedRequest(D, 0, Actor("user"), Rule(env.path), Origin("wp_deploy"), event_reason="wp_deploy_fill")
    dedicated_bridge.dedicated_save(req)
    row = {"node_key": "research", "slot": "instruction"}
    assert deployment_bridge.deployment_only(documents.get_by_id(D), mark, F, row)
    assert not deployment_bridge.deployment_only(documents.get_by_id(D), mark, F, {**row, "slot": "result"})
    env.db._execute("DELETE FROM workflow_events WHERE event_type='doc_edited'")
    assert not deployment_bridge.deployment_only(documents.get_by_id(D), mark, F, row)


def test_recovery_and_editor_serialize_and_stale_editor_preserves_recovered_body(env, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from flowgate.card_document_edit import revise
    from test_card_document_edit_0127 import HEADER
    entered, release = threading.Event(), threading.Event()
    env.path.unlink()
    monkeypatch.setattr(body_recovery, "recover_write_checks", lambda doc: None)
    monkeypatch.setattr(body_recovery, "diagnose_doc", lambda *a: {"method": body_recovery.REGENERATE_STUB,
        "body_lost": "true", "candidate": None})
    monkeypatch.setattr(body_recovery, "recorded_or_standard", lambda doc: env.path)
    def stub(doc):
        entered.set()
        assert release.wait(10)
        return (HEADER + "recovered").encode()
    monkeypatch.setattr(body_recovery, "stub_bytes", stub)
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery = pool.submit(body_recovery.recover_one, D, body_recovery.REGENERATE_STUB, "user")
        assert entered.wait(10)
        editor = pool.submit(revise, env.req)
        release.set()
        assert recovery.result(timeout=15)["data"]["revision_no"] == 1
        with pytest.raises(Exception) as exc:
            editor.result(timeout=15)
        assert getattr(exc.value, "code", None) == "revision_conflict"
    assert env.path.read_text() == HEADER + "recovered"
    assert documents.get_by_id(D)["revision_no"] == 1


def test_wp_healing_needs_write_permission_and_advances_same_document(env, monkeypatch):
    from modules.flow_gate.services import work_plan_service as wp
    from flowgate.card_document_edit import guards
    documents.update(D, {"type_code": "WP"})
    raw = "---\ntype: WP\n---\n"
    env.path.write_text(raw)
    monkeypatch.setattr(wp, "is_unwritten_plan", lambda raw: True)
    monkeypatch.setattr(wp_route, "_plan_path", lambda doc: env.path)
    monkeypatch.setattr(wp_route, "_canonical_path", lambda doc: env.path.with_suffix(".json"))
    monkeypatch.setattr(wp_route, "_workflow_items", lambda doc: [])
    monkeypatch.setattr(body_recovery, "recover_write_checks", lambda doc: None)
    monkeypatch.setattr(windows, "editable", lambda doc: None)
    error = wp.WorkPlanUnreadable("not_json", raw=raw)
    monkeypatch.setattr(guards, "has_permission", lambda *a: False)
    assert recovery_bridge.heal(documents.get_by_id(D), error, "user") is None
    assert not env.path.with_suffix(".json").exists()
    monkeypatch.setattr(guards, "has_permission", lambda *a: True)
    doc = documents.get_by_id(D)
    result = recovery_bridge.heal(doc, error, "user")
    assert result and doc["revision_no"] == 1
    assert env.path.read_text() == raw
    assert env.path.with_suffix(".json").is_file()


def test_tsr_generated_create_join_and_late_run_are_guarded(env, monkeypatch):
    from modules.flow_gate.db import test_runs
    from modules.flow_gate.services import test_run_service
    from flowgate.card_document_edit import generated_bridge
    documents.update(D, {"type_code": "TS"})
    doc = documents.get_by_id(D)
    run = test_runs.insert_run(doc_id=D, revision_no=0, triggered_via="ui", runner_id="user", cases=[])
    monkeypatch.setattr(test_run_service, "_register_tsr_result", lambda *a: None)
    builds = []
    def render(doc, current_run, cases, locale):
        assert in_transaction() and flow_guard.held_guard() == ("flow", F)
        builds.append(current_run["run_id"])
        return "report " + str(len(builds)), "Report"
    monkeypatch.setattr(generated_bridge, "render_result", render)
    doc_id = generated_bridge.tsr(doc, run=run, run_chain=False)
    first = documents.get_by_id(doc_id)
    path = paths.resolve_storage_path(first["file_path"], "flowgate")
    assert first["revision_no"] == 0 and path.read_text() == "report 1"
    generated_bridge.tsr(doc, run=run, run_chain=False)
    assert documents.get_by_id(doc_id)["revision_no"] == 1 and path.read_text() == "report 2"
    assert len(document_revisions.list_by_doc(doc_id)) == 1
    def fail():
        generated_bridge.tsr(doc, run=run, run_chain=False)
        raise RuntimeError("outer run recording failed")
    with pytest.raises(RuntimeError):
        flow_guard.with_flow_guard(F, fail)
    assert documents.get_by_id(doc_id)["revision_no"] == 1 and path.read_text() == "report 2"
    test_runs.insert_run(doc_id=D, revision_no=0, triggered_via="ui", runner_id="user", cases=[])
    with pytest.raises(Exception):
        generated_bridge.tsr(doc, run=run, run_chain=False)
    assert documents.get_by_id(doc_id)["revision_no"] == 1


def test_tsr_wrong_transaction_and_missing_actor_fail_before_write(env, monkeypatch):
    from modules.flow_gate.db import test_runs
    from flowgate.card_document_edit import generated_bridge
    run = test_runs.insert_run(doc_id=D, revision_no=0, triggered_via="ui", runner_id="unknown", cases=[])
    before = business_state(env)
    with env.db.transaction():
        with pytest.raises(flow_guard.GuardNotHeld):
            generated_bridge.tsr(env.doc, run=run)
    with pytest.raises(Exception):
        generated_bridge.tsr(env.doc, run=run)
    assert business_state(env)[:4] == before[:4]


@pytest.mark.parametrize("fail", [False, True])
def test_spec_admission_report_link_and_body_share_outer_transaction(env, monkeypatch, fail):
    from modules.flow_gate.db import test_runs
    from modules.flow_gate.services import test_run_service, spec_execution_service
    from flowgate.card_document_edit import generated_bridge
    documents.update(D, {"type_code": "TS"})
    doc = documents.get_by_id(D)
    monkeypatch.setattr(test_run_service, "_register_tsr_result", lambda *a: None)
    cases = [{"case_id": "TC-1", "title": "Save", "category": "normal", "required": True,
              "execution_mode": "automated", "expected": "saved", "procedure": "save"}]
    def admit():
        documents.update(D, {"doc_review_status": "approved"})
        result = spec_execution_service.admit_on_approval(doc, {"cases": cases}, cases,
                                                        runner_id="user", locale="en")
        assert in_transaction() and not env.events
        if fail:
            raise RuntimeError("approval commit rejected")
        return result
    before_files = set(p for p in env.root.rglob("*") if p.is_file())
    if fail:
        with pytest.raises(RuntimeError, match="approval commit rejected"):
            generated_bridge.generated_boundary(doc, admit)
        assert documents.get_by_id(D)["doc_review_status"] == doc["doc_review_status"]
        assert test_runs.list_by_doc(D) == [] and env.events == []
        assert set(p for p in env.root.rglob("*") if p.is_file()) == before_files
        return
    result = generated_bridge.generated_boundary(doc, admit)
    run = test_runs.get_run(result["run"]["run_id"])
    report = documents.get_by_id(result["tsr_doc_id"])
    path = paths.resolve_storage_path(report["file_path"], "flowgate")
    assert run["tsr_doc_id"] == report["doc_id"] and report["revision_no"] == 0
    assert "TC-1" in path.read_text(encoding="utf-8")
    test_run_service.record_execution_progress(doc, run["run_id"], [
        {"case_id": "TC-1", "status": "PASS", "actual": "saved", "evidence": []}])
    assert documents.get_by_id(report["doc_id"])["revision_no"] == 1
    assert "PASS" in path.read_text(encoding="utf-8")
    saved = path.read_bytes()
    def broken_link(*args):
        raise RuntimeError("run link unavailable")
    monkeypatch.setattr(test_runs, "set_run_tsr_doc", broken_link)
    with pytest.raises(RuntimeError, match="run link unavailable"):
        test_run_service.open_execution_tsr(doc, cases, run, locale="en")
    assert documents.get_by_id(report["doc_id"])["revision_no"] == 1 and path.read_bytes() == saved


def test_generated_report_rejects_unreadable_body_and_changed_run_binding(env, monkeypatch):
    from modules.flow_gate.db import test_runs
    from modules.flow_gate.services import test_run_service
    from flowgate.card_document_edit import generated_bridge
    documents.update(D, {"type_code": "TS"})
    doc = documents.get_by_id(D)
    run = test_runs.insert_run(doc_id=D, revision_no=0, triggered_via="ui", runner_id="user", cases=[])
    monkeypatch.setattr(test_run_service, "_register_tsr_result", lambda *a: None)
    monkeypatch.setattr(generated_bridge, "render_result", lambda *a: ("report", "Report"))
    report_id = generated_bridge.tsr(doc, run=run)
    report = documents.get_by_id(report_id)
    path = paths.resolve_storage_path(report["file_path"], "flowgate")
    path.write_bytes(b"\xffbad")
    with pytest.raises(Exception):
        generated_bridge.tsr(doc, run=run)
    assert path.read_bytes() == b"\xffbad" and documents.get_by_id(report_id)["revision_no"] == 0
    path.unlink()
    generated_bridge.tsr(doc, run=run)
    assert path.read_text() == "report" and documents.get_by_id(report_id)["revision_no"] == 1
    test_runs.set_run_tsr_doc(run["run_id"], D)
    with pytest.raises(Exception):
        generated_bridge.tsr(doc, run=run)
    assert documents.get_by_id(report_id)["revision_no"] == 1



def test_failed_recovery_snapshot_fsync_rolls_back_partial_file_and_revision(env, monkeypatch):
    import os
    class Rule(RuleCall):
        def build(self, fresh, body):
            return Built(b"restored", snapshot_policy="RECOVERY_BEFORE")
    original = business_state(env)
    def fail(fd):
        raise OSError("snapshot fsync failed")
    monkeypatch.setattr(os, "fsync", fail)
    req = DedicatedRequest(D, 0, Actor("user"), Rule(env.path), Origin("recovery"))
    with pytest.raises(OSError):
        dedicated_bridge.dedicated_save(req)
    assert business_state(env)[:4] == original[:4] and not list(env.path.parent.glob("revisions/*"))
    assert env.events == []



@pytest.mark.parametrize("unsafe", ["duplicate", "shared", "replaced"])
def test_card_restore_revalidates_snapshot_identity_and_single_row(env, monkeypatch, unsafe):
    from flowgate.card_document_edit import store as edit_store
    backup = env.path.parent / "saved.md"
    backup.write_text(HEADER + "saved", encoding="utf-8")
    row = {"doc_id": D, "revision_no": 0, "backup_path": paths.to_storage_relative(backup, "flowgate"),
           "created_by": "user", "edit_reason": "user_comment"}
    document_revisions.create(row)
    if unsafe == "duplicate":
        # create() is deliberately idempotent; simulate legacy duplicate rows directly.
        env.db._execute("INSERT INTO document_revisions "
            "(doc_id, revision_no, backup_path, edit_reason, linked_doc_id, created_by, created_at) "
            "SELECT doc_id, revision_no, backup_path, edit_reason, linked_doc_id, created_by, created_at "
            "FROM document_revisions WHERE doc_id = ?", [D])
        assert len(document_revisions.list_by_doc_revision(D, 0)) == 2
    if unsafe == "shared":
        documents.create({"doc_id": F + ".0003-N", "project_id": "flowgate", "group_id": F, "type_code": "N",
                          "seq": 3, "title": "Other", "file_path": row["backup_path"]})
    env.path.unlink()
    monkeypatch.setattr(body_recovery, "recover_write_checks", lambda doc: None)
    monkeypatch.setattr(body_recovery, "recorded_or_standard", lambda doc: env.path)
    if unsafe == "replaced":
        real = edit_store.body_replace
        def replace(inp):
            real(inp)
            other = backup.with_suffix(".other")
            other.write_bytes(backup.read_bytes())
            other.replace(backup)
        monkeypatch.setattr(edit_store, "body_replace", replace)
    with pytest.raises(body_recovery.RecoveryRefused) as caught:
        body_recovery.recover_one(D, body_recovery.RESTORE_REVISION, "user")
    assert caught.value.error == "diagnosis_changed"
    assert documents.get_by_id(D)["revision_no"] == 0 and not env.path.exists()
    assert env.events == [] and backup.read_text(encoding="utf-8") == HEADER + "saved"


def test_generated_join_never_waits_for_admission_while_holding_flow(env):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from flowgate.card_document_edit import generated_bridge
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.services import test_run_service
    entered, release = Event(), Event()
    called = []
    before = business_state(env)
    def hold_admission():
        with test_run_service._admission_lock:
            entered.set()
            assert release.wait(10)
    with ThreadPoolExecutor(1) as pool:
        owner = pool.submit(hold_admission)
        try:
            assert entered.wait(10)
            with pytest.raises(GraphApiError) as error:
                flow_guard.with_flow_guard(F, lambda: generated_bridge.generated_boundary(
                    env.doc, lambda: called.append(True)))
            assert error.value.code == "internal_error"
        finally:
            release.set()
        owner.result(timeout=5)
    assert not called and business_state(env)[:4] == before[:4] and not env.events
