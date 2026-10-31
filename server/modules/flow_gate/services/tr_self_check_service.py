"""Advisory self-check lifecycle on a live managed TR worktree."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import tr_self_check_runs as db_runs
from modules.flow_gate.services import git_service
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_gate as gate
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_result as result_block
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services.git import lock_manager
from modules.flow_gate.storage import filesystem as storage_fs

_log = logging.getLogger(__name__)
_controls: dict[str, tuple[executor.ProcessControl, threading.Event]] = {}
_controls_lock = threading.RLock()
_HOST_PATH = os.environ.get("PATH", "")
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+|bearer\s+)[^\s\"']+")
_SECRET_RE = re.compile(r"(?i)\b((?:FLOWGATE_TOKEN|[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|SECRET_KEY|PASSWORD))\s*[:=]\s*)[^\s\"']+")


class SelfCheckError(Exception):
    def __init__(self, status: int, code: str, detail: str | None = None, details: dict | None = None):
        self.status, self.code, self.detail = status, code, detail or code
        self.details = details or {}
        super().__init__(self.detail)


def _cleanup_runtime(runtime: Path, run_id: str, *, tracked: bool = True) -> None:
    try:
        storage_fs.remove_managed_tree(runtime, Path(tempfile.gettempdir()), missing_ok=True)
    except Exception as exc:
        error = "runtime_cleanup_failed code=%s errno=%s winerror=%s" % (
            getattr(exc, "code", "cleanup_failed"), getattr(exc, "errno", None),
            getattr(exc, "winerror", None),
        )
        _log.warning("scratch cleanup failed: owner=self-check id=%s stage=runtime %s path=%s",
                     run_id, error, runtime.name)
        if tracked:
            try:
                db_runs.set_cleanup_result(run_id, cleanup_pending=True, cleanup_error=error)
            except Exception:
                _log.warning("scratch cleanup state failed: owner=self-check id=%s stage=cleanup_result",
                             run_id)


def _emit(row: dict | None) -> None:
    if row:
        git_service._emit("self_check_run_updated", row["project_id"], row["group_id"], {
            "project_id": row["project_id"], "group_id": row["group_id"],
            "tr_doc_id": row["tr_doc_id"], "self_check_run_id": row["self_check_run_id"],
            "draft": row.get("tr_doc_id") is None,
            "status": row["status"], "cancel_requested": row["cancel_requested"],
            "recovery_state": row["recovery_state"]})


def _redact(value: str | None) -> str:
    text = value or ""
    text = _BEARER_RE.sub(r"\1[REDACTED]", text)
    text = _SECRET_RE.sub(r"\1[REDACTED]", text)
    return text


def public(row: dict) -> dict:
    keep = ("self_check_run_id", "project_id", "group_id", "tr_doc_id", "status", "policy_version",
            "program", "args", "resolved_executable_name", "executable_origin", "cwd_relative",
            "timeout_seconds", "exit_code", "timed_out", "cancel_requested", "stdout_tail", "stderr_tail",
            "source_head_before", "source_head_after", "source_branch_before", "source_branch_after",
            "source_changed_during_run", "worktree_state_changed", "recovery_state", "error_code",
            "created_at", "started_at", "finished_at", "updated_at", "linked_at",
            # 0110 T#3: a Graph attempt run's basis (None for a group worktree run).
            "attempt_id", "input_commit")
    result = {key: row.get(key) for key in keep}
    result["stdout_tail"] = _redact(result["stdout_tail"])
    result["stderr_tail"] = _redact(result["stderr_tail"])
    # 0114 T#3 (D 5): the result block summary and its environment evidence. Evidence only --
    # status / exit_code / timed_out above stay the run's verdict fields.
    result["result_summary"] = result_block.loads(row.get("result_summary"))
    result["environment_evidence"] = result_block.loads(row.get("environment_evidence"))
    return result


_ACTIVE = ("pending", "running")


def summary(row: dict) -> dict:
    """Which run holds a Group's single run slot, for any reader of that Group (0080 T#2,
    NR0007 §5 item 1). The slot is per Group, not per TR: the holder may be another TR's run
    or a draft run. No arguments and no output tails -- only what tells the run apart and
    where it is read (``group_read_path``, under the API base)."""
    rid = row["self_check_run_id"]
    draft = row.get("tr_doc_id") is None
    keep = ("project_id", "group_id", "tr_doc_id", "status", "recovery_state", "cancel_requested",
            "program", "created_at", "started_at", "updated_at", "linked_at", "attempt_id")
    return {"self_check_run_id": rid, **{key: row.get(key) for key in keep}, "draft": draft,
            "owner_kind": "draft" if draft else "tr", "group_read_path": f"/self-check/group/runs/{rid}"}


#: 0080 T#3 (NR0007 §5 item 8): the reason-specific code next to a refusal's general code,
#: as ``details.reason_code`` (409 already_running / cancel_unconfirmed) or as the code of a
#: refused re-verification. Keys are ``tr_self_check_proof`` reason codes.
_REASON_CODES = {
    proof.OWNER_ALIVE: "selfcheck_owner_alive",
    proof.OWNERSHIP_UNKNOWN: "selfcheck_ownership_unknown",
    proof.OWNER_SERVER_UNPROVEN: "selfcheck_ownership_unknown",
    proof.TREE_UNPROVEN: "selfcheck_tree_unproven",
    "recovery_error": "selfcheck_tree_unproven",
}


def _already_running(run_id: str, target: dict) -> SelfCheckError:
    """``selfcheck_already_running`` with the holder's summary; ``same_owner`` = the caller's
    own TR or draft token started it. A holder of another owner (another TR, another draft
    token) adds ``reason_code: selfcheck_foreign_run`` -- the caller can read it through the
    Group route but neither read nor cancel it through its own."""
    details = {}
    try:
        row = db_runs.get_run(run_id)
    except Exception:
        _log.warning("selfcheck %s: reading the active run failed", run_id, exc_info=True)
        row = None
    if row:
        own_tr = target.get("tr_doc_id") is not None and row.get("tr_doc_id") == target.get("tr_doc_id")
        own_token = target.get("owner_token_id") is not None and row.get("owner_token_id") == target.get("owner_token_id")
        details["run"] = {**summary(row), "same_owner": bool(own_tr or own_token)}
        if not (own_tr or own_token):
            details["reason_code"] = "selfcheck_foreign_run"
    return SelfCheckError(409, "selfcheck_already_running", run_id, details=details)


def _document(doc_id: str) -> dict:
    doc = db_documents.get_by_id(doc_id)
    if not doc or str(doc.get("type_code") or "").upper() != "TR":
        raise SelfCheckError(404, "selfcheck_document_unavailable")
    group = db_groups.get_by_id(doc["group_id"])
    if not group or group.get("deleted_at") or str(group.get("status") or "").upper() == "DISPOSED":
        raise SelfCheckError(409, "selfcheck_worktree_unavailable")
    return doc


def draft_target(token: dict) -> dict:
    """Admit a TR(new) worker token as the owner of pre-registration runs (0638 T#1).

    The TR row does not exist yet, so the owner is the live token itself. The scope name
    alone is not enough: the token's sequence document must sit in the token's own
    project/group and its workflow head must be a pending TR -- the same head lookup that
    decides the token's source tools and help (remote_tool_service). Returns the target
    dict ``_start`` and ``_worktree`` take.

    0110 T#3: a Graph attempt token is admitted the same way; where its run executes is
    added by ``_start`` (``_attempt_target``, under the Stop gate), so reading or cancelling
    its own runs does not depend on the attempt still being writable.
    """
    from modules.flow_gate.services import remote_tool_service  # lazy -- import cycle

    token_id, doc_ref = token.get("token_id"), token.get("doc_ref")
    project_id, group_id = token.get("project"), token.get("group_id")
    if token.get("action_scope") != "new" or not (token_id and doc_ref and project_id and group_id):
        raise SelfCheckError(403, "selfcheck_forbidden")
    doc = db_documents.get_by_id(doc_ref)
    if not doc or doc.get("project_id") != project_id or doc.get("group_id") != group_id:
        raise SelfCheckError(403, "selfcheck_forbidden")
    step_type, lookup_failed = remote_tool_service._worker_token_step_type_result(token)
    if lookup_failed or str(step_type or "").upper() != "TR":
        raise SelfCheckError(403, "selfcheck_forbidden")
    group = db_groups.get_by_id(group_id)
    if not group or group.get("deleted_at") or str(group.get("status") or "").upper() == "DISPOSED":
        raise SelfCheckError(409, "selfcheck_worktree_unavailable")
    return {"project_id": project_id, "group_id": group_id, "tr_doc_id": None,
            "owner_token_id": token_id, "draft_doc_ref": doc_ref}


def _attempt_target(token: dict | None, project_id: str, group_id: str) -> dict:
    """0110 T#3 (NR0003 §3 T-B): the Graph attempt fields of a run target.

    ``{}`` for an unmapped v0.1 token, whose run keeps the group worktree. A token mapped to
    a Graph attempt runs in that attempt's tree, resolved by the T-A helper
    ``graph_source_hooks.attempt_scope`` (active attempt, worktree mode, owner marker,
    ``marker.input_commit == row.input_commit``). A stopping run or an inactive attempt is
    refused first (``require_writable``, 409 ``graph_attempt_inactive``); an attempt whose
    tree cannot be resolved is 409 ``graph_worktree_unavailable`` with its reason. The group
    worktree is never used in its place.

    Called by ``_start`` inside its ``write_session``: the judgement's shared hold of the Stop
    gate is kept until the run's spawn is over (see ``_start``)."""
    from modules.flow_gate.services import graph_source_hooks
    try:
        bound = graph_source_hooks.binding(token)
    except Exception as exc:
        # A failed mapping lookup does not prove a v0.1 token: refuse, never fall back.
        _log.warning("selfcheck: Graph attempt mapping lookup failed", exc_info=True)
        raise SelfCheckError(409, "selfcheck_worktree_unavailable",
                             details={"reason": "binding_lookup_failed"}) from exc
    if not bound:
        return {}
    try:
        graph_source_hooks.require_writable(token, "self_check")
        scope = graph_source_hooks.attempt_scope(token, project_id=project_id, group_id=group_id)
    except graph_source_hooks.GraphSourceError as exc:
        raise SelfCheckError(409, exc.code, exc.message, details=dict(exc.details)) from exc
    return {"attempt_id": scope["attempt_id"], "input_commit": scope["input_commit"],
            "attempt_token": token}


def _attempt_worktree(target: dict) -> Path:
    """The attempt tree of an attempt target, resolved again (after the Group lock) by the
    same helper. Any failure, or an attempt / input that is no longer the admitted one, is
    ``selfcheck_worktree_unavailable`` -- no group fallback."""
    from modules.flow_gate.services import graph_source_hooks
    details = {"attempt_id": target["attempt_id"]}
    try:
        scope = graph_source_hooks.attempt_scope(target["attempt_token"], project_id=target["project_id"],
                                                 group_id=target["group_id"])
    except graph_source_hooks.GraphSourceError as exc:
        raise SelfCheckError(409, "selfcheck_worktree_unavailable",
                             details={**details, "reason": exc.details.get("reason") or exc.code}) from exc
    if not scope or (scope["attempt_id"], scope["input_commit"]) != (target["attempt_id"], target["input_commit"]):
        raise SelfCheckError(409, "selfcheck_worktree_unavailable", details={**details, "reason": "attempt_changed"})
    try:
        path = Path(scope["root"]).resolve(strict=True)
        if not path.is_dir():
            raise OSError("not a directory")
        return path
    except OSError as exc:
        raise SelfCheckError(409, "selfcheck_worktree_unavailable",
                             details={**details, "reason": "worktree_missing"}) from exc


def _attempt_lock(ctx: lock_manager.ExecutionContext, project_id: str, attempt_id: str) -> str:
    """0110 T#3: the attempt's W -- the lock its source writes take (graph_run.workspace
    .mutation, W alone, no G) -- held for the run, so the tree under test is not written
    meanwhile. It follows the run's G: released with it once the tree is proved empty,
    protected with it while not (``_hold_unsettled``, ``_finish``), and freed by recovery /
    the reaper / re-verification with it (``lock_manager.selfcheck_release_recovered``).
    Stored as ``holder_kind='selfcheck'`` under the run's holder (``scr:<run_id>``), so a
    stale reclaim passes the Self-check gate (owner and tree proved), never the instance
    state alone."""
    from modules.flow_gate.services.git import merge_target
    branch = merge_target.GRAPH_ATTEMPT_BRANCH_PREFIX + merge_target.workspace_key(attempt_id)
    try:
        outcome = lock_manager.acquire("W", project_id, target_key=branch, holder_kind="selfcheck",
                                       mode="selfcheck_start", ctx=ctx)
    except lock_manager.LockProgramError as exc:
        raise SelfCheckError(500, "selfcheck_internal_error") from exc
    if not outcome.ok:
        raise SelfCheckError(409, "selfcheck_source_busy",
                             details={"attempt_id": attempt_id, **lock_manager.outcome_details(outcome)})
    return outcome.lock_key


def _worktree(doc: dict) -> Path:
    if doc.get("attempt_id"):
        return _attempt_worktree(doc)
    root, reason = git_service.effective_src_root_ex(doc["project_id"], doc["group_id"])
    if reason != "worktree" or not root:
        raise SelfCheckError(409, "selfcheck_worktree_unavailable")
    try:
        path = Path(root).resolve(strict=True)
        if not path.is_dir():
            raise OSError("not a directory")
        return path
    except OSError as exc:
        raise SelfCheckError(409, "selfcheck_worktree_unavailable") from exc


def available(doc_id: str) -> bool:
    try:
        doc = _document(doc_id)
        if not db_projects.tr_self_check_enabled(doc["project_id"]):
            return False
        _worktree(doc)
        return True
    except Exception:
        return False


def availability(doc_id: str) -> str:
    """Structured admission for a TR edit worker; ``available`` hides the reason, this keeps it.

    Returns one of: available, managed_worktree_missing, self_check_disabled,
    self_check_unavailable, recovery_incomplete.
    """
    try:
        doc = db_documents.get_by_id(doc_id)
        if not doc or str(doc.get("type_code") or "").upper() != "TR":
            return "self_check_unavailable"
        if not db_projects.tr_self_check_enabled(doc["project_id"]):
            return "self_check_disabled"
        try:
            _document(doc_id)
            _worktree(doc)
        except SelfCheckError:
            return "managed_worktree_missing"
        if db_runs.has_group_recovery_incomplete(doc["project_id"], doc["group_id"]):
            return "recovery_incomplete"
        return "available"
    except Exception:
        return "self_check_unavailable"


def is_canonical_run(run: dict) -> bool:
    """True for a TR edit run: Self-check is its only test/verification execution path."""
    if not run or run.get("action_scope") != "edit" or not run.get("doc_ref"):
        return False
    try:
        doc = db_documents.get_by_id(run["doc_ref"])
    except Exception:
        return False
    return bool(doc) and str(doc.get("type_code") or doc.get("type") or "").upper() == "TR"


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], shell=False, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=15, check=False)
    if result.returncode != 0:
        raise SelfCheckError(500, "selfcheck_state_probe_failed")
    return result.stdout


