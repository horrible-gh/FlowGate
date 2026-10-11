"""Advisory self-check lifecycle on a live managed TR worktree."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import tr_self_check_runs as db_runs
from modules.flow_gate.services import git_service
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_policy as policy
from modules.flow_gate.services.git import lock_manager

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
            "created_at", "started_at", "finished_at", "updated_at", "linked_at")
    result = {key: row.get(key) for key in keep}
    result["stdout_tail"] = _redact(result["stdout_tail"])
    result["stderr_tail"] = _redact(result["stderr_tail"])
    return result


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


def _worktree(doc: dict) -> Path:
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


def _finish(run_id: str, ctx: lock_manager.ExecutionContext, lock_key: str, root: Path, before: dict,
            runtime: Path, command: policy.ResolvedCommand, args: list[str], cwd: Path,
            env: dict[str, str], timeout: int) -> None:
    holder = ctx.ctx_id
    control = None
    ownership = {}
    cancelled = threading.Event()
    terminal = None
    try:
        control, ownership = executor.spawn([command.executable, *command.argv_prefix, *args], cwd, env, timeout, run_id)
        with _controls_lock:
            _controls[run_id] = control, cancelled
        row = db_runs.mark_running(run_id, source_lock_holder=holder,
            source_head_before=before["head"], source_branch_before=before["branch"],
            source_refs_hash_before=before["refs_hash"], source_index_hash_before=before["index_hash"],
            source_status_hash_before=before["status_hash"], index_lock_before=int(before["index_lock"]), **ownership)
        if row is None:
            raise SelfCheckError(500, "selfcheck_state_conflict")
        _emit(row)
        if db_runs.get_run(run_id)["cancel_requested"]:
            cancelled.set()
            control.cancel()
        result = executor.wait(control, timeout, cancelled)
        after = _probe(root)
        tails = {"stdout_tail": _redact(result.stdout_tail), "stderr_tail": _redact(result.stderr_tail)}
        fields = _source_fields(before, after)
        if result.cancelled or db_runs.get_run(run_id)["cancel_requested"]:
            terminal = db_runs.finish_cancelled(run_id, exit_code=result.exit_code, **tails, **fields)
        else:
            terminal = db_runs.finish_completed(run_id, exit_code=result.exit_code,
                                                timed_out=result.timed_out, **tails, **fields)
    except Exception as exc:
        if control is not None:
            control.cancel()
            try:
                control.proc.wait(timeout=5)
            except Exception:
                pass
            pid, identity = ownership.get("target_pid"), ownership.get("target_start_identity")
            if os.name != "nt" and pid and identity and executor.start_identity(pid) == identity:
                import signal
                import time
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
                deadline = time.monotonic() + 3
                while executor.start_identity(pid) == identity and time.monotonic() < deadline:
                    time.sleep(.1)
            if control.proc.poll() is None or (pid and identity and executor.start_identity(pid) == identity):
                # L 2.24.2: the Group's G stays as a protected row; only this Group is blocked.
                lock_manager.protect(ctx, lock_key, "selfcheck_reap_unconfirmed")
                recovering = db_runs.mark_recovering(run_id)
                if recovering:
                    db_runs.mark_recovery_incomplete(run_id, "process tree could not be reaped")
                return
        code = exc.code if isinstance(exc, (SelfCheckError, policy.PolicyError)) else (
            "selfcheck_process_owner_unavailable" if isinstance(exc, executor.OwnershipError) else "selfcheck_internal_error")
        terminal = db_runs.finish_failed(run_id, error_code=code)
    finally:
        if control is not None:
            control.close()
        with _controls_lock:
            _controls.pop(run_id, None)
        if ctx.lock_lost:
            _log.warning("selfcheck %s lost its Group lock during the run", run_id)
        # A missing terminal commit keeps the Group lock (protected) for startup recovery.
        if terminal is not None:
            lock_manager.release(ctx, lock_key)
            _emit(terminal)
            try:
                shutil.rmtree(runtime)
            except OSError as exc:
                db_runs.set_cleanup_result(run_id, cleanup_pending=True, cleanup_error=str(exc))
        elif ctx.find_held(lock_key) is not None:
            lock_manager.protect(ctx, lock_key, "selfcheck_terminal_unrecorded")


def start(doc_id: str, request: dict, requested_by: str | None = None) -> dict:
    doc = _document(doc_id)
    return _start({"project_id": doc["project_id"], "group_id": doc["group_id"], "tr_doc_id": doc_id},
                  request, requested_by)


def start_draft(token: dict, request: dict) -> dict:
    """Run Self-check for a TR that is not registered yet; the TR(new) token owns the run."""
    return _start(draft_target(token), request, token.get("issued_to"))


def _start(target: dict, request: dict, requested_by: str | None) -> dict:
    """One admission/execution path for TR-bound and draft runs (same policy, worktree, G lock)."""
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
        raise SelfCheckError(409, "selfcheck_already_running", existing["self_check_run_id"])
    run_id = f"scr_{uuid.uuid4().hex[:24]}"
    # D 3.8 / L 2.24.1: this Group's G for the whole run (long, heartbeated).
    ctx = lock_manager.new_context("scr", run_id)
    holder = ctx.ctx_id
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
    try:
        root = _worktree(target)
        if root != first_root:
            path_value, _ = policy.controlled_path(root, _HOST_PATH)
            command = policy.resolve_command(program, args, root, path_value)
        cwd = policy.validate_cwd(root, relative_cwd)
        existing = db_runs.get_active(project_id, group_id)
        if existing:
            raise SelfCheckError(409, "selfcheck_already_running", existing["self_check_run_id"])
        before = _probe(root)
        runtime = Path(tempfile.mkdtemp(prefix=f"flowgate-{run_id}-"))
        env = policy.scrubbed_env(path_value, runtime)
        row = db_runs.create_pending(project_id, group_id, target["tr_doc_id"], requested_by,
            policy.POLICY_VERSION, program, args, command.executable, command.name,
            command.origin, relative_cwd, timeout, list(env), run_id=run_id, source_lock_holder=holder,
            owner_token_id=target.get("owner_token_id"), draft_doc_ref=target.get("draft_doc_ref"))
        worker = threading.Thread(target=_finish, args=(run_id, ctx, lock_key, root, before,
            runtime, command, args, cwd, env, timeout), daemon=True, name=f"selfcheck-{run_id}")
        try:
            worker.start()
        except Exception as exc:
            failed = db_runs.finish_failed(run_id, error_code="selfcheck_internal_error")
            if failed is not None:
                lock_manager.release(ctx, lock_key)
                _emit(failed)
                shutil.rmtree(runtime, ignore_errors=True)
            raise SelfCheckError(500, "selfcheck_internal_error") from exc
        _emit(row)
        return public(row)
    except db_runs.SelfCheckAlreadyRunningError as exc:
        raise SelfCheckError(409, "selfcheck_already_running", exc.existing_run_id) from exc
    except db_runs.DraftOwnerClosedError as exc:
        # The TR registered (or the token was revoked) after draft_target admitted it.
        raise SelfCheckError(403, "selfcheck_forbidden", "draft owner token is no longer open") from exc
    except policy.PolicyError as exc:
        raise SelfCheckError(422, exc.code) from exc
    finally:
        if row is None:
            lock_manager.release(ctx, lock_key)
            if runtime is not None:
                shutil.rmtree(runtime, ignore_errors=True)


def read(doc_id: str, run_id: str) -> dict:
    _document(doc_id)
    row = db_runs.get_run_for_doc(run_id, doc_id)
    if row is None:
        raise SelfCheckError(404, "selfcheck_run_not_found")
    return public(row)


def list_runs(doc_id: str, limit: int = 20) -> list[dict]:
    _document(doc_id)
    return [public(row) for row in db_runs.list_by_doc(doc_id, limit=min(max(1, limit), 100))]


def cancel(doc_id: str, run_id: str) -> dict:
    return _cancel(run_id, lambda: db_runs.get_run_for_doc(run_id, doc_id))


def _cancel(run_id: str, lookup) -> dict:
    row = lookup()
    if row is None:
        raise SelfCheckError(404, "selfcheck_run_not_found")
    db_runs.request_cancel(run_id)
    with _controls_lock:
        item = _controls.get(run_id)
    if item:
        item[1].set()
        item[0].cancel()
    # A draft run may be linked to its TR between the two reads; fall back to the id.
    row = lookup() or db_runs.get_run(run_id)
    _emit(row)
    return public(row)


def read_draft(token: dict, run_id: str) -> dict:
    target = draft_target(token)
    row = db_runs.get_run_for_owner(run_id, target["owner_token_id"])
    if row is None:
        raise SelfCheckError(404, "selfcheck_run_not_found")
    return public(row)


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
    """
    import signal
    import time

    fallback: set[str] = set()
    for row in db_runs.list_orphan_active_runs():
        rid = row["self_check_run_id"]
        state = row["recovery_state"]
        if state == "none":
            current = db_runs.mark_recovering(rid)
        elif state == "incomplete":
            # Re-verification of a protected run must pass incomplete -> recovering first.
            current = db_runs.retry_recovery_incomplete(rid)
        else:
            current = row
        if current is None:
            continue
        target_pid = row.get("target_pid")
        target_identity = row.get("target_start_identity")
        supervisor_pid = row.get("supervisor_pid")
        supervisor_identity = row.get("supervisor_start_identity")
        safe = bool(target_pid and target_identity)
        if row.get("process_owner_kind") == "posix_supervisor":
            safe = safe and bool(supervisor_pid and supervisor_identity)
        elif row.get("process_owner_kind") != "windows_job":
            safe = False
        if safe and os.name != "nt" and executor.start_identity(int(target_pid)) == target_identity:
            # The target is its own session/group leader. Kill only after verifying
            # start identity, then wait for the whole group to disappear.
            try:
                os.killpg(int(target_pid), signal.SIGTERM)
                deadline = time.monotonic() + 3
                while executor.start_identity(int(target_pid)) == target_identity and time.monotonic() < deadline:
                    time.sleep(.1)
                if executor.start_identity(int(target_pid)) == target_identity:
                    os.killpg(int(target_pid), signal.SIGKILL)
                    time.sleep(.2)
            except (OSError, ProcessLookupError):
                pass
        if safe and executor.start_identity(int(target_pid)) == target_identity:
            safe = False
        if safe and supervisor_pid:
            # Server-death control EOF normally makes the supervisor exit. A live
            # matching owner after the bounded wait cannot be proved safe.
            deadline = time.monotonic() + 3
            while executor.start_identity(int(supervisor_pid)) == supervisor_identity and time.monotonic() < deadline:
                time.sleep(.1)
            if executor.start_identity(int(supervisor_pid)) == supervisor_identity:
                safe = False
        project_id, group_id = row["project_id"], row["group_id"]
        if safe:
            finished = db_runs.finish_recovered_interrupted(rid)
            if finished:
                try:
                    lock_manager.selfcheck_release_recovered(project_id, group_id, rid)
                except Exception:
                    _log.warning("selfcheck %s: releasing Group lock failed", rid, exc_info=True)
                _emit(finished)
                continue
        incomplete = db_runs.mark_recovery_incomplete(rid, "process ownership could not be proved absent")
        if incomplete:
            _emit(incomplete)
        if not lock_manager.selfcheck_ensure_protected(project_id, group_id, rid):
            _log.warning("selfcheck %s: G protection not written; recovery_incomplete keeps "
                         "the Group closed", rid)
            fallback.add(project_id)
    return fallback