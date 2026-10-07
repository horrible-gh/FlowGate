"""flowgate.default.0682 T#1 / 0684 T#2 — Test Basis exact source identity, run copy, stale.

Regression set over real git repositories and the production fingerprint, copy, manifest
and verdict code (see basis_support).

0684 T#2 (D#1 §3-3, §3-5, §3-6, §3-7): there is no Source Bundle and no Basis on the TS.
A run's preparing phase copies the Group worktree into its disposable root and
fingerprints the bytes it copied; that is the run's Basis. Uncommitted and untracked work
is part of it, other Groups never reach it, a change during the copy refuses the run, a
change after the copy never reaches the run root and makes the result stale, display
paths reuse a memo and never hash, an asset edit just stales the results, and v1/v2
Basis values are outdated.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from basis_support import CASES, git
from basis_support import BasisEnv
from group_lock_stub import stub_group_lock
from modules.flow_gate.services import source_fingerprint as fingerprint
from modules.flow_gate.services import spec_execution_service as execution
from modules.flow_gate.services import test_asset_service as assets
from modules.flow_gate.services import test_basis_service as basis
from modules.flow_gate.services import test_run_service as runner


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = BasisEnv(tmp_path, monkeypatch)
    environment.repo("g1")
    return environment


def _files(root):
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _dirs(root):
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()}


def _count_hashing(monkeypatch):
    """Count every file the fingerprint module hashes (copy or measure)."""
    calls = []
    real = fingerprint._hash_file

    def counting(*args, **kwargs):
        calls.append(args[1])
        return real(*args, **kwargs)
    monkeypatch.setattr(fingerprint, "_hash_file", counting)
    return calls


# ── identity: the run's copy, uncommitted / untracked product work ────────────

def test_run_copy_holds_uncommitted_and_untracked_work_and_its_basis_is_the_copied_bytes(env):
    root = env.roots["g1"]
    (root / "server" / "app.py").write_text("VALUE = 2\n")          # uncommitted change
    (root / "server" / "new_module.py").write_text("NEW = True\n")  # untracked product file
    doc = env.ts()
    run_basis, run_root, scratch = env.run_basis(doc)
    assert run_basis["basis_version"] == 3 and run_basis["source"]["kind"] == "run_source"
    assert run_basis["binding"]["source_dirty"] is True
    assert run_basis["binding"]["run_id"] == "run-1"
    assert run_root == scratch / "source"
    copied = _files(run_root)
    assert {"server/new_module.py", "server/app.py", "tests/test_a.py"} <= copied
    assert ".env" not in copied and not (run_root / ".git").exists()
    assert (run_root / "server" / "app.py").read_text() == "VALUE = 2\n"
    # The fingerprint is the copied bytes': recomputing it from the run root agrees.
    entries = [{"path": path, "size": (run_root / path).stat().st_size,
                "sha256": hashlib.sha256((run_root / path).read_bytes()).hexdigest()}
               for path in sorted(copied)]
    dirs = [(name, None) for name in sorted(_dirs(run_root))]
    assert fingerprint._fingerprint(entries, dirs) == run_basis["source"]["content_fingerprint"]
    assert {a["path"]: a["content_hash"] for a in run_basis["manifest"]} == {
        "tests/fixture.json": hashlib.sha256((run_root / "tests/fixture.json").read_bytes()).hexdigest(),
        "tests/test_a.py": hashlib.sha256((run_root / "tests/test_a.py").read_bytes()).hexdigest()}
    assert basis.verdict(env.doc(doc["doc_id"]), run_basis)["state"] == basis.VALID
    # Nothing is stored on the TS and no Bundle is captured or pinned.
    assert "test_basis" not in json.loads(env.doc(doc["doc_id"])["meta"])


def test_run_copy_keeps_each_file_s_permission_bits(env, monkeypatch):
    """0684 T#2 rework: the copy is given every source file's own mode bits."""
    (env.roots["g1"] / "tests" / "helper.sh").write_text("#!/bin/sh\necho helper-ran\n")
    kept = {}
    real = fingerprint._keep_mode

    def spying(copied, st):
        kept[copied.as_posix()] = st.st_mode
        return real(copied, st)
    monkeypatch.setattr(fingerprint, "_keep_mode", spying)
    _, run_root, _ = env.run_basis(env.ts())
    copied = _files(run_root)
    assert {Path(path).relative_to(run_root).as_posix() for path in kept} == copied
    for path, mode in kept.items():
        source = env.roots["g1"] / Path(path).relative_to(run_root)
        assert mode == source.lstat().st_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute bit")
