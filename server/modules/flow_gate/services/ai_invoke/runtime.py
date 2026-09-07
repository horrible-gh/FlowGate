"""AI invoke shared state + parameters (0501 NR0003 §12/§13 `runtime.py`).

The bottom of the package's dependency graph: this module imports nothing else from
`ai_invoke/`, and every other module in the package may import it. It owns

  * the engine's parameters -- timeouts, retry budgets, stop codes, notification sets,
    the API tool schemas -- which used to be `ai_invoke_service.py`'s constants block;
  * the in-memory run registry (`_runs` / `_runs_lock` / the run-id counter /
    `_group_resume_locks` / `_auto_resume`) and the accessors that read it;
  * the per-run scratch directory (create / validate / mark complete / delete /
    retention sweep), which is run-scoped mutable state on disk;
  * the per-run bookkeeping of issued tokens and prompts, plus the text primitives
    (`excerpt`, `_redact_secrets`, `prompt_digest`) that bookkeeping is expressed in.

Deliberately NOT here (NR0003 §13): how a run is admitted, executed, judged, finalized
or continued. This module answers "what does this process currently hold?", nothing
about what to do with it.

── Why the registry is still read through the shim ────────────────────────────────────
The mutable registry objects are DEFINED here, but roughly thirty existing tests reset
them with a whole-object `monkeypatch.setattr(<ai_invoke_service alias>, "_runs", {...})`
reassignment. A plain module-global read here would keep pointing at the pre-patch dict
and silently stop observing the test's registry (confirmed by an actual failure when a
plain alias was tried in 0501 T3). Every accessor below therefore reaches the CURRENT
attribute on the compatibility shim at call time -- `_svc()._runs` -- which is the same
object this module created until a test replaces it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from fastapi import HTTPException
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.settings import ai_execution_policy_service
from modules.flow_gate.storage import paths as storage_paths

# The engine's log channel keeps its historical name: `caplog`/handler
# configuration and every existing log line in this subsystem were written
# against `modules.flow_gate.services.ai_invoke_service`, and a package move
# is not a reason to renumber them. Every module in the package imports this
# one logger rather than making its own.
logger = logging.getLogger("modules.flow_gate.services.ai_invoke_service")


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
# above. -1 is the "될 때까지" sentinel (unlimited attempts); 0..N (N = the configured
# ceiling, flowgate.default.0490 T0005) are restart counts (RESTARTS, not total attempts —
# total = restarts + 1). Default matches the constant's pre-existing behavior exactly:
# 1 restart == NO_OUTPUT_MAX_ATTEMPTS(2) total attempts.
#
# flowgate.default.0501 T0019: 0490 T0005 turned the fixed tuple into this function in
# ai_invoke_service.py's parameter block. That block IS runtime.py now, so the function
# lands here — same body, same SSOT call, one address further in.
def restart_max_attempts_choices() -> tuple[int, ...]:
    """The dialog's selectable "재시작 횟수" set. SSOT is ai_execution_policy_service
    (flowgate.default.0490 T0005) — this used to be the fixed tuple (-1, 0, 1, 2, 3)."""
    return ai_execution_policy_service.repeat_count_choices(allow_zero=True)

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
# flowgate.default.0490 T0005 retired the frozen REVIEW_COUNT_VALUES literal that stood
# here: resolve_review_count reads the same live SSOT the write path validates against
# (ai_execution_policy_service.repeat_count_choices), so there is no second ceiling to
# drift away from the setting.
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

_SEQUENCE_EDIT_TOOL_NAME = "save_workflow_sequence"

_SEQUENCE_EDIT_TOOL_DESC = (
    "Save the complete editable workflow sequence for the bound document. Return all five "
    "provenance values (note, source_doc_id, source_revision_no, provider_id, "
    "provider_display_name) unchanged for untouched instruction/general rows; clear all five "
    "for new rows or rows whose type changed. Omit deleted rows and NR/TR/TSR rows."
)

_SEQUENCE_EDIT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "label": {"type": "string"},
                    "note": {"type": ["string", "null"]},
                    "source_doc_id": {"type": ["string", "null"]},
                    "source_revision_no": {"type": ["integer", "null"]},
                    "provider_id": {"type": ["string", "null"]},
                    "provider_display_name": {"type": ["string", "null"]},
                },
                "required": ["type", "label"],
            },
        },
        "force_encoding_reason": {"type": ["string", "null"]},
        "expected_workflow_tag": {"type": ["string", "null"]},
        "expected_plan": {"type": ["object", "null"]},
    },
    "required": ["items"],
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


def _http_error(status_code: int, code: str, message: str, **payload) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **payload})


def _next_run_id() -> str:
    """Mint this process's next run id, floored against today's durable serials.

    The counter and its floor date are read and written through the compatibility shim
    rather than as this module's own globals, for the same reason the registry is (see
    the module docstring): `test_ai_invoke_lease_recovery_0401.py` resets both with a
    plain `svc._run_counter = 0` / `svc._run_counter_floor_date = None` between cases,
    and a module-global here would not observe that reset -- the counter would keep
    climbing across cases and the floored id would be whatever the previous one left
    behind. A `global` statement cannot follow a name the way the rest of the seam does,
    so this one function spells the indirection out.
    """
    svc = _svc()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    with _runs_lock:
        if svc._run_counter_floor_date != date_str:
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
                svc._run_counter = max(svc._run_counter, floor)
            except Exception:
                logger.warning("run_id floor lookup failed for %s", date_str, exc_info=True)
            svc._run_counter_floor_date = date_str
        svc._run_counter += 1
        return f"aiv_{date_str}_{svc._run_counter:06d}"


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
        if _svc()._is_reparse_or_symlink(scratch) or scratch.resolve(strict=True).parent != root_resolved:
            raise ValueError("scratch is not a direct managed child")
        (scratch / "tmp").mkdir()
        (scratch / "cache").mkdir()
        _atomic_write_manifest(scratch, _manifest_for(project_id, run_id, scratch))
    except Exception:
        if scratch.exists() and not _svc()._is_reparse_or_symlink(scratch):
            shutil.rmtree(scratch)
        raise
    _safe_scratch_log(project_id, run_id, scratch, "created", "manifest_written")
    return scratch


def _validate_scratch_manifest(project_id: str, run_id: str, scratch: Path) -> tuple[Optional[dict], str]:
    if not _RUN_ID_RE.fullmatch(str(run_id)):
        return None, "invalid_run_id"
    try:
        root = _project_scratch_root(project_id).resolve(strict=True)
        if _svc()._is_reparse_or_symlink(scratch):
            return None, "reparse_or_symlink"
        resolved = scratch.resolve(strict=True)
        if resolved == root or resolved.parent != root or scratch.parent.resolve(strict=True) != root:
            return None, "outside_or_nested"
        if not resolved.is_dir() or resolved.name != run_id:
            return None, "path_identity_mismatch"
        manifest_path = resolved / SCRATCH_MANIFEST_NAME
        if _svc()._is_reparse_or_symlink(manifest_path) or not manifest_path.is_file():
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
        if not root.is_dir() or _svc()._is_reparse_or_symlink(root):
            return
        now = datetime.now(timezone.utc)
        for child in root.iterdir():
            run_id = child.name
            if not child.is_dir() or _svc()._is_reparse_or_symlink(child):
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
            deleted, delete_reason = _svc()._delete_owned_scratch(project_id, run_id, child)
            if not deleted:
                _safe_scratch_log(project_id, run_id, child, "retained", delete_reason)
    except Exception:
        logger.warning("ai-invoke scratch sweep failed")


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


def _svc():
    """The `ai_invoke_service` compatibility shim, resolved at CALL time.

    The package's one compatibility seam, defined here and imported by every module
    that needs it, so there is a single place that explains it.

    WHY a lookup and not an import: every `monkeypatch.setattr(<any alias of
    ai_invoke_service>, "<name>", fake)` in the existing suite replaces the attribute on
    THAT module object, so a seam has to be read from it at the moment of use, not bound
    once at import time. Roughly thirty tests reset the run registry the same way, with
    a whole-object `svc._runs = {...}` reassignment a bound name would never see.

    WHY at call time and not a module-level `import ai_invoke_service as svc`: that
    import would put the shim back into this package's own graph and make the direction
    cyclic -- `ai_invoke_service` -> `facade` -> every module here -> `ai_invoke_service`
    -- which breaks the moment a fresh interpreter enters at `facade`. Deferring it keeps
    the package's imports strictly one-way (0501 NR0003 §28) while leaving the seam
    working byte for byte. It is the pattern the pre-package `ai_invoke_runtime.py`
    already used for exactly this reason.
    """
    from modules.flow_gate.services import ai_invoke_service
    return ai_invoke_service


def _absolute_cap_sec() -> int:
    """The run's hard ceiling, in seconds (0446 T0014 §2-4).

    Deliberately RUN_TIMEOUT_CAP_SEC itself, read at CALL time instead of copied into
    a second four-hour literal: that constant is already four hours, already the roof
    of the per-document formula and already what a `target_to_end` run gets, so a
    duplicate could only drift away from it. Reading it through a function is also
    what keeps `test_ai_invoke_0187.py::TestForcedKill` honest — it shortens the cap to
    one second to prove the timeout path, and a bound-at-import alias would have
    silently ignored it.

    T6 moved this off `worker.py` onto `runtime.py`: `admission.py`, `finalize.py` and
    `provider_cli.py` all need the same ceiling, and none of them may import `worker`
    without closing a cycle back through `chain`/`finalize`.
    """
    return _svc().RUN_TIMEOUT_CAP_SEC


def _absolute_remaining_sec(run: dict) -> float:
    """Seconds left before the run's hard ceiling (0446 T0014 §2-4).

    Measured from `started_mono` — the HOP's start, not the attempt's — so a no-output
    retry inherits what is left of the four hours instead of being handed a fresh four.
    """
    return _absolute_cap_sec() - (_svc()._now_mono() - run["started_mono"])


def group_resume_lock(group_id: str) -> threading.Lock:
    svc = _svc()
    with svc._group_resume_locks_guard:
        lock = svc._group_resume_locks.get(group_id)
        if lock is None:
            lock = svc._group_resume_locks[group_id] = threading.Lock()
        return lock


def active_run_for_group(group_id: str) -> Optional[dict]:
    svc = _svc()
    with svc._runs_lock:
        for run in svc._runs.values():
            if run["group_id"] == group_id and run["status"] != "finished":
                return run
    return None


def get_run_record(run_id: str) -> Optional[dict]:
    svc = _svc()
    with svc._runs_lock:
        return svc._runs.get(run_id)


def is_run_live(run_id: str) -> bool:
    """Is *run_id* an admission this process still tracks and has not finished?

    The one definition of "alive" shared by the lease-owner mutation gate
    (``mutation_policy._locked``), the manual lease-release guard, and the
    ``GET /ai-invoke/leases`` route — a duplicated check here is exactly how the
    screen and the server told two different stories before (0401 NR0003 §3 cause 3).
    """
    run = get_run_record(run_id)
    return run is not None and run.get("status") != "finished"


def list_live_runs(*, group_id: Optional[str] = None, project_id: Optional[str] = None) -> list[dict]:
    """In-memory runs still going, scoped to exactly one of group/project (caller's
    choice) — the "live" half of GET /ai-invoke/runs (L0007 §2.10.3)."""
    svc = _svc()
    with svc._runs_lock:
        snapshot = list(svc._runs.values())
    items = []
    for run in snapshot:
        if run["status"] == "finished":
            continue
        if group_id is not None and run["group_id"] != group_id:
            continue
        if project_id is not None and run["project_id"] != project_id:
            continue
        items.append(run_list_item_live(run))
    return items


def run_list_item_live(run: dict) -> dict:
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
