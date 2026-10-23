"""Run-less provider probe adapters (flowgate.v02.0014 HOLD 2.3 / 2.6).

AI Evaluation executes its baseline prompt through `provider_api.probe_api_completion` and
`provider_cli.probe_cli_completion` instead of owning sockets and processes. These tests pin the
adapter contract the evaluation service relies on: outcome classification, timeout, secret
handling and - above all - that a cancel through the shared `ProbeHandle` interrupts a blocked
request or kills the CLI process tree, whenever in the call it arrives.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from modules.flow_gate.services.ai_invoke import provider_api, provider_cli  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime as probe  # noqa: E402


class _Api:
    """Loopback API double recording every request it receives."""

    def __init__(self, status=200, body=None, delay=0.0, block=None):
        self.requests: list[dict] = []
        self.entered = threading.Event()
        outer = self
        payload = json.dumps(body if body is not None else {"choices": [{"message": {"content": "ok"}}]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": json.loads(self.rfile.read(length) or b"{}")})
                outer.entered.set()
                if block is not None:
                    block.wait(10)
                elif delay:
                    time.sleep(delay)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def api():
    made = []

    def make(**kw):
        made.append(_Api(**kw))
        return made[-1]
    yield make
    for item in made:
        item.close()


def _call(base, **over):
    args = dict(kind="openai", base_url=base, model="m", credential="secret-key", prompt="hello",
                timeout=5, handle=probe.ProbeHandle())
    args.update(over)
    return provider_api.probe_api_completion(**args)


# ── ProbeHandle ────────────────────────────────────────────────────────────────

def test_handle_fires_bound_aborts_once_and_bind_after_cancel_aborts_immediately():
    handle = probe.ProbeHandle()
    fired = []
    assert handle.bind(lambda: fired.append("a")) is True
    handle.cancel()
    handle.cancel()
    assert handle.cancelled and fired == ["a"]
    assert handle.bind(lambda: fired.append("late")) is False
    assert fired == ["a", "late"]


def test_handle_swallows_a_failing_abort_so_the_canceller_never_breaks():
    handle = probe.ProbeHandle()
    fired = []

    def broken():
        raise RuntimeError("boom")
    handle.bind(broken)
    handle.bind(lambda: fired.append("second"))
    handle.cancel()
    assert fired == ["second"]


# ── API adapter ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("missing", ["base_url", "model", "credential"])
def test_api_missing_field_is_launch_failed_without_network(missing):
    assert _call("http://127.0.0.1:1", **{missing: None}).kind == probe.PROBE_LAUNCH_FAILED


@pytest.mark.parametrize("url", ["ftp://example.test/x", "not a url", "http:///nohost"])
def test_api_invalid_url_is_transport_failed(url):
    assert _call(url).kind == probe.PROBE_TRANSPORT_FAILED


def test_api_openai_wire_and_secret_only_in_the_authorization_header(api):
    server = api()
    outcome = _call(server.base + "/v1")
    assert (outcome.kind, outcome.text) == (probe.PROBE_OK, "ok")
    request = server.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["authorization"] == "Bearer secret-key"
    assert request["body"] == {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
    assert "secret-key" not in json.dumps(request["body"])
    assert "secret-key" not in repr(outcome)


def test_api_claude_wire(api):
    server = api(body={"content": [{"type": "text", "text": "hi"}, {"type": "tool_use"}, {"type": "text", "text": "there"}]})
    outcome = _call(server.base, kind="claude")
    assert (outcome.kind, outcome.text) == (probe.PROBE_OK, "hi\nthere")
    request = server.requests[0]
    assert request["path"] == "/v1/messages"
    assert request["headers"]["x-api-key"] == "secret-key"
    assert request["headers"]["anthropic-version"] == provider_api.ANTHROPIC_VERSION
    assert request["body"]["max_tokens"] == 100 and request["body"]["model"] == "m"


def test_api_http_error_is_transport_failed_and_does_not_echo_the_secret(api):
    server = api(status=500, body={"error": "secret-key leaked?"})
    outcome = _call(server.base)
    assert outcome.kind == probe.PROBE_TRANSPORT_FAILED
    assert "secret-key" not in repr(outcome)


def test_api_slow_response_is_timeout(api):
    server = api(delay=3)
    started = time.monotonic()
    assert _call(server.base, timeout=0.5).kind == probe.PROBE_TIMEOUT
    assert time.monotonic() - started < 2.5


def test_api_pre_cancelled_handle_never_touches_the_network(api):
    server = api()
    handle = probe.ProbeHandle()
    handle.cancel()
    assert _call(server.base, handle=handle).kind == probe.PROBE_CANCELLED
    assert server.requests == []


def test_api_cancel_mid_flight_aborts_the_blocked_request(api):
    release = threading.Event()
    server = api(block=release)
    handle = probe.ProbeHandle()
    result = []
    worker = threading.Thread(target=lambda: result.append(_call(server.base, handle=handle, timeout=30)))
    worker.start()
    try:
        assert server.entered.wait(5)
        started = time.monotonic()
        handle.cancel()
        worker.join(5)
        assert not worker.is_alive(), "cancel left the API request blocked"
        assert time.monotonic() - started < 3
        assert result[0].kind == probe.PROBE_CANCELLED
    finally:
        release.set()


def test_api_without_a_handle_keeps_the_plain_urlopen_path(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        seen["timeout"] = timeout
        seen["url"] = request.full_url
        return Response()
    monkeypatch.setattr(provider_api.urllib.request, "urlopen", fake_urlopen)
    assert provider_api._http_post_json("http://h/x", {}, {"a": 1}, 7) == {"ok": True}
    assert seen == {"timeout": 7, "url": "http://h/x"}


# ── CLI adapter ────────────────────────────────────────────────────────────────
# probe_cli_completion is a thin caller of the same CLI boundary a run uses (scratch manifest,
# _prepare_cli_command, _resolve_cli_launch policy, _CliProcess lifecycle). These tests run real
# child processes against a temp scratch root and pin the boundary, not a re-implementation.

@pytest.fixture(autouse=True)
def scratch_root(monkeypatch, tmp_path):
    root = tmp_path / "scratch" / "probe"
    monkeypatch.setattr(probe, "_project_scratch_root", lambda project_id: root)
    monkeypatch.setattr(probe.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path)
    return root


def _script(tmp_path, body):
    path = tmp_path / "provider.py"
    path.write_text(body)
    return f'"{sys.executable}" "{path}"'


def _cli(command, **over):
    args = dict(project_id="p", kind="custom", command=command, prompt="ping", timeout=20,
                handle=probe.ProbeHandle())
    args.update(over)
    return provider_cli.probe_cli_completion(**args)


def _leftover(root):
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def test_cli_prompt_goes_through_stdin_and_stdout_is_the_answer(tmp_path, scratch_root):
    outcome = _cli(_script(tmp_path, "import sys\nprint('echo:' + sys.stdin.read())\n"))
    assert outcome.kind == probe.PROBE_OK
    assert "echo:ping" in outcome.text
    assert _leftover(scratch_root) == []


def test_cli_does_not_inherit_flowgate_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWGATE_TOKEN", "must-not-leak")
    outcome = _cli(_script(tmp_path, "import os\nprint('token=' + repr(os.environ.get('FLOWGATE_TOKEN')))\n"))
    assert outcome.kind == probe.PROBE_OK
    assert "must-not-leak" not in outcome.text


def test_cli_child_runs_in_a_managed_scratch_that_is_deleted_afterwards(tmp_path, scratch_root):
    outcome = _cli(_script(tmp_path, (
        "import os\n"
        "print(os.getcwd())\n"
        "print(os.environ['TMP'])\n"
        "print(os.environ.get('FLOWGATE_SCRATCH'))\n")))
    assert outcome.kind == probe.PROBE_OK
    # custom kind answers with the last non-blank block; the three lines are one block
    cwd, tmp, scratch = [line.strip() for line in outcome.text.splitlines()]
    root = scratch_root.resolve()
    assert Path(cwd).resolve().parent == root
    assert Path(cwd).name.startswith("aiv_")
    assert Path(scratch).resolve() == Path(cwd).resolve()
    assert Path(tmp).resolve().is_relative_to(Path(cwd).resolve())
    assert _leftover(scratch_root) == []


def test_cli_answer_longer_than_a_run_last_message_is_returned_whole(tmp_path):
    # A judge's JSON reply past LAST_MESSAGE_MAX_BYTES lost its front (and its opening brace)
    # to the run's tail truncation, failing the evaluation as judge_output_invalid.
    answer = json.dumps({"instruction": "x", "files": [{"path": "a.txt", "content": "가" * probe.LAST_MESSAGE_MAX_BYTES}]},
                        ensure_ascii=False)
    reply = tmp_path / "reply.json"
    reply.write_bytes(answer.encode("utf-8"))
    outcome = _cli(_script(tmp_path, (
        "import sys\n"
        "sys.stdin.read()\n"
        f"sys.stdout.buffer.write(open(r'{reply}', 'rb').read())\n")), kind="claude")
    assert outcome.kind == probe.PROBE_OK, outcome
    assert outcome.text == answer


def test_run_last_message_is_still_front_truncated():
    run: dict = {}
    provider_cli._recover_cli_last_message(run, "claude", "a" * 10 + "b" * probe.LAST_MESSAGE_MAX_BYTES,
                                           Path("__missing__"))
    assert run["last_message"] == "b" * probe.LAST_MESSAGE_MAX_BYTES


def test_cli_codex_goes_through_the_shared_command_normalisation(tmp_path):
    outcome = _cli(_script(tmp_path, (
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "assert '--skip-git-repo-check' in args, args\n"
        "target = args[args.index('--output-last-message') + 1]\n"
        "pathlib.Path(target).write_text('answer-from-file')\n"
        "print('stdout noise')\n")), kind="codex")
    assert outcome.kind == probe.PROBE_OK, outcome
    assert outcome.text == "answer-from-file"


def test_cli_blocked_launch_policy_is_launch_failed_and_nothing_is_spawned(tmp_path, monkeypatch, scratch_root):
    marker = tmp_path / "spawned.txt"
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch", lambda provider, run, command: (None, "scratch_unavailable"))
    command = _script(tmp_path, f"import pathlib\npathlib.Path(r'{marker}').write_text('x')\n")
    assert _cli(command).kind == probe.PROBE_LAUNCH_FAILED
    assert not marker.exists()
    assert _leftover(scratch_root) == []


def test_cli_scratch_that_cannot_be_created_is_launch_failed(tmp_path, monkeypatch):
    def boom(project_id, run_id):
        raise OSError("disk full")
    monkeypatch.setattr(provider_cli, "_create_scratch", boom)
    assert _cli(_script(tmp_path, "print('x')\n")).kind == probe.PROBE_LAUNCH_FAILED


@pytest.mark.parametrize("command", [None, "", "   "])
def test_cli_empty_command_is_launch_failed(command, scratch_root):
    assert _cli(command).kind == probe.PROBE_LAUNCH_FAILED
    assert _leftover(scratch_root) == []


def test_cli_non_zero_exit_is_execution_failed(tmp_path, scratch_root):
    assert _cli(_script(tmp_path, "import sys\nsys.exit(3)\n")).kind == probe.PROBE_EXECUTION_FAILED
    assert _leftover(scratch_root) == []


def test_cli_timeout_kills_the_process_and_reports_timeout(tmp_path, scratch_root):
    started = time.monotonic()
    outcome = _cli(_script(tmp_path, "import time\ntime.sleep(60)\n"), timeout=1)
    assert outcome.kind == probe.PROBE_TIMEOUT
    assert time.monotonic() - started < 20
    assert _leftover(scratch_root) == []


def test_cli_pre_cancelled_handle_reports_cancelled(tmp_path):
    handle = probe.ProbeHandle()
    handle.cancel()
    assert _cli(_script(tmp_path, "import time\ntime.sleep(60)\n"), handle=handle).kind == probe.PROBE_CANCELLED


def test_cli_cancel_mid_flight_kills_the_process_tree(tmp_path, scratch_root):
    beat = tmp_path / "beat.txt"
    command = _script(tmp_path, (
        "import pathlib, sys, time\n"
        f"beat = pathlib.Path(r'{beat}')\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    beat.write_text(str(i))\n"
        "    time.sleep(0.1)\n"))
    handle = probe.ProbeHandle()
    result = []
    worker = threading.Thread(target=lambda: result.append(_cli(command, handle=handle, timeout=60)))
    worker.start()
    deadline = time.time() + 15
    while not beat.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert beat.exists(), "CLI provider never started"
    handle.cancel()
    worker.join(15)
    assert not worker.is_alive(), "cancel left the CLI process running"
    assert result[0].kind == probe.PROBE_CANCELLED
    first = beat.read_text()
    time.sleep(0.6)
    assert beat.read_text() == first
    assert _leftover(scratch_root) == []


# ── anti-duplication guard ─────────────────────────────────────────────────────

def _module_tree():
    return ast.parse(Path(provider_cli.__file__).read_text(encoding="utf-8"))


def _called_names(node):
    names = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            names.append(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def test_probe_cli_completion_owns_no_process_lifecycle_of_its_own():
    tree = _module_tree()
    probe_fn = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "probe_cli_completion")
    used = {n.attr for n in ast.walk(probe_fn) if isinstance(n, ast.Attribute)}
    used |= {n.id for n in ast.walk(probe_fn) if isinstance(n, ast.Name)}
    duplicated = {"Popen", "WindowsProcessOwner", "communicate_with_cleanup", "kill_process_tree",
                  "popen_kwargs", "attach", "unc_safe_shell", "normalize_cli_command"}
    assert not (used & duplicated), used & duplicated
    assert {"_resolve_cli_launch", "_prepare_cli_command", "_CliProcess", "_cli_popen_kwargs",
            "_cli_scratch_env"} <= used


def test_the_cli_process_lifecycle_exists_once_and_is_shared_with_runs():
    tree = _module_tree()
    calls = _called_names(tree)
    assert calls.count("Popen") == 1
    assert calls.count("WindowsProcessOwner") == 1
    shared = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "_CliProcess")
    assert calls.count("communicate_with_cleanup") == _called_names(shared).count("communicate_with_cleanup") > 0
    assert calls.count("normalize_cli_command") == 1
    cli_execute = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_cli_execute")
    used = {n.id for n in ast.walk(cli_execute) if isinstance(n, ast.Name)}
    assert {"_CliProcess", "_prepare_cli_command", "_cli_popen_kwargs", "_cli_scratch_env"} <= used
