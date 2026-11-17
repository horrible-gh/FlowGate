"""Card placement and run start of a chat (stage 1). Everything is the Graph's own write path:

- placement: one whole-draft save (``graph_service.save_draft``: ownership, run lock, draft version, protected cards,
  card documents and new card settings), built on the current draft (else the latest revision);
- start: the draft, if any, confirmed against the latest revision (``confirm_service.confirm_revision``), then
  ``run_service.start_run`` in ``full`` mode (an idle or paused run goes on with the latest revision, as the Run
  button does).

A start refuses work cards whose instruction line has no body (409 ``chat_run_instruction_missing``) unless the
caller allows their instruction AI: the chat is meant to write the instruction itself (0002-CH turn 30).

0002-CH turn 48 (test server E2E):
- a new group's Flow has no card yet (its bug / requirement is a free document on the Canvas): placement starts the
  chain with a start card of the Flow's one live root (``placement`` ``new_root``); two live roots are ambiguous (409
  ``chat_cards_root_ambiguous``), none leaves it unplaceable (409 ``chat_cards_flow_empty``);
- a start answers why a card already waits instead of running (``waiting``: each node run's ``wait_reason`` /
  ``intervention``, e.g. ``manual_write_wait`` for a card with no AI), so the chat can tell the user the cause.

flowgate.v02gui.0028: after the draft save a placement records the placing chat of each new work card (``owner``:
its CH conversation document, token, AI run, user; ``card_role_store``), the only chat the card role API
(``card_roles``) lets read or change that card's AI roles.

flowgate.v02gui.0032 (decision 8): the chat chooses each card's AI roles itself. A placed card may carry ``roles``
(``{instruction|execution|review: {kind, provider_id?}}``, the card role PATCH grammar); they are checked before the
save (``placement_roles.precheck``: 422 ``ai_not_selectable``) and written in the draft save's own Flow guard after
the defaults were copied (``placement_roles.write``), so a refusal places nothing. The answer gives each card's roles
and where they came from (``chat`` / ``default`` / ``person`` / ``unset``); roles left on a default are listed in
``defaults_used`` for the chat to choose.

flowgate.v02gui.0032 (0002-CH seq 15-17): a placed card may also carry ``settings`` — its review count, restarts and
time limit (``run_settings``: a test card has only the review count), the values the person's AI tab offers. They are
checked before the save (422 with the field's ``choices``) and written in the same guard as the roles (one
``chat_card_setting_audit`` row per setting when the placing chat is known); the answer gives each card's values now.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import repository as card_lines_store
from flowgate.card_documents import slots as cs
from modules.flow_gate.graph_workflow.domain import draft_request as dr
from modules.flow_gate.graph_workflow.domain.graph_model import LABEL_MAX_LEN
from modules.flow_gate.graph_workflow.domain.root_binding import doc_key, resolve_root
from modules.flow_gate.graph_workflow.projection.flow_snapshot import alive_documents, read_snapshot
from modules.flow_gate.graph_workflow.services import confirm_service, graph_service, root_start, run_service
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.node_title_names import PALETTE_KEY_OF_DOC_TYPE, PALETTE_NAMES
from modules.flow_gate.graph_workflow.services.root_conversion_cards import ROOT_NAMES, ROOT_PALETTE_IDS
from modules.flow_gate.graph_workflow.store import definition_store, run_store

from . import card_role_store, placement, placement_roles, run_settings, topology

_log = logging.getLogger(__name__)
WORK_TYPES = ("T", "TS")
MAX_CARDS = 5
PROTECTED = frozenset({"done", "open", "review"})
GATE_LABELS = {"ko": "확인", "ja": "確認", "en": "Confirm"}
RUN_FIELDS = ("run_id", "status", "revision_no", "mode", "idle_reason", "open_node_keys", "pending_node_keys")
NEXT_AFTER_PLACE = ("Tell the user each card's AI and review count with a short reason. Write each card's instruction "
                    "into its instruction line (card document edit, slot=instruction): the user's own words, facts you checked (file:line), what was decided and rejected, the done "
                    "criteria and what not to do. Then start the run (start_flow_run).")
NEXT_DEFAULTS_USED = ("Roles in `defaults_used` came from the AI role group / account defaults, not from you: choose "
                      "them yourself (set_card_roles) unless the user asked for those defaults or no AI fits.")
NEXT_AFTER_START = ("The cards now run (work AI, then review). This chat is not woken when the run stops: tell the "
                    "user what you started and ask them to send a message such as \"결과 봐\" when it is done; then "
                    "read runs/current and the canvas and answer from them.")
NEXT_WHEN_WAITING = ("Some cards already wait instead of running (`waiting`). manual_write_wait: the card has no AI for "
                     "that role, so it waits for a person; ask the user to set the card's AI (card settings) or write "
                     "it, then start the run again. ai_setup_required / ai_unavailable: the card's AI is not set up / "
                     "cannot run now (see detail). needs_intervention / system_error: a card in review that nothing "
                     "completes (see detail). Tell the user which card waits and why; never report the cause as unknown.")
WAIT_KEYS = ("code", "role", "stage", "seq", "detail")


def _invalid(field: str, reason: str) -> GraphApiError:
    return GraphApiError(422, "invalid_request", {"errors": [{"field": field, "reason": reason}]})


def _locale(locale: Optional[str]) -> str:
    return locale if locale in ("ko", "ja", "en") else "ko"


def parse_place(body: Any, locale: Optional[str]) -> tuple[list[dict], Optional[str], bool]:
    if not isinstance(body, dict):
        raise _invalid("body", "not_an_object")
    unknown = set(body) - {"cards", "after", "gate", "links", "join"}
    if unknown:
        raise _invalid(sorted(unknown)[0], "unknown_field")
    raw = body.get("cards")
    if not isinstance(raw, list) or (not raw and "links" not in body):
        raise _invalid("cards", "required_non_empty_list")
    if len(raw) > MAX_CARDS:
        raise _invalid("cards", f"at_most_{MAX_CARDS}")
    cards = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - {"label", "doc_type", "roles", "settings"}:
            raise _invalid(f"cards[{i}]", "only_label_doc_type_roles_and_settings")
        doc_type = item.get("doc_type", "T")
        if doc_type not in WORK_TYPES:
            raise _invalid(f"cards[{i}].doc_type", "not_one_of:" + ",".join(WORK_TYPES))
        palette = PALETTE_KEY_OF_DOC_TYPE[doc_type]
        label = item.get("label")
        if label is None:
            label = PALETTE_NAMES[palette][_locale(locale)]
        if not isinstance(label, str) or not label.strip() or len(label) > LABEL_MAX_LEN:
            raise _invalid(f"cards[{i}].label", "label_length")
        card = {"doc_type": doc_type, "label": label.strip(), "palette_item_id": palette}
        roles = placement_roles.parse_card_roles(item.get("roles"), f"cards[{i}].roles")
        if roles:
            card["roles"] = roles
        chosen_settings = run_settings.parse(item.get("settings"), f"cards[{i}].settings")
        if chosen_settings:
            card["settings"] = chosen_settings
        cards.append(card)
    after = body.get("after")
    if after is not None and (not isinstance(after, str) or not after):
        raise _invalid("after", "not_a_node_key")
    gate = body.get("gate", True)
    if not isinstance(gate, bool):
        raise _invalid("gate", "not_a_boolean")
    topology.parse(body, len(cards))
    return cards, after, gate


def _definition(flow_id: str) -> tuple[dict, int]:
    """(the definition the chat changes, its draft version): the draft, else the latest revision as version 0."""
    draft = definition_store.draft(flow_id)
    if draft is not None:
        return draft, int(draft["draft_version"])
    latest_no = definition_store.latest_revision_no(flow_id)
    latest = definition_store.revision(flow_id, latest_no) if latest_no else None
    return latest or {"nodes": [], "edges": []}, 0


def _root(flow_id: str) -> Optional[dict]:
    """The Flow's one live root (``doc_id``, ``doc_type``) a card-less definition starts from; None: no root."""
    status, candidates = resolve_root(alive_documents(read_snapshot(flow_id)))
    if status == "none":
        return None
    if status == "ambiguous":
        raise GraphApiError(409, "chat_cards_root_ambiguous",
                            {"flow_id": flow_id, "doc_ids": [d["doc_id"] for d in candidates]})
    doc = candidates[0]
    return {"doc_id": doc["doc_id"], "doc_type": str(doc.get("type_code") or "").upper()}


