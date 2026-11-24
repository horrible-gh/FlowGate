"""Executed inside a built image by build_notice_sbom.py; emits evidence as JSON."""
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path


def collect():
    root = Path('/app')
    for path in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'third_party/licenses'):
        if not (root / path).exists():
            raise RuntimeError(f'missing image notice: {path}')
    evidence = []
    required = ('psycopg2-binary', 'certifi', 'passlib')
    optional = ('Pillow',)
    for name in required + optional:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            if name in required:
                raise
            evidence.append({'name': name, 'status': 'not-installed', 'required': False})
            continue
        notices = []
        native = []
        for item in dist.files or []:
            value = str(item)
            path = Path(dist.locate_file(item))
            if '.dist-info/' in value and any(word in item.name.lower() for word in ('license', 'copying', 'notice')):
                data = path.read_bytes()
                if not data:
                    raise RuntimeError(f'empty installed license: {path}')
                notices.append({'path': value, 'sha256': hashlib.sha256(data).hexdigest()})
            if '.so' in item.name or item.suffix.lower() in ('.dll', '.pyd', '.dylib'):
                native.append(value)
        if not notices:
            raise RuntimeError(f'installed dist-info license missing: {name}')
        evidence.append({'name': name, 'version': dist.version, 'notices': notices, 'native_files': native})
    return evidence


if __name__ == '__main__':
    print(json.dumps(collect(), indent=2))
