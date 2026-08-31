"""AI execution policy — repeat-count ceiling SSOT (R0001 / NR0003 §7·§11·§12).

This is the ONLY place that reads or defines the finite ceiling a repeat-count
selection (연속체인 재시작 / 단계별 검수 / 실패 재시작 / 문서 검수 루프) may reach. Every
caller that used to carry its own fixed `(-1, 0, 1, 2, 3)`-shaped literal now asks
this module instead, so raising the ceiling from 3 to (say) 30 is a system-settings
write, never a code change.

Follows settings/source_mode_service.py's shape on purpose (module constants,
`_clean`, public getter/validator) — a second pattern for "one system setting +
validation + effective getter" would just be a second thing to keep in sync with
the first. `system_settings_service.py` is deliberately NOT imported here: it pulls
in `subprocess`/git for its build-info helpers, and §4-2 has it import THIS module
(for save-time range validation) — importing back would be a cycle.

NR0003 §14 / §19 R9 asked write and read to be asymmetric — a stored/paused chain's
`continuation_review_count_overrides` (etc.) was accepted under whatever max was in
effect the day it was written, and re-checking it against TODAY's (possibly lower)
max on read would silently demote it to the "not reviewed" default, which FlowGate's
time-machine contract normally forbids. flowgate.default.0490.0006-TR could not ship
that: the two read-side helpers this module feeds
(`ai_invoke_service.resolve_review_count`, `ai_invoke_service._resolve_restart_max_attempts`)
already had pre-T0005 unit coverage (`test_ai_invoke_review_gate_0414.py`,
`test_ai_invoke_restart_count_0443.py`) asserting the OLD, symmetric, bound-checked
behavior, T0005 §5 forbids editing those two files, and no single bound satisfies
both "an out-of-range read at the default ceiling degrades" (what they assert) and
"an out-of-range read at a since-lowered ceiling survives" (what §19 R9 asks) for
the same function. This module's decision — confirmed across three review rounds —
keeps read and write SYMMETRIC: both `resolve_review_count` and
`_resolve_restart_max_attempts` call `repeat_count_choices()` (and therefore
`get_repeat_count_max()`) on every read, exactly like the write path. Lowering the
ceiling now demotes an already-stored pick on read, same as it always did before this
module existed — a deliberate, documented trade-off, not an oversight. Both helpers
are called at hop-decision points (gate resolution), not inside a tight per-token
engine loop, so the extra settings read is the same order of cost as the existing
write-path check.
"""
from __future__ import annotations

from modules.flow_gate.db import system_settings as _system_settings

SETTING_KEY = "ai_repeat_count_max"
REPEAT_COUNT_DEFAULT_MAX = 3
REPEAT_COUNT_MIN_MAX = 1
REPEAT_COUNT_HARD_MAX = 30


def get_repeat_count_max() -> int:
    """The current effective ceiling N. Never raises.

    A missing row, a legacy/corrupt value ("", "abc", "0", "31", non-numeric) or a
    DB access failure all fall back to REPEAT_COUNT_DEFAULT_MAX silently — existing
    tests (e.g. test_ai_invoke_document_review_loop_0417.py) call routes that reach
    this function without going through the store-patched fixtures some other tests
    use, so an exception here would fail unrelated tests, not just this feature.
    """
    try:
        raw = _system_settings.get_value(SETTING_KEY)
        if raw is None:
            return REPEAT_COUNT_DEFAULT_MAX
        value = int(str(raw).strip(), 10)
        if not (REPEAT_COUNT_MIN_MAX <= value <= REPEAT_COUNT_HARD_MAX):
            return REPEAT_COUNT_DEFAULT_MAX
        return value
    except Exception:  # noqa: BLE001 — this getter must never fail a caller
        return REPEAT_COUNT_DEFAULT_MAX


def valid_setting_value(value: object) -> bool:
    """Is `value` an acceptable NEW ai_repeat_count_max (save-time range check)?

    Rejects bool explicitly (`True == 1` in Python would otherwise pass as "1"),
    and anything that is not an integer in [REPEAT_COUNT_MIN_MAX, REPEAT_COUNT_HARD_MAX]
    once parsed — "", "abc", "3.5", None, "0", "31" are all rejected.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return False
        try:
            candidate = int(text, 10)
        except ValueError:
            return False
    else:
        return False
    return REPEAT_COUNT_MIN_MAX <= candidate <= REPEAT_COUNT_HARD_MAX


def repeat_count_choices(*, allow_zero: bool, allow_unlimited: bool = True) -> tuple[int, ...]:
    """The full selectable set for one feature, built from the current effective max.

    `allow_unlimited=True` prepends -1 ("될 때까지"), `allow_zero=True` prepends 0
    ("검수/재시작 없음") — each feature keeps its own existing -1/0 semantics
    (NR0003 §19 R5); only the finite tail `1..get_repeat_count_max()` is dynamic.
    Always returns a sorted tuple: -1, then 0, then 1..N.
    """
    values: list[int] = []
    if allow_unlimited:
        values.append(-1)
    if allow_zero:
        values.append(0)
    values.extend(range(1, get_repeat_count_max() + 1))
    return tuple(values)


def valid_repeat_count(value: object, *, allow_zero: bool, allow_unlimited: bool = True) -> bool:
    """Judge by the SAME set `repeat_count_choices` returns — never a separate rule,

    so a validation message built from `repeat_count_choices()` can never name a value
    this function would also accept, or reject a value it lists.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value in repeat_count_choices(allow_zero=allow_zero, allow_unlimited=allow_unlimited)


def execution_policy_payload() -> dict:
    """The client-facing shape ridden on GET /api/v1/ai-invoke/providers (§3.5)."""
    return {
        "repeat_count_max": get_repeat_count_max(),
        "repeat_count_min": REPEAT_COUNT_MIN_MAX,
        "repeat_count_hard_max": REPEAT_COUNT_HARD_MAX,
    }