def _run_rows(flow_id: str) -> dict[str, dict]:
    run = run_store.current_run(flow_id)
    return {r["node_key"]: r for r in run_store.node_runs(run["run_id"])} if run else {}


def _instruction_line(flow_id: str, node_key: str) -> dict:
    row = next((r for r in card_lines_store.rows(flow_id, [node_key]) if r["slot"] == cs.INSTRUCTION), None)
    return {"slot": cs.INSTRUCTION, "doc_id": row["doc_id"] if row else None}


def _record_owner(flow_id: str, node_keys: list[str], owner: Optional[dict]) -> bool:
    """flowgate.v02gui.0028: the placing chat of each new work card (``card_role_store.chat_placed_cards``), recorded
    after the draft save committed. Only the work cards: the start card and the confirm card have no AI roles, so the
    chat card role API never needs them. A failed record is logged and the placement stands: the cards then have no
    owner and the chat role API refuses them (fail closed), the user's card settings screen still sets their AI."""
    if not owner or not node_keys:
        return False
    try:
        if not card_role_store.available():
            return False
        card_role_store.record_placed(flow_id, node_keys, owner["chat_doc_id"], owner.get("token_id"),
                                      owner.get("ai_run_id"), owner.get("user_id"))
        return True
    except Exception:  # noqa: BLE001 - never a credential in the log: ids only
        _log.warning("chat card placement record failed flow=%s nodes=%s", flow_id, node_keys, exc_info=True)
        return False


