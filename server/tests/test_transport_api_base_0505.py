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

    def test_malformed_agent_base_falls_back_to_loopback_not_the_public_operator(self, monkeypatch):
        """0496 T0004: a misconfigured `FLOWGATE_AGENT_API_BASE` (prod-type intent
        signalled but unusable) must not silently reinstate the public/proxy operator
        origin as the self-HTTP target -- that IS the pre-0505 cross-instance topology
        (NR0003), just reached through a broken override instead of a missing one. The
        safe degrade is the same loopback+FLOWGATE_PORT address dev-type already uses,
        never the public origin the override existed to avoid dialing in the first place."""
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", "not a valid origin")
        run = _run(PROD_OPERATOR)
        resolved = svc._resolve_transport_api_base(run)
        # PROD_OPERATOR's own scheme (https) and path are kept -- only the host moves
        # to loopback, exactly like the no-override dev-type computation.
        assert resolved == "https://127.0.0.1:8089/flowgate/api/v1"
        assert "flowgate.example" not in resolved

    def test_reset_attempt_state_clears_the_cache_for_the_next_attempt(self, tmp_path):
        run = _run(DEV_OPERATOR, scratch_dir=str(tmp_path))
        svc._resolve_transport_api_base(run)
        assert run["_transport_api_base_resolved"]
        svc._reset_attempt_state(run)
        assert run.get("_transport_api_base_resolved") is None


