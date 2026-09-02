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

── File split (flowgate.default.0497 T0009) ─────────────────────────────────

This module's body had grown past 9,000 lines in one file — too large to read or
edit in one piece, by a person or by a tool. The blank lines between top-level
definitions were collapsed to one, and the body was then cut into three files at
two existing section boundaries:

  ai_invoke_service.py       constants, run registry, scratch, oracles, start_run
  ai_invoke_part2_worker.py  worker, retries, CLI/API adapters, judging, stop rows
  ai_invoke_part3_chain.py   status/cancel, pause/resume, next hop, the review gate

`_load_parts()` at the bottom of this file executes the other two IN THIS MODULE'S
globals(), in the original order. Nothing about the namespace moves: definition
order, module-level state and every attribute visible from outside (including the
private ones tests patch) are exactly what they were before the split. No code was
rewritten — the lines were carried over verbatim, which is why the compiled code
object of every function is byte-identical to the pre-split one.

Each part repeats the same import block so that opening one file on its own still
shows what it uses; a part imported directly (by tooling that walks the package)
fills the rest of the namespace from this module. Turning the parts into a real
import structure is a separate group's refactoring task, deliberately not done here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

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
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate import template_provision
from modules.flow_gate.services import api_server_tools, git_service, invoke_mention_service, process_runner, q_service, register_binding, token_service
from modules.flow_gate.services.ai_invoke_helpers import prioritize_chain as _prioritize_chain
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
# 0466 T0007 §3.2.5 × 0458 T0007 §2.1 (merge): a park corrects THREE surfaces -- the
# stored record, the finished event and, when the stop is one this speaker owns, the
# notification. The first two apply to every park; the third does not. All seven
# REVIEW_STOP_CODES are members of ENGINE_NOTIFY_STOP_CODES (L194-199), so announcing a park
# with the full NOTIFY_STOP_CODES would also raise a "continuous work failed" for
# `review_verdict_hold` -- a hop waiting on a human answer, not a failure, by the same
# reasoning that keeps `question_pending` out of the set entirely -- and for `review_stalled`
# / `review_reject_denied` / `review_reject_failed`, none of which T0007 asked to speak here.
# `review_no_verdict` is the one review stop that must, because the reviewer left no row and
# nothing else reports that.
PARK_NOTIFY_STOP_CODES = (
    (NOTIFY_STOP_CODES - REVIEW_STOP_CODES) | {REVIEW_NO_VERDICT_STOP_CODE}
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

_CHAT_TOOL_NAME = "send_chat_reply"
_CHAT_TOOL_DESC = "Send the assistant reply for this chat conversation."
_CHAT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string", "description": "Complete assistant reply text"},
    },
    "required": ["body"],
}

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