def _kind(doc_type: str) -> Optional[str]:
    from flowgate.card_settings import fields
    return fields.card_kind("document", doc_type)


def _precheck_settings(cards: list[dict]) -> None:
    """flowgate.v02gui.0032: every chosen run setting fits its card's kind and choices, before anything is saved."""
    if not any(card.get("settings") for card in cards):
        return
    run_settings.require_available()
    errors = []
    for i, card in enumerate(cards):
        if card.get("settings"):
            try:
                run_settings.check(_kind(card["doc_type"]), card["settings"], f"cards[{i}].settings")
            except GraphApiError as exc:
                errors += (exc.params or {}).get("errors") or []
    if errors:
        raise GraphApiError(422, "invalid_request", {"errors": errors}, message="The request does not fit this API.")


def _write_settings(flow_id: str, node_key: str, chosen: dict, user_id: str, owner: Optional[dict],
                    field: str) -> None:
    """In the draft save's guard: a new card's chosen run settings, the version moved once, audited per setting when
    the placing chat is known. A refusal rolls the whole placement back."""
    from flowgate.card_settings import fields, repository, settings

    node = settings._card(flow_id, node_key)
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    changed, before, after = run_settings.write(flow_id, node_key, kind, chosen, user_id, field)
    if changed:
        repository.bump_version(flow_id, node_key)
    if owner and owner.get("chat_doc_id"):
        run_settings.audit(flow_id, node_key, chosen, before, after, changed,
                           repository.settings_version(flow_id, node_key), owner, user_id)


def _settings_summary(flow_id: str, node_key: str, doc_type: str) -> dict:
    """``{field: value now}`` of a placed card's chat run settings; empty when it cannot be read (never fails the
    committed placement)."""
    try:
        return run_settings.values(flow_id, node_key, _kind(doc_type))
    except Exception:  # noqa: BLE001
        return {}


