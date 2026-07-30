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
WAIT_TIMEOUT_SEC = 60
WAIT_POLL_SEC = 0.5
MAX_SETUP_STEPS = 20
MAX_TEARDOWN_STEPS = 20
MAX_SERVICES = 5
TSR_CASE_EXCERPT_CHARS = 1000

_admission_lock = threading.Lock()


class TestCaseParseError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# T0009: TS 문법의 영어 별칭. 파서는 아래 두 이름을 모두 받고, 필드명도 별칭으로
# 정규화해서 이후 로직(예: fields.get("기대", "")) 은 그대로 한국어 키를 쓴다.
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


def _execute_run_inner(run: dict) -> None:
    doc = db_docs.get_by_id(run["doc_id"])
    if doc is None:
        db_test_runs.finish_run(
            run_id=run["run_id"], status="failed", error="doc_not_found"
        )
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
            run["run_id"],
            doc.get("project_id"),
            root,
        )
        db_test_runs.finish_run(
            run_id=run["run_id"],
            status="failed",
            error="src_root_missing",
        )
        _emit_finished(doc, db_test_runs.get_run(run["run_id"]) or run, None)
        return

    _record_source_root(doc, run, root)

    port = _allocate_port()
    scratch = _scratch_dir(doc, run["run_id"])
    scratch.mkdir(parents=True, exist_ok=True)
    db_test_runs.set_run_port(run["run_id"], port)
    run = db_test_runs.get_run(run["run_id"]) or {**run, "port": port}

    all_items = db_test_runs.list_cases(run["run_id"])
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
            doc, run, setup_steps, root, port, scratch, env, services, run_started
        )

        if not setup_failed:
            for idx, case in enumerate(cases, start=1):
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
                _execute_case(doc, run, case, idx, len(cases), root, port, scratch, env)

        _execute_teardown(doc, run, teardown_steps, root, port, scratch, env, run_started)
    finally:
        _finalize_services(services)
        _remove_scratch(scratch)

    final_cases = [
        item
        for item in db_test_runs.list_cases(run["run_id"])
        if (item.get("kind") or "case") == "case"
    ]
    if setup_failed:
        db_test_runs.finish_run(
            run_id=run["run_id"],
            status="failed",
            case_passed=0,
            case_failed=0,
            error=setup_error or "setup_failed",
        )
        finished_run = db_test_runs.get_run(run["run_id"]) or run
        _emit_finished(doc, finished_run, None)
        # flowgate.default.0157: a setup failure is the canonical INFRA case — try the auto-recovery loop
        # first. If it re-fires (or escalates at the cap) it owns the signal, so suppress the generic
        # "chain failed" alarm; otherwise fall through to it. Best-effort; never affects the verdict.
        recovery = engine_recipe_service.handle_run_failure(
            doc, finished_run, db_test_runs.list_cases(run["run_id"])
        )
        if recovery not in ("repair", "escalated"):
            # R0001 group 0154 / NR0004 Gap A: surface the silent stop (best-effort).
            _maybe_notify_chain_failure(doc, finished_run)
        return

    passed = sum(1 for case in final_cases if case.get("result") == "pass")
    failed = sum(1 for case in final_cases if case.get("result") in {"fail", "timeout"})
    status = "passed" if failed == 0 else "failed"
    tsr_doc_id = None
    report_error = None
    if status == "passed" and not process_service.is_group_disposed(doc.get("group_id")):
        try:
            all_final_items = db_test_runs.list_cases(run["run_id"])
            tsr_doc_id = assemble_tsr(doc, db_test_runs.get_run(run["run_id"]) or run, all_final_items)
        except Exception as exc:
            # 0257 NR0003 §2: a passed run with no report is not a success — record it as a
            # distinct terminal error rather than leaving a green run whose TSR never existed.
            logger.warning("TSR assembly failed for %s: %s", run["run_id"], exc, exc_info=True)
            status = "failed"
            report_error = "report_assembly_failed"
        # flowgate.default.0152: reflect this passed run's setup/case commands into the project's
        # verified test-command registry (L §2-4). Must never affect the run verdict (L §5) — the
        # reflect call swallows its own errors; this guard mirrors the TSR disposed/passed gate.
        # Still reflected on report_assembly_failed by intent: every case passed, so the commands
        # are verified — only the report write failed, which says nothing about the commands.
        try:
            passed_items = db_test_runs.list_cases(run["run_id"])
            test_command_service.reflect_from_passed_run(doc, passed_items)
            # flowgate.default.0157: also reflect this run's setup/run command into the GLOBAL engine
            # recipe (auto-learn, L §2-4). Self-isolating like the 0152 reflect — never affects verdict.
            engine_recipe_service.reflect_from_passed_run(
                doc, db_test_runs.get_run(run["run_id"]) or run, passed_items
            )
        except Exception as exc:
            logger.warning(
                "test-command reflect failed for %s: %s", run["run_id"], exc, exc_info=True
            )

    db_test_runs.finish_run(
        run_id=run["run_id"],
        status=status,
        case_passed=passed,
        case_failed=failed,
        tsr_doc_id=tsr_doc_id,
        error=report_error,
    )
    finished_run = db_test_runs.get_run(run["run_id"]) or run
    _emit_finished(doc, finished_run, tsr_doc_id)
    if report_error is not None:
        # 0257 NR0003 §2: terminal, so it must NOT enter the 0157 recovery loop below — every
        # case passed, so there is no INFRA fault to repair and re-firing green tests cannot
        # produce the missing report. Surface it once and stop.
        _maybe_notify_chain_failure(doc, finished_run)
    elif status == "failed":
        # flowgate.default.0157: route the failure through the auto-recovery loop first. An INFRA
        # failure (env/tooling) is re-fired or escalated and owns its own signal; a real RED (CODE)
        # returns "code" and falls through to the chain-failed alarm + existing rework chain.
        recovery = engine_recipe_service.handle_run_failure(
            doc, finished_run, db_test_runs.list_cases(run["run_id"])
        )
        if recovery not in ("repair", "escalated"):
            # R0001 group 0154 / NR0004 Gap A: a RED chain run assembles no TSR and stops with nothing to
            # hand on — surface it once so the unmanned chain no longer goes silent (was: only an
            # ephemeral SSE broadcast). Best-effort; never affects the verdict.
            _maybe_notify_chain_failure(doc, finished_run)


