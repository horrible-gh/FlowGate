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

import hashlib
import json
import logging
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import HTTPException

from modules.flow_gate.db import conversation_turns as db_conversation_turns
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import questions as db_questions
from modules.flow_gate.db import question_items as db_question_items
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate import template_provision
from modules.flow_gate.services import git_service, invoke_mention_service, process_runner, q_service, token_service
from modules.flow_gate.services.git_service import GitServiceError
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.utils.api_key_crypto import ApiKeyCryptoError

logger = logging.getLogger(__name__)

# ── Parameters (L0006 §1) ─────────────────────────────────────────────────────
RUN_TIMEOUT_BASE_SEC = 3600      # per target document
RUN_TIMEOUT_CAP_SEC = 14400      # run total = min(BASE × docs_target, CAP)
FAST_FAIL_WINDOW_SEC = 15        # nonzero exit + 0 docs inside this window ⇒ startup failure
SCRATCH_RETENTION_DAYS = 7       # failed-run scratch retention
LAST_MESSAGE_MAX_BYTES = 16384   # keep the tail, truncate the front
OUTPUT_TAIL_BYTES = 8192         # stdout/stderr auxiliary tails
# 0446 T0016 2/3-3: how many spilled source paths a finished hop reports and stores. It
# was a bare `[:20]` inside _finalize_run; naming it is what lets the durable row, the
# finished payload and the rework handoff all provably use the SAME 20 rather than three
# independently-chosen limits.
SOURCE_DIRTY_FILES_LIMIT = 20
API_MAX_TURNS_PER_DOC = 4        # API agent loop cap = docs_target × 4
API_MAX_TOOL_NUDGES = 2          # retry when the model claims completion without using the tool
ORACLE_SETTLE_SEC = 3            # wait before judging (late-commit slack)
CONCURRENT_RUNS_PER_GROUP = 1

# ── 0359 L0007 §1: no-output retry, hop budget, stop codes ───────────────────
# NR0003 §3: the hop loop only ever moved FORWARD ("a document was registered"); a hop that
# ran, produced nothing and exited 0 fell straight out of the loop with no retry, no record
# and no signal. These parameters bound the retry branch that closes that hole.
HOP_TIMEOUT_SEC = 3600           # continuous hop budget — fixed, never scaled by slots left
# flowgate.default.0400 M0005: the duration section's fixed option list, as seconds —
# 30/45/60/90/120/180/240 minutes. A pick outside this range is rejected by the route (422).
# 0446 T0010 §3-2: these two bounds are now shared by BOTH entry points — ContinuousWorkDialog
# (a continuous hop) and AiInvokeDialog (a single rejection rework) — and by the route's own
# validation, deliberately with no single-run-only pair beside them; a second pair would let
# the screen, the route and the engine disagree about where the edge is. With no pick at all
# the mode's own default still applies: HOP_TIMEOUT_SEC for a continuous hop, and
# min(RUN_TIMEOUT_BASE_SEC × max(1, docs_target), RUN_TIMEOUT_CAP_SEC) for a single run.
STEP_TIMEOUT_MIN_SEC = 1800
STEP_TIMEOUT_MAX_SEC = 14400
NO_OUTPUT_MAX_ATTEMPTS = 2       # per hop: the first attempt + exactly ONE no-output retry on the SAME provider
# flowgate.default.0443 T0002 (R0001): ContinuousWorkDialog's 기본 설정 탭 "재시작 횟수"
# select — the no-output retry count is now a per-run pick instead of the fixed constant
# above. -1 is the "될 때까지" sentinel (unlimited attempts); 0/1/2/3 are restart counts
# (RESTARTS, not total attempts — total = restarts + 1). Default matches the constant's
# pre-existing behavior exactly: 1 restart == NO_OUTPUT_MAX_ATTEMPTS(2) total attempts.
RESTART_MAX_ATTEMPTS_CHOICES = (-1, 0, 1, 2, 3)
RESTART_MAX_ATTEMPTS_DEFAULT = 1
RETRY_MIN_REMAINING_SEC = 300    # with less budget than this left, do not open another attempt
LAST_MESSAGE_EXCERPT_BYTES = 512 # list/notification excerpt of the worker's last message
RUN_LIST_LIMIT_DEFAULT = 20      # GET /ai-invoke/runs default page size (L0007 §2.10.3)
RUN_LIST_LIMIT_MAX = 100         # GET /ai-invoke/runs clamp ceiling

# ── 0446 T0014 §2: no-progress threshold vs. absolute ceiling ────────────────
# T0010 made the budget above choosable; NR0003 then measured what it is actually
# spent on — hops still registering documents when the hour ran out, and workers that
# had been dead for fifty minutes when it did. One number cannot separate those two,
# so from here the budget `_resolve_timeout_sec` returns is read as the NO-PROGRESS
# threshold (how long a run may show nothing new — `_stall_remaining_sec`), and the
# run's hard ceiling is named separately (`_absolute_remaining_sec`). The formula, the
# 1800..14400 bounds and the 30/45/60/90/120/180/240 list are deliberately untouched:
# this T raises nobody's default, it only stops charging a working run for the time it
# spent working.
#
# RUN_TIMEOUT_CAP_SEC is already exactly four hours — and already the budget a
# `target_to_end` run gets — so the ceiling REUSES that constant (see
# `_absolute_cap_sec`) rather than adding a second 14400 literal that could drift away
# from it. The distinction lives in the helper names and their comments, not in the
# number: `_stall_remaining_sec` is the threshold, `_absolute_remaining_sec` the
# ceiling.
STALL_POLL_INTERVAL_SEC = 15                 # watchdog tick: short, finite, bounded
# `_git_status_paths` shells out with its own 30s timeout, so a tick already inside it
# needs more than one tick length to come back and be joined.
STALL_WATCHDOG_JOIN_SEC = 40
# ── 0414 L0008 §1: the [검수] gate's parameters and its seven stop codes ──────────────
# Nothing about a review round is STORED. Rounds used, "which stage is running", whether a
# rejection already happened — all of it is re-derived from document_reviews plus the
# document's revision_no/doc_review_status on every read (§2.3), which is what makes a
# restart, a cold [이어서 진행] and an in-flight hop boundary all agree for free.
REVIEW_COUNT_VALUES = frozenset({-1, 0, 1, 2, 3})
REVIEW_COUNT_DEFAULT = 0                 # no selection = this step is not reviewed
# -1 ("until it passes") has NO ceiling: review and rework repeat until a `pass`
# verdict. No round count hands the chain to a human. What keeps it from spinning is
# the pair of loop breakers in _check_expected_progress — a rework that raised no new
# revision, and the same findings coming back twice.
REVIEW_ROUNDS_NO_LIMIT = -1              # resolve_round_limit's answer for count == -1
REVIEW_STALL_ROUNDS = 2                  # this many identical issues digests = no progress
REVIEW_REASON_MAX_FINDINGS = 20          # findings copied into the auto-rejection text
# Deliberately equal to pipeline_service.AI_RESPONSE_MAX_LEN: the rejection and the response
# to it sit side by side on the same screen, so one cap serves both.
REVIEW_REASON_MAX_CHARS = 4000
REVIEW_VERDICTS = frozenset({"pass", "issues", "hold"})
WORK_HOP_KIND = "work"
REVIEW_HOP_KIND = "review"
REWORK_HOP_KIND = "rework"
# A review/rework hop is mode="single" but it IS one of the chain's hops, so it takes the
# chain's per-hop budget instead of the single-run formula. Both resolve to 3600 when no
# pick was made, which is what a single run already got — no existing run moves (L0008 §5).
CHAIN_MEMBER_HOP_KINDS = frozenset({REVIEW_HOP_KIND, REWORK_HOP_KIND})

# L0008 §1.2. `review_hold` is deliberately NOT reused: that name already belongs to
# [AI 검토 모드]'s stop, and a reviewer's `hold` VERDICT is a different event.
# 0414 M0020 + the 0022-TR rejection: the gate no longer stops on a round COUNT at all.
# A finite budget cannot be "exhausted" (its last round is reworked and then the step
# advances), and -1 has no ceiling left to reach. BOTH codes stay because chains parked
# before those changes still carry them, and their cards must keep rendering the same
# sentence and notification. The cap code additionally became resumable (see
# RESUMABLE_STOP_CODES below): with the round-count branch gone from resolve_review_gate,
# resuming one just re-derives the gate like any fresh -1 chain.
REVIEW_EXHAUSTED_STOP_CODE = "review_exhausted"          # legacy rows only, never emitted now
REVIEW_CAP_REACHED_STOP_CODE = "review_cap_reached"      # legacy rows only, never emitted now
REVIEW_VERDICT_HOLD_STOP_CODE = "review_verdict_hold"    # the reviewer returned hold
REVIEW_STALLED_STOP_CODE = "review_stalled"              # no new revision, or repeated findings
REVIEW_NO_VERDICT_STOP_CODE = "review_no_verdict"        # the review hop left no review row
REVIEW_REJECT_DENIED_STOP_CODE = "review_reject_denied"  # issuer lacks document.reject
REVIEW_REJECT_FAILED_STOP_CODE = "review_reject_failed"  # the reject transition raised
REVIEW_STOP_CODES = frozenset({
    REVIEW_EXHAUSTED_STOP_CODE, REVIEW_CAP_REACHED_STOP_CODE, REVIEW_VERDICT_HOLD_STOP_CODE,
    REVIEW_STALLED_STOP_CODE, REVIEW_NO_VERDICT_STOP_CODE, REVIEW_REJECT_DENIED_STOP_CODE,
    REVIEW_REJECT_FAILED_STOP_CODE,
})

# L0007 §4.2 — one criterion: can re-running this hop still do the work? Human-triage stops
# (head/approve/advance) and intended stops (cancel) are deliberately NOT resumable.
RESUMABLE_STOP_CODES = frozenset({
    "no_output_exhausted", "providers_exhausted", "timeout", "user_paused",
    "question_pending",
    # 0414 L0008 §1.2: of the seven review stops, only this one answers "would re-running
    # the hop have a chance?" with yes — the reviewer simply said nothing. Four of the
    # remaining five need a person: a verdict says hold, the loop is not making progress,
    # or a permission/transition is broken.
    REVIEW_NO_VERDICT_STOP_CODE,
    # 0414 0022-TR rejection review: resolve_review_gate no longer has a round-count branch
    # at all, so a legacy review_cap_reached row answers "yes" too — resuming it re-derives
    # the gate from the current reviews/revision rows exactly like a fresh -1 chain, and
    # review+rework simply continues until a pass. REVIEW_EXHAUSTED_STOP_CODE stays out:
    # nothing here has verified its resume path the way the cap row's was verified below.
    REVIEW_CAP_REACHED_STOP_CODE,
})
# L0007 §2.11 — every stop code that must reach a human. The set is SPLIT by speaker: the
# engine fires the three below (the inbox never sees them — no request arrives), the inbox
# self-chain fires the rest (they also happen on a copy-mention chain with no engine run).
# Disjoint by construction ⇒ a double notification is impossible.
ENGINE_NOTIFY_STOP_CODES = frozenset({
    "no_output_exhausted", "providers_exhausted", "timeout",
    # 0414 L0008 §1.2: every review stop is spoken by the ENGINE — the inbox never sees
    # these branches (no request arrives). Putting them here and nowhere else keeps
    # INBOX_NOTIFY_STOP_CODES disjoint, so a double notification stays impossible.
    *REVIEW_STOP_CODES,
})
# "question_pending" (NR0003 follow-up proposal 1/3) is deliberately NOT in this set: it means
# the hop stopped because it is waiting on a human answer, not because it failed, so it
# must not raise the "continuous work failed" notification the three codes above do.
INBOX_NOTIFY_STOP_CODES = frozenset({
    "head_slot_mismatch", "approve_denied", "approve_failed", "advance_blocked",
})
NOTIFY_STOP_CODES = ENGINE_NOTIFY_STOP_CODES | INBOX_NOTIFY_STOP_CODES
assert not (ENGINE_NOTIFY_STOP_CODES & INBOX_NOTIFY_STOP_CODES), (
    "L0007 §2.11: the engine and inbox notification sets must stay disjoint"
)

# ── Hop handoff durability (0406 T0022 work item 4) ─────────────────────────
# The next hop's intent lived only in a process-memory dict (_auto_resume). A server
# restart wiped it wholesale; if the current hop was not exited the queue was popped
# and then discarded, and a _spawn_auto_resume exception ended as a single log line.
# FlowGate is a time machine: dropping the queue must not erase the intent. That intent
# is kept as a system stop row and deleted **only after the next hop actually starts**.
HOP_HANDOFF_STOP_CODE = "hop_handoff"
# A normal handoff completes within 0-3 seconds (measured in 0406). The durable row
# exists during that window too, so within this grace period it is NOT drawn as a stop
# card in the miniplayer: if "stopped" flickers on every successful handoff the badge
# means nothing. A row still there past the grace really did break, and must show.
HOP_HANDOFF_GRACE_SEC = 120
# A handoff whose in-memory queue was lost to a restart. Switching to this code at
# startup skips the grace check so it becomes a [resume] card immediately.
HOP_HANDOFF_INTERRUPTED_STOP_CODE = "hop_handoff_interrupted"
# The queue was there but the follow-up hop could not be spawned (_spawn_auto_resume).
HOP_HANDOFF_FAILED_STOP_CODE = "hop_handoff_failed"

# T0005 SS2: the single literal a launch-time 422 and the active-all resume-state
# preview must agree on, so a paused card advance warning and its later toast are
# word-for-word the same sentence.
PROVIDER_UNAVAILABLE_CODE = "provider_unavailable"
PROVIDER_UNAVAILABLE_MESSAGE = "The selected AI provider is not enabled for this project."

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
# 0401 NR0003 §4 / T0004 work item 7: which UTC date the counter above was last floored
# against the durable stores for. None until the first _next_run_id() call.
_run_counter_floor_date: Optional[str] = None

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


# T0004 work item 6 / NR0003 finding 6: the worktree_unavailable 409 always went out in
# Korean with no locale branch. It reuses the same locale-dictionary pattern as
# remote_tool_service._ERROR_MESSAGES / _CUSTOM_ERROR_MESSAGES.
_WORKTREE_UNAVAILABLE_COPY = {
    "ko": (
        "이 그룹의 작업 폴더(워크트리)를 확인할 수 없어 AI 실행을 시작하지 않습니다 "
        "(원인: {cause}). 워크트리 없이 실행하면 작업이 원본 체크아웃(main)에 "
        "남습니다. 그룹 Git 상태를 복구한 뒤 다시 실행하십시오."
    ),
    "en": (
        "AI execution was not started because this group's working folder (worktree) "
        "could not be confirmed (cause: {cause}). Running without a worktree would leave "
        "the work in the original checkout (main). Recover the group's Git state and "
        "retry."
    ),
    "ja": (
        "このグループの作業フォルダ(worktree)を確認できないため、AI実行を開始しません"
        "(原因: {cause})。worktreeなしで実行すると、作業が元のチェックアウト(main)に"
        "残ります。グループのGit状態を復旧してから再実行してください。"
    ),
}

# Found while reviewing T0004 (flagged by the generalised static guard): the
# run_id_collision 409 using the same _http_error helper was un-branched Korean too.
# It reuses the continuation_locale already received in the same function (start_run).
_RUN_ID_COLLISION_COPY = {
    "ko": "실행 번호 발급이 충돌했습니다. 다시 시도해 주세요.",
    "en": "Run-id issuance collided. Please try again.",
    "ja": "実行番号の発行が競合しました。もう一度お試しください。",
}


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


