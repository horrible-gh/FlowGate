"""Work plan deploy: status, preview and execution (flowgate.v02.0074 T#4, D0006 §3.1 ~ §3.6).

**Where.** The cards go to the Graph draft of the WP card's own Flow (``graph_service.write_draft``: the draft save's
ownership, run lock, version, protected node and card document rules), never to a v0.1 sequence. The Flow must be
``unassigned`` or ``graph`` (409 ``flow_owned_by_v01`` / ``flow_ownership_conflict``); a deploy confirms nothing, so
the ownership stays as it is.

**Which cards.** A node is a deployed card of a plan when its hidden card setting ``wp_source`` names the plan; only
the nodes of the current draft (else the latest revision) count. It is *valid* when its kind is the card id's type
and no other node holds that card id; a retyped card (``kept_type_mismatch``) or a duplicated one
(``kept_duplicate``) is reported and left alone, and its card id still counts as deployed (no second card).

**Modes.** ``first`` (no deployed card yet) makes every target card; ``add`` makes the card ids that have no card yet
and fills the still-empty instruction lines of valid cards; ``cleanup`` does ``add`` and removes the valid cards of
card ids the plan no longer has, when every document of the card is untouched (``protection.card_verdict``) —
also with no target left (a plan saved back to zero). New cards are chained after the tail of the deploy chain;
the chain is edited only while it is sound (``chain``), else 409 ``deploy_blocked``. Removals and new cards make one
final chain (``chain.rebuild``), and the final graph passes the draft save's own request check (``parse_draft``:
item limits, keys, edge ends) before anything is written, else the blocker ``deploy_draft_invalid``.

**Consistency.** Preview and execution hold the WP save lock (``work_plan_locks``); execution then takes the Flow
guard and, inside it, the WP document's row lock, re-reads the revision (409 ``wp_changed``), the plan bytes (their
digest, ``plan_digest``) and the pre-instruction files (409 ``wp_attachment_unreadable``), and keeps all three locks
to its commit. The answer order of an execution is D0006 §3.6.4: rejections, ``wp_changed``, *no change* (200 with
nothing written — a resend, a double click, the second of two requests), mode, graph baseline
(``draft_version_conflict``), blockers, a cleanup that would remove more than the preview showed
(``deploy_preview_stale``), then the writes.

**Failure.** Every row is in the guard's transaction. Files: a new document's file is removed by its rollback
(``write_new_file``); an existing empty draft is replaced last and restorably (``replace_restorably``): a failure
puts back every original at once, a failed commit by the rollback callbacks under the guard's durable lock, and the
kept originals go after the commit. An original that cannot be put back stays kept and the answer says
``deploy_restore_incomplete``. Events are queued after the commit only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import repository as card_lines_store
from flowgate.card_documents import slots as cs
from flowgate.card_documents import legacy_adapter as card_docs_legacy
from flowgate.card_settings import fields as setting_fields
from flowgate.card_settings import legacy_adapter as settings_legacy
from flowgate.card_settings import repository as card_settings
from flowgate.work_plan_deploy import chain as deploy_chain
from flowgate.work_plan_deploy import legacy_adapter as legacy
from flowgate.work_plan_deploy import plan as deploy_plan
from flowgate.work_plan_deploy import protection
from flowgate.work_plan_deploy.plan import ADD, CLEANUP, FIRST, MODES

SOURCE_FIELD = "wp_source"
_CLIENT_PREFIX = "tmp_wpd_n"


class DeployError(legacy.GraphApiError):
    """A refusal in the WP API's shape (``code`` + message + params)."""


# ── reading the state ────────────────────────────────────────────────────────

@dataclass
class State:
    doc: dict
    flow_id: str
    group: dict
    wp_key: Optional[str]
    record: dict
    draft_version: int
    base_revision_no: Optional[int]
    ownership: str
    refusal: Optional[legacy.GraphApiError] = None
    body: Optional[dict] = None
    plan_digest: Optional[str] = None
    targets: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)       # node_key -> wp_source of this plan (nodes in ``record``)


def _wp_document(doc_id: str) -> dict:
    doc = legacy.document(doc_id)
    if doc is None:
        raise DeployError(404, "wp_not_found", {"doc_id": doc_id})
    if str(doc.get("type_code") or "").upper() != "WP":
        raise DeployError(422, "not_a_work_plan", {"doc_id": doc_id})
    if not doc.get("group_id"):
        raise DeployError(409, "wp_not_on_card", {"doc_id": doc_id})
    return doc