def _probe(root: Path) -> dict:
    head = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    branch_result = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"],
                                   shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if branch_result.returncode not in (0, 1):
        raise SelfCheckError(500, "selfcheck_state_probe_failed")
    branch = branch_result.stdout.decode("utf-8", "replace").strip() or None
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    index = _git(root, "diff", "--cached", "--binary", "--no-ext-diff")
    refs = _git(root, "for-each-ref", "--sort=refname", "--format=%(refname) %(objectname)")
    git_path = _git(root, "rev-parse", "--git-path", "index.lock").decode("utf-8", "replace").strip()
    lock_path = Path(git_path)
    if not lock_path.is_absolute():
        lock_path = root / lock_path
    digest = lambda data: hashlib.sha256(data).hexdigest()
    return {"head": head, "branch": branch, "status_hash": digest(status),
            "index_hash": digest(index), "refs_hash": digest(refs), "index_lock": lock_path.exists()}


def _source_fields(before: dict, after: dict) -> dict:
    return {"source_head_after": after["head"], "source_branch_after": after["branch"],
            "source_refs_hash_after": after["refs_hash"], "source_index_hash_after": after["index_hash"],
            "source_status_hash_after": after["status_hash"], "index_lock_after": int(after["index_lock"]),
            "source_changed_during_run": before["status_hash"] != after["status_hash"],
            "worktree_state_changed": any(before[k] != after[k] for k in ("head", "branch", "refs_hash", "index_hash", "index_lock"))}


