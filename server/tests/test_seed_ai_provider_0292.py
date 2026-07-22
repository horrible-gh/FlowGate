"""seed_ai_provider.py — the install-time AI provider seed (flowgate.default.0292 T0003).

Covers the parts that decide WHAT gets written: option/env resolution, the catalog
lookup (the script must never carry its own copy of the command strings), the duplicate
guard that makes a re-run a no-op, and the non-interactive escape hatch. The DB write and
the probe are the service layer's own code, already covered by test_ai_settings_api.py and
the 0281 probe suite.
"""
import os
import sys

import pytest

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)


@pytest.fixture
def seed():
    """Import the script. It chdirs to server/ on import (config resolves paths from the
    cwd), so the previous directory is restored for whatever runs next."""
    cwd = os.getcwd()
    try:
        import seed_ai_provider
        yield seed_ai_provider
    finally:
        os.chdir(cwd)


@pytest.fixture
def svc():
    from modules.flow_gate.settings import ai_settings_service
    return ai_settings_service


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """FLOWGATE_AI_* leaking in from the developer's shell would silently steer these."""
    for key in list(os.environ):
        if key.startswith("FLOWGATE_AI_"):
            monkeypatch.delenv(key, raising=False)


def _args(seed, *argv):
    return seed.build_parser().parse_args(list(argv))


# ── Catalog is the single source of the command strings ──────────────────────

def test_command_default_comes_from_the_settings_catalog(seed, svc):
    """The whole point of the split (CH0002): the seed and the settings screen read the
    same dict, so fixing a command in one place fixes it in both."""
    from modules.flow_gate.services import test_command_service as tcs

    for kind in ("claude", "codex", "copilot"):
        assert seed.default_cli_command(svc, kind) == \
            svc._CLI_COMMAND_EXAMPLES[kind][tcs.current_os()]


def test_kind_alone_is_enough_for_a_cli_provider(seed, svc):
    """`--kind claude` = "the documented claude command for this host" — that is what
    lets the installers hand over without repeating any command string."""
    row = seed.provider_from_options(svc, _args(seed, "--kind", "claude"))
    assert row["exec_type"] == "cli"
    assert row["cli_command"] == seed.default_cli_command(svc, "claude")
    assert row["enabled"] is True


def test_explicit_command_wins_over_the_catalog(seed, svc):
    row = seed.provider_from_options(svc, _args(
        seed, "--kind", "codex", "--command", "codex exec -",
    ))
    assert row["cli_command"] == "codex exec -"


def test_custom_kind_needs_an_explicit_command(seed, svc):
    """`custom` is the arbitrary-command escape hatch, so the catalog has nothing to
    offer it — that must be reported, not written as an empty command."""
    assert seed.provider_from_options(svc, _args(seed, "--kind", "custom")) is None


# ── Env fallback (unattended installs) ───────────────────────────────────────

def test_env_supplies_the_provider_when_no_flags_are_given(seed, svc, monkeypatch):
    monkeypatch.setenv("FLOWGATE_AI_KIND", "codex")
    row = seed.provider_from_options(svc, _args(seed))
    assert (row["exec_type"], row["kind"]) == ("cli", "codex")


def test_flags_win_over_env(seed, svc, monkeypatch):
    monkeypatch.setenv("FLOWGATE_AI_KIND", "codex")
    row = seed.provider_from_options(svc, _args(seed, "--kind", "claude"))
    assert row["kind"] == "claude"


def test_nothing_specified_reads_as_nothing(seed, svc):
    """None is the signal main() uses to fall through to the prompts (or, with no TTY,
    to exit without touching the DB). It must not be confused with a bad value."""
    assert seed.provider_from_options(svc, _args(seed)) is None


def test_api_provider_needs_a_model(seed, svc):
    assert seed.provider_from_options(
        svc, _args(seed, "--exec-type", "api", "--kind", "openai")) is None
    row = seed.provider_from_options(svc, _args(
        seed, "--exec-type", "api", "--kind", "openai", "--api-model", "gpt-5.6-sol",
        "--api-key", "sk-test",
    ))
    assert (row["exec_type"], row["api_model"], row["api_key"]) == \
        ("api", "gpt-5.6-sol", "sk-test")


def test_unknown_exec_type_is_refused(seed, svc):
    args = _args(seed, "--kind", "claude")
    args.exec_type = "smoke-signal"
    assert seed.provider_from_options(svc, args) is None


# ── Re-run safety ────────────────────────────────────────────────────────────

