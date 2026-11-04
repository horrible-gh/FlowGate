"""The result of one step / full command (flowgate.v02.0113 T#5, 0030-M §5, 0002-D 개정 §3.14 / §6.5).

``GET /api/v1/runs/{run_id}/command-result[?command_seq=N]`` (``payload``): what one [다음 단계] / [전체 실행] command did,
read from what the run already records — every existing trace stays.

- The command (``command_window``): from the run status event a person's command wrote (``user_start`` /
  ``user_command``, or ``user_resume`` of an idle run; the resume of a paused run goes on with the paused command) up to
  the next command or a Graph Time Machine operation. Inside that range only the command's own events are its: every
  event while its run works (running / paused / stopping), and while the run is idle only the events of its own cards
  (the work its commit left behind: a satisfied card's automatic approval, a person's save of a card it opened and
  waits for) — never a Hold set or released, never another card's event. The run going on from idle belongs to it only
  as ``work_resumed`` when it waits for a person (``waiting_approval`` of its own mode — ``command._waiting_command``'s
  chain) or when its own cards' work woke it; any other move (a Stop of the idle run, a wake for another card) ends it.
  ``command_seq`` is its start event's sequence; none given: the run's latest command. A command another one took over
  before its run was idle (a paused run commanded again) ended there (``superseded``). ``continues``: the latest
  command ended waiting and nothing ended it yet — a person's save or approval may still take it on (to completed or
  failed) with no new command, so a screen keeps watching it.
- Its scope: ``step`` / ``full``, the mode its start event tells.
- Its nodes: every node a node event of the command moved (the root binding's input completion is no work and not
  counted), every node its stage events or role invocation records name. Each node's outcome is judged at the command's
  end: ``done``, ``failed`` (a failure of this command and the node not done), ``waiting`` (open / in review), ``running``
  (the command has not ended), ``pending``.
- Its role invocation records (§3.10): a record is the command's by the run event its request was made at — the run's
  ``last_event_seq`` the execution ledger stored with the request (``run_event_seq``), else the stage assignment event
  of the record's attempt / stage / seq — inside the command's event range: never by a rounded time. Only a record
  with neither (a record of no Graph execution) falls back to its time, given to exactly one command (the latest one
  started at or before it, and not after that command's end); such a message says so (``attribution: "time"``).
- Its failures (the errors this command made, D 개정 §3.14-1): an AI start or call that failed (a role invocation record
  ``admission_refused`` / ``start_failed`` / ``call_failed`` with no later success of the same node and role), a failed
  stage (``stage_failed``), an AI that cannot run (a ``wait_unavailable`` stage assignment), an automatic approval not
  made (B-1 ``auto_approval_skipped``: a review not passed / exhausted, a refusal, …; a person's work plan result waiting
  for its person is no failure), a rejected node, a failed dispatch, and — for the run's latest command, the node as the
  command left it — the run's own error family (``settle._failed``). One failing branch fails the command even while
  another waits.
- Its state: ``running`` (not ended), ``failed``, ``completed`` (the run completed, no failure), ``stopped`` (a Stop),
  ``superseded``, else ``waiting`` (a step boundary, a manual write wait, a branch waiting). AI calls are never counted
  for it: a Flow of manual cards that ends is ``completed`` with no AI call.
- Its messages (§3.10 records and node events of the command): each role invocation record with its role, card, the
  model requested and the model confirmed (told apart), its time, its body and its full error — the launch's own output
  (``ai_role_invocation_outputs``, ``body_source: "launch"``); for a launch with none stored (a record made before that
  store) only the AI run's last launch shows the run's final output (``"run"``), any other launch says its output was not
  preserved (``"not_preserved"``) and never shows another launch's output or error. Each node event and each failure is
  a short event message. Nothing is cut.
- Its blocking (flowgate.v02.0124 T#2, 0003-NR §5.3-1): only the run's latest command that ended idle waiting for a
  person (``PERSON_WAIT_REASONS``) and was not cut lists the cards that wait on a person's intervention — the current
  node runs whose ``engine.intervention`` is a dict (a gate, a manual write wait and a Hold are none) — each with the
  intervention's own ``reason`` (never translated) and a ``release`` code the server judges from the stage assignment
  rows (``_release_of``): ``new_submission`` (a person's document), ``issuer_permission``, ``memo_rereview``, or None
  (a system error, or a card the next command requests again). Every other command: ``[]``. ``nodes`` / ``counts`` /
  ``state`` keep their meaning. A read only.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from flowgate.ai_roles import record_store
from flowgate.graph_run import legacy_adapter as engine

log = logging.getLogger(__name__)

COMMAND_CAUSES = ("user_start", "user_command", "user_resume")
STATE_RUNNING, STATE_COMPLETED, STATE_FAILED, STATE_WAITING = "running", "completed", "failed", "waiting"
STATE_STOPPED, STATE_SUPERSEDED = "stopped", "superseded"
FAILED_IDLE_REASONS = ("error", "blocked_rejected", "merge_conflict", "blocked_recovery")
FAILED_RESULTS = ("admission_refused", "start_failed", "call_failed")
ROOT_BOUND_CAUSE = "root_bound_start"
TIME_MACHINE_CAUSE = "graph_time_machine"
WORK_RESUMED = "work_resumed"
WAITING_APPROVAL, STOPPED_REASON, COMPLETED_REASON = "waiting_approval", "stopped", "completed"
# flowgate.v02.0119 T#3 (T-2): a person's wait is a confirm (``waiting_confirm``) or a card's intervention
# (``needs_intervention``); ``waiting_approval`` is the value stored before T-2, read the same way.
PERSON_WAIT_REASONS = ("waiting_confirm", "needs_intervention", WAITING_APPROVAL)
HOLD_CAUSES = ("hold_set", "hold_released")             # a person's Hold setting: a refresh signal, no work
STAGE_ASSIGNED, STAGE_FAILED, APPROVAL_SKIPPED = "stage_assigned", "stage_failed", "auto_approval_skipped"
ISSUER_LACKS_APPROVE = "issuer_lacks_approve"
PERSON_APPROVAL_WAITS = (ISSUER_LACKS_APPROVE,)         # a person's work plan result waits for its person (0083 WP-4)
# flowgate.v02.0124 T#2 (0003-NR §5.3-1): how a blocking card is released (``blocking[].release``; None: no line).
RELEASE_NEW_SUBMISSION, RELEASE_ISSUER, RELEASE_MEMO = "new_submission", "issuer_permission", "memo_rereview"
ROLE_OF_STAGE = {"instruction": "instruction", "result": "execution", "result_review": "review",
                 "instruction_review": "review"}
ROLE_OF_INVOCATION = {"instruction": "instruction", "execution": "execution", "review": "review",
                      "instruction_review": "review", "rework": "execution"}
WAIT_OF_ACTOR = {"person": "manual_write_wait", "wait_setup": "ai_setup_required", "wait_unavailable": "ai_unavailable"}
BODY_LAUNCH, BODY_RUN, BODY_NOT_PRESERVED, BODY_NONE = "launch", "run", "not_preserved", "none"


class CommandNotFound(LookupError):
    """No command of the run has the asked start sequence (or the run has no command at all)."""


def _ms(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return int(at.timestamp() * 1000 + 0.5)


def _payload_of(event: dict) -> dict:
    try:
        data = json.loads(event.get("payload_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_run_status(event: dict) -> bool:
    return event.get("event_type") == engine.RUN_STATUS_EVENT


def _moves_run(event: dict) -> bool:
    """A run status event that changes the run's status (not a stage / approval record of an unchanged run)."""
    return _is_run_status(event) and (event.get("from_status") or "") != (event.get("to_status") or "")


