"""Policy for advisory TR self-check commands.  No command string is parsed."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

POLICY_VERSION = "tr-selfcheck-v1"
OPERATORS = frozenset({"|", "||", "&&", ";", ">", ">>", "<", "&"})
DENIED = frozenset({
    "cmd", "command", "powershell", "pwsh", "bash", "sh", "zsh", "fish", "wsl",
    "git", "gh", "hub", "svn", "hg", "fossil",
    "curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "netcat", "telnet", "ftp",
    "docker", "podman", "kubectl", "helm", "vagrant", "virsh", "qemu", "vboxmanage",
    "rm", "rmdir", "del", "erase", "mv", "move", "chmod", "chown", "chgrp", "attrib", "icacls",
    "sudo", "su", "runas", "doas", "pkexec",
    "kill", "killall", "pkill", "taskkill", "sc", "systemctl", "service", "shutdown", "reboot", "poweroff",
    "apt", "apt-get", "dnf", "yum", "pacman", "brew", "choco", "winget", "scoop",
    "reg", "regedit", "schtasks", "wmic", "mshta", "rundll32", "regsvr32", "certutil", "bitsadmin",
    "cscript", "wscript", "msiexec", "installutil", "start", "open", "xargs", "env", "eval", "exec",
    "npx", "pnpx", "pipx", "uvx", "corepack", "bunx", "make", "just", "task",
})
INTERPRETERS = {
    "python": {"-c"}, "python3": {"-c"}, "py": {"-c"},
    "node": {"-e", "--eval", "-p", "--print"},
    "perl": {"-e"}, "ruby": {"-e"}, "php": {"-r"},
}
HOST_KEYS = ("SYSTEMROOT", "WINDIR", "PATHEXT", "LANG", "LC_ALL", "LC_CTYPE", "JAVA_HOME", "DOTNET_ROOT", "GOROOT")


class PolicyError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ResolvedCommand:
    executable: str
    argv_prefix: tuple[str, ...]
    name: str
    origin: str
    path: str


def validate_program(program: str) -> str:
    if not isinstance(program, str) or not program or program in (".", ".."):
        raise PolicyError("selfcheck_invalid_program")
    if any(ord(c) < 32 or ord(c) == 127 for c in program):
        raise PolicyError("selfcheck_invalid_program")
    if "/" in program or "\\" in program or ":" in program or Path(program).is_absolute():
        raise PolicyError("selfcheck_invalid_program")
    return program


def canonical_name(program: str) -> str:
    name = validate_program(program).lower()
    if os.name == "nt":
        for suffix in (".exe", ".com", ".cmd", ".bat", ".ps1"):
            if name.endswith(suffix):
                return name[:-len(suffix)]
    return name


def validate_args(program: str, args: list[str]) -> None:
    if not isinstance(args, list) or len(args) > 256 or any(
        not isinstance(a, str) or len(a) > 8192 or "\0" in a for a in args
    ):
        raise PolicyError("selfcheck_invalid_args")
    if any(a in OPERATORS for a in args):
        raise PolicyError("selfcheck_shell_operator")
    name = canonical_name(program)
    flags = INTERPRETERS.get(name, set())
    for arg in args:
        if arg == "--":
            break
        if arg in flags or (name == "deno" and arg == "eval"):
            raise PolicyError("selfcheck_inline_execution")
        # Python -cX and node --eval=X are still inline execution.
        if name in {"python", "python3", "py"} and re.match(r"^-[A-Za-z]*c(?:$|.+)", arg):
            raise PolicyError("selfcheck_inline_execution")
        if name == "node" and (arg.startswith(("--eval=", "--print=", "-e", "-p"))):
            raise PolicyError("selfcheck_inline_execution")
        if name in {"perl", "ruby"} and arg.startswith("-e"):
            raise PolicyError("selfcheck_inline_execution")
        if name == "php" and arg.startswith("-r"):
            raise PolicyError("selfcheck_inline_execution")
    low = [a.lower() for a in args]
    if name in {"npm", "pnpm", "yarn", "pip", "pip3", "cargo", "poetry", "mvn", "gradle", "dotnet", "go"}:
        if any(a in {"publish", "deploy", "login", "logout", "token", "auth", "whoami", "config"} for a in low):
            raise PolicyError("selfcheck_package_mutation")
        if any(a in {"-g", "--global", "--user", "--break-system-packages", "--prefix", "--root", "--target", "--isolated", "exec", "dlx"} for a in low):
            raise PolicyError("selfcheck_package_mutation")


def validate_cwd(worktree: Path, cwd: str) -> Path:
    if not isinstance(cwd, str) or not cwd or "\0" in cwd or Path(cwd).is_absolute() or re.match(r"^[A-Za-z]:", cwd):
        raise PolicyError("selfcheck_invalid_cwd")
    root = worktree.resolve(strict=True)
    try:
        target = (root / cwd).resolve(strict=True)
    except OSError as exc:
        raise PolicyError("selfcheck_invalid_cwd") from exc
    if target != root and root not in target.parents or not target.is_dir():
        raise PolicyError("selfcheck_invalid_cwd")
    return target


def controlled_path(worktree: Path, host_path: str | None = None) -> tuple[str, list[Path]]:
    root = worktree.resolve(strict=True)
    folders = [root / name / ("Scripts" if os.name == "nt" else "bin") for name in (".venv", "venv")]
    folders.append(root / "node_modules" / ".bin")
    safe: list[Path] = []
    for folder in folders:
        try:
            resolved = folder.resolve(strict=True)
            if resolved.is_dir() and (resolved == root or root in resolved.parents):
                safe.append(resolved)
        except OSError:
            pass
    for raw in (host_path if host_path is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if not raw or not Path(raw).is_absolute():
            continue
        try:
            resolved = Path(raw).resolve(strict=True)
            if resolved.is_dir() and resolved not in safe:
                safe.append(resolved)
        except OSError:
            pass
    return os.pathsep.join(map(str, safe)), safe


def resolve_command(program: str, args: list[str], worktree: Path, path_value: str,
                    denied: frozenset = DENIED, deny_git_helpers: bool = True) -> ResolvedCommand:
    # 0670 T0004: ``denied``/``deny_git_helpers`` let the chat command policy reuse this
    # exact resolution (argv checks, PATH control, launcher adapter) with its own deny
    # list. The defaults are the Self-check policy, unchanged.
    name = canonical_name(program)
    if name in denied or (deny_git_helpers and name.startswith("git-")):
        raise PolicyError("selfcheck_program_denied")
    validate_args(program, args)
    resolved = shutil.which(program, path=path_value)
    if not resolved:
        raise PolicyError("selfcheck_executable_not_found")
    real = Path(resolved).resolve(strict=True)
    real_name = canonical_name(real.name)
    if real_name in denied or (deny_git_helpers and real_name.startswith("git-")):
        raise PolicyError("selfcheck_program_denied")
    if not real.is_file():
        raise PolicyError("selfcheck_executable_not_found")
    suffix = real.suffix.lower()
    if suffix in {".cmd", ".bat", ".ps1"}:
        if os.name != "nt" or program.lower() not in {"npm", "pnpm", "yarn"} or suffix != ".cmd":
            raise PolicyError("selfcheck_launcher_unsupported")
        launcher_file = {"npm": "npm-cli.js", "pnpm": "pnpm.cjs", "yarn": "yarn.js"}[name]
        expected_root = (real.parent / "node_modules" / name).resolve()
        launcher = (expected_root / "bin" / launcher_file).resolve()
        if not launcher.is_file() or expected_root not in launcher.parents:
            raise PolicyError("selfcheck_launcher_unsupported")
        node_path = shutil.which("node", path=path_value)
        if not node_path:
            raise PolicyError("selfcheck_launcher_unsupported")
        node = Path(node_path).resolve(strict=True)
        if not node.is_file() or node.suffix.lower() != ".exe" or node.stem.lower() != "node":
            raise PolicyError("selfcheck_launcher_unsupported")
        return ResolvedCommand(str(node), (str(launcher),), name, "known_launcher_adapter", path_value)
    if os.name == "nt" and suffix not in {".exe", ".com"}:
        raise PolicyError("selfcheck_launcher_unsupported")
    root = worktree.resolve(strict=True)
    origin = "project_local" if root == real or root in real.parents else "host_path"
    return ResolvedCommand(str(real), (), name, origin, path_value)


def scrubbed_env(path_value: str, run_root: Path, host: dict[str, str] | None = None) -> dict[str, str]:
    host = os.environ if host is None else host
    env = {key: host[key] for key in HOST_KEYS if key in host and len(host[key]) < 4096 and "\0" not in host[key]}
    folders = {name: run_root / name for name in ("home", "tmp", "cache", "config", "data", "bin")}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    env.update({"PATH": path_value, "HOME": str(folders["home"]), "USERPROFILE": str(folders["home"]),
        "APPDATA": str(folders["config"]), "LOCALAPPDATA": str(folders["data"]),
        "XDG_CACHE_HOME": str(folders["cache"]), "XDG_CONFIG_HOME": str(folders["config"]),
        "XDG_DATA_HOME": str(folders["data"]), "TMP": str(folders["tmp"]), "TEMP": str(folders["tmp"]),
        "TMPDIR": str(folders["tmp"]), "PIP_CACHE_DIR": str(folders["cache"] / "pip"),
        "NPM_CONFIG_CACHE": str(folders["cache"] / "npm"), "GOBIN": str(folders["bin"]),
        "PIP_REQUIRE_VIRTUALENV": "1"})
    return env
