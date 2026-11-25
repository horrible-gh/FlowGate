"""Durable, bounded automatic resolution of graph_attempt_merge sessions only.

Each dispatch is recorded before the provider worker is registered. Recovery never
replays a dispatch: it follows its run/lease, or counts an interrupted admission as
one failed attempt. A database claim serializes coordinators across processes;
versioned writes fence human intervention. Git writes additionally hold G.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlsplit

from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.db import git_integration as sessions
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services.git.credentials import GitServiceError

log = logging.getLogger(__name__)
TIMEOUT = 600
CLAIM_SECONDS = 900
_dispatch = ContextVar("graph_merge_auto_dispatch", default=None)
_commit = ContextVar("graph_merge_auto_commit", default=None)
_claim_owner = ContextVar("graph_merge_auto_claim", default=None)
_loop_lock = threading.Lock()
_loop = None


def available():
    store = get_store()
    return bool(store and getattr(store, "table_exists", lambda name: False)("graph_merge_auto_resolve"))


def get(merge_id):
    if not available():
        return None
    row = get_store()._fetch_one("SELECT * FROM graph_merge_auto_resolve WHERE merge_id = ?", [merge_id])
    if row:
        row["data"] = json.loads(row["payload"])
    return row


def save(row):
    data = row["data"]
    n = get_store()._execute_affected(
        "UPDATE graph_merge_auto_resolve SET state = ?, payload = ?, version = version + 1 "
        "WHERE merge_id = ? AND version = ?",
        [data["state"], json.dumps(data, ensure_ascii=False), row["merge_id"], row["version"]])
    if n != 1:
        raise RuntimeError("auto_resolution_changed")
    row["version"] += 1
    row["state"] = data["state"]


def wire(row):
    if not row:
        return None
    d = row["data"]
    out = {k: d[k] for k in ("state", "attempt", "max_attempts", "attempts", "unresolved")}
    out["count"] = len(sessions.remaining_conflicts(row["merge_id"]))
    return out


def view(flow_id, node_key, attempt_id=None):
    if not available():
        return None
    row = get_store()._fetch_one(
        "SELECT merge_id FROM graph_merge_auto_resolve WHERE group_id = ? AND node_key = ? "
        + ("AND attempt_id = ? " if attempt_id else "") + "ORDER BY merge_id DESC LIMIT 1",
        [flow_id, node_key] + ([attempt_id] if attempt_id else []))
    return wire(get(row["merge_id"])) if row else None


def working(run_id):
    return available() and bool(get_store()._fetch_one(
        "SELECT merge_id FROM graph_merge_auto_resolve WHERE run_id = ? AND state = 'running'", [run_id]))


def start(attempt, merge_id):
    if not get(merge_id):
        run = run_store.run_by_id(attempt["run_id"])
        state = "skipped_paused" if not run or run["status"] in ("paused", "stopping") else "running"
        d = dict(state=state, attempt=1, max_attempts=3, attempts=[], unresolved=[], phase="next", ai_run_id=None)
        try:
            get_store()._execute(
                "INSERT INTO graph_merge_auto_resolve "
                "(merge_id, attempt_id, group_id, node_key, run_id, state, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [merge_id, attempt["attempt_id"], attempt["group_id"], attempt["node_key"], attempt["run_id"],
                 state, json.dumps(d)])
        except Exception:
            if not get(merge_id):
                raise
    ensure_loop()


def claim(merge_id, owner):
    return get_store()._execute_affected(
        "UPDATE graph_merge_auto_resolve SET owner = ?, claim_until = ?, version = version + 1 WHERE merge_id = ? "
        "AND (owner IS NULL OR claim_until < ?) AND state IN ('running', 'skipped_paused')",
        [owner, int(time.time()) + CLAIM_SECONDS, merge_id, int(time.time())]) == 1


def assert_running(merge_id):
    row = get(merge_id)
    run = run_store.run_by_id(row["run_id"]) if row else None
    if not row or row["state"] != "running" or not run or run["status"] not in ("running", "idle"):
        raise GitServiceError(409, "auto_resolution_stopped", "Automatic conflict resolution stopped")
    owner = _claim_owner.get()
    if owner is not None and (row["owner"] != owner or row["claim_until"] < int(time.time())):
        raise GitServiceError(409, "auto_resolution_claim_lost", "Automatic coordinator ownership changed")
    return row


def before_resolve(merge_id, resolver_run_id):
    row = get(merge_id)
    if not row:
        return
    if resolver_run_id is None:
        stop_by_person(merge_id, locked=True)
    elif resolver_run_id in row["data"].get("run_ids", []):
        current = assert_running(merge_id)
        if current["data"].get("ai_run_id") != resolver_run_id:
            raise GitServiceError(409, "stale_auto_resolver", "This automatic attempt has ended")


def before_commit(merge_id, root):
    if _commit.get() != merge_id:
        return
    assert_running(merge_id)
    from flowgate.graph_run import result_verify
    from modules.flow_gate.services import git_service as git
    tree = git._run_git(["write-tree"], cwd=root)
    if tree.returncode:
        raise GitServiceError(409, "verification_failed", "Cannot read resolved index")
    verdict = result_verify.check(root, git._rev_parse(root, "HEAD"), tree.stdout.strip())
    if verdict["status"] != result_verify.PASSED:
        raise GitServiceError(422, "verification_failed", json.dumps(verdict, ensure_ascii=False))


def stop_by_person(merge_id, *, locked=False):
    row = get(merge_id)
    if not row or row["state"] not in ("running", "skipped_paused"):
        return
    if not locked:
        from flowgate.graph_run import result_merge as rm, workspace
        project = rm._project_of(row["group_id"])
        lock = rm.git_commit._acquire_group_lock(project, row["group_id"], "graph_attempt_merge", no_wait=True)
        if lock is None:
            raise GitServiceError(409, "git_busy", "Automatic merge is completing; retry the human action")
        try:
            return stop_by_person(merge_id, locked=True)
        finally:
            rm.git_commit._release_group_lock(lock)
    while True:
        row = get(merge_id)
        if not row or row["state"] not in ("running", "skipped_paused"):
            return
        row["data"]["state"] = "stopped_by_person"
        try:
            save(row)
            cancel(row)
            return
        except RuntimeError:
            continue


def cancel(row):
    from modules.flow_gate.services import ai_invoke_service as ai, token_service
    from modules.flow_gate.db import group_ai_leases
    run_id = row["data"].get("ai_run_id")
    if not run_id:
        return
    lease = group_ai_leases.get_active(row["group_id"])
    if lease and lease.get("run_id") == run_id and lease.get("token_id"):
        token_service.revoke(lease["token_id"], reason="graph_auto_resolution_stopped")
    try:
        ai.cancel_run(run_id)
    except Exception:
        log.info("Automatic resolver %s no longer local; token fenced", run_id)


def admission_requested(merge_id):
    if merge_id is not None and _dispatch.get() is None:
        stop_by_person(merge_id)


def bind_run(run):
    dispatch = _dispatch.get()
    if dispatch is None:
        return
    merge_id, no = dispatch
    row = assert_running(merge_id)
    if row["data"]["attempt"] != no or row["data"]["phase"] != "launching":
        raise RuntimeError("auto_resolution_changed")
    row["data"].update(ai_run_id=run["run_id"], phase="waiting")
    row["data"].setdefault("run_ids", []).append(run["run_id"])
    save(row)  # before the worker can run; a failed write refuses admission


def notify(row):
    from modules.flow_gate.services import git_service as git
    from flowgate.graph_run import execution, result_merge
    project = result_merge._project_of(row["group_id"])
    try:
        git._emit("git_status_changed", project, row["group_id"],
                  {"group_id": row["group_id"], "merge_id": row["merge_id"],
                   "reason": "conflict_auto_" + row["data"]["state"], "auto_resolve": wire(row)})
    finally:
        result_merge._after(row["attempt_id"])
        execution.settle(row["group_id"])


def unresolved(row):
    from modules.flow_gate.services.git import conflict
    from modules.flow_gate.storage.safe_path import resolve_in_root
    _, _, _, root = conflict._session_context(row["group_id"], row["merge_id"])
    out = []
    for path in sessions.remaining_conflicts(row["merge_id"]):
        hunks, start_line = [], None
        target = resolve_in_root(root, path) if root else None
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines() if target else []
        except OSError:
            lines = []
        for no, line in enumerate(lines, 1):
            if line.startswith("<<<<<<<"):
                start_line = no
            elif line.startswith(">>>>>>>") and start_line is not None:
                hunks.append(dict(start_line=start_line, end_line=no))
                start_line = None
        if start_line is not None:
            hunks.append(dict(start_line=start_line, end_line=len(lines)))
        out.append(dict(path=path, hunks=hunks))
    return out


def reopen_candidate(row):
    # A human edit and reopening a rejected candidate use the same G as commit/resolve.
    from flowgate.graph_run import result_merge as rm
    project = rm._project_of(row["group_id"])
    lock = rm.git_commit._acquire_group_lock(project, row["group_id"], "graph_attempt_merge", no_wait=True)
    if lock is None:
        raise GitServiceError(409, "git_busy", "Candidate is being edited")
    try:
        fresh = assert_running(row["merge_id"])
        if fresh["version"] != row["version"]:
            raise RuntimeError("auto_resolution_changed")
        paths = [f["path"] for f in sessions.session_files(row["merge_id"])]
        sessions.replace_session_files(row["merge_id"], paths)
        session = sessions.get_session(row["merge_id"])
        context = sessions.session_context(session)
        context["review_state"] = "open"
        context.pop("review_fingerprint", None)
        sessions.set_session_context(row["merge_id"], context)
    finally:
        rm.git_commit._release_group_lock(lock)


def reason_summary(result, reason):
    """Readable display text, with the original diagnostic kept in reason."""
    if not reason:
        return None
    raw = str(reason)
    if result == "check_failed":
        try:
            verdict = json.loads(raw)
        except (ValueError, TypeError):
            verdict = None
        if isinstance(verdict, dict):
            items = []
            for failure in verdict.get("failures") or []:
                location = failure.get("path") or "unknown file"
                if failure.get("line") is not None:
                    location += ":" + str(failure["line"])
                items.append(f"{failure.get('checker') or '검사'} · {location}: {failure.get('message') or '검사 실패'}")
            raw = "; ".join(items) or "병합 검사 실패: " + str(verdict.get("status") or "unknown")
    return " ".join(raw.split())[:1000]


def ai_failure(record):
    if not record:
        return "AI 실행 등록 전에 중단되었습니다"
    failed = (record.get("outcome") in ("error", "failed")
              or record.get("end_reason") in ("worker_error", "timeout", "provider_failed")
              or record.get("stop_code") == "provider_failed"
              or record.get("exit_code") not in (None, 0))
    if not failed:
        return None
    detail = record.get("error_message") or record.get("message") or record.get("end_reason") or record.get("stop_code") or "실행 실패"
    exit_code = record.get("exit_code")
    return f"AI 실행 실패 (exit code {exit_code}): {detail}" if exit_code not in (None, 0) else f"AI 실행 실패: {detail}"


def finish_attempt(row, result, reason=None):
    d = row["data"]
    d["attempts"][-1].update(result=result, reason=" ".join(str(reason).splitlines())[:2000] if reason else None,
                              reason_summary=reason_summary(result, reason), ended_at=now_iso())
    d["ai_run_id"] = None
    if result == "check_failed":
        reopen_candidate(row)
    if result == "resolved":
        d["state"] = "succeeded"
    elif d["attempt"] >= 3:
        d["state"] = "exhausted"
        d["unresolved"] = unresolved(row)
    else:
        d["phase"] = "next"
    save(row)
    try:
        notify(row)
    except Exception:
        log.exception("Automatic resolution outcome recorded; notification failed for %s", row["merge_id"])


def providers(row):
    from modules.flow_gate.services import ai_invoke_service as ai
    from modules.flow_gate.db import groups
    from flowgate.card_merge.service import _default_provider
    group = groups.get_by_id(row["group_id"]) or {}
    user = group.get("owner_id") or (run_store.run_by_id(row["run_id"]) or {}).get("started_by")
    catalog = ai.list_runtime_providers(group["project_id"], user)
    chosen = _default_provider(row["group_id"], row["node_key"]) or {"id": catalog.get("default_provider_id")}
    candidates = catalog.get("providers") or []
    if row["data"]["attempt"] == 3:
        used = {a["provider"]["id"] for a in row["data"]["attempts"] if a.get("provider")}
        chosen = next((p for p in candidates if p.get("id") not in used), None)
    else:
        chosen = next((p for p in candidates if p.get("id") == chosen.get("id")), chosen)
    return group, user, ({"id": chosen["id"], "name": chosen.get("name") or chosen["id"]}
                         if chosen and chosen.get("id") else None)


def launch(row, group, user, provider):
    from starlette.requests import Request
    from modules.flow_gate.graph_workflow.services.ai_node_starter import api_base_url
    from modules.flow_gate.api.v1.git_routes import _resolve_conflict_mention_builder
    from modules.flow_gate.services import ai_invoke_service as ai
    base = api_base_url(user)
    parsed = urlsplit(base)
    request = Request({"type": "http", "method": "POST", "scheme": parsed.scheme,
                       "server": (parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)),
                       "path": parsed.path, "root_path": "", "query_string": b"", "headers": []})
    messages = ["Resolve this card result against the latest flow branch. Preserve both intents. "
                "The server automatically validates and merges the result; no human approval is required."]
    messages += [f"Attempt {a['no']} ({a['mode']}) failed: {a['result']}: {a['reason']}"
                 for a in row["data"]["attempts"][:-1]]
    token = _dispatch.set((row["merge_id"], row["data"]["attempt"]))
    try:
        assert_running(row["merge_id"])
        ai.start_run(project_id=group["project_id"], module=group.get("module"), group_id=row["group_id"],
                     doc_ref="", action_scope="resolve_conflict", mode="single", continuation_target_seq=None,
                     continuation_review_mode=False, continuation_instruction_mode=None, continuation_locale="ko",
                     issued_to=user, api_base_url=base, provider_id=provider["id"], provider_pinned=True,
                     continuation_step_timeout_sec=TIMEOUT, continuation_restart_max_attempts=0,
                     merge_id=row["merge_id"], mention_builder=_resolve_conflict_mention_builder(
                         group_id=row["group_id"], project_id=group["project_id"], merge_id=row["merge_id"],
                         request=request, locale="ko", messages=messages))
    finally:
        _dispatch.reset(token)


def reapply(row):
    """Abort only this session's merge; replay the original result onto current HEAD under G.
    --no-commit keeps even a clean replay behind the existing candidate checks.
    """
    from flowgate.graph_run import result_merge as rm, workspace
    from modules.flow_gate.services import git_service as git
    from modules.flow_gate.services.git import conflict
    attempt = workspace.attempt(row["attempt_id"])
    project, root, _ = rm._group_root(attempt)
    lock = rm.git_commit._acquire_group_lock(project, row["group_id"], "graph_attempt_merge", no_wait=True)
    if lock is None:
        return False
    try:
        assert_running(row["merge_id"])
        session = sessions.get_session(row["merge_id"])
        if not session or session["status"] != "open":
            raise RuntimeError("session_closed")
        head = git._rev_parse(root, "MERGE_HEAD")
        if head:
            if head != attempt["result_commit"]:
                raise RuntimeError("merge_identity_changed")
            if git._abort_merge(root).returncode:
                raise RuntimeError("merge_abort_failed")
        proc = git._run_git([*git._GIT_IDENT, "-c", "merge.conflictStyle=zdiff3",
                              "merge", "--no-commit", "--no-ff", attempt["result_commit"]], cwd=root)
        paths = git._unmerged_paths(root)
        if proc.returncode and not paths:
            raise RuntimeError("reapply_failed")
        context = sessions.session_context(session)
        context.pop("review_fingerprint", None)
        context.pop("snapshot_tree", None)
        context["review_state"] = "open"
        context["reapplied_head"] = git._rev_parse(root, "HEAD")
        previous_paths = [f["path"] for f in sessions.session_files(row["merge_id"])]
        sessions.replace_session_files(row["merge_id"], paths or previous_paths, resolved=not paths)
        if not paths:
            context.update(conflict._freeze_tr_candidate(root, context))
            context["review_state"] = "resolved"
        sessions.set_session_context(row["merge_id"], context)
        if not paths and git._rev_parse(root, "MERGE_HEAD") is None and git._is_ancestor(root, attempt["result_commit"], "HEAD"):
            from flowgate.graph_run import result_verify
            verdict = result_verify.check(root, attempt["input_commit"], git._rev_parse(root, "HEAD"))
            if verdict["status"] != result_verify.PASSED:
                raise GitServiceError(422, "verification_failed", json.dumps(verdict))
            sessions.close_session(row["merge_id"], "done")
            git._set_status(row["group_id"], context.get("prev_status") or "waiting")
        return True
    finally:
        rm.git_commit._release_group_lock(lock)


def commit_candidate(row):
    from modules.flow_gate.services import tr_commit_service
    token = _commit.set(row["merge_id"])
    try:
        context = sessions.session_context(sessions.get_session(row["merge_id"]))
        tr_commit_service.commit_conflict_resolution(row["group_id"], row["merge_id"],
                                                    review_fingerprint=context.get("review_fingerprint"))
    finally:
        _commit.reset(token)


def step(row):
    from modules.flow_gate.db import group_ai_leases, ai_invoke_runs
    from modules.flow_gate.services import ai_invoke_service as ai
    d, merge_id = row["data"], row["merge_id"]
    session = sessions.get_session(merge_id)
    if not session or session["status"] != "open":
        if d["attempts"] and session and session["status"] == "done":
            finish_attempt(row, "resolved")
        else:
            d["state"] = "skipped_paused"
            save(row)
        return
    run = run_store.run_by_id(row["run_id"])
    if not run or run["status"] not in ("running", "idle"):
        d["state"] = "skipped_paused"
        save(row)
        cancel(row)
        return
    if row["state"] == "skipped_paused":
        d["state"] = "running"
        save(row)
        notify(row)
    lease = group_ai_leases.get_active(row["group_id"])
    if d["attempts"] and d["phase"] not in ("next", "waiting", "launching"):
        started = datetime.fromisoformat(d["attempts"][-1]["started_at"])
        if (datetime.now(timezone.utc) - started).total_seconds() >= TIMEOUT:
            finish_attempt(row, "timeout", "Attempt exceeded 600 seconds while replaying or validating")
            return
    if d["phase"] == "next":
        if lease:
            return
        no = len(d["attempts"]) + 1
        if no > 3:
            return
        d.update(attempt=no, phase="rebase" if no == 2 else "prepare", ai_run_id=None)
        d["attempts"].append(dict(no=no, mode={1:"ai", 2:"rebase", 3:"switch_ai"}[no], provider=None,
                                  result="running", reason=None, started_at=now_iso(), ended_at=None))
        save(row)
    if d["phase"] == "rebase":
        if not reapply(row):
            return
        if sessions.get_session(merge_id)["status"] == "done":
            finish_attempt(row, "resolved")
            return
        d["phase"] = "prepare"
        save(row)
    if d["phase"] == "prepare":
        if not sessions.remaining_conflicts(merge_id):
            d["phase"] = "commit"
            save(row)
        else:
            group, user, provider = providers(row)
            if provider is None:
                finish_attempt(row, "no_other_ai" if d["attempt"] == 3 else "ai_error",
                               "다른 AI 없음" if d["attempt"] == 3 else "사용 가능한 AI 없음")
                return
            d["attempts"][-1]["provider"] = provider
            if d["attempt"] == 2:
                d["attempts"][-1]["mode"] = "rebase_ai"
            d["phase"] = "launching"
            save(row)
            launch(row, group, user, provider)
            return
    if d["phase"] in ("launching", "waiting"):
        run_id = d.get("ai_run_id")
        started = datetime.fromisoformat(d["attempts"][-1]["started_at"])
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if lease and (not run_id or lease.get("run_id") == run_id):
            if elapsed >= TIMEOUT:
                d["timed_out"] = True
                save(row)
                cancel(row)
            return  # never overlap a worker, even while cancellation is settling
        record = (ai.get_run_record(run_id) or ai_invoke_runs.get(run_id)) if run_id else None
        if record and record.get("status") not in (None, "finished"):
            if elapsed >= TIMEOUT:
                d["timed_out"] = True
                save(row)
                cancel(row)
            return
        if d.pop("timed_out", False):
            finish_attempt(row, "timeout", "Resolver exceeded 600 seconds")
            return
        failure = ai_failure(record)
        if failure:
            finish_attempt(row, "ai_error", failure)
            return
        if sessions.remaining_conflicts(merge_id):
            rejection = sessions.session_context(session).get("resolver_attempt") or {}
            finish_attempt(row, "conflicts_left", rejection.get("code") or "remaining_conflicts is not empty")
            return
        d["phase"] = "commit"
        save(row)
    if d["phase"] == "commit":
        try:
            commit_candidate(row)
        except Exception as exc:
            if getattr(exc, "code", None) == "git_busy":
                return
            finish_attempt(row, "check_failed", getattr(exc, "message", None) or str(exc))
        else:
            finish_attempt(row, "resolved")


def tick(merge_id):
    owner = uuid.uuid4().hex
    if not claim(merge_id, owner):
        return
    token = _claim_owner.set(owner)
    try:
        row = get(merge_id)
        try:
            step(row)
        except Exception as exc:
            fresh = get(merge_id)
            if fresh and fresh["state"] == "running" and fresh["version"] == row["version"] and fresh["data"]["attempts"] and not fresh["data"].get("ai_run_id"):
                finish_attempt(fresh, "check_failed" if getattr(exc, "code", None) == "verification_failed" else "ai_error", str(exc))
            else:
                log.info("Automatic resolution changed during tick %s", merge_id)
    finally:
        _claim_owner.reset(token)
        get_store()._execute("UPDATE graph_merge_auto_resolve SET owner = NULL, claim_until = 0 "
                             "WHERE merge_id = ? AND owner = ?", [merge_id, owner])


def ensure_loop():
    global _loop
    with _loop_lock:
        if _loop is None or not _loop.is_alive():
            _loop = threading.Thread(target=_run, name="graph-merge-auto", daemon=True)
            _loop.start()


def _run():
    while True:
        try:
            rows = get_store()._fetch_all("SELECT merge_id FROM graph_merge_auto_resolve "
                                          "WHERE state IN ('running', 'skipped_paused')", [])
            for row in rows:
                tick(row["merge_id"])
        except Exception:
            log.exception("Automatic card merge recovery failed")
        time.sleep(2)


def recover():
    if not available():
        return
    from flowgate.graph_run import workspace
    for session in sessions.list_open_sessions():
        context = sessions.session_context(session)
        if (sessions.session_kind(session) == sessions.SESSION_KIND_GRAPH_ATTEMPT_MERGE
                and context.get("auto_resolve_requested") and not get(session["merge_id"])):
            attempt = workspace.attempt(context.get("attempt_id"))
            if attempt and attempt["status"] == "active":
                start(attempt, session["merge_id"])
    ensure_loop()
