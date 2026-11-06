"""Regression coverage for static-file path traversal and removed v0.2 UI routes."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_SERVER_DIR))
_CLIENT_DIST = _SERVER_DIR.parent / "client" / "dist"
_seeded = False
if "routers.main" not in sys.modules and not _CLIENT_DIST.is_dir():
    _CLIENT_DIST.mkdir(parents=True, exist_ok=True)
    (_CLIENT_DIST / "index.html").write_text("<html>fixture</html>", encoding="utf-8")
    _seeded = True

from fastapi.testclient import TestClient  # noqa: E402
import routers.main as main_module  # noqa: E402

if _seeded:
    shutil.rmtree(_CLIENT_DIST, ignore_errors=True)

pytestmark = pytest.mark.skipif(
    not main_module._client_dist_exists,
    reason="SPA fallback was not registered when routers.main was imported",
)


def test_safe_dist_file_allows_a_file_inside_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app.js").write_text("ok", encoding="utf-8")
    assert main_module._safe_dist_file(str(dist.resolve()), "assets/app.js") == str((dist / "assets" / "app.js").resolve())


@pytest.mark.parametrize("relative", ["../secret.txt", "../../secret.txt", "assets/../../secret.txt"])
def test_safe_dist_file_blocks_traversal(tmp_path, relative):
    dist = tmp_path / "dist"
    dist.mkdir()
    assert main_module._safe_dist_file(str(dist.resolve()), relative) is None


@pytest.fixture
def legacy_dist(tmp_path, monkeypatch):
    dist = tmp_path / "client_dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>legacy-shell</html>", encoding="utf-8")
    (dist / "main.html").write_text("<html>main-shell</html>", encoding="utf-8")
    (dist / "settings.html").write_text("<html>settings-shell</html>", encoding="utf-8")
    outside_secret = tmp_path / "server.env"
    outside_secret.write_text("SECRET_KEY=should-not-leak", encoding="utf-8")
    monkeypatch.setattr(main_module, "_CLIENT_DIST", str(dist))
    monkeypatch.setattr(main_module, "_CLIENT_DIST_REAL", str(dist.resolve()))
    monkeypatch.setattr(main_module, "_client_dist_exists", True)
    return dist, outside_secret


def test_legacy_static_catchall_blocks_encoded_traversal(legacy_dist):
    dist, secret = legacy_dist
    rel = os.path.relpath(str(secret), str(dist))
    encoded = "/".join("%2e%2e" if item == ".." else item for item in rel.split(os.sep))
    resp = TestClient(main_module.app).get(f"/{encoded}")
    assert resp.status_code == 200
    assert "should-not-leak" not in resp.text


@pytest.mark.parametrize("path", ["/app", "/app/", "/app/designer", "/app/assets/fake.js"])
def test_removed_v02_client_returns_404(legacy_dist, path):
    resp = TestClient(main_module.app).get(path)
    assert resp.status_code == 404
