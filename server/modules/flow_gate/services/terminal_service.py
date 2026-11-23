"""Terminal system settings and shell bundle installation."""
from __future__ import annotations

import atexit
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import uuid

from modules.flow_gate.db import system_settings
from . import terminal_bundle as bundle, terminal_download as download, terminal_sessions
from .terminal_errors import BundleError
from .terminal_lock import installation_lock

SHELL_TEST_SECONDS = 15
SHELL_TEST_SCRIPT = '''import sys
from modules.flow_gate.services import terminal_pty as pty
bundle_path, cwd = sys.argv[1], sys.argv[2]
argv, env = pty.default_shell_argv(), pty.shell_env()
p = None
try:
    if pty.IS_WINDOWS:
        p = pty.WindowsPty(argv, cwd, env, 80, 24, bundle_path=bundle_path)
    else:
        p = pty.open_pty(argv, cwd, env, 80, 24)
    if not p.pid:
        raise RuntimeError("Shell did not start")
finally:
    if p is not None:
        p.terminate()
'''


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def job_path(job_id, suffix=".json"):
    return bundle.root() / "jobs" / (job_id + suffix)


def kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
    else:
        import psutil
        for child in psutil.Process(proc.pid).children(recursive=True):
            try:
                child.kill()
            except psutil.Error:
                pass
        proc.kill()
    proc.wait(timeout=10)


