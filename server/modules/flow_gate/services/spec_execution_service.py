"""Contract-2 execution orchestration on the existing test-run worker.

0684 T#1 (D#1 §3-1~§3-4): a run is admitted without asking whether a stored Basis still
matches the source -- by TS approval (inside the approval transaction), by [run again] or
by a single Case. The run measures what it executes: its preparing phase captures the
Basis from the Group worktree (T#1 interim, D#1 §7: capture then run from that capture)
and stores it on the TS, so approval never captures and never waits for the source.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
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


def _attach_chain(doc: dict, chain_context: dict) -> dict | None:
    """Hand an unmanned chain to the server run its TS approval already started.

    0684 T#1: the chain reaches the TSR head after the approval queued a run. Instead of
    refusing ``run_in_progress`` the chain rides that run (its context goes into the run's
    meta, so the recorded result carries it). A run that already PASSed the gate for this
    revision is attached as ``finished``. Anything else is not attachable: a new run starts.
    """
    from modules.flow_gate.services import test_run_service as runner
    doc_id = doc["doc_id"]
    with runner._admission_lock:
        running = db_test_runs.get_running_by_doc(doc_id)
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
        attached = _attach_chain(doc, chain_context)
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
    def measure(doc: dict, run: dict, cases: list[dict]) -> dict:
        """Run Basis (0684 T#1 interim, D#1 §7): capture the worktree and store it on the TS.

        The capture is the Source Bundle path T#2 replaces with a copy-and-hash run root;
        either way it runs here, in the run's preparing phase, never in the approval. The
        stored Basis is what the gate and the views judge until the next run measures again.
        """
        from modules.flow_gate.db import events as db_events
        from modules.flow_gate.db.connection import get_store
        from modules.flow_gate.services import test_run_service as runner
        doc_id = doc["doc_id"]
        revision = run.get("revision_no") or 0
        current_doc = db_docs.get_by_id(doc_id) or doc
        if (current_doc.get("revision_no") or 0) != revision:
            raise ValueError("ts_changed_before_execution")
        basis = test_basis_service.capture(current_doc, cases)
        with runner._admission_lock, get_store().transaction():
            fresh = db_docs.get_by_id(doc_id)
            if not fresh or (fresh.get("revision_no") or 0) != revision:
                raise ValueError("ts_changed_before_execution")
            updated = db_docs.update(doc_id, {
                "meta": test_basis_service.metadata_with_basis(fresh, basis)})
            if not updated:
                raise RuntimeError("test_basis_update_failed")
            test_basis_service.pin(updated, basis)
            db_events.insert_event(doc_id, "test_spec_basis_created", note=json.dumps({
                "basis_id": basis["basis_id"], "ts_revision_no": revision,
                "run_id": run["run_id"], "source": basis["source"],
                "binding": basis.get("binding"),
                "manifest_hash": basis["test_assets"]["manifest_hash"],
            }, ensure_ascii=False))
        return basis

    @staticmethod
    def prepare(doc: dict, run: dict, basis: dict) -> tuple[Path, Path]:
        """Execution Root Builder (0682 D#1 3.9): a disposable copy of the Basis's Bundle.

        Never the live worktree and never ``git archive HEAD``: what runs is exactly the
        captured source the Basis was judged on, uncommitted and untracked work included.
        """
        from modules.flow_gate.db import source_bundles as db_source_bundles
        from modules.flow_gate.services import test_run_service as runner
        current_doc = db_docs.get_by_id(doc["doc_id"]) or doc
        stored = test_basis_service.current(current_doc)
        judged = test_basis_service.verdict(current_doc, stored, execution_basis=basis)
        if judged["state"] != test_basis_service.VALID:
            prefix = ("basis_stale_before_execution" if judged["state"] == test_basis_service.STALE
                      else "basis_unverifiable_before_execution")
            raise ValueError(prefix + ": " + ",".join(judged["reasons"]))
        effective = stored
        try:
            opened = test_basis_service.open_bundle(current_doc, effective)
        except ValueError:
            # Bundle deleted, damaged or not this Group's: the live source still matches
            # (verdict above), so capture again and move the binding; basis_id stays.
            effective = test_basis_service.rebind(current_doc, effective, run_id=run["run_id"])
            opened = test_basis_service.open_bundle(current_doc, effective)
        captured = {entry["path"]: entry["sha256"] for entry in opened["manifest"]["files"]}
        for asset in effective.get("manifest") or []:
            if captured.get(asset["path"]) != asset["content_hash"]:
                raise ValueError("test_asset_not_captured: " + asset["path"])
        scratch = runner._scratch_dir(doc, run["run_id"])
        root = scratch / "source"
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            test_basis_service.copy_bundle_source(opened["source"], root, opened["manifest"])
        except Exception:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        db_source_bundles.record_usage(
            opened["row"]["bundle_id"], "spec_execution", run_id=run["run_id"],
            document_id=doc["doc_id"], detail={"basis_id": effective["basis_id"]},
        )
        return root, scratch


# Prepare refusals end the run under their own error, pytest never started: the run could
# not measure its source (capture refused, an automated test file missing or not an asset,
# the TS changed while queued), a Basis that is no longer the live source, or a run root
# that is not byte-for-byte the Basis's Bundle. None of them is a FAIL (D#1 §3-3 step 6).
_PREPARE_REFUSALS = {"basis_stale_before_execution", "basis_unverifiable_before_execution",
                     "execution_source_mismatch", "unsafe_execution_source",
                     "test_asset_not_captured", "basis_capture_failed",
                     "automation_asset_missing", "product_source_or_invalid_test_asset",
                     "test_asset_path_case_mismatch", "ts_changed_before_execution",
                     "source_root_missing"}


class ExistingRunnerAdapter:
    @staticmethod
    def run_pytest(nodeid: str, root: Path, scratch: Path, active) -> tuple[str, str, int | None, str]:
        from modules.flow_gate.services import test_run_service as runner
        junit_path = scratch / ("junit_" + str(abs(hash(nodeid))) + ".xml")
        argv = ["python", "-m", "pytest", nodeid, "-q", "-p", "no:cacheprovider",
                "--junitxml=" + str(junit_path)]
        command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        result, exit_code, output, _ = runner._run_shell_command(
            command, root, runner.CASE_TIMEOUT_SEC,
            runner._execution_env(runner._allocate_port(), scratch), active,
        )
        xml = junit_path.read_text(encoding="utf-8") if junit_path.is_file() else ""
        return result, xml, exit_code, output[-4000:]


def _enter_phase(doc: dict, run: dict, phase: str) -> None:
    from modules.flow_gate.services import test_run_service as runner
    merge_run_meta(run["run_id"], phase=phase)
    runner._emit_phase(doc, run, phase)


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
            basis = ExecutionRootResolver.measure(doc, run, spec_cases)
            merge_run_meta(run_id, basis_id=basis["basis_id"], test_basis=basis)
            if runner._bail_if_cancelled(run_id, doc, active):
                return
            root, scratch = ExecutionRootResolver.prepare(doc, run, basis)
        except ValueError as exc:
            code, _, reasons = str(exc).partition(":")
            if code.strip() not in _PREPARE_REFUSALS:
                raise
            # Not run at all: the run could not measure its source, or the copied run root
            # is not exactly the measured capture.
            merge_run_meta(run_id, phase=PHASE_FINISHED,
                           prepare_refused={"error": code.strip(), "reasons": reasons.strip()})
            db_test_runs.finish_run(run_id=run_id, status="failed", error=code.strip())
            runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
            return
        # A rebind during prepare moved the binding; record the Bundle that really ran.
        bound = test_basis_service.current(db_docs.get_by_id(doc["doc_id"]) or {})
        executed = bound if bound and bound["basis_id"] == basis["basis_id"] else basis
        executed_identity = test_basis_service.source_identity(executed)
        execution_root = {"kind": (executed.get("source") or {}).get("kind"),
                          "basis_id": executed["basis_id"],
                          "source": executed.get("source"),
                          "binding": executed.get("binding"),
                          "source_identity": executed_identity,
                          "disposable": True}
        merge_run_meta(run_id, execution_root=execution_root)
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
        with runner._get_run_lock(run_id):
            if active.cancel_event.is_set():
                merge_run_meta(run_id, phase=PHASE_FINISHED)
                runner._finalize_cancelled(run_id, doc)
                return
            current = db_docs.get_by_id(doc["doc_id"])
            judged = test_basis_service.verdict(
                current, test_basis_service.current(current or {}), execution_basis=basis)
            stale = judged["state"] != test_basis_service.VALID
            # stale -> superseded evidence; unverifiable -> evidence, run ends basis_unverifiable.
            stale_error = ("basis_superseded" if judged["state"] == test_basis_service.STALE
                           else "basis_unverifiable")
            # A chain may have attached while this run executed: read its context now.
            live_meta = merge_run_meta(run_id, phase=PHASE_FINISHED)
            chain = live_meta.get("chain")
            finished_execution = False
            if stale:
                normalized = test_spec_service.normalize_results(
                    results, submitted_by=run.get("runner_id") or "system", now=now_iso(),
                    default_source_identity=executed_identity, default_origin="automated")
                mapped = test_spec_service.map_results(spec_cases, normalized)
                summary = test_spec_service.compute_overall(mapped["cases"])
                db_test_runs.insert_spec_run(
                    doc_id=doc["doc_id"], revision_no=run["revision_no"],
                    triggered_via=run["triggered_via"], runner_id=run["runner_id"],
                    rows=mapped["cases"], status="failed", overall=summary["overall"],
                    result_meta=json.dumps({"run_kind": "superseded_execution", "basis_id": basis["basis_id"],
                                            "test_basis": basis, "stale": True,
                                            "basis_state": judged["state"],
                                            "basis_reasons": judged["reasons"],
                                            "execution_root": execution_root,
                                            "counts": summary["counts"],
                                            "required_counts": summary["required_counts"],
                                            "optional_counts": summary["optional_counts"],
                                            "unmapped": mapped["unmapped"],
                                            "conflicts": mapped["conflicts"]}, ensure_ascii=False),
                    case_passed=summary["counts"]["pass"], case_failed=summary["counts"]["fail"],
                    error=stale_error, locale=run.get("locale"),
                    case_meta=[test_spec_service.case_row_to_meta(row) for row in mapped["cases"]],
                )
            elif results:
                with runner._admission_lock:
                    recorded = runner.record_spec_results(
                        doc_id=doc["doc_id"], runner_id=run["runner_id"],
                        # A chain-carried result is the chain's (its token decides resume).
                        triggered_via="token" if chain else run["triggered_via"],
                        results=results,
                        locale=run.get("locale") or "ko", execution_run_id=run_id,
                        execution_basis=executed, execution_cases=spec_cases,
                        chain_context=chain,
                    )
                    db_test_runs.finish_run(run_id=run_id, status="passed",
                                            case_passed=len(results), case_failed=0)
                    finished_execution = True
                    runner.finalize_spec_results(recorded["doc"], recorded["run"],
                                                 locale=run.get("locale") or "ko")
            if not finished_execution:
                db_test_runs.finish_run(run_id=run_id, status="failed" if stale else "passed",
                                        case_passed=len(results) if not stale else 0,
                                        case_failed=0, error=stale_error if stale else None)
        runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
    except Exception as exc:
        runner.logger.warning("spec execution failed for %s: %s", run_id, exc, exc_info=True)
        try:
            merge_run_meta(run_id, phase=PHASE_FINISHED)
        except Exception:  # noqa: BLE001 -- the terminal write below matters more
            pass
        db_test_runs.finish_run(run_id=run_id, status="failed", error="spec_execution_error")
        runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        runner._unregister_active_run(run_id)
