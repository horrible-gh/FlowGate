"""Global engine test-recipe registry + test-run auto-recovery (flowgate.default.0157).

Design chain: R0001 → D0002 → P0003 → L0004 → DB0005 → T0006. Implements the L logic:
  - engine normalization / identity, manual+system CRUD with the suppressed-tombstone model
  - the command help API surface (list / single-engine) workers read to author TS setup steps
  - auto-learning: a PASSED remote run's setup/run command is reflected into its engine's recipe
  - failure classification (infra vs code) and the auto-recovery loop that re-fires infra failures
    with a fresh test_run repair token, escalating to the user only when the attempt cap is hit
  - the TS-mention "Engine recipes" block

Storage: db.engine_recipes (migration 057), GLOBAL scope. The recovery loop and repair tokens reuse
existing tables (test_runs, tokens) with no schema change — attempt counts are derived from test_runs
history (L §2-6). Every reflect/recovery entry point swallows its own errors: none may change a run
verdict (L §5), matching the 0152 isolation principle.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from modules.flow_gate.db import engine_recipes as db
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db.connection import now_iso

logger = logging.getLogger(__name__)

# ── L §1 parameters (single source of truth) ─────────────────────────────────
MAX_REPAIR_ATTEMPTS = 3          # per-doc infra-failure auto re-fire cap (0156.0002-CH user-confirmed)
ENGINE_MAX_LEN = 50
LABEL_MAX_LEN = 100
SETUP_MAX_LEN = 1000
RUN_EXAMPLE_MAX_LEN = 500
NOTES_MAX_LEN = 500
MAX_RECIPES = 100
HELP_MAX_ITEMS = 50              # also enforced by the SQL LIMIT (DB Q1)
LOG_TAIL_LINES = 20

_WS_RE = re.compile(r"\s+")

INFRA = "infra"
CODE = "code"

# L §2-5. Substrings that mark an environment/infra failure (test never got to run). Case-sensitive
# except where the tool prints them; kept conservative — the default verdict is CODE, so a missed
# infra pattern degrades safely to the (correct-for-code) rework chain, never a false auto-refire.
_INFRA_PATTERNS = (
    "command not found",     # bash/sh
    ": not found",           # dash, e.g. "sh: 1: npm: not found"
    "No module named",       # python tool absent
    "ENOENT",                # node missing binary/path
)
_INFRA_ERROR_CODES = {"setup_failed", "doc_not_found", "src_root_missing", "runner_timeout",
                      "orphaned_by_restart"}


class EngineRecipeValidationError(ValueError):
    """422 — bad input (empty / too-long field, or the list is full)."""


class EngineRecipeConflictError(ValueError):
    """409 — an active row with the same normalized engine already exists."""


# ── normalization / helpers ──────────────────────────────────────────────────

def normalize_engine(raw: str) -> str:
    """L §2-1: trim, lowercase, collapse internal whitespace runs to a single space."""
    return _WS_RE.sub(" ", (raw or "").strip()).lower()


def _normalize_text(raw: str) -> str:
    """trim + collapse internal whitespace runs (case preserved) — 0152 normalize_command."""
    return _WS_RE.sub(" ", (raw or "").strip())


def _truncate(text: Optional[str], limit: int) -> str:
    if not text:
        return ""
    return text[:limit]


def _origin_of(actor: str) -> str:
    if actor == "seed":
        return "seed"
    if actor == "auto-learn":
        return "auto"
    return "worker"


def _to_view(row: Optional[dict], *, used_by_project: Optional[bool] = None) -> Optional[dict]:
    """P §item fields wire shape. `status` is internal (only active rows are exposed)."""
    if row is None:
        return None
    view = {
        "id": row.get("id"),
        "engine": row.get("engine"),
        "label": row.get("label") or "",
        "setup": row.get("setup") or "",
        "run_example": row.get("run_example") or "",
        "notes": row.get("notes") or "",
        "origin": row.get("origin"),
        "last_success_run_id": row.get("last_success_run_id"),
        "last_success_at": row.get("last_success_at"),
        "updated_by": row.get("updated_by") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    if used_by_project is not None:
        view["used_by_project"] = bool(used_by_project)
    return view


# ── help API (P §help 목록 / 단건) ────────────────────────────────────────────

def list_help(engine: Optional[str] = None) -> list[dict]:
    """Active recipes as views. With `engine`, exact-match single (empty list if none — never 404)."""
    if engine:
        row = db.get_active_by_engine(normalize_engine(engine))
        return [_to_view(row)] if row else []
    return [_to_view(r) for r in db.list_active()]


# ── management CRUD (P §등록/수정/비활성; L §2-2) ──────────────────────────────

def create(engine_raw: str, label: str, setup: str, run_example: str, notes: str, actor: str) -> dict:
    engine = normalize_engine(engine_raw)
    if engine == "" or len(engine) > ENGINE_MAX_LEN:
        raise EngineRecipeValidationError("engine is required (<= 50 chars)")
    setup_n = _normalize_text(setup)
    if setup_n == "" or len(setup_n) > SETUP_MAX_LEN:
        raise EngineRecipeValidationError("setup is required (<= 1000 chars)")
    fields = {
        "label": _truncate(label, LABEL_MAX_LEN),
        "setup": setup_n,
        "run_example": _truncate(_normalize_text(run_example), RUN_EXAMPLE_MAX_LEN),
        "notes": _truncate(notes, NOTES_MAX_LEN),
    }
    existing = db.find_by_engine(engine)          # includes suppressed
    if existing is not None:
        if existing.get("status") == "active":
            raise EngineRecipeConflictError(f"active recipe already exists for engine: {engine}")
        # suppressed → revive (L §2-2). last_success_* preserved (past success is a fact).
        revived = db.update_row(
            existing["id"],
            {**fields, "status": "active", "origin": _origin_of(actor), "updated_by": actor},
        )
        return _to_view(revived)
    if db.count_active() >= MAX_RECIPES:
        raise EngineRecipeValidationError("engine recipe list is full")
    row = db.insert(
        engine, fields["label"], fields["setup"], fields["run_example"], fields["notes"],
        _origin_of(actor), actor, status="active",
    )
    return _to_view(row)


def patch(recipe_id: int, fields: dict, actor: str) -> Optional[dict]:
    """Update an ACTIVE row. Returns None (→404) for missing/suppressed.

    `engine`, `origin`, `last_success_*` are not caller-editable (P §수정) — attempting to change
    engine is a 422. `updated_by` is stamped with the caller automatically.
    """
    row = db.get_by_id(recipe_id)
    if row is None or row.get("status") != "active":
        return None
    if fields.get("engine") is not None and normalize_engine(fields["engine"]) != row.get("engine"):
        raise EngineRecipeValidationError("engine is immutable; register a new recipe instead")
    updates: dict = {}
    if fields.get("label") is not None:
        updates["label"] = _truncate(fields["label"], LABEL_MAX_LEN)
    if fields.get("setup") is not None:
        setup_n = _normalize_text(fields["setup"])
        if setup_n == "" or len(setup_n) > SETUP_MAX_LEN:
            raise EngineRecipeValidationError("invalid setup")
        updates["setup"] = setup_n
    if fields.get("run_example") is not None:
        updates["run_example"] = _truncate(_normalize_text(fields["run_example"]), RUN_EXAMPLE_MAX_LEN)
    if fields.get("notes") is not None:
        updates["notes"] = _truncate(fields["notes"], NOTES_MAX_LEN)
    if not updates:
        return _to_view(row)
    updates["updated_by"] = actor
    return _to_view(db.update_row(recipe_id, updates))


def suppress(recipe_id: int, actor: str) -> bool:
    """DELETE → tombstone. Returns False when there is no active row (→404)."""
    row = db.get_by_id(recipe_id)
    if row is None or row.get("status") != "active":
        return False
    db.update_row(recipe_id, {"status": "suppressed", "updated_by": actor})
    return True


# ── engine detection (auto-learn + repair, L §2-3) ────────────────────────────

_ENGINE_TAG_RE = re.compile(r"^engine:\s*(\S+)", re.MULTILINE)


def detect_engine(doc: dict, items: list[dict]) -> Optional[str]:
    """1st: explicit `engine: <x>` tag in the TS body. 2nd: command-string scan (ordered)."""
    content = (doc or {}).get("content") or ""
    m = _ENGINE_TAG_RE.search(content)
    if m:
        return normalize_engine(m.group(1))
    cmds = " ".join((it.get("cmd") or "") for it in (items or []))
    # order = priority: the test runner wins over an incidental helper step
    if "pytest" in cmds:
        return "pytest"
    if "npm" in cmds or "npx" in cmds:
        return "npm"
    if "cargo" in cmds:
        return "cargo"
    if "go test" in cmds:
        return "go"
    if "mvn" in cmds or "gradle" in cmds:
        return "jvm"
    return None


# ── auto-learning from a PASSED run (L §2-4) ─────────────────────────────────

def reflect_from_passed_run(doc: dict, run: dict, items: list[dict]) -> None:
    """Reflect a passed run's setup/run command into its engine recipe. Never raises (L §5)."""
    try:
        engine = detect_engine(doc, items)
        if engine is None:
            return
        setup_cmds = [i.get("cmd") or "" for i in (items or []) if (i.get("kind") or "case") == "setup"]
        case_cmds = [i.get("cmd") or "" for i in (items or []) if (i.get("kind") or "case") == "case"]
        if not setup_cmds and not case_cmds:
            return
        setup = " && ".join(c for c in setup_cmds if c.strip())
        run_example = next((c for c in case_cmds if c.strip()), "")
        run_id = (run or {}).get("run_id")
        now = now_iso()
        row = db.find_by_engine(engine)           # includes suppressed
        if row is not None and row.get("status") == "suppressed":
            return                                 # tombstone — respect it (L §2-4 ④)
        if row is not None:
            updates = {
                "last_success_run_id": run_id,
                "last_success_at": now,
                "updated_by": "auto-learn",
            }
            # setup too long → keep the existing recipe body, only record the success trace (L §5)
            if setup and len(setup) <= SETUP_MAX_LEN:
                updates["setup"] = setup
            if run_example and len(run_example) <= RUN_EXAMPLE_MAX_LEN:
                updates["run_example"] = run_example
            db.update_row(row["id"], updates)
            return
        # no recipe yet → create origin='auto' (L §2-4 ③)
        if db.count_active() >= MAX_RECIPES:
            logger.info("engine-recipe auto-register skipped (list full): %s", engine)
            return
        db.insert(
            engine, engine, _truncate(setup, SETUP_MAX_LEN),
            _truncate(run_example, RUN_EXAMPLE_MAX_LEN), "", "auto", "auto-learn",
            status="active", last_success_run_id=run_id, last_success_at=now,
        )
    except Exception:
        logger.warning(
            "engine-recipe reflect failed for doc %s", (doc or {}).get("doc_id"), exc_info=True
        )