def test_an_executable_helper_still_runs_from_the_run_copy(env):
    """0684 T#2 rework: a 0755 helper a Case invokes stays executable in the copy."""
    root = env.roots["g1"]
    helper = root / "tests" / "helper.sh"
    helper.write_text("#!/bin/sh\necho helper-ran\n")
    helper.chmod(0o755)
    (root / "tests" / "data.txt").write_text("plain\n")
    (root / "tests" / "data.txt").chmod(0o640)
    _, run_root, _ = env.run_basis(env.ts())
    copied = run_root / "tests" / "helper.sh"
    assert stat.S_IMODE(copied.stat().st_mode) == 0o755
    assert stat.S_IMODE((run_root / "tests" / "data.txt").stat().st_mode) == 0o640
    out = subprocess.run([str(copied)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and out.stdout.strip() == "helper-ran"


# ── execute bits are part of the source identity (0684 T#2 rework 2) ─────────

def test_execute_bits_enter_the_unchanged_check_the_fingerprint_and_the_memo(monkeypatch):
    """A chmod changes neither bytes, size nor mtime; on POSIX it is still a change."""
    from types import SimpleNamespace

    def state(mode):
        return SimpleNamespace(st_mode=stat.S_IFREG | mode, st_size=8, st_mtime_ns=123,
                               st_dev=4, st_ino=99)
    plain, executable = state(0o644), state(0o755)
    entry = {"path": "tests/helper.sh", "size": 8, "sha256": "a" * 64}
    monkeypatch.setattr(fingerprint, "_EXEC_BITS", 0o111)  # the POSIX rule
    assert fingerprint._same(executable, executable)
    assert not fingerprint._same(plain, executable)
    assert not fingerprint._same(executable, state(0o744))
    assert fingerprint._fingerprint([entry]) != fingerprint._fingerprint([{**entry, "exec": "111"}])
    assert (fingerprint._scan_signature([("tests/helper.sh", plain)], [])
            != fingerprint._scan_signature([("tests/helper.sh", executable)], []))
    monkeypatch.setattr(fingerprint, "_EXEC_BITS", 0)  # Windows: synthesized bits are ignored
    assert fingerprint._same(plain, executable)
    assert (fingerprint._scan_signature([("tests/helper.sh", plain)], [])
            == fingerprint._scan_signature([("tests/helper.sh", executable)], []))


def _execute_bit_switch(monkeypatch, helper):
    """chmod 0755/0644 on POSIX. Windows has no execute bit, so there only the bit the
    fingerprint module reads for ``helper`` is switched; its bytes, size and mtime stay."""
    if os.name != "nt":
        return lambda on: helper.chmod(0o755 if on else 0o644)
    marker = 1_600_000_000_000_000_000
    os.utime(helper, ns=(marker, marker))
    state = {"on": False}
    monkeypatch.setattr(fingerprint, "_exec_bits",
                        lambda st: 0o111 if state["on"] and st.st_mtime_ns == marker else 0)
    return lambda on: state.update(on=on)


def test_a_helper_s_execute_bit_alone_changes_the_basis_and_the_gate(env, monkeypatch):
    """0684 T#2 rework 2: a PASS recorded with an executable helper; chmod 0644 (same
    bytes) makes it stale for the TSR gate and unchecked on display; chmod 0755 again
    makes the same PASS valid."""
    from modules.flow_gate.services import test_spec_service as spec
    helper = env.roots["g1"] / "tests" / "helper.sh"
    helper.write_text("#!/bin/sh\necho helper-ran\n")
    switch = _execute_bit_switch(monkeypatch, helper)
    switch(True)
    doc = env.ts()
    executable, _, _ = env.run_basis(doc)
    content = helper.read_bytes()
    switch(False)
    assert helper.read_bytes() == content
    hashed = _count_hashing(monkeypatch)
    shown = basis.verdict(env.doc(doc["doc_id"]), executable, memo=True)
    assert shown["state"] == basis.UNCHECKED and hashed == []  # the memo no longer fits
    passed = {"run_id": "r-pass", "overall": "PASS", "status": "passed", "created_at": "1",
              "contract_version": spec.CONTRACT_SPEC,
              "result_meta": json.dumps({"basis_id": executable["basis_id"],
                                         "test_basis": executable})}
    monkeypatch.setattr(runner.db_test_runs, "latest_by_doc", lambda _id: passed)
    monkeypatch.setattr(runner.db_test_runs, "get_running_by_doc", lambda _id: None)
    monkeypatch.setattr(runner.db_test_runs, "latest_spec_run", lambda *a: passed)
    monkeypatch.setattr(runner.db_test_runs, "latest_spec_result",
                        lambda _id, _rev, basis_id: passed if basis_id == executable["basis_id"] else None)
    tsr = {"target_id": doc["doc_id"], "doc_review_status": "pending_review"}
    gate = runner.tsr_gate_state(tsr)
    assert gate["passed"] is False and gate["overall"] == "NOT_RUN"
    assert gate["basis_state"] == basis.STALE and gate["basis_reasons"] == [basis.REASON_SOURCE]
    # The next run measures the non-executable helper: another Basis and a 0644 copy.
    plain, run_root, _ = env.run_basis(env.doc(doc["doc_id"]), "run-2")
    assert plain["basis_id"] != executable["basis_id"]
    if os.name != "nt":
        assert stat.S_IMODE((run_root / "tests" / "helper.sh").stat().st_mode) == 0o644
    switch(True)
    gate = runner.tsr_gate_state(tsr)
    assert gate["passed"] is True and gate["run_id"] == "r-pass"


@pytest.mark.skipif(os.name == "nt", reason="POSIX execute bit")
def test_a_chmod_during_the_copy_or_the_measure_is_a_source_change(env, monkeypatch):
    """0684 T#2 rework 2: a chmod of the helper while the run copies it refuses the run;
    a chmod after the Live Probe hashed it is caught by the closing scan."""
    root = env.roots["g1"]
    helper = root / "tests" / "helper.sh"
    helper.write_text("#!/bin/sh\necho helper-ran\n")
    helper.chmod(0o755)
    doc = env.ts()
    real = fingerprint._hash_file

    def chmod_while_copying(source, relative, expected_stat, deadline, out=None, **kwargs):
        if relative == "tests/helper.sh" and out is not None:
            helper.chmod(0o644)  # bytes, size and mtime unchanged
        return real(source, relative, expected_stat, deadline, out, **kwargs)
    monkeypatch.setattr(fingerprint, "_hash_file", chmod_while_copying)
    with pytest.raises(ValueError, match="basis_capture_failed:source_changed"):
        env.run_basis(doc, "run-chmod")
    assert not (env.tmp_path / "runs" / "run-chmod").exists()
    helper.chmod(0o755)

    def chmod_after_hashing(source, relative, expected_stat, deadline, out=None, **kwargs):
        if relative == "tests/test_a.py" and out is None:  # tests/helper.sh is hashed
            helper.chmod(0o644)
        return real(source, relative, expected_stat, deadline, out, **kwargs)
    monkeypatch.setattr(fingerprint, "_hash_file", chmod_after_hashing)
    with pytest.raises(fingerprint.SourceFingerprintError) as raised:
        fingerprint.measure(root.resolve())
    assert raised.value.code == "source_changed"


def test_basis_id_excludes_binding_and_includes_identity_fields(env):
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)
    identity = {k: run_basis[k] for k in ("basis_version", "ts_document_id", "ts_revision_no",
                                          "source", "test_assets", "execution_profile")}
    assert run_basis["basis_id"] == basis.canonical_hash(identity)
    assert set(run_basis["source"]) == {"kind", "exclusion_policy_version", "content_fingerprint"}
    assert run_basis["source"]["exclusion_policy_version"] == fingerprint.POLICY_VERSION
    assert run_basis["test_assets"]["policy_version"] == basis.ASSET_POLICY_VERSION
    assert set(run_basis["binding"]) >= {"git_revision", "source_dirty", "measured_at",
                                         "group_id", "run_id"}
    assert "bundle_id" not in run_basis["binding"]
    identity_row = basis.source_identity(run_basis)
    assert identity_row["content_fingerprint"] == run_basis["source"]["content_fingerprint"]
    assert "bundle_id" not in identity_row and "bundle_sha256" not in identity_row
    from modules.flow_gate.services import test_spec_service as spec
    assert spec.normalize_source_identity(identity_row) == identity_row
    # The same source measured by another run is the same Basis (binding aside).
    again, _, _ = env.run_basis(doc, "run-2")
    assert again["basis_id"] == run_basis["basis_id"]
    assert again["binding"]["run_id"] == "run-2"


# ── stale: one verdict for every change ───────────────────────────────────────

@pytest.mark.parametrize("change", ["modify", "add", "delete", "add_dir"])
def test_source_change_after_the_run_is_stale_and_the_next_run_measures_it(env, change):
    root = env.roots["g1"]
    doc = env.ts()
    run_basis, run_root, _ = env.run_basis(doc)
    if change == "modify":
        (root / "server" / "app.py").write_text("VALUE = 9\n")
    elif change == "add":
        (root / "server" / "extra.py").write_text("X = 1\n")
    elif change == "delete":
        (root / "server" / "app.py").unlink()
    else:
        (root / "server" / "empty_pkg").mkdir()
    judged = basis.verdict(env.doc(doc["doc_id"]), run_basis)
    assert judged["state"] == basis.STALE and judged["reasons"] == [basis.REASON_SOURCE]
    assert judged["basis_valid"] is False and judged["stale"] is True
    # The run root is the copy: a live change after the copy never reaches it.
    assert (run_root / "server" / "app.py").read_text() == "VALUE = 1\n"
    # [run again]: the next run measures the changed source and is valid for it.
    rerun, _, _ = env.run_basis(env.doc(doc["doc_id"]), "run-2")
    assert rerun["basis_id"] != run_basis["basis_id"]
    assert basis.verdict(env.doc(doc["doc_id"]), rerun)["state"] == basis.VALID


def test_asset_change_reports_source_and_manifest(env):
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)
    (env.roots["g1"] / "tests" / "fixture.json").write_text('{"v":2}\n')
    judged = basis.verdict(env.doc(doc["doc_id"]), run_basis)
    assert judged["reasons"] == [basis.REASON_SOURCE, basis.REASON_MANIFEST]