def _reclaim_orphan_lease_token(lease_row: dict, reason: str) -> None:
    """Best-effort revoke of a dead lease's still-active token (0447 T0007).

    A lease alone only blocks re-entry into the group; the `token_id` it was issued
    with is a separate credential that otherwise survives a restart until its own
    TTL, letting a human replay it back into the same group. No-op when the lease
    carries no token_id, the token cannot be found, or the token is already
    consumed or revoked -- a consumed single-use token must never be flipped to
    revoked after the fact (that would rewrite a settled audit trail for a token
    nobody can replay anyway), and an already-revoked token must not draw a second
    `token_revoked` event. Whether an end record exists or was written for this
    victim's run plays no part in this decision; only the token's own
    consumed/revoked state does. Goes through token_service.revoke() (never
    db/tokens.py directly) so the existing workflow_events.token_revoked audit
    contract is preserved, and that call is itself idempotent under a race --
    including a race against a sibling process, decided by the atomic claim
    marker in db_tokens.revoke() rather than any in-process lock (0447 T0007
    review rev1).
    """
    token_id = lease_row.get("token_id")
    if not token_id:
        return
    token = db_tokens.get_by_id(token_id)
    if token is None:
        return
    if token.get("consumed_at") or token.get("revoked_at"):
        return
    token_service.revoke(token_id, reason=reason)

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
        # Independent of the end-record write above (0447 T0007 item 2): a victim
        # whose run already has a normal end record -- e.g. the process died after
        # persisting ai_invoke_runs but before releasing the lease -- still carries
        # an active orphan token that must be reclaimed here.
        try:
            _reclaim_orphan_lease_token(row, "orphaned_by_restart")
        except Exception:
            logger.warning(
                "orphaned-lease token revoke failed for run %s token %s",
                row.get("run_id"), row.get("token_id"), exc_info=True,
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

def ai_run_succeeded(row: dict) -> bool:
    """Return the single terminal success verdict used by list and detail views."""
    outcome = str(row.get("outcome") or "").strip().lower()
    stop_code = str(row.get("stop_code") or "").strip()
    end_reason = str(row.get("end_reason") or "").strip().lower()
    return outcome == "complete" and not stop_code and end_reason not in {
        "cancelled", "canceled", "failed", "error", "stopped", "timeout",
    }

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
    else:
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        row = db_runs.get(run_id)
        if row is None:
            raise _http_error(404, "run_not_found", "Unknown or expired run id.")
        payload = _run_detail_from_row(row)
        payload["persisted"] = True

    project_id = str(payload.get("project_id") or payload.get("group_id") or "").split(".", 1)[0]
    payload["project_id"] = project_id
    payload["succeeded"] = ai_run_succeeded(payload)
    payload["doc_title"] = None
    if payload.get("doc_ref"):
        try:
            doc = db_docs.get_by_id(payload["doc_ref"])
            if doc and doc.get("project_id") == project_id:
                payload["doc_title"] = doc.get("title")
        except Exception:
            logger.debug("AI detail document enrichment skipped", exc_info=True)
    return payload

def _persisted_register_failures(run_id: str) -> list:
    """Structured binding failures for a finished run, or [] when it predates them."""
    try:
        from modules.flow_gate.db import register_context_failures as db_register_failures

        return db_register_failures.list_by_run(run_id)
    except Exception:
        logger.debug("register context failure lookup skipped", exc_info=True)
        return []

def _run_detail_from_row(row: dict) -> dict:
    """Shape a persisted `ai_invoke_runs` row like the live `finished_payload` (same
    field names) so a client renders both through one path."""
    payload = {
        "ok": True,
        "run_id": row["run_id"],
        "status": "finished",
        "mode": row["mode"],
        "group_id": row["group_id"],
        "project_id": row.get("project_id"),
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
        "selected_provider_source": row.get("selected_provider_source"),
        "fallback_allowed": bool(row.get("fallback_allowed")),
        "attempt_no": row.get("attempt_no"),
        "fallback_history": row.get("fallback_history"),
        "register_errors": row.get("register_errors"),
        # 0492 T0018 item 3: the axis-classified form of the same failures, when this run
        # has one. `register_errors` above is deliberately left as it was — every existing
        # reader still uses it, and it is both the backfill source and the rollback safety
        # net. A run from before migration 094 has no rows here and keeps answering out of
        # the legacy array alone.
        "register_context_failures": _persisted_register_failures(row["run_id"]),
        "tool_call_misses": row.get("tool_call_misses"),
        "turn_limit_exhausted": bool(row.get("turn_limit_exhausted")),
        "oracle_mismatch": bool(row.get("oracle_mismatch")),
        # 0505 T0006 (DB0005 3.3): same names, same restart-half contract as the four
        # exit diagnostics below -- a run from before migration 095 has none of this
        # and reads back as None on every one of these ten keys.
        "operator_api_base": row.get("operator_api_base"),
        "transport_api_base": row.get("transport_api_base"),
        "last_tool_name": row.get("last_tool_name"),
        "last_tool_status": row.get("last_tool_status"),
        "last_tool_error": row.get("last_tool_error"),
        "api_turns_used": row.get("api_turns_used"),
        "model_http_calls": row.get("model_http_calls"),
        "model_last_http_status": row.get("model_last_http_status"),
        "tool_calls_received": row.get("tool_calls_received"),
        "tool_calls_executed": row.get("tool_calls_executed"),
        "api_turn_trace": row.get("api_turn_trace") or [],
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
        "document_review_loop": document_review_loop_payload({
            "document_review_loop": _restore_document_review_loop(row["run_id"])
        }),
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

SCRATCH_MANIFEST_NAME = ".flowgate-ai-scratch.json"
SCRATCH_MANIFEST_SCHEMA = 1
_RUN_ID_RE = re.compile(r"\Aaiv_[0-9]{8}_[0-9]{6}\Z")

def _safe_scratch_log(project_id: str, run_id: str, scratch: Path, action: str, reason: str) -> None:
    """Emit one escaped event without prompts, commands, credentials, or raw paths."""
    try:
        root = _project_scratch_root(project_id).resolve(strict=False)
        relative = scratch.resolve(strict=False).relative_to(root).as_posix()
    except Exception:
        relative = "<outside-managed-root>"
    logger.info("ai-invoke scratch %s", json.dumps({
        "run_id": str(run_id) if _RUN_ID_RE.fullmatch(str(run_id)) else "<invalid>",
        "scratch": relative,
        "action": action,
        "reason": reason,
    }, ensure_ascii=True))

def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
        reparse_flag = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attrs & reparse_flag)
    except OSError:
        return True

def _atomic_write_manifest(scratch: Path, manifest: dict) -> None:
    target = scratch / SCRATCH_MANIFEST_NAME
    temporary = scratch / (SCRATCH_MANIFEST_NAME + ".tmp")
    data = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
        handle.flush()
    temporary.replace(target)

def _manifest_for(project_id: str, run_id: str, scratch: Path) -> dict:
    return {
        "schema": SCRATCH_MANIFEST_SCHEMA,
        "owner": "flowgate.ai-invoke",
        "project_id": project_id,
        "run_id": run_id,
        "scratch_path": str(scratch.resolve(strict=True)),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "policy": {"retention_days": SCRATCH_RETENTION_DAYS, "delete_on_complete": True},
    }

def _create_scratch(project_id: str, run_id: str) -> Path:
    if not _RUN_ID_RE.fullmatch(str(run_id)):
        raise ValueError("invalid ai-invoke run id")
    root = _project_scratch_root(project_id)
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve(strict=True)
    scratch = root / run_id
    scratch.mkdir(exist_ok=False)
    try:
        if _is_reparse_or_symlink(scratch) or scratch.resolve(strict=True).parent != root_resolved:
            raise ValueError("scratch is not a direct managed child")
        (scratch / "tmp").mkdir()
        (scratch / "cache").mkdir()
        _atomic_write_manifest(scratch, _manifest_for(project_id, run_id, scratch))
    except Exception:
        if scratch.exists() and not _is_reparse_or_symlink(scratch):
            shutil.rmtree(scratch)
        raise
    _safe_scratch_log(project_id, run_id, scratch, "created", "manifest_written")
    return scratch

def _validate_scratch_manifest(project_id: str, run_id: str, scratch: Path) -> tuple[Optional[dict], str]:
    if not _RUN_ID_RE.fullmatch(str(run_id)):
        return None, "invalid_run_id"
    try:
        root = _project_scratch_root(project_id).resolve(strict=True)
        if _is_reparse_or_symlink(scratch):
            return None, "reparse_or_symlink"
        resolved = scratch.resolve(strict=True)
        if resolved == root or resolved.parent != root or scratch.parent.resolve(strict=True) != root:
            return None, "outside_or_nested"
        if not resolved.is_dir() or resolved.name != run_id:
            return None, "path_identity_mismatch"
        manifest_path = resolved / SCRATCH_MANIFEST_NAME
        if _is_reparse_or_symlink(manifest_path) or not manifest_path.is_file():
            return None, "manifest_missing"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_policy = {"retention_days": SCRATCH_RETENTION_DAYS, "delete_on_complete": True}
        if manifest.get("schema") != SCRATCH_MANIFEST_SCHEMA or manifest.get("owner") != "flowgate.ai-invoke":
            return None, "manifest_identity_invalid"
        if manifest.get("project_id") != project_id or manifest.get("run_id") != run_id:
            return None, "manifest_owner_mismatch"
        if manifest.get("scratch_path") != str(resolved) or manifest.get("policy") != expected_policy:
            return None, "manifest_path_or_policy_mismatch"
        return manifest, "valid"
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return None, "manifest_unreadable"

def _mark_scratch_completed(project_id: str, run_id: str, scratch: Path, completed_at: str) -> bool:
    manifest, reason = _validate_scratch_manifest(project_id, run_id, scratch)
    if manifest is None:
        _safe_scratch_log(project_id, run_id, scratch, "retained", reason)
        return False
    manifest["completed_at"] = completed_at
    try:
        target = scratch / SCRATCH_MANIFEST_NAME
        temporary = scratch / (SCRATCH_MANIFEST_NAME + ".update")
        temporary.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)
        return True
    except OSError:
        _safe_scratch_log(project_id, run_id, scratch, "retained", "manifest_update_failed")
        return False

