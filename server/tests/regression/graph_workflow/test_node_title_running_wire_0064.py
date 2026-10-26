"""0064 T#4 (0062 D0005 set 2 §3.2 잠금 표, L0006 2.7 step 4) — the running lock a card's title edit follows on screen.

Every node run of the run read (and so of the Canvas read, which carries the run's node runs) tells ``node_running``:
the card settings' running lock (``legacy_adapter.node_running``), the very judgement a title save is refused by
(409 ``node_title_locked``). It is not the approval state: a review card whose AI review works is running (and not
awaiting approval), while a review card that only waits for its result integration is not awaiting approval either but
is not running — its title saves. On the real SQLite DB and fixtures of ``test_fg_card_settings``.
"""
from __future__ import annotations

import os

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

import config  # noqa: E402,F401 — its import-time env export happens at collection, not in a test

import _flowgate_test_imports  # noqa: F401
from test_fg_card_settings import (  # noqa: E402,F401 — fixtures used by name
    FA, _doc, _h, _run_one_card, _sql, _start, db_path, decided, env,
)

HUMAN = {"kind": "human", "user_id": "u_lee", "provider_id": None}


def put_title(env, key, title, expect):
    res = env["client"].put(f"/api/v1/flows/{FA}/graph/nodes/{key}/title", json={"title": title}, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def run_node(env, key):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return next(n for n in res.json()["run"]["node_runs"] if n["node_key"] == key)


def canvas_run(env, key):
    res = env["client"].get(f"/api/v1/flows/{FA}/canvas", headers=_h())
    assert res.status_code == 200, res.text
    return next(n for n in res.json()["nodes"] if n["node_key"] == key)["node_run"]


def shown(env, key):
    """(status, awaiting_approval, node_running) of the run read, the Canvas node run the same."""
    read, drawn = run_node(env, key), canvas_run(env, key)
    facts = (read["status"], read["awaiting_approval"], read["node_running"])
    assert (drawn["status"], drawn["awaiting_approval"], drawn["node_running"]) == facts
    return facts


def in_review(env, key, number):
    from modules.flow_gate.graph_workflow.api import v01_contact

    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["token_id"]
    result = _doc(FA, number, "T")
    v01_contact.after_inbox_registered(FA, result, "T", token)
    return result


def test_an_open_card_is_running_and_its_title_is_refused(env, decided):
    from flowgate.card_settings import legacy_adapter

    key = _run_one_card(env, HUMAN)
    _start(env)
    assert shown(env, key) == ("open", False, True)
    assert legacy_adapter.node_running(FA, key) is True
    assert put_title(env, key, "실행 중", 409)["error"]["code"] == "node_title_locked"


def test_a_review_card_is_running_only_while_its_ai_review_works(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key = _run_one_card(env, HUMAN)
    _start(env)
    result = in_review(env, key, 92)
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: doc_id == result)
    assert shown(env, key) == ("review", False, True)                       # the AI review works: running
    assert put_title(env, key, "검수 중", 409)["error"]["code"] == "node_title_locked"
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: False)
    assert shown(env, key) == ("review", True, False)                       # awaiting a person: not running
    assert put_title(env, key, "승인 대기", 200)["title_state"]["title"] == "승인 대기"


def test_a_review_card_waiting_only_for_its_result_integration_is_not_running(env, decided, monkeypatch):
    from modules.flow_gate.services import graph_execution_hooks

    from flowgate.card_settings import legacy_adapter

    key = _run_one_card(env, HUMAN)
    _start(env)
    in_review(env, key, 93)
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: False)
    wired = graph_execution_hooks._handler
    monkeypatch.setattr(graph_execution_hooks, "_handler",
                        lambda action, *a, **k: True if action == "integration_waiting" else wired(action, *a, **k))
    # Not awaiting approval (integration_waiting) — and still not running: the server takes its title.
    assert shown(env, key) == ("review", False, False)
    assert legacy_adapter.node_running(FA, key) is False
    assert put_title(env, key, "병합 대기", 200)["title_state"]["title"] == "병합 대기"


def test_a_done_card_and_a_pending_card_are_not_running(env, decided):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    key = _run_one_card(env, HUMAN)
    _start(env)
    result = in_review(env, key, 94)
    db_documents.update(result, {"doc_review_status": "approved"})
    v01_contact.after_review_decided(FA, result, "approve")
    assert shown(env, key)[0::2] == ("done", False)
    assert all(n["node_running"] is False for n in
               env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h()).json()["run"]["node_runs"])
    assert put_title(env, key, "끝난 카드", 200)["title_state"]["title"] == "끝난 카드"


def test_without_the_card_settings_port_no_node_run_is_running(env, decided):
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports

    key = _run_one_card(env, HUMAN)
    _start(env)
    saved = ports._handler(ports.NODE_RUNNING)
    ports.unregister(ports.NODE_RUNNING)
    try:
        assert run_node(env, key)["node_running"] is False
    finally:
        if saved is not None:
            ports.register(ports.NODE_RUNNING, saved)