def _require_group_worktree(
    project_id: str, module: str, group_id: str, branch: str, locale: Optional[str] = None,
) -> None:
    """Refuse to launch a run that would execute in the base tree (0299 R0001).

    This is the root cause R0001 describes: AI workers sometimes work on the main branch
    instead of the one assigned to them. The remote CRUD endpoints have been gated since
    0205 (remote_tool_service._resolve_root_for_mutation), but the invoked worker's
    *cwd* was not — it came from the fallback-first resolver, so a group whose
    worktree was missing got a CLI agent pointed straight at the base checkout, free
    to edit files there with its own tools. The TR work-scope check (0299 D0004) catches
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
    normalized_locale = template_provision.normalize_locale(locale)
    raise _http_error(
        409, "worktree_unavailable",
        _WORKTREE_UNAVAILABLE_COPY[normalized_locale].format(cause=cause),
        group_id=group_id, cause=cause, provision_error=provision_error,
    )


def _next_run_id() -> str:
    global _run_counter, _run_counter_floor_date
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    with _runs_lock:
        if _run_counter_floor_date != date_str:
            # 0401 NR0003 §4 / T0004 work item 7: this counter starts at 0 per process, so
            # the first run_id a fresh process mints today can already belong to a run
            # an earlier process issued -- ai_invoke_runs.upsert() is keyed on run_id, so
            # that earlier row would be silently overwritten the next time THIS process's
            # same-numbered run finalizes. Floor once per date against whatever the
            # durable stores already show as today's highest serial: finished runs AND
            # leases still open on one (an open lease has no ai_invoke_runs row yet).
            # Best-effort -- a lookup failure keeps the counter as-is rather than
            # blocking a run from starting at all.
            try:
                from modules.flow_gate.db import ai_invoke_runs as db_runs

                floor = max(
                    db_runs.max_serial_for_date(date_str),
                    db_group_ai_leases.max_serial_for_date(date_str),
                )
                _run_counter = max(_run_counter, floor)
            except Exception:
                logger.warning("run_id floor lookup failed for %s", date_str, exc_info=True)
            _run_counter_floor_date = date_str
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


def is_run_live(run_id: str) -> bool:
    """Is *run_id* an admission this process still tracks and has not finished?

    The one definition of "alive" shared by the lease-owner mutation gate
    (``mutation_policy._locked``), the manual lease-release guard below, and the
    ``GET /ai-invoke/leases`` route — a duplicated check here is exactly how the
    screen and the server told two different stories before (0401 NR0003 §3 cause 3).
    """
    run = get_run_record(run_id)
    return run is not None and run.get("status") != "finished"


def _record_orphaned_lease_run(lease_row: dict, end_reason: str) -> None:
    """Give a dead lease's run a durable end record, if it doesn't already have one
    (0401 NR0003 / T0004 items 1-2). A lease row alone (group/run/token/timestamps) has
    no doc_ref or mode — both live on the token it was issued with — so this looks
    the token up. Best-effort: a run this cannot explain still gets its lease
    cleared by the caller either way, it just won't carry the extra explanation.
    """
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    run_id = str(lease_row.get("run_id") or "")
    if not run_id or db_runs.get(run_id) is not None:
        return
    doc_ref, mode = "", "single"
    token_id = lease_row.get("token_id")
    if token_id:
        token = db_tokens.get_by_id(token_id)
        if token:
            doc_ref = token.get("doc_ref") or ""
            mode = "continuous" if token.get("continuation_target_seq") is not None else "single"
    stamp = now_iso()
    started = lease_row.get("acquired_at") or stamp
    db_runs.upsert({
        "run_id": run_id,
        "group_id": lease_row["group_id"],
        "project_id": lease_row["project_id"],
        "doc_ref": doc_ref,
        "mode": mode,
        "outcome": "none",
        "end_reason": end_reason,
        "resumable": False,
        "started_at": started,
        "finished_at": stamp,
        "created_at": started,
        "updated_at": stamp,
    })


def startup_recover_leases() -> int:
    """Reclaim AI-run leases orphaned by a server restart (0401 NR0003 / T0004 item 1).

    Called once from ``server/startup.py``, before the app accepts traffic. Every
    lease still on the table at that instant is dead: this process's own ``_runs``
    registry starts empty, so nothing it has admitted yet could hold one. Bound to
    the process's own start time so a multi-process deployment cannot reclaim a
    lease a sibling process is mid-admission on.
    """
    before = now_iso()
    victims = db_group_ai_leases.reclaim_orphaned(before)
    for row in victims:
        try:
            _record_orphaned_lease_run(row, "orphaned_by_restart")
        except Exception:
            logger.warning(
                "orphaned-lease end record failed for run %s", row.get("run_id"), exc_info=True
            )
    if victims:
        logger.warning("[ai_invoke] startup reclaimed %d orphaned group lease(s)", len(victims))
    # 0406 T0022 work item 4: the same startup also recovers hop handoffs that were cut
    # off. It pairs with lease reclaim: one recovers the lock, the other the intent.
    startup_recover_handoffs()
    return len(victims)


def force_release_group_lease(group_id: str) -> dict:
    """Manually release a group's lease from the blocked screen (0401 T0004 item 2).

    Refuses (and leaves the lease untouched) when the lease's run is still live —
    the same :func:`is_run_live` gate everything else uses, so this can never cut
    off a run that is actually working. Only a lease whose run this process cannot
    find, or has already finished, is orphaned and eligible.
    """
    lease = db_group_ai_leases.get(group_id)
    if lease is None:
        raise _http_error(404, "lease_not_found", "No AI run lease is held for this group.",
                          group_id=group_id)
    run_id = str(lease.get("run_id") or "")
    if is_run_live(run_id):
        raise _http_error(409, "run_still_live",
                          "This group's AI run is still active; it cannot be force-released.",
                          group_id=group_id, run_id=run_id)
    released = db_group_ai_leases.release(group_id, run_id)
    if released:
        try:
            _record_orphaned_lease_run(lease, "orphaned_by_manual_release")
        except Exception:
            logger.warning("orphaned-lease end record failed for run %s", run_id, exc_info=True)
    return {"ok": True, "group_id": group_id, "run_id": run_id, "released": bool(released)}


# ── 0359 L0007 §2.10.2~3: run lookup that survives a restart (bundle 4) ──────────
# Live runs never leave `_runs` (a process only forgets them on restart), so the
# DB fallback below is only ever consulted for a run that finished in an EARLIER
# process. Memory wins whenever both would answer — the moment right after
# finalize persists the row is the only overlap, and it is the freshest source.

def get_run_detail(run_id: str) -> dict:
    """Detail lookup for GET /ai-invoke/{run_id} (L0007 §2.10.2).

    A live or same-process-finished run answers exactly as :func:`get_status`
    always has — only ``persisted`` is added, so no existing caller's response
    shape moves. A run this process never saw falls back to the `ai_invoke_runs`
    row DB0008 kept for it.
    """
    if get_run_record(run_id) is not None:
        payload = get_status(run_id)
        payload["persisted"] = False
        return payload

    from modules.flow_gate.db import ai_invoke_runs as db_runs

    row = db_runs.get(run_id)
    if row is None:
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")
    payload = _run_detail_from_row(row)
    payload["persisted"] = True
    return payload


def _run_detail_from_row(row: dict) -> dict:
    """Shape a persisted `ai_invoke_runs` row like the live `finished_payload` (same
    field names) so a client renders both through one path."""
    payload = {
        "ok": True,
        "run_id": row["run_id"],
        "status": "finished",
        "mode": row["mode"],
        "group_id": row["group_id"],
        "doc_ref": row.get("doc_ref"),
        "outcome": row.get("outcome"),
        "docs_reached": row.get("docs_reached"),
        "docs_target": row.get("docs_target"),
        "reached_doc_ids": row.get("reached_doc_ids"),
        "end_reason": row.get("end_reason"),
        "exit_code": row.get("exit_code"),
        "last_message_received": row.get("last_message") is not None,
        "last_message": row.get("last_message"),
        "last_message_excerpt": row.get("last_message_excerpt"),
        "provider_id": row.get("provider_id"),
        "provider_name": row.get("provider_name"),
        "attempt_no": row.get("attempt_no"),
        "fallback_history": row.get("fallback_history"),
        "register_errors": row.get("register_errors"),
        "tool_call_misses": row.get("tool_call_misses"),
        "turn_limit_exhausted": bool(row.get("turn_limit_exhausted")),
        "oracle_mismatch": bool(row.get("oracle_mismatch")),
        "source_dirty": row.get("source_dirty"),
        "scratch_retained": row.get("scratch_retained"),
        "duration_ms": row.get("duration_ms"),
        "stop_code": row.get("stop_code"),
        "stop_reason": row.get("stop_reason"),
        "resumable": bool(row.get("resumable")),
        "hop_item_seq": row.get("hop_item_seq"),
        "token_id": row.get("token_id"),
        "issued_to": row.get("issued_to"),
        "attempts_used": row.get("attempts_used"),
        "attempts_max": row.get("attempts_max"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "timeout_sec": row.get("timeout_sec"),
        "deadline_at": row.get("deadline_at"),
        # 0406 T0022 items 3 and 5: the same questions must stay answerable after the run
        # ends: who ran this hop, what the server handled, whether a handoff note went in.
        "worker_document_type": row.get("worker_document_type"),
        "continuation_instruction_mode_requested": row.get(
            "continuation_instruction_mode_requested"
        ),
        "continuation_instruction_mode_normalized": row.get(
            "continuation_instruction_mode_normalized"
        ),
        "continuation_instruction_mode_fallback_applied": bool(
            row.get("continuation_instruction_mode_fallback_applied")
        ),
        "auto_handled_item_seqs": row.get("auto_handled_item_seqs") or [],
        "prompt_message_source": row.get("prompt_message_source"),
        "prompt_common_default_applied": bool(row.get("prompt_common_default_applied")),
        "prompt_user_message_length": row.get("prompt_user_message_length"),
        "prompt_user_message_sha256": row.get("prompt_user_message_sha256"),
        "prompt_final_length": row.get("prompt_final_length"),
        "prompt_final_sha256": row.get("prompt_final_sha256"),
        # 0446 T0016 §3-4: same names, same meaning as `finished_payload` — this is the
        # restart half of the pair, and a field that reads differently here than it did in
        # memory is exactly the divergence that section forbids.
        "timeout_kind": row.get("timeout_kind"),
        "timeout_diagnosis": row.get("timeout_diagnosis"),
        "stdout_tail": row.get("stdout_tail"),
        "stderr_tail": row.get("stderr_tail"),
    }
    # `finished_payload` carries the path list only when something actually spilled. Mirror
    # that, so the key's PRESENCE means the same thing on both sides of a restart.
    if row.get("source_dirty"):
        payload["source_dirty_files"] = list(row.get("source_dirty_files") or [])
    return payload


def list_live_runs(*, group_id: Optional[str] = None, project_id: Optional[str] = None) -> list[dict]:
    """In-memory runs still going, scoped to exactly one of group/project (caller's
    choice) — the "live" half of GET /ai-invoke/runs (L0007 §2.10.3)."""
    with _runs_lock:
        snapshot = list(_runs.values())
    items = []
    for run in snapshot:
        if run["status"] == "finished":
            continue
        if group_id is not None and run["group_id"] != group_id:
            continue
        if project_id is not None and run["project_id"] != project_id:
            continue
        items.append(_run_list_item_live(run))
    return items


def _run_list_item_live(run: dict) -> dict:
    status = run["status"]
    if status == "running" and run.get("pause_requested"):
        status = "pause_requested"
    provider = run.get("provider") or {}
    return {
        "run_id": run["run_id"],
        "group_id": run["group_id"],
        "project_id": run["project_id"],
        "doc_ref": run["doc_ref"],
        "mode": run["mode"],
        "status": status,
        "provider_id": provider.get("id") or run.get("provider_id"),
        "provider_name": provider.get("name"),
        "docs_target": run.get("docs_target"),
        "attempts_used": int(run.get("attempts_used") or 0),
        "hop_item_seq": run.get("hop_item_seq"),
        "started_at": run.get("started_at"),
        # L0007 §2.10.3: the finish-only columns stay null on a live row — present, not
        # omitted, so a client can render every item through the same column set.
        "outcome": None,
        "docs_reached": None,
        "end_reason": None,
        "stop_code": None,
        "resumable": None,
        "finished_at": None,
        "duration_ms": None,
        "last_message_excerpt": None,
    }


def _run_list_item_stored(row: dict) -> dict:
    return {
        "run_id": row["run_id"],
        "group_id": row["group_id"],
        "project_id": row.get("project_id"),
        "doc_ref": row.get("doc_ref"),
        "mode": row["mode"],
        "status": "finished",
        "provider_id": row.get("provider_id"),
        "provider_name": row.get("provider_name"),
        "docs_target": row.get("docs_target"),
        "attempts_used": row.get("attempts_used"),
        "hop_item_seq": row.get("hop_item_seq"),
        "started_at": row.get("started_at"),
        "outcome": row.get("outcome"),
        "docs_reached": row.get("docs_reached"),
        "end_reason": row.get("end_reason"),
        "stop_code": row.get("stop_code"),
        "resumable": bool(row.get("resumable")),
        "finished_at": row.get("finished_at"),
        "duration_ms": row.get("duration_ms"),
        "last_message_excerpt": row.get("last_message_excerpt"),
    }


def list_runs(*, group_id: Optional[str] = None, project: Optional[str] = None,
               limit: Optional[int] = None) -> dict:
    """GET /ai-invoke/runs (L0007 §2.10.3): live runs (memory) merged with finished
    runs (DB0008's `ai_invoke_runs`), newest first. Exactly one of group_id/project
    scopes the query — the route layer validates format and permission; this
    function still enforces the XOR and existence checks so a direct caller gets
    the same contract.
    """
    if (group_id is None) == (project is None):
        raise HTTPException(status_code=422, detail={
            "code": "validation_failed",
            "errors": [{"loc": "group_id",
                        "msg": "exactly one of group_id or project is required"}],
        })
    limit_value = limit if limit else RUN_LIST_LIMIT_DEFAULT
    limit_value = max(1, min(limit_value, RUN_LIST_LIMIT_MAX))
    project_id = project if project is not None else group_id.split(".", 1)[0]
    if db_projects.get_by_id(project_id) is None:
        raise _http_error(404, "project_not_found", f"Project not found: {project_id}")

    from modules.flow_gate.db import ai_invoke_runs as db_runs

    if group_id is not None:
        live_items = list_live_runs(group_id=group_id)
        fetch_limit = limit_value + len(live_items)
        stored_rows = db_runs.list_by_group(group_id, fetch_limit)
        total = db_runs.count_by_group(group_id)
    else:
        live_items = list_live_runs(project_id=project_id)
        fetch_limit = limit_value + len(live_items)
        stored_rows = db_runs.list_by_project(project_id, fetch_limit)
        total = db_runs.count_by_project(project_id)

    live_ids = {item["run_id"] for item in live_items}
    stored_ids = {row["run_id"] for row in stored_rows}
    stored_items = [_run_list_item_stored(row) for row in stored_rows if row["run_id"] not in live_ids]
    merged = live_items + stored_items
    merged.sort(key=lambda item: (item.get("started_at") or "", item["run_id"]), reverse=True)
    # A live run has no stored row yet (it has not finished) — it inflates `total`
    # by exactly one over what the table can currently count.
    unstored_live = sum(1 for item in live_items if item["run_id"] not in stored_ids)
    total = total + unstored_live
    items = merged[:limit_value]
    has_more = total > len(items)

    result = {"ok": True, "limit": limit_value, "total": total, "has_more": has_more, "items": items}
    if group_id is not None:
        result["group_id"] = group_id
    else:
        result["project"] = project_id
    return result


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
    # 0352 T0004 §3.5: only the N/T item_seqs the ai_direct chain selected for server
    # auto-handling are excluded from the worker's document count — an unselected ai_direct
    # N/T is still a real worker document and must stay counted.
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[int]:
    """docs_target in the workflow item_seq coordinate system (0226 B0001 / NR0003 §5-1).

    ``continuation_target_seq`` lives in the workflow-sequence item_seq space, which is
    unrelated to the group document seq space (item_seq turns sparse after
    edit_workflow_pending renumbers the pending tail past max_item_seq). The former
    ``target - get_group_max_seq()`` subtraction mixed the two spaces, yielding
    arbitrary targets (the reported 0/9 and 4/3). Count instead the sequence items up
    to the target that will land as worker-visible documents. In ``auto_approved``, N/T
    instruction heads are server-created drafts and remain excluded; in ``ai_direct``
    they are independent worker documents and are counted UNLESS the chain selected that
    exact item_seq for server auto-handling (0353 B0001 / NR0003 §8; 0352 T0004 §2).

    ``pending_only=True`` counts only unrealized slots (start-of-run admission).
    The to-end resolution paths pass False: the whole freshly-decided sequence is the
    run's scope regardless of what has been realized by the time of the query.
    ``target_item_seq=None`` means "no upper bound" (to-end).
    Returns None when the doc has no decided workflow sequence.
    """
    from modules.flow_gate.services.workflow_decision_service import is_auto_handled_step

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
        if is_auto_handled_step(
            head_type=item.get("type"),
            item_seq=item_seq,
            instruction_mode=continuation_instruction_mode,
            auto_approve_item_seqs=continuation_auto_approve_item_seqs,
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


def _scope_oracle_retry_open(mode: Optional[str], action_scope: Optional[str],
                             scope_oracle_run: Optional[bool]) -> bool:
    """0446 T0008 §3-2: may this SINGLE run use the no-output recovery machinery?

    NR0003 measured 27 of 242 post-rejection rework runs ending with nothing registered,
    and found the cause structural rather than incidental: retry, stop code and failure
    notification all live inside `mode == "continuous"`, while rework is always `mode="single"`
    carrying an `edit` token.

    Two deliberate narrowings:
      * `scope_oracle_run` — the ENGINE planted this run's judge (`_scope_oracle`). A
        caller-supplied `completion_oracle` override (0248 B0001, the legacy Q&A follow-up in
        `q_answer_invoke_service`) keeps the old block: its success criterion is defined
        outside the engine, so the engine must not re-ask or re-run it.
      * `edit` only — `_SCOPE_PROBES` also holds chat / review / test_run /
        workflow_sequence_edit, but NR0003's measurement is the 264 edit/single runs. Opening
        the others would move the 0259/0268 judging contract with nothing behind it.
    """
    return bool(scope_oracle_run) and mode == "single" and action_scope == "edit"


def _scope_oracle_retry_run(run: dict) -> bool:
    """The same question asked of a live run dict — `scope_oracle_run` rides on it (§3-1)."""
    return _scope_oracle_retry_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run")
    )


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

    0293 NR0004 finding 5: the worker mention is built BEFORE the run picks a provider, and
    `_worker` may fall through the whole chain. Naming chain[0] in the mention would
    therefore be a guess that reads like a server-confirmed fact. A name is only
    returned when the effective chain collapses to exactly ONE provider — an explicit
    UI pin (start_run's `chain = [selected]`), or a project with a single enabled
    provider — because only then is fallback structurally impossible.

    Finding 4: the value is the provider's display NAME, not a model id. `api_model` exists
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
    provider_pinned: Optional[bool] = None,
    issue_builder: Optional[Callable[[], dict]] = None,
    merge_id: Optional[int] = None,
    completion_oracle: Optional[Callable[[], bool]] = None,
    # 0317 T0010 rev4: item_seq (as str, JSON-body keys) -> provider_id, chosen in
    # ContinuousWorkDialog's per-step override table. Session-scoped — this run's start
    # request is the only place it lives; never persisted (T0010 Q&A: session-scoped o1).
    continuation_provider_overrides: Optional[dict] = None,
    # 0346 T0005: handoff-note tab values — a common note for every hop and/or item_seq -> note
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
    # 0352 T0004 §3.5: the ai_direct chain's per-item_seq N/T auto-approve selection. Rides
    # the run (session-scoped, like the provider/note overrides above) so every hop's
    # provider resolution / docs_target counting / worker item_seq folding agrees with the
    # SAME selection the user made once at the start of the chain.
    continuation_auto_approve_item_seqs: Optional[list] = None,
    # flowgate.default.0400 M0005 + 0446 T0010 §3-1: THIS RUN's wall-clock budget in seconds,
    # picked either in ContinuousWorkDialog's duration section (a continuous hop) or in
    # AiInvokeDialog's time section (a single rejection rework). The `continuation_` prefix is
    # a misnomer kept on purpose — renaming it would move a route field, this argument, an
    # ai_invoke_paused_chains column and a client prop at once, which T0010 §3-1 rules out of
    # scope. Read it as "the run's budget pick", not as "continuous only".
    # Session-scoped like the provider/note overrides above — never persisted outside the
    # paused-chain row a continuous pause snapshots it into. None (or a value outside
    # STEP_TIMEOUT_MIN_SEC..STEP_TIMEOUT_MAX_SEC) falls back to the mode's own default:
    # HOP_TIMEOUT_SEC for continuous, the per-document formula for single.
    continuation_step_timeout_sec: Optional[int] = None,
    # flowgate.default.0443 T0002 (R0001): the dialog's "재시작 횟수" pick — how many times
    # a no-output hop retries on the SAME step-assigned provider (never a different one).
    # Session-scoped like the fields above; None or an unrecognized value falls back to
    # RESTART_MAX_ATTEMPTS_DEFAULT.
    continuation_restart_max_attempts: Optional[int] = None,
    # 0414 P0007: the [검수] tab's two session-scoped maps — mode-aware worker item_seq ->
    # review count, and -> reviewer provider_id. They ride the RUN (never a token), exactly
    # like the provider/note maps, and are snapshotted into the paused row so a resume or a
    # hop handoff keeps reviewing with the same selection (DB0009).
    continuation_review_count_overrides: Optional[dict] = None,
    continuation_reviewer_overrides: Optional[dict] = None,
    # 0414 L0008 §5: work / review / rework. A review or rework hop makes no document, so
    # the chain counters do not move for it — this is what lets a card say WHAT is running
    # instead of reporting a frozen progress number.
    hop_kind: str = WORK_HOP_KIND,
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
        normalize_continuation_auto_approve_item_seqs,
        normalize_continuation_instruction_mode,
    )

    requested_continuation_instruction_mode = continuation_instruction_mode
    continuation_instruction_mode = normalize_continuation_instruction_mode(
        requested_continuation_instruction_mode
    )
    continuation_auto_approve_item_seqs = normalize_continuation_auto_approve_item_seqs(
        continuation_auto_approve_item_seqs
    )
    effective = ai_settings_service.resolve_effective(project_id)
    chain = effective.get("providers") or []
    chain_source = effective.get("source")
    # A per-step override names this exact hop and remains the highest tier. 0435 T0004
    # deliberately removes every startup fallback tail from an explicit choice: a provider
    # that cannot start fails visibly instead of silently switching to a more expensive one.
    step_override_provider = None
    if mode == "continuous" and continuation_provider_overrides:
        step_override_provider = _resolve_continuation_hop_override(
            doc_ref,
            continuation_provider_overrides,
            chain,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )

    stored_provider_id = None
    stored_provider_name = None
    stored_provider_item_seq = None
    if (
        mode == "continuous"
        and not step_override_provider
        and not (provider_pinned and provider_id)
    ):
        stored_provider_id, stored_provider_name, stored_provider_item_seq = stored_hop_provider(
            doc_ref,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
    stored_provider_active = bool(
        stored_provider_id
        and any(provider.get("id") == stored_provider_id for provider in chain)
    )

    if step_override_provider:
        selected = next(
            (provider for provider in chain if provider.get("id") == step_override_provider),
            None,
        )
        chain = [selected] if selected else []
    elif mode == "continuous" and provider_pinned and provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, PROVIDER_UNAVAILABLE_CODE,
                PROVIDER_UNAVAILABLE_MESSAGE,
            )
        chain = [selected]
    elif stored_provider_active:
        # An unpinned run still follows the persisted sequence assignment (D0006 §6.2).
        chain = _prioritize_chain(chain, stored_provider_id)
    elif provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, PROVIDER_UNAVAILABLE_CODE,
                PROVIDER_UNAVAILABLE_MESSAGE,
            )
        chain = [selected] if mode == "single" else _prioritize_chain(chain, provider_id)
    elif mode == "continuous":
        hop_provider = _resolve_continuation_hop_provider(
            project_id,
            doc_ref,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        if hop_provider:
            chain = _prioritize_chain(chain, hop_provider)

    if mode == "continuous" and stored_provider_id and not stored_provider_active and chain:
        logger.warning(
            "continuation hop provider fallback: %s not active for %s item_seq %s, "
            "falling back to %s",
            stored_provider_id,
            doc_ref,
            stored_provider_item_seq,
            chain[0].get("id"),
        )
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

    # Durable lease admission is authoritative. Memory remains only a UI/live-process signal.
    active = db_group_ai_leases.get_active(group_id)
    handoff_allowed = bool(
        active
        and active.get("state") == "releasing"
        and chain_id
        and active.get("chain_id") == chain_id
    )
    if active is not None and not handoff_allowed:
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id=active["run_id"])
    # 0299 R0001: refuse before minting a token / creating scratch — a run that would
    # execute in the base tree must not start at all, and failing here keeps the
    # rollback trivial (nothing has been created yet). The doc's branch is only the
    # fallback-branch hint; the guard itself is about the group worktree.
    _require_group_worktree(
        project_id, module, group_id,
        (db_docs.get_by_id(doc_ref) or {}).get("branch") or "main",
        locale=template_provision.normalize_locale(continuation_locale),
    )

    baseline_seq = db_docs.get_group_max_seq(group_id)
    target_to_end = mode == "continuous" and continuation_target_seq == -1
    scope_oracle_run = _uses_scope_oracle(action_scope, mode, completion_oracle)
    if completion_oracle is not None:
        # Scoped-oracle run: success is judged by the caller's predicate, not by documents.
        docs_target = 0
    elif action_scope in ("workflow_decide", "resolve_conflict"):
        docs_target = 0
    elif mode == "continuous" and continuation_review_mode:
        # NR0003 follow-up proposal 2: review mode is the pre-flight Q-registration phase —
        # mention_service._CONTINUOUS_REVIEW_TEXT tells the worker NOT to create the next
        # document, so a review-mode hop that only registers a Q (or the "no blockers" ack
        # Q) always reaches this doc_ref with docs_reached=0. Targeting >=1 document made
        # that hop indistinguishable from "the hop ran and left nothing" (0359's no-output
        # retry), which reopened wasted attempts and a false "continuous work failed" alert for
        # every review-mode hop. Forcing the target to 0, like the other non-document
        # scopes above, judges it "complete" on 0 reached and never opens a retry.
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
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        if resolved_target is None:
            message = f"continuous run requires a decided workflow sequence on {doc_ref}"
            raise HTTPException(status_code=422, detail={
                "code": "validation_failed",
                "message": message,
                "errors": [{"loc": "continuation_target_seq", "msg": message}],
            })
        docs_target = resolved_target
        if docs_target <= 0:
            message = f"no pending worker step at or below workflow item_seq {target}"
            raise HTTPException(status_code=422, detail={
                "code": "validation_failed",
                "message": message,
                "errors": [{"loc": "continuation_target_seq", "msg": message}],
            })

    # Allocate and lease before token issuance/worker spawn. The DB primary key makes two
    # concurrent starts atomic across processes; an acquiring lease self-reclaims on expiry.
    run_id = _next_run_id()
    lease_chain_id = chain_id or run_id
    try:
        lease = db_group_ai_leases.acquire(
            group_id=group_id,
            project_id=project_id,
            run_id=run_id,
            chain_id=lease_chain_id,
            action_scope=action_scope,
            worker_identity=issued_to,
        )
    except db_group_ai_leases.RunIdCollision:
        # 0401 NR0003 §4 / T0004 work item 7: two runs minted the same today-serial in the
        # instant -- genuinely rare even without the floor in _next_run_id, and that floor
        # makes it rarer still. One retry with a freshly minted id is enough for something
        # this rare; a second hit is a real systemic problem, so it surfaces as a clean
        # 409 instead of retrying forever or falling through as a raw DB error.
        run_id = _next_run_id()
        lease_chain_id = chain_id or run_id
        try:
            lease = db_group_ai_leases.acquire(
                group_id=group_id,
                project_id=project_id,
                run_id=run_id,
                chain_id=lease_chain_id,
                action_scope=action_scope,
                worker_identity=issued_to,
            )
        except db_group_ai_leases.RunIdCollision:
            raise _http_error(
                409, "run_id_collision",
                _RUN_ID_COLLISION_COPY[template_provision.normalize_locale(continuation_locale)],
            )
    if lease is None:
        active = db_group_ai_leases.get_active(group_id) or {}
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id=active.get("run_id"))

    if issue_builder is not None:
        # 0359 L0007 §2.9: hand the run identity to the builder so the token it mints carries
        # ai_run_id. NR0003 §4 measured 1,346 continuous tokens with an EMPTY ai_run_id — every
        # one of them was issued through this branch, which never passed it, so there was no
        # bridge from a dead hop's token back to the run that died. Builders that do not accept
        # the keyword (review / sequence_edit / test_run) keep being called with no arguments.
        issue = _call_issue_builder(issue_builder, run_id)
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
            continuation_auto_approve_item_seqs=(
                continuation_auto_approve_item_seqs if mode == "continuous" else None
            ),
        )
        mention = mention_builder(issue["raw_token"], issue["scratch_dir"])
    if not mention:
        # No prompt ⇒ nothing to launch. Discard the token and its acquiring lease.
        try:
            token_service.revoke(issue["token_id"], reason="ai_invoke_mention_unavailable")
        except Exception:
            logger.warning("token revoke failed after mention_unavailable", exc_info=True)
        db_group_ai_leases.release(group_id, run_id)
        raise _http_error(409, "mention_unavailable",
                          "Could not build a worker mention for this document.")

    lease = db_group_ai_leases.activate(
        group_id, run_id, issue.get("token_id"), action_scope, issued_to, RUN_TIMEOUT_CAP_SEC
    )
    if lease is None:
        try:
            token_service.revoke(issue["token_id"], reason="ai_invoke_lease_lost")
        except Exception:
            logger.warning("token revoke failed after lease loss", exc_info=True)
        raise _http_error(409, "run_lease_lost", "The AI run lease could not be activated.")
    # 0346 T0005 §2-5 / D0004 §3-3: the handoff-note tab's common note and/or this hop's
    # individual note are prepended here, at the single point every hop's prompt (built by
    # whichever of the three builders ran above) has already converged into one string — see
    # D0004 §3-4 for why the builders themselves are never touched. Unlike the provider
    # override, an individual note does NOT replace the common one: D0004 §3-3 treats them as
    # stackable ("what this is for" + "what you take on"), so both are adopted when present.
    # A resolution failure must not stall the hop (same contract as the provider override).
    # 0406 T0022 work item 5: where this hop's user message came from, and what the final
    # prompt became, ride on the run. The text is not kept — only kind, length and hash.
    prompt_audit: dict = {
        "prompt_message_source": "none",
        "prompt_common_default_applied": False,
        "prompt_user_message_length": 0,
        "prompt_user_message_sha256": None,
    }
    if mode == "continuous" or (mode == "single" and action_scope == "new"):
        mention = _inject_hop_notes(
            mention,
            doc_ref,
            default_note=(continuation_default_note if mode == "continuous" else None),
            note_overrides=(continuation_note_overrides if mode == "continuous" else None),
            instruction_mode=continuation_instruction_mode,
            auto_approve_item_seqs=continuation_auto_approve_item_seqs,
            fold_worker_item_seq=(mode == "continuous"),
            locale=continuation_locale,
            audit=prompt_audit,
        )
    _prompt_final_length, _prompt_final_sha256 = prompt_digest(mention)

    if scope_oracle_run:
        # After issue() (the judge target comes from the token) but before the worker is
        # launched below, so the baseline cannot include the work this run is about to do.
        completion_oracle = _scope_oracle(action_scope, issue.get("token_id"), doc_ref)

    _cleanup_retained_scratches(project_id)
    # 0357 T0004: the chain identity/counters this hop inherits. `run_id` is allocated
    # once, above, and stays the hop's own id — the CHAIN id is what travels hop to hop.
    if mode == "continuous":
        chain_id = chain_id or run_id
        if chain_docs_target is None:
            chain_docs_target = docs_target
        if chain_docs_reached is None:
            chain_docs_reached = 0
    else:
        # A single run is a degenerate one-hop chain. Returning the same payload
        # shape keeps clients simple without changing the run counters' meaning.
        #
        # 0414 L0008 §5 체인 카운터: unless the CALLER named a chain. A review/rework hop is
        # mode="single" but belongs to a running chain, and overwriting chain_id with its own
        # run_id would break the lease handoff (which requires a matching chain_id) and reset
        # the miniplayer's progress to 0. An ordinary single run passes none of these three
        # and still resolves to exactly the values above, so nothing existing moves.
        chain_id = chain_id or run_id
        if chain_docs_target is None:
            chain_docs_target = docs_target
        if chain_docs_reached is None:
            chain_docs_reached = 0
    scratch = _create_scratch(project_id, run_id)

    doc = db_docs.get_by_id(doc_ref) or {}
    # 0187 rev2: same group-worktree routing as the test runner — the invoked AI's
    # cwd and the pollution diff must watch the tree the group's CRUD writes to.
    source_root = storage_paths.resolve_project_src_root(
        project_id, doc.get("branch") or "main", group_id=group_id
    )

    started_at = now_iso()
    timeout_sec = _resolve_timeout_sec(
        mode, docs_target, target_to_end, continuation_step_timeout_sec, hop_kind
    )
    # 0414 P0007: what THIS hop's review selection resolves to, answered in the start
    # response rather than after the fact — "I picked a reviewer, did it take?" has to be
    # answerable while the run is going, not once it is over (0406 T0022 작업 3's reasoning
    # for the instruction mode, applied to the same class of question).
    review_count_overrides = (
        continuation_review_count_overrides if mode == "continuous" else None
    )
    reviewer_overrides = (
        continuation_reviewer_overrides if mode == "continuous" else None
    )
    hop_item_seq = _hop_item_seq_or_none(doc_ref) if mode == "continuous" else None
    hop_review_count = resolve_review_count(review_count_overrides, hop_item_seq)
    hop_reviewer_provider_id = (
        resolve_reviewer(reviewer_overrides, hop_item_seq, project_id)
        if hop_review_count else None
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
        # 0357 T0004: chain-lifetime progress, carried across the per-hop runs an
        # unmanned continuous chain is made of (docs_* stay per-hop judging values).
        "chain_id": chain_id,
        "chain_docs_target": int(chain_docs_target or 0),
        "chain_docs_reached": int(chain_docs_reached or 0),
        "chain_docs_accounted": False,
        "baseline_seq": baseline_seq,
        "timeout_sec": timeout_sec,
        # 0359 P0006 [hop budget]: the wall-clock the budget actually lands on. Until now the
        # limit appeared in NO response at all, so "did it die on the clock?" could only be
        # answered by re-deriving the formula from logs — which is exactly the work NR0003 had
        # to do (and got wrong on its first pass).
        "deadline_at": _deadline_iso(started_at, timeout_sec),
        # ── 0446 T0014 §2: two clocks, kept apart by name ───────────────────────
        # `timeout_sec` / `deadline_at` above keep their exact stored meaning and are
        # read from here on as the NO-PROGRESS threshold: the EARLIEST this run may be
        # stopped, and only if it shows nothing new for that long. The ceiling below is
        # the latest it can possibly run, progress or not. Both live in memory on
        # purpose — the column and the migration belong to T#2 (§2-5).
        "absolute_cap_sec": _absolute_cap_sec(),
        "absolute_deadline_at": _deadline_iso(started_at, _absolute_cap_sec()),
        "stall_anchor_mono": None,     # start of the current no-progress window
        "last_progress_mono": None,    # None = nothing was ever observed to move
        "last_progress_at": None,
        "last_progress_signal": None,
        "progress_observations": 0,
        "watchdog_kill": None,         # raw, monotonic, process-local (0446 T0014)
        # 0446 T0016 3-1: the durable reading of the above, resolved once at finalize.
        # These two are what the row, the detail response and the next rework prompt read.
        "timeout_kind": None,
        "timeout_diagnosis": None,
        "stdout_tail": None,
        "stderr_tail": None,
        "provider": None,
        "provider_id": None,
        "attempt_no": 0,
        "fallback_history": [],
        "register_errors": [],
        "tool_call_misses": 0,
        "turn_limit_exhausted": False,
        "oracle_mismatch": False,
        "started_at": started_at,
        "started_mono": time.monotonic(),
        "attempt_started_mono": time.monotonic(),
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
        # 0446 T0008 §3-1: did the ENGINE plant this run's completion oracle, or did the
        # caller hand one in? Computed at the top of start_run and, until now, discarded —
        # which left `completion_oracle is not None` an unconditional retry block for every
        # scoped run. That is the fourth gate NR0003 §6-2 did not name.
        "scope_oracle_run": scope_oracle_run,
        "target_to_end": target_to_end,
        "continuation_instruction_mode": (
            continuation_instruction_mode if mode == "continuous" else None
        ),
        # 0352 T0004 §3.5: the per-item_seq N/T auto-approve selection rides the run the same
        # way instruction_mode does, so every hop's provider/note/docs-target logic re-reads
        # the SAME selection the user made once at the start of the chain.
        "continuation_auto_approve_item_seqs": (
            continuation_auto_approve_item_seqs if mode == "continuous" else None
        ),
        # ── 0406 T0022 items 2/3/5: values for judging this hop after the fact ───────
        # The mode as the request sent it / as the server read it / whether normalisation
        # actually fired. Keeping the three apart is what separates "the user picked
        # auto_approved" from "the entry point omitted it and the server chose instead".
        "continuation_instruction_mode_requested": (
            requested_continuation_instruction_mode if mode == "continuous" else None
        ),
        "continuation_instruction_mode_normalized": (
            continuation_instruction_mode if mode == "continuous" else None
        ),
        "continuation_instruction_mode_fallback_applied": bool(
            mode == "continuous"
            and _instruction_mode_fallback_applied(requested_continuation_instruction_mode)
        ),
        # The document type of the slot this hop's worker actually filled, plus the item_seq
        # of N/T the server auto-handled. advance_workflow reports it (not every builder does).
        "worker_document_type": issue.get("worker_document_type"),
        "auto_handled_item_seqs": list(issue.get("auto_handled_item_seqs") or []),
        # How the handoff note resolved, plus its length/hash. The text is not stored.
        **prompt_audit,
        "prompt_final_length": _prompt_final_length,
        "prompt_final_sha256": _prompt_final_sha256,
        # 0359 L0007 §2.5: the retry rebuilds this hop's prompt from scratch when the token
        # has to be reissued, and a prompt is only correct in the locale the chain chose.
        "continuation_locale": (continuation_locale if mode == "continuous" else None),
        # 0317 TR0011 (Q153 opt-1): the per-step override map rides on the run so each
        # re-spawned hop can re-apply it (it never touches a token; it is session-scoped).
        "continuation_provider_overrides": (
            continuation_provider_overrides if mode == "continuous" else None
        ),
        # 0435 T0004: retry code never replays the priority tiers. The finalized chain head is
        # the single source of truth for this hop, regardless of whether it came from a step
        # override, a human pin, a stored row, a header default, or a doc-type assignment.
        # 0446 T0008 §3-5: a scope-oracle rework run needs the same head on the record, or
        # `_retry_provider_chain` returns [] and the loop ends as
        # "providers_exhausted_for_retry" with every gate above it already open. The 0435
        # T0004 contract is untouched: attempt 1 retries exactly once on THIS provider and
        # never switches to another. `_stop_reason_text` reads the name back for the human
        # sentence.
        "continuation_selected_provider_id": (
            chain[0].get("id")
            if chain and (
                mode == "continuous"
                or _scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
            )
            else None
        ),
        "continuation_selected_provider_name": (
            chain[0].get("name")
            if chain and (
                mode == "continuous"
                or _scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
            )
            else None
        ),
        # 0346 T0005: the handoff-note bundle rides the run forward the same way the
        # provider override map does, so a re-spawned hop (_maybe_auto_resume_hop ->
        # _spawn_auto_resume) can re-apply it. Session-scoped — never persisted on a token.
        "continuation_note_overrides": (
            continuation_note_overrides if mode == "continuous" else None
        ),
        "continuation_default_note": (
            continuation_default_note if mode == "continuous" else None
        ),
        # flowgate.default.0400 M0005: the budget PICK rides the run forward the same way the
        # provider/note selections do, so a re-spawned hop (auto-resume, or a resume after a
        # user pause) re-applies the same choice instead of silently falling back to
        # HOP_TIMEOUT_SEC. Session-scoped — never persisted outside a paused-chain snapshot.
        #
        # 0446 T0010 §3-3: no longer blanked on a single run. This read
        # `continuation_step_timeout_sec if mode == "continuous" else None`, so a single run
        # forgot the ORIGIN of its own budget the instant it started — get_run_record showed
        # a 4-hour timeout_sec with nothing to say where it came from. The value only ever
        # LEAVES this dict through continuous-chain code (_maybe_auto_resume_hop /
        # _spawn_auto_resume / _apply_stop_row), and _apply_stop_row returns on
        # `mode != "continuous"` at its first line, so a single run still writes no
        # ai_invoke_paused_chains row and still queues no auto-resume hop. Keeping the value
        # is a record of the user's pick, not a new execution path.
        "continuation_step_timeout_sec": continuation_step_timeout_sec,
        # flowgate.default.0443 T0002 (R0001): the dialog's "재시작 횟수" pick, carried
        # the same way the budget pick above is — read by attempts_max below and by every
        # pause/resume/handoff snapshot that already threads continuation_step_timeout_sec.
        "continuation_restart_max_attempts": (
            continuation_restart_max_attempts if mode == "continuous" else None
        ),
        # 0317 T0013 defect ③: the header default provider pin rides the run too. Without it a
        # re-spawned hop that has NO per-step override lost the user's chosen default and fell
        # back to the doc-type assignment / project default chain — contradicting the
        # "default: <name>" tag every ContinuousWorkDialog row promises. Session-scoped like the
        # override map; never persisted on a token.
        "continuation_base_provider_id": (provider_id if mode == "continuous" else None),
        "continuation_provider_pinned": (
            bool(provider_pinned and provider_id) if mode == "continuous" else False
        ),
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
        # ── 0359 L0007 §2.9 / §2.6: what the no-output retry loop needs to open attempt 2 ──
        # The token ID (was: only the raw token, so the retry could not ask whether the token
        # was still usable), the mention it was handed, and the builder that can mint a fresh
        # one for the SAME head when the old token is spent.
        "token_id": issue.get("token_id"),
        "mention": mention,
        "issue_builder": issue_builder,
        # Which workflow slot this hop is filling — rides the run into the record, the
        # notification and the stop row so "where did the chain die?" has an answer.
        "hop_item_seq": hop_item_seq,
        # 0414 P0007 / L0008 §2.9: the two [검수] maps ride the run hop to hop, exactly like
        # the provider/note maps above. Dropped at ANY carrier, the chain reviews its first
        # step and then silently stops reviewing — the failure shape L0008 §2.9 names.
        "continuation_review_count_overrides": review_count_overrides,
        "continuation_reviewer_overrides": reviewer_overrides,
        "hop_kind": hop_kind,
        "hop_review_count": hop_review_count,
        "hop_reviewer_provider_id": hop_reviewer_provider_id,
        "hop_reviewer_provider_name": _provider_name_of(project_id, hop_reviewer_provider_id),
        "attempts_used": 0,
        # flowgate.default.0443 T0002 (R0001): a continuous hop resolves the user's
        # "재시작 횟수" pick via _resolve_restart_max_attempts instead of the fixed
        # NO_OUTPUT_MAX_ATTEMPTS constant.
        # 0446 T0008 §3-3: a record/display value — the real ceiling is the
        # `attempts_used >= NO_OUTPUT_MAX_ATTEMPTS` comparison in `_retry_eligible`. Leaving
        # this at 1 for a scope-oracle rework run would make `ai_invoke_runs` and the screen
        # say one thing while the engine does another. continuation_restart_max_attempts is
        # None outside continuous mode, so _resolve_restart_max_attempts falls back to
        # reproducing the previous fixed NO_OUTPUT_MAX_ATTEMPTS(2) behavior for it.
        "attempts_max": (
            _resolve_restart_max_attempts(continuation_restart_max_attempts)
            if mode == "continuous"
            or _scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
            else 1
        ),
        "retry_block_reason": None,
        "last_message_seen": None,
        "stop_code": None,
        "stop_reason": None,
        "resumable": False,
        # Tagged by the inbox self-chain (mark_chain_stop) when IT is the one that stopped
        # the chain; the engine's own classification still wins for cancel/timeout/pause.
        "inbox_stop_code": None,
        "failure_signal_sent": False,
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
        "continuation_auto_approve_item_seqs": (
            continuation_auto_approve_item_seqs if mode == "continuous" else None
        ),
        # 0406 T0022 work item 3: the start response already answers it — the requested and
        # normalised mode (and whether normalisation fired), this hop's real worker slot and
        # document type, and the N/T the server handled with no worker. If these were only
        # known after the run, "the N/T vanished" could never be checked while it runs.
        "continuation_instruction_mode_requested": run[
            "continuation_instruction_mode_requested"
        ],
        "continuation_instruction_mode_normalized": run[
            "continuation_instruction_mode_normalized"
        ],
        "continuation_instruction_mode_fallback_applied": run[
            "continuation_instruction_mode_fallback_applied"
        ],
        "hop_item_seq": run["hop_item_seq"],
        # 0414 P0007 시작 응답: the selection as stored, plus what it resolved to for THIS
        # hop. docs_target above is deliberately untouched — review rounds are not documents,
        # so a reviewed chain and an unreviewed one report the same target.
        "continuation_review_count_overrides": run["continuation_review_count_overrides"],
        "continuation_reviewer_overrides": run["continuation_reviewer_overrides"],
        "hop_kind": run["hop_kind"],
        "hop_review_count": run["hop_review_count"],
        "hop_reviewer_provider_id": run["hop_reviewer_provider_id"],
        "hop_reviewer_provider_name": run["hop_reviewer_provider_name"],
        "worker_document_type": run["worker_document_type"],
        "auto_handled_item_seqs": run["auto_handled_item_seqs"],
        "provider": _provider_brief(chain[0]),
        "attempt_no": 1,
        "started_at": run["started_at"],
        # 0359 P0006 [hop budget]: the budget and its wall-clock deadline travel with every
        # start / status / finish payload, so nobody has to reconstruct them from logs again.
        "timeout_sec": run["timeout_sec"],
        "deadline_at": run["deadline_at"],
    }


def _normalized_instruction_mode(mode: Optional[str]) -> str:
    from modules.flow_gate.services.workflow_decision_service import (
        normalize_continuation_instruction_mode,
    )

    return normalize_continuation_instruction_mode(mode)


def _instruction_mode_fallback_applied(mode: Optional[str]) -> bool:
    from modules.flow_gate.services.workflow_decision_service import (
        instruction_mode_fallback_applied,
    )

    return instruction_mode_fallback_applied(mode)


def _provider_brief(provider: Optional[dict]) -> Optional[dict]:
    if provider is None:
        return None
    return {
        "id": provider.get("id"),
        "name": provider.get("name"),
        "exec_type": provider.get("exec_type"),
        "kind": provider.get("kind"),
    }


# ── 0359 L0007: hop budget, run identity, prompt reuse ───────────────────────

def _resolve_timeout_sec(
    mode: str,
    docs_target: int,
    target_to_end: bool,
    continuation_step_timeout_sec: Optional[int] = None,
    hop_kind: str = WORK_HOP_KIND,
) -> int:
    """The run's time budget (L0007 §2.13 / P0006 [hop budget]).

    A continuous run IS one hop (0317 TR0011 re-spawns a worker per step), so scaling its
    budget by how many slots are still ahead — the old min(3600 × slots_left, 14400) — handed
    the LAST hop the SMALLEST budget, which is backwards. NR0003 §7 cleared this of causing
    the reported incident (that hop had 2h and used 2m25s) but kept it as a live hazard: TR
    hops of 74 minutes were measured. Fixed per hop now.

    flowgate.default.0400 M0005: that fixed per-hop budget became a user pick (30-240 minutes,
    ContinuousWorkDialog duration section) instead of always HOP_TIMEOUT_SEC.

    0446 NR0003 §3-5 (R5) / T0010 §3-2: the pick is no longer continuous-only. The single-run
    formula min(RUN_TIMEOUT_BASE_SEC × max(1, docs_target), RUN_TIMEOUT_CAP_SEC) bottoms out
    at exactly 3600 for a rejection rework — its docs_target is 1 and the max(1, …) floor
    pins it there — so every rework got precisely one hour and no screen could ask for more
    (264/264 measured runs had timeout_sec=3600; NR §2 lost two of them at the 3603s boundary
    and a third quit at 59.6 minutes). So the explicit pick is read FIRST, above the mode
    branch:

        an in-range explicit pick is this run's budget, whatever the mode;
        otherwise the previous order stands — continuous ⇒ HOP_TIMEOUT_SEC,
        target_to_end ⇒ RUN_TIMEOUT_CAP_SEC, else the per-document formula.

    The bounds stay STEP_TIMEOUT_MIN_SEC..STEP_TIMEOUT_MAX_SEC — the same pair
    ai_invoke_routes validates against (422) and the same list both dialogs offer — so the
    screen, the route and the engine cannot drift apart. The no-pick default is deliberately
    untouched: 3600 was never the defect, "cannot choose" was.
    """
    # 0446 T0010 §3-1: an explicit in-range pick outranks the mode branch below,
    # whatever the mode — a single rejection rework otherwise bottoms out at exactly
    # 3600 and no screen can ask for more (NR0003 R5).
    if (
        continuation_step_timeout_sec is not None
        and STEP_TIMEOUT_MIN_SEC <= continuation_step_timeout_sec <= STEP_TIMEOUT_MAX_SEC
    ):
        return int(continuation_step_timeout_sec)
    # 0414 L0008 §5: a review/rework hop is mode="single" but belongs to the chain, so it
    # takes the chain's per-hop budget rather than the single-run formula.
    if mode == "continuous" or hop_kind in CHAIN_MEMBER_HOP_KINDS:
        return HOP_TIMEOUT_SEC
    if target_to_end:
        return RUN_TIMEOUT_CAP_SEC
    return min(RUN_TIMEOUT_BASE_SEC * max(1, docs_target), RUN_TIMEOUT_CAP_SEC)


def _resolve_restart_max_attempts(continuation_restart_max_attempts: Optional[int]) -> int:
    """Total attempts allowed for one hop (0443 R0001 "재시작 횟수").

    The dialog picks a RESTART count (-1/0/1/2/3), not a total-attempts count — this
    converts it: N restarts == N+1 total attempts, and -1 stays -1 (the "될 때까지"
    unlimited sentinel _retry_eligible/_retry_provider_chain both check for explicitly).
    An unset or unrecognized value falls back to RESTART_MAX_ATTEMPTS_DEFAULT, which
    reproduces the fixed NO_OUTPUT_MAX_ATTEMPTS(2) behavior this feature replaces.
    """
    restart_count = continuation_restart_max_attempts
    if restart_count not in RESTART_MAX_ATTEMPTS_CHOICES:
        restart_count = RESTART_MAX_ATTEMPTS_DEFAULT
    if restart_count == -1:
        return -1
    return int(restart_count) + 1


def _deadline_iso(started_at: str, timeout_sec: int) -> Optional[str]:
    """started_at + timeout_sec, in the same ISO/timezone shape now_iso() produces."""
    try:
        return (
            datetime.fromisoformat(started_at) + timedelta(seconds=int(timeout_sec))
        ).isoformat(timespec="seconds")
    except Exception:
        return None


def _call_issue_builder(issue_builder: Callable, run_id: str) -> dict:
    """Call a token issuer, handing it the run identity when it can take one (L0007 §2.9).

    The contract gained `ai_run_id`, but four of the five builders in the codebase mint a
    single-shot token that has no run worth pointing at, and existing tests call them bare.
    So inspect rather than force: a builder that declares the keyword gets it, the rest are
    called exactly as before.
    """
    try:
        import inspect

        params = inspect.signature(issue_builder).parameters
        accepts = "ai_run_id" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
    except (TypeError, ValueError):
        accepts = False
    return issue_builder(ai_run_id=run_id) if accepts else issue_builder()


def _hop_item_seq_or_none(doc_ref: str) -> Optional[int]:
    """Which workflow slot this hop is filling — best effort (L0007 §2.9 / §5).

    None when the sequence is not decided yet (a workflow_decide run). The null rides through
    to the record, the event and the notification rather than suppressing any of them."""
    try:
        return _next_incomplete_item_seq(doc_ref)
    except Exception:
        logger.warning("ai-invoke hop item_seq lookup failed for %s", doc_ref, exc_info=True)
        return None


def prompt_digest(text: Optional[str]) -> tuple[int, Optional[str]]:
    """Keep only the length and sha256 — never the text itself (0406 T0022 item 5).

    Deciding "did the user's text actually make it in?" does not need the text. Hashing
    the same string again and comparing is enough, and the length alone already settles
    an "an empty value went in" report. Keeping the text would itself leak information.
    """
    if not text:
        return 0, None
    encoded = text.encode("utf-8")
    return len(text), hashlib.sha256(encoded).hexdigest()


def _inject_hop_notes(
    mention: Optional[str],
    doc_ref: str,
    *,
    default_note: Optional[str],
    note_overrides: Optional[dict],
    instruction_mode: Optional[str],
    locale: Optional[str],
    auto_approve_item_seqs: Optional[list] = None,
    fold_worker_item_seq: bool = True,
    audit: Optional[dict] = None,
) -> Optional[str]:
    """Prepend the common note plus the effective step note for continuous and single runs.

    The note is read from the row the AI worker actually fills, and from that row ONLY.
    0408 M0019 re-rejection ("why is the TR/NR mention using T/N's?"): a pair fallback made an
    auto-approved NR hop speak the sentence written for N. Each row carries its own note now
    (work_plan_sequence_service.attach_auto_rows), so the fold picks the row and the row picks
    the note. A present override key wins even when its value normalizes to empty: that empty
    string is the user's tombstone and suppresses the stored note. Only an absent key falls
    back to the note stored on the row. Continuous hops use the mode-aware N/T -> NR/TR fold;
    a single new hop writes the current head directly and therefore does not fold.

    0406 T0022 item 5 — pass ``audit`` and it reports whether this hop's user message
    resolved from (a) a step override, (b) the common default, (c) the stored sequence
    note fallback, or (d) nothing — plus that string's length and sha256. This is exactly
    the structural gap NR0021 §8 pinned down: a session-scoped handoff note is persisted
    nowhere, so what the user remembers typing could be neither proved nor disproved.
    """
    if not mention:
        return mention

    notes: list[str] = []
    common_applied = bool(default_note and default_note.strip())
    if common_applied:
        notes.append(default_note.strip())

    hop_note: Optional[str] = None
    hop_source: Optional[str] = None
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        head = db_wfseq.get_effective_head(seq["id"]) if seq is not None else None
        item_seq = None
        if head:
            item_seq = (
                _hop_worker_item_seq(
                    seq["id"],
                    head,
                    continuation_instruction_mode=instruction_mode,
                    continuation_auto_approve_item_seqs=auto_approve_item_seqs,
                )
                if fold_worker_item_seq
                else head.get("item_seq")
            )

        override_present = False
        override_value = None
        if item_seq is not None and isinstance(note_overrides, dict):
            if str(item_seq) in note_overrides:
                override_present = True
                override_value = note_overrides[str(item_seq)]
            elif item_seq in note_overrides:
                override_present = True
                override_value = note_overrides[item_seq]

        if override_present:
            from modules.flow_gate.services.work_plan_sequence_service import normalize_note

            hop_note = normalize_note(override_value) or None
            hop_source = "override"
        elif item_seq is not None:
            hop_note = resolve_stored_step_note(doc_ref, item_seq)
            hop_source = "stored_note" if hop_note else None
    except Exception:  # noqa: BLE001 — a note lookup failure must not stall the hop
        logger.warning("continuation hop note resolution failed for %s", doc_ref, exc_info=True)

    if hop_note:
        notes.append(hop_note)
    elif hop_source == "override":
        # An empty override is the user's tombstone: it means "wipe the stored note", so
        # it is recorded as "an override applied" but contributes no text.
        hop_source = "override_tombstone"
    try:
        if notes:
            mention = invoke_mention_service.prepend_messages_section(mention, notes, locale)
    except Exception:  # noqa: BLE001 — a note failure must not stall the hop
        logger.warning("continuation hop note injection failed for %s", doc_ref, exc_info=True)
    if audit is not None:
        sources = []
        if hop_source:
            sources.append(hop_source)
        if common_applied:
            sources.append("common_default")
        applied = "\n\n".join(notes)
        length, digest = prompt_digest(applied)
        audit.update({
            "prompt_message_source": "+".join(sources) or "none",
            "prompt_common_default_applied": common_applied,
            "prompt_user_message_length": length,
            "prompt_user_message_sha256": digest,
        })
    return mention


def resolve_stored_step_note(doc_ref: str, item_seq: int) -> Optional[str]:
    """Return one normalized sequence-row note; lookup failures degrade to no note."""
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        for item in db_wfseq.get_sequence_items(seq["id"]) or []:
            if item.get("item_seq") == item_seq:
                from modules.flow_gate.services.work_plan_sequence_service import normalize_note

                return normalize_note(item.get("note")) or None
    except Exception:  # noqa: BLE001 — prompt enrichment must never stop execution
        logger.warning("stored step note resolution failed for %s", doc_ref, exc_info=True)
    return None


def excerpt(text: Optional[str], max_bytes: int = LAST_MESSAGE_EXCERPT_BYTES) -> Optional[str]:
    """One-line, byte-bounded digest of a worker's message (L0007 §2.10.4).

    Strips list markers / backticks and collapses whitespace so a markdown report reads as a
    sentence in a notification row, then cuts on a UTF-8 CHARACTER boundary — Korean is three
    bytes per character, so a naive byte slice would emit a broken one.
    """
    if not text or not text.strip():
        return None
    cleaned = re.sub(r"(?m)^[\s>]*(?:[-*+]\s+|#{1,6}\s*)", "", text)
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return None
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= max_bytes:
        return cleaned
    return encoded[: max(0, max_bytes - 3)].decode("utf-8", errors="ignore") + "…"


def _resolve_continuation_hop_provider(
    project_id: str,
    doc_ref: str,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[str]:
    """Return the doc-type-assigned provider for the worker hop, or the default-chain tier.

    A head only folds to its paired report type when it is server auto-handled — either
    every N/T under ``auto_approved``, or the specific item_seqs selected under ``ai_direct``
    (0352 T0004 §2). Every other N/T, and TS in either mode, is worker-authored and resolves
    its own head type (flowgate.default.0353 B0001 / NR0003). Never raises: lookup gaps fall
    through.
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
            is_auto_handled_step,
        )

        fold_to_report = is_auto_handled_step(
            head_type=head_type,
            item_seq=head.get("item_seq"),
            instruction_mode=continuation_instruction_mode,
            auto_approve_item_seqs=continuation_auto_approve_item_seqs,
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


def _paired_report_row(items: list[dict], head: dict) -> Optional[dict]:
    """Return the first paired worker report after an N/T head.

    TSR is server-assembled and never has a provider, so TS intentionally has no provider
    fallback candidate here.
    """
    from modules.flow_gate.services.workflow_decision_service import AUTO_REPORT_MAP

    head_item_seq = head.get("item_seq")
    report_type = AUTO_REPORT_MAP.get((head.get("type") or "").upper())
    if head_item_seq is None or not report_type or report_type == "TSR":
        return None
    return next(
        (
            item
            for item in sorted(items or [], key=lambda i: i.get("item_seq") or 0)
            if (item.get("item_seq") or -1) > head_item_seq
            and (item.get("type") or "").upper() == report_type
        ),
        None,
    )


def _hop_worker_item_seq(
    seq_id: int,
    head: dict,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> Optional[int]:
    """Return the item_seq of the slot this worker fills.

    A server auto-handled N/T head — every one under ``auto_approved``, or the specific
    item_seqs selected under ``ai_direct`` (0352 T0004 §2) — folds to the paired NR/TR slot.
    Every other N/T head, and TS in both modes, is an independent worker hop and retains its
    own item_seq (flowgate.default.0353 B0001 / NR0003). Missing or unknown modes normalize
    to the legacy ``auto_approved`` behavior.
    """
    head_item_seq = head.get("item_seq")
    head_type = (head.get("type") or "").upper()
    from modules.flow_gate.services.workflow_decision_service import (
        AUTO_REPORT_MAP,
        is_auto_handled_step,
    )

    fold_to_report = is_auto_handled_step(
        head_type=head_type,
        item_seq=head_item_seq,
        instruction_mode=continuation_instruction_mode,
        auto_approve_item_seqs=continuation_auto_approve_item_seqs,
    )
    if not fold_to_report or head_item_seq is None:
        return head_item_seq
    report = _paired_report_row(db_wfseq.get_sequence_items(seq_id) or [], head)
    return report.get("item_seq") if report is not None else head_item_seq


def _hop_worker_rows(
    seq_id: int,
    head: dict,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> list[dict]:
    """Return provider/note candidates in worker-row then paired-row priority order."""
    worker_item_seq = _hop_worker_item_seq(
        seq_id,
        head,
        continuation_instruction_mode=continuation_instruction_mode,
        continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
    )
    rows = db_wfseq.get_sequence_items(seq_id) or []
    worker = next((row for row in rows if row.get("item_seq") == worker_item_seq), None)
    candidates = [worker] if worker is not None else []
    if head.get("item_seq") != worker_item_seq:
        candidates.append(head)
    else:
        report = _paired_report_row(rows, head)
        if report is not None:
            candidates.append(report)
    return candidates


def stored_hop_provider(
    doc_ref: str,
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Return the provider stored on the worker row, falling back to the head row."""
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None, None, None
        head = db_wfseq.get_effective_head(seq["id"])
        if not head:
            return None, None, None
        candidates = _hop_worker_rows(
            seq["id"],
            head,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        for row in candidates:
            if row and row.get("provider_id"):
                return (
                    row.get("provider_id"),
                    row.get("provider_display_name"),
                    row.get("item_seq"),
                )
        return None, None, None
    except Exception:  # noqa: BLE001 — stored preference resolution must never stall a hop
        logger.warning("stored continuation hop provider resolution failed for %s", doc_ref,
                       exc_info=True)
        return None, None, None


def _resolve_continuation_hop_override(
    doc_ref: str,
    overrides: dict,
    chain: list[dict],
    *,
    continuation_instruction_mode: Optional[str] = None,
    continuation_auto_approve_item_seqs: Optional[list] = None,
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
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
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
    continuation_auto_approve_item_seqs: Optional[list] = None,
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
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
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
    (D0004 §3: assignment beats fallback, but a spawn failure falls through). Unlike an explicit
    UI pin — which collapses the chain to one provider and disables fallback — a doc-type
    assignment only re-orders, so the existing _worker fallback loop still protects the run."""
    head = [p for p in chain if p.get("id") == provider_id]
    if not head:
        return chain
    return head + [p for p in chain if p.get("id") != provider_id]


# ── Worker: provider fallback loop (L0006 §2.2) ──────────────────────────────

def _worker(run: dict, chain: list[dict], prompt: str) -> None:
    """One hop — one or more attempts (0359 L0007 §2.1).

    Before 0359 this ran `provider loop → classify exit reason → judge+close` with judgment welded onto
    finalize, so there was no seam where "the hop ran and produced NOTHING" could be turned
    back into another attempt. NR0003 §3 is the consequence: the loop had one forward edge
    ("a document was registered") and no other edge at all, so a single wasted lap ended the
    whole chain — silently, with 11 untried providers still on the bench. Judgment and finalize
    are separated here, and the no-output retry lives in the seam between them.
    """
    try:
        current_chain = chain
        current_prompt = prompt
        run["provider"] = _provider_brief(current_chain[0])
        run["provider_id"] = current_chain[0].get("id")
        run["attempt_no"] = 1
        # Exactly once per run, however many attempts follow (P0006 appendix D: no new event
        # types — a retry is reported as a provider switch, which the UI already draws).
        _broadcast(run, "ai_invoke_started", {
            "run_id": run["run_id"],
            "group_id": run["group_id"],
            "doc_ref": run["doc_ref"],
            "mode": run["mode"],
            "started_at": run["started_at"],
            "docs_target": run["docs_target"],
            "chain_id": run["chain_id"],
            "chain_docs_target": run["chain_docs_target"],
            "chain_docs_reached": run["chain_docs_reached"],
            "provider_id": current_chain[0].get("id"),
            "provider_name": current_chain[0].get("name"),
            "attempt_no": 1,
            # 0406 T0022 item 3: the miniplayer builds its card from this payload. Without
            # these the live card has no real worker type until after the run finishes.
            "hop_item_seq": run.get("hop_item_seq"),
            "worker_document_type": run.get("worker_document_type"),
            "auto_handled_item_seqs": list(run.get("auto_handled_item_seqs") or []),
        })

        while True:
            started_ok = _execute_provider_chain(run, current_chain, current_prompt)
            _classify_end_reason(run, started_ok)
            _judge_hop(run)
            run["attempts_used"] = int(run.get("attempts_used") or 0) + 1

            if not _retry_eligible(run):
                break
            if not _recheck_no_output(run):
                break
            next_chain = _retry_provider_chain(run)
            if not next_chain:
                run["retry_block_reason"] = "providers_exhausted_for_retry"
                break
            prepared = _prepare_retry_token(run)
            if prepared is None:
                run["retry_block_reason"] = "token_unavailable"
                break

            previous = run.get("provider") or {}
            _archive_attempt(run, "no_output", prepared["token_id_before"])
            _reset_attempt_state(run)
            current_chain = next_chain
            current_prompt = prepared["mention"]
            run["provider"] = _provider_brief(current_chain[0])
            run["provider_id"] = current_chain[0].get("id")
            run["attempt_no"] = len(run["fallback_history"]) + 1
            _broadcast(run, "ai_invoke_provider_switched", {
                "run_id": run["run_id"],
                "group_id": run["group_id"],
                "from_provider_id": previous.get("id"),
                "from_provider_name": previous.get("name"),
                "to_provider_id": current_chain[0].get("id"),
                "to_provider_name": current_chain[0].get("name"),
                # 0359 P0006 [core] 3: the fourth switch reason. The existing three all mean
                # "it never started"; this one means "it started, finished politely and left
                # nothing" — the shape this incident actually had, and the more common one.
                "reason": "no_output",
                "detail": run["fallback_history"][-1].get("detail"),
                "attempt_no": run["attempt_no"],
                "retry_kind": "no_output",
                "hop_item_seq": run.get("hop_item_seq"),
                "token_id": prepared["token_id"],
                "token_reissued": prepared["reissued"],
            })

        _finalize_run(run)
        # 0317 TR0011 (Q153 opt-1): the run is now finished, so start_run's active-run guard
        # is clear — re-spawn the next hop's worker if the self-chain flagged a boundary.
        _maybe_auto_resume_hop(run)
    except Exception:
        logger.exception("ai-invoke worker crashed for %s", run["run_id"])
        run["end_reason"] = run.get("end_reason") or "exited"
        try:
            # A crashed attempt is never retried (L0007 §2.1): judge what it left, close out.
            _judge_hop(run)
            _finalize_run(run)
        except Exception:
            logger.exception("ai-invoke settle failed for %s", run["run_id"])
            run["status"] = "finished"
        # A crashed hop is a real stop, not a boundary: drop any pending re-spawn so the
        # chain does not silently continue past a failure.
        # 0406 T0022 item 4 — drop the queue, keep the intent. A crash is the third branch
        # that does not spawn: _finalize_run only calls begin_handoff when it sees pending
        # and skips release, so just clearing it blocks the group until the lease expires.
        # A durable row lets the user resume the chain from the same place.
        crashed_pending = pop_auto_resume(run.get("group_id"))
        if crashed_pending is not None:
            crashed_code = run.get("stop_code") or HOP_HANDOFF_FAILED_STOP_CODE
            if crashed_code == HOP_HANDOFF_STOP_CODE:
                crashed_code = HOP_HANDOFF_FAILED_STOP_CODE
            _park_handoff(run, crashed_pending, crashed_code)


def _execute_provider_chain(run: dict, chain: list[dict], prompt: str) -> bool:
    """One attempt: walk the provider chain until one actually STARTS (L0006 §2.2).

    Unchanged in substance — startup/transport failures (spawn_failed / fast_fail / api_error)
    fall through to the next provider, and the first provider that runs at all ends the walk.
    Whether its work was any good is the judge's question, never this loop's.
    """
    started_ok = False
    last_reason = None
    for index, provider in enumerate(chain):
        if run["cancel_event"].is_set():
            break
        if index > 0:
            prev = chain[index - 1]
            run["provider"] = _provider_brief(provider)
            run["provider_id"] = provider.get("id")
            run["attempt_no"] = len(run["fallback_history"]) + 1
            _broadcast(run, "ai_invoke_provider_switched", {
                "run_id": run["run_id"],
                "group_id": run["group_id"],
                "from_provider_id": prev.get("id"),
                "from_provider_name": prev.get("name"),
                "to_provider_id": provider.get("id"),
                "to_provider_name": provider.get("name"),
                "reason": last_reason,
                "attempt_no": run["attempt_no"],
                # 0359: name the KIND of switch, now that there is more than one kind.
                "retry_kind": "spawn_failure",
                "hop_item_seq": run.get("hop_item_seq"),
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
            "attempt_no": run.get("attempt_no"),
            "token_id": run.get("token_id"),
        })
        if run["cancel_event"].is_set():
            break

    if not started_ok:
        run["provider_id"] = None
        run["provider"] = None
    return started_ok


def _classify_end_reason(run: dict, started_ok: bool) -> None:
    """end_reason classification (L0006 §4.1 / L0007 §2.1.2) — order unchanged, default "exited".

    "user_paused" (0252 P0008 S4): the inbox self-chain hit the user's pause flag at a step
    boundary and withheld the next token; the worker then exits normally, so the flag — not the
    exit itself — is what distinguishes a boundary stop.

    Producing nothing is deliberately NOT an end_reason. It is a JUDGMENT (outcome == "none"),
    and it is the combination of a clean "exited" with that judgment that opens a retry.
    """
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


# ── No-output retry (0359 L0007 §2.3 ~ §2.6) ─────────────────────────────────

def _attempt_elapsed_sec(run: dict) -> int:
    started = run.get("attempt_started_mono") or run.get("started_mono") or time.monotonic()
    return max(0, int(time.monotonic() - started))


def _has_pending_question(doc_ref: Optional[str]) -> bool:
    """NR0003 follow-up proposal 1: does doc_ref carry a query still waiting on the human?

    q_service.add_questions/register_answer keep the container's status 'pending' for as
    long as any item has answer_count=0, and flip it to 'done' only once every item is
    answered — so 'pending' here means exactly "a Q was just registered and nobody has
    answered it yet", never a stale done container. An AI that registers a Q and exits
    (mention_service._REMINDER_TEXT explicitly tells it to) produced nothing on purpose;
    treating that hop the same as one that silently failed is the defect this guards.

    The registration router does not query doc_ref directly either — past the first hop
    it reanchors the container through q_service.resolve_question_anchor before touching
    it (q_tapi_routes.py). Querying doc_ref (the run's spine) here would silently miss
    every container the router actually wrote once that reanchoring moved off the spine.
    """
    if not doc_ref:
        return False
    try:
        anchor = q_service.resolve_question_anchor(doc_ref)
        container = db_questions.get_container_by_doc(anchor)
    except Exception:
        logger.warning("ai-invoke pending-question probe failed for %s", doc_ref, exc_info=True)
        return False
    return bool(container) and container.get("status") == "pending"


def _retry_eligible(run: dict) -> bool:
    """May this hop open ANOTHER attempt? (L0007 §2.4 — this exact order.)

    Condition 3 is the axis the whole fix turns on. The existing provider-switch test is
    "exit code != 0 within 15 seconds", which the incident's worker passed cleanly: it ran for
    145 seconds, reported that it could not work, and exited 0. That is not a startup failure —
    it is a completed attempt with nothing to show, and it needs an edge of its own.
    """
    scope_retry = _scope_oracle_retry_run(run)
    if run.get("mode") != "continuous" and not scope_retry:
        return False
    cancel_event = run.get("cancel_event")
    if cancel_event is not None and cancel_event.is_set():
        return False
    if run.get("end_reason") != "exited":
        # cancelled / timeout / user_paused are human or clock decisions — reviving them would
        # override the person who made them. all_providers_failed is already the provider
        # walk's own verdict, reached inside the attempt.
        return False
    if run.get("pause_requested"):
        return False
    if run.get("completion_oracle") is not None and not scope_retry:
        # 0446 T0008 §3-1: the ENGINE's own scope default judge is re-askable —
        # `_recheck_no_output` below does exactly that before a second worker starts. A
        # caller's override is not, so it keeps the original block.
        return False
    if run.get("action_scope") in ("workflow_decide", "resolve_conflict"):
        return False
    if int(run.get("docs_target") or 0) < 1 and not scope_retry:
        # 0446 T0008 §3-2: start_run pins a scope-oracle run's docs_target to 0 because its
        # token cannot register a document at all. For that run the count is not a low
        # number — it is not a measurement.
        return False
    if peek_auto_resume(run.get("group_id")) is not None:
        return False        # this hop DID hand off; the next hop is already queued
    if int(run.get("docs_reached") or 0) >= 1:
        return False        # partial output is still output — a rerun would double-write
    if scope_retry and run.get("outcome") != "none":
        # 0446 T0008 §3-2: the guard directly above cannot see a scope-oracle SUCCESS —
        # `_judge_hop` pins docs_reached to 0 for every scoped run, satisfied or not. Without
        # this line a rework that correctly raised the document's revision would qualify for a
        # retry and write a SECOND revision over its own work. The scoped equivalent of
        # "output is output" is the judge's own verdict, so require it explicitly.
        return False
    # Reached with outcome == "none": a hop that only registered a Q looks exactly like one
    # that died, and the guard below is the only thing that tells them apart (§3-2).
    if _has_pending_question(run.get("doc_ref")):
        # NR0003 follow-up proposal 1: this hop stopped to wait for a human answer, not because
        # it failed — spending another attempt (and another provider) on a question the
        # human has not even seen yet would waste both without ever getting a different
        # outcome. _resolve_stop_code below reads this back into "question_pending" instead
        # of "no_output_exhausted" so the false-failure notification never fires.
        run["retry_block_reason"] = "question_pending"
        return False
    # 0443 T0002 (R0001): the cap is now the run's own resolved "재시작 횟수" pick
    # (attempts_max), not always the fixed constant — -1 means unlimited.
    attempts_max = run.get("attempts_max")
    if attempts_max is None:
        attempts_max = NO_OUTPUT_MAX_ATTEMPTS
    if attempts_max != -1 and int(run.get("attempts_used") or 0) >= attempts_max:
        return False
    if _retry_remaining_sec(run) < RETRY_MIN_REMAINING_SEC:
        # 0446 T0014 §4-4: the reading, not the gate, changed — `_retry_remaining_sec` is
        # min(no-progress clock, absolute ceiling). A hop that ran 90 productive minutes on
        # a 60-minute threshold has a NEGATIVE `_remaining_sec` and would have been refused
        # a retry for a budget it never actually exhausted. With no watchdog anchor recorded
        # the two readings are identical, so every case below still decides as it did.
        # 0446 T0010 §3-4 — the hole T0008 §3-6 / TR0009 §7-4 explicitly left to R5. This gate
        # blocked the retry and said nothing. `_stop_reason_text` already listed "a budget too
        # far spent" among the reasons no further attempt opened, but no code ever SET that
        # reason, so the only durable field (stop_reason) read "produced no document in N
        # attempts" and the real cause never left the process. No new stop_code: the reason
        # rides the existing "No further attempt was opened: <reason>." tail on
        # no_output_exhausted. Ordered BELOW the question_pending probe on purpose — a Q stop
        # is not a failure (T0008 §3-2) and must keep its own name.
        run["retry_block_reason"] = "budget_exhausted"
        return False
    # register_errors / tool_call_misses / turn_limit_exhausted deliberately do NOT block:
    # "tried to register and failed" is still zero documents, and another AI may get through.
    return True


def _recheck_no_output(run: dict) -> bool:
    """Last gate before a retry: is the hop STILL empty? (L0007 §5 — the last guard against duplicate documents.)

    The judge already waited out the settle window, but a registration can land between that
    judgment and the moment a second worker would start. Two documents from one hop is worse
    than one wasted hop, so ask again and cancel the retry if anything appeared.
    """
    if _scope_oracle_retry_run(run):
        # 0446 T0008 §3-4: a rework run never creates a document, so the document query below
        # would answer "still empty" for it under every circumstance — turning the one guard
        # standing between a late write and a duplicate one into a constant True. Ask the
        # scope's own judge again instead, exactly as `_judge_hop` did.
        oracle = run.get("completion_oracle")
        if oracle is None:
            return False
        try:
            satisfied = bool(oracle())
        except Exception:
            logger.warning("ai-invoke scoped retry recheck failed for %s",
                           run["run_id"], exc_info=True)
            # Deliberately the opposite of the document path's fallback below: when the judge
            # cannot answer, a wasted hop is cheaper than a second revision written over the
            # worker's own (L0007 §5).
            return False
        if not satisfied:
            return True
        # The same shape `_judge_hop` gives a satisfied scope oracle.
        run["docs_reached"] = 0
        run["reached_doc_ids"] = []
        run["outcome"] = "complete"
        run["oracle_mismatch"] = False
        return False
    try:
        new_docs = _oracle_new_docs(run)
    except Exception:
        logger.warning("ai-invoke retry recheck failed for %s", run["run_id"], exc_info=True)
        return True
    if not new_docs:
        return True
    hop_target = run.get("docs_target") or 1
    if run.get("mode") == "continuous" and peek_auto_resume(run.get("group_id")) is not None:
        hop_target = 1
    run["docs_reached"] = len(new_docs)
    run["reached_doc_ids"] = [d["doc_id"] for d in new_docs]
    run["outcome"] = "complete" if len(new_docs) >= hop_target else "partial"
    run["oracle_mismatch"] = False
    return False


def _retry_provider_chain(run: dict) -> list[dict]:
    """Return the same selected provider for this hop's no-output retry.

    0435 T0004 replaces both older schedules — individual -> individual -> common and
    configured-order fallback — with one contract for every origin tier: a retry NEVER
    replays the priority tiers or falls back to another provider, only the finalized chain
    head. 0443 T0002 (R0001) makes how MANY retries fire a per-run pick ("재시작 횟수")
    instead of a fixed one-shot — `attempts_max` (see _resolve_restart_max_attempts) is
    the same cap `_retry_eligible` already checked before calling this, re-checked here
    as a second line of defense; -1 means unlimited.
    """
    attempts_used = int(run.get("attempts_used") or 0)
    attempts_max = run.get("attempts_max")
    if attempts_max is None:
        attempts_max = NO_OUTPUT_MAX_ATTEMPTS
    if attempts_max != -1 and attempts_used >= attempts_max:
        return []
    selected_provider_id = run.get("continuation_selected_provider_id")
    if not selected_provider_id:
        return []
    try:
        effective = ai_settings_service.resolve_effective(run["project_id"])
        chain = effective.get("providers") or []
    except Exception:
        logger.warning("ai-invoke retry chain lookup failed for %s", run["run_id"], exc_info=True)
        return []
    selected = next(
        (provider for provider in chain if provider.get("id") == selected_provider_id),
        None,
    )
    return [selected] if selected else []


def _token_reusable(row: dict) -> bool:
    """Can the dead attempt's work token simply be handed to the next provider?

    Not merely "is it alive": a token with two minutes left would expire mid-attempt, and
    handing a worker a token that dies under it is worse than minting a fresh one (L0007 §2.5).
    """
    if row.get("consumed_at") or row.get("revoked_at"):
        return False
    expires_at = row.get("expires_at")
    if not expires_at:
        return False
    try:
        remaining = (
            datetime.fromisoformat(str(expires_at)) - datetime.now(timezone.utc)
        ).total_seconds()
    except Exception:
        return False
    return remaining >= RETRY_MIN_REMAINING_SEC


def _prepare_retry_token(run: dict) -> Optional[dict]:
    """A usable work token + prompt for the next attempt (L0007 §2.5).

    Reuse first. A dead hop's token is normally neither consumed nor revoked — NR0003 §6 found
    24 of them sitting in the tokens table, one being this incident's own tok_20260730_000032 —
    so the next provider can just be handed the same one. Reissue only when it is spent, revoked
    or about to expire: advance_workflow re-opens the SAME head (this hop registered nothing, so
    the effective head has not moved) and revokes the stale token itself, which is why this can
    neither double-issue nor skip a slot. No reissue path at all means no retry.
    """
    token_id = run.get("token_id")
    row = None
    if token_id:
        try:
            row = db_tokens.get_by_id(token_id)
        except Exception:
            logger.warning("ai-invoke retry token lookup failed for %s",
                           run["run_id"], exc_info=True)
            row = None
    if row is not None and _token_reusable(row):
        return {"mention": run.get("mention"), "token_id": token_id,
                "token_id_before": token_id, "reissued": False}

    issue_builder = run.get("issue_builder")
    if issue_builder is None:
        return None
    try:
        issue = _call_issue_builder(issue_builder, run["run_id"])
    except Exception:
        logger.warning("ai-invoke retry token reissue failed for %s",
                       run["run_id"], exc_info=True)
        return None
    mention = issue.get("mention")
    if not mention:
        return None
    if run.get("mode") == "continuous" or (
        run.get("mode") == "single" and run.get("action_scope") == "new"
    ):
        retry_audit: dict = {}
        mention = _inject_hop_notes(
            mention,
            run["doc_ref"],
            default_note=run.get("continuation_default_note"),
            note_overrides=run.get("continuation_note_overrides"),
            instruction_mode=run.get("continuation_instruction_mode"),
            auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
            fold_worker_item_seq=(run.get("mode") == "continuous"),
            locale=run.get("continuation_locale"),
            audit=retry_audit,
        )
        # 0406 T0022 item 5: a retry rebuilds the prompt from scratch, so the audit must
        # point at THAT prompt — otherwise attempt 1's hash gets attached to attempt 2's
        # run while still claiming "the note went in".
        run.update(retry_audit)
    before = token_id
    run["token_id"] = issue.get("token_id")
    if run.get("group_id"):
        db_group_ai_leases.update_token(run["group_id"], run["run_id"], run["token_id"])
    run["raw_token"] = issue.get("raw_token") or run.get("raw_token")
    run["mention"] = mention
    run["prompt_final_length"], run["prompt_final_sha256"] = prompt_digest(mention)
    if issue.get("worker_document_type"):
        run["worker_document_type"] = issue.get("worker_document_type")
    if issue.get("auto_handled_item_seqs") is not None:
        run["auto_handled_item_seqs"] = list(issue.get("auto_handled_item_seqs") or [])
    return {"mention": mention, "token_id": issue.get("token_id"),
            "token_id_before": before, "reissued": True}


def _no_output_detail(run: dict) -> Optional[str]:
    """The sentence that finally reaches a human (L0007 §2.6).

    In the incident this text existed, was precise, and even named its own fix — and lived only
    in a scratch file the server deletes after seven days.
    """
    head = (
        f"worker exited {run.get('exit_code')} after {_attempt_elapsed_sec(run)}s "
        "without registering a document"
    )
    message = excerpt(run.get("last_message"))
    return head if not message else f"{head}; last message: {message}"


def _archive_attempt(run: dict, reason: str, token_id: Optional[str]) -> None:
    """Fold the attempt that just ended into the history (L0007 §2.6)."""
    run["fallback_history"].append({
        "provider_id": run.get("provider_id"),
        "provider_name": (run.get("provider") or {}).get("name"),
        "reason": reason,
        "detail": _no_output_detail(run),
        "token_id": token_id,
        "attempt_no": run.get("attempt_no"),
        "exit_code": run.get("exit_code"),
        "duration_sec": _attempt_elapsed_sec(run),
    })
    # Keep the most recent NON-EMPTY message: a later attempt may say nothing at all, and the
    # sentence that explains the failure is usually the one an earlier attempt left behind.
    if run.get("last_message"):
        run["last_message_seen"] = run["last_message"]


def _reset_attempt_state(run: dict) -> None:
    """Clear the per-ATTEMPT observations, keep the per-HOP identity (L0007 §2.6).

    run_id / started_mono / timeout_sec / deadline_at / baseline_seq all survive: one hop gets
    one budget and one baseline no matter how many attempts it spends inside them.
    """
    run["exit_code"] = None
    run["timed_out"] = False
    run["last_message"] = None
    run["last_message_received"] = False
    run["stdout_tail"] = None
    run["stderr_tail"] = None
    run["register_errors"] = []
    run["tool_call_misses"] = 0
    run["turn_limit_exhausted"] = False
    run["attempt_started_mono"] = time.monotonic()
    # 0446 T0014 §4-1: the previous attempt's watchdog verdict is not this one's. The
    # no-progress window is re-anchored when the next watchdog starts; the absolute
    # ceiling is not, because started_mono survives this reset by the contract above.
    run["watchdog_kill"] = None
    # codex writes its final message to a file in the run scratch. Leaving the previous
    # attempt's file behind would let the next attempt inherit words it never said.
    try:
        stale = Path(run["scratch_dir"]) / "last_message.txt"
        if stale.is_file():
            stale.unlink()
    except Exception:
        logger.warning("ai-invoke stale last-message cleanup failed for %s",
                       run["run_id"], exc_info=True)


def _now_mono() -> float:
    """The monotonic clock behind ONE name, so the watchdog's 30-minute and 4-hour
    edges can be exercised without waiting for them (0446 T0014 §5). Nothing else
    about it differs from calling time.monotonic() directly."""
    return time.monotonic()


def _remaining_sec(run: dict) -> float:
    """The hop's nominal budget measured from hop START — unchanged, and still exactly
    what the `timeout_sec` / `deadline_at` of every response and stored row means.

    0446 T0014 §2-5: that deadline is now the EARLIEST this run may be stopped rather
    than the latest, and it lands only if the run shows nothing new for the whole
    budget. The two helpers below are the ones the watchdog and the retry gate ask.
    """
    return run["timeout_sec"] - (time.monotonic() - run["started_mono"])


def _stall_remaining_sec(run: dict) -> float:
    """Seconds left on the NO-PROGRESS clock (0446 T0014 §2-3).

    The same budget with a different anchor: it runs from the last point this run was
    known to be moving (`stall_anchor_mono` — set when an attempt launches, moved
    forward by every observed document or source change) instead of from hop start.
    With no anchor recorded this is precisely `_remaining_sec`, which is why a run that
    never had a watchdog keeps its previous behaviour to the second.
    """
    anchor = run.get("stall_anchor_mono")
    if anchor is None:
        anchor = run["started_mono"]
    return run["timeout_sec"] - (_now_mono() - anchor)


def _absolute_cap_sec() -> int:
    """The run's hard ceiling, in seconds (0446 T0014 §2-4).

    Deliberately RUN_TIMEOUT_CAP_SEC itself, read at CALL time instead of copied into
    a second four-hour literal: that constant is already four hours, already the roof
    of the per-document formula and already what a `target_to_end` run gets, so a
    duplicate could only drift away from it. Reading it through a function is also
    what keeps `test_ai_invoke_0187.py::TestForcedKill` honest — it shortens the cap to
    one second to prove the timeout path, and a bound-at-import alias would have
    silently ignored it.
    """
    return RUN_TIMEOUT_CAP_SEC


def _absolute_remaining_sec(run: dict) -> float:
    """Seconds left before the run's hard ceiling (0446 T0014 §2-4).

    Measured from `started_mono` — the HOP's start, not the attempt's — so a no-output
    retry inherits what is left of the four hours instead of being handed a fresh four.
    """
    return _absolute_cap_sec() - (_now_mono() - run["started_mono"])


def _retry_remaining_sec(run: dict) -> float:
    """The budget the retry gate asks about: how long could another attempt run?

    0446 T0014 §4-4: `_remaining_sec` alone would answer "none" for every hop that
    legitimately outlived its no-progress threshold BY WORKING, and `_retry_eligible`
    would then report `budget_exhausted` for a run with hours of ceiling left. Both
    limits are real, so the smaller one is the answer.
    """
    return min(_stall_remaining_sec(run), _absolute_remaining_sec(run))


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


# ── No-progress watchdog (0446 T0014 §3) ─────────────────────────────────────
#
# `_cli_execute` waits for the worker with a single `communicate(timeout=...)`, so the
# only question it could ever ask was "has the clock run out?". NR0003 measured both
# ways that goes wrong on a fixed hour: a 74-minute TR hop that was still registering
# documents got cut off, and a worker that died in its first minutes still held its
# group for the remaining 59. This watchdog asks the other question — "is anything
# still happening?" — beside that wait, on its own thread, and answers it from the two
# signals the run already carries: the group's document max-seq and `git status` on the
# group worktree. The stdin prompt is still handed to `communicate()` exactly once and
# the watchdog never touches the pipes (§3-1).
_watchdog_kill_lock = threading.Lock()


def _observe_group_max_seq(run: dict) -> Optional[int]:
    """The document signal, or None for "could not observe" (§3-5).

    Deliberately the same draft-INCLUSIVE max-seq `_work_landed` falls back on: counting
    a stray draft as progress only makes this guard more reluctant to kill, and that is
    the safe direction for a "may I end this process?" question.
    """
    try:
        return int(db_docs.get_group_max_seq(run["group_id"]))
    except Exception:
        logger.warning("ai-invoke %s: progress watchdog could not read the document seq",
                       run.get("run_id"), exc_info=True)
        return None


def _claim_watchdog_kill(run: dict, proc, stop_event: threading.Event,
                         kind: str, now: float) -> bool:
    """Decide — once — whether the watchdog may end this process tree (§4-2).

    Every other exit owns the same process, so the claim is taken under a lock and
    re-checks all of them: the run thread already past `communicate()` (`stop_event`), a
    user cancel (which outranks the clock in `_classify_end_reason` and must stay
    `cancelled`), an earlier tick's claim, and a child that exited on its own between the
    poll and here. Losing any of those races means doing nothing at all.
    """
    with _watchdog_kill_lock:
        if stop_event.is_set():
            return False
        cancel_event = run.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            return False
        if run.get("watchdog_kill") is not None:
            return False
        if proc.poll() is not None:
            return False
        anchor = run.get("stall_anchor_mono")
        if anchor is None:
            anchor = run["started_mono"]
        run["watchdog_kill"] = {
            "kind": kind,                       # "no_progress" | "absolute_cap"
            "stalled_sec": int(max(0.0, now - anchor)),
            "elapsed_sec": int(max(0.0, now - run["started_mono"])),
            "threshold_sec": int(run.get("timeout_sec") or 0),
            "absolute_cap_sec": _absolute_cap_sec(),
            "last_progress_at": run.get("last_progress_at"),
            "progress_observations": int(run.get("progress_observations") or 0),
            "attempt_no": int(run.get("attempt_no") or 0),
        }
        # The same flag an expired `communicate()` raises, so this ends as end_reason
        # "timeout" / stop_code "timeout" and NEVER as a provider spawn_failed or
        # fast_fail (§4-3). `watchdog_kill` is the minimal in-memory mark that tells the
        # two kinds apart; T#2 turns it into the durable sentence. No new stop code and
        # no new column here (§3-4).
        run["timed_out"] = True
        claim = run["watchdog_kill"]
    logger.warning(
        "ai-invoke %s: %s — ending the worker (stalled %ss of %ss, elapsed %ss of %ss)",
        run.get("run_id"), kind, claim["stalled_sec"], claim["threshold_sec"],
        claim["elapsed_sec"], claim["absolute_cap_sec"],
    )
    try:
        process_runner.kill_process_tree(proc)
    except Exception:
        logger.warning("ai-invoke %s: watchdog kill failed", run.get("run_id"), exc_info=True)
    return True


def _progress_watchdog_loop(run: dict, proc, stop_event: threading.Event,
                            interval: float = STALL_POLL_INTERVAL_SEC) -> Optional[str]:
    """The poll body. Returns the kill kind, or None if it never killed anything."""
    source_root = Path(run["source_root"]) if run.get("source_root") else None
    # §3-3: the run's OWN start snapshot is the first comparison point, and every tick
    # after that is compared with the previous SUCCESSFUL read. Comparing forever against
    # the baseline instead would count one early edit as progress on every later tick — a
    # worker that wrote a single file and then hung would look busy until the ceiling.
    git_watermark = run.get("dirty_baseline")
    # A run with no source tree at all (the scratch fallback) has no source signal to
    # fail: that is not an unreadable sample, and treating it as one would disable the
    # guard outright for those runs. The document signal alone speaks for them.
    git_enabled = source_root is not None and source_root.is_dir()
    doc_watermark = run.get("baseline_seq")          # §3-2
    if not git_enabled:
        logger.info("ai-invoke %s: progress watchdog has no source tree — documents only",
                    run.get("run_id"))

    while not stop_event.wait(interval):
        now = _now_mono()
        cancel_event = run.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            return None
        if proc.poll() is not None:
            return None
        # The ceiling is unconditional (§3-6): it does not care how much progress there
        # has been, and it does not need a readable sample to be true.
        if now - run["started_mono"] >= _absolute_cap_sec():
            return "absolute_cap" if _claim_watchdog_kill(
                run, proc, stop_event, "absolute_cap", now) else None

        moved: list[str] = []
        readable = True
        seq = _observe_group_max_seq(run)
        if seq is None:
            readable = False
        elif doc_watermark is None:
            doc_watermark = seq                      # first reading: nothing to compare to
        elif seq > doc_watermark:
            doc_watermark = seq
            moved.append("document")
        if git_enabled:
            paths = _git_status_paths(source_root)
            if paths is None:
                readable = False
            elif git_watermark is None:
                git_watermark = paths
            elif paths != git_watermark:
                git_watermark = paths
                moved.append("source")               # added AND removed paths both count

        if moved:
            # §3-5, second half: one signal actually moving is enough — the other one
            # standing still proves nothing about it.
            run["stall_anchor_mono"] = now
            run["last_progress_mono"] = now
            run["last_progress_at"] = now_iso()
            run["last_progress_signal"] = ",".join(moved)
            run["progress_observations"] = int(run.get("progress_observations") or 0) + 1
            continue
        if not readable:
            # §3-5, first half: an unreadable sample means "unknown", not "nothing
            # happened". Warn and let the next tick decide. A permanently blind run is
            # still ended on time by the ceiling above.
            logger.warning("ai-invoke %s: progress watchdog sample was unreadable — retrying",
                           run.get("run_id"))
            continue
        anchor = run.get("stall_anchor_mono")
        if anchor is None:
            anchor = run["started_mono"]
        if now - anchor >= float(run["timeout_sec"]):
            return "no_progress" if _claim_watchdog_kill(
                run, proc, stop_event, "no_progress", now) else None
    return None


def _start_progress_watchdog(run: dict, proc,
                             interval: float = STALL_POLL_INTERVAL_SEC) -> tuple:
    """Start the watchdog for ONE attempt. Returns (stop_event, thread)."""
    stop_event = threading.Event()
    # Each attempt opens its own no-progress window — a fresh worker cannot be charged
    # for the silence of the one before it. The ABSOLUTE ceiling deliberately does NOT
    # reset: it is measured from started_mono, which `_reset_attempt_state` keeps (§2-4).
    run["stall_anchor_mono"] = _now_mono()
    run["watchdog_kill"] = None
    thread = threading.Thread(
        target=_progress_watchdog_loop, args=(run, proc, stop_event, interval),
        name=f"ai-invoke-watchdog-{run.get('run_id')}", daemon=True,
    )
    thread.start()
    return stop_event, thread


def _stop_progress_watchdog(stop_event: Optional[threading.Event], thread,
                            run_id: Optional[str] = None) -> None:
    """Stop and join the watchdog on EVERY exit of the attempt (§4-1).

    Called from `_cli_execute`'s finally, so a natural exit, a TimeoutExpired, a broken
    stdin pipe, a user cancel and a fast-fail all come through here. The join matters as
    much as the event: a thread left running would still hold a `proc` the next attempt
    is about to replace.
    """
    if stop_event is None:
        return
    stop_event.set()
    if thread is None:
        return
    thread.join(timeout=STALL_WATCHDOG_JOIN_SEC)
    if thread.is_alive():
        # It can no longer kill anything — `_claim_watchdog_kill` re-reads stop_event
        # under the lock — but it should not have taken this long, so say so.
        logger.warning("ai-invoke %s: progress watchdog did not stop within %ss",
                       run_id, STALL_WATCHDOG_JOIN_SEC)


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
    # 0446 T0014 §3-1: the no-progress threshold is enforced BESIDE this wait, by the
    # watchdog thread, because `communicate()` cannot be asked "is it still working?".
    # What is left for the wait itself is the absolute ceiling — the one deadline that
    # holds however well the worker is doing (§3-6) — so a run that keeps producing is
    # no longer cut off at its threshold, and a run that produces nothing is still
    # ended there, by the watchdog, long before this timeout could fire.
    watchdog_stop, watchdog_thread = _start_progress_watchdog(run, proc)
    remaining = max(1.0, _absolute_remaining_sec(run))
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
        if (
            elapsed < FAST_FAIL_WINDOW_SEC
            and not _work_landed(run)
            # 0446 T0014 §4-3: a watchdog kill is a clock decision, never a provider
            # startup failure — it must not send the chain to the next provider.
            and run.get("watchdog_kill") is None
        ):
            return "spawn_failed", str(exc)[:500]
    finally:
        run["proc"] = None
        _stop_progress_watchdog(watchdog_stop, watchdog_thread, run.get("run_id"))

    # 0446 T0014 §4-3: a watchdog kill ends `communicate()` NORMALLY — the child is
    # already gone, so there is no TimeoutExpired to catch and the local flag above is
    # still False. Merge the verdict in before anything reads it: the exit code of a
    # killed worker is not a verdict (it stays None, as on any other timeout) and a
    # killed worker is not a fast_fail candidate. `watchdog_kill` — not `run["timed_out"]`
    # — is the source here, because it is re-armed per attempt by the watchdog itself.
    timed_out = timed_out or run.get("watchdog_kill") is not None
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
    try:
        key = ai_settings_service.get_provider_secret(secret_scope, provider.get("id"))
    except ApiKeyCryptoError:
        # 0371: a key IS stored, the master key just cannot read it. Reporting it as
        # "not set" would send the operator hunting for a key nobody removed.
        return "spawn_failed", "api_key_unreadable"
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
        # 0446 T0014 §4-5: the model-call loop keeps the ORIGINAL reading. It has no
        # subprocess to watch and no watchdog attached, so `_stall_remaining_sec` would
        # return this very number anyway; naming `_remaining_sec` here keeps the API
        # call ceiling and its cancel/timeout behaviour bit-for-bit unchanged.
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
                        continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
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
    """Judge, then finalize — the pre-0359 shape, kept for every caller that wants both.

    0359 L0007 §2.1 split these two apart so the no-output retry has somewhere to stand:
    _worker now calls _judge_hop once per ATTEMPT and _finalize_run once per HOP.
    """
    _judge_hop(run)
    _finalize_run(run)


def _judge_hop(run: dict) -> None:
    """Decide what this attempt produced (L0007 §2.2). Exactly once per attempt — never twice
    for one attempt, and never by merging two attempts' verdicts. docs_reached is always
    measured from the hop's baseline_seq, so a later attempt's document is simply the answer.

    Judgment content is unchanged from the pre-0359 _settle_and_judge; only the finalize call
    that used to be welded to each branch has been lifted out.
    """
    time.sleep(ORACLE_SETTLE_SEC)
    if run.get("action_scope") == "resolve_conflict":
        resolved = _conflict_resolved(run)
        run["docs_reached"] = 0
        run["reached_doc_ids"] = []
        run["outcome"] = "complete" if resolved else "none"
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
                    continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
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


# ── 0446 T0016 §2/§3-1: the durable reading of a watchdog stop ───────────────
# T0014's verdict lives in `run["watchdog_kill"]`, and every number in it is an offset
# from a monotonic clock that means nothing once this process is gone. These helpers
# turn it into the pair that goes on the row: a fixed vocabulary the next step can
# branch on, and one sentence a person can read.
#
# The sentence is English, like `stop_reason` — the column right beside it, written by
# `_stop_reason_text`, and read by the same UI. A stored row has no locale to be written
# in; the rework block that quotes this line localizes its own labels around it (§4-5).
_TIMEOUT_KINDS = ("no_progress", "absolute_cap")


def _format_span(seconds: int) -> str:
    """Short, stable reading of a duration — whole minutes once there is one."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} sec"
    return f"{seconds // 60} min"


def _resolve_timeout_diagnostics(run: dict) -> tuple[Optional[str], Optional[str]]:
    """`(timeout_kind, timeout_diagnosis)` for a finished run, or `(None, None)`.

    Called once from `_finalize_run` AFTER `duration_ms` is fixed, so the total this
    sentence quotes is exactly the total the row stores. Two rounding rules matter:
    `duration_ms` is floored to whole seconds, and the stalled window is then clamped to
    that total. The watchdog measured its own number one poll before finalize measured
    the total, so without the clamp a boundary case can print a stall longer than the run
    that contains it.

    A run with no watchdog mark — a plain `communicate()` expiry, an API-mode run, any row
    written before T0014 — returns `(None, None)`. That NULL is the third state (§2-1):
    not "unknown kind of stall", but "nothing watched this one".
    """
    kill = run.get("watchdog_kill")
    if not isinstance(kill, dict):
        return None, None
    kind = kill.get("kind")
    if kind not in _TIMEOUT_KINDS:
        return None, None
    total_sec = max(0, int(run.get("duration_ms") or 0) // 1000)
    stalled_sec = min(max(0, int(kill.get("stalled_sec") or 0)), total_sec)
    if kind == "absolute_cap":
        # §2-2: a run that was still producing when it hit the ceiling must NOT be filed as
        # stalled. The observation count is the evidence, and it is honest about the blind
        # case too (a run whose samples never read stops here with 0 observations).
        cap_sec = int(kill.get("absolute_cap_sec") or _absolute_cap_sec())
        return kind, (
            f"Reached the {_format_span(cap_sec)} absolute run ceiling after "
            f"{_format_span(total_sec)}; not a no-progress stop "
            f"(progress observations: {int(kill.get('progress_observations') or 0)})."
        )
    return kind, (
        f"No document registration or source change during the last "
        f"{_format_span(stalled_sec)} of {_format_span(total_sec)} total."
    )


def previous_timeout_handoff(group_id: str, doc_ref: Optional[str] = None) -> Optional[dict]:
    """What the run right before this one left behind — but only if it timed out (§4-1/2).

    Read while the rework prompt is being assembled, which is before the new run has a row
    of its own (finalize writes that), so "the latest finished row" really is the previous
    hop. Exactly ONE row is considered: if the newest run exited cleanly this returns None
    even when an older timeout sits behind it, because a stop that has already been
    superseded is not a handoff.

    Returns None for every other shape too — no row at all, a cancel, a fast_fail, another
    group, another document — and that None is what keeps the existing rework prompt
    byte-identical for every run that is not resuming a timeout.

    Never raises. A handoff is an aid; if the row cannot be read the worker gets exactly
    the prompt it would have got before this existed.
    """
    try:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        row = db_runs.latest_finished_for_group(group_id, doc_ref=doc_ref)
    except Exception:
        logger.warning("ai-invoke previous-run handoff lookup failed for group %s",
                       group_id, exc_info=True)
        return None
    if not row:
        return None
    if row.get("end_reason") != "timeout" and row.get("stop_code") != "timeout":
        return None
    source_dirty = row.get("source_dirty")
    return {
        "run_id": row.get("run_id"),
        "finished_at": row.get("finished_at"),
        "timeout_kind": row.get("timeout_kind"),
        "timeout_diagnosis": row.get("timeout_diagnosis"),
        # Three states, kept apart: True (files below), False (the tree was clean), None
        # (the run could not read git at all, so nothing is claimed either way).
        "source_dirty": None if source_dirty is None else bool(source_dirty),
        # A dirty run with an empty list stays dirty — the block says so rather than
        # inventing file names for it.
        "source_dirty_files": (
            list(row.get("source_dirty_files") or [])[:SOURCE_DIRTY_FILES_LIMIT]
            if source_dirty else []
        ),
    }


def _finalize_run(run: dict) -> None:
    """Close the hop out — once, whatever it took to get here (L0007 §2.7).

    Three ordering rules, and all three are about what a human sees:
      1. the stop row is written BEFORE the broadcast — the browser re-reads active-all the
         moment it sees the finish event, so a late row means a late card;
      2. the record is persisted BEFORE the broadcast — a detail lookup triggered by that
         same event must not answer 404;
      3. neither of them may block the broadcast. Both swallow their own failures: a record
         is an aid, not the run.
    """
    # Final last_message (L0007 §2.6): the last attempt may have said nothing at all, while an
    # earlier one left the sentence that actually explains the failure. Keep the explanation.
    if not run.get("last_message") and run.get("last_message_seen"):
        run["last_message"] = run["last_message_seen"]
        run["last_message_received"] = True

    # 0357 T0004: fold this HOP's documents into the CHAIN counter — here, at finalize,
    # not in the judge. A hop may be judged several times now (once per no-output attempt,
    # L0007 §2.2), and crediting the chain on the first of those would freeze the count at
    # attempt 1's zero and lose the document a later attempt went on to produce.
    if not run.get("chain_docs_accounted"):
        run["chain_docs_reached"] = (
            int(run.get("chain_docs_reached") or 0) + int(run.get("docs_reached") or 0)
        )
        run["chain_docs_accounted"] = True

    respawn_pending = peek_auto_resume(run.get("group_id")) is not None
    # Keep ownership across a hop boundary; the successor atomically transfers generation.
    if respawn_pending:
        db_group_ai_leases.begin_handoff(run["group_id"], run["run_id"])
    run["stop_code"] = _resolve_stop_code(run, respawn_pending)
    run["resumable"] = is_resumable(run["stop_code"])
    run["stop_reason"] = _stop_reason_text(run["stop_code"], run)

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
        run["source_dirty_files"] = spilled[:SOURCE_DIRTY_FILES_LIMIT]

    # The whole hop, every attempt inside it — not just the last one.
    run["duration_ms"] = int((time.monotonic() - run["started_mono"]) * 1000)
    # 0446 T0016 §3-1: source delta and duration are both settled now, so read the
    # watchdog's verdict ONCE, here. Nothing downstream — the row, the finished payload,
    # the next rework prompt — touches `watchdog_kill` again; they read these two.
    run["timeout_kind"], run["timeout_diagnosis"] = _resolve_timeout_diagnostics(run)
    run["finished_at"] = now_iso()
    run["status"] = "finished"

    _apply_stop_row(run, respawn_pending)
    _persist_run_record(run)
    _notify_chain_failure_if_needed(run)

    _broadcast(run, "ai_invoke_finished", finished_payload(run))
    _broadcast(run, "group_view_refresh", {
        "group_id": run["group_id"],
        "reason": "ai_invoke_finished",
    })
    if not respawn_pending:
        db_group_ai_leases.release(run["group_id"], run["run_id"])

# ── Stop classification (0359 L0007 §4.1 ~ §4.3) ─────────────────────────────

def _resolve_stop_code(run: dict, respawn_pending: bool) -> Optional[str]:
    """Why did the chain stop here? (L0007 §4.1 — evaluated in this exact order.)

    1-4 outrank whatever the inbox tagged: a human cancel or a blown deadline is the truth no
    matter what the self-chain thought it was doing when the request arrived. A single-mode or
    non-document run has no stop code at all — nothing about it can stop a chain.
    """
    cancel_event = run.get("cancel_event")
    if (cancel_event is not None and cancel_event.is_set()) or run.get("end_reason") == "cancelled":
        return "cancelled"
    if run.get("end_reason") == "timeout":
        return "timeout"
    if run.get("end_reason") == "user_paused":
        return "user_paused"
    if run.get("end_reason") == "all_providers_failed":
        return "providers_exhausted"
    # 0393 B0001 / T0005 §2-6: the group gate refused this run's OWN worker, so nothing the
    # worker submitted was ever registered. It outranks whatever the inbox tagged because
    # the inbox never saw the request — the middleware turned it away first. Deliberately
    # mode-independent: B0001's three dead reviews were mode="single", the exact shape that
    # used to fall all the way through this function to `return None` (no code, no reason).
    # Only claimed when the run produced nothing; a run that did land its documents and then
    # tripped the gate on a stray call keeps its ordinary ending and the register_errors row.
    if run.get("lease_denied_code") and int(run.get("docs_reached") or 0) == 0:
        return "group_lease_denied"
    if run.get("inbox_stop_code"):
        return run["inbox_stop_code"]
    if respawn_pending:
        return "hop_handoff"
    if run.get("retry_block_reason") == "question_pending":
        # NR0003 follow-up proposal 1/3: this hop's own docs_target/docs_reached shape is
        # identical to no_output_exhausted's (target ≥1, reached 0) — the only thing that
        # tells them apart is WHY nothing landed. _has_pending_question already told
        # _retry_eligible this hop is waiting on a human answer, not silently dead; name it
        # separately so it never reaches _notify_chain_failure_if_needed as a failure.
        return "question_pending"
    if int(run.get("docs_reached") or 0) == 0 and run.get("outcome") == "none" and (
        (run.get("mode") == "continuous" and int(run.get("docs_target") or 0) >= 1)
        # 0446 T0008 §3-7: the same fact told on the axis a scope-oracle rework run actually
        # has — it exited on its own and its judge was not satisfied (the `oracle_mismatch`
        # shape). Its docs_target is 0 by construction, so the document-count clause above can
        # never speak for it, and all 15 measured clean failures ended with stop_code NULL.
        # A rework that DID raise the revision has outcome "complete" and keeps its NULL.
        or (_scope_oracle_retry_run(run) and run.get("end_reason") == "exited")
    ):
        # The shape of this incident: the hop ran, the hop finished, the hop made nothing —
        # and until now the system had no name for that, so it treated it as an ordinary end.
        return "no_output_exhausted"
    return None


def is_resumable(stop_code: Optional[str]) -> bool:
    """L0007 §4.2 — one criterion: would re-running this hop have a chance?

    Stops a human has to clean up (head mismatch, approval, blocked advance) and stops a human
    meant (cancel) are deliberately excluded. Resuming those would undo a person's decision.
    """
    return stop_code in RESUMABLE_STOP_CODES


def _stop_reason_text(stop_code: Optional[str], run: dict) -> Optional[str]:
    """L0007 §4.3 — English, because the same sentence is read by the worker, stored on the
    record and shown on the card; one wording for all three."""
    if stop_code is None:
        return None
    if stop_code == "chain_completed":
        return (f"Target step {run.get('continuation_target_seq')} reached; "
                "the chain is complete.")
    if stop_code == "hop_handoff":
        return "This hop produced its document; the next hop starts in a new worker."
    if stop_code == "no_output_exhausted":
        provider_name = run.get("continuation_selected_provider_name") or run.get("continuation_selected_provider_id") or "Selected provider"
        # 0446 T0008 §3-6: `retry_block_reason` has no column in `ai_invoke_runs` and is not
        # in `finished_payload` either, so a run that never got to open its retry
        # ("token_unavailable", "providers_exhausted_for_retry", or a budget too far spent)
        # left no durable trace of WHY. `stop_reason` is persisted, so the reason rides here.
        # question_pending never reaches this branch — it is named above.
        blocked = run.get("retry_block_reason")
        blocked_text = f" No further attempt was opened: {blocked}." if blocked else ""
        return (f'"{provider_name}" produced no document in '
                f"{int(run.get('attempts_used') or 0)} attempts. "
                "The chain stopped without switching to another provider."
                f"{blocked_text}")
    if stop_code == "question_pending":
        return ("This hop registered a query and is waiting for a human answer. "
                "The chain stopped and can be resumed once it is answered.")
    if stop_code == "providers_exhausted":
        return ("No AI provider could be started for this hop. "
                "The chain stopped and can be resumed.")
    if stop_code == "timeout":
        return (f"The hop exceeded its {run.get('timeout_sec')}s budget. "
                "The chain stopped and can be resumed.")
    if stop_code == "cancelled":
        return f"Cancelled by the user during attempt {run.get('attempt_no')}."
    if stop_code == "user_paused":
        return "Paused by the user at the step boundary."
    if stop_code == "head_slot_mismatch":
        return ("The submitted document did not fill the current workflow head slot; "
                "a human must triage.")
    if stop_code == "approve_denied":
        return ("The token issuer lacks document.approve; a human must approve before "
                "continuing.")
    if stop_code == "approve_failed":
        return f"Auto-approval failed: {run.get('inbox_stop_detail') or 'unknown error'}"
    if stop_code == "advance_blocked":
        return (f"Could not advance to the next step: "
                f"{run.get('inbox_stop_detail') or 'unknown error'}")
    if stop_code == "review_hold":
        return "Review mode: waiting for the human go."
    # 0414 L0008 §1.2 — one English sentence per code, read by the worker, stored on the
    # record and shown on the card alike.
    if stop_code == REVIEW_EXHAUSTED_STOP_CODE:
        # Legacy (0414 M0020): only chains parked before that change carry this code.
        return ("Every review round this step was given has been used and the reviewer "
                "still reported issues. The document is left rejected with those findings; "
                "a human must take it from here.")
    if stop_code == REVIEW_CAP_REACHED_STOP_CODE:
        # Legacy (0414 0022-TR rejection): "until it passes" has no round ceiling any
        # more, so nothing emits this now; only chains parked earlier carry it. Resuming
        # it is a normal resume — see REVIEW_CAP_REACHED_STOP_CODE in RESUMABLE_STOP_CODES.
        return ("Review was set to run until it passes and was parked at the round "
                "ceiling that setting used to carry. That ceiling is gone: resuming this "
                "run lets review and rework repeat until a pass, same as any other -1 run.")
    if stop_code == REVIEW_VERDICT_HOLD_STOP_CODE:
        return ("The reviewer returned 'hold' — it could not judge this document. "
                "A human must decide; the chain does not resume on its own.")
    if stop_code == REVIEW_STALLED_STOP_CODE:
        return ("The review loop stopped making progress: the rework hop raised no new "
                "revision, or the same findings came back twice. A human must triage.")
    if stop_code == REVIEW_NO_VERDICT_STOP_CODE:
        return ("The review hop finished without recording a verdict. "
                "The chain stopped and can be resumed.")
    if stop_code == REVIEW_REJECT_DENIED_STOP_CODE:
        return ("The chain issuer lacks document.reject, so the reviewer's issues could not "
                "be turned into a rejection. The document is left unapproved.")
    if stop_code == REVIEW_REJECT_FAILED_STOP_CODE:
        return (f"The automatic rejection failed: "
                f"{run.get('review_reject_detail') or 'unknown error'}")
    if stop_code == "group_lease_denied":
        # 0393 T0005 §2-6: the sentence a human reads on the card instead of a bare code.
        denied_op = run.get("lease_denied_operation") or "a group change"
        denied_code = run.get("lease_denied_code") or "GROUP_AI_RUN_OWNER_MISMATCH"
        return (
            f"The group gate refused this run's own worker ({denied_code}) on {denied_op}, "
            "so nothing it submitted was registered. A human must clear this: the run is "
            "not resumable."
        )
    return None


def stop_reason_text(stop_code: Optional[str], *, target_seq: Optional[int] = None,
                     detail: Optional[str] = None) -> Optional[str]:
    """The §4.3 sentence for a caller that has no run dict — i.e. the inbox self-chain.

    The inbox decides a stop on the request thread, and often there is no engine run alive to
    ask (a copy-mention chain has none at all). Routing it back through the same table keeps
    ONE sentence per stop code: the worker reading the response, the stored record and the
    miniplayer card all say the same thing.
    """
    return _stop_reason_text(stop_code, {
        "continuation_target_seq": target_seq,
        "inbox_stop_detail": detail,
    })


def mark_chain_stop(group_id: Optional[str], stop_code: str,
                    detail: Optional[str] = None) -> bool:
    """Let the inbox self-chain tag the live run with ITS stop reason (L0007 §4.1-5).

    Returns False when there is no engine run to tag — a copy-mention (semi-manned) chain,
    where the inbox is the only party that can tell a human anything at all.
    """
    if not group_id or not stop_code:
        return False
    run = _active_run_for_group(group_id)
    if run is None:
        return False
    run["inbox_stop_code"] = stop_code
    run["inbox_stop_detail"] = detail
    return True


# Bounded so a worker that retries a refused call in a loop cannot grow the record without
# limit; the first refusal is the informative one and it is never evicted.
LEASE_DENIAL_RECORD_LIMIT = 10


def mark_group_lease_denied(
    *,
    group_id: Optional[str],
    run_id: str,
    code: str,
    operation: str,
    status_code: int = 403,
    by_worker: bool = True,
) -> bool:
    """Tell the lease-owning run that the group gate turned its own worker away (§2-6).

    Called from `mutation_policy._record_denied_mutation`, off the event loop. Returns False
    when the run is not in this process's registry (a chain resumed after a restart, or a
    lease left behind by a run that already finished) — the caller treats that as a no-op.

    Two things are written, because the incident report asked for both:
      * `register_errors` — NR0003 §3 measured an empty error list on all three dead reviews.
        The refusal belongs in the same list a failed inbox POST lands in.
      * `lease_denied_*` — read by `_resolve_stop_code` / `_stop_reason_text` so the run
        ends with the name `group_lease_denied` and a sentence, instead of exit code 0.
    """
    run = get_run_record(run_id) or (_active_run_for_group(group_id) if group_id else None)
    if run is None:
        return False
    with _runs_lock:
        if not by_worker:
            # GROUP_AI_RUN_LOCKED — somebody ELSE was held off while this run worked. Worth
            # having on the run (T0005 §2-6 names both codes), but it is not this run's
            # error and must never end it.
            others = run.setdefault("lease_blocked_others", [])
            if len(others) < LEASE_DENIAL_RECORD_LIMIT:
                others.append({"status": int(status_code), "code": code, "operation": operation})
            return True
        run.setdefault("register_errors", [])
        if len(run["register_errors"]) < LEASE_DENIAL_RECORD_LIMIT:
            run["register_errors"].append({
                "status": int(status_code),
                "reason": f"{code}: {operation}",
                "turn": run.get("turn"),
            })
        run["lease_denied_code"] = code
        run["lease_denied_operation"] = operation
        run["lease_denied_count"] = int(run.get("lease_denied_count") or 0) + 1
    return True


def stamp_chain_stop(
    envelope: dict,
    stop_code: str,
    *,
    project_id: str,
    group_id: Optional[str],
    actor_user_id: Optional[str],
    anchor_doc_id: Optional[str],
    token_id: Optional[str] = None,
    item_seq: Optional[int] = None,
    detail: Optional[str] = None,
) -> dict:
    """One continuation stop, told to everyone who needs it (L0007 §2.11 / §2.12).

    Called by the self-chaining paths (the inbox `new` handler and the decide kickoff), which
    stop a chain while its worker is still holding the HTTP response open. Until now such a
    stop said WHY in that response body alone — and the only reader of that body is a worker
    about to exit, which is how NR0003 §4's chains died with the explanation still in them.

    Three things happen here:

      1. the envelope gets a machine-readable code, the §4.3 sentence and the resumable flag,
      2. the live engine run is tagged so its record and its miniplayer card end up saying the
         same thing the worker was just told (a no-op on a copy-mention chain, which has no
         engine run at all), and
      3. the four stops a human has to clean up leave a notification behind.

    2 and 3 are best-effort: the submitted document is already saved, and nothing here may turn
    that into a failure. The engine and the inbox hold disjoint notify sets, so a stop is
    announced exactly once regardless of which side saw it.
    """
    reason = stop_reason_text(
        stop_code,
        target_seq=envelope.get("continuation_target_seq"),
        detail=detail,
    )
    envelope["continuation_stop_code"] = stop_code
    envelope["continuation_stop_reason"] = reason
    envelope["continuation_resumable"] = is_resumable(stop_code)

    try:
        mark_chain_stop(group_id, stop_code, detail)
    except Exception:
        logger.warning("chain stop tagging failed for %s (ignored)", group_id, exc_info=True)

    if stop_code not in INBOX_NOTIFY_STOP_CODES:
        return envelope
    try:
        from modules.flow_gate.workflow import event_logger

        # The notification lands on the document that was just submitted: for all four codes
        # that IS the thing needing triage — the stray document, the one waiting on approval,
        # or the last one that did land before the advance failed.
        anchor = (db_docs.get_by_id(anchor_doc_id) or {}) if anchor_doc_id else {}
        # Carry the run this stop belongs to, exactly as the engine's own signal does — NR0003
        # §4 found 1,346 continuous tokens with no bridge back to their execution, and a
        # notification that cannot name its run repeats the same dead end.
        stopped = _active_run_for_group(group_id) if group_id else None
        event_logger.log_continuous_work_failed(
            project_id=project_id,
            actor_user_id=actor_user_id,
            document_id=anchor.get("id"),
            doc_id=anchor_doc_id,
            group_id=anchor.get("group_id") or group_id,
            run_id=(stopped or {}).get("run_id"),
            error=reason,
            target_seq=envelope.get("continuation_target_seq"),
            extra={"stop_code": stop_code, "token_id": token_id, "item_seq": item_seq},
        )
    except Exception:
        # Same best-effort contract as continuous_work_ended (L0007 §5).
        logger.warning("chain stop signal failed for %s (ignored)", group_id, exc_info=True)
    return envelope


# ── Stop row / record / human signal (0359 L0007 §2.8, §2.10.1, §2.11) ───────

def _apply_stop_row(run: dict, respawn_pending: bool) -> None:
    """Maintain the miniplayer's [resume] card (L0007 §2.8 / §4.5).

    Before 0359 this deleted the row for EVERY end_reason other than "user_paused", which is
    exactly why NR0003 §6's 24 dead chains could not be resumed: the system destroyed the only
    coordinate the resume path takes. A resumable stop now parks a *system* row instead, and
    resume_chain consumes it through the identical path a user pause uses.
    """
    if run.get("mode") != "continuous":
        return
    if respawn_pending:
        return                                       # the next hop is already queued
    if run.get("end_reason") == "user_paused":
        # The user's own row stays (0252 L0009 §3) — but it is REFRESHED, not left as it
        # was: pause_run snapshots at request time, before the in-flight hop reaches its
        # boundary, so the document that completed while the pause was pending would
        # otherwise be missing from what resume/bootstrap reads back (0357 T0004).
        try:
            from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

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
                    stop_kind=row.get("stop_kind") or "user",
                    stop_code=row.get("stop_code"),
                    stop_run_id=row.get("stop_run_id"),
                    stop_last_message_excerpt=row.get("stop_last_message_excerpt"),
                    # This upsert overwrites every column, and it ALWAYS runs right after
                    # a pause. Omitting the selections here would erase what pause_run
                    # just stored (0365 DB0004 §5-3 invariant I3).
                    continuation_base_provider_id=run.get("continuation_base_provider_id"),
                    continuation_provider_pinned=run.get("continuation_provider_pinned"),
                    continuation_provider_overrides=run.get("continuation_provider_overrides"),
                    continuation_default_note=run.get("continuation_default_note"),
                    continuation_note_overrides=run.get("continuation_note_overrides"),
                    # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq auto-approve
                    # selection are exactly as perishable as the provider/note selections above
                    # — this same "overwrites every column" upsert is what silently dropped the
                    # mode on every user-paused chain before this fix.
                    continuation_instruction_mode=run.get("continuation_instruction_mode"),
                    continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
                    continuation_step_timeout_sec=run.get("continuation_step_timeout_sec"),
                    continuation_restart_max_attempts=run.get("continuation_restart_max_attempts"),
                    # 0414: this refresh runs immediately after pause_run and overwrites every
                    # column — omitting the two maps here would erase what pause_run just wrote.
                    continuation_review_count_overrides=run.get(
                        "continuation_review_count_overrides"),
                    continuation_reviewer_overrides=run.get("continuation_reviewer_overrides"),
                )
        except Exception:
            logger.warning(
                "ai-invoke paused-row finalization failed for %s", run["run_id"], exc_info=True
            )
        return
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        if not run.get("resumable"):
            # Cancelled / human-triage stops: no card, exactly as before — a ghost "paused"
            # card for a chain that is really over is worse than none.
            db_paused.delete_by_group(run["group_id"])
            return
        existing = db_paused.get_by_group(run["group_id"])
        if existing is not None and (existing.get("stop_kind") or "user") != "system":
            # A row a HUMAN put there outranks one the system would write (§5 stop-row race).
            return
        db_paused.upsert(
            group_id=run["group_id"],
            doc_ref=run["doc_ref"],
            paused_by=run.get("issued_to"),
            paused_at=run.get("finished_at") or now_iso(),
            continuation_target_seq=run.get("continuation_target_seq"),
            docs_target=run.get("docs_target"),
            docs_reached=int(run.get("docs_reached") or 0),
            # A SYSTEM row deliberately carries no chain counters (0357 T0004): the run
            # record this stop points at (stop_run_id) has none either, and resume_chain
            # re-derives the target from the sequence when the row leaves them NULL. The
            # user-pause refresh above does carry them — that row is a snapshot taken
            # mid-chain and would otherwise go stale.
            stop_kind="system",
            stop_code=run.get("stop_code"),
            stop_run_id=run.get("run_id"),
            stop_last_message_excerpt=excerpt(run.get("last_message")),
            # 0365 DB0004's provider/note preference columns are deliberately NOT written
            # here unless 0435's explicit provider pin is active. This branch only creates
            # a row where none existed (a user row outranks it and returns above), so an
            # unpinned system stop must resume through normal doc-type/default resolution.
            #
            # The explicit pin is the narrow exception: it is execution policy, and a manual
            # resume after no-output exhaustion must keep that provider instead of reviving
            # the stored/default provider.
            continuation_base_provider_id=(
                run.get("continuation_base_provider_id")
                if run.get("continuation_provider_pinned")
                else None
            ),
            continuation_provider_pinned=run.get("continuation_provider_pinned"),
            # 0352 T0004 §3.6 deliberately widens this: unlike the provider/note *preference*
            # columns above, instruction_mode is not a "nice-to-have default" — it is the
            # policy the chain is actually running under. A system stop (e.g.
            # no_output_exhausted) that drops it would resume an ai_direct chain back to
            # auto_approved, silently auto-approving N/T the user chose to author themselves
            # — exactly the mode-loss bug this TR fixes. So mode + its per-item_seq selection
            # ARE written on every system row, even though the other four selections are not.
            # flowgate.default.0400 M0005: the per-hop budget pick is policy in the same sense
            # — dropping it here would resume a chain the user picked 240 minutes for back at
            # HOP_TIMEOUT_SEC (60), silently reopening the exact "hop budget too short" report
            # this feature exists to fix. Written alongside instruction_mode for that reason.
            continuation_instruction_mode=run.get("continuation_instruction_mode"),
            continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
            continuation_step_timeout_sec=run.get("continuation_step_timeout_sec"),
            continuation_restart_max_attempts=run.get("continuation_restart_max_attempts"),
            # 0414 DB0009 W4 / L0008 §4.3: policy, not preference — same treatment as
            # instruction_mode and the budget above, and for the same reason. A system stop
            # that dropped the selection would resume the chain with the gate switched off,
            # which is the one failure invariant R1 exists to prevent.
            continuation_review_count_overrides=run.get("continuation_review_count_overrides"),
            continuation_reviewer_overrides=run.get("continuation_reviewer_overrides"),
        )
    except Exception:
        logger.warning("ai-invoke stop-row update failed for %s", run["run_id"], exc_info=True)


def _persist_run_record(run: dict) -> None:
    """Write the one durable row for this hop (L0007 §2.10.1).

    Exactly once, at finalize — while a run is alive, memory is the truth. NR0003 §4: before
    this, a run that ended while the browser happened to be looking at another project left
    nothing behind at all; the only copy of the worker's explanation was a scratch file the
    server deletes after seven days.
    """
    try:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        stamp = now_iso()
        db_runs.upsert({
            "run_id": run["run_id"],
            "group_id": run["group_id"],
            "project_id": run["project_id"],
            "doc_ref": run["doc_ref"],
            "mode": run["mode"],
            "outcome": run.get("outcome"),
            "docs_reached": int(run.get("docs_reached") or 0),
            "docs_target": run.get("docs_target"),
            "reached_doc_ids": list(run.get("reached_doc_ids") or []),
            "end_reason": run.get("end_reason"),
            "stop_code": run.get("stop_code"),
            "stop_reason": run.get("stop_reason"),
            "resumable": bool(run.get("resumable")),
            "exit_code": run.get("exit_code"),
            "last_message": run.get("last_message"),
            "last_message_excerpt": excerpt(run.get("last_message")),
            "provider_id": run.get("provider_id"),
            "provider_name": (run.get("provider") or {}).get("name"),
            "attempt_no": int(run.get("attempt_no") or 0),
            "attempts_used": int(run.get("attempts_used") or 0),
            "attempts_max": run.get("attempts_max"),
            "fallback_history": list(run.get("fallback_history") or []),
            "register_errors": list(run.get("register_errors") or []),
            "tool_call_misses": int(run.get("tool_call_misses") or 0),
            "turn_limit_exhausted": bool(run.get("turn_limit_exhausted")),
            "oracle_mismatch": bool(run.get("oracle_mismatch")),
            "source_dirty": run.get("source_dirty"),
            "scratch_retained": run.get("scratch_retained"),
            "hop_item_seq": run.get("hop_item_seq"),
            "token_id": run.get("token_id"),
            "issued_to": run.get("issued_to"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "duration_ms": run.get("duration_ms"),
            "timeout_sec": run.get("timeout_sec"),
            "deadline_at": run.get("deadline_at"),
            # ── 0406 T0022 work items 3 and 5 ───────────────────────────────
            # NR0021 §8: the session-scoped handoff note and the final prompt were kept
            # nowhere, so "did the user's text really go in?" could not be decided later.
            # This fills that gap, in one row addressable by run_id.
            "worker_document_type": run.get("worker_document_type"),
            "continuation_instruction_mode_requested": run.get(
                "continuation_instruction_mode_requested"
            ),
            "continuation_instruction_mode_normalized": run.get(
                "continuation_instruction_mode_normalized"
            ),
            "continuation_instruction_mode_fallback_applied": bool(
                run.get("continuation_instruction_mode_fallback_applied")
            ),
            "auto_handled_item_seqs": list(run.get("auto_handled_item_seqs") or []),
            "prompt_message_source": run.get("prompt_message_source"),
            "prompt_common_default_applied": bool(run.get("prompt_common_default_applied")),
            "prompt_user_message_length": int(run.get("prompt_user_message_length") or 0),
            "prompt_user_message_sha256": run.get("prompt_user_message_sha256"),
            "prompt_final_length": int(run.get("prompt_final_length") or 0),
            "prompt_final_sha256": run.get("prompt_final_sha256"),
            # ── 0446 T0016 §3-2 (migration 086) ─────────────────────────────
            # The four exit diagnostics that were memory-only until now. Every one is read
            # with .get(): a spawn failure never opened a watchdog, an API-mode run has no
            # tails, and neither may turn a finished hop into an unsaved one.
            "timeout_kind": run.get("timeout_kind"),
            "timeout_diagnosis": run.get("timeout_diagnosis"),
            "stdout_tail": run.get("stdout_tail"),
            "stderr_tail": run.get("stderr_tail"),
            "source_dirty_files": list(run.get("source_dirty_files") or []),
            "created_at": stamp,
            "updated_at": stamp,
        })
        db_runs.maybe_purge()
    except Exception:
        # L0007 §5: a storage failure must never turn a finished hop into a crashed one.
        logger.warning("ai-invoke run record persist failed for %s", run["run_id"], exc_info=True)


def _notify_chain_failure_if_needed(run: dict) -> None:
    """Put the stop somewhere a human will still find it tomorrow (L0007 §2.11).

    NR0003 §4: the worker's explanation existed and was even delivered — over SSE, to a browser
    that was looking at a different project in that second, with no way to look back. This
    writes the same fact to the notification feed, which survives not watching.
    """
    # 0446 T0008 §3-8: scope-oracle rework runs speak here too. `question_pending` is still
    # absent from ENGINE_NOTIFY_STOP_CODES, so a hop waiting on a human answer stays silent.
    if run.get("mode") != "continuous" and not _scope_oracle_retry_run(run):
        return
    if run.get("stop_code") not in ENGINE_NOTIFY_STOP_CODES:
        return
    if run.get("failure_signal_sent"):
        return
    run["failure_signal_sent"] = True
    try:
        from modules.flow_gate.workflow import event_logger

        document_pk, document_doc_id = _anchor_document(run)
        event_logger.log_continuous_work_failed(
            project_id=run["project_id"],
            actor_user_id=run.get("issued_to"),
            document_id=document_pk,
            doc_id=document_doc_id,
            group_id=run["group_id"],
            run_id=run["run_id"],
            target_seq=run.get("continuation_target_seq"),
            error=_failure_error_text(run),
            extra={
                "stop_code": run.get("stop_code"),
                "token_id": run.get("token_id"),
                "item_seq": run.get("hop_item_seq"),
                "provider_id": run.get("provider_id"),
                "attempts_used": int(run.get("attempts_used") or 0),
                # 0446 T0008 §3-8: the event TYPE stays `continuous_work_failed` — it is the
                # only one `dashboard_service._NOTIFICATION_EVENT_TYPES` carries to the 🔔
                # feed, and `test_run_service` already fires it from a non-continuous context.
                # These two fields are how a reader tells a dead continuous chain apart from a
                # dead single rework run.
                "mode": run.get("mode"),
                "action_scope": run.get("action_scope"),
            },
        )
    except Exception:
        # Same best-effort contract as continuous_work_ended (L0007 §5).
        logger.warning("ai-invoke failure signal failed for %s", run["run_id"], exc_info=True)


def _failure_error_text(run: dict) -> str:
    return (f"{int(run.get('attempts_used') or 0)} attempts produced no document; "
            f"last worker message: {excerpt(run.get('last_message')) or '(none)'}")


def _anchor_document(run: dict) -> tuple[Optional[int], Optional[str]]:
    """Where the notification lands when a human clicks it (L0007 §2.11).

    A dead hop has no document of its own to point at, so it points at the last place the chain
    actually reached — "this much got done" — and falls back to the chain's spine document. An
    anchorless notification still beats a notification nobody gets (L0007 §5).
    """
    candidates: list[str] = []
    reached = run.get("reached_doc_ids") or []
    if reached:
        candidates.append(reached[-1])
    hop_seq = run.get("hop_item_seq")
    try:
        seq = db_wfseq.get_sequence_for_member_doc(run["doc_ref"])
        items = db_wfseq.get_sequence_items(seq["id"]) if seq is not None else []
        previous = [
            item for item in items or []
            if item.get("result_doc_id")
            and item.get("result_doc_review_status") == "approved"
            and (hop_seq is None or (item.get("item_seq") or 0) < int(hop_seq))
        ]
        if previous:
            candidates.append(
                max(previous, key=lambda item: item.get("item_seq") or 0)["result_doc_id"]
            )
    except Exception:
        logger.warning("ai-invoke anchor lookup failed for %s", run["run_id"], exc_info=True)
    candidates.append(run["doc_ref"])
    for doc_id in candidates:
        try:
            doc = db_docs.get_by_id(doc_id) or {}
        except Exception:
            continue
        if doc.get("id") is not None:
            return int(doc["id"]), doc_id
    return None, None


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
        "provider_name": (run.get("provider") or {}).get("name"),
        "started_at": run.get("started_at"),
        "attempt_no": run["attempt_no"],
        "fallback_history": run["fallback_history"],
        "register_errors": run.get("register_errors", []),
        "tool_call_misses": run.get("tool_call_misses", 0),
        "turn_limit_exhausted": bool(run.get("turn_limit_exhausted")),
        "oracle_mismatch": bool(run.get("oracle_mismatch")),
        "source_dirty": run["source_dirty"],
        # 0446 T0016 §3-4: the live half of the restart pair. `source_dirty_files` keeps its
        # existing conditional place at the bottom of this function.
        "timeout_kind": run.get("timeout_kind"),
        "timeout_diagnosis": run.get("timeout_diagnosis"),
        "stdout_tail": run.get("stdout_tail"),
        "stderr_tail": run.get("stderr_tail"),
        "duration_ms": run["duration_ms"],
        # 0359 P0006 [stop confirmed]: why it stopped, whether it can be picked up again, how many
        # attempts it cost and against what budget. All additive — no existing field moved.
        "stop_code": run.get("stop_code"),
        "stop_reason": run.get("stop_reason"),
        "resumable": bool(run.get("resumable")),
        # 0393 T0005 §2-6: refusals this run's lease handed to OTHER actors. Live signal
        # only (there is no column for it), but it is what tells a reader that the lease was
        # actually in force while this run was going.
        "lease_blocked_others": list(run.get("lease_blocked_others") or []),
        "hop_item_seq": run.get("hop_item_seq"),
        # 0414 L0008 §5: which KIND of hop this was. A review or rework hop registers no
        # document, so without this a card can only report "0 documents" for a hop that did
        # exactly what it was asked to do.
        "hop_kind": run.get("hop_kind"),
        "hop_review_count": run.get("hop_review_count"),
        "hop_reviewer_provider_id": run.get("hop_reviewer_provider_id"),
        "hop_reviewer_provider_name": run.get("hop_reviewer_provider_name"),
        "token_id": run.get("token_id"),
        "attempts_used": int(run.get("attempts_used") or 0),
        "attempts_max": run.get("attempts_max"),
        "timeout_sec": run.get("timeout_sec"),
        "deadline_at": run.get("deadline_at"),
        # 0406 T0022 items 3 and 5: values that let a finished hop's card tell "the N/T
        # vanished" apart from "the TR worker ran fine". Same names on a live run.
        "worker_document_type": run.get("worker_document_type"),
        "continuation_instruction_mode": run.get("continuation_instruction_mode"),
        "continuation_instruction_mode_requested": run.get(
            "continuation_instruction_mode_requested"
        ),
        "continuation_instruction_mode_normalized": run.get(
            "continuation_instruction_mode_normalized"
        ),
        "continuation_instruction_mode_fallback_applied": bool(
            run.get("continuation_instruction_mode_fallback_applied")
        ),
        "auto_handled_item_seqs": list(run.get("auto_handled_item_seqs") or []),
        "prompt_message_source": run.get("prompt_message_source"),
        "prompt_common_default_applied": bool(run.get("prompt_common_default_applied")),
        "prompt_user_message_length": run.get("prompt_user_message_length"),
        "prompt_user_message_sha256": run.get("prompt_user_message_sha256"),
        "prompt_final_length": run.get("prompt_final_length"),
        "prompt_final_sha256": run.get("prompt_final_sha256"),
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
    # silently revert the card from "stop scheduled" back to plain running.
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
        # 0414 P0007 상태 응답 — the same review facts a live card needs mid-hop.
        "hop_item_seq": run.get("hop_item_seq"),
        "hop_kind": run.get("hop_kind"),
        "hop_review_count": run.get("hop_review_count"),
        "hop_reviewer_provider_id": run.get("hop_reviewer_provider_id"),
        "hop_reviewer_provider_name": run.get("hop_reviewer_provider_name"),
        "continuation_review_count_overrides": run.get("continuation_review_count_overrides"),
        "continuation_reviewer_overrides": run.get("continuation_reviewer_overrides"),
        # 0359 P0006 [hop budget]: a live card can now say how much of the budget is gone
        # without anyone re-deriving the formula from the server log.
        "timeout_sec": run.get("timeout_sec"),
        "deadline_at": run.get("deadline_at"),
        "attempts_used": int(run.get("attempts_used") or 0),
        "attempts_max": run.get("attempts_max"),
        # Server truth: an explicit empty array clears stale client state on the next poll.
        "pending_q_doc_ids": _open_q_doc_ids(run["group_id"]),
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
    try:
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
            # A user pause belongs to one concrete continuous run. Persisting that identity
            # prevents a stale group-level row from tagging an unrelated single run.
            stop_kind="user",
            stop_run_id=run_id,
            # 0365 B0001: the provider / handoff-note selections live only on the run object
            # (session-scoped by design). The pause is where that memory ends, so they go
            # into the row here — otherwise resume_chain has nothing to restore and falls
            # back to the project default chain's first entry (NR0003 §2-2).
            continuation_base_provider_id=run.get("continuation_base_provider_id"),
            continuation_provider_pinned=run.get("continuation_provider_pinned"),
            continuation_provider_overrides=run.get("continuation_provider_overrides"),
            continuation_default_note=run.get("continuation_default_note"),
            continuation_note_overrides=run.get("continuation_note_overrides"),
            # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq auto-approve
            # selection must survive the pause the same way the provider/note selections
            # do — this is the root fix for the pause->resume mode-loss bug (resume_chain
            # used to hard-code "auto_approved" because this row never carried anything else).
            continuation_instruction_mode=run.get("continuation_instruction_mode"),
            continuation_auto_approve_item_seqs=run.get("continuation_auto_approve_item_seqs"),
            # flowgate.default.0400 M0005: same reasoning as the provider/note selections above
            # — this row is where the run object's memory ends, so the per-hop budget pick must
            # be written here or resume_chain has nothing to restore it from.
            continuation_step_timeout_sec=run.get("continuation_step_timeout_sec"),
            # 0443 T0002 (R0001): the "재시작 횟수" pick is exactly as perishable — write it
            # here too or a resumed chain silently falls back to the default retry count.
            continuation_restart_max_attempts=run.get("continuation_restart_max_attempts"),
            # 0414 P0007 [정상] 일시정지→재개 왕복: the pause is where the run object's memory
            # of the [검수] selection ends, so this row is the only thing that can hand it
            # back to resume_chain.
            continuation_review_count_overrides=run.get("continuation_review_count_overrides"),
            continuation_reviewer_overrides=run.get("continuation_reviewer_overrides"),
        )
    except Exception:
        logger.warning("ai-invoke paused-row upsert failed for %s", run_id, exc_info=True)
    return {
        "ok": True,
        "run_id": run_id,
        "group_id": run["group_id"],
        "status": "pause_requested",
        "effective_at": "step_boundary",
    }


def mark_user_paused(group_id: str, run_id: Optional[str]) -> bool:
    """Tag only the continuous run that owns the persisted user-pause row.

    Group identity alone is not enough: a stale system stop can coexist with a later
    single review in the same group. Returning the decision lets boundary callers avoid
    withholding a token unless the row and live run identities agree.
    """
    if not run_id:
        return False
    run = _active_run_for_group(group_id)
    if (
        run is None
        or run.get("run_id") != run_id
        or run.get("mode") != "continuous"
        or run.get("status") == "finished"
        or not run.get("pause_requested")
    ):
        return False
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        row = db_paused.get_by_group(group_id)
    except Exception:
        logger.warning("user-pause identity lookup failed for %s", group_id, exc_info=True)
        return False
    if (
        row is None
        or (row.get("stop_kind") or "user") != "user"
        or row.get("stop_run_id") != run_id
    ):
        return False
    run["user_paused"] = True
    return True


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
    worker; consumed by _maybe_auto_resume_hop when the current hop's worker settles.

    0406 T0022 item 4: the same intent is also written to the DB. The in-memory dict does
    not survive a restart, and the abnormal-exit branch pops and discards it — either way
    "what was going to happen next" quietly disappeared. Rather than adding a new store
    this reuses ai_invoke_paused_chains: it already has every column needed to revive one
    chain, and resume_chain restarts the chain from that single row.
    """
    if not group_id:
        return
    with _auto_resume_lock:
        _auto_resume[group_id] = dict(payload)
    _write_handoff_row(group_id, payload, _active_run_for_group(group_id))


def _carry(pending: dict, pending_key: str, run: dict, run_key: str):
    """The queued value when the queue has one, otherwise the run's (0414 L0008 §2.9).

    `is not None` rather than truthiness on purpose: chain_docs_reached=0 and
    provider_pinned=False are real values, not "unset".
    """
    value = pending.get(pending_key)
    return value if value is not None else run.get(run_key)


def _handoff_bundle(pending: dict, run: Optional[dict]) -> dict:
    """The queued intent plus this hop's session picks = one set that revives the next hop.

    The payload inbox enqueues has no provider pin, handoff note, or hop budget: those
    ride the run rather than the token, hop to hop. The durable row is only meaningful
    when both halves are joined — storing only half drops a resume back to the project
    default provider and an empty note (the defect 0365 hit).
    """
    run = run or {}
    return {
        "doc_ref": pending.get("doc_ref") or run.get("doc_ref"),
        "target_seq": pending.get("target_seq"),
        "review_mode": bool(pending.get("review_mode")),
        "instruction_mode": (
            pending.get("instruction_mode") or run.get("continuation_instruction_mode")
        ),
        "auto_approve_item_seqs": (
            pending.get("auto_approve_item_seqs")
            if pending.get("auto_approve_item_seqs") is not None
            else run.get("continuation_auto_approve_item_seqs")
        ),
        "locale": pending.get("locale") or run.get("continuation_locale") or "ko",
        "issued_to": pending.get("issued_to") or run.get("issued_to"),
        "api_base_url": pending.get("api_base_url") or run.get("api_base_url"),
        # 0414: the queued value wins when it has one. The inbox's payload never carries
        # these (they ride the run), so a plain inbox handoff still reads them off the run
        # exactly as before — but a gate bundle DOES carry them, and it must not be
        # overwritten by a review/rework hop's run, which is mode="single" and holds None
        # for every continuous-only field. That overwrite is precisely how a chain reviews
        # its first step and then silently stops reviewing.
        "provider_overrides": _carry(pending, "provider_overrides",
                                     run, "continuation_provider_overrides"),
        "base_provider_id": _carry(pending, "base_provider_id",
                                   run, "continuation_base_provider_id"),
        "provider_pinned": _carry(pending, "provider_pinned",
                                  run, "continuation_provider_pinned"),
        "note_overrides": _carry(pending, "note_overrides",
                                 run, "continuation_note_overrides"),
        "default_note": _carry(pending, "default_note", run, "continuation_default_note"),
        "step_timeout_sec": _carry(pending, "step_timeout_sec",
                                   run, "continuation_step_timeout_sec"),
        # flowgate.default.0443 T0002 (R0001): the "재시작 횟수" pick carries forward
        # the same way the budget pick above does — dropped here, a re-spawned hop
        # silently falls back to RESTART_MAX_ATTEMPTS_DEFAULT.
        "restart_max_attempts": _carry(pending, "restart_max_attempts",
                                       run, "continuation_restart_max_attempts"),
        # 0414 P0007 전달 지점 3·4: the two [검수] maps join the durable bundle. DB0009 §5-3
        # calls omitting them the worst of the I3 violations — a lost provider means a
        # pricier resume, a lost review selection means "approved with nobody reviewing it".
        "review_count_overrides": _carry(pending, "review_count_overrides",
                                         run, "continuation_review_count_overrides"),
        "reviewer_overrides": _carry(pending, "reviewer_overrides",
                                     run, "continuation_reviewer_overrides"),
        "chain_id": _carry(pending, "chain_id", run, "chain_id"),
        "chain_docs_target": _carry(pending, "chain_docs_target", run, "chain_docs_target"),
        "chain_docs_reached": _carry(pending, "chain_docs_reached", run, "chain_docs_reached"),
        "stop_run_id": run.get("run_id"),
    }


def _write_handoff_row(
    group_id: Optional[str],
    pending: dict,
    run: Optional[dict],
    *,
    stop_code: str = HOP_HANDOFF_STOP_CODE,
) -> None:
    """Record the handoff intent as a system stop row (0406 T0022 item 4).

    Upholds invariant I3: this upsert overwrites every column, so omitting even one drops
    a resume to the default provider / empty note. A stop row a person created
    (stop_kind='user') is left alone: a user pause outranks a system row.
    Best effort — a failed write must never kill a chain that is running.
    """
    if not group_id:
        return
    bundle = _handoff_bundle(pending, run)
    if not bundle.get("doc_ref") or not bundle.get("issued_to"):
        return
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        existing = db_paused.get_by_group(group_id)
        if existing is not None and (existing.get("stop_kind") or "user") != "system":
            return
        stamp = now_iso()
        db_paused.upsert(
            group_id=group_id,
            doc_ref=bundle["doc_ref"],
            paused_by=bundle["issued_to"],
            paused_at=stamp,
            continuation_target_seq=bundle.get("target_seq"),
            docs_target=(run or {}).get("docs_target"),
            docs_reached=int((run or {}).get("docs_reached") or 0),
            chain_id=bundle.get("chain_id"),
            chain_docs_target=bundle.get("chain_docs_target"),
            chain_docs_reached=int(bundle.get("chain_docs_reached") or 0),
            stop_kind="system",
            stop_code=stop_code,
            stop_run_id=bundle.get("stop_run_id"),
            stop_last_message_excerpt=excerpt((run or {}).get("last_message")),
            continuation_base_provider_id=bundle.get("base_provider_id"),
            continuation_provider_pinned=bundle.get("provider_pinned"),
            continuation_provider_overrides=bundle.get("provider_overrides"),
            continuation_default_note=bundle.get("default_note"),
            continuation_note_overrides=bundle.get("note_overrides"),
            continuation_instruction_mode=bundle.get("instruction_mode"),
            continuation_auto_approve_item_seqs=bundle.get("auto_approve_item_seqs"),
            continuation_step_timeout_sec=bundle.get("step_timeout_sec"),
            continuation_restart_max_attempts=bundle.get("restart_max_attempts"),
            # 0414 DB0009 W2/W4: written on EVERY handoff settlement and every system park.
            # These two run on every hop, so a miss here loses the selection from the second
            # hop onward — the quietest possible way to break invariant R1.
            continuation_review_count_overrides=bundle.get("review_count_overrides"),
            continuation_reviewer_overrides=bundle.get("reviewer_overrides"),
        )
    except Exception:  # noqa: BLE001 — the record is an aid, not a precondition
        logger.warning("ai-invoke handoff row write failed for %s", group_id, exc_info=True)


def _clear_handoff_row(group_id: Optional[str], stop_run_id: Optional[str]) -> None:
    """Call only after the follow-up hop has actually started (0406 T0022 item 4).

    delete_system_stop removes only the **system** row for that stop_run_id — a pause a
    person pressed meanwhile, or a newer stop row, survives.
    """
    if not group_id or not stop_run_id:
        return
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        db_paused.delete_system_stop(group_id, stop_run_id)
    except Exception:  # noqa: BLE001
        logger.warning("ai-invoke handoff row cleanup failed for %s", group_id, exc_info=True)


def _park_handoff(run: dict, pending: dict, stop_code: str) -> None:
    """Terminus of every branch that decides not to spawn the next hop (0406 T0022 item 4).

    Two things must happen.
      1. Leave a durable row, distinguishing the reason via stop_code, so the user can
         resume the chain from the same place. No branch disappears silently.
      2. Release the lease the handoff switched to releasing. _finalize_run only calls
         begin_handoff and skips release when it sees a queue, so without this the
         group's next run is blocked until the lease expires. release only deletes rows
         whose run_id matches, so calling it twice is safe (restart reclaim included).
    """
    group_id = run.get("group_id")
    _write_handoff_row(group_id, pending, run, stop_code=stop_code)
    if not group_id:
        return
    try:
        db_group_ai_leases.release(group_id, run["run_id"])
    except Exception:  # noqa: BLE001
        logger.warning("ai-invoke handoff lease release failed for %s", group_id, exc_info=True)


def startup_recover_handoffs() -> int:
    """Turn handoffs whose in-memory queue was lost to a restart into an explicit
    awaiting-resume state (0406 T0022 item 4).

    At startup this process's ``_auto_resume`` is empty, so every ``hop_handoff`` row
    still in the table means "the process died just as it was about to spawn the next
    hop". The row is NOT deleted — that would be losing the intent. Only stop_code
    changes, taking it out of the grace check and making it a card the user can resume.
    """
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        rows = [
            row for row in db_paused.list_all_system_stops()
            if (row.get("stop_code") or "") == HOP_HANDOFF_STOP_CODE
        ]
        for row in rows:
            db_paused.mark_stop_code(
                row["group_id"], HOP_HANDOFF_INTERRUPTED_STOP_CODE,
                stop_run_id=row.get("stop_run_id"),
            )
        if rows:
            logger.warning(
                "[ai_invoke] startup recovered %d interrupted hop handoff(s)", len(rows)
            )
        return len(rows)
    except Exception:  # noqa: BLE001
        logger.warning("ai-invoke handoff startup recovery failed", exc_info=True)
        return 0


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
    # 0406 T0022 item 4: the two branches below used to pop the queue and throw it away.
    # The queue is still dropped — a hop that ended abnormally must not auto-continue —
    # but the **intent** is kept as a durable row, and the releasing lease is released.
    cancel_event = run.get("cancel_event")
    cancelled = cancel_event is not None and cancel_event.is_set()
    if run.get("end_reason") != "exited" or cancelled:
        parked_code = run.get("stop_code")
        if not parked_code or parked_code == HOP_HANDOFF_STOP_CODE:
            parked_code = "cancelled" if cancelled else HOP_HANDOFF_FAILED_STOP_CODE
        _park_handoff(run, pending, parked_code)
        return
    # Carry the session override map AND the header default pin forward so the re-spawned hop
    # applies them too (neither is persisted on a token — both ride the run, hop to hop). The
    # base pin is what an override-less step resolves to (0317 T0013 결함 ③).
    # 0414: _carry, not a bare overwrite. A review/rework hop is mode="single", so its run
    # holds None for every continuous-only field — clobbering the queued bundle with those
    # would drop the provider pin, the notes, the budget AND the review selection the moment
    # the chain ran its first review hop.
    pending = {
        **pending,
        "provider_overrides": _carry(pending, "provider_overrides",
                                     run, "continuation_provider_overrides"),
        "base_provider_id": _carry(pending, "base_provider_id",
                                   run, "continuation_base_provider_id"),
        "provider_pinned": _carry(pending, "provider_pinned",
                                  run, "continuation_provider_pinned"),
        # 0346 T0005: carry the [전달멘트] note bundle forward the same way — the first-hop-only
        # gap is the exact regression shape this fix is guarding against (D0004 구현 시 반드시
        # 지켜야 할 제약 4).
        "note_overrides": _carry(pending, "note_overrides",
                                 run, "continuation_note_overrides"),
        "default_note": _carry(pending, "default_note", run, "continuation_default_note"),
        # flowgate.default.0400 M0005: the per-hop budget pick carries forward the same way —
        # dropping it here would silently reset a re-spawned hop back to HOP_TIMEOUT_SEC.
        "step_timeout_sec": _carry(pending, "step_timeout_sec",
                                   run, "continuation_step_timeout_sec"),
        # flowgate.default.0443 T0002 (R0001): the "재시작 횟수" pick carries forward
        # the same way the budget pick above does — dropped here, a re-spawned hop
        # silently falls back to RESTART_MAX_ATTEMPTS_DEFAULT.
        "restart_max_attempts": _carry(pending, "restart_max_attempts",
                                       run, "continuation_restart_max_attempts"),
        # 0414 P0007 전달 지점 5 / L0008 §2.9 지점 10.
        "review_count_overrides": _carry(pending, "review_count_overrides",
                                         run, "continuation_review_count_overrides"),
        "reviewer_overrides": _carry(pending, "reviewer_overrides",
                                     run, "continuation_reviewer_overrides"),
        # 0357 T0004: the chain identity and its lifetime counters, so the next hop keeps
        # counting the CHAIN's progress instead of restarting at 0/1 in the miniplayer.
        "chain_id": _carry(pending, "chain_id", run, "chain_id"),
        "chain_docs_target": _carry(pending, "chain_docs_target", run, "chain_docs_target"),
        "chain_docs_reached": _carry(pending, "chain_docs_reached", run, "chain_docs_reached"),
        # The stop code this hop ended on, so the gate can tell "waiting on a human answer"
        # apart from "the hop left nothing" without re-querying (L0008 §2.3).
        "last_stop_code": run.get("stop_code"),
        # 0352 T0004 §3.5: the ai_direct per-item_seq auto-approve selection rides forward
        # the same way instruction_mode does, hop to hop — dropping it here would silently
        # revert a re-spawned hop to "select nothing", handing the AI an N/T the user picked
        # for server auto-handling.
        "auto_approve_item_seqs": run.get("continuation_auto_approve_item_seqs"),
    }
    # Rewrite the durable row as a complete set as of now. At request_auto_resume time only
    # inbox's half was there (document, target, mode); the provider pin, handoff note and
    # hop budget ride the run, so only here is the set complete — invariant I3.
    _write_handoff_row(group_id, pending, run)
    try:
        # 0414 L0008 §2.1 진입점 2: the gate decides what the next hop IS — review, rework,
        # approve-and-continue, or stop. With no review selection it resolves to "work" and
        # calls the same _spawn_auto_resume this line used to call directly.
        started = run_review_gate(group_id, pending, run)
    except HTTPException as exc:
        logger.warning("ai-invoke auto-resume rejected for %s: %s",
                       group_id, getattr(exc, "detail", exc))
        _park_handoff(run, pending, HOP_HANDOFF_FAILED_STOP_CODE)
        return
    except Exception:
        logger.exception("ai-invoke auto-resume failed for %s", group_id)
        _park_handoff(run, pending, HOP_HANDOFF_FAILED_STOP_CODE)
        return
    if not started:
        return          # the gate parked the chain; its durable row IS the resume card
    # The follow-up hop actually started. **Only now** is the intent cleared.
    _clear_handoff_row(group_id, run.get("run_id"))


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
    provider_pinned = bool(pending.get("provider_pinned"))
    note_overrides = pending.get("note_overrides")
    default_note = pending.get("default_note")
    step_timeout_sec = pending.get("step_timeout_sec")
    restart_max_attempts = pending.get("restart_max_attempts")
    chain_id = pending.get("chain_id")
    chain_docs_target = pending.get("chain_docs_target")
    chain_docs_reached = pending.get("chain_docs_reached")
    auto_approve_item_seqs = pending.get("auto_approve_item_seqs")
    # 0414 P0007 전달 지점 6: the next WORK hop re-reads the same two maps and resolves them
    # against ITS own worker item_seq.
    review_count_overrides = pending.get("review_count_overrides")
    reviewer_overrides = pending.get("reviewer_overrides")

    parts = group_id.split(".")
    project_id = parts[0]
    module = parts[1] if len(parts) > 2 and parts[1] != "none" else None

    def _issue_next(ai_run_id: Optional[str] = None) -> dict:
        adv = workflow_decision_service.advance_workflow(
            doc_id=doc_ref,
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            continuous=True,
            continuation_target_seq=target_seq,
            continuation_review_mode=review_mode,
            continuation_instruction_mode=instruction_mode,
            # 0359 L0007 §2.9: stamp the hop's run onto its token. Every continuous token ever
            # issued through this path had an empty ai_run_id (NR0003 §4), so a dead hop's
            # token led nowhere — the incident had to be reconstructed from a scratch file.
            ai_run_id=ai_run_id,
            continuation_auto_approve_item_seqs=auto_approve_item_seqs,
        )
        return {
            "raw_token": adv["token"],
            "token_id": adv["token_id"],
            "scratch_dir": adv["scratch_dir"],
            "mention": adv["mention"],
            # 0406 T0022 item 3 — a re-spawned hop carries the same facts.
            "worker_document_type": adv.get("worker_document_type"),
            "auto_handled_item_seqs": adv.get("auto_handled_item_seqs") or [],
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
        provider_pinned=provider_pinned,
        continuation_provider_overrides=overrides,
        continuation_note_overrides=note_overrides,
        continuation_default_note=default_note,
        chain_id=chain_id,
        chain_docs_target=chain_docs_target,
        chain_docs_reached=chain_docs_reached,
        continuation_auto_approve_item_seqs=auto_approve_item_seqs,
        continuation_step_timeout_sec=step_timeout_sec,
        continuation_restart_max_attempts=restart_max_attempts,
        continuation_review_count_overrides=review_count_overrides,
        continuation_reviewer_overrides=reviewer_overrides,
    )


# ── 0414 L0008: the [검수] gate ───────────────────────────────────────────────────────
#
# Three entry points call resolve_review_gate and they all get the same answer, because the
# answer is DERIVED, never stored (§2.1/§2.3):
#   1. the inbox self-chain boundary (_continuation_self_chain) — "is this slot reviewed?"
#   2. the engine hop settlement (_maybe_auto_resume_hop) — "review / rework / approve / stop?"
#   3. the human resume (resume_chain) — the same question after a restart, with no memory
#
# invariant R1 (0414 M0020 / CH0019): a step whose review count is not 0 never advances with
# a reviewer's complaint left unanswered. It passed, or every round it was given was reviewed
# AND reworked, or the chain stopped. There is no fourth path.
# The earlier form of R1 — "never advances without a `pass`" — ended a spent budget by parking
# the chain, which left the LAST round's findings recorded and never fixed. That is exactly
# what M0020 refused ("지적을 두번했으면 당연히 수정도 두번해야지"), so a finite count is now a
# budget of review+rework PAIRS: N 검수 · 지적마다 수정 · 마지막 수정 뒤 다음 단계.


def _enabled_provider_chain(project_id: Optional[str]) -> list[dict]:
    """The project's effective provider chain, or [] when it cannot be read."""
    if not project_id:
        return []
    try:
        return (ai_settings_service.resolve_effective(project_id) or {}).get("providers") or []
    except Exception:  # noqa: BLE001 — start_run re-resolves and reports the real failure
        logger.warning("review gate provider chain lookup failed for %s", project_id,
                       exc_info=True)
        return []


def _provider_enabled(project_id: Optional[str], provider_id: Optional[str]) -> bool:
    return bool(provider_id) and any(
        p.get("id") == provider_id for p in _enabled_provider_chain(project_id)
    )


def _first_enabled_provider_id(project_id: Optional[str]) -> Optional[str]:
    """The project default — first entry of the effective chain (L0008 §2.2)."""
    chain = _enabled_provider_chain(project_id)
    return chain[0].get("id") if chain else None


def _provider_name_of(project_id: Optional[str], provider_id: Optional[str]) -> Optional[str]:
    """Display name for a resolved provider id; the id itself when the name is unknown."""
    if not provider_id:
        return None
    for provider in _enabled_provider_chain(project_id):
        if provider.get("id") == provider_id:
            return provider.get("name") or provider_id
    return provider_id


def _map_lookup(overrides: Optional[dict], item_seq: Optional[int]):
    """Both key spellings, exactly as _resolve_continuation_hop_override accepts them."""
    if not overrides or item_seq is None:
        return None
    return overrides.get(str(item_seq), overrides.get(item_seq))


def resolve_review_count(review_count_overrides: Optional[dict], item_seq: Optional[int]) -> int:
    """How many times this step's output is reviewed (L0008 §2.2).

    0 for every step the user did not pick — count 0 never reaches storage, because P0007's
    normalization already dropped it, so "absent" and "0" are the same fact. A value outside
    REVIEW_COUNT_VALUES can only come from a hand-edited row (the write path is 422-guarded),
    and is read as "no review" rather than crashing the chain.
    """
    raw = _map_lookup(review_count_overrides, item_seq)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return REVIEW_COUNT_DEFAULT
    if raw not in REVIEW_COUNT_VALUES:
        logger.warning("review gate: ignoring out-of-range review count %r for item_seq %s",
                       raw, item_seq)
        return REVIEW_COUNT_DEFAULT
    return raw


def resolve_round_limit(count: int) -> int:
    """How many review+rework rounds this step gets; REVIEW_ROUNDS_NO_LIMIT = no ceiling.

    -1 is the user asking for "until it passes", and it is taken literally (0414 0022-TR
    rejection): there is no round number at which the chain gives up and calls a human.
    Only a `pass`, a `hold`, or a loop breaker ends it.
    """
    return REVIEW_ROUNDS_NO_LIMIT if count == -1 else int(count)


def review_rounds_remain(rounds_used: int, limit: int) -> bool:
    """Is another review round allowed? An unbounded budget always says yes."""
    return limit == REVIEW_ROUNDS_NO_LIMIT or rounds_used < limit


def resolve_reviewer(
    reviewer_overrides: Optional[dict], item_seq: Optional[int], project_id: Optional[str]
) -> Optional[str]:
    """Who reviews this step (L0008 §2.2): the step's own pick, else the project default.

    The step EXECUTOR's provider tiers are deliberately not consulted — a reviewer is chosen
    to have the work read by someone else, and folding the executor in here would quietly
    make that self-review.

    A pick that is no longer enabled degrades to the default rather than removing the review:
    a chain a person parked must stay resumable (P0007 [엣지] 재개 시 검수자 소멸). The 422
    that refuses the same pick outright belongs to the fresh-request path only.
    """
    provider_id = _map_lookup(reviewer_overrides, item_seq)
    if provider_id and _provider_enabled(project_id, provider_id):
        return provider_id
    if provider_id:
        logger.warning(
            "review gate: reviewer %s is no longer enabled for %s — "
            "falling back to the project default reviewer for item_seq %s",
            provider_id, project_id, item_seq,
        )
    return _first_enabled_provider_id(project_id)


def _stored_provider_for_item_seq(doc_ref: Optional[str], item_seq: Optional[int]) -> Optional[str]:
    """The provider persisted on that sequence row, if any."""
    if not doc_ref or item_seq is None:
        return None
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        for row in db_wfseq.get_sequence_items(seq["id"]) or []:
            if row.get("item_seq") == item_seq:
                return row.get("provider_id")
    except Exception:  # noqa: BLE001 — a stored preference must never stall a hop
        logger.warning("review gate stored provider lookup failed for %s", doc_ref, exc_info=True)
    return None


def resolve_step_executor(
    bundle: dict, item_seq: Optional[int], project_id: Optional[str], doc_ref: Optional[str]
) -> Optional[str]:
    """Who REWORKS this step (L0008 §2.2) — the step's executor, not its reviewer.

    Re-plays start_run's own priority order (step override → explicit pin → stored sequence
    assignment → project default) ahead of time, because the rework hop is mode="single" and
    start_run's continuous tiers would not run for it.
    """
    provider_id = _map_lookup(bundle.get("provider_overrides"), item_seq)
    if provider_id and _provider_enabled(project_id, provider_id):
        return provider_id
    base_provider_id = bundle.get("base_provider_id")
    if bundle.get("provider_pinned") and _provider_enabled(project_id, base_provider_id):
        return base_provider_id
    stored = _stored_provider_for_item_seq(doc_ref, item_seq)
    if stored and _provider_enabled(project_id, stored):
        return stored
    return _first_enabled_provider_id(project_id)


# doc_review_status values that mean "this output is not through the gate yet".
REVIEW_PENDING_DOC_STATUSES = frozenset({"pending_review", "revised", "rejected"})


def _pending_review_slot(doc_ref: Optional[str]) -> Optional[dict]:
    """The slot waiting on the gate — at most one per group (L0008 §2.3).

    One running chain has one hop, which fills one document, so the search is simply "the
    most recently FILLED slot": if its document is still unapproved it is the waiting slot,
    and if it is already approved there is nothing waiting. Slots with no result document
    yet are skipped rather than ending the scan — an ai_direct N/T head can sit empty ahead
    of the report slot that was actually just filled.
    """
    if not doc_ref:
        return None
    try:
        seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
        if seq is None:
            return None
        items = db_wfseq.get_sequence_items(seq["id"]) or []
    except Exception:  # noqa: BLE001 — an unreadable sequence falls back to the old flow
        logger.warning("review gate slot lookup failed for %s", doc_ref, exc_info=True)
        return None
    for item in sorted(items, key=lambda i: i.get("item_seq") or 0, reverse=True):
        result_doc_id = item.get("result_doc_id")
        if not result_doc_id:
            continue
        doc = db_docs.get_by_id(result_doc_id) or {}
        status = doc.get("doc_review_status") or ""
        if status not in REVIEW_PENDING_DOC_STATUSES:
            return None          # the newest filled slot is already approved → nothing waits
        return {
            "item_seq": item.get("item_seq"),
            "doc_id": doc.get("doc_id") or result_doc_id,
            "doc_type": (doc.get("type_code") or item.get("type") or "").upper(),
            "revision_no": int(doc.get("revision_no") or 0),
            "review_status": status,
        }
    return None


def _review_findings(review: Optional[dict]) -> list:
    """A review row's findings as a list — the column stores a JSON array string."""
    findings = (review or {}).get("findings")
    if isinstance(findings, str):
        try:
            findings = json.loads(findings)
        except (TypeError, ValueError):
            return []
    return findings if isinstance(findings, list) else []


def _normalize_ws(value) -> str:
    return " ".join(str(value or "").split())


def review_finding_digest(review: Optional[dict]) -> str:
    """A deterministic fingerprint of one review's findings (L0008 §2.3).

    Whitespace-normalized so a reflowed line is not mistaken for a new complaint. Two
    consecutive `issues` verdicts with the SAME digest mean the rework changed nothing the
    reviewer cares about, which is the practical safety net behind an unbounded -1.
    """
    parts = []
    for finding in _review_findings(review):
        if isinstance(finding, dict):
            parts.append(_normalize_ws(finding.get("locus")) + "\u241f"
                         + _normalize_ws(finding.get("note")))
        else:
            parts.append(_normalize_ws(finding))
    return hashlib.sha256("\u241e".join(parts).encode("utf-8")).hexdigest()


def _check_expected_progress(
    bundle: dict, slot: dict, reviews: list[dict]
) -> Optional[str]:
    """Did the hop that just ran leave what it was supposed to leave? (L0008 §2.3)

    Without this the gate re-reads `rounds_used == 0` after a review hop that recorded
    nothing and launches another review hop — forever. This is the only thing standing
    between the gate and that loop.

    Skipped entirely on a COLD start (`last_stage` absent): a human pressing [이어서 진행]
    after a restart has no previous hop to hold to account, and the DB derivation alone is
    already correct for them.
    """
    last_stage = bundle.get("last_stage")
    if not last_stage:
        return None
    if bundle.get("last_stop_code") == "question_pending":
        return "question_pending"          # waiting on a human answer — do not spin the loop
    if last_stage == REVIEW_HOP_KIND and len(reviews) <= int(bundle.get("rounds_before") or 0):
        return REVIEW_NO_VERDICT_STOP_CODE
    if last_stage == REWORK_HOP_KIND and int(slot.get("revision_no") or 0) <= int(
        bundle.get("revision_before") or 0
    ):
        return REVIEW_STALLED_STOP_CODE
    if (
        len(reviews) >= REVIEW_STALL_ROUNDS
        and (reviews[0].get("verdict") or "").lower() == "issues"
        and review_finding_digest(reviews[0]) == review_finding_digest(reviews[1])
    ):
        return REVIEW_STALLED_STOP_CODE
    return None


def resolve_review_gate(bundle: dict) -> dict:
    """What happens next for the slot this chain is standing on (L0008 §2.3).

    Returns {stage: work|review|rework|stop, ...}. Every fact it reads is re-derived here
    and now — rounds used is `len(document_reviews)`, "a rework landed" is
    `document.revision_no > the last review's revision_no`, "already rejected" is
    `doc_review_status`. Nothing about the loop's position is persisted, which is what makes
    a restart, an auto-handoff and a manual resume converge on one answer.
    """
    slot = _pending_review_slot(bundle.get("doc_ref"))
    if slot is None:
        return {"stage": WORK_HOP_KIND}                      # nothing to review — old flow

    count = resolve_review_count(bundle.get("review_count_overrides"), slot["item_seq"])
    if count == 0:
        return {"stage": WORK_HOP_KIND, "approve_first": True, "slot": slot, "count": 0}

    limit = resolve_round_limit(count)
    try:
        reviews = db_reviews.list_by_doc(slot["doc_id"]) or []      # newest first
    except Exception:  # noqa: BLE001
        logger.warning("review gate could not read reviews for %s", slot["doc_id"],
                       exc_info=True)
        reviews = []
    rounds_used = len(reviews)
    latest = reviews[0] if rounds_used else None
    common = {"slot": slot, "count": count, "limit": limit, "rounds_used": rounds_used}

    blocked = _check_expected_progress(bundle, slot, reviews)
    if blocked is not None:
        return {"stage": "stop", "stop_code": blocked, **common}

    if rounds_used == 0:
        return {"stage": REVIEW_HOP_KIND, "round_no": 1, **common}

    verdict = (latest.get("verdict") or "").lower()
    if verdict == "pass":
        return {"stage": WORK_HOP_KIND, "approve_first": True, **common}
    if verdict == "hold":
        return {"stage": "stop", "stop_code": REVIEW_VERDICT_HOLD_STOP_CODE, **common}

    # verdict == "issues" from here, and TWO independent conditions gate the rejection:
    #
    #   * idempotency — a document already in `rejected` (by this gate on an earlier pass,
    #     or by a human) is not rejected again;
    #   * revision match — the complaint has to be about the revision standing there NOW.
    #
    # The second one is 0459 NR0003's second defect. `reject_first` used to read the status
    # alone, so a rework that had already landed (revision_no past the review's, status
    # `revised`) was pushed back to `rejected` just before the NEXT review round started.
    # That round then passed, and the pass tried to approve a `rejected` document — a
    # combination transition_rules deliberately does not list — so settle_completed_step
    # returned approve_failed BEFORE its target check and the chain parked one approval
    # short of `completed`. The fix is here, at the cause: an old verdict does not reject a
    # new revision. `rejected + approve` stays absent from the transition table.
    latest_revision = int(latest.get("revision_no") or 0)
    slot_revision = int(slot["revision_no"])
    reject_first = slot["review_status"] != "rejected" and slot_revision == latest_revision

    if slot_revision > latest_revision:
        # The rework for this complaint already landed. Deliberately NO `reject_first`:
        # the fresh revision keeps its `revised` status into the next round, which is what
        # lets a later `pass` settle through the ordinary `revised + approve -> approved`
        # transition and reach `completed`.
        if review_rounds_remain(rounds_used, limit):
            # -1 never leaves this branch: "until it passes" reviews the fresh revision
            # too, round after round, for as long as the reviewer keeps finding issues.
            return {"stage": REVIEW_HOP_KIND, "round_no": rounds_used + 1, **common}
        # 0414 M0020 / CH0019: a finite count is a budget of review+rework PAIRS, and the
        # last pair has just closed — every complaint this step produced was reworked, so
        # the step is done. The reworked revision is approved and the chain moves on.
        logger.info(
            "review gate: item_seq %s advances after %s review+rework round(s); the finite "
            "budget is spent and every finding was reworked",
            slot["item_seq"], rounds_used,
        )
        return {"stage": WORK_HOP_KIND, "approve_first": True, **common}

    # No rework has landed for this complaint yet. EVERY `issues` verdict earns its rework
    # hop, the LAST round's included — 0414 M0020 "지적을 두번했으면 수정도 두번": a complaint
    # that is only recorded and never fixed is not a review. So count=1 runs
    # review → rework → advance, and count=2 runs review → rework → review → rework → advance.
    return {"stage": REWORK_HOP_KIND, "round_no": rounds_used,
            "reject_first": reject_first, **common}


# The automatic rejection text. English on purpose: T0010 작업 4 forbids new Korean literals
# in server modules, and build_review_mention's own review instructions are English in every
# locale, so the rejection the same reviewer's findings produce matches what it reads.
REVIEW_REJECT_HEADING = "## Automated review rejection"
REVIEW_LOCUS_UNSPECIFIED = "(locus unspecified)"


def build_auto_reject_reason(review: Optional[dict], slot: dict, api_base_url: Optional[str]) -> str:
    """The rejection text, which IS the rework instruction (L0008 §2.6).

    transition_document_review refuses an empty reason, and an `issues` verdict is allowed to
    carry neither a comment nor findings — so the heading is unconditional and the reason can
    never come out blank. Over-length is trimmed from the TAIL: the heading and the first
    findings are the part a reworker needs, and the full set stays one GET away.
    """
    lines = [REVIEW_REJECT_HEADING]
    comment = (review or {}).get("comment")
    if comment and str(comment).strip():
        lines += ["", str(comment).strip()]
    findings = _review_findings(review)
    shown = findings[:REVIEW_REASON_MAX_FINDINGS]
    if shown:
        lines.append("")
        for finding in shown:
            if isinstance(finding, dict):
                locus = _normalize_ws(finding.get("locus")) or REVIEW_LOCUS_UNSPECIFIED
                note = str(finding.get("note") or "").strip()
            else:
                locus, note = REVIEW_LOCUS_UNSPECIFIED, str(finding).strip()
            lines.append(f"- {locus}: {note}")
    if len(findings) > REVIEW_REASON_MAX_FINDINGS:
        lines.append(
            f"({len(findings) - REVIEW_REASON_MAX_FINDINGS} further finding(s) omitted here.)"
        )
    lines += ["", f"GET {(api_base_url or '').rstrip('/')}/document/{slot['doc_id']}/reviews"]
    text = "\n".join(lines)
    return text[:REVIEW_REASON_MAX_CHARS] if len(text) > REVIEW_REASON_MAX_CHARS else text


def _auto_reject(slot: dict, review: Optional[dict], bundle: dict) -> dict:
    """Turn an `issues` verdict into a real rejection (L0008 §2.6).

    Goes through pipeline_service.transition_document_review — the SINGLE writer of
    doc_review_status — with the chain issuer's real permissions, resolved by the same
    resolver the inbox auto-approve uses. Approval is never bypassed here and neither is
    rejection: an issuer without document.reject stops the chain instead of forcing it.
    """
    actor_user_id = bundle.get("issued_to")
    try:
        from modules.flow_gate.db import users as db_users
        from modules.flow_gate.workflow.routers.workflow import (
            _get_user_permissions as _resolve_user_permissions,
        )

        actor = db_users.get_by_id(actor_user_id) or {"user_id": actor_user_id, "is_admin": 0}
        permissions = _resolve_user_permissions(actor)
    except Exception as exc:  # noqa: BLE001
        logger.exception("review gate permission resolution failed for %s", actor_user_id)
        return {"ok": False, "stop_code": REVIEW_REJECT_DENIED_STOP_CODE, "detail": str(exc)}
    if "document.reject" not in permissions:
        return {"ok": False, "stop_code": REVIEW_REJECT_DENIED_STOP_CODE,
                "detail": "issuer lacks document.reject"}
    reason = build_auto_reject_reason(review, slot, bundle.get("api_base_url"))
    try:
        from modules.flow_gate.workflow.pipeline_service import transition_document_review

        transition_document_review(
            doc_id=slot["doc_id"],
            action="reject",
            actor_user_id=actor_user_id,
            user_permissions=permissions,
            comment=reason,
        )
    except Exception as exc:  # noqa: BLE001 — the stored document is never touched
        logger.warning("review gate auto-reject failed for %s", slot["doc_id"], exc_info=True)
        return {"ok": False, "stop_code": REVIEW_REJECT_FAILED_STOP_CODE, "detail": str(exc)}
    return {"ok": True}


def _latest_review_of(slot: dict) -> Optional[dict]:
    try:
        return db_reviews.get_latest_by_doc(slot["doc_id"])
    except Exception:  # noqa: BLE001
        logger.warning("review gate latest-review lookup failed for %s", slot["doc_id"],
                       exc_info=True)
        return None


def _user_pause_row_pending(group_id: Optional[str]) -> bool:
    """A user pause the engine must honour before it starts another hop.

    mark_user_paused cannot answer here: it needs a LIVE run tagged pause_requested, and by
    the time the gate runs the hop that carried that tag has already finished. The durable
    row is what survives, and it is the same row resume_chain consumes.
    """
    if not group_id:
        return False
    try:
        from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

        row = db_paused.get_by_group(group_id)
    except Exception:  # noqa: BLE001 — fail open: a probe must not stall a healthy chain
        logger.warning("review gate user-pause probe failed for %s", group_id, exc_info=True)
        return False
    return row is not None and (row.get("stop_kind") or "user") == "user"


def _settle_gate_pass(group_id: str, slot: dict, bundle: dict, run: dict) -> str:
    """Everything that follows a step FINISHING — the SAME helper the inbox uses (§2.7).

    A second implementation of "approve → target reached? → user pause? → continue" would
    drift from the first, so the reviewed path and the unreviewed path share one.

    Two things bring a gated step here: a `pass` verdict, and (0414 M0020) a finite budget
    whose last review+rework pair has closed. Both mean "this step is done", so both settle
    identically — the document the second one approves is the reworked revision.
    """
    from modules.flow_gate.api import inbox_routes as _inbox

    result = _inbox.settle_completed_step(
        project=str(group_id).split(".", 1)[0],
        group_id=group_id,
        doc_id=slot["doc_id"],
        doc_type=slot.get("doc_type") or "",
        actor_user_id=bundle.get("issued_to"),
        completed_seq=slot.get("item_seq"),
        target_seq=bundle.get("target_seq"),
        user_paused_probe=lambda: _user_pause_row_pending(group_id),
    )
    outcome = result.get("outcome")
    if outcome == "continue":
        return "continue"
    stop_code = result.get("stop_code") or "approve_failed"
    if outcome == "completed":
        # The chain reached its target with the last step reviewed AND approved. No card:
        # settle_completed_step already removed the paused row, so only the lease is left.
        _clear_handoff_row(group_id, run.get("run_id"))
        try:
            db_group_ai_leases.release(group_id, run["run_id"])
        except Exception:  # noqa: BLE001
            logger.warning("review gate lease release failed for %s", group_id, exc_info=True)
        return outcome
    # user_paused keeps the human's own row (_write_handoff_row refuses to overwrite it);
    # approve_denied / approve_failed get a system row so the chain is pickable again.
    run["review_reject_detail"] = result.get("detail")
    _park_handoff(run, bundle, stop_code)
    return outcome


def _queue_gate_bundle(group_id: str, bundle: dict) -> None:
    """Record the next hop's intent BEFORE launching it (L0008 §2.4).

    _finalize_run reads this queue to decide between begin_handoff and releasing the group
    lease. Launch first and the lease is gone by the time the successor asks for it, so the
    successor dies on 409 run_in_progress.
    """
    request_auto_resume(group_id, bundle)


def _spawn_review_hop(group_id: str, bundle: dict, gate: dict) -> None:
    """Launch the review hop (L0008 §2.5).

    mode="single" + action_scope="review" is not cosmetic: it is the combination that gives
    the run _probe_doc_reviews as its judge ("did a review row appear?"). Anything else falls
    back to the document oracle, which a review can never satisfy.

    The token carries NO continuation_target_seq, so _continuation_self_chain does not run
    for the verdict submission at all — a review structurally cannot approve its own target
    or advance the chain.
    """
    from modules.flow_gate.services import workflow_decision_service

    slot = gate["slot"]
    parts = group_id.split(".")
    project_id = parts[0]
    module = parts[1] if len(parts) > 2 and parts[1] != "none" else None
    locale = bundle.get("locale") or "ko"
    api_base_url = bundle.get("api_base_url")
    issued_to = bundle.get("issued_to")
    reviewer_id = resolve_reviewer(bundle.get("reviewer_overrides"), slot["item_seq"], project_id)
    executor_id = resolve_step_executor(bundle, slot["item_seq"], project_id, bundle.get("doc_ref"))
    if reviewer_id and reviewer_id == executor_id:
        # Allowed — a person may deliberately pick it — but never silent (L0008 §2.2).
        logger.warning(
            "review gate: item_seq %s is being self-reviewed (reviewer and executor are both %s)",
            slot["item_seq"], reviewer_id,
        )

    def _issue_review(ai_run_id: Optional[str] = None) -> dict:
        issued = workflow_decision_service.request_review(
            doc_id=slot["doc_id"],
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            ai_run_id=ai_run_id,
        )
        mention = issued.get("mention") or ""
        return {
            "raw_token": issued["token"],
            "token_id": issued["token_id"],
            "scratch_dir": issued["scratch_dir"],
            "mention": _append_engine_review_clause(mention, gate),
        }

    start_run(
        project_id=project_id,
        module=module,
        group_id=group_id,
        doc_ref=slot["doc_id"],
        action_scope="review",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        continuation_instruction_mode=bundle.get("instruction_mode"),
        continuation_locale=locale,
        issued_to=issued_to,
        api_base_url=api_base_url,
        mention_builder=lambda _raw, _scratch: None,
        issue_builder=_issue_review,
        provider_id=reviewer_id,
        chain_id=bundle.get("chain_id"),
        chain_docs_target=bundle.get("chain_docs_target"),
        chain_docs_reached=bundle.get("chain_docs_reached"),
        continuation_step_timeout_sec=bundle.get("step_timeout_sec"),
        continuation_review_count_overrides=bundle.get("review_count_overrides"),
        continuation_reviewer_overrides=bundle.get("reviewer_overrides"),
        hop_kind=REVIEW_HOP_KIND,
    )


def _append_engine_review_clause(mention: str, gate: dict) -> str:
    """The one clause an ENGINE-driven review hop adds to build_review_mention (L0008 §2.5).

    build_review_mention itself is never touched — the human [멘트복사] path shares it, and
    its body correctly tells that reader "a human decides afterward". On an unmanned chain
    that premise is false, so the reviewer has to be told the verdict wires straight into an
    automatic rejection and how many rounds are left. English, like the review instructions
    it extends (T0010 작업 4: no new Korean literals in server modules).
    """
    if not mention:
        return mention
    count = gate.get("count")
    round_no = gate.get("round_no")
    limit = gate.get("limit")
    budget = (
        "until the document passes"
        if count == -1
        else f"round {round_no} of {limit}"
    )
    # 0414 M0020: every round's findings are reworked, the last round's included, so the
    # clause no longer says "when a round remains". What the LAST reviewer of a finite budget
    # does need to know is that nobody reviews the fix its findings produce — the chain moves
    # on with it — so that round is told to name everything that still has to change.
    last_round = count != -1 and round_no == limit
    return mention + (
        "\n\n## Automated follow-up\n---\n"
        "This review runs inside an unmanned continuous chain, so no human reads your verdict "
        "before it takes effect. 'issues' rejects the document automatically with your comment "
        "and findings as the rejection reason, and hands it back to the step's own worker to "
        f"fix — every round's findings get their fix, this round's included. This is {budget}"
        + (" — there is no round ceiling: review and fix repeat until you return "
           "'pass', so keep reviewing until the document is right."
           if count == -1 else ".")
        + (
            " This is the LAST round: after the fix your findings produce, the chain moves on "
            "to the next step without another review, so name everything that still has to "
            "change." if last_round else ""
        )
        + " 'pass' approves the document and lets the chain move on; 'hold' stops the chain "
        "for a human. Judge accordingly."
    )


def _spawn_rework_hop(group_id: str, bundle: dict, gate: dict) -> None:
    """Launch the rework hop (L0008 §2.6).

    The REWORKER is the step's own executor, not the reviewer — the reviewer reads, the
    author fixes. The issuer is invoke_mention_service.issue_rework_request, the same one
    the human [AI 수정] button uses, so the two can never drift into separate prompts.
    """
    from modules.flow_gate.services import invoke_mention_service

    slot = gate["slot"]
    parts = group_id.split(".")
    project_id = parts[0]
    module = parts[1] if len(parts) > 2 and parts[1] != "none" else None
    locale = bundle.get("locale") or "ko"
    api_base_url = bundle.get("api_base_url")
    issued_to = bundle.get("issued_to")
    executor_id = resolve_step_executor(bundle, slot["item_seq"], project_id, bundle.get("doc_ref"))

    def _issue_rework(ai_run_id: Optional[str] = None) -> dict:
        return invoke_mention_service.issue_rework_request(
            doc_id=slot["doc_id"],
            issued_to=issued_to,
            api_base_url=api_base_url,
            locale=locale,
            ai_run_id=ai_run_id,
        )

    start_run(
        project_id=project_id,
        module=module,
        group_id=group_id,
        doc_ref=slot["doc_id"],
        # The TOKEN scope is what start_run receives (ai_invoke_routes maps rework->edit
        # before calling it), and it is also what picks _probe_doc_revision as the judge:
        # "did the revision number go up?" — the same fact §2.3 checks for review_stalled.
        action_scope="edit",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        continuation_instruction_mode=bundle.get("instruction_mode"),
        continuation_locale=locale,
        issued_to=issued_to,
        api_base_url=api_base_url,
        mention_builder=lambda _raw, _scratch: None,
        issue_builder=_issue_rework,
        provider_id=executor_id,
        chain_id=bundle.get("chain_id"),
        chain_docs_target=bundle.get("chain_docs_target"),
        chain_docs_reached=bundle.get("chain_docs_reached"),
        continuation_step_timeout_sec=bundle.get("step_timeout_sec"),
        continuation_review_count_overrides=bundle.get("review_count_overrides"),
        continuation_reviewer_overrides=bundle.get("reviewer_overrides"),
        hop_kind=REWORK_HOP_KIND,
    )


def run_review_gate(group_id: str, bundle: dict, run: dict) -> bool:
    """Derive the gate and act on it (L0008 §2.4). True when a next hop actually started.

    False means the chain was parked (a durable row + a released lease), so the caller must
    NOT clear the handoff row it wrote — that row is now the [이어서 진행] card.
    """
    gate = resolve_review_gate(bundle)
    slot = gate.get("slot")

    # 10-1: the rejection happens first and independently of what comes next, so a
    # "rounds exhausted" stop still leaves the reviewer's findings attached to the document.
    if gate.get("reject_first") and slot is not None:
        result = _auto_reject(slot, _latest_review_of(slot), bundle)
        if not result.get("ok"):
            run["review_reject_detail"] = result.get("detail")
            _park_handoff(run, bundle, result["stop_code"])
            return False

    stage = gate.get("stage")
    if stage == "stop":
        _park_handoff(run, bundle, gate.get("stop_code") or HOP_HANDOFF_FAILED_STOP_CODE)
        return False

    if stage == WORK_HOP_KIND:
        if gate.get("approve_first") and slot is not None:
            if _settle_gate_pass(group_id, slot, bundle, run) != "continue":
                return False
        # Deliberately NOT re-queued, unlike the two branches below: _finalize_run already
        # ran begin_handoff for this boundary, and the work hop's own inbox self-chain is
        # what queues the hop after it. Queueing here instead would leave a live entry
        # behind a hop that produced nothing, and the engine would re-spawn it forever
        # rather than stopping on no_output_exhausted.
        _spawn_auto_resume(group_id, {**bundle, "last_stage": WORK_HOP_KIND})
        return True

    if stage in (REVIEW_HOP_KIND, REWORK_HOP_KIND):
        # last_stage / rounds_before / revision_before live ONLY in the memory queue, never
        # in the paused row (L0008 §2.9): a cold start after a restart must reach the DB
        # derivation path, where the absence of these is exactly the right answer.
        queued = {**bundle, "last_stage": stage}
        if stage == REVIEW_HOP_KIND:
            queued["rounds_before"] = int(gate.get("rounds_used") or 0)
        else:
            queued["revision_before"] = int((slot or {}).get("revision_no") or 0)
        _queue_gate_bundle(group_id, queued)
        try:
            if stage == REVIEW_HOP_KIND:
                _spawn_review_hop(group_id, queued, gate)
            else:
                _spawn_rework_hop(group_id, queued, gate)
        except Exception:
            clear_auto_resume(group_id)     # take the intent back out; the caller parks it
            raise
        return True

    return False


def active_review_selection(group_id: Optional[str]) -> tuple[Optional[dict], Optional[dict]]:
    """This group's live [검수] selection, for the inbox boundary (L0008 §2.8).

    The maps ride the RUN, not the token, so the inbox — which only ever sees a token —
    has to ask the engine. (None, None) when no engine run is driving this group, which is
    also the correct answer: a copy-mention chain has nothing to launch a review hop with.
    """
    run = _active_run_for_group(group_id)
    if run is None:
        return None, None
    return (
        run.get("continuation_review_count_overrides"),
        run.get("continuation_reviewer_overrides"),
    )


def _sequence_completion_state(doc_ref: Optional[str]) -> tuple[bool, Optional[int]]:
    """``(a sequence was read, first incomplete item_seq)`` — 0459 T0005 §2-3.

    ``_next_incomplete_item_seq`` collapses two very different facts into one ``None``:
    "every slot is done" and "there is no sequence to look at". Resuming may treat both as
    "nothing to resume", but DELETING a stopped chain's card may not — a missing or
    unreadable sequence is no evidence at all. So the two facts are separated here and the
    old name keeps its single-value contract on top.

    Raises whatever the DB layer raises; callers that must not fail decide what an
    unreadable sequence means to them.
    """
    if not doc_ref:
        return False, None
    seq = db_wfseq.get_sequence_for_member_doc(doc_ref)
    if seq is None:
        return False, None
    items = db_wfseq.get_sequence_items(seq["id"]) or []
    if not items:
        return False, None          # a sequence with no slots proves nothing was finished
    for item in sorted(items, key=lambda i: i.get("item_seq") or 0):
        if item.get("result_doc_id") is None or item.get("result_doc_review_status") != "approved":
            return True, item.get("item_seq")
    return True, None


def _next_incomplete_item_seq(doc_ref: str) -> Optional[int]:
    """First workflow slot that is not complete (L0009 §2.3). Completion uses the
    existing slot definition — result document exists AND is approved — exactly as
    ai_invoke_routes._continuation_target_error judges it."""
    return _sequence_completion_state(doc_ref)[1]


def _group_workflow_finished(group_id: Optional[str]) -> bool:
    """Did this whole group already reach final approval? (0459 T0005 §2-2)

    The same DB reading git_service's finalize state and the dashboard's terminal-group
    filter use — R/B root at ``wf_done`` — reached through the public db.documents helper
    rather than another service's private function. Never raises: a probe that cannot read
    the table answers "not finished", which preserves the row.
    """
    if not group_id:
        return False
    try:
        return bool(db_docs.group_root_wf_done(group_id))
    except Exception:  # noqa: BLE001
        logger.warning("group wf_done probe failed for %s", group_id, exc_info=True)
        return False


def _system_pause_row_is_stale(row: dict) -> bool:
    """Whether a system stop still describes work this group actually owes.

    Judged in this order, most conclusive first (0459 T0005 §2):

      1. the group's R/B workflow root is ``wf_done`` — the group is over, so nothing it
         parked can still be waiting on anybody;
      2. a sequence WAS read and it has no incomplete slot left, or its next incomplete
         slot is past the row's own ``continuation_target_seq`` — the stored scope is
         finished, the same reading ``resume_chain`` calls ``nothing_to_resume``;
      3. only if neither piece of completion evidence exists, the original head test:
         the stop's ``hop_item_seq`` against the current next-incomplete slot.

    Step 3 is why steps 1 and 2 had to come first. Review and rework hops run
    ``mode="single"``, and only continuous runs record a ``hop_item_seq`` — so every card
    a review hop parked reached ``stopped_seq is None`` and returned "not stale" forever,
    which is 0459 NR0003's first defect (the 0457 ``approve_failed`` card).

    Scoped and conservative throughout: only ``stop_kind='system'`` rows carrying a
    ``stop_run_id`` are judged at all, a user pause is never touched, and an absent or
    unreadable sequence is NOT "everything finished" — it is no evidence, so the row is
    kept and a warning is logged. Never raises.
    """
    if (row.get("stop_kind") or "user") != "system" or not row.get("stop_run_id"):
        return False
    group_id = row.get("group_id") or ""

    # 1 — the whole group is finished.
    if _group_workflow_finished(group_id):
        return True

    # 2 — the stored scope is finished.
    try:
        sequence_read, next_seq = _sequence_completion_state(row.get("doc_ref"))
    except Exception:  # noqa: BLE001
        logger.warning(
            "system paused-row sequence lookup failed for %s",
            group_id, exc_info=True,
        )
        sequence_read, next_seq = False, None
    if sequence_read:
        if next_seq is None:
            return True
        target_seq = row.get("continuation_target_seq")
        if target_seq is not None:
            try:
                if int(next_seq) > int(target_seq):
                    return True
            except (TypeError, ValueError):
                pass
    else:
        # Neither wf_done nor a readable sequence: keep the card. A chain whose sequence
        # cannot be read is exactly the one a person still has to look at.
        logger.warning(
            "system paused-row kept for %s: no wf_done and no readable workflow sequence "
            "for %s", group_id, row.get("doc_ref"),
        )
        return False

    # 3 — the original head test, on the evidence it was written for.
    try:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        stopped = db_runs.get(row["stop_run_id"])
        stopped_seq = (stopped or {}).get("hop_item_seq")
    except Exception:  # noqa: BLE001
        logger.warning(
            "system paused-row staleness lookup failed for %s",
            group_id, exc_info=True,
        )
        return False
    if stopped_seq is None:
        # A single-hop stop with no completion evidence above: real outstanding work,
        # and nothing here can tell which slot it was. Missing evidence is not stale.
        return False
    try:
        return int(next_seq) != int(stopped_seq)
    except (TypeError, ValueError):
        return False


def _resumable_base_provider(project_id: str, provider_id: Optional[str]) -> Optional[str]:
    """The stored header pin, but only while it is still usable (0365 DB0004 §5-2).

    Called ONLY for an UNPINNED resume (resume_chain passes the pinned id straight
    through to start_run instead, so start_run's 422 provider_unavailable stays the
    sole authority over a pin — T0005 §3 item 1). start_run rejects an explicit pin
    that is not in the project's enabled chain with a 422; for an unpinned resume there
    is no such guard, so a stored provider that was deleted or switched off degrades to
    "no pin" here — the resume then follows the normal doc-type assignment →
    default-chain order instead of the paused card becoming un-resumable.
    """
    if not provider_id:
        return None
    try:
        chain = ai_settings_service.resolve_effective(project_id).get("providers") or []
    except Exception:  # noqa: BLE001 — start_run re-resolves and reports the real failure
        logger.warning("resume provider re-check failed for %s", project_id, exc_info=True)
        return None
    if any(p.get("id") == provider_id for p in chain):
        return provider_id
    logger.info(
        "paused chain %s pinned provider %s is no longer enabled — resuming unpinned",
        project_id, provider_id,
    )
    return None


def _paused_row_resume_state(
    project_id: str, row: dict, *, include_target: bool = False,
) -> dict:
    """Evaluate deterministic pause->resume admission without changing state.

    active-all and resume_chain both call this evaluator. Only read-only facts that are
    stable enough to decide at lookup time belong here: the decided sequence, pending
    worker count, enabled provider chain, and an explicit provider pin. Lease admission,
    worktree repair and advance/start side effects remain launch-time checks.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    def _state(
        available: bool,
        code: Optional[str] = None,
        reason: Optional[str] = None,
        provider_name: Optional[str] = None,
        target_seq: Optional[int] = None,
    ) -> dict:
        result = {
            "resume_available": available,
            "resume_block_code": code,
            "resume_block_reason": reason,
            "resume_provider_name": provider_name,
        }
        if include_target:
            result["_resume_target_seq"] = target_seq
        return result

    try:
        sequence = db_wfseq.get_sequence_for_member_doc(row["doc_ref"])
        items = db_wfseq.get_sequence_items(sequence["id"]) if sequence is not None else []
    except Exception:  # noqa: BLE001 — active-all stays available on transient lookup failure
        logger.warning("resume-state sequence check failed for %s", row.get("group_id"), exc_info=True)
        if include_target:
            raise _http_error(
                500,
                "resume_lookup_failed",
                "Could not read the workflow sequence to resume. Retry later.",
            )
        return _state(True)
    if sequence is None:
        return _state(
            False,
            "sequence_unavailable",
            f"continuous run requires a decided workflow sequence on {row['doc_ref']}",
        )

    target_seq = row.get("continuation_target_seq")
    if target_seq is None:
        target_seq = max(
            (item["item_seq"] for item in items or [] if item.get("item_seq") is not None),
            default=None,
        )
    if target_seq is None:
        return _state(
            False,
            "sequence_unavailable",
            f"continuous run requires a decided workflow sequence on {row['doc_ref']}",
        )

    try:
        docs_target = _continuation_docs_target(
            row["doc_ref"],
            int(target_seq),
            continuation_instruction_mode=row.get("continuation_instruction_mode"),
            continuation_auto_approve_item_seqs=db_paused.load_json_list(
                row.get("continuation_auto_approve_item_seqs")
            ),
        )
    except Exception:  # noqa: BLE001 — same fail-open preview / fail-safe resume split
        logger.warning("resume-state worker-step check failed for %s", row.get("group_id"), exc_info=True)
        if include_target:
            raise _http_error(
                500,
                "resume_lookup_failed",
                "Could not read the workflow sequence to resume. Retry later.",
            )
        return _state(True, target_seq=int(target_seq))
    if docs_target is None:
        return _state(
            False,
            "sequence_unavailable",
            f"continuous run requires a decided workflow sequence on {row['doc_ref']}",
            target_seq=int(target_seq),
        )
    if docs_target <= 0:
        return _state(
            False,
            "no_pending_worker_steps",
            f"no pending worker step at or below workflow item_seq {int(target_seq)}",
            target_seq=int(target_seq),
        )

    try:
        chain = ai_settings_service.resolve_effective(project_id).get("providers") or []
    except Exception:  # noqa: BLE001 — start_run re-resolves and reports the real failure
        logger.warning("resume-state provider check failed for %s", project_id, exc_info=True)
        return _state(True, target_seq=int(target_seq))
    if not chain:
        return _state(
            False,
            "no_enabled_provider",
            "No enabled AI provider for this project. Configure providers in AI settings.",
            target_seq=int(target_seq),
        )

    provider_id = row.get("continuation_base_provider_id")
    if not bool(row.get("continuation_provider_pinned")) or not provider_id:
        return _state(True, target_seq=int(target_seq))
    active = next((provider for provider in chain if provider.get("id") == provider_id), None)
    if active is not None:
        return _state(True, provider_name=active.get("name"), target_seq=int(target_seq))

    name = None
    try:
        settings_view = ai_settings_service.get_project_settings(project_id, include_catalog=False)
        for provider in settings_view.get("providers") or []:
            if provider.get("id") == provider_id:
                name = provider.get("name")
                break
    except Exception:  # noqa: BLE001 — a missing display name is not a block on its own
        logger.warning("resume-state provider name lookup failed for %s", project_id, exc_info=True)
    return _state(
        False,
        PROVIDER_UNAVAILABLE_CODE,
        PROVIDER_UNAVAILABLE_MESSAGE,
        provider_name=name,
        target_seq=int(target_seq),
    )


def _resumable_reviewer_overrides(
    project_id: str, reviewer_overrides: Optional[dict]
) -> Optional[dict]:
    """Drop reviewers the project no longer has, keep the rest (P0007 [엣지] 재개).

    The counterpart of _resumable_base_provider, and the opposite of the fresh-request rule:
    a NEW request naming a disabled reviewer is a visible 422, because the person is still
    at the screen and can pick again. A resume has nobody to ask, so it degrades that ONE
    entry to the project default reviewer and says so in the log — the review itself is
    never dropped, only the pick.
    """
    if not reviewer_overrides:
        return None
    kept = {
        item_seq: provider_id
        for item_seq, provider_id in reviewer_overrides.items()
        if _provider_enabled(project_id, provider_id)
    }
    dropped = sorted(set(reviewer_overrides) - set(kept))
    if dropped:
        logger.warning(
            "paused chain reviewer(s) %s are no longer enabled — resuming with the project "
            "default reviewer for item_seq %s",
            sorted({reviewer_overrides[k] for k in dropped}), ", ".join(dropped),
        )
    return kept or None


def resume_chain(
    *, group_id: str, user_id: str, api_base_url: str, locale: str = "ko",
    is_admin: bool = False,
) -> dict:
    """Resume a user-paused continuous chain from its next incomplete step (L0009 §2.4).

    The ORDER is the overlap protection: (1) group lock → (2) active-run check →
    (3) atomic paused-row consumption → (4) start. A row consumed by another path
    (auto-resume, another session, an external worker) surfaces as resume_conflict,
    never as a second concurrent run.

    0459 T0007 §2: only the user who paused the chain (``paused_by``) or an admin may
    resume it -- a project-read-only teammate sending this group's id must not be able
    to consume or relaunch somebody else's paused chain. The check reads the row and
    the consumption below re-verifies the SAME snapshot via a compare-and-swap
    (0459 TR0008 rev1 fix): a plain group-only delete after the authorization read
    would let a newer row -- upserted by a *different* user between the two calls --
    be consumed by the old owner's already-authorized request. Tying the delete to
    the exact row the authorization check inspected closes that window.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
    from modules.flow_gate.services import workflow_decision_service

    with _group_resume_lock(group_id):
        active = _active_run_for_group(group_id)
        if active is not None:
            raise _http_error(409, "run_already_active",
                              "An active run already exists for this group.",
                              run_id=active["run_id"])
        pre_row = db_paused.get_by_group(group_id)
        if pre_row is None:
            raise _http_error(409, "resume_conflict",
                              "This chain was already resumed by another path.",
                              group_id=group_id)
        # 0459 T0007 SS2: ownership is judged BEFORE anything else this row could tell
        # the caller -- a project-read-only teammate sending this group's id must not be
        # able to consume, relaunch, OR preflight somebody else's paused chain.
        if not is_admin and pre_row.get("paused_by") != user_id:
            raise _http_error(403, "paused_chain_forbidden",
                              "Only the user who paused this chain (or an admin) may "
                              "resume it.", group_id=group_id)
        project_id = group_id.split(".", 1)[0]
        preflight = _paused_row_resume_state(project_id, pre_row, include_target=True)
        if not preflight["resume_available"]:
            raise _http_error(
                422,
                preflight["resume_block_code"],
                preflight["resume_block_reason"],
                group_id=group_id,
            )

        # CAS-consume the exact row the authorization and preflight checks above just
        # read -- not a bare delete_and_return(group_id), which would happily take
        # whatever row is live at THIS instant even if it is not the row that was
        # authorized. A row that changed identity (newer paused_by/paused_at/stop_kind/
        # stop_run_id) between the reads fails the predicate and falls through to
        # resume_conflict below, same as "nothing to consume".
        row = db_paused.release_owned(
            group_id,
            paused_by=pre_row.get("paused_by"),
            paused_at=pre_row.get("paused_at"),
            stop_kind=pre_row.get("stop_kind"),
            stop_run_id=pre_row.get("stop_run_id"),
        )
        # release_owned is tri-state (0459 TR0008 rev2): a bare None (nothing left to
        # consume) and a ReleaseSuperseded (a newer row replaced the one this call was
        # authorized against) both fall through to the SAME resume_conflict here --
        # either way the row this call was authorized for is not the one to relaunch.
        if row is None or isinstance(row, db_paused.ReleaseSuperseded):
            raise _http_error(409, "resume_conflict",
                              "This chain was already resumed by another path.",
                              group_id=group_id)
        target_seq = preflight["_resume_target_seq"]

        def _restore_row() -> None:
            # The resume did not happen — put the consumed row back so the paused
            # card survives and the user can retry (L0009 §5: row preservation).
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
                    stop_kind=row.get("stop_kind") or "user",
                    stop_code=row.get("stop_code"),
                    stop_run_id=row.get("stop_run_id"),
                    stop_last_message_excerpt=row.get("stop_last_message_excerpt"),
                    # Restoring the row means restoring it whole: a retry of this resume
                    # must still find the user's selections (0365 DB0004 §5-3 case 5).
                    continuation_base_provider_id=row.get("continuation_base_provider_id"),
                    continuation_provider_pinned=row.get("continuation_provider_pinned"),
                    continuation_provider_overrides=row.get("continuation_provider_overrides"),
                    continuation_default_note=row.get("continuation_default_note"),
                    continuation_note_overrides=row.get("continuation_note_overrides"),
                    # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq selection are
                    # part of "restoring it whole" too — a failed resume must not silently
                    # drop the chain's ai_direct policy on retry.
                    continuation_instruction_mode=row.get("continuation_instruction_mode"),
                    continuation_auto_approve_item_seqs=row.get("continuation_auto_approve_item_seqs"),
                    continuation_step_timeout_sec=row.get("continuation_step_timeout_sec"),
                    continuation_restart_max_attempts=row.get("continuation_restart_max_attempts"),
                    # 0414: "restore it whole" includes the [검수] selection — a failed resume
                    # must not quietly turn the retry into an unreviewed chain.
                    continuation_review_count_overrides=row.get(
                        "continuation_review_count_overrides"),
                    continuation_reviewer_overrides=row.get("continuation_reviewer_overrides"),
                )
            except Exception:
                logger.warning("paused-row restore failed for %s", group_id, exc_info=True)

        try:
            # Preflight resolved the target before the row was consumed. Re-check only the
            # next item here: if it disappeared in the narrow lookup/delete window, the
            # existing nothing_to_resume self-cleaning contract remains authoritative.
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

        # 0352 T0004 §3.6: the root of the pause->resume mode-loss bug — this row is the
        # ONLY place the chain's N/T authoring policy survives a pause, and until this fix
        # neither the row schema nor this function read it back: _issue_resume never passed
        # continuation_instruction_mode to advance_workflow at all (silently normalizing to
        # auto_approved), and the start_run call below then hard-coded the literal string
        # "auto_approved". An ai_direct chain that got paused resumed as if the user had
        # never chosen ai_direct — the very selection this whole feature exists to keep.
        resume_instruction_mode = workflow_decision_service.normalize_continuation_instruction_mode(
            row.get("continuation_instruction_mode")
        )
        resume_auto_approve_item_seqs = (
            workflow_decision_service.normalize_continuation_auto_approve_item_seqs(
                db_paused.load_json_list(row.get("continuation_auto_approve_item_seqs"))
            )
        )

        def _issue_resume(ai_run_id: Optional[str] = None) -> dict:
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
                continuation_instruction_mode=resume_instruction_mode,
                continuation_auto_approve_item_seqs=resume_auto_approve_item_seqs,
                ai_run_id=ai_run_id,      # 0359 L0007 §2.9
            )
            return {
                "raw_token": adv["token"],
                "token_id": adv["token_id"],
                "scratch_dir": adv["scratch_dir"],
                "mention": adv["mention"],
                # 0406 T0022 item 3 — a resumed hop is the same.
                "worker_document_type": adv.get("worker_document_type"),
                "auto_handled_item_seqs": adv.get("auto_handled_item_seqs") or [],
            }

        parts = group_id.split(".")
        module = parts[1] if len(parts) > 2 and parts[1] != "none" else None
        # 0365 B0001: hand the paused chain's own selections back to start_run. Passing
        # nothing here is what made every resume fall through to the default chain's first
        # (most expensive) provider — the same loss the per-hop re-spawn path already fixed
        # for auto-resume in 0317 T0013 (NR0003 §2-5).
        provider_pinned = bool(row.get("continuation_provider_pinned"))
        base_provider_id = (
            row.get("continuation_base_provider_id")
            if provider_pinned
            else _resumable_base_provider(project_id, row.get("continuation_base_provider_id"))
        )
        provider_overrides = db_paused.load_json_map(
            row.get("continuation_provider_overrides")
        )
        note_overrides = db_paused.load_json_map(row.get("continuation_note_overrides"))
        default_note = (row.get("continuation_default_note") or "").strip() or None
        step_timeout_sec = row.get("continuation_step_timeout_sec")
        restart_max_attempts = row.get("continuation_restart_max_attempts")
        # 0414 P0007 [엣지] 재개 시 검수자 소멸: load_json_map degrades corrupt or non-object
        # text to None, so one damaged row loses its selection instead of blocking the resume.
        # A reviewer that has since been switched off is dropped from the map — and ONLY from
        # the map: the review COUNT survives, so that step is still reviewed, by the project
        # default reviewer. A new request would be refused outright (422 reviewer_unavailable);
        # a chain a person parked must never become a card that cannot restart.
        review_count_overrides = db_paused.load_json_map(
            row.get("continuation_review_count_overrides")
        )
        reviewer_overrides = _resumable_reviewer_overrides(
            project_id, db_paused.load_json_map(row.get("continuation_reviewer_overrides"))
        )
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
                continuation_instruction_mode=resume_instruction_mode,
                continuation_locale=locale,
                issued_to=user_id,
                api_base_url=api_base_url,
                mention_builder=lambda _raw, _scratch: None,
                issue_builder=_issue_resume,
                provider_id=base_provider_id,
                provider_pinned=provider_pinned,
                continuation_provider_overrides=provider_overrides,
                continuation_default_note=default_note,
                continuation_note_overrides=note_overrides,
                chain_id=row.get("chain_id"),
                chain_docs_target=row.get("chain_docs_target"),
                chain_docs_reached=row.get("chain_docs_reached"),
                continuation_auto_approve_item_seqs=resume_auto_approve_item_seqs,
                continuation_step_timeout_sec=step_timeout_sec,
                continuation_restart_max_attempts=restart_max_attempts,
                continuation_review_count_overrides=review_count_overrides,
                continuation_reviewer_overrides=reviewer_overrides,
            )
        except HTTPException as exc:
            _restore_row()
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if exc.status_code == 409:
                raise _http_error(
                    409, "resume_launch_failed",
                    str(detail.get("message") or exc.detail or "Resume launch failed."),
                    group_id=group_id, restored=True, resume_stage="advance_or_start",
                    cause_code=detail.get("code"),
                )
            raise
        except LookupError as exc:
            _restore_row()
            raise _http_error(
                404, "resume_advance_unavailable", str(exc),
                group_id=group_id, restored=True, resume_stage="advance",
            )
        except ValueError as exc:
            _restore_row()
            raise _http_error(
                409, "resume_advance_blocked", str(exc),
                group_id=group_id, restored=True, resume_stage="advance",
            )


