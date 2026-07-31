"""AI-provider connection test / launch probe (flowgate.default.0281 T0005, R1).

The AI-provider settings had no way to check whether a registered `cli_command` actually
runs on THIS host before a real document run fired it (NR0003 §4 F5). The only feedback was
a document run failing deep in the chain with `end_reason == "all_providers_failed"`, which
reads to the operator as "the provider just doesn't work". This module ports the pattern
Git settings already uses — `git_service.test_connection()` driven by GitSettingsView's
"Test connection" button, which tests the CURRENT form values before saving (P0005 §3-2) —
to the AI-provider path.

It is a *launch* probe, not an end-to-end model call: it starts `cli_command` through the
same executor a real run uses (`process_runner.popen_kwargs`, shell=True), feeds the prompt
on stdin as UTF-8 bytes (the invoke path's contract — args are forbidden, cp932 truncation,
0187 L0006 §2.5 / NR0003 §7-2), waits a few seconds, and reports what happened:

    - command_failed : exited non-zero quickly (e.g. codex's platform-specific
                       `--sandbox` rejecting Windows — NR0003 §5-2 H1). stderr tail is the
                       diagnostic the operator could not otherwise see.
    - ok             : exited 0 within the window.
    - launched       : still running at timeout (killed). The binary resolved and did not
                       fail immediately — the most a short probe can confirm for an
                       interactive CLI.

Every result also carries `permission_skip`: whether the command that was tested runs with
permission confirmation switched off (0371 NR0007 §5). A CLI parked on an approval prompt
and a CLI thinking hard about the prompt are the same observation from out here, so the
probe states the mode rather than letting `launched` be read as "unattended runs work".

Nothing is persisted. Requires the same permission as saving the provider, so it grants no
new ability — saving already lets that principal register an arbitrary shell command.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from modules.flow_gate.services import process_runner
from modules.flow_gate.services import test_command_service as _tcs
from modules.flow_gate.settings import ai_settings_service as _ai_settings

logger = logging.getLogger(__name__)

# Short by design: we are checking that the command *starts* and does not fail immediately,
# not that a model round-trip completes. Long enough to let a doomed launch (bad flag,
# unsupported sandbox, command-not-found) surface its non-zero exit and stderr.
PROBE_TIMEOUT_SEC = 12
# stdout/stderr are truncated to a tail — matches the 500-char fallback_history detail the
# invoke path already keeps (NR0003 §4 F5), but a little roomier for interactive diagnosis.
OUTPUT_TAIL_CHARS = 2000


def probe_provider(form: dict) -> dict:
    """Run one connection test for the given editor form values.

    `form` keys: exec_type, kind, cli_command, api_base_url, prompt (all optional).
    Never raises — every failure mode is reported in the returned dict so the UI always has
    something to show.
    """
    exec_type = (form.get("exec_type") or "cli").strip()
    kind = (form.get("kind") or "").strip()
    host_os = _tcs.current_os()
    host_shell = _tcs.current_shell()

    base = {
        "ok": True,
        "exec_type": exec_type,
        "kind": kind,
        "os": host_os,
        "shell": host_shell,
        "launched": False,
        "timed_out": False,
        "permission_skip": False,
        "exit_code": None,
        "duration_ms": None,
        "stdout_tail": "",
        "stderr_tail": "",
    }

    # API providers are reached over HTTP, not spawned — a launch probe does not apply.
    # Report cleanly rather than pretending to test something (NR0003 R1 scope is CLI).
    if exec_type != "cli":
        return {**base, "status": "skipped", "reason": "not_cli",
                "message": "Connection test currently supports CLI providers only."}

    cli_command = (form.get("cli_command") or "").strip()
    if cli_command == "":
        return {**base, "status": "skipped", "reason": "required_for_cli",
                "message": "Enter a command before testing."}

    # 0295 NR0003 §5-3: probe the command the invoke path would actually spawn, not the raw
    # stored string. Without this the probe is not just inaccurate but inverted for codex —
    # the mkdtemp() cwd below is never a git repo, so `codex exec` exits 1 before reading
    # stdin and every codex provider, however correct, comes back `command_failed`.
    cli_command = _ai_settings.normalize_cli_command(kind, cli_command)
    permission_rule = _ai_settings.permission_skip_rule(kind)
    permission_skip = _ai_settings.has_permission_skip(kind, cli_command)

    prompt = form.get("prompt") or ""

    # A throwaway scratch dir as cwd, so a command that writes files does not touch the
    # project tree. The env mirrors the three keys the invoke path injects (with harmless
    # values — this is not an authenticated run) plus FLOWGATE_OS / FLOWGATE_SHELL so the
    # child can tell which host it is on (NR0003 R5).
    root = Path(tempfile.mkdtemp(prefix="flowgate_probe_"))
    env = {
        "FLOWGATE_TOKEN": "",
        "FLOWGATE_SCRATCH": str(root),
        "FLOWGATE_API_BASE": (form.get("api_base_url") or "").strip(),
        "FLOWGATE_OS": host_os,
        "FLOWGATE_SHELL": host_shell,
    }

    started = time.monotonic()
    try:
        timed_out, exit_code, stdout, stderr = _run_probe(
            cli_command, root, PROBE_TIMEOUT_SEC, env, prompt,
        )
    except Exception as exc:  # noqa: BLE001 — the probe must never surface as a 500
        logger.warning("provider probe crashed", exc_info=True)
        return {**base, "status": "command_failed", "reason": "command_failed",
                "message": f"Could not launch the command: {exc}",
                "stderr_tail": str(exc)[-OUTPUT_TAIL_CHARS:]}
    finally:
        shutil.rmtree(root, ignore_errors=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    result = {
        **base,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
        "permission_skip": permission_skip,
        "exit_code": exit_code,
        "stdout_tail": (stdout or "")[-OUTPUT_TAIL_CHARS:],
        "stderr_tail": (stderr or "")[-OUTPUT_TAIL_CHARS:],
    }

    if timed_out:
        # Still running when we pulled the plug: the binary resolved and did not fail fast.
        # For an interactive CLI waiting on a model, this is the healthy signal — and it is
        # also what a CLI stopped at an approval prompt looks like, which is why the mode is
        # spelled out for a kind that has one (0371 NR0007 §5).
        waiting = "" if (permission_rule is None or permission_skip) else (
            " Note: this command still asks before it reads, writes or runs anything, and a"
            " FlowGate run has nobody to answer that prompt. Switch the permission-skip"
            " option on if this host needs unattended tool use."
        )
        return {**result, "launched": True, "status": "launched",
                "message": (
                    f"Command launched and was still running after {PROBE_TIMEOUT_SEC}s "
                    f"(then stopped). It did not fail immediately.{waiting}"
                )}
    if exit_code == 0:
        return {**result, "launched": True, "status": "ok",
                "message": f"Command exited 0 under {host_shell}."}
    return {**result, "launched": False, "status": "command_failed",
            "reason": "command_failed",
            "message": (
                f"Command exited with code {exit_code} under {host_shell}. "
                f"See the error output below."
            )}


def _run_probe(
    cmd: str,
    root: Path,
    timeout: int,
    env: dict,
    prompt: str,
) -> tuple[bool, "int | None", str, str]:
    """Spawn cmd, feed `prompt` on stdin (UTF-8), and wait up to `timeout` seconds.

    Returns (timed_out, exit_code, stdout, stderr). On timeout the whole process tree is
    killed (reusing the invoke path's primitives) and partial output is recovered.
    """
    kwargs = process_runner.popen_kwargs(root, env, include_stdio=True)
    kwargs["stdin"] = subprocess.PIPE

    proc = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(input=prompt.encode("utf-8"), timeout=timeout)
        return (
            False,
            proc.returncode,
            process_runner.safe_decode(stdout),
            process_runner.safe_decode(stderr),
        )
    except subprocess.TimeoutExpired:
        process_runner.kill_process_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = b"", b""
        return (
            True,
            None,
            process_runner.safe_decode(stdout),
            process_runner.safe_decode(stderr),
        )