def _delete_owned_scratch(project_id: str, run_id: str, scratch: Path) -> tuple[bool, str]:
    manifest, reason = _validate_scratch_manifest(project_id, run_id, scratch)
    if manifest is None:
        return False, reason
    try:
        shutil.rmtree(scratch)
    except Exception:
        return False, "delete_failed"
    if scratch.exists() or scratch.is_symlink():
        return False, "delete_incomplete"
    _safe_scratch_log(project_id, run_id, scratch, "deleted", "verified_absent")
    return True, "deleted"

def _cleanup_retained_scratches(project_id: str) -> None:
    """Delete only manifest-proven direct children retained for at least seven days."""
    try:
        root = _project_scratch_root(project_id)
        if not root.is_dir() or _is_reparse_or_symlink(root):
            return
        now = datetime.now(timezone.utc)
        for child in root.iterdir():
            run_id = child.name
            if not child.is_dir() or _is_reparse_or_symlink(child):
                _safe_scratch_log(project_id, run_id, child, "skipped", "not_owned_directory")
                continue
            manifest, reason = _validate_scratch_manifest(project_id, run_id, child)
            if manifest is None:
                _safe_scratch_log(project_id, run_id, child, "skipped", reason)
                continue
            try:
                completed = datetime.fromisoformat(str(manifest.get("completed_at")))
                if completed.tzinfo is None:
                    raise ValueError
                age = now - completed.astimezone(timezone.utc)
            except (TypeError, ValueError):
                _safe_scratch_log(project_id, run_id, child, "skipped", "completion_time_invalid")
                continue
            if age < timedelta(days=SCRATCH_RETENTION_DAYS):
                _safe_scratch_log(project_id, run_id, child, "retained", "retention_active")
                continue
            deleted, delete_reason = _delete_owned_scratch(project_id, run_id, child)
            if not deleted:
                _safe_scratch_log(project_id, run_id, child, "retained", delete_reason)
    except Exception:
        logger.warning("ai-invoke scratch sweep failed")

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

