#!/usr/bin/env python3
"""Register FlowGate's first AI provider (flowgate.default.0292 T0003).

Until now a finished install started with an EMPTY `ai_providers` table, so nothing
AI-driven worked until an operator found the AI settings screen and registered a
provider by hand. Worse, the omission only surfaced much later, at run time, as
`end_reason == "all_providers_failed"` — which reads as "the provider is broken",
not "nothing was ever registered".

setup.sh / setup.ps1 now ask one y/n question right after the admin account and hand
the whole job here (0292 CH0002: "UX is merged, the logic stays separate" — the same
split create_dev_user.py already uses). Everything about *which* provider and *what*
command lives in this one script, so the two shells never carry a second copy of the
prompts or of the command catalog.

Operators normally reach this through ../setup-ai.sh (Windows: ..\\setup-ai.ps1), the
standalone installer step that only locates the venv interpreter and forwards its
arguments here. Calling this file directly is equivalent.

Usage:
    python seed_ai_provider.py                       # interactive
    python seed_ai_provider.py --list
    python seed_ai_provider.py --kind claude         # take the catalog command as-is
    python seed_ai_provider.py --kind codex --command "codex ... -"
    python seed_ai_provider.py --kind claude --skip-permissions   # opt in, see below
    python seed_ai_provider.py --exec-type api --kind openai --api-model gpt-5.6-sol \
                               --api-key sk-...
    python seed_ai_provider.py --no-probe

Environment fallbacks (for unattended installs; CLI flags win):
    FLOWGATE_AI_EXEC_TYPE    cli | api                      (default cli)
    FLOWGATE_AI_KIND         claude | copilot | codex | custom | openai
    FLOWGATE_AI_NAME         display name                   (default derived from kind)
    FLOWGATE_AI_CLI_COMMAND  cli command   (default: the catalog example for this host)
    FLOWGATE_AI_API_MODEL    api model id
    FLOWGATE_AI_API_BASE_URL api base url  (default: the per-kind default)
    FLOWGATE_AI_API_KEY      api key       (stored in ai_providers.api_key only)
    FLOWGATE_AI_PROBE        0 to skip the post-insert connection probe
    FLOWGATE_AI_SKIP_PERMISSIONS  1 to register the command with permission confirmation
                             switched off (same as --skip-permissions; default: off)

Permission confirmation is ON by default (0371 NR0007 §5). The catalog commands used to
carry each CLI's permission-skip flag, so every seeded provider silently ran without one —
a choice no operator ever made. It is still available, it just has to be asked for, here or
in the AI settings screen. Be aware of the trade in both directions: with it off, an
unattended run can stop at a prompt nobody can answer; with it on, the CLI reads, writes and
executes on this host without asking. The flags themselves live in ai_settings_service, so
this script never names one.

Re-runnable on its own: run it during install, or any time afterwards to add another
provider. A provider that is already registered is skipped, exactly as
create_dev_user.py skips an existing account.

The command strings are NOT duplicated here — they are read from
`ai_settings_service._CLI_COMMAND_EXAMPLES`, the same catalog the settings screen
renders, so a fix in one place reaches both.
"""

import argparse
import getpass
import io
import os
import shutil
import sys

# Force UTF-8 so Korean/emoji output does not break in Windows cp932/cp949 consoles
# (identical guard to create_dev_user.py — this script is called from the same installers).
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# config.py resolves the DB and storage roots relative to the process cwd, so run from
# server/ regardless of where the installer invoked us.
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

