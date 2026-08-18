"""Per-project verified test-command registry (flowgate.default.0152).

Design chain: R0001 → D0002 → P0003 → L0004 → T0005. Implements the L logic — command
normalization/identity, manual CRUD with the suppressed-tombstone model, auto-reflection from
passed remote test runs, and the TS-mention "Verified test commands" block.

Storage: db.project_test_commands (migration 055). RBAC reuses project.settings.* like
project_messages (P §auth). The block/description literals are English, locale-independent
(L §2-5), matching the existing TS authoring guide.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from modules.flow_gate.db import project_test_commands as db
from modules.flow_gate.db.connection import now_iso

logger = logging.getLogger(__name__)

# L §1 parameters
MAX_COMMANDS_PER_PROJECT = 50
MENTION_MAX_ITEMS = 20
COMMAND_MAX_LEN = 500
DESCRIPTION_MAX_LEN = 200

_WS_RE = re.compile(r"\s+")

# Host-OS awareness (flowgate.default.0277 B0001 → NR0003 §4 F2).
# A registered command is a shell string, and the shell differs per host: FlowGate spawns
# every test command with shell=True, which resolves to %COMSPEC% (cmd.exe) on Windows and
# /bin/sh on POSIX. A command that passed on one is not evidence for the other, so the
# registry records WHERE it passed and the TS-mention block filters on that.
OS_WINDOWS = "nt"
OS_POSIX = "posix"

_SHELL_NAME = {OS_WINDOWS: "cmd.exe", OS_POSIX: "/bin/sh"}


def current_os() -> str:
    """'nt' on Windows, 'posix' elsewhere — the key the registry stores and filters on."""
    return OS_WINDOWS if os.name == "nt" else OS_POSIX


def current_shell() -> str:
    """The shell `shell=True` actually resolves to on this host."""
    return _SHELL_NAME.get(current_os(), "/bin/sh")


class TestCommandValidationError(ValueError):
    """422 — bad input (empty / too-long command, or the list is full)."""


class TestCommandConflictError(ValueError):
    """409 — an active row with the same normalized command already exists."""


# ── normalization / helpers ──────────────────────────────────────────────────

def normalize_command(raw: str) -> str:
    """L §2-1: trim, then collapse internal whitespace runs to a single space.

    Case-sensitive — shell commands treat case and argument order as significant, so no
    further semantic equivalence is attempted. Identity is exact match of this result.
    """
    return _WS_RE.sub(" ", (raw or "").strip())


def _truncate(text: Optional[str], limit: int) -> str:
    if not text:
        return ""
    return text[:limit]


def _short_doc_id(doc_id: str) -> str:
    """Group + doc-code short form, e.g. 'flowgate.default.0152.0005-TS' -> '0152.0005-TS'."""
    parts = (doc_id or "").split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return doc_id or ""


def _to_view(row: Optional[dict]) -> Optional[dict]:
    """P §item fields wire shape. `status` is internal (only active rows are exposed)."""
    if row is None:
        return None
    return {
        "id": row.get("id"),
        "project_id": row.get("project"),
        "command": row.get("command"),
        "description": row.get("description") or "",
        "origin": row.get("origin"),
        "last_success_at": row.get("last_success_at"),
        "verified_os": row.get("verified_os"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ── management API (P §list/add/update/delete) ──────────────────────────────

def list_for_view(project: str) -> list[dict]:
    return [_to_view(r) for r in db.list_active(project)]


def create_manual(project: str, command_raw: str, description: str) -> dict:
    cmd = normalize_command(command_raw)
    if cmd == "":
        raise TestCommandValidationError("command is required")
    if len(cmd) > COMMAND_MAX_LEN:
        raise TestCommandValidationError("command too long")
    desc = _truncate(description, DESCRIPTION_MAX_LEN)
    existing = db.find_by_command(project, cmd)          # includes suppressed
    if existing is not None:
        if existing.get("status") == "active":
            raise TestCommandConflictError(f"command already exists: {cmd}")
        # suppressed → revive (L §2-2). last_success_at is preserved (past success is a fact).
        revived = db.update_row(
            project,
            existing["id"],
            {"status": "active", "origin": "manual", "description": desc, "command": cmd},
        )
        return _to_view(revived)
    if db.count_active(project) >= MAX_COMMANDS_PER_PROJECT:
        raise TestCommandValidationError("command list is full")
    row = db.insert(project, cmd, desc, "manual", None, status="active")
    return _to_view(row)


def patch(project: str, command_id: int, fields: dict) -> Optional[dict]:
    """Update command/description of an ACTIVE row. Returns None (→404) for missing/suppressed.

    origin and last_success_at are not user-editable (P §update).
    """
    row = db.get_by_id(project, command_id)
    if row is None or row.get("status") != "active":
        return None
    updates: dict = {}
    if fields.get("command") is not None:
        cmd = normalize_command(fields["command"])
        if cmd == "" or len(cmd) > COMMAND_MAX_LEN:
            raise TestCommandValidationError("invalid command")
        other = db.find_by_command(project, cmd)
        if other is not None and other["id"] != command_id and other.get("status") == "active":
            raise TestCommandConflictError(f"command already exists: {cmd}")
        updates["command"] = cmd
    if fields.get("description") is not None:
        updates["description"] = _truncate(fields["description"], DESCRIPTION_MAX_LEN)
    if not updates:
        return _to_view(row)
    return _to_view(db.update_row(project, command_id, updates))


def delete(project: str, command_id: int) -> bool:
    """User DELETE → suppress (tombstone). Returns False when there is no active row (→404)."""
    row = db.get_by_id(project, command_id)
    if row is None or row.get("status") != "active":
        return False
    db.update_row(project, command_id, {"status": "suppressed"})
    return True


# ── auto-reflection from a passed remote test run (L §2-4) ────────────────────

def reflect_from_passed_run(doc: dict, items: list[dict]) -> None:
    """Collect setup(cmd) + case commands from a PASSED run into the registry.

    Never raises — a failure here must not affect the test-run verdict (L §5). The caller
    already guards status=='passed' and non-disposed group; an absent project is re-checked
    defensively. Excludes service/wait/teardown steps (L §2-4).
    """
    try:
        project = (doc or {}).get("project_id")
        if not project:
            return
        short = _short_doc_id((doc or {}).get("doc_id") or "")
        now = now_iso()

        collected: list[tuple[str, str]] = []
        for item in items or []:
            kind = item.get("kind") or "case"
            cmd_raw = item.get("cmd") or ""
            if kind == "setup":
                # T0009 task 4: this registry's description literals are English and
                # locale-independent by design (module docstring, L 2-5) — every other
                # branch already is. This one Korean literal was the odd one out, not a
                # genuine locale-dictionary candidate; corrected to match, not localized.
                desc = f"Prep step ({short})"
            elif kind == "case":
                title = (item.get("case_title") or "").strip()
                case_no = item.get("case_no") or ""
                desc = f"{title} ({short} {case_no})".strip()
            else:
                continue  # service / wait / teardown are not reusable test commands
            collected.append((cmd_raw, desc))

        seen: set[str] = set()
        for cmd_raw, desc in collected:
            cmd = normalize_command(cmd_raw)
            if cmd == "" or len(cmd) > COMMAND_MAX_LEN:
                continue
            if cmd in seen:                       # dedup within this run (L §2-4)
                continue
            seen.add(cmd)
            try:
                _reflect_one(project, cmd, desc, now)
            except Exception:
                logger.warning("test-command reflect skipped one entry: %s", cmd, exc_info=True)
    except Exception:
        logger.warning(
            "test-command reflect failed for doc %s", (doc or {}).get("doc_id"), exc_info=True
        )


def _reflect_one(project: str, cmd: str, desc: str, now: str) -> None:
    row = db.find_by_command(project, cmd)        # includes suppressed
    if row is not None:
        if row.get("status") == "suppressed":
            return                                # tombstone — do not re-register or touch it
        # origin/description untouched; verified_os follows last_success_at because the two
        # record the same event — "this command passed, here, now".
        db.update_row(
            project,
            row["id"],
            {"last_success_at": now, "verified_os": current_os()},
        )
        return
    if db.count_active(project) >= MAX_COMMANDS_PER_PROJECT:
        logger.info("test-command auto-register skipped (list full): %s / %s", project, cmd)
        return
    db.insert(
        project,
        cmd,
        _truncate(desc, DESCRIPTION_MAX_LEN),
        "auto",
        now,
        status="active",
        verified_os=current_os(),
    )


# ── TS-mention block (L §2-5 / P §TS authoring mention) ─────────────────────

def build_verified_commands_block(project: str) -> str:
    """Return the section BODY for the TS mention, or '' when the project has no commands.

    English literal, locale-independent (L §2-5). The header ('## Verified test commands
    (project: …)') is added by the mention assembler.
    """
    rows = db.list_active(project)
    if not rows:
        return ""

    # Host-OS filter (0277 NR0003 §4 F2). A row whose verified_os names a DIFFERENT OS than
    # this host passed under a different shell; presenting it as "verified" is worse than
    # saying nothing, so it is withheld and only counted. verified_os NULL means we have no
    # OS evidence either way (manual entries, and every row predating migration 068) — those
    # are still shown, but auto rows among them lose the unqualified "last pass" claim.
    host_os = current_os()
    usable = [r for r in rows if (r.get("verified_os") or host_os) == host_os]
    withheld = len(rows) - len(usable)
    if not usable:
        return ""

    shown = usable[:MENTION_MAX_ITEMS]
    hidden = len(usable) - len(shown)
    lines = [
        f"This host runs test commands through {current_shell()} (os.name={host_os}). The",
        "commands below are registered for this project (auto entries were verified by a",
        "previous successful remote test run ON THIS OS). Prefer these over guessing:",
        "",
    ]
    for row in shown:
        suffix = ""
        desc = (row.get("description") or "").strip()
        if desc:
            suffix = "  # " + desc
        last_success_at = row.get("last_success_at")
        if last_success_at:
            if row.get("verified_os"):
                suffix = suffix + " · last pass " + _format_date(last_success_at)
            else:
                # Passed at some point, but before the registry tracked the host OS — the
                # date alone would imply an endorsement this row has not earned.
                suffix = suffix + " · last pass " + _format_date(last_success_at) + " (OS unverified)"
        lines.append("- " + (row.get("command") or "") + suffix)
    if hidden > 0:
        lines.append(f"(+{hidden} more in project settings)")
    lines.append("")
    if withheld > 0:
        lines.append(
            f"({withheld} further command(s) are registered but were verified on a different OS "
            f"and are not listed — they will not run as-is under {current_shell()}.)"
        )
        lines.append("")
    lines.append("If none of these fit the code under test, you may still author a new command —")
    lines.append("it will be verified when the remote test run executes it.")
    return "\n".join(lines)


def _format_date(ts: str) -> str:
    """YYYY-MM-DD prefix of an ISO timestamp (date only, L LAST_PASS_DATE_FORMAT)."""
    return (ts or "")[:10]
