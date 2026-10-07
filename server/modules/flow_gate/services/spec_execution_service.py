"""Contract-2 execution orchestration on the existing test-run worker.

0684 T#1 (D#1 §3-1~§3-4): a run is admitted without asking whether a stored Basis still
matches the source -- by TS approval (inside the approval transaction), by [run again] or
by a single Case.

0684 T#2 (D#1 §3-3): the run measures what it executes. Its preparing phase copies the
Group worktree into a disposable run root while fingerprinting the copied bytes (one read,
under the Group's source lock), and that Basis is kept on the run -- no Source Bundle, no
Basis on the TS. Cases execute in the copy through the shared process layer
(tr_self_check_executor). Finalizing records the result under the executed Basis and
measures the live source once to decide whether the result counts for it.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import test_basis_service, test_spec_service


class AutomationRefResolver:
    resolve = staticmethod(test_basis_service.locator)


RUN_KIND = "spec_execution"
ADMISSION_APPROVAL = "approval"
ADMISSION_REQUEST = "request"

# Run phases (D#1 §3-3), kept in result_meta.phase; "queued" is a running row not yet picked.
PHASE_QUEUED = "queued"
PHASE_PREPARING = "preparing"
PHASE_EXECUTING = "executing"
PHASE_FINALIZING = "finalizing"
PHASE_FINISHED = "finished"


def automated_selection(cases: list[dict]) -> list[dict]:
    """Every automated Case the server runner can execute (Case or suite locator)."""
    return [case for case in cases if case.get("execution_mode") == "automated"
            and AutomationRefResolver.resolve(case)["capability"] in
            {"case_selectable", "suite_only"}]


def _select(all_cases: list[dict], case_id: str | None, doc_id: str) -> list[dict]:
    from modules.flow_gate.services import test_run_service as runner
    if case_id is not None:
        selected = [case for case in all_cases
                    if test_spec_service.case_id_key(case["case_id"]) ==
                    test_spec_service.case_id_key(case_id)]
        if not selected:
            raise runner._http_error(404, "case_not_found", case_id=case_id)
        capability = AutomationRefResolver.resolve(selected[0])["capability"]
        if capability != "case_selectable":
            raise runner._http_error(422, "case_not_selectable", case_id=case_id,
                                     capability=capability)
        return selected
    selected = automated_selection(all_cases)
    if not selected:
        raise runner._http_error(422, "no_automated_bindings", doc_id=doc_id)
    return selected


def check_automation_assets(doc: dict, selected: list[dict]) -> None:
    """Approval's light asset check (D#1 §3-1 step 4): exists and is safe, nothing hashed.

    Raises ``ValueError("<code>: <case_id>: <path>")`` for the first Case whose test file
    is missing or unsafe in the Group worktree.
    """
    root = test_basis_service.source_root(doc)
    for case in selected:
        path = AutomationRefResolver.resolve(case).get("path")
        if not path:
            continue
        try:
            test_basis_service.safe_asset_file(root, path)
        except ValueError as exc:
            raise ValueError(f"{exc}: {case['case_id']}: {path}") from exc


def admission_blocker(doc_id: str) -> dict | None:
    """The run that keeps a new run out: one in progress, or a FAIL awaiting its origin."""
    running = db_test_runs.get_running_by_doc(doc_id)
    if running:
        return {"error": "run_in_progress", "run_id": running["run_id"]}
    pending = db_test_runs.get_pending_failure_origin(doc_id)
    if pending:
        return {"error": "failure_origin_pending", "run_id": pending["run_id"]}
    return None


def _insert_run(doc: dict, all_cases: list[dict], selected: list[dict], *, runner_id: str,
                locale: str, triggered_via: str, admission: str,
                chain_context: dict | None) -> dict:
    """Caller holds the admission lock and has checked ``admission_blocker``."""
    selected_ids = [case["case_id"] for case in selected]
    return db_test_runs.insert_run(
        doc_id=doc["doc_id"], revision_no=doc.get("revision_no") or 0,
        triggered_via=triggered_via, runner_id=runner_id,
        cases=[{"kind": "case", "case_no": case["case_id"],
                "title": case["title"], "cmd": "",
                "expect": case.get("expected") or ""} for case in selected],
        locale=locale, contract_version=2,
        result_meta=json.dumps({"run_kind": RUN_KIND, "admission": admission,
                                "phase": PHASE_QUEUED, "basis_id": None, "test_basis": None,
                                "selected_case_ids": selected_ids, "spec_cases": all_cases,
                                "chain": chain_context}, ensure_ascii=False),
    )


def merge_run_meta(run_id: str, **updates) -> dict:
    """Read-modify-write of one run's result_meta under the admission lock.

    The worker, a chain attaching to the run and the report bookkeeping all write here;
    merging keeps one writer from dropping another's keys (the chain context above all).
    """
    from modules.flow_gate.services import test_run_service as runner
    with runner._admission_lock:
        current = db_test_runs.get_run(run_id) or {}
        meta = test_spec_service.load_result_meta(current.get("result_meta"))
        meta.update(updates)
        db_test_runs.set_run_result_meta(run_id, json.dumps(meta, ensure_ascii=False))
    return meta


def admit_on_approval(doc: dict, parsed: dict, selected: list[dict], *, runner_id: str,
                      locale: str) -> dict:
    """Queue the approved TS's automated Cases and open its report (D#1 §3-1 step 5).

    Called inside the approval's DB transaction and admission lock, after the TS row was
    updated: a failure here rolls the approval back with it, so there is never an approved
    TS without its run or a run without its approval. Nothing here touches the source.
    """
    from modules.flow_gate.services import test_run_service as runner
    run = _insert_run(doc, parsed["cases"], selected, runner_id=runner_id, locale=locale,
                      triggered_via="ui", admission=ADMISSION_APPROVAL, chain_context=None)
    tsr_doc_id = runner.open_execution_tsr(doc, parsed["cases"], run, locale=locale)
    return {"run": db_test_runs.get_run(run["run_id"]) or run, "tsr_doc_id": tsr_doc_id,
            "selected_case_ids": [case["case_id"] for case in selected]}


ANY_RUN = object()


def attach_chain(doc: dict, chain_context: dict, *, run_id=ANY_RUN) -> dict | None:
    """Hand an unmanned chain to the server run its TS approval already started.

    0684 T#1: the chain reaches the TSR head after the approval queued a run. Instead of
    refusing ``run_in_progress`` the chain rides that run (its context goes into the run's
    meta, so the recorded result carries it). A run that already PASSed the gate for this
    revision is attached as ``finished``. Anything else is not attachable: a new run starts.

    ``run_id`` narrows the run in progress the chain may ride: by default any; a run id
    rides only that run (the caller holds its run lock); None rides none.
    """
    from modules.flow_gate.services import test_run_service as runner
    doc_id = doc["doc_id"]
    with runner._admission_lock:
        running = db_test_runs.get_running_by_doc(doc_id)
        if running and run_id is not ANY_RUN and running["run_id"] != run_id:
            running = None
        if running and running.get("contract_version") == test_spec_service.CONTRACT_SPEC:
            meta = merge_run_meta(running["run_id"], chain=chain_context)
            return {**runner._run_response(running), "attached": True,
                    "basis_id": meta.get("basis_id"),
                    "selected_case_ids": meta.get("selected_case_ids") or []}
    latest = db_test_runs.latest_by_doc(doc_id)
    if (latest and latest.get("contract_version") == test_spec_service.CONTRACT_SPEC
            and latest.get("status") == "passed" and latest.get("overall") == "PASS"
            and (latest.get("revision_no") or 0) == (doc.get("revision_no") or 0)):
        tsr = runner._active_tsr_for_ts(doc)
        if tsr and runner.tsr_gate_state(tsr).get("passed"):
            meta = test_spec_service.load_result_meta(latest.get("result_meta"))
            return {**runner._run_response(latest), "attached": True, "finished": True,
                    "tsr_doc_id": tsr["doc_id"], "basis_id": meta.get("basis_id"),
                    "selected_case_ids": []}
    return None


_attach_chain = attach_chain


def _stop_chain_without_result(doc: dict, run_id: str) -> None:
    """A run that ended without recording a result still answers its chain (D#1 §3-9).

    A prepare refusal or an execution error records no result, so the result path never
    reaches the test gate. A chain riding this run would wait forever: tell the human why it
    stopped and let the gate relabel the parked chain as blocked.
    """
    from modules.flow_gate.services import test_run_service as runner
    finished = db_test_runs.get_run(run_id) or {"run_id": run_id}
    if not test_spec_service.load_result_meta(finished.get("result_meta")).get("chain"):
        return
    try:
        runner._maybe_notify_chain_failure(doc, {**finished, "triggered_via": "token"})
        runner.continue_chain_after_test_gate(doc, None)
    except Exception:  # noqa: BLE001 -- the run's terminal state is already written
        runner.logger.warning("chain stop after %s failed", run_id, exc_info=True)


def admit(doc_id: str, *, case_id: str | None, runner_id: str, locale: str,
          triggered_via: str = "ui", chain_context: dict | None = None) -> dict:
    """[Run again] / Case run / chain hand-off (D#1 §3-2).

    The stored Basis is not asked: the run measures the source when it prepares. The
    remaining conditions are the existing ones -- approved TS, report not approved, no run
    in progress, no FAIL awaiting its origin review.
    """
    from modules.flow_gate.services import test_run_service as runner
    doc, parsed = runner.load_spec_ts(doc_id)
    runner._require_ts_admissible(doc, doc_id)
    paired = runner._active_tsr_for_ts(doc)
    if paired and paired.get("doc_review_status") == "approved":
        raise runner._http_error(409, "tsr_already_approved", doc_id=doc_id)
    if chain_context is not None and case_id is None:
        attached = attach_chain(doc, chain_context)
        if attached is not None:
            return attached
    all_cases = parsed["cases"]
    selected = _select(all_cases, case_id, doc_id)
    with runner._admission_lock:
        blocker = admission_blocker(doc_id)
        if blocker:
            raise runner._http_error(409, blocker["error"], run_id=blocker["run_id"])
        run = _insert_run(doc, all_cases, selected, runner_id=runner_id, locale=locale,
                          triggered_via=triggered_via, admission=ADMISSION_REQUEST,
                          chain_context=chain_context)
        try:
            runner.open_execution_tsr(doc, all_cases, run, locale=locale)
        except Exception:  # noqa: BLE001 -- the run still records; finalize assembles the report
            runner.logger.warning("opening the report for %s failed", run["run_id"], exc_info=True)
    runner._emit_started(doc, run)
    return {**runner._run_response(run), "basis_id": None,
            "selected_case_ids": [case["case_id"] for case in selected]}


class ExecutionRootResolver:
    @staticmethod
    def prepare(doc: dict, run: dict, cases: list[dict]) -> tuple[dict, Path, Path]:
        """Run root and run Basis in one read (0684 T#2, D#1 §3-3 preparing).

        Under this Group's source lock (holder ``run_prepare``; writes of the same Group
        wait, other Groups do not) the worktree is copied into the run's disposable root
        while every copied byte is hashed. The fingerprint of those bytes is the run's
        Basis: what runs is exactly what was measured, uncommitted and untracked work
        included, and a live change after the copy never reaches the run. The lock covers
        the copy only, never the execution. Nothing is stored on the TS; the caller keeps
        the Basis on the run. Refusals are ``ValueError("<code>: <detail>")``.
        """
        from modules.flow_gate.services.git import lock_manager
        from modules.flow_gate.services import test_run_service as runner
        revision = run.get("revision_no") or 0
        current_doc = db_docs.get_by_id(doc["doc_id"]) or doc
        if (current_doc.get("revision_no") or 0) != revision:
            raise ValueError("ts_changed_before_execution")
        scratch = runner._scratch_dir(doc, run["run_id"])
        root = scratch / "source"
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)  # a retried pickup starts clean
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            project_id, group_id = current_doc.get("project_id"), current_doc.get("group_id")
            lock = None
            if project_id and group_id:
                outcome, ctx = lock_manager.acquire_group(project_id, group_id,
                                                          holder_kind="run_prepare",
                                                          mode="run_prepare")
                if not outcome.ok:
                    raise ValueError("basis_capture_failed:source_busy")
                lock = (ctx, outcome.lock_key)
            try:
                basis = test_basis_service.measure_run(current_doc, cases, root,
                                                       run_id=run["run_id"])
            finally:
                if lock is not None:
                    lock_manager.release(*lock)
        except Exception:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        return basis, root, scratch


# Prepare refusals end the run under their own error, pytest never started: the run could
# not copy and measure its source (source busy or changed mid-copy, a limit, an automated
# test file missing or not an asset) or the TS changed while queued. None of them is a
# FAIL (D#1 §3-3 step 6).
_PREPARE_REFUSALS = {"test_asset_not_captured", "basis_capture_failed",
                     "automation_asset_missing", "product_source_or_invalid_test_asset",
                     "test_asset_path_case_mismatch", "ts_changed_before_execution",
                     "source_root_missing"}


def _python(env: dict) -> str:
    return shutil.which("python", path=env.get("PATH")) or sys.executable


class ExistingRunnerAdapter:
    @staticmethod
    def run_pytest(nodeid: str, root: Path, scratch: Path, active) -> tuple[str, str, int | None, str]:
        """One pytest node in the run root, through the shared execution layer (D#1 §3-3).

        Process ownership, timeout, cancel and output tails are tr_self_check_executor's
        (the layer TR Self-check and Chat use); the command, its environment and what the
        result means stay the spec run's own.
        """
        from modules.flow_gate.services import test_run_service as runner
        from modules.flow_gate.services import tr_self_check_executor as executor
        junit_path = scratch / ("junit_" + str(abs(hash(nodeid))) + ".xml")
        env = {**os.environ, **runner._execution_env(runner._allocate_port(), scratch)}
        argv = [_python(env), "-m", "pytest", nodeid, "-q", "-p", "no:cacheprovider",
                "--junitxml=" + str(junit_path)]
        timeout = runner.CASE_TIMEOUT_SEC
        cancelled = active.cancel_event if active is not None else threading.Event()
        try:
            control, _ownership = executor.spawn(argv, root, env, timeout,
                                                 getattr(active, "run_id", None) or "spec-run")
        except executor.OwnershipError as exc:
            return "error", "", None, "process owner unavailable: " + str(exc)
        if active is not None:
            runner._set_current_control(active, control)
        try:
            outcome = executor.wait(control, timeout, cancelled)
        finally:
            if active is not None:
                runner._clear_current_control(active, control)
            control.close()
        output = (outcome.stdout_tail or "") + (outcome.stderr_tail or "")
        if outcome.timed_out:
            result = "timeout"
        else:
            result = "pass" if outcome.exit_code == 0 else "fail"
        xml = junit_path.read_text(encoding="utf-8") if junit_path.is_file() else ""
        return result, xml, outcome.exit_code, output[-4000:]


def _enter_phase(doc: dict, run: dict, phase: str) -> None:
    from modules.flow_gate.services import test_run_service as runner
    merge_run_meta(run["run_id"], phase=phase)
    runner._emit_phase(doc, run, phase)


def _record_basis(doc: dict, run: dict, basis: dict) -> dict:
    """Keep the run's Basis on the run (D#1 §3-3 step 4) and leave an audit event."""
    from modules.flow_gate.db import events as db_events
    binding = basis.get("binding") or {}
    execution_root = {"kind": "disposable_copy", "basis_id": basis["basis_id"],
                      "source": basis.get("source"), "binding": binding,
                      "source_identity": test_basis_service.source_identity(basis),
                      "disposable": True}
    merge_run_meta(run["run_id"], basis_id=basis["basis_id"], test_basis=basis,
                   execution_root=execution_root)
    db_events.insert_event(doc["doc_id"], "test_spec_basis_created", note=json.dumps({
        "basis_id": basis["basis_id"], "ts_revision_no": basis.get("ts_revision_no"),
        "run_id": run["run_id"], "source": basis["source"],
        "git_revision": binding.get("git_revision"), "source_dirty": binding.get("source_dirty"),
        "metrics": binding.get("metrics"),
        "manifest_hash": basis["test_assets"]["manifest_hash"],
    }, ensure_ascii=False))
    return execution_root


def execute(run: dict) -> None:
    from modules.flow_gate.services import test_run_service as runner
    run_id = run["run_id"]
    doc = db_docs.get_by_id(run["doc_id"])
    if doc is None:
        db_test_runs.finish_run(run_id=run_id, status="failed", error="doc_not_found")
        return
    meta = test_spec_service.load_result_meta(run.get("result_meta"))
    spec_cases = meta["spec_cases"]
    selected_ids = set(meta["selected_case_ids"])
    active = runner._register_active_run(run_id)
    scratch = None
    try:
        if runner._bail_if_cancelled(run_id, doc, active):
            return
        _enter_phase(doc, run, PHASE_PREPARING)
        try:
            basis, root, scratch = ExecutionRootResolver.prepare(doc, run, spec_cases)
        except ValueError as exc:
            code, _, reasons = str(exc).partition(":")
            if code.strip() not in _PREPARE_REFUSALS:
                raise
            # Not run at all: the run could not copy and measure its source. The terminal
            # write takes the run lock a binding chain holds, so the chain's token exists
            # whenever its context does (test_run_service.hand_chain_to_server_run).
            with runner._get_run_lock(run_id):
                merge_run_meta(run_id, phase=PHASE_FINISHED,
                               prepare_refused={"error": code.strip(), "reasons": reasons.strip()})
                db_test_runs.finish_run(run_id=run_id, status="failed", error=code.strip())
            runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
            _stop_chain_without_result(doc, run_id)
            return
        _record_basis(doc, run, basis)
        executed_identity = test_basis_service.source_identity(basis)
        if runner._bail_if_cancelled(run_id, doc, active):
            return
        _enter_phase(doc, run, PHASE_EXECUTING)
        results = []
        executed_nodes = set()
        titles = {test_spec_service.case_id_key(case["case_id"]): case.get("title")
                  for case in spec_cases}
        reported_ids: list[str] = []
        for case in spec_cases:
            if case["case_id"] not in selected_ids or active.cancel_event.is_set():
                continue
            loc = AutomationRefResolver.resolve(case)
            nodeid = loc["path"] + ("::" + loc["node"] if loc.get("node") else "")
            if nodeid in executed_nodes:
                continue
            executed_nodes.add(nodeid)
            _, xml, exit_code, output = ExistingRunnerAdapter.run_pytest(nodeid, root, scratch, active)
            if active.cancel_event.is_set():
                break
            if xml:
                parsed = test_spec_service.parse_junit_xml(
                    xml, submitted_by=run.get("runner_id") or "system", now=now_iso(),
                    default_source_identity=executed_identity,
                )
                if loc["capability"] == "case_selectable" and len(parsed) == 1:
                    parsed[0]["case_id"] = case["case_id"]
                for item in parsed:
                    if not item.get("case_id") or not any(
                        test_spec_service.case_id_key(item["case_id"]) ==
                        test_spec_service.case_id_key(selected_id) for selected_id in selected_ids
                    ):
                        item["case_id"] = None  # Preserve as unmapped evidence; never run manual/external.
                results.extend(parsed)
                node_results = [item for item in parsed if item.get("case_id")]
            else:
                blocked = {"case_id": case["case_id"], "status": "BLOCKED",
                           "actual": "pytest produced no JUnit report",
                           "evidence": [{"kind": "log", "value":
                                         ("pytest exit=" + str(exit_code) + "\n" + output)[-4000:]}],
                           "execution_mode": "automated"}
                results.append(blocked)
                node_results = [blocked]
            if node_results:
                # D#1 §3-4: the Case result is kept and shown before the remaining Cases run.
                reported_ids.extend(item["case_id"] for item in node_results)
                merge_run_meta(run_id, reported_case_ids=reported_ids)
                runner.record_execution_progress(doc, run_id, node_results)
            # D#1 §3-3: one Case-finished event per Case as its result arrives.
            for index, item in enumerate(node_results, start=len(reported_ids) - len(node_results) + 1):
                runner._emit_case_finished(doc, run, {
                    "case_no": item["case_id"],
                    "case_title": titles.get(test_spec_service.case_id_key(item["case_id"])),
                    "result": item.get("status"), "exit_code": exit_code,
                }, index, len(selected_ids))
        _enter_phase(doc, run, PHASE_FINALIZING)
        # D#1 §3-3 finalizing step 3: measure the live source once, before any lock. The
        # result is recorded under the executed Basis either way; this decides its gate.
        live = None
        if results and not active.cancel_event.is_set():
            live = test_basis_service.live_state(db_docs.get_by_id(doc["doc_id"]), spec_cases)
        with runner._get_run_lock(run_id):
            if active.cancel_event.is_set():
                merge_run_meta(run_id, phase=PHASE_FINISHED)
                runner._finalize_cancelled(run_id, doc)
                return
            # A chain may have attached while this run executed: read its context now.
            live_meta = merge_run_meta(run_id, phase=PHASE_FINISHED)
            chain = live_meta.get("chain")
            finished_execution = False
            if results:
                with runner._admission_lock:
                    recorded = runner.record_spec_results(
                        doc_id=doc["doc_id"], runner_id=run["runner_id"],
                        # A chain-carried result is the chain's (its token decides resume).
                        triggered_via="token" if chain else run["triggered_via"],
                        results=results,
                        locale=run.get("locale") or "ko", execution_run_id=run_id,
                        execution_basis=basis, execution_cases=spec_cases,
                        execution_live=live, chain_context=chain,
                    )
                    db_test_runs.finish_run(run_id=run_id, status="passed",
                                            case_passed=len(results), case_failed=0)
                    finished_execution = True
                    # The live verdict travels in the record: finalize does not measure again.
                    runner.finalize_spec_results(recorded["doc"], recorded["run"],
                                                 locale=run.get("locale") or "ko")
            if not finished_execution:
                db_test_runs.finish_run(run_id=run_id, status="passed", case_passed=0,
                                        case_failed=0)
        runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
    except Exception as exc:
        runner.logger.warning("spec execution failed for %s: %s", run_id, exc, exc_info=True)
        with runner._get_run_lock(run_id):
            try:
                merge_run_meta(run_id, phase=PHASE_FINISHED)
            except Exception:  # noqa: BLE001 -- the terminal write below matters more
                pass
            db_test_runs.finish_run(run_id=run_id, status="failed", error="spec_execution_error")
        runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
        _stop_chain_without_result(doc, run_id)
    finally:

        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        runner._unregister_active_run(run_id)