def _validate_request(request: dict) -> tuple[str, list[str], str, int]:
    if not isinstance(request, dict) or set(request) - {"program", "args", "cwd", "timeout_seconds"}:
        raise SelfCheckError(422, "selfcheck_invalid_request")
    program = request.get("program")
    args = request.get("args", [])
    cwd = request.get("cwd", ".")
    timeout = request.get("timeout_seconds", 300)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 1800:
        raise SelfCheckError(422, "selfcheck_invalid_timeout")
    try:
        policy.validate_program(program)
        policy.validate_args(program, args)
    except policy.PolicyError as exc:
        raise SelfCheckError(422, exc.code) from exc
    return program, args, cwd, timeout


def _settle_tree(run_id: str, row: dict | None, ownership: dict) -> bool:
    """0080 T#1 (NR0007 §5-0 ②): the run's whole tree -- named Job, or target group plus run
    marker -- is proved empty, after a verified kill of what is still its own. The owning
    server is this one, so no server proof is needed. False = members remain or unseen.

    Without ``row`` (spawn() or mark_running failed) the stored row -- its owner_marker was
    written before the spawn -- plus what spawn learnt (``ownership``) is what is proved."""
    try:
        record = dict(row or db_runs.get_run(run_id) or {})
        if not record.get("owner_marker"):
            record["owner_marker"] = proof.owner_marker(run_id)
        record.update({key: value for key, value in ownership.items() if value is not None})
        record["self_check_run_id"] = run_id
        return proof.terminate_tree(record).state == proof.ABSENT
    except Exception:
        _log.warning("selfcheck %s: process tree check failed", run_id, exc_info=True)
        return False


def _cancel_poll_sec() -> float:
    try:
        value = float(os.environ.get("FLOWGATE_SELFCHECK_CANCEL_POLL_SEC") or 2.0)
    except ValueError:
        value = 2.0
    return max(0.2, min(30.0, value))


def _watch_cancel(run_id: str, control: executor.ProcessControl, cancelled: threading.Event) -> threading.Event:
    """0080 T#2 (NR0007 §5 item 3-b): while the worker waits for its child, poll the row's
    ``cancel_requested`` -- a cancel that reached another server, or this one without the
    control, still stops the run. Returns the stop event the worker sets when it is done."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(_cancel_poll_sec()):
            if cancelled.is_set():
                return
            try:
                requested = (db_runs.get_run(run_id) or {}).get("cancel_requested")
            except Exception:
                continue
            if requested and not stop.is_set():
                cancelled.set()
                try:
                    control.cancel()
                except Exception:
                    _log.warning("selfcheck %s: cancel from the watcher failed", run_id, exc_info=True)
                return

    threading.Thread(target=loop, daemon=True, name=f"selfcheck-cancel-watch-{run_id}").start()
    return stop


class _LockKeeper:
    """0080 T#3 (NR0007 §5 item 7): what a run does once its G heartbeat is declared lost.

    ``_Heartbeats._lost`` drops the hold from the heartbeat for good, so without this the run
    goes on with an expired, unprotected G. ``lost`` is the context's ``on_lock_lost``; it
    marks the hold LOSING and starts one retry loop (``lock_manager.selfcheck_regain``) that
    ends in one of:

    - HELD: the same epoch/holder row was still there and its heartbeat is renewed (a);
    - PROTECTED: the row is ours but could not be renewed, so it is marked protected -- no
      one reclaims or takes it; ``_finish`` deletes it once the tree is proved empty (b;
      ``release()`` never deletes a protected row);
    - GONE: no row of this epoch/holder -- another holder may be in the worktree. The run
      is cancelled; ``_finish`` records ``failed(selfcheck_lock_lost)`` once the tree is
      proved empty, else holds it like any unsettled run (c).

    The worker never decides on a loss the keeper is still judging: ``resolve`` closes the
    keeper (no later outcome can change the state it returns), waits for a judgement in
    flight and judges a loss not settled yet itself; one the store cannot settle is
    UNCONFIRMED and the run is held, not ended.

    It complements the reclaim gate (T#2) and replaces nothing of it: the gate keeps a
    live owner's G from being deleted, this shortens the time that G stays expired."""

    HELD, LOSING, PROTECTED, GONE, UNCONFIRMED = "held", "losing", "protected", "gone", "unconfirmed"
    _RESOLVE_ATTEMPTS = 3

    def __init__(self, run_id: str, ctx: lock_manager.ExecutionContext, lock_key: str):
        self.run_id, self.ctx, self.lock_key = run_id, ctx, lock_key
        self.state = self.HELD
        self._stop = threading.Event()
        self._guard = threading.Lock()
        self._judging = threading.Lock()       # one selfcheck_regain and its outcome at a time
        self._closed = False
        self._resolved = False
        self._thread: threading.Thread | None = None

    def lost(self, _ctx, _held) -> None:
        with self._guard:
            if self._closed:
                return
            if self.state == self.HELD:
                self.state = self.LOSING
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name=f"selfcheck-lock-keeper-{self.run_id}")
            self._thread.start()

    def _regain(self) -> str | None:
        try:
            return lock_manager.selfcheck_regain(self.ctx, self.lock_key)
        except Exception:
            _log.warning("selfcheck %s: regaining its Group lock failed", self.run_id, exc_info=True)
            return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._judging:
                if self._closed:
                    return
                outcome = self._regain()
                if outcome is not None:
                    self._settle(outcome, act=not self._closed)
                    return
            if self._stop.wait(lock_manager.heartbeat_interval_sec()):
                return

    def _settle(self, outcome: str, *, act: bool) -> None:
        """Record a judgement (under ``_judging``); ``act`` = the run still goes: cancel it on GONE."""
        with self._guard:
            if outcome == lock_manager.SELFCHECK_RENEWED:
                self.state = self.HELD
            elif outcome == lock_manager.SELFCHECK_PROTECTED:
                self.state = self.PROTECTED
            else:
                self.state = self.GONE
        if outcome == lock_manager.SELFCHECK_RENEWED:
            _log.warning("selfcheck %s: Group lock heartbeat renewed after it was lost", self.run_id)
            return
        if outcome == lock_manager.SELFCHECK_PROTECTED:
            _log.warning("selfcheck %s: Group lock could not be renewed; kept protected until the run ends",
                         self.run_id)
            return
        _log.error("selfcheck %s: Group lock is gone; the run is cancelled", self.run_id)
        if not act:
            return
        try:
            db_runs.request_cancel(self.run_id)
        except Exception:
            _log.warning("selfcheck %s: recording the cancel failed", self.run_id, exc_info=True)
        with _controls_lock:
            item = _controls.get(self.run_id)
        if item:
            item[1].set()
            item[0].cancel()

    def resolve(self) -> str:
        """The hold's final state, for the worker's terminal decision (after the tree proof).

        Closes the keeper first, so nothing it learns afterwards changes what the worker acts
        on; waits for a judgement in flight; then a loss not settled yet (LOSING, or
        ``ctx.lock_lost`` without a callback, e.g. the instance declared dead) is judged here,
        a few times. HELD / PROTECTED / GONE, or UNCONFIRMED when the store could not tell --
        the caller then holds the run. Idempotent."""
        with self._guard:
            self._closed = True
        self._stop.set()
        with self._judging:
            if not self._resolved:
                self._resolved = True
                with self._guard:
                    unsettled = self.state == self.LOSING or (self.state == self.HELD and self.ctx.lock_lost)
                if unsettled:
                    outcome = None
                    for attempt in range(self._RESOLVE_ATTEMPTS):
                        outcome = self._regain()
                        if outcome is not None:
                            break
                        if attempt + 1 < self._RESOLVE_ATTEMPTS:
                            time.sleep(min(0.5, lock_manager.heartbeat_interval_sec()))
                    if outcome is None:
                        with self._guard:
                            self.state = self.UNCONFIRMED
                        _log.error("selfcheck %s: whether its Group lock is still held cannot be read; "
                                   "the run is held", self.run_id)
                    else:
                        self._settle(outcome, act=False)
            with self._guard:
                return self.state

    def stop(self) -> None:
        with self._guard:
            self._closed = True
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)


