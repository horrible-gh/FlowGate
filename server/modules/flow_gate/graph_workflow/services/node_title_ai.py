"""Node title — the AI's first naming of a card (0062 D0005 set 3, L0006 2.3~2.6 / 4.1 / 4.2, 0064 L0005 2.17,
P0006 §4.2.5 / §4.2.6, DB0007 Q5 / Q6, question #441 o1).

- ``claim_on_ai_start``: inside the mapping guard of an AI start (``ai_node_starter._map_if_open``), after its
  refusals and before its token is mapped, in its own savepoint. The first AI start of a card the naming is for
  (a palette / R / B document card, not Custom, showing a default name in every view) claims the card's title
  opportunity and the start's mention asks the AI for a title; every other start closes or leaves the opportunity
  and its mention is unchanged. An earlier AI start found in the token map (``map_kind = 'ai_start'``, or a legacy
  mapping judged by L1~L6) ends it (``prior_evidence``; L6, which cannot be told apart, ``prior_evidence_legacy``).
  A failing judgement rolls back only the savepoint: the token is mapped and the AI starts without the request.
  A refused mapping rolls back the guard and the claim with it.
- ``after_registration_title``: the inbox hook (``node_title_hooks.after_registration``), after the document is
  registered and the token consumed, outside any transaction. The claiming token's candidate is applied once, after
  the checks of L0006 2.6 in the Flow guard; whatever the candidate or a failure, the inbox answer, the document,
  the token, its binding, approval and review are what they were. Only an applied title is told (SSE ``title``).
  No AI is ever called for a title.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.projection.graph_projector import card_document, card_title_source
from modules.flow_gate.graph_workflow.projection.run_documents import registered_documents
from modules.flow_gate.graph_workflow.store import definition_store, run_store, title_store

from . import node_title_names as names
from .canvas_events import CANVAS_REASON_TITLE, publish_canvas_changed
from .canvas_service import title_views
from .flow_guard import with_flow_guard
from .node_dispatch import _savepoint
from .node_title import flow_disposed_strict
from .root_conversion_cards import ROOT_NAMES

_log = logging.getLogger(__name__)

CLAIM_SAVEPOINT = "node_title_claim"
CANDIDATE_SCOPES = ("new", "edit")
# L0006 2.3: the rules that find an earlier AI start. L6 (no mark, an AI node's later mapping, no token row, no AI
# run record) cannot be told from a re-dispatch; question #441 o1 counts it, closed as its own reason.
EVIDENCE_RULES = ("E1", "L2", "L3", "L6")
REASON_OF_RULE = {"E1": "prior_evidence", "L2": "prior_evidence", "L3": "prior_evidence",
                  "L6": "prior_evidence_legacy"}


# ── persistent evidence of an earlier AI start (L0006 2.3, DB0007 Q5 / Q6) ──

def _mappings(flow_id: str, node_key: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT m.run_id, r.revision_no, m.node_key, m.token_id, m.mapped_at, m.map_kind FROM graph_run_token_map m "
        "JOIN graph_runs r ON r.run_id = m.run_id WHERE r.group_id = ? AND m.node_key = ?", [flow_id, node_key]) or []


def _revision_row(flow_id: str, revision_no, node_key: str, cache: dict) -> Optional[dict]:
    if revision_no not in cache:
        cache[revision_no] = definition_store.revision(flow_id, int(revision_no)) if revision_no is not None else None
    return next((r for r in (cache[revision_no] or {}).get("nodes") or [] if r["node_key"] == node_key), None)


def _classify(flow_id: str, m: dict, index: int, cache: dict) -> Optional[str]:
    """The rule a mapping is an AI start by, or None (a dispatch / attempt mapping, no evidence)."""
    if m.get("map_kind") == run_store.MAP_AI_START:
        return "E1"
    if m.get("map_kind") is not None:
        return None                                       # E0 dispatch; an attempt token is no evidence (DB0007 Q5)
    row = _revision_row(flow_id, m.get("revision_no"), m["node_key"], cache)
    if row is not None and row.get("assignee_kind") == "human":
        return None                                       # L1: that revision never starts an AI on the node
    store = get_store()
    token = store._fetch_one("SELECT ai_run_id FROM tokens WHERE token_id = ?", [m["token_id"]])
    if token is not None and token.get("ai_run_id"):
        return "L2"
    if store.table_exists("ai_invoke_runs") and store._fetch_one(
            "SELECT run_id FROM ai_invoke_runs WHERE token_id = ? AND group_id = ? LIMIT 1", [m["token_id"], flow_id]):
        return "L3"
    if token is not None or index == 0:
        return None                                       # L4: a dispatch token; L5: a node run's first mapping
    return "L6"


def prior_ai_start(flow_id: str, node_key: str, exclude_token_id: Optional[str]) -> Optional[str]:
    """``has_prior_ai_start`` over every run of the Flow: the rule of the evidence found (E1 / L2 / L3 before L6),
    or None. Reads only."""
    runs: dict[str, list[dict]] = {}
    for m in _mappings(flow_id, node_key):
        runs.setdefault(m["run_id"], []).append(m)
    cache: dict = {}
    found: Optional[str] = None
    for group in runs.values():
        for index, m in enumerate(sorted(group, key=lambda r: (str(r.get("mapped_at")), str(r["token_id"])))):
            if m["token_id"] == exclude_token_id:
                continue
            rule = _classify(flow_id, m, index, cache)
            if rule in ("E1", "L2", "L3"):
                _log.info("node title evidence rule=%s flow=%s run=%s token=%s", rule, flow_id, m["run_id"],
                          m["token_id"])
                return rule
            found = found or rule
    if found:
        _log.info("node title evidence rule=L6 flow=%s node=%s", flow_id, node_key)
    return found


# ── the card's shown title (L0006 2.5) ────────────────────────────────────

def display_is_default(flow_id: str, node_key: str, fallback_row: Optional[dict], exempt_doc_ids=frozenset()) -> bool:
    """True when every view drawing the card shows a default name from its label (a document's title, whatever it
    reads, is never one — but a document of ``exempt_doc_ids`` counts as the label). No view: the fallback row."""
    views, by_id = title_views(flow_id)
    seen = False
    for view in views:
        row = next((r for r in view.nodes if r["node_key"] == node_key), None)
        if row is None:
            continue
        seen = True
        kind, text, doc_id = card_title_source(row, card_document(view, row, by_id))
        if kind == "document" and doc_id in exempt_doc_ids:
            kind, text = "label", row.get("label")
        if kind == "document" or not names.is_default_name(text, row):
            return False
    if not seen:
        return fallback_row is not None and names.is_default_name(fallback_row.get("label"), fallback_row)
    return True


def naming_target(row: dict) -> bool:
    """L0006 2.4 step 6 with 0064 L0005 2.17-1: a document card of a default palette item (by id, else by its type)
    or an R / B card; never Custom, a gate or a type with no palette name."""
    item = row.get("palette_item_id")
    if row.get("node_type") != "document" or str(item or "").startswith("custom."):
        return False
    doc_type = cm.normalize_type(row.get("doc_type"))
    return item in names.PALETTE_NAMES or doc_type in names.PALETTE_KEY_OF_DOC_TYPE or doc_type in ROOT_NAMES


# ── the claim at an AI start (L0006 2.4 / 4.1) ──────────────────────────────

def _decide(job: dict, token_id: str, st: Optional[dict]) -> tuple[bool, Optional[str]]:
    """(requested, close reason). Neither: nothing is written."""
    flow_id, node_key, row = job["flow_id"], job["node_key"], job.get("node") or {}
    if st and st["opportunity_state"] == title_store.CLOSED:
        return False, None
    if st and st["opportunity_state"] == title_store.CLAIMED:
        return False, (None if st["claimed_token_id"] == token_id else "superseded_start")
    rule = prior_ai_start(flow_id, node_key, token_id)
    if rule:
        return False, REASON_OF_RULE[rule]
    if st and st["human_edited"]:
        return False, "human_edited"
    if st and st["title"]:
        return False, "not_target_title"
    if not naming_target(row):
        return False, "not_target_node"
    if not display_is_default(flow_id, node_key, row):
        return False, "not_target_display"
    return True, None


def claim_on_ai_start(job: dict, token_id: str, ai_run_id: Optional[str]) -> bool:
    """True when this start claimed the card's title opportunity (its mention asks for a title). Never raises."""
    flow_id, node_key = job["flow_id"], job["node_key"]
    try:
        if not title_store.available():
            return False
        with _savepoint(CLAIM_SAVEPOINT):
            st = title_store.state(flow_id, node_key)
            requested, reason = _decide(job, token_id, st)
            version = st["version"] if st else None
            if requested:
                changed = title_store.claim(flow_id, node_key, token_id, ai_run_id, version)
            elif reason:
                changed = title_store.close(flow_id, node_key, reason, version)
            else:
                return False
            if changed != 1:
                raise RuntimeError("the title state changed under the guard")
        _log.info("node_title_%s flow=%s run=%s node=%s stage=%s token=%s ai_run=%s reason=%s",
                  "claimed" if requested else "claim_closed", flow_id, job.get("run_id"), node_key, job.get("stage"),
                  token_id, ai_run_id, reason)
        return requested
    except Exception:  # noqa: BLE001 — the title never stops an AI start (L0006 5)
        _log.warning("node_title_claim_failed flow=%s run=%s node=%s stage=%s token=%s", flow_id, job.get("run_id"),
                     node_key, job.get("stage"), token_id, exc_info=True)
        return False


