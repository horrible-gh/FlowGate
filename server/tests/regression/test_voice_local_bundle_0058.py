"""Optional real whisper.cpp smoke test using the operator's read-only STT bundle.

Runs on stg when C:/tmp/stt-test exists, or FLOWGATE_VOICE_TEST_DIR is provided.
All binaries and recordings are copied to pytest's temporary directory before use.
"""
import os
from pathlib import Path
import shutil
import pytest
from modules.flow_gate.services.voice_runtime import Runtime, convert


def test_local_bundle_transcribes_recording(tmp_path):
    bundle = Path(os.environ.get("FLOWGATE_VOICE_TEST_DIR", "C:/tmp/stt-test"))
    if not bundle.is_dir():
        pytest.skip("Local STT bundle not present")
    binaries = list(bundle.rglob("whisper-server.exe"))
    ffmpegs = list(bundle.rglob("ffmpeg.exe"))
    models = sorted(bundle.rglob("ggml-*.bin"), key=lambda p: p.stat().st_size)
    recordings = list(bundle.rglob("Recording_1.m4a"))
    assert binaries and ffmpegs and models and recordings, [str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file()][:80]
    working = tmp_path / "runtime"
    shutil.copytree(binaries[0].parent, working)
    shutil.copyfile(ffmpegs[0], working / "ffmpeg.exe")
    model = tmp_path / models[0].name
    shutil.copyfile(models[0], model)
    wav = convert(working, recordings[0].read_bytes(), tmp_path)
    runtime = Runtime()
    try:
        runtime.start(working, model)
        assert runtime.healthy()
        text = runtime.transcribe(wav)
        assert len(text.strip()) > 2
        encoded = convert(working, recordings[0].read_bytes(), tmp_path, m4a=True)
        assert encoded.stat().st_size > 100
    finally:
        runtime.stop()
    assert not runtime.healthy()