def _record_source_root(doc: dict, run: dict, root: Path) -> None:
    """Persist the tree this run executes in, before a single command runs.

    0280 NR0003 §6-2: the runner resolved a root and then forgot it, so "the tests
    ran in main" could never be confirmed or refuted — a correct worktree run and a
    silent fallback to base were indistinguishable after the fact. Written early so
    a run that dies mid-way still carries its location. Best-effort in both
    directions: bookkeeping must never fail a run, and a failure to record must not
    masquerade as a recorded base run (the column stays NULL → "기록 없음").
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
) -> tuple[bool, Optional[str]]:
    if not steps:
        return False, None
    stage_started = time.monotonic()
    failed_step = None
    for step in steps:
        if time.monotonic() - run_started > RUN_TIMEOUT_SEC:
            _mark_step_timeout(step, "[run timeout during setup]")
            failed_step = step.get("case_no")
            break
        kind = step.get("kind")
        if kind == "setup":
            result = _execute_step_command(
                step, root, port, scratch, env, SETUP_STEP_TIMEOUT_SEC
            )
        elif kind == "service":
            result = _start_service_step(step, root, port, scratch, env, services)
        elif kind == "wait":
            result = _execute_wait_step(step, port)
        else:
            result = "fail"
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
) -> None:
    if not steps:
        return
    stage_started = time.monotonic()
    for step in steps:
        if time.monotonic() - run_started > RUN_TIMEOUT_SEC:
            _mark_step_timeout(step, "[run timeout during teardown]")
            continue
        _execute_step_command(step, root, port, scratch, env, TEARDOWN_STEP_TIMEOUT_SEC)
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
) -> None:
    started = time.monotonic()
    result, exit_code, output = _run_shell_command(
        _replace_placeholders(case["cmd"], port, scratch),
        root,
        CASE_TIMEOUT_SEC,
        env,
    )
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
) -> str:
    started = time.monotonic()
    result, exit_code, output = _run_shell_command(
        _replace_placeholders(step["cmd"], port, scratch),
        root,
        timeout,
        env,
    )
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
) -> Optional[str]:
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


def _execute_wait_step(step: dict, port: int) -> str:
    started = time.monotonic()
    deadline = started + WAIT_TIMEOUT_SEC
    result = "timeout"
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=WAIT_POLL_SEC):
                result = "pass"
                break
        except OSError:
            time.sleep(WAIT_POLL_SEC)
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


def _finalize_services(services: list[dict]) -> None:
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


def assemble_tsr(doc: dict, run: dict, cases: list[dict]) -> str:
    """Assemble this run's TSR into the TS's single active report slot (0257 NR0003 §1).

    Two distinct re-entry paths land here, and they need different answers:

    * the *same* run re-finishing (worker retry) — return the report it already produced,
      keyed on run_id. Narrow by construction: finish_run records tsr_doc_id only after
      this returns, so this guard is False on a first assembly and True only afterwards.
    * a *new* run for the same TS (the B0001 rerun) — revise the report already holding
      the workflow slot instead of reserving a second number. Reserving one per attempt is
      what produced two TSR documents, the second of which the slot never adopted.

    Per-attempt history stays in test_runs/test_run_cases; the workflow keeps one document.
    """
    if run.get("tsr_doc_id"):
        return str(run["tsr_doc_id"])

    group_id = doc.get("group_id")
    project_id = doc.get("project_id")
    module = doc.get("module") or "none"
    branch = doc.get("branch") or "main"
    if not group_id or not project_id:
        raise RuntimeError("TS document has no group/project")

    title = f"테스트 레포트 — {doc.get('title') or doc['doc_id']}"
    content = _tsr_content(doc, run, cases, title)

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
    # empty — the "연결된 MD 파일이 없습니다" preview of B0001.
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


def _tsr_content(doc: dict, run: dict, cases: list[dict], title: str) -> str:
    setup = [case for case in cases if (case.get("kind") or "case") in {"setup", "service", "wait"}]
    case_rows = [case for case in cases if (case.get("kind") or "case") == "case"]
    teardown = [case for case in cases if (case.get("kind") or "case") == "teardown"]
    prep_rows = []
    for step in [*setup, *teardown]:
        duration = step.get("duration_ms")
        duration_text = "-" if duration is None else f"{round(duration / 1000, 1)}s"
        result = step.get("result") or ("기동" if (step.get("kind") or "") == "service" else "")
        prep_rows.append(
            f"| {step.get('case_no')} | {_kind_label(step.get('kind'))} | "
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
            f"> 대상 TS: {doc['doc_id']} (revision {run.get('revision_no')})",
            f"> 실행 회차: {run['run_id']}",
            f"> 실행 시각: {run.get('started_at')} ~ {now_iso()}",
            f"> 결과: 통과 {len(case_rows)}/{len(case_rows)}",
            "",
            "## 실행 환경",
            "",
            f"- 프로젝트: {doc.get('project_id')} / 문서 브랜치: {doc.get('branch') or 'main'}",
            f"- 실행 위치: {_src_root_label(run)}",
            f"- 할당 포트: {run.get('port')} / 스크래치: 회차 전용 (종료 시 삭제됨)",
            "- 실행 주체: FlowGate 서버 (워커 로컬 환경 불개입)",
            "",
            "## 준비 / 정리",
            "",
            "| 단계 | 종류 | 명령 | 결과 | 소요 |",
            "|---|---|---|---|---|",
            *prep_rows,
            "",
            "## 케이스별 결과",
            "",
            "| 케이스 | 이름 | 결과 | exit | 소요 |",
            "|---|---|---|---|---|",
            *result_rows,
            "",
            "## 케이스별 출력 발췌",
            "",
            *excerpts,
            f"*이 레포트는 실행 회차 {run['run_id']} 의 기록으로부터 FlowGate가 자동 조립했다.*",
            "",
        ]
    )


# 0280 NR0003 §4-A: the 실행 환경 block used to print doc['branch'] (always "main",
# it is the *document's* branch, not the worktree's) next to the hardcoded string
# "프로젝트 소스 루트 (src_root)". A run that executed correctly in the group worktree
# was therefore reported as having run in main — the direct source of the repeated
# "tests run in main" reports. These labels render what the run actually recorded.
_SRC_ROOT_REASON_LABELS = {
    "git_integration_off": "이 프로젝트는 git 통합이 꺼져 있다",
    "no_group_git_state": "git 통합은 켜져 있으나 그룹의 git 상태 기록이 없다",
    "worktree_unregistered": "그룹 워크트리가 등록돼 있지 않다 (머지/푸시 후 해제된 경우 포함)",
    "state_branch_empty": "그룹 git 상태에 브랜치 값이 없다",
    "project_name_missing": "프로젝트명을 확인할 수 없다",
    "worktree_dir_missing": "등록된 워크트리 디렉터리가 실제로 존재하지 않는다",
    "no_group_context": "그룹 정보 없이 실행됐다",
    "resolution_error": "워크트리 해석 중 오류가 발생했다",
}


def _src_root_label(run: dict) -> str:
    """Render the run's recorded execution root for the TSR 실행 환경 block."""
    stored = run.get("source_root")
    kind = run.get("source_root_kind")
    if not stored:
        # Runs predating 0280 T0005, and runs whose bookkeeping failed. Say so
        # rather than repeating the old guess.
        return "기록 없음 (이 회차는 실행 루트를 기록하지 않았다)"
    if kind == "worktree":
        return f"그룹 워크트리 `{stored}` — 작업 브랜치에서 실행됨"
    if kind == "unknown":
        return f"`{stored}` (워크트리 여부 미상)"
    reason = _SRC_ROOT_REASON_LABELS.get(kind, kind)
    return f"프로젝트 base 트리 `{stored}` — 워크트리 미사용: {reason}"


