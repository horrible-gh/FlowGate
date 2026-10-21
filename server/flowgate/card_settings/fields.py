"""Card run settings: the settings of each card kind and the values they take (flowgate.v02.0053 T#2).

Pure. The 0053 CH agreement (§4 "settings as drawn") and the v7 settings blocks name the items:

- run cards (research N, work T / T2, design D / P / L / DB, custom): instruction use / omit, instruction AI
  (flowgate.v02.0056 T#2), run AI, restart, review AI, review count, and the advanced time limit and review
  basis;
- test cards (TS): instruction and the scenario writing block (writer AI, review AI, review count). The run block shows each case's mode, which lives on the case inside the TS
  document (0549 contract 2), so the card stores nothing for it;
- requirement cards (R / B): AI delegation off / on, its scope, AI Team and the safety limits —
  values only, no delegation engine (0053 CH: out of this scope);
- check cards (gate): usage, Git apply mode, merge target, push, keep branch;
- work plan cards (WP, flowgate.v02.0072 T#3): the AI pre-instruction writing switch, and the run block the
  v7 plan settings draw (work AI, restart, review AI, review count, the advanced time limit and review basis).
  flowgate.v02.0083 T#1 — 0072 T#3 개정 (revised): the run block is no longer values only. A plan card whose writer is
  an AI (``settings.uses_ai``: an explicit person assignee -> a person; else the card's work AI ``run_ai`` -> AI; else
  an explicit AI assignee -> AI; else a person, the default) runs as one AI run of its own: ``run_plan`` hands on its
  work AI, time limit and restarts (no instruction stage, no result type: the AI writes the WP itself, the type the
  result binder binds), and ``after_result`` gives it the run cards' review / approval. A plan card a person writes is
  unchanged: its result waits for a person's approval of the card. Still no engine reads the switch (no
  pre-instruction writer exists), and the run block is not the Steps' own AI / review AI inside the WP document body
  (``provider_id`` / ``reviewer_provider_id``), which nothing here reads or changes. Turning the switch off deletes no
  Step's pre-instruction.

A field with no stored value shows its default. The default is what the engine does without the
setting today, so storing nothing and storing the default run the same way. ``None`` as a default
means "decided elsewhere": the node's assignee AI, the project's default reviewer, or the final
approval dialog that asks the Git choice.

How a run applies them (``run_plan``, ``instruction_stage``, ``result_doc_type``, ``after_result``,
``review_loop``):

- instruction use (the default; flowgate.v02.0056 T#2, 0002-CH turn 5 / 6 §3 / 10 O2): a run card runs in two
  stages. The instruction stage writes the card's instruction (N, T, T2, DS, a Custom card's T) with the
  instruction AI — ``instruction_ai``, else ``run_ai``, else the node's assignee AI — once, with no review,
  and approves it; an instruction that already has a body is not written again, only approved. The result
  stage then writes the card's report (NR, TR, TR2, the design document D / P / L / DB, a Custom card's TR)
  with the run AI, the instruction attached as a reference; that report is the node's result and the review
  below applies to it alone. So a work card that wrote only its T now goes on to its TR, and a design card
  that wrote only its design document now writes its DS first. A test card (TS) has no instruction AI and
  writes as before (the TS series is reorganized later);
- instruction omit: the AI writes the card's report instead of the instruction-led node's own type
  (N -> NR, T -> TR, v0.1 AUTO_REPORT_MAP) and that report is the node's result. A Custom card runs as a
  work instruction (flowgate.v02.0053 T#8), so it folds like a work card: T -> TR. A design card
  (D / P / L / DB) already writes its own document after the DS instruction, a test card's TS never
  folds (its TSR is server-assembled, as in v0.1), and an apply card's report (TR2) names the T2 it
  applies (``source_t2_doc_id``) while omit leaves the card no instruction document (the v7 settings
  hint; v0.1 folds a T2 only after it has materialized and approved that T2), so omit changes nothing
  for them;
- review count not 0: when the node's AI run has ended, the card's review AI reviews the result with
  the v0.1 document review loop (review count, review basis, rework by the run AI); every review hop
  is told the review basis (``review_basis_note``);
- what follows the review is fixed (flowgate.v02.0056 T#1, 0002-CH turn 5~10: the after review setting
  is gone): the result is approved when the review passed, or at once with no review; a review that
  ends any other way (count spent, failure, stop) and a result a person wrote wait for a person's
  approval of the card. A stored ``after_review`` value is not a field any more, so nothing reads it
  (migration 147 deletes the rows).

Value checks that need the server (the repeat count range, an AI or AI Team that exists) are passed in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from flowgate.card_documents.slots import CUSTOM_DOC_TYPES, instruction_and_report

RUN, TEST, REQUIREMENT, CHECK, PLAN = "run", "test", "requirement", "check", "plan"
CARD_KINDS = (RUN, TEST, REQUIREMENT, CHECK, PLAN)

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
# The instruction stage of a run card (``instruction_stage``) and its stage names (``graph_node_runs.stage``).
INSTRUCTION_STAGE, RESULT_STAGE = "instruction", "result"
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
_PLAN_TYPES = frozenset({"WP"})
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    default: Any = None
    choices: tuple = ()


_INSTRUCTION = Field("instruction", CHOICE, "use", ("use", "omit"))
_INSTRUCTION_AI = Field("instruction_ai", AI)    # None: the run AI, else the node's assignee AI
_RUN_AI = Field("run_ai", AI)                    # None: the node's assignee AI
_REVIEW_AI = Field("review_ai", AI)              # None: the project's default reviewer
_REVIEW_COUNT = Field("review_count", REPEAT, 0)
_RESTART = Field("restart", REPEAT, 0)           # a single node run is one-shot today
_TIME_LIMIT = Field("time_limit", MINUTES, DEFAULT_TIME_LIMIT_MINUTES, TIME_LIMIT_MINUTES)
_REVIEW_BASIS = Field("review_basis", CHOICE, "type", tuple(REVIEW_CRITERIA))
# flowgate.v02.0072 T#3: the work plan card's AI pre-instruction writing switch. Off by default: nothing writes a
# pre-instruction by itself today, so storing nothing and storing off mean the same.
PRE_INSTRUCTION_AUTO = Field("pre_instruction_auto", FLAG, False)
_FIELDS = {
    RUN: (
        _INSTRUCTION, _INSTRUCTION_AI, _RUN_AI, _RESTART, _REVIEW_AI, _REVIEW_COUNT, _TIME_LIMIT, _REVIEW_BASIS,
        Field("instruction_review_ai", AI), Field("instruction_review_count", REPEAT, 0),
        Field("instruction_note", "stage_text", ""), Field("run_note", "stage_text", ""),
        Field("run_pre_instruction", "stage_text", ""),
    ),
    TEST: (_INSTRUCTION, _RUN_AI, _REVIEW_AI, _REVIEW_COUNT),
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
    PLAN: (PRE_INSTRUCTION_AUTO, _RUN_AI, _RESTART, _REVIEW_AI, _REVIEW_COUNT, _TIME_LIMIT, _REVIEW_BASIS),
}


# 0061 T#11: Hold (the node is not opened by a Graph run while on). Every card has it, whatever its kind (also a card
# kind with no run settings), so it is not one of a kind's fields: no run plan hands it on, and its write rules are the
# Graph run's (``settings.write`` -> ``flowgate.graph_run.hold``), not the card settings lock.
HOLD = Field("hold", FLAG, False)


class InvalidValue(ValueError):
    """The value does not fit the field (``reason`` names why)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def card_kind(node_type: Optional[str], doc_type: Optional[str]) -> Optional[str]:
    """The settings kind of a card, or None (an unknown type has no settings). flowgate.v02.0072 T#3: a work plan
    card keeps the plan settings (flowgate.v02.0083 T#1, 0072 T#3 개정: its run block runs an AI writer, see the head
    of this module)."""
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
    if t in _PLAN_TYPES:
        return PLAN
    return RUN if t in _RUN_DOC_TYPES else None


