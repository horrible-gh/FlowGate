"""L-3 launch journal. Evidence failures never change admission or execution."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import uuid

from flowgate.ai_roles import model_evidence as models, record_store as store

log = logging.getLogger(__name__)
ROLES = {"instruction_write": "instruction", "instruction_write_retry": "instruction",
         "result_write": "execution", "result_write_retry": "execution",
         "instruction_review_round": "instruction_review", "result_review_round": "review",
         "instruction_rework": "rework", "result_rework": "rework", "qa_response": "qa_response"}
TARGET = {"group_id": "flow_id", "graph_run_id": "graph_run_id", "node_key": "node_key",
          "attempt_id": "attempt_id", "stage": "stage", "seq": "seq", "doc_id": "doc_id",
          "doc_revision_no": "doc_revision_no"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def guarded(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.warning("role evidence %s unavailable (%s)", fn.__name__, type(exc).__name__)
            return None
    return call


def admission(fn):
    """Record an explicit origin's HTTP refusal exactly once across nested callers."""
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not getattr(exc, "_role_refusal_recorded", False):
                refused(kwargs.get("invocation_origin"), exc, kwargs.get("graph_exec_id"))
                exc._role_refusal_recorded = True
            raise
    return call


def fingerprint(provider):
    exec_type, kind = provider.get("exec_type"), provider.get("kind")
    if exec_type == "api":
        settings = [exec_type, kind, provider.get("api_base_url"), provider.get("api_model")]
    else:
        from modules.flow_gate.settings.ai_settings_service import normalize_cli_command
        settings = [exec_type, kind, normalize_cli_command(kind, provider.get("cli_command") or "")]
    try:
        return store.provider_fingerprint(json.dumps(settings, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        return None


def _confirmed(record, conf):
    record.update(confirmed_model_state=conf["state"], confirmed_model_values_json=
                  json.dumps(conf["values"]) if conf["state"] == "confirmed" else None,
                  confirmed_model_truncated=int(conf["truncated"]), confirmed_coverage=conf["coverage"],
                  bad_lines=conf["bad_lines"], unverifiable_reason=conf["reason"])
    for key in ("total", "reported", "absent", "unreadable", "failed"):
        record["calls_" + key] = (conf.get("calls") or {}).get(key)


def _request(record, req):
    for key in ("state", "value", "reason"):
        record["requested_model_" + key] = req[key]


def _latest(record, memory, purposes=None, unit_hash=None, read_db=True):
    unit_hash = unit_hash or record["unit_key_hash"]
    choices = [r for r in memory if r["unit_key_hash"] == unit_hash and r["create_seq"] < record["create_seq"]
               and (purposes is None or r.get("purpose") in purposes)]
    try:
        found = store.latest(unit_hash, record["create_seq"], purposes) if read_db else None
    except Exception:
        found = None
    if found:
        choices.append(found)
    return max(choices, key=lambda r: (r["create_seq"], r["inv_id"])) if choices else None


def _links(record, memory, origin, read_db=True):
    def latest(purposes=None, unit_hash=None):
        return _latest(record, memory, purposes, unit_hash, read_db)
    prev_run = [r for r in memory if r.get("ai_run_id") == record.get("ai_run_id") and r.get("ai_run_id")]
    prev = prev_run[-1] if prev_run else None
    purpose = record.get("purpose") or ""
    link, dep = "root", None
    if prev and prev["hop_no"] == record["hop_no"]:
        link = ("startup_fallback" if prev["result"] == "start_failed" and
                prev.get("actual_ai_id") != record.get("actual_ai_id") else "same_provider_restart")
        dep = prev
    elif purpose.endswith("write_retry"):
        link, dep = "stage_retry", latest()
    elif purpose.endswith("review_round"):
        link, dep = "review_of", latest(["result_rework", "instruction_rework"])
        if dep is None:
            author = origin.get("author")
            if author is None and record["origin_kind"] == "card_assigned":
                from flowgate.ai_roles import assignment_store
                a = assignment_store.read(record["target_attempt_id"], record["target_stage"], record["target_seq"])
                author = (a or {}).get("author")
            elif author is None and record["origin_kind"] == "card_legacy_attempt" and record.get("target_doc_id"):
                from flowgate.card_settings import legacy_adapter
                doc = legacy_adapter.document_row(record["target_doc_id"]) or {}
                if doc.get("revision_no", 0) == record.get("target_doc_revision_no"):
                    author = legacy_adapter.written_by(record["target_doc_id"]).get("author")
            if author == "ai":
                writer = dict(record, target_stage="instruction" if purpose.startswith("instruction") else "result",
                              target_seq=0)
                _, parts = store.unit_key(writer)
                dep = latest(["instruction_write", "instruction_write_retry", "result_write",
                              "result_write_retry"], store.unit_key_hash(parts))
    elif purpose.endswith("rework"):
        link, dep = "rework_of", latest(["result_review_round", "instruction_review_round"])
    elif purpose == "qa_response":
        link = "answers_question_of"
        dep = {"inv_id": record.get("qa_asker_inv_id")}
    record.update(link_kind=link, prev_inv_id=(dep or {}).get("inv_id"))


def _new(run, provider=None, refusal=None):
    memory = run.setdefault("invocations", [])
    origin = deepcopy(run.get("invocation_origin") or {})
    kind = origin.get("origin_kind", origin.get("kind", "non_card"))
    target, qa = origin.get("target") or {}, origin.get("qa") or {}
    stamp = now()
    launch = len(memory) + 1 if refusal is None else None
    inv_id = f"{run['run_id']}#{launch}" if launch else "refused#" + uuid.uuid4().hex
    purpose = origin.get("purpose")
    hop = run.get("hop_kind")
    if purpose and (purpose.endswith("review_round") or purpose.endswith("rework")) and hop in ("review", "rework"):
        base = "instruction" if purpose.startswith("instruction") else "result"
        purpose = base + ("_rework" if hop == "rework" else "_review_round")
    record = dict(inv_id=inv_id, ai_run_id=run.get("run_id") if launch else None,
                  launch_no=launch, hop_no=run.get("_invocation_hop_no", 1) if launch else None,
                  graph_exec_id=run.get("graph_exec_id"), origin_kind=kind, inv_role=ROLES.get(purpose),
                  purpose=purpose, role_source=origin.get("role_source"),
                  requested_ai_id=origin.get("requested_ai_id", origin.get("requested_ai", run.get("requested_provider_id"))),
                  target_unresolved_reason=origin.get("target_unresolved_reason"),
                  legacy_selected_provider_source=run.get("selected_provider_source") if kind == "card_legacy_attempt" else None,
                  round_no=origin.get("round_no"), actual_ai_id=(provider or {}).get("id"),
                  actual_ai_name=(provider or {}).get("name"), provider_exec_type=(provider or {}).get("exec_type"),
                  provider_kind=(provider or {}).get("kind"), provider_shell_kind=None, provider_fingerprint=None,
                  result="running" if launch else "admission_refused", result_detail=refusal,
                  started_at=stamp if launch else None, ended_at=None if launch else stamp,
                  created_at=stamp, updated_at=stamp, confirmed_model_truncated=0)
    if purpose and (purpose.endswith("review_round") or purpose.endswith("rework")):
        record["requested_ai_id"] = run.get("provider_id") or record["requested_ai_id"]
        loop = run.get("document_review_loop") or {}
        record["round_no"] = loop.get("round_no") or record["round_no"]
    for column, field in TARGET.items():
        record["target_" + column] = target.get(field) if kind != "non_card" else None
    if kind == "card_legacy_attempt":
        record["target_seq"] = None
    if kind == "card_assigned" and purpose and purpose.endswith("rework"):
        try:
            from flowgate.ai_roles import assignment_store
            writer = assignment_store.read(record["target_attempt_id"],
                "instruction" if purpose.startswith("instruction") else "result", 0)
            if writer:
                record["role_source"] = writer["source"]
        except Exception:
            pass
    for key in ("item_id", "question_id", "asker_ai_run_id", "asker_inv_id", "card_class"):
        record["qa_" + key] = qa.get(key)
    if kind == "non_card":
        record.update(inv_role=None, purpose=None, role_source=None)
        if run.get("graph_exec_id"):
            record["target_unresolved_reason"] = "origin_missing"
            log.warning("role invocation origin_missing")
    record["unit_kind"], parts = store.unit_key(record)
    record["unit_key_hash"] = store.unit_key_hash(parts)
    lookup_ok = True
    try:
        floor = store.max_create_seq(record["unit_key_hash"])
    except Exception:
        floor, lookup_ok = None, False
    record["create_seq"] = store.issue_create_seq(None if floor is None else floor + 1,
                                                  max((r["create_seq"] for r in memory), default=0) + 1)
    record["chain_root_id"] = None
    try:
        first = store.first(record["unit_key_hash"])
        choices = [r for r in memory if r["unit_key_hash"] == record["unit_key_hash"]]
        if first:
            choices.append(first)
        if lookup_ok:
            record["chain_root_id"] = min(choices, key=lambda r: (r["create_seq"], r["inv_id"]))["inv_id"] if choices else inv_id
    except Exception:
        lookup_ok = False
    try:
        _links(record, memory, origin, lookup_ok)
    except Exception:
        record.setdefault("link_kind", "root")
        record.setdefault("prev_inv_id", None)
    if (kind == "card_assigned" or (kind == "card_qa" and qa.get("card_class") == "qa_new")) and provider and (
            record["requested_ai_id"] != record["actual_ai_id"]):
        log.warning("role invocation provider mismatch %s", inv_id)
    _request(record, {"state": "not_parsed", "value": None, "reason": "not_launched"})
    if provider:
        if provider.get("exec_type") == "api":
            _request(record, models.requested(provider))
        record["model_capability"] = models.capability(provider, models.requested(provider))
        record["provider_fingerprint"] = fingerprint(provider)
    else:
        _confirmed(record, models.confirmed("cli", "custom", {}))
    return record


@guarded
def flush(run, final=False):
    if run.get("_invocations_closed"):
        return
    saved = []
    try:
        outputs = run.get("_invocation_outputs") or {}
        for record in sorted(run.get("invocations", []), key=lambda r: r["create_seq"]):
            if not store.write_record(record):
                log.warning("role invocation prefix not stored at %s", record["inv_id"])
                break  # S-1: no later insert may overtake this record.
            saved.append(record)
            if record["inv_id"] in outputs and store.write_output(outputs[record["inv_id"]]):
                outputs.pop(record["inv_id"], None)  # T#5: this launch's own output, stored once
            if record.get("chain_root_id") is None:
                try:
                    first = store.first(record["unit_key_hash"])
                    if first:
                        from modules.flow_gate.db.connection import get_store
                        with get_store().transaction():
                            store.fill_root(record["inv_id"], first["inv_id"])
                except Exception:
                    log.warning("role invocation root not filled for %s", record["inv_id"])
    finally:
        if final:
            run["invocations"] = saved  # S-2: dropped suffix cannot become a later Q&A target.
            run["_invocations_closed"] = True  # S-2: never write after final handoff.


@guarded
def begin(run, provider):
    if run.get("_invocations_closed") or not store.available():
        return
    if any(r["result"] == "running" for r in run.get("invocations", [])):
        finish(run, "launch_outcome_missing", None)
    signature = (run.get("hop_kind"), (run.get("invocation_origin") or {}).get("round_no"))
    if "_invocation_hop_signature" in run and signature != run["_invocation_hop_signature"]:
        run["_invocation_hop_no"] = run.get("_invocation_hop_no", 1) + 1
    run["_invocation_hop_signature"] = signature
    record = _new(run, provider)
    run["invocations"].append(record)
    run["_launch_outcome"] = {"model_calls": [], "flags": {k: _flag(run, k) for k in
                                                          ("cancelled", "timed_out", "user_paused")},
                              "seen": _seen(run)}
    if provider.get("exec_type") == "api":
        flush(run)


def _seen(run):
    """flowgate.v02.0113 T#5: the run's output fields as this launch begins — what is still the same object when it
    ends was left by an earlier launch, not written by this one."""
    errors = run.get("register_errors")
    return {"last_message": run.get("last_message"), "stderr_tail": run.get("stderr_tail"),
            "stdout_tail": run.get("stdout_tail"), "errors": len(errors) if isinstance(errors, list) else 0,
            "errors_list": errors if isinstance(errors, list) else None}


def _launch_output(run, record, seen, detail):
    """flowgate.v02.0113 T#5: the output this launch itself left (its last message, stderr / stdout tail, the
    registration errors it added, the classification detail) — none of an earlier launch's."""
    def fresh(key):
        value = run.get(key)
        return value if isinstance(value, str) and value and value is not seen.get(key) else None
    errors = run.get("register_errors")
    added = []
    if isinstance(errors, list):
        added = errors[seen.get("errors", 0):] if errors is seen.get("errors_list") else list(errors)
    return {"inv_id": record["inv_id"], "ai_run_id": record.get("ai_run_id"), "launch_no": record.get("launch_no"),
            "body": fresh("last_message"), "stderr_tail": fresh("stderr_tail"), "stdout_tail": fresh("stdout_tail"),
            "register_errors_json": json.dumps(added, ensure_ascii=False, default=str) if added else None,
            "detail": detail if isinstance(detail, str) and detail else None, "created_at": now()}


def _flag(run, key):
    if key == "cancelled":
        return bool(run.get("cancel_event") and run["cancel_event"].is_set())
    return bool(run.get(key))


@guarded
def cli_prepared(run, provider, shell_kind):
    outcome = run.get("_launch_outcome")
    if outcome is None:
        return
    from modules.flow_gate.settings.ai_settings_service import normalize_cli_command
    req = models.requested(provider, normalize_cli_command(provider.get("kind"), provider.get("cli_command") or ""), shell_kind)
    record = run["invocations"][-1]
    record["provider_shell_kind"] = shell_kind
    _request(record, req)
    record["model_capability"] = models.capability(provider, req)
    flush(run)


@guarded
def cli_outcome(run, *, stdout_full, exit_code, timed_out, process_exited_itself):
    if run.get("_launch_outcome") is not None:
        run["_launch_outcome"].update(stdout_full=stdout_full, exit_code=exit_code, timed_out=timed_out,
                                      process_exited_itself=process_exited_itself, ended_at=now())


class ModelResponse(tuple):
    """Preserve the established 3-tuple protocol, keeping evidence out of conversation messages."""
    def __new__(cls, text, calls, message, body):
        obj = super().__new__(cls, (text, calls, message))
        obj.model_evidence = models.evidence(body)
        return obj


@guarded
def api_response(run, response=None, exc=None):
    o = run.get("_launch_outcome")
    if o is None:
        return
    if exc is None:
        o["model_calls"].append(getattr(response, "model_evidence", ("unreadable", None)))
        return
    o["model_calls"].append(("failed", None))
    from modules.flow_gate.services.ai_invoke.provider_api import _is_timeout_error
    import urllib.error
    kind = ("model_http_error" if isinstance(exc, urllib.error.HTTPError) else
            "model_call_timeout" if _is_timeout_error(exc) else "model_transport_error")
    if run.get("_api_turn", 1) > 1:
        o["model_call_failure"] = {"kind": kind, "call_no": len(o["model_calls"]),
                                   "turn_no": run["_api_turn"], "http_status": getattr(exc, "code", None)}


@guarded
def finish(run, classification, detail=None):
    records = run.get("invocations") or []
    if not records or records[-1]["result"] != "running":
        return
    r = records[-1]
    o = run.pop("_launch_outcome", None)
    if o is None or classification == "launch_outcome_missing":
        result, reason, o = "call_failed", "launch_outcome_missing", o or {}
    else:
        flags = {k: _flag(run, k) and not o["flags"][k] for k in o["flags"]}
        failure = o.get("model_call_failure")
        if classification != "started_ok":
            result, reason = "start_failed", "launch_refused" if detail in (
                "launch_refused", "Graph execution lease refused") else classification
        elif flags["cancelled"]:
            result, reason = "user_stopped", "cancelled"
        elif flags["timed_out"] or o.get("timed_out"):
            result, reason = "call_failed", "timeout"
        elif flags["user_paused"]:
            result, reason = "user_stopped", "user_paused"
        elif failure:
            result, reason = "call_failed", failure["kind"]
        elif r["provider_exec_type"] == "cli" and o.get("exit_code") is None:
            result, reason = "call_failed", "exit_unknown"
        elif r["provider_exec_type"] == "cli" and o["exit_code"] != 0:
            result, reason = "call_failed", "exit_nonzero"
        else:
            result, reason = "succeeded", None
    r.update(result=result, result_detail=reason, exit_code=o.get("exit_code"),
             timeout_kind=run.get("timeout_kind") if reason == "timeout" else None,
             ended_at=o.get("ended_at") or now(), updated_at=now())
    failure = o.get("model_call_failure") or {}
    for key, column in (("kind", "model_call_failure_kind"), ("call_no", "model_call_no"),
                        ("turn_no", "model_turn_no"), ("http_status", "model_http_status")):
        r[column] = failure.get(key)
    _confirmed(r, models.confirmed(r["provider_exec_type"], r["provider_kind"], o))
    run.setdefault("_invocation_outputs", {})[r["inv_id"]] = _launch_output(run, r, o.get("seen") or {}, detail)
    flush(run)


@guarded
def finalize(run):
    if run.get("_invocations_closed"):
        return
    if any(r["result"] == "running" for r in run.get("invocations", [])):
        finish(run, "launch_outcome_missing")
    if run.get("invocations"):
        run["invocations"][-1].update(run_end_reason=run.get("end_reason"), run_stop_code=run.get("stop_code"))
    flush(run, final=True)


@guarded
def refused(origin, exc, graph_exec_id=None):
    if not origin or not store.available():
        return
    detail = getattr(exc, "detail", None)
    code = detail.get("code") if isinstance(detail, dict) else None
    if not isinstance(code, str):
        return
    run = {"invocation_origin": origin, "graph_exec_id": graph_exec_id}
    record = _new(run, refusal=code)
    if store.write_record(record):  # S-4: one attempt, no late save.
        message = detail.get("message") if isinstance(detail.get("message"), str) else None
        store.write_output({"inv_id": record["inv_id"], "ai_run_id": None, "launch_no": None, "body": None,
                            "stderr_tail": None, "stdout_tail": None, "register_errors_json": None,
                            "detail": message or code, "created_at": now()})


@guarded
def orphan(run_id):
    if not store.available():
        return
    outcome = dict(result="call_failed", result_detail="orphaned_by_restart", ended_at=now())
    _confirmed(outcome, {"state": "unverifiable", "values": [], "truncated": False,
                         "coverage": None, "calls": None, "bad_lines": None, "reason": "no_result"})
    from modules.flow_gate.db.connection import get_store
    with get_store().transaction():
        for r in store.by_run(run_id):
            if r["result"] == "running":
                finish_outcome = dict(outcome)
                # Preserve only this launch's evidence already collected before the crash.
                if r.get("confirmed_model_state"):
                    for k in store.FINISH_COLUMNS:
                        if k.startswith(("confirmed_", "calls_")) or k in ("bad_lines", "unverifiable_reason"):
                            finish_outcome[k] = r.get(k)
                store.finish(r["inv_id"], finish_outcome)
