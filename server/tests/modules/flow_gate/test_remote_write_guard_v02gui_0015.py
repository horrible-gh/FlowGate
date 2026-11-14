"""Remote write/patch content guards (v02gui 0015 T5, 0006-NR §6-2 a/c/e/f).

Completion cases of 0006-NR §6-2 (rev 4), numbered as there:
  ① masked-only overwrite -> 422          ② patch whose new_string carries a masked line -> 422
  ③ masked old_string patch -> 404         ④ counter-example 1 (existing *** literal edit) -> allowed
  ⑤ counter-example 2 (mask + tail edit) -> 422   ⑥ pure *** literal line added -> allowed
  ⑦ normal edit in a file that already has *** -> allowed
  ⑧ intentional substitution: refused without allow_mask_substitution+reason, allowed + audited with it
  ⑨ base_sha256 mismatch -> 409             ⑩ new U+FFFD -> 422
  ⑪ CRLF file normal edit -> allowed        ⑫ syntax error -> post_write_check (not blocking)
⑬ (reversible placeholders) is out of scope for T5.

Harness (env fixture / _call) is shared with test_remote_tool_0003_T0012.
"""
from __future__ import annotations

import hashlib
import json
import logging

import pytest

from test_remote_tool_0003_T0012 import _call, env  # noqa: F401  (env is a fixture)

SECRET = "example-secret-value-42"


def _put(env, rel: str, data: bytes) -> None:
    target = env.src / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _get(env, rel: str) -> bytes:
    return (env.src / rel).read_bytes()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def grant(env):
    env.make_grant(["read", "write"])
    return env


def _reason(payload) -> str:
    return payload["error"]["details"]["reason"]


# ── ① ⑤ overwrite: masked substitution is refused, nothing is written ───────────

def test_masked_only_overwrite_is_refused_and_file_untouched(grant):
    original = f'import os\nTOKEN = "{SECRET}"\nprint(TOKEN)\n'.encode()
    _put(grant, "app/conf.py", original)
    status, payload = _call("read", {"path": "app/conf.py"})
    masked = payload["content"].replace(SECRET, "***")

    status, payload = _call("write", {"path": "app/conf.py", "content": masked})

    assert status == 422
    assert _reason(payload) == "masked_substitution_suspect"
    assert payload["error"]["details"]["lines"] == [2]
    assert _get(grant, "app/conf.py") == original
    # the refusal never echoes the removed line (it may be the secret)
    assert SECRET not in json.dumps(payload, ensure_ascii=False)


def test_counterexample2_mask_plus_tail_edit_is_refused_for_overwrite(grant):
    original = b'a = 1\nconst token = "example-secret"; // version 1\nb = 2\n'
    _put(grant, "app/x.js", original)
    new = 'a = 1\nconst token = "***"; // version 2\nb = 2\n'

    status, payload = _call("write", {"path": "app/x.js", "content": new})

    assert status == 422 and _reason(payload) == "masked_substitution_suspect"
    assert _get(grant, "app/x.js") == original


def test_unquoted_value_mask_is_refused(grant):
    _put(grant, "app/env.cfg", b"token = abcdef123456 # v1\n")
    status, payload = _call("write", {"path": "app/env.cfg", "content": "token = *** # v2\n"})
    assert status == 422 and _reason(payload) == "masked_substitution_suspect"


# ── ② ③ ⑤ patch ──────────────────────────────────────────────────────────────────

def test_patch_new_string_with_masked_line_is_refused(grant):
    original = f'x = 1\nAPI_KEY = "{SECRET}"\ny = 2\n'.encode()
    _put(grant, "app/k.py", original)

    status, payload = _call("patch", {
        "path": "app/k.py",
        "old_string": f'x = 1\nAPI_KEY = "{SECRET}"\ny = 2\n',
        "new_string": 'x = 10\nAPI_KEY = "***"\ny = 2\n',
    })

    assert status == 422 and _reason(payload) == "masked_substitution_suspect"
    assert payload["error"]["details"]["lines"] == [2]
    assert _get(grant, "app/k.py") == original


def test_patch_mid_line_masked_value_is_judged_with_its_line_head(grant):
    # new_string alone ("***") has no head; the guard judges the whole changed line.
    _put(grant, "app/k.py", f'API_KEY = "{SECRET}"\n'.encode())
    status, payload = _call("patch", {"path": "app/k.py", "old_string": SECRET, "new_string": "***"})
    assert status == 422 and _reason(payload) == "masked_substitution_suspect"


