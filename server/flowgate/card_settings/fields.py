"""Card run settings: the settings of each card kind and the values they take (flowgate.v02.0053 T#2).

Pure. The 0053 CH agreement (§4 "settings as drawn") and the v7 settings blocks name the items:

- run cards (research N, work T / T2, design D / P / L / DB, custom): instruction use / omit, after
  review, run AI, restart, review AI, review count, and the advanced time limit and review basis;
- test cards (TS): instruction, after review, and the scenario writing block (writer AI, review AI,
  review count). The run block shows each case's mode, which lives on the case inside the TS
  document (0549 contract 2), so the card stores nothing for it;
- requirement cards (R / B): AI delegation off / on, its scope, AI Team and the safety limits —
  values only, no delegation engine (0053 CH: out of this scope);
- check cards (gate): usage, Git apply mode, merge target, push, keep branch.

A field with no stored value shows its default. The default is what the engine does without the
setting today, so storing nothing and storing the default run the same way. ``None`` as a default
means "decided elsewhere": the node's assignee AI, the project's default reviewer, or the final
approval dialog that asks the Git choice.

How a run applies them (``run_plan``, ``result_doc_type``, ``after_result``, ``review_loop``):

- instruction omit: the AI writes the card's report instead of the instruction-led node's own type
  (N -> NR, T -> TR, v0.1 AUTO_REPORT_MAP) and that report is the node's result. A design card
  (D / P / L / DB) already writes its own document after the DS instruction, a test card's TS never
  folds (its TSR is server-assembled, as in v0.1), and an apply card's report (TR2) names the T2 it
  applies (``source_t2_doc_id``) while omit leaves the card no instruction document (the v7 settings
  hint; v0.1 folds a T2 only after it has materialized and approved that T2), so omit changes nothing
  for them;
- review count not 0: when the node's AI run has ended, the card's review AI reviews the result with
  the v0.1 document review loop (review count, review basis, rework by the run AI); every review hop
  is told the review basis (``review_basis_note``);
- after review: follow approves the result when the review passed (or at once with no review),
  human leaves it to a person, as before.

Value checks that need the server (the repeat count range, an AI or AI Team that exists) are passed in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

RUN, TEST, REQUIREMENT, CHECK = "run", "test", "requirement", "check"
CARD_KINDS = (RUN, TEST, REQUIREMENT, CHECK)

# Value kinds.
CHOICE, AI, REPEAT, MINUTES, LIMIT, TEAM, BRANCH, FLAG = (
    "choice", "ai", "repeat", "minutes", "limit", "team", "branch", "flag")

# The run time limit choices (minutes): the AI invoke dialogs' list, inside the engine's
# STEP_TIMEOUT_MIN_SEC..STEP_TIMEOUT_MAX_SEC (checked by the tests against the engine).
TIME_LIMIT_MINUTES = (30, 45, 60, 90, 120, 180, 240)
# A single run's own budget with no pick: RUN_TIMEOUT_BASE_SEC x 1 document = 60 minutes.
DEFAULT_TIME_LIMIT_MINUTES = 60
LIMIT_MIN, LIMIT_MAX = 1, 999
TEXT_MAX_LEN = 191
# The v0.1 document review loop names of the two review bases.
REVIEW_CRITERIA = {"type": "document_type_default", "last": "last_rejection_only"}

_RUN_DOC_TYPES = frozenset({"N", "T", "T2", "D", "P", "L", "DB"})
# The report an instruction-led card writes with its instruction omitted (v0.1 AUTO_REPORT_MAP). T2 is
# not here: its TR2 names the T2 it applies, and an omitted instruction leaves no T2 to name.
REPORT_TYPES = {"N": "NR", "T": "TR"}
# What follows a node's AI result (``after_result``).
REVIEW, APPROVE = "review", "approve"
# The v0.1 document review loop's own value sets (its request model and table CHECKs): the rework
# hop budget, the loop budget and the failure restarts it accepts.
LOOP_REWORK_TIMEOUTS_SEC = (1800, 3600, 7200)
LOOP_TOTAL_TIMEOUT_SEC = 14400
LOOP_FAILURE_RESTARTS = (-1, 0, 1, 2)
_ROOT_TYPES = frozenset({"R", "B"})
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    default: Any = None
    choices: tuple = ()


_INSTRUCTION = Field("instruction", CHOICE, "use", ("use", "omit"))
# Today a node's result waits in review for a person's decision.
_AFTER_REVIEW = Field("after_review", CHOICE, "human", ("follow", "human"))
_RUN_AI = Field("run_ai", AI)                    # None: the node's assignee AI
_REVIEW_AI = Field("review_ai", AI)              # None: the project's default reviewer
_REVIEW_COUNT = Field("review_count", REPEAT, 0)
_FIELDS = {
    RUN: (
        _INSTRUCTION, _AFTER_REVIEW, _RUN_AI,
        Field("restart", REPEAT, 0),             # a single node run is one-shot today
        _REVIEW_AI, _REVIEW_COUNT,
        Field("time_limit", MINUTES, DEFAULT_TIME_LIMIT_MINUTES, TIME_LIMIT_MINUTES),
        Field("review_basis", CHOICE, "type", tuple(REVIEW_CRITERIA)),
    ),
    TEST: (_INSTRUCTION, _AFTER_REVIEW, _RUN_AI, _REVIEW_AI, _REVIEW_COUNT),
    REQUIREMENT: (
        Field("delegation", CHOICE, "off", ("off", "on")),
        Field("scope", CHOICE, "end", ("end", "confirm")),
        Field("team", TEAM),
        Field("limit_max_nodes", LIMIT),
        Field("limit_total_reviews", LIMIT),
        Field("limit_retries_per_card", LIMIT),
        Field("limit_replans", LIMIT),
    ),
    CHECK: (
        Field("usage", CHOICE, None, ("mid", "end")),
        Field("merge_mode", CHOICE, None, ("merge", "commit", "hold")),
        Field("merge_target", BRANCH),
        Field("push", FLAG),
        Field("keep_branch", FLAG),
    ),
}


class InvalidValue(ValueError):
    """The value does not fit the field (``reason`` names why)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def card_kind(node_type: Optional[str], doc_type: Optional[str]) -> Optional[str]:
    """The settings kind of a card, or None (a work plan card or an unknown type has no settings)."""
    if node_type == "custom":
        return RUN                               # a palette [+] card works like research / work
    if node_type == "gate":
        return CHECK
    if node_type != "document":
        return None
    t = str(doc_type or "").strip().upper()
    if t in _ROOT_TYPES:
        return REQUIREMENT
    if t == "TS":
        return TEST
    return RUN if t in _RUN_DOC_TYPES else None


