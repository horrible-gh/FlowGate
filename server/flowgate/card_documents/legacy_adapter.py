"""Reuse boundary of the card documents (flowgate.v02.0053 T#1, D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never
changed: the v0.1 document rows, numbering (``reserve_document``), the document service
(``create_document`` / ``delete_document``, with their ``doc_created`` / ``doc_deleted`` events),
the document file path, the empty work plan body, the Graph's document liveness / supersede
rules (so a card shows a document exactly as the Canvas does) and the v0.1 approval's empty body rule
(``approvable_body``, the one judgement behind ``lines.has_body``, flowgate.v02.0056 T#2).

flowgate.v02.0056 T#3: the review history counts of a line's document as the v0.1 document read carries the
history (``document_reviews`` rows, ``rejection_history`` items), the Graph API envelope / RBAC / Flow load,
disposal check and guard of the line write route, the project's registered types, and - for a node open in the
Flow's active run - the Graph's own judgements of what its result is (``result_binder.result_type`` /
``in_instruction_stage``), whether its AI still works (``card_settings_ports.ai_working``), its dispatch token,
and the registration of a result made with that token: inside the line write's Flow guard the token is claimed
(the compare-and-swap consume, ``token_consumed``) and the document submitted; after the commit the Graph is told
(the inbox's contact, ``after_inbox_registered``).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.api import envelope, v01_contact
from modules.flow_gate.graph_workflow.domain.canvas_mapping import CANCELLED_STATUS, is_alive
from modules.flow_gate.graph_workflow.domain.supersede import live_end
from modules.flow_gate.graph_workflow.projection.flow_snapshot import registered_doc_types
from modules.flow_gate.graph_workflow.services import canvas_service, card_settings_ports, graph_service
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.rbac.permission_service import has_permission

from flowgate.card_documents import repository
from flowgate.card_documents import slots as cs

_log = logging.getLogger(__name__)

TITLE_MAX_LEN = 100            # v0.1 related-document title limit

__all__ = ["CANCELLED_STATUS", "is_alive", "live_end", "flow_documents", "document", "supersede",
           "create_document", "delete_document", "approvable_body", "review_counts", "rejection_count",
           "GraphApiError", "with_flow_guard", "registered_doc_types", "authenticate", "authorize", "invalid", "run",
           "load_flow", "require_writable", "running_node", "node_result", "register_result", "discard_file",
           "tell_graph"]

CREATE_PERMISSION = "perm_document_create"     # the v0.1 document create routes' permission


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


def create_document(group: dict, node: dict, slot: cs.CardSlot, user_id: Optional[str],
                    body: Optional[str] = None) -> str:
    """An empty draft of the line's type, numbered and filed like a v0.1 related document; ``body`` (a person's
    line write, flowgate.v02.0056 T#3) is written after its front matter."""
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
        text = _front_matter(project_id, module, flow_id, slot.doc_type, code, title)
        body = text + body if body else text
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


# ── review history counts (flowgate.v02.0056 T#3) ────────────────────────────

def review_counts(doc_ids: list[str]) -> dict[str, int]:
    """``doc_id -> number of AI reviews``: the ``document_reviews`` rows the v0.1 document read shows as its
    ``ai_review_history`` (one query for all the lines of a read)."""
    wanted = sorted({d for d in doc_ids if d})
    if not wanted:
        return {}
    marks = ", ".join("?" for _ in wanted)
    try:
        found = get_store()._fetch_all(
            f"SELECT doc_id, COUNT(*) AS n FROM document_reviews WHERE doc_id IN ({marks}) GROUP BY doc_id", wanted)
    except Exception:  # noqa: BLE001 — a count is display only, as the document read's own history is
        _log.warning("card line review counts not read", exc_info=True)
        return {}
    return {r["doc_id"]: int(r["n"]) for r in found or []}


def rejection_count(doc: Optional[dict]) -> int:
    """The rejections of ``doc`` the v0.1 document read shows (its ``rejection_history`` items with a reason)."""
    from modules.flow_gate.workflow.pipeline_service import parse_rejection_history   # noqa: PLC0415

    history = parse_rejection_history((doc or {}).get("rejection_history"))
    return sum(1 for item in history if isinstance(item.get("reason"), str) and item["reason"].strip())


# ── a person's line write (flowgate.v02.0056 T#3) ────────────────────────────

def authenticate(request) -> str:
    return envelope.authenticate(request)


def authorize(user_id: str, group: dict) -> None:
    """A line's new document needs the v0.1 document create permission."""
    if not has_permission(user_id, group.get("project_id") or "", CREATE_PERMISSION):
        raise GraphApiError(403, "flow_access_denied", {"flow_id": group["group_id"], "required": "create"})


def invalid(request, errors: list[dict]) -> GraphApiError:
    return envelope.invalid(request, errors)