def _graph_record(flow_id: str) -> tuple[dict, int, Optional[int]]:
    """(the definition deploy works on, its ``draft_version`` (0: no draft), the latest revision when no draft)."""
    current = legacy.draft(flow_id)
    if current is not None:
        return current, int(current["draft_version"]), None
    latest_no, latest = legacy.latest_revision(flow_id)
    return (latest or {"nodes": [], "edges": []}), 0, latest_no


def _wp_card(flow_id: str, doc_id: str, record: dict) -> Optional[str]:
    keys = {n["node_key"] for n in record.get("nodes", [])}
    for row in card_lines_store.rows(flow_id):
        if row["slot"] == cs.PLAN and row["doc_id"] == doc_id and row["node_key"] in keys:
            return row["node_key"]
    return None


def _refusal(state: State) -> Optional[legacy.GraphApiError]:
    """The first rejection of D0006 §3.6.4 row 1 / §3.6.6 (permission is the route's), or None."""
    try:
        legacy.require_writable(state.flow_id)
    except legacy.GraphApiError as exc:
        return exc
    locked = legacy.editable(state.doc)
    if locked is not None:
        return DeployError(422, "wp_not_editable", {"doc_id": state.doc["doc_id"], "reason": locked})
    if state.wp_key is None:
        return DeployError(409, "wp_not_on_card", {"doc_id": state.doc["doc_id"], "flow_id": state.flow_id})
    try:
        legacy.require_graph_editable(state.flow_id, legacy.read_ownership(state.flow_id))
        legacy.require_run_editable(state.flow_id)
        legacy.require_no_group_ai_run(state.doc)
    except legacy.GraphApiError as exc:
        return exc
    return None


def _read_state(doc_id: str) -> State:
    doc = _wp_document(doc_id)
    flow_id = doc["group_id"]
    group = legacy.group(flow_id) or {"group_id": flow_id}
    record, draft_version, base = _graph_record(flow_id)
    state = State(doc, flow_id, group, _wp_card(flow_id, doc_id, record), record, draft_version, base,
                  legacy.read_ownership(flow_id).state)
    state.refusal = _refusal(state)
    keys = [n["node_key"] for n in record.get("nodes", [])]
    for key in keys:
        value = card_settings.values(flow_id, key).get(SOURCE_FIELD)
        if deploy_plan.is_source(value, doc_id):
            state.sources[key] = value
    return state


def _read_plan(state: State) -> None:
    """The plan bytes, their digest and the validated body (409 ``wp_unreadable`` when it is no plan)."""
    try:
        raw = legacy.plan_bytes(state.doc)
        body = legacy.load_plan(state.doc)
        again = legacy.plan_bytes(state.doc)
    except legacy.wp.WorkPlanUnreadable as exc:
        raise DeployError(409, "wp_unreadable", {"doc_id": state.doc["doc_id"], "reason": exc.reason}) from exc
    except OSError as exc:
        raise DeployError(409, "wp_unreadable", {"doc_id": state.doc["doc_id"], "reason": "file_missing"}) from exc
    if raw != again:
        raise DeployError(409, "wp_changed", {"doc_id": state.doc["doc_id"], "reason": "plan_digest"})
    state.body, state.plan_digest = body, legacy.digest(raw)
    state.targets, state.skipped = deploy_plan.targets(body, legacy.card_type_of_step)


def _instructions(state: State, locale: str) -> dict[str, dict]:
    """``card_id -> instruction document`` (``work_plan_instruction_text``) of the targets that carry one. A
    pre-instruction file that is not the registered one: 409 ``wp_attachment_unreadable``, nothing written."""
    out: dict[str, dict] = {}
    for target in state.targets:
        text, reference = deploy_plan.pre_instruction(target)
        attachment_text = None
        if reference is not None:
            try:
                attachment_text = legacy.attachment_text(state.doc["doc_id"], reference)
            except legacy.wp_attach.PreInstructionAttachmentError as exc:
                raise DeployError(409, "wp_attachment_unreadable", {
                    "doc_id": state.doc["doc_id"], "card_id": target.card_id, "reason": exc.code}) from exc
        document = legacy.instruction_text.instruction_document(text, reference, attachment_text)
        if document is not None:
            out[target.card_id] = document
    return out


# ── the plan of writes ───────────────────────────────────────────────────────

@dataclass
class Fill:
    node_key: str
    target: Any
    verdict: Optional[protection.Verdict]       # None: the line has no document (one is made with the body)
    slot: Any


