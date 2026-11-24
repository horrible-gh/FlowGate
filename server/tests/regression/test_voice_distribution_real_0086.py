"""Opt-in real-artifact verification. No production data is modified.

FLOWGATE_VOICE_EVIDENCE_DIR must be an operator-selected scratch directory.
The acquisition tests use upstream; offline tests explicitly deny network access.
"""
import os
from pathlib import Path
import threading
import pytest
from modules.flow_gate.services import voice_download as dl, voice_service as vs


@pytest.fixture(autouse=True)
def explicit_scratch(request, monkeypatch):
    # An explicit --basetemp is an operator-selected scratch location. Artifacts
    # are siblings so independent bounded self-checks can resume large downloads.
    base = request.config.getoption('basetemp')
    if base and not os.environ.get('FLOWGATE_VOICE_EVIDENCE_DIR'):
        monkeypatch.setenv('FLOWGATE_VOICE_EVIDENCE_DIR', str(Path(base).resolve().parent / 'voice-artifacts'))


def evidence():
    value = os.environ.get('FLOWGATE_VOICE_EVIDENCE_DIR')
    if not value:
        pytest.skip('Set FLOWGATE_VOICE_EVIDENCE_DIR to a scratch directory for real artifacts')
    return Path(value)


def test_acquire_korean_original():
    root = evidence()
    spec = vs.specification('sherpa-onnx', 'zipformer-ko')
    path = dl.fetch(spec, root / 'original', threading.Event(), lambda *a: None)
    assert dl.verify(path, spec)
    print('VERIFIED_ORIGINAL', path.name, spec['size_bytes'], spec['sha256'])


def test_acquire_engines_and_ffmpeg():
    root = evidence()
    for service in ['sherpa-onnx', 'whisper.cpp']:
        for spec in vs.specification(service)['artifacts']:
            if spec['component_type'] == 'sample':
                continue
            directory = root / ('ffmpeg' if spec['component_type'] == 'ffmpeg' else service)
            path = dl.fetch(spec, directory, threading.Event(), lambda *a: None)
            assert dl.verify(path, spec)
            print('VERIFIED_ARTIFACT', spec['filename'], spec['size_bytes'], spec['sha256'])


def test_prepare_actual_korean_package():
    root = evidence()
    from modules.flow_gate.services.voice_sanitized import prepare_model
    spec = vs.specification('sherpa-onnx', 'zipformer-ko')
    output = root / 'clean' / spec['sanitized']['filename']
    if not output.exists():
        prepare_model(spec, root / 'original' / spec['filename'], output, threading.Event())
    assert dl.verify(output, spec['sanitized'])
    import tarfile
    with tarfile.open(output) as archive:
        members = archive.getmembers()
        assert not any('test_wavs' in m.name.split('/') for m in members)
        assert len(members) == len(spec['sanitized']['members']) + 1
        import hashlib
        for m in spec['sanitized']['members']:
            data = archive.extractfile(m['filename'])
            digest = hashlib.file_digest(data, 'sha256').hexdigest()
            assert digest == m['sha256']
        assert archive.extractfile(spec['sanitized']['license_member']).read()
        print('CLEAN_MEMBERS', [(m.name, m.size) for m in members])
    print('VERIFIED_CLEAN', output.stat().st_size, spec['sanitized']['sha256'])


