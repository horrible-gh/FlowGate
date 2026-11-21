"""Private, loopback-only Vosk C API host. Same HTTP contract as the sherpa worker."""
import argparse
import ctypes as C
from http.server import BaseHTTPRequestHandler, HTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import wave

PIECE = 8000  # 0.25 s of PCM16 mono 16 kHz, so utterance boundaries are seen promptly.


class Recognizer:
    online = True

    def __init__(self, library, model_dir):
        self.dll_dirs = [os.add_dll_directory(str(library.parent.resolve()))] if hasattr(os, "add_dll_directory") else []
        self.lib = C.CDLL(str(library.resolve()))
        self.function("vosk_set_log_level", [C.c_int], None)(-1)
        # The archive holds one top-level folder; Kaldi wants the folder with am/ and conf/.
        roots = sorted(p.parent for p in model_dir.rglob("final.mdl"))
        if not roots:
            raise RuntimeError("Missing model file final.mdl")
        root = str(roots[0].parent.resolve())
        # Kaldi opens files through the narrow C runtime: use the ANSI code page on Windows.
        encoded = root.encode("mbcs") if os.name == "nt" else os.fsencode(root)
        self.model = self.function("vosk_model_new", [C.c_char_p], C.c_void_p)(encoded)
        if not self.model:
            raise RuntimeError("Cannot load model")
        self.create = self.function("vosk_recognizer_new", [C.c_void_p, C.c_float], C.c_void_p)
        self.free = self.function("vosk_recognizer_free", [C.c_void_p], None)
        self.accept = self.function("vosk_recognizer_accept_waveform", [C.c_void_p, C.c_char_p, C.c_int], C.c_int)
        self.result = self.function("vosk_recognizer_result", [C.c_void_p], C.c_char_p)
        self.partial = self.function("vosk_recognizer_partial_result", [C.c_void_p], C.c_char_p)
        self.final = self.function("vosk_recognizer_final_result", [C.c_void_p], C.c_char_p)
        self.stream = None
        self.segments = []
        self.samples = 0

    def function(self, name, args, result):
        fn = getattr(self.lib, name)
        fn.argtypes, fn.restype = args, result
        return fn

    def close(self):
        if self.stream:
            self.free(self.stream)
            self.stream = None

    def begin(self):
        self.close()
        self.stream = self.create(self.model, 16000.0)
        if not self.stream:
            raise RuntimeError("Cannot create stream")
        self.segments = []
        self.samples = 0

    def keep(self, raw, key):
        text = json.loads(raw.decode("utf-8")).get(key, "").strip() if raw else ""
        if text and key == "text":
            self.segments.append(text)
        return text

    def feed(self, pcm, final=False):
        if not self.stream or len(pcm) % 2:
            raise ValueError("Invalid PCM stream")
        self.samples += len(pcm) // 2
        if self.samples > 120 * 16000:
            raise ValueError("Audio exceeds two minutes")
        for start in range(0, len(pcm), PIECE):
            piece = pcm[start:start + PIECE]
            if self.accept(self.stream, piece, len(piece)):
                self.keep(self.result(self.stream), "text")
        if final:
            self.keep(self.final(self.stream), "text")
            pending = ""
            self.close()
        else:
            pending = self.keep(self.partial(self.stream), "partial")
        return {"text": " ".join([*self.segments, pending]).strip(), "final": final}


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
                elif self.path == "/start":
                    recognizer.begin()
                    result = {"text": "", "final": False}
                elif self.path in ("/chunk", "/finish"):
                    result = recognizer.feed(body, self.path == "/finish")
                else:
                    raise ValueError("Unsupported operation")
                self.respond(result)
            except Exception as exc:
                self.respond({"error": type(exc).__name__}, 422)

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
