"""0608 T0007 — the API provider's tool-driven conflict resolver.

T0005 took large conflicts' chunk text out of the resolver mention; a CLI worker reads it
back over `/remote/read`, but an API provider only had `resolve_git_conflict`, 4 turns and
one 8,192-token reply for whole files. This file pins what connects it:

  1. one tool contract — the mention's Remote source tools == the API tool list ==
     tool_registry's resolve_conflict kind == the remote scopes that token gets; never a
     write/patch/remove;
  2. `chunks` submissions: resolve_conflicts assembles the whole file from per-chunk
     resolutions and hands it to the SAME validation a `content` submission gets;
  3. the conflict-only budget (turns from the conflict's size, read calls per turn, output
     ceiling) and the elision of reads behind an accepted partial submit;
  4. a failed read — bad range, missing path, missing scope, oversized result, bad ref,
     timeout, malformed result, a tool that is not exposed — reaches the model as a failed
     tool result and changes nothing: no file, no index stage, no submit;
  5. the server enforces (4) even for a model that does not stop: a resolve sent in the
     same response as a failed read -- or a read whose result is a broken envelope (`{}`,
     no/non-boolean `ok`, `ok` contradicting the status, no error object) -- is never
     dispatched; it comes back as `prerequisite_tool_failed`.

The connected runs (0594's real blobs through a real finalize session, and the inline/
validator case) are test_git_integration_0115.py::TestApiConflictToolLoop0608.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())
os.environ.setdefault("FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-apiconflict-0608-"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.api import token_routes  # noqa: E402
from modules.flow_gate.api.v1 import git_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as ai_svc  # noqa: E402
from modules.flow_gate.services import api_server_tools  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services import remote_tool_service as remote  # noqa: E402
from modules.flow_gate.services import tool_registry  # noqa: E402
from modules.flow_gate.services.ai_invoke import provider_api, runtime, worker  # noqa: E402
from modules.flow_gate.services.git import conflict  # noqa: E402
from modules.flow_gate.services.git.credentials import GitServiceError  # noqa: E402

_API = "http://127.0.0.1:8089/flowgate/api/v1"
_CONFLICT = (
    "head\n<<<<<<< HEAD\nours one\n||||||| base\nbase one\n=======\ntheirs one\n>>>>>>> grp\n"
    "middle\n<<<<<<< HEAD\nours two\n||||||| base\nbase two\n=======\ntheirs two\n>>>>>>> grp\ntail\n"
)
_WRITERS = {"write_source_file", "patch_source_file", "remove_source_file"}


def _mention(content: str = _CONFLICT) -> str:
    payload = {"ok": True, "merge_id": 7, "branch": "grp", "base_branch": "main", "kind": "merge",
               "tr_conflict": None, "files": [{"path": "a.py", "content": content, "conflict_count": 2}]}
    original = token_routes.git_service.list_conflicts
    token_routes.git_service.list_conflicts = lambda group_id, merge_id: payload
    try:
        return token_routes._build_conflict_mention(
            group_id="grp.default.0001", project_id="grp", merge_id=7,
            scratch_dir="/scratch/x", raw_token="tok_raw", api_base_url=_API,
        )
    finally:
        token_routes.git_service.list_conflicts = original


# ── 1. one contract: mention == API tools == registry == remote scopes ─────────────


class TestOneToolContract:
    def test_mention_tools_line_equals_the_api_tool_list(self):
        mention = _mention()
        tools_line = next(line for line in mention.splitlines() if line.startswith("Tools: "))
        advertised = [name.strip() for name in tools_line[len("Tools: "):].split(",")]
        exposed = [spec["name"] for spec in api_server_tools.conflict_tool_definitions()]
        assert exposed[0] == "read_help"
        assert [api_server_tools.SOURCE_OPS[name] for name in exposed[1:]] == advertised
        kind, _ = tool_registry.kind_for_step("resolve_conflict")
        assert kind == "read"
        assert advertised == tool_registry.tool_names(kind, "resolve_conflict")
        assert not _WRITERS & set(exposed)

    def test_every_exposed_read_tool_is_granted_by_the_token_scopes_and_writes_are_not(self):
        kind, _ = tool_registry.kind_for_step("resolve_conflict")
        scopes = set(remote._SCOPES_BY_KIND[kind])
        for spec in api_server_tools.conflict_tool_definitions()[1:]:
            assert remote.OP_SCOPE[api_server_tools.SOURCE_OPS[spec["name"]]] in scopes, spec["name"]
        for op in ("write", "patch", "remove"):
            assert remote.OP_SCOPE[op] not in scopes

    def test_write_is_refused_by_the_remote_endpoint_for_that_token(self, monkeypatch, tmp_path):
        kind, _ = tool_registry.kind_for_step("resolve_conflict")
        monkeypatch.setattr(remote, "_authenticate", lambda raw: {"grant_id": 1, "project": "p", "group_id": "g"})
        monkeypatch.setattr(remote.db_grants, "get_scopes", lambda gid: set(remote._SCOPES_BY_KIND[kind]))
        monkeypatch.setattr(remote, "_resolve_src_root", lambda g, op="read": tmp_path)
        monkeypatch.setattr(remote, "_log", lambda *a, **kw: None)
        monkeypatch.setattr(remote, "_locale_for_grant", lambda g: "en")
        status, _ = remote.handle("write", "tok", {"path": "a.py", "content": "x"})
        assert status == 403
        assert not (tmp_path / "a.py").exists()

    def test_a_widened_registry_still_never_hands_the_resolver_a_writer(self, monkeypatch):
        monkeypatch.setattr(tool_registry, "tool_names", lambda kind, scope=None: ["read", "write", "patch", "remove"])
        names = [spec["name"] for spec in api_server_tools.conflict_tool_definitions()]
        assert names == ["read_help", "read_source_file"]

    def test_api_guidance_names_exactly_the_exposed_tools(self):
        names = [spec["name"] for spec in api_server_tools.conflict_tool_definitions()] + ["resolve_git_conflict"]
        guidance = provider_api._api_conflict_guidance(names)
        assert "Tools: " + ", ".join(f"`{n}`" for n in names) + "." in guidance
        assert "`read_source_file` (path, start_line/end_line, ref)" in guidance
        assert "http" not in guidance.replace("HTTP", "")

    def test_read_source_file_takes_the_remote_line_selector(self):
        schema = api_server_tools.SCHEMAS["read_source_file"]
        assert {"start_line", "end_line", "ref"} <= set(schema["properties"])
        api_server_tools.validate(schema, {"path": "a.py", "start_line": 3, "end_line": 9})
        with pytest.raises(api_server_tools.ToolError):
            api_server_tools.validate(schema, {"path": "a.py", "start_line": 0, "end_line": 9})

    def test_mention_and_tool_describe_the_chunks_form(self):
        mention = _mention()
        assert "a file may carry `chunks`" in mention
        item = runtime._RESOLVE_TOOL_SCHEMA["properties"]["files"]["items"]
        assert item["required"] == ["path"]
        assert set(item["properties"]["chunks"]["items"]["properties"]) == {"chunk", "content"}
        assert "chunks" in runtime._RESOLVE_TOOL_DESC and "remaining_conflicts" in runtime._RESOLVE_TOOL_DESC


# ── 2. chunk submissions are assembled, then validated like content ──────────────────


class TestChunkAssembly:
    def test_every_chunk_resolved_gives_the_whole_file(self):
        text = conflict._content_from_chunk_resolutions("a.py", _CONFLICT, [
            {"chunk": 2, "content": "two merged\n"}, {"chunk": 1, "content": "one merged\nplus\n"},
        ])
        assert text == "head\none merged\nplus\nmiddle\ntwo merged\ntail\n"

    def test_a_chunk_left_out_keeps_its_markers_for_the_existing_check(self):
        text = conflict._content_from_chunk_resolutions("a.py", _CONFLICT, [{"chunk": 1, "content": "one\n"}])
        assert git_service.has_conflict_markers(text)
        assert "ours two" in text and "theirs two" in text

    def test_chunk_numbers_match_the_mention(self):
        session = json.loads(_mention().split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        numbered = [c.get("chunk", i) for i, c in enumerate(session["files"][0]["chunks"], 1)]
        assert numbered == [1, 2]
        ranges = conflict._chunk_original_ranges(conflict._split_content_segments(_CONFLICT))
        mention_ranges = [(c["start_line"], c["end_line"]) for c in token_routes._split_conflict_chunks(_CONFLICT)]
        assert ranges == mention_ranges

    def test_crlf_original_and_empty_resolution(self):
        text = conflict._content_from_chunk_resolutions("a.py", _CONFLICT.replace("\n", "\r\n"), [
            {"chunk": 1, "content": ""}, {"chunk": 2, "content": "two\r\n"},
        ])
        assert text == "head\nmiddle\ntwo\ntail\n"

    @pytest.mark.parametrize("chunks, message", [
        ([{"chunk": 3, "content": "x"}], "chunk 3 does not exist"),
        ([{"chunk": 1, "content": "x"}, {"chunk": 1, "content": "y"}], "sent twice"),
        ([{"chunk": True, "content": "x"}], "integer chunk number"),
        ([{"chunk": 1}], "integer chunk number"),
        ([], "non-empty list"),
    ])
    def test_malformed_chunks_are_422(self, chunks, message):
        with pytest.raises(GitServiceError) as err:
            conflict._content_from_chunk_resolutions("a.py", _CONFLICT, chunks)
        assert err.value.status == 422 and err.value.code == "invalid_request"
        assert message in err.value.message

    def test_a_file_without_markers_cannot_take_chunks(self):
        with pytest.raises(GitServiceError) as err:
            conflict._content_from_chunk_resolutions("a.py", "clean\n", [{"chunk": 1, "content": "x"}])
        assert err.value.status == 422 and "send content instead" in err.value.message

    def test_route_model_carries_chunks_and_refuses_stray_keys(self):
        body = git_routes.ResolveBody(files=[{"path": "a.py", "chunks": [{"chunk": 1, "content": "x"}]}])
        assert body.files[0].model_dump()["chunks"] == [{"chunk": 1, "content": "x"}]
        with pytest.raises(Exception):
            git_routes.ResolveBody(files=[{"path": "a.py", "chunks": [{"chunk": 1, "content": "x", "write": 1}]}])


class _FakeSessionDb:
    def __init__(self, paths):
        self.paths = paths
        self.resolved: list[str] = []

    def touch_session(self, merge_id):
        pass

    def session_context(self, session):
        return {}

    def session_files(self, merge_id):
        return [{"path": p} for p in self.paths]

    def mark_file_resolved(self, merge_id, path):
        self.resolved.append(path)


class TestResolveConflictsTakesChunks:
    """`resolve_conflicts` itself, on a real conflicted file; the session row is faked."""

    def _session(self, monkeypatch, tmp_path):
        (tmp_path / "a.py").write_text(_CONFLICT, encoding="utf-8", newline="\n")
        db = _FakeSessionDb(["a.py"])
        monkeypatch.setattr(git_service, "_session_context", lambda g, m: ({"id": m}, {}, "p", tmp_path))
        monkeypatch.setattr(git_service, "db_git", db)
        return db

    @pytest.mark.parametrize("entry, code", [
        ({"path": "a.py", "content": "x\n", "chunks": [{"chunk": 1, "content": "x"}]}, "invalid_request"),
        ({"path": "a.py"}, "invalid_request"),
        ({"path": "a.py", "chunks": [{"chunk": 1, "content": "ours one\ntheirs one\n"}]}, "conflict_markers_remain"),
        # both chunks drop the group side (chunks <= 3 common lines apart are judged as one
        # group by the existing validator, for `content` and `chunks` alike)
        ({"path": "a.py", "chunks": [{"chunk": 1, "content": "ours one\n"},
                                     {"chunk": 2, "content": "ours two\n"}]}, "conflict_side_dropped"),
        ({"path": "a.py", "supersede": {"side": "ours", "reason": "r"},
          "chunks": [{"chunk": 1, "content": "ours one\ntheirs one\n"},
                     {"chunk": 2, "content": "ours two\ntheirs two\n"}]}, "conflict_supersede_invalid"),
    ])
    def test_refusals_write_nothing(self, monkeypatch, tmp_path, entry, code):
        db = self._session(monkeypatch, tmp_path)
        with pytest.raises(GitServiceError) as err:
            conflict.resolve_conflicts("g", 1, [entry], True)
        assert err.value.code == code
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == _CONFLICT
        assert db.resolved == []


# ── 3. the conflict-only budget ─────────────────────────────────────────────────────


class TestConflictBudget:
    @pytest.mark.parametrize("counts, turns", [((6, 24), 58), ((1, 1), 7), ((1, 2), 9), ((10, 100), 60)])
    def test_turns_follow_the_conflict(self, monkeypatch, counts, turns):
        monkeypatch.setattr(api_server_tools, "open_conflict_counts", lambda run: counts)
        assert worker._conflict_turn_budget({"run_id": "r"}) == turns

    def test_unreadable_session_keeps_the_old_flat_budget(self, monkeypatch):
        def boom(run):
            raise GitServiceError(404, "not_found", "gone")
        monkeypatch.setattr(api_server_tools, "open_conflict_counts", boom)
        assert worker._conflict_turn_budget({"run_id": "r"}) == runtime.API_MAX_TURNS_PER_DOC == 4

    def test_output_ceiling_is_conflict_only(self, monkeypatch):
        sent = []
        monkeypatch.setattr(provider_api._svc(), "_http_post_json",
                            lambda url, headers, body, timeout: sent.append(body) or {"content": []})
        spec = [{"name": "t", "description": "d", "schema": {"type": "object"}}]
        provider_api._call_anthropic("http://x", "m", "k", [], 1, spec, "", {}, True)
        provider_api._call_anthropic("http://x", "m", "k", [], 1, spec, "", {}, True,
                                     max_tokens=runtime.API_CONFLICT_MAX_TOKENS)
        assert [body["max_tokens"] for body in sent] == [runtime.API_MAX_TOKENS, 16384]
        assert runtime.API_MAX_TOKENS == 8192

    @pytest.mark.parametrize("kind", ["claude", "openai"])
    def test_accepted_partial_submit_elides_earlier_reads_only(self, kind):
        old = worker._tool_result_msg(kind, {"id": "a"}, '{"ok": true, "content": "old"}')
        new = worker._tool_result_msg(kind, {"id": "b"}, '{"ok": true, "content": "new"}')
        reads = [(1, old), (3, new)]
        worker._elide_conflict_reads(kind, reads, 3)

        def text(msg):
            return msg["content"][0]["content"] if kind == "claude" else msg["content"]
        assert json.loads(text(old))["elided"]
        assert json.loads(text(new))["content"] == "new"
        assert reads == [(3, new)]


# ── 4. failed reads change nothing ──────────────────────────────────────────────────


def _git(args, cwd):
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="T",
               GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, env=env)


def _conflicted_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    _git(["init", "-b", "main", str(repo)], tmp)
    _git(["config", "core.autocrlf", "false"], repo)
    (repo / "a.py").write_text("head\none\nmiddle\ntwo\ntail\n", encoding="utf-8", newline="\n")
    (repo / "big.txt").write_text("x" * 40_000 + "\n", encoding="utf-8", newline="\n")
    _git(["add", "-A"], repo); _git(["commit", "-m", "base"], repo)
    _git(["checkout", "-b", "grp"], repo)
    (repo / "a.py").write_text("head\ntheirs one\nmiddle\ntheirs two\ntail\n", encoding="utf-8", newline="\n")
    _git(["commit", "-am", "theirs"], repo)
    _git(["checkout", "main"], repo)
    (repo / "a.py").write_text("head\nours one\nmiddle\nours two\ntail\n", encoding="utf-8", newline="\n")
    _git(["commit", "-am", "ours"], repo)
    assert _git(["-c", "merge.conflictStyle=zdiff3", "merge", "grp"], repo).returncode != 0
    return repo


def _state(repo: Path) -> tuple:
    return ((repo / "a.py").read_bytes(), _git(["ls-files", "-u"], repo).stdout,
            _git(["diff", "--name-only", "--diff-filter=U"], repo).stdout)


_FAILURES = {
    "invalid_line_range": ({"path": "a.py", "start_line": 9, "end_line": 2}, None),
    "not_found": ({"path": "missing.py", "start_line": 1, "end_line": 2}, None),
    "forbidden": ({"path": "a.py", "start_line": 1, "end_line": 2}, "no_scopes"),
    "result_too_large": ({"path": "big.txt"}, None),
    "bad_ref": ({"path": "a.py", "ref": "0123456789abcdef0123456789abcdef01234567"}, None),
    "timeout": ({"path": "a.py", "start_line": 1, "end_line": 2}, "timeout"),
    "malformed": ({"path": "a.py", "start_line": 1, "end_line": 2}, "malformed"),
    "not_exposed": ({"path": "a.py", "content": "resolved\n"}, "write_tool"),
}


class TestFailedReadChangesNothing:
    @pytest.mark.parametrize("case", sorted(_FAILURES))
    def test_failed_read_is_a_failed_tool_result_and_nothing_moves(self, monkeypatch, tmp_path, case):
        repo = _conflicted_repo(tmp_path)
        body, mode = _FAILURES[case]
        kind, _ = tool_registry.kind_for_step("resolve_conflict")
        scopes = set() if mode == "no_scopes" else set(remote._SCOPES_BY_KIND[kind])
        monkeypatch.setattr(remote, "_authenticate", lambda raw: {"grant_id": 1, "project": "p", "group_id": "g"})
        monkeypatch.setattr(remote.db_grants, "get_scopes", lambda gid: scopes)
        monkeypatch.setattr(remote, "_resolve_src_root", lambda g, op="read": repo)
        monkeypatch.setattr(remote, "_log", lambda *a, **kw: None)
        monkeypatch.setattr(remote, "_locale_for_grant", lambda g: "en")
        if mode == "timeout":
            def slow(*_a):
                raise TimeoutError("remote tool timed out")
            monkeypatch.setattr(api_server_tools, "source_call", slow)
        if mode == "malformed":
            monkeypatch.setattr(api_server_tools, "source_call", lambda *_a: (200, "not an envelope"))
        monkeypatch.setattr(api_server_tools, "open_conflict_counts", lambda run: (1, 2))
        submitted = []
        monkeypatch.setattr(worker, "_resolve_conflict", lambda *a: submitted.append(a) or (200, {}))
        results: list[dict] = []

        def model(_u, _m, _k, conversation, _t, specs, _d, _s, _f=False, max_tokens=None):
            for msg in conversation:
                for block in (msg.get("content") if isinstance(msg.get("content"), list) else []):
                    if block.get("type") == "tool_result" and block["tool_use_id"] == "r1" and not results:
                        results.append(json.loads(block["content"]))
            if results:            # a careful model: the read failed, so it stops
                return "I could not read the chunk.", [], {"role": "assistant", "content": []}
            name = "write_source_file" if mode == "write_tool" else "read_source_file"
            call = {"id": "r1", "name": name, "input": body}
            return None, [call], {"role": "assistant", "content": [
                {"type": "tool_use", "id": "r1", "name": name, "input": body}]}

        monkeypatch.setattr(ai_svc, "_call_anthropic", model)
        monkeypatch.setattr(ai_svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")
        before = _state(repo)
        run = {"project_id": "p", "chain_source": "system", "run_id": f"aiv_{case}", "docs_target": 0,
               "raw_token": "tok", "action_scope": "resolve_conflict", "mode": "single", "group_id": "g",
               "merge_id": 1, "api_base_url": _API, "cancel_event": threading.Event(),
               "started_mono": time.monotonic(), "timeout_sec": 30}
        provider = {"id": "aip", "exec_type": "api", "kind": "claude", "api_base_url": "http://x", "api_model": "m"}

        assert ai_svc._api_execute(provider, _mention(), run) == ("started_ok", None)

        assert len(results) == 1 and results[0]["ok"] is False
        error = results[0]["error"]
        reason = (error.get("details") or {}).get("reason") or error.get("code")
        expected = {
            "invalid_line_range": {"invalid_line_range"},
            "not_found": {"not_found"},
            "forbidden": {"forbidden"},
            "result_too_large": {"result_too_large"},
            "bad_ref": {"not_found", "invalid_request", "invalid_ref", "ref_not_found"},
            "timeout": {"tool_failed"},
            "malformed": {"malformed_tool_result"},
            "not_exposed": {"invalid_tool_call"},
        }[case]
        assert reason in expected or error.get("code") in expected, results[0]
        assert submitted == []
        assert _state(repo) == before

    def test_read_calls_past_the_budget_are_refused_not_run(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api_server_tools, "open_conflict_counts", lambda run: (1, 1))
        monkeypatch.setattr(worker, "API_CONFLICT_SOURCE_CALLS_PER_TURN", 0)
        ran = []
        monkeypatch.setattr(api_server_tools, "source_call", lambda *a: ran.append(a) or (200, {"ok": True}))
        seen = []

        def model(_u, _m, _k, conversation, _t, specs, _d, _s, _f=False, max_tokens=None):
            for msg in conversation:
                for block in (msg.get("content") if isinstance(msg.get("content"), list) else []):
                    if block.get("type") == "tool_result" and not seen:
                        seen.append(json.loads(block["content"]))
            if seen:
                return None, [], {"role": "assistant", "content": []}
            body = {"path": "a.py", "start_line": 1, "end_line": 2}
            return None, [{"id": "r1", "name": "read_source_file", "input": body}], {
                "role": "assistant", "content": [{"type": "tool_use", "id": "r1", "name": "read_source_file", "input": body}]}

        monkeypatch.setattr(ai_svc, "_call_anthropic", model)
        monkeypatch.setattr(ai_svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")
        run = {"project_id": "p", "chain_source": "system", "run_id": "aiv_budget", "docs_target": 0,
               "raw_token": "tok", "action_scope": "resolve_conflict", "mode": "single", "group_id": "g",
               "merge_id": 1, "api_base_url": _API, "cancel_event": threading.Event(),
               "started_mono": time.monotonic(), "timeout_sec": 30}
        provider = {"id": "aip", "exec_type": "api", "kind": "claude", "api_base_url": "http://x", "api_model": "m"}
        ai_svc._api_execute(provider, _mention(), run)
        assert ran == []
        assert seen[0]["error"]["details"]["reason"] == "tool_call_budget_exhausted"
        assert run["api_turn_budget"] == 7


# ── 5. the server, not the model, blocks a resolve sent with a failed read ────────────

# A result the loop must treat as malformed (502 malformed_tool_result), not as a read.
_MALFORMED_RESULTS = {
    "not_a_dict": (200, "not an envelope"),
    "empty_dict": (200, {}),
    "no_ok": (200, {"op": "read", "content": "ours one\n"}),
    "ok_not_bool": (200, {"ok": "true", "op": "read", "content": "ours one\n"}),
    "ok_true_on_error_status": (404, {"ok": True, "op": "read", "content": "ours one\n"}),
    "ok_false_on_2xx": (200, {"ok": False, "op": "read", "error": {"code": "not_found", "message": "x"}}),
    "error_missing": (404, {"ok": False, "op": "read"}),
    "error_not_object": (404, {"ok": False, "op": "read", "error": "not_found"}),
    "error_without_code": (404, {"ok": False, "op": "read", "error": {"message": "x"}}),
    "error_blank_code": (404, {"ok": False, "op": "read", "error": {"code": " ", "message": "x"}}),
    "error_message_not_str": (404, {"ok": False, "op": "read", "error": {"code": "not_found", "message": 3}}),
}


class TestToolResultEnvelope:
    @pytest.mark.parametrize("case", sorted(_MALFORMED_RESULTS))
    def test_broken_envelopes_are_malformed(self, case):
        status, resp = _MALFORMED_RESULTS[case]
        assert worker._conflict_tool_result_well_formed(status, resp) is False

    def test_the_real_envelopes_are_well_formed(self):
        ok_env = remote._envelope(True, "read", extra={"content": "x"})
        fail_env = remote._fail_envelope("read", 404, "en")
        status, tool_err = api_server_tools.error_payload(
            "read_source_file", api_server_tools.ToolError(413, "result_too_large"))
        help_err = {"ok": False, "http_status": 404, "error_message": "Unknown help item: x", "help_url": "u"}
        assert worker._conflict_tool_result_well_formed(200, ok_env)
        assert worker._conflict_tool_result_well_formed(404, fail_env)
        assert worker._conflict_tool_result_well_formed(status, tool_err)
        assert worker._conflict_tool_result_well_formed(404, help_err)


_RESOLVE_INPUT = {"files": [{"path": "a.py", "chunks": [
    {"chunk": 1, "content": "ours one\n"}, {"chunk": 2, "content": "ours two\n"}]}], "complete": True}
_SAME_TURN_FAILURES = sorted(set(_FAILURES) - {"malformed"}) + [f"malformed:{k}" for k in sorted(_MALFORMED_RESULTS)]
_PROVIDER = {"id": "aip", "exec_type": "api", "kind": "claude", "api_base_url": "http://x", "api_model": "m"}


def _arm_remote(monkeypatch, repo, mode):
    kind, _ = tool_registry.kind_for_step("resolve_conflict")
    scopes = set() if mode == "no_scopes" else set(remote._SCOPES_BY_KIND[kind])
    monkeypatch.setattr(remote, "_authenticate", lambda raw: {"grant_id": 1, "project": "p", "group_id": "g"})
    monkeypatch.setattr(remote.db_grants, "get_scopes", lambda gid: scopes)
    monkeypatch.setattr(remote, "_resolve_src_root", lambda g, op="read": repo)
    monkeypatch.setattr(remote, "_log", lambda *a, **kw: None)
    monkeypatch.setattr(remote, "_locale_for_grant", lambda g: "en")
    monkeypatch.setattr(api_server_tools, "open_conflict_counts", lambda run: (1, 2))
    monkeypatch.setattr(ai_svc.ai_settings_service, "get_provider_secret", lambda scope, pid: "key")


def _results_by_id(conversation) -> dict:
    found = {}
    for msg in conversation:
        for block in (msg.get("content") if isinstance(msg.get("content"), list) else []):
            if block.get("type") == "tool_result":
                found.setdefault(block["tool_use_id"], json.loads(block["content"]))
    return found


def _conflict_run(run_id):
    return {"project_id": "p", "chain_source": "system", "run_id": run_id, "docs_target": 0,
            "raw_token": "tok", "action_scope": "resolve_conflict", "mode": "single", "group_id": "g",
            "merge_id": 1, "api_base_url": _API, "cancel_event": threading.Event(),
            "started_mono": time.monotonic(), "timeout_sec": 30}


def _spy_resolve(monkeypatch, repo, session):
    """A resolve that really moves the file, the index and the session if it is ever called."""
    def resolve(run, token, tool_input):
        session["calls"].append(tool_input)
        (repo / "a.py").write_text("head\nours one\nmiddle\nours two\ntail\n", encoding="utf-8", newline="\n")
        _git(["add", "a.py"], repo)
        session["resolved"] = True
        return 200, {"ok": True, "result": {"status": "resolved"}}
    monkeypatch.setattr(worker, "_resolve_conflict", resolve)


def _turn(calls):
    return None, calls, {"role": "assistant", "content": [{"type": "tool_use", **c} for c in calls]}


class TestFailedReadBlocksSameTurnResolve:
    @pytest.mark.parametrize("resolve_first", [False, True], ids=["read_then_resolve", "resolve_then_read"])
    @pytest.mark.parametrize("case", _SAME_TURN_FAILURES)
    def test_resolve_next_to_a_failed_read_is_never_dispatched(self, monkeypatch, tmp_path, case, resolve_first):
        repo = _conflicted_repo(tmp_path)
        if case.startswith("malformed:"):
            body, mode = {"path": "a.py", "start_line": 1, "end_line": 2}, "malformed"
            bad = _MALFORMED_RESULTS[case.split(":", 1)[1]]
        else:
            (body, mode), bad = _FAILURES[case], None
        _arm_remote(monkeypatch, repo, mode)
        if mode == "timeout":
            def slow(*_a):
                raise TimeoutError("remote tool timed out")
            monkeypatch.setattr(api_server_tools, "source_call", slow)
        if mode == "malformed":
            monkeypatch.setattr(api_server_tools, "source_call", lambda *_a: bad)
        session = {"calls": [], "resolved": False}
        _spy_resolve(monkeypatch, repo, session)
        seen: dict = {}

        def model(_u, _m, _k, conversation, _t, specs, _d, _s, _f=False, max_tokens=None):
            seen.update(_results_by_id(conversation))
            if seen:
                return "stopping", [], {"role": "assistant", "content": []}
            name = "write_source_file" if mode == "write_tool" else "read_source_file"
            read = {"id": "r1", "name": name, "input": body}
            submit = {"id": "c1", "name": "resolve_git_conflict", "input": json.loads(json.dumps(_RESOLVE_INPUT))}
            return _turn([submit, read] if resolve_first else [read, submit])

        monkeypatch.setattr(ai_svc, "_call_anthropic", model)
        before = _state(repo)
        run = _conflict_run(f"aiv_same_{case}")

        ai_svc._api_execute(_PROVIDER, _mention(), run)

        assert seen["r1"]["ok"] is False
        if mode == "malformed":
            assert seen["r1"]["error"]["details"]["reason"] == "malformed_tool_result"
        assert seen["c1"]["ok"] is False
        assert seen["c1"]["error"]["details"]["reason"] == "prerequisite_tool_failed"
        assert session == {"calls": [], "resolved": False}
        assert _state(repo) == before
        assert run["conflict_blocked_submits"] == 1

    def test_the_block_is_per_turn_a_later_successful_read_can_submit(self, monkeypatch, tmp_path):
        repo = _conflicted_repo(tmp_path)
        _arm_remote(monkeypatch, repo, None)
        session = {"calls": [], "resolved": False}
        _spy_resolve(monkeypatch, repo, session)
        turns = []

        def model(_u, _m, _k, conversation, _t, specs, _d, _s, _f=False, max_tokens=None):
            turns.append(_results_by_id(conversation))
            submit = json.loads(json.dumps(_RESOLVE_INPUT))
            if len(turns) == 1:    # a bad range next to a guess: blocked
                return _turn([{"id": "r1", "name": "read_source_file",
                               "input": {"path": "a.py", "start_line": 9, "end_line": 2}},
                              {"id": "c1", "name": "resolve_git_conflict", "input": submit}])
            if len(turns) == 2:    # a successful re-read next to the submit: dispatched
                return _turn([{"id": "r2", "name": "read_source_file",
                               "input": {"path": "a.py", "start_line": 1, "end_line": 5}},
                              {"id": "c2", "name": "resolve_git_conflict", "input": submit}])
            return "done", [], {"role": "assistant", "content": []}

        monkeypatch.setattr(ai_svc, "_call_anthropic", model)
        run = _conflict_run("aiv_block_then_submit")

        ai_svc._api_execute(_PROVIDER, _mention(), run)

        assert turns[1]["r1"]["ok"] is False
        assert turns[1]["c1"]["error"]["details"]["reason"] == "prerequisite_tool_failed"
        assert len(session["calls"]) == 1 and session["resolved"] is True
        assert run["conflict_blocked_submits"] == 1