@dataclass
class Work:
    cards: list = field(default_factory=list)          # one entry per card id / node (the answer)
    creates: list = field(default_factory=list)        # targets to make, plan order
    fills: list = field(default_factory=list)          # instruction writes on existing cards
    removes: list = field(default_factory=list)        # node keys to remove (cleanup)
    blockers: list = field(default_factory=list)
    chain: Optional[deploy_chain.Chain] = None
    counts: dict = field(default_factory=dict)
    new_keys: list = field(default_factory=list)       # client keys of ``creates``, same order
    draft: Any = None                                  # the checked draft save request of the final graph

    @property
    def writes(self) -> bool:
        return bool(self.creates or self.fills or self.removes)


def _instruction_slot(node: dict) -> Optional[cs.CardSlot]:
    return next((s for s in cs.card_slots(node.get("node_type"), node.get("doc_type"))
                 if s.slot == cs.INSTRUCTION and not s.shares and s.author == cs.INSTRUCTED), None)


def _card_rows(flow_id: str, node_key: str) -> list[dict]:
    """The card's line rows; a line that only shows another line's document (the TS scenario) is that line's."""
    rows = card_lines_store.rows(flow_id, [node_key])
    owned_ids = {r["doc_id"] for r in rows if r["owned"]}
    return [r for r in rows if r["owned"] or r["doc_id"] not in owned_ids]


def _settings_skipped(target, registered_ai: Any = None) -> list[dict]:
    kind = setting_fields.card_kind(deploy_plan.NODE_TYPE, target.doc_type)
    skipped = []
    for name, value in deploy_plan.initial_settings(target):
        reason = _setting_problem(kind, name, value)
        if reason:
            skipped.append({"field": name, "reason": reason})
    return skipped


def _setting_problem(kind: Optional[str], name: str, value: Any) -> Optional[str]:
    setting = setting_fields.field_of(kind, name)
    if setting is None:
        return "not_a_setting_of_this_card"
    try:
        setting_fields.check_value(setting, value, repeat_choices=settings_legacy.repeat_choices(),
                                   team_exists=settings_legacy.team_exists, ai_exists=settings_legacy.ai_exists)
    except setting_fields.InvalidValue as exc:
        return exc.reason
    return None