def place(group: dict, user_id: str, body: Any, locale: Optional[str] = None, owner: Optional[dict] = None) -> dict:
    """Place the chat's work cards (and a confirm card when needed) on the Flow's draft. Nothing is confirmed or run.
    ``owner`` (``authz.owner``): the placing chat, recorded for each new work card (flowgate.v02gui.0028)."""
    flow_id = group["group_id"]
    cards, after, want_gate = parse_place(body, locale)
    links = topology.parse(body, len(cards))
    placement_roles.precheck(cards, user_id)
    _precheck_settings(cards)
    # flowgate.v02gui.0019: the Flow's start card first, whatever the draft already holds (the one rule of
    # ``root_start``, also run when the root is registered); no edge is made to or from it here.
    # Explicit topology must not create a root before a rejected request.
    started = (with_flow_guard(flow_id, lambda: root_start.ensure_start_card(group, user_id, _locale(locale)))
               if links is None else None)
    record, version = _definition(flow_id)
    rows = _run_rows(flow_id)
    protected = {k for k, r in rows.items() if r.get("status") in PROTECTED}
    # A definition still without any card: no single live root to start from (none, or ambiguous: 409).
    root = _root(flow_id) if not record.get("nodes") else None
    try:
        if links is not None:
            chosen = topology.choose(record, protected, after, want_gate, body.get("join"), root)
        else:
            chosen = placement.plan(record.get("nodes", []), record.get("edges", []), protected, after, want_gate,
                                    root_type=root["doc_type"] if root else None,
                                    root_keys=[doc_key(root["doc_id"])] if root else ())
    except placement.PlacementError as exc:
        status = 404 if exc.code == "chat_cards_anchor_unknown" else 409
        raise GraphApiError(status, exc.code, {"flow_id": flow_id, **exc.params}) from exc
    root_card = None
    if chosen.new_root is not None:
        root_card = {"label": ROOT_NAMES[chosen.new_root][_locale(locale)],
                     "palette_item_id": ROOT_PALETTE_IDS[chosen.new_root]}
    if links is None:
        draft = placement.draft_body(record, version, chosen, cards, GATE_LABELS[_locale(locale)], root_card)
    else:
        draft = topology.draft_body(record, version, chosen, cards, GATE_LABELS[_locale(locale)], root_card, links)
    request = dr.parse_draft(draft)
    if request.errors or request.draft_version is None:
        raise GraphApiError(422, "chat_cards_draft_invalid", {"flow_id": flow_id,
                                                              "errors": dr.sort_errors(request.errors)})
    def write_roles(saved: dict) -> None:
        # flowgate.v02gui.0032: the chat's chosen roles, in the draft save's guard after the defaults were copied
        keys = saved.get("key_map") or {}
        chosen = [(keys[f"{placement.CLIENT_NODE}{i}"], card["roles"]) for i, card in enumerate(cards)
                  if card.get("roles")]
        if chosen:
            placement_roles.write(flow_id, group.get("project_id"), user_id, chosen, owner)
        for i, card in enumerate(cards):
            if card.get("settings"):
                _write_settings(flow_id, keys[f"{placement.CLIENT_NODE}{i}"], card["settings"], user_id, owner,
                                f"cards[{i}].settings")

    chooses = any(c.get("roles") or c.get("settings") for c in cards)
    saved = graph_service.save_draft(group, user_id, request, None, _locale(locale),
                                     after_save=write_roles if chooses else None)
    key_map = saved.get("key_map") or {}
    placed = []
    for i, card in enumerate(cards):
        key = key_map[f"{placement.CLIENT_NODE}{i}"]
        placed.append({"node_key": key, "label": card["label"], "doc_type": card["doc_type"],
                       "instruction": _instruction_line(flow_id, key),
                       "roles": placement_roles.summary(flow_id, key, card.get("roles")),
                       "settings": _settings_summary(flow_id, key, card["doc_type"])})
    _record_owner(flow_id, [card["node_key"] for card in placed], owner)
    defaults = placement_roles.defaults_used({c["node_key"]: c["roles"] for c in placed})
    gate = None
    if chosen.before is not None and any(n["node_key"] == chosen.before and placement.is_gate(n)
                                         for n in record.get("nodes", [])):
        gate = {"node_key": chosen.before, "created": False}
    elif chosen.new_gate:
        gate = {"node_key": key_map[placement.CLIENT_GATE], "created": True}
    start_card = None
    if chosen.new_root is not None:
        start_card = {"node_key": key_map[placement.CLIENT_ROOT], "doc_type": chosen.new_root,
                      "doc_id": root["doc_id"], "created": True}
    elif started is not None:
        made = _root(flow_id)
        start_card = {"node_key": started, "doc_type": made["doc_type"] if made else None,
                      "doc_id": made["doc_id"] if made else None, "created": True}
    return {"flow_id": flow_id, "draft_version": saved.get("draft_version"),
            "after": key_map.get(chosen.anchor, chosen.anchor), "root": start_card,
            "cards": placed, "gate": gate, "defaults_used": defaults,
            **({"join": body.get("join")} if links is not None else {}),
            "next": NEXT_AFTER_PLACE + (" " + NEXT_DEFAULTS_USED if defaults else "")}


