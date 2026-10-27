"""Card run settings rules (flowgate.v02.0053 T#2): read a card's settings, change one value, and
answer what a node's AI start takes from its card.

- A card is a node key of the Flow's Graph definition: a card only in the draft (just placed) and a
  card of confirmed revisions (also one whose run is done) are read and written the same way. Its
  kind (``fields.card_kind``) follows the draft node, else the latest revision node.
- One request changes one value, saved at once (no save button): ``value`` null takes the default
  back. The write runs inside the Flow guard, so it orders with draft saves and run steps.
- 0061 T#3: running/paused flows lock every card setting (409 ``graph_run_locked``).
  Done/open/review nodes remain protected while idle (409 ``graph_node_protected``), including
  instruction use and AI settings. The earlier node_settings_locked check remains as a fallback.
- 0061 T#11: Hold (``hold``: true / false or null) is written through the same PATCH for every card, with the Graph
  run's rules instead of the lock above (``flowgate.graph_run.hold``): refused while a run is running / paused /
  stopping (409 ``graph_run_locked``), setting refused on a node done in the Flow's run (409 ``graph_hold_done_node``),
  allowed on an open / in-review node (it bounds the opening only). Releasing changes the setting only: nothing opens
  until the next step / full command. Every change is a ``graph_canvas_changed`` (reason ``hold``) after the commit,
  with or without a run, and a ``graph_node_state_changed`` too when the Flow's run has the node.
- flowgate.v02.0085 T#1 (D0007 §7.2): a confirm gate (check card) is the exception to that lock. Its settings change
  while the gate is pending or open in a running or idle run (or in a Flow with no run) and nothing holds it - no
  reserved decision, no finished gate, no Time Machine operation, no open merge conflict, no pending round split
  (``flowgate.gate_decisions.settings_lock``); anything else is 409 ``gate_settings_locked`` with a reason. Every card
  has a settings version (``repository.settings_version``) that each write changing a value moves by one; a PATCH may
  carry ``expected_settings_version`` and a stale one is refused (409 ``gate_context_changed``) before anything is
  written. The structure lock of the definition is not touched.
- The requirement card's AI delegation on / off adds / removes the card's AI delegation document
  line (``card_documents.lines.set_delegation``), in the same guard.
- flowgate.v02.0072 T#3: a work plan card (WP) reads and writes its plan settings (``fields.PLAN``: the AI
  pre-instruction writing switch and the run block) by the same GET / PATCH, locks and failures. Nothing here touches
  the plan document (its Steps' AI, review AI, pre-instructions and attachments). flowgate.v02.0083 T#1 — 0072 T#3 개정
  (revised): the run block is read by a run now. ``uses_ai`` judges the plan card's writer (an explicit person assignee,
  else the card's work AI, else an explicit AI assignee, else a person) and ``run_settings`` hands an AI writer its
  work AI, time limit and restarts (``fields.run_plan``).
- ``run_settings`` is the Graph's ``card_settings_ports.run_settings`` handler: only stored values are
  handed on, so a card with nothing stored starts exactly as before. ``result_type`` is its
  ``result_type`` handler: the type an AI node's result is bound as (the dispatch told the AI the same).
- ``token_step_type`` is the source tools' ``worker_step_type_hooks`` handler: the token of an open AI
  node whose card changes what it writes is judged by that type (an omitted instruction's report),
  so the source access of the token follows what the AI was told to write. flowgate.v02.0083 T#2: a card
  that changes nothing (a WP / TS card, an omitted D / P / L / DB / T2 instruction, a card run opened before
  migration 148, a person's node) answers its node's own type, as the result binder binds it
  (``result_binder.result_type``) — never None, which the source tools read as the Flow root's type.
- flowgate.v02.0056 T#2: ``instruction_document`` is the Graph's ``card_settings_ports.instruction_document``
  handler — an AI card that runs an instruction stage (instruction use) names its instruction type and its
  instruction line's document, and whether that document is written (``card_documents.lines.has_body``: its
  body, not its existence, decides whether the instruction AI runs). While a node is in that stage its
  tokens write the instruction, so ``token_step_type`` judges them by the instruction type.
- flowgate.v02.0098 T#1 (D0004 §3.1 / §3.3 / §3.5): a check card's ``merge_target_branch`` (an exact local branch, null:
  the group's work base) is saved only after the Git merge target judgement of the authenticated group's project passes
  (``legacy.judge_merge_target``, in the same Flow guard, after the lock and version checks, in every merge mode); a
  refusal changes neither the value nor the version and keeps its own code (``_merge_target_refusal``). Null is removed
  with no Git read. A check card's ``push`` true is one change of ``push`` and ``merge_target`` = origin in the guard's
  one transaction (``_push_on``): the version moves once when either changes, an old branch value still in
  ``merge_target`` is kept as ``legacy_merge_target`` first, and a different legacy value already kept refuses the
  whole change (409 ``gate_settings_invalid``, reason ``legacy_merge_target_conflict``). A check card's GET / PATCH
  answer also carries the merge target outputs (``_with_merge_targets``), read after the write committed and never
  failing the response.
"""
from __future__ import annotations

