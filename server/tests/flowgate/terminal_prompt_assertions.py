"""Shared assertions on delivered instructions, independent of the formatter parser."""
HEADERS = ('## 지금 할 일', '## What to do now', '## 今やること')


def assert_terminal(text):
    assert '<!-- flowgate-terminal-task:' not in text
    lines = text.splitlines()
    positions = [i for i, line in enumerate(lines) if line in HEADERS]
    assert len(positions) == 1
    start = positions[0]
    assert len(lines) - start == 5
    assert text.endswith(lines[-1])
    assert all(lines[start + i] for i in range(5))
    assert max(map(len, lines[start:])) <= 500
    return '\n'.join(lines[:start]).rstrip()
