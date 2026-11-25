"""Short title-only calls on the existing remote Agent execution boundary."""
import threading
import time
import uuid
from datetime import datetime, timezone


def complete(project_id, provider, prompt, user_id, timeout):
    from modules.flow_gate.services.ai_invoke import provider_remote, runtime
    run_id = "title_" + uuid.uuid4().hex
    # No workflow lease, worker token, document write or API callback is needed.
    # Distinct identity allows this job to overlap the card/chat executor.
    run = dict(run_id=run_id, project_id=project_id, group_id=None, doc_ref=None,
               mode="single", status="running", provider=provider, issued_to=user_id,
               title_only=True, cancel_event=threading.Event(), started_mono=time.monotonic(),
               started_at=datetime.now(timezone.utc).isoformat(),
               timeout_sec=timeout, chat_timeout_policy=True, chat_timeout_sec=timeout)
    with runtime._runs_lock:
        runtime._runs[run_id] = run
    try:
        status, _ = provider_remote.execute(provider, prompt, run)
        if (status != "started_ok" or run.get("timed_out") or
                run["cancel_event"].is_set() or run.get("exit_code") != 0):
            return None, (run.get("remote_final_failure") or {}).get("code", "execution_failed")
        return run.get("last_message"), None
    finally:
        # The supervisor retains ownership after a watch error until terminal/fenced.
        if not run.get("remote_job_active"):
            with runtime._runs_lock:
                runtime._runs.pop(run_id, None)
