"""Contract-2 execution orchestration on the existing test-run worker."""
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


def admit(doc_id: str, *, case_id: str | None, runner_id: str, locale: str,
          triggered_via: str = "ui", chain_context: dict | None = None) -> dict:
    from modules.flow_gate.services import test_run_service as runner
    doc, parsed = runner.load_spec_ts(doc_id)
    runner._require_ts_admissible(doc, doc_id)
    paired = runner._active_tsr_for_ts(doc)
    if paired and paired.get("doc_review_status") == "approved":
        raise runner._http_error(409, "tsr_already_approved", doc_id=doc_id)
    basis = test_basis_service.current(doc)
    if not basis:
        raise runner._http_error(409, "basis_missing", doc_id=doc_id)
    error = test_basis_service.verdict_error(
        test_basis_service.verdict(doc, basis, parsed["cases"]), runner._http_error, doc_id=doc_id)
    if error is not None:
        raise error
    all_cases = parsed["cases"]
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
    else:
        selected = [case for case in all_cases if case.get("execution_mode") == "automated"
                    and AutomationRefResolver.resolve(case)["capability"] in
                    {"case_selectable", "suite_only"}]
        if not selected:
            raise runner._http_error(422, "no_automated_bindings", doc_id=doc_id)
    with runner._admission_lock:
        running = db_test_runs.get_running_by_doc(doc_id)
        if running:
            raise runner._http_error(409, "run_in_progress", run_id=running["run_id"])
        pending = db_test_runs.get_pending_failure_origin(doc_id)
        if pending:
            raise runner._http_error(409, "failure_origin_pending", run_id=pending["run_id"])
        selected_ids = [case["case_id"] for case in selected]
        run = db_test_runs.insert_run(
            doc_id=doc_id, revision_no=doc.get("revision_no") or 0,
            triggered_via=triggered_via, runner_id=runner_id,
            cases=[{"kind": "case", "case_no": case["case_id"],
                    "title": case["title"], "cmd": "",
                    "expect": case.get("expected") or ""} for case in selected],
            locale=locale, contract_version=2,
            result_meta=json.dumps({"run_kind": "spec_execution", "basis_id": basis["basis_id"],
                                    "test_basis": basis, "selected_case_ids": selected_ids,
                                    "spec_cases": all_cases, "chain": chain_context}, ensure_ascii=False),
        )
    runner._emit_started(doc, run)
    return {**runner._run_response(run), "basis_id": basis["basis_id"],
            "selected_case_ids": selected_ids}


class ExecutionRootResolver:
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


# Prepare refusals end the run under their own error, pytest never started: a Basis that is
# no longer the live source, or a run root that is not byte-for-byte the Basis's Bundle.
_PREPARE_REFUSALS = {"basis_stale_before_execution", "basis_unverifiable_before_execution",
                     "execution_source_mismatch", "unsafe_execution_source",
                     "test_asset_not_captured"}


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


def execute(run: dict) -> None:
    from modules.flow_gate.services import test_run_service as runner
    run_id = run["run_id"]
    doc = db_docs.get_by_id(run["doc_id"])
    if doc is None:
        db_test_runs.finish_run(run_id=run_id, status="failed", error="doc_not_found")
        return
    meta = test_spec_service.load_result_meta(run.get("result_meta"))
    basis = meta["test_basis"]
    spec_cases = meta["spec_cases"]
    selected_ids = set(meta["selected_case_ids"])
    active = runner._register_active_run(run_id)
    scratch = None
    try:
        if runner._bail_if_cancelled(run_id, doc, active):
            return
        try:
            root, scratch = ExecutionRootResolver.prepare(doc, run, basis)
        except ValueError as exc:
            code, _, reasons = str(exc).partition(":")
            if code not in _PREPARE_REFUSALS:
                raise
            # Not run at all: the Basis is no longer the source the run was admitted on,
            # or the copied run root is not exactly the approved Bundle.
            meta["prepare_refused"] = {"error": code, "reasons": reasons.strip()}
            db_test_runs.set_run_result_meta(run_id, json.dumps(meta, ensure_ascii=False))
            db_test_runs.finish_run(run_id=run_id, status="failed", error=code)
            runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
            return
        # A rebind during prepare moved the binding; record the Bundle that really ran.
        bound = test_basis_service.current(db_docs.get_by_id(doc["doc_id"]) or {})
        executed = bound if bound and bound["basis_id"] == basis["basis_id"] else basis
        executed_identity = test_basis_service.source_identity(executed)
        meta["execution_root"] = {"kind": (executed.get("source") or {}).get("kind"),
                                  "basis_id": executed["basis_id"],
                                  "source": executed.get("source"),
                                  "binding": executed.get("binding"),
                                  "source_identity": executed_identity,
                                  "disposable": True}
        db_test_runs.set_run_result_meta(run_id, json.dumps(meta, ensure_ascii=False))
        results = []
        executed_nodes = set()
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
            else:
                results.append({"case_id": case["case_id"], "status": "BLOCKED",
                                "actual": "pytest produced no JUnit report",
                                "evidence": [{"kind": "log", "value":
                                              ("pytest exit=" + str(exit_code) + "\n" + output)[-4000:]}],
                                "execution_mode": "automated"})
        with runner._get_run_lock(run_id):
            if active.cancel_event.is_set():
                runner._finalize_cancelled(run_id, doc)
                return
            current = db_docs.get_by_id(doc["doc_id"])
            judged = test_basis_service.verdict(
                current, test_basis_service.current(current or {}), execution_basis=basis)
            stale = judged["state"] != test_basis_service.VALID
            # stale -> superseded evidence; unverifiable -> evidence, run ends basis_unverifiable.
            stale_error = ("basis_superseded" if judged["state"] == test_basis_service.STALE
                           else "basis_unverifiable")
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
                                            "execution_root": meta["execution_root"],
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
                        triggered_via=run["triggered_via"], results=results,
                        locale=run.get("locale") or "ko", execution_run_id=run_id,
                        execution_basis=executed, execution_cases=spec_cases,
                        chain_context=meta.get("chain"),
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
        db_test_runs.finish_run(run_id=run_id, status="failed", error="spec_execution_error")
        runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        runner._unregister_active_run(run_id)
