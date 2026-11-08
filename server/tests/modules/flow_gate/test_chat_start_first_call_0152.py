"""flowgate.v02.0152 -- the first AI call of a chat failed with "could not call the AI"; the second worked.

Cause (code-confirmed): ``POST /ai-invoke/start`` answered only after admission had hashed the
whole group worktree -- twice for a chat run (admission's own ``_chat_start_tree`` and the chat
start hook's ``run_changes.start_snapshot``). On a cold file cache that outlasted the browser's 30s
request timeout; the browser then asked ``/ai-invoke/active`` once, before the run was registered,
and reported a failure for a run the server went on to carry out. A later press could start the
same request again once that run had finished.

Fixed and pinned here:

* the start-tree snapshot runs on the worker thread after the handoff gate opens and before any
  provider starts (``worker._prepare_chat_start_tree``) -- once, through the conversation port when
  it is registered, else through the 0670 in-module path; a cancel that arrived first skips it and
  a failure never escapes;
* a chat press names itself (``client_request_id``): a resend gets the run the first request
  admitted (running or finished) instead of a second run, 409 ``start_pending`` while the first is
  still being admitted, and a fresh admission when the first was refused.
"""
from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services import chat_run_hooks  # noqa: E402
from modules.flow_gate.services import chat_start_requests as requests_  # noqa: E402
from modules.flow_gate.services.ai_invoke import worker  # noqa: E402
from modules.flow_gate.services.ai_invoke.runtime import _http_error  # noqa: E402

CHAT = "flowgate.v02.0152.0002-CH"
KEY = "run-6f1d2c3b-0152"


@pytest.fixture(autouse=True)
def clean_registry():
    requests_.clear()
    yield
    requests_.clear()


@pytest.fixture(autouse=True)
def preserve_runtime_environment():
    # The route's lazy config import can publish this value. Restore its exact prior state.
    key = "FLOWGATE_AGENT_PEPPER_ID"
    previous = os.environ.get(key)
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


# -- the press registry --------------------------------------------------------

def test_a_press_is_new_then_pending_then_started():
    first = requests_.claim("u", CHAT, KEY)
    assert first.kind == requests_.NEW
    assert requests_.claim("u", CHAT, KEY).kind == requests_.PENDING
    requests_.complete("u", CHAT, KEY, first.token, "aiv2_20261108_000101")
    again = requests_.claim("u", CHAT, KEY)
    assert (again.kind, again.run_id) == (requests_.STARTED, "aiv2_20261108_000101")


def test_a_refused_press_is_forgotten_and_only_by_its_owner():
    first = requests_.claim("u", CHAT, KEY)
    requests_.release("u", CHAT, KEY, object())            # someone else's token: no effect
    assert requests_.claim("u", CHAT, KEY).kind == requests_.PENDING
    requests_.release("u", CHAT, KEY, first.token)
    assert requests_.claim("u", CHAT, KEY).kind == requests_.NEW


def test_presses_are_scoped_by_requester_and_chat():
    requests_.complete("u", CHAT, KEY, requests_.claim("u", CHAT, KEY).token, "aiv2_20261108_000102")
    assert requests_.claim("other-user", CHAT, KEY).kind == requests_.NEW
    assert requests_.claim("u", "flowgate.v02.0152.0003-CH", KEY).kind == requests_.NEW


