"""Card placement and run start of a chat (stage 1). Everything is the Graph's own write path:

- placement: one whole-draft save (``graph_service.save_draft``: ownership, run lock, draft version, protected cards,
  card documents and new card settings), built on the current draft (else the latest revision);
- start: the draft, if any, confirmed against the latest revision (``confirm_service.confirm_revision``), then
  ``run_service.start_run`` in ``full`` mode (an idle or paused run goes on with the latest revision, as the Run
  button does).

A start refuses work cards whose instruction line has no body (409 ``chat_run_instruction_missing``) unless the
caller allows their instruction AI: the chat is meant to write the instruction itself (0002-CH turn 30).
"""
from __future__ import annotations

from typing import Any, Optional

from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import repository as card_lines_store
from flowgate.card_documents import slots as cs
from modules.flow_gate.graph_workflow.domain import draft_request as dr
from modules.flow_gate.graph_workflow.domain.graph_model import LABEL_MAX_LEN
from modules.flow_gate.graph_workflow.services import confirm_service, graph_service, run_service
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.node_title_names import PALETTE_KEY_OF_DOC_TYPE, PALETTE_NAMES
from modules.flow_gate.graph_workflow.store import definition_store, run_store

from . import placement

WORK_TYPES = ("T", "TS")
MAX_CARDS = 5
PROTECTED = frozenset({"done", "open", "review"})
GATE_LABELS = {"ko": "확인", "ja": "確認", "en": "Confirm"}
RUN_FIELDS = ("run_id", "status", "revision_no", "mode", "idle_reason", "open_node_keys", "pending_node_keys")
NEXT_AFTER_PLACE = ("Write each card's instruction into its instruction line (card document edit, slot=instruction): "
                    "the user's own words, facts you checked (file:line), what was decided and rejected, the done "
                    "criteria and what not to do. Then start the run (start_flow_run).")
NEXT_AFTER_START = ("The cards now run (work AI, then review). This chat is not woken when the run stops: tell the "
                    "user what you started and ask them to send a message such as \"결과 봐\" when it is done; then "
                    "read runs/current and the canvas and answer from them.")


def _invalid(field: str, reason: str) -> GraphApiError:
    return GraphApiError(422, "invalid_request", {"errors": [{"field": field, "reason": reason}]})


def _locale(locale: Optional[str]) -> str:
    return locale if locale in ("ko", "ja", "en") else "ko"


def parse_place(body: Any, locale: Optional[str]) -> tuple[list[dict], Optional[str], bool]:
    if not isinstance(body, dict):
        raise _invalid("body", "not_an_object")
    unknown = set(body) - {"cards", "after", "gate"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "unknown_field")
    raw = body.get("cards")
    if not isinstance(raw, list) or not raw:
        raise _invalid("cards", "required_non_empty_list")
    if len(raw) > MAX_CARDS:
        raise _invalid("cards", f"at_most_{MAX_CARDS}")
    cards = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - {"label", "doc_type"}:
            raise _invalid(f"cards[{i}]", "only_label_and_doc_type")
        doc_type = item.get("doc_type", "T")
        if doc_type not in WORK_TYPES:
            raise _invalid(f"cards[{i}].doc_type", "not_one_of:" + ",".join(WORK_TYPES))
        palette = PALETTE_KEY_OF_DOC_TYPE[doc_type]
        label = item.get("label")
        if label is None:
            label = PALETTE_NAMES[palette][_locale(locale)]
        if not isinstance(label, str) or not label.strip() or len(label) > LABEL_MAX_LEN:
            raise _invalid(f"cards[{i}].label", "label_length")
        cards.append({"doc_type": doc_type, "label": label.strip(), "palette_item_id": palette})
    after = body.get("after")
    if after is not None and (not isinstance(after, str) or not after):
        raise _invalid("after", "not_a_node_key")
    gate = body.get("gate", True)
    if not isinstance(gate, bool):
        raise _invalid("gate", "not_a_boolean")
    return cards, after, gate


def _definition(flow_id: str) -> tuple[dict, int]:
    """(the definition the chat changes, its draft version): the draft, else the latest revision as version 0."""
    draft = definition_store.draft(flow_id)
    if draft is not None:
        return draft, int(draft["draft_version"])
    latest_no = definition_store.latest_revision_no(flow_id)
    latest = definition_store.revision(flow_id, latest_no) if latest_no else None
    return latest or {"nodes": [], "edges": []}, 0


def _run_rows(flow_id: str) -> dict[str, dict]:
    run = run_store.current_run(flow_id)
    return {r["node_key"]: r for r in run_store.node_runs(run["run_id"])} if run else {}


def _instruction_line(flow_id: str, node_key: str) -> dict:
    row = next((r for r in card_lines_store.rows(flow_id, [node_key]) if r["slot"] == cs.INSTRUCTION), None)
    return {"slot": cs.INSTRUCTION, "doc_id": row["doc_id"] if row else None}


