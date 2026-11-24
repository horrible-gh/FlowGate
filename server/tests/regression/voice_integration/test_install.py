"""Opt-in full installs in temp storage, never the server's configured voice directory."""
import copy
import time
import wave
import pytest
from modules.flow_gate.services import voice_service as vs
from modules.flow_gate.services.voice_sherpa_runtime import SherpaRuntime


STREAMING = {"zipformer-en-20m": "country", "zipformer-ko": "주민등록증", "small-en-us": "country", "small-ko": "주민등록증"}


@pytest.mark.parametrize("service,model", [
    ("whisper.cpp", "tiny"), ("whisper.cpp", "large-v3-turbo"),
    ("sherpa-onnx", "sensevoice-small-int8"), ("sherpa-onnx", "zipformer-en-20m"), ("sherpa-onnx", "zipformer-ko"),
    ("vosk", "small-en-us"), ("vosk", "small-ko")])
def test_real_install_and_inference(request, tmp_path, monkeypatch, service, model):
    if not request.config.getoption("--voice-downloads"):
        pytest.skip("Pass --voice-downloads to fetch pinned artifacts into temporary storage")
    monkeypatch.setenv("FLOWGATE_VOICE_DIR", str(tmp_path / "voice"))
    state = {"enabled": False, "default": None}
    monkeypatch.setattr(vs, "configuration", lambda: copy.deepcopy(state))
    monkeypatch.setattr(vs, "save_configuration", lambda value: state.update(copy.deepcopy(value)))
    manager = vs.Manager()
    job_id = manager.install(service, model, license_ack=model == "sensevoice-small-int8")["install_id"]
    deadline = time.monotonic() + 900
    while not manager.job(job_id)["done"] and time.monotonic() < deadline:
        time.sleep(0.5)
    job = manager.job(job_id)
    try:
        assert job["done"] and not job["error"], job
        assert manager.status()["enabled"] is (model != "sensevoice-small-int8")
        if vs.specification(service, model).get("self_test", {}).get("mode") == "load_only":
            assert manager.runtime.healthy()
            assert not list(manager.model_path(service, model).rglob("test_wavs"))
            return
        sample = manager.service_dir(service) / "jfk.wav"
        expected = "country"
        test = vs.specification(service, model).get("self_test")
        if test:
            # Non-English models are checked with the sample their catalog entry names.
            sample = next(p for base in (manager.model_path(service, model), manager.service_dir(service))
                          if base.is_dir() for p in sorted(base.rglob(test["sample"])))
            expected = test["expect"]
        result = manager.transcribe(sample.read_bytes(), service, model, testing=True)
        print("VOICE_RESULT", service, model, result["elapsed_ms"], ascii(result["text"]))
        assert expected in "".join(result["text"].lower().split()), result
        if model in STREAMING:
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
                print("VOICE_STREAM", service, model, ascii(final["text"]))
                assert STREAMING[model] in "".join(final["text"].lower().split()), final
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
                from modules.flow_gate.services.voice_errors import VoiceError
                with pytest.raises(VoiceError) as error:
                    offline.install(service, model, source)
                assert error.value.code == "voice_redistribution_pending"
                assert not offline.installed(service, model)
            finally:
                offline.shutdown()
    finally:
        manager.shutdown()