def test_patch_counterexample2_is_refused(grant):
    original = b'const token = "example-secret"; // version 1\n'
    _put(grant, "app/x.js", original)
    status, payload = _call("patch", {
        "path": "app/x.js",
        "old_string": '"example-secret"; // version 1',
        "new_string": '"***"; // version 2',
    })
    assert status == 422 and _reason(payload) == "masked_substitution_suspect"
    assert _get(grant, "app/x.js") == original


def test_patch_with_masked_old_string_is_404_as_before(grant):
    original = f'TOKEN = "{SECRET}"\n'.encode()
    _put(grant, "app/k.py", original)
    status, payload = _call("patch", {"path": "app/k.py", "old_string": 'TOKEN = "***"', "new_string": 'TOKEN = "x"'})
    assert status == 404
    assert _reason(payload) == "no_match"
    assert _get(grant, "app/k.py") == original


# ── ④ ⑥ ⑦ allowed edits ────────────────────────────────────────────────────────────

def test_counterexample1_existing_literal_edit_is_allowed_overwrite_and_patch(grant):
    line = 'const example = "Long documentation literal: ***old value";\n'
    _put(grant, "app/doc.js", line.encode())

    status, payload = _call("write", {
        "path": "app/doc.js", "content": line.replace("***old value", "***value"),
    })
    assert status == 200, payload
    assert _get(grant, "app/doc.js") == line.replace("***old value", "***value").encode()

    status, payload = _call("patch", {"path": "app/doc.js", "old_string": "***value", "new_string": "***newer value"})
    assert status == 200, payload


def test_pure_literal_line_addition_is_allowed(grant):
    _put(grant, "app/a.py", b"x = 1\n")
    status, payload = _call("write", {"path": "app/a.py", "content": 'x = 1\nMASK = "***"\n'})
    assert status == 200, payload
    status, payload = _call("patch", {"path": "app/a.py", "old_string": "x = 1\n", "new_string": 'x = 1\nOTHER = "***"\n'})
    assert status == 200, payload


def test_normal_edit_in_file_with_existing_mask_literals_is_allowed(grant):
    _put(grant, "app/m.py", b'msg = "***done"\nMASK = "***"\nn = 1\n')
    status, payload = _call("write", {"path": "app/m.py", "content": 'msg = "***finished"\nMASK = "***"\nn = 2\n'})
    assert status == 200, payload


def test_markdown_emphasis_insertion_is_not_a_substitution(grant):
    _put(grant, "docs/n.md", b"# t\n- item one\n")
    status, payload = _call("write", {"path": "docs/n.md", "content": "# t\n- ***item one***\n"})
    assert status == 200, payload


# ── ⑧ intentional substitution: override flag + reason, audited ──────────────────

def test_intentional_substitution_needs_flag_and_reason(grant, caplog):
    _put(grant, "app/l.py", b'label = "Total"\n')
    body = {"path": "app/l.py", "content": 'label = "***"\n'}

    status, payload = _call("write", body)
    assert status == 422 and _reason(payload) == "masked_substitution_suspect"
    assert payload["error"]["details"]["override"] == "allow_mask_substitution+reason"

    status, payload = _call("write", {**body, "allow_mask_substitution": True})
    assert status == 422 and _reason(payload) == "override_reason_required"
    status, payload = _call("write", {**body, "allow_mask_substitution": True, "reason": "   "})
    assert status == 422 and _reason(payload) == "override_reason_required"
    assert _get(grant, "app/l.py") == b'label = "Total"\n'

    with caplog.at_level(logging.WARNING, logger="modules.flow_gate.services.remote_tool_service"):
        status, payload = _call("write", {**body, "allow_mask_substitution": True, "reason": "redacted label by design"})
    assert status == 200, payload
    assert payload["content_guard"] == {
        "overridden": {"masked_substitution_suspect": [1]}, "reason": "redacted label by design",
    }
    assert _get(grant, "app/l.py") == b'label = "***"\n'
    assert any("content guard overridden" in r.getMessage() and "app/l.py" in r.getMessage()
               for r in caplog.records)


