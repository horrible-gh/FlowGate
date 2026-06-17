from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.storage import paths as storage_paths


TEST_PROJECT_ID = "proj-t509"
TEST_ROOT = Path(__file__).resolve().parent / "_scratch_t509_src"


def _prepare_root() -> Path:
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    return TEST_ROOT


def _client(monkeypatch) -> TestClient:
    root = _prepare_root()
    monkeypatch.setattr(db_projects, "get_by_id", lambda project_id: {"project_name": "t509-project"} if project_id == TEST_PROJECT_ID else None)
    monkeypatch.setattr(db_projects, "get_settings", lambda _project_id: {"branch": "main"})
    monkeypatch.setattr(storage_paths, "src_root", lambda _project_name, _branch: root)
    return TestClient(app)


def teardown_module(_module) -> None:
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


def test_src_content_get_and_head_support_empty_file(monkeypatch):
    client = _client(monkeypatch)
    file_path = TEST_ROOT / "docs" / "empty.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("", encoding="utf-8")

    get_res = client.get(f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content", params={"path": "docs/empty.md"})
    assert get_res.status_code == 200
    assert get_res.text == ""

    head_res = client.head(f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content", params={"path": "docs/empty.md"})
    assert head_res.status_code == 200
    assert head_res.headers.get("content-length") == "0"


def test_src_content_patch_updates_existing_file(monkeypatch):
    client = _client(monkeypatch)
    file_path = TEST_ROOT / "docs" / "editable.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("before", encoding="utf-8")

    patch_res = client.patch(
        f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content",
        params={"path": "docs/editable.md"},
        json={"content": "after"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json() == {"path": "docs/editable.md", "content_length": 5}
    assert file_path.read_text(encoding="utf-8") == "after"

    get_res = client.get(f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content", params={"path": "docs/editable.md"})
    assert get_res.status_code == 200
    assert get_res.text == "after"


def test_src_content_patch_returns_404_for_missing_file(monkeypatch):
    client = _client(monkeypatch)

    patch_res = client.patch(
        f"/flowgate/api/v1/projects/{TEST_PROJECT_ID}/files/src-content",
        params={"path": "docs/missing.md"},
        json={"content": "after"},
    )
    assert patch_res.status_code == 404
    assert patch_res.json()["detail"] == "Not found"
