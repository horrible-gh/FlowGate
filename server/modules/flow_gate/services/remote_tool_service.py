"""Project-control remote tool — processing pipeline (L0006).

Orchestrates the 7-step pipeline (L0006 §2) for the remote tool API defined by
P0005 (message format) and DB0007 (storage):

  ① Authentication → 401 unauthorized        (not logged: subject unidentified)
  ② Permission     → 403 forbidden            (logged as denied)
  ③ Path validation → 422 invalid_request     (logged as error)
  ④ Request validity → 422 invalid_request     (logged as error)
  ⑤ Execute operation → 404 / 409 / 413 / 503  (logged per result)
  ⑥ History logging (both success and failure, after authentication passes)
  ⑦ Completion ment (on mutating-op success) → 200 response

0347 (P0004 / L0005) adds `patch` (exact old_string/new_string edit), `stat`
(metadata only) and a byte window (`offset`/`length`) on `read`.

The router is thin and delegates everything here. This module owns the P0005
common envelope, the fail-fast error ordering (L0006 §7), op-logging timing
(L0006 §5.1), and the completion-ment condition (L0006 §6).

Branch resolution note (NR0009 §8.3): the operation target is the project
*source root* `src_root(project_name, branch)`. P0005 carries no branch in the
request and DB0007's grant has no branch column, so branch is taken from
`project_settings.branch` (default 'main') — the only spec-available source,
matching file_transfer_routes._get_src_root.
"""
from __future__ import annotations

import codecs
import logging
import os
import re
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import remote_tool_grants as db_grants
from modules.flow_gate.db import remote_tool_op_log as db_oplog
from modules.flow_gate.db import projects as db_projects
# 0382 proposal 5-c: uses the same rule as the screens and the check so that only debris cleanup may delete recursively.
from modules.flow_gate.services import path_exclusion_rules
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import token_service
from modules.flow_gate.storage.paths import src_root
from modules.flow_gate import template_provision
from modules.flow_gate.storage.safe_path import (
    is_safe_relative,
    resolve_in_root,
    _under_root,
)

_logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

OPS = ("read", "write", "grep", "glob", "remove", "patch", "stat")
_WORKER_GRANT_PREFIX = "worker_"
# The mutating step types live in tool_registry.MUTATING_STEP_TYPES — the single judgement
# point (0349 D0004 D-2). This module reaches them through tool_registry.kind_for_token.
# Types whose own document type decides their source scope, ignoring any workflow
# sequence they happen to sit in (see _worker_token_step_type).
_SELF_SCOPED_WORK_TYPES = {"CH"}

# 0279 T0005 (NR0003 cause 2): directory names never worth walking for a source
# scan. `server/.venv` alone dominated the measured 40s grep — it is dependency
# code, not project source, and every byte of it was being read and regex-matched.
# Skipped for grep and glob only; read/write/remove address files directly and are
# unaffected, so an explicit path into one of these directories still works.
_SCAN_EXCLUDE_DIRS = frozenset({".venv", "venv", "node_modules", ".git", "__pycache__"})

# operation → required scope (P0005 §3.2 / L0006 §3.2 / DB0007 §5). glob shares the grep scope.
# 0347 P0004 §0.4: patch/stat deliberately reuse the existing write/read scopes — no new
# scope value, so remote_tool_grant_scope needs no migration and an investigation token that
# only holds read/grep is barred from patch automatically.
OP_SCOPE = {
    "read": "read",
    "write": "write",
    "grep": "grep",
    "glob": "grep",
    "remove": "remove",
    "patch": "write",
    "stat": "read",
}

# 0347 L0005 §1: the mutating-op set used to be the literal ("write", "remove") repeated at
# three decision points (mutation source-root gate / explorer SSE / continuation ment). patch
# has to appear at all three, and a single constant is what stops a future op from reaching
# two of them and silently landing in the base checkout (0205). `stat` is NOT here — it reads.
_MUTATING_OPS = frozenset({"write", "remove", "patch"})

# ③ path validation: ops whose only path-bearing field is `path`. grep/glob carry a second
# pattern field and keep their own branches. Leaving patch/stat out would skip ③ entirely.
_PATH_VALIDATE_SINGLE_FIELD_OPS = frozenset({"read", "write", "remove", "patch", "stat"})

# HTTP status → P0005 §6 error code.
ERROR_CODE_BY_STATUS = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    422: "invalid_request",
    503: "unavailable",
}

# HTTP status → op_log result value (DB0007 §7.3·§7.5). 200 → success, 401 not logged.
RESULT_BY_STATUS = {
    403: "denied",
    422: "error",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    503: "unavailable",
}

_MAX_WRITE_BYTES = 100 * 1024 * 1024   # 100 MB — write/patch result guard (413)
_MAX_READ_BYTES = 100 * 1024 * 1024    # 100 MB — read *window* guard (413, 0347 P0004 §7.3)
_GREP_FILE_SKIP_BYTES = 1024 * 1024    # 1 MB — skip large files when scanning (NR0011 §2)
_STAT_SAMPLE_BYTES = 64 * 1024         # 64 KB — stat EOL/binary sample (0347 L0005 §1)

_JST = timezone(timedelta(hours=9))


class _OpError(Exception):
    """Signals a fail-fast pipeline stop with an HTTP status (L0006 §7).

    ``details`` / ``message`` are optional (0205 P scenario 5): a write/remove
    rejected for a missing worktree carries a structured cause so the worker can
    cite exactly what is blocking it. Absent → the generic per-status envelope."""

    def __init__(
        self, status: int, *, details: Optional[dict] = None, message: Optional[str] = None
    ):
        super().__init__(f"remote tool op error: {status}")
        self.status = status
        self.details = details
        self.message = message


# ── ① Authentication ───────────────────────────────────────────────────────

def _authenticate(raw_token: Optional[str]) -> Optional[dict]:
    """Return the active grant for the token, or None (→ 401).

    Reuses token_service hashing/pepper/constant-time compare (NR0009 §4.2 — no
    duplicate crypto). A grant is valid when its token_hash matches and
    status='active' and (expires_at IS NULL OR in the future) (DB0007 §4).
    """
    if not raw_token:
        return None
    try:
        _pepper_id, pepper = token_service._active_pepper()
    except RuntimeError:
        return None
    candidate_hash = token_service._hash_token(raw_token, pepper)
    grant = db_grants.get_by_token_hash(candidate_hash)
    if grant is None:
        return _worker_grant_from_flowgate_token(raw_token)
    if not token_service._verify_hash(grant["token_hash"], candidate_hash):
        return None
    if not _worker_grant_still_valid(grant):
        return None
    if grant.get("status") != "active":
        return None
    expires_at = grant.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return None
        except ValueError:
            return None
    if _is_worker_grant(grant) and not _reconcile_worker_grant_scopes(grant):
        return None
    return grant