def test_guard_field_shapes_are_validated(grant):
    _put(grant, "app/a.py", b"x = 1\n")
    status, payload = _call("write", {"path": "app/a.py", "content": "x = 2\n", "base_sha256": "nothex"})
    assert status == 422 and _reason(payload) == "invalid_base_sha256"
    status, payload = _call("patch", {"path": "app/a.py", "old_string": "1", "new_string": "2",
                                      "allow_mask_substitution": "yes", "reason": "r"})
    assert status == 422 and _reason(payload) == "invalid_field"
    status, payload = _call("write", {"path": "app/new.py", "content": "x\n", "mode": "create",
                                      "base_sha256": "0" * 64})
    assert status == 422 and _reason(payload) == "base_sha256_not_applicable"
    assert not (grant.src / "app" / "new.py").exists()


# ── ⑨ base_sha256 ─────────────────────────────────────────────────────────────────

def test_base_sha256_chain_read_write_patch(grant):
    original = b"a = 1\nb = 2\n"
    _put(grant, "app/c.py", original)

    status, payload = _call("read", {"path": "app/c.py"})
    assert payload["source_sha256"] == _sha(original)
    assert payload["transformed"] is False
    status, payload = _call("read", {"path": "app/c.py", "start_line": 1, "end_line": 1})
    assert payload["source_sha256"] == _sha(original)

    status, payload = _call("write", {"path": "app/c.py", "content": "a = 1\nb = 3\n", "base_sha256": _sha(original)})
    assert status == 200, payload
    assert payload["sha256"] == _sha(b"a = 1\nb = 3\n")

    status, payload = _call("patch", {"path": "app/c.py", "old_string": "b = 3", "new_string": "b = 4",
                                      "base_sha256": payload["sha256"].upper()})
    assert status == 200, payload
    assert payload["sha256"] == _sha(_get(grant, "app/c.py"))


def test_base_sha256_mismatch_refuses_write_and_patch(grant):
    _put(grant, "app/c.py", b"a = 1\n")
    stale = _sha(b"a = 0\n")

    status, payload = _call("write", {"path": "app/c.py", "content": "a = 2\n", "base_sha256": stale})
    assert status == 409
    assert _reason(payload) == "base_changed"
    assert payload["error"]["details"]["current_sha256"] == _sha(b"a = 1\n")
    status, payload = _call("patch", {"path": "app/c.py", "old_string": "1", "new_string": "2", "base_sha256": stale})
    assert status == 409 and _reason(payload) == "base_changed"
    assert _get(grant, "app/c.py") == b"a = 1\n"

    status, payload = _call("write", {"path": "app/gone.py", "content": "x\n", "base_sha256": stale})
    assert status == 409 and payload["error"]["details"]["current_sha256"] is None
    assert not (grant.src / "app" / "gone.py").exists()


def test_without_base_sha256_behaviour_is_unchanged(grant):
    status, payload = _call("write", {"path": "docs/readme.md", "content": "# new\n"})
    assert status == 200 and payload["created"] is False
    assert "post_write_check" not in payload     # .md is not syntax-checked


# ── ⑩ U+FFFD ──────────────────────────────────────────────────────────────────────

def test_read_marks_transformed_and_writing_it_back_is_refused(grant):
    original = b"x = 1\nname = 'caf\xe9'\ny = 2\n"     # latin-1 byte, not UTF-8
    _put(grant, "app/l1.py", original)

    status, payload = _call("read", {"path": "app/l1.py"})
    assert payload["transformed"] is True
    assert "�" in payload["content"]
    edited = payload["content"].replace("y = 2", "y = 3")

    status, payload = _call("write", {"path": "app/l1.py", "content": edited})
    assert status == 422
    assert _reason(payload) == "replacement_char_introduced"
    assert payload["error"]["details"]["lines"] == [2]
    assert _get(grant, "app/l1.py") == original

    status, payload = _call("write", {"path": "app/l1.py", "content": edited,
                                      "allow_replacement_char": True, "reason": "re-encode the file as UTF-8"})
    assert status == 200, payload
    assert payload["content_guard"]["overridden"] == {"replacement_char_introduced": [2]}


def test_patch_and_append_adding_replacement_char_are_refused(grant):
    _put(grant, "app/u.py", b"a = 1\n")
    status, payload = _call("patch", {"path": "app/u.py", "old_string": "a = 1", "new_string": "a = '�'"})
    assert status == 422 and _reason(payload) == "replacement_char_introduced"
    status, payload = _call("write", {"path": "app/u.py", "content": "b = '�'\n", "mode": "append"})
    assert status == 422 and _reason(payload) == "replacement_char_introduced"
    assert _get(grant, "app/u.py") == b"a = 1\n"


