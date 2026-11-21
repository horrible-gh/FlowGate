"""v0.1 (client) is gone and client_gui is the
only client.

- `/` and `/login` (with `?invite=` / `?next=`) return client_gui's login.html — the v0.2
  sign-in page — and any other unknown non-API path does too, as v0.1's index.html did.
- v0.1's `/main` and `/settings` addresses (and everything below them) redirect into /app
  following the the requirement table, whether or not a client build exists.
- API prefixes still 404 as JSON and /app keeps serving the SPA shell.
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

INDEX_HTML = "<html><body>app-spa-shell-sentinel</body></html>"
LOGIN_HTML = "<html><body>v02-login-page-sentinel</body></html>"


@pytest.fixture
def dist(tmp_path, monkeypatch):
    dist = tmp_path / "client_gui_dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "login.html").write_text(LOGIN_HTML, encoding="utf-8")
    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST", str(dist))
    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST_REAL", os.path.realpath(str(dist)))
    monkeypatch.setattr(main_module, "_client_gui_dist_exists", True)
    return dist


@pytest.fixture
def no_dist(tmp_path, monkeypatch):
    missing = tmp_path / "never-built"
    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST", str(missing))
    monkeypatch.setattr(main_module, "_CLIENT_GUI_DIST_REAL", os.path.realpath(str(missing)))
    monkeypatch.setattr(main_module, "_client_gui_dist_exists", False)


@pytest.fixture
def client():
    return TestClient(main_module.app)


# ── Sign-in page ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "url",
    ["/", "/login", "/login/", "/login?invite=abc123", "/login?next=%2Fapp%2Fprojects", "/?next=%2Fapp"],
)
def test_root_and_login_serve_the_v02_login_page(dist, client, url):
    resp = client.get(url, follow_redirects=False)

    assert resp.status_code == 200
    assert resp.text == LOGIN_HTML
    assert resp.headers["cache-control"] == "no-cache, max-age=0"


def test_unknown_non_api_path_falls_back_to_the_login_page(dist, client):
    resp = client.get("/some/old/bookmark", follow_redirects=False)

    assert resp.status_code == 200
    assert resp.text == LOGIN_HTML


def test_login_page_404s_when_the_build_has_no_login_html(dist, client):
    (dist / "login.html").unlink()

    assert client.get("/login").status_code == 404
    assert client.get("/").status_code == 404


def test_login_page_404s_without_a_build(no_dist, client):
    assert client.get("/login").status_code == 404
    assert client.get("/").status_code == 404


def test_app_still_serves_the_spa_shell_not_the_login_page(dist, client):
    for url in ("/app", "/app/", "/app/projects/p1"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.text == INDEX_HTML


def test_login_html_is_not_reachable_as_a_raw_app_file(dist, client):
    # /app/login.html is an .html request under /app: the SPA shell answers, never the raw file.
    resp = client.get("/app/login.html")

    assert resp.status_code == 200
    assert resp.text == INDEX_HTML


@pytest.mark.parametrize("url", ["/flowgate/api/v1/does-not-exist", "/api/does-not-exist"])
def test_api_prefixes_still_404_as_json(dist, client, url):
    resp = client.get(url, follow_redirects=False)

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


# ── v0.1 address redirects ────────────────────────────────────────

REDIRECTS = [
    ("/main", "/app"),
    ("/main/", "/app"),
    ("/main/foo", "/app"),
    ("/main/groups/abc/docs", "/app"),
    ("/settings", "/app/settings"),
    ("/settings/", "/app/settings"),
    ("/settings/security", "/app/settings/me/security"),
    ("/settings/security/sessions", "/app/settings/me/security"),
    ("/settings/users", "/app/settings/system/users"),
    ("/settings/users/123", "/app/settings/system/users"),
    ("/settings/projects", "/app/settings/system/projects"),
    ("/settings/projects/p-1", "/app/settings/system/projects"),
    ("/settings/system/agents", "/app/settings/system/agents"),
    ("/settings/system/agents/a-1", "/app/settings/system/agents"),
    ("/settings/system/ai", "/app/ai"),
    ("/settings/system/ai/providers", "/app/ai"),
    ("/settings/system", "/app/settings/system/general"),
    ("/settings/system/env-vars", "/app/settings/system/general"),
    ("/settings/project", "/app/settings/project"),
    ("/settings/project/messages", "/app/settings/project"),
    ("/settings/ai-run-monitor", "/app/settings/me/account"),
    ("/settings/document-types", "/app/settings"),
    ("/settings/anything/else", "/app/settings"),
]


@pytest.mark.parametrize("url,target", REDIRECTS)
def test_v01_addresses_redirect_into_app(dist, client, url, target):
    resp = client.get(url, follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == target


@pytest.mark.parametrize("url,target", [("/main", "/app"), ("/settings/users/9", "/app/settings/system/users")])
def test_v01_redirects_work_without_a_build(no_dist, client, url, target):
    resp = client.get(url, follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == target


def test_v01_redirect_keeps_the_query_string(dist, client):
    resp = client.get("/settings/system/agents?agent=a-1", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/settings/system/agents?agent=a-1"


@pytest.mark.parametrize("url", ["/mainframe", "/settingsx", "/settings-old", "/maintenance"])
def test_lookalike_prefixes_are_not_redirected(dist, client, url):
    resp = client.get(url, follow_redirects=False)

    assert resp.status_code == 200
    assert resp.text == LOGIN_HTML


def test_redirect_table_matches_more_specific_prefixes_first():
    assert main_module._legacy_redirect_target("settings/system/ai") == "/app/ai"
    assert main_module._legacy_redirect_target("settings/system/agents") == "/app/settings/system/agents"
    assert main_module._legacy_redirect_target("settings/system") == "/app/settings/system/general"
    assert main_module._legacy_redirect_target("settings/projects") == "/app/settings/system/projects"
    assert main_module._legacy_redirect_target("settings/project") == "/app/settings/project"
    assert main_module._legacy_redirect_target("login") is None
    assert main_module._legacy_redirect_target("") is None
    assert main_module._legacy_redirect_target("app/settings") is None
