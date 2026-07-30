"""AI invoke engine (flowgate.default.0187 D0004 / P0005 / L0006).

First real consumer of the 0164 AI-provider settings: starts an AI run for a
document, walks the default provider chain (list order = fallback order, startup/
transport failures only), watches it with the process_runner primitives
(process-group spawn, tree kill, timeout), and judges success by an oracle —
"did the work actually land?" — never by exit codes. Exit code / last (dying)
message are recorded as auxiliary observations only, and work-landed vs
message-receipt are independent columns (a killed run keeps what it registered).

Which oracle depends on what the run's token can produce (0259 B0001): a `new`
token registers documents, so it is judged by document-reach ("did documents land
in the group past the baseline seq"); every other scope is judged by the row IT is
allowed to write (`_SCOPE_PROBES`), because the inbox forbids it from creating a
document at all — judging those by document-reach made success unreachable.

Run state lives in an in-memory registry for the server's lifetime (history
persistence is DEFERRED per D0004); a restart loses in-flight runs, which the
status API surfaces as 404 run_not_found.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import HTTPException

from modules.flow_gate.db import conversation_turns as db_conversation_turns
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import git_service, invoke_mention_service, process_runner, token_service
from modules.flow_gate.services.git_service import GitServiceError
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.storage import paths as storage_paths

logger = logging.getLogger(__name__)

# ── Parameters (L0006 §1) ─────────────────────────────────────────────────────
RUN_TIMEOUT_BASE_SEC = 3600      # per target document
RUN_TIMEOUT_CAP_SEC = 14400      # run total = min(BASE × docs_target, CAP)
FAST_FAIL_WINDOW_SEC = 15        # nonzero exit + 0 docs inside this window ⇒ startup failure
SCRATCH_RETENTION_DAYS = 7       # failed-run scratch retention
LAST_MESSAGE_MAX_BYTES = 16384   # keep the tail, truncate the front
OUTPUT_TAIL_BYTES = 8192         # stdout/stderr auxiliary tails
API_MAX_TURNS_PER_DOC = 4        # API agent loop cap = docs_target × 4
API_MAX_TOOL_NUDGES = 2          # retry when the model claims completion without using the tool
ORACLE_SETTLE_SEC = 3            # wait before judging (late-commit slack)
CONCURRENT_RUNS_PER_GROUP = 1

ANTHROPIC_VERSION = "2023-06-01"
API_CALL_MAX_TIMEOUT_SEC = 600   # single model-call ceiling inside the run deadline
API_MAX_TOKENS = 8192

_REGISTER_TOOL_NAME = "register_document"
_REGISTER_TOOL_DESC = (
    "Register a completed document to FlowGate. Call this once per finished "
    "document with the full markdown body. The tool result tells you whether "
    "the chain continues (next instructions) or is complete."
)
_REGISTER_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Document title"},
        "content": {"type": "string", "description": "Full markdown content of the document"},
        "doc_type": {"type": "string", "description": "Document type code, e.g. NR, D, P, L, T, TR"},
    },
    "required": ["title", "content", "doc_type"],
}

_DECIDE_TOOL_NAME = "decide_workflow"
_DECIDE_TOOL_DESC = (
    "Save the workflow decision for the target requirement. Choose the document class "
    "and the ordered, non-empty sequence required by the workflow-decision instruction."
)
_DECIDE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_class": {"type": "string", "description": "Workflow document class"},
        "sequence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "type": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["id", "type", "label"],
            },
            "minItems": 1,
        },
    },
    "required": ["doc_class", "sequence"],
}

_RESOLVE_TOOL_NAME = "resolve_git_conflict"
_RESOLVE_TOOL_DESC = (
    "Submit complete resolved file contents for the bound git merge conflict session. "
    "All conflict markers must be removed and complete must be true when every file is resolved."
)
_RESOLVE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        "complete": {"type": "boolean"},
    },
    "required": ["files", "complete"],
}

# ── Registry ─────────────────────────────────────────────────────────────────

_runs: dict[str, dict] = {}
_runs_lock = threading.Lock()
_run_counter = 0

# Per-group resume serialization (0252 L0009 §2.4 step 1): the FIRST line of defense
# against double resume. The atomic paused-row delete (delete_and_return) is the second.
_group_resume_locks: dict[str, threading.Lock] = {}
_group_resume_locks_guard = threading.Lock()

# 0317 TR0011 (Q153 opt-1): per-hop worker RE-SPAWN for unmanned continuous chains.
# The reject cause was that an uninterrupted continuous chain spawns ONE worker (one
# provider process) that self-continues through every hop via next_token, so every
# document was written by hop-1's provider (all three "Anthropic Claude Sonnet 5"). The
# fix automates the existing resume re-spawn: at each step boundary the inbox self-chain
# withholds next_token and records the next hop here; when the finished hop's worker
# settles, the engine re-enters start_run for the next hop, which re-resolves THAT hop's
# provider (_resolve_continuation_hop_provider / the per-step override map). Session-
# scoped, in-memory, keyed by group_id, cleared at chain end — never persisted (T0010 o1).
_auto_resume: dict[str, dict] = {}
_auto_resume_lock = threading.Lock()


def _group_resume_lock(group_id: str) -> threading.Lock:
    with _group_resume_locks_guard:
        lock = _group_resume_locks.get(group_id)
        if lock is None:
            lock = _group_resume_locks[group_id] = threading.Lock()
        return lock


def _http_error(status_code: int, code: str, message: str, **payload) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **payload})


def _is_group_worktree(project_id: str, group_id: str, root: Optional[Path]) -> bool:
    """Is *root* the group's OWN worktree, as opposed to the base project tree?

    ``resolve_project_src_root`` is fallback-first by design: when the worktree is
    missing it silently hands back the ordinary project-branch folder (main). The
    return value alone therefore cannot answer "did we get the worktree?", so this
    compares it against the path the group's ledger branch would occupy.
    """
    if root is None:
        return False
    try:
        state = db_git.get_state(group_id) or {}
        branch = (state.get("branch") or "").strip()
        project_name = git_service._project_name(project_id)
        if not branch or not project_name:
            return False
        expected = git_service.src_root(project_name, branch)
        return root.resolve() == expected.resolve()
    except Exception:  # noqa: BLE001 — an unanswerable comparison is a "no"
        return False


def _require_group_worktree(project_id: str, module: str, group_id: str, branch: str) -> None:
    """Refuse to launch a run that would execute in the base tree (0299 R0001).

    This is the root cause R0001 describes: "간혹 AI들이 부여받은 브랜치에 안하고
    main브랜치에 작업할 때가 있다". The remote CRUD endpoints have been gated since
    0205 (remote_tool_service._resolve_root_for_mutation), but the invoked worker's
    *cwd* was not — it came from the fallback-first resolver, so a group whose
    worktree was missing got a CLI agent pointed straight at the base checkout, free
    to edit files there with its own tools. TR 작업범위 검증 (0299 D0004) catches that
    afterwards, at report time; this closes it at the front, before any work happens.

    Same shape as the remote-write gate on purpose: one synchronous ensure_worktree
    self-heal, then a 409 carrying the blocking cause. Non-integrated projects and
    group-less runs are untouched — they have no worktree to demand.
    """
    if not group_id or not project_id:
        return
    try:
        cfg = db_git.get_config(project_id)
    except Exception:  # noqa: BLE001 — a config lookup failure must not block a run
        return
    if cfg is None or not cfg.get("enabled"):
        return  # non-integrated project: the base tree IS the source of truth

    root = storage_paths.resolve_project_src_root(project_id, branch, group_id=group_id)
    if _is_group_worktree(project_id, group_id, root):
        return
    try:
        if git_service.ensure_worktree(
            project_id, module or "default", group_id, trigger="ai_invoke_retry"
        ) == "ok":
            root = storage_paths.resolve_project_src_root(project_id, branch, group_id=group_id)
            if _is_group_worktree(project_id, group_id, root):
                return
    except Exception:  # noqa: BLE001 — ensure_worktree never raises, but be certain
        logger.warning("ensure_worktree retry failed for group %s", group_id, exc_info=True)

    # Still not there. Report WHY — "worktree unavailable" with no cause is what makes
    # this class of incident unfixable after the fact (0280 NR0003 §4-B).
    try:
        state = db_git.get_state(group_id) or {}
        provision_error = state.get("provision_error")
        session = git_service.open_merge_session_of_project(project_id)
    except Exception:  # noqa: BLE001
        provision_error, session = None, None
    if session is not None:
        cause = "merge_conflict_open"
    elif provision_error:
        cause = "provision_failed"
    else:
        cause = "worktree_missing"
    logger.warning(
        "ai_invoke blocked for group %s — no group worktree (cause=%s, resolved=%s)",
        group_id, cause, root,
    )
    raise _http_error(
        409, "worktree_unavailable",
        f"이 그룹의 작업 폴더(워크트리)를 확인할 수 없어 AI 실행을 시작하지 않습니다 "
        f"(원인: {cause}). 워크트리 없이 실행하면 작업이 원본 체크아웃(main)에 "
        f"남습니다. 그룹 Git 상태를 복구한 뒤 다시 실행하십시오.",
        group_id=group_id, cause=cause, provision_error=provision_error,
    )


def _next_run_id() -> str:
    global _run_counter
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    with _runs_lock:
        _run_counter += 1
        return f"aiv_{date_str}_{_run_counter:06d}"


def _active_run_for_group(group_id: str) -> Optional[dict]:
    with _runs_lock:
        for run in _runs.values():
            if run["group_id"] == group_id and run["status"] != "finished":
                return run
    return None


def get_run_record(run_id: str) -> Optional[dict]:
    with _runs_lock:
        return _runs.get(run_id)


def get_active_status(group_id: str) -> dict:
    """Return the live run for a group, if any, without exposing token/process state."""
    run = _active_run_for_group(group_id)
    if run is None:
        return {"ok": True, "active": False, "group_id": group_id}
    return {
        **get_status(run["run_id"]),
        "active": True,
        "group_id": group_id,
        "doc_ref": run["doc_ref"],
    }


def _continuation_docs_target(
    doc_ref: str,
    target_item_seq: Optional[int],
    *,
    pending_only: bool = True,
    continuation_instruction_mode: Optional[str] = None,
) -> Optional[int]:
    """docs_target in the workflow item_seq coordinate system (0226 B0001 / NR0003 §5-1).

    ``continuation_target_seq`` lives in the workflow-sequence item_seq space, which is
    unrelated to the group document seq space (item_seq turns sparse after
    edit_workflow_pending renumbers the pending tail past max_item_seq). The former
    ``target - get_group_max_seq()`` subtraction mixed the two spaces, yielding
    arbitrary targets (the reported 0/9 and 4/3). Count instead the sequence items up
    to the target that will land as worker-visible documents. In ``auto_approved``, N/T
    instruction heads are server-created drafts and remain excluded; in ``ai_direct``
    they are independent worker documents and are counted (0353 B0001 / NR0003 §8).

    ``pending_only=True`` counts only unrealized slots (start-of-run admission).
    The to-end resolution paths pass False: the whole freshly-decided sequence is the
    run's scope regardless of what has been realized by the time of the query.
    ``target_item_seq=None`` means "no upper bound" (to-end).
    Returns None when the doc has no decided workflow sequence.
    """
    from modules.flow_gate.services.workflow_decision_service import (
        CONTINUATION_INSTRUCTION_AUTO_APPROVED,
        INSTRUCTION_AUTO_TYPES,
        normalize_continuation_instruction_mode,
    )

    instruction_mode = normalize_continuation_instruction_mode(continuation_instruction_mode)
    seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
    if seq is None:
        return None
    count = 0
    for item in db_wfseq.get_sequence_items(seq["id"]) or []:
        item_seq = item.get("item_seq")
        if (
            target_item_seq is not None
            and item_seq is not None
            and int(item_seq) > int(target_item_seq)
        ):
            continue
        if pending_only and item.get("result_doc_id") is not None:
            continue
        if (
            instruction_mode == CONTINUATION_INSTRUCTION_AUTO_APPROVED
            and (item.get("type") or "").upper() in INSTRUCTION_AUTO_TYPES
        ):
            continue
        count += 1
    return count


# ── Scratch lifecycle (L0006 §2.7) ───────────────────────────────────────────

def _sanitize_project_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", name) or "_"


def _project_scratch_root(project_id: str) -> Path:
    project = db_projects.get_by_id(project_id)
    project_name = project["project_name"] if project else project_id
    return storage_paths.get_storage_root(project_id, create=True) / "scratch" / _sanitize_project_name(project_name)


def _create_scratch(project_id: str, run_id: str) -> Path:
    scratch = _project_scratch_root(project_id) / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def _cleanup_retained_scratches(project_id: str) -> None:
    """Best-effort: drop retained scratches older than SCRATCH_RETENTION_DAYS."""
    try:
        root = _project_scratch_root(project_id)
        if not root.is_dir():
            return
        cutoff = time.time() - SCRATCH_RETENTION_DAYS * 86400
        for child in root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except Exception:
                logger.warning("scratch retention sweep failed for %s", child, exc_info=True)
    except Exception:
        logger.warning("scratch retention sweep failed for project %s", project_id, exc_info=True)


# ── Source-spill check (L0006 §2.8) ──────────────────────────────────────────

def _git_status_paths(source_root: Optional[Path]) -> Optional[set[str]]:
    """Path set from `git status --porcelain`; None = unknown (git absent/failed)."""
    if source_root is None or not source_root.is_dir():
        return None
    try:
        timed_out, exit_code, output = process_runner.run_command(
            "git status --porcelain", source_root, 30, None
        )
        if timed_out or exit_code != 0:
            return None
        return {line[3:].strip() for line in output.splitlines() if line.strip()}
    except Exception:
        return None


# ── Default completion oracle per token scope (0259 B0001) ───────────────────
#
# Only a `new`-scope token can register a document, so only a `new` run may be judged by
# the document-reach oracle. The inbox rejects `action:'new'` from any other token
# outright (inbox_routes `_handle_new`/`_handle_edit`/`_handle_review` scope guards), so
# "docs_reached >= docs_target=1" was an UNREACHABLE success condition for every other
# single-mode scope — review/edit/rework/vr_correction/chat runs settled 'none' no matter
# how well the worker did. Each scope is judged by the row its own token may write.
#
# 0248 added `completion_oracle` for the same defect but left it opt-in per call site, and
# the very call sites it cited were never migrated. The default now lives in the engine and
# is keyed by the scope, so a scope that registers no judge cannot silently inherit the
# document oracle; `completion_oracle` stays as the per-call override.


def _probe_conversation_head(doc_id: str) -> int:
    """Highest stored turn seq for an append-only chat run."""
    return db_conversation_turns.current_head_seq(doc_id)

def _probe_doc_revision(doc_id: str) -> int:
    """Revisions of the bound document. `_handle_edit` does `revision_no = revision_no + 1`."""
    return int((db_docs.get_by_id(doc_id) or {}).get("revision_no") or 0)


def _probe_doc_reviews(doc_id: str) -> int:
    """Review rows on the bound document. `_handle_review` INSERTs one child row per review."""
    return len(db_reviews.list_by_doc(doc_id) or [])


def _probe_test_runs(doc_id: str) -> int:
    """Test-run rows on the bound document (0268 B0001).

    A `test_run` token may only POST /documents/test-run, which INSERTs one run row for the
    TS it is bound to — it can never register a document, so the document oracle would make
    success unreachable here exactly as 0259 B0001 described for edit/review.
    """
    return len(db_test_runs.list_by_doc(doc_id) or [])


def _probe_sequence_max_item(doc_id: str) -> int:
    """Highest item_seq in the bound document's workflow sequence (0268 B0001).

    A `workflow_sequence_edit` worker calls PATCH /workflow/sequence, which registers no
    document — so, like edit/review, the document oracle could never credit it. The probe
    is the sequence's max item_seq because `edit_workflow_pending` deletes the pending tail
    and re-inserts at `max_item_seq + 1`: a plain count could FALL across a valid shrink,
    but the max is strictly monotonic across any edit that inserts.

    Known limitation, deliberate: shrinking a sequence to locked-steps-only inserts nothing,
    so that one edit settles 'none'. A false negative on a rare edit is the safer error here
    than the false POSITIVE the alternative gives — with no probe at all, docs_target 0 makes
    `docs_reached >= docs_target` trivially true and a worker that did NOTHING reports
    'complete', which is the "verdict renamed instead of judged" failure 0259 B0001 fixed.
    """
    try:
        seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    except Exception:  # noqa: BLE001
        return 0
    if seq is None:
        return 0
    return int(db_wfseq.get_max_item_seq(seq["id"]) or 0)


# Keyed by TOKEN scope — the value `start_run` actually receives. Chat is no longer
# remapped to edit: its append-only endpoint advances the conversation head without
# revising the document row, so it needs its own completion probe.
_SCOPE_PROBES: dict[str, Callable[[str], int]] = {
    "chat": _probe_conversation_head,
    "edit": _probe_doc_revision,
    "review": _probe_doc_reviews,
    "test_run": _probe_test_runs,
    "workflow_sequence_edit": _probe_sequence_max_item,
}


def _oracle_doc_id(token_id: Optional[str], fallback: str) -> str:
    """The document the run's TOKEN binds to — the only one its worker may write.

    Not the run's own doc_ref: the two differ on the legacy Q&A follow-up, which starts the
    run on the Q document while `qa_service.issue_followup_token` binds the token to the
    parent work item. The inbox honours the token (`token_rec['doc_ref'] != doc_id` ⇒ 403),
    so a judge that watched the run's doc_ref would watch a document the worker cannot touch.
    """
    if not token_id:
        return fallback
    try:
        token = db_tokens.get_by_id(token_id)
    except Exception:
        logger.warning("ai-invoke token lookup failed for %s", token_id, exc_info=True)
        return fallback
    return (token or {}).get("doc_ref") or fallback


def _probe(probe: Callable[[str], int], doc_id: str) -> Optional[int]:
    try:
        return probe(doc_id)
    except Exception:
        logger.warning("ai-invoke scope probe failed for %s", doc_id, exc_info=True)
        return None


def _scope_oracle(action_scope: str, token_id: Optional[str], doc_ref: str) -> Optional[Callable[[], bool]]:
    """The scope's default "did the work land?" predicate, or None to keep the document oracle.

    Returning None here means `new` (and any future document-producing scope): those are
    judged by documents, which is what the document oracle is for.
    """
    probe = _SCOPE_PROBES.get(action_scope)
    if probe is None:
        return None
    doc_id = _oracle_doc_id(token_id, doc_ref)
    # Baseline BEFORE the worker starts, so the oracle only credits this run's work.
    baseline = _probe(probe, doc_id)

    def _oracle() -> bool:
        current = _probe(probe, doc_id)
        # An unresolvable baseline/probe cannot confirm the work landed. This is not the
        # old unreachable case — a missing target means the worker had nothing it could
        # write, and the inbox would have refused it too.
        return baseline is not None and current is not None and current > baseline

    return _oracle


def _uses_scope_oracle(action_scope: str, mode: str, completion_oracle: Optional[Callable]) -> bool:
    """mode='single' only: a continuous run's scope is new/workflow_decide, and its
    docs_target is derived from the sequence's pending worker items, which do make
    documents — the document oracle can see those, so it was never wrong for them."""
    return completion_oracle is None and mode == "single" and action_scope in _SCOPE_PROBES


# ── Start (L0006 §2.1) ───────────────────────────────────────────────────────

def list_runtime_providers(project_id: str) -> dict:
    """Safe effective-provider view for ordinary document readers."""
    effective = ai_settings_service.resolve_effective(project_id)
    return {
        "ok": True,
        "project": project_id,
        "providers": [_provider_brief(provider) for provider in effective.get("providers") or []],
        "default_provider_id": effective.get("default_provider_id"),
    }


def resolve_pinned_provider_name(project_id: str, provider_id: Optional[str]) -> Optional[str]:
    """The provider name a mention may claim, or None when it must not claim one.

    0293 NR0004 발견 5: the worker mention is built BEFORE the run picks a provider, and
    `_worker` may fall through the whole chain. Naming chain[0] in the mention would
    therefore be a guess that reads like a server-confirmed fact. A name is only
    returned when the effective chain collapses to exactly ONE provider — an explicit
    UI pin (start_run's `chain = [selected]`), or a project with a single enabled
    provider — because only then is fallback structurally impossible.

    발견 4: the value is the provider's display NAME, not a model id. `api_model` exists
    for exec_type='api' only; a CLI provider's model is buried in cli_command flags that
    differ per kind, so there is no model string the server reliably knows. The name is
    user-authored, unique per scope, and present for both exec types.

    Never raises: an unusable answer here must not fail the run (start_run validates the
    pin for real, and a missing name only costs a badge)."""
    try:
        chain = ai_settings_service.resolve_effective(project_id).get("providers") or []
    except Exception:  # noqa: BLE001
        return None
    if provider_id:
        selected = next((p for p in chain if p.get("id") == provider_id), None)
        return (selected or {}).get("name") or None
    if len(chain) == 1:
        return chain[0].get("name") or None
    return None


def start_run(
    *,
    project_id: str,
    module: Optional[str],
    group_id: str,
    doc_ref: str,
    action_scope: str,
    mode: str,
    continuation_target_seq: Optional[int],
    continuation_review_mode: bool,
    continuation_instruction_mode: Optional[str],
    continuation_locale: Optional[str],
    issued_to: str,
    api_base_url: str,
    mention_builder: Callable[[str, str], Optional[str]],
    provider_id: Optional[str] = None,
    issue_builder: Optional[Callable[[], dict]] = None,
    merge_id: Optional[int] = None,
    completion_oracle: Optional[Callable[[], bool]] = None,
    # 0317 T0010 rev4: item_seq (as str, JSON-body keys) -> provider_id, chosen in
    # ContinuousWorkDialog's per-step override table. Session-scoped — this run's start
    # request is the only place it lives; never persisted (T0010 Q&A: session-scoped o1).
    continuation_provider_overrides: Optional[dict] = None,
    # 0346 T0005: [전달멘트] tab values — a common note for every hop and/or item_seq -> note
    # overrides for individual hops. Never persisted (D0004 §4: session-scoped, like the
    # provider overrides above).
    continuation_default_note: Optional[str] = None,
    continuation_note_overrides: Optional[dict] = None,
    # 0357 T0004: an unmanned continuous chain is made of a fresh run per hop.
    # These internal handoff values keep display progress at chain lifetime while
    # docs_target/docs_reached retain their existing per-run judging semantics.
    chain_id: Optional[str] = None,
    chain_docs_target: Optional[int] = None,
    chain_docs_reached: Optional[int] = None,
) -> dict:
    """Admit and launch a run. mention_builder(raw_token, scratch_dir) builds the
    worker mention through the exact token_routes path so the prompt the AI reads
    is byte-identical to the copy-mention flow (the raw token never leaves the
    server — it is consumed only as the run's FLOWGATE_TOKEN env).

    completion_oracle (0248 B0001): a caller-supplied "did the work land?" predicate for
    runs whose result is NOT a new document, so the document-reach oracle cannot see it.
    The Q&A [Request AI answer] run writes an answer row onto an existing document — under
    the document oracle it would settle as outcome='none' (docs_reached 0 < docs_target 1)
    no matter how well the worker did. Supplying an oracle switches the run to that scoped
    judge and pins docs_target to 0, mirroring the resolve_conflict branch below.

    0259 B0001: that opt-in is now only an OVERRIDE. A scope that produces no document gets
    its default judge from `_SCOPE_PROBES` here in the engine, so forgetting to pass one no
    longer silently falls back to the unreachable document oracle."""
    from modules.flow_gate.services.workflow_decision_service import (
        normalize_continuation_instruction_mode,
    )

    continuation_instruction_mode = normalize_continuation_instruction_mode(
        continuation_instruction_mode
    )
    effective = ai_settings_service.resolve_effective(project_id)
    chain = effective.get("providers") or []
    chain_source = effective.get("source")
    # 0317 T0010 rev4: a per-step override takes priority over everything else below —
    # including an explicit UI pin — because it names this EXACT hop, not just "no explicit
    # choice was made". Only re-orders the chain (like the doc-type resolver below), so a
    # startup failure still degrades to the existing fallback tail.
    step_override_provider = None
    if mode == "continuous" and continuation_provider_overrides:
        step_override_provider = _resolve_continuation_hop_override(
            doc_ref,
            continuation_provider_overrides,
            chain,
            continuation_instruction_mode=continuation_instruction_mode,
        )
    if step_override_provider:
        chain = _prioritize_chain(chain, step_override_provider)
    elif provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, "provider_unavailable",
                "The selected AI provider is not enabled for this project.",
            )
        # An explicit UI selection pins the run. Fallback order only applies when no provider was specified.
        chain = [selected]
    elif mode == "continuous":
        # 0317 D0004: no explicit pin on a continuous hop — consult the per-document-type
        # 배정 규칙. The assigned provider (if any, and if enabled) leads the chain; the rest
        # stay as the fallback tail so a startup failure still degrades gracefully (§3).
        hop_provider = _resolve_continuation_hop_provider(
            project_id,
            doc_ref,
            continuation_instruction_mode=continuation_instruction_mode,
        )
        if hop_provider:
            chain = _prioritize_chain(chain, hop_provider)
    if not chain:
        # 0292 T0003: "no provider was ever registered" used to be indistinguishable
        # from "the registered ones are all switched off" — both read as
        # no_enabled_provider, and the operator of a fresh install was sent to a
        # settings screen to toggle rows that do not exist. An install that skipped
        # the provider seed is a normal path now, so it gets its own code and the
        # command that fixes it.
        # source == "disabled" is excluded: that project turned AI off on purpose, so
        # "nothing is registered" would be a misleading thing to tell its operator.
        if chain_source != "disabled" and not effective.get("registered_count"):
            raise _http_error(
                409, "no_provider_registered",
                "No AI provider is registered. Register one in AI settings, "
                "or run the installer's provider step: ./setup-ai.sh "
                "(Windows: .\\setup-ai.ps1)",
            )
        raise _http_error(
            409, "no_enabled_provider",
            "No enabled AI provider for this project. Configure providers in AI settings.",
        )

    active = _active_run_for_group(group_id)
    if active is not None:
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id=active["run_id"])

    # 0299 R0001: refuse before minting a token / creating scratch — a run that would
    # execute in the base tree must not start at all, and failing here keeps the
    # rollback trivial (nothing has been created yet). The doc's branch is only the
    # fallback-branch hint; the guard itself is about the group worktree.
    _require_group_worktree(
        project_id, module, group_id,
        (db_docs.get_by_id(doc_ref) or {}).get("branch") or "main",
    )

    baseline_seq = db_docs.get_group_max_seq(group_id)
    target_to_end = mode == "continuous" and continuation_target_seq == -1
    scope_oracle_run = _uses_scope_oracle(action_scope, mode, completion_oracle)
    if completion_oracle is not None:
        # Scoped-oracle run: success is judged by the caller's predicate, not by documents.
        docs_target = 0
    elif action_scope in ("workflow_decide", "resolve_conflict"):
        docs_target = 0
    elif scope_oracle_run:
        # 0259 B0001: this scope's token cannot register a document, so targeting one made
        # success unreachable. Its default scope oracle is built below (it needs the token).
        docs_target = 0
    elif mode == "single":
        docs_target = 1
    else:
        # 0226 B0001 / NR0003 §5-1: the target is a workflow item_seq, never a group
        # document seq — derive docs_target from the sequence's pending worker items.
        target = int(continuation_target_seq or 0)
        resolved_target = _continuation_docs_target(
            doc_ref,
            target,
            continuation_instruction_mode=continuation_instruction_mode,
        )
        if resolved_target is None:
            raise HTTPException(status_code=422, detail={
                "code": "validation_failed",
                "errors": [{"loc": "continuation_target_seq",
                            "msg": "continuous run requires a decided workflow sequence "
                                   f"on {doc_ref}"}],
            })
        docs_target = resolved_target
        if docs_target <= 0:
            raise HTTPException(status_code=422, detail={
                "code": "validation_failed",
                "errors": [{"loc": "continuation_target_seq",
                            "msg": "no pending worker step at or below workflow item_seq "
                                   f"{target}"}],
            })

    # Allocate the run identity before token issuance so a chat turn can retain
    # its server-owned source_run_id and provider identity in the token.
    run_id = _next_run_id()

    if issue_builder is not None:
        issue = issue_builder()
        mention = issue.get("mention")
    else:
        issue = token_service.issue(
            project=project_id,
            group_id=group_id,
            action_scope=action_scope,
            doc_ref=doc_ref,
            issued_to=issued_to,
            continuation_target_seq=continuation_target_seq if mode == "continuous" else None,
            continuation_review_mode=bool(mode == "continuous" and continuation_review_mode),
            continuation_instruction_mode=continuation_instruction_mode if mode == "continuous" else None,
            continuation_locale=continuation_locale if mode == "continuous" else None,
            merge_id=merge_id if action_scope == "resolve_conflict" else None,
            provider_id=provider_id,
            ai_run_id=run_id,
        )
        mention = mention_builder(issue["raw_token"], issue["scratch_dir"])
    if not mention:
        # No prompt ⇒ nothing to launch. Discard the just-minted token.
        try:
            token_service.revoke(issue["token_id"], reason="ai_invoke_mention_unavailable")
        except Exception:
            logger.warning("token revoke failed after mention_unavailable", exc_info=True)
        raise _http_error(409, "mention_unavailable",
                          "Could not build a worker mention for this document.")

    # 0346 T0005 §2-5 / D0004 §3-3: the [전달멘트] tab's common note and/or this hop's
    # individual note are prepended here, at the single point every hop's prompt (built by
    # whichever of the three builders ran above) has already converged into one string — see
    # D0004 §3-4 for why the builders themselves are never touched. Unlike the provider
    # override, an individual note does NOT replace the common one: D0004 §3-3 treats them as
    # stackable ("무엇을 위한 것인가" + "너는 무엇을 맡는가"), so both are adopted when present.
    # A resolution failure must not stall the hop (same contract as the provider override).
    if mode == "continuous" and (continuation_note_overrides or continuation_default_note):
        try:
            notes: list[str] = []
            if continuation_default_note and continuation_default_note.strip():
                notes.append(continuation_default_note.strip())
            if continuation_note_overrides:
                hop_note = _resolve_continuation_hop_note(
                    doc_ref,
                    continuation_note_overrides,
                    continuation_instruction_mode=continuation_instruction_mode,
                )
                if hop_note:
                    notes.append(hop_note)
            if notes:
                mention = invoke_mention_service.prepend_messages_section(
                    mention, notes, continuation_locale,
                )
        except Exception:  # noqa: BLE001 — a note failure must not stall the hop
            logger.warning("continuation hop note injection failed for %s", doc_ref, exc_info=True)

    if scope_oracle_run:
        # After issue() (the judge target comes from the token) but before the worker is
        # launched below, so the baseline cannot include the work this run is about to do.
        completion_oracle = _scope_oracle(action_scope, issue.get("token_id"), doc_ref)

    _cleanup_retained_scratches(project_id)
    run_id = _next_run_id()
    if mode == "continuous":
        chain_id = chain_id or run_id
        if chain_docs_target is None:
            chain_docs_target = docs_target
        if chain_docs_reached is None:
            chain_docs_reached = 0
    else:
        # A single run is a degenerate one-hop chain. Returning the same payload
        # shape keeps clients simple without changing the run counters' meaning.
        chain_id = run_id
        chain_docs_target = docs_target
        chain_docs_reached = 0
    scratch = _create_scratch(project_id, run_id)

    doc = db_docs.get_by_id(doc_ref) or {}
    # 0187 rev2: same group-worktree routing as the test runner — the invoked AI's
    # cwd and the pollution diff must watch the tree the group's CRUD writes to.
    source_root = storage_paths.resolve_project_src_root(
        project_id, doc.get("branch") or "main", group_id=group_id
    )

    run = {
        "run_id": run_id,
        "status": "running",
        "mode": mode,
        "project_id": project_id,
        "module": module,
        "group_id": group_id,
        "doc_ref": doc_ref,
        "docs_target": docs_target,
        "chain_id": chain_id,
        "chain_docs_target": int(chain_docs_target or 0),
        "chain_docs_reached": int(chain_docs_reached or 0),
        "chain_docs_accounted": False,
        "baseline_seq": baseline_seq,
        "timeout_sec": RUN_TIMEOUT_CAP_SEC if target_to_end else min(RUN_TIMEOUT_BASE_SEC * max(1, docs_target), RUN_TIMEOUT_CAP_SEC),
        "provider": None,
        "provider_id": None,
        "attempt_no": 0,
        "fallback_history": [],
        "register_errors": [],
        "tool_call_misses": 0,
        "turn_limit_exhausted": False,
        "oracle_mismatch": False,
        "started_at": now_iso(),
        "started_mono": time.monotonic(),
        "cancel_event": threading.Event(),
        "proc": None,
        "timed_out": False,
        "end_reason": None,
        "exit_code": None,
        "last_message": None,
        "last_message_received": False,
        "outcome": None,
        "docs_reached": 0,
        "reached_doc_ids": [],
        "source_dirty": None,
        "source_dirty_files": [],
        "scratch_dir": str(scratch),
        "scratch_retained": None,
        "duration_ms": None,
        "finished_at": None,
        "dirty_baseline": _git_status_paths(source_root),
        "source_root": str(source_root) if source_root else None,
        "api_base_url": api_base_url,
        "chain_source": chain_source,
        "action_scope": action_scope,
        "target_to_end": target_to_end,
        "continuation_instruction_mode": (
            continuation_instruction_mode if mode == "continuous" else None
        ),
        # 0317 TR0011 (Q153 opt-1): the per-step override map rides on the run so each
        # re-spawned hop can re-apply it (it never touches a token; it is session-scoped).
        "continuation_provider_overrides": (
            continuation_provider_overrides if mode == "continuous" else None
        ),
        # 0346 T0005: the [전달멘트] tab's note bundle rides the run forward the same way the
        # provider override map does, so a re-spawned hop (_maybe_auto_resume_hop ->
        # _spawn_auto_resume) can re-apply it. Session-scoped — never persisted on a token.
        "continuation_note_overrides": (
            continuation_note_overrides if mode == "continuous" else None
        ),
        "continuation_default_note": (
            continuation_default_note if mode == "continuous" else None
        ),
        # 0317 T0013 결함 ③: the header default provider pin rides the run too. Without it a
        # re-spawned hop that has NO per-step override lost the user's chosen default and fell
        # back to the doc-type assignment / project default chain — contradicting the
        # "기본: <이름>" tag every ContinuousWorkDialog row promises. Session-scoped like the
        # override map; never persisted on a token.
        "continuation_base_provider_id": (provider_id if mode == "continuous" else None),
        # 0252 L0009 §2.8: keep the requester on the record so the global active list
        # (active_all) can filter runs per user, and §2.1: the continuation target for
        # the paused-row snapshot (None = to-end, resolved again at resume time).
        "issued_to": issued_to,
        "continuation_target_seq": (
            None if target_to_end or mode != "continuous" else continuation_target_seq
        ),
        "pause_requested": False,
        "user_paused": False,
        "raw_token": issue["raw_token"],
        "merge_id": merge_id,
        "completion_oracle": completion_oracle,
    }
    with _runs_lock:
        _runs[run_id] = run

    thread = threading.Thread(
        target=_worker,
        args=(run, chain, mention),
        daemon=True,
        name=f"ai-invoke-{run_id}",
    )
    thread.start()

    return {
        "ok": True,
        "run_id": run_id,
        "status": "running",
        "mode": mode,
        "group_id": group_id,
        "doc_ref": doc_ref,
        "docs_target": docs_target,
        "chain_id": run["chain_id"],
        "chain_docs_target": run["chain_docs_target"],
        "chain_docs_reached": run["chain_docs_reached"],
        "continuation_instruction_mode": (
            continuation_instruction_mode if mode == "continuous" else None
        ),
        "provider": _provider_brief(chain[0]),
        "attempt_no": 1,
        "started_at": run["started_at"],
    }


def _provider_brief(provider: Optional[dict]) -> Optional[dict]:
    if provider is None:
        return None
    return {
        "id": provider.get("id"),
        "name": provider.get("name"),
        "exec_type": provider.get("exec_type"),
        "kind": provider.get("kind"),
    }


def _resolve_continuation_hop_provider(
    project_id: str,
    doc_ref: str,
    *,
    continuation_instruction_mode: Optional[str] = None,
) -> Optional[str]:
    """Return the doc-type-assigned provider for the worker hop, or the default-chain tier.

    Only ``auto_approved`` N/T heads fold to their paired report type. ``ai_direct`` N/T
    and TS in either mode are worker-authored, so they resolve their own head type
    (flowgate.default.0353 B0001 / NR0003). Never raises: lookup gaps fall through.
    """
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None
        head_type = (head.get("type") or "").upper()
        from modules.flow_gate.services.workflow_decision_service import (
            AUTO_REPORT_MAP,
            CONTINUATION_INSTRUCTION_AUTO_APPROVED,
            INSTRUCTION_AUTO_TYPES,
            normalize_continuation_instruction_mode,
        )

        fold_to_report = (
            normalize_continuation_instruction_mode(continuation_instruction_mode)
            == CONTINUATION_INSTRUCTION_AUTO_APPROVED
            and head_type in INSTRUCTION_AUTO_TYPES
        )
        worker_type = AUTO_REPORT_MAP.get(head_type, head_type) if fold_to_report else head_type
        # Preserve the legacy auto-approved preference: report assignment first, raw N/T
        # assignment second. Non-folded heads resolve only their own worker-authored type.
        assigned = ai_settings_service.resolve_doctype_provider(project_id, worker_type)
        if assigned is None and fold_to_report and worker_type != head_type:
            assigned = ai_settings_service.resolve_doctype_provider(project_id, head_type)
        return assigned
    except Exception:  # noqa: BLE001 — a resolution failure must not stall the hop
        logger.warning("continuation hop provider resolution failed for %s", doc_ref,
                       exc_info=True)
        return None


def _hop_worker_item_seq(
    seq_id: int,
    head: dict,
    *,
    continuation_instruction_mode: Optional[str] = None,
) -> Optional[int]:
    """Return the item_seq of the slot this worker fills.

    ``auto_approved`` N/T heads are server-created and fold to the paired NR/TR slot.
    ``ai_direct`` N/T heads and TS in both modes are independent worker hops and retain
    their own item_seq (flowgate.default.0353 B0001 / NR0003). Missing or unknown modes
    normalize to the legacy ``auto_approved`` behavior.
    """
    head_item_seq = head.get("item_seq")
    head_type = (head.get("type") or "").upper()
    from modules.flow_gate.services.workflow_decision_service import (
        AUTO_REPORT_MAP,
        CONTINUATION_INSTRUCTION_AUTO_APPROVED,
        INSTRUCTION_AUTO_TYPES,
        normalize_continuation_instruction_mode,
    )

    fold_to_report = (
        normalize_continuation_instruction_mode(continuation_instruction_mode)
        == CONTINUATION_INSTRUCTION_AUTO_APPROVED
        and head_type in INSTRUCTION_AUTO_TYPES
    )
    report_type = AUTO_REPORT_MAP.get(head_type)
    if not fold_to_report or not report_type or head_item_seq is None:
        return head_item_seq
    for item in sorted(
        db_wfseq.get_sequence_items(seq_id) or [],
        key=lambda i: i.get("item_seq") or 0,
    ):
        if (
            (item.get("item_seq") or -1) > head_item_seq
            and (item.get("type") or "").upper() == report_type
        ):
            return item.get("item_seq")
    return head_item_seq


def _resolve_continuation_hop_override(
    doc_ref: str,
    overrides: dict,
    chain: list[dict],
    *,
    continuation_instruction_mode: Optional[str] = None,
) -> Optional[str]:
    """Return the enabled provider override keyed to this mode-aware worker item_seq.

    String JSON keys and integer keys are both accepted. A missing or disabled provider
    silently falls through to the explicit pin / doc-type / default tiers.
    """
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None
        item_seq = _hop_worker_item_seq(
            seq["id"],
            head,
            continuation_instruction_mode=continuation_instruction_mode,
        )
        if item_seq is None:
            return None
        provider_id = overrides.get(str(item_seq), overrides.get(item_seq))
        if not provider_id:
            return None
        if not any(p.get("id") == provider_id for p in chain):
            return None
        return provider_id
    except Exception:  # noqa: BLE001 — a resolution failure must not stall the hop
        logger.warning("continuation hop override resolution failed for %s", doc_ref,
                       exc_info=True)
        return None


def _resolve_continuation_hop_note(
    doc_ref: str,
    overrides: dict,
    *,
    continuation_instruction_mode: Optional[str] = None,
) -> Optional[str]:
    """Return the individual note for the same mode-aware item_seq as the provider override.

    Keeping both resolvers on `_hop_worker_item_seq` preserves the D0004 constraint that a
    visible row's provider and note always address the same hop. Failures degrade to no note.
    """
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None
        item_seq = _hop_worker_item_seq(
            seq["id"],
            head,
            continuation_instruction_mode=continuation_instruction_mode,
        )
        if item_seq is None:
            return None
        note = overrides.get(str(item_seq), overrides.get(item_seq))
        if not note or not str(note).strip():
            return None
        return str(note).strip()
    except Exception:  # noqa: BLE001 — a resolution failure must not stall the hop
        logger.warning("continuation hop note resolution failed for %s", doc_ref, exc_info=True)
        return None


def _prioritize_chain(chain: list[dict], provider_id: str) -> list[dict]:
    """Move the assigned provider to the front, keeping the rest as the fallback tail
    (D0004 §3: 배정이 폴백보다 우선하되, 기동 실패 시 폴백 순서로 넘어간다). Unlike an explicit
    UI pin — which collapses the chain to one provider and disables fallback — a doc-type
    assignment only re-orders, so the existing _worker fallback loop still protects the run."""
    head = [p for p in chain if p.get("id") == provider_id]
    if not head:
        return chain
    return head + [p for p in chain if p.get("id") != provider_id]


# ── Worker: provider fallback loop (L0006 §2.2) ──────────────────────────────

def _worker(run: dict, chain: list[dict], prompt: str) -> None:
    try:
        run["provider"] = _provider_brief(chain[0])
        run["provider_id"] = chain[0].get("id")
        run["attempt_no"] = 1
        _broadcast(run, "ai_invoke_started", {
            "run_id": run["run_id"],
            "group_id": run["group_id"],
            "doc_ref": run["doc_ref"],
            "mode": run["mode"],
            "docs_target": run["docs_target"],
            "chain_id": run["chain_id"],
            "chain_docs_target": run["chain_docs_target"],
            "chain_docs_reached": run["chain_docs_reached"],
            "provider_id": chain[0].get("id"),
            "provider_name": chain[0].get("name"),
            "attempt_no": 1,
        })

        started_ok = False
        last_reason = None
        for index, provider in enumerate(chain):
            if run["cancel_event"].is_set():
                break
            attempt_no = index + 1
            if index > 0:
                prev = chain[index - 1]
                run["provider"] = _provider_brief(provider)
                run["provider_id"] = provider.get("id")
                run["attempt_no"] = attempt_no
                _broadcast(run, "ai_invoke_provider_switched", {
                    "run_id": run["run_id"],
                    "group_id": run["group_id"],
                    "from_provider_id": prev.get("id"),
                    "from_provider_name": prev.get("name"),
                    "to_provider_id": provider.get("id"),
                    "to_provider_name": provider.get("name"),
                    "reason": last_reason,
                    "attempt_no": attempt_no,
                })

            if provider.get("exec_type") == "api":
                classification, detail = _api_execute(provider, prompt, run)
            else:
                classification, detail = _cli_execute(provider, prompt, run)

            if classification == "started_ok":
                started_ok = True
                break
            last_reason = classification
            run["fallback_history"].append({
                "provider_id": provider.get("id"),
                "provider_name": provider.get("name"),
                "reason": classification,
                "detail": detail,
            })
            if run["cancel_event"].is_set():
                break

        if not started_ok:
            run["provider_id"] = None
            run["provider"] = None

        # end_reason classification (L0006 §4.1) — default "exited".
        # "user_paused" (0252 P0008 S4): the inbox self-chain hit the user's pause flag
        # at a step boundary and withheld the next token; the worker then exits normally,
        # so the flag — not the exit itself — is what distinguishes a boundary stop.
        if not started_ok and not run["cancel_event"].is_set():
            run["end_reason"] = "all_providers_failed"
        elif run["cancel_event"].is_set():
            run["end_reason"] = "cancelled"
        elif run["timed_out"]:
            run["end_reason"] = "timeout"
        elif run.get("user_paused"):
            run["end_reason"] = "user_paused"
        else:
            run["end_reason"] = "exited"

        _settle_and_judge(run)
        # 0317 TR0011 (Q153 opt-1): the run is now finished, so start_run's active-run guard
        # is clear — re-spawn the next hop's worker if the self-chain flagged a boundary.
        _maybe_auto_resume_hop(run)
    except Exception:
        logger.exception("ai-invoke worker crashed for %s", run["run_id"])
        run["end_reason"] = run.get("end_reason") or "exited"
        try:
            _settle_and_judge(run)
        except Exception:
            logger.exception("ai-invoke settle failed for %s", run["run_id"])
            run["status"] = "finished"
        # A crashed hop is a real stop, not a boundary: drop any pending re-spawn so the
        # chain does not silently continue past a failure.
        clear_auto_resume(run.get("group_id"))


def _remaining_sec(run: dict) -> float:
    return run["timeout_sec"] - (time.monotonic() - run["started_mono"])


def _work_landed(run: dict) -> bool:
    """Did this run already produce something? Fast-fail's "nothing was lost" check.

    0259 B0001 §3: this used to be a raw group max-seq delta for every run. On a run whose
    product is not a document that is False however well the worker did, so a worker that
    finished its edit and then exited nonzero inside the fast-fail window was re-run on the
    next provider. Ask the run's own judge — the scope default or the caller's override —
    and only fall back to the seq delta for the document-producing scopes it is true for.

    NOTE this is deliberately NOT the judge's `_oracle_new_docs` (non-draft docs past the
    baseline): the seq delta is the wider net, and counting a stray draft here only makes
    fast-fail more conservative, which is the safe direction for a "may I discard this
    attempt?" question.
    """
    oracle = run.get("completion_oracle")
    if oracle is not None:
        try:
            return bool(oracle())
        except Exception:
            logger.warning("ai-invoke fast-fail oracle failed for %s", run["run_id"], exc_info=True)
            return False
    try:
        return db_docs.get_group_max_seq(run["group_id"]) > run["baseline_seq"]
    except Exception:
        return False


def _truncate_front(text: Optional[str], max_bytes: int = LAST_MESSAGE_MAX_BYTES) -> Optional[str]:
    """Keep the tail (the dying message's end matters most), drop the front."""
    if text is None:
        return None
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[-max_bytes:].decode("utf-8", errors="replace")


def _resolve_agent_api_base(operator_api_base: str) -> str:
    """Agent-reachable inbox base for the EXTERNAL AGENT (CLI) path (D0005 §3-4 /
    L0008 §2-5).

    ``operator_api_base`` is the operator-facing base the mention was built with
    ({scheme}://{host}:{port}{CONTEXT}/api/v1). Priority:
      1. FLOWGATE_AGENT_API_BASE setting (origin) + the operator base's path.
      2. Same-host loopback: swap the host for 127.0.0.1, keep scheme/port/path.
      3. Fall back to the operator base unchanged.
    Server-direct (exec_type=api) runs never call this - they post to themselves.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not operator_api_base:
        return operator_api_base
    parts = urlsplit(operator_api_base)

    setting = ""
    try:
        from config import settings as _settings
        setting = (getattr(_settings, "FLOWGATE_AGENT_API_BASE", None) or "").strip()
    except Exception:
        setting = ""
    if setting:
        if "://" not in setting:
            setting = "http://" + setting
        s = urlsplit(setting)
        if s.netloc:
            return urlunsplit((s.scheme or parts.scheme, s.netloc, parts.path, "", ""))

    host = parts.hostname
    if not host:
        return operator_api_base
    netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# ── CLI adapter (L0006 §2.3) ─────────────────────────────────────────────────

def _cli_execute(provider: dict, prompt: str, run: dict) -> tuple[str, Optional[str]]:
    """stdin-injected CLI run (claude/copilot/codex; args are forbidden — cp932
    truncation). Returns (classification, failure_detail)."""
    import subprocess

    cmd = (provider.get("cli_command") or "").strip()
    if not cmd:
        return "spawn_failed", "cli_command not set"
    kind = provider.get("kind") or ""
    # 0295 NR0003 §5-2: injects codex's --skip-git-repo-check when the stored command lacks
    # it. The cwd resolved below is NOT guaranteed to be a git repo (scratch fallback, or a
    # project mirror that is not a checkout), and codex exec exits 1 immediately when it is
    # not — burning the provider as a fast_fail before it ever reads the prompt.
    cmd = ai_settings_service.normalize_cli_command(kind, cmd)
    scratch = Path(run["scratch_dir"])
    last_message_file = scratch / "last_message.txt"
    if kind == "codex":
        cmd = f'{cmd} --output-last-message "{last_message_file}"'

    # 0278 NR0003: resolve_project_src_root() returns the project's source-mirror path
    # WITHOUT checking that it exists, and only git provisioning ever creates that
    # directory. A non-git project therefore yields a real-looking path to a folder
    # that is not there, and Popen(cwd=...) raises for every provider in the chain --
    # the run dies as all_providers_failed with no last message. The mirror is not
    # required for a CLI worker (it registers through the inbox API, not the tree),
    # so fall back to the run scratch dir. The bare `else scratch` never covered this
    # case because the resolver hands back a path, not None.
    resolved_root = Path(run["source_root"]) if run.get("source_root") else None
    if resolved_root is not None and resolved_root.is_dir():
        source_root = resolved_root
    else:
        if resolved_root is not None:
            logger.warning(
                "ai-invoke %s: source mirror missing at %s - running in scratch %s",
                run["run_id"], resolved_root, scratch,
            )
        source_root = scratch
    # Group 0235 (D0005 §3-4 / L0008 §2-5): the external agent runs on THIS host and
    # must post results to an address it can actually reach. The mention was built
    # with the operator-facing base; rewrite it (and export it) to an agent-reachable
    # base (configured setting -> same-host loopback -> operator base).
    operator_api_base = run.get("api_base_url") or ""
    agent_api_base = _resolve_agent_api_base(operator_api_base)
    if agent_api_base and operator_api_base and agent_api_base != operator_api_base:
        prompt = prompt.replace(operator_api_base, agent_api_base)
    # CLI providers authenticate themselves; a configured api_key is deliberately
    # NOT exported (leak prevention, L0006 §2.3).
    env = {
        "FLOWGATE_TOKEN": run["raw_token"],
        "FLOWGATE_SCRATCH": run["scratch_dir"],
        "FLOWGATE_API_BASE": agent_api_base or operator_api_base,
    }
    eff_cmd, eff_cwd = process_runner.unc_safe_shell(cmd, source_root)
    kwargs = process_runner.popen_kwargs(source_root, env)
    kwargs["cwd"] = eff_cwd
    kwargs["stdin"] = subprocess.PIPE

    launched = time.monotonic()
    try:
        proc = subprocess.Popen(eff_cmd, **kwargs)
    except Exception as exc:
        return "spawn_failed", str(exc)[:500]

    run["proc"] = proc
    # Close the cancel-vs-spawn race: a cancel that landed between admission and
    # Popen saw proc=None and killed nothing — reap the child ourselves now.
    if run["cancel_event"].is_set():
        process_runner.kill_process_tree(proc)
    timed_out = False
    remaining = max(1.0, _remaining_sec(run))
    try:
        stdout, stderr = proc.communicate(input=prompt.encode("utf-8"), timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        process_runner.kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout = getattr(exc, "output", None)
            stderr = getattr(exc, "stderr", None)
    except Exception as exc:
        # e.g. stdin pipe broken before the child read the prompt
        process_runner.kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = None, None
        elapsed = time.monotonic() - launched
        if elapsed < FAST_FAIL_WINDOW_SEC and not _work_landed(run):
            return "spawn_failed", str(exc)[:500]
    finally:
        run["proc"] = None

    elapsed = time.monotonic() - launched
    out_text = process_runner.safe_decode(stdout)
    err_text = process_runner.safe_decode(stderr)
    run["stdout_tail"] = out_text[-OUTPUT_TAIL_BYTES:]
    run["stderr_tail"] = err_text[-OUTPUT_TAIL_BYTES:]
    run["exit_code"] = proc.returncode if not timed_out else None
    if timed_out:
        run["timed_out"] = True

    cancelled = run["cancel_event"].is_set()
    # Fast-fail: startup failure ⇒ fallback candidate. A user cancel or timeout is
    # never a provider startup failure (L0006 §2.2 / §4.3).
    if (
        not cancelled
        and not timed_out
        and proc.returncode is not None
        and proc.returncode != 0
        and elapsed < FAST_FAIL_WINDOW_SEC
        and not _work_landed(run)
    ):
        detail = (err_text or out_text).strip()[-500:] or f"exit {proc.returncode} within {int(elapsed)}s"
        return "fast_fail", detail

    _recover_cli_last_message(run, kind, out_text, last_message_file)
    return "started_ok", None


def _copilot_last_message(stdout_text: str) -> Optional[str]:
    """Last assistant text from copilot's `--output-format=json` event stream.

    The stream is NDJSON — one event object per line, no blank lines anywhere — so the
    blank-line block splitter below returns the WHOLE dump as a single "message" and the
    operator sees MCP server status logs where the answer should be (0292 CH0002, 0295
    NR0003 §6). The answer lives in the last `assistant.message` event's `data.content`;
    `assistant.message_delta` carries the same text in fragments and `result` carries no
    text at all, so neither is a usable substitute.

    Returns None when nothing parses, leaving the caller on its block-splitting fallback —
    a copilot run that failed before emitting any event still has stderr/plain output worth
    showing.
    """
    message: Optional[str] = None
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant.message":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            message = content.strip()
    return message


def _recover_cli_last_message(run: dict, kind: str, stdout_text: str, last_message_file: Path) -> None:
    """Per-kind last-message recovery (hive providers.py rule table, rules only):
    claude = full stdout trimmed / codex = --output-last-message file /
    copilot = last `assistant.message` event / custom = last non-blank block of the tail."""
    message: Optional[str] = None
    if kind == "claude":
        message = stdout_text.strip() or None
    elif kind == "codex":
        try:
            if last_message_file.is_file():
                message = last_message_file.read_text(encoding="utf-8", errors="replace").strip() or None
        except Exception:
            message = None
    elif kind == "copilot":
        message = _copilot_last_message(stdout_text)
    if message is None and kind not in ("claude", "codex"):
        tail = stdout_text[-OUTPUT_TAIL_BYTES:]
        blocks = [b.strip() for b in re.split(r"\n\s*\n", tail) if b.strip()]
        message = blocks[-1] if blocks else None
    run["last_message"] = _truncate_front(message)
    run["last_message_received"] = bool(message)


# ── API adapter: minimal agent loop (L0006 §2.4) ─────────────────────────────

def _api_execute(provider: dict, prompt: str, run: dict) -> tuple[str, Optional[str]]:
    """Minimal tool loop for API providers, including workflow decision kickoff."""
    run.setdefault("register_errors", [])
    run.setdefault("tool_call_misses", 0)
    run.setdefault("turn_limit_exhausted", False)
    secret_scope = run["project_id"] if run.get("chain_source") == "project" else None
    key = ai_settings_service.get_provider_secret(secret_scope, provider.get("id"))
    if not key:
        return "spawn_failed", "api_key_not_set"
    logger.info(
        "ai-invoke %s: api provider %s key present (len=%d)",
        run["run_id"], provider.get("id"), len(key),
    )

    kind = provider.get("kind") or "openai"
    base_url = (provider.get("api_base_url") or "").rstrip("/")
    model = provider.get("api_model") or ""
    max_turns = max(API_MAX_TURNS_PER_DOC, max(1, run["docs_target"]) * API_MAX_TURNS_PER_DOC)

    current_token = run["raw_token"]
    registered = 0
    workflow_pending = run.get("action_scope") == "workflow_decide"
    conflict_pending = run.get("action_scope") == "resolve_conflict"
    last_text: Optional[str] = None
    conversation: list[dict] = [{"role": "user", "content": prompt}]
    turn = 0

    while turn < max_turns:
        turn += 1
        if run["cancel_event"].is_set():
            break
        remaining = _remaining_sec(run)
        if remaining <= 0:
            run["timed_out"] = True
            break
        call_timeout = min(remaining, API_CALL_MAX_TIMEOUT_SEC)
        if workflow_pending:
            tool_name, tool_desc, tool_schema = _DECIDE_TOOL_NAME, _DECIDE_TOOL_DESC, _DECIDE_TOOL_SCHEMA
        elif conflict_pending:
            tool_name, tool_desc, tool_schema = _RESOLVE_TOOL_NAME, _RESOLVE_TOOL_DESC, _RESOLVE_TOOL_SCHEMA
        else:
            tool_name, tool_desc, tool_schema = _REGISTER_TOOL_NAME, _REGISTER_TOOL_DESC, _REGISTER_TOOL_SCHEMA
        try:
            if kind == "claude":
                reply_text, tool_call, assistant_msg = _call_anthropic(
                    base_url, model, key, conversation, call_timeout,
                    tool_name, tool_desc, tool_schema,
                )
            else:
                reply_text, tool_call, assistant_msg = _call_openai(
                    base_url, model, key, conversation, call_timeout,
                    tool_name, tool_desc, tool_schema,
                )
        except urllib.error.HTTPError as exc:
            if turn == 1:
                return "api_error", f"{exc.code} {exc.reason}"
            logger.warning("ai-invoke %s: api error after first turn: %s", run["run_id"], exc)
            break
        except Exception as exc:
            if turn == 1:
                return "spawn_failed", str(exc)[:500]
            logger.warning("ai-invoke %s: api transport error after first turn: %s", run["run_id"], exc)
            break

        conversation.append(assistant_msg)
        if reply_text:
            last_text = reply_text
        if tool_call is None:
            run["tool_call_misses"] += 1
            if run["tool_call_misses"] <= API_MAX_TOOL_NUDGES:
                conversation.append({
                    "role": "user",
                    "content": (
                        f"The required action is not complete. Call the `{tool_name}` tool now with "
                        "the actual full payload. Do not merely say that you registered or attached it."
                    ),
                })
                continue
            break

        if workflow_pending:
            status, resp = _workflow_decide(run, current_token, tool_call["input"])
            if 200 <= status < 300:
                workflow_pending = False
                next_token = resp.get("next_token")
                next_mention = resp.get("next_mention")
                resolved_target = resp.get("continuation_target_seq")
                if run.get("target_to_end") and isinstance(resolved_target, int) and resolved_target > 0:
                    # 0226 NR0003 §5-1: resolved_target is an item_seq — count the decided
                    # sequence's worker items instead of subtracting the group doc seq.
                    resolved = _continuation_docs_target(
                        run["doc_ref"],
                        resolved_target,
                        pending_only=False,
                        continuation_instruction_mode=run["continuation_instruction_mode"],
                    )
                    if resolved is not None:
                        run["docs_target"] = resolved
                        # A to-end workflow-decision run starts before the sequence
                        # exists, so its chain target is resolved exactly once here.
                        if run.get("chain_docs_target", 0) <= 0:
                            run["chain_docs_target"] = resolved
                    max_turns = max(max_turns, turn + max(1, run["docs_target"]) * API_MAX_TURNS_PER_DOC)
                if next_token:
                    current_token = next_token
                result_text = next_mention or json.dumps(resp, ensure_ascii=False)[:4000]
                conversation.append(_tool_result_msg(kind, tool_call, result_text))
                if run["mode"] == "single" or not next_token:
                    break
                continue
            conversation.append(_tool_result_msg(
                kind, tool_call,
                f"Workflow decision failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))
            continue

        if conflict_pending:
            status, resp = _resolve_conflict(run, current_token, tool_call["input"])
            if 200 <= status < 300:
                conversation.append(_tool_result_msg(kind, tool_call, json.dumps(resp, ensure_ascii=False)[:4000]))
                break
            conversation.append(_tool_result_msg(
                kind, tool_call,
                f"Conflict resolve failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))
            continue

        status, resp = _inbox_register(run, current_token, tool_call["input"])
        if 200 <= status < 300:
            registered += 1
            next_token = resp.get("next_token")
            next_mention = resp.get("next_mention")
            if next_token:
                current_token = next_token
            result_text = next_mention or json.dumps(
                {k: resp.get(k) for k in ("ok", "doc_id", "message") if k in resp},
                ensure_ascii=False,
            )
            conversation.append(_tool_result_msg(kind, tool_call, result_text))
            if run["mode"] == "single" or registered >= run["docs_target"] or not next_token:
                break
        else:
            reason = _registration_error_summary(resp)
            run["register_errors"].append({
                "status": status,
                "reason": reason,
                "turn": turn,
            })
            conversation.append(_tool_result_msg(
                kind, tool_call,
                f"Registration failed (HTTP {status}): {json.dumps(resp, ensure_ascii=False)[:2000]}",
            ))

    goal_met = (
        not workflow_pending
        and (
            (run.get("action_scope") == "workflow_decide" and run["mode"] == "single")
            or registered >= run["docs_target"]
        )
    )
    if (
        turn >= max_turns
        and not goal_met
        and not run["cancel_event"].is_set()
        and not run.get("timed_out")
    ):
        run["turn_limit_exhausted"] = True

    run["exit_code"] = None
    run["last_message"] = _truncate_front(last_text)
    run["last_message_received"] = bool(last_text)
    return "started_ok", None

def _registration_error_summary(response: dict) -> str:
    for key in ("code", "error", "message", "detail"):
        value = response.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)[:500]
        return str(value)[:500]
    return json.dumps(response, ensure_ascii=False)[:500] or "unknown registration error"


def _tool_result_msg(kind: str, tool_call: dict, text: str) -> dict:
    if kind == "claude":
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_call["id"],
                "content": text,
            }],
        }
    return {"role": "tool", "tool_call_id": tool_call["id"], "content": text}


def _resolve_conflict(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    body = {
        "files": tool_input.get("files") or [],
        "complete": bool(tool_input.get("complete")),
    }
    req = urllib.request.Request(
        f"{run['api_base_url']}/groups/{run['group_id']}/git/merge/{run['merge_id']}/resolve-token",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {raw_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}


def _http_post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_anthropic(
    base_url: str, model: str, key: str, conversation: list[dict], timeout: float,
    tool_name: str, tool_desc: str, tool_schema: dict,
) -> tuple[Optional[str], Optional[dict], dict]:
    data = _http_post_json(
        f"{base_url}/v1/messages",
        {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        {
            "model": model,
            "max_tokens": API_MAX_TOKENS,
            "messages": conversation,
            "tools": [{
                "name": tool_name,
                "description": tool_desc,
                "input_schema": tool_schema,
            }],
        },
        timeout,
    )
    content = data.get("content") or []
    text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
    tool_call = None
    for block in content:
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            tool_call = {"id": block.get("id"), "name": tool_name, "input": block.get("input") or {}}
            break
    assistant_msg = {"role": "assistant", "content": content}
    return ("\n".join(p for p in text_parts if p) or None), tool_call, assistant_msg


def _call_openai(
    base_url: str, model: str, key: str, conversation: list[dict], timeout: float,
    tool_name: str, tool_desc: str, tool_schema: dict,
) -> tuple[Optional[str], Optional[dict], dict]:
    data = _http_post_json(
        f"{base_url}/v1/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {
            "model": model,
            "messages": conversation,
            "tools": [{
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_desc,
                    "parameters": tool_schema,
                },
            }],
        },
        timeout,
    )
    choices = data.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    tool_call = None
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        if fn.get("name") == tool_name:
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            tool_call = {"id": tc.get("id"), "name": tool_name, "input": args}
            break
    return message.get("content"), tool_call, message


def _workflow_decide(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    body = {
        "doc_class": tool_input.get("doc_class") or "standard",
        "sequence": tool_input.get("sequence") or [],
    }
    req = urllib.request.Request(
        f"{run['api_base_url']}/workflow/{run['doc_ref']}/decide",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {raw_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}

def _inbox_register(run: dict, raw_token: str, tool_input: dict) -> tuple[int, dict]:
    """Server-side proxy registration for API providers: POST the model-authored
    body to our own /inbox with the run token, exactly as an external worker
    would — every inbox validation and the chain self-advance stay in force."""
    body = {
        "action": "new",
        "project": run["project_id"],
        "module": run.get("module") or "none",
        "group_name": run["group_id"],
        "doc_type": (tool_input.get("doc_type") or "").strip(),
        "prev_doc_id": run["doc_ref"],
        "title": tool_input.get("title") or "",
        "content": tool_input.get("content") or "",
    }
    req = urllib.request.Request(
        f"{run['api_base_url']}/inbox",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {raw_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:
        return 0, {"error": str(exc)}


# ── Judge / finish (L0006 §2.6–2.8) ──────────────────────────────────────────

def _oracle_new_docs(run: dict) -> list[dict]:
    """Run-attributed documents: non-draft docs past the run's baseline seq.

    The single filter shared by the live progress counter (get_status) and the final
    judge (_settle_and_judge) — 0226 NR0003 §5-2. The live counter previously showed
    the raw group max-seq delta (drafts and documents this run never made included),
    so it could read 4/3 mid-run while the judge later clamped to 3/3.
    """
    docs = db_docs.get_documents_by_group_id(run["group_id"])
    return sorted(
        (
            d for d in docs
            if (d.get("seq") or 0) > run["baseline_seq"] and (d.get("status") or "") != "draft"
        ),
        key=lambda d: d.get("seq") or 0,
    )


def _settle_and_judge(run: dict) -> None:
    time.sleep(ORACLE_SETTLE_SEC)
    if run.get("action_scope") == "resolve_conflict":
        resolved = _conflict_resolved(run)
        run["docs_reached"] = 0
        run["reached_doc_ids"] = []
        run["outcome"] = "complete" if resolved else "none"
        _finish_run_record(run)
        return

    oracle = run.get("completion_oracle")
    if oracle is not None:
        # The run's product is not a document (a Q&A answer row, an in-place revision, a
        # review row), so the document-reach oracle would judge every such run 'none'. Ask
        # the scoped judge instead — the scope default from `_SCOPE_PROBES` (0259 B0001) or
        # the caller's `completion_oracle` override (0248 B0001).
        try:
            satisfied = bool(oracle())
        except Exception:
            logger.warning("ai-invoke scoped oracle failed for %s", run["run_id"], exc_info=True)
            satisfied = False
        run["docs_reached"] = 0
        run["reached_doc_ids"] = []
        run["outcome"] = "complete" if satisfied else "none"
        # Same "exited cleanly but produced nothing" signal the document oracle raises —
        # here it means the worker returned without ever writing its scope's row.
        run["oracle_mismatch"] = bool(
            not satisfied
            and run.get("end_reason") == "exited"
            and not run.get("register_errors")
            and not run.get("tool_call_misses")
            and not run.get("turn_limit_exhausted")
        )
        _finish_run_record(run)
        return

    new_docs: list[dict] = []
    try:
        new_docs = _oracle_new_docs(run)
    except Exception:
        logger.warning("ai-invoke oracle query failed for %s", run["run_id"], exc_info=True)

    workflow_decided = False
    if run.get("action_scope") == "workflow_decide":
        try:
            sequence = db_wfseq.get_sequence_by_doc_id(run["doc_ref"])
            workflow_decided = sequence is not None
            if workflow_decided and run.get("target_to_end"):
                # 0226 NR0003 §5-1: to-end scope = every worker item of the decided
                # sequence (item_seq space), not a group doc seq subtraction.
                resolved = _continuation_docs_target(
                    run["doc_ref"],
                    None,
                    pending_only=False,
                    continuation_instruction_mode=run["continuation_instruction_mode"],
                )
                if resolved is not None:
                    run["docs_target"] = resolved
                    if run.get("chain_docs_target", 0) <= 0:
                        run["chain_docs_target"] = resolved
        except Exception:
            logger.warning("ai-invoke workflow oracle failed for %s", run["run_id"], exc_info=True)

    # 0226 NR0003 §5-2: no min() clamp — an overrun (more docs than the target) stays
    # visible in docs_reached/docs_target instead of being normalized away at the end.
    docs_reached = len(new_docs)
    run["docs_reached"] = docs_reached
    run["reached_doc_ids"] = [d["doc_id"] for d in new_docs]
    if not run.get("chain_docs_accounted"):
        run["chain_docs_reached"] = int(run.get("chain_docs_reached") or 0) + docs_reached
        run["chain_docs_accounted"] = True
    # 0317 TR0011 (Q153 opt-1): under per-hop re-spawn each continuous hop delivers ONE
    # document and then hands the chain off (the self-chain withheld next_token and queued
    # the next hop). Judge such a hop against 1, not the whole remaining chain target, so an
    # intermediate hop settles "complete" (scratch cleaned) instead of a misleading "partial".
    hop_target = run["docs_target"]
    if run["mode"] == "continuous" and peek_auto_resume(run.get("group_id")) is not None:
        hop_target = 1
    if run.get("action_scope") == "workflow_decide" and run["mode"] == "single":
        run["outcome"] = "complete" if workflow_decided else "none"
    elif run.get("action_scope") == "workflow_decide" and not workflow_decided:
        # Pre-decision continuous run that never decided: no resolved target to satisfy.
        run["outcome"] = "partial" if docs_reached >= 1 else "none"
    elif docs_reached >= hop_target:
        run["outcome"] = "complete"
    elif docs_reached >= 1:
        run["outcome"] = "partial"
    else:
        run["outcome"] = "none"

    run["oracle_mismatch"] = bool(
        run["outcome"] == "none"
        and run.get("end_reason") == "exited"
        and not run.get("register_errors")
        and not run.get("tool_call_misses")
        and not run.get("turn_limit_exhausted")
    )

    _finish_run_record(run)


def _conflict_resolved(run: dict) -> bool:
    merge_id = run.get("merge_id")
    if merge_id is None:
        return False
    try:
        conflicts = git_service.list_conflicts(run["group_id"], int(merge_id))
    except GitServiceError as exc:
        # A successful complete=true resolve closes the merge session; list_conflicts
        # then returns not_found. Treat closed/missing as terminal for this scoped oracle.
        return exc.status == 404
    except Exception:
        logger.warning("ai-invoke conflict oracle failed for %s", run["run_id"], exc_info=True)
        return False
    files = conflicts.get("files") or []
    return sum(int(f.get("conflict_count") or 0) for f in files) == 0


def _finish_run_record(run: dict) -> None:
    # Scratch lifecycle (§2.7): success cleans up, everything else retains.
    scratch = Path(run["scratch_dir"])
    if run["outcome"] == "complete":
        try:
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:
            logger.warning("ai-invoke scratch cleanup failed: %s", scratch, exc_info=True)
    else:
        try:
            run["scratch_retained"] = storage_paths.to_storage_relative(scratch, run["project_id"])
        except Exception:
            run["scratch_retained"] = run["scratch_dir"]

    # Source-spill check (§2.8): only the delta vs the start-time snapshot.
    baseline = run.get("dirty_baseline")
    now_paths = _git_status_paths(Path(run["source_root"]) if run.get("source_root") else None)
    if baseline is None or now_paths is None:
        run["source_dirty"] = None
        run["source_dirty_files"] = []
    else:
        spilled = sorted(now_paths - baseline)
        run["source_dirty"] = bool(spilled)
        run["source_dirty_files"] = spilled[:20]

    run["duration_ms"] = int((time.monotonic() - run["started_mono"]) * 1000)
    run["finished_at"] = now_iso()
    run["status"] = "finished"

    # Chain-termination cleanup (0252 L0009 §3 / §5): a continuous run that ends for any
    # reason OTHER than the boundary pause (natural finish, cancel, worker failure before
    # the boundary) must not leave the paused row behind, or the bootstrap would revive a
    # ghost "paused" card for a chain that is actually over.
    if run["mode"] == "continuous":
        try:
            from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
            if run.get("end_reason") != "user_paused":
                db_paused.delete_by_group(run["group_id"])
            else:
                # pause_run snapshots at request time, before the in-flight hop reaches
                # its boundary. Refresh it after settlement so resume/bootstrap includes
                # the document that completed while the pause was pending.
                row = db_paused.get_by_group(run["group_id"])
                if row is not None:
                    db_paused.upsert(
                        group_id=row["group_id"],
                        doc_ref=row["doc_ref"],
                        paused_by=row["paused_by"],
                        paused_at=row["paused_at"],
                        continuation_target_seq=row.get("continuation_target_seq"),
                        docs_target=run.get("docs_target"),
                        docs_reached=int(run.get("docs_reached") or 0),
                        chain_id=run.get("chain_id"),
                        chain_docs_target=run.get("chain_docs_target"),
                        chain_docs_reached=int(run.get("chain_docs_reached") or 0),
                    )
        except Exception:
            logger.warning(
                "ai-invoke paused-row finalization failed for %s", run["run_id"], exc_info=True
            )

    _broadcast(run, "ai_invoke_finished", finished_payload(run))
    _broadcast(run, "group_view_refresh", {
        "group_id": run["group_id"],
        "reason": "ai_invoke_finished",
    })


def finished_payload(run: dict) -> dict:
    payload = {
        "run_id": run["run_id"],
        "group_id": run["group_id"],
        "outcome": run["outcome"],
        "docs_reached": run["docs_reached"],
        "docs_target": run["docs_target"],
        "chain_id": run.get("chain_id"),
        "chain_docs_target": int(run.get("chain_docs_target") or 0),
        "chain_docs_reached": int(run.get("chain_docs_reached") or 0),
        "reached_doc_ids": run["reached_doc_ids"],
        "end_reason": run["end_reason"],
        "exit_code": run["exit_code"],
        "last_message_received": run["last_message_received"],
        "last_message": run["last_message"],
        "provider_id": run["provider_id"],
        "attempt_no": run["attempt_no"],
        "fallback_history": run["fallback_history"],
        "register_errors": run.get("register_errors", []),
        "tool_call_misses": run.get("tool_call_misses", 0),
        "turn_limit_exhausted": bool(run.get("turn_limit_exhausted")),
        "oracle_mismatch": bool(run.get("oracle_mismatch")),
        "source_dirty": run["source_dirty"],
        "duration_ms": run["duration_ms"],
    }
    if run["source_dirty"]:
        payload["source_dirty_files"] = run["source_dirty_files"]
    if run["scratch_retained"]:
        payload["scratch_retained"] = run["scratch_retained"]
    return payload


# ── Status / cancel (P0005 scenarios 6, 8) ───────────────────────────────────

def get_status(run_id: str) -> dict:
    run = get_run_record(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if run["status"] == "finished":
        return {"ok": True, "run_id": run_id, "status": "finished", "mode": run["mode"],
                **finished_payload(run)}
    # 0226 NR0003 §5-2: count run-attributed documents (the same oracle filter the
    # final judge uses) instead of the raw group max-seq delta, which inflated the
    # live counter with drafts (auto-created N/T) and documents outside this run.
    docs_so_far = 0
    if run.get("completion_oracle") is None:
        # A scoped-oracle run targets 0 documents; counting group documents here would
        # report progress like 1/0 against work it does not measure (0248 B0001).
        try:
            docs_so_far = len(_oracle_new_docs(run))
        except Exception:
            pass
    # 0252 P0008 S4: surface the accepted pause request so a reload/poll does not
    # silently revert the card from "정지 예약됨" back to plain running.
    status = run["status"]
    if status == "running" and run.get("pause_requested"):
        status = "pause_requested"
    return {
        "ok": True,
        "run_id": run_id,
        "status": status,
        "mode": run["mode"],
        "group_id": run["group_id"],
        "docs_target": run["docs_target"],
        "docs_reached_so_far": docs_so_far,
        "chain_id": run.get("chain_id"),
        "chain_docs_target": int(run.get("chain_docs_target") or 0),
        "chain_docs_reached": (
            int(run.get("chain_docs_reached") or 0)
            + (0 if run.get("chain_docs_accounted") else docs_so_far)
        ),
        "provider": run["provider"],
        "attempt_no": run["attempt_no"],
        "started_at": run["started_at"],
        "elapsed_ms": int((time.monotonic() - run["started_mono"]) * 1000),
    }


def cancel_run(run_id: str) -> dict:
    run = get_run_record(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if run["status"] == "finished":
        # Cancel raced the natural finish — idempotent OK, no kill (L0006 §5).
        return {"ok": True, "run_id": run_id, "status": "finished"}
    run["status"] = "cancelling"
    run["cancel_event"].set()
    proc = run.get("proc")
    if proc is not None:
        try:
            process_runner.kill_process_tree(proc)
        except Exception:
            logger.warning("ai-invoke cancel kill failed for %s", run_id, exc_info=True)
    return {"ok": True, "run_id": run_id, "status": "cancelling"}


# ── Pause / resume / global active list (group 0252 D0007·P0008·L0009) ──────


def pause_run(run_id: str, user_id: str) -> dict:
    """Accept a user pause for a continuous run (L0009 §2.1).

    The run is NOT interrupted: the in-flight step runs to completion and the inbox
    self-chain withholds the next token at the step boundary (P0008 S4). The paused
    row is persisted immediately so a server restart cannot lose the user's intent
    (D0007 decision 2). Repeat pause is idempotent (upsert).
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    run = get_run_record(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    if run["mode"] != "continuous":
        raise _http_error(422, "pause_not_supported",
                          "Single-mode runs cannot be paused. Use cancel instead.",
                          run_id=run_id)
    if run["status"] == "finished":
        raise _http_error(409, "run_already_finished", "The run has already finished.",
                          run_id=run_id)
    run["pause_requested"] = True
    docs_reached = 0
    try:
        docs_reached = len(_oracle_new_docs(run))
    except Exception:
        logger.warning("ai-invoke pause oracle query failed for %s", run_id, exc_info=True)
    db_paused.upsert(
        group_id=run["group_id"],
        doc_ref=run["doc_ref"],
        paused_by=user_id,
        paused_at=now_iso(),
        continuation_target_seq=run.get("continuation_target_seq"),
        docs_target=run.get("docs_target"),
        docs_reached=docs_reached,
        chain_id=run.get("chain_id"),
        chain_docs_target=run.get("chain_docs_target"),
        chain_docs_reached=(
            int(run.get("chain_docs_reached") or 0)
            + (0 if run.get("chain_docs_accounted") else docs_reached)
        ),
    )
    return {
        "ok": True,
        "run_id": run_id,
        "group_id": run["group_id"],
        "status": "pause_requested",
        "effective_at": "step_boundary",
    }


def mark_user_paused(group_id: str) -> None:
    """Called by the inbox self-chain when the boundary check withheld the next token:
    tag the live run so its end_reason classifies as "user_paused" (P0008 S4)."""
    run = _active_run_for_group(group_id)
    if run is not None:
        run["user_paused"] = True


# ── Per-hop re-spawn for unmanned continuous chains (0317 TR0011 / Q153 opt-1) ────────

def has_active_run(group_id: Optional[str]) -> bool:
    """True when an engine-driven run is live for this group. The inbox self-chain uses this
    to separate an unmanned ENGINE chain (which re-spawns a worker — and re-resolves the
    provider — per hop) from a copy-mention semi-manned chain (no engine worker to re-spawn,
    so it must keep next_token self-continuation)."""
    if not group_id:
        return False
    return _active_run_for_group(group_id) is not None


def request_auto_resume(group_id: Optional[str], payload: dict) -> None:
    """Queue the next hop of an unmanned continuous chain for a fresh worker. Called by the
    inbox self-chain at a step boundary INSTEAD of handing next_token to the still-running
    worker; consumed by _maybe_auto_resume_hop when the current hop's worker settles."""
    if not group_id:
        return
    with _auto_resume_lock:
        _auto_resume[group_id] = dict(payload)


def peek_auto_resume(group_id: Optional[str]) -> Optional[dict]:
    """The queued next hop for this group, WITHOUT consuming it (used by the settle judge to
    recognize a hop that handed the chain off)."""
    if not group_id:
        return None
    with _auto_resume_lock:
        return _auto_resume.get(group_id)


def pop_auto_resume(group_id: Optional[str]) -> Optional[dict]:
    if not group_id:
        return None
    with _auto_resume_lock:
        return _auto_resume.pop(group_id, None)


def clear_auto_resume(group_id: Optional[str]) -> None:
    if not group_id:
        return
    with _auto_resume_lock:
        _auto_resume.pop(group_id, None)


def _maybe_auto_resume_hop(run: dict) -> None:
    """After a hop's worker finalizes, re-spawn the next hop if the inbox self-chain queued
    one (Q153 opt-1). Server-triggered automation of resume_chain: the next start_run
    re-resolves the hop's provider, delivering true per-step providers on an unmanned chain.
    Any real stop (cancel / timeout / provider exhaustion / crash) drops the queued hop rather
    than continuing past it."""
    group_id = run.get("group_id")
    pending = pop_auto_resume(group_id)
    if pending is None:
        return
    if run.get("end_reason") != "exited":
        return
    cancel_event = run.get("cancel_event")
    if cancel_event is not None and cancel_event.is_set():
        return
    # Carry the session override map AND the header default pin forward so the re-spawned hop
    # applies them too (neither is persisted on a token — both ride the run, hop to hop). The
    # base pin is what an override-less step resolves to (0317 T0013 결함 ③).
    pending = {
        **pending,
        "provider_overrides": run.get("continuation_provider_overrides"),
        "base_provider_id": run.get("continuation_base_provider_id"),
        # 0346 T0005: carry the [전달멘트] note bundle forward the same way — the first-hop-only
        # gap is the exact regression shape this fix is guarding against (D0004 구현 시 반드시
        # 지켜야 할 제약 4).
        "note_overrides": run.get("continuation_note_overrides"),
        "default_note": run.get("continuation_default_note"),
        "chain_id": run.get("chain_id"),
        "chain_docs_target": run.get("chain_docs_target"),
        "chain_docs_reached": run.get("chain_docs_reached"),
    }
    try:
        _spawn_auto_resume(group_id, pending)
    except HTTPException as exc:
        logger.warning("ai-invoke auto-resume rejected for %s: %s",
                       group_id, getattr(exc, "detail", exc))
    except Exception:
        logger.exception("ai-invoke auto-resume failed for %s", group_id)


def _spawn_auto_resume(group_id: str, pending: dict) -> None:
    """Advance the workflow one step and launch a fresh worker for it — the same
    advance_workflow → start_run handoff resume_chain uses, minus the user-pause row. The
    just-completed step was auto-approved by the self-chain, so advance_workflow resolves the
    NEXT head here, and start_run re-resolves that head's provider."""
    from modules.flow_gate.services import workflow_decision_service

    doc_ref = pending["doc_ref"]
    target_seq = pending["target_seq"]
    review_mode = bool(pending.get("review_mode"))
    instruction_mode = pending.get("instruction_mode")
    locale = pending.get("locale") or "ko"
    issued_to = pending["issued_to"]
    api_base_url = pending["api_base_url"]
    overrides = pending.get("provider_overrides")
    base_provider_id = pending.get("base_provider_id")
    note_overrides = pending.get("note_overrides")
    default_note = pending.get("default_note")
    chain_id = pending.get("chain_id")
    chain_docs_target = pending.get("chain_docs_target")
    chain_docs_reached = pending.get("chain_docs_reached")

    parts = group_id.split(".")
    project_id = parts[0]
    module = parts[1] if len(parts) > 2 and parts[1] != "none" else None

    def _issue_next() -> dict:
        adv = workflow_decision_service.advance_workflow(
            doc_id=doc_ref,
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            continuous=True,
            continuation_target_seq=target_seq,
            continuation_review_mode=review_mode,
            continuation_instruction_mode=instruction_mode,
        )
        return {
            "raw_token": adv["token"],
            "token_id": adv["token_id"],
            "scratch_dir": adv["scratch_dir"],
            "mention": adv["mention"],
        }

    start_run(
        project_id=project_id,
        module=module,
        group_id=group_id,
        doc_ref=doc_ref,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target_seq,
        continuation_review_mode=review_mode,
        continuation_instruction_mode=instruction_mode,
        continuation_locale=locale,
        issued_to=issued_to,
        api_base_url=api_base_url,
        mention_builder=lambda _raw, _scratch: None,
        issue_builder=_issue_next,
        provider_id=base_provider_id,
        continuation_provider_overrides=overrides,
        continuation_note_overrides=note_overrides,
        continuation_default_note=default_note,
        chain_id=chain_id,
        chain_docs_target=chain_docs_target,
        chain_docs_reached=chain_docs_reached,
    )


def _next_incomplete_item_seq(doc_ref: str) -> Optional[int]:
    """First workflow slot that is not complete (L0009 §2.3). Completion uses the
    existing slot definition — result document exists AND is approved — exactly as
    ai_invoke_routes._continuation_target_error judges it."""
    seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
    if seq is None:
        return None
    for item in sorted(
        db_wfseq.get_sequence_items(seq["id"]) or [],
        key=lambda i: i.get("item_seq") or 0,
    ):
        if item.get("result_doc_id") is None or item.get("result_doc_review_status") != "approved":
            return item.get("item_seq")
    return None


def resume_chain(*, group_id: str, user_id: str, api_base_url: str, locale: str = "ko") -> dict:
    """Resume a user-paused continuous chain from its next incomplete step (L0009 §2.4).

    The ORDER is the overlap protection: (1) group lock → (2) active-run check →
    (3) atomic paused-row consumption → (4) start. A row consumed by another path
    (auto-resume, another session, an external worker) surfaces as resume_conflict,
    never as a second concurrent run.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
    from modules.flow_gate.services import workflow_decision_service

    with _group_resume_lock(group_id):
        active = _active_run_for_group(group_id)
        if active is not None:
            raise _http_error(409, "run_already_active",
                              "An active run already exists for this group.",
                              run_id=active["run_id"])
        row = db_paused.delete_and_return(group_id)
        if row is None:
            raise _http_error(409, "resume_conflict",
                              "This chain was already resumed by another path.",
                              group_id=group_id)

        def _restore_row() -> None:
            # The resume did not happen — put the consumed row back so the paused
            # card survives and the user can retry (L0009 §5: 행 보존).
            try:
                db_paused.upsert(
                    group_id=row["group_id"],
                    doc_ref=row["doc_ref"],
                    paused_by=row["paused_by"],
                    paused_at=row["paused_at"],
                    continuation_target_seq=row.get("continuation_target_seq"),
                    docs_target=row.get("docs_target"),
                    docs_reached=int(row.get("docs_reached") or 0),
                    chain_id=row.get("chain_id"),
                    chain_docs_target=row.get("chain_docs_target"),
                    chain_docs_reached=int(row.get("chain_docs_reached") or 0),
                )
            except Exception:
                logger.warning("paused-row restore failed for %s", group_id, exc_info=True)

        try:
            target_seq = row.get("continuation_target_seq")
            if target_seq is None:
                # NULL target = "to the end" (DB0010 §2): resolve against the decided
                # sequence, which must exist by now (documents were being produced).
                seq = db_wfseq.get_sequence_for_member_doc(row["doc_ref"])
                items = db_wfseq.get_sequence_items(seq["id"]) if seq is not None else []
                target_seq = max(
                    (i["item_seq"] for i in items or [] if i.get("item_seq") is not None),
                    default=None,
                )
            next_seq = _next_incomplete_item_seq(row["doc_ref"])
        except Exception:
            _restore_row()
            logger.exception("ai-invoke resume lookup failed for %s", group_id)
            raise _http_error(500, "resume_lookup_failed",
                              "Could not read the workflow sequence to resume. Retry later.")
        if next_seq is None or target_seq is None or int(next_seq) > int(target_seq):
            # Every step at or below the stored target is already complete; the consumed
            # row stays deleted on purpose (self-cleaning, L0009 §2.4).
            raise _http_error(409, "nothing_to_resume",
                              "No remaining workflow step to resume.", group_id=group_id)

        def _issue_resume() -> dict:
            # Same advance_workflow path as the continuous first hop / every inbox
            # self-chain hop, so instruction heads (N/T) keep their server-side
            # auto-creation instead of being handed to the AI to write (0226 B0001 ④).
            adv = workflow_decision_service.advance_workflow(
                doc_id=row["doc_ref"],
                issued_to=user_id,
                api_base_url=api_base_url,
                locale=locale,
                continuous=True,
                continuation_target_seq=target_seq,
                continuation_review_mode=False,
            )
            return {
                "raw_token": adv["token"],
                "token_id": adv["token_id"],
                "scratch_dir": adv["scratch_dir"],
                "mention": adv["mention"],
            }

        parts = group_id.split(".")
        project_id = parts[0]
        module = parts[1] if len(parts) > 2 and parts[1] != "none" else None
        try:
            return start_run(
                project_id=project_id,
                module=module,
                group_id=group_id,
                doc_ref=row["doc_ref"],
                action_scope="new",
                mode="continuous",
                continuation_target_seq=target_seq,
                continuation_review_mode=False,
                continuation_instruction_mode="auto_approved",
                continuation_locale=locale,
                issued_to=user_id,
                api_base_url=api_base_url,
                mention_builder=lambda _raw, _scratch: None,
                issue_builder=_issue_resume,
                chain_id=row.get("chain_id"),
                chain_docs_target=row.get("chain_docs_target"),
                chain_docs_reached=row.get("chain_docs_reached"),
            )
        except HTTPException:
            _restore_row()
            raise
        except LookupError as exc:
            _restore_row()
            raise _http_error(404, "resume_advance_unavailable", str(exc))
        except ValueError as exc:
            _restore_row()
            raise _http_error(409, "resume_advance_blocked", str(exc))


def _open_q_doc_ids(group_id: str) -> list[str]:
    """Open Q documents of the group — the live source for pending_q_doc_ids
    (DB0010 §4: derived, never stored; answering flips the Q doc to 'answered')."""
    try:
        docs = db_docs.get_documents_by_group_id(group_id)
    except Exception:
        logger.warning("open-Q lookup failed for %s", group_id, exc_info=True)
        return []
    return sorted(
        d["doc_id"] for d in docs
        if (d.get("type_code") or "").upper() == "Q" and (d.get("status") or "") == "open"
    )


def active_all(user_id: str) -> dict:
    """Global widget bootstrap (P0008 S1): every live run the user started plus every
    chain the user paused — the refresh-proof source the miniplayer restores from."""
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    with _runs_lock:
        candidates = [
            run for run in _runs.values()
            if run.get("issued_to") == user_id and run["status"] != "finished"
        ]
    runs = []
    for run in candidates:
        try:
            status = get_status(run["run_id"])
        except HTTPException:
            continue  # finished/expired between the snapshot and the status read
        status["doc_ref"] = run["doc_ref"]
        runs.append(status)

    paused = []
    try:
        rows = db_paused.list_by_user(user_id)
    except Exception:
        logger.warning("paused-chain list failed for %s", user_id, exc_info=True)
        rows = []
    for row in rows:
        paused.append({
            "group_id": row["group_id"],
            "doc_ref": row["doc_ref"],
            "mode": row.get("mode") or "continuous",
            "paused_by": row["paused_by"],
            "paused_at": row["paused_at"],
            "continuation_target_seq": row.get("continuation_target_seq"),
            "docs_target": row.get("docs_target"),
            "docs_reached": int(row.get("docs_reached") or 0),
            "chain_id": row.get("chain_id"),
            "chain_docs_target": row.get("chain_docs_target"),
            "chain_docs_reached": int(row.get("chain_docs_reached") or 0),
            "pending_q_doc_ids": _open_q_doc_ids(row["group_id"]),
        })
    return {"ok": True, "runs": runs, "paused": paused}


# ── SSE ──────────────────────────────────────────────────────────────────────

def _broadcast(run: dict, event_type: str, payload: dict) -> None:
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )

        broadcast_event_threadsafe(
            FlowEvent(
                event_type=event_type,
                payload=payload,
                audience="*",
                project=run["project_id"],
                group_id=run["group_id"],
                doc_id=run["doc_ref"],
            )
        )
    except Exception:
        logger.warning("ai-invoke SSE broadcast failed", exc_info=True)
