"""Remote tool patch / read offset / stat — pipeline tests (group 0347 R0001).

Covers P0004 (message format) · L0005 (processing logic) · DB0006 (op enum):
  patch  — exact match, 0건 404 / 2건 이상 409, all-or-nothing, CRLF 2단계, strict decode
  read   — offset/length byte window, character-boundary correction, legacy compatibility
  stat   — metadata only, 부재는 200(exists=false), 비변이 op

The op_log assertions double as the DB0006/073 migration check: `_log` swallows a
failed insert, so a `patch`/`stat` row that is actually present is the proof that
the widened `remote_tool_op_log.op` CHECK constraint is in place.

Harness (env fixture / _call) is shared with test_remote_tool_0003_T0012.
"""
from __future__ import annotations

import re

import pytest

from test_remote_tool_0003_T0012 import RAW_TOKEN, _call, env  # noqa: F401  (env is a fixture)

_MTIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")


def _write(env, rel: str, data: bytes) -> None:
    target = env.src / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _read(env, rel: str) -> bytes:
    return (env.src / rel).read_bytes()


# ── op registry ───────────────────────────────────────────────────────────────

def test_ops_and_scopes_registered():
    from modules.flow_gate.services import remote_tool_service as svc

    # 0482 T0011 added the group-less base-dirty decision op; it is a real entry in the
    # op registry (scope "write"), advertised only to its own action_scope.
    assert set(svc.OPS) == {"read", "write", "grep", "glob", "remove", "patch", "stat", "diff", "log",
                            "show", "merge_preview", "resolve_base_dirty"}
    # P0004 §0.4 — no new scope value, so remote_tool_grant_scope needs no migration.
    assert svc.OP_SCOPE["patch"] == "write"
    assert svc.OP_SCOPE["stat"] == "read"
    # P0004 §0.5 — patch mutates, stat does not.
    assert svc._MUTATING_OPS == {"write", "remove", "patch", "resolve_base_dirty"}
    assert "stat" not in svc._MUTATING_OPS
    assert svc._PATH_VALIDATE_SINGLE_FIELD_OPS >= {"patch", "stat"}


# ── patch — success ───────────────────────────────────────────────────────────

