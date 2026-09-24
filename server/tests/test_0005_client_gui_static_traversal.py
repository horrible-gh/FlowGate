"""Regression coverage for the encoded path-traversal fix in the `/app` and legacy
static-file catch-all (server/routers/main.py `_safe_dist_file` / `_serve_client`).

Before the fix, the catch-all joined the URL-decoded `full_path` straight onto
`_CLIENT_GUI_DIST` / `_CLIENT_DIST` and served whatever `FileResponse` found there.
Starlette's `{full_path:path}` converter hands the route the already-decoded value, so
a request for `/app/%2e%2e/%2e%2e/<file outside dist>` arrived as the literal string
"app/../../<file outside dist>" — joining that onto the dist dir and never resolving
`..`/symlinks let the handler read and return files outside the built SPA (e.g.
server/.env). `_safe_dist_file` closes this by resolving the joined path with
`os.path.realpath` and rejecting anything that lands outside the dist directory.
"""
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

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))
_REPO_ROOT = _SERVER_DIR.parent

# The SPA catch-all route in routers/main.py is only registered (at import time) when
# at least one of client/dist or client_gui/dist already exists on disk. A fresh
# checkout that hasn't run either build.sh yet would otherwise silently skip every test
# below (the route wouldn't exist to monkeypatch). Guarantee registration by pre-seeding
# a throwaway client_gui/dist before the first import — the path is gitignored
# (.gitignore: /client_gui/dist/), so this never shows up as repo pollution, and it is
# removed again immediately after import regardless of whether we created it.
_CLIENT_GUI_DIST_PATH = _REPO_ROOT / "client_gui" / "dist"
_CLIENT_DIST_PATH = _REPO_ROOT / "client" / "dist"
_seeded_client_gui_dist = False
if "routers.main" not in sys.modules and not _CLIENT_GUI_DIST_PATH.is_dir() and not _CLIENT_DIST_PATH.is_dir():
    (_CLIENT_GUI_DIST_PATH / "assets").mkdir(parents=True, exist_ok=True)
    (_CLIENT_GUI_DIST_PATH / "index.html").write_text("<html>import-time fixture</html>", encoding="utf-8")
    _seeded_client_gui_dist = True

from fastapi.testclient import TestClient  # noqa: E402

import routers.main as main_module  # noqa: E402

if _seeded_client_gui_dist:
    shutil.rmtree(_CLIENT_GUI_DIST_PATH, ignore_errors=True)

pytestmark = pytest.mark.skipif(
    not (main_module._client_dist_exists or main_module._client_gui_dist_exists),
    reason=(
        "neither client/dist nor client_gui/dist existed when routers.main was first "
        "imported in this test session, so the SPA catch-all route was never registered "
        "(it is defined inside `if _client_dist_exists or _client_gui_dist_exists:`)."
    ),
)


# ── Unit coverage: _safe_dist_file/_dist_ready always run, independent of route state ──

