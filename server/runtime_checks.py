"""Boot-time runtime checks (flowgate.v02.0137 T0004 §4-2/§4-3).

NR0003 B found both deployed server venvs on sqloader 0.2.15 while requirements.txt pins
0.2.17. 0.2.15 builds its pool with a hard-coded minconn=1 and silently ignores the
DB_POOL_MIN/DB_POOL_MAX the config hands it, so every concurrent query above the first
opened and closed a TCP connection to PostgreSQL — the TIME_WAIT churn behind the
ephemeral-port exhaustion. Nothing at boot said so. These checks make the three facts
visible on every start:

1. installed package versions vs the `==` pins in requirements.txt;
2. the pool the installed sqloader ACTUALLY built vs the configured DB_POOL_MIN/MAX;
3. the configured pool against PostgreSQL's connection budget, across all FlowGate
   server processes that share the database.

Policy (`FLOWGATE_DEPENDENCY_PIN_POLICY`): `warn` (default) logs a mismatch at ERROR and
starts; `fail` refuses to start when a critical package (sqloader, psycopg2-binary) is
off-pin; `off` skips the pin check. The default is `warn` on purpose: the stg deploy
script does not reinstall requirements, so a refusing default would turn the next code
deploy into an outage instead of a loud log line. Every check is read-only.
"""

from __future__ import annotations

import logging
import os
import re
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterable, Optional