def _lock_state(keeper: "_LockKeeper | None") -> str:
    return keeper.resolve() if keeper is not None else _LockKeeper.HELD


_OWNERSHIP_KEYS = ("process_owner_kind", "target_pid", "target_start_identity",
                   "supervisor_pid", "supervisor_start_identity")


def _store_ownership(run_id: str, ownership: dict) -> None:
    fields = {key: ownership[key] for key in _OWNERSHIP_KEYS if ownership.get(key) is not None}
    if fields and not db_runs.record_spawn_ownership(run_id, **fields):
        raise SelfCheckError(500, "selfcheck_state_conflict")


def _record_spawn(run_id: str):
    """``executor.spawn``'s ``record`` (0080 T#3): the pre-spawn stamp, then each process as
    soon as it exists, so the stored row never proves more absent than the spawn knows."""
    def record(stage: str, ownership: dict) -> None:
        if stage == "spawning":
            if not db_runs.mark_spawn_started(run_id, ownership["process_owner_kind"]):
                raise SelfCheckError(500, "selfcheck_state_conflict")
            return
        _store_ownership(run_id, ownership)

    return record


#: recovery_reason of a run whose G loss could not be judged before its terminal write.
LOCK_UNCONFIRMED = "lock_unconfirmed"


def _hold_unsettled(ctx: lock_manager.ExecutionContext, lock_key: str, run_id: str,
                    ownership: dict | None = None, reason: str = proof.TREE_UNPROVEN,
                    attempt_lock: str | None = None) -> None:
    """No finish_* and no release while the tree is not proved empty (L 2.24.2: the Group's
    G stays as a protected row; only this Group is blocked). What the spawn learnt is
    stored first: a later reaper proves the tree from the stored row only.

    0110 T#3: a Graph attempt run's W (``attempt_lock``) is protected the same way -- the
    attempt's source writes take that W alone, so a protected G does not keep them out of a
    tree the run's processes may still be in. Recovery, the reaper and re-verification free
    both once the tree is proved empty (``lock_manager.selfcheck_release_recovered``)."""
    if ownership:
        try:
            _store_ownership(run_id, ownership)
        except Exception:
            _log.warning("selfcheck %s: storing the spawn's process records failed", run_id, exc_info=True)
    for key in (attempt_lock, lock_key):
        if key is not None and ctx.find_held(key) is not None:
            lock_manager.protect(ctx, key, "selfcheck_reap_unconfirmed")
    recovering = db_runs.mark_recovering(run_id)
    if recovering:
        db_runs.mark_recovery_incomplete(run_id, reason)


def _finish(run_id: str, ctx: lock_manager.ExecutionContext, lock_key: str, root: Path, before: dict,
            runtime: Path, command: policy.ResolvedCommand, args: list[str], cwd: Path,
            env: dict[str, str], timeout: int, keeper: "_LockKeeper | None" = None,
            attempt_lock: str | None = None, spawned: threading.Event | None = None) -> None:
    """``spawned`` (0110 T#3, a Graph attempt run): set once the spawn is over -- the child
    exists and the run is marked running, or the spawn failed. ``_start`` keeps the Stop
    gate's shared hold until then."""
    holder = ctx.ctx_id
    control = None
    ownership = {}
    row = None
    cancelled = threading.Event()
    terminal = None
    watch = None
    try:
        try:
            control, ownership = executor.spawn([command.executable, *command.argv_prefix, *args], cwd, env, timeout,
                                                run_id, job_name=proof.job_name(run_id), record=_record_spawn(run_id))
            with _controls_lock:
                _controls[run_id] = control, cancelled
            row = db_runs.mark_running(run_id, source_lock_holder=holder,
                source_head_before=before["head"], source_branch_before=before["branch"],
                source_refs_hash_before=before["refs_hash"], source_index_hash_before=before["index_hash"],
                source_status_hash_before=before["status_hash"], index_lock_before=int(before["index_lock"]), **ownership)
            if row is None:
                raise SelfCheckError(500, "selfcheck_state_conflict")
        finally:
            if spawned is not None:
                spawned.set()
        _emit(row)
        if db_runs.get_run(run_id)["cancel_requested"] or (keeper is not None and keeper.state == _LockKeeper.GONE):
            cancelled.set()
            control.cancel()
        watch = _watch_cancel(run_id, control, cancelled)
        result = executor.wait(control, timeout, cancelled)
        if not _settle_tree(run_id, row, ownership):
            _hold_unsettled(ctx, lock_key, run_id, ownership, attempt_lock=attempt_lock)
            return
        # The tree is empty. A G loss is judged now, and nothing the keeper learns later
        # changes the decision below.
        lock = _lock_state(keeper)
        if lock == _LockKeeper.UNCONFIRMED:
            _hold_unsettled(ctx, lock_key, run_id, ownership, LOCK_UNCONFIRMED, attempt_lock=attempt_lock)
            return
        after = _probe(root)
        tails = {"stdout_tail": _redact(result.stdout_tail), "stderr_tail": _redact(result.stderr_tail)}
        fields = _source_fields(before, after)
        if lock == _LockKeeper.GONE:
            # G was gone under the run (NR0007 §5 item 7-c): another holder may have entered.
            # Stopped and its tree proved empty above; recorded as a failure, not a result.
            terminal = db_runs.finish_failed(run_id, error_code="selfcheck_lock_lost", exit_code=result.exit_code,
                                             timed_out=result.timed_out, **tails, **fields)
        elif result.cancelled or db_runs.get_run(run_id)["cancel_requested"]:
            terminal = db_runs.finish_cancelled(run_id, exit_code=result.exit_code, **tails, **fields)
        else:
            terminal = db_runs.finish_completed(run_id, exit_code=result.exit_code,
                                                timed_out=result.timed_out, **tails, **fields)
            if terminal is not None and not result.timed_out:
                _record_result_summary(run_id, terminal)
    except Exception as exc:
        if control is not None:
            control.cancel()
            try:
                control.proc.wait(timeout=5)
            except Exception:
                pass
        else:
            # spawn() failed. That proves nothing absent: a supervisor, target or Job member
            # may exist (a failed handshake, a cleanup wait that timed out). What spawn learnt
            # comes with the error; the stored row (owner_marker, the spawn's records) covers
            # the rest. Only a spawn that knows it created nothing takes its stamp back.
            ownership = dict(getattr(exc, "ownership", None) or {})
            if getattr(exc, "created", True) is False:
                ownership = {}
                try:
                    db_runs.clear_spawn_started(run_id)
                except Exception:
                    _log.warning("selfcheck %s: clearing the spawn stamp failed", run_id, exc_info=True)
        # The target and the supervisor are not the whole run: its group / Job must be
        # empty too, else the failure is not recorded, the run keeps its active_key and G
        # stays protected (recovery_incomplete / tree_unproven).
        if (control is not None and control.proc.poll() is None) or not _settle_tree(run_id, row, ownership):
            _hold_unsettled(ctx, lock_key, run_id, ownership, attempt_lock=attempt_lock)
            return
        # A confirmed G loss outranks the error, with the same code as the normal path.
        lock = _lock_state(keeper)
        if lock == _LockKeeper.UNCONFIRMED:
            _hold_unsettled(ctx, lock_key, run_id, ownership, LOCK_UNCONFIRMED, attempt_lock=attempt_lock)
            return
        if lock == _LockKeeper.GONE:
            code = "selfcheck_lock_lost"
        else:
            code = exc.code if isinstance(exc, (SelfCheckError, policy.PolicyError)) else (
                "selfcheck_process_owner_unavailable" if isinstance(exc, executor.OwnershipError) else "selfcheck_internal_error")
        terminal = db_runs.finish_failed(run_id, error_code=code)
    finally:
        if watch is not None:
            watch.set()
        if keeper is not None:
            keeper.stop()
        if control is not None:
            control.close()
        with _controls_lock:
            _controls.pop(run_id, None)
        if ctx.lock_lost:
            _log.warning("selfcheck %s lost its Group lock during the run", run_id)
        # A missing terminal commit keeps the Group lock (protected) for startup recovery.
        # 0110 T#3: an attempt run's W follows its G -- released only with a terminal write
        # (made after the tree proof), else protected with it; an unsettled run's W was
        # protected by _hold_unsettled already.
        if terminal is not None:
            if attempt_lock is not None and ctx.find_held(attempt_lock) is not None:
                lock_manager.release(ctx, attempt_lock)
            if ctx.find_held(lock_key) is not None:
                lock_manager.release(ctx, lock_key)
            if keeper is not None and keeper.state == _LockKeeper.PROTECTED:
                # release() never deletes a protected row (L 2.5.4). The keeper protected this
                # run's own G after its heartbeat could not be renewed; the tree was proved
                # empty before the terminal write, so the holder's row goes now.
                try:
                    lock_manager.selfcheck_release_recovered(terminal["project_id"], terminal["group_id"], run_id)
                except Exception:
                    _log.warning("selfcheck %s: releasing its protected Group lock failed", run_id, exc_info=True)
            _emit(terminal)
            _cleanup_runtime(runtime, run_id)
        else:
            for key in (attempt_lock, lock_key):
                if key is not None and ctx.find_held(key) is not None:
                    lock_manager.protect(ctx, key, "selfcheck_terminal_unrecorded")


