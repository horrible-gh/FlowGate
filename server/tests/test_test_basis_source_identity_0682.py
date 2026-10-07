"""flowgate.default.0682 T#1 — Test Basis exact source identity, execution source, stale.

D#1 §7 T#1 regression set over real git repositories and the production Source Bundle
capture (see basis_bundle_support): uncommitted and untracked product work is part of
the Basis and of the execution root, every caller gets the same verdict, a commit with
the same content is not a change, Groups stay isolated, a capture that cannot complete
refuses the run's measurement without writing anything, Pins keep the Bundle a Basis
runs from, Rebind recovers a lost Bundle, asset edits create a successor, and a v1 Basis
is stale.

0684 T#1: TS approval no longer captures. It queues the run (see the approval tests
below); the run's preparing phase captures (``ExecutionRootResolver.measure``).
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from basis_bundle_support import CASES, BasisEnv, git
from group_lock_stub import stub_group_lock
from modules.flow_gate.services import source_bundle_cleanup_service as cleanup
from modules.flow_gate.services import source_bundle_materializer as materializer
from modules.flow_gate.services import spec_execution_service as execution
from modules.flow_gate.services import test_asset_service as assets
from modules.flow_gate.services import test_basis_service as basis
from modules.flow_gate.services import test_run_service as runner


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = BasisEnv(tmp_path, monkeypatch)
    environment.repo("g1")
    return environment


def _prepare(env, doc_id, run_id="run-1"):
    doc = env.doc(doc_id)
    return execution.ExecutionRootResolver.prepare(doc, {"run_id": run_id}, basis.current(doc))


def _files(root):
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


# ── identity: uncommitted / untracked product work ────────────────────────────

def test_uncommitted_and_untracked_product_source_enter_basis_and_execution_root(env):
    root = env.roots["g1"]
    (root / "server" / "app.py").write_text("VALUE = 2\n")          # uncommitted change
    (root / "server" / "new_module.py").write_text("NEW = True\n")  # untracked product file
    doc = env.ts()
    stored = env.approve(doc)  # no source_worktree_dirty refusal any more
    assert stored["basis_version"] == 2 and stored["source"]["kind"] == "source_bundle"
    assert stored["binding"]["source_dirty"] is True
    captured = env.bundle_files(stored["binding"]["bundle_id"])
    assert "server/new_module.py" in captured and ".env" not in captured
    assert captured["server/app.py"] == hashlib.sha256(
        (root / "server" / "app.py").read_bytes()).hexdigest()
    assert {a["path"]: a["content_hash"] for a in stored["manifest"]} == {
        "tests/fixture.json": captured["tests/fixture.json"],
        "tests/test_a.py": captured["tests/test_a.py"]}
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.VALID

    run_root, scratch = _prepare(env, doc["doc_id"])
    assert _files(run_root) == set(captured)  # exactly the Bundle; .env never copied
    assert (run_root / "server" / "app.py").read_text() == "VALUE = 2\n"
    assert (run_root / "server" / "new_module.py").read_text() == "NEW = True\n"
    assert not (run_root / ".git").exists()
    usage = env.db.list_usage_for_run("run-1")
    assert [(u["bundle_id"], u["operation"]) for u in usage] == [
        (stored["binding"]["bundle_id"], "spec_execution")]


def test_basis_id_excludes_binding_and_includes_identity_fields(env):
    doc = env.ts()
    stored = env.approve(doc)
    identity = {k: stored[k] for k in ("basis_version", "ts_document_id", "ts_revision_no",
                                       "source", "test_assets", "execution_profile")}
    assert stored["basis_id"] == basis.canonical_hash(identity)
    assert set(stored["source"]) == {"kind", "exclusion_policy_version", "content_fingerprint"}
    assert stored["test_assets"]["policy_version"] == basis.ASSET_POLICY_VERSION
    assert set(stored["binding"]) >= {"bundle_id", "bundle_sha256", "git_revision",
                                      "source_dirty", "captured_at", "group_id"}
    identity_row = basis.source_identity(stored)
    assert identity_row["content_fingerprint"] == stored["source"]["content_fingerprint"]
    assert identity_row["bundle_sha256"] == stored["binding"]["bundle_sha256"]
    from modules.flow_gate.services import test_spec_service as spec
    assert spec.normalize_source_identity(identity_row) == identity_row


# ── stale: one verdict for every change ───────────────────────────────────────

@pytest.mark.parametrize("change", ["modify", "add", "delete", "add_dir"])
def test_product_change_after_approval_is_stale_with_source_reason(env, change):
    root = env.roots["g1"]
    doc = env.ts()
    stored = env.approve(doc)
    if change == "modify":
        (root / "server" / "app.py").write_text("VALUE = 9\n")
    elif change == "add":
        (root / "server" / "extra.py").write_text("X = 1\n")
    elif change == "delete":
        (root / "server" / "app.py").unlink()
    else:
        (root / "server" / "empty_pkg").mkdir()
    judged = basis.verdict(env.doc(doc["doc_id"]), stored)
    assert judged["state"] == basis.STALE and judged["reasons"] == [basis.REASON_SOURCE]
    assert judged["basis_valid"] is False and judged["stale"] is True
    with pytest.raises(ValueError, match="basis_stale_before_execution"):
        _prepare(env, doc["doc_id"])


def test_asset_change_reports_source_and_manifest(env):
    doc = env.ts()
    stored = env.approve(doc)
    (env.roots["g1"] / "tests" / "fixture.json").write_text('{"v":2}\n')
    judged = basis.verdict(env.doc(doc["doc_id"]), stored)
    assert judged["reasons"] == [basis.REASON_SOURCE, basis.REASON_MANIFEST]


def test_ts_revision_and_policy_changes_have_their_own_reasons(env, monkeypatch):
    doc = env.ts()
    stored = env.approve(doc)
    env.store.docs[doc["doc_id"]]["revision_no"] = 2
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["reasons"] == [basis.REASON_TS_REVISION]
    env.store.docs[doc["doc_id"]]["revision_no"] = 1
    current_policy = basis.ASSET_POLICY_VERSION
    monkeypatch.setattr(basis, "ASSET_POLICY_VERSION", current_policy + "-next")
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["reasons"] == [basis.REASON_ASSET_POLICY]
    monkeypatch.setattr(basis, "ASSET_POLICY_VERSION", current_policy)
    monkeypatch.setattr(materializer, "POLICY_VERSION", "source-bundle-v2")
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["reasons"] == [basis.REASON_BUNDLE_POLICY]


def test_commit_with_same_content_is_not_stale(env):
    root = env.roots["g1"]
    (root / "server" / "app.py").write_text("VALUE = 2\n")
    (root / "server" / "new_module.py").write_text("NEW = True\n")
    doc = env.ts()
    stored = env.approve(doc)
    git(root, "add", "server")
    git(root, "commit", "-m", "TR commit point")
    judged = basis.verdict(env.doc(doc["doc_id"]), stored)
    assert judged["state"] == basis.VALID
    run_root, _ = _prepare(env, doc["doc_id"])  # the pinned Bundle still matches: no rebind
    assert (run_root / "server" / "new_module.py").is_file()
    assert not [e for e in env.store.events if e[1] == "test_spec_basis_rebound"]


def test_unmeasurable_source_is_unverifiable_not_stale(env):
    import shutil
    doc = env.ts()
    stored = env.approve(doc)
    shutil.rmtree(env.roots["g1"], ignore_errors=True)
    env.roots.pop("g1")
    judged = basis.verdict(env.doc(doc["doc_id"]), stored)
    assert judged["state"] == basis.UNVERIFIABLE
    assert judged["reasons"] == ["source_unmeasurable:group_worktree_unavailable"]
    error = basis.verdict_error(judged, runner._http_error, doc_id=doc["doc_id"])
    assert error.status_code == 409 and error.detail["error"] == "basis_unavailable"
    with pytest.raises(ValueError, match="basis_unverifiable_before_execution"):
        _prepare(env, doc["doc_id"])


def test_display_memo_reuses_hashes_only_while_scan_is_unchanged(env, monkeypatch):
    doc = env.ts()
    stored = env.approve(doc)
    assert basis.verdict(env.doc(doc["doc_id"]), stored, memo=True)["state"] == basis.VALID
    calls = []
    real = materializer.inspect_source
    monkeypatch.setattr(materializer, "inspect_source",
                        lambda root, deadline: calls.append(root) or real(root, deadline))
    assert basis.verdict(env.doc(doc["doc_id"]), stored, memo=True)["state"] == basis.VALID
    assert calls == []  # unchanged scan: memo hit
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 3\n")
    assert basis.verdict(env.doc(doc["doc_id"]), stored, memo=True)["state"] == basis.STALE
    assert len(calls) == 1
    basis.verdict(env.doc(doc["doc_id"]), stored)  # decision paths always measure
    assert len(calls) == 2


# ── Group isolation ───────────────────────────────────────────────────────────

def test_other_group_changes_and_bundles_never_reach_this_basis(env):
    env.repo("g2", value="200")
    first = env.ts("g1", 10)
    other = env.ts("g2", 20)
    stored = env.approve(first)
    other_basis = env.approve(other)
    (env.roots["g2"] / "server" / "app.py").write_text("VALUE = 201\n")
    (env.roots["g2"] / "server" / "only_g2.py").write_text("G2 = 1\n")
    assert basis.verdict(env.doc(first["doc_id"]), stored)["state"] == basis.VALID
    # A binding that names the other Group's Bundle is never opened for this TS.
    tampered = {**stored, "binding": dict(other_basis["binding"])}
    meta = env.doc(first["doc_id"])
    env.store.docs[first["doc_id"]]["meta"] = basis.metadata_with_basis(meta, tampered)
    with pytest.raises(ValueError, match="bundle_mismatch"):
        basis.open_bundle(env.doc(first["doc_id"]), tampered)
    run_root, _ = _prepare(env, first["doc_id"])
    assert (run_root / "server" / "app.py").read_text() == "VALUE = 1\n"
    assert not (run_root / "server" / "only_g2.py").exists()
    rebound = basis.current(env.doc(first["doc_id"]))
    assert rebound["basis_id"] == stored["basis_id"]
    assert rebound["binding"]["group_id"] == "g1"
    assert rebound["binding"]["bundle_id"] != other_basis["binding"]["bundle_id"]


# ── approval queues the run; the run's measurement owns the capture ──────────

def _approve_through_pipeline(env, monkeypatch, doc_id, blocker=None):
    from modules.flow_gate.services import test_spec_service, workflow_rework_service
    from modules.flow_gate.workflow import pipeline_service as pipeline
    monkeypatch.setattr(pipeline, "_require_document_body_for_approval", lambda *a: None)
    monkeypatch.setattr(pipeline, "_require_test_gate_for_approval", lambda *a: None)
    monkeypatch.setattr(pipeline, "_require_workflow_head_for_approval", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "log_state_changed", lambda **kw: None)
    monkeypatch.setattr(pipeline, "get_store", lambda: env.store)
    monkeypatch.setattr(workflow_rework_service, "clear_return_point_if_complete", lambda *a: None)
    monkeypatch.setattr(runner, "_read_doc_content_or_empty", lambda _doc: "spec")
    monkeypatch.setattr(test_spec_service, "detect_contract_version",
                        lambda _content: test_spec_service.CONTRACT_SPEC)
    monkeypatch.setattr(test_spec_service, "parse_spec",
                        lambda _content: {"cases": env.cases, "errors": [], "title": "t"})
    monkeypatch.setattr(runner, "_active_tsr_for_ts", lambda _doc: None)
    monkeypatch.setattr(basis, "source_root", lambda d: env.roots[d["group_id"]])
    monkeypatch.setattr(execution, "admission_blocker", lambda _doc_id: blocker)
    env.admitted = []

    def admit_on_approval(doc, parsed, selected, *, runner_id, locale):
        env.admitted.append({"doc": doc, "case_ids": [c["case_id"] for c in selected]})
        return {"run": {"run_id": "run-approval", "doc_id": doc["doc_id"]},
                "tsr_doc_id": "tsr", "selected_case_ids": [c["case_id"] for c in selected]}
    monkeypatch.setattr(execution, "admit_on_approval", admit_on_approval)
    monkeypatch.setattr(runner, "_emit_started", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_broadcast", lambda *a, **k: None)
    return pipeline.transition_document_review(
        doc_id=doc_id, action="approve", actor_user_id="u",
        user_permissions={"document.approve"})


def test_pipeline_approval_queues_the_run_without_capturing(env, monkeypatch):
    # 0684 T#1: a dirty worktree, a stored Basis from an earlier approval -- approval
    # captures nothing, pins nothing, sets the old Basis aside and queues the run.
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 5\n")
    doc = env.ts()
    earlier = env.approve(doc)
    env.store._execute("DELETE FROM source_bundle_pins")
    bundles_before = env.store._fetch_all("SELECT bundle_id FROM source_bundles")
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review",
                                          "type_code": "TS", "id": 1})
    capture = []
    monkeypatch.setattr(basis, "capture", lambda *a, **kw: capture.append(a))
    result = _approve_through_pipeline(env, monkeypatch, doc["doc_id"])
    assert result["doc_review_status"] == "approved"
    assert result["spec_execution"] == {"run_id": "run-approval", "tsr_doc_id": "tsr",
                                        "selected_case_ids": ["TC-001"], "status": "queued"}
    assert capture == []
    assert env.admitted[0]["case_ids"] == ["TC-001"]
    assert env.admitted[0]["doc"]["doc_review_status"] == "approved"
    stored = env.doc(doc["doc_id"])
    assert basis.current(stored) is None
    assert json.loads(stored["meta"])["superseded_test_basis"]["basis_id"] == earlier["basis_id"]
    assert env.db.pin_get(doc["doc_id"]) is None
    assert env.store._fetch_all("SELECT bundle_id FROM source_bundles") == bundles_before
    assert [e[1] for e in env.store.events] == ["test_spec_execution_admitted"]


def test_approval_precheck_measures_nothing_and_creates_no_bundle(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    (env.roots["g1"] / "server" / "untracked.py").write_text("U = 1\n")
    doc = env.ts()
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review",
                                          "type_code": "TS", "id": 1})
    _approve_through_pipeline(env, monkeypatch, doc["doc_id"])  # installs the guards
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review", "meta": "{}"})
    env.admitted.clear()
    monkeypatch.setattr(basis, "probe", lambda *a, **kw: pytest.fail("precheck must not measure"))
    checked = pipeline.precheck_document_review_transition(
        doc_id=doc["doc_id"], action="approve", actor_user_id="u",
        user_permissions={"document.approve"})
    assert checked["next_status"] == "approved"
    assert env.store._fetch_all("SELECT * FROM source_bundles") == []
    assert env.admitted == []
    assert env.doc(doc["doc_id"])["doc_review_status"] == "pending_review"


def test_approval_refuses_a_missing_automated_test_file_by_case_and_path(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    doc = env.ts()
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review",
                                          "type_code": "TS", "id": 1})
    before = env.doc(doc["doc_id"])
    (env.roots["g1"] / "tests" / "test_a.py").unlink()
    with pytest.raises(pipeline.TransitionError) as refused:
        _approve_through_pipeline(env, monkeypatch, doc["doc_id"])
    assert "TC-001" in str(refused.value) and "tests/test_a.py" in str(refused.value)
    assert "test_asset_missing" in str(refused.value)
    assert env.doc(doc["doc_id"]) == before
    assert env.admitted == [] and env.store.events == []


def test_approval_refuses_while_a_run_is_in_progress(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    doc = env.ts()
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review",
                                          "type_code": "TS", "id": 1})
    before = env.doc(doc["doc_id"])
    with pytest.raises(pipeline.TransitionError, match="run-busy"):
        _approve_through_pipeline(env, monkeypatch, doc["doc_id"],
                                  blocker={"error": "run_in_progress", "run_id": "run-busy"})
    assert env.doc(doc["doc_id"]) == before
    assert env.admitted == [] and env.store.events == []


@pytest.mark.parametrize("failure", ["busy", "changed"])
def test_run_measure_failure_writes_nothing(env, monkeypatch, failure):
    # The capture moved from the approval to the run's preparing phase; a capture that
    # cannot complete refuses the run's measurement exactly as it refused approval before.
    doc = env.ts()
    before = env.doc(doc["doc_id"])
    if failure == "busy":
        stub_group_lock(monkeypatch, grant=False)
        expected = "basis_capture_failed:source_busy"
    else:
        real = materializer._hash_file
        target = env.roots["g1"] / "server" / "app.py"

        def racing(root, relative, expected_stat, deadline, out=None):
            if relative == "server/app.py" and out is not None:
                target.write_text("VALUE = 77  # changed during capture\n")
            return real(root, relative, expected_stat, deadline, out)
        monkeypatch.setattr(materializer, "_hash_file", racing)
        expected = "basis_capture_failed:source_changed"
    with pytest.raises(ValueError, match=expected):
        execution.ExecutionRootResolver.measure(
            env.doc(doc["doc_id"]), {"run_id": "run-1", "revision_no": 1}, env.cases)
    assert env.doc(doc["doc_id"]) == before
    assert env.store._fetch_all("SELECT * FROM source_bundle_pins") == []
    assert env.store._fetch_all("SELECT * FROM source_bundles WHERE status='created'") == []
    assert env.store.events == []


def test_excluded_asset_is_refused_as_not_captured(env):
    root = env.roots["g1"]
    (root / "tests" / "secrets.json").write_text("{}\n")
    git(root, "add", "-f", "tests/secrets.json")
    git(root, "commit", "-m", "secret-named fixture")
    env.cases = [{**CASES[0], "test_assets": "tests/secrets.json"}]
    with pytest.raises(ValueError, match="test_asset_not_captured: tests/secrets.json"):
        basis.capture(env.ts(), env.cases)


def test_product_path_cannot_enter_manifest(env):
    env.cases = [{**CASES[0], "test_assets": "server/app.py"}]
    with pytest.raises(ValueError, match="product_source_or_invalid_test_asset"):
        basis.capture(env.ts(), env.cases)


# ── retention Pin, TTL and Group cleanup ──────────────────────────────────────

def _expire_all(env):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    env.store._execute("UPDATE source_bundles SET expires_at=?", [past])


def test_pinned_bundle_survives_ttl_and_successor_releases_it(env, monkeypatch):
    removed = []
    monkeypatch.setattr(cleanup, "_safe_remove", lambda path: removed.append(path.name))
    doc = env.ts()
    first = env.approve(doc)
    _expire_all(env)
    assert cleanup.sweep_expired()["bundle_matched"] == 0
    assert cleanup.cleanup_bundle(first["binding"]["bundle_id"]) is False  # explicit: refused
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 4\n")
    second = env.approve(env.doc(doc["doc_id"]))  # re-approval replaces the Pin
    assert env.db.pin_get(doc["doc_id"])["bundle_id"] == second["binding"]["bundle_id"]
    _expire_all(env)
    swept = cleanup.sweep_expired()
    assert removed == [first["binding"]["bundle_id"]] and swept["bundle_deleted"] == 1
    assert env.db.get(second["binding"]["bundle_id"])["status"] == "created"
    group = cleanup.cleanup_for_group("g1")
    assert group["bundle_deleted"] == 1 and env.db.pin_get(doc["doc_id"]) is None


# ── Rebind ────────────────────────────────────────────────────────────────────

def _lose_bundle(env, bundle_id):
    env.remove_bundle_dir(bundle_id)
    env.db.deleted(bundle_id)


def test_lost_bundle_rebinds_when_source_is_unchanged(env):
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 6\n")
    doc = env.ts()
    stored = env.approve(doc)
    _lose_bundle(env, stored["binding"]["bundle_id"])
    run_root, _ = _prepare(env, doc["doc_id"], "run-rebind")
    rebound = basis.current(env.doc(doc["doc_id"]))
    assert rebound["basis_id"] == stored["basis_id"]
    assert rebound["binding"]["bundle_id"] != stored["binding"]["bundle_id"]
    assert env.db.pin_get(doc["doc_id"])["bundle_id"] == rebound["binding"]["bundle_id"]
    event = [e for e in env.store.events if e[1] == "test_spec_basis_rebound"]
    assert event and event[0][2]["old_bundle_id"] == stored["binding"]["bundle_id"]
    assert (run_root / "server" / "app.py").read_text() == "VALUE = 6\n"
    assert env.db.list_usage_for_run("run-rebind")[0]["bundle_id"] == rebound["binding"]["bundle_id"]


def test_lost_bundle_with_changed_source_is_stale_not_rebound(env):
    doc = env.ts()
    stored = env.approve(doc)
    _lose_bundle(env, stored["binding"]["bundle_id"])
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 8\n")
    with pytest.raises(ValueError, match="basis_stale_before_execution"):
        _prepare(env, doc["doc_id"])
    assert basis.current(env.doc(doc["doc_id"]))["binding"] == stored["binding"]
    assert not [e for e in env.store.events if e[1] == "test_spec_basis_rebound"]


# ── Execution Root bytes ──────────────────────────────────────────────────────

def _writable(path):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)


@pytest.mark.parametrize("when", ["after_open", "during_copy"])
def test_execution_root_refuses_bundle_bytes_changed_after_open(env, monkeypatch, when):
    doc = env.ts()
    stored = env.approve(doc)
    bundle_app = env.tmp_path / "bundles" / stored["binding"]["bundle_id"] / "source" / "server" / "app.py"

    def tamper():
        _writable(bundle_app)
        bundle_app.write_text("VALUE = 9\n")  # same size: only the bytes differ

    if when == "after_open":
        real_open = basis.open_bundle

        def open_then_tamper(d, b):
            opened = real_open(d, b)
            tamper()
            return opened
        monkeypatch.setattr(basis, "open_bundle", open_then_tamper)
    else:
        real_copy = basis._copy_hashed

        def copy_while_tampered(source, target):
            if source == bundle_app:
                tamper()
            return real_copy(source, target)
        monkeypatch.setattr(basis, "_copy_hashed", copy_while_tampered)
    with pytest.raises(ValueError, match="execution_source_mismatch"):
        _prepare(env, doc["doc_id"], "run-tampered")
    assert not (env.tmp_path / "runs" / "run-tampered").exists()
    assert env.db.list_usage_for_run("run-tampered") == []


def test_execution_root_refuses_a_directory_outside_the_manifest(env, monkeypatch):
    doc = env.ts()
    stored = env.approve(doc)
    source = env.tmp_path / "bundles" / stored["binding"]["bundle_id"] / "source"
    real_open = basis.open_bundle

    def open_then_add_dir(d, b):
        opened = real_open(d, b)
        _writable(source)
        (source / "stray").mkdir()  # empty: invisible to a file-only comparison
        return opened
    monkeypatch.setattr(basis, "open_bundle", open_then_add_dir)
    with pytest.raises(ValueError, match="execution_source_mismatch"):
        _prepare(env, doc["doc_id"], "run-stray")
    assert not (env.tmp_path / "runs" / "run-stray").exists()


def test_execution_root_hashes_match_the_manifest(env):
    doc = env.ts()
    stored = env.approve(doc)
    run_root, _ = _prepare(env, doc["doc_id"], "run-hashes")
    assert {path: hashlib.sha256((run_root / path).read_bytes()).hexdigest()
            for path in _files(run_root)} == env.bundle_files(stored["binding"]["bundle_id"])


# ── v1 Basis ──────────────────────────────────────────────────────────────────

def _v1_basis(doc_id):
    return {"basis_id": "1" * 64, "basis_version": 1, "ts_document_id": doc_id,
            "ts_revision_no": 1,
            "source": {"kind": "compat_worktree", "git_revision": "a" * 40, "tree": "b" * 40},
            "test_assets": {"manifest_hash": "c" * 64, "asset_count": 2},
            "execution_profile": {"runner_generation": 1}, "manifest": []}


def test_v1_basis_is_stale_but_does_not_overturn_an_approved_tsr(env, monkeypatch):
    doc = env.ts()
    old = _v1_basis(doc["doc_id"])
    env.store.docs[doc["doc_id"]]["meta"] = basis.metadata_with_basis(env.doc(doc["doc_id"]), old)
    judged = basis.verdict(env.doc(doc["doc_id"]), old)
    assert judged["state"] == basis.STALE and judged["reasons"] == [basis.REASON_BASIS_OUTDATED]
    from modules.flow_gate.services import test_spec_service as spec
    monkeypatch.setattr(runner.db_test_runs, "latest_by_doc",
                        lambda _id: {"contract_version": spec.CONTRACT_SPEC, "result_meta": "{}",
                                     "run_id": "r1"})
    monkeypatch.setattr(runner.db_test_runs, "get_running_by_doc", lambda _id: None)
    monkeypatch.setattr(runner.db_test_runs, "latest_spec_result", lambda *a: {
        "run_id": "r1", "overall": "PASS", "status": "passed", "created_at": "1"})
    approved = runner.tsr_gate_state({"target_id": doc["doc_id"], "doc_review_status": "approved"})
    assert approved["passed"] is True and approved["stale"] is False
    pending = runner.tsr_gate_state({"target_id": doc["doc_id"],
                                     "doc_review_status": "pending_review"})
    assert pending["passed"] is False and pending["overall"] == "NOT_RUN"
    assert pending["basis_reasons"] == [basis.REASON_BASIS_OUTDATED]


# ── test asset edit → successor Basis ─────────────────────────────────────────

def _asset_edit_env(env, monkeypatch, doc):
    monkeypatch.setattr(assets, "_load", lambda ts_id: (
        env.doc(ts_id), {"cases": env.cases}, basis.current(env.doc(ts_id))))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda d: None)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_result", lambda *a: None)
    monkeypatch.setattr(assets.db_test_runs, "get_running_by_doc", lambda ts_id: None)
    monkeypatch.setattr(basis, "source_root", lambda d: env.roots[d["group_id"]])


def test_asset_edit_creates_successor_with_new_pin(env, monkeypatch):
    doc = env.ts()
    stored = env.approve(doc)
    _asset_edit_env(env, monkeypatch, doc)
    path = "tests/fixture.json"
    old_hash = hashlib.sha256((env.roots["g1"] / path).read_bytes()).hexdigest()
    response = assets.update(doc["doc_id"], path, expected_hash=old_hash,
                             content='{"v":2}\n', actor_id="u")
    successor = basis.current(env.doc(doc["doc_id"]))
    assert response["basis_id"] == successor["basis_id"] != stored["basis_id"]
    assert successor["binding"]["bundle_id"] != stored["binding"]["bundle_id"]
    assert env.db.pin_get(doc["doc_id"])["bundle_id"] == successor["binding"]["bundle_id"]
    assert [e[1] for e in env.store.events] == [
        "test_spec_asset_updated", "test_spec_basis_superseded",
        "test_spec_basis_created", "test_spec_results_invalidated"]
    # Results recorded under the old Basis no longer count: it is not the stored one.
    assert basis.verdict(env.doc(doc["doc_id"]), successor, execution_basis=stored)[
        "reasons"] == [basis.REASON_BASIS_REPLACED]
    assert basis.verdict(env.doc(doc["doc_id"]), successor)["state"] == basis.VALID
    # 0684 T#1: no zero-result initialization run, no report rewrite (D#1 §3-7).
    assert "initialization_run_id" not in response


def test_asset_edit_refused_while_a_run_is_in_progress(env, monkeypatch):
    doc = env.ts()
    stored = env.approve(doc)
    _asset_edit_env(env, monkeypatch, doc)
    monkeypatch.setattr(assets.db_test_runs, "get_running_by_doc",
                        lambda ts_id: {"run_id": "run-live"})
    path = env.roots["g1"] / "tests" / "fixture.json"
    before = path.read_bytes()
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":9}\n', actor_id="u")
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "run_in_progress"
    assert path.read_bytes() == before
    assert basis.current(env.doc(doc["doc_id"])) == stored


def test_asset_edit_failure_restores_bytes_basis_and_pin(env, monkeypatch):
    doc = env.ts()
    stored = env.approve(doc)
    _asset_edit_env(env, monkeypatch, doc)
    # The last write of the edit transaction fails: bytes, Basis and Pin all roll back.
    monkeypatch.setattr(basis, "pin",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("pin failed")))
    path = env.roots["g1"] / "tests" / "fixture.json"
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="pin failed"):
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":3}\n', actor_id="u")
    assert path.read_bytes() == before
    assert basis.current(env.doc(doc["doc_id"])) == stored
    assert env.db.pin_get(doc["doc_id"])["bundle_id"] == stored["binding"]["bundle_id"]
    assert env.store.events == []
    assert basis.verdict(env.doc(doc["doc_id"]), stored)["state"] == basis.VALID


def test_asset_edit_on_stale_basis_is_refused(env, monkeypatch):
    doc = env.ts()
    env.approve(doc)
    _asset_edit_env(env, monkeypatch, doc)
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 10\n")
    path = env.roots["g1"] / "tests" / "fixture.json"
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                      content='{"v":4}\n', actor_id="u")
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "basis_stale"
    assert refused.value.detail["reasons"] == [basis.REASON_SOURCE]
    assert path.read_text() == '{"v":1}\n'


@pytest.mark.parametrize("failing", ["fsync", "replace"])
def test_asset_write_failure_keeps_old_bytes_basis_and_pin(env, monkeypatch, failing):
    # A write that fails part-way (staged bytes written, then an I/O error) never reaches
    # the asset: the old bytes, Basis and Pin stay current and nothing is left beside it.
    doc = env.ts()
    stored = env.approve(doc)
    _asset_edit_env(env, monkeypatch, doc)
    captured = []
    monkeypatch.setattr(basis, "capture", lambda *a, **kw: captured.append(a) or {})

    def io_error(*_a, **_kw):
        raise OSError("disk full")
    monkeypatch.setattr(assets.os, failing, io_error)
    tests_dir = env.roots["g1"] / "tests"
    path = tests_dir / "fixture.json"
    before, listing = path.read_bytes(), sorted(p.name for p in tests_dir.iterdir())
    with pytest.raises(OSError, match="disk full"):
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":5, "pad": "' + "x" * 4096 + '"}\n', actor_id="u")
    assert path.read_bytes() == before
    assert sorted(p.name for p in tests_dir.iterdir()) == listing  # no staged leftovers
    assert captured == []
    assert basis.current(env.doc(doc["doc_id"])) == stored
    assert env.db.pin_get(doc["doc_id"])["bundle_id"] == stored["binding"]["bundle_id"]
    assert env.store.events == []


def test_product_change_while_waiting_for_the_group_lock_is_refused(env, monkeypatch):
    # The first verdict passes, then a product file changes before G is granted: the
    # verdict taken again under G refuses the edit, so no successor adopts the change.
    from modules.flow_gate.services.git import lock_manager
    doc = env.ts()
    stored = env.approve(doc)
    _asset_edit_env(env, monkeypatch, doc)
    captured = []
    monkeypatch.setattr(basis, "capture", lambda *a, **kw: captured.append(a) or {})
    real_acquire = lock_manager.acquire_group

    def product_changes_while_waiting(*a, **kw):
        (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 7\n")
        return real_acquire(*a, **kw)
    monkeypatch.setattr(lock_manager, "acquire_group", product_changes_while_waiting)
    path = env.roots["g1"] / "tests" / "fixture.json"
    before = path.read_bytes()
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":6}\n', actor_id="u")
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "basis_stale"
    assert refused.value.detail["reasons"] == [basis.REASON_SOURCE]
    assert path.read_bytes() == before
    assert captured == []
    assert basis.current(env.doc(doc["doc_id"])) == stored
    assert env.db.pin_get(doc["doc_id"])["bundle_id"] == stored["binding"]["bundle_id"]
    assert env.store.events == []


def test_product_change_after_the_recheck_never_enters_a_successor(env, monkeypatch):
    # A product file written between the recheck under G and the capture is in the new
    # Bundle; the successor must be the approved source plus this asset only, so it is
    # refused and the asset goes back to its old bytes.
    doc = env.ts()
    stored = env.approve(doc)
    _asset_edit_env(env, monkeypatch, doc)
    real_replace = assets._replace_bytes
    writes = []

    def replace_then_product_changes(target, data):
        real_replace(target, data)
        writes.append(data)
        if len(writes) == 1:
            (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 8\n")
    monkeypatch.setattr(assets, "_replace_bytes", replace_then_product_changes)
    path = env.roots["g1"] / "tests" / "fixture.json"
    before = path.read_bytes()
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":7}\n', actor_id="u")
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "basis_stale"
    assert refused.value.detail["reasons"] == [basis.REASON_SOURCE]
    assert path.read_bytes() == before
    assert basis.current(env.doc(doc["doc_id"])) == stored
    assert env.db.pin_get(doc["doc_id"])["bundle_id"] == stored["binding"]["bundle_id"]
    assert env.store.events == []
