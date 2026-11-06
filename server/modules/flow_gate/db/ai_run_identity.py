"""0138: committed daily serials and immutable ownership, independent of run purge.

No request-time MAX, local fallback, automatic migration or live cutover. Version
1 is enabled only by initialize(), with all issuers drained and scratch inventoried.
Old binaries do not understand this gate: deployment MUST fence them separately.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import UUID

from .connection import get_store, in_transaction
from . import dialect

VERSION = 1
RUN_ID = re.compile(r"aiv_([0-9]{8})_([0-9]{6})\Z")
IDENTITY_FIELDS = ("execution_nonce", "project_id", "group_id", "started_at")


class RunIdentityConflict(RuntimeError):
    def __init__(self):
        # Never include nonce, token or SQL parameters in diagnostics.
        super().__init__("ai_run_identity_conflict")


def _standalone():
    if in_transaction():
        raise RuntimeError("ai_run_identity_requires_independent_commit")


def _require_version(store):
    row = store._fetch_one("SELECT version FROM ai_run_id_contract WHERE singleton = 1")
    if row is None or row["version"] != VERSION:
        raise RuntimeError("ai_run_allocator_cutover_required")


def initialize(*, scratch_roots, all_issuers_stopped: bool) -> dict[str, int]:
    """Explicit offline cutover. Roots are each issuer's project/evaluation scratch roots.

    A missing/unreadable root or source aborts initialization. An empty list is
    allowed only for a fresh installation with no scratch roots. The caller owns
    the cross-server admission fence; this function never stops a server itself.
    Repeating initialization can only raise floors, never lower them.
    """
    _standalone()
    if all_issuers_stopped is not True:
        raise RuntimeError("ai_run_issuers_must_be_stopped")
    floors: dict[str, int] = {}

    def include(value):
        match = RUN_ID.fullmatch(str(value or ""))
        if match:
            day, serial = match.groups()
            datetime.strptime(day, "%Y%m%d")
            floors[day] = max(floors.get(day, 0), int(serial))

    for root_value in scratch_roots:
        root = Path(root_value)
        if not root.is_dir() or root.is_symlink():
            raise RuntimeError("ai_run_scratch_inventory_unavailable")
        for entry in root.iterdir():
            include(entry.name)
    store = get_store()
    with store.transaction():
        # First write serializes concurrent initializers, including SQLite readers.
        store._execute_affected("UPDATE ai_run_id_contract SET version = version WHERE singleton = 1")
        # MySQL reports zero changed rows for this lock-only no-op. Check existence
        # while retaining the lock rather than interpreting its row count as absence.
        contract = store._fetch_one("SELECT version FROM ai_run_id_contract WHERE singleton = 1")
        if contract is None:
            raise RuntimeError("ai_run_contract_missing")
        if contract["version"] not in (0, VERSION):
            raise RuntimeError("ai_run_allocator_incompatible_version")
        for table, column in (
            ("ai_invoke_runs", "run_id"), ("tokens", "ai_run_id"),
            ("group_ai_leases", "run_id"), ("project_ai_leases", "run_id"),
            ("ai_evaluations", "scratch_ref"), ("ai_run_owners", "run_id"),
        ):
            for row in store._fetch_all(f"SELECT {column} AS run_id FROM {table}"):
                include(row["run_id"])
        for day, floor in floors.items():
            store._execute(
                "INSERT INTO ai_run_id_counters (utc_date, serial) VALUES (?, ?) "
                "ON CONFLICT(utc_date) DO UPDATE SET serial = "
                "CASE WHEN ai_run_id_counters.serial < excluded.serial "
                "THEN excluded.serial ELSE ai_run_id_counters.serial END", [day, floor])
        store._execute("UPDATE ai_run_id_contract SET version = ? WHERE singleton = 1", [VERSION])
    return floors


def allocate(*, at: datetime | None = None) -> str:
    """Return only after the reservation commits. Every failed admission burns its ID."""
    _standalone()
    stamp = at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("ai_run_allocation_requires_aware_time")
    day = stamp.astimezone(timezone.utc).strftime("%Y%m%d")
    store = get_store()
    with store.transaction():
        _require_version(store)
        if store.dialect in (dialect.SQLITE, dialect.POSTGRESQL):
            row = store._fetch_one(
                "INSERT INTO ai_run_id_counters (utc_date, serial) VALUES (?, 1) "
                "ON CONFLICT(utc_date) DO UPDATE SET serial = ai_run_id_counters.serial + 1 "
                "WHERE ai_run_id_counters.serial < 999999 RETURNING serial", [day])
        elif store.dialect == dialect.MYSQL:
            # MySQL has no INSERT RETURNING. Keep the row lock through the read/commit.
            store.insert_isolated(
                "INSERT INTO ai_run_id_counters (utc_date, serial) VALUES (?, 0)",
                [day], "ai_run_counter_insert")
            changed = store._execute_affected(
                "UPDATE ai_run_id_counters SET serial = serial + 1 "
                "WHERE utc_date = ? AND serial < 999999", [day])
            row = store._fetch_one(
                "SELECT serial FROM ai_run_id_counters WHERE utc_date = ?", [day]) if changed else None
        else:
            raise RuntimeError("ai_run_allocator_unsupported_database")
        if row is None:
            raise RuntimeError("ai_run_id_exhausted")
        serial = int(row["serial"])
    return f"aiv_{day}_{serial:06d}"


def _matches(owner, run):
    return owner is not None and all(owner.get(k) == run.get(k) for k in IDENTITY_FIELDS)


def claim(run: dict) -> None:
    """Claim before any token, lease, scratch or worker is created. No adoption."""
    _standalone()
    if not RUN_ID.fullmatch(str(run.get("run_id") or "")):
        raise RunIdentityConflict()
    try:
        UUID(run["execution_nonce"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RunIdentityConflict() from None
    store = get_store()
    with store.transaction():
        _require_version(store)
        store.insert_isolated(
            "INSERT INTO ai_run_owners (run_id, execution_nonce, project_id, group_id, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [run["run_id"], *(run[k] for k in IDENTITY_FIELDS)], "ai_run_owner_insert")
        assert_owner(run, store=store)


def assert_owner(run: dict, *, store=None) -> None:
    store = store or get_store()
    if not run.get("execution_nonce"):
        raise RunIdentityConflict()
    owner = store._fetch_one("SELECT * FROM ai_run_owners WHERE run_id = ?", [run["run_id"]])
    if not _matches(owner, run):
        raise RunIdentityConflict()
    existing = store._fetch_one("SELECT * FROM ai_invoke_runs WHERE run_id = ?", [run["run_id"]])
    if existing is not None and not _matches(existing, run):
        raise RunIdentityConflict()


def scratch_owner(project_id: str, run_id: str, execution_nonce: str) -> bool:
    """Read-only ownership check; absent nonce never adopts a legacy directory."""
    if not execution_nonce:
        return False
    owner = get_store()._fetch_one("SELECT * FROM ai_run_owners WHERE run_id = ?", [run_id])
    return bool(owner and owner["project_id"] == project_id
                and owner["execution_nonce"] == execution_nonce)
