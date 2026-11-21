"""Regression coverage for the encoded path-traversal fix in the `/app` static-file
catch-all (server/routers/main.py `_safe_dist_file` / `_serve_client`).

Before the fix, the catch-all joined the URL-decoded `full_path` straight onto
the dist directory and served whatever `FileResponse` found there.
Starlette's `{full_path:path}` converter hands the route the already-decoded value, so
a request for `/app/%2e%2e/%2e%2e/<file outside dist>` arrived as the literal string
"app/../../<file outside dist>" — joining that onto the dist dir and never resolving
`..`/symlinks let the handler read and return files outside the built SPA (e.g.
server/.env). `_safe_dist_file` closes this by resolving the joined path with
`os.path.realpath` and rejecting anything that lands outside the dist directory.
"""
from __future__ import annotations

import os
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

from fastapi.testclient import TestClient  # noqa: E402

import routers.main as main_module  # noqa: E402

# the catch-all route is registered whether or not a client build
# exists (the v0.1 /main and /settings redirects must work either way), so these tests no
# longer need a pre-seeded dist to run; each integration test points the dist at a fixture.


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


# ── Integration coverage: the real /app catch-all route, through TestClient ──

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


def test_app_catch_all_404s_cleanly_on_an_empty_dist(tmp_path, monkeypatch):
    """A client_gui/dist without an index.html (half-written or emptied build): /app must
    404 like a genuinely-absent build, not error trying to FileResponse a file that was
    never written."""
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