def _review_hop_recovery_open(mode: Optional[str], action_scope: Optional[str],
                              scope_oracle_run: Optional[bool],
                              hop_kind: Optional[str]) -> bool:
    """flowgate.default.0466 T0007 §3.1.1: may an ENGINE-spawned review hop with no verdict
    reopen a second attempt in the SAME round?

    Deliberately a second, narrower predicate rather than a widening of
    `_scope_oracle_retry_open`'s edit-only condition (T0007 explicitly forbids that).
    `hop_kind == REVIEW_HOP_KIND` is the whole narrowing: `start_run`'s `hop_kind` parameter
    defaults to `WORK_HOP_KIND`, and `_spawn_review_hop` is the ONLY caller anywhere in the
    codebase that ever passes `REVIEW_HOP_KIND` — a person's plain single review call
    (`POST /ai-invoke` with action_scope='review') never does, so it stays one-shot exactly
    as before. (`chain_id` is NOT part of this predicate: `start_run` defaults every
    single-mode run's chain_id to its own run_id — `chain_id = chain_id or run_id` — so a
    plain single call already carries a non-empty chain_id and checking it would narrow
    nothing.)
    """
    return (
        bool(scope_oracle_run)
        and mode == "single"
        and action_scope == "review"
        and hop_kind == REVIEW_HOP_KIND
    )

