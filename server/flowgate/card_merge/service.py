"""flowgate.v02test.0042: the card's merge state and a person's choices on it (see the package docstring).

State (``view``), from the card's active attempt (``result_merge``):

    hidden        nothing to merge, or merged (also no run / no attempt)
    waiting       ``pending``: queued, or behind another card's merge (``blocked_by``: ``result_merge.blocked_by``)
    catching_up   ``integrating``, or an AI resolving the card's open conflict session (the group's AI lease)
    needs_choice  ``conflict``: its group session open and no AI on it, or deferred with no session
                  (``conflict_deferred``: the group is free, a choice asks the merge again with a session)
    failed        ``failed`` (verification_failed, conflict_aborted, ...)

Each file carries how it overlaps (``kind``: both / deleted_latest / deleted_card), and a conflicting or failed card
the cards whose merge waits behind it (``waiting_cards``).

Choices (``act``), human only (the route takes a user JWT), each judged against ``expected_attempt_id`` and the
actions the state offers now (else 409 ``merge_state_changed``):

    ai_resolve     the existing resolve_conflict AI run on the card's session (default AI: the card's work AI)
    pick           per file: ``card`` (the card's result) / ``latest`` (the group branch) written as is, ``ai`` left
                   to one AI run. In the group session the group is ``ours`` (HEAD) and the result ``theirs``
                   (MERGE_HEAD); a person's whole-side pick is recorded as a supersede of that side (no AI check:
                   it is a person's explicit choice). A side that deleted the file cannot be picked (422). With no file
                   left to the AI, the merge goes on at once ([이대로 맞추고 병합]).
    merge          every file resolved: the existing session commit (``tr_commit_service.commit_conflict_resolution``);
                   the card is then done through the session's close notice as before
    retry          ``failed``: the merge is asked again as is (``pending`` / ``retry``, the Flow's drain)
    discard_rerun  an open session is aborted first (``abort_conflict_resolution``), then the card is retried on the
                   latest code (``graph_run.recovery.retry``, a new attempt; the old result stays in its history)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Optional

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store
from flowgate.graph_run import result_merge as rm, workspace

log = logging.getLogger(__name__)

HIDDEN, WAITING, CATCHING_UP, NEEDS_CHOICE, FAILED = "hidden", "waiting", "catching_up", "needs_choice", "failed"
AI_RESOLVE, PICK, MERGE, DISCARD_RERUN, RETRY = "ai_resolve", "pick", "merge", "discard_rerun", "retry"
ACTIONS = (AI_RESOLVE, PICK, MERGE, DISCARD_RERUN, RETRY)
SIDE_AI, SIDE_CARD, SIDE_LATEST = "ai", "card", "latest"
SIDES = (SIDE_AI, SIDE_CARD, SIDE_LATEST)
# The group session merges the result (MERGE_HEAD) into the group branch (HEAD): the group is ours.
SESSION_SIDES = {SIDE_CARD: ("theirs", "MERGE_HEAD"), SIDE_LATEST: ("ours", "HEAD")}
PICK_REASON = "person_pick_on_card"
RESOLVE_SCOPE = "resolve_conflict"
STATE_CHANGED = "merge_state_changed"


# ── reads ─────────────────────────────────────────────────────────────────────

def _title(flow_id: str, node_key: Optional[str]) -> Optional[str]:
    if not node_key:
        return None
    try:
        from flowgate.card_settings import repository as card_settings
        node = card_settings.card_node(flow_id, node_key) or {}
        return node.get("label") or node_key
    except Exception:  # noqa: BLE001 — a title is a label only
        return node_key


def _attempt(flow_id: str, node_key: str) -> tuple[Optional[dict], Optional[dict]]:
    run = run_store.current_run(flow_id)
    if run is None:
        return None, None
    attempt_id = run_store.active_attempt_id(run["run_id"], node_key)
    return run, (workspace.attempt(attempt_id) if attempt_id else None)


def _open_session(row: dict) -> Optional[dict]:
    merge_id = rm._merge_id(row)
    session = db_git.get_session(merge_id) if merge_id else None
    return session if session is not None and session.get("status") == "open" else None


def _resolving(flow_id: str) -> Optional[dict]:
    """The group's AI lease when a resolve_conflict run holds it (one open session per group: the card's)."""
    try:
        from modules.flow_gate.db import group_ai_leases
        lease = group_ai_leases.get_active(flow_id)
    except Exception:  # noqa: BLE001
        log.warning("card merge could not read the AI lease of %s", flow_id, exc_info=True)
        return None
    if lease and lease.get("action_scope") == RESOLVE_SCOPE:
        return {"run_id": lease.get("run_id")}
    return None


def _default_provider(flow_id: str, node_key: str) -> Optional[dict]:
    try:
        from flowgate.card_settings import repository as card_settings
        from modules.flow_gate.db import ai_providers
        provider_id = (card_settings.values(flow_id, node_key) or {}).get("run_ai") or \
            (card_settings.card_node(flow_id, node_key) or {}).get("assignee_provider_id")
        if not provider_id:
            return None
        return {"id": str(provider_id), "name": ai_providers.get_name_any(str(provider_id)) or str(provider_id)}
    except Exception:  # noqa: BLE001
        log.warning("card merge could not read the work AI of %s/%s", flow_id, node_key, exc_info=True)
        return None


def _empty(flow_id: str, node_key: str, attempt_id: Optional[str]) -> dict:
    return {"flow_id": flow_id, "node_key": node_key, "attempt_id": attempt_id, "state": HIDDEN,
            "integration_status": None, "reason": None, "blocked_by": None, "files": [], "session": None,
            "resolving": None, "default_provider": None, "actions": [], "waiting_cards": []}


def _waiting_cards(flow_id: str, row: dict) -> list[dict]:
    """The other cards of the run whose merge waits behind this one (``blocked_by`` names it): the tab's
    "기다리는 카드". Never raises."""
    try:
        rows = run_store._all(
            "SELECT run_id, node_key, integration_status, integration_reason FROM graph_node_attempts "
            "WHERE run_id = ? AND status = 'active' AND integration_status = 'pending' AND node_key <> ? "
            "ORDER BY created_at, attempt_id", [row["run_id"], row["node_key"]])
        keys = [r["node_key"] for r in rows if rm.blocked_by(r) == row["node_key"]]
    except Exception:  # noqa: BLE001 — a list for the screen only
        log.warning("card merge could not list the cards waiting on %s", row.get("node_key"), exc_info=True)
        return []
    return [{"node_key": key, "title": _title(flow_id, key)} for key in keys]


def _blocked_why(flow_id: str, key: str) -> str:
    """Why the card ``key`` holds the group: ``conflict`` (its open conflict session) or ``merging``."""
    try:
        session = db_git.get_open_session_by_group(flow_id)
        if session is not None and db_git.session_context(session).get("node_key") == key:
            return "conflict"
    except Exception:  # noqa: BLE001 — a label only
        pass
    return "merging"


def _session_kinds(row: dict) -> dict[str, str]:
    """How each file of the card's open session overlaps (read on the group worktree, where the merge is). {} when
    unreadable."""
    try:
        _project, root, _branch = rm._group_root(row)
        return rm.conflict_kinds(root) if root is not None else {}
    except Exception:  # noqa: BLE001 — a label only
        return {}


def view(flow_id: str, node_key: str) -> dict:
    run, row = _attempt(flow_id, node_key)
    out = _empty(flow_id, node_key, row["attempt_id"] if row else None)
    status = rm._status(row) if row else rm.NONE
    if row is None or status not in rm.WAITING:
        return out
    reason = row.get("integration_reason")
    out.update(integration_status=status, reason=reason, default_provider=_default_provider(flow_id, node_key))
    if status == rm.PENDING:
        key = rm.blocked_by(row)
        out.update(state=WAITING, blocked_by={"node_key": key, "title": _title(flow_id, key),
                                              "why": _blocked_why(flow_id, key)} if key else None)
    elif status == rm.INTEGRATING:
        out["state"] = CATCHING_UP
    elif status == rm.FAILED:
        out.update(state=FAILED, actions=[RETRY, DISCARD_RERUN])
    else:                                                    # conflict
        session = _open_session(row)
        if session is not None:
            merge_id = int(session["merge_id"])
            context = db_git.session_context(session)
            review_state = context.get("review_state") or "open"
            kinds = _session_kinds(row)
            files = [{"path": f["path"], "resolved": bool(f.get("resolved")), "kind": kinds.get(f["path"], rm.KIND_BOTH)}
                     for f in db_git.session_files(merge_id)]
            out.update(files=files, session={"merge_id": merge_id, "kind": db_git.session_kind(session),
                                             "review_state": review_state})
            resolving = _resolving(flow_id)
            if resolving is not None:
                out.update(state=CATCHING_UP, resolving=resolving)
            else:
                ready = review_state == "resolved" and not any(not f["resolved"] for f in files)
                out.update(state=NEEDS_CHOICE,
                           actions=[AI_RESOLVE, PICK] + ([MERGE] if ready else []) + [DISCARD_RERUN])
        elif reason == rm.CONFLICT_DEFERRED:
            paths, kinds = rm.deferred_conflict(row["attempt_id"])
            out.update(state=NEEDS_CHOICE, actions=[AI_RESOLVE, PICK, DISCARD_RERUN],
                       files=[{"path": p, "resolved": False, "kind": kinds.get(p, rm.KIND_BOTH)} for p in paths])
        else:
            out["state"] = WAITING          # its session closed, the close not settled yet: the next drain settles it
    if out["state"] in (NEEDS_CHOICE, FAILED, CATCHING_UP):
        out["waiting_cards"] = _waiting_cards(flow_id, row)
    return out


def merge_items(user_id: str, may_read: Callable[[str, str], bool]) -> list[dict]:
    """Every card waiting on its merge in a Flow the user may read (``may_read(user_id, project_id)``), for the
    notification window's runs tab. Hidden ones (merged meanwhile) are left out."""
    rows = run_store._all(
        "SELECT g.group_id, a.node_key FROM graph_node_attempts a JOIN graph_runs g ON g.run_id = a.run_id "
        "WHERE a.status = 'active' AND g.status IN ('running', 'paused', 'idle') "
        "AND a.integration_status IN ('pending', 'integrating', 'conflict', 'failed') "
        "ORDER BY g.group_id, a.created_at, a.attempt_id", [])
    from modules.flow_gate.db import groups as db_groups
    items: list[dict] = []
    groups: dict[str, Optional[dict]] = {}
    for r in rows:
        flow_id = r["group_id"]
        if flow_id not in groups:
            group = db_groups.get_by_id(flow_id)
            groups[flow_id] = group if group and may_read(user_id, group.get("project_id") or "") else None
        group = groups[flow_id]
        if group is None:
            continue
        try:
            state = view(flow_id, r["node_key"])
        except Exception:  # noqa: BLE001 — one unreadable card leaves the others listed
            log.warning("card merge item unreadable %s/%s", flow_id, r["node_key"], exc_info=True)
            continue
        if state["state"] == HIDDEN:
            continue
        items.append({"flow_id": flow_id, "flow_title": group.get("title") or group.get("name") or flow_id,
                      "node_key": r["node_key"], "title": _title(flow_id, r["node_key"]), "state": state["state"],
                      "reason": state["reason"], "blocked_by": state["blocked_by"]})
    return items


