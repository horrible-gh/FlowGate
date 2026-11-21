"""Streaming ownership, ordering, limits and service lifecycle interlocks."""
import time
import pytest
from modules.flow_gate.services import voice_streams as streams
from modules.flow_gate.services import voice_service as vs
from modules.flow_gate.services.voice_errors import VoiceError


class Runtime:
    def __init__(self):
        self.stopped = False
    def start(self, *args):
        pass
    def request(self, path, data=None):
        return {"text": "partial transcript", "final": path == "/finish"}
    def stop(self):
        self.stopped = True


@pytest.fixture
def stream(monkeypatch, tmp_path):
    monkeypatch.setattr(streams, "_sessions", {})
    monkeypatch.setattr(streams, "_stopping", False)
    monkeypatch.setattr(streams, "SherpaRuntime", Runtime)
    monkeypatch.setattr(streams, "configuration", lambda: {"enabled": True})
    monkeypatch.setattr(streams.manager, "select", lambda *a: {"service": "sherpa-onnx", "model": "zipformer-en-20m"})
    monkeypatch.setattr(streams.manager, "busy", lambda *a: False)
    monkeypatch.setattr(streams.manager, "service_dir", lambda *a: tmp_path)
    monkeypatch.setattr(streams.manager, "model_path", lambda **k: tmp_path)
    session = streams.start("u1")
    yield session["stream_id"]
    streams.stop_all()


def test_wrong_user_cannot_feed_or_cancel(stream):
    with pytest.raises(VoiceError) as error:
        streams.chunk(stream, "u2", b"", 0)
    assert error.value.status_code == 404
    with pytest.raises(VoiceError):
        streams.cancel(stream, "u2")
    assert streams.active("sherpa-onnx")


def test_order_limit_and_final_cleanup(stream):
    with pytest.raises(VoiceError):
        streams.chunk(stream, "u1", b"00", 1)
    with pytest.raises(VoiceError):
        streams.chunk(stream, "u1", b"0", 0)
    result = streams.chunk(stream, "u1", b"00", 0)
    assert result["next_chunk"] == 1 and not result["final"]
    result = streams.chunk(stream, "u1", b"", 1, True)
    assert result["final"] and not streams.active()


def test_two_minute_limit_terminates_process(stream):
    runtime = streams._sessions[stream]["runtime"]
    streams._sessions[stream]["samples"] = 120 * 16000
    with pytest.raises(VoiceError) as error:
        streams.chunk(stream, "u1", b"00", 0)
    assert error.value.code == "voice_duration_exceeded"
    assert runtime.stopped


def test_abandoned_stream_expires(stream):
    runtime = streams._sessions[stream]["runtime"]
    streams._sessions[stream]["touched"] = time.monotonic() - 31
    streams.expire()
    assert not streams.active() and runtime.stopped


def test_live_session_blocks_service_removal(stream):
    with pytest.raises(VoiceError) as error:
        vs.Manager().remove("sherpa-onnx", "zipformer-en-20m")
    assert error.value.code == "voice_busy"
