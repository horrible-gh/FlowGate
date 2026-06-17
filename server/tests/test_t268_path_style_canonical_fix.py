"""T268 ? Path-style route canonical reconstruction fix (NR068 ?1 defect A).

Verify that the path-style 4-segment endpoint reconstructs individual segments into a canonical ID before validation.

T268 (NR068 ?3 Option 1A): Reconstruct canonical IDs from path segments before validation.
Canonical format per D013 ?3-1:
  group_canonical = {project}-{module}-{group}
  doc_canonical = {project}-{module}-{group}-{doc}

Test scenarios (NR068 ?4 regression protection):
1. GET /document/test/__ALL__/0001/R0001 ? 200 or 404 (not 422) ? original bug scenario
2. GET /document/{canonical_doc_id} ? 200 or 404 (canonical 1-segment endpoint unaffected)
3. Invalid non-canonical segments ? 422 (validator behavior preserved)
4. Existing tests (test_t261_id_validators.py, etc.) all pass
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


def _make_test_app():
    """Create a test FastAPI app (DB / auth mock)."""
    from fastapi import FastAPI
    app = FastAPI()

    from modules.flow_gate.api.v1 import document_routes
    app.include_router(document_routes.router)

    return app


class TestPathStyleCanonicalReconstruction:
    """T268: path-style segments → canonical ID reconstruction."""

    def setup_method(self):
        from starlette.testclient import TestClient

        app = _make_test_app()
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_scenario_1_path_style_segments_no_422(self):
        """Scenario 1: GET /document/test/__ALL__/0001/R0001 ? 200 or 404 (not 422).
        
        Original NR068 defect A: segments were passed directly to the canonical validator, causing 422.
        After the T268 fix: reconstruct segments first, then validate ? no 422.
        """
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            with patch(
                "modules.flow_gate.api.v1.document_routes.db_docs.get_by_id",
                return_value=None,  # Document not found
            ):
                resp = self.client.get(
                    "/api/v1/document/test/__ALL__/0001/R0001",
                    headers={"Authorization": "Bearer tok"},
                )
        # Expected: 404 (document not found) or 200 (found)
        # NOT 422 (validation error)
        assert resp.status_code in (200, 404), (
            f"Scenario 1 failed: expected 200/404, got {resp.status_code}. "
            f"If 422, segments were passed to the canonical validator unchanged."
        )

    def test_scenario_1b_path_style_with_found_document(self):
        """Scenario 1B: return normally when the document exists."""
        mock_doc = {
            "doc_id": "test-__ALL__-0001-R0001",
            "type_code": "R",
            "title": "Test Document",
            "status": "draft",
            "revision_no": 1,
            "owner_id": "u1",
            "triggered_by": None,
            "group_id": "test-__ALL__-0001",
            "project_id": "test",
            "module": "__ALL__",
            "file_path": None,
            "created_at": "2026-05-18T00:00:00",
            "updated_at": "2026-05-18T00:00:00",
        }
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            with patch(
                "modules.flow_gate.api.v1.document_routes.db_docs.get_by_id",
                return_value=mock_doc,
            ), patch(
                "modules.flow_gate.api.v1.document_routes.get_answers_for_document",
                return_value=[],
            ):
                resp = self.client.get(
                    "/api/v1/document/test/__ALL__/0001/R0001",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200, (
            f"Scenario 1B failed: expected 200, got {resp.status_code}"
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["doc_id"] == "test-__ALL__-0001-R0001"

    def test_scenario_2_canonical_1segment_endpoint_unchanged(self):
        """Scenario 2: GET /document/{canonical_doc_id} endpoint is unaffected (regression protection).
        
        The canonical single-ID endpoint should still work normally.
        """
        mock_doc = {
            "doc_id": "test-__ALL__-0001-R0001",
            "type_code": "R",
            "title": "Test Document",
            "status": "draft",
            "revision_no": 1,
            "owner_id": "u1",
            "triggered_by": None,
            "group_id": "test-__ALL__-0001",
            "project_id": "test",
            "module": "__ALL__",
            "file_path": None,
            "created_at": "2026-05-18T00:00:00",
            "updated_at": "2026-05-18T00:00:00",
        }
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            with patch(
                "modules.flow_gate.api.v1.document_routes.db_docs.get_by_id",
                return_value=mock_doc,
            ), patch(
                "modules.flow_gate.api.v1.document_routes.get_answers_for_document",
                return_value=[],
            ):
                resp = self.client.get(
                    "/api/v1/document/test-__ALL__-0001-R0001",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200, (
            f"Scenario 2 failed: canonical 1-segment endpoint affected. "
            f"Expected 200, got {resp.status_code}"
        )
        body = resp.json()
        assert body["ok"] is True
        assert body["doc_id"] == "test-__ALL__-0001-R0001"

    def test_scenario_2b_canonical_1segment_invalid_returns_422(self):
        """Scenario 2B: invalid format on the canonical 1-segment endpoint ? 422."""
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            resp = self.client.get(
                "/api/v1/document/invalid-format",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422, (
            f"Scenario 2B failed: canonical validation should still work. "
            f"Expected 422, got {resp.status_code}"
        )

    def test_scenario_3_invalid_segments_returns_422(self):
        """Scenario 3: invalid non-canonical segments ? 422 (validator behavior preserved).
        
        Example: uppercase project, malformed group, lowercase type code in doc, etc.
        """
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            # project with uppercase (invalid)
            resp = self.client.get(
                "/api/v1/document/TestProject/__ALL__/0001/R0001",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422, (
            f"Scenario 3 failed: invalid project (uppercase) → expected 422, "
            f"got {resp.status_code}"
        )

    def test_scenario_3b_invalid_segment_group_returns_422(self):
        """Scenario 3B: group segment is not numeric-only ? validator rejects after reconstruction."""
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            # group with letters (invalid numeric)
            resp = self.client.get(
                "/api/v1/document/test/__ALL__/001a/R0001",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422, (
            f"Scenario 3B failed: invalid group seq (001a) → expected 422, "
            f"got {resp.status_code}"
        )

    def test_scenario_3c_invalid_segment_doc_returns_422(self):
        """Scenario 3C: doc segment has a lowercase type code ? validator rejects after reconstruction."""
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            # doc type code lowercase (invalid)
            resp = self.client.get(
                "/api/v1/document/test/__ALL__/0001/r0001",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422, (
            f"Scenario 3C failed: invalid doc type (lowercase) → expected 422, "
            f"got {resp.status_code}"
        )

    def test_scenario_4_module_not_all_returns_400(self):
        """Scenario 4: module != __ALL__ ? 400.
        
        The current implementation only supports __ALL__.
        """
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            resp = self.client.get(
                "/api/v1/document/test/other-module/0001/R0001",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 400, (
            f"Scenario 4 failed: module != __ALL__ → expected 400, "
            f"got {resp.status_code}"
        )

    def test_scenario_5_korean_characters_in_segments(self):
        """Scenario 5: Korean project/module/etc. are allowed (SLUG_CHARS support)."""
        mock_doc = {
            "doc_id": "myproject-__ALL__-0001-R0001",
            "type_code": "R",
            "title": "Test Document",
            "status": "draft",
            "revision_no": 1,
            "owner_id": "u1",
            "triggered_by": None,
            "group_id": "myproject-__ALL__-0001",
            "project_id": "myproject",
            "module": "__ALL__",
            "file_path": None,
            "created_at": "2026-05-18T00:00:00",
            "updated_at": "2026-05-18T00:00:00",
        }
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            with patch(
                "modules.flow_gate.api.v1.document_routes.db_docs.get_by_id",
                return_value=mock_doc,
            ), patch(
                "modules.flow_gate.api.v1.document_routes.get_answers_for_document",
                return_value=[],
            ):
                resp = self.client.get(
                    "/api/v1/document/myproject/__ALL__/0001/R0001",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200, (
            f"Scenario 5 failed: Korean project names must be supported. "
            f"Expected 200, got {resp.status_code}"
        )
        body = resp.json()
        assert body["doc_id"] == "myproject-__ALL__-0001-R0001"
