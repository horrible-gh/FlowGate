"""Read the pytest result block out of a finished Self-check run's stdout tail (flowgate.v02.0114 T#3).

D 0006 3.2 / 8-1: the block is what the test process printed, so it is untrusted input. It is
summarized only for a run that ended normally, never changes the run's status, exit_code or
timed_out, and anything that does not parse exactly is "no structured result" / "incomplete"
-- nothing is guessed. The block's environment evidence and test definition evidence are only
ever compared between two runs (``tr_self_check_baseline``); they are never a reason to pass.

Block (written by ``server/tests/selfcheck_evidence.py`` at the very end of pytest's output):

    ===FLOWGATE-RESULT-BLOCK-BEGIN v1===
    {one JSON object, at most MAX_BLOCK_BYTES UTF-8 bytes}
    ===FLOWGATE-RESULT-BLOCK-END v1===
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

BEGIN_MARKER = "===FLOWGATE-RESULT-BLOCK-BEGIN v1==="
END_MARKER = "===FLOWGATE-RESULT-BLOCK-END v1==="
SCHEMA = "flowgate.pytest-result/1"
MAX_BLOCK_BYTES = 32 * 1024

#: Summary status. Only COMPLETE can make a run a baseline (D 3.4 condition 3).
NONE = "none"                # no block at all: no structured result
INCOMPLETE = "incomplete"    # a block that cannot be trusted whole (broken, truncated, cut, ...)
MISMATCH = "mismatch"        # the block and the exit code disagree; neither side is promoted
COMPLETE = "complete"

#: Environment evidence categories carried by the block (D 3.4 condition 7 a-e). (f) is the
#: run row's host_node_key, not part of the block.
ENV_CATEGORIES = ("tools", "host", "env", "dependencies", "workers")
#: A category that is present but does not have the expected shape.
UNREADABLE = "unreadable"
#: A definition fingerprint the collector could not trace (D 3.4 condition 8 judgement rule).
UNTRACEABLE = "untraceable"

_OUTCOMES = ("passed", "failed", "error", "skipped", "xfailed", "xpassed")
#: The outcomes an item ends with at most once (a setup error has none of them).
_PRIMARY = ("passed", "failed", "skipped", "xfailed", "xpassed")
#: pytest exit statuses of a session that ran to its end (0 all passed, 1 some failed).
_NORMAL_EXIT = (0, 1)
_MAX_EXIT = 255
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_NODEID = 2000
_MAX_ITEMS = 20000


class _Malformed(ValueError):
    pass


def _int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _Malformed(field)
    return value


def _nodeid(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_NODEID or "\0" in value:
        raise _Malformed(field)
    return value


def _fingerprint(value: Any, field: str) -> str:
    if value == UNTRACEABLE or (isinstance(value, str) and _HEX64.match(value)):
        return value
    raise _Malformed(field)


def _failures(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > _MAX_ITEMS:
        raise _Malformed("failures")
    seen, out = set(), []
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("outcome") not in ("failed", "error"):
            raise _Malformed("failures")
        nodeid = _nodeid(entry.get("nodeid"), "failures")
        if nodeid in seen:
            raise _Malformed("failures")
        seen.add(nodeid)
        out.append({"nodeid": nodeid, "outcome": entry["outcome"]})
    return out


def _parallel(raw: Any) -> Optional[dict]:
    """0114 T#4 (D 5): the worker slot pool's account the block reports -- requested and applied
    workers, slot source, pool size, the run tree's processor limit. Evidence only; None when
    absent or malformed, and it never changes the summary's status."""
    if not isinstance(raw, dict) or set(raw) != {"requested", "applied", "slot_source", "pool_size", "cpu_limit"}:
        return None
    try:
        account = {"requested": _int(raw["requested"], "parallel"), "applied": _int(raw["applied"], "parallel"),
                   "pool_size": _int(raw["pool_size"], "parallel")}
        limit = raw["cpu_limit"]
        account["cpu_limit"] = None if limit is None else _int(limit, "parallel")
    except _Malformed:
        return None
    if raw["slot_source"] not in ("pool", "none") or account["pool_size"] < 1:
        return None
    account["slot_source"] = raw["slot_source"]
    return account