def test_actual_korean_offline_install_and_transcription(monkeypatch):
    root = evidence()
    from modules.flow_gate.services.voice_runtime import convert
    from modules.flow_gate.services.voice_sanitized import local_model
    import copy
    import hashlib
    import shutil
    import uuid
    clean = local_model(vs.specification('sherpa-onnx', 'zipformer-ko'))
    # A new input directory contains only the sanitized model and pinned engine.
    offline = root / ('input-' + uuid.uuid4().hex)
    offline.mkdir()
    shutil.copyfile(root / 'clean' / clean['filename'], offline / clean['filename'])
    engine = next(a for a in vs.specification('sherpa-onnx')['artifacts'] if a['component_type'] == 'engine')
    shutil.copyfile(root / 'sherpa-onnx' / engine['filename'], offline / engine['filename'])
    original_recording = Path(os.environ.get('FLOWGATE_VOICE_TEST_DIR', 'C:/tmp/stt-test')) / 'Recording_1.m4a'
    recording_hash = hashlib.sha256(original_recording.read_bytes()).hexdigest()
    target = root / ('installation-' + uuid.uuid4().hex)
    monkeypatch.setenv('FLOWGATE_VOICE_DIR', str(target))
    state = {'enabled': False, 'default': None}
    monkeypatch.setattr(vs, 'configuration', lambda: copy.deepcopy(state))
    monkeypatch.setattr(vs, 'save_configuration', lambda value: state.update(copy.deepcopy(value)))
    # Loopback is needed by engines. Block all external URLs, not local inference.
    def no_download(*args, **kwargs):
        raise AssertionError('Offline install attempted a network download')
    monkeypatch.setattr(dl.urllib.request, 'urlopen', no_download)
    manager = vs.Manager()
    try:
        result = manager.install('sherpa-onnx', 'zipformer-ko', offline, root / 'ffmpeg')
        manager.threads[result['install_id']].join(40)
        job = manager.job(result['install_id'])
        assert job['done'] and job['error'] is None, job
        assert manager.installed('sherpa-onnx', 'zipformer-ko')
        assert not list(target.rglob('test_wavs'))
        assert not list(target.rglob('korean-sample.wav'))
        assert not list(offline.rglob('test_wavs'))
        assert not (offline / vs.specification('sherpa-onnx', 'zipformer-ko')['filename']).exists()
        answer = manager.transcribe(original_recording.read_bytes(), 'sherpa-onnx', 'zipformer-ko', testing=True)
        assert len(answer['text'].strip()) > 2 and any('\uac00' <= c <= '\ud7a3' for c in answer['text'])
        print('REAL_KOREAN_OFFLINE', {'installed': True, 'characters': len(answer['text']),
              'transcript_sha256': hashlib.sha256(answer['text'].encode()).hexdigest(),
              'recording_sha256': recording_hash, 'elapsed_ms': answer['elapsed_ms']})
    finally:
        manager.shutdown()
    assert hashlib.sha256(original_recording.read_bytes()).hexdigest() == recording_hash


def test_actual_whisper_cli_export_and_offline_install(monkeypatch):
    root = evidence()
    import copy
    import hashlib
    import json
    import shutil
    import subprocess
    import sys
    import uuid
    from modules.flow_gate.services.voice_distribution import redistribution_components
    entry, model = vs.specification('whisper.cpp'), vs.specification('whisper.cpp', 'base')
    inputs = root / ('whisper-input-' + uuid.uuid4().hex)
    inputs.mkdir()
    engine = next(a for a in entry['artifacts'] if a['component_type'] == 'engine')
    shutil.copyfile(root / 'whisper.cpp' / engine['filename'], inputs / engine['filename'])
    fixture = Path(os.environ.get('FLOWGATE_VOICE_TEST_DIR', 'C:/tmp/stt-test'))
    shutil.copyfile(fixture / model['filename'], inputs / model['filename'])
    output = root / ('export-' + uuid.uuid4().hex)
    server = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(server / 'setup_voice.py'), '--service', 'whisper.cpp', '--model', 'base',
               '--from-dir', str(inputs), '--export-bundle', str(output)]
    result = subprocess.run(command, cwd=server, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, (result.stdout, result.stderr)
    expected = set()
    for component in redistribution_components(entry, model):
        members = component['expanded'] if component.get('redistribution_layout') == 'expanded' else [component]
        for member in members:
            relative = member.get('relative_path', member['filename'])
            assert dl.verify(output / relative, member)
            expected.add(relative)
        for notice in component['notices']:
            relative = 'licenses/' + notice['filename']
            assert dl.verify(output / relative, notice)
            expected.add(relative)
    files = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
    assert files == expected, files.symmetric_difference(expected)
    assert not list(output.rglob('jfk.wav')) and not list(output.rglob('ffmpeg*'))
    target = root / ('whisper-install-' + uuid.uuid4().hex)
    monkeypatch.setenv('FLOWGATE_VOICE_DIR', str(target))
    state = {'enabled': False, 'default': None}
    monkeypatch.setattr(vs, 'configuration', lambda: copy.deepcopy(state))
    monkeypatch.setattr(vs, 'save_configuration', lambda value: state.update(copy.deepcopy(value)))
    monkeypatch.setattr(dl.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Offline network access'))
    manager = vs.Manager()
    try:
        job = manager.install('whisper.cpp', 'base', output, root / 'ffmpeg')
        manager.threads[job['install_id']].join(30)
        status = manager.job(job['install_id'])
        assert status['done'] and status['error'] is None, status
        response = manager.transcribe((fixture / 'Recording_1.m4a').read_bytes(), 'whisper.cpp', 'base', testing=True)
        assert len(response['text'].strip()) > 2
        print('REAL_WHISPER_BUNDLE', {'cli_exit': result.returncode, 'files': len(files),
              'installed': True, 'characters': len(response['text']), 'elapsed_ms': response['elapsed_ms'],
              'transcript_sha256': hashlib.sha256(response['text'].encode()).hexdigest()})
    finally:
        manager.shutdown()