def _record_result_summary(run_id: str, terminal: dict) -> None:
    """0114 T#3 (D 3.2): summarize the result block of a run that completed normally, from the
    tail already stored with its terminal record. The terminal record is written first; a
    summary that cannot be made or stored is logged and never touches it."""
    try:
        summary, environment = result_block.summarize(terminal.get("stdout_tail"), terminal.get("exit_code"))
        db_runs.record_result_summary(run_id, result_block.dumps(summary), result_block.dumps(environment))
    except Exception:
        _log.warning("selfcheck %s: result block summary not recorded", run_id, exc_info=True)


def start(doc_id: str, request: dict, requested_by: str | None = None, token: dict | None = None) -> dict:
    """``token``: the caller's verified token (0110 T#3) -- a Graph attempt token's run executes
    in its attempt tree; without one, or for an unmapped token, the group worktree."""
    doc = _document(doc_id)
    target = {"project_id": doc["project_id"], "group_id": doc["group_id"], "tr_doc_id": doc_id,
              "caller_token": token}
    return _start(target, request, requested_by)


def start_draft(token: dict, request: dict) -> dict:
    """Run Self-check for a TR that is not registered yet; the TR(new) token owns the run."""
    target = {**draft_target(token), "caller_token": token}
    return _start(target, request, token.get("issued_to"))


def _start(target: dict, request: dict, requested_by: str | None) -> dict:
    """One admission/execution path for TR-bound and draft runs (same policy, worktree, G lock).

    0110 T#3: a Graph attempt token's run (``_attempt_target``) runs in its attempt tree and
    also holds the attempt's W. Its writability (``require_writable``) is judged under the
    Stop gate's shared hold inside a ``write_session``, and that hold is kept -- through the
    Group's G and the attempt's W (the gate, then the Git locks), the run row and the
    worker's spawn -- until the spawn is over (``_await_spawn``). A Stop's ``stopping`` or
    the runtime's invalidation of the attempt (both take the gate exclusively) therefore
    commits either before the judgement, which refuses with nothing created, locked or
    spawned, or after the child exists. The token judged is the caller's (``caller_token``,
    set by ``start`` / ``start_draft``); a target that already carries an attempt is judged
    again with its token. An unmapped token holds nothing and does not wait."""
    from modules.flow_gate.services import graph_source_hooks
    token = target.get("caller_token") or target.get("attempt_token")
    with graph_source_hooks.write_session():
        judged = _attempt_target(token, target["project_id"], target["group_id"])
        if target.get("attempt_id") and judged.get("attempt_id") != target["attempt_id"]:
            raise SelfCheckError(409, "selfcheck_worktree_unavailable",
                                 details={"attempt_id": target["attempt_id"], "reason": "attempt_changed"})
        return _admit({**target, **judged}, request, requested_by)


def _await_spawn(worker: threading.Thread, spawned: threading.Event) -> None:
    """0110 T#3: the request thread holds the Stop gate until the worker's spawn is over
    (``_finish`` sets ``spawned``), or the worker is gone."""
    while not spawned.wait(0.2):
        if not worker.is_alive():
            return


def _admit(target: dict, request: dict, requested_by: str | None) -> dict:
    """``_start`` past the attempt judgement. The active-run slot stays one per Group: the
    run holds its Group's G for its whole length in either case, so a second run of the same
    Group -- another attempt's too -- could not start meanwhile anyway, and recovery (G
    protection, recovery_incomplete) is judged per Group."""
    project_id, group_id = target["project_id"], target["group_id"]
    if not db_projects.tr_self_check_enabled(project_id):
        raise SelfCheckError(403, "selfcheck_disabled")
    if db_runs.has_group_recovery_incomplete(project_id, group_id):
        raise SelfCheckError(409, "selfcheck_recovery_incomplete")
    program, args, relative_cwd, timeout = _validate_request(request)
    first_root = _worktree(target)
    path_value, _ = policy.controlled_path(first_root, _HOST_PATH)
    try:
        command = policy.resolve_command(program, args, first_root, path_value)
        policy.validate_cwd(first_root, relative_cwd)
    except policy.PolicyError as exc:
        raise SelfCheckError(422, exc.code) from exc
    existing = db_runs.get_active(project_id, group_id)
    if existing:
        raise _already_running(existing["self_check_run_id"], target)
    run_id = f"scr_{uuid.uuid4().hex[:24]}"
    # D 3.8 / L 2.24.1: this Group's G for the whole run (long, heartbeated).
    ctx = lock_manager.new_context("scr", run_id)
    holder = ctx.ctx_id
    keeper = _LockKeeper(run_id, ctx, lock_manager.lock_key("G", project_id, group_id))
    ctx.on_lock_lost = keeper.lost
    try:
        outcome = lock_manager.acquire("G", project_id, group_id=group_id, holder_kind="selfcheck",
                                       mode="selfcheck_start", ctx=ctx)
    except lock_manager.LockProgramError as exc:
        raise SelfCheckError(500, "selfcheck_internal_error") from exc
    if not outcome.ok:
        code = ("selfcheck_recovery_incomplete" if outcome.kind == lock_manager.RECOVERY_REQUIRED
                else "selfcheck_source_busy")
        raise SelfCheckError(409, code, details=lock_manager.outcome_details(outcome))
    lock_key = outcome.lock_key
    row = None
    runtime = None
    attempt_lock = None
    try:
        if target.get("attempt_id"):
            attempt_lock = _attempt_lock(ctx, project_id, target["attempt_id"])
        root = _worktree(target)
        if root != first_root:
            path_value, _ = policy.controlled_path(root, _HOST_PATH)
            command = policy.resolve_command(program, args, root, path_value)
        cwd = policy.validate_cwd(root, relative_cwd)
        existing = db_runs.get_active(project_id, group_id)
        if existing:
            raise _already_running(existing["self_check_run_id"], target)
        before = _probe(root)
        runtime = Path(tempfile.mkdtemp(prefix=f"flowgate-{run_id}-"))
        env = policy.scrubbed_env(path_value, runtime)
        # 0080 T#1: every process of the run inherits the run marker (Linux marker scan).
        env[proof.MARKER_ENV] = run_id
        from modules.flow_gate.services.git import instance_registry
        # Owner instance + run-unit ownership record land before the spawn (NR0007 §5 item 4).
        row = db_runs.create_pending(project_id, group_id, target["tr_doc_id"], requested_by,
            policy.POLICY_VERSION, program, args, command.executable, command.name,
            command.origin, relative_cwd, timeout, list(env), run_id=run_id, source_lock_holder=holder,
            owner_token_id=target.get("owner_token_id"), draft_doc_ref=target.get("draft_doc_ref"),
            owner_instance_id=instance_registry.current_instance_id(), owner_marker=proof.owner_marker(run_id),
            attempt_id=target.get("attempt_id"), input_commit=target.get("input_commit"),
            host_node_key=instance_registry.node_key())
        spawned = threading.Event() if attempt_lock is not None else None
        worker = threading.Thread(target=_finish, args=(run_id, ctx, lock_key, root, before,
            runtime, command, args, cwd, env, timeout, keeper, attempt_lock, spawned), daemon=True,
            name=f"selfcheck-{run_id}")
        try:
            worker.start()
        except Exception as exc:
            failed = db_runs.finish_failed(run_id, error_code="selfcheck_internal_error")
            if failed is not None:
                if attempt_lock is not None:
                    lock_manager.release(ctx, attempt_lock)
                lock_manager.release(ctx, lock_key)
                _emit(failed)
                _cleanup_runtime(runtime, run_id)
            raise SelfCheckError(500, "selfcheck_internal_error") from exc
        if spawned is not None:
            _await_spawn(worker, spawned)
        _emit(row)
        return public(row)
    except db_runs.SelfCheckAlreadyRunningError as exc:
        raise _already_running(exc.existing_run_id, target) from exc
    except db_runs.DraftOwnerClosedError as exc:
        # The TR registered (or the token was revoked) after draft_target admitted it.
        raise SelfCheckError(403, "selfcheck_forbidden", "draft owner token is no longer open") from exc
    except policy.PolicyError as exc:
        raise SelfCheckError(422, exc.code) from exc
    finally:
        if row is None:
            keeper.stop()
            if attempt_lock is not None:
                lock_manager.release(ctx, attempt_lock)
            lock_manager.release(ctx, lock_key)
            if runtime is not None:
                _cleanup_runtime(runtime, run_id, tracked=False)


