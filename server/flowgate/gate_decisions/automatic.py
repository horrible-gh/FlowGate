"""Automatic confirm uses the normal, versioned decision intake once per gate attempt.

The request event is durable; a lost post-commit callback may be replayed using the same
idempotency key. Refusals are terminal for automatic execution and leave manual recovery open.
"""
from __future__ import annotations

import json
from flowgate.gate_decisions import intake, legacy_adapter as legacy, repository
from flowgate.card_settings import repository as settings
from flowgate.graph_run import legacy_adapter as engine
from modules.flow_gate.db.connection import get_store

REQUESTED = "gate_auto_confirm_requested"
FAILED = "gate_auto_confirm_failed"


def latest(run_id, key, attempt):
    row = get_store()._fetch_one(
        "SELECT cause, reason, payload_json FROM graph_run_events WHERE run_id = ? AND node_key = ? "
        "AND cause IN (?, ?) ORDER BY event_seq DESC LIMIT 1", [run_id, key, REQUESTED, FAILED])
    if not row:
        return None
    payload = json.loads(row["payload_json"] or "{}")
    return {**dict(row), **payload} if payload.get("auto_attempt_id") == attempt else None


def failure(run_id, key):
    attempt = legacy.run_store.active_attempt_id(run_id, key)
    row = latest(run_id, key, attempt) if attempt else None
    return row.get("reason") if row and row["cause"] == FAILED else None


def schedule(ctx):
    if ctx.run.get("status") not in ("running", "idle") or ctx.run.get("idle_reason") == "stopped":
        return
    if not legacy.gate_rule_decided(ctx.run_id):
        return
    keys = settings.keys_with(ctx.flow_id, "auto_approve", True)
    if not keys:
        return
    queued = getattr(ctx, "_auto_confirm_queued", set())
    ctx._auto_confirm_queued = queued
    for node in legacy.run_store.node_runs(ctx.run_id):
        key = node["node_key"]
        if key not in keys or key in queued or node["status"] != "open":
            continue
        if (ctx.node_rows.get(key) or {}).get("node_type") != "gate":
            continue
        if repository.reservation(ctx.run_id, key):
            continue
        attempt = legacy.run_store.active_attempt_id(ctx.run_id, key)
        if not attempt:
            continue
        old = latest(ctx.run_id, key, attempt)
        if old and old["cause"] == FAILED:
            continue
        version = old["auto_settings_version"] if old else settings.settings_version(ctx.flow_id, key)
        actor = ctx.run.get("started_by")
        if not actor:
            continue
        if not old:
            engine.emit_node_state(ctx, key, "open", "open", REQUESTED, None,
                note={"actor_user_id": actor, "payload": {"auto_attempt_id": attempt,
                      "auto_settings_version": version}})
        queued.add(key)
        ctx.jobs.append(lambda key=key, attempt=attempt, version=version, actor=actor:
                        execute(ctx.flow_id, ctx.run_id, key, attempt, version, actor))


def execute(flow_id, run_id, key, attempt, version, actor):
    try:
        group = legacy.load_flow(flow_id)
        legacy.authorize(actor, group, "write")
        intake.decide(group, run_id, key, "confirm", "자동 승인", actor,
                      {"request_id": "auto-confirm:" + attempt, "expected_attempt_id": attempt,
                       "expected_settings_version": version, "automatic": True})
    except Exception as exc:
        code = getattr(exc, "code", None) or "auto_confirm_failed"
        def record(run, jobs):
            if legacy.run_store.active_attempt_id(run_id, key) != attempt:
                return {}
            ctx = legacy.load_context(run, None, actor, jobs)
            engine.emit_node_state(ctx, key, "open", "open", FAILED, None,
                note={"actor_user_id": actor, "reason": code,
                      "payload": {"auto_attempt_id": attempt, "auto_settings_version": version,
                                  "auto_approval_error": code}})
            return {}
        legacy.guarded_run(run_id, record)
