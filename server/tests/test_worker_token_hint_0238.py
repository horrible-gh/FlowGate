"""Group 0238 T0004: worker-token 401 hint + real help_url.

NR0003 traced a false bug report to two things the server told the worker:

  1. The internal UI API (plural /documents/…) answers a worker token with a bare
     "Invalid authentication credentials", which the worker read as "my token has
     no document-read scope" rather than "wrong API — use singular /document/{id}".
  2. Every error envelope offered help_url = https://example.com/api/v1/help, a
     placeholder domain, so the hint that should have corrected the worker was dead.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-0238-testing-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


class TestHelpUrlResolution:
    """help_url is derived from CONTEXT, never a placeholder domain."""

    def test_help_url_matches_mounted_route(self):
        from config import settings
        from modules.flow_gate.utils.help_url import help_url, outbound_api_base

        # main.py mounts the outbound routers at {CONTEXT}/api/v1.
        context = settings.CONTEXT.rstrip("/")
        assert outbound_api_base() == f"{context}/api/v1"
        assert help_url() == f"{context}/api/v1/help"

    def test_help_url_has_no_placeholder_host(self):
        from modules.flow_gate.utils.help_url import help_url

        assert "example.com" not in help_url()

    def test_help_url_survives_unimportable_config(self):
        from modules.flow_gate.utils import help_url as mod

        with patch.object(mod, "_context", return_value=""):
            assert mod.help_url() == "/api/v1/help"


class TestHelpPayload:
    """GET /help advertises a reachable base and warns about the plural UI API."""

    def _payload(self) -> dict:
        from modules.flow_gate.api.v1.help_routes import endpoint_catalog

        return endpoint_catalog()

    def test_base_url_and_error_format_are_reachable(self):
        from modules.flow_gate.utils.help_url import help_url, outbound_api_base

        data = self._payload()
        assert data["base_url"] == outbound_api_base()
        assert data["error_format"]["help_url"] == help_url()
        assert "example.com" not in json.dumps(data)

    def test_notes_warn_about_singular_vs_plural(self):
        notes = " ".join(self._payload()["notes"])
        assert "/document/{id}" in notes
        assert "/documents/" in notes


class TestWorkerTokenHint:
    """A worker token aimed at the internal UI API gets told so."""

    def test_worker_token_gets_corrective_401(self):
        from modules.flow_gate.auth import middleware

        with patch(
            "modules.flow_gate.services.token_service.verify",
            return_value={"issued_to": "u1", "project": "p1"},
        ):
            with pytest.raises(HTTPException) as exc:
                middleware.verify_token("a-worker-token-which-is-not-a-jwt")

        assert exc.value.status_code == 401
        detail = exc.value.detail
        assert "worker token" in detail
        assert "/api/v1/document/{doc_id}" in detail
        assert "example.com" not in detail

    def test_bogus_credential_keeps_generic_401(self):
        """Anything that is not a live worker token must not learn more than before."""
        from modules.flow_gate.auth import middleware

        with patch(
            "modules.flow_gate.services.token_service.verify",
            side_effect=HTTPException(401, "Invalid or expired token"),
        ):
            with pytest.raises(HTTPException) as exc:
                middleware.verify_token("total-garbage")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid authentication credentials"

    def test_token_lookup_failure_still_401(self):
        """The hint is best-effort: a broken token store must not turn 401 into 500."""
        from modules.flow_gate.auth import middleware

        with patch(
            "modules.flow_gate.services.token_service.verify",
            side_effect=RuntimeError("token store unavailable"),
        ):
            with pytest.raises(HTTPException) as exc:
                middleware.verify_token("total-garbage")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid authentication credentials"

    def test_expired_jwt_message_unchanged(self):
        """An expired session JWT is a different failure and keeps its own message."""
        import jwt as _jwt
        from modules.flow_gate.auth import middleware

        with patch.object(
            middleware, "decode_token", side_effect=_jwt.ExpiredSignatureError()
        ):
            with pytest.raises(HTTPException) as exc:
                middleware.verify_token("expired-jwt")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Token has expired"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