def _is_worker_grant(grant: dict) -> bool:
    return str(grant.get("grant_id") or "").startswith(_WORKER_GRANT_PREFIX)


def _worker_token_for_grant(grant: dict) -> Optional[dict]:
    grant_id = str(grant.get("grant_id") or "")
    if not grant_id.startswith(_WORKER_GRANT_PREFIX):
        return None
    token_id = grant_id[len(_WORKER_GRANT_PREFIX):]
    return db_tokens.get_by_id(token_id)


def _worker_grant_still_valid(grant: dict) -> bool:
    """Worker-token grants are valid only while the backing inbox token is active."""
    if not _is_worker_grant(grant):
        return True
    token_rec = _worker_token_for_grant(grant)
    if not token_rec:
        return False
    if token_rec.get("revoked_at") or token_rec.get("consumed_at"):
        return False
    expires_at = token_rec.get("expires_at")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp >= datetime.now(timezone.utc)
    except ValueError:
        return False


def _reconcile_worker_grant_scopes(grant: dict) -> bool:
    """Keep an existing lazy worker grant aligned with the current token type policy."""
    token_rec = _worker_token_for_grant(grant)
    if not token_rec:
        return False
    scopes = _scopes_for_worker_token(token_rec)
    if not scopes:
        return False
    grant_id = str(grant.get("grant_id") or "")
    try:
        if db_grants.get_scopes(grant_id) != set(scopes):
            db_grants.replace_scopes(grant_id, scopes)
    except Exception:
        return False
    return True


def _worker_grant_from_flowgate_token(raw_token: Optional[str]) -> Optional[dict]:
    """Create a remote-tool grant lazily for a normal FlowGate worker token.

    Copy mentions hand workers a single Bearer token for document reads and inbox
    submission. The remote source API stores its own grant rows for auditing, so
    the first remote call creates a grant tied to that worker token. The grant id
    embeds the token id, and _worker_grant_still_valid keeps it from outliving the
    backing token after inbox consumption or revocation.
    """
    if not raw_token:
        return None
    try:
        token_rec = token_service.verify(raw_token)
    except Exception:
        return None

    token_id = token_rec.get("token_id")
    token_hash = token_rec.get("hash")
    project = token_rec.get("project")
    if not token_id or not token_hash or not project:
        return None

    grant_id = f"{_WORKER_GRANT_PREFIX}{token_id}"
    existing = db_grants.get_by_id(grant_id)
    if existing:
        if not _worker_grant_still_valid(existing):
            return None
        return existing if _reconcile_worker_grant_scopes(existing) else None

    scopes = _scopes_for_worker_token(token_rec)
    if not scopes:
        return None

    group_id = token_rec.get("group_id") or ""
    parts = group_id.split(".", 2)
    module = parts[1] if len(parts) == 3 else "default"
    try:
        grant = db_grants.create(
            {
                "grant_id": grant_id,
                "token_hash": token_hash,
                "project": project,
                "module": module,
                # 0115 G1: carry the FULL group id so source resolution can route
                # this worker to its group worktree (L0006 §2.2). Legacy grants
                # keep NULL and fall back to the project branch (L0006 §4.1).
                "group_id": group_id or None,
                "report_doc_id": None,
                "session_id": token_rec.get("issued_to"),
                "status": "active",
                "issued_at": token_rec.get("created_at"),
                "expires_at": token_rec.get("expires_at"),
            },
            scopes,
        )
    except Exception:
        # Another request may have created it concurrently, or storage may reject it.
        existing = db_grants.get_by_id(grant_id)
        if not existing or not _worker_grant_still_valid(existing):
            return None
        return existing if _reconcile_worker_grant_scopes(existing) else None

    # 0115 H2: mutating (T/TR/TSR) workers get their group worktree (re)guaranteed at
    # the kind judgement point — the backstop for a failed or
    # restarted H1 (decide-time) provisioning. Idempotent and never raises; on
    # failure source access simply keeps resolving to the fallback branch folder.
    if group_id and "write" in scopes:
        try:
            from modules.flow_gate.services import git_service  # lazy — import cycle

            git_service.ensure_worktree(project, module, group_id)
        except Exception:
            pass
    return grant


_SCOPES_BY_KIND = {
    "read_write": ["read", "write", "grep", "remove"],
    "read": ["read", "grep"],
    "none": [],
}


def _scopes_for_worker_token(token_rec: dict) -> list[str]:
    """Map a document worker token to remote source scopes.

    0349 D0004 D-2: the judgement itself lives in the tool registry — the same call the
    mention builders and /help/tools make — and this function only renames its answer into
    scope vocabulary. When the two judged independently (they did until now), the mention
    could advertise a tool this function then refused.

    Implementation work (T/TR/TSR) receives full CRUD. Investigation/review/design
    handoffs receive read/search only so their source-access instructions cannot
    silently override the document-level "do not modify source" scope.
    """
    from modules.flow_gate.services import tool_registry  # lazy — import cycle

    kind, _reason = tool_registry.kind_for_token(token_rec)
    return list(_SCOPES_BY_KIND.get(kind, []))


def _worker_token_step_type_result(token_rec: dict) -> tuple[Optional[str], bool]:
    """Resolve the workflow head type and preserve whether lookup raised.

    A chat document answers for itself. Chat is auto-completed the moment it is
    created, so when a CH sits in a workflow sequence get_effective_head() already
    points at the NEXT pending slot — and if that slot is T/TR/TSR the chat token
    would inherit source write/remove, while its mention says read/search only.
    The worker acts on the mention it was given, so a CH doc_ref is pinned to its
    own type here.
    """
    doc_ref = token_rec.get("doc_ref")
    if not doc_ref:
        return None, False
    try:
        doc = db_documents.get_by_id(doc_ref)
        own_type = str(doc["type_code"]) if doc and doc.get("type_code") else None
        if own_type in _SELF_SCOPED_WORK_TYPES:
            return own_type, False
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            seq = db_wfseq.get_sequence_by_doc_id(doc_ref)
        if seq is not None:
            head = db_wfseq.get_effective_head(seq["id"])
            if head and head.get("type"):
                return str(head["type"]), False
        return own_type, False
    except Exception:
        return None, True


def _worker_token_step_type(token_rec: dict) -> Optional[str]:
    """Backward-compatible value-only wrapper for existing callers."""
    return _worker_token_step_type_result(token_rec)[0]


# ── ③ Path safety / ④ request validity ───────────────────────────────────────

def _present_path_fields(op: str, body: dict) -> list[str]:
    """Path-bearing input values that are *present* (L0006 §4.1) — for ③.

    Missing required fields are ④'s concern, so only values that exist (the key
    is present and not None) are returned here.
    """
    out: list[str] = []
    if op in _PATH_VALIDATE_SINGLE_FIELD_OPS:
        if body.get("path") is not None:
            out.append(body["path"])
    elif op == "grep":
        if body.get("path") is not None:
            out.append(body["path"])
        if body.get("glob") is not None:
            out.append(body["glob"])
    elif op == "glob":
        if body.get("path") is not None:
            out.append(body["path"])
        if body.get("pattern") is not None:
            out.append(body["pattern"])
    return out