# ──────────────────────────────────────────────
# Load .env (same two-step as create_dev_user.py: python-dotenv when present,
# a minimal parser otherwise, so a venv without the extra still works)
# ──────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    _env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(_env_path):
        with open(_env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

PROBE_PROMPT = "Reply with the single word OK."

# Friendly labels for the numbered menu. Keys not listed fall back to the kind itself,
# so adding a kind to KINDS_CLI never leaves a blank row here.
_KIND_LABELS = {
    "claude": "Claude Code",
    "codex":  "Codex",
    "copilot": "GitHub Copilot",
    "openai": "OpenAI",
    "custom": "Custom",
}


def _import_services():
    """Import the service layer late so --help and --version never touch the DB.

    Importing `ai_settings_service` pulls in db.connection, which pulls in config.py,
    which builds the DB instance and runs the migrator. That is the right behaviour when
    we are about to write a row and the wrong cost for a help screen.
    """
    from modules.flow_gate.settings import ai_settings_service as svc
    from modules.flow_gate.services import ai_provider_probe_service as probe
    return svc, probe


# ──────────────────────────────────────────────
# Discovery
# ──────────────────────────────────────────────
def discover_cli_kinds(svc) -> list[tuple[str, str]]:
    """(kind, resolved path) for every catalog CLI kind actually present on PATH.

    `custom` is excluded on purpose: it is the escape hatch for an arbitrary command,
    so there is no binary to look for.
    """
    found = []
    for kind in svc.KINDS_CLI:
        if kind == "custom":
            continue
        path = shutil.which(kind)
        if path:
            found.append((kind, path))
    return found


def default_cli_command(svc, kind: str) -> str:
    """The catalog example for this kind on THIS host OS ("nt" / "posix")."""
    from modules.flow_gate.services import test_command_service as tcs
    return svc._CLI_COMMAND_EXAMPLES.get(kind, {}).get(tcs.current_os(), "")


def skip_permissions_requested(args) -> bool:
    """Did someone explicitly ask to switch permission confirmation off?

    Explicit means explicit: a flag or an env var set for this install. There is no
    "smart" fallback that turns it on because a host looks unattended — that is exactly
    how it ended up on for everyone (0371 NR0007 §5).
    """
    if getattr(args, "skip_permissions", False):
        return True
    value = os.environ.get("FLOWGATE_AI_SKIP_PERMISSIONS", "").strip().lower()
    return value in ("1", "true", "yes", "y", "on")


def announce_permission_mode(svc, kind: str, command: str) -> None:
    """Say, in the install log, which way this provider was registered."""
    if svc.permission_skip_rule(kind) is None:
        return
    if svc.has_permission_skip(kind, command):
        print("   [!] Permission confirmation is OFF for this command: it will read, write")
        print("       and run things on this host without asking.")
    else:
        print("   Permission confirmation is ON (the default). If a run stops waiting for")
        print("       an approval nobody can give, re-run with --skip-permissions.")


def default_name(svc, exec_type: str, kind: str, taken: set[str]) -> str:
    """A unique display name — names are unique per scope (L0004 §2.1 duplicate_name)."""
    base = f"{_KIND_LABELS.get(kind, kind)} ({exec_type.upper()})"
    name, n = base, 2
    while name.lower() in taken:
        name, n = f"{base} {n}", n + 1
    return name[:svc.NAME_MAX]


def find_duplicate(existing: list[dict], row: dict) -> "dict | None":
    """An already-registered provider that would make this one a no-op.

    Same exec_type + kind, and the same command (CLI) or the same model (API). Name is
    deliberately not part of the match: renaming a provider must not make the installer
    seed a second copy of it.
    """
    for p in existing:
        if p.get("exec_type") != row["exec_type"] or p.get("kind") != row["kind"]:
            continue
        if row["exec_type"] == "cli":
            if (p.get("cli_command") or "").strip() == row["cli_command"].strip():
                return p
        else:
            if (p.get("api_model") or "").strip() == (row.get("api_model") or "").strip():
                return p
    return None


# ──────────────────────────────────────────────
# Interactive prompts
# ──────────────────────────────────────────────
def _ask(prompt: str, default: str = "") -> str:
    try:
        answer = input(prompt).strip()
    except EOFError:
        return default
    return answer or default


def prompt_for_provider(svc) -> "dict | None":
    """Walk the operator through one provider. None = they chose to stop."""
    found = discover_cli_kinds(svc)
    if found:
        print("\nSupported AI CLIs found on PATH:")
        for i, (kind, path) in enumerate(found, start=1):
            print(f"  [{i}] {_KIND_LABELS.get(kind, kind):<15} {path}")
        print("  [a] an API provider instead (key required)")
        print("  [s] skip — register nothing")
        choice = _ask(f"\nWhich provider? [1]: ", "1").lower()
    else:
        # Not a failure: an API-only host is a perfectly normal install.
        print("\nNo supported AI CLI found on PATH "
              f"({', '.join(k for k in svc.KINDS_CLI if k != 'custom')}).")
        print("  [a] register an API provider (key required)")
        print("  [s] skip — register nothing")
        choice = _ask("\nWhich provider? [s]: ", "s").lower()

    if choice in ("s", "skip", "n", "no", ""):
        return None
    if choice in ("a", "api"):
        return _prompt_api(svc)

    if not choice.isdigit() or not (1 <= int(choice) <= len(found)):
        print(f"[!] '{choice}' is not one of the choices — nothing registered.")
        return None

    kind = found[int(choice) - 1][0]
    suggested = default_cli_command(svc, kind)
    print(f"\nCommand for {_KIND_LABELS.get(kind, kind)} (Enter accepts the suggestion):")
    print(f"  {suggested}")
    command = _ask("Command: ", suggested)
    if not command:
        print("[!] A CLI provider needs a command — nothing registered.")
        return None
    command = _prompt_permission_skip(svc, kind, command)
    return {"exec_type": "cli", "kind": kind, "cli_command": command, "enabled": True}


def _prompt_permission_skip(svc, kind: str, command: str) -> str:
    """Offer the permission-skip opt-in for a kind that has one. Default is no.

    Asked here rather than assumed, because both answers cost something and only the
    operator knows which cost this host can pay (0371 NR0007 §5).
    """
    if svc.permission_skip_rule(kind) is None or svc.has_permission_skip(kind, command):
        return command
    print("\n  This command asks before it reads, writes or runs anything. FlowGate runs it")
    print("  unattended, so a run can stop at a prompt with nobody there to answer it.")
    print("  Answering yes lets this CLI act on this host without asking.")
    if _ask("  Skip permission confirmation? [y/N]: ", "n").lower() not in ("y", "yes"):
        return command
    command = svc.set_permission_skip(kind, command, True)
    print(f"  Permission confirmation OFF: {command}")
    return command


def _prompt_api(svc) -> "dict | None":
    kinds = list(svc.KINDS_API)
    print("\nAPI provider kind:")
    for i, kind in enumerate(kinds, start=1):
        print(f"  [{i}] {_KIND_LABELS.get(kind, kind)}")
    choice = _ask("Kind [1]: ", "1")
    if not choice.isdigit() or not (1 <= int(choice) <= len(kinds)):
        print(f"[!] '{choice}' is not one of the choices — nothing registered.")
        return None
    kind = kinds[int(choice) - 1]

    model = _ask("Model id (e.g. claude-opus-4-8): ")
    if not model:
        print("[!] An API provider needs a model id — nothing registered.")
        return None
    base_url = _ask(f"Base URL [{svc._DEFAULT_BASE_URLS.get(kind, '(required)')}]: ")
    # getpass so the key never lands in the terminal scrollback or the shell history.
    try:
        api_key = getpass.getpass("API key (hidden, Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        api_key = ""
    return {
        "exec_type": "api", "kind": kind, "api_model": model,
        "api_base_url": base_url or None, "api_key": api_key or None, "enabled": True,
    }


# ──────────────────────────────────────────────
# Non-interactive assembly
# ──────────────────────────────────────────────
def provider_from_options(svc, args) -> "dict | None":
    """Build the row from --flags / FLOWGATE_AI_*. None = nothing was specified."""
    exec_type = (args.exec_type or os.environ.get("FLOWGATE_AI_EXEC_TYPE") or "").strip().lower()
    kind = (args.kind or os.environ.get("FLOWGATE_AI_KIND") or "").strip().lower()
    command = args.command or os.environ.get("FLOWGATE_AI_CLI_COMMAND") or ""
    model = args.api_model or os.environ.get("FLOWGATE_AI_API_MODEL") or ""
    base_url = args.api_base_url or os.environ.get("FLOWGATE_AI_API_BASE_URL") or ""
    api_key = args.api_key or os.environ.get("FLOWGATE_AI_API_KEY") or ""

    if not (exec_type or kind or command or model):
        return None
    exec_type = exec_type or "cli"
    if exec_type not in svc.EXEC_TYPES:
        print(f"[!] Unknown exec type '{exec_type}' (expected: {', '.join(svc.EXEC_TYPES)}).")
        return None

    if exec_type == "cli":
        if not kind:
            print("[!] --kind is required for a CLI provider "
                  f"({', '.join(svc.KINDS_CLI)}).")
            return None
        # Falling back to the catalog is the point of --kind on its own: the installer
        # can seed "the documented claude command for this host" without repeating it.
        command = command or default_cli_command(svc, kind)
        if not command.strip():
            print(f"[!] No catalog command for kind '{kind}' — pass --command explicitly.")
            return None
        # Applied to --command too, not just the catalog default: the flag means "this
        # install wants unattended tool use", which is just as true for a hand-written
        # command. A command that already carries the skip is returned unchanged.
        if skip_permissions_requested(args):
            command = svc.set_permission_skip(kind, command, True)
        return {"exec_type": "cli", "kind": kind, "cli_command": command, "enabled": True}

    if not kind:
        print(f"[!] --kind is required for an API provider ({', '.join(svc.KINDS_API)}).")
        return None
    if not model.strip():
        print("[!] --api-model is required for an API provider.")
        return None
    return {
        "exec_type": "api", "kind": kind, "api_model": model,
        "api_base_url": base_url or None, "api_key": api_key or None, "enabled": True,
    }


# ──────────────────────────────────────────────
# Persist + probe
# ──────────────────────────────────────────────
def register(svc, row: dict) -> "dict | None":
    """Append `row` to the GLOBAL provider chain. Returns the saved view, or None if skipped.

    The whole list is re-submitted because save_system_settings() is a full replace
    (L0004 §2.2). Existing rows go back verbatim; their api_key is preserved because an
    absent `api_key` key means "keep" in the merge rules (L0004 §2.3), and the views we
    read back never carry the raw secret.
    """
    current = svc.get_system_settings(include_catalog=False)
    existing = current["providers"]

    dup = find_duplicate(existing, row)
    if dup is not None:
        print(f"  [skip] already registered: {dup['name']} ({dup['exec_type']}/{dup['kind']})")
        return None

    row = dict(row)
    row["name"] = row.get("name") or default_name(
        svc, row["exec_type"], row["kind"], {p["name"].lower() for p in existing},
    )

    try:
        saved = svc.save_system_settings(
            providers=existing + [row],
            default_provider_id=current.get("default_provider_id"),
            default_provider_index=None,
            updated_by="seed_ai_provider",
        )
    except svc.AiSettingsValidationError as exc:
        print("[!] The provider was rejected by validation:")
        for err in exc.errors:
            print(f"    - {err}")
        return None

    # save_system_settings returns the whole chain; ours is the one we appended.
    return saved["providers"][-1]


def run_probe(probe, row: dict) -> None:
    """Fire the same launch probe the settings screen's [Test connection] button uses.

    This is what turns "the run failed somewhere deep" into "the install log says so":
    a command that cannot start on THIS host (a Windows-hostile flag, a binary that is
    not really on PATH for the service user) reports here, while the operator is
    still sitting in front of the installer.
    """
    print("\n  Testing the provider...")
    result = probe.probe_provider({**row, "prompt": PROBE_PROMPT})
    status = result.get("status")
    icon = {"ok": "✅", "launched": "✅", "skipped": "➖"}.get(status, "❌")
    print(f"  {icon} [{status}] {result.get('message', '')}")
    if result.get("stderr_tail"):
        print("  --- stderr ---")
        for line in result["stderr_tail"].strip().splitlines()[-10:]:
            print(f"    {line}")
    if status == "command_failed":
        print("  The provider IS registered — only the command failed to start here.")
        print("  Fix it in AI settings (or re-run this script) once you know the right form.")


def print_providers(svc) -> None:
    settings = svc.get_system_settings(include_catalog=False)
    providers = settings["providers"]
    if not providers:
        print("No AI provider is registered.")
        return
    default_id = settings.get("default_provider_id")
    print(f"\n{'Name':<28} {'Type':<5} {'Kind':<9} {'On':<4} Command / model")
    print("-" * 100)
    for p in providers:
        target = p.get("cli_command") if p["exec_type"] == "cli" else p.get("api_model")
        mark = "*" if p["id"] == default_id else " "
        print(f"{mark}{p['name']:<27} {p['exec_type']:<5} {p['kind']:<9} "
              f"{'yes' if p['enabled'] else 'no':<4} {(target or '')[:50]}")
    print("\n(* = default; chain order = fallback order)")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Register FlowGate's first AI provider",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--list", action="store_true", help="Show the registered providers and exit")
    p.add_argument("--exec-type", choices=("cli", "api"), help="cli (default) or api")
    p.add_argument("--kind", help="claude / copilot / codex / custom (cli), claude / openai / custom (api)")
    p.add_argument("--name", help="display name (default: derived from the kind)")
    p.add_argument("--command", help="cli command (default: the catalog example for this host)")
    p.add_argument("--api-model", help="model id (api providers)")
    p.add_argument("--api-base-url", help="base url (api providers; default: per-kind)")
    p.add_argument("--api-key", help="api key — prefer FLOWGATE_AI_API_KEY so it stays out of the shell history")
    p.add_argument("--skip-permissions", action="store_true",
                   help="register the command with permission confirmation OFF "
                        "(default: on — the CLI asks before it reads or writes)")
    p.add_argument("--no-probe", action="store_true", help="Skip the post-insert connection test")
    return p


def main() -> None:
    args = build_parser().parse_args()
    svc, probe = _import_services()

    if args.list:
        print_providers(svc)
        return

    row = provider_from_options(svc, args)
    if row is None:
        # Nothing preset. Ask only if someone is there to answer: an unattended install
        # (CI, piped shell) must never block on a read — the same rule setup.sh applies
        # to the admin account.
        if not sys.stdin.isatty():
            print("No AI provider settings given and no terminal to ask on — nothing registered.")
            print("  Re-run interactively, or preset FLOWGATE_AI_KIND (see --help).")
            return
        print("\n🤖 AI provider setup")
        row = prompt_for_provider(svc)
    if row is None:
        print("\nNo provider registered. Run this step again whenever you want one:")
        print("  ./setup-ai.sh          (Windows: .\\setup-ai.ps1)")
        return

    if args.name or os.environ.get("FLOWGATE_AI_NAME"):
        row["name"] = args.name or os.environ["FLOWGATE_AI_NAME"]

    saved = register(svc, row)
    if saved is None:
        return

    print(f"\n✅ Registered: {saved['name']} ({saved['exec_type']}/{saved['kind']})")
    if saved["exec_type"] == "cli":
        print(f"   command: {saved['cli_command']}")
        announce_permission_mode(svc, saved.get("kind"), saved.get("cli_command") or "")
    else:
        print(f"   model  : {saved['api_model']}  @ {saved.get('api_base_url')}")
        # The key lives in ai_providers.api_key and is never echoed back (L0004 §2.3).
        print(f"   api key: {'set' if saved['api_key_set'] else 'NOT set'}")

    probe_off = args.no_probe or os.environ.get("FLOWGATE_AI_PROBE", "").strip() == "0"
    if not probe_off:
        run_probe(probe, row)


if __name__ == "__main__":
    main()