def _is_command_start(event: dict) -> bool:
    return (_is_run_status(event) and event.get("cause") in COMMAND_CAUSES
            and not (event.get("cause") == "user_resume" and event.get("from_status") == engine.PAUSED))


def _node_of(event: dict) -> Optional[str]:
    return event.get("node_key") or _payload_of(event).get("node_key")


def command_starts(events: list[dict]) -> list[int]:
    """The indexes of the run's command start events, oldest first."""
    return [i for i, e in enumerate(events) if _is_command_start(e)]


@dataclass
class Window:
    """One command's events: ``included`` (indexes into the run's events, the start first), how it ended, and the
    run's status after its last event."""
    start: int
    included: list[int] = field(default_factory=list)
    cut: bool = False                  # the next command / a Time Machine / another move of the idle run ended it
    cut_index: Optional[int] = None    # the event that ended it (None: nothing yet)
    status: Optional[str] = None
    ended_by: Optional[str] = None     # "idle" / "superseded" / None (still going)

    @property
    def end(self) -> Optional[int]:
        return self.included[-1] if self.ended_by else None

    @property
    def last(self) -> int:
        return self.included[-1]


def command_window(events: list[dict], start: int) -> Window:
    """The command starting at ``events[start]`` (see the module doc)."""
    head = events[start]
    mode = _payload_of(head).get("mode")
    window = Window(start, [start], status=head.get("to_status"))
    nodes: set[str] = set()
    person_wait = False   # its run waits for a person of this command (waiting_approval of its own mode)
    linked = False        # since its run went idle, an event of its own cards was its
    stopped = False       # a Stop settled it: nothing more is its
    for i in range(start + 1, len(events)):
        event = events[i]
        if _is_command_start(event) or event.get("cause") == TIME_MACHINE_CAUSE:
            window.cut, window.cut_index = True, i
            break
        key = _node_of(event)
        if _moves_run(event):
            if window.status == engine.IDLE and (
                    stopped or event.get("cause") != WORK_RESUMED or not (person_wait or linked)):
                window.cut, window.cut_index = True, i          # the idle run moved for something else
                break
            window.included.append(i)
            window.status = event.get("to_status")
            if window.status == engine.IDLE:
                data = _payload_of(event)
                person_wait = data.get("idle_reason") in PERSON_WAIT_REASONS and data.get("mode") == mode
                stopped = data.get("idle_reason") == STOPPED_REASON
                linked = False
            continue
        if event.get("cause") in HOLD_CAUSES:
            continue
        if window.status != engine.IDLE:
            window.included.append(i)
            if key:
                nodes.add(key)
        elif not stopped and key and key in nodes:
            window.included.append(i)
            linked = True
    if window.status == engine.IDLE:
        window.ended_by = "idle"
    elif window.cut:
        window.ended_by = "superseded"
    return window