def _plan_work(state: State, mode: str, instructions: dict[str, dict], registered: set[str], locale: str) -> Work:
    work = Work()
    nodes = {n["node_key"]: n for n in state.record.get("nodes", [])}
    by_card: dict[str, list[str]] = {}
    for key, source in state.sources.items():
        by_card.setdefault(source["card_id"], []).append(key)
    target_ids = {t.card_id: t for t in state.targets}
    docs_by_id = {d["doc_id"]: d for d in legacy.flow_documents(state.flow_id) if d.get("doc_id")}
    started_cache: dict[str, Any] = {}

    def started(key: str) -> Any:
        if key not in started_cache:
            try:
                started_cache[key] = legacy.node_started(state.flow_id, key)
            except Exception as exc:  # noqa: BLE001 — unreadable run facts protect the card
                legacy.log_warning("work plan deploy: run facts unreadable for %s: %s", key, exc)
                started_cache[key] = None
        return started_cache[key]

    def instruction_of(entry: dict, key: str, node: dict, target) -> None:
        if target.card_id not in instructions:
            return
        slot = _instruction_slot(node)
        if slot is None or slot.doc_type not in registered:
            entry.update(instruction="instruction_kept", instruction_reason="no_instruction_line")
            return
        row = next((r for r in card_lines_store.rows(state.flow_id, [key])
                    if (r["slot"], r["doc_type"]) == cs.slot_signature(slot)), None)
        is_started = started(key)
        if is_started is None:
            entry.update(instruction="instruction_kept", instruction_reason=protection.UNKNOWN)
            return
        verdict = None
        if row is not None:
            verdict = protection.judge(state.flow_id, row, docs_by_id, started=bool(is_started))
            if verdict.state == protection.PROTECTED:
                entry.update(instruction="instruction_kept", instruction_reason=verdict.reason)
                return
            if verdict.state == protection.ABSENT:
                verdict = None
        elif is_started:
            entry.update(instruction="instruction_kept", instruction_reason=protection.STARTED)
            return
        entry.update(instruction="instruction_write")
        work.fills.append(Fill(key, target, verdict, slot))

    for target in state.targets:
        keys = by_card.get(target.card_id, [])
        entry = {"card_id": target.card_id, "doc_type": target.doc_type, "node_key": None, "result": None,
                 "reason": None, "instruction": None, "instruction_reason": None, "settings_skipped": []}
        if not keys:
            if target.doc_type not in registered:
                entry.update(result="skipped_type", reason="doc_type_not_registered")
                work.cards.append(entry)
                continue
            entry.update(result="create", settings_skipped=_settings_skipped(target))
            if target.card_id in instructions:
                entry["instruction"] = "instruction_write"
            work.creates.append(target)
            work.cards.append(entry)
            continue
        if len(keys) > 1:
            for key in sorted(keys):
                work.cards.append({**entry, "node_key": key, "result": "kept_duplicate"})
            continue
        key = keys[0]
        node = nodes[key]
        entry["node_key"] = key
        if not deploy_plan.node_is(node, target.doc_type):
            entry.update(result="kept_type_mismatch",
                         reason=str(node.get("doc_type") or node.get("node_type") or ""))
            work.cards.append(entry)
            continue
        entry["result"] = "already_deployed"
        instruction_of(entry, key, node, target)
        work.cards.append(entry)

    orphans: list[str] = []
    for card_id, keys in by_card.items():
        if card_id in target_ids:
            continue
        for key in sorted(keys):
            source, node = state.sources[key], nodes[key]
            entry = {"card_id": card_id, "doc_type": source.get("doc_type"), "node_key": key, "result": None,
                     "reason": None, "instruction": None, "instruction_reason": None, "settings_skipped": []}
            if len(keys) > 1:
                entry["result"] = "kept_duplicate"
            elif not deploy_plan.node_is(node, source.get("doc_type")):
                entry.update(result="kept_type_mismatch",
                             reason=str(node.get("doc_type") or node.get("node_type") or ""))
            elif mode != CLEANUP:
                entry.update(result="kept_orphan", reason="not_cleanup")
            else:
                reason = protection.card_verdict(state.flow_id, key, _card_rows(state.flow_id, key), docs_by_id,
                                                 source)
                if reason:
                    entry.update(result="kept_orphan", reason=reason)
                else:
                    entry["result"] = "remove"
                    orphans.append(key)
            work.cards.append(entry)
    work.cards.extend(dict(s, node_key=None, instruction=None, instruction_reason=None, settings_skipped=[])
                      for s in state.skipped)

    deployed = set(state.sources)
    edges = state.record.get("edges", [])
    work.chain = deploy_chain.walk(state.wp_key, edges, deployed) if state.wp_key else None
    protected = legacy.protected_nodes(state.flow_id)
    if orphans and work.chain is not None:
        removing = set(orphans)
        for key in [k for k in work.chain.cards if k in removing]:
            nxt = deploy_chain.successor(work.chain, key, removing)
            if nxt is not None and nxt in protected:
                removing.discard(key)
                for entry in work.cards:
                    if entry["node_key"] == key:
                        entry.update(result="kept_orphan", reason="position_protected")
        work.removes = [k for k in work.chain.cards if k in removing] + sorted(
            k for k in removing if k not in work.chain.cards)
    else:
        work.removes = sorted(orphans)

    if work.creates:
        if not deployed:
            successors = [e for e in edges if e["source"] == state.wp_key]
            if successors:
                work.blockers.append({"code": "wp_card_has_successor", "node_key": state.wp_key,
                                      "edge_ids": sorted(e["edge_id"] for e in successors)})
        elif work.chain is not None and not work.chain.sound:
            work.blockers.append(_nonlinear(work.chain))
        elif work.chain is not None and work.chain.outside_next in protected:
            work.blockers.append({"code": "deploy_position_blocked", "node_key": work.chain.outside_next})
    if work.removes and work.chain is not None and not work.chain.sound and not any(
            b["code"] == "deploy_chain_nonlinear" for b in work.blockers):
        work.blockers.append(_nonlinear(work.chain))
    if work.writes and not work.blockers:
        _final_draft(state, work, locale)
        if work.draft.errors:
            work.blockers.append({"code": "deploy_draft_invalid", "errors": legacy.dr.sort_errors(work.draft.errors)})
    work.counts = _counts(state, by_card, nodes)
    return work


def _final_draft(state: State, work: Work, locale: str) -> None:
    """The graph the execution saves — removed cards out, new cards in, the chain rebuilt in one step
    (``deploy_chain.rebuild``) — as a draft save request, checked by the draft save's own request check
    (``parse_draft``: item limits, keys, duplicates, edge ends). A request with errors is a blocker: nothing is
    written."""
    removed = set(work.removes)
    nodes = [_wire_node(n) for n in state.record.get("nodes", []) if n["node_key"] not in removed]
    work.new_keys = [f"{_CLIENT_PREFIX}{i}" for i in range(len(work.creates))]
    for client_key, target in zip(work.new_keys, work.creates):
        palette = legacy.PALETTE_KEY_OF_DOC_TYPE.get(target.doc_type)
        names = legacy.PALETTE_NAMES.get(palette or "", {})
        nodes.append({"node_key": None, "client_key": client_key, "node_type": deploy_plan.NODE_TYPE,
                      "doc_type": target.doc_type, "palette_item_id": palette,
                      "label": names.get(locale) or names.get("ko") or target.doc_type, "assignee": None})
    edges = state.record.get("edges", [])
    if work.removes or work.new_keys:
        chain = work.chain if state.sources else deploy_chain.Chain(state.wp_key)
        edges = deploy_chain.rebuild(edges, chain, work.removes, work.new_keys)
    work.draft = legacy.dr.parse_draft({
        "draft_version": state.draft_version, "nodes": nodes,
        "edges": [{"edge_id": e.get("edge_id"), "client_key": e.get("client_key"), "source": e["source"],
                   "target": e["target"], "condition": e.get("condition")} for e in edges]})


