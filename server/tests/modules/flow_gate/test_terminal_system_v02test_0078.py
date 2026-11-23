"""Short terminal system contracts; no network or external server required."""
import hashlib
import json
from pathlib import Path
import threading
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from modules.flow_gate.api.v1 import terminal_routes as routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import terminal_service as service, terminal_bundle as bundle
from modules.flow_gate.services import terminal_download as download, terminal_sessions as sessions
from modules.flow_gate.services.terminal_lock import installation_lock
from modules.flow_gate.services.terminal_errors import BundleError


@pytest.fixture
def env(tmp_path, monkeypatch):
    values = {}
    monkeypatch.setattr(bundle, "root", lambda: tmp_path / "terminal")
    monkeypatch.setattr(service.system_settings, "get_value", lambda k, default=None: values.get(k, default))
    def set_value(key, value, *, value_type, updated_by=None):
        # Match the DB contract so invalid setting types cannot pass silently.
        assert value_type in {"string", "integer", "boolean", "json"}
        if key == "terminal.enabled":
            assert value_type == "boolean"
            assert value in {"true", "false"}
        values[key] = value
    monkeypatch.setattr(service.system_settings, "set_value", set_value)
    mgr = service.Manager()
    monkeypatch.setattr(service, "manager", mgr)
    session_mgr = sessions.TerminalManager(flow_resolver=lambda f: ("project", str(tmp_path)), recorder=lambda *a: None)
    monkeypatch.setattr(sessions, "manager", session_mgr)
    yield mgr, values, session_mgr
    for event in mgr.events.values():
        event.set()
    for thread in mgr.threads.values():
        thread.join(5)
    session_mgr.shutdown()


def client(admin=True):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": admin}
    return TestClient(app)


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/system", None), ("POST", "/system/installs", None),
    ("GET", "/system/installs/ti_x", None), ("DELETE", "/system/installs/ti_x", None),
    ("DELETE", "/system/bundle", None), ("PUT", "/system/enabled", {"enabled": False}),
    ("POST", "/system/test", None),
])
def test_all_system_endpoints_admin_only(env, method, path, body):
    r = client(False).request(method, "/api/v1/terminal" + path, json=body)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "terminal_not_admin"


def test_native_status_enable_disable_and_pending_sessions(env, monkeypatch):
    mgr, values, sessions_mgr = env
    monkeypatch.setattr(bundle, "required", lambda: False)
    c = client()
    status = c.get("/api/v1/terminal/system").json()
    assert status["installed"] and not status["bundle_required"] and status["bundle"] is None
    assert c.put("/api/v1/terminal/system/enabled", json={"enabled": True}).json() == {"enabled": True, "closed_sessions": 0}
    ticket = c.post("/api/v1/terminal/sessions", json={"flow_id": "f"}).json()["ticket"]
    assert c.put("/api/v1/terminal/system/enabled", json={"enabled": False}).json() == {"enabled": False, "closed_sessions": 1}
    assert sessions_mgr.redeem(ticket) is None
    assert c.get("/api/v1/terminal/status").json()["reason"] == "disabled"
    assert c.post("/api/v1/terminal/system/installs").json()["error"]["code"] == "terminal_bundle_not_required"
    assert c.put("/api/v1/terminal/system/enabled", json={"enabled": "true"}).status_code == 422


