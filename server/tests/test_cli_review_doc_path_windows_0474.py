"""Windows subprocess E2E for the 0474 CLI review doc_path transport."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from inbox_client import post_inbox as route_post_inbox
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services.ai_invoke import oracle
from test_review_doc_path_0393 import DOC_ID, PROJECT, review_env  # noqa: F401


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher E2E")

KOREAN = (
    "윈도우 명령줄에 직접 싣지 않고 UTF-8 파일 경계를 통과해야 하는 충분히 긴 한국어 "
    "검토 문단입니다. 인용과 코드페이지 손상을 함께 탐지합니다. "
) * 30
EXPECTED = {
    "verdict": "issues",
    "findings": [{
        "locus": "모듈/位置/🧭🚀",
        "note": KOREAN + "日本語の指摘を保存します。 🧪🌌",
    }],
    "comment": KOREAN + "最終コメントです。 😀𠮷🚀",
}


def test_windows_real_cli_process_posts_only_doc_path_and_preserves_unicode(
    monkeypatch, tmp_path, review_env,
):
    received = []
    responses = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            envelope = json.loads(self.rfile.read(length).decode("utf-8"))
            received.append(envelope)
            response = route_post_inbox(envelope, raw_token="raw")
            responses.append(response.status_code)
            body = response.content
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = review_env["scratch"] / "가짜 claude 실행기.py"
        prompt_capture = review_env["scratch"] / "받은 prompt.txt"
        payload_literal = repr(EXPECTED)
        script.write_text(
            "import json, os, pathlib, sys, urllib.request\n"
            "scratch = pathlib.Path(os.environ['FLOWGATE_SCRATCH'])\n"
            "prompt = sys.stdin.buffer.read().decode('utf-8')\n"
            f"(scratch / {prompt_capture.name!r}).write_text(prompt, encoding='utf-8')\n"
            f"payload = {payload_literal}\n"
            "target = scratch / '긴 review 결과 🚀.json'\n"
            "target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')\n"
            "base = os.environ['FLOWGATE_API_BASE'].rstrip('/')\n"
            "def post(body):\n"
            "    data=json.dumps(body).encode('utf-8')\n"
            "    req=urllib.request.Request(base + '/inbox', data=data, headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['FLOWGATE_TOKEN']})\n"
            "    with urllib.request.urlopen(req, timeout=20) as r: return json.loads(r.read().decode('utf-8'))\n"
            f"base_body={{'action':'review','project':{PROJECT!r},'doc_id':{DOC_ID!r},'doc_path':str(target)}}\n"
            "dry=post(dict(base_body, dry_run=True))\n"
            "post(dict(base_body, receipt=dry['receipt']))\n",
            encoding="utf-8",
        )

        project_id = "project-0474"
        run_id = "aiv_20260917_047400"
        monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _p: {"project_name": "p0474"})
        monkeypatch.setattr(
            svc.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path / "storage root 日本語"
        )
        scratch = svc._create_scratch(project_id, run_id)
        worktree = tmp_path / "work tree 한글"
        worktree.mkdir()
        monkeypatch.setattr(svc.db_git, "get_config", lambda _p: {"enabled": True})
        monkeypatch.setattr(svc, "_is_group_worktree", lambda *_a: True)
        monkeypatch.setattr(svc, "_start_progress_watchdog", lambda *_a, **_k: (None, None))
        monkeypatch.setattr(svc, "_stop_progress_watchdog", lambda *_a, **_k: None)
        monkeypatch.setattr(svc, "_absolute_remaining_sec", lambda _run: 30.0)
        monkeypatch.setattr(oracle, "_work_landed", lambda _run: True)

        # The child must write into the token scratch validated by the inbox route.
        review_env["scratch"] = scratch
        token = inbox_token = {
            "token_id": "tok-review-0393", "project": PROJECT, "issued_to": "user-1",
            "action_scope": "review", "doc_ref": DOC_ID, "scratch_dir": str(scratch),
            "ai_run_id": "air_review_0393", "expires_at": "2036-09-12T00:00:00+09:00",
        }
        from modules.flow_gate.api import inbox_routes
        monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: inbox_token)

        command = subprocess.list2cmdline([sys.executable, str(script)])
        assert KOREAN not in command and EXPECTED["comment"] not in command
        run = {
            "project_id": project_id, "group_id": "flowgate.default.0474",
            "run_id": run_id, "scratch_dir": str(scratch), "source_root": str(worktree),
            "raw_token": "raw", "api_base_url": f"http://127.0.0.1:{server.server_port}",
            "cancel_event": threading.Event(), "fallback_history": [],
            "started_mono": time.monotonic(),
        }
        status, detail = svc._cli_execute(
            {"exec_type": "cli", "kind": "claude", "cli_command": command},
            "PROMPT ✓ 日本語 한글", run,
        )

        assert (status, detail) == ("started_ok", None)
        assert responses == [200, 201]
        assert len(received) == 2
        for envelope in received:
            assert set(envelope) <= {"action", "project", "doc_id", "doc_path", "dry_run", "receipt"}
            assert not ({"content", "verdict", "findings", "comment"} & set(envelope))
            assert Path(envelope["doc_path"]).is_absolute()
            assert Path(envelope["doc_path"]).is_relative_to(scratch)
        payload_path = Path(received[0]["doc_path"])
        expected_bytes = json.dumps(EXPECTED, ensure_ascii=False).encode("utf-8")
        assert payload_path.read_bytes() == expected_bytes
        kwargs = review_env["insert"].call_args.kwargs
        assert kwargs["comment"] == EXPECTED["comment"]
        assert json.loads(kwargs["findings_json"]) == EXPECTED["findings"]
        assert (scratch / prompt_capture.name).read_text(encoding="utf-8") == "PROMPT ✓ 日本語 한글"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