def _wire_node(node: dict) -> dict:
    """A stored node as the draft save request carries it."""
    kind = node.get("assignee_kind")
    assignee = ({"kind": "human", "user_id": node.get("assignee_user_id")} if kind == "human" else
                {"kind": "ai", "provider_id": node.get("assignee_provider_id")} if kind == "ai" else None)
    return {"node_key": node["node_key"], "client_key": None, "node_type": node["node_type"],
            "doc_type": node.get("doc_type"), "palette_item_id": node.get("palette_item_id"),
            "label": node.get("label"), "assignee": assignee}


def _nonlinear(chain: deploy_chain.Chain) -> dict:
    return {"code": "deploy_chain_nonlinear", "node_keys": chain.off_chain, "edge_ids": chain.break_edges}


def _counts(state: State, by_card: dict, nodes: dict) -> dict:
    target_ids = {t.card_id: t for t in state.targets}
    mismatch = 0
    for card_id, keys in by_card.items():
        if len(keys) > 1:
            mismatch += len(keys)
            continue
        expected = target_ids[card_id].doc_type if card_id in target_ids else state.sources[keys[0]].get("doc_type")
        if not deploy_plan.node_is(nodes[keys[0]], expected):
            mismatch += 1
    return {
        "deployed_count": len(state.sources),
        "target_count": len(state.targets),
        "missing_count": sum(1 for t in state.targets if t.card_id not in by_card),
        "orphan_count": sum(len(keys) for card_id, keys in by_card.items() if card_id not in target_ids),
        "mismatch_count": mismatch,
    }


def _modes(counts: dict, refusal: Optional[legacy.GraphApiError]) -> dict:
    allowed = refusal is None
    deployed, targets = counts.get("deployed_count", 0), counts.get("target_count", 0)
    return {FIRST: allowed and deployed == 0 and targets >= 1,
            ADD: allowed and deployed >= 1 and targets >= 1,
            CLEANUP: allowed and deployed >= 1}


def _baseline(state: State, work: Optional[Work]) -> dict:
    return {"wp_revision_no": int(state.doc.get("revision_no") or 0), "plan_digest": state.plan_digest,
            "draft_version": state.draft_version, "base_revision_no": state.base_revision_no,
            "remove_node_keys": list(work.removes) if work else []}


def _refusal_block(exc: legacy.GraphApiError) -> dict:
    return {"code": exc.code, **{k: v for k, v in (exc.params or {}).items() if k not in ("code",)}}


# ── status / preview ─────────────────────────────────────────────────────────

def status(doc_id: str) -> dict:
    """``GET .../work-plan/deploy``: whether the plan can deploy and its counts (D0006 §5)."""
    with legacy.plan_save_lock(doc_id):
        return legacy.with_read_snapshot(lambda: _status(doc_id))


def _status(doc_id: str) -> dict:
    state = _read_state(doc_id)
    counts = {"deployed_count": len(state.sources), "target_count": 0, "missing_count": 0, "orphan_count": 0,
              "mismatch_count": 0}
    cards: list[dict] = []
    refusal = state.refusal
    if state.wp_key is not None:
        try:
            _read_plan(state)
        except DeployError as exc:
            refusal = refusal or exc
        if state.body is not None:
            nodes = {n["node_key"]: n for n in state.record.get("nodes", [])}
            by_card: dict[str, list[str]] = {}
            for key, source in state.sources.items():
                by_card.setdefault(source["card_id"], []).append(key)
            counts = _counts(state, by_card, nodes)
            target_types = {t.card_id: t.doc_type for t in state.targets}
            for key, source in sorted(state.sources.items()):
                expected = target_types.get(source["card_id"], source.get("doc_type"))
                cards.append({"card_id": source["card_id"], "node_key": key,
                              "doc_type": nodes[key].get("doc_type"),
                              "valid": len(by_card[source["card_id"]]) == 1 and deploy_plan.node_is(nodes[key], expected),
                              "in_plan": source["card_id"] in target_types})
    return {
        "doc_id": doc_id, "flow_id": state.flow_id, "wp_node_key": state.wp_key, "ownership": state.ownership,
        "wp_revision_no": int(state.doc.get("revision_no") or 0),
        "deployable": refusal is None, "reason": refusal.code if refusal is not None else None,
        **counts, "cards": cards, "modes": _modes(counts, refusal),
    }