def test_edit_of_a_line_that_really_holds_replacement_char_is_allowed(grant):
    _put(grant, "app/r.py", "a = '�'  # v1\n".encode())
    status, payload = _call("write", {"path": "app/r.py", "content": "a = '�'  # v2\n"})
    assert status == 200, payload


# ── ⑪ CRLF ────────────────────────────────────────────────────────────────────────

def test_crlf_file_normal_edits_are_allowed_and_masking_still_caught(grant):
    _put(grant, "app/w.py", b"a = 1\r\nkey = 'abc123'\r\nb = 2\r\n")
    status, payload = _call("write", {"path": "app/w.py", "content": "a = 1\r\nkey = 'abc123'\r\nb = 3\r\n"})
    assert status == 200, payload
    status, payload = _call("patch", {"path": "app/w.py", "old_string": "b = 3\n", "new_string": "b = 4\n"})
    assert status == 200 and payload["eol_normalized"] is True
    status, payload = _call("patch", {"path": "app/w.py", "old_string": "key = 'abc123'\nb = 4\n",
                                      "new_string": "key = '***'\nb = 5\n"})
    assert status == 422 and _reason(payload) == "masked_substitution_suspect"
    assert _get(grant, "app/w.py") == b"a = 1\r\nkey = 'abc123'\r\nb = 4\r\n"


# ── ⑫ post_write_check ─────────────────────────────────────────────────────────────

def test_post_write_check_reports_syntax_without_blocking(grant):
    status, payload = _call("write", {"path": "app/bad.py", "content": "def f(:\n    pass\n"})
    assert status == 200
    check = payload["post_write_check"]
    assert check["checker"] == "python_compile" and check["status"] == "syntax_error" and check["line"] == 1
    assert _get(grant, "app/bad.py") == b"def f(:\n    pass\n"

    status, payload = _call("patch", {"path": "app/bad.py", "old_string": "def f(:", "new_string": "def f():"})
    assert status == 200 and payload["post_write_check"] == {"checker": "python_compile", "status": "ok"}

    status, payload = _call("write", {"path": "app/c.json", "content": "{\"a\": }"})
    assert status == 200 and payload["post_write_check"]["status"] == "syntax_error"

    _put(grant, "app/tail.py", b"x = (\n")
    status, payload = _call("write", {"path": "app/tail.py", "content": "1)\n", "mode": "append"})
    assert status == 200 and payload["post_write_check"]["status"] == "ok"   # judged on the whole stored file


# ── help / API schema stay in step with the service ──────────────────────────────

@pytest.mark.parametrize("op", ["write", "patch"])
def test_help_and_api_schema_list_the_guard_fields(op):
    from modules.flow_gate.services import api_server_tools, remote_tool_service as svc, tool_registry

    for locale in ("ko", "ja", "en"):
        names = {f["name"] for f in tool_registry._request_fields(op, locale)}
        assert names == svc._ALLOWED_FIELDS[op]
    props = api_server_tools.SCHEMAS[f"{op}_source_file"]["properties"]
    assert {"base_sha256", "allow_mask_substitution", "allow_replacement_char", "reason"} <= set(props)
    assert "base_changed" in api_server_tools.DESCRIPTIONS[f"{op}_source_file"]


# ── guard unit: the prefix rule table of 0006-NR §6-2 (c) ─────────────────────────

@pytest.mark.parametrize("removed, added, suspect", [
    ('const example = "Long documentation literal: ***old value";',
     'const example = "Long documentation literal: ***value";', False),
    ('const token = "example-secret"; // version 1', 'const token = "***"; // version 2', True),
    ('const token = "example-secret";', 'const token = "***";', True),
    ("token = abcdef123456 # v1", "token = *** # v2", True),
    ('msg = "***done"', 'msg = "***finished"', False),
    ('label = "Total"', 'label = "***"', True),
    ("- item one", "- ***item one***", False),
    ("---", "***", False),
])
def test_prefix_rule_table(removed, added, suspect):
    from modules.flow_gate.services import remote_write_guard as g
    assert g.is_masked_substitution(added, removed) is suspect