def test_dist_ready_true_only_with_an_index_html_present(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()

    assert main_module._dist_ready(str(dist)) is False

    (dist / "index.html").write_text("<html></html>", encoding="utf-8")

    assert main_module._dist_ready(str(dist)) is True


def test_dist_ready_false_for_a_missing_directory(tmp_path):
    assert main_module._dist_ready(str(tmp_path / "never-created")) is False


def test_safe_dist_file_allows_a_real_file_inside_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets" / "app.js").write_text("ok", encoding="utf-8")
    dist_real = os.path.realpath(str(dist))

    result = main_module._safe_dist_file(dist_real, "assets/app.js")

    assert result == os.path.realpath(str(dist / "assets" / "app.js"))


@pytest.mark.parametrize(
    "relative",
    [
        "../secret.txt",
        "../../secret.txt",
        "assets/../../secret.txt",
        "a/b/../../../secret.txt",
    ],
)
def test_safe_dist_file_rejects_traversal_outside_dist(tmp_path, relative):
    dist = tmp_path / "app" / "dist"
    dist.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("TOP-SECRET-OUTSIDE-DIST", encoding="utf-8")
    dist_real = os.path.realpath(str(dist))

    assert main_module._safe_dist_file(dist_real, relative) is None


# ── Integration coverage: the real /app and legacy catch-all route, through TestClient ──

@pytest.fixture
def app_dist_fixture(tmp_path, monkeypatch):
    """Point the /app dist at a throwaway fixture dir with a sibling secret file."""
    dist = tmp_path / "client_gui_dist"
    (dist / "assets").mkdir(parents=True)
    index_html = "<html><body>app-spa-shell-sentinel</body></html>"
    (dist / "index.html").write_text(index_html, encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"ICO-BYTES")
    outside_secret = tmp_path / "server.env"
    outside_secret.write_text("SECRET_KEY=leaked-if-this-is-returned", encoding="utf-8")

    dist_real = os.path.realpath(str(dist))
    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST", str(dist))
    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST_REAL", dist_real)
    monkeypatch.setattr(main_module, "_client_gui_dist_exists", True)
    return {"dist": dist, "index_html": index_html, "outside_secret": outside_secret}


@pytest.fixture
def legacy_dist_fixture(tmp_path, monkeypatch):
    """Point the legacy (v0.1) dist at a throwaway fixture dir with a sibling secret file."""
    dist = tmp_path / "client_dist"
    dist.mkdir(parents=True)
    index_html = "<html><body>legacy-spa-shell-sentinel</body></html>"
    (dist / "index.html").write_text(index_html, encoding="utf-8")
    outside_secret = tmp_path / "server.env"
    outside_secret.write_text("SECRET_KEY=leaked-if-this-is-returned", encoding="utf-8")

    dist_real = os.path.realpath(str(dist))
    monkeypatch.setattr(main_module, "_CLIENT_DIST", str(dist))
    monkeypatch.setattr(main_module, "_CLIENT_DIST_REAL", dist_real)
    monkeypatch.setattr(main_module, "_client_dist_exists", True)
    return {"dist": dist, "index_html": index_html, "outside_secret": outside_secret}


def _encode_dot_segments(relative: str) -> str:
    """Turn a `os.path.relpath`-style string into a URL path with `..` percent-encoded
    (%2e%2e), mirroring what a browser/attacker sends and Starlette decodes back to
    literal ".." before handing it to the {full_path:path} converter."""
    segments = relative.replace("\\", "/").split("/")
    return "/".join("%2e%2e" if seg == ".." else seg for seg in segments)


def test_app_catch_all_rejects_encoded_traversal_outside_dist(app_dist_fixture):
    client = TestClient(main_module.app)
    rel = os.path.relpath(str(app_dist_fixture["outside_secret"]), start=str(app_dist_fixture["dist"]))
    resp = client.get(f"/app/{_encode_dot_segments(rel)}")

    assert resp.status_code == 200
    assert "leaked-if-this-is-returned" not in resp.text
    assert resp.text == app_dist_fixture["index_html"]


def test_app_catch_all_404s_cleanly_on_a_docker_fallback_empty_dist(tmp_path, monkeypatch):
    """Mirrors the Dockerfile's client_gui build-failure fallback: the stage still
    creates client_gui/dist (`mkdir -p dist`) so the runtime stage's COPY doesn't fail,
    but it has no index.html. /app must 404 like a genuinely-absent build, not error
    trying to FileResponse a file that was never written."""
    dist = tmp_path / "client_gui_dist_empty"
    dist.mkdir(parents=True)

    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST", str(dist))
    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST_REAL", os.path.realpath(str(dist)))
    # Recomputed with the real production helper, not hardcoded — this is what
    # startup would actually derive for this exact directory state.
    monkeypatch.setattr(main_module, "_client_gui_dist_exists", main_module._dist_ready(str(dist)))

    client = TestClient(main_module.app)
    resp = client.get("/app/")

    assert resp.status_code == 404


def test_app_catch_all_still_serves_a_real_file_inside_dist(app_dist_fixture):
    client = TestClient(main_module.app)
    resp = client.get("/app/favicon.ico")

    assert resp.status_code == 200
    assert resp.content == b"ICO-BYTES"


def test_legacy_catch_all_rejects_encoded_traversal_outside_dist(legacy_dist_fixture):
    client = TestClient(main_module.app)
    rel = os.path.relpath(str(legacy_dist_fixture["outside_secret"]), start=str(legacy_dist_fixture["dist"]))
    resp = client.get(f"/{_encode_dot_segments(rel)}")

    assert resp.status_code == 200
    assert "leaked-if-this-is-returned" not in resp.text
    assert resp.text == legacy_dist_fixture["index_html"]