def fields_of(kind: Optional[str]) -> tuple:
    return _FIELDS.get(kind or "", ())


def field_of(kind: Optional[str], name: str) -> Optional[Field]:
    if name == HOLD.name:
        return HOLD                                # 0061 T#11: every card
    return next((f for f in fields_of(kind) if f.name == name), None)


def check_value(field: Field, value: Any, *, repeat_choices: Iterable[int],
                team_exists: Callable[[str], bool], ai_exists: Callable[[str], bool]) -> Any:
    """The value to store (normalized), or ``InvalidValue``. ``None`` is never passed here."""
    if field.kind == "stage_text":
        if not isinstance(value, str) or len(value) > 100_000 or "\x00" in value:
            raise InvalidValue("invalid_text")
        return value
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
    """``field -> {value, stored, default, choices}`` in display order (shown value = stored, else default); 0061
    T#11: the card's Hold last, for every card."""
    choices = tuple(repeat_choices)
    out = {}
    for f in fields_of(kind) + (HOLD,):
        has = f.name in stored
        out[f.name] = {"value": stored[f.name] if has else f.default, "stored": has,
                       "default": f.default, "choices": choices_of(f, choices)}
    return out


def _omitted(stored: dict) -> bool:
    return stored.get("instruction") == "omit"


def instruction_stage(node_type: Optional[str], doc_type: Optional[str], stored: dict) -> Optional[str]:
    """The instruction type a run card's AI run writes first (instruction use: N / T / T2 / DS, a Custom
    card's T), else None: no instruction stage (instruction omitted, a test / requirement / plan card)."""
    if _omitted(stored) or node_type not in ("document", "custom"):
        return None
    pair = instruction_and_report(node_type, doc_type)
    return pair[0] if pair else None