class TestTransportFallbackKind:
    """0496 T0006 SS3.2: `_resolve_transport_api_base` has always known WHICH of its
    three branches produced the value it returned -- the try succeeded outright, a
    broken override was retried away (safe loopback), or nothing parsed at all so the
    operator base rode through unchanged (the one branch NR0003/0496 T0004 traced the
    original cross-instance 401 to) -- but that distinction lived only in a
    logger.warning line until now. This pins the cached signal
    (`run["_transport_fallback_kind_resolved"]`) the same way
    TestResolveTransportApiBase pins `_transport_api_base_resolved` just above: one test
    per branch, plus the reset contract."""

    def test_no_override_configured_yields_none(self):
        run = _run(DEV_OPERATOR)
        svc._resolve_transport_api_base(run)
        assert run["_transport_fallback_kind_resolved"] == "none"

    def test_working_override_yields_none(self, monkeypatch):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        run = _run(PROD_OPERATOR)
        svc._resolve_transport_api_base(run)
        assert run["_transport_fallback_kind_resolved"] == "none"

    def test_malformed_override_yields_override_ignored(self, monkeypatch):
        """Mirrors TestResolveTransportApiBase.
        test_malformed_agent_base_falls_back_to_loopback_not_the_public_operator: same
        setup, but this asserts on the WHY, not just the resolved value."""
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", "not a valid origin")
        run = _run(PROD_OPERATOR)
        svc._resolve_transport_api_base(run)
        assert run["_transport_fallback_kind_resolved"] == "override_ignored"

    def test_unparseable_operator_base_yields_operator_base_unsafe(self):
        """Mirrors TestResolveTransportApiBase.
        test_falls_back_to_operator_base_without_raising_on_a_bad_port -- the ONE branch
        that is genuinely unsafe (the operator base itself rides through unchanged), so
        it must land a DIFFERENT string than override_ignored, never be collapsed into
        the same flag."""
        bad_operator = "http://host:99999/flowgate/api/v1"
        run = _run(bad_operator)
        svc._resolve_transport_api_base(run)
        assert run["_transport_fallback_kind_resolved"] == "operator_base_unsafe"
        assert run["_transport_fallback_kind_resolved"] != "override_ignored"

    def test_reset_attempt_state_clears_the_fallback_kind_cache_too(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", "not a valid origin")
        run = _run(PROD_OPERATOR, scratch_dir=str(tmp_path))
        svc._resolve_transport_api_base(run)
        assert run["_transport_fallback_kind_resolved"] == "override_ignored"
        svc._reset_attempt_state(run)
        assert run.get("_transport_fallback_kind_resolved") is None


class TestTransportFallbackKindPersistsAlongsideTransportApiBase:
    """0496 T0006 SS3.2 completion condition 3: the diagnostic signal must be reachable
    through an actual mediated self-HTTP call, not just the private cache above -- the
    same hop-first-wins call sites in worker.py that persist `transport_api_base` now
    persist `transport_fallback_kind` alongside it (same guarded block, same `run`
    write), ready for _persist_run_record/finished_payload/diagnostics to carry into
    the durable row and response instead of a grep-only logger.warning line."""

    def test_dev_type_hop_persists_none(self, monkeypatch):
        _capture_urlopen(monkeypatch)
        run = _run(DEV_OPERATOR)
        svc._conversation_turn_register(run, "raw", {"body": "hi"})
        assert run["transport_fallback_kind"] == "none"

    def test_working_override_hop_persists_none(self, monkeypatch):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        _capture_urlopen(monkeypatch)
        run = _run(PROD_OPERATOR)
        svc._conversation_turn_register(run, "raw", {"body": "hi"})
        assert run["transport_fallback_kind"] == "none"

    def test_malformed_override_hop_persists_override_ignored(self, monkeypatch):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", "not a valid origin")
        _capture_urlopen(monkeypatch)
        run = _run(PROD_OPERATOR)
        svc._conversation_turn_register(run, "raw", {"body": "hi"})
        assert run["transport_fallback_kind"] == "override_ignored"
        # Sits alongside the existing base-value regression: the resolved base itself
        # must still degrade to loopback, never the public operator origin.
        assert run["transport_api_base"] is not None
        assert "flowgate.example" not in run["transport_api_base"]

    def test_first_site_in_the_hop_wins_like_transport_api_base_does(self, monkeypatch):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", "not a valid origin")
        _capture_urlopen(monkeypatch)
        run = _run(PROD_OPERATOR)
        svc._conversation_turn_register(run, "raw", {"body": "hi"})
        assert run["transport_fallback_kind"] == "override_ignored"
        # A later site in the SAME hop must not recompute or move it, mirroring
        # transport_api_base's own "FIRST in this hop wins" contract (worker.py).
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", None)
        svc._workflow_decide(run, "raw", {"doc_class": "standard", "sequence": []})
        assert run["transport_fallback_kind"] == "override_ignored"


# 0505 T0018: conversation_context moved to a direct in-process call and dropped out
# of this list -- it no longer dials self-HTTP at all, dev-type or prod-type, so it has
# its own no-urlopen regression below instead (TestDirectCallSitesIgnoreTheTransportBase).
FOUR_SITES = [
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


class TestFourSelfHttpSitesUseTheResolvedTransportBase:
    """Each site captured via `urllib.request.urlopen` -- not by re-reading source --
    once dev-type (operator already loopback, must be unchanged) and once prod-type
    (public operator, configured agent loopback, must NOT dial the public origin).

    0505 T0018 dependency check: these four are exactly the self-HTTP sites T0018 left
    untouched (item 2's "group"-classified four, plus api_bound_request which still
    serves create_question), so this is the direct proof that T#2's transport_api_base
    separation still holds for them after the two GET-only sites were converted."""

    @pytest.mark.parametrize("name,call,path", FOUR_SITES, ids=[c[0] for c in FOUR_SITES])
    def test_dev_type_stays_on_the_operator_base(self, monkeypatch, name, call, path):
        captured = _capture_urlopen(monkeypatch)
        run = _run(DEV_OPERATOR)
        call(run)
        assert captured["request"].full_url == f"{DEV_OPERATOR}{path}"

    @pytest.mark.parametrize("name,call,path", FOUR_SITES, ids=[c[0] for c in FOUR_SITES])
    def test_prod_type_routes_through_the_agent_base_not_the_public_origin(
        self, monkeypatch, name, call, path,
    ):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        captured = _capture_urlopen(monkeypatch)
        run = _run(PROD_OPERATOR)
        call(run)
        assert captured["request"].full_url == f"{PROD_RESOLVED}{path}"
        assert "flowgate.example" not in captured["request"].full_url


# 0505 T0018: conversation_context and api_read_document no longer dial self-HTTP at
# all -- unlike FOUR_SITES above, dev-type vs prod-type transport-base settings must
# make no difference to them, because they never read `_resolve_transport_api_base`'s
# result to decide where to call.
DIRECT_CALL_SITES = [
    ("conversation_context", lambda run, raw: svc._conversation_context(run, raw)),
    ("api_read_document", lambda run, raw: svc._api_read_document(run, raw, {})),
]


class TestDirectCallSitesIgnoreTheTransportBase:
    """Garbage bearer token is enough here: the point is not auth (covered in depth by
    TestProdTypeGenuineTopologyProof below), it is that urlopen is never touched under
    either topology."""

    @pytest.mark.parametrize("name,call", DIRECT_CALL_SITES, ids=[c[0] for c in DIRECT_CALL_SITES])
    def test_dev_type_never_dials_self_http(self, monkeypatch, token_store, name, call):
        monkeypatch.setattr(
            svc.urllib.request, "urlopen",
            lambda *_a, **_k: pytest.fail(f"{name} must not dial self-HTTP (0505 T0018)"),
        )
        run = _run(DEV_OPERATOR)
        status, _payload = call(run, "garbage-not-a-real-token")
        assert status == 401

    @pytest.mark.parametrize("name,call", DIRECT_CALL_SITES, ids=[c[0] for c in DIRECT_CALL_SITES])
    def test_prod_type_never_dials_self_http(self, monkeypatch, token_store, name, call):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        monkeypatch.setattr(
            svc.urllib.request, "urlopen",
            lambda *_a, **_k: pytest.fail(f"{name} must not dial self-HTTP (0505 T0018)"),
        )
        run = _run(PROD_OPERATOR)
        status, _payload = call(run, "garbage-not-a-real-token")
        assert status == 401


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
# TestFourSelfHttpSitesUseTheResolvedTransportBase / TestInboxRegisterUsesTheResolvedTransportBase
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

    # ── conversation_context (0505 T0018: direct in-process call, not self-HTTP) ─
    #
    # Six mediated self-HTTP sites became four (T0018 item 2): GET routes never reach
    # GroupMutationPolicyMiddleware, since mutation_policy.classify_mutation_route's
    # first check is `methods & MUTATION_METHODS` (POST/PUT/PATCH/DELETE only) --
    # conversation_context and api_read_document dropped the loopback round trip
    # entirely and call the route's own plain-Python auth+handler in-process. Each test
    # below fails `urlopen` outright to prove that.

    def _no_self_http(self, monkeypatch, site_name):
        monkeypatch.setattr(
            svc.urllib.request, "urlopen",
            lambda *_a, **_k: pytest.fail(f"{site_name} must not dial self-HTTP (0505 T0018)"),
        )

    def test_conversation_context_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
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
        self._no_self_http(monkeypatch, "_conversation_context")
        run = _run(PROD_OPERATOR)
        status, payload = svc._conversation_context(run, raw)
        assert status == 200
        assert payload == {"turns": [], "head_seq": 0}

    def test_conversation_context_rejects_a_token_bound_to_a_different_doc(self, monkeypatch, token_store):
        """The binding this call has always enforced -- action_scope/doc_ref/project/
        group match (conversation_routes._authenticate) -- must still reject a token
        issued for a different document now that the call is direct (T0018 completion
        criteria)."""
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        raw = _seed_real_token(
            token_store, action_scope="chat", project=PROJECT, group_id=GROUP,
            doc_ref="flowgate.default.0505.9999-T", issued_to="usr-1",
        )
        self._no_self_http(monkeypatch, "_conversation_context")
        run = _run(PROD_OPERATOR)
        status, _payload = svc._conversation_context(run, raw)
        assert status == 403

    def test_conversation_context_rejects_a_garbage_bearer_token(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        self._no_self_http(monkeypatch, "_conversation_context")
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

    # ── api_read_document (0505 T0018: direct in-process call, not self-HTTP) ────
    #
    # Unlike api_bound_request above (still self-HTTP, still used by create_question),
    # _api_read_document now calls document_routes.get_document/get_document_section
    # in-process. Its binding is auth_outbound.verify_bearer -- project + has_permission
    # (perm_document_read), not a doc_ref/group match like conversation_context's -- so
    # the "differently-bound token" regression this call's completion criteria calls for
    # is a real token the permission table denies, not a different doc_ref.

    def test_api_read_document_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        monkeypatch.setattr(auth_outbound, "has_permission", lambda *_a, **_k: True)
        raw = _seed_real_token(token_store, project=PROJECT, issued_to="usr-1")
        monkeypatch.setattr(
            document_routes.db_docs, "get_by_id",
            lambda doc_id: {
                "doc_id": doc_id, "type_code": "T", "title": "x", "status": "open",
                "group_id": GROUP, "project_id": PROJECT,
            },
        )
        monkeypatch.setattr(document_routes, "get_answers_for_document", lambda _doc_id: [])
        self._no_self_http(monkeypatch, "_api_read_document")
        run = _run(PROD_OPERATOR)
        status, payload = svc._api_read_document(run, raw, {})
        assert status == 200
        assert payload["doc_id"] == DOC

    def test_api_read_document_section_authenticates_and_routes_for_real(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        monkeypatch.setattr(auth_outbound, "has_permission", lambda *_a, **_k: True)
        raw = _seed_real_token(token_store, project=PROJECT, issued_to="usr-1")
        monkeypatch.setattr(
            document_routes.db_docs, "get_by_id",
            lambda doc_id: {
                "doc_id": doc_id, "type_code": "T", "title": "x", "status": "open",
                "group_id": GROUP, "project_id": PROJECT, "revision_no": 0,
            },
        )
        monkeypatch.setattr(
            document_routes, "_resolve_live_content", lambda _doc: "# Heading\nBody text.\n",
        )
        self._no_self_http(monkeypatch, "_api_read_document")
        run = _run(PROD_OPERATOR)
        status, payload = svc._api_read_document(run, raw, {"lines": {"start": 1, "end": 1}})
        assert status == 200
        assert payload["doc_id"] == DOC

    def test_api_read_document_rejects_a_garbage_bearer_token(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        self._no_self_http(monkeypatch, "_api_read_document")
        run = _run(PROD_OPERATOR)
        status, _payload = svc._api_read_document(run, "garbage-not-a-real-token", {})
        assert status == 401

    def test_api_read_document_rejects_a_token_without_read_permission(self, monkeypatch, token_store):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", PROD_AGENT_SETTING)
        monkeypatch.setattr(auth_outbound, "has_permission", lambda *_a, **_k: False)
        raw = _seed_real_token(token_store, project=PROJECT, issued_to="usr-1")
        self._no_self_http(monkeypatch, "_api_read_document")
        run = _run(PROD_OPERATOR)
        status, _payload = svc._api_read_document(run, raw, {})
        assert status == 403

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


# ── First failing endpoint, bound to concrete identifiers (0496 T0004 §4) ────────
#
# For a `new`-scope API hop, `_inbox_register` -> POST /inbox is the FIRST (and, since
# T0018 converted read_document/read_help/source ops to in-process calls, now the ONLY)
# self-HTTP call site the hop can reach -- worker.py:1189. This section reproduces
# NR0003's Case A topology ("a different FlowGate DB never saw this token") for real,
# with two separate `db_tokens.get_by_hash` backing stores standing in for two separate
# FlowGate instances, dispatched purely by which hostname a request actually dials --
# so which store answers is a direct function of `_resolve_transport_api_base`'s output,
# the exact value this TR's fix changes. Unlike the malformed-agent-base string
# comparisons above, this binds the failure to a concrete run_id, token_id, transport
# base, method+path, and status, using the real route, the real `token_service.verify`,
# and the real `_inbox_register`/`_bind_register_context` code paths.

CROSS_INSTANCE_RUN_ID = "aiv_20260904_cross_instance_repro"
CROSS_INSTANCE_TOKEN_ID = "tok_cross_instance_repro"


def _dual_store_urlopen(monkeypatch, client: TestClient, active: dict):
    """Routes to `client` regardless of destination -- both "instances" are the same
    process/app in this proof, exactly like the pair of real FlowGate deployments
    NR0003 SS8 describes share the same server code and differ only in which DB/pepper
    each is wired to. `active["which"]` records which hostname was actually dialled so
    the monkeypatched `db_tokens.get_by_hash` (installed by the caller) can answer from
    the matching store -- the destination the real code chose decides the outcome, not
    the test."""

    def fake_open(request, timeout=None):
        active["which"] = "public" if "flowgate.example" in request.full_url else "local"
        parsed = urlsplit(request.full_url)
        path_qs = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        headers = dict(request.header_items())
        resp = client.request(
            request.get_method(), path_qs, content=request.data, headers=headers,
        )
        return _RealResponse(resp)

    monkeypatch.setattr(svc.urllib.request, "urlopen", fake_open)


class TestFirstFailingEndpointBoundToConcreteIdentifiers:
    """T1 completion condition 1 / §14: the actual first 401, bound to run_id,
    token_id, transport base, method/path, and status -- not a conditional example."""

    @pytest.fixture(autouse=True)
    def _inbox_dependencies(self, monkeypatch):
        monkeypatch.setattr(process_service_module, "is_group_disposed", lambda *_a, **_k: False)
        monkeypatch.setattr(auth_outbound, "has_permission", lambda *_a, **_k: True)
        monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
        monkeypatch.setattr(inbox_routes, "_is_valid_doc_type", lambda *_a, **_k: True)
        monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _t: False)
        monkeypatch.setattr(
            inbox_routes.db_docs, "get_by_id",
            lambda doc_id: {"doc_id": doc_id, "group_id": GROUP, "project_id": PROJECT},
        )
        monkeypatch.setattr(
            inbox_routes, "_resolve_group",
            lambda _project, _group_name: {"group_id": GROUP, "project_id": PROJECT},
        )
        monkeypatch.setattr(inbox_routes.db_wfseq, "get_pending_head_by_group", lambda *_a, **_k: None)
        monkeypatch.setattr(svc.token_service, "increment_dry_run", lambda *_a, **_k: None)
        original_envelope = svc._register_envelope

        def _envelope_with_dry_run(context, run, tool_input):
            body = original_envelope(context, run, tool_input)
            body["dry_run"] = True
            return body

        monkeypatch.setattr(svc, "_register_envelope", _envelope_with_dry_run)

    def test_wrong_destination_genuinely_401s_the_same_valid_token_the_right_one_accepts(
        self, monkeypatch,
    ):
        monkeypatch.setattr(settings, "FLOWGATE_AGENT_API_BASE", "not a valid origin")
        local_store: dict = {}
        public_store: dict = {}
        active = {"which": "local"}

        def get_by_hash(h):
            store = public_store if active["which"] == "public" else local_store
            return dict(store[h]) if h in store else None

        monkeypatch.setattr(db_tokens_module, "get_by_hash", get_by_hash)
        raw = _seed_real_token(
            local_store, token_id=CROSS_INSTANCE_TOKEN_ID, action_scope="new",
            project=PROJECT, group_id=GROUP, doc_ref=DOC, issued_to="usr-1",
            ai_run_id=CROSS_INSTANCE_RUN_ID,
        )
        client = TestClient(_build_prod_app(), raise_server_exceptions=False)
        _dual_store_urlopen(monkeypatch, client, active)

        # -- The concrete "first failing endpoint": the SAME real run_id/token_id,
        # self-HTTP forced onto the public/operator origin -- the exact fallback this
        # TR's fix removes (the pre-fix `except ValueError: resolved = operator_base`,
        # reachable whenever FLOWGATE_AGENT_API_BASE was configured but unusable).
        # `_bind_register_context`'s own local pre-check runs against THIS process
        # (active=="local"), so only the outbound self-HTTP round trip below is what
        # lands on the wrong instance.
        active["which"] = "local"
        run_wrong = _run(
            PROD_OPERATOR, run_id=CROSS_INSTANCE_RUN_ID, token_id=CROSS_INSTANCE_TOKEN_ID,
            current_token_id=CROSS_INSTANCE_TOKEN_ID,
        )
        run_wrong["_transport_api_base_resolved"] = PROD_OPERATOR
        status_wrong, payload_wrong = svc._inbox_register(
            run_wrong, raw, {"doc_type": "T", "content": "Plain body text for the repro."},
        )
        # Bound record (T1 §14): run_id=aiv_20260904_cross_instance_repro
        # token_id=tok_cross_instance_repro transport_base=https://flowgate.example/...
        # method=POST path=/inbox status=401
        assert status_wrong == 401
        assert payload_wrong.get("error_message") == "Token is invalid"

        # -- The SAME run_id/token_id/raw token, resolved the way the FIXED
        # `_resolve_transport_api_base` actually resolves a configured-but-malformed
        # override: ignored, degrading to loopback -- never the public origin above.
        # Lands on the store that actually has the token, and succeeds.
        active["which"] = "local"
        run_right = _run(
            PROD_OPERATOR, run_id=CROSS_INSTANCE_RUN_ID, token_id=CROSS_INSTANCE_TOKEN_ID,
            current_token_id=CROSS_INSTANCE_TOKEN_ID,
        )
        status_right, payload_right = svc._inbox_register(
            run_right, raw, {"doc_type": "T", "content": "Plain body text for the repro."},
        )
        assert status_right == 200
        assert payload_right["ok"] is True
        assert payload_right["dry_run"] is True
        resolved = svc._resolve_transport_api_base(run_right)
        assert resolved != PROD_OPERATOR
        assert "flowgate.example" not in resolved
