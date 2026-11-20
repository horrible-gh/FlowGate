"""Behavioral tests for the content-based Korean comment ratchet."""
from collections import Counter
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("korean_comment_baseline", ROOT / "tools/korean_comment_baseline.py")
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

OLD = "\uae30\uc874 \uc8fc\uc11d"
NEW = "\uc0c8 \uc8fc\uc11d"
LABEL = "\uc2e4\ud589"


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_repository_comments_match_baseline():
    findings = guard.scan(ROOT)
    baseline = guard.load_baseline((ROOT / guard.BASELINE).read_text(encoding="utf-8"))
    assert not (messages := guard.differences(findings, baseline)), "\n".join(messages)


def test_added_comment_is_rejected(tmp_path):
    put(tmp_path, "server/test.py", f"value = 1  # {NEW}\n")
    messages = guard.differences(guard.scan(tmp_path), Counter())
    assert len(messages) == 1
    assert f"server/test.py:1: # {NEW}" == messages[0]


def test_same_count_replacement_and_duplicate_are_rejected(tmp_path):
    path = put(tmp_path, "server/test.py", f"# {OLD}\n")
    baseline = guard.counts(guard.scan(tmp_path))
    path.write_text(f"# {NEW}\n", encoding="utf-8")
    assert len(guard.differences(guard.scan(tmp_path), baseline)) == 2
    path.write_text(f"# {OLD}\n# {OLD}\n", encoding="utf-8")
    assert len(guard.differences(guard.scan(tmp_path), baseline)) == 1


def test_runtime_expectation_strings_are_not_comments(tmp_path):
    put(tmp_path, "server/test.py", f"expected = {NEW!r}\nassert expected == {NEW!r}\n")
    assert guard.scan(tmp_path) == []


def test_localized_file_is_exempt(tmp_path):
    put(tmp_path, "client_gui/src/app/i18n/ko.ts", f"// {NEW}\nexport default {{ title: '{LABEL}' }}\n")
    assert guard.scan(tmp_path) == []


def test_only_exact_runtime_quotes_are_exempt(tmp_path):
    put(tmp_path, "server/test.py", f"label = '{LABEL}'\n# Shows \"{LABEL}\"\n# Opens [{LABEL}]\n# Shows 「{LABEL}」\n")
    assert guard.scan(tmp_path) == []
    put(tmp_path, "server/apostrophe.py", f"# A card's [{LABEL}] is checked by the server's code.\n")
    assert guard.scan(tmp_path) == []
    put(tmp_path, "server/unknown.py", f"# Shows \"{NEW}\"\n")
    findings = guard.scan(tmp_path)
    assert len(findings) == 1
    assert findings[0][0] == "server/unknown.py"


def test_docstrings_are_comments_not_literal_evidence(tmp_path):
    put(tmp_path, "server/test.py", f'"""{NEW}"""\n# Shows "{NEW}"\n')
    assert len(guard.scan(tmp_path)) == 2


def test_documents_do_not_use_quote_exemption(tmp_path):
    put(tmp_path, "server/test.py", f"label = '{LABEL}'\n")
    put(tmp_path, "README.md", f'"{LABEL}"\n')
    assert len(guard.scan(tmp_path)) == 1


def test_baseline_can_only_shrink_and_removed_lines_require_update(tmp_path):
    source = put(tmp_path, "server/test.py", f"# {OLD}\n")
    baseline_path = tmp_path / guard.BASELINE
    baseline = guard.counts(guard.scan(tmp_path))
    guard.update_baseline(baseline_path, baseline)
    source.write_text(f"# {NEW}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing"):
        guard.update_baseline(baseline_path, guard.counts(guard.scan(tmp_path)))
    assert guard.load_baseline(baseline_path.read_text()) == baseline
    source.write_text("# Translated comment\n", encoding="utf-8")
    assert guard.differences(guard.scan(tmp_path), baseline)
    guard.update_baseline(baseline_path, guard.counts(guard.scan(tmp_path)))
    assert guard.load_baseline(baseline_path.read_text()) == Counter()


def test_sql_literals_and_dollar_strings_are_not_comments(tmp_path):
    put(tmp_path, "server/sql/test.sql", f"SELECT '-- {NEW}', $$/* {NEW} */$$, 'it''s {NEW}';\n-- {OLD}\n/* {NEW} */\n")
    assert [line for _, line, _ in guard.scan(tmp_path)] == [2, 3]


def test_javascript_templates_regex_and_vue_comments(tmp_path):
    put(tmp_path, "client_gui/src/test.ts", f'''const label = '{LABEL}';
const value = `// {NEW} ${{label /* {OLD} */}}`;
const pattern = /[/*]{NEW}/;
// Shows "{LABEL}"
// {NEW}
''')
    findings = guard.scan(tmp_path)
    assert [line for _, line, _ in findings] == [2, 5]
    put(tmp_path, "client_gui/src/Example.vue", f'''<template>
<div title="{LABEL}">{LABEL}<!-- {OLD} --></div>
</template>
<script setup lang="ts">
const label = '{LABEL}'; // {NEW}
</script>
<style>/* {OLD} */ .x {{ color: red; }}</style>
''')
    findings = guard.scan(tmp_path)
    assert [line for path, line, _ in findings if path.endswith('.vue')] == [2, 5, 7]


def test_invalid_python_fails_closed(tmp_path):
    put(tmp_path, "server/broken.py", f"def broken(  # {NEW}\n")
    with pytest.raises(ValueError, match="Cannot scan server/broken.py"):
        guard.scan(tmp_path)
