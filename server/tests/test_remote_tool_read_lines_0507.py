"""Remote tool read — line-based selector and unknown-field rejection
(group 0507 T0004 / NR0003).

NR0003: an AI worker asked for start_line=1590/end_line=1640 (~50 lines) on a
196KB source file. Because those fields were not in the read contract, they
were silently dropped and the whole file came back — prompt tokens for the
next model call jumped from ~15K to ~72K. This file covers the fix:
  - unknown fields on `read` are rejected (422), never silently dropped (§1)
  - start_line/end_line is a real selector that returns only that window (§2)
  - the response carries enough metadata to prove the window was honored (§3)

Byte-window (offset/length) read behaviour is already covered by
test_remote_tool_patch_stat_0347.py and is not repeated here.

Harness (env fixture / _call) is shared with test_remote_tool_0003_T0012.
"""
from __future__ import annotations

import pytest

from test_remote_tool_0003_T0012 import RAW_TOKEN, _call, env  # noqa: F401  (env is a fixture)


def _write(env, rel: str, text: str) -> None:
    target = env.src / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _numbered_lines(n: int) -> str:
    return "".join(f"line {i}\n" for i in range(1, n + 1))


# ── unknown field → 422 (never a silent whole-file fallback) ────────────────

def test_read_unknown_field_is_422(env):
    env.make_grant(["read"])
    _write(env, "app/big.py", _numbered_lines(50))
    status, payload = _call(
        "read", {"path": "app/big.py", "start_lin": 1, "end_lin": 5}
    )
    assert status == 422
    assert payload["error"]["details"]["reason"] == "unknown_field"
    assert payload["error"]["details"]["fields"] == ["end_lin", "start_lin"]


def test_read_unknown_field_alongside_known_fields_is_still_422(env):
    env.make_grant(["read"])
    _write(env, "app/big.py", _numbered_lines(50))
    status, payload = _call("read", {"path": "app/big.py", "bogus": True})
    assert status == 422
    assert payload["error"]["details"]["reason"] == "unknown_field"
    assert payload["error"]["details"]["fields"] == ["bogus"]


def test_read_allowed_fields_are_unaffected_by_the_unknown_field_check(env):
    env.make_grant(["read"])
    _write(env, "app/big.py", _numbered_lines(50))
    status, payload = _call(
        "read", {"path": "app/big.py", "max_bytes": 10, "offset": 0, "length": 5, "encoding": "utf-8"}
    )
    assert status == 200


# ── start_line/end_line — the NR0003 regression itself ──────────────────────

def test_read_line_range_returns_only_the_requested_lines_not_the_whole_file(env):
    env.make_grant(["read"])
    content = _numbered_lines(2000)  # a "big" file, mirrors the 0507 incident shape
    _write(env, "app/big.py", content)

    status, payload = _call("read", {"path": "app/big.py", "start_line": 1590, "end_line": 1640})

    assert status == 200
    assert payload["ok"] is True
    expected = "".join(f"line {i}\n" for i in range(1590, 1641))
    assert payload["content"] == expected
    assert payload["start_line"] == 1590
    assert payload["end_line"] == 1640
    assert payload["returned_start_line"] == 1590
    assert payload["returned_end_line"] == 1640
    assert payload["total_lines"] == 2000
    assert payload["eof"] is False
    assert payload["truncated"] is True
    # the whole point of the fix: the response is the window, not the whole file
    assert payload["returned_bytes"] < len(content.encode("utf-8")) // 10


def test_read_line_range_from_start_of_file(env):
    env.make_grant(["read"])
    _write(env, "app/small.py", _numbered_lines(10))
    status, payload = _call("read", {"path": "app/small.py", "start_line": 1, "end_line": 3})
    assert status == 200
    assert payload["content"] == "line 1\nline 2\nline 3\n"
    assert payload["returned_start_line"] == 1
    assert payload["returned_end_line"] == 3
    assert payload["total_lines"] == 10
    assert payload["eof"] is False


def test_read_single_line_selector(env):
    env.make_grant(["read"])
    _write(env, "app/small.py", _numbered_lines(5))
    status, payload = _call("read", {"path": "app/small.py", "start_line": 3, "end_line": 3})
    assert status == 200
    assert payload["content"] == "line 3\n"
    assert payload["returned_start_line"] == 3
    assert payload["returned_end_line"] == 3


def test_read_line_range_reaching_last_line_reports_eof(env):
    env.make_grant(["read"])
    _write(env, "app/small.py", _numbered_lines(10))
    status, payload = _call("read", {"path": "app/small.py", "start_line": 8, "end_line": 10})
    assert status == 200
    assert payload["content"] == "line 8\nline 9\nline 10\n"
    assert payload["eof"] is True
    assert payload["truncated"] is False


def test_read_line_range_past_eof_is_an_empty_window_not_an_error(env):
    env.make_grant(["read"])
    _write(env, "app/small.py", _numbered_lines(10))
    status, payload = _call("read", {"path": "app/small.py", "start_line": 50, "end_line": 60})
    assert status == 200
    assert payload["content"] == ""
    assert payload["eof"] is True
    assert payload["truncated"] is False
    assert payload["total_lines"] == 10


