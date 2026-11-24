"""F86 R3/R6: direct-import license policy and target lock integrity."""
import ast
import importlib.metadata as metadata
import os
from pathlib import Path
import re

import pytest
from packaging.requirements import Requirement

SERVER = Path(__file__).resolve().parents[1]
# Explicit exception requested by R3-3; native notices are tracked separately in R6-2.
EXCEPTIONS = {"psycopg2", "psycopg2-binary"}
KNOWN_RESTRICTED = {"unidecode", "text_unidecode", "chardet", "fuzzywuzzy", "Levenshtein", "MySQLdb", "pylint"}


def direct_imports(source):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            yield node.module.split('.')[0]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == '__import__') or (
                isinstance(func, ast.Attribute) and func.attr == 'import_module'):
                value = node.args[0].value
                if isinstance(value, str) and not value.startswith('.'):
                    yield value.split('.')[0]


def restricted(meta):
    expression = meta.get('License-Expression', '')
    # A documented permissive alternative is usable; AND does not remove GPL obligations.
    if expression and ' OR ' in expression and re.search(r'\b(MIT|BSD-[23]-Clause|ISC|Apache-2.0)\b', expression):
        return False
    labels = expression + '\n' + meta.get('License', '') + '\n' + '\n'.join(meta.get_all('Classifier', []))
    return bool(re.search(r'\b(?:A?GPL|LGPL)(?:\b|[-v])|GNU (?:Lesser |Affero |Library )?General Public', labels, re.I))


def test_no_restricted_direct_imports():
    mapping = metadata.packages_distributions()
    blocked = set(KNOWN_RESTRICTED)
    for module, distributions in mapping.items():
        for distribution in distributions:
            if distribution.lower() not in EXCEPTIONS and restricted(metadata.metadata(distribution)):
                blocked.add(module)
    violations = []
    for root, dirs, files in os.walk(SERVER):
        dirs[:] = [d for d in dirs if d not in {'tests', '.venv', 'venv', '__pycache__', 'node_modules', '.git'}]
        for name in files:
            if name.endswith('.py'):
                path = Path(root) / name
                for module in direct_imports(path.read_text(encoding='utf-8-sig')):
                    if module in blocked and module not in EXCEPTIONS:
                        violations.append(f'{path.relative_to(SERVER)}: {module}')
    assert not violations, '\n'.join(violations)


@pytest.mark.parametrize('source', ['import unidecode as u', 'from unidecode import unidecode', 'importlib.import_module("unidecode")', '__import__("unidecode")'])
def test_import_detector(source):
    assert 'unidecode' in set(direct_imports(source))


def test_license_classifier():
    from email.message import Message
    for label, expected in [('GPL-3.0-only', True), ('LGPL-2.1-only', True), ('MIT', False), ('GPL-3.0-only OR MIT', False), ('GPL-3.0-only AND MIT', True)]:
        meta = Message()
        meta['License-Expression'] = label
        assert restricted(meta) == expected


def lock_entries(path):
    entries = {}
    for line in path.read_text(encoding='utf-8').replace('\\\n', ' ').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        declaration, *hashes = line.split('--hash=sha256:')
        requirement = Requirement(declaration.strip())
        assert len(requirement.specifier) == 1
        specifier = next(iter(requirement.specifier))
        assert specifier.operator == '==' and '*' not in specifier.version
        assert hashes and all(re.fullmatch(r'[0-9a-f]{64}', h.strip()) for h in hashes)
        entries[requirement.name.lower().replace('_', '-')] = specifier.version
    return entries


@pytest.mark.parametrize('target,platform', [('linux', 'linux'), ('windows', 'win32')])
def test_target_locks(target, platform):
    from packaging.markers import default_environment
    env = default_environment()
    env.update(python_version='3.12', python_full_version='3.12.0', sys_platform=platform,
               os_name='nt' if platform == 'win32' else 'posix', platform_system='Windows' if platform == 'win32' else 'Linux')
    locked = lock_entries(SERVER / 'requirements' / f'{target}-py312.lock')
    assert 'unidecode' not in locked
    for line in (SERVER / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        req = Requirement(line)
        assert len(req.specifier) == 1
        spec = next(iter(req.specifier))
        assert spec.operator == '==' and '*' not in spec.version
        if req.marker is None or req.marker.evaluate(env):
            assert locked[req.name.lower().replace('_', '-')] == spec.version
    assert ('pywinpty' in locked) == (target == 'windows')
    assert ('psutil' in locked) == (target == 'windows')
    assert ('uvloop' in locked) == (target == 'linux')
