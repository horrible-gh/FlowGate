"""Durable first-flow naming ledger; every access is under its Flow guard.

Use the existing transactional key/value store, with one namespaced key per Flow.
This survives workers/restarts without a schema rollout. No chat is created.
"""
from __future__ import annotations
import json
from modules.flow_gate.db import system_settings

PREFIX = "graph_first_names:"


def load(flow_id: str) -> dict:
    value = system_settings.get_value(PREFIX + flow_id)
    return json.loads(value) if value else {}


def save(flow_id: str, record: dict) -> None:
    system_settings.set_value(PREFIX + flow_id, json.dumps(record, ensure_ascii=False), "json")


def mark_flow_manual(flow_id: str) -> None:
    record = load(flow_id)
    record["flow_manual"] = True
    save(flow_id, record)