def _mode(mode: Any) -> str:
    if mode not in MODES:
        raise DeployError(422, "invalid_request", {"errors": [{"field": "mode", "reason": "not_one_of:" + ",".join(MODES)}]})
    return mode


def preview(doc_id: str, mode: Any, wp_revision_no: Any, locale: str) -> dict:
    """``POST .../work-plan/deploy/preview``: what an execution would do now, and the baseline it must carry."""
    mode = _mode(mode)
    with legacy.plan_save_lock(doc_id):
        return legacy.with_read_snapshot(lambda: _preview(doc_id, mode, wp_revision_no, locale))


def _preview(doc_id: str, mode: str, wp_revision_no: Any, locale: str) -> dict:
    state = _read_state(doc_id)
    current = int(state.doc.get("revision_no") or 0)
    if wp_revision_no is not None and wp_revision_no != current:
        raise DeployError(409, "wp_changed", {"doc_id": doc_id, "wp_revision_no": wp_revision_no,
                                              "current_revision_no": current})
    blockers = [_refusal_block(state.refusal)] if state.refusal is not None else []
    work = None
    if state.wp_key is not None:
        _read_plan(state)
        instructions = _instructions(state, locale)
        work = _plan_work(state, mode, instructions, legacy.registered_doc_types(state.group.get("project_id")),
                          locale)
        blockers += work.blockers
        mismatch = _mode_mismatch(mode, len(state.sources))
        if mismatch and work.writes:
            blockers.append({"code": mismatch})
    return {
        "doc_id": doc_id, "mode": mode, "flow_id": state.flow_id, "wp_node_key": state.wp_key,
        "cards": work.cards if work else [], "blockers": blockers,
        "writes": bool(work and work.writes), "no_change": not (work and work.writes),
        "executable": not blockers and bool(work and work.writes),
        "counts": work.counts if work else {}, "baseline": _baseline(state, work),
    }


def _mode_mismatch(mode: str, deployed: int) -> Optional[str]:
    if mode == FIRST and deployed > 0:
        return "deploy_already_deployed"
    if mode in (ADD, CLEANUP) and deployed == 0:
        return "deploy_not_deployed"
    return None


# ── execution ────────────────────────────────────────────────────────────────

@dataclass
class _Run:
    replaced: list = field(default_factory=list)       # (doc_id, ReplacedFile)
    restore_failed: list = field(default_factory=list)  # doc ids whose original stays kept


def execute(doc_id: str, request: dict, user_id: str, locale: str) -> dict:
    """``POST .../work-plan/deploy``: the deploy itself, with the preview's baseline (D0006 §3.6.4)."""
    mode = _mode(request.get("mode"))
    run = _Run()
    with legacy.plan_save_lock(doc_id):
        doc = _wp_document(doc_id)
        try:
            return legacy.with_flow_guard(doc["group_id"], lambda: _execute(doc_id, mode, request, user_id, locale, run))
        except Exception as exc:
            if run.restore_failed:
                raise DeployError(500, "deploy_restore_incomplete", {
                    "doc_id": doc_id, "doc_ids": sorted(set(run.restore_failed))}) from exc
            raise


