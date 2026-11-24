"""Deterministic KSS-free model package from the exact reviewed upstream archive.

The source-controlled catalog pins both ends of this transformation. An imported
manifest cannot grant permission or substitute hashes. Never modify the input.
"""
from pathlib import Path, PurePosixPath
import hashlib
import os
import tarfile
import io
import uuid
from .voice_errors import VoiceError
from .voice_download import verify, check_cancel


def safe_relative(name):
    path = PurePosixPath(name.replace('\\', '/'))
    if (not path.parts or path.is_absolute() or '..' in path.parts
            or ':' in name or any(p.endswith((' ', '.')) for p in path.parts)):
        raise VoiceError('voice_archive_invalid', 'Unsafe package path', 422)
    return path


def local_model(model):
    if not model.get('excluded_paths'):
        return model
    clean = model.get('sanitized')
    if not clean or not clean.get('sha256') or not clean.get('members'):
        raise VoiceError('voice_redistribution_pending', 'Reviewed sanitized model is required', 422)
    return {**model, **{k: clean[k] for k in ('filename', 'size_bytes', 'sha256')},
            'excluded_paths': [], 'sanitized_from': model['sha256']}


def _regular(path):
    return not path.is_symlink() and not any(p.is_symlink() for p in path.parents)


def prepare_model(model, source, destination, cancel):
    """Emit a canonical USTAR archive; no samples are extracted, even temporarily."""
    source, destination = Path(source), Path(destination)
    clean = model.get('sanitized')
    if not clean or not model.get('excluded_paths'):
        raise VoiceError('voice_model_not_sanitizable', 'No reviewed model transformation', 422)
    safe_relative(clean['filename'])
    if not _regular(source) or not verify(source, model):
        raise VoiceError('voice_offline_file_invalid', 'Unverified original model archive', 422)
    if destination.exists() or destination.is_symlink():
        raise VoiceError('voice_offline_file_invalid', 'Output must not already exist', 422)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.part')
    expected = {m['filename']: m for m in clean['members']}
    seen = set()
    excluded = {p.strip('/') for p in model['excluded_paths']}
    published = False
    try:
        with tarfile.open(source, 'r|bz2') as src, tarfile.open(temporary, 'w', format=tarfile.USTAR_FORMAT) as dst:
            for member in src:
                check_cancel(cancel)
                name = safe_relative(member.name)
                if not (member.isdir() or member.isfile()):
                    raise VoiceError('voice_archive_invalid', 'Links and special files are forbidden', 422)
                if member.isdir() or excluded.intersection(name.parts):
                    continue
                key = name.as_posix()
                if key not in expected or key in seen or member.size != expected[key]['size_bytes']:
                    raise VoiceError('voice_archive_invalid', 'Unexpected, duplicate or changed model member', 422)
                seen.add(key)
                info = tarfile.TarInfo(key)
                info.size, info.mode, info.mtime = member.size, 0o644, 0
                dst.addfile(info, src.extractfile(member))
            if seen != set(expected):
                raise VoiceError('voice_archive_invalid', 'Model member missing', 422)
            # Original archive has no license file. Preserve exact reviewed Apache
            # text as a separate addition; never invent an upstream NOTICE.
            notice = clean['license']
            license_path = Path(__file__).resolve().parents[4] / 'third_party/licenses' / notice['filename']
            if not verify(license_path, notice):
                raise VoiceError('voice_offline_notice_invalid', 'Model license is missing or changed', 422)
            info = tarfile.TarInfo(clean['license_member'])
            info.size, info.mode, info.mtime = notice['size_bytes'], 0o644, 0
            dst.addfile(info, io.BytesIO(license_path.read_bytes()))
        check_cancel(cancel)
        if not verify(temporary, clean):
            raise VoiceError('voice_sha256_mismatch', 'Sanitized model differs from reviewed package', 422)
        # Exclusive publish also protects an input at the requested output path.
        with destination.open('xb') as dst, temporary.open('rb') as src:
            published = True
            import shutil
            shutil.copyfileobj(src, dst)
        if not verify(destination, clean):
            destination.unlink(missing_ok=True)
            raise VoiceError('voice_sha256_mismatch', 'Sanitized package changed while publishing', 422)
        return destination
    except Exception:
        if published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