def test_patch_replaces_single_match_and_leaves_rest_byte_identical(env):
    env.make_grant(["write"], report_doc_id="flowgate.default.0347.0010-TR")
    before = _read(env, "app/main.py")

    status, payload = _call(
        "patch",
        {"path": "app/main.py", "old_string": "# TODO: validate", "new_string": "# done"},
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["op"] == "patch"
    assert payload["replacements"] == 1
    assert payload["size_before"] == len(before)
    assert payload["encoding"] == "utf-8"
    assert payload["eol"] == "lf"
    assert payload["eol_normalized"] is False

    after = _read(env, "app/main.py")
    assert after == before.replace(b"# TODO: validate", b"# done")
    assert payload["size_after"] == len(after)
    assert payload["bytes_written"] == len(after)
    # untouched lines keep their exact bytes — the whole point of patch over write
    assert after.startswith(b"import sys\n")
    assert after.endswith(b"print('hi')\n")

    log = env.oplogs()[0]
    assert log["op"] == "patch"
    assert log["result"] == "success"
    assert log["error_code"] is None
    assert log["target_path"] == "app/main.py"
    assert log["target_pattern"] is None
    assert log["bytes_processed"] == payload["size_after"]

    # ⑦ mutating op → continuation ment
    assert payload["continuation"]["next_action"] == "write_report"
    assert payload["continuation"]["report_doc_id"] == "flowgate.default.0347.0010-TR"


def test_patch_new_string_may_be_empty_deleting_the_match(env):
    env.make_grant(["write"])
    status, payload = _call(
        "patch",
        {"path": "app/main.py", "old_string": "# TODO: validate\n", "new_string": ""},
    )
    assert status == 200
    assert payload["replacements"] == 1
    assert _read(env, "app/main.py") == b"import sys\nprint('hi')\n"


def test_patch_replace_all_replaces_every_match(env):
    env.make_grant(["write"])
    _write(env, "app/dup.py", b"x = 1\nx = 1\nx = 1\n")

    status, payload = _call(
        "patch",
        {"path": "app/dup.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True},
    )

    assert status == 200
    assert payload["replacements"] == 3
    assert _read(env, "app/dup.py") == b"x = 2\nx = 2\nx = 2\n"


def test_patch_emits_explorer_refresh(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service as svc

    env.make_grant(["write"])
    seen: list[str] = []
    monkeypatch.setattr(svc, "_emit_explorer_refresh", lambda _g, op: seen.append(op))

    status, _ = _call(
        "patch", {"path": "app/main.py", "old_string": "print('hi')", "new_string": "print('yo')"}
    )
    assert status == 200
    assert seen == ["patch"]


def test_patch_success_carries_the_continuation_ment(env):
    """Third and last `_MUTATING_OPS` site (⑦, L0006 §6.1). The gate and the SSE
    are asserted above; without this the ment site could still read the old
    ("write", "remove") literal and patch would silently answer without a ment."""
    env.make_grant(["write"], report_doc_id="flowgate.default.0347.0010-TR")

    status, payload = _call(
        "patch", {"path": "app/main.py", "old_string": "print('hi')", "new_string": "print('yo')"}
    )

    assert status == 200
    cont = payload["continuation"]
    assert cont["next_action"] == "write_report"
    assert cont["report_doc_id"] == "flowgate.default.0347.0010-TR"


def test_patch_failure_carries_no_continuation_ment(env):
    """⑦ fires only on a successful state change — a 404 changed nothing."""
    env.make_grant(["write"], report_doc_id="flowgate.default.0347.0010-TR")

    status, payload = _call(
        "patch", {"path": "app/main.py", "old_string": "nowhere at all", "new_string": "x"}
    )

    assert status == 404
    assert "continuation" not in payload


def test_patch_uses_the_mutation_root_gate_not_the_read_fallback(env, monkeypatch):
    """P0004 §0.5: patch must go through _resolve_root_for_mutation, or a missing
    group worktree would silently drop the edit into the base checkout (0205)."""
    from modules.flow_gate.services import remote_tool_service as svc

    env.make_grant(["write"])
    calls: list[str] = []
    original = svc._resolve_root_for_mutation
    monkeypatch.setattr(
        svc, "_resolve_root_for_mutation",
        lambda grant, op: (calls.append(f"mutation:{op}"), original(grant, op))[1],
    )
    monkeypatch.setattr(
        svc, "_resolve_src_root",
        lambda grant, op="read": calls.append(f"read:{op}"),
    )

    status, _ = _call(
        "patch", {"path": "app/main.py", "old_string": "import sys", "new_string": "import os"}
    )
    assert status == 200
    assert calls == ["mutation:patch"]


# ── patch — match failures (the core contract) ────────────────────────────────

def test_patch_no_match_is_404_and_file_untouched(env):
    env.make_grant(["write"])
    before = _read(env, "app/main.py")

    status, payload = _call(
        "patch", {"path": "app/main.py", "old_string": "nowhere in the file", "new_string": "x"}
    )

    assert status == 404
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["details"]["reason"] == "no_match"
    assert payload["error"]["details"]["match_count"] == 0
    assert payload["error"]["details"]["path"] == "app/main.py"
    assert _read(env, "app/main.py") == before

    log = env.oplogs()[0]
    assert log["op"] == "patch"
    assert log["result"] == "not_found"
    assert log["error_code"] == "not_found"
    assert log["bytes_processed"] is None


def test_patch_multiple_matches_is_409_and_file_untouched(env):
    env.make_grant(["write"])
    _write(env, "app/dup.py", b"x = 1\nx = 1\nx = 1\n")
    before = _read(env, "app/dup.py")

    status, payload = _call(
        "patch", {"path": "app/dup.py", "old_string": "x = 1", "new_string": "x = 2"}
    )

    assert status == 409
    assert payload["error"]["code"] == "conflict"
    assert payload["error"]["details"]["reason"] == "multiple_matches"
    assert payload["error"]["details"]["match_count"] == 3
    assert "replace_all" in payload["error"]["message"]
    assert _read(env, "app/dup.py") == before

    log = env.oplogs()[0]
    assert log["result"] == "conflict"
    assert log["error_code"] == "conflict"
    assert log["bytes_processed"] is None


def test_patch_worktree_conflict_and_match_conflict_are_distinguishable(env):
    """Both are 409; P0004 §1.3 says the client tells them apart by details.reason."""
    env.make_grant(["write"])
    _write(env, "app/dup.py", b"x = 1\nx = 1\n")
    _, payload = _call("patch", {"path": "app/dup.py", "old_string": "x = 1", "new_string": "y"})
    details = payload["error"]["details"]
    assert "reason" in details and "cause" not in details


# ── patch — request validity (④) ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "body",
    [
        {"path": "app/main.py", "old_string": "", "new_string": "x"},          # empty old_string
        {"path": "app/main.py", "old_string": "import sys"},                    # new_string missing
        {"path": "app/main.py", "old_string": "import sys", "new_string": 1},   # new_string type
        {"old_string": "import sys", "new_string": "x"},                        # path missing
        {"path": "app/main.py", "old_string": "a", "new_string": "b", "replace_all": "yes"},
        {"path": "app/main.py", "old_string": "a", "new_string": "b", "encoding": "no-such-enc"},
    ],
)
def test_patch_invalid_request_422(env, body):
    env.make_grant(["write"])
    before = _read(env, "app/main.py")
    status, payload = _call("patch", body)
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"
    assert _read(env, "app/main.py") == before


def test_patch_identical_strings_is_422_no_op_edit(env):
    env.make_grant(["write"])
    status, payload = _call(
        "patch", {"path": "app/main.py", "old_string": "import sys", "new_string": "import sys"}
    )
    assert status == 422
    assert payload["error"]["details"]["reason"] == "no_op_edit"


def test_patch_path_escape_is_422(env):
    env.make_grant(["write"])
    status, payload = _call(
        "patch", {"path": "../escape.py", "old_string": "a", "new_string": "b"}
    )
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"


def test_patch_without_write_scope_is_403(env):
    env.make_grant(["read", "grep"])   # investigation-style token
    before = _read(env, "app/main.py")
    status, payload = _call(
        "patch", {"path": "app/main.py", "old_string": "import sys", "new_string": "import os"}
    )
    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    assert _read(env, "app/main.py") == before
    assert env.oplogs()[0]["result"] == "denied"


# ── patch — target kind / encoding ────────────────────────────────────────────

def test_patch_missing_file_is_404_and_does_not_create_it(env):
    env.make_grant(["write"])
    status, payload = _call(
        "patch", {"path": "app/absent.py", "old_string": "a", "new_string": "b"}
    )
    assert status == 404
    assert payload["error"]["details"]["reason"] == "file_not_found"
    assert not (env.src / "app" / "absent.py").exists()


def test_patch_directory_target_is_404_not_a_file(env):
    env.make_grant(["write"])
    status, payload = _call("patch", {"path": "docs", "old_string": "a", "new_string": "b"})
    assert status == 404
    assert payload["error"]["details"]["reason"] == "not_a_file"


def test_patch_binary_file_is_422_not_text_and_untouched(env):
    """strict decode: a lenient one would write U+FFFD back over the whole file."""
    env.make_grant(["write"])
    blob = b"\x00\x01\xff\xfe binary \x80\x81\n"
    _write(env, "app/logo.bin", blob)

    status, payload = _call(
        "patch", {"path": "app/logo.bin", "old_string": "binary", "new_string": "text"}
    )

    assert status == 422
    assert payload["error"]["details"]["reason"] == "not_text"
    assert _read(env, "app/logo.bin") == blob


def test_patch_preserves_leading_bom(env):
    env.make_grant(["write"])
    _write(env, "app/bom.py", "﻿alpha\nbeta\n".encode("utf-8"))

    status, _ = _call(
        "patch", {"path": "app/bom.py", "old_string": "beta", "new_string": "gamma"}
    )

    assert status == 200
    assert _read(env, "app/bom.py") == "﻿alpha\ngamma\n".encode("utf-8")


def test_patch_result_over_size_limit_is_413_and_file_untouched(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service as svc

    env.make_grant(["write"])
    monkeypatch.setattr(svc, "_MAX_WRITE_BYTES", 20)
    before = _read(env, "app/main.py")

    status, payload = _call(
        "patch", {"path": "app/main.py", "old_string": "import sys", "new_string": "import sys" * 5}
    )

    assert status == 413
    assert payload["error"]["code"] == "too_large"
    assert _read(env, "app/main.py") == before


# ── patch — EOL handling (P0004 §1.5) ─────────────────────────────────────────

def test_patch_crlf_file_matches_lf_old_string_via_second_pass(env):
    env.make_grant(["write"])
    _write(env, "app/win.vue", b"const a = 1\r\nconst b = 2\r\nconst c = 3\r\n")

    status, payload = _call(
        "patch",
        {
            "path": "app/win.vue",
            "old_string": "const a = 1\nconst b = 2",
            "new_string": "const a = 1\nconst b = 9",
        },
    )

    assert status == 200
    assert payload["eol"] == "crlf"
    assert payload["eol_normalized"] is True
    assert payload["replacements"] == 1
    # the inserted text uses CRLF too, and untouched lines are byte-identical
    assert _read(env, "app/win.vue") == b"const a = 1\r\nconst b = 9\r\nconst c = 3\r\n"


def test_patch_crlf_file_still_accepts_an_exact_crlf_old_string(env):
    env.make_grant(["write"])
    _write(env, "app/win.vue", b"a\r\nb\r\n")
    status, payload = _call(
        "patch", {"path": "app/win.vue", "old_string": "a\r\nb", "new_string": "a\r\nz"}
    )
    assert status == 200
    assert payload["eol_normalized"] is False
    assert _read(env, "app/win.vue") == b"a\r\nz\r\n"


def test_patch_mixed_eol_file_does_not_retry_and_reports_404(env):
    env.make_grant(["write"])
    _write(env, "app/mixed.txt", b"a\r\nb\nc\r\n")
    before = _read(env, "app/mixed.txt")

    status, payload = _call(
        "patch", {"path": "app/mixed.txt", "old_string": "a\nb", "new_string": "a\nz"}
    )

    assert status == 404
    assert payload["error"]["details"]["reason"] == "no_match"
    assert _read(env, "app/mixed.txt") == before


# ── read — offset / length window ─────────────────────────────────────────────

def test_read_window_returns_only_the_requested_slice(env):
    env.make_grant(["read"])
    body = b"0123456789abcdefghij"
    _write(env, "app/window.txt", body)

    status, payload = _call("read", {"path": "app/window.txt", "offset": 4, "length": 6})

    assert status == 200
    assert payload["content"] == "456789"
    assert payload["offset"] == 4
    assert payload["returned_bytes"] == 6
    assert payload["size"] == 20
    assert payload["eof"] is False
    assert payload["truncated"] is True
    assert env.oplogs()[0]["bytes_processed"] == 6   # window bytes, not file size


def test_read_window_reaching_end_reports_eof(env):
    env.make_grant(["read"])
    _write(env, "app/window.txt", b"0123456789")
    status, payload = _call("read", {"path": "app/window.txt", "offset": 6, "length": 4096})
    assert status == 200
    assert payload["content"] == "6789"
    assert payload["returned_bytes"] == 4
    assert payload["eof"] is True
    assert payload["truncated"] is False


def test_read_offset_only_reads_to_end_of_file(env):
    env.make_grant(["read"])
    _write(env, "app/window.txt", b"0123456789")
    status, payload = _call("read", {"path": "app/window.txt", "offset": 7})
    assert status == 200
    assert payload["content"] == "789"
    assert payload["eof"] is True


def test_read_offset_past_eof_is_an_empty_window_not_an_error(env):
    env.make_grant(["read"])
    _write(env, "app/window.txt", b"0123456789")

    status, payload = _call("read", {"path": "app/window.txt", "offset": 9999, "length": 100})

    assert status == 200
    assert payload["content"] == ""
    assert payload["offset"] == 9999
    assert payload["returned_bytes"] == 0
    assert payload["eof"] is True
    assert payload["truncated"] is False
    assert env.oplogs()[0]["result"] == "success"


@pytest.mark.parametrize(
    "body",
    [
        {"path": "app/main.py", "offset": -1},
        {"path": "app/main.py", "length": -5},
        {"path": "app/main.py", "offset": True},
        {"path": "app/main.py", "length": 1.5},
    ],
)
def test_read_invalid_window_arguments_are_422(env, body):
    env.make_grant(["read"])
    status, payload = _call("read", body)
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"
    assert env.oplogs()[0]["result"] == "error"


def test_read_window_over_limit_is_413(env, monkeypatch):
    from modules.flow_gate.services import remote_tool_service as svc

    env.make_grant(["read"])
    monkeypatch.setattr(svc, "_MAX_READ_BYTES", 8)
    _write(env, "app/window.txt", b"0123456789")

    status, payload = _call("read", {"path": "app/window.txt", "offset": 0, "length": 9})
    assert status == 413
    assert payload["error"]["code"] == "too_large"

    status, payload = _call("read", {"path": "app/window.txt", "offset": 0, "length": 8})
    assert status == 200
    assert payload["returned_bytes"] == 8


# ── read — backward compatibility ─────────────────────────────────────────────

def test_read_without_window_fields_is_unchanged(env):
    env.make_grant(["read"])
    original = (env.src / "app" / "main.py").read_bytes()

    status, payload = _call("read", {"path": "app/main.py"})

    assert status == 200
    assert payload["content"] == original.decode()
    assert payload["size"] == len(original)
    assert payload["truncated"] is False
    # new, report-only fields
    assert payload["offset"] == 0
    assert payload["returned_bytes"] == len(original)
    assert payload["eof"] is True


def test_read_max_bytes_truncation_is_unchanged(env):
    env.make_grant(["read"])
    original = (env.src / "app" / "main.py").read_bytes()

    status, payload = _call("read", {"path": "app/main.py", "max_bytes": 10})

    assert status == 200
    assert payload["content"] == original[:10].decode()
    assert payload["size"] == len(original)
    assert payload["truncated"] is True
    assert payload["returned_bytes"] == 10
    assert payload["eof"] is False


def test_read_length_and_max_bytes_take_the_smaller_window(env):
    env.make_grant(["read"])
    _write(env, "app/window.txt", b"0123456789")
    status, payload = _call(
        "read", {"path": "app/window.txt", "offset": 1, "length": 6, "max_bytes": 3}
    )
    assert status == 200
    assert payload["content"] == "123"
    assert payload["returned_bytes"] == 3


# ── read — multibyte character boundaries ─────────────────────────────────────

_KOR = "가나다라마바사아자차"   # 3 UTF-8 bytes per character


def test_read_window_starting_mid_character_advances_the_offset(env):
    env.make_grant(["read"])
    _write(env, "docs/kor.txt", _KOR.encode("utf-8"))

    status, payload = _call("read", {"path": "docs/kor.txt", "offset": 1, "length": 8})

    assert status == 200
    assert payload["offset"] == 3          # advanced past the split character
    assert "�" not in payload["content"]
    assert payload["content"] == "나다"    # bytes 3..9 → two whole characters
    assert payload["returned_bytes"] == 6


def test_read_walking_a_multibyte_file_in_slices_reassembles_it_exactly(env):
    env.make_grant(["read"])
    text = _KOR * 3
    _write(env, "docs/kor.txt", text.encode("utf-8"))

    chunks: list[str] = []
    offset = 0
    for _ in range(200):
        status, payload = _call("read", {"path": "docs/kor.txt", "offset": offset, "length": 5})
        assert status == 200
        assert "�" not in payload["content"]
        chunks.append(payload["content"])
        offset = payload["offset"] + payload["returned_bytes"]
        if payload["eof"]:
            break
    else:
        pytest.fail("byte-window walk did not terminate")

    assert "".join(chunks) == text


def test_read_window_ending_at_eof_keeps_its_trailing_bytes(env):
    """A truncated trailing sequence at EOF must not be trimmed — trimming it would
    leave eof=False forever and the caller's walk would never end."""
    env.make_grant(["read"])
    _write(env, "docs/cut.txt", "가".encode("utf-8")[:2])   # deliberately truncated file

    status, payload = _call("read", {"path": "docs/cut.txt", "offset": 0, "length": 64})

    assert status == 200
    assert payload["returned_bytes"] == 2
    assert payload["eof"] is True


# ── stat ──────────────────────────────────────────────────────────────────────

def test_stat_file_reports_metadata_without_content(env):
    env.make_grant(["read"])
    original = (env.src / "app" / "main.py").read_bytes()

    status, payload = _call("stat", {"path": "app/main.py"})

    assert status == 200
    assert payload["ok"] is True
    assert payload["op"] == "stat"
    assert payload["path"] == "app/main.py"
    assert payload["exists"] is True
    assert payload["type"] == "file"
    assert payload["size"] == len(original)
    assert payload["eol"] == "lf"
    assert payload["binary"] is False
    assert _MTIME_RE.match(payload["mtime"])
    assert "content" not in payload
    assert "continuation" not in payload   # non-mutating → no ment

    log = env.oplogs()[0]
    assert log["op"] == "stat"
    assert log["result"] == "success"
    assert log["target_path"] == "app/main.py"
    assert log["bytes_processed"] is None   # never reads content


def test_stat_missing_path_is_200_with_exists_false(env):
    env.make_grant(["read"])

    status, payload = _call("stat", {"path": "app/not-here.py"})

    assert status == 200
    assert payload["ok"] is True
    assert payload["exists"] is False
    assert payload["type"] is None
    assert payload["size"] is None
    assert payload["mtime"] is None
    assert payload["eol"] is None
    assert payload["binary"] is None
    assert env.oplogs()[0]["result"] == "success"


def test_stat_directory(env):
    env.make_grant(["read"])
    status, payload = _call("stat", {"path": "docs"})
    assert status == 200
    assert payload["exists"] is True
    assert payload["type"] == "dir"
    assert payload["size"] is None
    assert payload["eol"] is None
    assert payload["binary"] is None
    assert _MTIME_RE.match(payload["mtime"])


@pytest.mark.parametrize(
    "blob,eol",
    [
        (b"a\r\nb\r\n", "crlf"),
        (b"a\nb\n", "lf"),
        (b"a\r\nb\nc\r\n", "mixed"),
        (b"single line no newline", "none"),
    ],
)
def test_stat_eol_detection(env, blob, eol):
    env.make_grant(["read"])
    _write(env, "app/eol.txt", blob)
    status, payload = _call("stat", {"path": "app/eol.txt"})
    assert status == 200
    assert payload["eol"] == eol
    assert payload["binary"] is False


def test_stat_binary_file(env):
    env.make_grant(["read"])
    _write(env, "app/logo.bin", b"\x89PNG\x00\x1a\n")
    status, payload = _call("stat", {"path": "app/logo.bin"})
    assert status == 200
    assert payload["binary"] is True
    assert payload["eol"] is None       # EOL is meaningless for binary
    assert payload["type"] == "file"


def test_stat_unsafe_path_is_422_not_exists_false(env):
    """'unsafe path' is not 'path that does not exist' (P0004 §9.2)."""
    env.make_grant(["read"])
    status, payload = _call("stat", {"path": "../outside.txt"})
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"
    assert env.oplogs()[0]["result"] == "error"


def test_stat_missing_path_field_is_422(env):
    env.make_grant(["read"])
    status, payload = _call("stat", {})
    assert status == 422
    assert payload["error"]["code"] == "invalid_request"


def test_stat_requires_read_scope(env):
    env.make_grant(["grep", "write"])   # no read scope
    status, payload = _call("stat", {"path": "app/main.py"})
    assert status == 403
    assert payload["error"]["code"] == "forbidden"
    assert env.oplogs()[0]["result"] == "denied"


def test_stat_is_not_a_mutating_op(env, monkeypatch):
    """No worktree gate, no explorer SSE, no continuation ment (P0004 §0.5)."""
    from modules.flow_gate.services import remote_tool_service as svc

    env.make_grant(["read"])
    calls: list[str] = []
    monkeypatch.setattr(
        svc, "_resolve_root_for_mutation",
        lambda grant, op: calls.append(f"mutation:{op}"),
    )
    monkeypatch.setattr(svc, "_emit_explorer_refresh", lambda _g, op: calls.append(f"sse:{op}"))

    status, payload = _call("stat", {"path": "app/main.py"})

    assert status == 200
    assert calls == []
    assert "continuation" not in payload


# ── router ────────────────────────────────────────────────────────────────────

def test_new_operations_are_reachable_through_the_router(env):
    """The route takes {operation} verbatim, so this is what proves the two new
    names actually resolve to the pipeline rather than falling through to 422."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1.remote_routes import router

    env.make_grant(["read", "write"])
    app = FastAPI()
    app.include_router(router, prefix="/flowgate")
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {RAW_TOKEN}"}

    r = client.post("/flowgate/api/v1/remote/stat", json={"path": "app/main.py"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["op"] == "stat"
    assert r.json()["exists"] is True

    r = client.post(
        "/flowgate/api/v1/remote/patch",
        json={"path": "app/main.py", "old_string": "print('hi')", "new_string": "print('bye')"},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["op"] == "patch"
    assert r.json()["replacements"] == 1

    r = client.post(
        "/flowgate/api/v1/remote/read",
        json={"path": "app/main.py", "offset": 0, "length": 6},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["returned_bytes"] == 6

    # an unknown operation still fails closed
    r = client.post("/flowgate/api/v1/remote/rename", json={"path": "a"}, headers=auth)
    assert r.status_code == 422


# ── stat → read(window) → patch, the flow the design was built for ────────────

def test_stat_then_windowed_read_then_patch(env):
    env.make_grant(["read", "write"])
    _write(env, "app/big.py", ("# header\n" + "filler\n" * 50 + "TARGET = 1\n").encode())

    status, meta = _call("stat", {"path": "app/big.py"})
    assert status == 200 and meta["exists"] is True and meta["binary"] is False
    total = meta["size"]

    status, window = _call("read", {"path": "app/big.py", "offset": total - 11, "length": 11})
    assert status == 200
    assert "TARGET = 1" in window["content"]
    assert window["eof"] is True

    status, patched = _call(
        "patch", {"path": "app/big.py", "old_string": "TARGET = 1", "new_string": "TARGET = 2"}
    )
    assert status == 200
    assert patched["replacements"] == 1
    assert _read(env, "app/big.py").endswith(b"TARGET = 2\n")
    assert patched["size_before"] == total