def _execute(doc_id: str, mode: str, request: dict, user_id: str, locale: str, run: _Run) -> dict:
    legacy.lock_document_row(doc_id)                     # a WP save of another process waits for this commit
    state = _read_state(doc_id)
    if state.refusal is not None:
        raise state.refusal
    current = int(state.doc.get("revision_no") or 0)
    if request.get("wp_revision_no") != current:
        raise DeployError(409, "wp_changed", {"doc_id": doc_id, "wp_revision_no": request.get("wp_revision_no"),
                                              "current_revision_no": current})
    _read_plan(state)
    if request.get("plan_digest") != state.plan_digest:
        raise DeployError(409, "wp_changed", {"doc_id": doc_id, "reason": "plan_digest"})
    instructions = _instructions(state, locale)
    registered = legacy.registered_doc_types(state.group.get("project_id"))
    work = _plan_work(state, mode, instructions, registered, locale)
    answer = {"doc_id": doc_id, "mode": mode, "flow_id": state.flow_id, "wp_node_key": state.wp_key,
              "cards": work.cards, "counts": work.counts}
    if not work.writes:
        return {**answer, "no_change": True, "draft_version": state.draft_version,
                "created_node_keys": [], "removed_node_keys": []}
    mismatch = _mode_mismatch(mode, len(state.sources))
    if mismatch:
        raise DeployError(409, mismatch, {"doc_id": doc_id, "deployed_count": len(state.sources)})
    if request.get("draft_version") != state.draft_version or (
            state.draft_version == 0 and request.get("base_revision_no") != state.base_revision_no):
        raise DeployError(409, "draft_version_conflict", {
            "flow_id": state.flow_id, "expected_draft_version": request.get("draft_version"),
            "current_draft_version": state.draft_version, "expected_base_revision_no": request.get("base_revision_no"),
            "current_base_revision_no": state.base_revision_no})
    if work.blockers:
        raise DeployError(409, "deploy_blocked", {"doc_id": doc_id, "blockers": work.blockers})
    if mode == CLEANUP:
        shown = set(request.get("remove_node_keys") or [])
        unseen = [k for k in work.removes if k not in shown]
        if unseen:
            raise DeployError(409, "deploy_preview_stale", {"doc_id": doc_id, "node_keys": unseen})
    result = _write(state, work, instructions, registered, user_id, locale, run)
    for entry in work.cards:
        if entry["result"] == "create":
            entry["node_key"] = result["key_map"].get(entry["card_id"])
    legacy.before_commit()
    return {**answer, "no_change": False, **result}


def _write(state: State, work: Work, instructions: dict, registered: set[str], user_id: str, locale: str,
           run: _Run) -> dict:
    wp_doc_id, revision = state.doc["doc_id"], int(state.doc.get("revision_no") or 0)
    # A removed card's documents carry only the deploy's own body save evidence (the protection checked it). The
    # evidence names its document, so it goes first for the documents the draft save deletes with the card; a
    # document a confirmed revision (or another line) keeps stays with its whole history.
    for doc_id in card_lines.disposed_documents(state.flow_id, work.removes):
        legacy.drop_deploy_save_evidence(doc_id)
    saved = legacy.write_draft(state.group, user_id, work.draft, registered)
    key_map = saved.get("key_map") or {}

    written: list[tuple] = []                      # (node_key, target, doc, path, identity, text)

    def instruction_line(key: str, target) -> None:
        """The card's instruction line after the draft save: the empty draft the save made for it takes the body
        (its doc id and front matter kept); only a line the save left without a document gets a new one."""
        node = {"node_key": key, "node_type": deploy_plan.NODE_TYPE, "doc_type": target.doc_type}
        slot = _instruction_slot(node)
        if slot is None or slot.doc_type not in registered:
            return
        verdict = _saved_line(state, key, slot)
        if verdict is None:
            _create_line(state, key, node, slot, target, instructions, user_id, locale, revision)
        elif verdict.state == protection.UNTOUCHED:
            written.append((key, target, verdict.doc, verdict.path, verdict.identity, verdict.text))
        else:
            _kept(work, target.card_id, verdict.reason or "no_instruction_line")

    for client_key, target in zip(work.new_keys, work.creates):
        key = key_map[client_key]
        card_settings.put(state.flow_id, key, SOURCE_FIELD, deploy_plan.source_value(wp_doc_id, target, revision),
                          user_id)
        kind = setting_fields.card_kind(deploy_plan.NODE_TYPE, target.doc_type)
        for name, value in deploy_plan.initial_settings(target):
            if _setting_problem(kind, name, value) is None:
                setting = setting_fields.field_of(kind, name)
                checked = setting_fields.check_value(setting, value, repeat_choices=settings_legacy.repeat_choices(),
                                                     team_exists=settings_legacy.team_exists,
                                                     ai_exists=settings_legacy.ai_exists)
                card_settings.put(state.flow_id, key, name, checked, user_id)
        if target.card_id in instructions:
            instruction_line(key, target)
    for fill in work.fills:
        if fill.verdict is None:                   # the line had no document when judged
            instruction_line(fill.node_key, fill.target)
        else:
            v = fill.verdict
            written.append((fill.node_key, fill.target, v.doc, v.path, v.identity, v.text))

    replacements = []
    for key, target, doc, path, identity, text in written:
        data = _instruction_file(text, doc, instructions[target.card_id], locale).encode("utf-8")
        legacy.stage_body_save(doc, legacy.storage_relative(path, doc.get("project_id")), len(data), user_id)
        _mark_instruction(state, key, target, revision, doc["doc_id"], data, user_id)
        replacements.append((doc, path, identity, data))
    _replace_files(replacements, run)
    return {"draft_version": saved.get("draft_version"),
            "created_node_keys": [key_map[k] for k in work.new_keys], "removed_node_keys": list(work.removes),
            "key_map": {c.card_id: key_map[k] for k, c in zip(work.new_keys, work.creates)}}