def test_ts_revision_and_policy_changes_have_their_own_reasons(env, monkeypatch):
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)
    env.store.docs[doc["doc_id"]]["revision_no"] = 2
    assert basis.verdict(env.doc(doc["doc_id"]), run_basis)["reasons"] == [basis.REASON_TS_REVISION]
    env.store.docs[doc["doc_id"]]["revision_no"] = 1
    current_policy = basis.ASSET_POLICY_VERSION
    monkeypatch.setattr(basis, "ASSET_POLICY_VERSION", current_policy + "-next")
    assert basis.verdict(env.doc(doc["doc_id"]), run_basis)["reasons"] == [basis.REASON_ASSET_POLICY]
    monkeypatch.setattr(basis, "ASSET_POLICY_VERSION", current_policy)
    monkeypatch.setattr(fingerprint, "POLICY_VERSION", "source-fingerprint-v2")
    assert basis.verdict(env.doc(doc["doc_id"]), run_basis)["reasons"] == [basis.REASON_BUNDLE_POLICY]


def test_commit_with_same_content_is_not_stale(env):
    root = env.roots["g1"]
    (root / "server" / "app.py").write_text("VALUE = 2\n")
    (root / "server" / "new_module.py").write_text("NEW = True\n")
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)
    git(root, "add", "server")
    git(root, "commit", "-m", "TR commit point")
    judged = basis.verdict(env.doc(doc["doc_id"]), run_basis)
    assert judged["state"] == basis.VALID
    rerun, _, _ = env.run_basis(env.doc(doc["doc_id"]), "run-2")
    assert rerun["basis_id"] == run_basis["basis_id"]  # git revision is display, not identity
    assert rerun["binding"]["git_revision"] != run_basis["binding"]["git_revision"]