def test_missing_bundle_cannot_enable(env, monkeypatch):
    monkeypatch.setattr(bundle, "required", lambda: True)
    monkeypatch.setattr(bundle, "active_path", lambda: None)
    r = client().put("/api/v1/terminal/system/enabled", json={"enabled": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "terminal_not_installed"


def wheel(tmp_path):
    directory = tmp_path / "offline"
    directory.mkdir()
    path = directory / "example.whl"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("winpty/__init__.py", "# fake wheel for installer contracts")
    data = path.read_bytes()
    return directory, dict(filename=path.name, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), url="https://invalid.example/wheel")


def test_offline_install_stages_disabled_and_remove(env, monkeypatch, tmp_path):
    mgr, values, _ = env
    directory, spec = wheel(tmp_path)
    monkeypatch.setattr(bundle, "required", lambda: True)
    monkeypatch.setattr(bundle, "specification", lambda: spec)
    seen = []
    monkeypatch.setattr(service, "shell_test", lambda path, cancel: seen.append((path / "winpty/__init__.py").is_file()))
    monkeypatch.setattr(download.urllib.request, "urlopen", lambda *a, **k: pytest.fail("offline made network request"))
    job = mgr.install(directory)
    mgr.threads[job["install_id"]].join(5)
    result = mgr.job(job["install_id"])
    assert result["status"] == "done", result
    assert result["stage"] == "apply" and result["progress"] == 1
    assert seen == [True] and bundle.installed() and not sessions.terminal_enabled()
    assert mgr.latest()["install_id"] == job["install_id"]
    mgr.enable(True)
    with pytest.raises(BundleError) as error:
        mgr.remove()
    assert error.value.code == "terminal_enabled"
    mgr.enable(False)
    assert mgr.remove() == {"ok": True}
    assert not bundle.installed() and (directory / spec["filename"]).exists()


def test_shared_lock_conflict_and_cancellation(env, monkeypatch, tmp_path):
    mgr, _, _ = env
    directory, spec = wheel(tmp_path)
    monkeypatch.setattr(bundle, "required", lambda: True)
    monkeypatch.setattr(bundle, "specification", lambda: spec)
    with installation_lock(bundle.root()):
        with pytest.raises(BundleError) as error:
            mgr.install(directory)
        assert error.value.code == "terminal_install_running"
    reached = threading.Event()
    def blocked(spec, directory, cancel, progress, from_dir):
        reached.set()
        while not cancel.is_set():
            threading.Event().wait(0.01)
        download.check_cancel(cancel)
    monkeypatch.setattr(download, "fetch", blocked)
    job = mgr.install(directory)
    assert reached.wait(2)
    assert service.Manager().job(job["install_id"])["status"] == "running"
    service.Manager().cancel(job["install_id"])
    mgr.threads[job["install_id"]].join(5)
    assert mgr.job(job["install_id"])["status"] == "cancelled"
    assert not bundle.installed()


def test_hash_failure_deletes_partial(env, tmp_path, monkeypatch):
    directory, spec = wheel(tmp_path)
    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, size):
            if hasattr(self, "sent"): return b""
            self.sent = True
            return b"x" * spec["size_bytes"]
    monkeypatch.setattr(download.urllib.request, "urlopen", lambda *a, **k: Response())
    target = tmp_path / "downloads"
    with pytest.raises(BundleError) as error:
        download.fetch(spec, target, threading.Event(), lambda *a: None)
    assert error.value.code == "terminal_sha256_mismatch"
    assert not list(target.iterdir())


def test_download_resumes_valid_range(env, tmp_path, monkeypatch):
    directory, spec = wheel(tmp_path)
    data = (directory / spec["filename"]).read_bytes()
    target = tmp_path / "downloads"
    target.mkdir()
    (target / (spec["filename"] + ".part")).write_bytes(data[:10])
    class Response:
        status = 206
        headers = {"Content-Range": f"bytes 10-{len(data)-1}/{len(data)}"}
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, size):
            if hasattr(self, "sent"): return b""
            self.sent = True
            return data[10:]
    def open_url(request, **kw):
        assert request.get_header("Range") == "bytes=10-"
        return Response()
    monkeypatch.setattr(download.urllib.request, "urlopen", open_url)
    result = download.fetch(spec, target, threading.Event(), lambda *a: None)
    assert result.read_bytes() == data


def test_network_failure_recorded(env, tmp_path, monkeypatch):
    import urllib.error
    mgr, _, _ = env
    _, spec = wheel(tmp_path)
    monkeypatch.setattr(bundle, "required", lambda: True)
    monkeypatch.setattr(bundle, "specification", lambda: spec)
    def fail(*a, **kw): raise urllib.error.URLError("offline")
    monkeypatch.setattr(download.urllib.request, "urlopen", fail)
    job = mgr.install()
    mgr.threads[job["install_id"]].join(5)
    assert mgr.job(job["install_id"])["error_code"] == "terminal_download_failed"


