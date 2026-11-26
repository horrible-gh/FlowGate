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
    from modules.flow_gate.services import test_run_service as runs
    run = test_runs.latest_spec_run(doc["doc_id"])
    if run:
        return runs.assemble_tsr(doc, run, test_runs.list_cases(run["run_id"]),
                                 locale=run.get("locale") or "ko", run_chain=False)
    return None


def saved(doc, before, after):
    if doc.get("type_code") != "TS" or spec.detect_contract_version(after) != spec.CONTRACT_SPEC:
        return
    running = test_runs.get_running_by_doc(doc["doc_id"])
    if running:
        from fastapi import HTTPException
        raise HTTPException(409, detail={"error": "run_in_progress", "run_id": running["run_id"]})
    if cases_changed(before, after):
        reopen(doc)
    regenerate(documents.get_by_id(doc["doc_id"]) or doc)
