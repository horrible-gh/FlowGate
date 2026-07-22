"""flowgate.default.0295 T0004: CLI-provider command normalization and last-message recovery.

Regression cover for 0295 B0001 / NR0003 — two independent ways a CLI provider could look
broken while the CLI itself was fine:

  * codex was spawned without ``--skip-git-repo-check``, so any run whose cwd was not a git
    repository (scratch fallback, non-git project mirror, the probe's mkdtemp) died with
    "Not inside a trusted directory..." inside the fast-fail window and was burned as a
    provider failure.
  * copilot's ``--output-format=json`` NDJSON stream contains no blank lines, so the shared
    block splitter handed the operator the whole event dump — MCP status logs and all —
    instead of the answer.

The copilot fixture below is a trimmed capture of a real
``copilot --model claude-sonnet-5 --output-format=json`` run on Windows.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service  # noqa: E402
from modules.flow_gate.services import ai_provider_probe_service  # noqa: E402
from modules.flow_gate.services import process_runner  # noqa: E402
from modules.flow_gate.settings import ai_settings_service  # noqa: E402

_SKIP_GIT = "--skip-git-repo-check"

# Real capture, trimmed: the session/mcp noise that used to be reported as the answer, the
# deltas that carry the same text in fragments, the assistant.message that actually holds
# it, and the textless result event.
COPILOT_STREAM = "\n".join(
    [
        '{"type":"session.mcp_server_status_changed","data":{"serverName":"github-mcp-server","status":"pending"},"id":"9bb82a28"}',
        '{"type":"session.mcp_servers_loaded","data":{"servers":[{"name":"github-mcp-server","status":"connected"}]},"id":"74f9e9e9"}',
        '{"type":"user.message","data":{"content":"Reply with exactly: HELLO123\\n"},"id":"6a4997ce"}',
        '{"type":"assistant.message_start","data":{"messageId":"a5c385de"},"id":"1"}',
        '{"type":"assistant.message_delta","data":{"messageId":"a5c385de","deltaContent":"H"},"id":"2"}',
        '{"type":"assistant.message_delta","data":{"messageId":"a5c385de","deltaContent":"ELLO123"},"id":"3"}',
        '{"type":"assistant.message","data":{"messageId":"a5c385de","model":"claude-sonnet-5","content":"HELLO123","toolRequests":[]},"id":"4"}',
        '{"type":"assistant.turn_end","data":{"turnId":"0"},"id":"5"}',
        '{"type":"result","timestamp":"2026-07-22T06:48:35.018Z","exitCode":0,"usage":{"premiumRequests":1}}',
    ]
)


class TestNormalizeCliCommand:
    def test_codex_gets_the_git_check_flag(self):
        stored = (
            "codex --ask-for-approval never --sandbox workspace-write exec "
            "-c sandbox_workspace_write.network_access=true --json --model gpt-5.6-sol -"
        )
        out = ai_settings_service.normalize_cli_command("codex", stored)
        assert out.startswith(stored)
        assert out.endswith(_SKIP_GIT)

    def test_codex_flag_is_not_duplicated(self):
        stored = f"codex exec {_SKIP_GIT} --json -"
        assert ai_settings_service.normalize_cli_command("codex", stored) == stored

    @pytest.mark.parametrize("kind", ["claude", "copilot", "custom", "", None])
    def test_other_kinds_are_untouched(self, kind):
        stored = "some-cli --flag -"
        assert ai_settings_service.normalize_cli_command(kind, stored) == stored

    def test_blank_command_stays_blank(self):
        # The probe reports "enter a command" for this; normalization must not invent one.
        assert ai_settings_service.normalize_cli_command("codex", "   ") == ""
        assert ai_settings_service.normalize_cli_command("codex", None) == ""

    def test_catalog_examples_carry_the_required_flags(self):
        examples = ai_settings_service.get_catalog()["cli_examples"]
        for host_os in ("posix", "nt"):
            assert _SKIP_GIT in examples["codex"][host_os]
            # A permission prompt nobody can answer is indistinguishable from a slow run.
            assert "--dangerously-skip-permissions" in examples["claude"][host_os]
            assert "--model" in examples["claude"][host_os]

    def test_catalog_examples_are_already_normalized(self):
        # Otherwise a freshly pasted example would still be rewritten at spawn time.
        for host_os in ("posix", "nt"):
            example = ai_settings_service.get_catalog()["cli_examples"]["codex"][host_os]
            assert ai_settings_service.normalize_cli_command("codex", example) == example


class TestRecoverCliLastMessage:
    def _recover(self, kind, stdout, last_message_file=Path("__missing__")):
        run: dict = {}
        ai_invoke_service._recover_cli_last_message(run, kind, stdout, last_message_file)
        return run

    def test_copilot_answer_is_extracted_not_the_event_dump(self):
        run = self._recover("copilot", COPILOT_STREAM)
        assert run["last_message"] == "HELLO123"
        assert run["last_message_received"] is True
        assert "mcp_server" not in run["last_message"]

    def test_copilot_takes_the_last_assistant_message(self):
        stream = COPILOT_STREAM + "\n" + '{"type":"assistant.message","data":{"content":"second"}}'
        assert self._recover("copilot", stream)["last_message"] == "second"

    def test_copilot_falls_back_to_block_split_on_plain_output(self):
        # e.g. a copilot that failed before emitting any event, or a command without
        # --output-format=json. Losing the diagnostic would be worse than a rough message.
        run = self._recover("copilot", "first block\n\nsomething went wrong")
        assert run["last_message"] == "something went wrong"

    def test_copilot_empty_output_reports_nothing_received(self):
        run = self._recover("copilot", "")
        assert run["last_message"] is None
        assert run["last_message_received"] is False

    def test_custom_still_uses_block_split(self):
        assert self._recover("custom", "block a\n\nblock b")["last_message"] == "block b"

    def test_claude_still_uses_whole_stdout(self):
        assert self._recover("claude", "  plain answer  ")["last_message"] == "plain answer"

    def test_codex_still_uses_the_last_message_file(self, tmp_path):
        lm = tmp_path / "last_message.txt"
        lm.write_text("from file\n", encoding="utf-8")
        run = self._recover("codex", '{"type":"item.completed"}', lm)
        assert run["last_message"] == "from file"

    def test_codex_missing_file_does_not_fall_back_to_the_json_stream(self):
        # The stream is machine output; reporting it as the answer is what NR0003 fixed.
        assert self._recover("codex", '{"type":"turn.completed"}')["last_message"] is None


class TestSpawnedCommand:
    """The flag has to reach the process that is actually spawned — a provider row stored
    before this fix (B0001's "Codex GPT-5.6") never passes through the catalog again."""

    def _spawn_cmd(self, monkeypatch, tmp_path, provider) -> str:
        seen: dict = {}

        def fake_unc_safe_shell(cmd, cwd):
            seen["cmd"] = cmd
            return cmd, cwd

        def boom(*a, **kw):
            raise OSError("not spawned by design")

        monkeypatch.setattr(process_runner, "unc_safe_shell", fake_unc_safe_shell)
        monkeypatch.setattr("subprocess.Popen", boom)
        run = {
            "run_id": "r1",
            "scratch_dir": str(tmp_path),
            "source_root": str(tmp_path),
            "raw_token": "t",
            "api_base_url": "",
        }
        status, _ = ai_invoke_service._cli_execute(provider, "hi", run)
        assert status == "spawn_failed"  # the fake Popen, i.e. we got past command assembly
        return seen["cmd"]

    def test_stored_codex_row_is_repaired_at_spawn_time(self, monkeypatch, tmp_path):
        provider = {
            "kind": "codex",
            "cli_command": "codex --ask-for-approval never exec --json --model gpt-5.6-sol -",
        }
        cmd = self._spawn_cmd(monkeypatch, tmp_path, provider)
        assert _SKIP_GIT in cmd
        # ...and the codex-only --output-last-message synthesis still lands after it.
        assert "--output-last-message" in cmd

    def test_non_codex_command_is_spawned_verbatim(self, monkeypatch, tmp_path):
        provider = {"kind": "claude", "cli_command": "claude -p -"}
        assert self._spawn_cmd(monkeypatch, tmp_path, provider) == "claude -p -"


class TestProbeCommand:
    """§5-3: the probe's cwd is tempfile.mkdtemp(), never a git repo, so without the same
    normalization every codex provider came back command_failed however correct it was."""

    def _probe_cmd(self, monkeypatch, form) -> str:
        seen: dict = {}

        def fake_run_probe(cli_command, root, timeout, env, prompt):
            seen["cmd"] = cli_command
            return False, 0, "", ""

        monkeypatch.setattr(ai_provider_probe_service, "_run_probe", fake_run_probe)
        result = ai_provider_probe_service.probe_provider(form)
        assert result["status"] == "ok"
        return seen["cmd"]

    def test_probe_runs_the_normalized_codex_command(self, monkeypatch):
        form = {
            "exec_type": "cli",
            "kind": "codex",
            "cli_command": "codex exec --json --model gpt-5.6-sol -",
        }
        assert _SKIP_GIT in self._probe_cmd(monkeypatch, form)

    def test_probe_leaves_other_kinds_alone(self, monkeypatch):
        form = {"exec_type": "cli", "kind": "copilot", "cli_command": "copilot -p -"}
        assert self._probe_cmd(monkeypatch, form) == "copilot -p -"

    def test_blank_command_still_reports_required_for_cli(self, monkeypatch):
        # Normalization must not turn "operator left it empty" into a spawn attempt.
        result = ai_provider_probe_service.probe_provider(
            {"exec_type": "cli", "kind": "codex", "cli_command": "  "}
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "required_for_cli"