def test_unmeasurable_source_is_unverifiable_not_stale(env):
    import shutil
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)
    shutil.rmtree(env.roots["g1"], ignore_errors=True)
    env.roots.pop("g1")
    judged = basis.verdict(env.doc(doc["doc_id"]), run_basis)
    assert judged["state"] == basis.UNVERIFIABLE
    assert judged["reasons"] == ["source_unmeasurable:group_worktree_unavailable"]
    error = basis.verdict_error(judged, runner._http_error, doc_id=doc["doc_id"])
    assert error.status_code == 409 and error.detail["error"] == "basis_unavailable"
    with pytest.raises(ValueError, match="basis_capture_failed:group_worktree_unavailable"):
        env.run_basis(env.doc(doc["doc_id"]), "run-2")
    assert not (env.tmp_path / "runs" / "run-2").exists()
    # A record made without a Basis (a TS with no Group worktree) stands as recorded.
    assert basis.verdict(env.doc(doc["doc_id"]), None)["state"] == basis.VALID


# ── display paths: the memo never hashes (D#1 §3-6) ───────────────────────────

def test_display_memo_never_hashes_and_unchecked_is_not_stale(env, monkeypatch):
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)  # the run's copy is a measurement: it leaves a memo
    hashed = _count_hashing(monkeypatch)
    for _ in range(3):
        assert basis.verdict(env.doc(doc["doc_id"]), run_basis, memo=True)["state"] == basis.VALID
    assert hashed == []  # unchanged scan: memo hit, nothing hashed
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 3\n")
    shown = basis.verdict(env.doc(doc["doc_id"]), run_basis, memo=True)
    assert shown["state"] == basis.UNCHECKED and shown["reasons"] == [basis.REASON_NOT_MEASURED]
    assert hashed == []  # a changed scan is "not measured", never a measurement
    # Decision paths always measure; their result is the next memo.
    assert basis.verdict(env.doc(doc["doc_id"]), run_basis)["state"] == basis.STALE
    measured = len(hashed)
    assert measured > 0
    assert basis.verdict(env.doc(doc["doc_id"]), run_basis, memo=True)["state"] == basis.STALE
    assert len(hashed) == measured


def test_display_cost_does_not_grow_with_requests_or_source_size(env, monkeypatch):
    root = env.roots["g1"]
    for index in range(200):
        (root / "server" / f"bulk_{index}.py").write_text(f"X = {index}\n" * 50)
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)
    hashed = _count_hashing(monkeypatch)
    measures = []
    real_measure = fingerprint.measure
    monkeypatch.setattr(fingerprint, "measure", lambda r: measures.append(r) or real_measure(r))
    started = time.monotonic()
    for _ in range(20):
        basis.verdict(env.doc(doc["doc_id"]), run_basis, memo=True)
    elapsed = time.monotonic() - started
    assert hashed == [] and measures == []
    assert elapsed < 10  # stat-only scans; a full hash per request is what this forbids


# ── Group isolation ───────────────────────────────────────────────────────────