from typing import Any, Optional

from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import slots as card_slots
from flowgate.card_settings import fields
from flowgate.card_settings import legacy_adapter as legacy
from flowgate.card_settings import repository
from flowgate.gate_decisions import settings_lock as gate_lock
from flowgate.graph_run import editing, hold

DELEGATION_FIELD = "delegation"
MERGE_TARGET_FIELD = "merge_target"
PUSH_FIELD = "push"
LEGACY_MERGE_TARGET = "legacy_merge_target"
ORIGIN = fields.MERGE_TARGET_REMOTES[0]
# The detail keys a merge target refusal may carry to the screen: names and counts, never a path, URL or diagnostic.
_MERGE_TARGET_DETAILS = ("base_branch", "checked_out_branch", "work_base_ref", "incoming_work_base_commits",
                         "merge_id", "blocking_group_id", "connected_group_id")
_MERGE_TARGET_MESSAGES = {
    "merge_target_invalid": "The merge target is not a valid local branch.",
    "merge_target_remote_only": "The merge target exists only on the remote.",
    "merge_target_internal_slot": "The merge target is a FlowGate group branch.",
    "merge_target_not_found": "The merge target branch does not exist.",
    "merge_target_same_branch": "The group's own branch cannot be its merge target.",
    "merge_target_incoming_work_base": "The merge target lacks history of the group's work base.",
    "merge_target_preview_unavailable": "Whether the merge target lacks work base history could not be checked.",
    "merge_target_busy": "Another merge uses the merge target.",
    "merge_target_owner_mismatch": "The merge target's workspace needs checking.",
}


def _card(flow_id: str, node_key: str) -> dict:
    node = repository.card_node(flow_id, node_key)
    if node is None:
        raise legacy.GraphApiError(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key},
                                   message="The card does not exist in this flow.")
    return node


def _payload(flow_id: str, node: dict, stored: dict) -> dict:
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    reason = gate_lock.lock_reason(flow_id, node["node_key"]) if kind == fields.CHECK else None
    # A gate is open while it waits for the person, so "open in the active run" (``legacy.node_running``) is not a
    # lock for it: only the gate's own lock decides. Every other card keeps the Graph lock and the running check.
    if kind == fields.CHECK:
        locked = reason is not None
    else:
        locked = editing.settings_locked(flow_id, node["node_key"]) or legacy.node_running(flow_id, node["node_key"])
    payload = {"flow_id": flow_id, "node_key": node["node_key"], "card_kind": kind,
               "locked": locked,
               "settings_version": repository.settings_version(flow_id, node["node_key"]),
               "fields": fields.view(kind, stored, legacy.repeat_choices())}
    if kind == fields.CHECK:
        payload["lock_reason"] = reason
        payload["legacy"] = fields.legacy_view(kind, stored)      # history only: usage, the old merge target branch
    return payload


def _with_merge_targets(group: dict, payload: dict, refresh: bool = False) -> dict:
    """flowgate.v02.0098 T#1 (D0004 §3.1 / §5): a check card's answer + the merge target outputs -
    ``merge_target_options``, ``default_target_branch``, ``merge_target_state`` (``merge_target.check_target_view``) and
    ``push_remote_check_required`` (push on with no remote chosen). Read-only: nothing stored changes, and a Git read
    failure is a state, never a failed response."""
    if payload.get("card_kind") != fields.CHECK:
        return payload
    shown = payload["fields"]
    branch = shown.get(fields.MERGE_TARGET_BRANCH.name) or {}
    selected = branch.get("value") if branch.get("stored") else None
    try:
        view = legacy.merge_target_view(group, selected if isinstance(selected, str) else None, refresh)
    except Exception:  # noqa: BLE001 - the settings answer stands without the candidates
        view = {"merge_target_options": [], "default_target_branch": None,
                "merge_target_state": {"status": "failed", "code": "merge_target_options_unavailable",
                                       "reason": "probe_failed", "current": None, "default": None}}
    push = (shown.get(PUSH_FIELD) or {}).get("value") is True
    return {**payload, **view,
            "push_remote_check_required": push and (shown.get(MERGE_TARGET_FIELD) or {}).get("value") != ORIGIN}