def test_duplicate_matches_on_the_command_not_the_name(seed):
    """Renaming a provider must not make the installer seed a second copy of it."""
    existing = [{"name": "renamed by the operator", "exec_type": "cli",
                 "kind": "claude", "cli_command": "claude -p"}]
    row = {"exec_type": "cli", "kind": "claude", "cli_command": "claude -p"}
    assert seed.find_duplicate(existing, row) is existing[0]

    other = {"exec_type": "cli", "kind": "claude", "cli_command": "claude --model x -p -"}
    assert seed.find_duplicate(existing, other) is None


def test_duplicate_ignores_a_same_command_row_of_another_kind(seed):
    existing = [{"name": "c", "exec_type": "cli", "kind": "custom", "cli_command": "claude -p"}]
    row = {"exec_type": "cli", "kind": "claude", "cli_command": "claude -p"}
    assert seed.find_duplicate(existing, row) is None


def test_api_duplicates_match_on_the_model(seed):
    existing = [{"name": "o", "exec_type": "api", "kind": "openai", "api_model": "gpt-5.6-sol"}]
    assert seed.find_duplicate(existing, {"exec_type": "api", "kind": "openai",
                                          "api_model": "gpt-5.6-sol"}) is not None
    assert seed.find_duplicate(existing, {"exec_type": "api", "kind": "openai",
                                          "api_model": "gpt-4"}) is None


def test_default_name_avoids_a_collision(seed, svc):
    """Names are unique per scope (L0004 duplicate_name), so a second provider of the
    same kind must not be handed a name the save would reject."""
    first = seed.default_name(svc, "cli", "claude", set())
    second = seed.default_name(svc, "cli", "claude", {first.lower()})
    assert second != first
    assert len(second) <= svc.NAME_MAX


# ── Discovery ────────────────────────────────────────────────────────────────

def test_discovery_never_offers_custom(seed, svc):
    """`custom` has no binary to look for — offering it in the PATH menu would promise
    a command the catalog cannot supply."""
    assert all(kind != "custom" for kind, _ in seed.discover_cli_kinds(svc))


def test_discovery_reports_what_is_on_path(seed, svc, monkeypatch):
    monkeypatch.setattr(seed.shutil, "which",
                        lambda name: r"C:\bin\codex.exe" if name == "codex" else None)
    assert seed.discover_cli_kinds(svc) == [("codex", r"C:\bin\codex.exe")]


# ── The standalone entry points (setup-ai.sh / setup-ai.ps1) ─────────────────
#
# The installers only ask y/n; these two files are how an operator who declined —
# or who wants a second provider — reaches the same step later, without having to
# know where the venv interpreter lives. They must stay thin: locate python, forward
# every argument. Anything else duplicated here would drift away from the script.

_REPO_ROOT = os.path.dirname(_SERVER_DIR)


def _entry_point(name: str) -> str:
    path = os.path.join(_REPO_ROOT, name)
    assert os.path.isfile(path), f"{name} is missing — the standalone provider setup entry point"
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("name", ["setup-ai.sh", "setup-ai.ps1"])
def test_entry_point_delegates_to_the_seed_script(name):
    body = _entry_point(name)
    assert "seed_ai_provider.py" in body


@pytest.mark.parametrize("name,forward", [("setup-ai.sh", '"$@"'), ("setup-ai.ps1", "@SeedArgs")])
def test_entry_point_forwards_its_arguments(name, forward):
    """--list / --kind / --help have to reach the script: the option list must live in
    one place, so a new flag is never a change in two shells."""
    assert forward in _entry_point(name)


@pytest.mark.parametrize("name,venv", [("setup-ai.sh", ".venv/bin/python"),
                                       ("setup-ai.ps1", r".venv\Scripts\python.exe")])
def test_entry_point_prefers_the_venv_interpreter(name, venv):
    """The venv is the interpreter that actually has the server's dependencies; the
    ambient python is only a fallback for a container/manual install."""
    assert venv in _entry_point(name)


@pytest.mark.parametrize("name", ["setup-ai.sh", "setup-ai.ps1"])
def test_entry_point_carries_no_copy_of_the_provider_catalog(name):
    """The whole point of the split (CH0002): kinds and command strings exist once, in
    ai_settings_service. A wrapper that names them would be a second catalog to fix."""
    body = _entry_point(name)
    for leaked in ("--dangerously-skip-permissions", "--output-format=json", "KINDS_CLI"):
        assert leaked not in body


@pytest.mark.parametrize("installer,entry", [("setup.sh", "setup-ai.sh"),
                                             ("setup.ps1", "setup-ai.ps1")])
def test_installer_points_a_declined_seed_at_the_entry_point(installer, entry):
    """Answering "n" is a normal path, so the recovery hint has to name something an
    operator can actually type — not a venv-relative python invocation."""
    with open(os.path.join(_REPO_ROOT, installer), encoding="utf-8") as f:
        assert entry in f.read()