def _validate_paths(op: str, body: dict) -> None:
    """③ — every present path-bearing value must be root-relative & escape-free."""
    for value in _present_path_fields(op, body):
        if not is_safe_relative(value):
            raise _OpError(422)


def _require_str(body: dict, key: str, *, allow_empty: bool = False) -> str:
    val = body.get(key)
    if not isinstance(val, str):
        raise _OpError(422)
    if not allow_empty and val == "":
        raise _OpError(422)
    return val


def _validate_required(op: str, body: dict) -> None:
    """④ — required fields present & well-typed (P0005 per-operation field table)."""
    if op == "read":
        _require_str(body, "path")
        _validate_max_int(body, "max_bytes")
        # 0347 P0004 §7.1 — same rule as max_bytes: int, not bool, not negative.
        _validate_max_int(body, "offset")
        _validate_max_int(body, "length")
    elif op == "write":
        _require_str(body, "path")
        # content is required but an empty body (empty file) is legal.
        if not isinstance(body.get("content"), str):
            raise _OpError(422)
        mode = body.get("mode")
        if mode is not None and mode not in ("create", "overwrite", "append"):
            raise _OpError(422)
        _validate_encoding(body)
    elif op == "remove":
        _require_str(body, "path")
        if body.get("recursive") is not None and not isinstance(body["recursive"], bool):
            raise _OpError(422)
    elif op == "grep":
        _require_str(body, "pattern")
        _validate_max_int(body, "max_results")
        if body.get("ignore_case") is not None and not isinstance(body["ignore_case"], bool):
            raise _OpError(422)
        # invalid regex → request form error (422)
        try:
            re.compile(body["pattern"])
        except re.error:
            raise _OpError(422)
    elif op == "glob":
        _require_str(body, "pattern")
    elif op == "patch":
        _require_str(body, "path")
        # An empty old_string would match everywhere (and at every character
        # boundary) — the one input that makes exact-match patching meaningless.
        old_string = _require_str(body, "old_string")
        if not isinstance(body.get("new_string"), str):
            raise _OpError(422)
        if body.get("replace_all") is not None and not isinstance(body["replace_all"], bool):
            raise _OpError(422)
        if old_string == body["new_string"]:
            raise _OpError(
                422,
                details={"reason": "no_op_edit"},
            )
        _validate_encoding(body)
    elif op == "stat":
        _require_str(body, "path")


def _validate_max_int(body: dict, key: str) -> None:
    val = body.get(key)
    if val is None:
        return
    if not isinstance(val, int) or isinstance(val, bool) or val < 0:
        raise _OpError(422)


def _validate_encoding(body: dict) -> None:
    enc = body.get("encoding")
    if enc is None:
        return
    if not isinstance(enc, str):
        raise _OpError(422)
    try:
        "".encode(enc)
    except LookupError:
        raise _OpError(422)


# ── Source-root resolution ────────────────────────────────────────────────────

def _fallback_project_root(grant: dict) -> Optional[Path]:
    """The ordinary project-branch source folder (no worktree), or None (→ 503).

    Branch comes from project_settings.branch (default 'main') — the only
    spec-available source (NR0009 §8.3)."""
    project_id = grant.get("project")
    row = db_projects.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    if not project_name:
        return None
    settings = db_projects.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"
    return src_root(project_name, branch).resolve()


def _read_worktree_expected_reasons(git_service) -> frozenset:
    """SRC_ROOT_* fallback reasons that mean an integrated group's worktree was
    *expected* but could not be resolved (0301 T0004 / NR0003 §6-3).

    The benign reasons are deliberately excluded: SRC_ROOT_NO_GROUP (legacy
    grant with no group context) and SRC_ROOT_INTEGRATION_OFF (project is not
    git-integrated) are ordinary fallback-first outcomes, so a read that lands on
    the base tree for either of them stays silent and untouched.
    """
    return frozenset({
        git_service.SRC_ROOT_NO_STATE,
        git_service.SRC_ROOT_UNREGISTERED,
        git_service.SRC_ROOT_NO_BRANCH,
        git_service.SRC_ROOT_NO_PROJECT_NAME,
        git_service.SRC_ROOT_DIR_MISSING,
        git_service.SRC_ROOT_DIR_BROKEN,
        git_service.SRC_ROOT_ERROR,
    })


def _resolve_src_root(grant: dict, op: str = "read") -> Optional[Path]:
    """Resolve the project source root for the grant, or None (→ 503).

    0115: a grant that carries a group_id is routed to that group's git worktree
    when the project is git-integrated and the worktree is registered (L0006
    §2.2). Everything else — no group on the grant (legacy rows), no config,
    disabled integration, missing worktree — falls back to the ordinary
    project-branch folder exactly as before (fallback-first principle). Used by
    read/grep/glob; write/remove use _resolve_root_for_mutation (0205 L §2.3).

    0301 T0004 (NR0003 §6-3): the read path used to return the base tree with no
    self-heal and no trace, so a worker that read source in the window before its
    group worktree was provisioned (the first-group timing gap, NR0003 §4.4)
    silently read `main` and began editing on top of that state — the very
    "it keeps working on the main branch" symptom of B0001. Reads must NEVER be blocked (that
    would break legacy and non-integrated access), but when an integrated group
    falls back for a reason that means the worktree was *expected*, we now mirror
    the write gate: one synchronous ensure_worktree self-heal retry, and if it is
    still unresolved a warning is logged so this is no longer the one traceless
    fallback path. Benign fallbacks (legacy grant / integration off) stay silent.
    """
    project_id = grant.get("project")
    group_id = grant.get("group_id")
    if not group_id:
        return _fallback_project_root(grant)   # legacy grant — silent fallback
    try:
        from modules.flow_gate.services import git_service  # lazy — import cycle

        wt, reason = git_service.effective_src_root_ex(project_id, group_id)
        if wt is not None:
            return wt
        if reason in _read_worktree_expected_reasons(git_service):
            # Provisioning-timing window: one self-heal retry (symmetric with
            # _resolve_root_for_mutation), then re-resolve. ensure_worktree is
            # idempotent and never raises fatally; on failure we keep serving the
            # base tree so the read still succeeds.
            module = grant.get("module") or "default"
            try:
                if git_service.ensure_worktree(
                    project_id, module, group_id, trigger="remote_read_retry"
                ) == "ok":
                    wt = git_service.effective_src_root(project_id, group_id)
                    if wt is not None:
                        return wt
            except Exception:
                pass
            _logger.warning(
                "remote read fell back to base tree for group %s (op=%s, "
                "reason=%s) — worktree expected but unresolved after self-heal; "
                "serving base checkout (read not blocked)",
                group_id, op, reason,
            )
    except Exception:
        pass  # resolution problems must never break the fallback path
    return _fallback_project_root(grant)


