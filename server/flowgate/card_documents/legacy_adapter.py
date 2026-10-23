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

flowgate.v02.0083 T#3: a person's work plan is registered as it is (the card's own plan draft, filled in its editor):
the work sets its stored plan counts (``work_plan_service.totals``), and whether the card's AI or review still works
on the node whatever its state (``card_settings_ports.ai_working`` with the node's bound result).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import after_rollback, get_store, in_transaction
from modules.flow_gate.storage.files import StorageUnwritable, write_new_file
from modules.flow_gate.graph_workflow.api import envelope, v01_contact
from modules.flow_gate.graph_workflow.domain.canvas_mapping import CANCELLED_STATUS, is_alive
from modules.flow_gate.graph_workflow.domain.supersede import live_end
from modules.flow_gate.graph_workflow.projection.flow_snapshot import registered_doc_types
from modules.flow_gate.graph_workflow.services import canvas_service, card_settings_ports, graph_service
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.services.root_conversion_cards import ROOT_NAMES
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.rbac.permission_service import has_permission

from flowgate.card_documents import repository
from flowgate.card_documents import slots as cs

_log = logging.getLogger(__name__)

TITLE_MAX_LEN = 100            # v0.1 related-document title limit

__all__ = ["CANCELLED_STATUS", "is_alive", "live_end", "flow_documents", "document", "supersede",
           "create_document", "delete_document", "approvable_body", "review_counts", "rejection_count",
           "GraphApiError", "with_flow_guard", "registered_doc_types", "authenticate", "authorize", "invalid", "run",
           "locale_of",
           "load_flow", "require_writable", "running_node", "node_result", "register_result", "discard_file",
           "tell_graph", "work_sets", "card_working"]

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


def line_body_facts(doc: Optional[dict]) -> tuple[bool, Optional[str]]:
    """One read supplies both line facts; an unreadable line cannot break Canvas."""
    if doc is None:
        return False, None
    from modules.flow_gate.storage.body import body_read_state
    from modules.flow_gate.workflow import pipeline_service

    try:
        state, text = body_read_state(doc)
    except (OSError, UnicodeError):
        _log.warning("card line body read failed: %s", doc.get("doc_id"), exc_info=True)
        return False, "read_error"
    # These types have always bypassed the file/body approval requirement.
    # Preserve has_body while exposing their independently measured body state.
    kind = str(doc.get("type_code") or "").upper()
    if kind in pipeline_service.AUTO_COMPLETE_TYPES or kind in pipeline_service.FILELESS_APPROVABLE_TYPES:
        return approvable_body(doc), state
    if state != "readable":
        return False, state
    return pipeline_service.approvable_text(doc, text), state


def supersede(old_id: str, new_id: str) -> None:
    db_documents.set_superseded_by(old_id, new_id)


def delete_document(doc_id: str, user_id: Optional[str]) -> None:
    from modules.flow_gate.documents import document_service   # noqa: PLC0415 — v0.1 stack on use

    document_service.delete_document(doc_id, actor_user_id=user_id or "")


def create_document(group: dict, node: dict, slot: cs.CardSlot, user_id: Optional[str],
                    body: Optional[str] = None, *, locale: Optional[str] = None,
                    resolved_title: Optional[str] = None) -> str:
    """An empty draft of the line's type, numbered and filed like a v0.1 related document; ``body`` (a person's
    line write, flowgate.v02.0056 T#3) is written after its front matter.

    flowgate.v02.0088 D0010 §3.1: the title is made in ``locale``, the creating request's (ko / ja / en; none or
    anything else is ko). ``resolved_title`` (internal only, never an API input) is a title the caller already
    decided with ``_title`` for the same document: a Markdown line takes it as is for its row and front matter. A
    work plan always derives its own title."""
    from modules.flow_gate.documents import document_service   # noqa: PLC0415
    from modules.flow_gate.numbering import numbering_service  # noqa: PLC0415

    flow_id, project_id = group["group_id"], group.get("project_id") or ""
    module = group.get("module") or "none"
    code = numbering_service.reserve_document(group_id=flow_id, doc_type=slot.doc_type, module=module)
    doc_id = f"{flow_id}.{code}"
    created_empty = not (body or "").strip()
    if slot.doc_type == "WP":
        body, title = _work_plan_body(project_id, locale)
    else:
        title = resolved_title if resolved_title is not None else _title(node.get("label") or "", slot, locale)
        text = _front_matter(project_id, module, flow_id, slot.doc_type, code, title)
        body = text + body if body else text
    file_path = _write_file(project_id, flow_id, module, code, slot.slot, body, node["node_key"])
    try:
        document_service.create_document({
            "doc_id": doc_id, "project_id": project_id, "module": module, "group_id": flow_id,
            "type_code": slot.doc_type, "seq": int(code.split("-", 1)[0]), "title": title, "status": "draft",
            "owner_id": user_id, "file_path": file_path,
            "meta": json.dumps({"graph_card": {"node_key": node["node_key"], "slot": slot.slot,
                                               "created_empty": created_empty}}, ensure_ascii=False),
        }, actor_user_id=user_id or "")
    except BaseException:
        if not in_transaction():
            from modules.flow_gate.storage.paths import resolve_storage_path
            try:
                path = resolve_storage_path(file_path, project_id, repository.project_branch(project_id))
                if path is not None:
                    path.unlink(missing_ok=True)
            except OSError:
                _log.warning("card document cleanup failed: %s", file_path, exc_info=True)
        raise
    return doc_id


def _title(label: str, slot: cs.CardSlot, locale: Optional[str] = None) -> str:
    """A new line document's title (flowgate.v02.0088 D0010 §3.2): a blank label or a known default name is the
    line's default name in ``locale`` for the type the document is (``slot.doc_type``, never guessed from the
    label); a person's label keeps its text and takes that name after it."""
    line = cs.slot_title(slot.slot, locale, slot.doc_type)
    label = label.strip()
    if not label or cs.name_key(label) in _default_names(slot):
        return line
    suffix = f" · {line}"
    return label[: TITLE_MAX_LEN - len(suffix)] + suffix


def _default_names(slot: cs.CardSlot) -> set[str]:
    """The labels taken as no label of their own: the line's names in every locale, and on the requirement line of
    an R / B document also the R / B card names (``요구사항``, ``버그``, ``バグ``, ``Bug`` ...)."""
    names = set()
    for titles in cs.SLOT_TITLES.values():
        value = titles[slot.slot]
        names.update(cs.name_key(name) for name in ((value,) if isinstance(value, str) else value))
    if slot.slot == cs.REQUIREMENT and slot.doc_type in cs.ROOT_TYPES:
        names.update(cs.name_key(name) for kind in cs.ROOT_TYPES for name in ROOT_NAMES[kind].values())
    return names


def _front_matter(project_id: str, module: str, flow_id: str, doc_type: str, code: str, title: str) -> str:
    """One rule with the body recovery's empty card draft (0064 T-B1d, L0005 2.12 ``card_draft_bytes``)."""
    from modules.flow_gate.documents.body_recovery import card_front_matter   # noqa: PLC0415

    return card_front_matter(project_id, module, flow_id, doc_type, code, title)


def _work_plan_body(project_id: str, locale: Optional[str] = None) -> tuple[str, str]:
    """A valid, undecided plan (the WP reader opens nothing else); WP titles are derived, in the creating
    request's locale (flowgate.v02.0088 D0010 §3.3)."""
    from modules.flow_gate.services import work_plan_service as wp   # noqa: PLC0415

    body = wp.empty_recovery_body(project_id or None)
    return wp.dumps(body), wp.derived_title(body, cs.title_locale(locale))


def _write_file(project_id: str, flow_id: str, module: str, code: str, slot: str, body: str,
                node_key: str) -> str:
    """Only return a storage-relative path after its file was successfully written."""
    from modules.flow_gate.storage import paths as storage_paths
    from modules.flow_gate.db.group_events import insert_group_event

    try:
        path = storage_paths.document_path(project_id=project_id, group_code=flow_id, doc_code=code,
                                           filename=f"{slot}.md", module=module,
                                           branch=repository.project_branch(project_id))
        write_new_file(path, body.encode("utf-8"))
        return storage_paths.to_storage_relative(path, project_id)
    except (StorageUnwritable, OSError) as exc:
        error = exc if isinstance(exc, StorageUnwritable) else StorageUnwritable(exception=type(exc).__name__)
        error.params.update(node_key=node_key, slot=slot)
        _log.warning("card document file not written flow=%s code=%s", flow_id, code, exc_info=True)
        reason = error.exception
        note = json.dumps({"flow_id": flow_id, "code": code, "slot": slot, "exception": reason}, ensure_ascii=False)
        def record_failure():
            insert_group_event(flow_id, "doc_storage_write_failed", reason=reason, note=note)
        if not after_rollback(record_failure):
            try:
                record_failure()
            except Exception:
                _log.warning("card storage failure event not written", exc_info=True)
        if error is exc:
            raise
        raise error from exc


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


def locale_of(request) -> str:
    """The request's X-Locale as the Graph envelope normalizes it (ko / ja / en, else ko)."""
    return envelope.locale_of(request)


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


def card_working(flow_id: str, node_key: str) -> bool:
    """flowgate.v02.0083 T#3: the node of the Flow's active run (open or in review) has its AI or its card's review
    still working on it — the card's review loop is judged on the node's bound result."""
    run_row = run_store.active_run(flow_id)
    if run_row is None:
        return False
    row = next((r for r in run_store.node_runs(run_row["run_id"]) if r["node_key"] == node_key), None)
    if row is None:
        return False
    return card_settings_ports.ai_working(flow_id, node_key, row.get("result_doc_id"))


def work_sets(doc: dict) -> int:
    """flowgate.v02.0083 T#3: the work sets the stored work plan of ``doc`` counts (``work_plan_service.totals``, the
    「작업 n세트」 of its derived title); 0 when the plan cannot be read."""
    from modules.flow_gate.services import work_plan_service as wp   # noqa: PLC0415

    try:
        body = wp.load_body(wp.plan_path_for_doc(doc), project_id=doc.get("project_id"), doc_id=doc.get("doc_id"))
    except Exception:  # noqa: BLE001 — an unreadable plan is no written plan
        _log.warning("card work plan not read doc=%s", doc.get("doc_id"), exc_info=True)
        return 0
    return int(wp.totals(body).get("work_sets") or 0)


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