def read(group: dict, node_key: str, refresh_merge_targets: bool = False) -> dict:
    """``GET /flows/{flow_id}/nodes/{node_key}/settings``. ``refresh_merge_targets``: a check card's candidates are
    judged again instead of read from their cache (nothing else changes)."""
    flow_id = group["group_id"]
    node = _card(flow_id, node_key)
    return _with_merge_targets(group, _payload(flow_id, node, repository.values(flow_id, node_key)),
                               refresh_merge_targets)


def write(group: dict, node_key: str, name: str, value: Any, user_id: Optional[str],
          locale: Optional[str] = None, expected_version: Optional[int] = None) -> dict:
    """``PATCH /flows/{flow_id}/nodes/{node_key}/settings`` — one field, saved at once. ``locale`` (the request's)
    titles an AI delegation line document the change makes (flowgate.v02.0088). ``expected_version`` is the
    settings version the screen was drawn from (None: not checked)."""
    flow_id = group["group_id"]
    legacy.require_writable(flow_id)
    if not repository.available():
        raise legacy.GraphApiError(409, "card_settings_unavailable", {"flow_id": flow_id},
                                   message="Card settings are not available on this server yet.")
    registered = legacy.registered_doc_types(group.get("project_id"))

    def body() -> dict:
        if name == fields.HOLD.name:
            return _write_hold(group, node_key, value, user_id)
        found = repository.card_node(flow_id, node_key)
        if found is not None and fields.card_kind(found.get("node_type"), found.get("doc_type")) == fields.CHECK:
            gate_lock.require_unlocked(flow_id, node_key)             # D0007 §7.2: the gate's own lock
        else:
            editing.require_editable(flow_id, node_key)
        node = _card(flow_id, node_key)
        kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
        field = fields.field_of(kind, name)
        if field is None:
            raise legacy.GraphApiError(422, "invalid_request", {"errors": [
                {"field": "field", "reason": "not_a_setting_of_this_card", "card_kind": kind}]},
                message="This card has no such setting.")
        if kind != fields.CHECK and legacy.node_running(flow_id, node_key):     # a gate: gate_lock decided above
            raise legacy.GraphApiError(409, "node_settings_locked", {"flow_id": flow_id, "node_key": node_key},
                                       message="The card is running; its settings cannot change now.")
        if expected_version is not None:
            current = repository.settings_version(flow_id, node_key)
            if expected_version != current:
                raise legacy.GraphApiError(409, "gate_context_changed", {
                    "flow_id": flow_id, "node_key": node_key, "reason": "settings_version",
                    "expected_settings_version": expected_version, "settings_version": current})
        if value is None:
            changed = repository.remove(flow_id, node_key, name)
        else:
            try:
                checked = fields.check_value(field, value, repeat_choices=legacy.repeat_choices(),
                                             team_exists=legacy.team_exists, ai_exists=legacy.ai_exists)
            except fields.InvalidValue as exc:
                raise legacy.GraphApiError(422, "invalid_request", {"errors": [
                    {"field": "value", "reason": exc.reason}]}, message="The value does not fit this setting.")
            if field.kind == fields.LOCAL_BRANCH:
                _check_merge_target(group, node_key, checked)
            if kind == fields.CHECK and name == PUSH_FIELD and checked is True:
                changed = _push_on(flow_id, node_key, user_id)
            else:
                changed = repository.put(flow_id, node_key, name, checked, user_id)
        if changed:
            repository.bump_version(flow_id, node_key)
        stored = repository.values(flow_id, node_key)
        if name == DELEGATION_FIELD:
            card_lines.set_delegation(group, node, stored.get(DELEGATION_FIELD) == "on", user_id, registered,
                                      locale)
        if changed and name == fields.MERGE_TARGET_BRANCH.name:
            legacy.invalidate_merge_targets(group)
        return _payload(flow_id, node, stored)

    return _with_merge_targets(group, legacy.with_flow_guard(flow_id, body))


