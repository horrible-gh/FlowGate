"""F81 mutation policy shared by human, worker and generated document saves."""
from modules.flow_gate.db import documents, test_runs
from modules.flow_gate.services import test_spec_service as spec


def reopen(doc, phase="test_run"):
    from modules.flow_gate.services import test_run_service as runs
    target = doc if phase == "case_write" else runs._active_tsr_for_ts(doc)
    if target and target.get("doc_review_status") in {"approved", "wf_done"}:
        documents.update(target["doc_id"], {"doc_review_status": "pending_review"})


def cases_changed(before, after):
    def keys(text):
        return {spec.case_id_key(c["case_id"]) for c in spec.parse_spec(text)["cases"]}
    return keys(before) != keys(after)


def current_rows(doc, run, stored):
    """Project only current cases; retain observations without rewriting run history."""
    from modules.flow_gate.services import test_run_service as runs, test_evidence_service as evidence
    cases = spec.parse_spec(runs._read_doc_content_or_empty(doc))["cases"]
    previous = {spec.case_id_key(r["case_id"]): r for r in
                (spec.stored_case_to_row(c) for c in stored if (c.get("kind") or "case") == "case")}
    rows = spec.map_results(cases, [], previous=previous, previous_run_id=run.get("run_id"))["cases"]
    tagged = evidence.tagged(doc)
    for row in rows:
        links = [{"kind": "file", "value": "fg-attachment:" + item["key"],
                  "label": item.get("display_name") or item["key"]}
                 for item in tagged if spec.case_id_key(item["test_evidence"]["case_id"]) == spec.case_id_key(row["case_id"])]
        row["evidence"] = [*(row.get("evidence") or []), *links]
    return rows


def regenerate(doc):
    from modules.flow_gate.graph_workflow.services.card_document_ports import dedicated_write
    return dedicated_write("tsr", doc, refresh=True)


def saved(doc, before, after):
    if doc.get("type_code") != "TS" or spec.detect_contract_version(after) != spec.CONTRACT_SPEC:
        return
    running = test_runs.get_running_by_doc(doc["doc_id"])
    if running:
        from modules.flow_gate.graph_workflow.services.errors import GraphApiError
        raise GraphApiError(409, "run_in_progress", {"run_id": running["run_id"]},
                            message="The test is running. Edit the scenario after it finishes.")
    if cases_changed(before, after):
        reopen(doc)
    return True


def regenerate_after_save(doc):
    import logging

    try:
        regenerate(doc)
    except Exception:
        logging.getLogger(__name__).exception(
            "TSR regeneration deferred after scenario save: %s", doc["doc_id"])