def _idle_reason_at(events: list[dict]) -> Optional[str]:
    """The idle reason the last move to ``idle`` among ``events`` told."""
    for event in reversed(events):
        if _is_run_status(event) and event.get("to_status") == engine.IDLE:
            reason = _payload_of(event).get("idle_reason")
            if reason:
                return reason
    return None


class _Titles:
    def __init__(self, flow_id: str, node_rows: dict):
        self.rows = node_rows
        self.titles = engine.node_titles(flow_id)

    def of(self, key: Optional[str]) -> dict:
        row = self.rows.get(key or "") or {}
        title = (self.titles.get(key or "") or {}).get("title") or row.get("label") or row.get("doc_type") or key
        return {"title": title, "doc_type": row.get("doc_type")}


def _invocation_failure(record: dict) -> bool:
    return record.get("result") in FAILED_RESULTS


def duplicate_starts(records: list[dict]) -> set[str]:
    """flowgate.v02test.0004 T#3: the records, among ``records``, of a harmless duplicate start stored before T#1 stopped
    recording them — a start refused ``admission_refused`` / ``run_in_progress`` of a Graph execution while a launch of
    that same execution (``graph_exec_id``) was running: made at or after that launch's start and not after its end (a
    launch still running has no end). Told by the execution and the launch's own times, never by the message. Such a
    record is no work and no failure of the command (``payload`` leaves it out); it stays stored as it is, and the
    card's own invocation list still shows it. A refusal of an execution no launch of it covered (another execution's
    lease, no free slot, a refusal before any launch) is not one of them."""
    launches: dict[str, list[tuple[int, Optional[int]]]] = {}
    for record in records:
        exec_id = record.get("graph_exec_id")
        if exec_id and record.get("ai_run_id") and record.get("launch_no") is not None:
            started = _ms(record.get("started_at"))
            if started is not None:
                ended = None if record.get("result") == "running" else _ms(record.get("ended_at"))
                launches.setdefault(exec_id, []).append((started, ended))
    found: set[str] = set()
    for record in records:
        if record.get("result") != "admission_refused" or record.get("result_detail") != "run_in_progress":
            continue
        at = _ms(record.get("created_at"))
        if at is None:
            continue
        if any(started <= at and (ended is None or at <= ended)
               for started, ended in launches.get(record.get("graph_exec_id") or "", ())):
            found.add(record["inv_id"])
    return found