def _with_baseline(row: dict, baseline_run_id: str | None) -> dict:
    """0114 T#3 (D 5): ``public(row)`` plus, when a baseline run id is named, the baseline
    judgement against it. Read-only and advisory; a judgement that fails is reported as not
    reusable, never as a pass."""
    result = public(row)
    if baseline_run_id is None:
        return result
    from modules.flow_gate.services import tr_self_check_baseline as baseline
    try:
        result["baseline_comparison"] = baseline.compare(row, baseline_run_id)
    except Exception:
        _log.warning("selfcheck %s: baseline comparison failed", row.get("self_check_run_id"), exc_info=True)
        result["baseline_comparison"] = {"baseline_run_id": baseline_run_id, "run_id": row.get("self_check_run_id"),
                                         "reusable": False, "reasons": [{"code": "comparison_failed"}],
                                         "comparison": None, "anchor": None, "advisory": True,
                                         "note": baseline.ADVISORY_NOTE}
    return result


def read(doc_id: str, run_id: str, baseline_run_id: str | None = None) -> dict:
    _document(doc_id)
    row = db_runs.get_run_for_doc(run_id, doc_id)
    if row is None:
        raise SelfCheckError(404, "selfcheck_run_not_found")
    return _with_baseline(row, baseline_run_id)


def list_runs(doc_id: str, limit: int = 20) -> list[dict]:
    _document(doc_id)
    return [public(row) for row in db_runs.list_by_doc(doc_id, limit=min(max(1, limit), 100))]


def cancel(doc_id: str, run_id: str) -> dict:
    return _cancel(run_id, lambda: db_runs.get_run_for_doc(run_id, doc_id))


def _cancel(run_id: str, lookup) -> dict:
    """Request a stop and say what is known of it (0080 T#2, NR0007 §5 item 3).

    The response's ``cancel`` tells the request being recorded (``accepted``) apart from the
    run having stopped (``stopped``); ``state`` is requested / stopped / terminate_failed /
    not_active. A run this process drives is signalled through its control (its worker ends
    it). A run no control here drives goes through ``_cancel_unowned``; when that proves
    nothing the answer is 409 ``selfcheck_cancel_unconfirmed`` and the run keeps its state.
    """
    row = lookup()
    if row is None:
        raise SelfCheckError(404, "selfcheck_run_not_found")
    active = row["status"] in _ACTIVE
    db_runs.request_cancel(run_id)
    with _controls_lock:
        item = _controls.get(run_id)
    state, reason = ("requested" if active else "not_active"), None
    if item:
        item[1].set()
        if not item[0].cancel():
            state, reason = "terminate_failed", "process_terminate_failed"
    elif active:
        try:
            state, reason = _cancel_unowned(row)
        except Exception:
            _log.warning("selfcheck %s: cancel check failed; the run is kept", run_id, exc_info=True)
            state, reason = "unconfirmed", "check_failed"
            try:
                current = db_runs.get_run(run_id)
                # The row was not in recovery when read above: a recovering row now is this
                # call's CAS, so it is held like recover() holds a failed row.
                if current and current.get("recovery_state") == "recovering":
                    _hold_recovery_incomplete(current, "recovery_error", set())
            except Exception:
                _log.warning("selfcheck %s: keeping the run protected failed", run_id, exc_info=True)
    # A draft run may be linked to its TR between the two reads; fall back to the id.
    row = lookup() or db_runs.get_run(run_id)
    _emit(row)
    stopped = row["status"] not in _ACTIVE
    if stopped and active:
        state, reason = "stopped", None
    cancel = {"accepted": bool(row.get("cancel_requested")), "stopped": stopped, "state": state, "reason": reason}
    if state == "unconfirmed":
        details = {"self_check_run_id": run_id, "cancel": cancel, "run": summary(row)}
        if _REASON_CODES.get(reason):
            details["reason_code"] = _REASON_CODES[reason]
        raise SelfCheckError(409, "selfcheck_cancel_unconfirmed", details=details)
    return {**public(row), "cancel": cancel}


def _worker_alive(run_id: str) -> bool:
    return any(t.name == f"selfcheck-{run_id}" and t.is_alive() for t in threading.enumerate())