def _definitions(raw: Any) -> Optional[dict]:
    """Validated definition evidence, or None when it is missing or malformed (then no item of
    the run has a usable definition -- D 3.2, 9.2-6 (n))."""
    if not isinstance(raw, dict):
        return None
    try:
        status = raw.get("run_status")
        if status not in ("ok", UNTRACEABLE, "inconsistent", "missing"):
            raise _Malformed("definitions.run_status")
        run = raw.get("run")
        if run is not None and not (isinstance(run, str) and _HEX64.match(run)):
            raise _Malformed("definitions.run")
        if raw.get("scope") not in ("all", "failed_only"):
            raise _Malformed("definitions.scope")
        items = raw.get("items")
        if not isinstance(items, dict) or len(items) > _MAX_ITEMS:
            raise _Malformed("definitions.items")
        checked = {_nodeid(k, "definitions.items"): _fingerprint(v, "definitions.items") for k, v in items.items()}
    except _Malformed:
        return None
    return {"run": run if status == "ok" else None, "run_status": status, "scope": raw["scope"], "items": checked}


def _host_keys() -> frozenset:
    """The variables Self-check passes through to the run (category c must name exactly these)."""
    from modules.flow_gate.services.tr_self_check_policy import HOST_KEYS
    return frozenset(HOST_KEYS)


def _category(name: str, value: Any) -> Any:
    """One environment category in its expected shape, else UNREADABLE.

    Shape includes every required sub-item and the relations between them: a category that
    lost a key (on one side or on both) is not "the same", it is unreadable evidence."""
    try:
        if name == "tools":
            if not isinstance(value, dict) or set(value) != {"python", "pytest", "xdist", "git"}:
                raise _Malformed(name)
            if not all(isinstance(v, str) and v for v in value.values()):
                raise _Malformed(name)
        elif name == "host":
            if not isinstance(value, dict) or set(value) != {"os", "platform", "capabilities"}:
                raise _Malformed(name)
            caps = value["capabilities"]
            if not (isinstance(value["os"], str) and value["os"] and isinstance(value["platform"], str)
                    and value["platform"] and isinstance(caps, dict) and caps
                    and all(isinstance(k, str) and isinstance(v, bool) for k, v in caps.items())):
                raise _Malformed(name)
        elif name == "env":
            # Every HOST_KEYS variable, each with its actual value (None = not set on the host).
            if not isinstance(value, dict) or set(value) != _host_keys():
                raise _Malformed(name)
            if not all(v is None or isinstance(v, str) for v in value.values()):
                raise _Malformed(name)
        elif name == "dependencies":
            if not isinstance(value, dict) or set(value) != {"count", "sha256"}:
                raise _Malformed(name)
            # pytest itself is installed, so an empty listing is no listing.
            if _int(value["count"], name) < 1:
                raise _Malformed(name)
            if not (isinstance(value["sha256"], str) and _HEX64.match(value["sha256"])):
                raise _Malformed(name)
        elif name == "workers":
            if not isinstance(value, dict) or set(value) != {"mode", "applied", "dist", "cpu_limit"}:
                raise _Malformed(name)
            mode, applied, dist = value["mode"], _int(value["applied"], name), value["dist"]
            if not isinstance(dist, str) or not dist:
                raise _Malformed(name)
            if mode == "serial":
                if applied != 0 or dist != "no":
                    raise _Malformed(name)
            elif mode == "xdist":
                if applied < 1 or dist == "no":
                    raise _Malformed(name)
            else:
                raise _Malformed(name)
            if value["cpu_limit"] is not None and _int(value["cpu_limit"], name) < 1:
                raise _Malformed(name)
        else:
            raise _Malformed(name)
        return value
    except (_Malformed, KeyError, TypeError):
        return UNREADABLE


def _environment(raw: Any) -> Optional[dict]:
    """Present categories, each in shape or UNREADABLE. None when the block carries none."""
    if not isinstance(raw, dict):
        return None
    return {name: _category(name, raw[name]) for name in ENV_CATEGORIES if name in raw}


def check_category(name: str, value: Any) -> Any:
    """``value`` if it is category ``name`` in its full expected shape, else UNREADABLE. The
    judge re-checks stored evidence with this, so a laxer earlier summary cannot pass."""
    if value == UNREADABLE:
        return UNREADABLE
    return _category(name, value)


def _inconsistency(exitstatus: int, completed: int, outcomes: dict, failures: list[dict],
                   failures_total: int, truncated: bool) -> Optional[str]:
    """The first contradiction between the block's own counts, or None.

    The plugin counts one primary outcome per finished item (setup errors have none), one
    failure entry per item with a failed/error report ("failed" when the call failed), and
    pytest exits 1 exactly when a report failed."""
    failing = outcomes["failed"] + outcomes["error"]
    if (failures_total == 0) != (failing == 0):
        return "failures_vs_outcomes"
    if not (outcomes["failed"] <= failures_total <= failing):
        return "failures_vs_outcomes"
    if not truncated:
        if sum(1 for f in failures if f["outcome"] == "failed") != outcomes["failed"]:
            return "failure_entries_vs_outcomes"
        if sum(1 for f in failures if f["outcome"] == "error") > outcomes["error"]:
            return "failure_entries_vs_outcomes"
    primary = sum(outcomes[k] for k in _PRIMARY)
    if not (primary <= completed <= primary + outcomes["error"]):
        return "outcomes_vs_completed"
    if (exitstatus == 0) != (failures_total == 0):
        return "exitstatus_vs_failures"
    return None


