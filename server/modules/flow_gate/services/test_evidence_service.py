"""Case evidence stored in the existing Flow attachment registry."""
from __future__ import annotations
import io
import json
from pathlib import Path
from urllib.parse import quote
from starlette.datastructures import UploadFile
from fastapi import HTTPException
from modules.flow_gate.db import documents, test_runs
from modules.flow_gate.services import test_spec_service as spec
from modules.flow_gate.documents.attachments import flow
from modules.flow_gate.documents.attachments.errors import AttachmentError


def validate_tag(flow_id, raw):
    try:
        tag = spec.normalize_evidence_tag(raw)
    except spec.SpecResultError as exc:
        raise AttachmentError(422, exc.code, exc.detail) from exc
    ts_id = raw.get("ts_doc_id")
    doc = documents.get_by_id(ts_id) if ts_id else None
    if not doc or doc.get("group_id") != flow_id or doc.get("type_code") != "TS":
        raise AttachmentError(422, "invalid_evidence_tag", "Evidence must belong to a TS in this Flow.")
    from modules.flow_gate.services.test_run_service import load_spec_ts
    _, parsed = load_spec_ts(ts_id)
    if not any(spec.case_id_key(c["case_id"]) == spec.case_id_key(tag["case_id"]) for c in parsed["cases"]):
        raise AttachmentError(404, "case_not_found", "Case not found.")
    return {**tag, "ts_doc_id": ts_id}


def tagged(doc):
    out = []
    for row in flow.rows(doc["group_id"]):
        tag = spec.load_result_meta(row.get("test_evidence"))
        if tag.get("ts_doc_id") == doc["doc_id"]:
            out.append({**row, "test_evidence": tag})
    return out


def executions(doc_id, case_id):
    key = spec.case_id_key(case_id)
    out = []
    for run in reversed(test_runs.list_by_doc(doc_id)):
        meta = spec.load_result_meta(run.get("result_meta"))
        if meta.get("run_kind") == "spec_execution" and key in {
            spec.case_id_key(cid) for cid in meta.get("selected_case_ids", [])
        }:
            out.append(run)
    return out


def attempt_for(doc_id, case_id, run_id=None):
    runs = executions(doc_id, case_id)
    if run_id:
        for index, run in enumerate(runs, 1):
            if run["run_id"] == run_id:
                return index
        raise HTTPException(409, detail={"error": "execution_not_found"})
    if runs:
        return len(runs)
    # Manual checks are retried by resetting a recorded result to NOT_RUN.
    # Carried/missing rows and repeated resets must not manufacture attempts.
    attempt, recorded = 1, False
    for run in reversed(test_runs.list_by_doc(doc_id)):
        if spec.load_result_meta(run.get("result_meta")).get("run_kind") == "initialization":
            continue
        for row in test_runs.list_cases(run["run_id"]):
            if spec.case_id_key(row.get("case_no") or "") != spec.case_id_key(case_id):
                continue
            meta = spec.load_result_meta(row.get("case_meta"))
            if meta.get("result_origin") == "missing" or meta.get("carried_from_run_id"):
                continue
            status = row.get("case_status")
            if status == "NOT_RUN":
                if recorded:
                    attempt += 1
                recorded = False
            elif status in ("PASS", "FAIL", "BLOCKED"):
                recorded = True
    return attempt


def pass_reason(doc, case, evidence=None, run_id=None):
    cid = case["case_id"]
    if spec.case_check(case)[0] == "code":
        runs = executions(doc["doc_id"], cid)
        if (not runs or (run_id and runs[-1]["run_id"] != run_id)
                or int(runs[-1].get("revision_no") or 0) != int(doc.get("revision_no") or 0)):
            return "exit_zero_required"
        rows = test_runs.list_cases(runs[-1]["run_id"])
        observation = next((r for r in rows if spec.case_id_key(r.get("case_no") or "") == spec.case_id_key(cid)), {})
        return None if observation.get("exit_code") == 0 else "exit_zero_required"
    current = attempt_for(doc["doc_id"], cid)
    for row in evidence if evidence is not None else tagged(doc):
        tag = row["test_evidence"]
        if (spec.case_id_key(tag["case_id"]) == spec.case_id_key(cid)
                and tag["kind"] == "output" and tag["attempt"] == current):
            return None
    return "output_evidence_required"


def validate_passes(doc, cases, results, run_id=None):
    by_id = {spec.case_id_key(c["case_id"]): c for c in cases}
    evidence = None
    for result in results:
        if result.get("status") != "PASS":
            continue
        case = by_id.get(spec.case_id_key(result.get("case_id") or ""))
        if not case:
            continue
        if spec.case_check(case)[0] != "code" and evidence is None:
            evidence = tagged(doc)
        reason = pass_reason(doc, case, evidence, run_id)
        if reason:
            raise HTTPException(422, detail={"error": reason, "case_id": case["case_id"]})