def _cancel_unowned(row: dict) -> tuple[str, str | None]:
    """Cancel of an active run with no control in this process (NR0007 §5 item 3-a, §5-0).

    Its worker may run in another server, or nowhere. The run is ended (cancelled, G freed)
    only on the release rule: the owning server proved dead -- or it is this server and its
    worker is proved gone, so nothing can spawn for the run any more -- then a verified kill
    of what is still the run's own and the whole tree proved empty, re-checked after the
    recovering CAS. An owner that is alive elsewhere is left to its worker, which polls
    ``cancel_requested``. Returns ``(state, reason)``; ``unconfirmed`` changes neither the
    run's state nor its protection, except that a run already fenced by the CAS keeps G
    protected as recovery_incomplete (the same hold as ``recover()``)."""
    rid = row["self_check_run_id"]
    if row.get("recovery_state") != "none":
        return "unconfirmed", f"recovery_{row.get('recovery_state')}"
    server = proof.owner_server_state(proof.owner_instance_of(row))
    if server.state == proof.MATCH and not server.detail.get("self"):
        return "requested", proof.OWNER_ALIVE
    if server.state == proof.MATCH:
        # This server owns the run. A pending run's worker may still be about to spawn; a
        # live worker reads the flag itself.
        if row["status"] == "pending" or _worker_alive(rid):
            return "requested", None
        if proof.terminate_tree(row).state != proof.ABSENT:
            return "unconfirmed", proof.TREE_UNPROVEN
        current = db_runs.mark_recovering(rid)
        if current is None:
            return "unconfirmed", "state_changed"
        if proof.tree_state(current).state != proof.ABSENT:
            _hold_recovery_incomplete(current, proof.TREE_UNPROVEN, set())
            return "unconfirmed", proof.TREE_UNPROVEN
    elif not server.gone:
        return "unconfirmed", server.reason or proof.OWNER_SERVER_UNPROVEN
    else:
        current = db_runs.mark_recovering(rid)
        if current is None:
            return "unconfirmed", "state_changed"
        verdict = proof.release_verdict(current, terminate=True)
        if verdict.decision != proof.RELEASE:
            _hold_recovery_incomplete(current, verdict.reason or proof.TREE_UNPROVEN, set())
            return "unconfirmed", verdict.reason or proof.TREE_UNPROVEN
    finished = db_runs.finish_recovered_cancelled(rid)
    if finished is None:
        return "unconfirmed", "state_changed"
    try:
        lock_manager.selfcheck_release_recovered(row["project_id"], row["group_id"], rid)
    except Exception:
        _log.warning("selfcheck %s: releasing Group lock after cancel failed", rid, exc_info=True)
    return "stopped", None


def read_draft(token: dict, run_id: str, baseline_run_id: str | None = None) -> dict:
    target = draft_target(token)
    row = db_runs.get_run_for_owner(run_id, target["owner_token_id"])
    if row is None:
        raise SelfCheckError(404, "selfcheck_run_not_found")
    return _with_baseline(row, baseline_run_id)


def list_draft_runs(token: dict, limit: int = 20) -> list[dict]:
    target = draft_target(token)
    return [public(row) for row in db_runs.list_by_owner(target["owner_token_id"], limit=min(max(1, limit), 100))]


def cancel_draft(token: dict, run_id: str) -> dict:
    target = draft_target(token)
    return _cancel(run_id, lambda: db_runs.get_run_for_owner(run_id, target["owner_token_id"]))


def group_draft_run(run_id: str) -> dict | None:
    """A still-unlinked draft run by id, for a console user (the route checks permission)."""
    row = db_runs.get_run(run_id)
    return row if row and row.get("tr_doc_id") is None else None


def list_group_draft_runs(group_id: str, limit: int = 20) -> tuple[str, list[dict]]:
    """``(project_id, runs)``: a group's unlinked draft runs, for a console user."""
    group = db_groups.get_by_id(group_id)
    if not group:
        raise SelfCheckError(404, "selfcheck_draft_group_not_found")
    project_id = group["project_id"]
    rows = db_runs.list_drafts_by_group(project_id, group_id, limit=min(max(1, limit), 100))
    return project_id, [public(row) for row in rows]


def cancel_group_draft(run_id: str) -> dict:
    return _cancel(run_id, lambda: group_draft_run(run_id))


def group_run(run_id: str) -> dict | None:
    """Any run by id, for a reader of its Group (the route checks the Group, 0080 T#2)."""
    return db_runs.get_run(run_id)


def group_active_runs(project_id: str, group_id: str) -> list[dict]:
    """The run holding the Group's slot, as a summary list (empty or one)."""
    row = db_runs.get_active(project_id, group_id)
    return [summary(row)] if row else []


def group_project(group_id: str) -> str:
    group = db_groups.get_by_id(group_id)
    if not group:
        raise SelfCheckError(404, "selfcheck_draft_group_not_found")
    return group["project_id"]


def link_draft_runs(token: dict, tr_doc_id: str | None) -> int:
    """Attach a TR(new) token's draft runs to the TR that token just registered.

    Called from token consumption, the one point every registration path (inbox new, the
    API provider's register_document) passes with the created doc id. Anything that is not
    a TR of the token's own project/group links nothing. Returns the number of linked runs.
    """
    token_id = token.get("token_id")
    if token.get("action_scope") != "new" or not token_id or not tr_doc_id:
        return 0
    doc = db_documents.get_by_id(tr_doc_id)
    if (not doc or str(doc.get("type_code") or "").upper() != "TR"
            or doc.get("project_id") != token.get("project") or doc.get("group_id") != token.get("group_id")):
        return 0
    linked = db_runs.link_owner_runs(token_id, tr_doc_id, doc["project_id"], doc["group_id"])
    if linked:
        for row in db_runs.list_by_doc(tr_doc_id, limit=100):
            if row.get("owner_token_id") == token_id:
                _emit(row)
    return linked


def recover() -> set[str]:
    """Recover orphan runs before Git session recovery (L 2.24.3, 2.26 step 5).

    Protection is per (project, group) G row now: a recovered run releases its Group's
    G, an unproven one leaves it protected. Returns the projects whose protection could
    not be written onto G; their run stays recovery_incomplete, which alone keeps the
    Group's G closed (``lock_manager.group_recovery_check``).

    0080 T#1 (NR0007 §5-0, §5 item 4): a run is ended (failed/interrupted_by_restart) and its
    G deleted only when its owning server is proved dead and THEN its whole process tree
    (target, supervisor, group / named Job, run marker) is proved empty --
    ``tr_self_check_proof.release_verdict``. A run whose owner is alive or unproven (MATCH,
    UNKNOWN, another node, no instance row) is not touched at all: marking it recovering
    would fence a live owner's own worker. A run with no recorded owner, or whose tree is
    not proved empty, stays recovery_incomplete with G protected and the reason recorded
    (ownership_unknown / owner_server_unproven / tree_unproven). Rows recover one by one; a
    failing row never stops the others.
    """
    fallback: set[str] = set()
    for row in db_runs.list_orphan_active_runs():
        try:
            _recover_row(row, fallback)
        except Exception:
            rid = row.get("self_check_run_id")
            _log.warning("selfcheck %s: recovery failed; the run is kept as it is", rid, exc_info=True)
            try:
                current = db_runs.get_run(rid)
                # Only a row this pass already took into recovery is protected here; one whose
                # owner was never judged dead is left exactly as it was.
                if current and current.get("recovery_state") == "recovering":
                    _hold_recovery_incomplete(current, "recovery_error", fallback)
            except Exception:
                _log.warning("selfcheck %s: keeping the run protected failed", rid, exc_info=True)
    return fallback


def _recover_row(row: dict, fallback: set[str]) -> None:
    rid = row["self_check_run_id"]
    server = proof.owner_server_state(proof.owner_instance_of(row))
    if not server.gone and server.reason != proof.OWNERSHIP_UNKNOWN:
        _log.info("selfcheck %s: owner server %s (%s); not recovered here", rid, server.state,
                  server.reason or proof.OWNER_ALIVE)
        return
    state = row["recovery_state"]
    if state == "none":
        current = db_runs.mark_recovering(rid)
    elif state == "incomplete":
        # Re-verification of a protected run must pass incomplete -> recovering first.
        current = db_runs.retry_recovery_incomplete(rid)
    else:
        current = row
    if current is None:
        return
    if not server.gone:
        # Nothing records which server owns the run, so its death can never be proved.
        _hold_recovery_incomplete(current, proof.OWNERSHIP_UNKNOWN, fallback)
        return
    # The CAS above fences the dead owner's worker (mark_running needs recovery_state='none');
    # release_verdict re-proves the server, kills only a verified own tree, re-checks both.
    verdict = proof.release_verdict(current, terminate=True)
    if verdict.decision == proof.RELEASE:
        finished = db_runs.finish_recovered_interrupted(rid)
        if finished:
            try:
                lock_manager.selfcheck_release_recovered(row["project_id"], row["group_id"], rid)
            except Exception:
                _log.warning("selfcheck %s: releasing Group lock failed", rid, exc_info=True)
            _emit(finished)
            return
    _hold_recovery_incomplete(current, verdict.reason or proof.TREE_UNPROVEN, fallback)


