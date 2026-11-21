"""User-owned PCM16 streaming sessions, one process per active session (cap one)."""
import threading
import time
import uuid
from .voice_errors import VoiceError
from .voice_sherpa_runtime import SherpaRuntime
from .voice_service import manager, configuration, specification

_condition = threading.Condition(threading.RLock())
_sessions = {}
_stopping = False


def _discard(session_id):
    session = _sessions.pop(session_id, None)
    if session:
        session["runtime"].stop()
        _condition.notify_all()


def _expire():
    now = time.monotonic()
    for session_id, session in list(_sessions.items()):
        if now - session["started"] > 150 or now - session["touched"] > 30:
            _discard(session_id)


def active(service=None):
    with _condition:
        _expire()
        return any(service is None or s["service"] == service for s in _sessions.values())


def start(user_id, service=None, model=None):
    global _stopping
    deadline = time.monotonic() + 130
    with _condition:
        while _sessions:
            _expire()
            if not _sessions:
                break
            if time.monotonic() > deadline:
                raise VoiceError("voice_busy", "Another live transcription is still active", 409)
            _condition.wait(timeout=1)
        if _stopping or not configuration()["enabled"]:
            raise VoiceError("voice_disabled", "Voice input is off", 409)
        with manager.inference:
            if manager.busy(service):
                raise VoiceError("voice_install_in_progress", "Wait for installation to finish", 409)
            selected = manager.select(service, model, user_id)
            if not specification(**selected)["realtime"]:
                raise VoiceError("voice_realtime_unsupported", "Selected model does not support realtime", 422)
            runtime = SherpaRuntime()
            try:
                runtime.start(manager.service_dir(selected["service"]), manager.model_path(**selected))
                runtime.request("/start", b"")
            except Exception:
                runtime.stop()
                raise
            session_id = "vs_" + uuid.uuid4().hex
            now = time.monotonic()
            _sessions[session_id] = {**selected, "owner": user_id, "runtime": runtime, "started": now,
                                     "touched": now, "samples": 0, "next_chunk": 0}
            timer = threading.Timer(151, expire)
            timer.daemon = True
            timer.start()
            return {"ok": True, "stream_id": session_id, "sample_rate": 16000, "channels": 1,
                    "format": "pcm_s16le", "max_seconds": 120, **selected}


def expire():
    with _condition:
        _expire()


def _owned(session_id, user_id):
    _expire()
    session = _sessions.get(session_id)
    if not session or session["owner"] != user_id:
        raise VoiceError("voice_stream_not_found", "Stream not found", 404)
    return session


def chunk(session_id, user_id, data, index, final=False):
    with _condition:
        session = _owned(session_id, user_id)
        if index != session["next_chunk"]:
            raise VoiceError("voice_chunk_out_of_order", "Unexpected chunk index", 409)
        if len(data) % 2 or len(data) > 16000 * 2 * 5:
            raise VoiceError("voice_invalid_pcm", "Send at most 5 seconds of mono PCM16 at 16 kHz", 422)
        if session["samples"] + len(data) // 2 > 120 * 16000:
            _discard(session_id)
            raise VoiceError("voice_duration_exceeded", "Audio exceeds 2 minutes", 422)
        try:
            result = session["runtime"].request("/finish" if final else "/chunk", data)
        except Exception as exc:
            _discard(session_id)
            raise VoiceError("voice_module_unavailable", "Live transcription failed", 503) from exc
        session["samples"] += len(data) // 2
        session["touched"] = time.monotonic()
        session["next_chunk"] += 1
        response = {"ok": True, "text": result["text"], "final": final, "next_chunk": session["next_chunk"],
                    "elapsed_ms": round((time.monotonic() - session["started"]) * 1000),
                    "service": session["service"], "model": session["model"]}
        if final:
            _discard(session_id)
        return response


def cancel(session_id, user_id):
    with _condition:
        _owned(session_id, user_id)
        _discard(session_id)
    return {"ok": True}


def stop_all():
    with _condition:
        for session_id in list(_sessions):
            _discard(session_id)


def shutdown():
    global _stopping
    with _condition:
        _stopping = True
        for session_id in list(_sessions):
            _discard(session_id)