def test_read_line_range_end_line_beyond_eof_clamps_to_available_lines(env):
    env.make_grant(["read"])
    _write(env, "app/small.py", _numbered_lines(10))
    status, payload = _call("read", {"path": "app/small.py", "start_line": 8, "end_line": 500})
    assert status == 200
    assert payload["content"] == "line 8\nline 9\nline 10\n"
    assert payload["returned_end_line"] == 10
    assert payload["eof"] is True
    assert payload["truncated"] is False


def test_read_line_range_on_empty_file(env):
    env.make_grant(["read"])
    _write(env, "app/empty.py", "")
    status, payload = _call("read", {"path": "app/empty.py", "start_line": 1, "end_line": 5})
    assert status == 200
    assert payload["content"] == ""
    assert payload["total_lines"] == 0
    assert payload["eof"] is True


# ── invalid line ranges → 422 ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "body",
    [
        {"start_line": 0, "end_line": 5},
        {"start_line": -1, "end_line": 5},
        {"start_line": 5, "end_line": 1},
        {"start_line": 1.5, "end_line": 5},
        {"start_line": True, "end_line": 5},
        {"start_line": "1", "end_line": 5},
        {"start_line": 5},  # end_line missing
        {"end_line": 5},  # start_line missing
        {"start_line": 1, "end_line": 0},
        {"start_line": 1, "end_line": -3},
    ],
)
def test_read_invalid_line_range_is_422(env, body):
    env.make_grant(["read"])
    _write(env, "app/small.py", _numbered_lines(10))
    status, payload = _call("read", {"path": "app/small.py", **body})
    assert status == 422
    assert payload["error"]["details"]["reason"] == "invalid_line_range"


# ── mixing line range with byte window → 422 ─────────────────────────────────

@pytest.mark.parametrize("extra", [{"offset": 0}, {"length": 10}, {"max_bytes": 10}])
def test_read_line_range_mixed_with_byte_window_is_422(env, extra):
    env.make_grant(["read"])
    _write(env, "app/small.py", _numbered_lines(10))
    body = {"path": "app/small.py", "start_line": 1, "end_line": 3}
    body.update(extra)
    status, payload = _call("read", body)
    assert status == 422
    assert payload["error"]["details"]["reason"] == "line_and_byte_selector"


# ── UTF-8 multibyte content is not corrupted by line slicing ────────────────

def test_read_line_range_with_multibyte_utf8_is_not_corrupted(env):
    env.make_grant(["read"])
    text = "".join(f"{i}번째 가나다라 줄\n" for i in range(1, 21))
    _write(env, "docs/kor.txt", text)
    status, payload = _call("read", {"path": "docs/kor.txt", "start_line": 5, "end_line": 7})
    assert status == 200
    expected = "".join(f"{i}번째 가나다라 줄\n" for i in range(5, 8))
    assert payload["content"] == expected


# ── large-file partial read never leaks the whole file into the response ────

def test_read_line_range_on_large_file_does_not_include_the_whole_file(env):
    env.make_grant(["read"])
    # ~1MB file, ask for 50 lines near the middle
    content = _numbered_lines(20000)
    _write(env, "app/huge.py", content)
    full_size = len(content.encode("utf-8"))

    status, payload = _call("read", {"path": "app/huge.py", "start_line": 10000, "end_line": 10050})

    assert status == 200
    assert payload["size"] == full_size  # size still reports the true file size
    assert payload["returned_bytes"] < full_size // 100  # but content is a tiny slice
    assert payload["content"].count("\n") == 51


def test_read_line_range_over_limit_is_413(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service as svc

    env.make_grant(["read"])
    monkeypatch.setattr(svc, "_MAX_READ_BYTES", 8)
    _write(env, "app/small.py", _numbered_lines(10))

    status, payload = _call("read", {"path": "app/small.py", "start_line": 1, "end_line": 2})
    assert status == 413
    assert payload["error"]["code"] == "too_large"


# ── committed-tree (ref) reads get the same line selector ───────────────────

def test_exec_ref_read_line_range_returns_only_the_requested_lines(tmp_path):
    """Direct unit test on _exec_ref_read: a ref read must not reopen the
    start_line/end_line -> whole-blob hole via the committed-tree path."""
    import subprocess

    from modules.flow_gate.services import remote_tool_service as svc

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "big.py").write_text(_numbered_lines(100), encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "big.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    extra, bytes_processed = svc._exec_ref_read(
        {"path": "big.py", "ref": "HEAD", "start_line": 10, "end_line": 12}, root
    )
    assert extra["content"] == "line 10\nline 11\nline 12\n"
    assert extra["returned_start_line"] == 10
    assert extra["returned_end_line"] == 12
    assert extra["total_lines"] == 100
    assert extra["eof"] is False
    assert bytes_processed == len(extra["content"].encode("utf-8"))


# ── Help contract sync ────────────────────────────────────────────────────────

def test_help_read_request_fields_match_the_allowed_field_set():
    from modules.flow_gate.services import remote_tool_service as svc
    from modules.flow_gate.services import tool_registry

    for locale in ("ko", "ja", "en"):
        fields = tool_registry._request_fields("read", locale)
        names = {f["name"] for f in fields}
        assert names == svc._ALLOWED_FIELDS["read"]