# ── choices ───────────────────────────────────────────────────────────────────

def _changed(**params: Any) -> GraphApiError:
    return GraphApiError(409, STATE_CHANGED, params, message="The card's merge state changed; read it again.")


def _git_error(exc) -> GraphApiError:
    return GraphApiError(int(getattr(exc, "status", 409) or 409), str(getattr(exc, "code", "git_error")),
                         dict(getattr(exc, "details", None) or {}), message=str(getattr(exc, "message", "") or exc))


def parse(body: Any) -> dict:
    errors: list[dict] = []
    if not isinstance(body, dict):
        raise GraphApiError(422, "invalid_request", {"errors": [{"field": "body", "reason": "object_required"}]})
    action = body.get("action")
    if action not in ACTIONS:
        errors.append({"field": "action", "reason": "unknown"})
    expected = body.get("expected_attempt_id")
    if not isinstance(expected, str) or not expected:
        errors.append({"field": "expected_attempt_id", "reason": "required"})
    provider_id = body.get("provider_id")
    if provider_id is not None and (not isinstance(provider_id, str) or not provider_id.strip()):
        errors.append({"field": "provider_id", "reason": "invalid"})
    files: list[dict] = []
    if action == PICK:
        raw = body.get("files")
        if not isinstance(raw, list) or not raw:
            errors.append({"field": "files", "reason": "required"})
        else:
            seen: set[str] = set()
            for i, f in enumerate(raw):
                path = f.get("path") if isinstance(f, dict) else None
                side = f.get("side") if isinstance(f, dict) else None
                if not isinstance(path, str) or not path or path in seen or side not in SIDES:
                    errors.append({"field": f"files[{i}]", "reason": "invalid"})
                    continue
                seen.add(path)
                files.append({"path": path, "side": side})
    if errors:
        raise GraphApiError(422, "invalid_request", {"errors": errors})
    return {"action": action, "expected_attempt_id": expected, "provider_id": provider_id, "files": files}