def _scope_oracle_retry_run(run: dict) -> bool:
    """The same question asked of a live run dict — `scope_oracle_run` rides on it (§3-1).

    0466 T0007 §3.1.1: ORs in `_review_hop_recovery_open` above, so every no-output-retry
    consumer (`_retry_eligible`, `_recheck_no_output`, the docs_target=0 guard) treats an
    ENGINE-spawned review hop's no-verdict outcome exactly like a scope-oracle edit/rework
    run — same recheck-before-retry, same "output is output" guard — without duplicating any
    of that machinery for review specifically.
    """
    return _scope_oracle_retry_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run")
    ) or _review_hop_recovery_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run"),
        run.get("hop_kind"),
    )

def _review_hop_recovery_run(run: dict) -> bool:
    """`_review_hop_recovery_open` asked of a live run dict, standalone (T0007 §2.3/§3.1.5) —
    used where the caller must tell a review-hop recovery apart from an edit/rework
    scope-oracle retry (they resolve their retry PROVIDER differently)."""
    return _review_hop_recovery_open(
        run.get("mode"), run.get("action_scope"), run.get("scope_oracle_run"),
        run.get("hop_kind"),
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
    document_review_loop: Optional[dict] = None,
    # A single-request acknowledgement. It is intentionally never persisted or forwarded.
    capability_warning_ack: Optional[bool] = None,
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
    # 0417 D0007/P0008: stage selection precedes provider-chain selection. In particular,
    # an unaddressed rejection starts with the fixed rework provider, never the reviewer.
    if document_review_loop is not None:
        document_review_loop = dict(document_review_loop)
        document_review_loop.update(compute_review_baseline(doc_ref))
        initial_stage = (
            REWORK_HOP_KIND if document_review_loop["starts_with_rework"] else REVIEW_HOP_KIND
        )
        provider_id = resolve_loop_provider(document_review_loop, initial_stage)
        provider_pinned = True
    continuation_instruction_mode = normalize_continuation_instruction_mode(
        requested_continuation_instruction_mode
    )
    continuation_auto_approve_item_seqs = normalize_continuation_auto_approve_item_seqs(
        continuation_auto_approve_item_seqs
    )
    effective = ai_settings_service.resolve_effective(project_id)
    chain = effective.get("providers") or []
    chain_source = effective.get("source")
    selected_provider_source = (
        "review_loop" if document_review_loop is not None else "project_default"
    )
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
        selected_provider_source = "step_override"
    elif mode == "continuous" and provider_pinned and provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, PROVIDER_UNAVAILABLE_CODE,
                PROVIDER_UNAVAILABLE_MESSAGE,
            )
        chain = [selected]
        selected_provider_source = "force_all"
    elif stored_provider_active:
        # An unpinned run still follows the persisted sequence assignment (D0006 §6.2).
        selected = next(
            (provider for provider in chain if provider.get("id") == stored_provider_id),
            None,
        )
        chain = [selected] if selected else []
        selected_provider_source = "stored_sequence"
    elif provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, PROVIDER_UNAVAILABLE_CODE,
                PROVIDER_UNAVAILABLE_MESSAGE,
            )
        chain = [selected]
        selected_provider_source = (
            "review_loop" if document_review_loop is not None else "request"
        )
    elif mode == "continuous":
        hop_provider = _resolve_continuation_hop_provider(
            project_id,
            doc_ref,
            continuation_instruction_mode=continuation_instruction_mode,
            continuation_auto_approve_item_seqs=continuation_auto_approve_item_seqs,
        )
        if hop_provider:
            selected = next(
                (provider for provider in chain if provider.get("id") == hop_provider),
                None,
            )
            if selected is not None:
                chain = [selected]
                selected_provider_source = "document_type"

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

    # Capability is checked only after the final provider resolution tier is known and before
    # any lease, token, run record, or worker side effect. This is the server authority: UI
    # badges are advisory and a caller cannot supply its own capability map.
    from modules.flow_gate.services.provider_capability_service import capability_finding
    doc_type = (db_docs.get_by_id(doc_ref) or {}).get("type")
    capability_warning = capability_finding(doc_ref, doc_type, chain[0])
    if capability_warning is not None:
        detail = {
            "code": (
                "provider_capability_restricted"
                if mode == "continuous" else "provider_capability_confirmation_required"
            ),
            "message": "The selected provider cannot modify source or run tests.",
            **capability_warning,
            "provider_resolution": (
                "override" if step_override_provider else
                "stored_step" if stored_provider_active else
                "pinned" if provider_pinned and provider_id else
                "selected" if provider_id else "effective_default"
            ),
        }
        # Continuous execution is never forceable. A single run accepts only literal True
        # on this request; no acknowledgement survives to a later run or hop.
        if mode == "continuous" or capability_warning_ack is not True:
            raise HTTPException(status_code=422, detail=detail)

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

    if document_review_loop is not None and issue_builder is not None:
        # 0417 T0013: tell the (possibly stage-aware) issue_builder which stage this hop is —
        # a loop that starts_with_rework must mint an edit-scoped token on its very first hop,
        # not a review-scoped one. See the matching comment on ai_invoke_routes._issue_review.
        issue_builder.loop_stage = initial_stage
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
    if document_review_loop is not None:
        document_review_loop.update({
            "round_no": 1,
            "current_stage": (REWORK_HOP_KIND if document_review_loop["starts_with_rework"] else REVIEW_HOP_KIND),
            "stop_reason": None,
            "stop_detail": None,
            "attempts_used": 0,
            "started_at": started_at,
            "deadline_at": _deadline_iso(started_at, int(document_review_loop["total_timeout_sec"])),
        })
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
        # 0505 T0006 (DB0005 2/3.3): operator_api_base is a one-time sanitized snapshot
        # of this same value, taken here at run start. transport_api_base starts empty
        # -- it is filled once, by whichever of the six mediated self-HTTP calls opens
        # first inside THIS hop (ai_invoke_part2_worker._sanitize_diagnostic_base).
        "operator_api_base": _sanitize_diagnostic_base(api_base_url),
        "transport_api_base": None,
        "last_tool_name": None,
        "last_tool_status": None,
        "last_tool_error": None,
        "api_turns_used": None,
        "model_http_calls": 0,
        "model_last_http_status": None,
        "tool_calls_received": 0,
        "tool_calls_executed": 0,
        "chain_source": chain_source,
        "selected_provider_source": selected_provider_source,
        "fallback_allowed": selected_provider_source == "project_default",
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
                or _review_hop_recovery_open(mode, action_scope, scope_oracle_run, hop_kind)
            )
            else None
        ),
        "continuation_selected_provider_name": (
            chain[0].get("name")
            if chain and (
                mode == "continuous"
                or _scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
                or _review_hop_recovery_open(mode, action_scope, scope_oracle_run, hop_kind)
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
        "document_review_loop": document_review_loop,
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
        # flowgate.default.0476 T0012 / CH0011: an engine-spawned review hop follows the
        # same restart pick as work and rework. With no pick, the resolver still returns 2,
        # preserving 0466 T0007's first-attempt-plus-one-retry default. A pick of -1 remains
        # bounded by the retry budget/recheck guards; a pick of 0 makes the hop one-shot.
        "attempts_max": (
            _resolve_restart_max_attempts(continuation_restart_max_attempts)
            if mode == "continuous"
            or _scope_oracle_retry_open(mode, action_scope, scope_oracle_run)
            or _review_hop_recovery_open(mode, action_scope, scope_oracle_run, hop_kind)
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
    _note_issued_raw_token(run, run.get("raw_token"))
    _note_issued_prompt(run, mention)
    with _runs_lock:
        # The durable loop row is created before the worker can finish; a successful start never
        # exposes memory-only loop state. Roll admission back if persistence fails.
        if document_review_loop is not None:
            try:
                _insert_document_review_loop(run)
            except Exception:
                db_group_ai_leases.release(group_id, run_id)
                try:
                    token_service.revoke(issue["token_id"], reason="document_review_loop_persist_failed")
                except Exception:
                    logger.warning("review-loop token rollback failed for %s", run_id, exc_info=True)
                raise _http_error(500, "document_review_loop_persist_failed", "Could not persist document review loop state.")
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
        "selected_provider_source": run["selected_provider_source"],
        "fallback_allowed": run["fallback_allowed"],
        "warnings": [capability_warning] if capability_warning is not None else [],
        "attempt_no": 1,
        "started_at": run["started_at"],
        # 0359 P0006 [hop budget]: the budget and its wall-clock deadline travel with every
        # start / status / finish payload, so nobody has to reconstruct them from logs again.
        "timeout_sec": run["timeout_sec"],
        "deadline_at": run["deadline_at"],
        "document_review_loop": document_review_loop_payload(run),
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

_AUTH_HEADER_RE = re.compile(r"(?i)authorization\s*:\s*[^\s,;]+(?:\s+[^\s,;]+)?")
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}")
# rev5: below this length, a shared prefix between the prompt and the raw output is too
# likely to be ordinary shared prose ("the ", "please ") rather than a genuine echo — the
# same floor `_BEARER_TOKEN_RE` uses for a bare token.
_PROMPT_ECHO_MIN_LEN = 40

def _redact_secrets(text: Optional[str], known_tokens: Optional[Iterable[str]] = None,
                    known_prompts: Optional[Iterable[str]] = None) -> Optional[str]:
    """flowgate.default.0466 T0007 §3.2.3: `stderr_tail`/`stdout_tail` are the provider
    process's raw, unfiltered output — unlike `last_message`, nothing upstream of this
    scrubs them. A provider that echoes its own outgoing `Authorization: Bearer ...` call
    (or any bare `Bearer <token>` fragment) on failure must not have that value ride
    verbatim into `stop_last_message_excerpt`, which `active_all`/the miniplayer show to
    every user with chain visibility, not just whoever owns the credential.

    rev4 (review finding): the two regexes above only catch a token wearing an
    `Authorization:`/`Bearer ` label. The run's own raw task token never wears one — it
    reaches the provider process unlabeled, as the `FLOWGATE_TOKEN` env var (L3477) — so a
    process that dumps its own environment or echoes that value bare on failure slips past
    both patterns untouched. `known_tokens` is the run's actual raw token(s) (this attempt's
    current one plus every earlier attempt's, from `_note_issued_raw_token`); each is erased
    by exact literal match, independent of any label.

    rev5 (review finding): none of the above touches the PROMPT itself — T0007 §3.2.3 bans
    that separately from a credential ("토큰·Authorization·전체 프롬프트·무제한 stdout을
    노출하지 마라"). A CLI worker that rejects its own stdin ("invalid input, got: <prompt>"
    is the shape a parse error takes) echoes the exact text this run wrote to the child's
    stdin — `run["mention"]`, set at issue time (L1843) and again on every reissued retry
    (L3048) — into `last_message`/`stderr_tail`/`stdout_tail` untouched by either regex or
    `known_tokens`, and a SHORT prompt fits whole inside `LAST_MESSAGE_EXCERPT_BYTES`, so
    `excerpt()` alone never trims it away. `known_prompts` is every prompt text this run has
    written to a provider's stdin — the current attempt's and every earlier (possibly
    already-rotated) attempt's, from `_note_issued_prompt`, the same rotation problem
    `known_tokens` already solves. Each is erased like a known token when it appears whole.
    Stdin was written to the child exactly once, front to back, so a provider that only
    echoes back part of what it read (its own output got cut off first) still echoes a
    PREFIX of the prompt, never an arbitrary interior slice — the loop below binary-searches
    for the longest such prefix still present verbatim (each shorter prefix of a present
    prefix is also present, so "is prompt[:k] in text" is monotonic and halves correctly)
    and erases that instead.

    rev6 (review finding): the prompt search MUST run against the ORIGINAL text, before the
    header/token substitutions below ever touch it — an issued FlowGate prompt routinely
    embeds this run's own `Authorization: Bearer <token>`/raw-token material as task context,
    and a provider that echoes it back echoes it unmodified. Doing header/token redaction
    first (the rev5 order) rewrites that embedded credential in-place, so no prefix of the
    ORIGINAL prompt beyond the credential can still be found verbatim in the mutated string —
    the binary search then tops out at the substring before the credential, and the
    confidential suffix that followed `Bearer <token>` in the prompt survives untouched. Doing
    the prompt search/removal FIRST sidesteps this entirely: the match is against the
    unmutated text, so whatever prefix truly echoed — credential and all — is found and
    erased as one block, before there is anything left for the header/token regexes to
    rewrite out from under it. Header/token redaction still runs afterward, unchanged, to
    catch credential material that appears outside of what got echoed as prompt.

    rev7 (review finding): `known_prompts` is a SET, and retry/reissue tracking (see
    `_note_issued_prompt`) deliberately keeps every earlier attempt's prompt around alongside
    the current one — so two retained prompts can legitimately overlap, one a literal prefix
    of the other (a reissued retry commonly reuses the whole prior prompt text and appends
    more). Iterating a set in whatever order Python happens to hash it into is not a security
    boundary. If the SHORTER prompt P is processed before the longer P+S and the provider
    echoed the full P+S, the binary search against P finds all of P present and
    `redacted.replace(prompt[:match_len], ...)` erases exactly that P-length span — leaving
    the confidential suffix S sitting right after it in the output. Worse, P+S's own turn
    then finds nothing: the P-prefix of P+S it binary-searches for no longer exists verbatim
    in `redacted` (that span was just replaced), so its match tops out at 0 and it erases
    nothing. Processing LONGEST prompts first closes this: a longer retained prompt is
    searched and erased as one whole block before any shorter prompt that happens to be one
    of its prefixes gets a turn, so the shorter prompt's own search — now run against text
    that already has that whole span replaced — correctly finds nothing left to do instead of
    carving out a partial, suffix-leaking match.
    """
    if not text:
        return text
    redacted = text
    for prompt in sorted((p for p in known_prompts or () if p), key=len, reverse=True):
        if len(prompt) < _PROMPT_ECHO_MIN_LEN:
            continue
        lo, hi, match_len = _PROMPT_ECHO_MIN_LEN, len(prompt), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if prompt[:mid] in redacted:
                match_len = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if match_len >= _PROMPT_ECHO_MIN_LEN:
            redacted = redacted.replace(prompt[:match_len], "[redacted prompt]")
    redacted = _AUTH_HEADER_RE.sub("Authorization: [redacted]", redacted)
    redacted = _BEARER_TOKEN_RE.sub("Bearer [redacted]", redacted)
    for token in known_tokens or ():
        if token and len(token) >= 6:
            redacted = redacted.replace(token, "[redacted]")
    return redacted

def _note_issued_raw_token(run: dict, token: Optional[str]) -> None:
    """Remember every raw task token this run has handed a provider process (T0007 rev4
    §3.2.3), so a later `_redact_secrets` call can scrub an unlabeled echo of it.

    Deliberately NOT one of the explicit fields `_persist_run_record`/`finished_payload`
    copy out of `run` — both build their own whitelisted dicts rather than serializing
    `run` wholesale, so this stays process-memory-only and is never persisted or broadcast.
    """
    if not token:
        return
    run.setdefault("_issued_raw_tokens", set()).add(token)

def _known_run_raw_tokens(run: dict) -> set[str]:
    return {t for t in run.get("_issued_raw_tokens") or () if t}

def _note_issued_prompt(run: dict, mention: Optional[str]) -> None:
    """Remember every prompt text this run has handed a provider process's stdin — the
    initial one and each reissued retry's (T0007 rev5 §3.2.3), mirroring
    `_note_issued_raw_token`. A rotated-out EARLIER attempt's own prompt can still be the
    thing echoed into ITS archived `fallback_history` detail, so redacting only the
    CURRENT `run["mention"]` (which a reissue overwrites in place, L3048) would miss it —
    same rotation problem the raw-token set already solves. Process-memory-only, same as
    the token set: never one of `_persist_run_record`/`finished_payload`'s explicit fields.
    """
    if not mention:
        return
    run.setdefault("_issued_prompts", set()).add(mention)

def _known_run_prompts(run: dict) -> set[str]:
    return {p for p in run.get("_issued_prompts") or () if p}

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

# ── Part loading (see the file-split note in the module docstring) ────────────
_PART_FILES = (
    "ai_invoke_part2_worker.py",
    "ai_invoke_part3_chain.py",
)

def _load_parts() -> None:
    """Execute each part file in THIS module's globals(), in order."""
    from importlib.machinery import SourceFileLoader
    here = Path(__file__).resolve().parent
    for filename in _PART_FILES:
        fullname = "%s.%s" % (__name__, filename[:-3])
        code = SourceFileLoader(fullname, str(here / filename)).get_code(fullname)
        exec(code, globals())

_load_parts()
