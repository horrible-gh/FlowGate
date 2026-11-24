#!/usr/bin/env python3
"""Install voice using the same manifest, verification and self-test as the UI."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    os.chdir(Path(__file__).resolve().parent)
    from dotenv import load_dotenv
    load_dotenv()
    from modules.flow_gate.services.voice_errors import VoiceError
    from modules.flow_gate.services.voice_service import manager
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", default="whisper.cpp")
    parser.add_argument("--model", default="base")
    parser.add_argument("--from-dir", type=Path, help="Offline directory containing the exact catalog engine and model; never modified")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--export-bundle", type=Path, help="Export reviewed redistribution files and notices from --from-dir")
    parser.add_argument("--prepare-model", type=Path, help="Read-only exact upstream model archive to sanitize")
    parser.add_argument("--prepared-output", type=Path, help="New path for the deterministic KSS-free model tar")
    parser.add_argument("--ffmpeg-from-dir", type=Path, help="Separately acquired pinned FFmpeg for offline installation")
    options = parser.parse_args()
    if options.list:
        print(json.dumps(manager.catalog(True), ensure_ascii=False, indent=2))
        return 0
    job_id = None
    try:
        from modules.flow_gate.services.voice_service import specification
        spec = specification(options.service, options.model)
        print(json.dumps({"model": options.model, "license": spec["license"],
                          "source": spec.get("source", spec["url"]), "license_url": spec.get("license_url"),
                          "distribution": "offline-redistribution" if options.from_dir else "direct-upstream"}, ensure_ascii=False), flush=True)
        if options.prepare_model:
            if not options.prepared_output:
                parser.error("--prepare-model requires --prepared-output")
            import threading
            from modules.flow_gate.services.voice_sanitized import prepare_model
            prepare_model(spec, options.prepare_model, options.prepared_output, threading.Event())
            return 0
        if options.export_bundle:
            if not options.from_dir:
                parser.error("--export-bundle requires --from-dir")
            from modules.flow_gate.services.voice_distribution import export_bundle
            export_bundle(specification(options.service), spec, options.from_dir, options.export_bundle)
            return 0
        result = manager.install(options.service, options.model, options.from_dir, options.ffmpeg_from_dir)
        job_id = result["install_id"]
        previous = None
        while True:
            job = manager.job(job_id)
            progress = (job["step"], job["received_bytes"], job["total_bytes"])
            if progress != previous:
                print(f"{progress[0]} {progress[1]}/{progress[2]}", flush=True)
                previous = progress
            if job["done"]:
                if job["error"]:
                    print(json.dumps(job["error"]), file=sys.stderr)
                    return 1
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        if job_id:
            manager.cancel(job_id)
            manager.threads[job_id].join(timeout=35)
        return 130
    except VoiceError as exc:
        print(json.dumps(exc.response()), file=sys.stderr)
        return 1
    finally:
        manager.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