def _with_session(flow_id: str, node_key: str, row: dict, state: dict) -> dict:
    """The card's open conflict session for a choice: a deferred conflict asks the merge again first (now kept as
    the group's session). Returns the fresh state with its session; a merge that went through meanwhile, a busy
    group or any other move is refused (409) with the fresh state."""
    if state["session"] is not None:
        return state
    outcome = rm.request_resolution(row["attempt_id"])
    fresh = view(flow_id, node_key)
    if fresh["session"] is not None and fresh["attempt_id"] == row["attempt_id"]:
        return fresh
    if outcome == rm.PENDING:
        raise GraphApiError(409, "group_busy", {"state": fresh}, message="Another merge holds the group now; "
                                                                         "the card merges when it is free.")
    raise _changed(state=fresh)


def _side_content(root, rev: str, path: str) -> Optional[str]:
    from modules.flow_gate.services import git_service as gs
    proc = gs._run_git(["show", f"{rev}:{path}"], cwd=root)
    if proc.returncode != 0:
        return None
    out = proc.stdout
    if isinstance(out, bytes):
        try:
            out = out.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if "\ufffd" in (out or ""):
        return None                          # not UTF-8 text (``_run_git`` decodes with replacement)
    return out


def _apply_picks(flow_id: str, merge_id: int, picks: list[dict]) -> None:
    """Writes the picked sides of the session's files as they are (a person's explicit choice), through the
    session's own locked apply; ``complete`` freezes the candidate once nothing is left."""
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import conflict
    from modules.flow_gate.services.git.credentials import GitServiceError
    from modules.flow_gate.storage.safe_path import resolve_in_root
    try:
        _session, _cfg, project_id, root = gs._session_context(flow_id, merge_id)
    except GitServiceError as exc:
        raise _git_error(exc) from exc
    paths = {f["path"] for f in db_git.session_files(merge_id)}
    staged, supersedes, unavailable = [], [], []
    for pick in picks:
        if pick["path"] not in paths:
            raise _changed(path=pick["path"])
        side, rev = SESSION_SIDES[pick["side"]]
        content = _side_content(root, rev, pick["path"])
        target = resolve_in_root(root, pick["path"])
        if content is None or target is None:
            unavailable.append(pick["path"])
            continue
        try:
            original = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            original = ""
        staged.append((pick["path"], target, content, original))
        supersedes.append({"path": pick["path"], "side": side, "reason": PICK_REASON, "chunks": []})
    if unavailable:
        raise GraphApiError(422, "pick_unavailable", {"files": unavailable},
                            message="That side has no such file (deleted, or not text); leave it to the AI.")
    try:
        conflict._apply_conflict_resolution_locked(
            flow_id, merge_id, staged, True, supersedes, {p for p, *_ in staged}, None,
            expected_project_id=project_id)
    except GitServiceError as exc:
        raise _git_error(exc) from exc


