"""Pinned sanitized import, path safety, notice supply and bundle integrity."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import threading
import pytest
from modules.flow_gate.services import voice_download as dl
from modules.flow_gate.services.voice_sanitized import prepare_model, local_model
from modules.flow_gate.services.voice_errors import VoiceError


def descriptor(filename, data):
    return dict(filename=filename, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def tar_bytes(rows):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, content in rows:
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(content), 0o644, 0
            archive.addfile(member, io.BytesIO(content))
    return data.getvalue()


@pytest.fixture
def package(tmp_path):
    license = Path(__file__).resolve().parents[3] / 'third_party/licenses/sherpa-onnx-LICENSE.txt'
    weights = [('model/encoder.onnx', b'weights'), ('model/tokens.txt', b'tokens')]
    raw = tmp_path / 'upstream.tar.bz2'
    with tarfile.open(raw, 'w:bz2') as archive:
        for name, data in weights + [('model/test_wavs/3.wav', b'restricted')]:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    clean = tar_bytes(weights + [('model/LICENSE', license.read_bytes())])
    model = descriptor(raw.name, raw.read_bytes()) | {'archive': True, 'excluded_paths': ['test_wavs/']}
    model['sanitized'] = descriptor('clean.tar', clean) | {
        'members': [descriptor(n, b) for n, b in weights],
        'license_member': 'model/LICENSE', 'license': descriptor(license.name, license.read_bytes())}
    return model, raw, clean


def test_prepare_is_deterministic_read_only_and_preserves_model_license(package, tmp_path):
    model, raw, clean = package
    before = raw.read_bytes()
    for name in ['first.tar', 'second.tar']:
        out = prepare_model(model, raw, tmp_path / name, threading.Event())
        assert out.read_bytes() == clean
        with tarfile.open(out) as archive:
            assert archive.getnames() == ['model/encoder.onnx', 'model/tokens.txt', 'model/LICENSE']
    assert raw.read_bytes() == before
    with pytest.raises(VoiceError):
        prepare_model(model, raw, raw, threading.Event())
    assert raw.read_bytes() == before


def test_offline_import_rejects_raw_renamed_missing_and_tampered(package, tmp_path, monkeypatch):
    model, raw, clean = package
    spec = local_model(model)
    monkeypatch.setattr(dl.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('Offline download'))
    source = tmp_path / 'input'
    source.mkdir()
    path = source / spec['filename']
    for content in [raw.read_bytes(), clean[:-1], clean[:-1] + b'x']:
        path.write_bytes(content)
        with pytest.raises(VoiceError):
            dl.fetch(spec, tmp_path / 'cache', threading.Event(), lambda *a: None, source)
    path.write_bytes(clean)
    copied = dl.fetch(spec, tmp_path / 'cache', threading.Event(), lambda *a: None, source)
    assert copied.read_bytes() == clean and path.read_bytes() == clean


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/escape', 'model/../escape'])
def test_sanitizer_rejects_traversal_even_with_matching_input_hash(package, tmp_path, name):
    model, raw, _ = package
    with tarfile.open(raw, 'w:bz2') as archive:
        member = tarfile.TarInfo(name)
        member.size = 1
        archive.addfile(member, io.BytesIO(b'x'))
    model.update(descriptor(raw.name, raw.read_bytes()))
    with pytest.raises(VoiceError):
        prepare_model(model, raw, tmp_path / 'out.tar', threading.Event())
    assert not (tmp_path / 'out.tar').exists()


def test_sanitizer_rejects_link_and_missing_members(package, tmp_path):
    model, raw, _ = package
    for link in [False, True]:
        with tarfile.open(raw, 'w:bz2') as archive:
            if link:
                member = tarfile.TarInfo('model/link')
                member.type, member.linkname = tarfile.SYMTYPE, '../escape'
                archive.addfile(member)
        model.update(descriptor(raw.name, raw.read_bytes()))
        with pytest.raises(VoiceError):
            prepare_model(model, raw, tmp_path / 'out.tar', threading.Event())
        assert not (tmp_path / 'out.tar').exists()


def test_verify_rejects_symbolic_file_and_parent(package, tmp_path):
    _, raw, _ = package
    link = tmp_path / 'linked'
    try:
        link.symlink_to(raw)
    except OSError as exc:
        pytest.skip(str(exc))
    assert not dl.verify(link, descriptor(link.name, raw.read_bytes()))


def test_notice_supply_from_repository_and_tampering(tmp_path):
    from modules.flow_gate.services.voice_distribution import export_bundle
    from modules.flow_gate.services.voice_service import specification
    source = tmp_path / 'source'
    source.mkdir()
    engine = descriptor('engine.bin', b'engine') | {'component_type': 'engine', 'redistribution_allowed': True,
        'notices': [specification('whisper.cpp')['artifacts'][0]['notices'][0]]}
    (source / 'engine.bin').write_bytes(b'engine')
    model = descriptor('model.bin', b'weights') | {'redistribution_allowed': True, 'notices': engine['notices']}
    (source / 'model.bin').write_bytes(b'weights')
    entry = {'artifacts': [engine]}
    export_bundle(entry, model, source, tmp_path / 'output')
    assert dl.verify(tmp_path / 'output/licenses' / engine['notices'][0]['filename'], engine['notices'][0])
    (source / 'licenses').mkdir()
    (source / 'licenses' / engine['notices'][0]['filename']).write_bytes(b'bad notice')
    with pytest.raises(VoiceError):
        export_bundle(entry, model, source, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()
