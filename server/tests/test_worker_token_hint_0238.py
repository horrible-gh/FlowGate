"""Group 0238 T0004: real help_url (and what became of the worker-token 401 hint).

NR0003 traced a false bug report to two things the server told the worker:

  1. The internal UI API (plural /documents/…) answers a worker token with a bare
     "Invalid authentication credentials", which the worker read as "my token has
     no document-read scope" rather than "wrong API — use singular /document/{id}".
  2. Every error envelope offered help_url = https://example.com/api/v1/help, a
     placeholder domain, so the hint that should have corrected the worker was dead.

Item 2 is still guarded below. Item 1 is not, because the product no longer does it:
`middleware.verify_token` has no worker-token branch, and GET /help now states the bare
401 as the expected answer ("worker token은 401 'Invalid authentication credentials'를
받는다", help_routes.py) — the correction moved from the error message into the docs the
worker reads first. The case asserting the corrective 401 was deleted under 0394 T0004
(NR0003 §4.2 S7) rather than repaired: it described a feature that was withdrawn, so
making it pass would mean putting the feature back, which is not this group's call. The
three cases around it still hold and are unchanged — the generic 401 for a bogus
credential, the 401 (not 500) when the token store is down, and the expired-JWT message.
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


class TestUiApiRejects:
    """What the internal UI API tells a non-JWT credential. See the module docstring
    for why the corrective-hint case is gone."""

    def test_worker_token_gets_the_same_generic_401(self):
        """A live worker token is still just "not a session JWT" to this API.

        Kept as the positive record of the withdrawn hint: the 401 is expected, and it
        must not leak anything about the token that was presented.
        """
        from modules.flow_gate.auth import middleware

        with patch(
            "modules.flow_gate.services.token_service.verify",
            return_value={"issued_to": "u1", "project": "p1"},
        ):
            with pytest.raises(HTTPException) as exc:
                middleware.verify_token("a-worker-token-which-is-not-a-jwt")

        assert exc.value.status_code == 401
        assert exc.value.detail == "Invalid authentication credentials"

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