def _start_ai(flow_id: str, merge_id: int, request, user_id: str, provider_id: Optional[str],
              default: Optional[dict], only: Optional[list[str]] = None) -> Optional[str]:
    from fastapi import HTTPException
    from modules.flow_gate.api.v1 import git_routes
    chosen = provider_id or (default or {}).get("id")
    messages = ["This merge conflict is a FlowGate card's result (the incoming side, MERGE_HEAD) against the latest "
                "group branch (HEAD). Keep the intent of both: the card's instruction and report, and the latest "
                "group changes."]
    if only:
        messages.append("A person already chose the other files on the card. Resolve only: " + ", ".join(only))
    try:
        return git_routes._start_resolve_conflict_run(
            group_id=flow_id, merge_id=merge_id, request=request, user_id=user_id, provider_id=chosen,
            provider_pinned=bool(chosen), messages=messages)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"detail": str(exc.detail)}
        raise GraphApiError(409, "ai_start_refused", detail, message="The AI could not start now.") from exc


def act(flow_id: str, node_key: str, user_id: str, body: Any, *, request=None,
        recheck: Optional[Callable[[], None]] = None) -> dict:
    choice = parse(body)
    run, row = _attempt(flow_id, node_key)
    state = view(flow_id, node_key)
    if row is None or row["attempt_id"] != choice["expected_attempt_id"]:
        raise _changed(state=state)
    if choice["action"] not in state["actions"]:
        raise _changed(state=state, action=choice["action"])
    started: Optional[str] = None
    action = choice["action"]
    if action == DISCARD_RERUN:
        _discard_rerun(flow_id, node_key, user_id, row, state, recheck)
    elif action == RETRY:
        # a failed merge asked again as is (the drain merges it on the latest group code)
        if not rm._set(row["attempt_id"], rm.PENDING, "retry", (rm.FAILED,)):
            raise _changed(state=view(flow_id, node_key))
        rm.request(flow_id)
    elif action == MERGE:
        _commit(flow_id, int(state["session"]["merge_id"]))
    else:
        state = _with_session(flow_id, node_key, row, state)
        merge_id = int(state["session"]["merge_id"])
        if action == PICK:
            direct = [f for f in choice["files"] if f["side"] != SIDE_AI]
            to_ai = [f["path"] for f in choice["files"] if f["side"] == SIDE_AI]
            if direct:
                _apply_picks(flow_id, merge_id, direct)
            if to_ai:
                started = _start_ai(flow_id, merge_id, request, user_id, choice["provider_id"],
                                    state["default_provider"], to_ai)
            elif MERGE in view(flow_id, node_key)["actions"]:
                # [이대로 맞추고 병합]: every file was picked by a person, so the merge goes on at once
                _commit(flow_id, merge_id)
        else:
            started = _start_ai(flow_id, merge_id, request, user_id, choice["provider_id"], state["default_provider"])
    return {**view(flow_id, node_key), "started_run_id": started}


