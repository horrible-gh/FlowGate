"""Complete a TS execution from recorded cases and the server report, never worker markdown."""
from modules.flow_gate.db import documents, test_runs
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.services.run_apply import load_context
from flowgate.card_settings import legacy_adapter as legacy, stage_assignment as stages


def report_ready(report):
    from modules.flow_gate.services import test_run_service as runner
    ts_id = report.get("target_id") or report.get("triggered_by")
    latest = test_runs.latest_by_doc(ts_id) if ts_id else None
    rows = test_runs.list_cases(latest["run_id"]) if latest else []
    cases = [r for r in rows if (r.get("kind") or "case") == "case"]
    return bool(cases and all(r.get("case_status") not in (None, "NOT_RUN", "PENDING") for r in cases)
                and runner.tsr_gate_state(report).get("passed"))


def finish(work):
    if (work.get("mode") != "single" or work.get("document_review_loop")
            or work.get("end_reason") != "exited"
            or work.get("stop_code") not in (None, "report_server_generated", "question_pending")):
        return False
    flow_id = work.get("group_id")
    attempt = legacy.attempt_of_ai_run(work)
    if not attempt:
        return False
    nodes = [n for n in legacy.nodes_of_ai_run(flow_id, work.get("run_id"))
             if n.get("doc_type") == "TS" or n.get("node_type") == "test"]
    if not nodes:
        return False
    # A real unresolved user question is not a report-write refusal.
    if legacy.open_questions_of_ai_run(flow_id, work.get("run_id")):
        return False
    jobs = []
    handled = False

    def commit():
        nonlocal handled
        active = legacy.active_run(flow_id)
        if not active:
            return
        for node in nodes:
            key = node["node_key"]
            row = legacy.node_run(active["run_id"], key)
            if (not row or row.get("status") != "open" or row.get("stage") != "result"
                    or not legacy.attempt_owns(active["run_id"], key, attempt, "test_execution_finish")):
                continue
            from modules.flow_gate.db.connection import get_store
            latest_work = get_store()._fetch_one(
                "SELECT exec_id FROM graph_attempt_runs WHERE attempt_id = ? AND kind = 'write' "
                "AND assignment_stage = 'result' ORDER BY created_at DESC, exec_id DESC LIMIT 1", [attempt])
            if not latest_work or latest_work["exec_id"] != work.get("graph_exec_id"):
                continue
            assignment = stages.store.read(attempt, stages.RESULT, 0)
            if not assignment or assignment.get("actor") != stages.AI:
                continue
            handled = True
            from modules.flow_gate.services import test_run_service as runner
            ts = documents.get_by_id(row.get("instruction_doc_id"))
            reason = "test_case_results_missing"
            report = None
            try:
                latest = test_runs.latest_by_doc(ts["doc_id"]) if ts else None
                if latest and latest.get("status") not in ("running", "cancelling"):
                    if int(latest.get("revision_no") or 0) == int(ts.get("revision_no") or 0):
                        # Rebuild even after a rejected worker TSR upload; the results are authoritative.
                        reason = "report_assembly_failed"
                        report_id = runner.assemble_tsr(ts, latest, test_runs.list_cases(latest["run_id"]), run_chain=False)
                        test_runs.set_run_tsr_doc(latest["run_id"], report_id)
                        report = documents.get_by_id(report_id)
                        gate = runner.tsr_gate_state(report)
                        reason = None if gate.get("passed") else "test_gate_" + str(gate.get("overall") or "incomplete").lower()
                        if not gate.get("passed") and gate.get("basis_reasons"):
                            reason = "test_gate_" + str(gate.get("basis_state") or "unverifiable") + ":" + ",".join(gate["basis_reasons"])
                        rows = test_runs.list_cases(latest["run_id"])
                        if reason is None and (not rows or any(
                                r.get("case_status") in (None, "NOT_RUN", "PENDING")
                                for r in rows if (r.get("kind") or "case") == "case")):
                            reason = "test_case_results_missing"
                if reason:
                    stages.fail(assignment, reason)
                    legacy.set_dispatch_error(active["run_id"], key, reason)
                    continue
                ctx = load_context(active, ts.get("project_id"), work.get("issued_to"), jobs)
                plan = stages.stage_plan(attempt) or {"instruction_type": "TS", "result_type": "TSR"}
                error = stages.take_written_result(ctx, node, assignment, work.get("issued_to") or "", report, plan,
                    author={"author": "ai", "provider_id": assignment.get("provider_id")})
                if error:
                    stages.fail(assignment, error)
                    legacy.set_dispatch_error(active["run_id"], key, error)
            except Exception:
                stages.fail(assignment, reason or "test_report_completion_failed")
                legacy.set_dispatch_error(active["run_id"], key, reason or "test_report_completion_failed")
                import logging
                logging.getLogger(__name__).exception("TS report completion failed for %s", key)
    with_flow_guard(flow_id, commit)
    for job in jobs:
        job()
    if handled:
        legacy.settle_graph_run(flow_id)
    return handled
