"""Private, loopback-only sherpa C API host. No Python ML package dependency."""
import argparse
from array import array
import ctypes as C
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import re
import secrets
import wave

from voice_sherpa_abi import SherpaOnnxOnlineRecognizerConfig, SherpaOnnxOfflineRecognizerConfig


def native_path(path):
    value = str(path.resolve())
    prefix = chr(92) * 2 + "?" + chr(92)
    return prefix + value if os.name == "nt" and not value.startswith(prefix) else value


class Recognizer:
    def __init__(self, library, model_dir):
        self.dll_dirs = [os.add_dll_directory(native_path(p)) for p in {library.parent, library.parent.parent / "bin"} if p.is_dir()]
        self.lib = C.CDLL(native_path(library))
        self.stream = None
        self.online = any(model_dir.rglob("encoder*.onnx"))
        def find(pattern):
            found = sorted(model_dir.rglob(pattern), key=lambda p: ("int8" not in p.name, len(p.name)))
            if not found:
                raise RuntimeError("Missing model file " + pattern)
            return os.fsencode(native_path(found[0]))
        config = SherpaOnnxOnlineRecognizerConfig() if self.online else SherpaOnnxOfflineRecognizerConfig()
        config.feat_config.sample_rate = 16000
        config.feat_config.feature_dim = 80
        config.model_config.num_threads = 2
        config.model_config.provider = b"cpu"
        config.model_config.tokens = find("tokens.txt")
        config.decoding_method = b"greedy_search"
        config.max_active_paths = 4
        if self.online:
            config.model_config.transducer.encoder = find("encoder*.onnx")
            config.model_config.transducer.decoder = find("decoder*.onnx")
            config.model_config.transducer.joiner = find("joiner*.onnx")
            config.enable_endpoint = 0
        else:
            # Only streaming transducer models are in the catalog. The SenseVoice offline
            # model was removed (F86-7); its ABI struct stays in voice_sherpa_abi.py for layout.
            raise RuntimeError("Unsupported sherpa-onnx model: streaming encoder/decoder/joiner files are required")
        prefix = "Online" if self.online else "Offline"
        self.create = self.function("SherpaOnnxCreate" + prefix + "Recognizer", [C.POINTER(type(config))], C.c_void_p)
        self.recognizer = self.create(C.byref(config))
        if not self.recognizer:
            raise RuntimeError("Cannot create recognizer")
        self.new_stream = self.function("SherpaOnnxCreate" + prefix + "Stream", [C.c_void_p], C.c_void_p)
        self.destroy_stream = self.function("SherpaOnnxDestroy" + prefix + "Stream", [C.c_void_p], None)
        self.accept = self.function("SherpaOnnxOnlineStreamAcceptWaveform" if self.online else "SherpaOnnxAcceptWaveformOffline",
                                    [C.c_void_p, C.c_int32, C.POINTER(C.c_float), C.c_int32], None)
        self.decode = self.function("SherpaOnnxDecode" + prefix + "Stream", [C.c_void_p, C.c_void_p], None)
        self.get_result = self.function("SherpaOnnxGet" + prefix + "StreamResultAsJson", [C.c_void_p, C.c_void_p] if self.online else [C.c_void_p], C.c_void_p)
        self.free_result = self.function("SherpaOnnxDestroy" + prefix + "StreamResultJson", [C.c_void_p], None)
        if self.online:
            self.ready = self.function("SherpaOnnxIsOnlineStreamReady", [C.c_void_p, C.c_void_p], C.c_int32)
            self.finished = self.function("SherpaOnnxOnlineStreamInputFinished", [C.c_void_p], None)

    def function(self, name, args, result):
        fn = getattr(self.lib, name)
        fn.argtypes, fn.restype = args, result
        return fn

    def begin(self):
        if self.stream:
            self.destroy_stream(self.stream)
        self.stream = self.new_stream(self.recognizer)
        if not self.stream:
            raise RuntimeError("Cannot create stream")
        self.samples = 0

    def feed(self, pcm, final=False):
        if not self.stream or len(pcm) % 2:
            raise ValueError("Invalid PCM stream")
        self.samples += len(pcm) // 2
        if self.samples > 120 * 16000:
            raise ValueError("Audio exceeds two minutes")
        values = array("h")
        values.frombytes(pcm)
        floats = array("f", (v / 32768.0 for v in values))
        if floats:
            buffer = (C.c_float * len(floats)).from_buffer(floats)
            self.accept(self.stream, 16000, buffer, len(floats))
        if self.online:
            if final:
                tail = (C.c_float * 8000)()
                self.accept(self.stream, 16000, tail, len(tail))
                self.finished(self.stream)
            while self.ready(self.recognizer, self.stream):
                self.decode(self.recognizer, self.stream)
        elif final:
            self.decode(self.recognizer, self.stream)
        pointer = self.get_result(self.recognizer, self.stream) if self.online else self.get_result(self.stream)
        try:
            result = json.loads(C.string_at(pointer)) if pointer else {"text": ""}
        finally:
            if pointer:
                self.free_result(pointer)
        if final:
            self.destroy_stream(self.stream)
            self.stream = None
        return {"text": re.sub(r"<\|.*?\|>", "", result.get("text", "")).strip(), "final": final}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    recognizer = Recognizer(args.library, args.model)
    token = os.environ.pop("FLOWGATE_VOICE_WORKER_SECRET")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Audio, authentication and transcripts never enter access logs.

        def respond(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            return secrets.compare_digest(self.headers.get("X-Voice-Secret", ""), token)

        def do_GET(self):
            self.respond({"status": "ok"}, 200 if self.path == "/health" and self.allowed() else 403)

        def do_POST(self):
            if not self.allowed():
                self.respond({"error": "forbidden"}, 403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 4 * 1024 * 1024:
                    raise ValueError("Invalid request size")
                self.connection.settimeout(15)
                body = self.rfile.read(size)
                if len(body) != size:
                    raise ValueError("Incomplete audio")
                if self.path == "/inference":
                    with wave.open(io.BytesIO(body), "rb") as wav:
                        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                            raise ValueError("Expected PCM16 mono 16kHz WAV")
                        pcm = wav.readframes(wav.getnframes())
                    recognizer.begin()
                    result = recognizer.feed(pcm, True)
                elif self.path == "/start" and recognizer.online:
                    recognizer.begin()
                    result = {"text": "", "final": False}
                elif self.path in ("/chunk", "/finish") and recognizer.online:
                    result = recognizer.feed(body, self.path == "/finish")
                else:
                    raise ValueError("Unsupported operation")
                self.respond(result)
            except Exception as exc:
                self.respond({"error": type(exc).__name__}, 422)

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
