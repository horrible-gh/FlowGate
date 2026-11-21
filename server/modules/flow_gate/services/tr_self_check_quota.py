"""resolve a work stage's limits and render its single-line allowance.

The durable invocation origin, not a provider hop/token, identifies a Graph stage.
Rework rounds have distinct origins. Queue rows retain that key and their charged starts,
so reconnects and server restarts cannot replenish the allowance. TS uses its own runner.
"""
from __future__ import annotations

import hashlib
import json

from flowgate.card_settings.verify_limits import default_limits, resolve_verify_limits
from modules.flow_gate.db import tr_self_check_runs as runs


def resolve(token: dict | None, target: dict | None = None) -> dict:
    token, target = token or {}, target or {}
    store = runs.get_store()
    mapping = None
    if token.get("token_id") and store.table_exists("graph_run_token_map"):
        mapping = store._fetch_one(
            "SELECT m.*, r.group_id AS flow_id FROM graph_run_token_map m JOIN graph_runs r ON r.run_id = m.run_id"
            " WHERE m.token_id = ?", [token["token_id"]])
    limits = (resolve_verify_limits(mapping["flow_id"], mapping["node_key"])
              if mapping else default_limits())
    origin = None
    if token.get("ai_run_id") and store.table_exists("ai_role_invocations"):
        origin = store._fetch_one(
            "SELECT target_attempt_id, target_stage, target_seq, inv_role, round_no"
            " FROM ai_role_invocations WHERE ai_run_id = ? AND target_attempt_id IS NOT NULL"
            " ORDER BY record_seq DESC LIMIT 1", [token["ai_run_id"]])
    if origin:
        identity = ["graph", origin["target_attempt_id"], origin["target_stage"],
                    origin["target_seq"], origin["inv_role"], origin["round_no"]]
    elif mapping:
        # Pre-origin executions still keep the same budget across token/provider renewals.
        identity = ["graph", mapping.get("attempt_id") or mapping["run_id"], mapping["node_key"],
                    mapping.get("stage"), token.get("ai_run_id") or token.get("action_scope")]
    else:
        identity = ["legacy", target.get("project_id") or token.get("project"),
                    target.get("group_id") or token.get("group_id"),
                    token.get("ai_run_id") or target.get("tr_doc_id") or token.get("doc_ref")
                    or target.get("owner_token_id"), token.get("action_scope")]
    key = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode("ascii")).hexdigest()
    return {"stage_key": key, "max_runs": limits.max_runs, "run_timeout_sec": limits.run_timeout_sec}


def notice(limits: dict, remaining: int, locale: str = "ko") -> str:
    minutes = limits["run_timeout_sec"] // 60
    line = {
        "ko": f"Self-check: 1회당 {minutes}분 제한, {remaining}회 남음",
        "en": f"Self-check: {minutes} min per run, {remaining} runs remaining",
        "ja": f"Self-check: 1回あたり{minutes}分制限、残り{remaining}回",
    }.get(locale)
    if line is None:
        return notice(limits, remaining, "ko")
    return ("⚠ " if remaining <= 2 else "") + line


def allowance(limits: dict, locale: str = "ko") -> dict:
    remaining = max(0, limits["max_runs"] - runs.verify_used(limits["stage_key"]))
    return {"max_runs": limits["max_runs"], "run_timeout_sec": limits["run_timeout_sec"],
            "remaining": remaining, "notice": notice(limits, remaining, locale)}


def token_notice(token: dict, locale: str = "ko") -> str:
    return allowance(resolve(token), locale)["notice"]
