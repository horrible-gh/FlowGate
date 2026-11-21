"""Owned loopback whisper-server process and bounded audio conversion."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import uuid
import wave

from .voice_errors import VoiceError

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_SECONDS = 120


def executable(root: Path, name: str) -> Path:
    found = sorted(root.rglob(name + ".exe"))
    if not found:
        raise VoiceError("voice_not_installed", name + " is not installed", 409)
    return found[0]


def convert(service_dir: Path, data: bytes, directory: Path, *, m4a=False) -> Path:
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise VoiceError("voice_audio_too_large", "Audio must be between 1 byte and 25 MiB", 413)
    supported = (data[:4] in (b"RIFF", bytes.fromhex("1a45dfa3")) or data[4:8] == b"ftyp"
                 or data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 255 and data[1] & 224 == 224))
    if not supported:
        raise VoiceError("voice_unsupported_format", "Expected webm, m4a, mp3 or wav audio", 415)
    # The demuxer reads bytes, not a client filename or URL. Network and playlists
    # cannot open remote resources; decoding is capped before measuring duration.
    source = directory / "input.audio"
    source.write_bytes(data)
    wav = directory / "decoded.wav"
    ffmpeg = executable(service_dir, "ffmpeg")
    command = [str(ffmpeg), "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
               "-i", str(source), "-t", "121", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)]
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        if result.returncode:
            raise VoiceError("voice_unsupported_format", "Cannot decode audio", 415)
        with wave.open(str(wav), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
        if duration <= 0:
            raise VoiceError("voice_unsupported_format", "Audio contains no samples", 415)
        if duration > MAX_SECONDS:
            raise VoiceError("voice_duration_exceeded", "Audio exceeds 2 minutes", 422)
        if not m4a:
            return wav
        target = directory / "audio.m4a"
        result = subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-y", "-i", str(wav),
                                 "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(target)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        if result.returncode:
            raise VoiceError("voice_conversion_failed", "Cannot encode m4a", 422)
        return target
    except subprocess.TimeoutExpired as exc:
        raise VoiceError("voice_conversion_timeout", "Audio conversion timed out", 422) from exc


class Runtime:
    """The manager's inference lock also protects start/stop/model replacement."""
    def __init__(self):
        self.process = None
        self.model = None
        self.delegate = None
        self.cancel_event = None
        self.port = None
        # Never route private audio through HTTP_PROXY/HTTPS_PROXY.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def healthy(self):
        if self.delegate is not None:
            return self.delegate.healthy()
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            with self.http.open(f"http://127.0.0.1:{self.port}/health", timeout=1) as response:
                return response.status == 200
        except Exception:
            return False

    def stop(self):
        if self.delegate is not None:
            self.delegate.stop()
            self.delegate = None
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
        self.model = None

    def start(self, service_dir: Path, model: Path):
        if model.is_dir():
            if self.delegate is None:
                self.stop()
                from .voice_sherpa_runtime import SherpaRuntime
                self.delegate = SherpaRuntime()
            self.delegate.cancel_event = self.cancel_event
            self.delegate.start(service_dir, model)
            self.model = model
            return
        if self.delegate is not None:
            self.stop()
        if self.model == model and self.healthy():
            return
        self.stop()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        binary = executable(service_dir, "whisper-server")
        self.process = subprocess.Popen([str(binary), "--host", "127.0.0.1", "--port", str(self.port),
                                         "-m", str(model), "-l", "auto", "-ng"], cwd=str(binary.parent),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.stop()
        raise VoiceError("voice_module_unavailable", "Voice module did not become ready", 503)

    def transcribe(self, wav: Path):
        if self.delegate is not None:
            return self.delegate.transcribe(wav)
        boundary = "flowgate" + uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n"
                "Content-Type: audio/wav\r\n\r\n").encode() + wav.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/inference", data=body,
                                         headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        try:
            with self.http.open(request, timeout=600) as response:
                result = json.load(response)
            if not isinstance(result.get("text"), str):
                raise ValueError("Missing transcription")
            return result["text"].strip()
        except Exception as exc:
            raise VoiceError("voice_module_unavailable", "Voice module failed to transcribe", 503) from exc
