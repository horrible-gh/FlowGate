"""Behavioral coverage for the internal work-reference comment ratchet."""
from collections import Counter
import importlib.util
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.area("guard")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
try:
    SPEC = importlib.util.spec_from_file_location(
        "internal_comment_baseline", ROOT / "tools/internal_comment_baseline.py")
    guard = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(guard)
finally:
    sys.path.pop(0)


def test_repository_has_no_new_internal_comment_references():
    baseline = guard.syntax.load_baseline((ROOT / guard.BASELINE).read_text(encoding="utf-8"))
    assert not (messages := guard.differences(guard.scan(ROOT), baseline)), "\n".join(messages)


def test_runtime_strings_and_external_numbers_are_not_references():
    source = 'value = "flowgate.v02.0123 T0004"\n# RFC 7231, HTTP 403, port 8080, year 2026.\n'
    assert guard.scan_sources({"server/example.py": source}) == []


def test_comments_and_docstrings_are_scanned():
    source = '"""Contract (D0006)."""\nvalue = 1  # flowgate.v02.0123 T0004\n'
    findings = guard.scan_sources({"server/example.py": source})
    assert [row[1] for row in findings] == [1, 2]
    assert len(guard.additions(findings, Counter())) == 2


def test_replacement_duplicate_and_new_path_cannot_spend_removed_entries():
    old = [("server/example.py", 1, "# T0004: Keep the lock.")]
    baseline = guard.syntax.counts(old)
    assert guard.additions([], baseline) == []
    assert "Stale baseline" in guard.differences([], baseline)[0]
    assert guard.additions([(old[0][0], 20, old[0][2])], baseline) == []
    assert len(guard.additions(old * 2, baseline)) == 1
    assert guard.additions([("server/example.py", 1, "# T0005: Keep the lock.")], baseline)
    assert guard.additions([("server/other.py", 1, old[0][2])], baseline)


def test_fixtures_and_migrations_are_excluded():
    sources = {
        "server/tests/fixtures/example.py": "# T0004: archived source\n",
        "server/migrations/example.py": "# T0004: immutable migration\n",
    }
    assert guard.scan_sources(sources) == []


def test_korean_reference_is_preserved_but_not_exempt_from_new_entries():
    findings = guard.scan_sources({"server/example.py": "# T0004: \uc2e4\ud589\n"})
    assert guard.additions(findings, Counter())


def test_javascript_strings_templates_and_vue_comments():
    sources = {
        "client_gui/src/example.ts": 'const text = `// T0004 ${1 /* T0005 */}`;\n// T0006\n',
        "client_gui/src/Example.vue": '<template><div title="T0004"><!-- T0005 --></div></template>\n',
    }
    findings = guard.scan_sources(sources)
    assert len(findings) == 3
    assert all("T0004" not in comment for _, _, comment in findings)


def test_invalid_source_fails_closed():
    with pytest.raises(SyntaxError):
        guard.scan_sources({"server/example.py": "def broken( # T0004\n"})