def _saved_line(state: State, key: str, slot) -> Optional[protection.Verdict]:
    """The verdict on the card's ``slot`` line as the draft save left it, or None when the line has no row."""
    row = next((r for r in card_lines_store.rows(state.flow_id, [key])
                if (r["slot"], r["doc_type"]) == cs.slot_signature(slot)), None)
    if row is None:
        return None
    docs_by_id = {d["doc_id"]: d for d in legacy.flow_documents(state.flow_id) if d.get("doc_id")}
    return protection.judge(state.flow_id, row, docs_by_id, started=False)


def _kept(work: Work, card_id: str, reason: str) -> None:
    for entry in work.cards:
        if entry["card_id"] == card_id and entry["result"] in ("create", "already_deployed"):
            entry.update(instruction="instruction_kept", instruction_reason=reason)


def _mark_instruction(state: State, key: str, target, revision: int, doc_id: str, data: bytes,
                      user_id: str) -> None:
    """``wp_source`` names the instruction document the deploy wrote and the digest of its file."""
    source = dict(card_settings.values(state.flow_id, key).get(SOURCE_FIELD) or
                  deploy_plan.source_value(state.doc["doc_id"], target, revision))
    source.update(instruction_doc_id=doc_id, instruction_digest=legacy.digest(data))
    card_settings.put(state.flow_id, key, SOURCE_FIELD, source, user_id)


def _instruction_file(text: str, doc: dict, document: dict, locale: str) -> str:
    """The line's file with the instruction as its body: its own front matter kept, the body after it."""
    match = legacy.instruction_text.LEADING_FRONTMATTER_RE.match(text or "")
    body = legacy.instruction_text.instruction_body(doc.get("title") or "", document, locale)
    if not match:
        return body
    return match.group(0).rstrip("\n") + "\n\n" + body


def _create_line(state: State, key: str, node: dict, slot, target, instructions: dict, user_id: str, locale: str,
                 revision: int) -> None:
    """The instruction line has no document even after the draft save: one is made with the body (the card line
    write's own creation) and linked, with the deploy's body save evidence in the same transaction — so the cleanup
    may take it back like any line only the deploy wrote. Its file goes with a rollback (``write_new_file``)."""
    title = card_docs_legacy._title(node.get("label") or "", slot)
    body = legacy.instruction_text.instruction_body(title, instructions[target.card_id], locale)
    doc_id = card_docs_legacy.create_document(state.group, node, slot, user_id, body)
    ordinal = [s.slot for s in cs.card_slots(node["node_type"], node["doc_type"])].index(slot.slot)
    card_lines_store.insert(state.flow_id, key, slot.slot, slot.doc_type, doc_id, True, ordinal, user_id)
    doc = legacy.document(doc_id) or {}
    path = legacy.file_of(doc)
    data = path.read_bytes()
    legacy.stage_body_save(doc, legacy.storage_relative(path, doc.get("project_id")), len(data), user_id)
    _mark_instruction(state, key, target, revision, doc_id, data, user_id)


def _replace_files(replacements: list, run: _Run) -> None:
    """The last step: each judged empty draft's file takes its instruction, restorably (D0006 §3.6.5 step 3)."""
    for doc, path, identity, data in replacements:
        try:
            replaced = legacy.replace_file(path, data, identity)
        except legacy.FileChanged as exc:
            _restore_now(run)
            raise DeployError(409, "instruction_target_changed", {"doc_id": doc["doc_id"]}) from exc
        except BaseException:
            _restore_now(run)
            raise
        run.replaced.append((doc["doc_id"], replaced))
        _queue_restore(doc["doc_id"], replaced, run)
        legacy.run_after(replaced.release, on_commit=True)


def _restore_now(run: _Run) -> None:
    for doc_id, replaced in reversed(run.replaced):
        try:
            ok = replaced.restore()
        except OSError:
            ok = False
        if not ok:
            run.restore_failed.append(doc_id)


def _queue_restore(doc_id: str, replaced, run: _Run) -> None:
    runner = legacy.cleanup_runner(f"work plan deploy restore {replaced.path}")

    def restore() -> None:
        try:
            ok = replaced.restore()
        except OSError:
            ok = False
        if not ok and doc_id not in run.restore_failed:
            run.restore_failed.append(doc_id)
            legacy.log_warning("work plan deploy: original of %s not restored (kept at %s)", doc_id, replaced.keep)

    legacy.run_after(restore if runner is None else (lambda: runner(restore)), on_commit=False)
