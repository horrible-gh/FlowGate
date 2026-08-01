"""Regression tests for normalized token scratch paths (flowgate.default.0374)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import token_service


@pytest.fixture
def scratch_service(monkeypatch, tmp_path):
    """Use a deliberately non-normalized storage root and a stable project name."""
    storage_root = tmp_path / "sub" / ".." / "sub"
    monkeypatch.setattr(token_service, "get_storage_root", lambda: storage_root)
    monkeypatch.setattr(
        token_service.db_projects,
        "get_by_id",
        lambda _project_id: {"project_name": "FlowGate"},
    )
    return token_service, storage_root


def test_scratch_dir_resolves_non_normalized_storage_root(scratch_service):
    service, storage_root = scratch_service

    result = service._scratch_dir("project-id", "tok_0374_000001")

    expected = (storage_root / "work" / "FlowGate" / "tok_0374_000001").resolve()
    assert result == expected
    assert ".." not in result.parts


def test_scratch_dir_matches_realpath(scratch_service):
    service, _storage_root = scratch_service

    result = service._scratch_dir("project-id", "tok_0374_000002")

    assert str(result) == os.path.realpath(str(result))


def test_scratch_dir_path_returns_normalized_string(scratch_service):
    service, storage_root = scratch_service

    result = service.scratch_dir_path("project-id", "tok_0374_000003")

    expected = (storage_root / "work" / "FlowGate" / "tok_0374_000003").resolve()
    assert result == str(expected)
    assert result == os.path.realpath(result)