def _record_lines(record: dict) -> list[str]:
    """What the record itself tells of its end (its own columns only)."""
    lines = [f"result: {record.get('result')}" + (f" ({record['result_detail']})" if record.get("result_detail") else "")]
    for label, value in (("exit_code", record.get("exit_code")), ("timeout", record.get("timeout_kind")),
                         ("model_call_failure", record.get("model_call_failure_kind")),
                         ("http_status", record.get("model_http_status")),
                         ("run_end_reason", record.get("run_end_reason")), ("run_stop_code", record.get("run_stop_code"))):
        if value not in (None, ""):
            lines.append(f"{label}: {value}")
    return lines


def _errors_text(errors) -> Optional[str]:
    if not errors:
        return None
    if isinstance(errors, str):
        return errors
    return json.dumps(errors, ensure_ascii=False)


def _error_text(record: dict, source: str, output: Optional[dict], ai_run: Optional[dict]) -> Optional[str]:
    """The full error of a record that did not succeed: its own end, then the output of that launch only — the launch's
    stored output (``launch``), the AI run's final output for its last launch (``run``, labelled so), or a line saying
    the launch's output was not preserved."""
    if record.get("result") in ("succeeded", "running"):
        return None
    lines = _record_lines(record)
    if source == BODY_LAUNCH and output:
        if output.get("detail"):
            lines.append(f"detail: {output['detail']}")
        errors = output.get("register_errors_json")
        if errors:
            lines.append(f"register_errors: {errors}")
        for label in ("stderr_tail", "stdout_tail"):
            if output.get(label):
                lines.append(f"{label}:\n{output[label]}")
                break
    elif source == BODY_RUN and ai_run:
        for label in ("end_reason", "stop_code", "stop_reason"):
            if ai_run.get(label) and str(ai_run[label]) not in "\n".join(lines):
                lines.append(f"ai_run.{label}: {ai_run[label]}")
        errors = _errors_text(ai_run.get("register_errors"))
        if errors:
            lines.append(f"ai_run.register_errors: {errors}")
        for label in ("stderr_tail", "stdout_tail"):
            if ai_run.get(label):
                lines.append(f"ai_run.{label}:\n{ai_run[label]}")
                break
    elif source == BODY_NOT_PRESERVED:
        lines.append("launch_output: not_preserved")
    return "\n".join(lines)


def _models(record: dict) -> tuple[dict, dict]:
    requested = {"ai_id": record.get("requested_ai_id"), "state": record.get("requested_model_state"),
                 "model": record.get("requested_model_value"), "reason": record.get("requested_model_reason")}
    try:
        values = json.loads(record.get("confirmed_model_values_json") or "[]")
    except (TypeError, ValueError):
        values = []
    confirmed = {"ai_id": record.get("actual_ai_id"), "ai_name": record.get("actual_ai_name"),
                 "state": record.get("confirmed_model_state"),
                 "models": [str(v) for v in values] if isinstance(values, list) else [],
                 "reason": record.get("unverifiable_reason")}
    return requested, confirmed


def _assignment_events(events: list[dict]) -> dict:
    """``{(attempt_id, stage, seq): event_seq}`` of the run's stage assignment events (the first of each)."""
    found: dict = {}
    for event in events:
        if _is_run_status(event) and event.get("cause") == STAGE_ASSIGNED:
            data = _payload_of(event)
            if data.get("attempt_id") and data.get("stage") is not None:
                seq = data.get("seq")
                found.setdefault((data["attempt_id"], data["stage"], None if seq is None else int(seq)),
                                 int(event["event_seq"]))
    return found


