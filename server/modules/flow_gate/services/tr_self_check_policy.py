"""Policy for advisory TR self-check commands.  No command string is parsed."""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

POLICY_VERSION = "tr-selfcheck-v2"
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

# flowgate.v02.0114 T#4 (D 0006 3.5): the host-wide resource cap. Every Self-check run of a host
# (same instance_registry.node_key) reserves R logical processors (at least 1, whatever the
# program), the active and protected runs' reservations never sum above the cap, and the run's
# whole process tree is limited to its R processors by the operating system (executor).
#   cap            min(HOST_LIMIT_ENV or HOST_LIMIT_DEFAULT, logical processors of this server)
#   serial reserve what a parallel share (the part above 1) must leave free, inside the cap
#   share          the request's parallel_share, default 1, at most SHARE_MAX (lowered to the cap)
HOST_LIMIT_DEFAULT = 4
SERIAL_RESERVE_DEFAULT = 1
SHARE_DEFAULT = 1
SHARE_MAX = 64
HOST_LIMIT_ENV = "FLOWGATE_SELFCHECK_HOST_LIMIT"
SERIAL_RESERVE_ENV = "FLOWGATE_SELFCHECK_SERIAL_RESERVE"
# The run's worker slot pool (D 3.5 (3)): a directory inside the run's own runtime folder holding
# R slot files, named to every process of the run through its environment. Read by
# server/tests/conftest.py; any other program ignores them.
SLOT_DIR_ENV = "FLOWGATE_SELFCHECK_SLOT_DIR"
SLOTS_ENV = "FLOWGATE_SELFCHECK_SLOTS"
SLOT_ENV_KEYS = (SLOT_DIR_ENV, SLOTS_ENV)


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
    # 0670 T0004 / flowgate.v02.0032 T#1: ``denied``/``deny_git_helpers`` let the chat
    # command policy reuse this exact resolution (argv checks, PATH control, launcher
    # adapter) with its own deny list. The defaults are the Self-check policy, unchanged.
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


def processor_ids() -> list[int]:
    """The logical processors this server may run on, lowest first, inside the first processor
    group on Windows (a Job's affinity is one group's mask). Empty when it cannot be read."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                                        ctypes.POINTER(ctypes.c_size_t)]
            kernel32.GetProcessAffinityMask.restype = wintypes.BOOL
            process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
            if not kernel32.GetProcessAffinityMask(kernel32.GetCurrentProcess(), ctypes.byref(process_mask),
                                                   ctypes.byref(system_mask)):
                return []
            mask = process_mask.value
            return [bit for bit in range(ctypes.sizeof(ctypes.c_size_t) * 8) if mask >> bit & 1]
        except Exception:
            return []
    try:
        return sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 0
        return list(range(count))


def _env_int(name: str, host: dict | None) -> int | None:
    raw = (os.environ if host is None else host).get(name)
    try:
        return int(str(raw).strip()) if raw not in (None, "") else None
    except ValueError:
        return None


def host_capacity(host: dict[str, str] | None = None) -> tuple[int, int]:
    """``(cap, serial reserve)`` of this host. A configured cap above the logical processor count
    is the processor count (D 3.5 (1)); the reserve stays inside the cap."""
    processors = len(processor_ids()) or 1
    configured = _env_int(HOST_LIMIT_ENV, host)
    cap = max(1, min(configured if configured and configured > 0 else HOST_LIMIT_DEFAULT, processors))
    reserve = _env_int(SERIAL_RESERVE_ENV, host)
    reserve = SERIAL_RESERVE_DEFAULT if reserve is None else reserve
    return cap, max(0, min(reserve, cap - 1))


def validate_share(value) -> int:
    """The request's ``parallel_share``: omitted = SHARE_DEFAULT, else an integer 1..SHARE_MAX.
    Not tied to the program: a pytest run and a type check asking the same share are equal."""
    if value is None:
        return SHARE_DEFAULT
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= SHARE_MAX:
        raise PolicyError("selfcheck_invalid_share")
    return value


def slot_pool_env(run_root: Path, slots: int) -> dict[str, str]:
    """Create the run's worker slot pool (``slot-0`` .. ``slot-<R-1>``) inside its runtime folder
    and return the two variables that name it. The files are only lock targets."""
    if not isinstance(slots, int) or isinstance(slots, bool) or slots < 1:
        raise PolicyError("selfcheck_invalid_share")
    pool = run_root / "slots"
    pool.mkdir(parents=True, exist_ok=True)
    for index in range(slots):
        (pool / f"slot-{index}").touch(exist_ok=True)
    return {SLOT_DIR_ENV: str(pool), SLOTS_ENV: str(slots)}


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