def place(group: dict, user_id: str, body: Any, locale: Optional[str] = None) -> dict:
    """Place the chat's work cards (and a confirm card when needed) on the Flow's draft. Nothing is confirmed or run."""
    flow_id = group["group_id"]
    cards, after, want_gate = parse_place(body, locale)
    record, version = _definition(flow_id)
    rows = _run_rows(flow_id)
    protected = {k for k, r in rows.items() if r.get("status") in PROTECTED}
    try:
        chosen = placement.plan(record.get("nodes", []), record.get("edges", []), protected, after, want_gate)
    except placement.PlacementError as exc:
        status = 404 if exc.code == "chat_cards_anchor_unknown" else 409
        raise GraphApiError(status, exc.code, {"flow_id": flow_id, **exc.params}) from exc
    request = dr.parse_draft(placement.draft_body(record, version, chosen, cards, GATE_LABELS[_locale(locale)]))
    if request.errors or request.draft_version is None:
        raise GraphApiError(422, "chat_cards_draft_invalid", {"flow_id": flow_id,
                                                              "errors": dr.sort_errors(request.errors)})
    saved = graph_service.save_draft(group, user_id, request, None, _locale(locale))
    key_map = saved.get("key_map") or {}
    placed = []
    for i, card in enumerate(cards):
        key = key_map[f"{placement.CLIENT_NODE}{i}"]
        placed.append({"node_key": key, "label": card["label"], "doc_type": card["doc_type"],
                       "instruction": _instruction_line(flow_id, key)})
    gate = None
    if chosen.before is not None:
        gate = {"node_key": chosen.before, "created": False}
    elif chosen.new_gate:
        gate = {"node_key": key_map[placement.CLIENT_GATE], "created": True}
    return {"flow_id": flow_id, "draft_version": saved.get("draft_version"), "after": chosen.anchor,
            "cards": placed, "gate": gate, "next": NEXT_AFTER_PLACE}


def parse_start(body: Any) -> bool:
    if body is None:
        return False
    if not isinstance(body, dict):
        raise _invalid("body", "not_an_object")
    unknown = set(body) - {"allow_instruction_ai"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "unknown_field")
    allow = body.get("allow_instruction_ai", False)
    if not isinstance(allow, bool):
        raise _invalid("allow_instruction_ai", "not_a_boolean")
    return allow


def missing_instructions(flow_id: str, record: dict) -> list[str]:
    """Work cards still to run whose instruction line has no written body (the instruction AI would write one)."""
    rows = _run_rows(flow_id)
    out = []
    for node in record.get("nodes", []):
        if node.get("node_type") != "document" or node.get("doc_type") not in WORK_TYPES:
            continue
        if (rows.get(node["node_key"]) or {}).get("status") in PROTECTED:
            continue
        line = card_lines.line_document(flow_id, node["node_key"], cs.INSTRUCTION, node.get("node_type"),
                                        node.get("doc_type"))
        if line is None:
            continue                                    # the card has no instruction line
        if not card_lines.has_body(line.get("document")):
            out.append(node["node_key"])
    return sorted(out)


def _run_view(run: Optional[dict]) -> Optional[dict]:
    if not isinstance(run, dict):
        return None
    out = {k: run.get(k) for k in RUN_FIELDS if k in run}
    if isinstance(run.get("node_runs"), list):
        out["node_runs"] = [{"node_key": n.get("node_key"), "status": n.get("status"), "stage": n.get("stage")}
                            for n in run["node_runs"] if isinstance(n, dict)]
    return out


def start(group: dict, user_id: str, body: Any = None) -> dict:
    """Confirm the chat's draft (when there is one) and start / go on with the Flow's run in ``full`` mode."""
    flow_id = group["group_id"]
    allow = parse_start(body)
    draft = definition_store.draft(flow_id)
    latest = definition_store.latest_revision_no(flow_id)
    record = draft if draft is not None else (definition_store.revision(flow_id, latest) if latest else None)
    if record is None:
        raise GraphApiError(404, "revision_not_found", {"flow_id": flow_id, "revision_no": None})
    if not allow:
        missing = missing_instructions(flow_id, record)
        if missing:
            raise GraphApiError(409, "chat_run_instruction_missing", {"flow_id": flow_id, "node_keys": missing})
    confirmed = None
    if draft is not None:
        confirmed = confirm_service.confirm_revision(group, user_id, int(draft["draft_version"]), latest,
                                                     None)["revision_no"]
    result = run_service.start_run(group, user_id, None, "full", None)
    return {"flow_id": flow_id, "confirmed_revision_no": confirmed, "run": _run_view(result.get("run")),
            "next": NEXT_AFTER_START}