# ── failure classification (L §2-5) ──────────────────────────────────────────

def classify_failure(run: dict, items: list[dict]) -> str:
    """Return INFRA (environment/tooling — test never ran) or CODE (a real RED). Default: CODE."""
    error = (run or {}).get("error")
    if error in _INFRA_ERROR_CODES:
        return INFRA
    # R0001 group 0381: these patterns mean "the harness could not run the test", so they only
    # carry that meaning from a stage that builds the environment. A test case that RAN and
    # printed "No module named 'x'" is the product's own missing import — a real RED. Scanning
    # case logs for them mislabelled 3 of the 27 genuine REDs in production history as INFRA,
    # which silently skipped the automatic rewind. Same stage scoping as "Permission denied".
    stage_logs = "\n".join(
        (it.get("output_tail") or "") for it in (items or []) if (it.get("kind") or "case") != "case"
    )
    if any(p in stage_logs for p in _INFRA_PATTERNS):
        return INFRA
    # exit 127 (command not found) on any case
    if any((it.get("exit_code") == 127) for it in (items or [])):
        return INFRA
    # "Permission denied" only counts as infra when it comes from a setup-stage step (L §2-5)
    setup_logs = "\n".join(
        (it.get("output_tail") or "") for it in (items or []) if (it.get("kind") or "case") == "setup"
    )
    if "Permission denied" in setup_logs:
        return INFRA
    passed = (run or {}).get("case_passed") or 0
    failed = (run or {}).get("case_failed") or 0
    if passed == 0 and failed == 0:
        return INFRA                               # nothing ran at all
    return CODE


