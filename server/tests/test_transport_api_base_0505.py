"""0505 T0008 -- dev/prod-type self-HTTP transport base separation.

NR0003 SS6-SS11 found that the CLI path already separates the browser-facing operator
base from the address this server dials itself at (`_resolve_agent_api_base`, fixed
for 0472 B0001). The API provider's six mediated self-HTTP call sites
(conversation_context, conversation_turn_register, api_bound_request, inbox_register,
resolve_conflict, workflow_decide) did not: all six sent `run["api_base_url"]` -- the
operator/browser origin -- straight back to themselves, which is exactly the topology
0472 B0001 broke on for the CLI path before it was fixed there. This suite pins the
fix for the API path:

* `_resolve_transport_api_base` computes and caches, per hop, the address this hop's
  self-HTTP should dial -- delegating to the same `_resolve_agent_api_base` the CLI
  path already trusted, and never raising on a malformed operator base;
* dev-type (operator base already loopback, no FLOWGATE_AGENT_API_BASE): resolved ==
  operator, so every one of the six sites keeps behaving exactly as before;
* prod-type (operator base is a public/proxy origin, FLOWGATE_AGENT_API_BASE names a
  loopback the server can actually reach): all six sites dial the configured base, not
  the public one -- the direct regression check for the 401-class topology NR0003
  SS7-SS8 named;
* `api_server_tools.read_help` keeps showing the OPERATOR base in its URLs regardless
  -- it is human-read help text, not a self-HTTP call, and must not follow this hop to
  loopback (WP0004 T#2 note).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
# token_service.verify/inspect_for_replay must run for REAL (hash + pepper + expiry +
# revocation) in the prod-type integration proof below, so a real pepper has to exist.
# setdefault so a real environment's own value (if any) is never overridden.
os.environ.setdefault("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "v1")
os.environ.setdefault("FLOWGATE_TOKEN_PEPPER_v1", "test-only-pepper-not-a-real-secret")
_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import secrets  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from config import settings  # noqa: E402
from modules.flow_gate import process_service as process_service_module  # noqa: E402
from modules.flow_gate.api import inbox_routes  # noqa: E402
from modules.flow_gate.api.v1 import conversation_routes  # noqa: E402
from modules.flow_gate.api.v1 import document_routes  # noqa: E402
from modules.flow_gate.api.v1 import git_routes  # noqa: E402
from modules.flow_gate.api.v1 import workflow_decision_routes as workflow_routes  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens_module  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import api_server_tools as tools  # noqa: E402
from modules.flow_gate.services import auth_outbound  # noqa: E402
from modules.flow_gate.services import register_binding as binding  # noqa: E402

PROJECT = "flowgate"
GROUP = "flowgate.default.0505"
DOC = "flowgate.default.0505.0008-T"

DEV_OPERATOR = "http://127.0.0.1:8089/flowgate/api/v1"
PROD_OPERATOR = "https://flowgate.example/flowgate/api/v1"
PROD_AGENT_SETTING = "http://127.0.0.1:8088"
PROD_RESOLVED = "http://127.0.0.1:8088/flowgate/api/v1"


def _run(api_base_url, **over):
    run = {
        "run_id": "aiv_test_transport", "project_id": PROJECT, "group_id": GROUP,
        "doc_ref": DOC, "merge_id": "m1", "token_id": "tok-transport",
        "current_token_id": "tok-transport", "raw_token": "raw",
        "action_scope": "new", "module": "default",
        "api_base_url": api_base_url, "register_errors": [],
    }
    run.update(over)
    return run


class _Response:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = body if body is not None else {"ok": True}

    def read(self):
        return json.dumps(self._body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _capture_urlopen(monkeypatch, status=200, body=None):
    captured = {}

    def fake_open(request, timeout=None):
        captured["request"] = request
        return _Response(status, body)

    monkeypatch.setattr(svc.urllib.request, "urlopen", fake_open)
    return captured


@pytest.fixture(autouse=True)
def _agent_base_unset(monkeypatch):
    """Dev-type default: no configured agent origin, and FLOWGATE_PORT matches the
    loopback port every DEV_OPERATOR/PROD case below already assumes."""
    monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", None)
    monkeypatch.setattr(settings, "FLOWGATE_PORT", 8089)


class TestResolveTransportApiBase:

    def test_dev_type_already_loopback_resolves_to_itself(self):
        run = _run(DEV_OPERATOR)
        assert svc._resolve_transport_api_base(run) == DEV_OPERATOR

    def test_prod_type_public_operator_resolves_to_configured_agent_base(self, monkeypatch):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        run = _run(PROD_OPERATOR)
        assert svc._resolve_transport_api_base(run) == PROD_RESOLVED

    def test_result_is_cached_for_the_hop(self, monkeypatch):
        run = _run(DEV_OPERATOR)
        first = svc._resolve_transport_api_base(run)
        # A setting change mid-hop must not move the cached value: the six sites
        # agreeing with EACH OTHER within one hop matters more than tracking a live
        # setting.
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", "http://127.0.0.1:9999")
        assert svc._resolve_transport_api_base(run) == first

    def test_falls_back_to_operator_base_without_raising_on_a_bad_port(self):
        bad_operator = "http://host:99999/flowgate/api/v1"
        run = _run(bad_operator)
        assert svc._resolve_transport_api_base(run) == bad_operator

    def test_reset_attempt_state_clears_the_cache_for_the_next_attempt(self, tmp_path):
        run = _run(DEV_OPERATOR, scratch_dir=str(tmp_path))
        svc._resolve_transport_api_base(run)
        assert run["_transport_api_base_resolved"]
        svc._reset_attempt_state(run)
        assert run.get("_transport_api_base_resolved") is None


# The five sites that need no token/binding setup -- everything but inbox_register.
FIVE_SITES = [
    ("conversation_context",
     lambda run: svc._conversation_context(run, "raw"),
     f"/conversation/{DOC}/turns?after_seq=0&include_head=1"),
    ("conversation_turn_register",
     lambda run: svc._conversation_turn_register(run, "raw", {"body": "hi"}),
     f"/conversation/{DOC}/turn"),
    ("api_bound_request",
     lambda run: svc._api_bound_request(run, "raw", f"/document/{DOC}"),
     f"/document/{DOC}"),
    ("resolve_conflict",
     lambda run: svc._resolve_conflict(run, "raw", {"files": [], "complete": False}),
     f"/groups/{GROUP}/git/merge/m1/resolve-token"),
    ("workflow_decide",
     lambda run: svc._workflow_decide(run, "raw", {"doc_class": "standard", "sequence": []}),
     f"/workflow/{DOC}/decide"),
]


class TestFiveSelfHttpSitesUseTheResolvedTransportBase:
    """Each site captured via `urllib.request.urlopen` -- not by re-reading source --
    once dev-type (operator already loopback, must be unchanged) and once prod-type
    (public operator, configured agent loopback, must NOT dial the public origin)."""

    @pytest.mark.parametrize("name,call,path", FIVE_SITES, ids=[c[0] for c in FIVE_SITES])
    def test_dev_type_stays_on_the_operator_base(self, monkeypatch, name, call, path):
        captured = _capture_urlopen(monkeypatch)
        run = _run(DEV_OPERATOR)
        call(run)
        assert captured["request"].full_url == f"{DEV_OPERATOR}{path}"

    @pytest.mark.parametrize("name,call,path", FIVE_SITES, ids=[c[0] for c in FIVE_SITES])
    def test_prod_type_routes_through_the_agent_base_not_the_public_origin(
        self, monkeypatch, name, call, path,
    ):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        captured = _capture_urlopen(monkeypatch)
        run = _run(PROD_OPERATOR)
        call(run)
        assert captured["request"].full_url == f"{PROD_RESOLVED}{path}"
        assert "flowgate.example" not in captured["request"].full_url


class TestInboxRegisterUsesTheResolvedTransportBase:
    """`_inbox_register`'s self-HTTP goes through `_bind_register_context` first, so it
    needs a verified token and a resolvable group -- unlike the five sites above."""

    @pytest.fixture(autouse=True)
    def _bound(self, monkeypatch):
        token = {
            "token_id": "tok-transport", "project": PROJECT, "group_id": GROUP,
            "doc_ref": DOC, "action_scope": "new", "issued_to": "usr-1",
            "ai_run_id": "aiv_test_transport", "provider_id": "prov-1",
        }
        monkeypatch.setattr(svc.token_service, "verify", lambda _raw: token)
        monkeypatch.setattr(
            binding.db_docs, "get_by_id",
            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP} if doc_id else None,
        )

    def test_dev_type_stays_on_the_operator_base(self, monkeypatch):
        captured = _capture_urlopen(monkeypatch)
        run = _run(DEV_OPERATOR)
        svc._inbox_register(run, "raw", {"doc_type": "TR", "content": "c"})
        assert captured["request"].full_url == f"{DEV_OPERATOR}/inbox"

    def test_prod_type_routes_through_the_agent_base_not_the_public_origin(self, monkeypatch):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        captured = _capture_urlopen(monkeypatch)
        run = _run(PROD_OPERATOR)
        svc._inbox_register(run, "raw", {"doc_type": "TR", "content": "c"})
        assert captured["request"].full_url == f"{PROD_RESOLVED}/inbox"
        assert "flowgate.example" not in captured["request"].full_url


class TestReadHelpStaysOnTheOperatorBase:
    """WP0004 T#2 note / T0008 SS4: read_help is a direct in-process call, not a self-
    HTTP request, and the URLs it hands back are read by a person -- they must keep
    showing the operator/browser origin even when the agent base differs from it."""

    def test_index_urls_use_operator_base_even_when_agent_base_differs(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        token = {"continuation_locale": "ko", "doc_ref": DOC}
        ctx = {
            "base_url": PROD_OPERATOR, "locale": "ko", "doc_id": DOC, "doc_type": "T",
            "action_scope": "new", "tool_kind": "read", "source_mode": "remote",
            "reason": None,
        }
        monkeypatch.setattr(tools.token_service, "verify", lambda _raw: token)
        monkeypatch.setattr(tools.help_catalog, "resolve_context", lambda *_a: ctx)
        monkeypatch.setattr(
            tools.help_catalog, "build_index", lambda _ctx: {"items": [], "hidden": []},
        )
        run = {
            "project_id": PROJECT, "group_id": GROUP, "doc_ref": DOC,
            "action_scope": "new", "source_root": str(tmp_path), "api_base_url": PROD_OPERATOR,
        }
        status, payload = tools.read_help(run, "raw", {})
        assert status == 200
        assert payload["item_url"] == f"{PROD_OPERATOR}/help/items/{{name}}"
        assert payload["detail_url"] == f"{PROD_OPERATOR}/help?detail=true"
        assert "127.0.0.1:8088" not in json.dumps(payload)


# ── Genuine prod-type integration proof ───────────────────────────────────────
#
# TestFiveSelfHttpSitesUseTheResolvedTransportBase / TestInboxRegisterUsesTheResolvedTransportBase
# above capture `urllib.request.urlopen` and assert only the outgoing `Request.full_url` --
# they never dispatch into a real route, so they cannot tell a correctly-resolved loopback
# URL from one that would 404 or 401 on a real server. This section replaces the captured
# fake with one that forwards the SAME captured `Request` (method, path+query, headers
# including Authorization, JSON body) into a real FastAPI `TestClient` mounted with the
# actual production route modules for all six target endpoints -- so a passing test here
# means the resolved loopback path is one the real app actually serves, AND the real auth
# function on that route (token_service.verify / inspect_for_replay, reached directly or
# through auth_outbound.verify_bearer) genuinely accepts a token whose row satisfies its
# real hashing/pepper/expiry check and genuinely rejects a garbage one.
#
# Only the DB round-trip is faked: `db_tokens.get_by_hash` is served from an in-memory
# dict seeded via `_seed_real_token`, whose hash is computed with the module's own real
# `_hash_token` + `_active_pepper` -- never a stub hash. Business logic past auth+routing
# (permission tables, document/group storage, git operations, the deep document-creation
# chain) is mocked at the DB/service-layer boundary, the same level of mocking this
# codebase already uses in test_resolve_conflict_issue_lease_block_0447.py and
# test_document_ai_running_guard_0378.py (real routers mounted into a minimal FastAPI()
# app, only DB-layer/service-layer functions monkeypatched, driven with TestClient).


def _build_prod_app() -> FastAPI:
    """The actual production route modules for the six target endpoints, mounted at the
    same `/flowgate` context prefix `routers/main.py` uses -- so a request built from
    `_resolve_transport_api_base`'s resolved loopback URL either matches a real route
    here or 404s, exactly as it would against the real app."""
    app = FastAPI()
    app.include_router(conversation_routes.router, prefix="/flowgate")
    app.include_router(document_routes.router, prefix="/flowgate")
    app.include_router(git_routes.router, prefix="/flowgate")
    app.include_router(workflow_routes.router, prefix="/flowgate")
    app.include_router(inbox_routes.router, prefix="/flowgate")
    return app


class _RealResponse:
    """`urlopen`-response-shaped wrapper around a real `TestClient` response, read by
    the six sites exactly like a real `http.client.HTTPResponse` -- `.status`/`.read()`
    under a context manager, never raising for a non-2xx (the six sites' own
    try/except around `urllib.error.HTTPError` handles that either way)."""

    def __init__(self, resp):
        self.status = resp.status_code
        self._body = resp.content

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _forward_to_app(client: TestClient, captured: dict):
    """A `urlopen` replacement that genuinely dispatches into `client` instead of
    returning a canned response. `captured["request"]` still records the outgoing
    `Request` so a test can assert on the resolved URL exactly like the existing
    `_capture_urlopen`-based tests above."""

    def fake_open(request, timeout=None):
        captured["request"] = request
        parsed = urlsplit(request.full_url)
        path_qs = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        headers = dict(request.header_items())
        resp = client.request(
            request.get_method(), path_qs, content=request.data, headers=headers,
        )
        return _RealResponse(resp)

    return fake_open


@pytest.fixture
def token_store(monkeypatch):
    """The faked storage layer: `db_tokens.get_by_hash` served from an in-memory dict
    this fixture owns. `token_service.verify` / `inspect_for_replay` run entirely for
    real against it -- only the SQL round-trip is faked."""
    store: dict[str, dict] = {}
    monkeypatch.setattr(
        db_tokens_module, "get_by_hash",
        lambda h, _store=store: dict(_store[h]) if h in _store else None,
    )
    return store


def _seed_real_token(token_store: dict, **fields) -> str:
    """Insert one row `token_service._find_token_by_raw` can find for real, hashed with
    the module's own real `_hash_token` + `_active_pepper` -- never a stub hash. Returns
    the raw bearer token a caller would send."""
    raw = secrets.token_urlsafe(24)
    _pepper_id, pepper = svc.token_service._active_pepper()
    token_hash = svc.token_service._hash_token(raw, pepper)
    row = {
        "token_id": "tok-transport",
        "hash": token_hash,
        "pepper_id": "v1",
        "revoked_at": None,
        "consumed_at": None,
        "expires_at": "2999-01-01T00:00:00+00:00",
        "scratch_dir": None,
        "continuation_locale": None,
        "continuation_auto_approve_item_seqs": [],
    }
    row.update(fields)
    token_store[token_hash] = row
    return raw


class TestProdTypeGenuineTopologyProof:
    """The rejection's own words: prove all six self-HTTP calls still authenticate and
    route successfully under the separated public-operator/loopback-agent topology --
    not just that the resolved URL string looks right.

    Real for every site: routing (the real FastAPI app's own ASGI dispatch table), and
    the real auth function the route calls. Mocked, per site, only what T0008 section 5
    does not ask this suite to re-prove: permission tables (has_permission), document/
    group storage lookups, git operations, and (for inbox specifically -- see that
    test's own docstring) the deep numbering/storage/DB/git document-creation chain.
    """

    def _client(self, monkeypatch) -> TestClient:
        monkeypatch.setattr(process_service_module, "is_group_disposed", lambda *_a, **_k: False)
        monkeypatch.setattr(auth_outbound, "has_permission", lambda *_a, **_k: True)
        return TestClient(_build_prod_app(), raise_server_exceptions=False)

    # ── conversation_context ──────────────────────────────────────────────────

    def test_conversation_context_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        raw = _seed_real_token(
            token_store, action_scope="chat", project=PROJECT, group_id=GROUP,
            doc_ref=DOC, issued_to="usr-1",
        )
        monkeypatch.setattr(
            conversation_routes.db_documents, "get_by_id",
            lambda doc_id: {"doc_id": doc_id, "project_id": PROJECT, "group_id": GROUP},
        )
        monkeypatch.setattr(
            conversation_routes.conversation_query_service, "list_turns",
            lambda **kw: {"turns": [], "head_seq": 0},
        )
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, payload = svc._conversation_context(run, raw)
        assert status == 200
        assert payload == {"turns": [], "head_seq": 0}
        assert captured["request"].full_url == (
            f"{PROD_RESOLVED}/conversation/{DOC}/turns?after_seq=0&include_head=1"
        )

    def test_conversation_context_rejects_a_garbage_bearer_token(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, _payload = svc._conversation_context(run, "garbage-not-a-real-token")
        assert status == 401

    # ── conversation_turn_register ────────────────────────────────────────────

    def test_conversation_turn_register_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        raw = _seed_real_token(
            token_store, action_scope="chat", project=PROJECT, group_id=GROUP,
            doc_ref=DOC, issued_to="usr-1",
        )
        monkeypatch.setattr(
            conversation_routes.db_documents, "get_by_id",
            lambda doc_id: {"doc_id": doc_id, "project_id": PROJECT, "group_id": GROUP},
        )
        monkeypatch.setattr(
            conversation_routes.conversation_turn_service, "append_turn",
            lambda **kw: {"replayed": False, "turn": {"seq": 1}},
        )
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, payload = svc._conversation_turn_register(run, raw, {"body": "hi"})
        assert status == 201
        assert payload["turn"]["seq"] == 1
        assert captured["request"].full_url == f"{PROD_RESOLVED}/conversation/{DOC}/turn"

    # ── api_bound_request (GET /document/{doc}) ───────────────────────────────

    def test_api_bound_request_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        raw = _seed_real_token(token_store, project=PROJECT, issued_to="usr-1")
        monkeypatch.setattr(
            document_routes.db_docs, "get_by_id",
            lambda doc_id: {
                "doc_id": doc_id, "type_code": "T", "title": "x", "status": "open",
                "group_id": GROUP, "project_id": PROJECT,
            },
        )
        monkeypatch.setattr(document_routes, "get_answers_for_document", lambda _doc_id: [])
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, payload = svc._api_bound_request(run, raw, f"/document/{DOC}")
        assert status == 200
        assert payload["doc_id"] == DOC
        assert captured["request"].full_url == f"{PROD_RESOLVED}/document/{DOC}"

    def test_api_bound_request_rejects_a_garbage_bearer_token(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, _payload = svc._api_bound_request(run, "garbage-not-a-real-token", f"/document/{DOC}")
        assert status == 401

    # ── resolve_conflict ───────────────────────────────────────────────────────

    def test_resolve_conflict_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        raw = _seed_real_token(
            token_store, action_scope="resolve_conflict", project=PROJECT,
            group_id=GROUP, merge_id=5, issued_to="usr-1",
        )
        monkeypatch.setattr(
            git_routes.git_service, "resolve_conflicts",
            lambda *_a, **_k: {"ok": True, "result": {"status": "in_progress"}},
        )
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR, merge_id="5")
        status, payload = svc._resolve_conflict(run, raw, {"files": [], "complete": False})
        assert status == 200
        assert payload == {"ok": True, "result": {"status": "in_progress"}}
        assert captured["request"].full_url == (
            f"{PROD_RESOLVED}/groups/{GROUP}/git/merge/5/resolve-token"
        )

    def test_resolve_conflict_rejects_a_garbage_bearer_token(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR, merge_id="5")
        status, _payload = svc._resolve_conflict(
            run, "garbage-not-a-real-token", {"files": [], "complete": False},
        )
        assert status == 401

    # ── workflow_decide ────────────────────────────────────────────────────────

    def test_workflow_decide_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        raw = _seed_real_token(
            token_store, action_scope="workflow_decide", project=PROJECT,
            group_id=GROUP, doc_ref=DOC, issued_to="usr-1",
        )
        monkeypatch.setattr(
            workflow_routes._db_documents, "get_by_id",
            lambda doc_id: {
                "doc_id": doc_id, "group_id": GROUP, "status": "open",
                "doc_review_status": "wf_pending",
            },
        )
        monkeypatch.setattr(
            workflow_routes, "decide_workflow",
            lambda **kw: {"ok": True, "doc_class": kw["doc_class"]},
        )
        # Step 8 of the real handler consumes the token after a successful decide -- a
        # real DB write this proof does not need (auth already ran for real above it).
        monkeypatch.setattr(svc.token_service, "consume", lambda **_k: None)
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, payload = svc._workflow_decide(
            run, raw,
            {"doc_class": "standard", "sequence": [{"id": 1, "type": "D", "label": "Design"}]},
        )
        assert status == 201
        assert payload["ok"] is True
        assert captured["request"].full_url == f"{PROD_RESOLVED}/workflow/{DOC}/decide"

    def test_workflow_decide_rejects_a_garbage_bearer_token(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, _payload = svc._workflow_decide(
            run, "garbage-not-a-real-token",
            {"doc_class": "standard", "sequence": [{"id": 1, "type": "D", "label": "Design"}]},
        )
        assert status == 401

    # ── inbox_register ─────────────────────────────────────────────────────────

    def test_inbox_register_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        """`_inbox_register`'s outbound body is built by `_register_envelope`, whose
        per-scope model-field allowlist (`_REGISTER_MODEL_FIELDS`) has no `dry_run`
        entry for the `new` scope -- an API-provider run never gets the preview an
        external CLI worker posting to /inbox directly can ask for. So the real,
        unmodified `_inbox_register` can never itself send `dry_run`, and a
        real-routing proof of this site would otherwise have to walk the full
        numbering/storage/DB/git/SSE chain -- exactly the depth T0008 section 5 does
        not ask this suite to re-prove (it only asks for auth + routing + the expected
        success status). This wraps the envelope builder for THIS TEST ONLY to stamp
        dry_run=True onto whatever the real, unchanged builder already produced.
        Everything upstream of that stamp -- token_service.verify (real), the real
        four-axis register_binding check, permission, doc_type validity and
        referential integrity -- still runs unmodified and for real, and the request
        reaches `_maybe_dry_run`'s real short-circuit before any storage/git write.
        """
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        client = self._client(monkeypatch)
        raw = _seed_real_token(
            token_store, action_scope="new", project=PROJECT, group_id=GROUP,
            doc_ref=DOC, issued_to="usr-1", ai_run_id="aiv_test_transport",
        )
        monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
        monkeypatch.setattr(inbox_routes, "_is_valid_doc_type", lambda *_a, **_k: True)
        # is_design_type("T") delegates to document_types.series in the DB (real gate,
        # DESIGN_TYPES is only a design-time mirror) -- "T" is not a design series type
        # in production either, this only avoids depending on that table's seed data.
        monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _t: False)
        monkeypatch.setattr(
            inbox_routes.db_docs, "get_by_id",
            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP, "project_id": PROJECT},
        )
        monkeypatch.setattr(
            inbox_routes, "_resolve_group",
            lambda _project, _group_name: {"group_id": GROUP, "project_id": PROJECT},
        )
        monkeypatch.setattr(
            inbox_routes.db_wfseq, "get_pending_head_by_group", lambda *_a, **_k: None,
        )
        monkeypatch.setattr(svc.token_service, "increment_dry_run", lambda *_a, **_k: None)

        original_envelope = svc._register_envelope

        def _envelope_with_dry_run(context, run, tool_input):
            body = original_envelope(context, run, tool_input)
            body["dry_run"] = True
            return body

        monkeypatch.setattr(svc, "_register_envelope", _envelope_with_dry_run)

        captured: dict = {}
        monkeypatch.setattr(svc.urllib.request, "urlopen", _forward_to_app(client, captured))
        run = _run(PROD_OPERATOR)
        status, payload = svc._inbox_register(
            run, raw, {"doc_type": "T", "content": "Plain body text for the loopback proof."},
        )
        assert status == 200
        assert payload["ok"] is True
        assert payload["dry_run"] is True
        assert payload["would_register"]["doc_type"] == "T"
        assert captured["request"].full_url == f"{PROD_RESOLVED}/inbox"
        assert "flowgate.example" not in captured["request"].full_url
