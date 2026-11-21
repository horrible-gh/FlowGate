"""Opt-in full installs in temp storage, never the server's configured voice directory."""
import copy
import time
import wave
import pytest
from modules.flow_gate.services import voice_service as vs
from modules.flow_gate.services.voice_sherpa_runtime import SherpaRuntime


@pytest.mark.parametrize("service,model", [("whisper.cpp", "tiny"), ("sherpa-onnx", "sensevoice-small-int8"), ("sherpa-onnx", "zipformer-en-20m")])
def test_real_install_and_inference(request, tmp_path, monkeypatch, service, model):
    if not request.config.getoption("--voice-downloads"):
        pytest.skip("Pass --voice-downloads to fetch pinned artifacts into temporary storage")
    monkeypatch.setenv("FLOWGATE_VOICE_DIR", str(tmp_path / "voice"))
    state = {"enabled": False, "default": None}
    monkeypatch.setattr(vs, "configuration", lambda: copy.deepcopy(state))
    monkeypatch.setattr(vs, "save_configuration", lambda value: state.update(copy.deepcopy(value)))
    manager = vs.Manager()
    job_id = manager.install(service, model)["install_id"]
    deadline = time.monotonic() + 900
    while not manager.job(job_id)["done"] and time.monotonic() < deadline:
        time.sleep(0.5)
    job = manager.job(job_id)
    try:
        assert job["done"] and not job["error"], job
        assert manager.status()["enabled"]
        sample = manager.service_dir(service) / "jfk.wav"
        result = manager.transcribe(sample.read_bytes(), service, model)
        assert "country" in result["text"].lower(), result
        if model == "zipformer-en-20m":
            runtime = SherpaRuntime()
            try:
                runtime.start(manager.service_dir(service), manager.model_path(service, model))
                runtime.request("/start", b"")
                with wave.open(str(sample), "rb") as wav:
                    while True:
                        pcm = wav.readframes(16000)
                        if not pcm:
                            break
                        partial = runtime.request("/chunk", pcm)
                        assert partial["final"] is False
                final = runtime.request("/finish", b"")
                assert "country" in final["text"].lower(), final
            finally:
                runtime.stop()
        if service == "whisper.cpp":
            # A matching offline bundle must install with all network calls forbidden.
            from modules.flow_gate.services import voice_download
            source = vs.root() / "downloads" / service
            manager.shutdown()
            monkeypatch.setenv("FLOWGATE_VOICE_DIR", str(tmp_path / "offline"))
            state.update(enabled=False, default=None)
            def no_network(*args, **kwargs):
                raise AssertionError("Offline install attempted a download")
            monkeypatch.setattr(voice_download.urllib.request, "urlopen", no_network)
            offline = vs.Manager()
            try:
                offline_id = offline.install(service, model, source)["install_id"]
                deadline = time.monotonic() + 300
                while not offline.job(offline_id)["done"] and time.monotonic() < deadline:
                    time.sleep(0.25)
                job = offline.job(offline_id)
                assert job["done"] and not job["error"], job
                assert offline.status()["enabled"]
            finally:
                offline.shutdown()
    finally:
        manager.shutdown()