def test_other_group_changes_never_reach_this_basis_or_run_root(env):
    env.repo("g2", value="200")
    first = env.ts("g1", 10)
    other = env.ts("g2", 20)
    first_basis, first_root, _ = env.run_basis(first, "run-g1")
    other_basis, other_root, _ = env.run_basis(other, "run-g2")
    assert first_basis["basis_id"] != other_basis["basis_id"]
    assert first_basis["binding"]["group_id"] == "g1" and other_basis["binding"]["group_id"] == "g2"
    (env.roots["g2"] / "server" / "app.py").write_text("VALUE = 201\n")
    (env.roots["g2"] / "server" / "only_g2.py").write_text("G2 = 1\n")
    assert basis.verdict(env.doc(first["doc_id"]), first_basis)["state"] == basis.VALID
    assert basis.verdict(env.doc(first["doc_id"]), first_basis, memo=True)["state"] == basis.VALID
    assert basis.verdict(env.doc(other["doc_id"]), other_basis)["state"] == basis.STALE
    assert (first_root / "server" / "app.py").read_text() == "VALUE = 1\n"
    assert not (first_root / "server" / "only_g2.py").exists()
    assert (other_root / "server" / "app.py").read_text() == "VALUE = 200\n"
    # One Group's result never judges against another Group's source.
    assert basis.verdict(env.doc(first["doc_id"]), other_basis)["state"] == basis.STALE


# ── concurrent change and the source lock ─────────────────────────────────────

def test_a_change_during_the_copy_refuses_the_run_and_leaves_nothing(env, monkeypatch):
    doc = env.ts()
    real = fingerprint._hash_file
    target = env.roots["g1"] / "server" / "app.py"

    def racing(root, relative, expected_stat, deadline, out=None, **kwargs):
        if relative == "server/app.py" and out is not None:
            target.write_text("VALUE = 77  # changed during the copy\n")
        return real(root, relative, expected_stat, deadline, out, **kwargs)
    monkeypatch.setattr(fingerprint, "_hash_file", racing)
    before = env.doc(doc["doc_id"])
    with pytest.raises(ValueError, match="basis_capture_failed:source_changed"):
        env.run_basis(doc, "run-race")
    assert not (env.tmp_path / "runs" / "run-race").exists()
    assert env.doc(doc["doc_id"]) == before and env.store.events == []


def test_the_copy_holds_the_group_source_lock_and_a_busy_source_refuses(env, monkeypatch):
    held = []
    stub_group_lock(monkeypatch, held=held)
    seen = []
    real = fingerprint.copy_measured
    monkeypatch.setattr(fingerprint, "copy_measured",
                        lambda root, target: seen.append(list(held)) or real(root, target))
    doc = env.ts()
    env.run_basis(doc)
    assert seen == [["run_prepare"]] and held == []  # held for the copy only
    stub_group_lock(monkeypatch, grant=False)
    with pytest.raises(ValueError, match="basis_capture_failed:source_busy"):
        env.run_basis(doc, "run-busy")
    assert not (env.tmp_path / "runs" / "run-busy").exists()


def test_run_prepare_lock_is_a_long_g_hold_stored_as_a_listed_kind():
    from modules.flow_gate.services.git import lock_manager
    assert lock_manager.stored_holder_kind("run_prepare") in lock_manager.STORED_HOLDER_KINDS
    assert lock_manager.default_hold_class("G", "run_prepare") == "long"
    assert lock_manager.wait_budget("G", "run_prepare") >= 0


def test_ts_changed_while_queued_refuses_before_copying(env):
    doc = env.ts()
    env.store.docs[doc["doc_id"]]["revision_no"] = 2
    with pytest.raises(ValueError, match="ts_changed_before_execution"):
        execution.ExecutionRootResolver.prepare(doc, {"run_id": "run-q", "revision_no": 1},
                                                env.cases)
    assert not (env.tmp_path / "runs" / "run-q").exists()


def test_excluded_asset_is_refused_as_not_captured(env):
    root = env.roots["g1"]
    (root / "tests" / "secrets.json").write_text("{}\n")
    git(root, "add", "-f", "tests/secrets.json")
    git(root, "commit", "-m", "secret-named fixture")
    env.cases = [{**CASES[0], "test_assets": "tests/secrets.json"}]
    with pytest.raises(ValueError, match="test_asset_not_captured: tests/secrets.json"):
        env.run_basis(env.ts())


def test_product_path_cannot_enter_manifest(env):
    env.cases = [{**CASES[0], "test_assets": "server/app.py"}]
    with pytest.raises(ValueError, match="product_source_or_invalid_test_asset"):
        env.run_basis(env.ts())


def test_missing_automated_test_file_is_refused_before_execution(env):
    (env.roots["g1"] / "tests" / "test_a.py").unlink()
    with pytest.raises(ValueError, match="automation_asset_missing: tests/test_a.py"):
        env.run_basis(env.ts(), "run-missing")
    assert not (env.tmp_path / "runs" / "run-missing").exists()


# ── the shared process layer runs the Case in the copy ────────────────────────

def test_case_runs_in_the_copy_through_the_shared_process_layer(env):
    doc = env.ts()
    (env.roots["g1"] / "tests" / "test_a.py").write_text(
        "from pathlib import Path\n\n\ndef test_a():\n"
        "    assert Path('server/app.py').read_text() == 'VALUE = 1\\n'\n")
    _, run_root, scratch = env.run_basis(doc, "run-exec")
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 2\n")  # live, after the copy
    active = runner._register_active_run("run-exec")
    try:
        result, xml, exit_code, _output = execution.ExistingRunnerAdapter.run_pytest(
            "tests/test_a.py::test_a", run_root, scratch, active)
    finally:
        runner._unregister_active_run("run-exec")
    assert (result, exit_code) == ("pass", 0)
    assert 'name="test_a"' in xml and "<failure" not in xml
    assert active.control is None