def fields_of(kind: Optional[str]) -> tuple:
    return _FIELDS.get(kind or "", ())


def field_of(kind: Optional[str], name: str) -> Optional[Field]:
    return next((f for f in fields_of(kind) if f.name == name), None)


def check_value(field: Field, value: Any, *, repeat_choices: Iterable[int],
                team_exists: Callable[[str], bool], ai_exists: Callable[[str], bool]) -> Any:
    """The value to store (normalized), or ``InvalidValue``. ``None`` is never passed here."""
    if field.kind == CHOICE:
        if not isinstance(value, str) or value not in field.choices:
            raise InvalidValue("not_a_choice")
        return value
    if field.kind == MINUTES:
        if isinstance(value, bool) or not isinstance(value, int) or value not in field.choices:
            raise InvalidValue("not_a_choice")
        return value
    if field.kind == REPEAT:
        if isinstance(value, bool) or not isinstance(value, int) or value not in tuple(repeat_choices):
            raise InvalidValue("not_a_choice")
        return value
    if field.kind == LIMIT:
        if isinstance(value, bool) or not isinstance(value, int) or not LIMIT_MIN <= value <= LIMIT_MAX:
            raise InvalidValue("out_of_range")
        return value
    if field.kind == FLAG:
        if not isinstance(value, bool):
            raise InvalidValue("not_a_boolean")
        return value
    text = _text(value)
    if field.kind == AI and not ai_exists(text):
        raise InvalidValue("unknown_ai")          # the node token names it (tokens.provider_id FK)
    if field.kind == TEAM and not team_exists(text):
        raise InvalidValue("unknown_team")
    if field.kind == BRANCH and (not _BRANCH_RE.match(text) or ".." in text or text.endswith((".", "/", ".lock"))):
        raise InvalidValue("invalid_branch")
    return text


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidValue("not_a_text")
    text = value.strip()
    if len(text) > TEXT_MAX_LEN or any(ord(c) < 32 for c in text):
        raise InvalidValue("invalid_text")
    return text


def choices_of(field: Field, repeat_choices: Iterable[int]) -> Optional[list]:
    """The selectable values of a field, or None (free value: an AI, a team, a branch, a number)."""
    if field.kind in (CHOICE, MINUTES):
        return list(field.choices)
    if field.kind == REPEAT:
        return list(repeat_choices)
    if field.kind == FLAG:
        return [True, False]
    return None


def view(kind: Optional[str], stored: dict, repeat_choices: Iterable[int]) -> dict:
    """``field -> {value, stored, default, choices}`` in display order (shown value = stored, else default)."""
    choices = tuple(repeat_choices)
    out = {}
    for f in fields_of(kind):
        has = f.name in stored
        out[f.name] = {"value": stored[f.name] if has else f.default, "stored": has,
                       "default": f.default, "choices": choices_of(f, choices)}
    return out