# ── the candidate a registration brings (L0006 2.6 / 4.2) ───────────────────

def candidate_of(raw, action_scope: Optional[str]) -> tuple[str, Optional[str]]:
    """``("none", None)`` (no candidate: absent, null, or not a new / edit registration), ``("invalid", reason)`` or
    ``("valid", normalized title)``."""
    if action_scope not in CANDIDATE_SCOPES or raw is None:
        return "none", None
    check = names.validate_title(raw, names.CANDIDATE_RAW_MAX_CHARS)
    return ("valid", check.value) if check.reason is None else ("invalid", check.reason)


def _claim_tokens(flow_id: str, st: dict) -> list[str]:
    tokens = [st["claimed_token_id"]]
    if st.get("claimed_ai_run_id"):
        rows = get_store()._fetch_all(
            "SELECT m.token_id FROM graph_run_token_map m JOIN graph_runs r ON r.run_id = m.run_id "
            "JOIN tokens t ON t.token_id = m.token_id WHERE r.group_id = ? AND m.node_key = ? AND t.ai_run_id = ?",
            [flow_id, st["node_key"], st["claimed_ai_run_id"]]) or []
        tokens += [r["token_id"] for r in rows]
    return tokens


def _definition_keys(flow_id: str) -> set[str]:
    draft = definition_store.draft(flow_id)
    latest_no = definition_store.latest_revision_no(flow_id)
    latest = definition_store.revision(flow_id, latest_no) if latest_no else None
    return {r["node_key"] for record in (draft, latest) for r in (record or {}).get("nodes") or []}