def test_entries_expire(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(requests_.time, "monotonic", lambda: now[0])
    first = requests_.claim("u", CHAT, KEY)
    now[0] += requests_.PENDING_TTL_SEC + 1                # an abandoned admission no longer blocks the press
    second = requests_.claim("u", CHAT, KEY)
    assert second.kind == requests_.NEW
    requests_.complete("u", CHAT, KEY, first.token, "stale")   # the old owner lost the slot
    assert requests_.claim("u", CHAT, KEY).kind == requests_.PENDING
    requests_.complete("u", CHAT, KEY, second.token, "aiv2_20261108_000103")
    now[0] += requests_.STARTED_TTL_SEC + 1
    assert requests_.claim("u", CHAT, KEY).kind == requests_.NEW


@pytest.mark.parametrize("value,ok", [(KEY, True), ("run-abc", False), ("x" * 201, False),
                                      ("run 6f1d2c3b", False), ("run:6f1d.2c3b_0152", True), (None, False)])
def test_request_id_shape(value, ok):
    assert requests_.valid(value) is ok


# -- the /ai-invoke/start route ------------------------------------------------

@pytest.fixture()
def route(monkeypatch):
    monkeypatch.setattr(ai_invoke_routes, "verify_bearer",
                        lambda request: {"_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes, "db_projects", SimpleNamespace(get_by_id=lambda project: {"id": project}))
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "get_run_record", lambda run_id: {"status": "running"})
    calls: list[dict] = []
    behaviour = {"start": lambda **kw: {"ok": True, "run_id": f"aiv2_20261108_{len(calls):06d}", "status": "running"}}

    def start_run(**kw):
        calls.append(kw)
        return behaviour["start"](**kw)

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", start_run)
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    def post(**extra):
        body = {"project": "flowgate", "module": "v02", "group": "0152", "doc_ref": CHAT,
                "action_scope": "chat", "mode": "single", "provider_id": "aip_opus", **extra}
        return client.post("/api/v1/ai-invoke/start", json=body, headers={"Authorization": "Bearer tok"})

    return SimpleNamespace(post=post, calls=calls, behaviour=behaviour)


def test_a_resent_press_gets_the_admitted_run_and_starts_nothing(route):
    first = route.post(client_request_id=KEY)
    assert first.status_code == 200, first.text
    run_id = first.json()["run_id"]
    again = route.post(client_request_id=KEY)
    assert again.status_code == 200, again.text
    assert again.json()["run_id"] == run_id and again.json()["replayed"] is True
    assert again.json()["action_scope"] == "chat" and again.json()["doc_ref"] == CHAT
    assert len(route.calls) == 1


def test_a_new_press_and_a_press_without_an_id_each_start_their_own_run(route):
    assert route.post(client_request_id=KEY).status_code == 200
    assert route.post(client_request_id=KEY + "-2").status_code == 200
    assert route.post().status_code == 200
    assert route.post().status_code == 200
    assert len(route.calls) == 4


def test_a_refused_press_is_admitted_afresh_on_resend(route):
    def refuse(**_kw):
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id="aiv2_someone_else")

    route.behaviour["start"] = refuse
    res = route.post(client_request_id=KEY)
    assert res.status_code == 409 and res.json()["code"] == "run_in_progress"
    route.behaviour["start"] = lambda **kw: {"ok": True, "run_id": "aiv2_20261108_000777", "status": "running"}
    res = route.post(client_request_id=KEY)
    assert res.status_code == 200 and res.json()["run_id"] == "aiv2_20261108_000777"
    assert "replayed" not in res.json()
    assert len(route.calls) == 2


def test_an_unexpected_failure_also_forgets_the_press(route):
    def crash(**_kw):
        raise RuntimeError("synthetic admission defect")

    route.behaviour["start"] = crash
    assert route.post(client_request_id=KEY).status_code == 500
    assert requests_.claim("u_kim", CHAT, KEY).kind == requests_.NEW


def test_a_resend_while_the_first_request_is_admitting_is_told_to_wait(route):
    entered, release = threading.Event(), threading.Event()

    def slow(**_kw):
        entered.set()
        assert release.wait(10)
        return {"ok": True, "run_id": "aiv2_20261108_000555", "status": "running"}

    route.behaviour["start"] = slow
    answers: list = []
    first = threading.Thread(target=lambda: answers.append(route.post(client_request_id=KEY)))
    first.start()
    try:
        assert entered.wait(10)
        pending = route.post(client_request_id=KEY)
        assert pending.status_code == 409 and pending.json()["code"] == "start_pending"
    finally:
        release.set()
        first.join(10)
    assert answers and answers[0].status_code == 200
    replay = route.post(client_request_id=KEY)
    assert replay.status_code == 200 and replay.json()["run_id"] == "aiv2_20261108_000555"
    assert len(route.calls) == 1


@pytest.mark.parametrize("extra", [
    {"client_request_id": "short"},
    {"client_request_id": KEY, "action_scope": "edit", "doc_ref": "flowgate.v02.0152.0001-R"},
])
def test_a_malformed_or_non_chat_request_id_is_refused(route, extra):
    res = route.post(**extra)
    assert res.status_code == 422
    assert "client_request_id" in res.text
    assert route.calls == []


# -- the start-tree snapshot on the worker -------------------------------------

def _run(**over):
    run = {"run_id": "aiv2_20261108_000201", "action_scope": "chat", "cancel_event": threading.Event(),
           "source_root": "memory-root", "source_start_tree": None}
    run.update(over)
    return run


@pytest.fixture()
def hooks(monkeypatch):
    monkeypatch.setattr(chat_run_hooks, "_handlers", {})
    return chat_run_hooks


def test_the_registered_port_takes_the_snapshot_once_and_the_legacy_path_does_not(hooks, monkeypatch):
    seen = []

    def prepare(run):
        seen.append(run["run_id"])
        run["source_start_tree"] = "t1"

    hooks.register(hooks.ON_CHAT_RUN_PREPARE, prepare)
    monkeypatch.setattr(worker.admission, "_chat_start_tree", lambda *a: pytest.fail("legacy snapshot must not run"))
    run = _run()
    worker._prepare_chat_start_tree(run)
    assert seen == [run["run_id"]] and run["source_start_tree"] == "t1"
    assert isinstance(run["chat_start_prepare_ms"], int)


def test_without_the_port_the_0670_path_takes_it(hooks, monkeypatch):
    calls = []

    def legacy(scope, root):
        calls.append((scope, root))
        return "t2"

    monkeypatch.setattr(worker.admission, "_chat_start_tree", legacy)
    run = _run()
    worker._prepare_chat_start_tree(run)
    assert calls == [("chat", "memory-root")] and run["source_start_tree"] == "t2"


def test_a_cancel_first_or_a_non_chat_run_takes_no_snapshot(hooks, monkeypatch):
    hooks.register(hooks.ON_CHAT_RUN_PREPARE, lambda run: pytest.fail("no snapshot expected"))
    monkeypatch.setattr(worker.admission, "_chat_start_tree", lambda *a: pytest.fail("no snapshot expected"))
    cancelled = _run()
    cancelled["cancel_event"].set()
    worker._prepare_chat_start_tree(cancelled)
    worker._prepare_chat_start_tree(_run(action_scope="edit"))
    assert cancelled["source_start_tree"] is None


def test_a_failing_snapshot_never_fails_the_run(hooks, monkeypatch):
    def boom(*_a):
        raise RuntimeError("synthetic git failure")

    monkeypatch.setattr(worker.admission, "_chat_start_tree", boom)
    run = _run()
    worker._prepare_chat_start_tree(run)                    # no raise
    assert run["source_start_tree"] is None
    hooks.register(hooks.ON_CHAT_RUN_PREPARE, boom)         # the port logs and swallows its own failure
    worker._prepare_chat_start_tree(run)
