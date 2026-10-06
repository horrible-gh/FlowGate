"""Command / cwd / worktree / category checks for chat command requests (D0006 §2.2, §3.7).

The chat command path must not become an unrestricted shell, yet it has to allow what
the self-check policy deliberately forbids (``git`` and ordinary developer tools). So
this module reuses the self-check building blocks -- structured argv (no command
string is ever parsed), shell-operator and inline-execution refusal, worktree-contained
cwd, controlled PATH, the npm/pnpm/yarn launcher adapter and the scrubbed environment
-- and swaps only the deny list.

Nothing here decides *whether* a command runs; that is the user's command policy
applied by ``commands``. This module only answers "is this a well-formed, non-shell
command bound to this worktree" and "what kind of command is it" (the category is
stored per request so per-category policies can be added later).

Every refusal is a :class:`CommandPolicyError` whose code becomes the 422 code of the
request (D0006 §5.3).
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from flowgate.conversation import legacy_adapter

POLICY_VERSION = "chat-command-v1"
TIMEOUT_DEFAULT_SEC = 300
TIMEOUT_MAX_SEC = 1800
ARGS_MAX = 256

# Shells, privilege escalation, host/service control, remote shells and network
# transfer tools, LOLBins and container/VM control. git, file utilities and
# build/test runners stay available: the user's approval policy governs those.
DENIED = frozenset({
    "cmd", "command", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "wsl",
    "sudo", "su", "runas", "doas", "pkexec",
    "ssh", "scp", "sftp", "nc", "ncat", "netcat", "telnet", "ftp", "curl", "wget",
    "gh", "hub",
    "docker", "podman", "kubectl", "helm", "vagrant", "virsh", "qemu", "vboxmanage",
    "kill", "killall", "pkill", "taskkill", "sc", "systemctl", "service", "shutdown", "reboot", "poweroff",
    "apt", "apt-get", "dnf", "yum", "pacman", "brew", "choco", "winget", "scoop",
    "reg", "regedit", "schtasks", "wmic", "mshta", "rundll32", "regsvr32", "certutil", "bitsadmin",
    "cscript", "wscript", "msiexec", "installutil", "start", "open", "xargs", "env", "eval", "exec",
})

# `git -c alias.x=!cmd`, a custom ssh/pager/editor command or a hooks path would turn
# one approved git invocation into an arbitrary command the approval card never showed.
_GIT_CONFIG_EXEC_KEYS = ("alias.", "core.sshcommand", "core.pager", "core.editor", "core.hookspath",
                         "core.fsmonitor", "sequence.editor", "diff.external", "credential.helper",
                         "gpg.program", "uploadpack.packobjectshook")

_GIT_READ = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "blame",
                       "describe", "shortlog", "grep", "ls-tree", "cat-file", "reflog", "whatchanged",
                       "version", "help"})
_GIT_HIGH_IMPACT = frozenset({"push", "reset", "clean", "rebase", "filter-branch", "gc", "prune"})
_FILE_MUTATION = frozenset({"rm", "rmdir", "del", "erase", "mv", "move", "cp", "copy", "mkdir",
                            "touch", "chmod", "chown", "chgrp", "attrib", "icacls", "ln"})
_TEST_BUILD = frozenset({"pytest", "py.test", "python", "python3", "py", "node", "npm", "pnpm", "yarn",
                         "npx", "vitest", "jest", "tsc", "vue-tsc", "eslint", "prettier", "ruff", "mypy",
                         "flake8", "black", "pylint", "tox", "nox", "cargo", "go", "mvn", "gradle",
                         "dotnet", "make", "cmake", "ctest", "deno", "bun", "uv", "poetry", "pip", "pip3"})
_READ = frozenset({"ls", "dir", "cat", "type", "head", "tail", "wc", "find", "findstr", "grep", "rg",
                   "tree", "pwd", "which", "where", "echo", "stat", "file", "du", "df"})

CATEGORIES = ("read", "test_build", "file_mutation", "git_read", "git_write", "other")
REQUEST_FIELDS = frozenset({"program", "args", "cwd", "timeout_seconds"})


class CommandPolicyError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class ChatCommand:
    program: str
    args: tuple[str, ...]
    cwd: str
    timeout_seconds: int
    category: str
    high_impact: bool


def _base():
    return legacy_adapter.selfcheck_policy()


def _code(selfcheck_code: str) -> str:
    """Self-check error codes, re-spoken in this contract's vocabulary."""
    return "chat_command_" + str(selfcheck_code).removeprefix("selfcheck_")