def count_infra_attempts(doc_id: str) -> int:
    """Consecutive INFRA failures since the last passed run (newest-first), L §2-6.

    Fail-safe: any error deriving the count returns MAX_REPAIR_ATTEMPTS so the loop escalates
    instead of re-firing forever (L §5).
    """
    try:
        runs = db_test_runs.list_by_doc(doc_id)    # newest first
        n = 0
        for r in runs:
            st = r.get("status")
            if st == "running":
                continue
            if st == "passed":
                break                              # count only since the last success
            cases = db_test_runs.list_cases(r.get("run_id"))
            if classify_failure(r, cases) == INFRA:
                n += 1
            else:
                break                              # a CODE failure breaks the consecutive run
        return n
    except Exception:
        logger.warning("count_infra_attempts failed for %s (fail-safe → cap)", doc_id, exc_info=True)
        return MAX_REPAIR_ATTEMPTS


# ── TS-mention block (L §2-7 / P §TS 작성 멘트) ───────────────────────────────

def build_engine_recipes_block(base_url: str) -> str:
    """Fixed English literal (locale-independent). Always emitted — even with zero recipes, so the

    first help call is what teaches the registry (L §2-7). No per-language rules (0156.0002-CH).
    """
    base = (base_url or "").rstrip("/")
    return (
        "Before writing the setup steps, fetch the engine recipes and follow one:\n"
        f"GET {base}/test-commands/help\n"
        "Pick the engine matching the code under test (e.g. ?engine=pytest) and copy its\n"
        "\"setup\" into your TS preparation steps as-is. If no recipe exists for your engine,\n"
        "author the setup yourself — a successful run will register it automatically."
    )