def run(request, handler, status: int = 200):
    return envelope.run(request, handler, status)


def load_flow(flow_id: str) -> dict:
    return canvas_service.load_flow(flow_id)


def require_writable(flow_id: str) -> None:
    graph_service.require_writable(flow_id)


def running_node(flow_id: str, node_key: str) -> Optional[dict]:
    """The node while it is open in the Flow's active run, judged as the result binder judges it:
    ``{run_id, result_type, instruction_stage, ai_working, token_id}`` — the type its result must have, whether
    it is in its card's instruction stage, whether its AI (or its card's review) still works, and its dispatch
    token not yet spent (the token a person's result is registered with, L0009 E-23; None when there is none).
    None when the node is not open in the active run."""
    from modules.flow_gate.graph_workflow.services import result_binder, run_apply   # noqa: PLC0415

    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return None
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == node_key), None)
    if row is None or row["status"] != "open":
        return None
    project_id = (db_groups.get_by_id(flow_id) or {}).get("project_id")
    ctx = run_apply.load_context(run_row, project_id, None, [])
    node = ctx.node_rows.get(node_key) or {"node_key": node_key}
    return {"run_id": run_row["run_id"], "result_type": result_binder.result_type(ctx, node, row),
            "instruction_stage": result_binder.in_instruction_stage(row),
            "ai_working": card_settings_ports.ai_working(flow_id, node_key, None),
            "token_id": _dispatch_token(run_row["run_id"], node_key)}


def node_result(flow_id: str, node_key: str) -> Optional[str]:
    """The result bound to the node in the Flow's active run, or None."""
    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return None
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == node_key), None)
    return (row or {}).get("result_doc_id")


def _dispatch_token(run_id: str, node_key: str) -> Optional[str]:
    """The node's dispatch token (mapped with no stage, minted for no AI run), while it is neither spent nor
    revoked."""
    row = get_store()._fetch_one(
        "SELECT m.token_id FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND m.node_key = ? AND m.stage IS NULL AND t.ai_run_id IS NULL "
        "AND t.consumed_at IS NULL AND t.revoked_at IS NULL ORDER BY m.mapped_at, m.token_id LIMIT 1",
        [run_id, node_key])
    return row["token_id"] if row else None


def register_result(doc_id: str, token_id: str, user_id: str) -> bool:
    """Inside the line write's Flow guard: register a person's result made with the node's dispatch token.

    The token is claimed first (``token_service.consume(require_claim=True)``: a compare-and-swap, so of two
    writers only one ever sees True, and only the winner records ``token_consumed`` with its document — the
    evidence the run reconciliation binds from); then the document opens and is submitted (pending_review), as the
    inbox registers one (``inbox_routes._handle_new``). False — nothing written — when the token was already spent;
    the caller then refuses and its guard's transaction rolls the document back."""
    from modules.flow_gate.db.connection import now_iso   # noqa: PLC0415
    from modules.flow_gate.services import token_service   # noqa: PLC0415
    from modules.flow_gate.workflow.pipeline_service import transition_document_review   # noqa: PLC0415
    from modules.flow_gate.workflow.transition_rules import get_doc_rule   # noqa: PLC0415

    doc = db_documents.get_by_id(doc_id) or {}
    if not token_service.consume(token_id=token_id, project_id=doc.get("project_id") or "", doc_id=doc_id,
                                 require_claim=True):
        return False
    rule = get_doc_rule(str(doc.get("status") or ""), "submit")
    if rule is not None:
        db_documents.update(doc_id, {"status": rule.next_state, "updated_at": now_iso()})   # draft -> open
    transition_document_review(doc_id=doc_id, action="submit", actor_user_id=user_id,
                               user_permissions={"document.update"})
    return True


def tell_graph(flow_id: str, doc_id: str, doc_type: str, token_id: str) -> None:
    """After the registration committed: the Graph contact of the inbox (the result binder binds the document as
    the token's node's result). Never raises; a lost contact is repaired by the run reconciliation, which reads the
    ``token_consumed`` record."""
    v01_contact.after_inbox_registered(flow_id, doc_id, doc_type, token_id)


def discard_file(doc_id: str) -> None:
    """The file of a document made in a transaction that is about to roll back (its rows go, its file would stay)."""
    from modules.flow_gate.storage import paths as storage_paths   # noqa: PLC0415

    doc = db_documents.get_by_id(doc_id) or {}
    if not doc.get("file_path"):
        return
    try:
        path = storage_paths.resolve_storage_path(doc["file_path"], doc.get("project_id") or "",
                                                  branch=repository.project_branch(doc.get("project_id") or ""))
        if path is not None:
            path.unlink()
    except Exception:  # noqa: BLE001 — a stray file is only untidy; the refusal still holds
        _log.warning("card document file not discarded doc=%s", doc_id, exc_info=True)