def test_cancel_reaches_the_case_process_through_the_run_handle(env):
    doc = env.ts()
    (env.roots["g1"] / "tests" / "test_a.py").write_text(
        "import time\n\n\ndef test_a():\n    time.sleep(60)\n")
    _, run_root, scratch = env.run_basis(doc, "run-cancel")
    active = runner._register_active_run("run-cancel")
    outcome = {}

    def run():
        outcome["value"] = execution.ExistingRunnerAdapter.run_pytest(
            "tests/test_a.py::test_a", run_root, scratch, active)
    worker = threading.Thread(target=run)
    started = time.monotonic()
    worker.start()
    deadline = time.monotonic() + 30
    while active.control is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert active.control is not None
    time.sleep(1)
    # What request_cancel does with a live spec run: set the event, cancel the control.
    with active.lock:
        active.cancel_event.set()
        control = active.control
    control.cancel()
    worker.join(timeout=30)
    runner._unregister_active_run("run-cancel")
    assert not worker.is_alive()
    assert time.monotonic() - started < 40
    assert outcome["value"][0] != "pass"


# ── approval queues the run; it measures nothing ──────────────────────────────

def _v2_basis(doc_id, tag="2"):
    return {"basis_id": tag * 64, "basis_version": 2, "ts_document_id": doc_id,
            "ts_revision_no": 1,
            "source": {"kind": "source_bundle", "exclusion_policy_version": "source-bundle-v1",
                       "content_fingerprint": tag * 64},
            "test_assets": {"policy_version": "test-asset-v2", "manifest_hash": tag * 64,
                            "asset_count": 2},
            "execution_profile": {"runner_generation": 1}, "manifest": [],
            "binding": {"bundle_id": "sb_" + "a" * 32, "bundle_sha256": tag * 64,
                        "git_revision": "a" * 40, "source_dirty": False}}


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


def _measuring_forbidden(monkeypatch, why):
    monkeypatch.setattr(fingerprint, "copy_measured", lambda *a, **kw: pytest.fail(why))
    monkeypatch.setattr(fingerprint, "measure", lambda *a, **kw: pytest.fail(why))
    monkeypatch.setattr(fingerprint, "_hash_file", lambda *a, **kw: pytest.fail(why))


def test_pipeline_approval_queues_the_run_without_measuring(env, monkeypatch):
    # A dirty worktree and a Basis an earlier implementation stored on the TS: approval
    # measures nothing, sets the old Basis aside and queues the run.
    (env.roots["g1"] / "server" / "app.py").write_text("VALUE = 5\n")
    doc = env.ts()
    earlier = _v2_basis(doc["doc_id"])
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review",
                                          "type_code": "TS", "id": 1,
                                          "meta": json.dumps({"test_basis": earlier})})
    _measuring_forbidden(monkeypatch, "approval must not measure")
    result = _approve_through_pipeline(env, monkeypatch, doc["doc_id"])
    assert result["doc_review_status"] == "approved"
    assert result["spec_execution"] == {"run_id": "run-approval", "tsr_doc_id": "tsr",
                                        "selected_case_ids": ["TC-001"], "status": "queued"}
    assert env.admitted[0]["case_ids"] == ["TC-001"]
    stored = json.loads(env.doc(doc["doc_id"])["meta"])
    assert "test_basis" not in stored
    assert stored["superseded_test_basis"]["basis_id"] == earlier["basis_id"]
    assert [e[1] for e in env.store.events] == ["test_spec_execution_admitted"]


def test_approval_precheck_measures_nothing(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    (env.roots["g1"] / "server" / "untracked.py").write_text("U = 1\n")
    doc = env.ts()
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review",
                                          "type_code": "TS", "id": 1})
    _approve_through_pipeline(env, monkeypatch, doc["doc_id"])  # installs the guards
    env.store.docs[doc["doc_id"]].update({"doc_review_status": "pending_review", "meta": "{}"})
    env.admitted.clear()
    _measuring_forbidden(monkeypatch, "precheck must not measure")
    checked = pipeline.precheck_document_review_transition(
        doc_id=doc["doc_id"], action="approve", actor_user_id="u",
        user_permissions={"document.approve"})
    assert checked["next_status"] == "approved"
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


# ── v1/v2 Basis (D#1 §7) ──────────────────────────────────────────────────────

def _v1_basis(doc_id):
    return {"basis_id": "1" * 64, "basis_version": 1, "ts_document_id": doc_id,
            "ts_revision_no": 1,
            "source": {"kind": "compat_worktree", "git_revision": "a" * 40, "tree": "b" * 40},
            "test_assets": {"manifest_hash": "c" * 64, "asset_count": 2},
            "execution_profile": {"runner_generation": 1}, "manifest": []}


