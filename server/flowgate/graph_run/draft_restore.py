"""(CH): a card the run opened is never missing from the Flow's draft.

A draft saved while a card was still pending may leave that card out, or change it or its input edges. Once the run
opens it, finishes it or has it in review (``editing.PROTECTED``, and a confirm gate still unresolved), the protection
rule (``editing.protect_definition``) refuses every later save and confirmation built on that draft, so the Flow could
never be edited again and every card a person placed vanished (B bug 1, the earlier change).

Instead the server puts those cards back into the draft the moment their run state changes: each protected card as
the execution revision has it, its input edges and, when one is missing, the card such an edge comes from — all under
their own keys (a ``draft_removed`` retirement of such a key is released). Everything else of the draft stays as it
was edited. The protection rule itself is unchanged.

- ``restored``: the pure merge over a definition's nodes / edges;
- ``restore_draft``: the stored draft repaired in place, inside the caller's Flow guard (draft version + 1 and a
  ``graph_canvas_changed`` ``draft`` event, only when something changed). An editor still holding the older version
  gets ``draft_version_conflict`` on its next save, reads the Flow again and keeps the cards it placed meanwhile
  (``draftController``);
- ``on_node_state``: the ``graph_execution_hooks`` action ``node_state_changed`` (``run_apply.emit_node_state``);
- ``restore_all``: startup, for a Flow left blocked before this rule existed.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import definition_store, layout_store, run_store

from flowgate.graph_run.editing import PROTECTED, _unresolved_gates

log = logging.getLogger(__name__)

# The node run states after which a card may be protected (a rejected confirm gate stays protected, the earlier implementation).
TRIGGER_STATES = frozenset(PROTECTED | {"rejected"})


def _protected_keys(flow_id: str, run: dict, before: dict) -> set[str]:
    """The cards ``protect_definition`` keeps as the execution revision ``before`` has them."""
    old = {n["node_key"]: n for n in before["nodes"]}
    rows = run_store.node_runs(run["run_id"])
    keys = {r["node_key"] for r in rows if r["status"] in PROTECTED}
    keys |= _unresolved_gates(flow_id, old, rows)
    return {k for k in keys if k in old}


def restored(flow_id: str, nodes: list[dict], edges: list[dict],
             run: Optional[dict] = None) -> tuple[list[dict], list[dict], list[str]]:
    """``(nodes, edges, added)``: the definition with the run's protected cards as the execution revision has them.

    A protected card already there keeps its place (its definition replaced when it differs); a missing one is
    appended. Its input edges are exactly the revision's: other edges into it are dropped, a missing one is appended
    with its source card (appended from the revision when the definition lost it too). ``added``: the node keys
    appended. Nothing changes without a current run."""
    nodes, edges = list(nodes), list(edges)
    run = run if run is not None else run_store.current_run(flow_id)
    if not run:
        return nodes, edges, []
    before = definition_store.revision(flow_id, int(run["revision_no"]))
    if before is None:
        return nodes, edges, []
    keys = _protected_keys(flow_id, run, before)
    if not keys:
        return nodes, edges, []
    old = {n["node_key"]: n for n in before["nodes"]}
    out_nodes = [dict(old[n["node_key"]]) if n["node_key"] in keys else n for n in nodes]
    present = {n["node_key"] for n in out_nodes}
    added: list[str] = []

    def add(key: str) -> None:
        if key not in present and key in old:
            out_nodes.append(dict(old[key]))
            present.add(key)
            added.append(key)

    for node in before["nodes"]:                    # revision order
        if node["node_key"] in keys:
            add(node["node_key"])
    wanted = {e["edge_id"]: e for e in before["edges"] if e["target"] in keys}
    kept: set[str] = set()
    out_edges = []
    for e in edges:
        if e["edge_id"] in wanted:
            if e == wanted[e["edge_id"]]:
                out_edges.append(e)
                kept.add(e["edge_id"])
            continue
        if e["target"] in keys:
            continue
        out_edges.append(e)
    for e in before["edges"]:                       # revision order
        if e["edge_id"] in wanted and e["edge_id"] not in kept:
            add(e["source"])
            out_edges.append(dict(e))
    return out_nodes, out_edges, added


def restore_draft(flow_id: str, run: Optional[dict] = None, project_id: Optional[str] = None) -> bool:
    """Inside the Flow guard of ``flow_id``: the stored draft with the run's protected cards put back
    (``restored``). Whether the draft changed."""
    draft = definition_store.draft(flow_id)
    if draft is None:
        return False
    nodes, edges, added = restored(flow_id, draft["nodes"], draft["edges"], run)
    if nodes == draft["nodes"] and edges == draft["edges"]:
        return False
    version = int(draft["draft_version"])
    if definition_store.bump_draft(flow_id, version, None) != 1:
        return False
    definition_store.replace_draft_content(flow_id, nodes, edges)
    if added:
        layout_store.release_retired(flow_id, added)
    from modules.flow_gate.graph_workflow.services.canvas_events import publish_canvas_changed
    publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason="draft", actor_user_id=None,
                           origin_client_id=None, draft_version=version + 1)
    log.info("[draft_restore] %s: protected cards put back into the draft (added %s)", flow_id, added)
    return True


def on_node_state(ctx, node_key: str, to_status: str) -> None:
    """``graph_execution_hooks`` ``node_state_changed``: inside the step's Flow guard, after the node's row and event
    were written. Never fails the step: a repair that cannot be made is logged and left to the next change."""
    if to_status not in TRIGGER_STATES:
        return
    try:
        restore_draft(ctx.flow_id, ctx.run, ctx.project_id)
    except Exception:                                # the run step must not roll back for the draft
        log.exception("[draft_restore] %s: draft repair after %s -> %s failed", ctx.flow_id, node_key, to_status)


def restore_all() -> list[str]:
    """Startup: every Flow with a draft and a current run, its draft repaired in its own Flow guard (a Flow blocked
    before this rule, e.g. the earlier change, edits again). The Flows whose draft changed."""
    if not definition_store.available():
        return []
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    fixed = []
    for row in get_store()._fetch_all("SELECT group_id FROM graph_drafts ORDER BY group_id", []) or []:
        flow_id = row["group_id"]
        try:
            if run_store.current_run(flow_id) is None:
                continue
            project_id = (db_groups.get_by_id(flow_id) or {}).get("project_id")
            if with_flow_guard(flow_id, lambda: restore_draft(flow_id, None, project_id)):
                fixed.append(flow_id)
        except Exception as exc:
            log.warning("[draft_restore] %s: startup repair failed: %s", flow_id, exc)
    return fixed
