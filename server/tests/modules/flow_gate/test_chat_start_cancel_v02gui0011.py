"""flowgate.v02gui.0011 B -- stop a chat AI call right after the send, before its run id is known.

Measured on stage (seq 6 of the 0011 chat): /ai-invoke/start answered after 17-50s, and during that
whole wait the browser had no run id while the server cancels by run id only, so nothing could be
stopped. The press already names itself (``client_request_id``, flowgate.v02.0152); a stop now
uses that name:

* before admission sees the press -- a ``cancelled`` entry; a /start that arrives later is refused
  with 409 ``start_cancelled`` and starts nothing;
* during admission -- the run is cancelled the moment it is admitted, and the /start answer says
  so (``start_cancelled``); a refused admission leaves the press stopped (a resend starts nothing);
* after admission -- the admitted run is cancelled.

The registry and the route are exercised with a fake ``start_run`` / ``cancel_run``: no DB, no
server, no worker.
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
from modules.flow_gate.services import chat_start_requests as requests_  # noqa: E402
from modules.flow_gate.services.ai_invoke.runtime import _http_error  # noqa: E402

CHAT = "flowgate.v02gui.0011.0004-CH"
KEY = "run-0011-stop-0001"


@pytest.fixture(autouse=True)
def clean_registry():
    requests_.clear()
    yield
    requests_.clear()


@pytest.fixture(autouse=True)
def preserve_runtime_environment():
    key = "FLOWGATE_AGENT_PEPPER_ID"
    previous = os.environ.get(key)
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


# -- the press registry --------------------------------------------------------

def test_a_stop_before_the_start_leaves_the_press_cancelled():
    assert requests_.cancel("u", CHAT, KEY).state == requests_.CANCELLED
    assert requests_.claim("u", CHAT, KEY).kind == requests_.CANCELLED
    assert requests_.cancel("u", CHAT, KEY).state == requests_.CANCELLED     # idempotent


def test_a_stop_during_admission_is_handed_to_the_admitting_request():
    first = requests_.claim("u", CHAT, KEY)
    assert requests_.cancel("u", CHAT, KEY).state == requests_.PENDING
    assert requests_.complete("u", CHAT, KEY, first.token, "aiv2_20261114_000001") is True
    again = requests_.cancel("u", CHAT, KEY)
    assert (again.state, again.run_id) == (requests_.STARTED, "aiv2_20261114_000001")


def test_a_press_that_was_not_stopped_completes_as_before():
    first = requests_.claim("u", CHAT, KEY)
    assert requests_.complete("u", CHAT, KEY, first.token, "aiv2_20261114_000002") is False
    claim = requests_.claim("u", CHAT, KEY)
    assert (claim.kind, claim.run_id) == (requests_.STARTED, "aiv2_20261114_000002")


def test_a_stop_after_admission_names_the_run():
    first = requests_.claim("u", CHAT, KEY)
    requests_.complete("u", CHAT, KEY, first.token, "aiv2_20261114_000003")
    outcome = requests_.cancel("u", CHAT, KEY)
    assert (outcome.state, outcome.run_id) == (requests_.STARTED, "aiv2_20261114_000003")


def test_a_refused_admission_of_a_stopped_press_stays_stopped():
    first = requests_.claim("u", CHAT, KEY)
    requests_.cancel("u", CHAT, KEY)
    requests_.release("u", CHAT, KEY, first.token)
    assert requests_.claim("u", CHAT, KEY).kind == requests_.CANCELLED


def test_a_stop_is_scoped_by_requester_and_chat():
    requests_.cancel("u", CHAT, KEY)
    assert requests_.claim("other-user", CHAT, KEY).kind == requests_.NEW
    assert requests_.claim("u", "flowgate.v02gui.0011.0005-CH", KEY).kind == requests_.NEW


def test_a_cancelled_entry_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(requests_.time, "monotonic", lambda: now[0])
    requests_.cancel("u", CHAT, KEY)
    now[0] += requests_.STARTED_TTL_SEC + 1
    assert requests_.claim("u", CHAT, KEY).kind == requests_.NEW


# -- the routes ----------------------------------------------------------------

@pytest.fixture()
def route(monkeypatch):
    monkeypatch.setattr(ai_invoke_routes, "verify_bearer",
                        lambda request: {"_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes, "db_projects", SimpleNamespace(get_by_id=lambda project: {"id": project}))
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "get_run_record", lambda run_id: {"status": "running"})
    calls: list[dict] = []
    cancelled: list[str] = []
    behaviour = {
        "start": lambda **kw: {"ok": True, "run_id": f"aiv2_20261114_{len(calls):06d}", "status": "running"},
        "cancel": lambda run_id: {"ok": True, "run_id": run_id, "status": "cancelling"},
    }

    def start_run(**kw):
        calls.append(kw)
        return behaviour["start"](**kw)

    def cancel_run(run_id, **_kw):
        cancelled.append(run_id)
        return behaviour["cancel"](run_id)

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", start_run)
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "cancel_run", cancel_run)
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": "Bearer tok"}

    def start(**extra):
        body = {"project": "flowgate", "module": "v02gui", "group": "0011", "doc_ref": CHAT,
                "action_scope": "chat", "mode": "single", "provider_id": "aip_opus",
                "client_request_id": KEY, **extra}
        return client.post("/api/v1/ai-invoke/start", json=body, headers=headers)

    def stop(**extra):
        body = {"doc_ref": CHAT, "client_request_id": KEY, **extra}
        return client.post("/api/v1/ai-invoke/start/cancel", json=body, headers=headers)

    return SimpleNamespace(start=start, stop=stop, calls=calls, cancelled=cancelled, behaviour=behaviour)


def test_stop_before_start_then_the_start_starts_nothing(route):
    res = route.stop()
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "run_id": None, "state": "cancelled"}
    late = route.start()
    assert late.status_code == 409 and late.json()["code"] == "start_cancelled"
    assert route.calls == [] and route.cancelled == []


def test_stop_during_admission_cancels_the_run_before_the_start_answers(route):
    entered, release = threading.Event(), threading.Event()

    def slow(**_kw):
        entered.set()
        assert release.wait(10)
        return {"ok": True, "run_id": "aiv2_20261114_000555", "status": "running"}

    route.behaviour["start"] = slow
    answers: list = []
    first = threading.Thread(target=lambda: answers.append(route.start()))
    first.start()
    try:
        assert entered.wait(10)
        res = route.stop()
        assert res.status_code == 200 and res.json()["state"] == "cancel_pending"
        assert route.cancelled == []
    finally:
        release.set()
        first.join(10)
    assert answers and answers[0].status_code == 200, answers[0].text
    body = answers[0].json()
    assert body["run_id"] == "aiv2_20261114_000555"
    assert body["start_cancelled"] is True and body["status"] == "cancelling"
    assert route.cancelled == ["aiv2_20261114_000555"]
    # A resend of the same press gets that run back; nothing new starts.
    replay = route.start()
    assert replay.status_code == 200 and replay.json()["run_id"] == "aiv2_20261114_000555"
    assert len(route.calls) == 1


def test_stop_after_admission_cancels_the_admitted_run(route):
    run_id = route.start().json()["run_id"]
    res = route.stop()
    assert res.status_code == 200
    assert res.json() == {"ok": True, "run_id": run_id, "state": "cancelling"}
    assert route.cancelled == [run_id]


def test_stop_of_a_run_that_already_ended(route):
    run_id = route.start().json()["run_id"]
    route.behaviour["cancel"] = lambda rid: {"ok": True, "run_id": rid, "status": "finished"}
    assert route.stop().json()["state"] == "finished"

    def gone(_rid):
        raise _http_error(404, "run_not_found", "Unknown or expired run id.")

    route.behaviour["cancel"] = gone
    res = route.stop()
    assert res.status_code == 200 and res.json() == {"ok": True, "run_id": run_id, "state": "finished"}


def test_a_refused_admission_of_a_stopped_press_is_not_restarted_by_a_resend(route):
    entered, release = threading.Event(), threading.Event()

    def refuse(**_kw):
        entered.set()
        assert release.wait(10)
        raise _http_error(409, "run_in_progress", "An AI run is already in progress for this group.",
                          run_id="aiv2_someone_else")

    route.behaviour["start"] = refuse
    answers: list = []
    first = threading.Thread(target=lambda: answers.append(route.start()))
    first.start()
    try:
        assert entered.wait(10)
        assert route.stop().json()["state"] == "cancel_pending"
    finally:
        release.set()
        first.join(10)
    assert answers[0].status_code == 409
    again = route.start()
    assert again.status_code == 409 and again.json()["code"] == "start_cancelled"
    assert len(route.calls) == 1


def test_a_press_without_a_stop_is_unchanged(route):
    res = route.start()
    assert res.status_code == 200 and "start_cancelled" not in res.json()
    assert route.cancelled == []


@pytest.mark.parametrize("extra", [{"client_request_id": "short"}, {"doc_ref": ""}])
def test_a_malformed_stop_is_refused(route, extra):
    res = route.stop(**extra)
    assert res.status_code == 422
    assert route.cancelled == []


def test_the_stop_route_is_not_taken_by_the_run_id_cancel_route(route):
    # ``/{run_id}/cancel`` would read "start" as a run id; the stop must reach its own route.
    res = route.stop()
    assert res.status_code == 200 and res.json()["state"] == "cancelled"
    assert route.cancelled == []
