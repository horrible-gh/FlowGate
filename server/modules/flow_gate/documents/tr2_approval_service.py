"""Strong TR2 approval: one source lock, one Git commit, one outer DB finalize."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import project_test_commands as db_commands
from modules.flow_gate.db import tr2_approval_attempts as db_attempts
from modules.flow_gate.db import tr_commit_ledger as db_ledger
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import after_commit, get_store, in_transaction, now_iso
from modules.flow_gate.documents import tr2_precheck, tr2_service as tr2
from modules.flow_gate.documents.tr2_apply_adapter import adapter
from modules.flow_gate.services import git_service, process_runner, test_command_service
from modules.flow_gate.services.mutation_policy import assert_group_mutation_allowed
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.workflow.event_logger import log_state_changed
from modules.flow_gate.workflow.transition_rules import check_permission, get_doc_review_rule

log = logging.getLogger(__name__)
COMMAND_TIMEOUT_SEC = 600
GATE_TIMEOUT_SEC = 3600
ATTEMPT_STALE_SEC = 600
HEARTBEAT_SEC = 30


def _raise(code: str, loc: str = "approval", **details):
    raise tr2.Tr2ValidationError(code, loc, details)


def _backup_root(doc: dict) -> Path:
    return (storage_paths.get_storage_root(doc["project_id"]) /
            "tr2_approval_backups" / doc["project_id"] / doc["group_id"]).resolve()


def _git(root: Path, args: list[str]):
    return git_service._run_git(args, cwd=root)


def _head(root: Path) -> str:
    proc = _git(root, ["rev-parse", "HEAD"])
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or len(sha) != 40:
        _raise("tr2_git_unavailable", "HEAD")
    return sha


def _clean(root: Path) -> bool:
    return git_service.probe_worktree_pending_changes(root) is False


def _check_commands(doc: dict, spec: dict) -> list[dict]:
    """Python exact match avoids a case-insensitive SQL collation widening trust."""
    rows = db_commands.list_active(doc["project_id"])
    selected = []
    for index, raw in enumerate(spec["gate"]["commands"]):
        command = test_command_service.normalize_command(raw)
        matches = [row for row in rows if
                   test_command_service.normalize_command(row["command"]) == command]
        loc = f"edit_spec.gate.commands[{index}]"
        if not matches:
            _raise("tr2_validation_command_unapproved", loc)
        host = test_command_service.current_os()
        if any(row.get("verified_os") and row["verified_os"] != host for row in matches):
            _raise("tr2_validation_command_os_mismatch", loc)
        if any(row.get("origin") == "auto" and not row.get("verified_os") for row in matches):
            _raise("tr2_validation_command_unverified", loc)
        row = matches[0]
        selected.append({"command": command, "registry_row_id": row["id"],
                         "origin": row.get("origin"), "verified_os": row.get("verified_os")})
    return selected


def _run_validation(root: Path, commands: list[dict], attempt_id: str) -> dict:
    if not commands:
        return {"status": "skipped_no_commands", "commands": []}
    deadline = time.monotonic() + GATE_TIMEOUT_SEC
    results = []
    for item in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "failed", "reason": "gate_timeout", "commands": results}
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(HEARTBEAT_SEC):
                try:
                    db_attempts.update(attempt_id, heartbeat_at=now_iso())
                except Exception:
                    log.warning("TR2 heartbeat failed for %s", attempt_id, exc_info=True)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        start = time.monotonic()
        try:
            timed_out, exit_code, output = process_runner.run_command(
                item["command"], root, max(1, min(COMMAND_TIMEOUT_SEC, int(remaining))), None)
        except Exception as exc:
            timed_out, exit_code, output = False, None, str(exc)
        finally:
            stop.set()
            thread.join(timeout=1)
        record = {**item, "exit_code": exit_code, "timed_out": timed_out,
                  "elapsed_seconds": round(time.monotonic() - start, 3),
                  "output_tail": output[-4000:]}
        results.append(record)
        db_attempts.update(attempt_id, validation_json={"status": "running", "commands": results})
        if timed_out or exit_code != 0:
            return {"status": "failed", "reason": "timeout" if timed_out else "exit_code",
                    "commands": results}
    return {"status": "passed", "commands": results}


def _commit_exact(locked, checked: dict, attempt_id: str) -> tuple[str, list[str]]:
    tr2_precheck.assert_source_lock(locked)
    paths = tr2.target_set(checked["spec"])
    if not paths or not checked["spec"]["edits"]:
        _raise("tr2_spec_invalid", "edit_spec.edits")
    status = _git(locked.root, ["status", "--porcelain", "-z", "--untracked-files=all"])
    if status.returncode != 0:
        _raise("tr2_commit_failed", "git_status")
    changed = set()
    entries = (status.stdout or "").split("\0")
    for entry in entries:
        if entry:
            changed.add(entry[3:])
    if changed != set(paths):
        _raise("tr2_commit_failed", "unexpected_paths", expected=paths, actual=sorted(changed))
    if _git(locked.root, ["add", "--", *paths]).returncode != 0:
        _raise("tr2_commit_failed", "git_add")
    staged = _git(locked.root, ["diff", "--cached", "--name-only", "-z"])
    if staged.returncode != 0 or set(filter(None, staged.stdout.split("\0"))) != set(paths):
        _raise("tr2_commit_failed", "staged_paths")
    subject = f"feat(tr2): approve {checked['document']['doc_id'].rsplit('.', 1)[-1]}"
    cfg = db_git.get_config(locked.project_id) or {}
    proc = git_service._run_git(
        [*git_service._GIT_IDENT, "commit", "-m", subject], cwd=locked.root,
        author_env=git_service._author_env_from_cfg(cfg))
    if proc.returncode != 0:
        _raise("tr2_commit_failed", "git_commit", reason=(proc.stderr or "")[-500:])
    sha = _head(locked.root)
    db_attempts.update(attempt_id, commit_sha=sha,
                       commit_json={"commit_sha": sha, "subject": subject, "paths": paths})
    return sha, paths


def _compensate(locked, attempt: dict, checked: dict | None) -> None:
    """Reset only our exact HEAD while holding the project lock; verify both trees."""
    sha = attempt.get("commit_sha")
    parent = attempt.get("pre_apply_head_sha")
    if not sha or not parent or _head(locked.root) != sha:
        _raise("tr2_recovery_required", "git_head")
    parent_check = _git(locked.root, ["rev-parse", "HEAD^"])
    if parent_check.returncode != 0 or (parent_check.stdout or "").strip() != parent:
        _raise("tr2_recovery_required", "git_parent")
    if not _clean(locked.root):
        _raise("tr2_recovery_required", "worktree_dirty")
    if _git(locked.root, ["reset", "--hard", parent]).returncode != 0:
        _raise("tr2_recovery_required", "git_reset")
    if _head(locked.root) != parent or not _clean(locked.root):
        _raise("tr2_recovery_required", "git_verify")
    if checked is None:
        journal = json.loads(attempt.get("precheck_json") or "{}")
        spec = journal.get("spec")
        if not isinstance(spec, dict) or tr2.spec_fingerprint(spec) != attempt["spec_fingerprint"]:
            _raise("tr2_recovery_required", "stored_spec")
    else:
        spec = checked["spec"]
    if tr2.target_fingerprint(spec, locked.root) != attempt["baseline_fingerprint"]:
        _raise("tr2_recovery_required", "baseline_fingerprint")


def _rollback(locked, attempt: dict, checked: dict | None, error: Exception) -> None:
    attempt_id = attempt["attempt_id"]
    db_attempts.update(attempt_id, phase="rollback")
    try:
        current = db_attempts.by_id(attempt_id)
        # A process can fail between `git commit` and journaling its SHA. The
        # project lock excludes a foreign writer, but verify the direct parent
        # before treating this HEAD as our commit.
        if not current.get("commit_sha") and current.get("pre_apply_head_sha"):
            head = _head(locked.root)
            if head != current["pre_apply_head_sha"]:
                parent = _git(locked.root, ["rev-parse", "HEAD^"])
                if parent.returncode != 0 or (parent.stdout or "").strip() != current["pre_apply_head_sha"]:
                    _raise("tr2_recovery_required", "unknown_git_head")
                current = db_attempts.update(attempt_id, commit_sha=head)
        if current.get("commit_sha"):
            _compensate(locked, current, checked)
        else:
            bundle = current.get("backup_bundle_id")
            if not bundle:
                _raise("tr2_recovery_required", "backup_bundle_id")
            if checked is None:
                journal = json.loads(current.get("precheck_json") or "{}")
                spec = journal.get("spec")
                if not isinstance(spec, dict) or tr2.spec_fingerprint(spec) != current["spec_fingerprint"]:
                    _raise("tr2_recovery_required", "stored_spec")
            else:
                spec = checked["spec"]
            tr2_precheck.restore_locked(locked, spec, current["baseline_fingerprint"],
                                        _backup_root(db_docs.get_by_id(current["tr2_doc_id"])),
                                        bundle, restart=True)
            if _git(locked.root, ["reset", "--mixed", "HEAD"]).returncode != 0 or not _clean(locked.root):
                _raise("tr2_recovery_required", "worktree_cleanup")
        code = error.code if isinstance(error, tr2.Tr2ValidationError) else "tr2_apply_failed"
        db_attempts.finish(attempt_id, state="failed", result_code="recovered_rollback",
                           error_code=code, error_detail=str(error))
    except Exception as recovery_error:
        db_attempts.finish(attempt_id, state="recovery_required", result_code="failed",
                           error_code="tr2_recovery_required", error_detail=str(recovery_error))
        _raise("tr2_recovery_required", "rollback", reason=str(recovery_error))


def _stale(row: dict) -> bool:
    try:
        stamp = datetime.fromisoformat(str(row["heartbeat_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp).total_seconds() > ATTEMPT_STALE_SEC
    except (ValueError, TypeError):
        return True


def _recover_stale(locked, row: dict) -> None:
    phase = row["phase"]
    if phase in {"created", "precheck", "snapshot"}:
        db_attempts.finish(row["attempt_id"], state="failed", result_code="recovered_no_write")
        return
    if row.get("commit_sha"):
        doc = db_docs.get_by_id(row["tr2_doc_id"])
        ledger = db_ledger.get_by_id(row["ledger_row_id"]) if row.get("ledger_row_id") else None
        reachable = _git(locked.root, ["merge-base", "--is-ancestor", row["commit_sha"], "HEAD"])
        if (doc and doc.get("doc_review_status") == "approved" and ledger
                and int(doc.get("revision_no") or 0) == int(row["document_revision"])
                and ledger.get("state") == "live"
                and ledger.get("doc_id") == row["tr2_doc_id"]
                and ledger.get("group_id") == row["group_id"]
                and ledger.get("commit_sha") == row["commit_sha"]
                and reachable.returncode == 0):
            db_attempts.finish(row["attempt_id"], state="succeeded", result_code="succeeded")
            return
        if doc and doc.get("doc_review_status") == "pending_review" and not ledger:
            _rollback(locked, row, None, RuntimeError("stale Git commit"))
            return
    elif phase in {"apply", "validation", "commit", "rollback"}:
        _rollback(locked, row, None, RuntimeError("stale approval"))
        return
    db_attempts.finish(row["attempt_id"], state="recovery_required", result_code="failed",
                       error_code="tr2_recovery_required", error_detail="stale state contradiction")
    _raise("tr2_recovery_required", "stale_attempt")


def _replay(row: dict) -> dict:
    state = row["state"]
    if state == "succeeded":
        doc = db_docs.get_by_id(row["tr2_doc_id"])
        if doc and doc.get("doc_review_status") == "approved":
            return doc
        _raise("tr2_history_invariant_error", "attempt_replay")
    if state == "failed":
        _raise(row.get("error_code") or "tr2_precheck_failed", "attempt_replay",
               attempt_id=row["attempt_id"])
    _raise("tr2_recovery_required" if state == "recovery_required" else "tr2_in_progress",
           "attempt_replay", attempt_id=row["attempt_id"])


def approve(*, doc_id: str, actor_user_id: str, user_permissions: set[str],
            mutation_principal, expected_revision: int | None = None,
            request_key: str | None = None, locale: str = "ko") -> dict:
    if in_transaction():
        _raise("tr2_nested_transaction")
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != "TR2":
        _raise("tr2_workflow_conflict", "doc_id")
    if mutation_principal is None:
        _raise("tr2_principal_required")
    if not check_permission(user_permissions, ("document.approve",)):
        from modules.flow_gate.workflow.pipeline_service import PermissionError
        raise PermissionError("Insufficient permissions: document.approve is required")
    if request_key is not None and (not isinstance(request_key, str) or not request_key or len(request_key) > 64):
        _raise("tr2_spec_invalid", "request_key")
    if request_key:
        replay = db_attempts.by_request_key(request_key)
        if replay:
            if replay["tr2_doc_id"] != doc_id:
                _raise("tr2_workflow_conflict", "request_key")
            if replay["state"] != "in_progress" or not _stale(replay):
                return _replay(replay)
    assert_group_mutation_allowed(doc["group_id"], mutation_principal, "tr2_approve")
    with tr2_precheck.source_lock(doc["project_id"], doc["group_id"], request_key=request_key) as locked:
        if request_key:
            replay = db_attempts.by_request_key(request_key)
            if replay and replay["state"] != "in_progress":
                return _replay(replay)
        if db_attempts.recovery_required(doc_id):
            _raise("tr2_recovery_required", "prior_attempt")
        active = db_attempts.in_progress(doc_id)
        if active:
            if not _stale(active):
                _raise("tr2_in_progress", "prior_attempt", attempt_id=active["attempt_id"])
            _recover_stale(locked, active)
            if request_key and active.get("request_key") == request_key:
                return _replay(db_attempts.by_id(active["attempt_id"]))
        doc = db_docs.get_by_id(doc_id)
        if doc.get("doc_review_status") == "approved":
            success = db_attempts.latest_success(doc_id)
            if success and success["document_revision"] == int(doc.get("revision_no") or 0):
                return doc
            _raise("tr2_history_invariant_error", "approved_without_attempt")
        if get_doc_review_rule(doc.get("doc_review_status") or "", "approve") != "approved":
            _raise("tr2_workflow_conflict", "review_status")
        revision = int(doc.get("revision_no") or 0)
        if expected_revision is not None and expected_revision != revision:
            _raise("tr2_spec_changed", "revision_no")
        prior = db_attempts.latest_success(doc_id)
        if prior and int(prior["document_revision"]) >= revision:
            _raise("tr2_history_revision_required", "revision_no")
        body = tr2.load_body(tr2.canonical_path_for_doc(doc))
        spec = tr2.canonicalize(tr2.validate(body, doc=doc))["edit_spec"]
        fingerprint = tr2.spec_fingerprint(spec)
        baseline = body.get("baseline_fingerprint")
        if not isinstance(baseline, str) or not tr2._SOURCE_HEX.fullmatch(baseline):
            _raise("tr2_history_invariant_error", "baseline_fingerprint")
        attempt = db_attempts.create(doc=doc, actor_user_id=actor_user_id,
                                     spec_fingerprint=fingerprint,
                                     baseline_fingerprint=baseline, request_key=request_key)
        attempt_id = attempt["attempt_id"]
        checked = None
        mutated = False
        finalized = False
        try:
            db_attempts.update(attempt_id, phase="precheck")
            checked = tr2_precheck.authoritative_precheck(
                doc_id, locked, expected_revision=revision,
                expected_spec_fingerprint=fingerprint)
            commands = _check_commands(doc, checked["spec"])
            head = _head(locked.root)
            db_attempts.update(attempt_id, pre_apply_head_sha=head,
                               live_fingerprint=checked["evaluation"]["live_fingerprint"],
                               precheck_json={"targets": tr2.target_set(spec), "commands": commands, "spec": checked["spec"]})
            db_attempts.update(attempt_id, phase="snapshot")
            bundle = adapter.create_backup(spec, locked.root, _backup_root(doc), baseline=baseline)
            db_attempts.update(attempt_id, backup_bundle_id=bundle, phase="apply")
            mutated = True
            applied = adapter.apply_all(spec, locked.root, _backup_root(doc), bundle)
            db_attempts.update(attempt_id, phase="validation", apply_json=applied)
            validation = _run_validation(locked.root, commands, attempt_id)
            db_attempts.update(attempt_id, validation_json=validation)
            if validation["status"] == "failed":
                _raise("tr2_validation_failed", "gate", reason=validation.get("reason"))
            db_attempts.update(attempt_id, phase="commit")
            sha, paths = _commit_exact(locked, checked, attempt_id)
            db_attempts.update(attempt_id, phase="finalize")
            with get_store().transaction():
                fresh = db_docs.get_by_id(doc_id)
                if not fresh or int(fresh.get("revision_no") or 0) != revision:
                    _raise("tr2_spec_changed", "revision_no")
                fresh_body = tr2.load_body(tr2.canonical_path_for_doc(fresh))
                fresh_spec = tr2.canonicalize(tr2.validate(fresh_body, doc=fresh))["edit_spec"]
                if tr2.spec_fingerprint(fresh_spec) != fingerprint:
                    _raise("tr2_spec_changed", "spec_fingerprint")
                ledger = db_ledger.record_commit(group_id=doc["group_id"], doc_id=doc_id,
                                                 commit_sha=sha,
                                                 commit_subject=f"feat(tr2): approve {doc_id.rsplit('.', 1)[-1]}")
                if not ledger or not ledger.get("id"):
                    _raise("tr2_history_invariant_error", "ledger")
                if not db_docs.update_review_cas(doc_id, revision, "pending_review",
                                                 {"doc_review_status": "approved"}):
                    _raise("tr2_spec_changed", "review_cas")
                next_head = db_wfseq.get_pending_head_by_group(doc["group_id"], doc["project_id"])
                if next_head and next_head.get("result_doc_id") == doc_id:
                    _raise("tr2_workflow_conflict", "workflow_progression")
                log_state_changed(project_id=doc["project_id"], actor_user_id=actor_user_id,
                                  from_state="review:pending_review", to_state="review:approved",
                                  group_id=doc["group_id"], document_id=doc.get("id"),
                                  action_code="review_approve")
                db_attempts.finish(attempt_id, state="succeeded", result_code="succeeded",
                                   ledger_row_id=ledger["id"],
                                   ledger_json={"id": ledger["id"], "commit_sha": sha})
                def notify():
                    from modules.flow_gate.services import workflow_rework_service
                    workflow_rework_service.clear_return_point_if_complete(doc["group_id"], doc)
                after_commit(notify)
            finalized = True
            return db_docs.get_by_id(doc_id)
        except Exception as exc:
            if finalized:
                raise
            if mutated:
                _rollback(locked, db_attempts.by_id(attempt_id), checked, exc)
            else:
                code = exc.code if isinstance(exc, tr2.Tr2ValidationError) else "tr2_precheck_failed"
                db_attempts.finish(attempt_id, state="failed", result_code="failed",
                                   error_code=code, error_detail=str(exc))
            raise