def _attribute(run_id: str, records: list[dict], events: list[dict], windows: list[Window]) -> dict:
    """``{inv_id: (window index, attribution)}`` — each record of the run to at most one command (module doc)."""
    anchors = engine.execution_anchors(run_id) if records else {}
    assigned = _assignment_events(events)
    seqs = [int(e["event_seq"]) for e in events]

    def anchor(record: dict) -> Optional[int]:
        seq = anchors.get(record.get("graph_exec_id") or "")
        if seq is None and record.get("target_attempt_id"):
            target_seq = record.get("target_seq")
            seq = assigned.get((record["target_attempt_id"], record.get("target_stage"),
                                None if target_seq is None else int(target_seq)))
        return seq

    # An AI run's launches are one request's: the run's first anchored launch tells them all.
    run_anchor: dict[str, int] = {}
    for record in records:
        if record.get("ai_run_id") and record["ai_run_id"] not in run_anchor:
            seq = anchor(record)
            if seq is not None:
                run_anchor[record["ai_run_id"]] = seq

    bounds = []
    for index, window in enumerate(windows):
        low = seqs[window.start]
        if window.ended_by:
            high = seqs[window.last]
        elif index + 1 < len(windows):
            high = seqs[windows[index + 1].start] - 1
        else:
            high = None
        bounds.append((low, high, _ms(events[window.start].get("created_at")),
                       _ms(events[window.last].get("created_at")) if window.ended_by else None))

    out: dict = {}
    for record in records:
        seq = run_anchor.get(record.get("ai_run_id") or "") if record.get("ai_run_id") else None
        if seq is None:
            seq = anchor(record)
        if seq is not None:
            for index, (low, high, _l, _h) in enumerate(bounds):
                if low <= seq and (high is None or seq <= high):
                    out[record["inv_id"]] = (index, "event")
                    break
            continue
        at = _ms(record.get("created_at"))
        if at is None:
            continue
        chosen = None
        for index, (_low, _high, start_ms, _end_ms) in enumerate(bounds):
            if start_ms is not None and start_ms <= at:
                chosen = index
        if chosen is not None and (bounds[chosen][3] is None or at <= bounds[chosen][3]):
            out[record["inv_id"]] = (chosen, "time")
    return out


def _intervention_of(flow_id: str, row: dict, node_type) -> Optional[dict]:
    """``{"kind", "reason"}`` of the node run's card in intervention, else None. A failed read tells none (as the run
    read does, ``run_service.intervention``): the card is left out and the read goes on."""
    try:
        return engine.intervention(flow_id, row, node_type)
    except Exception:  # noqa: BLE001 — not told
        log.warning("command result intervention read failed flow=%s node=%s", flow_id, row.get("node_key"), exc_info=True)
        return None


def _release_of(run_id: str, key: str, found: dict) -> Optional[str]:
    """flowgate.v02.0124 T#2 (D4): how the blocking card ``found`` is released, judged from the stage assignment rows,
    never from the reason text. A system error: None (the automatic retry takes it). ``issuer_lacks_approve``:
    ``issuer_permission`` (before the author, 0083). A person's document (the latest result review's ``author`` is the
    person, an assigned attempt): ``new_submission``. A spent budget of an AI's document with its reviewer and rework AI:
    None (the next command requests it again, 0124 T#1). Any other: ``memo_rereview``. A failed read: None. A read only."""
    from flowgate.card_intervention import reasons  # noqa: PLC0415
    from flowgate.card_settings import legacy_adapter as settings  # noqa: PLC0415
    from flowgate.card_settings import stage_assignment as sa  # noqa: PLC0415

    try:
        reason = found.get("reason")
        if found.get("kind") == reasons.SYSTEM_ERROR:
            return None
        if reason == ISSUER_LACKS_APPROVE:
            return RELEASE_ISSUER
        attempt_id = settings.active_attempt_id(run_id, key)
        cur = None if sa.is_legacy_attempt(attempt_id) else sa.current_review_assignment(attempt_id, sa.RESULT_REVIEW)
        if cur is not None and cur.get("author") == sa.PERSON:
            return RELEASE_NEW_SUBMISSION
        if reasons.command_reentry_due(reason) and not sa.is_legacy_attempt(attempt_id):
            return None                          # 0126: explicit reentry judges the current settings
        return RELEASE_MEMO
    except Exception:  # noqa: BLE001 — not told: no release line
        log.warning("command result release read failed run=%s node=%s", run_id, key, exc_info=True)
        return None