def _mutation_block_message(
    op: str, group_id: str, cause: str, blocking_group_id: Optional[str],
    provision_error: Optional[str],
) -> str:
    """Human-readable reason a write/remove was blocked (0205 P scenario 5)."""
    if cause == "merge_conflict_open":
        why = f"unresolved merge of group '{blocking_group_id}'"
    elif cause == "provision_failed":
        why = f"worktree provisioning failed: {provision_error}"
    else:
        why = "worktree missing"
    return (
        f"git worktree unavailable for group '{group_id}' — {op} blocked (cause: {why})"
    )


def _resolve_root_for_mutation(grant: dict, op: str) -> Optional[Path]:
    """Source root for a write/remove, with the silent base fallback REMOVED
    (0205 L §2.3 / P scenario 5).

    A worker whose group worktree is unavailable used to have its edits land in
    the base checkout — outside git management — which is exactly how the 0203
    tangle spread. Now the write is REJECTED (409) with the blocking cause, after
    one synchronous self-heal ensure_worktree retry. Legacy grants (no group_id)
    and non-integrated projects still fall back, unchanged.
    """
    project_id = grant.get("project")
    group_id = grant.get("group_id")
    if not group_id:
        return _fallback_project_root(grant)   # legacy grant — unchanged

    from modules.flow_gate.services import git_service  # lazy — import cycle
    from modules.flow_gate.db import git_integration as db_git

    wt = git_service.effective_src_root(project_id, group_id)
    if wt is not None:
        return wt

    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return _fallback_project_root(grant)   # non-integrated project — unchanged

    # Self-heal: one synchronous ensure_worktree retry (WRITE_GATE_PROVISION_RETRY=1).
    module = grant.get("module") or "default"
    try:
        if git_service.ensure_worktree(
            project_id, module, group_id, trigger="remote_write_retry"
        ) == "ok":
            wt = git_service.effective_src_root(project_id, group_id)
            if wt is not None:
                return wt
    except Exception:
        pass

    # Still unavailable — reject with the cause (priority: open session, then a
    # recorded provisioning failure, else a bare missing worktree).
    session = git_service.open_merge_session_of_project(project_id)
    state = db_git.get_state(group_id)
    provision_error = state.get("provision_error") if state else None
    if session is not None:
        cause, blocking = "merge_conflict_open", session.get("group_id")
    elif provision_error:
        cause, blocking = "provision_failed", None
    else:
        cause, blocking = "worktree_missing", None
    raise _OpError(
        409,
        details={
            "group_id": group_id, "cause": cause,
            "blocking_group_id": blocking, "provision_error": provision_error,
        },
        message=_mutation_block_message(op, group_id, cause, blocking, provision_error),
    )


# ── ⑤ Execution ───────────────────────────────────────────────────────────────

def _execute(op: str, body: dict, root: Path) -> tuple[dict, Optional[int]]:
    """Run the operation. Returns (response_extra_fields, bytes_processed)."""
    if op == "read":
        return _exec_read(body, root)
    if op == "write":
        return _exec_write(body, root)
    if op == "remove":
        return _exec_remove(body, root)
    if op == "grep":
        return _exec_grep(body, root)
    if op == "glob":
        return _exec_glob(body, root)
    if op == "patch":
        return _exec_patch(body, root)
    if op == "stat":
        return _exec_stat(body, root)
    raise _OpError(422)  # unreachable (op validated earlier)


# ── Byte/EOL helpers shared by read · patch · stat (0347 L0005 §2.7) ───────────

def _detect_dominant_eol(sample: bytes) -> str:
    """'lf' / 'crlf' / 'mixed' / 'none' for a byte sample.

    patch (the CRLF retry decision, P0004 §1.5) and stat (its `eol` field) must
    share ONE definition — two private variants produce the contradiction "patch
    retried because the file is crlf, but stat reports mixed".
    """
    crlf = sample.count(b"\r\n")
    lone_lf = sample.count(b"\n") - crlf
    if crlf and lone_lf:
        return "mixed"
    if crlf:
        return "crlf"
    if lone_lf:
        return "lf"
    return "none"


def _contains_only_lf_newline(text: str) -> bool:
    return "\n" in text and "\r\n" not in text


def _is_utf8_encoding(encoding: str) -> bool:
    """True for the UTF-8 family, where the continuation-byte rule below holds.

    Other multi-byte encodings (UTF-16 …) are left unadjusted — L0005 DEFERRED.
    """
    try:
        return codecs.lookup(encoding).name in ("utf-8", "utf-8-sig")
    except LookupError:
        return False


def _utf8_sequence_len(byte: int) -> int:
    """Bytes in the UTF-8 sequence this lead byte starts, or 0 if not a lead byte."""
    if byte < 0x80:
        return 1
    if 0xC0 <= byte < 0xE0:
        return 2
    if 0xE0 <= byte < 0xF0:
        return 3
    if 0xF0 <= byte < 0xF8:
        return 4
    return 0  # continuation byte (0x80–0xBF) or invalid lead


def _leading_continuation_len(raw: bytes) -> int:
    i = 0
    while i < len(raw) and 0x80 <= raw[i] < 0xC0:
        i += 1
    return i


def _trim_incomplete_trailing_sequence(raw: bytes) -> bytes:
    """Drop a multi-byte sequence the window cut in half, so the caller can
    re-read it whole from `offset + returned_bytes`."""
    for k in (1, 2, 3):
        if len(raw) < k:
            break
        seq_len = _utf8_sequence_len(raw[len(raw) - k])
        if seq_len > k:
            return raw[: len(raw) - k]
    return raw


