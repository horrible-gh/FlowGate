"""T-c real Chromium -> production Vue components -> real HTTP archive/restore -> SQLite.
Run through TR Self-check with --basetemp pointing into FLOWGATE_SCRATCH.
Authentication is a fixture; archive, tree, run, gate decision and persistence are real.
"""
from pathlib import Path
import json
import socket
import subprocess
import threading
import time

from fastapi.staticfiles import StaticFiles
import uvicorn
from test_flow_archive_v02test_0002 import env, seed_run, seed_decision, sql, FLOW


def test_browser_archive_restore_and_mockup(env, tmp_path):
    from modules.flow_gate.api.v1 import tree_routes
    seed_run(env)
    seed_decision(env)
    preserved = {table: sql(env, f"SELECT * FROM {table}") for table in ["documents", "graph_runs", "gate_decisions"]}
    out = tmp_path / "browser"
    out.mkdir()
    app = env["client"].app
    app.include_router(tree_routes.router)
    app.mount("/visual", StaticFiles(directory=str(out)), name="visual")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started
        client_dir = Path(__file__).resolve().parents[4] / "client_gui"
        result = subprocess.run(["node", "tests/visual/flowArchive/measure.mjs", str(out), base_url],
                                cwd=client_dir, capture_output=True, text=True, encoding="utf-8", timeout=180)
        (out / "runner.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
        measured = json.loads((out / "results.json").read_text(encoding="utf-8"))
        assert measured["actions"] == ["archive", "restore"]
        assert measured["adjacent"] and measured["normalTreeHidden"] and measured["restoredTerminalVisible"]
        assert measured["idleRunButtons"] == 0
        row = sql(env, "SELECT archived_at,archived_by,archive_restored_at FROM groups WHERE group_id=?", [FLOW])[0]
        assert row["archived_at"] is None and row["archived_by"] is None and row["archive_restored_at"]
        assert len(env["events"]) == 2
        assert {table: sql(env, f"SELECT * FROM {table}") for table in preserved} == preserved
        for spy in env["forbidden"]:
            spy.assert_not_called()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