def _hold_recovery_incomplete(row: dict, reason: str, fallback: set[str]) -> None:
    rid, project_id, group_id = row["self_check_run_id"], row["project_id"], row["group_id"]
    incomplete = db_runs.mark_recovery_incomplete(rid, reason)
    if incomplete:
        _emit(incomplete)
    if not lock_manager.selfcheck_ensure_protected(project_id, group_id, rid):
        _log.warning("selfcheck %s: G protection not written; recovery_incomplete keeps "
                     "the Group closed", rid)
        fallback.add(project_id)


# ── 0080 T#3: periodic reaper and operator re-verification (NR0007 §5 items 5, 6) ──

RELEASED, PROTECTED, KEPT = "released", "protected", "kept"


def _reprove(row: dict) -> tuple[str, str | None]:
    """One judgement of an active run that no worker drives: ``(outcome, reason)``.

    RELEASED: the run is ended (failed / interrupted_by_restart) and its G deleted in the
    same pass. PROTECTED: fenced but not proved -- recovery_incomplete, G protected.
    KEPT: nothing written (``reason`` says why).

    The order is ``_recover_row``'s. The owner is judged first, read only, by the four-way
    proof: another server alive (MATCH) or unproven (UNKNOWN, another node, no instance
    row, no owner recorded) keeps the run as it is. Instance DEAD, ``status='dead'`` and
    heartbeat expiry (``step_instance_liveness`` / ``is_stale``) are not read. This server
    as the owner counts only when the run's worker thread is gone and the run is past its
    fresh pending state: then nothing can spawn for it any more. Only then the CAS fences the
    row (none / incomplete -> recovering; a ``recovering`` row left behind is proved under
    its fence, as the gate does) and the release rule proves the tree, killing only what is
    verified the run's own, re-checked right before the writes.
    """
    rid, state = row["self_check_run_id"], row.get("recovery_state")
    if row.get("status") not in _ACTIVE or state not in ("none", "incomplete", "recovering"):
        return KEPT, "not_applicable"
    if state == "recovering" and not gate._left_behind(row):
        return KEPT, "recovery_recovering"     # recover(), a cancel or a gate pass is proving it
    server = proof.owner_server_state(proof.owner_instance_of(row))
    own = server.state == proof.MATCH and bool(server.detail.get("self"))
    if own:
        if _worker_alive(rid) or (row["status"] == "pending" and state == "none"):
            return KEPT, proof.OWNER_ALIVE
    elif server.state == proof.MATCH:
        return KEPT, proof.OWNER_ALIVE
    elif not server.gone:
        return KEPT, server.reason or proof.OWNER_SERVER_UNPROVEN
    if state == "none":
        current = db_runs.mark_recovering(rid)
    elif state == "incomplete":
        current = db_runs.retry_recovery_incomplete(rid)
    else:
        current = db_runs.get_run(rid)
        if current is None or current.get("recovery_state") != "recovering":
            current = None
    if current is None:
        return KEPT, "state_changed"
    try:
        if own:
            ok = (proof.terminate_tree(current).state == proof.ABSENT
                  and proof.tree_state(current).state == proof.ABSENT)
            reason = None if ok else proof.TREE_UNPROVEN
        else:
            verdict = proof.release_verdict(current, terminate=True)
            ok, reason = verdict.decision == proof.RELEASE, verdict.reason or proof.TREE_UNPROVEN
        if ok:
            finished = db_runs.finish_recovered_interrupted(rid)
            if finished is None:
                return KEPT, "state_changed"
            try:
                lock_manager.selfcheck_release_recovered(row["project_id"], row["group_id"], rid)
            except Exception:
                # The run is ended; its leftover G is a terminal run's, reclaimed by the gate.
                _log.warning("selfcheck %s: releasing Group lock failed", rid, exc_info=True)
            _emit(finished)
            return RELEASED, None
        _hold_recovery_incomplete(current, reason, set())
        return PROTECTED, reason
    except Exception:
        _log.warning("selfcheck %s: re-proof failed; the run is kept protected", rid, exc_info=True)
        fenced = db_runs.get_run(rid)
        if fenced and fenced.get("recovery_state") == "recovering":
            _hold_recovery_incomplete(fenced, "recovery_error", set())
        return PROTECTED, "recovery_error"


def _reap_terminal_g(lock: dict) -> bool:
    """A protected Self-check G whose run has ended (the gate protected it while the tree
    was not proved empty): deleted once the tree is proved empty. An ended run spawns
    nothing more, so no server proof is needed (gate table, terminal rows). 0110 T#3: a
    protected attempt W of such a run is reached the same way and freed with its G."""
    rid = gate.run_id_of(lock)
    run = db_runs.get_run(rid) if rid else None
    if run is None or run["status"] in _ACTIVE or proof.tree_state(run).state != proof.ABSENT:
        return False
    lock_manager.selfcheck_release_recovered(run["project_id"], run["group_id"], rid)
    _log.warning("selfcheck %s: ended run's protected G released, tree proved empty", rid)
    return True


def reap() -> int:
    """The sweeper's Self-check reaper step (0080 T#3, NR0007 §5 item 5). Returns the
    number of runs ended / G rows freed.

    Every active run is judged by ``_reprove`` -- a run whose owner is proved dead (or is
    this server with no worker left) and whose tree is proved empty ends here, with its G,
    in one judgement; an unproved one stays (or becomes) recovery_incomplete with G
    protected, and is tried again next tick (``retry_recovery_incomplete``). An owner that
    is alive or unproven is not written at all. Then the protected G rows of ended runs.
    One failing row never stops the others."""
    reaped = 0
    for row in db_runs.list_orphan_active_runs():
        try:
            outcome, reason = _reprove(row)
        except Exception:
            _log.warning("selfcheck %s: reaper failed; the run is kept as it is",
                         row.get("self_check_run_id"), exc_info=True)
            continue
        if outcome == RELEASED:
            reaped += 1
        elif outcome == PROTECTED:
            _log.warning("selfcheck %s: reaper kept the run protected (%s)", row["self_check_run_id"], reason)
    try:
        locks = lock_manager.selfcheck_protected_locks()
    except Exception:
        _log.warning("selfcheck reaper: listing protected G rows failed", exc_info=True)
        locks = []
    for lock in locks:
        try:
            reaped += int(_reap_terminal_g(lock))
        except Exception:
            _log.warning("selfcheck G %s: reaper failed; kept", lock.get("lock_key"), exc_info=True)
    return reaped


def reverify(run_id: str) -> dict:
    """An operator's ownership re-verification of a recovery_incomplete run (0080 T#3,
    NR0007 §5 item 6; the route requires perm_document_update).

    The server runs the reaper's proof (``_reprove``) once, now. Proved -> the run is ended
    and its G freed. Not proved -> refused with the reason's code (``selfcheck_owner_alive``,
    ``selfcheck_ownership_unknown``, ``selfcheck_tree_unproven``, ``selfcheck_state_conflict``)
    and the run keeps its state and protection. There is no forced release."""
    row = db_runs.get_run(run_id)
    if row is None:
        raise SelfCheckError(404, "selfcheck_run_not_found")
    if row["status"] not in _ACTIVE or row.get("recovery_state") != "incomplete":
        raise SelfCheckError(409, "selfcheck_reverify_not_applicable",
                             details={"self_check_run_id": run_id, "run": summary(row)})
    try:
        outcome, reason = _reprove(row)
    except Exception as exc:
        _log.warning("selfcheck %s: re-verification failed; the run is kept", run_id, exc_info=True)
        raise SelfCheckError(500, "selfcheck_internal_error") from exc
    current = db_runs.get_run(run_id) or row
    _emit(current)
    if outcome == RELEASED:
        return {**public(current), "reverify": {"released": True, "reason": None}}
    code = _REASON_CODES.get(reason, "selfcheck_state_conflict")
    raise SelfCheckError(409, code, details={"self_check_run_id": run_id,
                                             "reverify": {"released": False, "reason": reason},
                                             "run": summary(current)})
