"""System settings and durable, cancellable terminal bundle installation."""
from __future__ import annotations

import atexit
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
import uuid
import urllib.error

from modules.flow_gate.db import system_settings
from . import terminal_bundle as bundle, terminal_download as download, terminal_sessions
from .terminal_errors import BundleError
from .terminal_lock import installation_lock


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def shell_test(directory=None, cancel=None):
    """Test in a child interpreter so staging DLLs never stay loaded in the server."""
    from . import terminal_pty
    argv = terminal_pty.default_shell_argv()
    script = '''import sys
from modules.flow_gate.services import terminal_pty
p = None
try:
    if terminal_pty.IS_WINDOWS:
        p = terminal_pty.WindowsPty(terminal_pty.default_shell_argv(), sys.argv[2], terminal_pty.shell_env(), 80, 24, bundle_path=sys.argv[1])
    else:
        p = terminal_pty.open_pty(terminal_pty.default_shell_argv(), sys.argv[2], terminal_pty.shell_env(), 80, 24)
    if not p.pid:
        raise RuntimeError("Shell did not start")
finally:
    if p is not None:
        p.terminate()
'''
    server = Path(__file__).resolve().parents[3]
    proc = subprocess.Popen([sys.executable, "-c", script, str(directory or bundle.active_path() or ""), str(bundle.root())],
                            cwd=server, env=terminal_pty.shell_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 15
    try:
        while proc.poll() is None:
            if cancel is not None:
                download.check_cancel(cancel)
            if time.monotonic() > deadline:
                raise BundleError("terminal_shell_test_failed", "Shell test timed out", 422)
            time.sleep(0.05)
        if proc.returncode:
            raise BundleError("terminal_shell_test_failed", "Shell test failed", 422)
        return {"ok": True, "shell": Path(argv[0]).name}
    finally:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10)
            else:
                import psutil
                parent = psutil.Process(proc.pid)
                for child in parent.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
                proc.kill()
            proc.wait(timeout=10)


class Manager:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.events = {}
        self.threads = {}

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            atomic_json(bundle.root() / "jobs" / (job_id + ".json"), self.jobs[job_id])

    def job(self, job_id):
        if not re.fullmatch(r"ti_[0-9a-f]{32}", job_id):
            raise BundleError("terminal_install_not_found", "Unknown install", 404)
        with self.lock:
            row = self.jobs.get(job_id)
            if row is None:
                try:
                    row = json.loads((bundle.root() / "jobs" / (job_id + ".json")).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise BundleError("terminal_install_not_found", "Unknown install", 404)
                # A CLI may currently own this job; only mark it interrupted when the
                # shared OS lock is free. Reads never change its stored state.
                if row["status"] in ("queued", "running"):
                    try:
                        with installation_lock(bundle.root()):
                            row.update(status="failed", error_code="terminal_install_interrupted", message="Retry to resume partial download")
                    except BundleError:
                        pass
            return copy.deepcopy(row)

    def latest(self):
        files = list((bundle.root() / "jobs").glob("ti_*.json"))
        if not files:
            return None
        return self.job(max(files, key=lambda p: p.stat().st_mtime_ns).stem)

    def status(self):
        spec = None
        if bundle.required():
            try:
                spec = bundle.specification()
            except BundleError:
                pass
        return {"platform": "windows" if bundle.required() else sys.platform,
                "bundle_required": bundle.required(), "installed": bundle.installed(),
                "enabled": terminal_sessions.terminal_enabled(),
                "open_sessions": terminal_sessions.manager.open_count(),
                "bundle": ({"name": bundle.CATALOG["name"], "version": bundle.CATALOG["version"],
                            "size_bytes": spec["size_bytes"] if spec else 0} if bundle.required() else None),
                "install": self.latest()}

    def install(self, from_dir=None):
        if not bundle.required():
            raise BundleError("terminal_bundle_not_required", "No bundle is required on this OS", 409)
        spec = bundle.specification()
        with self.lock:
            lock = installation_lock(bundle.root())
            lock.__enter__()  # Acquired before 202; web and CLI conflict synchronously.
            try:
                if terminal_sessions.terminal_enabled():
                    raise BundleError("terminal_enabled", "Turn off the terminal before installing", 409)
                job_id = "ti_" + uuid.uuid4().hex
                self.jobs[job_id] = dict(install_id=job_id, status="queued", stage="download", progress=0.0,
                                         bytes_done=0, bytes_total=spec["size_bytes"], error_code=None, message=None)
                self.events[job_id] = threading.Event()
                self._update(job_id)
                worker = threading.Thread(target=self._worker, args=(job_id, spec, from_dir, lock), daemon=True, name=job_id)
                self.threads[job_id] = worker
                worker.start()
            except BaseException:
                lock.__exit__(*sys.exc_info())
                raise
            return self.job(job_id)

    def cancel(self, job_id):
        with self.lock:
            row = self.job(job_id)
            if row["status"] in ("queued", "running"):
                atomic_json(bundle.root() / "jobs" / (job_id + ".cancel"), True)
                if job_id in self.events:
                    self.events[job_id].set()
            return row

    def _worker(self, job_id, spec, from_dir, lock):
        staging = bundle.root() / "staging" / job_id
        event = self.events[job_id]
        class Cancellation:
            def is_set(self):
                return event.is_set() or (bundle.root() / "jobs" / (job_id + ".cancel")).exists()
        cancel = Cancellation()
        try:
            self._update(job_id, status="running")
            last = [0.0]
            def progress(done, total):
                now = time.monotonic()
                if done == total or now - last[0] > 0.25:
                    self._update(job_id, bytes_done=done, progress=min(0.6, done / total * 0.6))
                    last[0] = now
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
            download.check_cancel(cancel)
            # Cancellation and apply linearize on the same manager lock.
            with self.lock:
                download.check_cancel(cancel)
                self._update(job_id, stage="apply", progress=0.95)
                destination = bundle.root() / "bundles" / spec["sha256"][:16]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    os.replace(staging, destination)
                atomic_json(bundle.root() / "installed.json", {"sha256": spec["sha256"], "version": bundle.CATALOG["version"]})
                self._update(job_id, status="done", progress=1.0)
        except Exception as exc:
            if cancel.is_set():
                code, message, status = "terminal_install_cancelled", "Installation cancelled", "cancelled"
            else:
                code = exc.code if isinstance(exc, BundleError) else ("terminal_download_failed" if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)) else "terminal_install_failed")
                message = exc.message if isinstance(exc, BundleError) else "Installation failed: " + type(exc).__name__
                status = "failed"
            self._update(job_id, status=status, error_code=code, message=message)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            lock.__exit__(None, None, None)

    def enable(self, enabled, user_id=None):
        with self.lock, installation_lock(bundle.root()):
            if enabled and not bundle.installed():
                raise BundleError("terminal_not_installed", "Install shell components first", 409)
            mgr = terminal_sessions.manager
            with mgr._lock:
                system_settings.set_value("terminal.enabled", json.dumps(enabled), value_type="bool", updated_by=user_id)
            closed = 0 if enabled else mgr.disable_all()
            return {"enabled": enabled, "closed_sessions": closed}

    def remove(self):
        with self.lock, installation_lock(bundle.root()):
            if terminal_sessions.terminal_enabled():
                raise BundleError("terminal_enabled", "Turn off the terminal before deleting", 409)
            if not bundle.required():
                raise BundleError("terminal_bundle_not_required", "No bundle is required on this OS", 409)
            bundle.prune_runtime()
            # Remove only downloaded components. Keep job history and the OS lock file.
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