def _commit(flow_id: str, merge_id: int) -> None:
    """The session's commit (the existing TR conflict commit): the card is then done through its close notice."""
    from modules.flow_gate.services import tr_commit_service
    from modules.flow_gate.services.git.credentials import GitServiceError
    try:
        tr_commit_service.commit_conflict_resolution(flow_id, merge_id)
    except GitServiceError as exc:
        raise _git_error(exc) from exc


def _discard_rerun(flow_id: str, node_key: str, user_id: str, row: dict, state: dict,
                   recheck: Optional[Callable[[], None]]) -> None:
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import tr_commit_service
    from modules.flow_gate.services.git.credentials import GitServiceError
    from flowgate.graph_run import recovery, recovery_basis
    if state["session"] is not None:
        try:
            tr_commit_service.abort_conflict_resolution(flow_id, int(state["session"]["merge_id"]))
        except GitServiceError as exc:
            raise _git_error(exc) from exc
        if view(flow_id, node_key)["session"] is not None:
            raise _changed(state=view(flow_id, node_key))
    group = db_groups.get_by_id(flow_id)
    if group is None:
        raise GraphApiError(404, "flow_not_found", {"flow_id": flow_id})
    context = recovery_basis.view(group, node_key)["context"]
    recovery.retry(group, user_id, node_key, {"request_id": "merge-discard-" + uuid.uuid4().hex,
                                              "expected_context": context}, recheck or (lambda: None))
