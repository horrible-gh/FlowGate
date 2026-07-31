"""flowgate.default.0371 T0014 / NR0007 §5 — permission confirmation is on by default.

Both agent CLIs FlowGate spawns can be told to stop asking before they read, write or run
anything: claude's ``--dangerously-skip-permissions`` and codex's ``--ask-for-approval
never``. There is no FlowGate setting for that — it is a word inside the free-text
``cli_command`` — and until this change both words sat in the catalog example, which is
what the installer seeds and what the settings screen suggests. So every provider created
the easy way ran with permission checks off, and nobody ever chose it.

What is covered here:

  * the catalog hands out the SAFE form, and nothing in the server re-introduces the flag;
  * the permissive form still exists, is derived from the safe one (never a second copy),
    and is reachable only by an explicit opt-in (editor checkbox, seed --skip-permissions);
  * detection is honest about hand-written and legacy commands, and does not fire on a
    longer word that merely starts with a flag we know;
  * the probe says which mode it tested, because "still running at timeout" is what a CLI
    parked on an approval prompt looks like too.

Existing rows are deliberately not rewritten, so the invoke path's own normalization is
asserted to leave them exactly as the operator stored them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_provider_probe_service  # noqa: E402
from modules.flow_gate.settings import ai_settings_service as svc  # noqa: E402

HOST_KEYS = ("posix", "nt")
CLAUDE_SKIP = "--dangerously-skip-permissions"
CODEX_SKIP = "--ask-for-approval never"
CODEX_SAFE = "--ask-for-approval on-request"

ALL_MARKERS = tuple(
    marker
    for rule in svc._PERMISSION_SKIP_RULES.values()
    for marker in rule["markers"]
)


def _examples():
    return svc.get_catalog()["cli_examples"]


def _skip_block():
    return svc.get_catalog()["cli_permission_skip"]


# ── The default the product ships ────────────────────────────────────────────

class TestCatalogDefaultsAreSafe:
    @pytest.mark.parametrize("host_os", HOST_KEYS)
    @pytest.mark.parametrize("kind", ["claude", "codex", "copilot", "custom"])
    def test_no_example_command_switches_permission_checks_off(self, kind, host_os):
        command = _examples()[kind][host_os]
        assert not svc.has_permission_skip(kind, command)
        for marker in ALL_MARKERS:
            assert marker not in command

    @pytest.mark.parametrize("host_os", HOST_KEYS)
    def test_claude_example_keeps_its_model_and_stdin_form(self, host_os):
        """Dropping the flag must not quietly undo the rest of the 0295 fix."""
        command = _examples()["claude"][host_os]
        assert "--model" in command
        assert command.endswith("-p -")

    @pytest.mark.parametrize("host_os", HOST_KEYS)
    def test_codex_example_names_a_policy_that_asks(self, host_os):
        """Removing `never` without naming a policy would leave the mode unstated."""
        command = _examples()["codex"][host_os]
        assert CODEX_SAFE in command
        assert svc.CODEX_SKIP_GIT_FLAG in command
        # ...and it is still what the invoke path would spawn, unchanged.
        assert svc.normalize_cli_command("codex", command) == command

    def test_catalog_states_the_default_out_loud(self):
        """The screen renders the checkbox from this, so "off" has to be data, not a habit."""
        assert _skip_block()["default_enabled"] is False

    def test_catalog_publishes_the_flags_instead_of_the_client_owning_them(self):
        rules = _skip_block()["rules"]
        assert rules["claude"]["skip"] == CLAUDE_SKIP
        assert rules["codex"]["skip"] == CODEX_SKIP
        assert rules["codex"]["safe"] == CODEX_SAFE
        assert CLAUDE_SKIP in rules["claude"]["markers"]
        # A kind with no verified flag gets no rule — the editor hides the control rather
        # than inventing an option that does nothing.
        assert "copilot" not in rules and "custom" not in rules


class TestUnattendedExamplesAreDerived:
    @pytest.mark.parametrize("host_os", HOST_KEYS)
    @pytest.mark.parametrize("kind", ["claude", "codex"])
    def test_the_opt_in_example_is_the_safe_one_plus_the_flag(self, kind, host_os):
        unattended = _skip_block()["examples"][kind][host_os]
        assert svc.has_permission_skip(kind, unattended)
        assert svc.set_permission_skip(kind, unattended, False) == _examples()[kind][host_os]

    def test_only_kinds_with_a_known_flag_appear(self):
        assert set(_skip_block()["examples"]) == {"claude", "codex"}


# ── Detection ────────────────────────────────────────────────────────────────

class TestDetection:
    @pytest.mark.parametrize("kind,command", [
        ("claude", f"claude {CLAUDE_SKIP} -p -"),
        ("codex", "codex --ask-for-approval never exec -"),
        ("codex", "codex --ask-for-approval=never exec -"),
        ("codex", "codex --yolo exec -"),
        ("codex", "codex --dangerously-bypass-approvals-and-sandbox exec -"),
    ])
    def test_reports_a_command_that_does_not_ask(self, kind, command):
        assert svc.has_permission_skip(kind, command) is True

    @pytest.mark.parametrize("kind,command", [
        ("claude", "claude --model claude-opus-4-8 -p -"),
        ("codex", "codex --ask-for-approval on-request exec -"),
        ("codex", "codex --ask-for-approval untrusted exec -"),
    ])
    def test_reports_a_command_that_asks(self, kind, command):
        assert svc.has_permission_skip(kind, command) is False

    @pytest.mark.parametrize("kind,command", [
        ("codex", "codex --ask-for-approval never-mind exec -"),
        ("codex", "codex --yolo-mode exec -"),
        ("claude", f"claude {CLAUDE_SKIP}-not -p -"),
    ])
    def test_a_longer_word_is_not_the_flag(self, kind, command):
        """Substring matching here would flag commands that do ask, and the editor would
        then offer to "turn off" something that is not there."""
        assert svc.has_permission_skip(kind, command) is False

    @pytest.mark.parametrize("kind", ["copilot", "custom", "", None])
    def test_kinds_without_a_known_flag_are_never_reported(self, kind):
        assert svc.has_permission_skip(kind, f"anything {CLAUDE_SKIP}") is False
        assert svc.permission_skip_rule(kind) is None

    @pytest.mark.parametrize("command", ["", "   ", None])
    def test_an_empty_command_is_not_a_skipping_command(self, command):
        assert svc.has_permission_skip("claude", command) is False


# ── The opt-in edit ──────────────────────────────────────────────────────────

class TestSetPermissionSkip:
    @pytest.mark.parametrize("host_os", HOST_KEYS)
    @pytest.mark.parametrize("kind", ["claude", "codex"])
    def test_toggling_twice_returns_the_original(self, kind, host_os):
        """The editor lets someone tick and untick the box before saving; that must not
        leave the command subtly rewritten."""
        original = _examples()[kind][host_os]
        on = svc.set_permission_skip(kind, original, True)
        assert on != original
        assert svc.set_permission_skip(kind, on, False) == original

    def test_the_flag_lands_before_the_stdin_dash_not_after_it(self):
        """`-p -` ends the claude command with the prompt argument; a flag appended after
        it is read by the wrong parsing stage."""
        out = svc.set_permission_skip("claude", "claude --model m -p -", True)
        assert out == f"claude {CLAUDE_SKIP} --model m -p -"

    def test_codex_policy_is_rewritten_where_it_stands(self):
        out = svc.set_permission_skip(
            "codex", "codex --ask-for-approval on-request --sandbox workspace-write exec -", True,
        )
        assert out == "codex --ask-for-approval never --sandbox workspace-write exec -"

    def test_switching_off_names_the_safe_policy_for_codex(self):
        out = svc.set_permission_skip("codex", "codex --yolo exec --json -", False)
        assert out == "codex --ask-for-approval on-request exec --json -"
        assert not svc.has_permission_skip("codex", out)

    def test_switching_on_twice_does_not_duplicate_the_flag(self):
        once = svc.set_permission_skip("claude", "claude -p -", True)
        assert svc.set_permission_skip("claude", once, True) == once
        assert once.count(CLAUDE_SKIP) == 1

    def test_switching_off_a_command_that_already_asks_changes_nothing(self):
        for kind, command in [
            ("claude", "claude --model m -p -"),
            ("codex", "codex exec --json -"),
            ("codex", "codex --yolo-mode exec -"),
        ]:
            assert svc.set_permission_skip(kind, command, False) == command

    @pytest.mark.parametrize("kind", ["copilot", "custom", "", None])
    def test_a_kind_with_no_known_flag_is_left_alone(self, kind):
        command = "copilot --model claude-sonnet-5 --output-format=json"
        assert svc.set_permission_skip(kind, command, True) == command
        assert svc.set_permission_skip(kind, command, False) == command

    @pytest.mark.parametrize("command", ["", "   ", None])
    def test_an_empty_command_is_not_given_a_flag(self, command):
        assert svc.set_permission_skip("claude", command, True) == ""


# ── Nothing puts the flag back on its own ────────────────────────────────────

class TestNothingReintroducesTheFlag:
    @pytest.mark.parametrize("host_os", HOST_KEYS)
    @pytest.mark.parametrize("kind", ["claude", "codex", "copilot", "custom"])
    def test_spawn_time_normalization_keeps_a_safe_command_safe(self, kind, host_os):
        """normalize_cli_command() injects what a run cannot work without. Permission
        confirmation is not in that category — it is a choice."""
        command = _examples()[kind][host_os]
        assert not svc.has_permission_skip(kind, svc.normalize_cli_command(kind, command))

    def test_a_stored_row_is_still_spawned_exactly_as_stored(self):
        """A provider registered before this change keeps running as its operator set it
        up; "default off" is about new providers, not about rewriting configuration."""
        stored = f"claude {CLAUDE_SKIP} -p -"
        assert svc.normalize_cli_command("claude", stored) == stored

    def test_no_other_server_module_writes_a_permission_flag(self):
        """A second place naming these flags would be a second default, invisible from the
        settings catalog — which is exactly how the old one survived four groups."""
        allowed = {
            _SERVER_DIR / "modules" / "flow_gate" / "settings" / "ai_settings_service.py",
        }
        offenders = []
        for path in sorted(_SERVER_DIR.rglob("*.py")):
            parts = path.parts
            if "tests" in parts or ".venv" in parts or path in allowed:
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            if any(marker in body for marker in ALL_MARKERS):
                offenders.append(str(path.relative_to(_SERVER_DIR)))
        assert offenders == []


# ── The probe reports which mode it tested ───────────────────────────────────

class TestProbeReportsThePermissionMode:
    def _probe(self, monkeypatch, kind, command, timed_out=True, exit_code=None):
        monkeypatch.setattr(
            ai_provider_probe_service, "_run_probe",
            lambda *a, **kw: (timed_out, exit_code, "", ""),
        )
        return ai_provider_probe_service.probe_provider(
            {"exec_type": "cli", "kind": kind, "cli_command": command},
        )

    def test_a_command_that_asks_is_not_reported_as_a_clean_launch(self, monkeypatch):
        """"Still running when we pulled the plug" is also what waiting for an approval
        looks like, so the operator is told which of the two they are looking at."""
        result = self._probe(monkeypatch, "claude", "claude --model m -p -")
        assert result["status"] == "launched"
        assert result["permission_skip"] is False
        assert "permission-skip" in result["message"]

    def test_a_skipping_command_gets_no_such_note(self, monkeypatch):
        result = self._probe(monkeypatch, "claude", f"claude {CLAUDE_SKIP} -p -")
        assert result["permission_skip"] is True
        assert "permission-skip" not in result["message"]

    def test_a_kind_without_a_known_flag_is_not_offered_an_option(self, monkeypatch):
        result = self._probe(monkeypatch, "copilot", "copilot --output-format=json")
        assert result["permission_skip"] is False
        assert "permission-skip" not in result["message"]

    def test_a_clean_exit_is_reported_as_before(self, monkeypatch):
        result = self._probe(monkeypatch, "claude", "claude -p -",
                             timed_out=False, exit_code=0)
        assert result["status"] == "ok"
        assert result["permission_skip"] is False

    def test_a_skipped_probe_still_answers_the_question(self, monkeypatch):
        result = ai_provider_probe_service.probe_provider(
            {"exec_type": "cli", "kind": "claude", "cli_command": "  "},
        )
        assert result["status"] == "skipped"
        assert result["permission_skip"] is False


# ── The installer asks instead of assuming ───────────────────────────────────

@pytest.fixture
def seed():
    cwd = os.getcwd()
    try:
        import seed_ai_provider
        yield seed_ai_provider
    finally:
        os.chdir(cwd)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("FLOWGATE_AI_"):
            monkeypatch.delenv(key, raising=False)


def _args(seed, *argv):
    return seed.build_parser().parse_args(list(argv))


class TestSeedOptIn:
    def test_the_seeded_command_asks_by_default(self, seed):
        row = seed.provider_from_options(svc, _args(seed, "--kind", "claude"))
        assert not svc.has_permission_skip("claude", row["cli_command"])
        assert row["cli_command"] == seed.default_cli_command(svc, "claude")

    def test_the_flag_switches_it_off(self, seed):
        row = seed.provider_from_options(
            svc, _args(seed, "--kind", "claude", "--skip-permissions"))
        assert svc.has_permission_skip("claude", row["cli_command"])

    def test_the_env_var_switches_it_off_for_an_unattended_install(self, seed, monkeypatch):
        monkeypatch.setenv("FLOWGATE_AI_SKIP_PERMISSIONS", "1")
        row = seed.provider_from_options(svc, _args(seed, "--kind", "codex"))
        assert svc.has_permission_skip("codex", row["cli_command"])

    @pytest.mark.parametrize("value", ["", "0", "no", "off"])
    def test_a_falsy_env_value_is_not_an_opt_in(self, seed, monkeypatch, value):
        monkeypatch.setenv("FLOWGATE_AI_SKIP_PERMISSIONS", value)
        row = seed.provider_from_options(svc, _args(seed, "--kind", "codex"))
        assert not svc.has_permission_skip("codex", row["cli_command"])

    def test_the_opt_in_reaches_a_hand_written_command_too(self, seed):
        row = seed.provider_from_options(svc, _args(
            seed, "--kind", "codex", "--command", "codex exec --json -",
            "--skip-permissions",
        ))
        assert row["cli_command"] == "codex --ask-for-approval never exec --json -"

    def test_the_opt_in_is_a_no_op_for_a_kind_with_no_known_flag(self, seed):
        row = seed.provider_from_options(
            svc, _args(seed, "--kind", "copilot", "--skip-permissions"))
        assert row["cli_command"] == seed.default_cli_command(svc, "copilot")

    def test_the_interactive_prompt_defaults_to_keeping_the_confirmation(self, seed,
                                                                        monkeypatch):
        monkeypatch.setattr(seed.shutil, "which",
                            lambda name: "/usr/bin/claude" if name == "claude" else None)
        answers = iter(["1", "", ""])  # provider, command (accept), skip? (just Enter)
        monkeypatch.setattr(seed, "_ask",
                            lambda prompt, default="": next(answers) or default)
        row = seed.prompt_for_provider(svc)
        assert not svc.has_permission_skip("claude", row["cli_command"])

    def test_the_interactive_prompt_honours_a_yes(self, seed, monkeypatch):
        monkeypatch.setattr(seed.shutil, "which",
                            lambda name: "/usr/bin/claude" if name == "claude" else None)
        answers = iter(["1", "", "y"])
        monkeypatch.setattr(seed, "_ask",
                            lambda prompt, default="": next(answers) or default)
        row = seed.prompt_for_provider(svc)
        assert svc.has_permission_skip("claude", row["cli_command"])

    def test_the_install_log_says_which_way_it_was_registered(self, seed, capsys):
        seed.announce_permission_mode(svc, "claude", "claude --model m -p -")
        asks = capsys.readouterr().out
        seed.announce_permission_mode(svc, "claude", f"claude {CLAUDE_SKIP} -p -")
        skips = capsys.readouterr().out
        assert "ON" in asks and "--skip-permissions" in asks
        assert "OFF" in skips
        # Nothing to say for a kind with no known flag.
        seed.announce_permission_mode(svc, "copilot", "copilot --output-format=json")
        assert capsys.readouterr().out == ""