REQUIREMENTS_PATH = Path(__file__).resolve().parent / "requirements.txt"
CRITICAL_PACKAGES = frozenset({"sqloader", "psycopg2-binary"})
POLICY_ENV = "FLOWGATE_DEPENDENCY_PIN_POLICY"
_POLICIES = ("warn", "fail", "off")
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*([^\s;#]+)")

_default_logger = logging.getLogger("flowgate.runtime_checks")


class DependencyPinError(RuntimeError):
    """A critical package is not the pinned version and the policy is `fail`."""


def normalize_name(name: str) -> str:
    """PEP 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text: str) -> dict[str, str]:
    """`name==version` pins of a requirements file (ranges and comments ignored)."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        match = _PIN.match(line)
        if match:
            pins[normalize_name(match.group(1))] = match.group(3)
    return pins


def installed_version(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def pin_mismatches(pins: dict[str, str],
                   lookup: Callable[[str], Optional[str]] = installed_version,
                   critical: Iterable[str] = CRITICAL_PACKAGES) -> list[dict]:
    """Installed packages that differ from their pin, plus missing CRITICAL ones.

    A missing non-critical pin (test-only tools on a production venv) is not reported.
    """
    critical = {normalize_name(c) for c in critical}
    problems: list[dict] = []
    for name, wanted in sorted(pins.items()):
        have = lookup(name)
        if have is None:
            if name in critical:
                problems.append({"package": name, "required": wanted, "installed": None,
                                 "critical": True})
            continue
        if have != wanted:
            problems.append({"package": name, "required": wanted, "installed": have,
                             "critical": name in critical})
    return problems


def resolve_policy(value: Optional[str] = None) -> str:
    policy = (value if value is not None else os.environ.get(POLICY_ENV, "warn")).strip().lower()
    return policy if policy in _POLICIES else "warn"


def _log_error(logger, message: str) -> None:
    getattr(logger, "error", logger.warning)(message)


def check_dependency_pins(*, requirements: Path = REQUIREMENTS_PATH, policy: Optional[str] = None,
                          lookup: Callable[[str], Optional[str]] = installed_version,
                          logger=None) -> list[dict]:
    """Log every off-pin package; raise DependencyPinError under `fail` for critical ones."""
    logger = logger or _default_logger
    policy = resolve_policy(policy)
    if policy == "off":
        return []
    try:
        pins = parse_pins(requirements.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.warning(f"[startup] dependency pin check skipped: requirements unreadable ({exc})")
        return []
    problems = pin_mismatches(pins, lookup)
    for item in problems:
        _log_error(logger, (
            "[startup] dependency pin mismatch: "
            f"{item['package']} required=={item['required']} installed={item['installed']}"
            f"{' (critical)' if item['critical'] else ''} — reinstall with "
            "`pip install -r server/requirements.txt` in this venv"
        ))
    critical = [p for p in problems if p["critical"]]
    if critical and policy == "fail":
        names = ", ".join(f"{p['package']}={p['installed']}" for p in critical)
        raise DependencyPinError(f"critical dependency off-pin: {names} ({POLICY_ENV}=fail)")
    return problems


def pool_state(db_instance) -> Optional[dict]:
    """The pool the installed driver actually built, or None (SQLite / unknown)."""
    pool = getattr(db_instance, "pool", None)
    if pool is None:
        return None
    return {
        "impl": type(pool).__name__,
        "minconn": getattr(pool, "minconn", None),
        "maxconn": getattr(pool, "maxconn", None),
        "max_parallel_queries": getattr(db_instance, "max_parallel_queries", None),
    }


def pool_problems(state: Optional[dict], *, pool_min: int, pool_max: int,
                  max_parallel_queries: int) -> list[str]:
    """Differences between the configured pool and the one that was built."""
    if state is None:
        return []
    problems = []
    if state.get("minconn") is not None and state["minconn"] != pool_min:
        problems.append(f"pool minconn={state['minconn']} but DB_POOL_MIN={pool_min}")
    if state.get("maxconn") is not None and state["maxconn"] != pool_max:
        problems.append(f"pool maxconn={state['maxconn']} but DB_POOL_MAX={pool_max}")
    if state.get("max_parallel_queries") is not None and state["max_parallel_queries"] != max_parallel_queries:
        problems.append(f"max_parallel_queries={state['max_parallel_queries']} but "
                        f"DB_MAX_PARALLEL_QUERIES={max_parallel_queries}")
    if state.get("minconn") is not None and state["minconn"] < max_parallel_queries:
        problems.append(
            f"pool minconn={state['minconn']} < max_parallel_queries={max_parallel_queries}: "
            "concurrent queries above minconn open and close a connection each")
    return problems


def connection_budget(*, max_connections: int, superuser_reserved: int, reserved_other: int,
                      processes: int, pool_max: int) -> dict:
    """Does `processes x pool_max` fit the server's non-superuser connection budget?"""
    budget = int(max_connections) - int(superuser_reserved) - int(reserved_other)
    demand = int(processes) * int(pool_max)
    return {"budget": budget, "demand": demand, "ok": demand <= budget,
            "max_connections": int(max_connections), "superuser_reserved": int(superuser_reserved),
            "reserved_other": int(reserved_other), "processes": int(processes),
            "pool_max": int(pool_max)}


def _show(db_instance, name: str) -> Optional[int]:
    try:
        row = db_instance.fetch_one(f"SHOW {name}")
    except Exception:
        return None
    if isinstance(row, dict):
        value = next(iter(row.values()), None)
    elif isinstance(row, (list, tuple)):
        value = row[0] if row else None
    else:
        value = row
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check_database_pool(settings, db_instance, *, logger=None) -> dict:
    """Log the built pool and the connection budget; never raises (T0004 §4-2/§4-3)."""
    logger = logger or _default_logger
    report: dict = {}
    try:
        state = pool_state(db_instance)
        report["pool"] = state
        if state is None:
            return report
        problems = pool_problems(state, pool_min=settings.DB_POOL_MIN, pool_max=settings.DB_POOL_MAX,
                                 max_parallel_queries=settings.DB_MAX_PARALLEL_QUERIES)
        report["pool_problems"] = problems
        message = (f"[startup] db pool {state['impl']} minconn={state['minconn']} "
                   f"maxconn={state['maxconn']} max_parallel_queries={state['max_parallel_queries']} "
                   f"sqloader={installed_version('sqloader')}")
        if problems:
            _log_error(logger, message + " — " + "; ".join(problems))
        else:
            logger.info(message)
        max_connections = _show(db_instance, "max_connections")
        if max_connections is None:
            return report
        superuser_reserved = _show(db_instance, "superuser_reserved_connections") or 0
        budget = connection_budget(
            max_connections=max_connections, superuser_reserved=superuser_reserved,
            reserved_other=settings.DB_RESERVED_CONNECTIONS, processes=settings.DB_SERVER_PROCESSES,
            pool_max=settings.DB_POOL_MAX,
        )
        report["budget"] = budget
        text = (f"[startup] db connection budget: {budget['processes']} process(es) x pool_max "
                f"{budget['pool_max']} = {budget['demand']} vs max_connections {max_connections} - "
                f"superuser_reserved {superuser_reserved} - reserved {budget['reserved_other']} "
                f"= {budget['budget']}")
        if budget["ok"]:
            logger.info(text)
        else:
            _log_error(logger, text + " — OVER BUDGET: lower DB_POOL_MAX / DB_MAX_PARALLEL_QUERIES "
                                      "or raise max_connections")
    except Exception as exc:  # noqa: BLE001 — a diagnostic must never stop the boot
        logger.warning(f"[startup] db pool check failed: {exc}")
    return report