def _git_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    """First non-option argument after git's own global options, plus the rest."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-c", "-C", "--git-dir", "--work-tree", "--namespace"):
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return arg.lower(), args[index + 1:]
    return None, []


def _validate_git(args: list[str]) -> None:
    index = 0
    while index < len(args):
        arg = args[index]
        value = None
        if arg == "-c" and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith("-c") and len(arg) > 2:
            value = arg[2:]
        elif arg.startswith("--config-env"):
            raise CommandPolicyError("chat_command_inline_execution", "git --config-env is not allowed")
        if arg in ("-C", "--git-dir", "--work-tree", "--exec-path") or arg.startswith(
            ("--git-dir=", "--work-tree=", "--exec-path=")
        ):
            # The command is bound to this conversation's worktree; pointing git at
            # another repository would bypass that binding.
            raise CommandPolicyError("chat_command_invalid_cwd", f"git {arg} is not allowed; use cwd instead")
        if value is not None:
            key = value.split("=", 1)[0].strip().lower()
            if any(key.startswith(prefix) for prefix in _GIT_CONFIG_EXEC_KEYS):
                raise CommandPolicyError("chat_command_inline_execution", f"git -c {key} is not allowed")
        index += 1


def classify(program: str, args: list[str]) -> tuple[str, bool]:
    """(category, high_impact) -- stored per request, shown on the approval card."""
    try:
        name = _base().canonical_name(program)
    except Exception:
        return "other", False
    low = [a.lower() for a in args]
    if name == "git":
        sub, rest = _git_subcommand(args)
        if sub in _GIT_READ or sub is None:
            return "git_read", False
        if sub == "branch" and not any(a in ("-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force") for a in rest):
            return "git_read", False
        if sub == "stash" and rest[:1] in (["list"], ["show"]):
            return "git_read", False
        high = sub in _GIT_HIGH_IMPACT or (sub == "checkout" and any(a in ("-f", "--force", ".") for a in rest)) \
            or (sub == "branch" and any(a in ("-D", "--force") for a in rest))
        return "git_write", bool(high)
    if name in _FILE_MUTATION:
        return "file_mutation", name in ("rm", "rmdir", "del", "erase")
    if name in _TEST_BUILD:
        return "test_build", any(a in ("publish", "deploy") for a in low)
    if name in _READ:
        return "read", False
    return "other", False


def validate_request(payload: object) -> ChatCommand:
    """Shape and argv checks that need no filesystem. Unknown fields are refused."""
    base = _base()
    if not isinstance(payload, dict):
        raise CommandPolicyError("chat_command_invalid_request", "the request must be an object")
    unknown = set(payload) - REQUEST_FIELDS
    if unknown:
        raise CommandPolicyError("chat_command_invalid_request",
                                 f"unknown field(s): {', '.join(sorted(map(str, unknown)))}")
    program = payload.get("program")
    args = payload.get("args", [])
    cwd = payload.get("cwd", ".")
    timeout = payload.get("timeout_seconds", TIMEOUT_DEFAULT_SEC)
    if args is None:
        args = []
    if cwd in (None, ""):
        cwd = "."
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= TIMEOUT_MAX_SEC:
        raise CommandPolicyError("chat_command_invalid_timeout",
                                 f"timeout_seconds must be an integer between 1 and {TIMEOUT_MAX_SEC}")
    try:
        base.validate_program(program)
        base.validate_args(program, args)
    except base.PolicyError as exc:
        raise CommandPolicyError(_code(exc.code)) from exc
    if not isinstance(cwd, str) or "\0" in cwd:
        raise CommandPolicyError("chat_command_invalid_cwd")
    name = base.canonical_name(program)
    if name in DENIED:
        raise CommandPolicyError("chat_command_program_denied",
                                 f"'{program}' is not allowed for chat command execution")
    if name == "git":
        _validate_git(list(args))
    category, high_impact = classify(program, list(args))
    return ChatCommand(program, tuple(args), cwd.replace("\\", "/"), timeout, category, high_impact)


def resolve(command: ChatCommand, worktree: Path):
    """Filesystem half: (resolved executable, absolute cwd, controlled PATH)."""
    base = _base()
    try:
        cwd = base.validate_cwd(worktree, command.cwd)
        path_value, _ = base.controlled_path(worktree)
        resolved = base.resolve_command(command.program, list(command.args), worktree, path_value,
                                        denied=DENIED, deny_git_helpers=False)
    except base.PolicyError as exc:
        raise CommandPolicyError(_code(exc.code)) from exc
    return resolved, cwd, path_value


def environment(path_value: str, runtime: Path) -> dict[str, str]:
    """The self-check scrubbed environment plus prompt-free git."""
    env = _base().scrubbed_env(path_value, runtime)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def display(program: str, args: list[str]) -> str:
    """The full command on one line, exactly as executed (never abbreviated)."""
    return " ".join(shlex.quote(str(part)) for part in [program, *args])