def listing(doc, case_id=None, kind=None):
    rows = tagged(doc)
    if kind is not None and kind not in ("input", "log", "output"):
        raise HTTPException(422, detail={"error": "invalid_evidence_kind"})
    items = []
    for row in rows:
        tag = row["test_evidence"]
        if case_id and spec.case_id_key(case_id) != spec.case_id_key(tag["case_id"]):
            continue
        if kind and kind != tag["kind"]:
            continue
        base = f"/api/v1/documents/{quote(doc['doc_id'], safe='')}/test-spec/evidence/{quote(row['key'], safe='')}"
        items.append({"key": row["key"], "filename": row["display_name"], "size": row["size"],
                      "content_type": row["content_type"], "test_evidence": tag,
                      "download_url": base + "/download", "preview_url": base + "/preview",
                      "link": f"fg-attachment:{row['key']}"})
    from modules.flow_gate.services.test_run_service import load_spec_ts
    _, parsed = load_spec_ts(doc["doc_id"])
    cases = [c for c in parsed["cases"] if not case_id or spec.case_id_key(c["case_id"]) == spec.case_id_key(case_id)]
    return {"ok": True, "attachments": items, "cases": {
        c["case_id"]: {"attempt": attempt_for(doc["doc_id"], c["case_id"]),
                       "pass_reason": pass_reason(doc, c, rows)} for c in cases}}


def upload(doc, case_id, kind, part, user, attempt=None):
    from modules.flow_gate.services.test_run_service import _admission_lock
    with _admission_lock:
        current = attempt_for(doc["doc_id"], case_id)
        if attempt is not None and attempt != current:
            raise HTTPException(409, detail={"error": "evidence_attempt_changed"})
        result = flow.upload(doc["group_id"], part, user, "keep_both", test_evidence={
            "ts_doc_id": doc["doc_id"], "case_id": case_id, "kind": kind, "attempt": current})
        from modules.flow_gate.services.test_card_changes import regenerate
        regenerate(doc)
        return result


def evidence_removed(raw_tag):
    """A tagged evidence file was deleted through the Flow attachment delete: the report md is rebuilt as on upload.

    The file is already gone when this runs, so a failed rebuild is logged instead of failing the delete; the next
    result, evidence or case change rebuilds the report again.
    """
    tag = spec.load_result_meta(raw_tag)
    doc = documents.get_by_id(tag["ts_doc_id"]) if tag.get("ts_doc_id") else None
    if not doc or doc.get("type_code") != "TS":
        return
    from modules.flow_gate.services.test_card_changes import regenerate
    try:
        regenerate(doc)
    except Exception:  # noqa: BLE001 - the delete is committed; only the report rebuild failed.
        import logging
        logging.getLogger(__name__).exception("test evidence report rebuild failed after delete: %s", doc["doc_id"])


def file_row(doc, key):
    row = next((r for r in tagged(doc) if r["key"] == key), None)
    if row is None:
        raise HTTPException(404, detail={"error": "evidence_not_found"})
    return row


def preview(doc, key):
    row = file_row(doc, key)
    from modules.flow_gate.documents.attachments import service
    path, _ = service.resolve_download(row["doc_id"], row["filename"])
    suffix = Path(row["filename"]).suffix.lower()
    if suffix in (".csv", ".json", ".txt", ".log"):
        with path.open("rb") as stream:
            content = stream.read(8192).decode("utf-8", errors="replace")
        return {"kind": "text", "text": "\n".join(content.splitlines()[:12])}
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        with path.open("rb") as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) <= 2 * 1024 * 1024:
            import base64
            media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix, "image/" + suffix[1:])
            return {"kind": "image", "src": "data:" + media + ";base64," + base64.b64encode(data).decode()}
        return {"kind": "image", "download": True}
    return {"kind": "file"}


def record_execution(doc, run, case_id, exit_code, duration_ms, stderr):
    rows = test_runs.list_cases(run["run_id"])
    row = next((r for r in rows if spec.case_id_key(r.get("case_no") or "") == spec.case_id_key(case_id)), None)
    if row is None:
        raise HTTPException(409, detail={"error": "execution_case_not_found"})
    test_runs.update_case_observation(case_id=row["id"], exit_code=exit_code,
                                    duration_ms=duration_ms, output_tail=stderr[-4000:])
    locale = run.get("locale") or "ko"
    words = {"ko": ("정상 종료", "에러 종료", "종료 코드", "걸린 시간"),
             "ja": ("正常終了", "エラー終了", "終了コード", "所要時間"),
             "en": ("Normal exit", "Error exit", "Exit code", "Duration")}.get(locale)
    words = words or ("Normal exit", "Error exit", "Exit code", "Duration")
    summary = f"{'✓ ' + words[0] if exit_code == 0 else '✕ ' + words[1]} · {words[2]}: {exit_code} · {words[3]}: {duration_ms} ms\n"
    attempt = attempt_for(doc["doc_id"], case_id, run["run_id"])
    # Server-owned output of an already admitted execution, not a user upload.
    user = {"user_id": run["runner_id"], "is_admin": True}
    def attach(name, content):
        part = UploadFile(filename=name, file=io.BytesIO(content.encode("utf-8")))
        try:
            return flow.upload(doc["group_id"], part, user, "keep_both", test_evidence={
                "ts_doc_id": doc["doc_id"], "case_id": case_id, "kind": "log", "attempt": attempt})
        finally:
            part.file.close()
    # Attachment failure must not erase the observation or abort the following case.
    try:
        attach(f"{case_id}-attempt-{attempt}-exit.txt", summary)
        if exit_code != 0:
            attach(f"{case_id}-attempt-{attempt}-error.log", "\n".join(stderr.splitlines()[-200:]))
    except (AttachmentError, HTTPException) as exc:
        meta = spec.load_result_meta((test_runs.get_run(run["run_id"]) or run).get("result_meta"))
        meta.setdefault("evidence_errors", []).append({"case_id": case_id, "error": str(exc)})
        test_runs.set_run_result_meta(run["run_id"], json.dumps(meta, ensure_ascii=False))