# ── settings visualization (P §가시화) ────────────────────────────────────────

def _used_by_project(recipe: dict, project: str) -> bool:
    """DB Q6: has this recipe's last_success_run_id run against a doc of this project?"""
    run_id = recipe.get("last_success_run_id")
    if not run_id or not project:
        return False
    try:
        run = db_test_runs.get_run(run_id)
    except Exception:
        return False
    if not run:
        return False
    doc_id = run.get("doc_id") or ""
    return doc_id.split(".")[0] == project


def list_for_project_view(project: str) -> list[dict]:
    """Global active recipes, each flagged whether this project's runs referenced it (read-only)."""
    return [_to_view(r, used_by_project=_used_by_project(r, project)) for r in db.list_active()]


# ── auto-recovery loop (L §2-6 / P §인프라 실패·재발사·에스컬레이션) ─────────────

def handle_run_failure(doc: dict, run: dict, items: list[dict]) -> str:
    """Classify a failed run; on an unmanned-chain infra failure re-fire it (or escalate at the cap).

    Gated exactly like _maybe_notify_chain_failure: only a run whose consumed test_run token carries a
    continuation target is an unmanned chain — a manned/UI run leaves a human watching, so the loop
    stays out of its way. Never raises: any failure here must not touch the run verdict (L §5).

    Returns one of: "repair" (re-fire delivered), "escalated" (cap hit, user notified), "code" (real
    RED — hand to the rework chain), "skip" (not an unmanned chain), "error". The caller suppresses the
    generic "chain failed" alarm on "repair"/"escalated" since those already carry a distinct signal.
    """
    try:
        doc_id = (doc or {}).get("doc_id")
        if not doc_id:
            return "skip"
        token_rec = _consumed_chain_token(doc_id)
        if token_rec is None:
            return "skip"                          # manned / UI run — not an unmanned chain
        if classify_failure(run, items) != INFRA:
            return "code"                          # a real RED → existing rework chain (L §2-6)
        attempts = count_infra_attempts(doc_id)
        if attempts >= MAX_REPAIR_ATTEMPTS:
            _emit_escalation(doc, doc_id)
            return "escalated"
        _emit_repair(doc, run, items, token_rec, attempts + 1)
        return "repair"
    except Exception:
        logger.warning(
            "engine-recipe recovery loop failed for run %s (ignored)",
            (run or {}).get("run_id"), exc_info=True,
        )
        return "error"


def _consumed_chain_token(doc_id: str) -> Optional[dict]:
    from modules.flow_gate.db import tokens as db_tokens

    rec = db_tokens.get_latest_consumed_by_scope_doc_ref("test_run", doc_id)
    if rec is None or rec.get("continuation_target_seq") is None:
        return None
    return rec


def _log_tail(items: list[dict]) -> tuple[str, str]:
    """Return (failing_step_cmd, last-N-lines log tail) from the failed setup/case steps."""
    failed = [
        it for it in (items or [])
        if it.get("result") in {"fail", "timeout"} or (it.get("exit_code") not in (None, 0))
    ]
    target = failed[-1] if failed else (items[-1] if items else {})
    cmd = (target or {}).get("cmd") or ""
    tail = (target or {}).get("output_tail") or ""
    lines = tail.splitlines()
    return cmd, "\n".join(lines[-LOG_TAIL_LINES:])


