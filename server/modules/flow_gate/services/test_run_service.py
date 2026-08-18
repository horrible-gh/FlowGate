"""Remote TS test execution service."""
from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from modules.flow_gate import process_service
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.numbering import id_formatter, numbering_service
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import (
    token_service,
    test_command_service,
    engine_recipe_service,
    process_runner,
)
from modules.flow_gate.storage import paths as storage_paths

logger = logging.getLogger(__name__)

CASE_TIMEOUT_SEC = 600
RUN_TIMEOUT_SEC = 3600
OUTPUT_TAIL_CHARS = 4000
MAX_CASES_PER_TS = 50
RUNNER_POLL_SEC = 5
CASE_EXIT_PASS = 0
SETUP_STEP_TIMEOUT_SEC = 600
TEARDOWN_STEP_TIMEOUT_SEC = 600
# flowgate.default.0358 T0004: once a cancel is accepted, teardown still runs
# best-effort (it may undo setup side effects) but must not hold the run in
# 'cancelling' for the full normal budget — that would defeat the "cancel immediately" promise.
CANCEL_TEARDOWN_STEP_TIMEOUT_SEC = 30
CANCEL_TEARDOWN_BUDGET_SEC = 60
WAIT_TIMEOUT_SEC = 60
WAIT_POLL_SEC = 0.5
MAX_SETUP_STEPS = 20
MAX_TEARDOWN_STEPS = 20
MAX_SERVICES = 5
TSR_CASE_EXCERPT_CHARS = 1000

_admission_lock = threading.Lock()


class _ActiveRun:
    """In-memory handle for a run currently executing (flowgate.default.0358 T0004).

    Nothing outside this module reaches into these fields directly; process_runner's
    kill_process_tree is always called through the module helpers below so cancel and
    the executing worker never race on ``proc``/``service_procs`` without the per-entry
    lock held.
    """

    __slots__ = ("run_id", "cancel_event", "lock", "proc", "service_procs")

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.proc: Optional[subprocess.Popen] = None
        self.service_procs: set = set()


_active_runs: dict[str, _ActiveRun] = {}
_active_runs_meta_lock = threading.Lock()

# Per-run_id lock serializing "decide the terminal status, then write it" between a
# cancel request and the worker's own natural-completion commit (0358 T0004 risk 2).
# FlowGateStore._execute reports no affected-row count, so a bare
# UPDATE ... WHERE status=? cannot tell a CAS winner from a loser on its own — this
# lock is what makes the DB CAS calls in this module actually exclusive, the same
# idiom numbering_service._get_lock uses for document numbering.
_run_locks: dict[str, threading.Lock] = {}
_run_locks_meta_lock = threading.Lock()


def _get_run_lock(run_id: str) -> threading.Lock:
    with _run_locks_meta_lock:
        lock = _run_locks.get(run_id)
        if lock is None:
            lock = threading.Lock()
            _run_locks[run_id] = lock
        return lock


def _register_active_run(run_id: str) -> _ActiveRun:
    """Get-or-create so a cancel arriving before the worker registers (and vice
    versa) both land on the same entry instead of one clobbering the other."""
    with _active_runs_meta_lock:
        entry = _active_runs.get(run_id)
        if entry is None:
            entry = _ActiveRun(run_id)
            _active_runs[run_id] = entry
        return entry


def _unregister_active_run(run_id: str) -> None:
    with _active_runs_meta_lock:
        _active_runs.pop(run_id, None)


def _get_active_run(run_id: str) -> Optional[_ActiveRun]:
    with _active_runs_meta_lock:
        return _active_runs.get(run_id)


def _set_current_proc(active: _ActiveRun, proc: subprocess.Popen) -> None:
    """Attach the just-spawned proc as the one a cancel should kill.

    If cancel already fired before this call (registration-vs-cancel race, T item 1),
    kill the proc we just created immediately instead of leaving it live and untracked.
    """
    with active.lock:
        if active.cancel_event.is_set():
            should_kill = True
        else:
            active.proc = proc
            should_kill = False
    if should_kill:
        _kill_process_tree(proc)


def _clear_current_proc(active: _ActiveRun, proc: subprocess.Popen) -> None:
    with active.lock:
        if active.proc is proc:
            active.proc = None


def _add_service_proc(active: _ActiveRun, proc: subprocess.Popen) -> None:
    with active.lock:
        if active.cancel_event.is_set():
            should_kill = True
        else:
            active.service_procs.add(proc)
            should_kill = False
    if should_kill:
        _kill_process_tree(proc)


def _remove_service_proc(active: _ActiveRun, proc: subprocess.Popen) -> None:
    with active.lock:
        active.service_procs.discard(proc)


def is_cancel_requested(run_id: str) -> bool:
    entry = _get_active_run(run_id)
    return bool(entry and entry.cancel_event.is_set())


class TestCaseParseError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# T0009: English aliases for the TS grammar. The parser accepts both names below and
# normalises field names through the aliases, so downstream logic keeps using the Korean canonical keys.
TEST_CASES_SECTION_NAMES = ("테스트 케이스", "Test Cases")
SETUP_SECTION_NAMES = ("테스트 준비", "Setup")
TEARDOWN_SECTION_NAMES = ("테스트 정리", "Teardown")

_FIELD_ALIASES = {
    "expect": "기대",
    "expected": "기대",
    "start": "기동",
    "wait": "대기",
}
_DISPLAY_FIELD = {"기대": "expect", "기동": "start", "대기": "wait"}


def _normalize_field(field: str) -> str:
    return _FIELD_ALIASES.get(field.strip().lower(), field.strip())


def parse_test_cases(content: str) -> list[dict]:
    lines = (content or "").splitlines()
    headings = {f"## {name}" for name in TEST_CASES_SECTION_NAMES}
    section_start: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.strip() in headings:
            section_start = idx + 1
            break
    if section_start is None:
        raise TestCaseParseError(
            "no_test_cases",
            "No '## Test Cases' section or zero valid case blocks.",
        )

    section: list[str] = []
    for line in lines[section_start:]:
        if line.startswith("## ") and not line.startswith("### "):
            break
        section.append(line)

    blocks: list[tuple[str, list[str]]] = []
    title: Optional[str] = None
    body: list[str] = []
    for line in section:
        if line.startswith("### "):
            if title is not None:
                blocks.append((title, body))
            title = line[4:].strip()
            body = []
        elif title is not None:
            body.append(line)
    if title is not None:
        blocks.append((title, body))

    if not blocks:
        raise TestCaseParseError(
            "no_test_cases",
            "No '## Test Cases' section or zero valid case blocks.",
        )
    if len(blocks) > MAX_CASES_PER_TS:
        raise TestCaseParseError(
            "invalid_case_block", f"case count exceeds {MAX_CASES_PER_TS}"
        )

    cases: list[dict] = []
    seen: set[str] = set()
    heading_re = re.compile(r"^(TC-\d+):\s*(.+)$")
    field_re = re.compile(r"^-\s*([^:]+):\s*(.*)$")
    for heading, block_lines in blocks:
        match = heading_re.match(heading)
        if match is None:
            raise TestCaseParseError(
                "invalid_case_block",
                f"{heading}: heading must be '### TC-{{n}}: {{title}}'",
            )
        case_no, case_title = match.group(1), match.group(2).strip()
        if case_no in seen:
            raise TestCaseParseError(
                "invalid_case_block", f"{case_no}: duplicate case number"
            )
        seen.add(case_no)

        fields: dict[str, str] = {}
        for line in block_lines:
            m = field_re.match(line.strip())
            if m:
                fields[_normalize_field(m.group(1))] = m.group(2).strip()
        cmd = _strip_wrapping_backticks(fields.get("cmd", "")).strip()
        expect = fields.get("기대", "").strip()
        if not cmd:
            raise TestCaseParseError(
                "invalid_case_block", f"{case_no}: required field 'cmd' missing"
            )
        if "\n" in cmd or "\r" in cmd:
            raise TestCaseParseError(
                "invalid_case_block", f"{case_no}: cmd must be a single line"
            )
        if not expect:
            raise TestCaseParseError(
                "invalid_case_block", f"{case_no}: required field 'expect' missing"
            )
        cases.append(
            {
                "kind": "case",
                "case_no": case_no,
                "title": case_title,
                "cmd": cmd,
                "expect": expect,
            }
        )
    return cases


def parse_test_plan(content: str) -> dict:
    return {
        "setup": _parse_step_section(
            content,
            SETUP_SECTION_NAMES,
            allowed={"cmd": "setup", "기동": "service", "대기": "wait"},
            prefix="SETUP",
            max_steps=MAX_SETUP_STEPS,
        ),
        "cases": parse_test_cases(content),
        "teardown": _parse_step_section(
            content,
            TEARDOWN_SECTION_NAMES,
            allowed={"cmd": "teardown"},
            prefix="CLEAN",
            max_steps=MAX_TEARDOWN_STEPS,
        ),
    }