def parse_start(body: Any) -> bool:
    if body is None:
        return False
    if not isinstance(body, dict):
        raise _invalid("body", "not_an_object")
    unknown = set(body) - {"allow_instruction_ai", "expected_context"}
    if "expected_context" in body and not isinstance(body["expected_context"], dict):
        raise _invalid("expected_context", "not_an_object")
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


def _picked(value: Any, keys: tuple) -> Optional[dict]:
    return {k: value.get(k) for k in keys} if isinstance(value, dict) else None


def _node_run_view(n: dict) -> dict:
    dispatch = n.get("dispatch") if isinstance(n.get("dispatch"), dict) else {}
    return {"node_key": n.get("node_key"), "status": n.get("status"), "stage": n.get("stage"),
            "wait_reason": _picked(n.get("wait_reason"), WAIT_KEYS),
            "intervention": _picked(n.get("intervention"), ("kind", "reason")),
            "error_code": dispatch.get("error_code")}


def _run_view(run: Optional[dict]) -> Optional[dict]:
    if not isinstance(run, dict):
        return None
    out = {k: run.get(k) for k in RUN_FIELDS if k in run}
    if isinstance(run.get("node_runs"), list):
        out["node_runs"] = [_node_run_view(n) for n in run["node_runs"] if isinstance(n, dict)]
    return out


def waiting(view: Optional[dict]) -> list[dict]:
    """The cards that wait instead of running, with why (a node run's ``wait_reason``, else its ``intervention``)."""
    out = []
    for n in (view or {}).get("node_runs") or []:
        wait, held = n.get("wait_reason"), n.get("intervention")
        if wait:
            out.append({"node_key": n["node_key"], "reason": wait.get("code"), "role": wait.get("role"),
                        "stage": wait.get("stage"), "detail": wait.get("detail")})
        elif held:
            out.append({"node_key": n["node_key"], "reason": held.get("kind"), "role": None,
                        "stage": n.get("stage"), "detail": held.get("reason")})
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
    expected = (body or {}).get("expected_context")
    if expected is not None:
        from flowgate.graph_run import recovery_basis
        recovery_basis.require_context(group, expected, expected.get("node_key"))
    confirmed = None
    if draft is not None:
        confirmed = confirm_service.confirm_revision(group, user_id, int(draft["draft_version"]), latest,
                                                     None)["revision_no"]
    if expected is not None:
        from flowgate.graph_run.recovery_start import CheckedStart
        result = run_service.start_run(group, user_id, None, "full", None,
                                       recovery=CheckedStart(group, expected, confirmed))
    else:
        result = run_service.start_run(group, user_id, None, "full", None)
    view = _run_view(result.get("run"))
    waits = waiting(view)
    return {"flow_id": flow_id, "confirmed_revision_no": confirmed, "run": view, "waiting": waits,
            "next": (NEXT_WHEN_WAITING + " " + NEXT_AFTER_START) if waits else NEXT_AFTER_START}
