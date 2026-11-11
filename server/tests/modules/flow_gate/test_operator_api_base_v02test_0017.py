"""flowgate.v02test.0017 CH0002: a Graph AI start hands a remote Agent a reachable operator base, not loopback.

The run of ``flowgate.v02test.0019.0004-N`` ended ``remote_api_base_unreachable: api_base_loopback`` before any Job:
``ai_node_starter.api_base_url()`` was always ``http://127.0.0.1:<port>``, which ``provider_remote.api_base_failure``
refuses, while chat on the same Agent passed the person's request origin. ``operator_base.resolve`` now answers the
configured ``FLOWGATE_OPERATOR_API_BASE``, else the run user's own latest non-loopback request origin, else loopback.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from config import settings  # noqa: E402
from modules.flow_gate.graph_workflow.services import operator_base  # noqa: E402
from modules.flow_gate.services.ai_invoke import provider_remote  # noqa: E402

PUBLIC = "http://flowgate.stg/"
PUBLIC_BASE = "http://flowgate.stg/flowgate/api/v1"
LOOPBACK_BASE = "http://127.0.0.1:8089/flowgate/api/v1"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT", "/flowgate")
    monkeypatch.setattr(settings, "FLOWGATE_PORT", 8089)
    monkeypatch.setattr(settings, "FLOWGATE_OPERATOR_API_BASE", None)
    operator_base._reset_for_tests()
    yield
    operator_base._reset_for_tests()


def _request(base_url: str):
    return SimpleNamespace(base_url=base_url)


def test_without_any_origin_the_start_keeps_loopback_and_remote_still_refuses_it():
    assert operator_base.resolve("u_kim") == LOOPBACK_BASE
    assert provider_remote.api_base_failure(operator_base.resolve("u_kim")) == "api_base_loopback"


def test_the_user_s_own_request_origin_is_used_and_passes_the_remote_check():
    operator_base.remember("u_kim", _request(PUBLIC))
    assert operator_base.resolve("u_kim") == PUBLIC_BASE
    assert provider_remote.api_base_failure(operator_base.resolve("u_kim")) is None
    # another person's origin is never used for this user's Agent
    assert operator_base.resolve("u_lee") == LOOPBACK_BASE
    assert operator_base.resolve(None) == LOOPBACK_BASE


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8089/", "http://localhost:8089/", "http://app.localhost/",
                                    "http://[::1]:8089/"])
def test_a_loopback_request_never_replaces_the_remembered_origin(origin):
    operator_base.remember("u_kim", _request(PUBLIC))
    operator_base.remember("u_kim", _request(origin))
    assert operator_base.resolve("u_kim") == PUBLIC_BASE


def test_the_configured_operator_origin_wins(monkeypatch):
    operator_base.remember("u_kim", _request(PUBLIC))
    monkeypatch.setattr(settings, "FLOWGATE_OPERATOR_API_BASE", "https://fg.example.com:8443/")
    assert operator_base.resolve("u_kim") == "https://fg.example.com:8443/flowgate/api/v1"
    assert operator_base.resolve(None) == "https://fg.example.com:8443/flowgate/api/v1"


@pytest.mark.parametrize("bad", ["", "   ", "flowgate.stg", "ftp://flowgate.stg", "http://flowgate.stg/path",
                                 "http://user:pw@flowgate.stg", "http://flowgate.stg:notaport"])
def test_an_unusable_configured_origin_is_ignored(monkeypatch, bad):
    monkeypatch.setattr(settings, "FLOWGATE_OPERATOR_API_BASE", bad)
    assert operator_base.resolve("u_kim") == LOOPBACK_BASE
    operator_base.remember("u_kim", _request(PUBLIC))
    assert operator_base.resolve("u_kim") == PUBLIC_BASE


def test_remember_never_raises_on_a_broken_request():
    class Broken:
        @property
        def base_url(self):
            raise RuntimeError("no url")

    operator_base.remember("u_kim", Broken())
    operator_base.remember(None, _request(PUBLIC))
    assert operator_base.resolve("u_kim") == LOOPBACK_BASE


def test_the_graph_api_authentication_remembers_the_user_s_origin(monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope

    monkeypatch.setattr(envelope, "verify_bearer", lambda _r: {"_is_user_jwt": True, "sub": "u_kim"})
    assert envelope.authenticate(_request(PUBLIC)) == "u_kim"
    assert operator_base.resolve("u_kim") == PUBLIC_BASE


def test_a_graph_node_start_hands_the_run_user_s_origin_to_the_ai_invoke(monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service

    calls = []
    monkeypatch.setattr(db_documents, "get_by_id", lambda doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(ai_invoke_service, "start_run", lambda **kw: calls.append(kw) or {"run_id": "air_1"})
    job = {"flow_id": "flowgate.v02test.0019", "run_id": "run_1", "node_key": "n_4", "project_id": "flowgate",
           "issued_to": "u_kim", "doc_ref": "flowgate.v02test.0019.0001-R", "doc_type": "N", "provider_id": "remote-cli"}

    ai_node_starter.start_ai_invoke(job)
    operator_base.remember("u_kim", _request(PUBLIC))
    ai_node_starter.start_ai_invoke(job)

    assert [c["api_base_url"] for c in calls] == [LOOPBACK_BASE, PUBLIC_BASE]
    assert provider_remote.api_base_failure(calls[-1]["api_base_url"]) is None