def _close(flow_id: str, node_key: str, reason: str, st: dict, token_id: str) -> None:
    if title_store.close(flow_id, node_key, reason, st["version"]) != 1:
        _log.info("node_title_apply_lost_race flow=%s node=%s token=%s", flow_id, node_key, token_id)
        return
    _log.info("node_title_apply_closed flow=%s node=%s token=%s reason=%s", flow_id, node_key, token_id, reason)


def _apply_body(flow_id: str, node_key: str, doc_id: Optional[str], token_id: str, cand: tuple) -> None:
    st = title_store.state(flow_id, node_key)
    if st is None or st["opportunity_state"] != title_store.CLAIMED:
        _log.info("node_title_apply_skipped flow=%s node=%s token=%s reason=not_claimed", flow_id, node_key, token_id)
        return
    kind, value = cand
    if st["claimed_token_id"] != token_id:
        return _close(flow_id, node_key, "other_registration", st, token_id)
    if kind != "valid":
        return _close(flow_id, node_key, "no_candidate" if kind == "none" else "invalid_candidate", st, token_id)
    if st["human_edited"] or st["title_source"] == title_store.SOURCE_HUMAN:
        return _close(flow_id, node_key, "human_edited", st, token_id)
    if flow_disposed_strict(flow_id):
        return _close(flow_id, node_key, "flow_disposed", st, token_id)
    if node_key not in _definition_keys(flow_id):
        return _close(flow_id, node_key, "node_gone", st, token_id)
    exempt = {d["doc_id"] for d in registered_documents(flow_id, _claim_tokens(flow_id, st))} | {doc_id}
    if not display_is_default(flow_id, node_key, None, exempt):
        return _close(flow_id, node_key, "source_changed", st, token_id)
    if title_store.apply_ai(flow_id, node_key, value, token_id, st["version"]) != 1:
        _log.info("node_title_apply_lost_race flow=%s node=%s token=%s", flow_id, node_key, token_id)
        return
    group = db_groups.get_by_id(flow_id) or {}
    publish_canvas_changed(project_id=group.get("project_id"), flow_id=flow_id, reason=CANVAS_REASON_TITLE,
                           actor_user_id=None, origin_client_id=None, node_keys=[node_key])
    _log.info("node_title_applied flow=%s node=%s token=%s doc=%s length=%s", flow_id, node_key, token_id, doc_id,
              len(value or ""))


def after_registration_title(flow_id: Optional[str], doc_id: Optional[str], token_id: Optional[str], raw_candidate,
                             action_scope: Optional[str]) -> None:
    """L0006 2.6 ``after_registration_title``: the inbox registration committed and its token is consumed. Never
    raises; never retried (``APPLY_RETRY_MAX = 0``): a failure leaves the opportunity claimed, i.e. used."""
    try:
        if not flow_id or not token_id:
            return
        if in_transaction():
            _log.warning("node_title_apply_skipped flow=%s token=%s reason=in_transaction", flow_id, token_id)
            return
        mapping = run_store.node_run_by_token(token_id)
        if mapping is None:
            if raw_candidate is not None:
                _log.info("node_title_apply_skipped flow=%s token=%s reason=not_graph_node", flow_id, token_id)
            return
        if not title_store.available():
            return
        cand = candidate_of(raw_candidate, action_scope)
        with_flow_guard(flow_id, lambda: _apply_body(flow_id, mapping["node_key"], doc_id, token_id, cand))
    except Exception:  # noqa: BLE001 — the answer, the document and the token stay as they are
        _log.warning("node_title_apply_failed flow=%s token=%s doc=%s", flow_id, token_id, doc_id, exc_info=True)