def _exec_read(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    """0347 P0004 §7: byte window (`offset`/`length`) on top of the original read.

    `offset`/`length` absent → byte-identical to the previous behaviour, plus the
    three new report-only fields (`offset`/`returned_bytes`/`eof`).
    """
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)  # symlink/realpath escape (③ category)
    if not target.is_file():
        raise _OpError(404)
    size = target.stat().st_size
    max_bytes = body.get("max_bytes")
    length = body.get("length")
    offset = body.get("offset") or 0
    encoding = body.get("encoding") or "utf-8"

    window_start = min(offset, size)   # past EOF → empty window, not an error (§8)
    if offset == 0 and length is None and max_bytes is None:
        # Legacy call: the 413 guard still judges the WHOLE file, exactly as before.
        if size > _MAX_READ_BYTES:
            raise _OpError(413)
        window_size = size
    else:
        bounds = [v for v in (length, max_bytes) if v is not None]
        window_size = min(bounds) if bounds else size - window_start
        # P0004 §7.3: the guard now measures the effective WINDOW, so a 200MB file
        # can be walked in slices. Intended behaviour change — a max_bytes above
        # 100MB used to skip the guard entirely and now raises 413.
        if window_size > _MAX_READ_BYTES:
            raise _OpError(413)

    to_read = max(min(window_start + window_size, size) - window_start, 0)
    with open(target, "rb") as fh:
        if window_start:
            fh.seek(window_start)
        raw = fh.read(to_read)

    # Report the offset as requested (P0004 §8 echoes an out-of-range offset back);
    # only the character-boundary correction moves it.
    adj_start = offset
    if _is_utf8_encoding(encoding):
        if window_start > 0:
            skipped = _leading_continuation_len(raw)
            adj_start += skipped
            raw = raw[skipped:]
        # Only trim when more of the file follows. At EOF the trailing bytes are
        # all there is, and dropping them would leave eof=False forever — the
        # caller's walk would never terminate.
        if adj_start + len(raw) < size:
            raw = _trim_incomplete_trailing_sequence(raw)

    content = raw.decode(encoding, errors="replace")
    returned_bytes = len(raw)
    consumed = adj_start + returned_bytes
    return (
        {
            "path": body["path"],
            "content": content,
            "encoding": encoding,
            "size": size,
            "offset": adj_start,
            "returned_bytes": returned_bytes,
            "eof": consumed >= size,
            "truncated": consumed < size,
        },
        returned_bytes,
    )