@pytest.mark.parametrize("old", ["v1", "v2"])
def test_old_basis_is_stale_without_measuring_and_an_approved_tsr_stands(env, monkeypatch, old):
    doc = env.ts()
    previous = _v1_basis(doc["doc_id"]) if old == "v1" else _v2_basis(doc["doc_id"])
    hashed = _count_hashing(monkeypatch)
    judged = basis.verdict(env.doc(doc["doc_id"]), previous)
    assert judged["state"] == basis.STALE and judged["reasons"] == [basis.REASON_BASIS_OUTDATED]
    assert hashed == []
    from modules.flow_gate.services import test_spec_service as spec
    record = {"run_id": "r1", "overall": "PASS", "status": "passed", "created_at": "1",
              "contract_version": spec.CONTRACT_SPEC,
              "result_meta": json.dumps({"basis_id": previous["basis_id"], "test_basis": previous})}
    monkeypatch.setattr(runner.db_test_runs, "latest_by_doc", lambda _id: record)
    monkeypatch.setattr(runner.db_test_runs, "get_running_by_doc", lambda _id: None)
    monkeypatch.setattr(runner.db_test_runs, "latest_spec_run", lambda *a: record)
    monkeypatch.setattr(runner.db_test_runs, "latest_spec_result",
                        lambda _id, _rev, basis_id: record if basis_id == previous["basis_id"] else None)
    approved = runner.tsr_gate_state({"target_id": doc["doc_id"], "doc_review_status": "approved"})
    assert approved["passed"] is True and approved["stale"] is False
    pending = runner.tsr_gate_state({"target_id": doc["doc_id"],
                                     "doc_review_status": "pending_review"})
    assert pending["passed"] is False and pending["overall"] == "NOT_RUN"
    assert pending["basis_state"] == basis.STALE
    assert pending["basis_reasons"] == [basis.REASON_BASIS_OUTDATED]


def test_a_newer_failure_on_another_source_blocks_the_result_when_the_source_returns(
        env, monkeypatch):
    """D#1 §3-5, 0684 T#2 rework: PASS on A, execution failure on B, source back to A.

    The PASS is the valid result for A again, but the newer failed execution (whatever
    source it ran on, or none for a prepare refusal) still blocks the gate."""
    from modules.flow_gate.services import test_spec_service as spec
    root = env.roots["g1"]
    doc = env.ts()
    on_a, _, _ = env.run_basis(doc)
    (root / "server" / "app.py").write_text("VALUE = 9\n")
    on_b, _, _ = env.run_basis(env.doc(doc["doc_id"]), "run-2")
    assert on_b["basis_id"] != on_a["basis_id"]
    (root / "server" / "app.py").write_text("VALUE = 1\n")         # back to A
    passed = {"run_id": "r-pass", "overall": "PASS", "status": "passed", "created_at": "1",
              "contract_version": spec.CONTRACT_SPEC,
              "result_meta": json.dumps({"basis_id": on_a["basis_id"], "test_basis": on_a})}
    failed_on_b = {"run_id": "r-fail", "overall": "NOT_RUN", "status": "failed",
                   "created_at": "2", "contract_version": spec.CONTRACT_SPEC,
                   "error": "spec_required_not_run",
                   "result_meta": json.dumps({"run_kind": "spec_execution",
                                              "basis_id": on_b["basis_id"]})}
    refused = {**failed_on_b, "run_id": "r-refused", "error": "basis_capture_failed:source_busy",
               "result_meta": json.dumps({"run_kind": "spec_execution"})}
    newest = {"row": failed_on_b}
    monkeypatch.setattr(runner.db_test_runs, "latest_by_doc", lambda _id: newest["row"])
    monkeypatch.setattr(runner.db_test_runs, "get_running_by_doc", lambda _id: None)
    monkeypatch.setattr(runner.db_test_runs, "latest_spec_run", lambda *a: passed)
    monkeypatch.setattr(runner.db_test_runs, "latest_spec_result",
                        lambda _id, _rev, basis_id: passed if basis_id == on_a["basis_id"] else None)
    tsr = {"target_id": doc["doc_id"], "doc_review_status": "pending_review"}
    gate = runner.tsr_gate_state(tsr)
    assert gate["passed"] is False and gate["overall"] == "BLOCKED"
    assert gate["run_id"] == "r-fail" and gate["basis_id"] == on_a["basis_id"]
    newest["row"] = refused                                         # a prepare refusal
    gate = runner.tsr_gate_state(tsr)
    assert gate["overall"] == "BLOCKED" and gate["run_id"] == "r-refused"
    newest["row"] = passed                                          # no newer failure
    gate = runner.tsr_gate_state(tsr)
    assert gate["passed"] is True and gate["run_id"] == "r-pass"


# ── test asset edit: bytes only, results stale by identity (D#1 §3-7) ─────────

def _asset_edit_env(env, monkeypatch):
    monkeypatch.setattr(assets, "_load", lambda ts_id: (env.doc(ts_id), {"cases": env.cases}))
    monkeypatch.setattr(assets.test_run_service, "_active_tsr_for_ts", lambda d: None)
    monkeypatch.setattr(assets.db_test_runs, "get_pending_failure_origin", lambda ts_id: None)
    monkeypatch.setattr(assets.db_test_runs, "latest_spec_run", lambda *a: None)
    monkeypatch.setattr(assets.db_test_runs, "get_running_by_doc", lambda ts_id: None)
    monkeypatch.setattr(basis, "source_root", lambda d: env.roots[d["group_id"]])


