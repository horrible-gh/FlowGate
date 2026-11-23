#!/usr/bin/env python3
"""Install terminal components online or from pinned offline wheels; never enable."""
import argparse
import json
import os
from pathlib import Path
import time


def main():
    os.chdir(Path(__file__).resolve().parent)
    from dotenv import load_dotenv
    load_dotenv()
    from modules.flow_gate.services import terminal_bundle as bundle
    from modules.flow_gate.services.terminal_service import manager
    from modules.flow_gate.services.terminal_sessions import TerminalError
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", type=Path)
    parser.add_argument("--list", action="store_true")
    options = parser.parse_args()
    if options.list:
        print(json.dumps(bundle.CATALOG, indent=2))
        return 0
    if not bundle.required():
        print("Native PTY available; no bundle required. Enable in System Settings.")
        return 0
    job_id = None
    try:
        job_id = manager.install(options.from_dir)["install_id"]
        previous = None
        while True:
            job = manager.job(job_id)
            if job != previous:
                print(json.dumps(job), flush=True)
                previous = job
            if job["status"] in ("done", "failed", "cancelled"):
                manager.threads[job_id].join()
                return 0 if job["status"] == "done" else 1
            time.sleep(0.25)
    except KeyboardInterrupt:
        if job_id:
            manager.cancel(job_id)
            manager.threads[job_id].join(timeout=45)
        return 130
    except TerminalError as exc:
        print(json.dumps(exc.body()))
        return 1
    finally:
        manager.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