def _emit_repair(doc: dict, run: dict, items: list[dict], token_rec: dict, attempt: int) -> None:
    from modules.flow_gate.services import token_service
    from modules.flow_gate.workflow import event_logger

    doc_id = doc["doc_id"]
    engine_hint = detect_engine(doc, items)
    issue = token_service.issue(
        project=doc.get("project_id") or token_rec.get("project") or "",
        group_id=doc.get("group_id") or token_rec.get("group_id"),
        action_scope="test_run",
        doc_ref=doc_id,
        issued_to=token_rec.get("issued_to") or "system",
        # inherit the chain's continuation fields so the re-fired run is still recognized as unmanned
        continuation_target_seq=token_rec.get("continuation_target_seq"),
        continuation_review_mode=bool(token_rec.get("continuation_review_mode")),
        continuation_locale=token_rec.get("continuation_locale"),
        # N/T authoring mode (group 0230 R0001 / T0005 WI-5): carry the instruction-mode choice
        # onto the repair token too, so a test auto-recovery retry keeps the run's N/T handling
        # policy on every subsequent hop instead of silently reverting to auto_approved.
        continuation_instruction_mode=token_rec.get("continuation_instruction_mode"),
        # 0352 T0004 §3.4: same reasoning — the ai_direct per-item_seq auto-approve selection
        # must ride the repair token too, or a chain that selected specific N/T steps loses
        # that selection the moment a TS auto-recovery retry fires.
        continuation_auto_approve_item_seqs=token_rec.get("continuation_auto_approve_item_seqs"),
    )
    mention = _build_repair_mention(doc, run, items, attempt, issue["raw_token"], engine_hint)
    doc_row_id = None
    try:
        from modules.flow_gate.db import documents as db_docs

        doc_row_id = (db_docs.get_by_id(doc_id) or {}).get("id")
    except Exception:
        pass
    event_logger.log_test_run_repair(
        project_id=doc.get("project_id"),
        actor_user_id=token_rec.get("issued_to") or "system",
        document_id=doc_row_id,
        doc_id=doc_id,
        group_id=doc.get("group_id"),
        run_id=(run or {}).get("run_id"),
        attempt=attempt,
        max_attempts=MAX_REPAIR_ATTEMPTS,
        engine=engine_hint,
        error=(run or {}).get("error"),
        token=issue["raw_token"],
        mention=mention,
    )
    logger.info(
        "engine-recipe repair delivery for %s attempt %s/%s (engine=%s)",
        doc_id, attempt, MAX_REPAIR_ATTEMPTS, engine_hint,
    )


def _build_repair_mention(
    doc: dict, run: dict, items: list[dict], attempt: int, raw_token: str, engine_hint: Optional[str]
) -> str:
    from config import settings

    context = settings.CONTEXT.rstrip("/")
    api = f"{context}/api/v1"
    doc_id = doc["doc_id"]
    failing_cmd, tail = _log_tail(items)
    help_url = f"{api}/test-commands/help" + (f"?engine={engine_hint}" if engine_hint else "")
    return (
        f"## Test-run repair (attempt {attempt} of {MAX_REPAIR_ATTEMPTS})\n"
        "---\n"
        f"The remote test run for {doc_id} FAILED before the tests could run.\n"
        f"run_id: {(run or {}).get('run_id')}\n"
        f"error: {(run or {}).get('error') or 'setup_failed'}\n"
        f"failing step: {failing_cmd}\n"
        "log tail:\n"
        f"{tail}\n\n"
        "Fix the TS preparation steps using an engine recipe:\n"
        f"GET {help_url}\n"
        "Then re-fire the run yourself with the token below. Do NOT stop to ask the user.\n\n"
        f"Re-fire: POST {api}/documents/test-run\n"
        f"Authorization: Bearer {raw_token}\n"
        "{\n"
        f'  "doc_id": "{doc_id}"\n'
        "}\n"
    )


def _emit_escalation(doc: dict, doc_id: str) -> None:
    from modules.flow_gate.workflow import event_logger

    attempts = []
    try:
        runs = db_test_runs.list_by_doc(doc_id)
        for r in runs:
            if r.get("status") == "passed":
                break
            if r.get("status") != "failed":
                continue
            cases = db_test_runs.list_cases(r.get("run_id"))
            if classify_failure(r, cases) != INFRA:
                break
            _, tail = _log_tail(cases)
            attempts.append({
                "run_id": r.get("run_id"),
                "error": r.get("error"),
                "summary": (tail.splitlines()[-1] if tail.strip() else (r.get("error") or "")),
            })
            if len(attempts) >= MAX_REPAIR_ATTEMPTS:
                break
    except Exception:
        logger.warning("escalation summary build failed for %s", doc_id, exc_info=True)
    doc_row_id = None
    try:
        from modules.flow_gate.db import documents as db_docs

        doc_row_id = (db_docs.get_by_id(doc_id) or {}).get("id")
    except Exception:
        pass
    event_logger.log_test_run_repair_exhausted(
        project_id=doc.get("project_id"),
        actor_user_id="system",
        document_id=doc_row_id,
        doc_id=doc_id,
        group_id=doc.get("group_id"),
        attempts=attempts,
    )
    logger.warning("engine-recipe repair EXHAUSTED for %s after %s attempts", doc_id, len(attempts))