def _check_merge_target(group: dict, node_key: str, branch: str) -> None:
    """The repository check of a new ``merge_target_branch`` (D0004 §3.1): the authenticated group's own project,
    judged now (never the candidate cache). A refusal is raised as its own API error, nothing written."""
    try:
        legacy.judge_merge_target(group, branch)
    except legacy.GitServiceError as exc:
        raise _merge_target_refusal(group["group_id"], node_key, branch, exc) from exc


def _merge_target_refusal(flow_id: str, node_key: str, branch: str, exc: Exception) -> legacy.GraphApiError:
    """``GitServiceError`` -> the D0004 §3.3 PATCH answer: the judgement's own status and code, the field and the target
    named, only name / count details (never a Git diagnostic, a remote URL or a worktree path). Git missing or
    unusable is 409 ``gate_git_unavailable`` with its reason; any other Git error is a Git failure, never
    ``invalid_request``."""
    code = getattr(exc, "code", None)
    details = getattr(exc, "details", None) or {}
    params = {"flow_id": flow_id, "node_key": node_key, "field": fields.MERGE_TARGET_BRANCH.name,
              "target_branch": branch}
    if code == legacy.MERGE_TARGET_GIT_UNAVAILABLE:
        return legacy.GraphApiError(409, code, {**params, "reason": details.get("reason") or "unavailable"})
    if code in legacy.MERGE_TARGET_UNFIT or code == "base_checkout_branch_mismatch":
        params.update({key: details[key] for key in _MERGE_TARGET_DETAILS if details.get(key) is not None})
        return legacy.GraphApiError(int(getattr(exc, "status", 409)), code, {**params, "reason": code},
                                    message=_MERGE_TARGET_MESSAGES.get(code))
    return legacy.GraphApiError(409, legacy.MERGE_TARGET_GIT_UNAVAILABLE,
                                {**params, "reason": "git_failed", "cause": code})


def _push_on(flow_id: str, node_key: str, user_id: Optional[str]) -> bool:
    """D0004 §3.5: a check card's ``push`` true together with its remote ``merge_target`` = origin, inside the caller's
    Flow guard transaction (a refusal rolls back both). An old branch name still in ``merge_target`` (not moved by
    migration 158) is kept as ``legacy_merge_target`` first, unless a different legacy value is already kept: then the
    whole change is refused. An existing ``legacy_merge_target`` is never removed or overwritten. True when anything
    changed (the caller moves the version once)."""
    stored = repository.values(flow_id, node_key)
    changed = False
    remote = stored.get(MERGE_TARGET_FIELD)
    if remote != ORIGIN:
        if remote is not None:
            if LEGACY_MERGE_TARGET not in stored:
                changed = repository.put(flow_id, node_key, LEGACY_MERGE_TARGET, remote, user_id) or changed
            elif stored[LEGACY_MERGE_TARGET] != remote:
                raise legacy.GraphApiError(409, "gate_settings_invalid", {
                    "flow_id": flow_id, "node_key": node_key, "field": PUSH_FIELD,
                    "reason": "legacy_merge_target_conflict"})
        changed = repository.put(flow_id, node_key, MERGE_TARGET_FIELD, ORIGIN, user_id) or changed
    changed = repository.put(flow_id, node_key, PUSH_FIELD, True, user_id) or changed
    return changed


def _write_hold(group: dict, node_key: str, value: Any, user_id: Optional[str]) -> dict:
    """0061 T#11, inside the Flow guard: Hold on (true) / off (false or null: the default, no row). Not the card
    settings lock — the Graph run's rules (``hold.require_settable``): any card, also one open / in review."""
    flow_id = group["group_id"]
    if value is not None and not isinstance(value, bool):
        raise legacy.GraphApiError(422, "invalid_request", {"errors": [
            {"field": "value", "reason": "not_a_boolean"}]}, message="The value does not fit this setting.")
    on = value is True
    hold.require_settable(flow_id, node_key, on)
    node = _card(flow_id, node_key)
    before = repository.values(flow_id, node_key).get(fields.HOLD.name) is True
    if on:
        repository.put(flow_id, node_key, fields.HOLD.name, True, user_id)
    else:
        repository.remove(flow_id, node_key, fields.HOLD.name)
    if on != before:
        hold.changed(flow_id, group.get("project_id"), node_key, on, user_id)
    return _payload(flow_id, node, repository.values(flow_id, node_key))


