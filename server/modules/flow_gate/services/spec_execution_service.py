"""Contract-2 execution orchestration on the existing test-run worker."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tarfile
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
    try:
        live = test_basis_service.resolve(doc, parsed["cases"])
    except ValueError as exc:
        raise runner._http_error(409, "basis_unavailable", detail=str(exc)) from exc
    if live["basis_id"] != basis["basis_id"]:
        raise runner._http_error(409, "basis_stale", basis_id=basis["basis_id"],
                                 live_basis_id=live["basis_id"])
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
        from modules.flow_gate.services import test_run_service as runner
        source = test_basis_service.source_root(doc)
        live = test_basis_service.resolve(doc, test_spec_service.parse_spec(
            runner._read_doc_content_or_empty(doc))["cases"])
        if live["basis_id"] != basis["basis_id"]:
            raise ValueError("basis_stale_before_execution")
        scratch = runner._scratch_dir(doc, run["run_id"])
        root = scratch / "source"
        scratch.mkdir(parents=True, exist_ok=True)
        root.mkdir(parents=True, exist_ok=True)
        archive = scratch / "source.tar"
        with archive.open("wb") as stream:
            process = subprocess.run(
                ["git", "-C", str(source), "archive", "--format=tar",
                 basis["source"]["git_revision"]], stdout=stream, stderr=subprocess.PIPE,
                timeout=120,
            )
        if process.returncode:
            raise ValueError("execution_archive_failed: " + process.stderr.decode(errors="replace")[:300])
        with tarfile.open(archive, "r") as packed:
            for member in packed:
                target = (root / member.name).resolve()
                if not target.is_relative_to(root.resolve()) or member.issym() or member.islnk():
                    raise ValueError("unsafe_execution_archive_member")
                packed.extract(member, root)
        archive.unlink(missing_ok=True)
        after = test_basis_service.resolve(doc, test_spec_service.parse_spec(
            runner._read_doc_content_or_empty(doc))["cases"])
        if after["basis_id"] != basis["basis_id"]:
            raise ValueError("basis_changed_during_copy")
        return root, scratch


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
        root, scratch = ExecutionRootResolver.prepare(doc, run, basis)
        meta["execution_root"] = {"kind": basis["source"]["kind"],
                                  "source_identity": basis["source"],
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
                    default_source_identity=basis["source"],
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
            current_basis = test_basis_service.current(current or {})
            try:
                live = test_basis_service.resolve(current, test_spec_service.parse_spec(
                    runner._read_doc_content_or_empty(current))["cases"])
            except (ValueError, TypeError):
                live = None
            stale = not current_basis or current_basis["basis_id"] != basis["basis_id"] or (
                not live or live["basis_id"] != basis["basis_id"])
            finished_execution = False
            if stale:
                normalized = test_spec_service.normalize_results(
                    results, submitted_by=run.get("runner_id") or "system", now=now_iso(),
                    default_source_identity=basis["source"], default_origin="automated")
                mapped = test_spec_service.map_results(spec_cases, normalized)
                summary = test_spec_service.compute_overall(mapped["cases"])
                db_test_runs.insert_spec_run(
                    doc_id=doc["doc_id"], revision_no=run["revision_no"],
                    triggered_via=run["triggered_via"], runner_id=run["runner_id"],
                    rows=mapped["cases"], status="failed", overall=summary["overall"],
                    result_meta=json.dumps({"run_kind": "superseded_execution", "basis_id": basis["basis_id"],
                                            "test_basis": basis, "stale": True,
                                            "counts": summary["counts"],
                                            "required_counts": summary["required_counts"],
                                            "optional_counts": summary["optional_counts"],
                                            "unmapped": mapped["unmapped"],
                                            "conflicts": mapped["conflicts"]}, ensure_ascii=False),
                    case_passed=summary["counts"]["pass"], case_failed=summary["counts"]["fail"],
                    error="basis_superseded", locale=run.get("locale"),
                    case_meta=[test_spec_service.case_row_to_meta(row) for row in mapped["cases"]],
                )
            elif results:
                with runner._admission_lock:
                    recorded = runner.record_spec_results(
                        doc_id=doc["doc_id"], runner_id=run["runner_id"],
                        triggered_via=run["triggered_via"], results=results,
                        locale=run.get("locale") or "ko", execution_run_id=run_id,
                        execution_basis=basis, execution_cases=spec_cases,
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
                                        case_failed=0, error="basis_superseded" if stale else None)
        runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
    except Exception as exc:
        runner.logger.warning("spec execution failed for %s: %s", run_id, exc, exc_info=True)
        db_test_runs.finish_run(run_id=run_id, status="failed", error="spec_execution_error")
        runner._emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        runner._unregister_active_run(run_id)
