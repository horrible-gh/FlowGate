"""T261 ? unit tests for id_validators + router integration tests (verify 422).

Unit: validate_project_id / validate_group_id / validate_doc_id
Integration: GET /document/{doc_id}, GET /project/{p}/source-path ? invalid formats return 422
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.utils.id_validators import (
    validate_project_id,
    validate_group_id,
    validate_doc_id,
    PROJECT_ID,
    GROUP_ID,
    DOC_ID,
)


# ?? Unit tests ?????????????????????????????????????????????????????????????????

class TestValidateProjectId:
    # positive
    def test_simple_ascii(self):
        assert validate_project_id("myproject") == "myproject"

    def test_with_hyphen(self):
        assert validate_project_id("my-project") == "my-project"

    def test_with_underscore(self):
        assert validate_project_id("my_project") == "my_project"

    def test_with_numbers(self):
        assert validate_project_id("project123") == "project123"

    def test_korean(self):
        assert validate_project_id("my-korean-project") == "my-korean-project"

    def test_mixed(self):
        assert validate_project_id("my-project_v2") == "my-project_v2"

    # negative
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            validate_project_id("")

    def test_uppercase_raises(self):
        with pytest.raises(ValueError):
            validate_project_id("MyProject")

    def test_space_raises(self):
        with pytest.raises(ValueError):
            validate_project_id("my project")

    def test_group_id_format_raises(self):
        # group_id format does not qualify as a project_id
        with pytest.raises(ValueError):
            validate_project_id("proj-__ALL__-0001")

    def test_doc_id_format_raises(self):
        with pytest.raises(ValueError):
            validate_project_id("proj-__ALL__-0001-NR0001")


class TestValidateGroupId:
    # positive
    def test_all_module(self):
        assert validate_group_id("myproject.none.0001") == "myproject.none.0001"

    def test_specific_module(self):
        assert validate_group_id("myproject.server.0001") == "myproject.server.0001"

    def test_korean_project(self):
        assert validate_group_id("my-korean-project.none.0042") == "my-korean-project.none.0042"

    def test_seq_4digits(self):
        assert validate_group_id("proj.none.9999") == "proj.none.9999"

    # negative
    def test_project_id_format_raises(self):
        with pytest.raises(ValueError):
            validate_group_id("myproject")

    def test_no_seq_raises(self):
        with pytest.raises(ValueError):
            validate_group_id("myproject-__ALL__")

    def test_seq_3digits_raises(self):
        with pytest.raises(ValueError):
            validate_group_id("myproject-__ALL__-001")

    def test_doc_id_format_raises(self):
        with pytest.raises(ValueError):
            validate_group_id("proj-__ALL__-0001-NR0001")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            validate_group_id("")


class TestValidateDocId:
    # positive
    def test_all_module(self):
        val = "myproject.none.0001.0001-NR"
        assert validate_doc_id(val) == val

    def test_specific_module(self):
        val = "myproject.server.0001.0042-D"
        assert validate_doc_id(val) == val

    def test_korean_project(self):
        val = "my-korean-project.none.0001.0001-R"
        assert validate_doc_id(val) == val

    def test_multi_char_type(self):
        val = "proj.none.0001.0010-DS"
        assert validate_doc_id(val) == val

    # negative
    def test_group_id_format_raises(self):
        with pytest.raises(ValueError):
            validate_doc_id("proj-__ALL__-0001")

    def test_project_id_format_raises(self):
        with pytest.raises(ValueError):
            validate_doc_id("myproject")

    def test_lowercase_type_raises(self):
        with pytest.raises(ValueError):
            validate_doc_id("proj-__ALL__-0001-nr0001")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            validate_doc_id("")

    def test_legacy_double_dash_raises(self):
        # reject legacy format (--)
        with pytest.raises(ValueError):
            validate_doc_id("myproject--NR0001")


# ?? Router integration tests ????????????????????????????????????????????????

def _make_test_app():
    """Create a test FastAPI app (DB / auth mock)."""
    from fastapi import FastAPI
    app = FastAPI()

    from modules.flow_gate.api.v1 import document_routes, project_routes
    app.include_router(document_routes.router)
    app.include_router(project_routes.router)

    return app


class TestDocumentRouteValidator:
    """GET /document/{doc_id} ? invalid format ? 422."""

    def setup_method(self):
        from starlette.testclient import TestClient

        app = _make_test_app()
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_invalid_doc_id_returns_422(self):
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            resp = self.client.get(
                "/api/v1/document/badformat",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422

    def test_legacy_double_dash_doc_id_returns_422(self):
        with patch(
            "modules.flow_gate.api.v1.document_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            resp = self.client.get(
                "/api/v1/document/proj--NR0001",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422


class TestProjectRouteValidator:
    """GET /project/{p}/source-path ? invalid format ? 422."""

    def setup_method(self):
        from starlette.testclient import TestClient
        app = _make_test_app()
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_uppercase_project_id_returns_422(self):
        with patch(
            "modules.flow_gate.api.v1.project_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            resp = self.client.get(
                "/api/v1/project/MyProject/source-path",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422

    def test_group_id_format_as_project_id_returns_422(self):
        with patch(
            "modules.flow_gate.api.v1.project_routes.verify_bearer",
            return_value={"user_id": "u1"},
        ):
            resp = self.client.get(
                "/api/v1/project/proj-__ALL__-0001/source-path",
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 422