def release_paused_chain(*, group_id: str, user_id: str, is_admin: bool = False) -> dict:
    """Explicit user cancel/release of a group-keyed PAUSED CHAIN (0459 T0007).

    Paused-row release ONLY -- never a live-run cancel (:func:`cancel_run`) and never
    a lease force-release (:func:`force_release_group_lease`). Order mirrors
    :func:`resume_chain` exactly so the two can never race into a double-consumption
    or a lost row: (1) group lock → (2) active-run / valid-lease check → (3) pause-row
    lookup + ownership judgement → (4) atomic compare-and-swap delete.

    Deliberately narrow lease handling: a VALID lease means resume/start/handoff may
    be mid-flight in another process (memory not finding the run here does not make
    that lease an orphan -- only :func:`force_release_group_lease`'s own liveness
    check gets to decide that), so this refuses with 409 and leaves both the pause row
    and the lease untouched. An EXPIRED lease is invisible here for free --
    ``db_group_ai_leases.get_active`` already reclaims it before answering.

    0459 TR0008 rev1: the active-run and valid-lease branches raise DIFFERENT 409
    codes (``run_already_active`` vs ``group_lease_active``) even though both stop
    the release -- the caller's remedy differs (adopt the other session's run vs.
    release the lease separately from the locked-group screen), and collapsing them
    into one code left both the API caller and the miniplayer unable to tell which
    applies.
    """
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    with _group_resume_lock(group_id):
        active = _active_run_for_group(group_id)
        if active is not None:
            raise _http_error(409, "run_already_active",
                              "An active run already exists for this group; the paused "
                              "chain was already resumed elsewhere.",
                              group_id=group_id, run_id=active["run_id"])
        lease = db_group_ai_leases.get_active(group_id)
        if lease is not None:
            # Distinct code from the active-run branch above (0459 TR0008 rev1): a
            # caller resuming elsewhere (run_already_active) and a caller that must
            # separately release an orphaned lease (group_lease_active) need
            # different follow-up actions, and both used to collapse into the same
            # code, leaving neither the API caller nor the UI able to tell them apart.
            raise _http_error(409, "group_lease_active",
                              "A valid group lease is held; resume/start/handoff may be "
                              "in progress. If the lease is actually orphaned, release it "
                              "separately from the locked-group screen.",
                              group_id=group_id, run_id=lease.get("run_id"))
        row = db_paused.get_by_group(group_id)
        if row is None:
            # Nothing to release -- same-request replay after a prior success, or the
            # chain already ended some other way. Idempotent 200, never 404 (T0007 §5).
            return {"ok": True, "group_id": group_id, "released": False,
                    "already_released": True}
        if not is_admin and row.get("paused_by") != user_id:
            raise _http_error(403, "paused_chain_forbidden",
                              "Only the user who paused this chain (or an admin) may "
                              "release it.", group_id=group_id)
        deleted = db_paused.release_owned(
            group_id,
            paused_by=row.get("paused_by"),
            paused_at=row.get("paused_at"),
            stop_kind=row.get("stop_kind"),
            stop_run_id=row.get("stop_run_id"),
        )
        if isinstance(deleted, db_paused.ReleaseSuperseded):
            # 0459 TR0008 rev2: a newer user pause or system stop won the read/delete
            # race -- the row THIS call inspected is gone, but a DIFFERENT row for the
            # same group is still live in the table right now. T0007 §5 permits the
            # idempotent already_released 200 ONLY when nothing exists for the group_id
            # at all (the rev1 bug folded this case into that same success and told the
            # store to delete a card for a chain that never left the table). Surface it
            # as a real, non-success conflict instead so the caller reconciles against
            # the CURRENT server state rather than optimistically dropping the card.
            raise _http_error(
                409, "release_conflict",
                "This paused chain was replaced by a newer pause or system stop "
                "before the release completed. Refresh to see the current state.",
                group_id=group_id,
            )
        if deleted is None:
            # Truly nothing left for this group_id -- same-request replay after a
            # prior success, or the chain ended some other way. Idempotent 200.
            return {"ok": True, "group_id": group_id, "released": False,
                    "already_released": True}
        return {"ok": True, "group_id": group_id, "released": True,
                "already_released": False}