def shell_test(directory=None, cancel=None):
    """Run in a child interpreter so staged DLLs are never loaded by the server."""
    from . import terminal_pty
    bundle_path = str(directory or bundle.active_path() or "")
    proc = subprocess.Popen([sys.executable, "-c", SHELL_TEST_SCRIPT, bundle_path, str(bundle.root())],
                            cwd=Path(__file__).resolve().parents[3], env=terminal_pty.shell_env(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + SHELL_TEST_SECONDS
    try:
        while proc.poll() is None:
            if cancel is not None:
                download.check_cancel(cancel)
            if time.monotonic() > deadline:
                raise BundleError("terminal_shell_test_failed", "Shell test timed out", 422)
            time.sleep(0.05)
        if proc.returncode:
            raise BundleError("terminal_shell_test_failed", "Shell test failed", 422)
        return {"ok": True, "shell": Path(terminal_pty.default_shell_argv()[0]).name}
    finally:
        if proc.poll() is None:
            kill_tree(proc)


def failure(exc):
    if isinstance(exc, BundleError):
        return exc.code, exc.message
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return "terminal_download_failed", "Installation failed: " + type(exc).__name__
    return "terminal_install_failed", "Installation failed: " + type(exc).__name__


class Cancellation:
    def __init__(self, job_id, event):
        self.marker = job_path(job_id, ".cancel")
        self.event = event

    def is_set(self):
        return self.event.is_set() or self.marker.exists()


class Manager:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.events = {}
        self.threads = {}

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            atomic_json(job_path(job_id), self.jobs[job_id])

    def job(self, job_id):
        if not re.fullmatch(r"ti_[0-9a-f]{32}", job_id):
            raise BundleError("terminal_install_not_found", "Unknown install", 404)
        with self.lock:
            row = self.jobs.get(job_id)
            if row is None:
                try:
                    row = json.loads(job_path(job_id).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise BundleError("terminal_install_not_found", "Unknown install", 404)
                # The CLI may own this job; it is interrupted only if the lock is free.
                if row["status"] in ("queued", "running"):
                    try:
                        with installation_lock(bundle.root()):
                            row.update(status="failed", error_code="terminal_install_interrupted",
                                       message="Retry to resume partial download")
                    except BundleError:
                        pass
            return copy.deepcopy(row)

    def latest(self):
        files = list((bundle.root() / "jobs").glob("ti_*.json"))
        if not files:
            return None
        return self.job(max(files, key=lambda p: p.stat().st_mtime_ns).stem)

    def status(self):
        required = bundle.required()
        info = None
        if required:
            try:
                size = bundle.specification()["size_bytes"]
            except BundleError:
                size = 0
            info = {"name": bundle.CATALOG["name"], "version": bundle.CATALOG["version"], "size_bytes": size}
        return {"platform": "windows" if required else sys.platform,
                "bundle_required": required,
                "installed": bundle.installed(),
                "enabled": terminal_sessions.terminal_enabled(),
                "open_sessions": terminal_sessions.manager.open_count(),
                "bundle": info,
                "install": self.latest()}

    def install(self, from_dir=None):
        if not bundle.required():
            raise BundleError("terminal_bundle_not_required", "No bundle is required on this OS", 409)
        spec = bundle.specification()
        with self.lock, ExitStack() as stack:
            # Taken before 202 so a running CLI install conflicts immediately.
            stack.enter_context(installation_lock(bundle.root()))
            if terminal_sessions.terminal_enabled():
                raise BundleError("terminal_enabled", "Turn off the terminal before installing", 409)
            job_id = "ti_" + uuid.uuid4().hex
            self.jobs[job_id] = dict(install_id=job_id, status="queued", stage="download", progress=0.0,
                                     bytes_done=0, bytes_total=spec["size_bytes"], error_code=None, message=None)
            self.events[job_id] = threading.Event()
            self._update(job_id)
            held = stack.pop_all()
            worker = threading.Thread(target=self._worker, args=(job_id, spec, from_dir, held), name=job_id, daemon=True)
            self.threads[job_id] = worker
            try:
                worker.start()
            except BaseException:
                held.close()
                raise
            return self.job(job_id)

    def cancel(self, job_id):
        with self.lock:
            row = self.job(job_id)
            if row["status"] in ("queued", "running"):
                atomic_json(job_path(job_id, ".cancel"), True)
                if job_id in self.events:
                    self.events[job_id].set()
            return row

    def _worker(self, job_id, spec, from_dir, held_lock):
        staging = bundle.root() / "staging" / job_id
        cancel = Cancellation(job_id, self.events[job_id])
        reported = 0.0

        def progress(done, total):
            nonlocal reported
            now = time.monotonic()
            if done == total or now - reported > 0.25:
                self._update(job_id, bytes_done=done, progress=min(0.6, done / total * 0.6))
                reported = now

        try:
            self._update(job_id, status="running")
            archive = download.fetch(spec, bundle.root() / "downloads", cancel, progress,
                                     Path(from_dir).resolve() if from_dir else None)
            self._update(job_id, stage="verify", progress=0.65)
            if not download.verify(archive, spec):
                archive.unlink(missing_ok=True)
                raise BundleError("terminal_sha256_mismatch", "Bundle failed SHA-256 verification", 422)
            download.check_cancel(cancel)
            self._update(job_id, stage="extract", progress=0.7)
            staging.mkdir(parents=True, exist_ok=True)
            download.extract(archive, staging, cancel)
            self._update(job_id, stage="test", progress=0.85)
            shell_test(staging, cancel)
            with self.lock:
                download.check_cancel(cancel)
                self._update(job_id, stage="apply", progress=0.95)
                destination = bundle.root() / "bundles" / spec["sha256"][:16]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    os.replace(staging, destination)
                atomic_json(bundle.root() / "installed.json",
                            {"sha256": spec["sha256"], "version": bundle.CATALOG["version"]})
                self._update(job_id, status="done", progress=1.0)
        except Exception as exc:
            if cancel.is_set():
                self._update(job_id, status="cancelled", error_code="terminal_install_cancelled",
                             message="Installation cancelled")
            else:
                code, message = failure(exc)
                self._update(job_id, status="failed", error_code=code, message=message)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            held_lock.close()

    def enable(self, enabled, user_id=None):
        with self.lock, installation_lock(bundle.root()):
            if enabled and not bundle.installed():
                raise BundleError("terminal_not_installed", "Install shell components first", 409)
            sessions = terminal_sessions.manager
            with sessions._lock:
                system_settings.set_value("terminal.enabled", json.dumps(enabled), value_type="boolean",
                                          updated_by=user_id)
            closed = 0 if enabled else sessions.disable_all()
            return {"enabled": enabled, "closed_sessions": closed}

    def remove(self):
        with self.lock, installation_lock(bundle.root()):
            if terminal_sessions.terminal_enabled():
                raise BundleError("terminal_enabled", "Turn off the terminal before deleting", 409)
            if not bundle.required():
                raise BundleError("terminal_bundle_not_required", "No bundle is required on this OS", 409)
            bundle.prune_runtime()
            (bundle.root() / "installed.json").unlink(missing_ok=True)
            for name in ("bundles", "downloads", "staging"):
                path = bundle.root() / name
                if path.exists():
                    shutil.rmtree(path)
            return {"ok": True}

    def test(self):
        with self.lock, installation_lock(bundle.root()):
            if not bundle.installed():
                return {"ok": False, "error_code": "terminal_not_installed", "message": "Install shell components first"}
            try:
                return shell_test()
            except Exception as exc:
                return {"ok": False, "error_code": getattr(exc, "code", "terminal_shell_test_failed"),
                        "message": getattr(exc, "message", "Shell test failed")}

    def shutdown(self):
        for event in list(self.events.values()):
            event.set()


manager = Manager()
atexit.register(manager.shutdown)
