"""AI invoke engine (flowgate.default.0187 D0004 / P0005 / L0006).

First real consumer of the 0164 AI-provider settings: starts an AI run for a
document, walks the default provider chain (list order = fallback order, startup/
transport failures only), watches it with the process_runner primitives
(process-group spawn, tree kill, timeout), and judges success by the
document-reach oracle — "did documents actually land in the group past the
baseline seq" — never by exit codes. Exit code / last (dying) message are
recorded as auxiliary observations only, and document-reach vs message-receipt
are independent columns (a killed run keeps its already-registered docs).

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

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import git_service, process_runner, token_service
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


def _http_error(status_code: int, code: str, message: str, **payload) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **payload})


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
) -> Optional[int]:
    """docs_target in the workflow item_seq coordinate system (0226 B0001 / NR0003 §5-1).

    ``continuation_target_seq`` lives in the workflow-sequence item_seq space, which is
    unrelated to the group document seq space (item_seq turns sparse after
    edit_workflow_pending renumbers the pending tail past max_item_seq). The former
    ``target - get_group_max_seq()`` subtraction mixed the two spaces, yielding
    arbitrary targets (the reported 0/9 and 4/3). Count instead the sequence items up
    to the target that will land as worker-visible documents: instruction heads (N/T,
    INSTRUCTION_AUTO_TYPES) are auto-created server-side as drafts, which the
    document-reach oracle never counts, so they are excluded here symmetrically.

    ``pending_only=True`` counts only unrealized slots (start-of-run admission).
    The to-end resolution paths pass False: the whole freshly-decided sequence is the
    run's scope regardless of what has been realized by the time of the query.
    ``target_item_seq=None`` means "no upper bound" (to-end).
    Returns None when the doc has no decided workflow sequence.
    """
    from modules.flow_gate.services.workflow_decision_service import INSTRUCTION_AUTO_TYPES

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
        if (item.get("type") or "").upper() in INSTRUCTION_AUTO_TYPES:
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
) -> dict:
    """Admit and launch a run. mention_builder(raw_token, scratch_dir) builds the
    worker mention through the exact token_routes path so the prompt the AI reads
    is byte-identical to the copy-mention flow (the raw token never leaves the
    server — it is consumed only as the run's FLOWGATE_TOKEN env)."""
    effective = ai_settings_service.resolve_effective(project_id)
    chain = effective.get("providers") or []
    chain_source = effective.get("source")
    if provider_id:
        selected = next((provider for provider in chain if provider.get("id") == provider_id), None)
        if selected is None:
            raise _http_error(
                422, "provider_unavailable",
                "The selected AI provider is not enabled for this project.",
            )
        # An explicit UI selection pins the run. Fallback order only applies when no provider was specified.
        chain = [selected]
    if not chain:
        raise _http_error(
            409, "no_enabled_provider",
            "No enabled AI provider for this project. Configure providers in AI settings.",
        )

    active = _active_run_for_group(group_id)
    if active is not None:
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id=active["run_id"])

    baseline_seq = db_docs.get_group_max_seq(group_id)
    target_to_end = mode == "continuous" and continuation_target_seq == -1
    if action_scope in ("workflow_decide", "resolve_conflict"):
        docs_target = 0
    elif mode == "single":
        docs_target = 1
    else:
        # 0226 B0001 / NR0003 §5-1: the target is a workflow item_seq, never a group
        # document seq — derive docs_target from the sequence's pending worker items.
        target = int(continuation_target_seq or 0)
        resolved_target = _continuation_docs_target(doc_ref, target)
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

    _cleanup_retained_scratches(project_id)
    run_id = _next_run_id()
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
        "raw_token": issue["raw_token"],
        "merge_id": merge_id,
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
        if not started_ok and not run["cancel_event"].is_set():
            run["end_reason"] = "all_providers_failed"
        elif run["cancel_event"].is_set():
            run["end_reason"] = "cancelled"
        elif run["timed_out"]:
            run["end_reason"] = "timeout"
        else:
            run["end_reason"] = "exited"

        _settle_and_judge(run)
    except Exception:
        logger.exception("ai-invoke worker crashed for %s", run["run_id"])
        run["end_reason"] = run.get("end_reason") or "exited"
        try:
            _settle_and_judge(run)
        except Exception:
            logger.exception("ai-invoke settle failed for %s", run["run_id"])
            run["status"] = "finished"


def _remaining_sec(run: dict) -> float:
    return run["timeout_sec"] - (time.monotonic() - run["started_mono"])


def _new_docs_registered(run: dict) -> bool:
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
    scratch = Path(run["scratch_dir"])
    last_message_file = scratch / "last_message.txt"
    if kind == "codex":
        cmd = f'{cmd} --output-last-message "{last_message_file}"'

    source_root = Path(run["source_root"]) if run.get("source_root") else scratch
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
    kwargs = process_runner.popen_kwargs(source_root, env)
    kwargs["stdin"] = subprocess.PIPE

    launched = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, **kwargs)
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
        if elapsed < FAST_FAIL_WINDOW_SEC and not _new_docs_registered(run):
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
        and not _new_docs_registered(run)
    ):
        detail = (err_text or out_text).strip()[-500:] or f"exit {proc.returncode} within {int(elapsed)}s"
        return "fast_fail", detail

    _recover_cli_last_message(run, kind, out_text, last_message_file)
    return "started_ok", None


def _recover_cli_last_message(run: dict, kind: str, stdout_text: str, last_message_file: Path) -> None:
    """Per-kind last-message recovery (hive providers.py rule table, rules only):
    claude = full stdout trimmed / codex = --output-last-message file /
    copilot & custom = last non-blank block of the stdout tail."""
    message: Optional[str] = None
    if kind == "claude":
        message = stdout_text.strip() or None
    elif kind == "codex":
        try:
            if last_message_file.is_file():
                message = last_message_file.read_text(encoding="utf-8", errors="replace").strip() or None
        except Exception:
            message = None
    else:
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
                        run["doc_ref"], resolved_target, pending_only=False
                    )
                    if resolved is not None:
                        run["docs_target"] = resolved
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
                    run["doc_ref"], None, pending_only=False
                )
                if resolved is not None:
                    run["docs_target"] = resolved
        except Exception:
            logger.warning("ai-invoke workflow oracle failed for %s", run["run_id"], exc_info=True)

    # 0226 NR0003 §5-2: no min() clamp — an overrun (more docs than the target) stays
    # visible in docs_reached/docs_target instead of being normalized away at the end.
    docs_reached = len(new_docs)
    run["docs_reached"] = docs_reached
    run["reached_doc_ids"] = [d["doc_id"] for d in new_docs]
    if run.get("action_scope") == "workflow_decide" and run["mode"] == "single":
        run["outcome"] = "complete" if workflow_decided else "none"
    elif run.get("action_scope") == "workflow_decide" and not workflow_decided:
        # Pre-decision continuous run that never decided: no resolved target to satisfy.
        run["outcome"] = "partial" if docs_reached >= 1 else "none"
    elif docs_reached >= run["docs_target"]:
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
    try:
        docs_so_far = len(_oracle_new_docs(run))
    except Exception:
        pass
    return {
        "ok": True,
        "run_id": run_id,
        "status": run["status"],
        "mode": run["mode"],
        "group_id": run["group_id"],
        "docs_target": run["docs_target"],
        "docs_reached_so_far": docs_so_far,
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