def _atomic_write_bytes(target: Path, data: bytes, mode: Optional[int] = None) -> None:
    """Write via a sibling temp file + os.replace, so a crash mid-write can never
    leave the original half-written (0347 L0005 §2.5).

    The replacement is a brand-new file, so the original's permission bits are
    carried over explicitly — otherwise patching an executable script would
    silently drop its +x.
    """
    tmp = target.with_name(target.name + ".flowgate-patch.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass   # best-effort (no-op on Windows) — never fail the patch for this
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _exec_patch(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    """0347 P0004 §1 — exact-match partial edit.

    Line-numbered patching silently edits the wrong place once an earlier change
    shifts the file, and still answers 200. So: exact match only, 0 hits → 404,
    2+ hits without replace_all → 409, and nothing is written in either case.
    Untouched bytes are never rewritten, which is what keeps a CRLF file from
    turning into a whole-file diff the way full-overwrite `write` did.
    """
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)
    if not target.exists():
        raise _OpError(404, details={"reason": "file_not_found", "path": body["path"]})
    if target.is_dir():
        raise _OpError(404, details={"reason": "not_a_file", "path": body["path"]})
    if not target.is_file():
        raise _OpError(404, details={"reason": "file_not_found", "path": body["path"]})

    encoding = body.get("encoding") or "utf-8"
    replace_all = bool(body.get("replace_all"))
    old_string = body["old_string"]
    new_string = body["new_string"]   # ④ already guaranteed old != new

    original_mode = target.stat().st_mode
    with open(target, "rb") as fh:
        raw_bytes = fh.read()
    try:
        # strict, never errors="replace": a lenient decode would write U+FFFD back
        # over the untouched part of the file and corrupt the original (P0004 §12.1-5).
        text = raw_bytes.decode(encoding)
    except (UnicodeDecodeError, ValueError):
        raise _OpError(422, details={"reason": "not_text", "path": body["path"]})

    eol = _detect_dominant_eol(raw_bytes)
    match_count = text.count(old_string)
    eol_normalized = False

    # 2nd pass (P0004 §1.5): a CRLF file with an old_string copied as LF. Skipped
    # for 'mixed' files, where guessing which newline was meant is not safe.
    if match_count == 0 and eol == "crlf" and _contains_only_lf_newline(old_string):
        alt_old = old_string.replace("\n", "\r\n")
        alt_count = text.count(alt_old)
        if alt_count > 0:
            old_string = alt_old
            new_string = new_string.replace("\n", "\r\n")
            match_count = alt_count
            eol_normalized = True

    if match_count == 0:
        raise _OpError(
            404,
            details={"reason": "no_match", "match_count": 0, "path": body["path"]},
        )
    if match_count >= 2 and not replace_all:
        raise _OpError(
            409,
            details={
                "reason": "multiple_matches",
                "match_count": match_count,
                "path": body["path"],
            },
        )

    if replace_all:
        new_text = text.replace(old_string, new_string)
        replacements = match_count
    else:
        new_text = text.replace(old_string, new_string, 1)
        replacements = 1

    try:
        new_bytes = new_text.encode(encoding, errors="strict")
    except UnicodeError:
        # new_string cannot be represented in the file's encoding — same 422 the
        # write op returns for the identical situation.
        raise _OpError(422)
    if len(new_bytes) > _MAX_WRITE_BYTES:
        raise _OpError(413)

    # All-or-nothing: match, replace and encode all succeeded, so this is the one
    # and only touch of the disk (P0004 §1.4).
    _atomic_write_bytes(target, new_bytes, original_mode)

    return (
        {
            "path": body["path"],
            "replacements": replacements,
            "size_before": len(raw_bytes),
            "size_after": len(new_bytes),
            "bytes_written": len(new_bytes),
            "encoding": encoding,
            "eol": eol,
            "eol_normalized": eol_normalized,
        },
        len(new_bytes),
    )


def _exec_stat(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    """0347 P0004 §9 — existence/kind/size/mtime/EOL without the content.

    Absence answers 200 with exists=false, the one deliberate exception to the
    pipeline's "target missing → 404": the whole point of stat is to report "not
    there", and 404 would force every caller into exception handling and blur the
    line against a permission or path error. An *unsafe* path is still 422 —
    "unsafe path" is not "path that does not exist" (§9.2).
    """
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)

    absent = {
        "path": body["path"], "exists": False, "type": None,
        "size": None, "mtime": None, "eol": None, "binary": None,
    }
    if not target.exists():
        return absent, None

    try:
        st = target.stat()
    except OSError:
        return absent, None
    mtime_iso = datetime.fromtimestamp(st.st_mtime, _JST).isoformat(timespec="seconds")

    if target.is_dir():
        return (
            {
                "path": body["path"], "exists": True, "type": "dir",
                "size": None, "mtime": mtime_iso, "eol": None, "binary": None,
            },
            None,
        )
    if not target.is_file():
        return (
            {
                "path": body["path"], "exists": True, "type": "other",
                "size": None, "mtime": mtime_iso, "eol": None, "binary": None,
            },
            None,
        )

    with open(target, "rb") as fh:
        sample = fh.read(_STAT_SAMPLE_BYTES)
    binary = b"\x00" in sample
    return (
        {
            "path": body["path"],
            "exists": True,
            "type": "file",
            "size": st.st_size,
            "mtime": mtime_iso,
            "eol": None if binary else _detect_dominant_eol(sample),
            "binary": binary,
        },
        None,   # bytes_processed always NULL — stat does not return content
    )


def _exec_write(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)
    mode = body.get("mode") or "overwrite"
    encoding = body.get("encoding") or "utf-8"
    try:
        data = body["content"].encode(encoding, errors="strict")
    except UnicodeError:
        # content cannot be represented in the requested narrow encoding (e.g. ascii) → request validity error (④).
        # Surface as 422 to preserve the P0005 envelope + ⑥ history contract (prevents a bare 500 leak).
        raise _OpError(422)
    if len(data) > _MAX_WRITE_BYTES:
        raise _OpError(413)

    existed = target.exists()
    if mode == "create" and existed:
        raise _OpError(409)

    target.parent.mkdir(parents=True, exist_ok=True)
    file_mode = "ab" if mode == "append" else "wb"
    with open(target, file_mode) as fh:
        fh.write(data)

    created = not existed
    return (
        {
            "path": body["path"],
            "bytes_written": len(data),
            "created": created,
        },
        len(data),
    )


def _clear_readonly_and_retry(func, path, _exc_info) -> None:
    """Retry hook for ``shutil.rmtree`` and single deletes — clear the read-only bit and try again.

    0382 §3-2: object files git has just created are read-only, so ``os.remove`` raises
    PermissionError. Python's standard idiom is to grant permission and call again, and that
    alone removed the 21 files that "would never delete" in this incident.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _delete_file(target: Path) -> None:
    """Delete even when read-only; if still blocked, answer with 'retrying will not help'."""
    try:
        os.remove(target)
    except PermissionError:
        try:
            _clear_readonly_and_retry(os.remove, str(target), None)
        except OSError:
            raise _OpError(409, details={"reason": "path_locked"})


def _delete_tree(target: Path) -> int:
    """Delete a whole directory and return how many files were removed."""
    count = sum(1 for path in target.rglob("*") if path.is_file())
    failures: list[str] = []

    def _on_error(func, path, exc_info):
        try:
            _clear_readonly_and_retry(func, path, exc_info)
        except OSError:
            failures.append(str(path))

    # Python 3.12+ deprecates onerror in favour of onexc. The handler ignores the third argument.
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(target), onexc=_on_error)
    else:
        shutil.rmtree(str(target), onerror=_on_error)
    if failures:
        raise _OpError(409, details={"reason": "path_locked"})
    return count


def _exec_remove(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)
    # _validate_required guards HTTP input types, but the executing function itself also refuses
    # to promote the string "false" to true. A delete must be safe from tests and internal calls too.
    recursive = body.get("recursive", False) is True
    if target.is_dir():
        # 0382 NR0003 proposal 5-c (debris cleanup). With no way to delete a folder wholesale,
        # removing 261 files meant calling remove 261 times. Recursive delete is opened, but
        # **only for paths judged to be tool debris** — the same rule the screens and the
        # submission check use, so the mis-deletion risk narrows to one question: what counts as debris.
        if not recursive:
            raise _OpError(404)  # existing contract kept: a directory without recursive is a 404
        normalized = path_exclusion_rules.normalize_repo_path(body["path"])
        # An empty normalised result is the source root itself, and the root .git is recovery
        # data. Neither may ever be deleted recursively, whatever the debris rule says.
        if not normalized or normalized == ".git" or normalized.startswith(".git/"):
            raise _OpError(422, details={"reason": "not_tool_artifact", "path": body["path"]})
        if not path_exclusion_rules.is_excluded_path(normalized):
            raise _OpError(422, details={"reason": "not_tool_artifact", "path": body["path"]})
        removed = _delete_tree(target)
        return (
            {"path": body["path"], "removed": True, "recursive": True,
             "removed_file_count": removed},
            None,
        )
    if not target.is_file():
        raise _OpError(404)  # missing, or not a regular file (single-file removal, P0005 §4.4)
    _delete_file(target)
    return ({"path": body["path"], "removed": True}, None)


def _is_excluded(real: str, base_real: str) -> bool:
    """True when `real` sits inside an excluded directory *below* `base_real`.

    0279 T0005: the check is relative to the requested base, not to the source
    root, so pointing a scan AT `.venv` (path=".venv") still scans it — only
    incidental descent into one of these directories is skipped.
    """
    rel = os.path.relpath(real, base_real)
    if rel.startswith(".."):
        return False
    segments = rel.replace("\\", "/").split("/")[:-1]  # directory segments only
    return any(seg in _SCAN_EXCLUDE_DIRS for seg in segments)


def _iter_files(root: Path, base: Path, glob_filter: Optional[str]):
    """Yield (posix_relpath_from_root, abspath) for files under base, jailed to root."""
    root_real = os.path.realpath(str(root))
    base_real = os.path.realpath(str(base))
    candidates = base.glob(glob_filter) if glob_filter else base.rglob("*")
    for cand in candidates:
        real = os.path.realpath(str(cand))
        if not _under_root(real, root_real):
            continue  # symlink escape — skip
        if _is_excluded(real, base_real):
            continue  # dependency/VCS directory — not project source (0279 T0005)
        if not os.path.isfile(real):
            continue
        rel = os.path.relpath(real, root_real).replace("\\", "/")
        yield rel, real


def _exec_grep(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    base = resolve_in_root(root, body.get("path") or "")
    if base is None:
        raise _OpError(422)
    if not base.is_dir():
        # NR0003 §2: pathlib's glob()/rglob() silently yield nothing for a file
        # or a nonexistent path, so a wrong `path` used to look identical to a
        # genuine zero-match search (both `ok:true, total:0`). Mirror _exec_read's
        # precedent (a bad target collapses to 404, whether missing or wrong type).
        raise _OpError(404)
    flags = re.IGNORECASE if body.get("ignore_case") else 0
    pattern = re.compile(body["pattern"], flags)
    max_results = body.get("max_results")
    glob_filter = body.get("glob")

    matches: list[dict] = []
    total = 0
    truncated = False
    # 0279 T0005 (NR0003 cause 2): scanning used to continue after max_results was
    # reached so that `total` was an exact full count. That made a max_results=1
    # call walk the entire source root — the measured 40s freeze was exactly such
    # a call, and the cost grew linearly as the repo grew. Stop at the first file
    # that fills the quota instead.
    #
    # `total` therefore changes meaning: it is exact when truncated is False, and a
    # LOWER BOUND (matches counted before the scan stopped) when truncated is True.
    # A truncated response already told the caller the result set was incomplete, so
    # no caller could have been relying on an exact count in that branch.
    for rel, real in _iter_files(root, base, glob_filter):
        try:
            if os.path.getsize(real) > _GREP_FILE_SKIP_BYTES:
                continue
            with open(real, "rb") as fh:
                blob = fh.read()
            if b"\x00" in blob:
                continue  # binary — skip
            text = blob.decode("utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                total += 1
                if max_results is not None and len(matches) >= max_results:
                    truncated = True
                else:
                    matches.append({"file": rel, "line": lineno, "text": line})
        if truncated:
            # Finish the current file (its matches are already counted), then stop
            # walking the tree — the quota is full and every further file is pure cost.
            break
    return ({"matches": matches, "total": total, "truncated": truncated}, None)


def _exec_glob(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    base = resolve_in_root(root, body.get("path") or "")
    if base is None:
        raise _OpError(422)
    if not base.is_dir():
        # NR0003 §2/§3 — same silent-zero defect as _exec_grep, same fix.
        raise _OpError(404)
    root_real = os.path.realpath(str(root))
    base_real = os.path.realpath(str(base))
    paths: list[str] = []
    for cand in sorted(base.glob(body["pattern"])):
        real = os.path.realpath(str(cand))
        if not _under_root(real, root_real):
            continue
        if _is_excluded(real, base_real):
            continue  # dependency/VCS directory — not project source (0279 T0005)
        rel = os.path.relpath(real, root_real).replace("\\", "/")
        paths.append(rel)
    return ({"paths": paths, "total": len(paths)}, None)


# ── Envelope / logging / continuation ─────────────────────────────────────────

def _envelope(
    ok: bool,
    op: str,
    *,
    extra: Optional[dict] = None,
    error: Optional[dict] = None,
    continuation: Optional[dict] = None,
) -> dict:
    """P0005 §3.3 common response envelope."""
    env: dict = {"ok": ok, "op": op, "server_ts": now_iso()}
    if error is not None:
        env["error"] = error
    if continuation is not None:
        env["continuation"] = continuation
    if extra:
        env.update(extra)
    return env


# Generic per-status envelope text (P0005 §6), one set per worker display locale
# (0355 T0015: this dict used to carry only Korean text regardless of the worker's
# requested locale — the 7 status codes below were always rendered in Korean even
# when the calling worker's continuation_locale was ja/en).
_ERROR_MESSAGES = {
    "ko": {
        401: "토큰이 없거나 유효하지 않습니다.",
        403: "해당 작업을 수행할 권한이 없습니다.",
        404: "대상 경로를 찾을 수 없습니다.",
        409: "대상 파일이 이미 존재합니다 (mode=create).",
        413: "요청 또는 대상 크기가 허용치를 초과했습니다.",
        422: "요청 형식이 올바르지 않습니다.",
        503: "서버가 일시적으로 요청을 처리할 수 없습니다.",
    },
    "en": {
        401: "The token is missing or invalid.",
        403: "You do not have permission to perform this operation.",
        404: "The target path could not be found.",
        409: "The target file already exists (mode=create).",
        413: "The request or target size exceeds the allowed limit.",
        422: "The request format is invalid.",
        503: "The server is temporarily unable to process the request.",
    },
    "ja": {
        401: "トークンが存在しないか無効です。",
        403: "この操作を行う権限がありません。",
        404: "対象のパスが見つかりません。",
        409: "対象のファイルは既に存在します (mode=create)。",
        413: "リクエストまたは対象のサイズが許容範囲を超えています。",
        422: "リクエストの形式が正しくありません。",
        503: "サーバーが一時的にリクエストを処理できません。",
    },
}


_CUSTOM_ERROR_MESSAGES = {
    # 0382 §3-3: permission/lock failures went out as a 503 "the server cannot handle the
    # request temporarily". The AI believed "temporarily", knocked three more times, and it was
    # never going to work. What a retry can fix and what it cannot must give different answers.
    "path_locked": {
        "ko": "읽기 전용이거나 다른 프로세스가 잡고 있어 지울 수 없습니다. 재시도해도 같은 결과입니다 — 잠금을 푼 뒤 다시 요청하세요.",
        "en": "The path is read-only or held by another process and cannot be deleted. Retrying will not help — release the lock first.",
        "ja": "読み取り専用か他のプロセスが掴んでいるため削除できません。再試行しても同じ結果です — ロックを解除してから再度要求してください。",
    },
    "not_tool_artifact": {
        "ko": "재귀 삭제는 도구가 남긴 흔적에만 허용됩니다. '{path}' 는 작업 산출물로 판정되므로 파일 단위로 지우세요.",
        "en": "Recursive delete is allowed only for tool-generated artifacts. '{path}' is judged real work — delete it file by file.",
        "ja": "再帰削除はツールが残した痕跡にのみ許可されます。'{path}' は作業成果物と判定されるため、ファイル単位で削除してください。",
    },
    "no_op_edit": {
        "ko": "old_string과 new_string이 동일합니다.",
        "en": "old_string and new_string must be different.",
        "ja": "old_string と new_string は異なる値にしてください。",
    },
    "no_match": {
        "ko": "old_string과 일치하는 내용을 찾지 못했습니다 (0건).",
        "en": "No content matching old_string was found (0 matches).",
        "ja": "old_string に一致する内容が見つかりませんでした (0件)。",
    },
    "multiple_matches": {
        "ko": "old_string이 {match_count}곳에서 일치합니다. 앞뒤 문맥을 포함해 유일하게 만들거나 replace_all=true를 지정하세요.",
        "en": "old_string matches {match_count} locations. Include surrounding context to make it unique, or set replace_all=true.",
        "ja": "old_string は {match_count} 箇所に一致します。前後の文脈を含めて一意にするか、replace_all=true を指定してください。",
    },
}

_CONTINUATION_MESSAGES = {
    "ko": {
        "with_report": "작업을 완료했습니다. 변경 내용을 레포트({report_doc_id})에 이어 정리해 제출을 계속해 주세요.",
        "without_report": "작업을 완료했습니다. 변경 내용을 작업 레포트(TR)로 정리해 제출을 이어가 주세요.",
    },
    "en": {
        "with_report": "The operation is complete. Continue by documenting and submitting the changes in report {report_doc_id}.",
        "without_report": "The operation is complete. Continue by documenting and submitting the changes in a task report (TR).",
    },
    "ja": {
        "with_report": "作業が完了しました。変更内容をレポート({report_doc_id})にまとめ、提出を続けてください。",
        "without_report": "作業が完了しました。変更内容を作業レポート(TR)にまとめ、提出を続けてください。",
    },
}


def _locale_for_grant(grant: Optional[dict]) -> str:
    """Worker display locale for a resolved grant, or the ko fallback.

    Mirrors mention_service's continuation_locale convention: a worker grant is
    backed by an inbox token carrying `continuation_locale` (set from the
    unmanned chain's chosen locale, group 0099 B0001), so the error envelope
    should match the same language the worker's mentions are already in. A
    grant with no backing token (legacy / non-worker grant, or auth failure
    where `grant` is None) has no locale signal, so it falls back to ko —
    unchanged prior behavior.
    """
    if grant is None:
        return template_provision.FALLBACK_LOCALE
    token_rec = _worker_token_for_grant(grant)
    if not token_rec:
        return template_provision.FALLBACK_LOCALE
    return template_provision.normalize_locale(token_rec.get("continuation_locale"))


def _fail_envelope(op: str, status: int, locale: str = "ko") -> dict:
    code = ERROR_CODE_BY_STATUS[status]
    messages = _ERROR_MESSAGES.get(locale) or _ERROR_MESSAGES[template_provision.FALLBACK_LOCALE]
    return _envelope(
        False,
        op,
        error={"code": code, "message": messages.get(status, code)},
    )


def _op_error_message(exc: _OpError, locale: str) -> str:
    code = ERROR_CODE_BY_STATUS[exc.status]
    messages = _ERROR_MESSAGES.get(locale) or _ERROR_MESSAGES[template_provision.FALLBACK_LOCALE]
    reason = (exc.details or {}).get("reason")
    custom = _CUSTOM_ERROR_MESSAGES.get(reason, {}).get(locale)
    if custom:
        return custom.format(**(exc.details or {}))
    if exc.message and (locale == "ko" or not re.search(r"[가-힣]", exc.message)):
        return exc.message
    return messages.get(exc.status, code)


def _log_targets(op: str, body: dict) -> tuple[Optional[str], Optional[str]]:
    """(target_path, target_pattern) for op_log (DB0007 §6)."""
    path = body.get("path") if isinstance(body.get("path"), str) else None
    if op == "grep":
        pattern = body.get("glob") if isinstance(body.get("glob"), str) else None
    elif op == "glob":
        pattern = body.get("pattern") if isinstance(body.get("pattern"), str) else None
    else:
        pattern = None
    return path, pattern


def _log(
    grant: dict,
    op: str,
    target_path: Optional[str],
    target_pattern: Optional[str],
    *,
    status: int,
    bytes_processed: Optional[int] = None,
) -> None:
    """⑥ — record one op_log row (every attempt after authentication passes). Never raises."""
    if status == 200:
        result, error_code = "success", None
    else:
        result = RESULT_BY_STATUS[status]
        error_code = ERROR_CODE_BY_STATUS[status]
    try:
        db_oplog.insert(
            grant_id=grant["grant_id"],
            op=op,
            result=result,
            error_code=error_code,
            target_path=target_path,
            target_pattern=target_pattern,
            bytes_processed=bytes_processed,
        )
    except Exception:
        # A history-write failure must not block the user response (best-effort, L0006 §5).
        pass


def _continuation(grant: dict, locale: str = "ko") -> dict:
    """⑦ — locale-safe completion ment for mutation success (P0005 §5 / L0006 §6.2)."""
    report_doc_id = grant.get("report_doc_id")
    copy = _CONTINUATION_MESSAGES.get(locale) or _CONTINUATION_MESSAGES["ko"]
    key = "with_report" if report_doc_id else "without_report"
    ment = copy[key].format(report_doc_id=report_doc_id)
    return {"ment": ment, "report_doc_id": report_doc_id, "next_action": "write_report"}


# ── Pipeline entry ─────────────────────────────────────────────────────────────

def handle(operation: str, raw_token: Optional[str], body: Optional[dict]) -> tuple[int, dict]:
    """Run the full pipeline. Returns (http_status, response_envelope).

    Fail-fast order (L0006 §7): 401 ▶ 403 ▶ 422 ▶ 404 ▶ 409 ▶ 413 ▶ 503.
    """
    op = operation
    body = body if isinstance(body, dict) else {}

    # ① Authentication — on failure the subject is unidentified, so no history is logged (L0006 §3.1).
    grant = _authenticate(raw_token)
    if grant is None:
        return 401, _fail_envelope(op, 401)

    locale = _locale_for_grant(grant)

    # Unknown operation name: no scope mapping + cannot be stored in the op enum, so not logged → 422.
    if op not in OP_SCOPE:
        return 422, _fail_envelope(op, 422, locale)

    log_path, log_pattern = _log_targets(op, body)
    try:
        # ② Permission scope
        if OP_SCOPE[op] not in db_grants.get_scopes(grant["grant_id"]):
            raise _OpError(403)
        # ③ Path safety (existing path-like values)
        _validate_paths(op, body)
        # ④ Request validity (required fields)
        _validate_required(op, body)
        # Resolve the target source root. write/remove must not silently fall
        # back to the base checkout when the group worktree is missing (0205
        # §2.3) — they use the mutation gate, which raises a 409 with the cause.
        if op in _MUTATING_OPS:
            root = _resolve_root_for_mutation(grant, op)
        else:
            root = _resolve_src_root(grant, op)
        if root is None:
            raise _OpError(503)
        # ⑤ Execute the operation
        extra, nbytes = _execute(op, body, root)
    except _OpError as exc:
        _log(grant, op, log_path, log_pattern, status=exc.status)
        if exc.details is not None or exc.message is not None:
            code = ERROR_CODE_BY_STATUS[exc.status]
            error = {
                "code": code,
                "message": _op_error_message(exc, locale),
            }
            if exc.details is not None:
                error["details"] = exc.details
            return exc.status, _envelope(False, op, error=error)
        return exc.status, _fail_envelope(op, exc.status, locale)
    except OSError:
        # Unexpected I/O failure → 503 unavailable (logged to history).
        _log(grant, op, log_path, log_pattern, status=503)
        return 503, _fail_envelope(op, 503, locale)
    except Exception:
        # Trap every other unexpected exception in the envelope so it cannot leak to the
        # router as a bare 500 — since the attempt passed authentication, ⑥ history is also
        # recorded ('every response = P0005 envelope' contract).
        _log(grant, op, log_path, log_pattern, status=503)
        return 503, _fail_envelope(op, 503, locale)

    # ⑥ Success history
    _log(grant, op, log_path, log_pattern, status=200, bytes_processed=nbytes)
    # 0192 T0005 §2-d: a worker's source mutation (write/remove) previously emitted
    # NO SSE, so an operator watching the file explorer saw the AI's edits only when
    # some unrelated document event happened to fire — the "changes don't show up
    # right away" complaint. Broadcast file_explorer_refresh on a successful mutation
    # so the tree, change list and '>' markers refresh live. Best-effort; a delivery
    # failure must never turn a successful op into an error.
    if op in _MUTATING_OPS:
        _emit_explorer_refresh(grant, op)
    # ⑦ Completion ment — only on successful state-changing operations (L0006 §6.1).
    continuation = _continuation(grant, locale) if op in _MUTATING_OPS else None
    return 200, _envelope(True, op, extra=extra, continuation=continuation)


def _emit_explorer_refresh(grant: dict, op: str) -> None:
    """Best-effort file_explorer_refresh broadcast after a worker source mutation
    (0192 T0005 §2-d). Scoped to the worker's project (and group when known) so
    the operator's explorer re-fetches the tree / change list / dirty markers."""
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )
        from modules.flow_gate.api.v1.events.event_types import EventType

        broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.FILE_EXPLORER_REFRESH,
            payload={"operation": op, "source": "remote_worker"},
            audience="*",
            project=grant.get("project"),
            group_id=grant.get("group_id"),
            doc_id=None,
        ))
    except Exception:
        pass
