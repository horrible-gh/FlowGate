"""Fail-closed redistribution policy; never infer model or native notices from an engine license."""
from pathlib import Path
import shutil
from .voice_errors import VoiceError

KOREAN_ARCHIVE = "sherpa-onnx-streaming-zipformer-korean-2024-06-16.tar.bz2"


def cleanup_legacy_samples(root: Path):
    """Only known application-owned samples/cache, never user recordings or import directories."""
    for service in ("vosk", "sherpa-onnx"):
        for area in ("services", "downloads"):
            base = root / area / service
            for suffix in ("", ".part"):
                (base / ("korean-sample.wav" + suffix)).unlink(missing_ok=True)
    model = root / "services" / "sherpa-onnx" / "models" / "zipformer-ko"
    for sample_dir in model.glob("**/test_wavs"):
        if sample_dir.is_symlink():
            sample_dir.unlink()
        elif sample_dir.is_dir():
            shutil.rmtree(sample_dir)
    for suffix in ("", ".part"):
        (root / "downloads" / "sherpa-onnx" / (KOREAN_ARCHIVE + suffix)).unlink(missing_ok=True)
    # Interrupted staging trees are application-owned, but may contain model weights.
    for sample in (root / "staging").glob("*/korean-sample.wav"):
        sample.unlink(missing_ok=True)
    for sample_dir in (root / "staging").glob("*/models/zipformer-ko/**/test_wavs"):
        if sample_dir.is_symlink():
            sample_dir.unlink()
        elif sample_dir.is_dir():
            shutil.rmtree(sample_dir)


def validate_local_install(entry, model):
    """로컬 반입은 재배포 승인을 요구하지 않되 제한 원본은 차단한다."""
    components = entry["artifacts"] + [model]
    if model.get("default_eligible") is False or any(c.get("excluded_paths") and not c.get("sanitized") for c in components):
        raise VoiceError("voice_redistribution_pending", "Restricted original excluded from offline installation", 422)


def redistribution_components(entry, model):
    """FFmpeg is separately obtained upstream, never carried in our offline bundle.

    Enabling a component requires exact-artifact review and pinned LICENSE/NOTICE
    files (including every bundled native dependency). Unknown is not permission.
    """
    validate_local_install(entry, model)
    from .voice_sanitized import local_model
    model = local_model(model)
    components = [a for a in entry["artifacts"] if a.get("component_type") not in ("ffmpeg", "sample")] + [model]
    for component in components:
        if not component.get("redistribution_allowed") or not component.get("notices"):
            raise VoiceError("voice_redistribution_pending",
                             "Verified component/native LICENSE and NOTICE files are pending: " + component["filename"], 422)
    return components


def notice_path(directory, notice):
    from .voice_sanitized import safe_relative
    relative = safe_relative(notice["filename"])
    supplied = directory / "licenses" / relative
    if supplied.exists() or supplied.is_symlink():
        return supplied
    return Path(__file__).resolve().parents[4] / "third_party/licenses" / relative


def validate_bundle(entry, model, directory):
    from .voice_download import verify
    components = redistribution_components(entry, model)
    for component in components:
        for notice in component["notices"]:
            # Paths and digests come only from the source-controlled catalog.
            if not verify(notice_path(directory, notice), notice):
                raise VoiceError("voice_offline_notice_invalid", "Missing or changed component notice", 422)
    return components


def export_bundle(entry, model, source, destination):
    """Publish only reviewed files and notices; verify bytes after copying too."""
    import zipfile
    from .voice_download import verify
    from .voice_sanitized import safe_relative
    source, destination = Path(source), Path(destination)
    components = validate_bundle(entry, model, source)
    files, expected = [], []
    for component in components:
        relative = Path(safe_relative(component['filename']))
        path = source / relative
        if component.get('redistribution_layout') == 'expanded':
            # Export a functional, reviewed runtime subset, never unrelated tools
            # from the upstream ZIP. All required DLLs remain present.
            archive = path if verify(path, component) else None
            for member in component['expanded']:
                relative = Path(safe_relative(member['relative_path']))
                candidate = next((p for p in source.rglob(member['filename']) if verify(p, member)), None)
                if candidate is None and archive is None:
                    raise VoiceError('voice_offline_file_invalid', 'Unverified runtime member', 422)
                files.append((candidate or archive, relative, member['relative_path'] if candidate is None else None))
                expected.append((relative, member))
        else:
            if not verify(path, component):
                raise VoiceError('voice_offline_file_invalid', 'Unverified bundle component', 422)
            files.append((path, relative, None))
            expected.append((relative, component))
        for notice in component['notices']:
            relative = Path('licenses') / Path(safe_relative(notice['filename']))
            files.append((notice_path(source, notice), relative, None))
            expected.append((relative, notice))
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for source_file, relative, member in files:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if member:
                with zipfile.ZipFile(source_file) as archive:
                    with archive.open(member) as src, target.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
            else:
                shutil.copyfile(source_file, target)
        for relative, spec in expected:
            if not verify(destination / relative, spec):
                raise VoiceError('voice_offline_file_invalid', 'File changed while exporting', 422)
        return destination
    except Exception:
        shutil.rmtree(destination)
        raise
