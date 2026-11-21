"""Lifecycle for the private sherpa HTTP worker, bound only to 127.0.0.1."""
import json
import logging
import tempfile
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from .voice_errors import VoiceError


class SherpaRuntime:
    def __init__(self):
        self.process = None
        self.model = None
        self.port = None
        self.diagnostics = None
        self.cancel_event = None
        self.secret = secrets.token_hex(32)
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, data=None, timeout=600):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}" + path, data=data, headers={"X-Voice-Secret": self.secret})
        with self.http.open(req, timeout=timeout) as response:
            return json.load(response)

    def healthy(self):
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            return self.request("/health", timeout=1).get("status") == "ok"
        except Exception:
            return False

    def start(self, directory, model):
        if self.model == model and self.healthy():
            return
        self.stop()
        libraries = list(directory.rglob("sherpa-onnx-c-api.dll"))
        if not libraries:
            raise VoiceError("voice_not_installed", "Sherpa runtime is not installed", 409)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        environment = {**os.environ, "FLOWGATE_VOICE_WORKER_SECRET": self.secret}
        self.diagnostics = tempfile.TemporaryFile()
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("voice_sherpa_worker.py")),
                                         "--library", str(libraries[0]), "--model", str(model), "--port", str(self.port)],
                                        cwd=str(directory), env=environment, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.diagnostics)
        self.model = model
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.stop()
                raise VoiceError("voice_install_cancelled", "Installation cancelled", 409)
            if self.healthy():
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.25)
        self.diagnostics.seek(0)
        logging.getLogger(__name__).error("Sherpa startup: %s", self.diagnostics.read(4096).decode("utf-8", "replace"))
        self.stop()
        raise VoiceError("voice_module_unavailable", "Sherpa worker did not become ready", 503)

    def transcribe(self, wav):
        try:
            return self.request("/inference", wav.read_bytes())["text"]
        except Exception as exc:
            raise VoiceError("voice_module_unavailable", "Sherpa transcription failed", 503) from exc

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.diagnostics is not None:
            self.diagnostics.close()
            self.diagnostics = None
        self.process = None
        self.model = None
