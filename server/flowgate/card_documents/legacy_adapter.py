"""Reuse boundary of the card documents (flowgate.v02.0053 T#1, D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never
changed: the v0.1 document rows, numbering (``reserve_document``), the document service
(``create_document`` / ``delete_document``, with their ``doc_created`` / ``doc_deleted`` events),
the document file path, the empty work plan body, the Graph's document liveness / supersede
rules (so a card shows a document exactly as the Canvas does) and the v0.1 approval's empty body rule
(``approvable_body``, the one judgement behind ``lines.has_body``, flowgate.v02.0056 T#2).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.graph_workflow.domain.canvas_mapping import CANCELLED_STATUS, is_alive
from modules.flow_gate.graph_workflow.domain.supersede import live_end

from flowgate.card_documents import repository
from flowgate.card_documents import slots as cs

_log = logging.getLogger(__name__)

TITLE_MAX_LEN = 100            # v0.1 related-document title limit

__all__ = ["CANCELLED_STATUS", "is_alive", "live_end", "flow_documents", "document", "supersede",
           "create_document", "delete_document", "approvable_body"]


def flow_documents(flow_id: str) -> list[dict]:
    return list(db_documents.get_documents_by_group_id(flow_id) or [])


def document(doc_id: str) -> Optional[dict]:
    return db_documents.get_by_id(doc_id)


def approvable_body(doc: dict) -> bool:
    """The v0.1 approval would not refuse ``doc`` as empty (``_require_document_body_for_approval``): its
    file is readable and holds a non-blank body after the front matter."""
    from modules.flow_gate.workflow import pipeline_service   # noqa: PLC0415 — v0.1 stack on use

    try:
        pipeline_service._require_document_body_for_approval(doc)
    except pipeline_service.TransitionError:
        return False
    return True


def supersede(old_id: str, new_id: str) -> None:
    db_documents.set_superseded_by(old_id, new_id)


def delete_document(doc_id: str, user_id: Optional[str]) -> None:
    from modules.flow_gate.documents import document_service   # noqa: PLC0415 — v0.1 stack on use

    document_service.delete_document(doc_id, actor_user_id=user_id or "")


def create_document(group: dict, node: dict, slot: cs.CardSlot, user_id: Optional[str]) -> str:
    """An empty draft of the line's type, numbered and filed like a v0.1 related document."""
    from modules.flow_gate.documents import document_service   # noqa: PLC0415
    from modules.flow_gate.numbering import numbering_service  # noqa: PLC0415

    flow_id, project_id = group["group_id"], group.get("project_id") or ""
    module = group.get("module") or "none"
    code = numbering_service.reserve_document(group_id=flow_id, doc_type=slot.doc_type, module=module)
    doc_id = f"{flow_id}.{code}"
    title = _title(node.get("label") or "", slot)
    if slot.doc_type == "WP":
        body, title = _work_plan_body(project_id)
    else:
        body = _front_matter(project_id, module, flow_id, slot.doc_type, code, title)
    document_service.create_document({
        "doc_id": doc_id, "project_id": project_id, "module": module, "group_id": flow_id,
        "type_code": slot.doc_type, "seq": int(code.split("-", 1)[0]), "title": title, "status": "draft",
        "owner_id": user_id, "file_path": _write_file(project_id, flow_id, module, code, slot.slot, body),
        "meta": json.dumps({"graph_card": {"node_key": node["node_key"], "slot": slot.slot}}, ensure_ascii=False),
    }, actor_user_id=user_id or "")
    return doc_id


def _title(label: str, slot: cs.CardSlot) -> str:
    suffix = f" · {cs.slot_title(slot.slot)}"
    return label.strip()[: TITLE_MAX_LEN - len(suffix)] + suffix


def _front_matter(project_id: str, module: str, flow_id: str, doc_type: str, code: str, title: str) -> str:
    lines = ["---", f"project: {project_id}", f"module: {module}", f"group: {flow_id.rsplit('.', 1)[-1]}",
             f"group_id: {flow_id}", f"type: {doc_type}", f"doc_number: {code}", f"title: {title}", "---", ""]
    return "\n".join(lines)


def _work_plan_body(project_id: str) -> tuple[str, str]:
    """A valid, undecided plan (the WP reader opens nothing else); WP titles are derived."""
    from modules.flow_gate.services import work_plan_service as wp   # noqa: PLC0415

    body = wp.empty_recovery_body(project_id or None)
    return wp.dumps(body), wp.derived_title(body, cs.DEFAULT_LOCALE)


def _write_file(project_id: str, flow_id: str, module: str, code: str, slot: str, body: str) -> Optional[str]:
    """The document file (storage-relative), or None when it cannot be written (v0.1 precedent:
    the row is still registered and ``regenerate`` recovers the file)."""
    from modules.flow_gate.storage import paths as storage_paths   # noqa: PLC0415

    try:
        path = storage_paths.document_path(project_id=project_id, group_code=flow_id, doc_code=code,
                                           filename=f"{slot}.md", module=module,
                                           branch=repository.project_branch(project_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return storage_paths.to_storage_relative(path, project_id)
    except Exception:  # noqa: BLE001
        _log.warning("card document file not written flow=%s code=%s", flow_id, code, exc_info=True)
        return None