def test_disable_closes_websocket_with_contract(env, monkeypatch):
    mgr, values, session_mgr = env
    monkeypatch.setattr(bundle, "required", lambda: False)
    values["terminal.enabled"] = "true"
    class FakePty:
        def __init__(self, *a): self.done = threading.Event()
        def read(self): self.done.wait(5); return None
        def terminate(self): self.done.set()
        def exit_code(self): return 0
        def wait(self, *a): return 0
        def has_children(self): return False
    session_mgr.pty_factory = FakePty
    session_mgr.card_probe = lambda f: False
    c = client()
    ticket = c.post("/api/v1/terminal/sessions", json={"flow_id": "f"}).json()["ticket"]
    with c.websocket_connect("/api/v1/terminal/ws?ticket=" + ticket) as ws:
        assert ws.receive_json()["type"] == "card_running"
        result = c.put("/api/v1/terminal/system/enabled", json={"enabled": False})
        assert result.json()["closed_sessions"] == 1
        with pytest.raises(WebSocketDisconnect) as error:
            while True: ws.receive_json()
        assert error.value.code == 4403 and error.value.reason == "terminal_disabled"


def test_create_resolution_cannot_race_disable(env):
    _, values, mgr = env
    values["terminal.enabled"] = "true"
    def resolve(flow):
        values["terminal.enabled"] = "false"
        mgr.disable_all()
        return "p", "/tmp"
    mgr.flow_resolver = resolve
    with pytest.raises(sessions.TerminalError) as error:
        mgr.create({"is_admin": True, "user_id": "u"}, "f", 80, 24)
    assert error.value.code == "terminal_disabled" and mgr.open_count() == 0


def test_failed_shell_test_never_publishes(env, monkeypatch, tmp_path):
    mgr, _, _ = env
    directory, spec = wheel(tmp_path)
    monkeypatch.setattr(bundle, "required", lambda: True)
    monkeypatch.setattr(bundle, "specification", lambda: spec)
    def fail(*a):
        raise BundleError("terminal_shell_test_failed", "No shell", 422)
    monkeypatch.setattr(service, "shell_test", fail)
    job = mgr.install(directory)
    mgr.threads[job["install_id"]].join(5)
    assert mgr.job(job["install_id"])["error_code"] == "terminal_shell_test_failed"
    assert not bundle.installed()


def test_archive_traversal_refused(tmp_path):
    archive = tmp_path / "bad.whl"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("../outside", "bad")
    with pytest.raises(BundleError):
        download.extract(archive, tmp_path / "extract", threading.Event())
    assert not (tmp_path / "outside").exists()


def test_retired_dotenv_key_ignored_without_relaxing_other_validation(tmp_path):
    import ast
    from pydantic import model_validator
    from pydantic_settings import BaseSettings, SettingsConfigDict
    path = Path(__file__).resolve().parents[3] / "config.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
    validator = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_ignore_retired_terminal_env")
    ns = {"model_validator": model_validator}
    exec(compile(ast.Module(body=[validator], type_ignores=[]), str(path), "exec"), ns)
    class RetiredSettings(BaseSettings):
        model_config = SettingsConfigDict(extra="forbid")
        _ignore_retired_terminal_env = ns["_ignore_retired_terminal_env"]
    dotenv = tmp_path / ".env"
    dotenv.write_text("terminal_enabled=garbage\n", encoding="utf-8")
    assert RetiredSettings(_env_file=dotenv).model_dump() == {}
    dotenv.write_text("unknown_setting=garbage\n", encoding="utf-8")
    with pytest.raises(ValueError):
        RetiredSettings(_env_file=dotenv)


def test_current_windows_abi_has_catalog_wheel():
    if bundle.required():
        spec = bundle.specification()
        assert spec["sha256"] and spec["size_bytes"] > 0


def test_windows_bundle_real_shell_smoke(env, request):
    import os
    if os.name != "nt" or not any(arg.endswith("::test_windows_bundle_real_shell_smoke") for arg in request.config.args):
        pytest.skip("Opt-in short Windows bundle smoke; downloads pinned wheel")
    from modules.flow_gate.services import terminal_pty
    mgr, _, _ = env
    job = mgr.install()
    mgr.threads[job["install_id"]].join(40)
    result = mgr.job(job["install_id"])
    assert result["status"] == "done", result
    assert not sessions.terminal_enabled()
    pty = terminal_pty.open_pty(terminal_pty.default_shell_argv(), str(bundle.root()), terminal_pty.shell_env(), 80, 24)
    try:
        assert pty.pid > 0
        import winpty
        assert Path(winpty.__file__).is_relative_to(bundle.root())
    finally:
        pty.terminate()
    mgr.remove()
    assert not bundle.installed()