def _parse_step_section(
    content: str,
    section_names: tuple[str, ...],
    *,
    allowed: dict[str, str],
    prefix: str,
    max_steps: int,
) -> list[dict]:
    section = _extract_h2_section(content, section_names)
    if section is None:
        return []

    # Error output is locale-neutral English. Parser aliases may remain Korean
    # internally, but must never leak into an en/ja API response.
    section_name = section_names[-1]
    allowed_display = "/".join(_DISPLAY_FIELD.get(k, k) for k in allowed)
    field_re = re.compile(r"^-\s*([^:]+):\s*(.*)$")
    steps: list[dict] = []
    service_count = 0
    for idx, raw in enumerate(section, start=1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("-"):
            continue
        m = field_re.match(line)
        if m is None:
            raise TestCaseParseError(
                "invalid_case_block",
                f"{section_name}: step {len(steps) + 1} has no recognized field "
                f"(allowed: {allowed_display})",
            )
        field = _normalize_field(m.group(1))
        if field not in allowed:
            if section_name == "Teardown":
                detail = "Teardown: only 'cmd' is allowed"
            else:
                detail = (
                    f"{section_name}: step {len(steps) + 1} has no recognized field "
                    f"(allowed: {allowed_display})"
                )
            raise TestCaseParseError("invalid_case_block", detail)
        value = _strip_wrapping_backticks(m.group(2).strip()).strip()
        if not value:
            display_field = _DISPLAY_FIELD.get(field, field)
            raise TestCaseParseError(
                "invalid_case_block",
                f"{section_name}: step {len(steps) + 1} has empty '{display_field}'",
            )
        if "\n" in value or "\r" in value:
            raise TestCaseParseError(
                "invalid_case_block",
                f"{section_name}: step {len(steps) + 1} must be a single line",
            )
        kind = allowed[field]
        if kind == "service":
            service_count += 1
            if service_count > MAX_SERVICES:
                raise TestCaseParseError(
                    "invalid_case_block", f"{section_name}: service count exceeds {MAX_SERVICES}"
                )
        steps.append(
            {
                "kind": kind,
                "case_no": f"{prefix}-{len(steps) + 1}",
                "title": "",
                "cmd": value,
                "expect": "",
            }
        )
        if len(steps) > max_steps:
            raise TestCaseParseError(
                "invalid_case_block", f"{section_name}: step count exceeds {max_steps}"
            )
    return steps


def _extract_h2_section(content: str, section_names: tuple[str, ...]) -> Optional[list[str]]:
    lines = (content or "").splitlines()
    start: Optional[int] = None
    headers = {f"## {name}" for name in section_names}
    for idx, line in enumerate(lines):
        if line.strip() in headers:
            start = idx + 1
            break
    if start is None:
        return None
    section: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") and not line.startswith("### "):
            break
        section.append(line)
    return section


def _strip_wrapping_backticks(value: str) -> str:
    if len(value) >= 2 and value[0] == "`" and value[-1] == "`":
        return value[1:-1]
    return value


def _read_doc_content(doc: dict) -> str:
    path = storage_paths.resolve_storage_path(
        doc.get("file_path") or "",
        doc.get("project_id"),
        branch=(doc.get("branch") or "main"),
    )
    if path is None:
        return ""
    return path.read_text(encoding="utf-8")


def _http_error(status_code: int, code: str, **payload) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": code, **payload})


def validate_and_create_run(
    *,
    doc_id: str,
    runner_id: str,
    triggered_via: str,
) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise _http_error(404, "doc_not_found", doc_id=doc_id)
    if process_service.is_group_disposed(doc.get("group_id")):
        raise _http_error(409, "group_disposed", doc_id=doc_id)
    if (doc.get("type_code") or "").upper() != "TS":
        raise _http_error(
            422,
            "no_test_cases",
            doc_id=doc_id,
            detail="document type is not TS",
        )
    review_status = doc.get("doc_review_status")
    if review_status != "approved":
        # Re-run affordance (0163 / B0001, extended by 0169): an approved TS that has
        # actually run moves to doc_review_status="pending_review" while its result awaits
        # review. If that result is rejected and the author revises the TS, the status becomes
        # "revised" and must remain re-runnable so the fixed command can produce fresh results.
        # A prior run bound to this doc proves approval was already cleared once when the first
        # run was admitted. Every other non-approved state (draft/rejected/…) still 409s. The
        # group_disposed (above) and run_in_progress (below) guards are unaffected.
        rerun_ok = review_status in {"pending_review", "revised"} and bool(
            db_test_runs.list_by_doc(doc_id)
        )
        if not rerun_ok:
            raise _http_error(
                409,
                "doc_not_approved",
                doc_id=doc_id,
                doc_review_status=review_status,
            )

    # Fail fast at admission: without a source mirror the async worker can only
    # die with src_root_missing, which surfaces late and pathless (0152 outage).
    # B0001 (0190): pass group_id so a git-integrated group resolves to its own
    # worktree (the work branch), not base(main). Must match _execute_run_inner's
    # resolution below — if the guard and the worker disagree on the folder, the
    # admission check passes against one tree while tests run in another.
    src_root_path = storage_paths.resolve_project_src_root(
        doc.get("project_id"),
        doc.get("branch") or "main",
        group_id=doc.get("group_id"),
    )
    if src_root_path is None or not src_root_path.is_dir():
        raise _http_error(
            422,
            "src_root_missing",
            doc_id=doc_id,
            detail=(
                f"project source mirror not found at {src_root_path}"
                if src_root_path is not None
                else "project row or project_name missing for src_root resolution"
            ),
        )

    content = _read_doc_content(doc)
    try:
        plan = parse_test_plan(content)
    except TestCaseParseError as exc:
        raise _http_error(422, exc.code, doc_id=doc_id, detail=exc.detail) from exc

    with _admission_lock:
        running = db_test_runs.get_running_by_doc(doc_id)
        if running is not None:
            raise _http_error(
                409, "run_in_progress", doc_id=doc_id, run_id=running["run_id"]
            )
        run = db_test_runs.insert_run(
            doc_id=doc_id,
            revision_no=doc.get("revision_no") or 0,
            triggered_via=triggered_via,
            runner_id=runner_id,
            setup=plan["setup"],
            cases=plan["cases"],
            teardown=plan["teardown"],
        )

    _emit_started(doc, run)
    return _run_response(run)


def _run_response(run: dict) -> dict:
    return {
        "ok": True,
        "run_id": run["run_id"],
        "doc_id": run["doc_id"],
        "revision_no": run.get("revision_no"),
        "status": run.get("status"),
        "case_total": run.get("case_total"),
        "setup_total": run.get("setup_total") or 0,
        "teardown_total": run.get("teardown_total") or 0,
        "started_at": run.get("started_at"),
        "message": f"Test run {run['run_id']} started ({run.get('case_total', 0)} cases).",
    }


def request_cancel(run_id: str) -> dict:
    """Accept a user cancel for run_id (flowgate.default.0358 T0004 / NR0003 contract).

    Returns {"run_id", "status"} where status is one of:
      - 'cancelled'  — either a not-yet-picked-up run terminated immediately, or the
        run had already reached a terminal state (idempotent replay).
      - 'cancelling' — a worker-owned run's kill was dispatched; final DB row lands
        once the executing worker reaches its own cancellation checkpoint.

    Never raises for an already-finished run (idempotent, matches the AI-invoke
    cancel_run contract at ai_invoke_service.py:2045). Raises 404 only when the
    run_id itself is unknown.
    """
    run = db_test_runs.get_run(run_id)
    if run is None:
        raise _http_error(404, "run_not_found", run_id=run_id)

    lock = _get_run_lock(run_id)
    with lock:
        run = db_test_runs.get_run(run_id) or run
        status = run.get("status")
        if status in {"passed", "failed", "cancelled"}:
            return {"run_id": run_id, "status": status}
        if status == "cancelling":
            return {"run_id": run_id, "status": "cancelling"}

        # status == "running": accept the cancel. CAS first so a racing worker that
        # is mid-pickup sees the row leave 'running' before it can act on it.
        db_test_runs.cas_running_to_cancelling(run_id)
        entry = _register_active_run(run_id)
        entry.cancel_event.set()
        with entry.lock:
            proc = entry.proc
            service_procs = list(entry.service_procs)
        for target in [proc, *service_procs]:
            if target is not None:
                _kill_process_tree(target)

        if run.get("picked_at") is None:
            # No worker owns this row — there is no process to wait on, so finalize
            # now instead of leaving it in 'cancelling' for a pickup that, since the
            # worker's SELECT only matches status='running', will never come.
            db_test_runs.cas_cancelling_to_cancelled(run_id, error="cancelled_by_user")
            finished = db_test_runs.get_run(run_id) or run
            doc = db_docs.get_by_id(finished.get("doc_id"))
            if doc is not None:
                _emit_finished(doc, finished, None)
            _unregister_active_run(run_id)
            return {"run_id": run_id, "status": "cancelled"}

        return {"run_id": run_id, "status": "cancelling"}


def issue_test_run_request(
    *,
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    continuation_target_seq: Optional[int] = None,
    continuation_review_mode: bool = False,
    continuation_instruction_mode: Optional[str] = None,
    locale: Optional[str] = None,
    continuous: bool = False,
    # 0393 NR0003 §6 — the AI run this token belongs to, so the group lease that run holds
    # recognises the worker as its own owner.
    ai_run_id: Optional[str] = None,
    # 0352 T0004 §3.4: TS is never a member of the auto-approve item_seq set (it is excluded
    # from INSTRUCTION_AUTO_TYPES entirely), so this has no bearing on THIS TSR hand-off's own
    # behavior — it is threaded through purely so the chain's selection keeps riding every
    # continuation token, including this one, without a silent drop at the TSR hop.
    continuation_auto_approve_item_seqs: Optional[list] = None,
) -> dict:
    """Issue a test_run-scoped token + execution mention for an approved TS.

    Two callers (P0005 §3's two entrances): the manned delegation route
    (POST /documents/test-run-request — ordinary token, continuation fields NULL) and
    the unmanned chain (advance_workflow's TSR-head wiring, group 0150 — the token
    inherits the chain's continuation fields, and those persisted fields are how
    _maybe_chain_auto_approve_tsr later recognizes a chain run).
    """
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")
    group_id = doc.get("group_id")
    if not group_id:
        raise ValueError(f"group_not_found:{doc_id}")
    issue = token_service.issue(
        project=doc.get("project_id") or "",
        group_id=group_id,
        action_scope="test_run",
        doc_ref=doc_id,
        issued_to=issued_to,
        continuation_target_seq=continuation_target_seq if continuous else None,
        continuation_review_mode=bool(continuous and continuation_review_mode),
        continuation_instruction_mode=continuation_instruction_mode if continuous else None,
        continuation_locale=locale if continuous else None,
        ai_run_id=ai_run_id,
        continuation_auto_approve_item_seqs=(
            continuation_auto_approve_item_seqs if continuous else None
        ),
    )
    mention = _build_test_run_mention(
        doc=doc,
        api_base_url=api_base_url,
        raw_token=issue["raw_token"],
        continuous=continuous,
        locale=locale,
    )
    return {
        "doc_ref": doc_id,
        "action_scope": "test_run",
        "group_id": group_id,
        "token": issue["raw_token"],
        "token_id": issue["token_id"],
        "expires_at": issue["expires_at"],
        "scratch_dir": issue["scratch_dir"],
        "mention": mention,
    }


def _build_test_run_mention(
    *,
    doc: dict,
    api_base_url: str,
    raw_token: str,
    continuous: bool = False,
    locale: Optional[str] = None,
) -> str:
    continuous_block = ""
    hand_off_note = ""
    if continuous:
        # Reuse the exact unmanned-chain directive every other chain mention carries so
        # the worker reads an identical framing on the test-run hop (group 0150).
        from modules.flow_gate.services.mention_service import _CONTINUOUS_TEXT

        chain_text = _CONTINUOUS_TEXT.get(locale or "ko", _CONTINUOUS_TEXT["ko"])
        continuous_block = f"## Continuous work\n---\n{chain_text}\n\n"
        hand_off_note = (
            "\nThis POST is your LAST step on this chain: FlowGate executes the TS "
            "server-side, and on all-green it auto-assembles AND auto-approves the TSR. "
            "Do NOT write the TSR yourself. If the run fails, a human resumes the chain.\n"
        )
    return (
        "## Document information\n"
        "---\n"
        f"project: {doc.get('project_id')}\n"
        f"module: {doc.get('module') or 'none'}\n"
        f"group: {doc.get('group_id')}\n"
        "type: TS\n"
        f"title: {doc.get('title') or ''}\n\n"
        f"{continuous_block}"
        "## Test execution request\n"
        "---\n"
        "Run the approved TS document through FlowGate. Do not edit the document in this step.\n"
        f"{hand_off_note}\n"
        "## Reference document\n"
        "---\n"
        f"GET {api_base_url}/document/{doc['doc_id']}\n\n"
        "## Artifact registration\n"
        "---\n"
        f"POST {api_base_url}/inbox\n"
        f"Authorization: Bearer {raw_token}\n\n"
        "{\n"
        '  "action": "test_run",\n'
        f'  "project": "{doc.get("project_id")}",\n'
        f'  "doc_id": "{doc["doc_id"]}"\n'
        "}\n"
    )


def shape_run(run: dict, *, include_cases: bool = False) -> dict:
    out = {
        "run_id": run.get("run_id"),
        "revision_no": run.get("revision_no"),
        "status": run.get("status"),
        "triggered_via": run.get("triggered_via"),
        "runner_id": run.get("runner_id"),
        "case_total": run.get("case_total"),
        "case_passed": run.get("case_passed"),
        "case_failed": run.get("case_failed"),
        "error": run.get("error"),
        "tsr_doc_id": run.get("tsr_doc_id"),
        # 0280 T0005: expose the recorded execution root so a "ran in main" report
        # can be checked against the run itself, not just the assembled TSR.
        "source_root": run.get("source_root"),
        "source_root_kind": run.get("source_root_kind"),
        "port": run.get("port"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "created_at": run.get("created_at"),
    }
    if include_cases:
        items = [_shape_case_item(case) for case in db_test_runs.list_cases(run["run_id"])]
        out["setup"] = [item for item in items if item["kind"] in {"setup", "service", "wait"}]
        out["cases"] = [item for item in items if item["kind"] == "case"]
        out["teardown"] = [item for item in items if item["kind"] == "teardown"]
    return out


def _shape_case_item(case: dict) -> dict:
    return {
        "step_no": case.get("case_no"),
        "kind": case.get("kind") or "case",
        "case_no": case.get("case_no"),
        "case_title": case.get("case_title"),
        "cmd": case.get("cmd"),
        "expect": case.get("expect"),
        "result": case.get("result"),
        "exit_code": case.get("exit_code"),
        "duration_ms": case.get("duration_ms"),
        "output_tail": case.get("output_tail"),
        "finished_at": case.get("finished_at"),
    }


def load_test_run_embed(doc_id: str) -> tuple[Optional[dict], list[dict]]:
    try:
        runs = db_test_runs.list_by_doc(doc_id)
    except Exception:
        return None, []
    history = [shape_run(row, include_cases=False) for row in runs]
    latest = shape_run(runs[0], include_cases=True) if runs else None
    return latest, history


def execute_run(run: dict) -> None:
    # Safety net (0163 / B0001 family): the inner executor finalizes the run row on every
    # *expected* path (setup_failed / passed / failed), but an unexpected exception anywhere
    # in it — or in port allocation / scratch setup before its try-block — would otherwise leave
    # the row at status="running". The live worker cannot reap that (only startup's
    # mark_orphaned_running clears it), so the doc would be pinned by run_in_progress and every
    # future re-run would 409 — the exact "re-run impossible" symptom this group fixes. Guarantee
    # the row is terminalized, then re-raise so the worker loop still logs the failure as before.
    try:
        _execute_run_inner(run)
    except Exception:
        try:
            current = db_test_runs.get_run(run["run_id"])
            if current and current.get("status") == "running":
                db_test_runs.finish_run(
                    run_id=run["run_id"], status="failed", error="internal_error"
                )
                doc = db_docs.get_by_id(run["doc_id"])
                if doc is not None:
                    _emit_finished(doc, db_test_runs.get_run(run["run_id"]) or run, None)
        except Exception:
            logger.warning(
                "execute_run safety-net finalize failed for %s",
                run.get("run_id"),
                exc_info=True,
            )
        raise


def _finalize_cancelled(run_id: str, doc: Optional[dict]) -> bool:
    """CAS 'cancelling'→'cancelled' and emit the shared completion event.

    Caller must hold ``_get_run_lock(run_id)``. Idempotent: a second call (e.g. the
    early-bail check racing the cancel route's own "not yet picked up" finalize) finds
    the row already 'cancelled' and the CAS is a no-op.
    """
    db_test_runs.cas_cancelling_to_cancelled(run_id, error="cancelled_by_user")
    finished = db_test_runs.get_run(run_id)
    if doc is not None and finished is not None:
        _emit_finished(doc, finished, None)
    return True


def _bail_if_cancelled(run_id: str, doc: Optional[dict], active: _ActiveRun) -> bool:
    """Early-exit hook for the checkpoints before any process has been spawned yet.

    A cancel arriving before/while the worker is still resolving doc/src_root (i.e.
    before _register_active_run's own registration-vs-cancel race matters) sets
    active.cancel_event under request_cancel's run lock, which already moved the DB
    row to 'cancelling' — this only needs to finish the CAS to 'cancelled'.
    """
    if not active.cancel_event.is_set():
        return False
    with _get_run_lock(run_id):
        _finalize_cancelled(run_id, doc)
    return True


def _execute_run_inner(run: dict) -> None:
    run_id = run["run_id"]
    active = _register_active_run(run_id)
    try:
        doc = db_docs.get_by_id(run["doc_id"])
        if doc is None:
            db_test_runs.finish_run(
                run_id=run_id, status="failed", error="doc_not_found"
            )
            return

        if _bail_if_cancelled(run_id, doc, active):
            return

        # B0001 (0190): resolve the group's worktree (work branch), not base(main).
        # group_id is the switch in resolve_project_src_root that selects the git
        # worktree via git_service.effective_src_root; without it a git-integrated
        # group runs its test commands in base(main), so TS cases that reference
        # files created on the work branch fail with a fast exit-1 (file-not-found)
        # even though CRUD/document views (which do pass group_id) show them present.
        root = storage_paths.resolve_project_src_root(
            doc.get("project_id"),
            doc.get("branch") or "main",
            group_id=doc.get("group_id"),
        )
        if root is None or not root.is_dir():
            logger.warning(
                "test-run %s: src_root missing (project_id=%s resolved=%s)",
                run_id,
                doc.get("project_id"),
                root,
            )
            db_test_runs.finish_run(
                run_id=run_id,
                status="failed",
                error="src_root_missing",
            )
            _emit_finished(doc, db_test_runs.get_run(run_id) or run, None)
            return

        _record_source_root(doc, run, root)

        if _bail_if_cancelled(run_id, doc, active):
            return

        port = _allocate_port()
        scratch = _scratch_dir(doc, run_id)
        scratch.mkdir(parents=True, exist_ok=True)
        db_test_runs.set_run_port(run_id, port)
        run = db_test_runs.get_run(run_id) or {**run, "port": port}

        all_items = db_test_runs.list_cases(run_id)
        setup_steps = [item for item in all_items if (item.get("kind") or "case") in {"setup", "service", "wait"}]
        cases = [item for item in all_items if (item.get("kind") or "case") == "case"]
        teardown_steps = [item for item in all_items if (item.get("kind") or "case") == "teardown"]
        run_started = time.monotonic()
        services: list[dict] = []
        env = _execution_env(port, scratch)
        setup_failed = False
        setup_error = None

        try:
            setup_failed, setup_error = _execute_setup(
                doc, run, setup_steps, root, port, scratch, env, services, run_started, active
            )

            if not setup_failed:
                for idx, case in enumerate(cases, start=1):
                    if active.cancel_event.is_set():
                        # NR0003 contract: remaining cases are not executed — leave them NULL,
                        # not timeout (that would be a false statistic reading "ran but overran").
                        break
                    if time.monotonic() - run_started > RUN_TIMEOUT_SEC:
                        db_test_runs.mark_case_finished(
                            case_id=case["id"],
                            result="timeout",
                            exit_code=None,
                            duration_ms=0,
                            output_tail="[run timeout: not executed]",
                        )
                        refreshed = {
                            **case,
                            "result": "timeout",
                            "exit_code": None,
                            "duration_ms": 0,
                        }
                        _emit_case_finished(doc, run, refreshed, idx, len(cases))
                        continue
                    _execute_case(doc, run, case, idx, len(cases), root, port, scratch, env, active)

            _execute_teardown(doc, run, teardown_steps, root, port, scratch, env, run_started, active)
        finally:
            _finalize_services(services, active)
            _remove_scratch(scratch)

        with _get_run_lock(run_id):
            if active.cancel_event.is_set():
                _finalize_cancelled(run_id, doc)
                cancelled = True
            else:
                cancelled = False
                final_cases = [
                    item
                    for item in db_test_runs.list_cases(run_id)
                    if (item.get("kind") or "case") == "case"
                ]
                if setup_failed:
                    db_test_runs.finish_run(
                        run_id=run_id,
                        status="failed",
                        case_passed=0,
                        case_failed=0,
                        error=setup_error or "setup_failed",
                    )
                    status = "failed"
                    tsr_doc_id = None
                    report_error = None
                else:
                    passed = sum(1 for case in final_cases if case.get("result") == "pass")
                    failed = sum(1 for case in final_cases if case.get("result") in {"fail", "timeout"})
                    status = "passed" if failed == 0 else "failed"
                    tsr_doc_id = None
                    report_error = None
                    if status == "passed" and not process_service.is_group_disposed(doc.get("group_id")):
                        try:
                            all_final_items = db_test_runs.list_cases(run_id)
                            tsr_doc_id = assemble_tsr(doc, db_test_runs.get_run(run_id) or run, all_final_items)
                        except Exception as exc:
                            # 0257 NR0003 §2: a passed run with no report is not a success — record it as a
                            # distinct terminal error rather than leaving a green run whose TSR never existed.
                            logger.warning("TSR assembly failed for %s: %s", run_id, exc, exc_info=True)
                            status = "failed"
                            report_error = "report_assembly_failed"
                        # flowgate.default.0152: reflect this passed run's setup/case commands into the project's
                        # verified test-command registry (L §2-4). Must never affect the run verdict (L §5) — the
                        # reflect call swallows its own errors; this guard mirrors the TSR disposed/passed gate.
                        # Still reflected on report_assembly_failed by intent: every case passed, so the commands
                        # are verified — only the report write failed, which says nothing about the commands.
                        try:
                            passed_items = db_test_runs.list_cases(run_id)
                            test_command_service.reflect_from_passed_run(doc, passed_items)
                            # flowgate.default.0157: also reflect this run's setup/run command into the GLOBAL engine
                            # recipe (auto-learn, L §2-4). Self-isolating like the 0152 reflect — never affects verdict.
                            engine_recipe_service.reflect_from_passed_run(
                                doc, db_test_runs.get_run(run_id) or run, passed_items
                            )
                        except Exception as exc:
                            logger.warning(
                                "test-command reflect failed for %s: %s", run_id, exc, exc_info=True
                            )

                    db_test_runs.finish_run(
                        run_id=run_id,
                        status=status,
                        case_passed=passed,
                        case_failed=failed,
                        tsr_doc_id=tsr_doc_id,
                        error=report_error,
                    )
            finished_run = db_test_runs.get_run(run_id) or run

        if cancelled:
            # 0358 T0004 risks 1/4/5: cancelled runs must never reach TSR assembly,
            # auto-recovery, or the chain-failure alarm — reuse the existing
            # test_run_finished/group_view_refresh broadcast only.
            _emit_finished(doc, finished_run, None)
            return

        _emit_finished(doc, finished_run, tsr_doc_id)
        if setup_failed:
            _handle_setup_stage_failure(
                doc, finished_run, db_test_runs.list_cases(run_id)
            )
            return

        if report_error is not None:
            # 0257 NR0003 §2: terminal, so it must NOT enter the 0157 recovery loop below — every
            # case passed, so there is no INFRA fault to repair and re-firing green tests cannot
            # produce the missing report. Surface it once and stop.
            _maybe_notify_chain_failure(doc, finished_run)
        elif status == "failed":
            _handle_terminal_case_failure(
                doc, finished_run, db_test_runs.list_cases(run_id)
            )
    finally:
        _unregister_active_run(run_id)


def _handle_terminal_case_failure(doc: dict, run: dict, items: list[dict]) -> dict | None:
    """Route one case-level terminal failure through repair or CODE rework."""
    failure_kind = engine_recipe_service.classify_failure(run, items)
    recovery = engine_recipe_service.handle_run_failure(doc, run, items)
    auto_reopen = None
    if failure_kind == engine_recipe_service.CODE:
        auto_reopen = _auto_reopen_failed_scenario(doc, run, "test_run_code_failure")
    if recovery not in ("repair", "escalated"):
        _maybe_notify_chain_failure(doc, run, auto_reopen=auto_reopen)
    return auto_reopen


def _handle_setup_stage_failure(doc: dict, run: dict, items: list[dict]) -> dict | None:
    """Route a setup-stage abort — the run never reached a single test case.

    flowgate.default.0157 treats this as the canonical INFRA case, and for an unmanned chain
    that is still right: the recovery loop re-fires it up to the cap and owns the signal, so
    nothing is rewound while it is working (T0004 completion criterion 3).

    What that left uncovered is every manual run, and it is the only failure the reporter ever
    reproduced: 15 consecutive runs of test.test.0042.0006-TS on the preview server ended with
    error='setup_failed', case_passed=0, case_failed=0. handle_run_failure returns "skip" for a
    manual run, so nothing at all happened — the test-scenario instruction stayed approved, the strip
    stayed parked on the empty test-report slot and the action bar stayed empty. But the setup
    commands are authored in the TS document itself (its setup section), so a setup step that
    exits non-zero is a defect in the test scenario — exactly the document the user has to edit
    and re-approve. It gets the same rewind as a RED. T0004 §2.3 had put this branch out of
    scope; that exclusion is what made the feature invisible in practice.
    """
    recovery = engine_recipe_service.handle_run_failure(doc, run, items)
    if recovery in ("repair", "escalated"):
        return None                                # unmanned repair loop owns this failure
    auto_reopen = _auto_reopen_failed_scenario(doc, run, "test_run_setup_failure")
    # R0001 group 0154 / NR0004 Gap A: surface the silent stop (best-effort).
    _maybe_notify_chain_failure(doc, run, auto_reopen=auto_reopen)
    return auto_reopen


def _auto_reopen_failed_scenario(doc: dict, run: dict, reason: str) -> dict:
    """Send one failed test-scenario instruction back to its pre-approval state through the shared Time Machine."""
    try:
        from modules.flow_gate.services import workflow_rework_service
        from modules.flow_gate.services.mutation_policy import system_principal

        auto_reopen = workflow_rework_service.auto_reopen_failed_ts(
            ts_doc_id=doc["doc_id"],
            target_seq=doc.get("seq"),
            actor_user_id=run.get("runner_id") or doc.get("owner_id"),
            reason=reason,
            run_id=run["run_id"],
            mutation_context=system_principal(
                user_id=run.get("runner_id") or "system",
                group_id=doc.get("group_id"),
                run_id=run["run_id"],
            ),
        )
    except Exception:
        logger.warning(
            "automatic TS reopen failed for run %s (ignored)",
            run.get("run_id"),
            exc_info=True,
        )
        auto_reopen = {
            "auto_reopened": False,
            "target_doc_id": doc.get("doc_id"),
            "target_seq": doc.get("seq"),
            "run_id": run.get("run_id"),
            "auto_reopen_skipped": "auto_reopen_error",
        }
    if not auto_reopen.get("auto_reopened"):
        # A skip is the one outcome that looks exactly like "the feature is not there":
        # the TS stays approved and nothing is emitted. A manual run carries no
        # continuation token, so the reason never reaches the failure feed either —
        # record it here so a silent no-op is always diagnosable from the server log.
        logger.warning(
            "automatic TS reopen skipped for run %s on %s: %s",
            run.get("run_id"),
            doc.get("doc_id"),
            auto_reopen.get("auto_reopen_skipped"),
        )
    else:
        # _emit_finished already broadcast this run's paired group_view_refresh BEFORE the
        # rewind ran, so every open browser has re-read the pre-rewind state: the TS still
        # renders as approved even though the row is now pending_review. That stale screen
        # is the whole visible feature, so the post-commit refresh belongs on BOTH paths —
        # the unmanned chain is watched too, and it never reloads by hand.
        _emit_auto_reopen_refresh(doc, run, auto_reopen)
    return auto_reopen


def _record_source_root(doc: dict, run: dict, root: Path) -> None:
    """Persist the tree this run executes in, before a single command runs.

    0280 NR0003 §6-2: the runner resolved a root and then forgot it, so "the tests
    ran in main" could never be confirmed or refuted — a correct worktree run and a
    silent fallback to base were indistinguishable after the fact. Written early so
    a run that dies mid-way still carries its location. Best-effort in both
    directions: bookkeeping must never fail a run, and a failure to record must not
    masquerade as a recorded base run (the column stays NULL → "no record").
    """
    try:
        kind = storage_paths.classify_src_root(
            doc.get("project_id"), doc.get("group_id"), root
        )
        stored = storage_paths.to_storage_relative(root, doc.get("project_id"))
        db_test_runs.set_run_source_root(run["run_id"], stored, kind)
        logger.info(
            "test-run %s: executing in %s (%s)", run["run_id"], stored, kind
        )
    except Exception:
        logger.warning(
            "test-run %s: failed to record source root", run["run_id"], exc_info=True
        )


def _execute_setup(
    doc: dict,
    run: dict,
    steps: list[dict],
    root: Path,
    port: int,
    scratch: Path,
    env: dict[str, str],
    services: list[dict],
    run_started: float,
    active: Optional[_ActiveRun] = None,
) -> tuple[bool, Optional[str]]:
    if not steps:
        return False, None
    stage_started = time.monotonic()
    failed_step = None
    for step in steps:
        if active is not None and active.cancel_event.is_set():
            break
        if time.monotonic() - run_started > RUN_TIMEOUT_SEC:
            _mark_step_timeout(step, "[run timeout during setup]")
            failed_step = step.get("case_no")
            break
        kind = step.get("kind")
        if kind == "setup":
            result = _execute_step_command(
                step, root, port, scratch, env, SETUP_STEP_TIMEOUT_SEC, active
            )
        elif kind == "service":
            result = _start_service_step(step, root, port, scratch, env, services, active)
        elif kind == "wait":
            result = _execute_wait_step(step, port, active)
        else:
            result = "fail"
        if result == "cancelled":
            break
        if result in {"fail", "timeout"}:
            failed_step = step.get("case_no")
            break
    failed_count = len(
        [
            step
            for step in db_test_runs.list_cases(run["run_id"])
            if (step.get("kind") or "") in {"setup", "service", "wait"}
            and step.get("result") in {"fail", "timeout"}
        ]
    )
    _emit_stage_finished(
        doc,
        run,
        "setup",
        ok=failed_step is None,
        total=len(steps),
        failed=failed_count,
        duration_ms=int((time.monotonic() - stage_started) * 1000),
        failed_step=failed_step,
    )
    return failed_step is not None, "setup_failed" if failed_step is not None else None


def _execute_teardown(
    doc: dict,
    run: dict,
    steps: list[dict],
    root: Path,
    port: int,
    scratch: Path,
    env: dict[str, str],
    run_started: float,
    active: Optional[_ActiveRun] = None,
) -> None:
    if not steps:
        return
    stage_started = time.monotonic()
    # 0358 T0004 §2: teardown still runs best-effort after a cancel (it may undo
    # setup side effects), but a cancelled run gets a short overall budget instead of
    # the normal 600s-per-step timeout — otherwise "cancel immediately" could stay stuck in
    # 'cancelling' for up to 10 minutes.
    cancel_deadline: Optional[float] = None
    for step in steps:
        is_cancelled = active is not None and active.cancel_event.is_set()
        if is_cancelled and cancel_deadline is None:
            cancel_deadline = time.monotonic() + CANCEL_TEARDOWN_BUDGET_SEC
        if cancel_deadline is not None and time.monotonic() > cancel_deadline:
            break
        if time.monotonic() - run_started > RUN_TIMEOUT_SEC:
            _mark_step_timeout(step, "[run timeout during teardown]")
            continue
        step_timeout = CANCEL_TEARDOWN_STEP_TIMEOUT_SEC if is_cancelled else TEARDOWN_STEP_TIMEOUT_SEC
        _execute_step_command(step, root, port, scratch, env, step_timeout, active)
    failed_count = len(
        [
            step
            for step in db_test_runs.list_cases(run["run_id"])
            if (step.get("kind") or "") == "teardown"
            and step.get("result") in {"fail", "timeout"}
        ]
    )
    _emit_stage_finished(
        doc,
        run,
        "teardown",
        ok=failed_count == 0,
        total=len(steps),
        failed=failed_count,
        duration_ms=int((time.monotonic() - stage_started) * 1000),
    )


def _execute_case(
    doc: dict,
    run: dict,
    case: dict,
    idx: int,
    total: int,
    root: Path,
    port: int,
    scratch: Path,
    env: dict[str, str],
    active: Optional[_ActiveRun] = None,
) -> None:
    if active is not None and active.cancel_event.is_set():
        return  # cancelled before this case started — leave it NULL, not a result
    started = time.monotonic()
    result, exit_code, output = _run_shell_command(
        _replace_placeholders(case["cmd"], port, scratch),
        root,
        CASE_TIMEOUT_SEC,
        env,
        active,
    )
    if active is not None and active.cancel_event.is_set():
        # Cancelled while the command was in flight (checked right after communicate()
        # returns, per T0004 §2) — the process was already killed; do not record a
        # pass/fail/timeout result for a command that never ran to completion.
        return
    duration_ms = int((time.monotonic() - started) * 1000)
    output_tail = output[-OUTPUT_TAIL_CHARS:]
    db_test_runs.mark_case_finished(
        case_id=case["id"],
        result=result,
        exit_code=exit_code,
        duration_ms=duration_ms,
        output_tail=output_tail,
    )
    refreshed = {
        **case,
        "result": result,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }
    _emit_case_finished(doc, run, refreshed, idx, total)


def _execute_step_command(
    step: dict,
    root: Path,
    port: int,
    scratch: Path,
    env: dict[str, str],
    timeout: int,
    active: Optional[_ActiveRun] = None,
) -> str:
    if active is not None and active.cancel_event.is_set():
        return "cancelled"
    started = time.monotonic()
    result, exit_code, output = _run_shell_command(
        _replace_placeholders(step["cmd"], port, scratch),
        root,
        timeout,
        env,
        active,
    )
    if active is not None and active.cancel_event.is_set():
        return "cancelled"
    db_test_runs.mark_case_finished(
        case_id=step["id"],
        result=result,
        exit_code=exit_code,
        duration_ms=int((time.monotonic() - started) * 1000),
        output_tail=output[-OUTPUT_TAIL_CHARS:],
    )
    return result


def _start_service_step(
    step: dict,
    root: Path,
    port: int,
    scratch: Path,
    env: dict[str, str],
    services: list[dict],
    active: Optional[_ActiveRun] = None,
) -> Optional[str]:
    if active is not None and active.cancel_event.is_set():
        return "cancelled"
    started = time.monotonic()
    cmd = _replace_placeholders(step["cmd"], port, scratch)
    log_path = scratch / f"{step['case_no'].lower()}-service.log"
    log_handle = log_path.open("ab")
    eff_cmd, eff_cwd = process_runner.unc_safe_shell(cmd, root)
    kwargs = _popen_kwargs(root, env)
    kwargs["cwd"] = eff_cwd
    kwargs["stdout"] = log_handle
    kwargs["stderr"] = subprocess.STDOUT
    try:
        proc = subprocess.Popen(eff_cmd, **kwargs)
    except Exception as exc:
        log_handle.close()
        db_test_runs.mark_case_finished(
            case_id=step["id"],
            result="fail",
            exit_code=None,
            duration_ms=int((time.monotonic() - started) * 1000),
            output_tail=str(exc)[-OUTPUT_TAIL_CHARS:],
        )
        return "fail"
    if active is not None:
        # T item 1: registration-vs-cancel race — if cancel already fired between the
        # is_set() check above and Popen returning, kill this proc immediately.
        _add_service_proc(active, proc)
    services.append(
        {
            "step": step,
            "proc": proc,
            "log_path": log_path,
            "log_handle": log_handle,
            "started": started,
        }
    )
    db_test_runs.update_case_observation(
        case_id=step["id"],
        exit_code=None,
        duration_ms=None,
        output_tail="",
    )
    return None


def _execute_wait_step(step: dict, port: int, active: Optional[_ActiveRun] = None) -> str:
    started = time.monotonic()
    deadline = started + WAIT_TIMEOUT_SEC
    result = "timeout"
    cancelled = False
    while time.monotonic() < deadline:
        if active is not None and active.cancel_event.is_set():
            cancelled = True
            break
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=WAIT_POLL_SEC):
                result = "pass"
                break
        except OSError:
            time.sleep(WAIT_POLL_SEC)
    if cancelled:
        return "cancelled"
    db_test_runs.mark_case_finished(
        case_id=step["id"],
        result=result,
        exit_code=None,
        duration_ms=int((time.monotonic() - started) * 1000),
        output_tail=None if result == "pass" else f"wait timeout: 127.0.0.1:{port}",
    )
    return result


def _mark_step_timeout(step: dict, output_tail: str) -> None:
    db_test_runs.mark_case_finished(
        case_id=step["id"],
        result="timeout",
        exit_code=None,
        duration_ms=0,
        output_tail=output_tail,
    )


def _run_case_command(cmd: str, root: Path) -> tuple[str, Optional[int], str]:
    return _run_shell_command(cmd, root, CASE_TIMEOUT_SEC, None)


def _run_shell_command(
    cmd: str,
    root: Path,
    timeout: int,
    env: Optional[dict[str, str]],
    active: Optional[_ActiveRun] = None,
) -> tuple[str, Optional[int], str]:
    eff_cmd, eff_cwd = process_runner.unc_safe_shell(cmd, root)
    kwargs = {
        "shell": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    kwargs.update(_popen_kwargs(root, env, include_stdio=False))
    kwargs["cwd"] = eff_cwd

    proc = subprocess.Popen(eff_cmd, **kwargs)
    if active is not None:
        # T item 1: if cancel already fired between the caller's is_set() check and
        # Popen returning here, this kills the proc immediately instead of leaving it
        # live and untracked.
        _set_current_proc(active, proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except Exception:
                stdout = getattr(exc, "output", None)
                stderr = getattr(exc, "stderr", None)
            output = _safe_decode(stdout) + _safe_decode(stderr)
            return "timeout", None, output

        result = "pass" if proc.returncode == CASE_EXIT_PASS else "fail"
        output = _safe_decode(stdout) + _safe_decode(stderr)
        return result, proc.returncode, output
    finally:
        if active is not None:
            _clear_current_proc(active, proc)


def _popen_kwargs(
    root: Path,
    env: Optional[dict[str, str]],
    *,
    include_stdio: bool = True,
) -> dict:
    return process_runner.popen_kwargs(root, env, include_stdio=include_stdio)


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _scratch_dir(doc: dict, run_id: str) -> Path:
    return (
        storage_paths.get_storage_root(doc.get("project_id"), create=True)
        / "test_runs"
        / run_id
    )


def _execution_env(port: int, scratch: Path) -> dict[str, str]:
    """Env every setup/case/teardown command sees.

    FLOWGATE_TEST_OS / FLOWGATE_TEST_SHELL (0277 B0001 -> NR0003 §4 F3) let a TS branch at
    run time instead of guessing the host: the same cmd string is handed to cmd.exe on
    Windows and /bin/sh on POSIX, and until these existed a TS had no way to tell which.
    PYTEST_ADDOPTS disables pytest's cacheprovider for FlowGate-managed runs. The provider
    creates ``pytest-cache-files-*`` beside ``.pytest_cache`` before its atomic rename;
    a killed or contending process can strand those directories in the source worktree.
    FlowGate does not rely on pytest's cross-run cache, so managed runs keep all transient
    state in FLOWGATE_TEST_SCRATCH instead of writing cache scaffolding into source.
    """
    return {
        "FLOWGATE_TEST_PORT": str(port),
        "FLOWGATE_TEST_SCRATCH": str(scratch),
        "FLOWGATE_TEST_OS": test_command_service.current_os(),
        "FLOWGATE_TEST_SHELL": test_command_service.current_shell(),
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
    }


def _replace_placeholders(value: str, port: int, scratch: Path) -> str:
    return value.replace("{PORT}", str(port)).replace("{SCRATCH}", str(scratch))


def _finalize_services(services: list[dict], active: Optional[_ActiveRun] = None) -> None:
    for service in services:
        proc = service["proc"]
        log_handle = service["log_handle"]
        try:
            log_handle.flush()
        except Exception:
            pass
        exit_code = proc.poll()
        duration_ms = None
        if exit_code is not None:
            duration_ms = int((time.monotonic() - service["started"]) * 1000)
        else:
            _kill_process_tree(proc)
        if active is not None:
            _remove_service_proc(active, proc)
        try:
            log_handle.close()
        except Exception:
            pass
        db_test_runs.update_case_observation(
            case_id=service["step"]["id"],
            exit_code=exit_code,
            duration_ms=duration_ms,
            output_tail=_read_tail(service["log_path"], OUTPUT_TAIL_CHARS),
        )


def _read_tail(path: Path, chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-chars:]


def _remove_scratch(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        logger.warning("test run scratch cleanup failed: %s", path, exc_info=True)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    process_runner.kill_process_tree(proc)


def _safe_decode(data) -> str:
    return process_runner.safe_decode(data)


_TSR_TITLE_TEMPLATES = {
    "ko": "테스트 레포트 — {name}",
    "en": "Test Report — {name}",
    "ja": "テストレポート — {name}",
}


def assemble_tsr(doc: dict, run: dict, cases: list[dict], locale: str = "ko") -> str:
    """Assemble this run's TSR into the TS's single active report slot (0257 NR0003 §1).

    Two distinct re-entry paths land here, and they need different answers:

    * the *same* run re-finishing (worker retry) — return the report it already produced,
      keyed on run_id. Narrow by construction: finish_run records tsr_doc_id only after
      this returns, so this guard is False on a first assembly and True only afterwards.
    * a *new* run for the same TS (the B0001 rerun) — revise the report already holding
      the workflow slot instead of reserving a second number. Reserving one per attempt is
      what produced two TSR documents, the second of which the slot never adopted.

    Per-attempt history stays in test_runs/test_run_cases; the workflow keeps one document.
    ``locale`` (T0009 task 4): no request context reaches this background assembly step
    today, so callers all pass the "ko" default — the parameter exists so the report body
    is localizable once one does, and so it can be unit-tested directly.
    """
    if run.get("tsr_doc_id"):
        return str(run["tsr_doc_id"])

    group_id = doc.get("group_id")
    project_id = doc.get("project_id")
    module = doc.get("module") or "none"
    branch = doc.get("branch") or "main"
    if not group_id or not project_id:
        raise RuntimeError("TS document has no group/project")

    title_template = _TSR_TITLE_TEMPLATES.get(locale) or _TSR_TITLE_TEMPLATES["ko"]
    title = title_template.format(name=doc.get("title") or doc["doc_id"])
    content = _tsr_content(doc, run, cases, title, locale)

    active = _active_tsr_for_ts(doc)
    if active is not None:
        return _revise_active_tsr(doc, active, content, title)

    doc_code = numbering_service.reserve_document(group_id, "TSR", module=module)
    tsr_doc_id = f"{group_id}.{doc_code}"
    _type, seq = id_formatter.parse_doc_code(doc_code)
    path = storage_paths.document_path(
        project_id=project_id,
        group_code=group_id,
        doc_code=doc_code,
        filename="document.md",
        module=module,
        branch=branch,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    db_docs.create(
        {
            "doc_id": tsr_doc_id,
            "project_id": project_id,
            "branch": branch,
            "module": module,
            "group_id": group_id,
            "type_code": "TSR",
            "seq": seq,
            "title": title,
            "file_path": storage_paths.to_storage_relative(path, project_id),
            "status": "open",
            "owner_id": doc.get("owner_id"),
            "target_id": doc["doc_id"],
            "triggered_by": doc["doc_id"],
        }
    )
    _register_tsr_workflow_result(doc, tsr_doc_id, path)
    return tsr_doc_id


def _active_tsr_for_ts(doc: dict) -> Optional[dict]:
    """The TSR document already holding this TS's report slot, if any (0257 NR0003 §1).

    A TSR is assembled for exactly one TS and records it as target_id, so the TS doc_id is
    the slot key. Superseded reports are ignored. When a group already carries the B0001
    duplicate, the slot-bound report wins over the orphan so a rerun converges back onto
    the document the workflow actually tracks (cleaning up the existing orphan is NR0003 §4
    admin work, deliberately not automated here).
    """
    existing = [
        candidate
        for candidate in db_docs.get_documents_by_target_id(doc["doc_id"], types=("TSR",))
        if not candidate.get("superseded_by")
    ]
    if not existing:
        return None
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    for candidate in existing:
        if db_wfseq.get_item_by_result_doc_id(candidate["doc_id"]) is not None:
            return candidate
    return existing[-1]


def _revise_active_tsr(doc: dict, active: dict, content: str, title: str) -> str:
    """Rewrite the active TSR in place for a fresh run, keeping its doc_id and slot."""
    tsr_doc_id = str(active["doc_id"])
    project_id = doc["project_id"]
    group_id = doc["group_id"]
    # Recompute rather than trust the stored file_path: it is the same deterministic path the
    # create branch below writes, and rebuilding it repairs a row whose path went stale or
    # empty — the "there is no linked MD file" preview of B0001.
    path = storage_paths.document_path(
        project_id=project_id,
        group_code=group_id,
        doc_code=tsr_doc_id[len(group_id) + 1:],
        filename="document.md",
        module=doc.get("module") or "none",
        branch=doc.get("branch") or "main",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    db_docs.update(
        tsr_doc_id,
        {
            "title": title,
            "file_path": storage_paths.to_storage_relative(path, project_id),
            "revision_no": (active.get("revision_no") or 0) + 1,
        },
    )
    _register_tsr_workflow_result(doc, tsr_doc_id, path)
    return tsr_doc_id


_TSR_STRINGS = {
    "ko": {
        "target_ts": "> 대상 TS: {doc_id} (revision {revision})",
        "run_seq": "> 실행 회차: {run_id}",
        "run_time": "> 실행 시각: {started} ~ {now}",
        "result_summary": "> 결과: 통과 {passed}/{total}",
        "env_heading": "## 실행 환경",
        "project_line": "- 프로젝트: {project} / 문서 브랜치: {branch}",
        "src_root_line": "- 실행 위치: {src_root}",
        "port_line": "- 할당 포트: {port} / 스크래치: 회차 전용 (종료 시 삭제됨)",
        "actor_line": "- 실행 주체: FlowGate 서버 (워커 로컬 환경 불개입)",
        "prep_heading": "## 준비 / 정리",
        "prep_table_header": "| 단계 | 종류 | 명령 | 결과 | 소요 |",
        "case_heading": "## 케이스별 결과",
        "case_table_header": "| 케이스 | 이름 | 결과 | exit | 소요 |",
        "excerpt_heading": "## 케이스별 출력 발췌",
        "footer": "*이 레포트는 실행 회차 {run_id} 의 기록으로부터 FlowGate가 자동 조립했다.*",
    },
    "en": {
        "target_ts": "> Target TS: {doc_id} (revision {revision})",
        "run_seq": "> Run: {run_id}",
        "run_time": "> Run time: {started} ~ {now}",
        "result_summary": "> Result: {passed}/{total} passed",
        "env_heading": "## Execution Environment",
        "project_line": "- Project: {project} / Document branch: {branch}",
        "src_root_line": "- Executed at: {src_root}",
        "port_line": "- Allocated port: {port} / Scratch: run-scoped (deleted on completion)",
        "actor_line": "- Executed by: FlowGate server (no worker-local environment involved)",
        "prep_heading": "## Setup / Teardown",
        "prep_table_header": "| Step | Kind | Command | Result | Duration |",
        "case_heading": "## Case Results",
        "case_table_header": "| Case | Name | Result | exit | Duration |",
        "excerpt_heading": "## Case Output Excerpts",
        "footer": "*This report was auto-assembled by FlowGate from run {run_id}'s record.*",
    },
    "ja": {
        "target_ts": "> 対象TS: {doc_id} (revision {revision})",
        "run_seq": "> 実行回次: {run_id}",
        "run_time": "> 実行時刻: {started} ~ {now}",
        "result_summary": "> 結果: 合格 {passed}/{total}",
        "env_heading": "## 実行環境",
        "project_line": "- プロジェクト: {project} / 文書ブランチ: {branch}",
        "src_root_line": "- 実行場所: {src_root}",
        "port_line": "- 割り当てポート: {port} / スクラッチ: 回次専用（終了時に削除）",
        "actor_line": "- 実行主体: FlowGateサーバー（ワーカーのローカル環境は関与しません）",
        "prep_heading": "## 準備 / 後片付け",
        "prep_table_header": "| 段階 | 種類 | コマンド | 結果 | 所要 |",
        "case_heading": "## ケース別結果",
        "case_table_header": "| ケース | 名前 | 結果 | exit | 所要 |",
        "excerpt_heading": "## ケース別出力抜粋",
        "footer": "*このレポートは実行回次 {run_id} の記録からFlowGateが自動組み立てしました。*",
    },
}


def _tsr_content(doc: dict, run: dict, cases: list[dict], title: str, locale: str = "ko") -> str:
    strings = _TSR_STRINGS.get(locale) or _TSR_STRINGS["ko"]
    setup = [case for case in cases if (case.get("kind") or "case") in {"setup", "service", "wait"}]
    case_rows = [case for case in cases if (case.get("kind") or "case") == "case"]
    teardown = [case for case in cases if (case.get("kind") or "case") == "teardown"]
    prep_rows = []
    for step in [*setup, *teardown]:
        duration = step.get("duration_ms")
        duration_text = "-" if duration is None else f"{round(duration / 1000, 1)}s"
        result = step.get("result") or (
            _kind_label("service", locale) if (step.get("kind") or "") == "service" else ""
        )
        prep_rows.append(
            f"| {step.get('case_no')} | {_kind_label(step.get('kind'), locale)} | "
            f"{step.get('cmd')} | {result} | {duration_text} |"
        )
    result_rows = []
    for case in case_rows:
        exit_text = "" if case.get("exit_code") is None else str(case.get("exit_code"))
        duration = case.get("duration_ms") or 0
        result_rows.append(
            f"| {case.get('case_no')} | {case.get('case_title')} | {case.get('result')} | "
            f"{exit_text} | {round(duration / 1000, 1)}s |"
        )
    excerpts: list[str] = []
    for case in case_rows:
        duration = case.get("duration_ms") or 0
        output = (case.get("output_tail") or "")[-TSR_CASE_EXCERPT_CHARS:]
        excerpts.extend(
            [
                f"### {case.get('case_no')}: {case.get('case_title')} "
                f"({case.get('result')}, {round(duration / 1000, 1)}s)",
                "",
                "```",
                output,
                "```",
                "",
            ]
        )
    return "\n".join(
        [
            f"# {title}",
            "",
            strings["target_ts"].format(doc_id=doc["doc_id"], revision=run.get("revision_no")),
            strings["run_seq"].format(run_id=run["run_id"]),
            strings["run_time"].format(started=run.get("started_at"), now=now_iso()),
            strings["result_summary"].format(passed=len(case_rows), total=len(case_rows)),
            "",
            strings["env_heading"],
            "",
            strings["project_line"].format(
                project=doc.get("project_id"), branch=doc.get("branch") or "main"
            ),
            strings["src_root_line"].format(src_root=_src_root_label(run, locale)),
            strings["port_line"].format(port=run.get("port")),
            strings["actor_line"],
            "",
            strings["prep_heading"],
            "",
            strings["prep_table_header"],
            "|---|---|---|---|---|",
            *prep_rows,
            "",
            strings["case_heading"],
            "",
            strings["case_table_header"],
            "|---|---|---|---|---|",
            *result_rows,
            "",
            strings["excerpt_heading"],
            "",
            *excerpts,
            strings["footer"].format(run_id=run["run_id"]),
            "",
        ]
    )


# 0280 NR0003 §4-A: the execution-environment block used to print doc['branch'] (always "main",
# it is the *document's* branch, not the worktree's) next to the hardcoded string
# the project source root (src_root). A run that executed correctly in the group worktree
# was therefore reported as having run in main — the direct source of the repeated
# "tests run in main" reports. These labels render what the run actually recorded.
_SRC_ROOT_REASON_LABELS = {
    "ko": {
        "git_integration_off": "이 프로젝트는 git 통합이 꺼져 있다",
        "no_group_git_state": "git 통합은 켜져 있으나 그룹의 git 상태 기록이 없다",
        "worktree_unregistered": "그룹 워크트리가 등록돼 있지 않다 (머지/푸시 후 해제된 경우 포함)",
        "state_branch_empty": "그룹 git 상태에 브랜치 값이 없다",
        "project_name_missing": "프로젝트명을 확인할 수 없다",
        "worktree_dir_missing": "등록된 워크트리 디렉터리가 실제로 존재하지 않는다",
        "no_group_context": "그룹 정보 없이 실행됐다",
        "resolution_error": "워크트리 해석 중 오류가 발생했다",
    },
    "en": {
        "git_integration_off": "this project has git integration turned off",
        "no_group_git_state": "git integration is on but the group has no recorded git state",
        "worktree_unregistered": "the group worktree is not registered (including after a merge/push release)",
        "state_branch_empty": "the group's git state has no branch value",
        "project_name_missing": "the project name could not be resolved",
        "worktree_dir_missing": "the registered worktree directory does not actually exist",
        "no_group_context": "run without group context",
        "resolution_error": "an error occurred while resolving the worktree",
    },
    "ja": {
        "git_integration_off": "このプロジェクトはgit連携がオフになっている",
        "no_group_git_state": "git連携はオンだが、グループのgit状態が記録されていない",
        "worktree_unregistered": "グループワークツリーが登録されていない（マージ/プッシュ後に解除された場合を含む）",
        "state_branch_empty": "グループのgit状態にブランチ値がない",
        "project_name_missing": "プロジェクト名を確認できない",
        "worktree_dir_missing": "登録済みのワークツリーディレクトリが実際には存在しない",
        "no_group_context": "グループ情報なしで実行された",
        "resolution_error": "ワークツリー解決中にエラーが発生した",
    },
}

_SRC_ROOT_TEMPLATES = {
    "ko": {
        "no_record": "기록 없음 (이 회차는 실행 루트를 기록하지 않았다)",
        "worktree": "그룹 워크트리 `{stored}` — 작업 브랜치에서 실행됨",
        "unknown": "`{stored}` (워크트리 여부 미상)",
        "base_tree": "프로젝트 base 트리 `{stored}` — 워크트리 미사용: {reason}",
    },
    "en": {
        "no_record": "no record (this run did not record its execution root)",
        "worktree": "group worktree `{stored}` — executed on the working branch",
        "unknown": "`{stored}` (worktree status unknown)",
        "base_tree": "project base tree `{stored}` — worktree not used: {reason}",
    },
    "ja": {
        "no_record": "記録なし（この回次は実行ルートを記録しなかった）",
        "worktree": "グループワークツリー `{stored}` — 作業ブランチで実行された",
        "unknown": "`{stored}` （ワークツリーかどうか不明）",
        "base_tree": "プロジェクトbaseツリー `{stored}` — ワークツリー未使用: {reason}",
    },
}


def _src_root_label(run: dict, locale: str = "ko") -> str:
    """Render the run's recorded execution root for the TSR execution-environment block."""
    templates = _SRC_ROOT_TEMPLATES.get(locale) or _SRC_ROOT_TEMPLATES["ko"]
    reason_labels = _SRC_ROOT_REASON_LABELS.get(locale) or _SRC_ROOT_REASON_LABELS["ko"]
    stored = run.get("source_root")
    kind = run.get("source_root_kind")
    if not stored:
        # Runs predating 0280 T0005, and runs whose bookkeeping failed. Say so
        # rather than repeating the old guess.
        return templates["no_record"]
    if kind == "worktree":
        return templates["worktree"].format(stored=stored)
    if kind == "unknown":
        return templates["unknown"].format(stored=stored)
    reason = reason_labels.get(kind, kind)
    return templates["base_tree"].format(stored=stored, reason=reason)


_KIND_LABELS = {
    "ko": {"setup": "cmd", "service": "기동", "wait": "대기", "teardown": "cmd"},
    "en": {"setup": "cmd", "service": "start", "wait": "wait", "teardown": "cmd"},
    "ja": {"setup": "cmd", "service": "起動", "wait": "待機", "teardown": "cmd"},
}


def _kind_label(kind: Optional[str], locale: str = "ko") -> str:
    labels = _KIND_LABELS.get(locale) or _KIND_LABELS["ko"]
    return labels.get(kind or "", kind or "")


def _register_tsr_workflow_result(doc: dict, tsr_doc_id: str, path: Path) -> None:
    try:
        from modules.flow_gate.db import workflow_sequences as db_wfseq
        from modules.flow_gate.workflow.pipeline_service import (
            register_workflow_result,
            transition_document_review,
        )
        from modules.flow_gate.workflow.transition_rules import get_doc_review_rule

        item = _tsr_slot_item(doc, db_wfseq)
        if item is not None and item.get("result_doc_id") != tsr_doc_id:
            register_workflow_result(
                item_id=item["id"],
                registered_path=storage_paths.to_storage_relative(path, doc.get("project_id")),
                registered_doc_id=tsr_doc_id,
                registered_at=now_iso(),
                actor_user_id=doc.get("owner_id") or "system",
            )
        # Submit only from a state the review matrix accepts. A report reused while still
        # awaiting review is already in the right state; re-submitting it raised an invalid
        # transition that the except below swallowed, hiding real failures behind a warning.
        review_status = (db_docs.get_by_id(tsr_doc_id) or {}).get("doc_review_status") or ""
        if get_doc_review_rule(review_status, "submit") is not None:
            transition_document_review(
                doc_id=tsr_doc_id,
                action="submit",
                actor_user_id=doc.get("owner_id") or "system",
                user_permissions={"document.update"},
            )
    except Exception:
        logger.warning("TSR workflow registration failed for %s", tsr_doc_id, exc_info=True)
    _maybe_chain_auto_approve_tsr(doc, tsr_doc_id)


def _tsr_slot_item(doc: dict, db_wfseq) -> Optional[dict]:
    """The sequence slot that holds this TS's report (0257 NR0003 §3).

    Resolved from the TS's own sequence, not the pending head: the head only names a TSR
    while the first report is outstanding, so a rerun's report used to find no slot and was
    left unbound — the orphan document of B0001. The workflow builder attaches TSR directly
    after its TS (AUTO_REPORT_MAP), so the slot is the first TSR item past the TS's own.
    Falls back to the pending head when the TS is not a registered slot result.
    """
    seq = db_wfseq.get_sequence_for_member_doc(doc["doc_id"])
    if seq is not None:
        items = sorted(
            db_wfseq.get_sequence_items(seq["id"]), key=lambda it: it.get("sort_order") or 0
        )
        ts_at = next(
            (i for i, it in enumerate(items) if it.get("result_doc_id") == doc["doc_id"]), None
        )
        if ts_at is not None:
            for item in items[ts_at + 1:]:
                if (item.get("type") or "").upper() == "TSR":
                    return item
    head = db_wfseq.get_pending_head_by_group(doc.get("group_id"), doc.get("project_id"))
    if head is not None and (head.get("type") or "").upper() == "TSR":
        return head
    return None


def _maybe_chain_auto_approve_tsr(doc: dict, tsr_doc_id: str) -> None:
    """Unmanned-chain gate passage for an auto-assembled TSR (group 0150).

    L0006 (0138) deliberately left the TSR gate to "a human or an unmanned-chain approval procedure"; this is
    the latter. Chain detection is the consumed test_run token for this TS: only the
    token minted by advance_workflow's TSR-head wiring carries continuation_target_seq
    (the manned test-run-request token leaves it NULL, so manned delegation keeps its
    human approval gate). Approval uses the token issuer's REAL resolved permissions —
    the same resolver as the inbox self-chain (approve is never bypassed, P0005 §4);
    lacking document.approve degrades to a submitted TSR, never an error.
    """
    try:
        from modules.flow_gate.db import tokens as db_tokens

        token_rec = db_tokens.get_latest_consumed_by_scope_doc_ref(
            "test_run", doc["doc_id"]
        )
        if token_rec is None or token_rec.get("continuation_target_seq") is None:
            return  # manned delegation (or UI) run — human keeps the TSR gate
        actor_user_id = token_rec.get("issued_to") or "system"

        from modules.flow_gate.db import users as db_users
        from modules.flow_gate.workflow.routers.workflow import (
            _get_user_permissions as _resolve_user_permissions,
        )

        actor_user = db_users.get_by_id(actor_user_id) or {
            "user_id": actor_user_id, "is_admin": 0,
        }
        approver_perms = _resolve_user_permissions(actor_user)
        if "document.approve" not in approver_perms:
            logger.warning(
                "chain TSR auto-approve skipped for %s: issuer %s lacks document.approve",
                tsr_doc_id, actor_user_id,
            )
            return
        from modules.flow_gate.workflow.pipeline_service import transition_document_review

        transition_document_review(
            doc_id=tsr_doc_id,
            action="approve",
            actor_user_id=actor_user_id,
            user_permissions=approver_perms,
        )
        # The worker loop cannot be resumed from an async run (no channel for a next
        # token), so the chain ends here by design — record the explicit end signal
        # (group 0125). Best-effort: a logging failure must not undo the approval.
        try:
            from modules.flow_gate.workflow import event_logger

            tsr_doc = db_docs.get_by_id(tsr_doc_id) or {}
            tsr_pk = tsr_doc.get("id")
            if tsr_pk is not None:
                event_logger.log_continuous_work_ended(
                    project_id=doc.get("project_id"),
                    actor_user_id=actor_user_id,
                    document_id=tsr_pk,
                    doc_id=tsr_doc_id,
                    group_id=doc.get("group_id"),
                    target_seq=token_rec.get("continuation_target_seq"),
                )
        except Exception:
            logger.warning(
                "continuous_work_ended signal failed for %s (ignored)",
                tsr_doc_id, exc_info=True,
            )
    except Exception:
        logger.warning(
            "chain TSR auto-approve failed for %s (TSR left submitted)",
            tsr_doc_id, exc_info=True,
        )



def _continuation_token_for_doc(doc: dict, run: Optional[dict] = None) -> Optional[dict]:
    """Return the consumed unmanned-chain token, never an ordinary/manual token."""
    if run is not None and run.get("triggered_via") == "ui":
        return None
    try:
        from modules.flow_gate.db import tokens as db_tokens

        token_rec = db_tokens.get_latest_consumed_by_scope_doc_ref(
            "test_run", doc["doc_id"]
        )
        if token_rec is not None and token_rec.get("continuation_target_seq") is not None:
            return token_rec
    except Exception:
        logger.warning("test-run continuation lookup failed", exc_info=True)
    return None


def _maybe_notify_chain_failure(
    doc: dict, run: dict, *, auto_reopen: Optional[dict] = None
) -> None:
    """Emit one persistent failure signal for an unmanned-chain terminal run."""
    try:
        from modules.flow_gate.workflow import event_logger

        token_rec = _continuation_token_for_doc(doc, run)
        if token_rec is None:
            return
        actor_user_id = token_rec.get("issued_to") or "system"
        doc_row = db_docs.get_by_id(doc["doc_id"]) or {}
        # Worker completion may be delivered twice. Keep the persistent failure feed
        # once-per-run even when the state-transition hook correctly no-ops the duplicate.
        try:
            from modules.flow_gate.db.connection import get_store

            event_run_id = run.get("run_id")
            existing = get_store()._fetch_one(
                "SELECT id FROM workflow_events WHERE event_type = ? AND metadata LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                ["continuous_work_failed", f'%"run_id": "{event_run_id}"%'],
            )
            if existing is not None:
                return
        except Exception:
            pass
        extra = None
        target_seq = token_rec.get("continuation_target_seq")
        if auto_reopen is not None:
            target_seq = auto_reopen.get("target_seq")
            extra = {
                "auto_reopened": bool(auto_reopen.get("auto_reopened")),
                "target_doc_id": auto_reopen.get("target_doc_id") or doc.get("doc_id"),
                "auto_reopen_skipped": auto_reopen.get("auto_reopen_skipped"),
            }
        event_logger.log_continuous_work_failed(
            project_id=doc.get("project_id"),
            actor_user_id=actor_user_id,
            document_id=doc_row.get("id"),
            doc_id=doc["doc_id"],
            group_id=doc.get("group_id"),
            run_id=run.get("run_id"),
            case_passed=run.get("case_passed"),
            case_failed=run.get("case_failed"),
            error=run.get("error"),
            target_seq=target_seq,
            extra=extra,
        )
    except Exception:
        logger.warning(
            "continuous_work_failed signal failed for run %s (ignored)",
            run.get("run_id"), exc_info=True,
        )


def _emit_auto_reopen_refresh(doc: dict, run: dict, result: dict) -> None:
    """Refresh every watching browser only after the rework transaction committed.

    Carries the reason code (not a rendered sentence) so the client localizes the
    "returned to the pre-approval step" notice from its own locale bundle.
    """
    _broadcast(
        "group_view_refresh",
        doc,
        {
            "group_id": doc.get("group_id"),
            "reason": "test_run_code_failure_auto_reopen",
            "doc_id": result.get("target_doc_id") or doc.get("doc_id"),
            "target_seq": result.get("target_seq"),
            "run_id": run.get("run_id"),
        },
    )




def _emit_started(doc: dict, run: dict) -> None:
    items = db_test_runs.list_cases(run["run_id"])
    _broadcast(
        "test_run_started",
        doc,
        {
            "doc_id": doc["doc_id"],
            "run_id": run["run_id"],
            "case_total": run.get("case_total"),
            "setup_total": run.get("setup_total")
            if run.get("setup_total") is not None
            else len([item for item in items if (item.get("kind") or "") in {"setup", "service", "wait"}]),
            "teardown_total": run.get("teardown_total")
            if run.get("teardown_total") is not None
            else len([item for item in items if (item.get("kind") or "") == "teardown"]),
        },
    )


def _emit_stage_finished(
    doc: dict,
    run: dict,
    stage: str,
    *,
    ok: bool,
    total: int,
    failed: int,
    duration_ms: int,
    failed_step: Optional[str] = None,
) -> None:
    payload = {
        "doc_id": doc["doc_id"],
        "run_id": run["run_id"],
        "stage": stage,
        "ok": ok,
        "steps_total": total,
        "steps_failed": failed,
        "duration_ms": duration_ms,
    }
    if failed_step:
        payload["failed_step"] = failed_step
    _broadcast("test_stage_finished", doc, payload)


def _emit_case_finished(doc: dict, run: dict, case: dict, idx: int, total: int) -> None:
    _broadcast(
        "test_case_finished",
        doc,
        {
            "doc_id": doc["doc_id"],
            "run_id": run["run_id"],
            "case_no": case.get("case_no"),
            "case_title": case.get("case_title"),
            "case_index": idx,
            "case_total": total,
            "result": case.get("result"),
            "exit_code": case.get("exit_code"),
            "duration_ms": case.get("duration_ms"),
        },
    )


def _emit_finished(doc: dict, run: dict, tsr_doc_id: Optional[str]) -> None:
    _broadcast(
        "test_run_finished",
        doc,
        {
            "doc_id": doc["doc_id"],
            "run_id": run["run_id"],
            "status": run.get("status"),
            "case_total": run.get("case_total"),
            "case_passed": run.get("case_passed"),
            "case_failed": run.get("case_failed"),
            "error": run.get("error"),
            "tsr_doc_id": tsr_doc_id,
        },
    )
    _broadcast(
        "group_view_refresh",
        doc,
        {"group_id": doc.get("group_id"), "reason": "test_run_finished"},
    )


def _broadcast(event_type: str, doc: dict, payload: dict) -> None:
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
                project=doc.get("project_id"),
                group_id=doc.get("group_id"),
                doc_id=doc.get("doc_id"),
            )
        )
    except Exception:
        logger.warning("test run SSE broadcast failed", exc_info=True)


def user_can_run_tests(user_id: str, project_id: str, is_admin: bool = False) -> bool:
    return bool(is_admin) or has_permission(user_id, project_id, "perm_test_run")


def token_can_run_tests(user_id: str, project_id: str) -> bool:
    """perm_test_run gate for worker-token callers (inbox action:test_run).

    Same semantics as the UI routes' user_can_run_tests — is_admin bypass first. The
    inbox handler previously called has_permission alone, which re-created the 0086
    unpopulated-RBAC trap for admin-issued chain tokens (group 0150).
    """
    from modules.flow_gate.db import users as db_users

    user = db_users.get_by_id(user_id) or {}
    return user_can_run_tests(user_id, project_id, bool(user.get("is_admin")))


class TestRunWorker:
    def __init__(self, poll_interval: float = RUNNER_POLL_SEC) -> None:
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="test-run-worker")
        self._thread.start()
        logger.info("[TestRunWorker] started")

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                run = db_test_runs.pick_next_running()
                if run is not None:
                    execute_run(run)
            except Exception:
                logger.exception("[TestRunWorker] processing failed")
            self._stop.wait(self._poll_interval)


_worker: Optional[TestRunWorker] = None
_worker_lock = threading.Lock()


def get_worker() -> TestRunWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = TestRunWorker()
        return _worker


def startup() -> None:
    try:
        db_test_runs.mark_orphaned_running()
    except Exception:
        logger.warning("failed to mark orphaned test runs", exc_info=True)
    get_worker().start()