def instruction_provider(stored: dict) -> Optional[str]:
    """The AI of the instruction stage: the card's instruction AI, else its run AI; None = the node's assignee."""
    return stored.get("instruction_ai") or stored.get("run_ai") or None


def result_doc_type(node_type: Optional[str], doc_type: Optional[str], stored: dict,
                    one_stage: bool = False) -> Optional[str]:
    """The type the node's AI writes as the node's result and the Graph binds, when the card changes it,
    else None (the node's own type).

    - instruction use: a run card's result is its report (NR / TR / TR2 / the design document, a Custom
      card's TR), written after the instruction stage (flowgate.v02.0056 T#2) — except for a card run
      opened before that (``one_stage``: its node run has no stage, migration 148), whose AI was told to
      write the node's own type and still does;
    - instruction omitted: an instruction-led card whose report stands alone (N / T, and a Custom card,
      which runs as its work instruction T) writes its report instead; the others are unchanged.
    """
    if node_type not in ("document", "custom"):
        return None
    if not _omitted(stored):
        if one_stage:
            return None
        pair = instruction_and_report(node_type, doc_type)
        return pair[1] if pair else None
    own = CUSTOM_DOC_TYPES[0] if node_type == "custom" else doc_type
    return REPORT_TYPES.get(str(own or "").strip().upper())


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def after_result(kind: Optional[str], stored: dict) -> Optional[str]:
    """What follows a node's AI result: the card's AI review (``REVIEW``: review count not 0), else an
    approval at once (``APPROVE``: no review); None for a card kind without a run (a person decides).
    flowgate.v02.0083 T#1 (0072 T#3 개정): a work plan card's AI result too; requirement and check cards stay None."""
    if kind not in (RUN, TEST, PLAN):
        return None
    if _count(stored.get("review_count")) not in (None, 0):
        return REVIEW
    return APPROVE


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

    Only stored values are handed on, except the two stages of instruction use (flowgate.v02.0056 T#2), which
    is the default: ``instruction`` (the instruction stage's type and AI) and the report as ``result_doc_type``.
    flowgate.v02.0083 T#1 (0072 T#3 개정): a work plan card hands on its work AI, time limit and restarts as a run card
    does; it has no instruction stage and no result type of its own (``instruction_stage`` / ``result_doc_type`` are
    None for WP), so its AI runs the result stage alone and writes the WP the result binder binds.
    """
    if kind not in (RUN, TEST, PLAN):
        return {}
    plan: dict = {"settings": {f.name: stored[f.name] for f in fields_of(kind) if f.name in stored}}
    written = result_doc_type(node_type, doc_type, stored)
    if written:
        plan["result_doc_type"] = written
    instruction = instruction_stage(node_type, doc_type, stored) if kind == RUN else None
    if instruction:
        plan["instruction"] = {"doc_type": instruction, "provider_id": instruction_provider(stored)}
    if stored.get("run_ai"):
        plan["provider_id"] = stored["run_ai"]
    if kind in (RUN, PLAN) and stored.get("time_limit") in TIME_LIMIT_MINUTES:
        plan["step_timeout_sec"] = int(stored["time_limit"]) * 60
    if kind in (RUN, PLAN) and isinstance(stored.get("restart"), int) and not isinstance(stored.get("restart"), bool):
        plan["restart_max"] = stored["restart"]
    return plan