def test_asset_edit_swaps_bytes_and_makes_earlier_results_stale(env, monkeypatch):
    doc = env.ts()
    run_basis, _, _ = env.run_basis(doc)
    _asset_edit_env(env, monkeypatch)
    measured = []
    monkeypatch.setattr(fingerprint, "copy_measured", lambda *a: measured.append(a))
    path = "tests/fixture.json"
    old_hash = hashlib.sha256((env.roots["g1"] / path).read_bytes()).hexdigest()
    response = assets.update(doc["doc_id"], path, expected_hash=old_hash,
                             content='{"v":2}\n', actor_id="u")
    assert (env.roots["g1"] / path).read_text() == '{"v":2}\n'
    assert response["content_hash"] == hashlib.sha256(b'{"v":2}\n').hexdigest()
    assert response["results_stale"] is True and "basis_id" not in response
    assert [e[1] for e in env.store.events] == ["test_spec_asset_updated"]
    assert measured == []  # no capture, no successor, no run
    assert "test_basis" not in json.loads(env.doc(doc["doc_id"])["meta"])
    judged = basis.verdict(env.doc(doc["doc_id"]), run_basis)
    assert judged["state"] == basis.STALE
    assert judged["reasons"] == [basis.REASON_SOURCE, basis.REASON_MANIFEST]


def test_asset_read_hashes_one_file_and_cas_mismatch_keeps_bytes(env, monkeypatch):
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)
    hashed = _count_hashing(monkeypatch)
    read = assets.content(doc["doc_id"], "tests/fixture.json")
    raw = (env.roots["g1"] / "tests" / "fixture.json").read_bytes()
    assert read["content"].encode("utf-8") == raw and hashed == []
    assert read["content_hash"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], "tests/fixture.json", expected_hash="0" * 64,
                      content='{"v":3}\n', actor_id="u")
    assert refused.value.detail["error"] == "expected_hash_mismatch"
    assert (env.roots["g1"] / "tests" / "fixture.json").read_text() == '{"v":1}\n'
    with pytest.raises(HTTPException) as outside:
        assets.content(doc["doc_id"], "server/app.py")
    assert outside.value.status_code == 403


def test_asset_edit_refused_while_a_run_is_in_progress(env, monkeypatch):
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)
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


def test_asset_edit_event_failure_restores_the_bytes(env, monkeypatch):
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)
    monkeypatch.setattr(assets.db_events, "insert_event",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("event failed")))
    path = env.roots["g1"] / "tests" / "fixture.json"
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="event failed"):
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":3}\n', actor_id="u")
    assert path.read_bytes() == before
    assert env.store.events == []


@pytest.mark.parametrize("failing", ["fsync", "replace"])
def test_asset_write_failure_keeps_old_bytes(env, monkeypatch, failing):
    # A write that fails part-way (staged bytes written, then an I/O error) never reaches
    # the asset: the old bytes stay and nothing is left beside it.
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)

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
    assert env.store.events == []


def test_asset_bytes_changed_while_waiting_for_the_group_lock_are_refused(env, monkeypatch):
    from modules.flow_gate.services.git import lock_manager
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)
    real_acquire = lock_manager.acquire_group
    path = env.roots["g1"] / "tests" / "fixture.json"

    def asset_changes_while_waiting(*a, **kw):
        path.write_text('{"v":"other editor"}\n')
        return real_acquire(*a, **kw)
    monkeypatch.setattr(lock_manager, "acquire_group", asset_changes_while_waiting)
    before = path.read_bytes()
    with pytest.raises(HTTPException) as refused:
        assets.update(doc["doc_id"], "tests/fixture.json",
                      expected_hash=hashlib.sha256(before).hexdigest(),
                      content='{"v":6}\n', actor_id="u")
    assert refused.value.status_code == 409
    assert refused.value.detail["error"] == "expected_hash_mismatch"
    assert path.read_text() == '{"v":"other editor"}\n'
    assert env.store.events == []


def test_asset_edit_holds_the_source_lock_as_a_source_mutation(env, monkeypatch):
    held = []
    stub_group_lock(monkeypatch, held=held)
    doc = env.ts()
    _asset_edit_env(env, monkeypatch)
    seen = []
    real = assets._replace_bytes
    monkeypatch.setattr(assets, "_replace_bytes",
                        lambda target, data: seen.append(list(held)) or real(target, data))
    path = env.roots["g1"] / "tests" / "fixture.json"
    assets.update(doc["doc_id"], "tests/fixture.json",
                  expected_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                  content='{"v":8}\n', actor_id="u")
    assert seen == [["source_mutation"]] and held == []


def test_run_root_never_writes_into_the_worktree(env):
    doc = env.ts()
    before = _files(env.roots["g1"])
    _, run_root, scratch = env.run_basis(doc)
    (run_root / "tests" / "artifact.log").write_text("test by-product\n")
    assert _files(env.roots["g1"]) == before
    assert Path(scratch).is_relative_to(env.tmp_path / "runs")
