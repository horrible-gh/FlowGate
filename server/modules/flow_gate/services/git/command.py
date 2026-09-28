"""Raw git subprocess execution: prompt-free auth injection, secret scrubbing,
and the one-shot ASKPASS helper.

Extracted from git_service.py (flowgate.default.0550 T0009, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .credentials import GitServiceError, _scrub

GIT_LOCAL_TIMEOUT_SEC = 30

# flowgate.default.0607 T0004 §3.1 (NR0003 §5/§9.6): git commands that write objects
# (merge, commit, fetch) end by running auto maintenance. Git for Windows cannot
# detach it, so a `git gc --auto` repack of the whole object store ran INSIDE the
# approval's `git merge --no-ff`, held the process ~35s after the merge commit
# already existed, and `_run_git`'s 30s kill turned a successful merge into a
# git_error. Every FlowGate git call runs on a request path, so auto gc/maintenance
# is switched off per invocation through git's command-scope environment config
# (GIT_CONFIG_COUNT/KEY_n/VALUE_n, the env twin of `-c`; same precedence, argv
# untouched). The repository's own config is never written. FlowGate has no other
# gc/maintenance path of its own — see the 0607 TR for the follow-up note.
_REQUEST_PATH_GIT_CONFIG: tuple[tuple[str, str], ...] = (
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
)


def _suppress_auto_maintenance(env: dict) -> None:
    """Append the request-path overrides after any env config already present."""
    try:
        count = max(int(env.get("GIT_CONFIG_COUNT") or 0), 0)
    except (TypeError, ValueError):
        count = 0
    for key, value in _REQUEST_PATH_GIT_CONFIG:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)


def git_available() -> bool:
    return shutil.which("git") is not None


def _write_askpass() -> tuple[Path, Path]:
    """One-shot ASKPASS helper pair (launcher + python echo script).

    The secret itself is NEVER written to disk — the helper echoes the
    FLOWGATE_GIT_ASK_USER / FLOWGATE_GIT_ASK_PASS env vars of the git child
    process. Both files are deleted right after the git call (L0006 §2.3).
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="fg-askpass-"))
    helper = tmpdir / "askpass.py"
    helper.write_text(
        "import os, sys\n"
        "prompt = (sys.argv[1] if len(sys.argv) > 1 else '').lower()\n"
        "key = 'FLOWGATE_GIT_ASK_USER' if 'username' in prompt else 'FLOWGATE_GIT_ASK_PASS'\n"
        "sys.stdout.write(os.environ.get(key, '') + '\\n')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = tmpdir / "askpass.bat"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n', encoding="utf-8"
        )
    else:
        launcher = tmpdir / "askpass.sh"
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n', encoding="utf-8"
        )
        os.chmod(launcher, 0o700)
    return launcher, tmpdir


def _run_git(
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = GIT_LOCAL_TIMEOUT_SEC,
    username: Optional[str] = None,
    secret: Optional[str] = None,
    author_env: Optional[dict] = None,
    extra_env: Optional[dict] = None,
) -> subprocess.CompletedProcess:
    """Run git with prompt-free auth injection and secret-scrubbed output.

    ``author_env`` carries GIT_AUTHOR_NAME/GIT_AUTHOR_EMAIL for a project-configured
    commit author (0237); None keeps git's own default, which the `-c user.*` ident
    on commit/merge argv resolves to the FlowGate identity. ``extra_env`` is a plain
    passthrough for anything else a caller needs set for one call — currently only
    ``GIT_INDEX_FILE``, which `_apply_write_plan_locked`'s isolated scratch-index helpers use
    to build/inspect a tree without ever touching this checkout's real index.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.pop("GIT_ASKPASS", None)
    env.pop("SSH_ASKPASS", None)
    # Never inherit an operator's ambient author from the server process env: the
    # author is either the project's configured one or the FlowGate default.
    env.pop("GIT_AUTHOR_NAME", None)
    env.pop("GIT_AUTHOR_EMAIL", None)
    if author_env:
        env.update(author_env)
    if extra_env:
        env.update(extra_env)
    _suppress_auto_maintenance(env)
    askpass_dir: Optional[Path] = None
    if secret is not None:
        launcher, askpass_dir = _write_askpass()
        env["GIT_ASKPASS"] = str(launcher)
        env["FLOWGATE_GIT_ASK_USER"] = username or ""
        env["FLOWGATE_GIT_ASK_PASS"] = secret
    try:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                ["git", *args], returncode=-1, stdout="", stderr="timeout_expired"
            )
        except FileNotFoundError:
            raise GitServiceError(
                500, "git_unavailable",
                "git binary not found on server (install git in the runtime image)",
            )
        proc = subprocess.CompletedProcess(
            proc.args, proc.returncode,
            _scrub(proc.stdout, secret), _scrub(proc.stderr, secret),
        )
        return proc
    finally:
        if askpass_dir is not None:
            shutil.rmtree(askpass_dir, ignore_errors=True)