def _kind_label(kind: Optional[str]) -> str:
    return {
        "setup": "cmd",
        "service": "기동",
        "wait": "대기",
        "teardown": "cmd",
    }.get(kind or "", kind or "")


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

    L0006 (0138) deliberately left the TSR gate to "사람 또는 무인체인 승인 절차"; this is
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



def _maybe_notify_chain_failure(doc: dict, run: dict) -> None:
    """Emit the one terminal "연속작업 실패" signal for an unmanned-chain test_run that went RED.

    R0001 group 0154 / NR0004 Gap A: a failed chain run assembles no TSR (tsr_doc_id stays null) and the
    chain stops with nothing to hand on — but until now that stop produced no persistent, discoverable
    signal at all (only a transient SSE `test_run_finished` broadcast), so the unmanned chain went
    silent and nobody knew until the run record was opened by hand (NR0004 §2.4). This records a single
    workflow_event that the dashboard promotes to the 🔔 feed as the failure counterpart of
    continuous_work_ended.

    Gated exactly like _maybe_chain_auto_approve_tsr: only the continuation-carrying token minted by
    advance_workflow's TSR-head wiring counts as an unmanned chain (a manned test-run-request token
    leaves continuation_target_seq NULL — that human is already watching, so no feed row is added).
    Best-effort: any failure here is swallowed and must never affect the run verdict.
    """
    try:
        from modules.flow_gate.db import tokens as db_tokens
        from modules.flow_gate.workflow import event_logger

        token_rec = db_tokens.get_latest_consumed_by_scope_doc_ref(
            "test_run", doc["doc_id"]
        )
        if token_rec is None or token_rec.get("continuation_target_seq") is None:
            return  # manned delegation (or UI) run — human keeps watch, no unmanned-chain alarm
        actor_user_id = token_rec.get("issued_to") or "system"
        doc_row = db_docs.get_by_id(doc["doc_id"]) or {}
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
            target_seq=token_rec.get("continuation_target_seq"),
        )
    except Exception:
        logger.warning(
            "continuous_work_failed signal failed for run %s (ignored)",
            run.get("run_id"), exc_info=True,
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
