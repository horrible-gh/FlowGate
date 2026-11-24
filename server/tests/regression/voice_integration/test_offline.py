"""Use the existing stg bundle read-only to exercise --from-dir installation."""
import copy
import os
from pathlib import Path
import time
import pytest
from modules.flow_gate.services import voice_service as vs, voice_download as dl


def test_stg_offline_bundle_integrity(request, tmp_path, monkeypatch):
    if not request.config.getoption("--voice-downloads"):
        pytest.skip("Opt-in local install integration")
    source = Path(os.environ.get("FLOWGATE_VOICE_TEST_DIR", "C:/tmp/stt-test"))
    if not source.is_dir():
        pytest.skip("STT bundle not present")
    entry = vs.specification("whisper.cpp")
    selected = next((model for model in entry["models"] if any(dl.verify(p, model) for p in source.rglob(model["filename"]))), None)
    assert selected, "Local models do not match the pinned catalog"
    before = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}
    monkeypatch.setenv("FLOWGATE_VOICE_DIR", str(tmp_path / "voice"))
    state = {"enabled": False, "default": None}
    monkeypatch.setattr(vs, "configuration", lambda: copy.deepcopy(state))
    monkeypatch.setattr(vs, "save_configuration", lambda value: state.update(copy.deepcopy(value)))
    def no_network(*args, **kwargs):
        raise AssertionError("Offline install attempted a network download")
    monkeypatch.setattr(dl.urllib.request, "urlopen", no_network)
    manager = vs.Manager()
    try:
        from modules.flow_gate.services.voice_errors import VoiceError
        with pytest.raises(VoiceError) as error:
            manager.install("whisper.cpp", selected["id"], source)
        assert error.value.code == "voice_redistribution_pending"
        assert not manager.installed("whisper.cpp", selected["id"])
    finally:
        manager.shutdown()
    after = {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}
    assert before == after