def uses_ai(flow_id: str, node: dict) -> bool:
    """0063 B7: Designer cards have no explicit assignee. Runnable cards use their run AI,
    then the invoke service's project default. An explicit human assignment is never overridden;
    requirement delegation, gates and cards without a run plan retain their own contracts.

    flowgate.v02.0083 T#1 (0072 T#3 개정): a work plan card (WP) is judged in this order — an explicit person assignee:
    a person; the card's work AI (``run_ai``) stored: AI; an explicit AI assignee: AI; nothing: a person (the default
    stays a person's writing, so a WP a person already writes is never handed to an AI by a later command). Run / test
    cards keep their judgement (unassigned: AI, the project default when the card names none)."""
    assignment = node.get("assignee_kind")
    if assignment is not None:
        return assignment == "ai"
    if not repository.available():
        return False
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    if kind == fields.PLAN:
        return bool(node.get("node_key")) and bool(repository.values(flow_id, node["node_key"]).get("run_ai"))
    return kind in (fields.RUN, fields.TEST)


def run_settings(flow_id: str, node: dict) -> dict:
    """What a node's AI start takes from its card (``node``: the run's node row)."""
    if not repository.available() or not node.get("node_key"):
        return {}
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    return fields.run_plan(kind, repository.values(flow_id, node["node_key"]), node.get("node_type"),
                           node.get("doc_type"))


def result_type(flow_id: str, node: dict) -> Optional[str]:
    """The type an AI node's result is when its card changes it (instruction omitted), else None.

    Only an AI node reads its card at dispatch, so only an AI node's result type follows the card.
    The card cannot change while the node is open (locked), so this is the type its AI was told.
    flowgate.v02.0056 T#2: a node row that carries its node run's stage as ``run_stage`` None (the result binder,
    the source tools) is an AI card run opened before migration 148 — its AI was told the one-stage type of that
    time (``fields.result_doc_type`` ``one_stage``), so that type stays its result's.
    """
    if not uses_ai(flow_id, node) or not repository.available() or not node.get("node_key"):
        return None
    one_stage = "run_stage" in node and node.get("run_stage") is None
    return fields.result_doc_type(node.get("node_type"), node.get("doc_type"),
                                  repository.values(flow_id, node["node_key"]), one_stage=one_stage)


def instruction_document(flow_id: str, node: dict) -> dict:
    """``{doc_type, doc_id, written}`` of an AI card's instruction when the card runs an instruction stage
    (``node``: the run's node row), else ``{}``: its instruction type, its instruction line's live document
    (None when the line has none) and whether that document has a body."""
    if not uses_ai(flow_id, node) or not repository.available() or not node.get("node_key"):
        return {}
    doc_type = fields.instruction_stage(node.get("node_type"), node.get("doc_type"),
                                        repository.values(flow_id, node["node_key"]))
    if not doc_type:
        return {}
    line = card_lines.line_document(flow_id, node["node_key"], card_slots.INSTRUCTION, node.get("node_type"),
                                    node.get("doc_type")) or {}
    doc = line.get("document")
    if doc and doc["doc_id"] in legacy.invalidated_documents(flow_id, node["node_key"]):
        # 0061 T#7: what an invalidated attempt of the node wrote is not this attempt's instruction: the line counts
        # as having none, so the instruction AI writes a new one (never into the old attempt's document).
        doc = None
    return {"doc_type": doc_type, "doc_id": doc["doc_id"] if doc else None, "written": card_lines.has_body(doc)}


def token_step_type(token_rec: dict) -> Optional[str]:
    """The type the holder of a node's token writes when the node's card changes it, else None.

    Only a token the server mapped to a node (the node's dispatch token, the tokens its AI start and that
    start's retries minted) counts, and only while the node is open in its Flow's active run: the card is
    locked then, so this is the type the dispatch told the AI and the result binder binds (``result_type``)
    — or, while the node is in its instruction stage, the instruction type that stage writes.
    flowgate.v02.0083 T#2: when the card changes nothing it is the node's own type (``graph_model.run_doc_type``,
    the result binder's fallback), so the token is never judged by its ``doc_ref`` (the Flow's root R / B).
    """
    if not repository.available():
        return None
    found = legacy.open_node_of_token(token_rec)
    if not found:
        return None
    flow_id, node = found
    if node.get("run_stage") == fields.INSTRUCTION_STAGE and uses_ai(flow_id, node):
        written = fields.instruction_stage(node.get("node_type"), node.get("doc_type"),
                                           repository.values(flow_id, node["node_key"]))
        if written:
            return written
    return result_type(flow_id, node) or legacy.run_doc_type(node.get("node_type"), node.get("doc_type"))
