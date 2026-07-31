"""CORS default-value hardening (flowgate.default.0371 NR0007 §2 / T0008).

ALLOWED_ORIGIN used to default to '*' across every deploy/setup path
(docker-compose.yml, deploy/docker-entrypoint.sh, server/.env.sample,
setup.sh, setup.ps1), which combined with allow_credentials=True in
server/routers/main.py let any origin call the API with credentials. The
fix moves the origin-list parsing and the wildcard/credentials guard into
modules.flow_gate.utils.cors_settings so both halves are covered here
without importing the full app (server/routers/main.py has heavy
side-effecting imports; see test_request_scope_cache_0291.py for the same
"minimal app + real middleware" convention this suite follows).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.utils.cors_settings import (  # noqa: E402
    parse_allowed_origins,
    resolve_allow_credentials,
)


def test_blank_value_parses_to_no_origins():
    assert parse_allowed_origins("") == []
    assert parse_allowed_origins("   ") == []


def test_wildcard_parses_as_a_single_origin():
    assert parse_allowed_origins("*") == ["*"]


def test_comma_separated_list_is_trimmed():
    assert parse_allowed_origins(" https://a.example ,https://b.example, ") == [
        "https://a.example",
        "https://b.example",
    ]


def test_empty_segments_are_dropped():
    assert parse_allowed_origins(",,") == []
    assert parse_allowed_origins("https://a.example,,https://b.example") == [
        "https://a.example",
        "https://b.example",
    ]


def test_credentials_disabled_for_wildcard():
    assert resolve_allow_credentials(["*"]) is False


def test_credentials_enabled_for_explicit_origins():
    assert resolve_allow_credentials(["https://a.example"]) is True


def test_credentials_enabled_when_no_origins_configured():
    # No origins allowed at all either way; True here just avoids a
    # surprising flip if an origin is added later without revisiting this.
    assert resolve_allow_credentials([]) is True


# ── HTTP behavior ──────────────────────────────────────────────────────────
#
# routers/main.py is not imported here (see module docstring). A minimal app
# wires up the same CORSMiddleware call with the same helpers, so what is
# under test is the actual header behavior an operator's default produces.


def _cors_app(raw_allowed_origin: str):
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    origins = parse_allowed_origins(raw_allowed_origin)
    app = FastAPI()

    @app.get("/ping")
    def ping():  # noqa: ANN202
        return {"ok": True}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=resolve_allow_credentials(origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


def test_blank_default_allows_no_cross_origin_header():
    from fastapi.testclient import TestClient

    with TestClient(_cors_app("")) as client:
        resp = client.get("/ping", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in resp.headers


def test_explicit_origin_is_allowed_and_others_are_not():
    from fastapi.testclient import TestClient

    with TestClient(_cors_app("https://good.example")) as client:
        good = client.get("/ping", headers={"Origin": "https://good.example"})
        assert good.headers["access-control-allow-origin"] == "https://good.example"

        bad = client.get("/ping", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in bad.headers


def test_comma_list_default_allows_each_listed_origin():
    from fastapi.testclient import TestClient

    with TestClient(_cors_app("https://a.example, https://b.example")) as client:
        a = client.get("/ping", headers={"Origin": "https://a.example"})
        assert a.headers["access-control-allow-origin"] == "https://a.example"

        b = client.get("/ping", headers={"Origin": "https://b.example"})
        assert b.headers["access-control-allow-origin"] == "https://b.example"


def test_wildcard_default_omits_allow_credentials_header():
    from fastapi.testclient import TestClient

    with TestClient(_cors_app("*")) as client:
        resp = client.get("/ping", headers={"Origin": "https://anything.example"})
        assert resp.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in resp.headers
