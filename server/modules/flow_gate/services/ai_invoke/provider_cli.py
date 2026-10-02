"""Subprocess/CLI transport (0501 NR0003 §12/§15 `provider_cli.py`).

Spawning a provider CLI in its own process group, the progress watchdog that kills a
run that has gone quiet past its threshold, exit-code and dying-message recovery, and
the shell/launch resolution each vendor CLI needs. No run state, no registry, no
judging -- exactly the CLI half of what `worker.py` orchestrates.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import process_runner
from modules.flow_gate.settings import ai_settings_service

# Not a transport of `worker.py` itself (NR0003 §28: transport must not import the
# orchestration it is transport FOR). The watchdog primitives this module used to reach
# there -- the absolute-ceiling clock and the progress probes -- are pure/constant-only,
# so they now live on `runtime` (the parameter block) and `oracle` (the progress probes,
# §16) instead.
from . import oracle
from .runtime import (
    OUTPUT_TAIL_BYTES,
    PROBE_CANCELLED,
    PROBE_EXECUTION_FAILED,
    PROBE_LAUNCH_FAILED,
    PROBE_OK,
    PROBE_TIMEOUT,
    ProbeOutcome,
    STALL_POLL_INTERVAL_SEC,
    STALL_WATCHDOG_JOIN_SEC,
    _RUN_ID_RE,
    _absolute_cap_sec,
    _absolute_remaining_sec,
    _create_scratch,
    _delete_owned_scratch,
    _mark_scratch_completed,
    _next_run_id,
    _svc,
    _validate_scratch_manifest,
    logger,
)


def _canonicalize_cli_prompt(prompt: str, operator_api_base: str) -> tuple[str, str]:
    """Rewrite only exact operator-base occurrences and return the exported base."""
    agent_api_base = _svc()._resolve_agent_api_base(operator_api_base)
    if agent_api_base and operator_api_base and agent_api_base != operator_api_base:
        prompt = prompt.replace(operator_api_base, agent_api_base)
    return prompt, agent_api_base or operator_api_base


# ── No-progress watchdog (0446 T0014 §3) ─────────────────────────────────────
#
# `_cli_execute` waits for the worker with a single `communicate(timeout=...)`, so the
# only question it could ever ask was "has the clock run out?". NR0003 measured both
# ways that goes wrong on a fixed hour: a 74-minute TR hop that was still registering
# documents got cut off, and a worker that died in its first minutes still held its
# group for the remaining 59. This watchdog asks the other question — "is anything
# still happening?" — beside that wait, on its own thread, and answers it from the two
# signals the run already carries: the group's document max-seq and `git status` on the
# group worktree. The stdin prompt is still handed to `communicate()` exactly once and
# the watchdog never touches the pipes (§3-1).
_watchdog_kill_lock = threading.Lock()


def _claim_watchdog_kill(run: dict, proc, stop_event: threading.Event,
                         kind: str, now: float) -> bool:
    """Decide — once — whether the watchdog may end this process tree (§4-2).

    Every other exit owns the same process, so the claim is taken under a lock and
    re-checks all of them: the run thread already past `communicate()` (`stop_event`), a
    user cancel (which outranks the clock in `_classify_end_reason` and must stay
    `cancelled`), an earlier tick's claim, and a child that exited on its own between the
    poll and here. Losing any of those races means doing nothing at all.
    """
    with _watchdog_kill_lock:
        if stop_event.is_set():
            return False
        cancel_event = run.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            return False
        if run.get("watchdog_kill") is not None:
            return False
        if proc.poll() is not None:
            return False
        anchor = run.get("stall_anchor_mono")
        if anchor is None:
            anchor = run["started_mono"]
        run["watchdog_kill"] = {
            "kind": kind,                       # "no_progress" | "absolute_cap"
            "stalled_sec": int(max(0.0, now - anchor)),
            "elapsed_sec": int(max(0.0, now - run["started_mono"])),
            "threshold_sec": int(run.get("timeout_sec") or 0),
            "absolute_cap_sec": _absolute_cap_sec(),
            "last_progress_at": run.get("last_progress_at"),
            "progress_observations": int(run.get("progress_observations") or 0),
            "attempt_no": int(run.get("attempt_no") or 0),
        }
        # The same flag an expired `communicate()` raises, so this ends as end_reason
        # "timeout" / stop_code "timeout" and NEVER as a provider spawn_failed or
        # fast_fail (§4-3). `watchdog_kill` is the minimal in-memory mark that tells the
        # two kinds apart; T#2 turns it into the durable sentence. No new stop code and
        # no new column here (§3-4).
        run["timed_out"] = True
        claim = run["watchdog_kill"]
    logger.warning(
        "ai-invoke %s: %s — ending the worker (stalled %ss of %ss, elapsed %ss of %ss)",
        run.get("run_id"), kind, claim["stalled_sec"], claim["threshold_sec"],
        claim["elapsed_sec"], claim["absolute_cap_sec"],
    )
    try:
        process_runner.kill_process_tree(proc)
    except Exception:
        logger.warning("ai-invoke %s: watchdog kill failed", run.get("run_id"), exc_info=True)
    return True


def _progress_watchdog_loop(run: dict, proc, stop_event: threading.Event,
                            interval: float = STALL_POLL_INTERVAL_SEC) -> Optional[str]:
    """The poll body. Returns the kill kind, or None if it never killed anything."""
    source_root = Path(run["source_root"]) if run.get("source_root") else None
    # §3-3: the run's OWN start snapshot is the first comparison point, and every tick
    # after that is compared with the previous SUCCESSFUL read. Comparing forever against
    # the baseline instead would count one early edit as progress on every later tick — a
    # worker that wrote a single file and then hung would look busy until the ceiling.
    git_watermark = run.get("dirty_baseline")
    # A run with no source tree at all (the scratch fallback) has no source signal to
    # fail: that is not an unreadable sample, and treating it as one would disable the
    # guard outright for those runs. The document signal alone speaks for them.
    git_enabled = source_root is not None and source_root.is_dir()
    doc_watermark = run.get("baseline_seq")          # §3-2
    if not git_enabled:
        logger.info("ai-invoke %s: progress watchdog has no source tree — documents only",
                    run.get("run_id"))
    # T0004 §5: subprocess liveness is a THIRD, weaker signal, read independently of the
    # two above -- its own first successful read is a baseline only (a run that already
    # has children when the watchdog starts must not look like it just spawned them), and
    # its failures never touch `readable` (§5 point 5: an unsupported/unreadable process
    # snapshot must never fail the provider or the document/source progress gate).
    process_watermark = None

    while not stop_event.wait(interval):
        now = _svc()._now_mono()
        cancel_event = run.get("cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            return None
        if proc.poll() is not None:
            return None
        # The ceiling is unconditional (§3-6): it does not care how much progress there
        # has been, and it does not need a readable sample to be true.
        if now - run["started_mono"] >= _absolute_cap_sec():
            return "absolute_cap" if _claim_watchdog_kill(
                run, proc, stop_event, "absolute_cap", now) else None

        moved: list[str] = []
        readable = True
        seq = oracle._observe_group_max_seq(run)
        if seq is None:
            readable = False
        elif doc_watermark is None:
            doc_watermark = seq                      # first reading: nothing to compare to
        elif seq > doc_watermark:
            doc_watermark = seq
            moved.append("document")
        if git_enabled:
            paths = _svc()._git_status_paths(source_root)
            if paths is None:
                readable = False
            elif git_watermark is None:
                git_watermark = paths
            elif paths != git_watermark:
                git_watermark = paths
                moved.append("source")               # added AND removed paths both count

        # T0004 §5/§6: process liveness never feeds `readable` or `moved` above -- a
        # snapshot failure here must not touch the no-progress gate, and a snapshot
        # CHANGE must not touch it either (§9: a live/churning subprocess must never
        # reset `stall_anchor_mono` on its own). It only ever writes `last_activity_*`.
        process_moved = False
        try:
            snapshot = process_runner.process_tree_snapshot(proc)
        except Exception:
            snapshot = None
            logger.warning("ai-invoke %s: process snapshot raised — treating as unavailable",
                           run.get("run_id"), exc_info=True)
        if snapshot is not None:
            if process_watermark is None:
                process_watermark = snapshot          # first reading: baseline only (§5)
            elif snapshot != process_watermark:
                process_watermark = snapshot
                process_moved = True

        if moved or process_moved:
            # §6: a document/source tick is strong progress AND weak activity at once;
            # a process-only tick is activity alone. Either way `last_activity_*` is the
            # one field both paths write.
            activity_signals = list(moved)
            if process_moved:
                activity_signals.append("process")
            run["last_activity_at"] = now_iso()
            run["last_activity_signal"] = ",".join(activity_signals)
            run["activity_observations"] = int(run.get("activity_observations") or 0) + 1

        if moved:
            # §3-5, second half: one signal actually moving is enough — the other one
            # standing still proves nothing about it.
            run["stall_anchor_mono"] = now
            run["last_progress_mono"] = now
            run["last_progress_at"] = now_iso()
            run["last_progress_signal"] = ",".join(moved)
            run["progress_observations"] = int(run.get("progress_observations") or 0) + 1
            continue
        if not readable:
            # §3-5, first half: an unreadable sample means "unknown", not "nothing
            # happened". Warn and let the next tick decide. A permanently blind run is
            # still ended on time by the ceiling above.
            logger.warning("ai-invoke %s: progress watchdog sample was unreadable — retrying",
                           run.get("run_id"))
            continue
        anchor = run.get("stall_anchor_mono")
        if anchor is None:
            anchor = run["started_mono"]
        if now - anchor >= float(run["timeout_sec"]):
            return "no_progress" if _claim_watchdog_kill(
                run, proc, stop_event, "no_progress", now) else None
    return None


def _start_progress_watchdog(run: dict, proc,
                             interval: float = STALL_POLL_INTERVAL_SEC) -> tuple:
    """Start the watchdog for ONE attempt. Returns (stop_event, thread)."""
    stop_event = threading.Event()
    # Each attempt opens its own no-progress window — a fresh worker cannot be charged
    # for the silence of the one before it. The ABSOLUTE ceiling deliberately does NOT
    # reset: it is measured from started_mono, which `_reset_attempt_state` keeps (§2-4).
    run["stall_anchor_mono"] = _svc()._now_mono()
    run["watchdog_kill"] = None
    thread = threading.Thread(
        target=_progress_watchdog_loop, args=(run, proc, stop_event, interval),
        name=f"ai-invoke-watchdog-{run.get('run_id')}", daemon=True,
    )
    thread.start()
    return stop_event, thread


def _stop_progress_watchdog(stop_event: Optional[threading.Event], thread,
                            run_id: Optional[str] = None) -> None:
    """Stop and join the watchdog on EVERY exit of the attempt (§4-1).

    Called from `_cli_execute`'s finally, so a natural exit, a TimeoutExpired, a broken
    stdin pipe, a user cancel and a fast-fail all come through here. The join matters as
    much as the event: a thread left running would still hold a `proc` the next attempt
    is about to replace.
    """
    if stop_event is None:
        return
    stop_event.set()
    if thread is None:
        return
    thread.join(timeout=STALL_WATCHDOG_JOIN_SEC)
    if thread.is_alive():
        # It can no longer kill anything — `_claim_watchdog_kill` re-reads stop_event
        # under the lock — but it should not have taken this long, so say so.
        logger.warning("ai-invoke %s: progress watchdog did not stop within %ss",
                       run_id, STALL_WATCHDOG_JOIN_SEC)


# ── CLI adapter (L0006 §2.3) ─────────────────────────────────────────────────

_CLI_LAUNCH_AUDIT_SCHEMA = "flowgate.external-cli-launch.v1"


def _shell_kind() -> str:
    return "windows_cmd" if os.name == "nt" else "posix_sh"


def _stable_provider_kind(provider: dict) -> str:
    kind = str(provider.get("kind") or "").lower()
    return kind if kind in {"codex", "claude"} else "other"


def _audit_cli_launch(decision: dict) -> None:
    """Emit exactly one secret-free, line-safe launch decision event."""
    allowed = {
        "schema", "event", "decision", "reason", "run_id", "provider_kind",
        "cwd_source", "spawn_cwd", "agent_cwd", "cwd_transition",
        "shell_kind", "is_unc",
    }
    event = {key: decision.get(key) for key in allowed}
    event["schema"] = _CLI_LAUNCH_AUDIT_SCHEMA
    event["event"] = "external_cli_launch_decision"
    try:
        logger.info("ai-invoke cli spawn decision %s", json.dumps(event, ensure_ascii=True))
    except Exception:
        pass  # logging must never affect the launch outcome


def _blocked_cli_launch(run: dict, provider: dict, reason: str) -> dict:
    return {
        "decision": "blocked", "reason": reason,
        "run_id": run.get("run_id") if _RUN_ID_RE.fullmatch(str(run.get("run_id") or "")) else "<invalid>",
        "provider_kind": _stable_provider_kind(provider),
        "cwd_source": None, "spawn_cwd": None, "agent_cwd": None,
        "cwd_transition": None, "shell_kind": _svc()._shell_kind(),
        "is_unc": None,
    }


def _resolve_cli_launch(provider: dict, run: dict, command: str) -> tuple[Optional[dict], str]:
    """Resolve the sole product CLI spawn contract, or fail closed with a fixed code.

    Product runs always carry project_id and therefore must prove the current run scratch
    manifest even when a group worktree is the agent cwd (the scratch is still the UNC
    bootstrap and temp/cache boundary). The project-less compatibility branch exists only
    for older isolated unit harnesses; start_run never creates such a run.
    """
    scratch = Path(run.get("scratch_dir") or "")
    project_id = run.get("project_id")
    run_id = str(run.get("run_id") or "")
    if project_id:
        manifest, reason = _validate_scratch_manifest(project_id, run_id, scratch)
        if manifest is None:
            return None, f"scratch_{reason}"
    try:
        scratch_abs = scratch.resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "scratch_unavailable"
    if not scratch_abs.is_absolute() or not scratch_abs.is_dir() or _svc()._is_reparse_or_symlink(scratch):
        return None, "scratch_unavailable"

    source = Path(run["source_root"]) if run.get("source_root") else None
    agent_cwd = scratch_abs
    cwd_source = "run_scratch"
    if source is not None and source.is_dir() and not project_id:
        # Test-only legacy run shape; real runs are covered by the manifest branch above.
        agent_cwd = source.absolute() if str(source).startswith("\\\\") else source.resolve(strict=True)
        cwd_source = "group_worktree"

    effective_command, effective_cwd = process_runner.unc_safe_shell(command, agent_cwd)
    is_unc = str(agent_cwd).startswith("\\\\")
    if effective_cwd is None:
        if not scratch_abs.is_absolute() or str(scratch_abs).startswith("\\\\"):
            return None, "unc_bootstrap_unavailable"
        spawn_cwd = scratch_abs
        transition = "pushd"
    else:
        spawn_cwd = Path(effective_cwd).resolve(strict=True)
        transition = "none"
    if not spawn_cwd.is_absolute() or not spawn_cwd.is_dir():
        return None, "spawn_cwd_unavailable"
    return {
        "decision": "launch", "reason": None, "run_id": run_id,
        "provider_kind": _stable_provider_kind(provider), "cwd_source": cwd_source,
        "spawn_cwd": str(spawn_cwd), "agent_cwd": str(agent_cwd),
        "cwd_transition": transition,
        "shell_kind": _svc()._shell_kind(),
        "is_unc": is_unc, "effective_command": effective_command,
    }, "valid"


def _prepare_cli_command(kind: str, command: str, last_message_file: Path) -> str:
    """The stored CLI command as a launch actually runs it (run and probe share this).

    0295 NR0003 §5-2: injects codex's --skip-git-repo-check when the stored command lacks
    it. The cwd resolved by _resolve_cli_launch is NOT guaranteed to be a git repo
    (scratch fallback, or a project mirror that is not a checkout), and codex exec exits 1
    immediately when it is not -- burning the provider as a fast_fail before it reads the
    prompt.
    """
    command = ai_settings_service.normalize_cli_command(kind, command)
    if kind == "codex":
        command = f'{command} --output-last-message "{last_message_file}"'
    return command


def _cli_scratch_env(scratch: Path) -> dict[str, str]:
    """Temp and cache locations confined to the scratch (never the OS temp directory)."""
    return {
        "TMP": str(scratch / "tmp"),
        "TEMP": str(scratch / "tmp"),
        "TMPDIR": str(scratch / "tmp"),
        "XDG_CACHE_HOME": str(scratch / "cache"),
        "PIP_CACHE_DIR": str(scratch / "cache" / "pip"),
        "NPM_CONFIG_CACHE": str(scratch / "cache" / "npm"),
    }


def _cli_popen_kwargs(decision: dict, env: dict[str, str]) -> dict:
    kwargs = process_runner.popen_kwargs(Path(decision["agent_cwd"]), env)
    kwargs["cwd"] = decision["spawn_cwd"]
    kwargs["stdin"] = subprocess.PIPE
    return kwargs


class _CliProcess:
    """The one spawn / own / communicate / reap boundary of a CLI child.

    A run (_cli_execute) and a run-less Evaluation probe (probe_cli_completion) both go
    through it, so the Job Object / process-group ownership, the bounded pipe drain, the
    process-tree kill on timeout and the final reap exist exactly once.
    """

    def __init__(self, run_id: Optional[str]) -> None:
        self.run_id = run_id
        self.owner = process_runner.WindowsProcessOwner(run_id)
        self.proc = None

    def start(self, command: str, kwargs: dict) -> bool:
        """Spawn and attach. False when the process could not be started."""
        if os.name == "nt":
            kwargs["creationflags"] = self.owner.creationflags(kwargs.get("creationflags", 0))
        try:
            self.proc = subprocess.Popen(command, **kwargs)
        except Exception:
            return False
        self.owner.attach(self.proc)
        return True

    def terminate(self) -> None:
        if self.proc is not None:
            process_runner.kill_process_tree(self.proc)

    def communicate(self, data: bytes, timeout: float) -> tuple:
        """Feed stdin and collect output: (stdout, stderr, timed_out, error).

        A timeout kills the whole tree and still salvages what the child wrote; any other
        failure (e.g. a stdin pipe broken before the child read the prompt) is returned
        as error for the caller to classify.
        """
        proc = self.proc
        try:
            stdout, stderr = process_runner.communicate_with_cleanup(
                proc, self.owner, input=data, timeout=timeout
            )
            return stdout, stderr, False, None
        except subprocess.TimeoutExpired as exc:
            timed_out, error = True, None
            fallback = (getattr(exc, "output", None), getattr(exc, "stderr", None))
        except Exception as exc:
            timed_out, error = False, exc
            fallback = (None, None)
        process_runner.kill_process_tree(proc)
        try:
            stdout, stderr = process_runner.communicate_with_cleanup(proc, self.owner, timeout=5)
        except Exception:
            stdout, stderr = fallback
        return stdout, stderr, timed_out, error

    def close(self) -> None:
        """Kill anything still owned, reap the child and release the ownership handle."""
        proc = self.proc
        try:
            if proc is not None and (self.owner.active or proc.poll() is None):
                process_runner.kill_process_tree(proc)
            if proc is not None:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    logger.warning("ai-invoke %s: process_reap_failed", self.run_id,
                                   exc_info=True)
        finally:
            self.owner.close()


def _cli_execute(provider: dict, prompt: str, run: dict) -> tuple[str, Optional[str]]:
    """stdin-injected CLI run (claude/copilot/codex; args are forbidden — cp932
    truncation). Returns (classification, failure_detail)."""
    cmd = (provider.get("cli_command") or "").strip()
    if not cmd:
        return "spawn_failed", "cli_command not set"
    kind = provider.get("kind") or ""
    scratch = Path(run["scratch_dir"])
    last_message_file = scratch / "last_message.txt"
    cmd = _prepare_cli_command(kind, cmd, last_message_file)

    # T0011: cwd is selected once below by _resolve_cli_launch. No caller cwd, HOME,
    # installation directory, base checkout, or OS temp fallback is permitted.
    # Group 0235 (D0005 §3-4 / L0008 §2-5): the external agent runs on THIS host and
    # must post results to an address it can actually reach. The mention was built
    # with the operator-facing base; rewrite it (and export it) to an agent-reachable
    # base (configured setting -> same-host loopback -> operator base).
    operator_api_base = run.get("api_base_url") or ""
    prompt, agent_api_base = _canonicalize_cli_prompt(prompt, operator_api_base)
    bundle_api_base = (agent_api_base or operator_api_base).rstrip("/") + "/source-bundles/cli"
    prompt = prompt.rstrip() + (
        "\n\n## Source Bundle CLI boundary\n"
        "Use FLOWGATE_BUNDLE_API with Authorization: Bearer $FLOWGATE_TOKEN for "
        "ensure, status, access (read/search/glob/stat), and allowed run calls. "
        "No human approval is required. Never consume an internal Bundle or Scratch path directly.\n"
    )
    # CLI providers authenticate themselves; a configured api_key is deliberately
    # NOT exported (leak prevention, L0006 §2.3).
    env = {
        "FLOWGATE_TOKEN": run["raw_token"],
        # T0004 (0581 NR0003): the CLI worker's file-submission contract
        # (FLOWGATE_SCRATCH/verdict.json -> POST /inbox doc_path) is validated against
        # the TOKEN's own scratch, not the run-internal one -- they must be the same
        # issue result as FLOWGATE_TOKEN above, or inbox's path jail rejects a file the
        # worker wrote exactly where it was told to. run-internal scratch (TMP/TEMP/
        # TMPDIR, provider output, retained diagnostics below) is untouched.
        "FLOWGATE_SCRATCH": run["token_scratch_dir"],
        **_cli_scratch_env(scratch),
        "FLOWGATE_API_BASE": agent_api_base or operator_api_base,
        "FLOWGATE_BUNDLE_API": bundle_api_base,
    }
    decision, reason = _resolve_cli_launch(provider, run, cmd)
    if decision is None:
        blocked = _blocked_cli_launch(run, provider, reason)
        _audit_cli_launch(blocked)
        return "spawn_failed", f"CLI launch blocked: {reason}"
    eff_cmd = decision.pop("effective_command")
    cli = _CliProcess(run.get("run_id"))
    watchdog_stop = watchdog_thread = None
    try:
        kwargs = _cli_popen_kwargs(decision, env)
        _audit_cli_launch(decision)

        launched = time.monotonic()
        if not cli.start(eff_cmd, kwargs):
            return "spawn_failed", "unable to start CLI process"
        proc = cli.proc

        run["proc"] = proc
        # Close the cancel-vs-spawn race: a cancel that landed between admission and
        # Popen saw proc=None and killed nothing — reap the child ourselves now.
        if run["cancel_event"].is_set():
            process_runner.kill_process_tree(proc)
        # 0446 T0014 §3-1: the no-progress threshold is enforced BESIDE this wait, by the
        # watchdog thread, because `communicate()` cannot be asked "is it still working?".
        # What is left for the wait itself is the absolute ceiling — the one deadline that
        # holds however well the worker is doing (§3-6) — so a run that keeps producing is
        # no longer cut off at its threshold, and a run that produces nothing is still
        # ended there, by the watchdog, long before this timeout could fire.
        watchdog_stop, watchdog_thread = _start_progress_watchdog(run, proc)
        remaining = max(1.0, _absolute_remaining_sec(run))
        stdout, stderr, timed_out, error = cli.communicate(prompt.encode("utf-8"), remaining)
        if error is not None:
            # e.g. stdin pipe broken before the child read the prompt
            elapsed = time.monotonic() - launched
            if (
                elapsed < _svc().FAST_FAIL_WINDOW_SEC
                and not oracle._work_landed(run)
                # 0446 T0014 §4-3: a watchdog kill is a clock decision, never a provider
                # startup failure — it must not send the chain to the next provider.
                and run.get("watchdog_kill") is None
            ):
                return "spawn_failed", str(error)[:500]
    finally:
        # Setup failures (attach/resume, watchdog start, remaining-budget lookup)
        # have exactly the same ownership boundary as normal communication.
        try:
            _stop_progress_watchdog(watchdog_stop, watchdog_thread, run.get("run_id"))
        finally:
            try:
                cli.close()
            finally:
                if run.get("proc") is cli.proc:
                    run["proc"] = None

    # 0446 T0014 §4-3: a watchdog kill ends `communicate()` NORMALLY — the child is
    # already gone, so there is no TimeoutExpired to catch and the local flag above is
    # still False. Merge the verdict in before anything reads it: the exit code of a
    # killed worker is not a verdict (it stays None, as on any other timeout) and a
    # killed worker is not a fast_fail candidate. `watchdog_kill` — not `run["timed_out"]`
    # — is the source here, because it is re-armed per attempt by the watchdog itself.
    timed_out = timed_out or run.get("watchdog_kill") is not None
    elapsed = time.monotonic() - launched
    out_text = process_runner.safe_decode(stdout)
    err_text = process_runner.safe_decode(stderr)
    run["stdout_tail"] = out_text[-OUTPUT_TAIL_BYTES:]
    run["stderr_tail"] = err_text[-OUTPUT_TAIL_BYTES:]
    run["exit_code"] = proc.returncode if not timed_out else None
    if timed_out:
        run["timed_out"] = True

    cancelled = run["cancel_event"].is_set()
    # Fast-fail: startup failure ⇒ fallback candidate. A user cancel or timeout is
    # never a provider startup failure (L0006 §2.2 / §4.3).
    if (
        not cancelled
        and not timed_out
        and proc.returncode is not None
        and proc.returncode != 0
        and elapsed < _svc().FAST_FAIL_WINDOW_SEC
        and not oracle._work_landed(run)
    ):
        detail = (err_text or out_text).strip()[-500:] or f"exit {proc.returncode} within {int(elapsed)}s"
        return "fast_fail", detail

    _recover_cli_last_message(run, kind, out_text, last_message_file)
    return "started_ok", None


def probe_cli_completion(*, project_id: str, kind: str, command: Optional[str], prompt: str,
                         timeout: float, handle, scratch: Optional[Path] = None,
                         run_id: Optional[str] = None) -> ProbeOutcome:
    """One run-less prompt/response round trip (AI connection test / AI Evaluation), on the
    product CLI boundary.

    Same command normalisation (_prepare_cli_command), manifest-bound run scratch
    (_create_scratch), launch policy (_resolve_cli_launch), scratch-confined env and
    _CliProcess ownership/timeout/reap as _cli_execute. Only the run-bound parts are left
    out: no Source Bundle prompt, no FlowGate token or API base (blanked, never inherited),
    no group watchdog. A handle abort kills the whole process tree.

    Without `scratch` the probe owns a fresh scratch and deletes it afterwards. With
    `scratch`/`run_id` (flowgate.v02.0017: an AI Evaluation's judge and target share one
    evaluation Scratch) the caller owns that manifest-bound scratch: the CLI runs inside it
    and it is left in place for the caller to settle.
    """
    command = (command or "").strip()
    if not command or not project_id:
        return ProbeOutcome(PROBE_LAUNCH_FAILED)
    owned = scratch is None
    if owned:
        run_id = _next_run_id()
        try:
            scratch = _create_scratch(project_id, run_id)
        except (OSError, ValueError):
            return ProbeOutcome(PROBE_LAUNCH_FAILED)
    elif not run_id:
        return ProbeOutcome(PROBE_LAUNCH_FAILED)
    try:
        provider = {"kind": kind}
        run = {"run_id": run_id, "project_id": project_id, "scratch_dir": str(scratch)}
        last_message_file = (scratch / "last_message.txt" if owned else
                             scratch / "tmp" / f"last_message_{time.monotonic_ns()}.txt")
        decision, reason = _resolve_cli_launch(
            provider, run, _prepare_cli_command(kind, command, last_message_file))
        if decision is None:
            _audit_cli_launch(_blocked_cli_launch(run, provider, reason))
            return ProbeOutcome(PROBE_LAUNCH_FAILED)
        eff_cmd = decision.pop("effective_command")
        env = {
            **_cli_scratch_env(scratch),
            "FLOWGATE_TOKEN": "", "FLOWGATE_API_BASE": "", "FLOWGATE_BUNDLE_API": "",
            "FLOWGATE_SCRATCH": str(scratch),
        }
        cli = _CliProcess(run_id)
        try:
            kwargs = _cli_popen_kwargs(decision, env)
            _audit_cli_launch(decision)
            if not cli.start(eff_cmd, kwargs):
                return ProbeOutcome(PROBE_LAUNCH_FAILED)
            if not handle.bind(cli.terminate):
                return ProbeOutcome(PROBE_CANCELLED)
            stdout, _stderr, timed_out, error = cli.communicate(prompt.encode("utf-8"), timeout)
            returncode = cli.proc.returncode
        finally:
            cli.close()
        if handle.cancelled:
            return ProbeOutcome(PROBE_CANCELLED)
        if timed_out:
            return ProbeOutcome(PROBE_TIMEOUT)
        if error is not None or returncode != 0:
            return ProbeOutcome(PROBE_EXECUTION_FAILED)
        # The whole answer, not the run's front-truncated last_message: a caller parses it
        # (an AI Evaluation judge replies with one JSON object), and cutting the front of a
        # reply past LAST_MESSAGE_MAX_BYTES drops its opening brace.
        return ProbeOutcome(PROBE_OK, _cli_last_message(
            kind, process_runner.safe_decode(stdout), last_message_file) or "")
    finally:
        if owned:
            _mark_scratch_completed(project_id, run_id, scratch, now_iso())
            deleted, why = _delete_owned_scratch(project_id, run_id, scratch)
            if not deleted:
                logger.warning("ai-invoke %s: probe scratch retained (%s)", run_id, why)


def _copilot_last_message(stdout_text: str) -> Optional[str]:
    """Last assistant text from copilot's `--output-format=json` event stream.

    The stream is NDJSON — one event object per line, no blank lines anywhere — so the
    blank-line block splitter below returns the WHOLE dump as a single "message" and the
    operator sees MCP server status logs where the answer should be (0292 CH0002, 0295
    NR0003 §6). The answer lives in the last `assistant.message` event's `data.content`;
    `assistant.message_delta` carries the same text in fragments and `result` carries no
    text at all, so neither is a usable substitute.

    Returns None when nothing parses, leaving the caller on its block-splitting fallback —
    a copilot run that failed before emitting any event still has stderr/plain output worth
    showing.
    """
    message: Optional[str] = None
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant.message":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        content = data.get("content")
        if isinstance(content, str) and content.strip():
            message = content.strip()
    return message


def _recover_cli_last_message(run: dict, kind: str, stdout_text: str, last_message_file: Path) -> None:
    """A run keeps the tail of its last message (front truncated to LAST_MESSAGE_MAX_BYTES)."""
    message = _cli_last_message(kind, stdout_text, last_message_file)
    run["last_message"] = oracle._truncate_front(message)
    run["last_message_received"] = bool(message)


def _cli_last_message(kind: str, stdout_text: str, last_message_file: Path) -> Optional[str]:
    """Per-kind last-message recovery (hive providers.py rule table, rules only):
    claude = full stdout trimmed / codex = --output-last-message file /
    copilot = last `assistant.message` event / custom = last non-blank block of the tail.
    Untruncated; storing it is the caller's concern."""
    message: Optional[str] = None
    if kind == "claude":
        message = stdout_text.strip() or None
    elif kind == "codex":
        try:
            if last_message_file.is_file():
                message = last_message_file.read_text(encoding="utf-8", errors="replace").strip() or None
        except Exception:
            message = None
    elif kind == "copilot":
        message = _copilot_last_message(stdout_text)
    if message is None and kind not in ("claude", "codex"):
        tail = stdout_text[-OUTPUT_TAIL_BYTES:]
        blocks = [b.strip() for b in re.split(r"\n\s*\n", tail) if b.strip()]
        message = blocks[-1] if blocks else None
    return message
