"""POSIX self-check process owner.  Stdin EOF means the server disappeared."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import threading
import time

LIMIT = 65536


def _tail(pipe, output: bytearray) -> None:
    while True:
        chunk = pipe.read(8192)
        if not chunk:
            return
        output.extend(chunk)
        if len(output) > LIMIT:
            del output[:-LIMIT]


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def main() -> None:
    payload = json.loads(sys.stdin.buffer.readline())
    proc = subprocess.Popen(payload["argv"], shell=False, cwd=payload["cwd"], env=payload["env"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    out, err = bytearray(), bytearray()
    threads = [threading.Thread(target=_tail, args=(pipe, dest), daemon=True)
               for pipe, dest in ((proc.stdout, out), (proc.stderr, err))]
    for thread in threads:
        thread.start()
    sys.stdout.write(json.dumps({"event": "started", "pid": proc.pid}) + "\n")
    sys.stdout.flush()
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.buffer, selectors.EVENT_READ)
    deadline = time.monotonic() + payload["timeout_seconds"]
    reason = None
    try:
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                reason = "timeout"
                break
            events = selector.select(min(.2, max(0, deadline - time.monotonic())))
            if events:
                command = sys.stdin.buffer.readline()
                reason = "cancel" if command.strip() == b"cancel" else "server_lost"
                break
        if reason:
            _terminate(proc)
        else:
            proc.wait()
    finally:
        selector.close()
        for thread in threads:
            thread.join(timeout=5)
        result = {"event": "result", "exit_code": proc.returncode,
                  "timed_out": reason == "timeout", "cancelled": reason == "cancel",
                  "stdout_tail": bytes(out).decode("utf-8", "ignore"),
                  "stderr_tail": bytes(err).decode("utf-8", "ignore")}
        try:
            sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            pass


if __name__ == "__main__":
    main()