def _open_q_doc_ids(group_id: str) -> list[str]:
    """Group documents that still have at least one unanswered container item."""
    try:
        pending: set[str] = set()
        for doc in db_docs.get_documents_by_group_id(group_id):
            doc_id = doc.get("doc_id")
            if not doc_id:
                continue
            container = db_questions.get_container_by_doc(doc_id)
            if container and db_question_items.list_unanswered(container["id"]):
                pending.add(doc_id)
        return sorted(pending)
    except Exception:
        logger.warning("open-Q lookup failed for %s", group_id, exc_info=True)
        return []


def _handoff_row_in_flight(row: dict) -> bool:
    """Is this a hop handoff that is still plausibly landing? (0406 T0022 item 4)

    True only for a ``hop_handoff`` system row whose group still has a live run, or whose
    write is younger than :data:`HOP_HANDOFF_GRACE_SEC`. Anything else — a handoff the
    startup recovery re-labelled, a parked failure, a user pause — is a real card.
    """
    if (row.get("stop_kind") or "user") != "system":
        return False
    if (row.get("stop_code") or "") != HOP_HANDOFF_STOP_CODE:
        return False
    if _active_run_for_group(row.get("group_id") or "") is not None:
        return True
    stamp = row.get("updated_at") or row.get("paused_at")
    if not stamp:
        return False
    try:
        written = datetime.fromisoformat(str(stamp))
    except ValueError:
        return False
    if written.tzinfo is None:
        written = written.astimezone()
    age = (datetime.now(timezone.utc) - written.astimezone(timezone.utc)).total_seconds()
    return age < HOP_HANDOFF_GRACE_SEC


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
        if _handoff_row_in_flight(row):
            # 0406 T0022 item 4: a normal handoff takes seconds. Drawing a "stopped" card
            # just because the durable row exists for those seconds makes it blink on every
            # successful hop, and the stop badge stops meaning anything. In-flight handoffs
            # are hidden; a row past the grace really did break and falls through to a card.
            continue
        try:
            stale = _system_pause_row_is_stale(row)
        except Exception:  # noqa: BLE001 — one unreadable row must not blank the widget
            logger.warning(
                "system paused-row staleness check failed for %s",
                row.get("group_id"), exc_info=True,
            )
            stale = False
        if stale:
            # Delete only the exact system-stop snapshot inspected above, by
            # (group_id, stop_kind='system', stop_run_id). A user pause written since the
            # read, or a newer system stop for the same group, fails that exact match and
            # survives — which a group-only DELETE would not.
            try:
                db_paused.delete_system_stop(row["group_id"], row.get("stop_run_id"))
            except Exception:
                logger.warning(
                    "stale system paused-row cleanup failed for %s",
                    row.get("group_id"), exc_info=True,
                )
                # The delete failed, but the row IS stale: keeping it out of the response
                # is still right, and the next active-all retries the delete.
            continue
        resume_state = _paused_row_resume_state(row["group_id"].split(".")[0], row)
        paused.append({
            "group_id": row["group_id"],
            "doc_ref": row["doc_ref"],
            "mode": row.get("mode") or "continuous",
            "paused_by": row["paused_by"],
            "paused_at": row["paused_at"],
            "continuation_target_seq": row.get("continuation_target_seq"),
            "docs_target": row.get("docs_target"),
            "docs_reached": int(row.get("docs_reached") or 0),
            # 0357 T0004: chain-lifetime progress, so a resumed card shows how far the
            # CHAIN got — not how far its last hop got.
            "chain_id": row.get("chain_id"),
            "chain_docs_target": row.get("chain_docs_target"),
            "chain_docs_reached": int(row.get("chain_docs_reached") or 0),
            "pending_q_doc_ids": _open_q_doc_ids(row["group_id"]),
            # 0359 P0006 [handover]: a chain the SYSTEM parked looks like one a person parked,
            # so the existing card and its [resume] button work unchanged — these four
            # fields only say which it was and why. A legacy row has no stop_kind: it predates
            # system stops, so it can only have been a person (DB0008 §2.3).
            "stop_kind": row.get("stop_kind") or "user",
            "stop_code": row.get("stop_code"),
            "stop_run_id": row.get("stop_run_id"),
            "stop_last_message_excerpt": row.get("stop_last_message_excerpt"),
            # T0005 §2: whether THIS paused row can actually resume under the current
            # provider settings snapshot — a display hint only, never a substitute for
            # start_run's own 422 at execution time.
            "resume_available": resume_state["resume_available"],
            "resume_block_code": resume_state["resume_block_code"],
            "resume_block_reason": resume_state["resume_block_reason"],
            "resume_provider_name": resume_state["resume_provider_name"],
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