def payload(run_id: str, command_seq: Optional[int] = None) -> dict:
    """``{"command_result": …}`` of the run's command starting at ``command_seq`` (none: its latest). Raises
    ``CommandNotFound`` when there is no such command. A read only."""
    run = engine.run_by_id(run_id)
    if run is None:
        raise CommandNotFound(run_id)
    events = engine.run_events(run_id)
    starts = command_starts(events)
    if command_seq is None:
        chosen = len(starts) - 1 if starts else None
    else:
        chosen = next((n for n, i in enumerate(starts) if int(events[i]["event_seq"]) == int(command_seq)), None)
    if chosen is None:
        raise CommandNotFound(run_id)
    windows = [command_window(events, i) for i in starts]
    win = windows[chosen]
    latest = chosen == len(starts) - 1
    end = win.end
    last = win.last
    head, head_payload = events[win.start], _payload_of(events[win.start])
    window = [events[i] for i in win.included]
    ctx = engine.load_context(run, None, None)
    titles = _Titles(run["group_id"], ctx.node_rows)

    # Node status as the command left it (every node event of the run up to its last event).
    status_at: dict[str, str] = {}
    for event in events[:last + 1]:
        if event.get("event_type") == engine.NODE_STATE_EVENT and event.get("node_key"):
            status_at[event["node_key"]] = event.get("to_status")
    rows = {r["node_key"]: r for r in engine.node_runs(run_id)}

    touched: list[str] = []
    done_in_command: set[str] = set()
    failures: list[dict] = []
    stage_waits: dict[str, str] = {}
    roles: dict[str, list[dict]] = {"instruction": [], "execution": [], "review": []}
    messages: list[dict] = []

    def touch(key: Optional[str]) -> None:
        if key and key not in touched:
            touched.append(key)

    def role_used(role: Optional[str], entry: dict) -> None:
        """One entry per (kind, AI) of a role; a name told by any record of it is kept."""
        if role not in roles:
            return
        same = next((e for e in roles[role] if (e["kind"], e["ai_id"]) == (entry["kind"], entry["ai_id"])), None)
        if same is None:
            roles[role].append(dict(entry))
        elif not same.get("ai_name") and entry.get("ai_name"):
            same["ai_name"] = entry["ai_name"]

    def fail(key: Optional[str], code: str, detail: Optional[str], at: Optional[str], role: Optional[str] = None,
             message: bool = True) -> None:
        failures.append({"node_key": key, **titles.of(key), "code": code, "detail": detail, "at": at, "role": role})
        if message:
            messages.append({"kind": "event", "event": "failure", "code": code, "detail": detail, "node_key": key,
                             **titles.of(key), "role": role, "at": at, "error": True})

    for event in window:
        data = _payload_of(event)
        at = event.get("created_at")
        if event.get("event_type") == engine.NODE_STATE_EVENT:
            key = event.get("node_key")
            if event.get("cause") == ROOT_BOUND_CAUSE:
                continue
            touch(key)
            if event.get("to_status") == "done":
                done_in_command.add(key)
            messages.append({"kind": "event", "event": "node_state", "code": event.get("cause"), "node_key": key,
                             **titles.of(key), "from": event.get("from_status"), "to": event.get("to_status"),
                             "detail": None, "role": None, "at": at, "error": False})
            if event.get("to_status") == "rejected":
                fail(key, "review_rejected", event.get("reason"), at)
            dispatch = data.get("dispatch") if isinstance(data.get("dispatch"), dict) else {}
            if dispatch.get("error_code") and not str(dispatch["error_code"]).startswith("ai_unavailable"):
                fail(key, "dispatch_failed", str(dispatch["error_code"]), at)
            continue
        cause = event.get("cause")
        key = data.get("node_key")
        if cause == STAGE_ASSIGNED:
            touch(key)
            role = ROLE_OF_STAGE.get(data.get("stage"))
            actor = data.get("actor") or event.get("reason")
            entry = ({"kind": "ai", "ai_id": data.get("provider_id"), "ai_name": data.get("provider_name")}
                     if actor == "ai" else {"kind": actor, "ai_id": data.get("provider_id"),
                                            "ai_name": data.get("provider_name")})
            role_used(role, entry)
            if actor in WAIT_OF_ACTOR and key:
                stage_waits[key] = WAIT_OF_ACTOR[actor]
            if actor == "wait_unavailable":
                fail(key, "ai_unavailable", data.get("cause_code"), at, role)
        elif cause == STAGE_FAILED:
            touch(key)
            fail(key, "stage_failed", data.get("fail_cause") or event.get("reason"), at, ROLE_OF_STAGE.get(data.get("stage")))
        elif cause == APPROVAL_SKIPPED and event.get("reason") not in PERSON_APPROVAL_WAITS:
            touch(key)
            fail(key, "auto_approval_failed", event.get("reason"), at)

    # The §3.10 role invocation records of the command: attributed by the run event their request was made at.
    all_records = record_store.for_graph_run(run_id) if record_store.available() else []
    owner = _attribute(run_id, all_records, events, windows)
    duplicates = duplicate_starts(all_records)          # flowgate.v02test.0004 T#3: stored before T#1, no work of it
    records = [r for r in all_records if owner.get(r["inv_id"], (None,))[0] == chosen and r["inv_id"] not in duplicates]
    last_launch: dict[str, int] = {}
    for record in all_records:
        if record.get("ai_run_id") and record.get("launch_no") is not None:
            last_launch[record["ai_run_id"]] = max(last_launch.get(record["ai_run_id"], 0), int(record["launch_no"]))
    outputs = record_store.outputs_for([r["inv_id"] for r in records]) if records else {}
    succeeded_after: dict[tuple, int] = {}
    for i, record in enumerate(records):
        if record.get("result") == "succeeded":
            succeeded_after[(record.get("target_node_key"), ROLE_OF_INVOCATION.get(record.get("inv_role")))] = i
    ai_runs: dict[str, Optional[dict]] = {}
    for i, record in enumerate(records):
        key, role = record.get("target_node_key"), ROLE_OF_INVOCATION.get(record.get("inv_role"))
        touch(key)
        ai_run_id = record.get("ai_run_id")
        requested, confirmed = _models(record)
        if record.get("actual_ai_id") or record.get("requested_ai_id"):
            role_used(role, {"kind": "ai", "ai_id": record.get("actual_ai_id") or record.get("requested_ai_id"),
                             "ai_name": record.get("actual_ai_name")})
        output = outputs.get(record["inv_id"])
        ai_run = None
        if output is not None:
            source, body = BODY_LAUNCH, output.get("body") or ""
        elif not ai_run_id or record.get("result") == "running":
            source, body = BODY_NONE, ""
        elif record.get("launch_no") is not None and int(record["launch_no"]) == last_launch.get(ai_run_id):
            if ai_run_id not in ai_runs:
                ai_runs[ai_run_id] = engine.ai_run_row(ai_run_id)
            ai_run = ai_runs[ai_run_id]
            source, body = (BODY_RUN, (ai_run or {}).get("last_message") or "") if ai_run else (BODY_NOT_PRESERVED, "")
        else:
            source, body = BODY_NOT_PRESERVED, ""
        error = _error_text(record, source, output, ai_run)
        at = record.get("started_at") or record.get("created_at")
        messages.append({"kind": "invocation", "inv_id": record["inv_id"], "ai_run_id": ai_run_id,
                         "launch_no": record.get("launch_no"), "role": record.get("inv_role"),
                         "purpose": record.get("purpose"), "node_key": key, **titles.of(key),
                         "stage": record.get("target_stage"), "round_no": record.get("round_no"),
                         "requested": requested, "confirmed": confirmed, "at": at, "ended_at": record.get("ended_at"),
                         "result": record.get("result"), "result_detail": record.get("result_detail"),
                         "body": body, "body_source": source, "attribution": owner[record["inv_id"]][1],
                         "error_text": error, "error": error is not None})
        if _invocation_failure(record) and succeeded_after.get((key, role), -1) < i:
            fail(key, record["result"], record.get("result_detail"), at, role, message=False)

    # The run's idle reason as the command left it. The latest command still holding the run reads it now (a read may
    # settle it with no event) unless something else happened after it (a Hold that made it ``hold``) — a completion
    # stays; one a later command took over, the reason the next command's start told of the idle run, else its events.
    idle_reason = None
    if win.ended_by == "idle":
        event_reason = _idle_reason_at(window)
        idle_reason = event_reason
        if latest and not win.cut and run.get("status") == engine.IDLE and run.get("idle_reason"):
            if win.last == len(events) - 1 or run["idle_reason"] == COMPLETED_REASON:
                idle_reason = run["idle_reason"]
        elif win.cut and win.cut_index is not None:
            boundary = events[win.cut_index]
            told = _payload_of(boundary).get("idle_reason")
            if boundary.get("from_status") == engine.IDLE and told:
                idle_reason = told

    # The node as the latest command left it: the run's own error family (a writer that ended missing its result, …).
    if latest and end is not None and not win.cut:
        from flowgate.graph_run import settle
        failed_keys = {f["node_key"] for f in failures}
        for key in touched:
            row = rows.get(key)
            if row and key not in failed_keys and row.get("status") == "open" == status_at.get(key):
                try:
                    if settle._failed(ctx, row):
                        fail(key, "node_failed", row.get("dispatch_error_code"), events[last].get("created_at"))
                except Exception:  # noqa: BLE001 — not told: no failure added
                    pass

    failed_keys = {f["node_key"] for f in failures if f["node_key"]}
    nodes = []
    for key in touched:
        status = status_at.get(key) or (rows.get(key) or {}).get("status")
        if status == "done":
            outcome = "done"
        elif key in failed_keys or status == "rejected":
            outcome = "failed"
        elif end is None:
            outcome = "running"
        elif status in ("open", "review"):
            outcome = "waiting"
        else:
            outcome = status or "pending"
        node_failures = [f for f in failures if f["node_key"] == key]
        reason = detail = None
        if outcome == "failed" and node_failures:
            reason, detail = node_failures[0]["code"], node_failures[0]["detail"]
        elif outcome == "failed":
            reason = "review_rejected"
        elif outcome == "waiting":
            reason = stage_waits.get(key) or ("review" if status == "review" else None)
        nodes.append({"node_key": key, **titles.of(key), "status": status, "outcome": outcome,
                      "stage": (rows.get(key) or {}).get("stage"), "done_in_command": key in done_in_command,
                      "reason": reason, "detail": detail})

    counted = [n for n in nodes if n["outcome"] == "failed"]
    if end is None:
        state = STATE_RUNNING
    elif win.ended_by == "superseded":
        state = STATE_SUPERSEDED
    elif counted or idle_reason in FAILED_IDLE_REASONS:
        state = STATE_FAILED
    elif idle_reason == COMPLETED_REASON:
        state = STATE_COMPLETED
    elif idle_reason == STOPPED_REASON:
        state = STATE_STOPPED
    else:
        state = STATE_WAITING
    first_failure = next((f for f in failures if f["node_key"] in {n["node_key"] for n in counted}), None) \
        or (failures[0] if failures else None)
    if state == STATE_FAILED and first_failure is None:
        first_failure = {"node_key": None, "title": None, "doc_type": None, "code": "run_" + str(idle_reason),
                         "detail": None, "at": events[last].get("created_at"), "role": None}
    continues = (state == STATE_WAITING and latest and not win.cut and run.get("status") == engine.IDLE
                 and run.get("idle_reason") != STOPPED_REASON)
    # flowgate.v02.0124 T#2 (D1, D2, D6): the cards waiting on a person's intervention, only for the latest command that
    # ended waiting for a person and was not cut — the cards it touched first, then the others in node order.
    blocking: list[dict] = []
    if latest and end is not None and not win.cut and idle_reason in PERSON_WAIT_REASONS:
        for key in touched + [k for k in rows if k not in touched]:
            row = rows.get(key)
            found = _intervention_of(run["group_id"], row, (ctx.node_rows.get(key) or {}).get("node_type")) if row else None
            if found:
                blocking.append({"node_key": key, **titles.of(key), "kind": found.get("kind"),
                                 "reason": found.get("reason"), "release": _release_of(run_id, key, found)})
    messages.sort(key=lambda m: (m.get("at") or ""))
    return {"command_result": {
        "run_id": run_id, "flow_id": run["group_id"], "command_seq": int(head["event_seq"]),
        "end_seq": int(events[last]["event_seq"]) if end is not None else None, "latest": latest,
        "scope": head_payload.get("mode") or run.get("run_mode") or engine.MODE_FULL,
        "cause": head.get("cause"), "started_at": head.get("created_at"),
        "ended_at": events[last].get("created_at") if end is not None else None, "ended": end is not None,
        "continues": continues,
        "state": state, "run_status": run.get("status"), "idle_reason": idle_reason,
        "next_state": idle_reason if end is not None else run.get("status"),
        "counts": {"done": len([n for n in nodes if n["outcome"] == "done"]), "failed": len(counted),
                   "waiting": len([n for n in nodes if n["outcome"] == "waiting"])},
        "nodes": nodes, "failure": first_failure if state == STATE_FAILED else None,
        "failures": failures, "blocking": blocking, "roles": roles, "message_count": len(messages), "messages": messages,
    }}