def _locate(text: str) -> tuple[Optional[str], Optional[str]]:
    """``(payload, None)`` for exactly one well-placed block, else ``(None, reason)``."""
    begins, ends = text.count(BEGIN_MARKER), text.count(END_MARKER)
    if begins == 0 and ends == 0:
        return None, "no_block"
    if begins > 1 or ends > 1:
        # A test may print a block of its own (a nested pytest, captured output): when more
        # than one is in the tail the last one cannot be trusted to be the run's own.
        return None, "multiple_blocks"
    if begins == 0:
        return None, "begin_missing"
    if ends == 0:
        return None, "end_missing"
    begin, end = text.find(BEGIN_MARKER), text.find(END_MARKER)
    if end < begin:
        return None, "markers_out_of_order"
    if text[end + len(END_MARKER):].strip():
        return None, "trailing_output"
    return text[begin + len(BEGIN_MARKER):end].strip(), None


def summarize(stdout_tail: Optional[str], exit_code: Optional[int]) -> tuple[dict, Optional[dict]]:
    """``(result_summary, environment_evidence)`` for a normally finished run's stored tail."""
    payload, reason = _locate(stdout_tail or "")
    if payload is None:
        status = NONE if reason == "no_block" else INCOMPLETE
        return {"status": status, "reason": reason}, None
    raw_bytes = payload.encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if len(raw_bytes) > MAX_BLOCK_BYTES:
        return {"status": INCOMPLETE, "reason": "oversize", "block_sha256": digest}, None
    try:
        block = json.loads(payload)
    except ValueError:
        return {"status": INCOMPLETE, "reason": "unparsable", "block_sha256": digest}, None
    try:
        if not isinstance(block, dict) or block.get("schema") != SCHEMA:
            raise _Malformed("schema")
        exitstatus = _int(block.get("exitstatus"), "exitstatus")
        if exitstatus > _MAX_EXIT:
            raise _Malformed("exitstatus")
        collected = _int(block.get("collected"), "collected")
        completed = _int(block.get("completed"), "completed")
        outcomes_raw = block.get("outcomes")
        if not isinstance(outcomes_raw, dict) or set(outcomes_raw) != set(_OUTCOMES):
            raise _Malformed("outcomes")
        outcomes = {k: _int(outcomes_raw[k], "outcomes") for k in _OUTCOMES}
        failures = _failures(block.get("failures"))
        failures_total = _int(block.get("failures_total"), "failures_total")
        truncated, complete_flag = block.get("truncated"), block.get("complete")
        if not isinstance(truncated, bool) or not isinstance(complete_flag, bool):
            raise _Malformed("flags")
        if failures_total < len(failures):
            raise _Malformed("failures_total")
    except _Malformed as exc:
        return {"status": INCOMPLETE, "reason": f"malformed:{exc}", "block_sha256": digest}, None

    summary = {
        "status": COMPLETE, "reason": None, "schema": SCHEMA, "exitstatus": exitstatus, "collected": collected,
        "completed": completed, "outcomes": outcomes, "failures": failures,
        "failures_total": failures_total, "truncated": truncated, "complete_flag": complete_flag,
        "definitions": _definitions(block.get("definitions")), "block_sha256": digest,
        "mismatch": False, "parallel": _parallel(block.get("parallel")),
    }
    if truncated or failures_total != len(failures):
        summary.update(status=INCOMPLETE, reason="truncated")
    elif not complete_flag or collected != completed:
        summary.update(status=INCOMPLETE, reason="not_complete")
    elif exitstatus not in _NORMAL_EXIT:
        # interrupted, internal error, usage error, no tests: the session did not run to its end
        summary.update(status=INCOMPLETE, reason="abnormal_exitstatus")
    else:
        contradiction = _inconsistency(exitstatus, completed, outcomes, failures, failures_total, truncated)
        if contradiction:
            summary.update(status=INCOMPLETE, reason=f"inconsistent:{contradiction}")
    # D 3.2: the block's exit status differing from the process exit code, exit code 0 with
    # failures in the block, or non-zero with none, is a mismatch; neither side is promoted.
    if exit_code is None or exit_code != exitstatus or (exit_code == 0) != (failures_total == 0):
        summary["mismatch"] = True
        if summary["status"] == COMPLETE:
            summary.update(status=MISMATCH, reason="exit_code_disagrees")
    return summary, _environment(block.get("environment"))


def dumps(value: Optional[dict]) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