def result_doc_type(node_type: Optional[str], doc_type: Optional[str], stored: dict) -> Optional[str]:
    """The type the node's AI writes and the Graph binds when the card changes it, else None.

    Only an instruction-led document card whose report stands alone (N / T) with its instruction
    omitted writes another type: its report.
    """
    if stored.get("instruction") != "omit" or node_type != "document":
        return None
    return REPORT_TYPES.get(str(doc_type or "").strip().upper())


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def after_result(kind: Optional[str], stored: dict) -> Optional[str]:
    """What follows a node's AI result: the card's AI review (``REVIEW``), an approval (``APPROVE``:
    after review follow with no review), or None (a person decides, as before)."""
    if kind not in (RUN, TEST):
        return None
    if _count(stored.get("review_count")) not in (None, 0):
        return REVIEW
    return APPROVE if stored.get("after_review") == "follow" else None


def review_loop(stored: dict, *, reviewer: str, reworker: str) -> dict:
    """The v0.1 document review loop request of a card's review (``after_result`` = ``REVIEW``).

    Review count and basis are the card's; the reviewer is its review AI (resolved by the caller,
    the project default when the card names none) and the rework goes to the AI that runs the node.
    The rework hop gets the longest loop budget within the card's time limit, failure restarts follow
    the card's restart within the loop's own range, and the loop as a whole gets its longest budget.
    """
    limit = (stored.get("time_limit") if stored.get("time_limit") in TIME_LIMIT_MINUTES
             else DEFAULT_TIME_LIMIT_MINUTES) * 60
    rework = max([s for s in LOOP_REWORK_TIMEOUTS_SEC if s <= limit] or [LOOP_REWORK_TIMEOUTS_SEC[0]])
    restart = _count(stored.get("restart"))
    if restart is None or restart < -1:
        restart = 0
    elif restart > LOOP_FAILURE_RESTARTS[-1]:
        restart = LOOP_FAILURE_RESTARTS[-1]
    basis = stored.get("review_basis") if stored.get("review_basis") in REVIEW_CRITERIA else "type"
    return {"review_count": stored["review_count"], "reviewer_provider_id": reviewer,
            "review_criteria": REVIEW_CRITERIA[basis], "rework_provider_id": reworker,
            "rework_timeout_sec": rework, "rework_message": "", "failure_restart_max_attempts": restart,
            "total_timeout_sec": LOOP_TOTAL_TIMEOUT_SEC}


# What a review hop is told to review against (``review_basis_note``), by the loop's review criteria.
_TYPE_BASIS_NOTE = ("Review basis: document type. Review the target document against all of the "
                    "requirements and pass criteria of its document type.")
_LAST_BASIS_NOTE = ("Review basis: last rejection only. Review ONLY whether every reason of the document's "
                    "last rejection below is resolved, and raise no issue that the last rejection did not "
                    "name; this replaces evaluating it against all of its requirements and pass criteria.\n\n"
                    "Last rejection:\n{reason}")
_LAST_BASIS_NONE_NOTE = ("Review basis: last rejection only. The document has no rejection yet, so this "
                         "review uses the document type basis: review it against all of the requirements "
                         "and pass criteria of its document type.")


def review_basis_note(criteria: Optional[str], last_rejection: Optional[str]) -> str:
    """What every review hop of a card's review is told to review against: the document type's
    requirements and pass criteria (``type``), or only the document's last rejection (``last``) -- and,
    while the document has no rejection yet, the document type's, said so."""
    if criteria != REVIEW_CRITERIA["last"]:
        return _TYPE_BASIS_NOTE
    reason = (last_rejection or "").strip()
    return _LAST_BASIS_NOTE.format(reason=reason) if reason else _LAST_BASIS_NONE_NOTE


def run_plan(kind: Optional[str], stored: dict, node_type: Optional[str] = None,
             doc_type: Optional[str] = None) -> dict:
    """What a node's AI start takes from its card (``card_settings_ports.run_settings``).

    Only stored values are handed on, so a card with nothing stored starts exactly as before.
    """
    if kind not in (RUN, TEST):
        return {}
    plan: dict = {"settings": {f.name: stored[f.name] for f in fields_of(kind) if f.name in stored}}
    written = result_doc_type(node_type, doc_type, stored)
    if written:
        plan["result_doc_type"] = written
    if stored.get("run_ai"):
        plan["provider_id"] = stored["run_ai"]
    if kind == RUN and stored.get("time_limit") in TIME_LIMIT_MINUTES:
        plan["step_timeout_sec"] = int(stored["time_limit"]) * 60
    if kind == RUN and isinstance(stored.get("restart"), int) and not isinstance(stored.get("restart"), bool):
        plan["restart_max"] = stored["restart"]
    return plan
