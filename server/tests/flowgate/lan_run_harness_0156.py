"""flowgate.v02.0156 T1 W6 (R4-B) / W7 — a real browser on an insecure LAN origin against a real graph run server.

Not collected by default (no ``test_`` file prefix): it launches Vite and headless Chrome. Run it explicitly, with the
pytest temporary root inside the work scratch directory:

    pytest -q -s server/tests/flowgate/lan_run_harness_0156.py --basetemp=<scratch>/pt [-k w7|r4b_default|r4b_prefix]

Server: the same isolated app as the 0149 server suites (``test_fg_card_settings.env``: all migrations on a fresh SQLite
file, the real graph / run / canvas routes, auth and RBAC stubbed, the AI starter recorded instead of launched), served by
uvicorn on this host's LAN IPv4 behind CORS. Nothing of the run command contract is stubbed: the
``graph_run_command_requests`` registry, the Flow guard and ``run_service.start_run`` answer the browser. Every
``POST /flows/{id}/runs`` is logged here (tab, phase, body, status).

Browser: client_gui/tests/browser/run-lan.0156.html served by Vite on 0.0.0.0 and opened by headless Chrome at
http://<LAN-IP>:<port> (insecure context: no crypto.randomUUID, no navigator.locks) and at http://127.0.0.1:<port> (secure,
the contrast). Chrome refuses DevTools remote debugging inside the Self-check sandbox, so the pages drive themselves
through the ``/harness/*`` endpoints below (next step, click barrier, reports).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import pytest

from test_fg_card_settings import _doc, _h, _sql, db_path, decided, edge, env, legacy_cards, node, put_draft  # noqa: F401
from test_fg_revision_adoption_precheck_0010 import document_write_ports  # noqa: F401

pytestmark = pytest.mark.usefixtures("legacy_cards")

SERVER_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
START_PATH = re.compile(r"^/api/v1/flows/([^/]+)/runs$")
CLIENT_GUI = Path(__file__).resolve().parents[3] / "client_gui"
PAGE = "/app/tests/browser/run-lan.0156.html"
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
HUMAN = {"kind": "human", "user_id": "u_lee", "provider_id": None}
R4B_ITERATIONS = 30


def lan_ipv4() -> str:
    """The routable IPv4 of this host (127.0.0.1 is a secure context and would hide the defect)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent for a UDP connect
        ip = s.getsockname()[0]
    finally:
        s.close()
    if ip.startswith("127."):
        pytest.skip("no non-loopback IPv4 interface")
    return ip


def free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def flow(env, group_id: str, idle_run: bool = False) -> str:
    """An R (approved) -> T (person) Flow confirmed as revision 1; with ``idle_run`` its run exists and is idle (step_done)."""
    from modules.flow_gate.db import groups as db_groups

    db_groups.create({"group_id": group_id, "project_id": "flowgate", "module": "v02", "title": group_id, "status": "OPEN"})
    _doc(group_id, 1, "R", "approved")
    put_draft(env, group_id, 0, [node("tmp_r", "R"), node("tmp_t", "T", assignee=HUMAN)], [edge("tmp_e", "tmp_r", "tmp_t")])
    confirmed = env["client"].post(f"/api/v1/flows/{group_id}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    if idle_run:
        started = env["client"].post(f"/api/v1/flows/{group_id}/runs", json={"mode": "step"}, headers=_h())
        assert started.status_code == 201, started.text
        _sql(env["db"], "UPDATE graph_runs SET status='idle', idle_reason='step_done' WHERE group_id=?", (group_id,))
    return group_id


def facts(env, group_id: str) -> dict:
    rows = _sql(env["db"], "SELECT command_request_id, state, command_seq, cause FROM graph_run_command_requests WHERE group_id=? ORDER BY created_at", (group_id,))
    events = _sql(env["db"], "SELECT e.cause FROM graph_run_events e JOIN graph_runs r ON r.run_id=e.run_id WHERE r.group_id=? AND e.cause IN ('user_start','user_command')", (group_id,))
    runs = _sql(env["db"], "SELECT run_id, status, idle_reason FROM graph_runs WHERE group_id=?", (group_id,))
    attempts = _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_node_attempts a JOIN graph_runs r ON r.run_id=a.run_id WHERE r.group_id=?", (group_id,))
    return {"rows": rows, "events": [e["cause"] for e in events], "runs": runs, "attempts": attempts[0]["n"]}


class Coordinator:
    """ASGI front of the graph app: the pages' /harness/* steps, and a log of every start POST (body and status)."""

    def __init__(self, app, queues: dict[str, list[str]], tabs: set[str]):
        self.app, self.queues, self.tabs = app, queues, tabs
        self.reports: list[dict] = []
        self.starts: list[dict] = []
        self.barriers: dict[str, dict] = {}
        self.lock = threading.Lock()

    async def _json(self, send, payload, status=200):
        body = json.dumps(payload).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path.startswith("/harness/"):
            q = {k: v[0] for k, v in parse_qs(scope.get("query_string", b"").decode()).items()}
            if path == "/harness/report":
                chunks = []
                while True:
                    message = await receive()
                    chunks.append(message.get("body", b""))
                    if not message.get("more_body"):
                        break
                with self.lock:
                    self.reports.append(json.loads(b"".join(chunks) or b"{}"))
                return await self._json(send, {"ok": True})
            if path == "/harness/next":
                with self.lock:
                    queue = self.queues.get(q.get("tab", ""), [])
                    url = queue.pop(0) if queue else None
                return await self._json(send, {"url": url})
            if path == "/harness/barrier":
                with self.lock:
                    b = self.barriers.setdefault(q.get("iter", ""), {"tabs": set(), "at": None})
                    b["tabs"].add(q.get("tab"))
                    if b["at"] is None and b["tabs"] >= self.tabs:
                        b["at"] = int(time.time() * 1000) + 600
                    at = b["at"]
                return await self._json(send, {"at": at})
            return await self._json(send, {"error": "unknown"}, 404)
        match = START_PATH.match(path) if scope["method"] == "POST" else None
        if not match:
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        entry = {"flow": match.group(1), "tab": headers.get("x-harness-tab"), "phase": headers.get("x-harness-phase"),
                 "body": b"", "status": None}

        async def receive_logged():
            message = await receive()
            entry["body"] += message.get("body", b"")
            return message

        async def send_logged(message):
            if message["type"] == "http.response.start":
                entry["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive_logged, send_logged)
        finally:
            try:
                entry["body"] = json.loads(entry["body"] or b"{}")
            except ValueError:
                entry["body"] = {}
            with self.lock:
                self.starts.append(entry)


class Harness:
    def __init__(self, env, tmp_path: Path, tabs: set[str]):
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware

        self.tmp = tmp_path
        self.host = lan_ipv4()
        self.port = free_port(self.host)
        self.vite_port = free_port("0.0.0.0")
        self.queues: dict[str, list[str]] = {}
        self.coordinator = Coordinator(env["client"].app, self.queues, tabs)
        app = CORSMiddleware(self.coordinator, allow_origin_regex=rf"http://(127\.0\.0\.1|localhost|{re.escape(self.host)}):\d+",
                             allow_credentials=True, allow_methods=["*"], allow_headers=["*"], expose_headers=["x-request-id"])
        self.server = uvicorn.Server(uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", access_log=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.procs: list[subprocess.Popen] = []

    @property
    def api(self) -> str:
        return f"http://{self.host}:{self.port}"

    def page(self, origin: str, **params) -> str:
        host = "127.0.0.1" if origin == "local" else self.host
        return f"http://{host}:{self.vite_port}{PAGE}?" + urlencode({"harness": self.api, **params})

    def __enter__(self):
        node_exe = shutil.which("node")
        if not node_exe or not Path(CHROME).exists() or not (CLIENT_GUI / "node_modules" / "vite").exists():
            pytest.skip(f"browser harness unavailable (node={node_exe}, chrome={Path(CHROME).exists()})")
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.1)
        vite_log = open(self.tmp / "vite.log", "w", encoding="utf-8")
        self.procs.append(subprocess.Popen([node_exe, "node_modules/vite/bin/vite.js", "--host", "0.0.0.0", "--port", str(self.vite_port), "--strictPort"],
                                           cwd=str(CLIENT_GUI), env={**os.environ, "VITE_API_BASE_URL": self.api}, stdout=vite_log, stderr=subprocess.STDOUT))
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(300):
            try:
                with opener.open(self.page("lan"), timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("Vite did not answer: " + (self.tmp / "vite.log").read_text(encoding="utf-8")[-1500:])
        return self

    def browse(self, first_url: str, expect_done: int, budget: float) -> list[dict]:
        """Headless Chrome on the first page (a page may open the second tab itself); waits for `expect_done` done reports."""
        profile = self.tmp / "chrome"
        chrome_env = dict(os.environ)
        for name, sub in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming"), ("TEMP", "temp"), ("TMP", "temp")):
            chrome_env[name] = str(profile / f"env-{sub}")
            Path(chrome_env[name]).mkdir(parents=True, exist_ok=True)
        chrome_log = open(self.tmp / "chrome.log", "w", encoding="utf-8")
        proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-default-browser-check",
                                 "--disable-extensions", "--disable-popup-blocking", "--disable-background-timer-throttling",
                                 "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                                 f"--user-data-dir={profile / 'data'}", first_url], env=chrome_env, stdout=chrome_log, stderr=subprocess.STDOUT)
        self.procs.append(proc)
        end = time.time() + budget
        while time.time() < end:
            done = [r for r in self.coordinator.reports if r.get("kind") == "done"]
            if len(done) >= expect_done:
                break
            time.sleep(0.5)
        proc.kill()
        return list(self.coordinator.reports)

    def __exit__(self, *exc):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
        self.server.should_exit = True
        self.thread.join(timeout=10)
        time.sleep(0.5)
        shutil.rmtree(self.tmp / "chrome", ignore_errors=True)


def test_w7_lan_post_runs_201(env, decided, tmp_path):
    cases = [("lan", "next", "flowgate.v02.9001"), ("lan", "all", "flowgate.v02.9002"),
             ("local", "next", "flowgate.v02.9003"), ("local", "all", "flowgate.v02.9004")]
    for _, _, group in cases:
        flow(env, group)
    with Harness(env, tmp_path, {"W"}) as h:
        urls = [h.page(o, flow=g, mode=m, tab="W", phase="w7") for o, m, g in cases]
        h.queues["W"] = urls[1:]
        reports = h.browse(urls[0], expect_done=1, budget=180)
        starts = list(h.coordinator.starts)
    w7 = [r for r in reports if r.get("kind") == "w7"]
    (tmp_path / "w7_raw.json").write_text(json.dumps({"reports": reports, "starts": starts}, ensure_ascii=False, indent=1), encoding="utf-8")
    assert len(w7) == len(cases), f"the browser measured {len(w7)} of {len(cases)} cases: {[r.get('kind') for r in reports]}"
    report = []
    for (origin, mode, group), item in zip(cases, w7):
        assert item["flow"] == group
        db = facts(env, group)
        posts = [s for s in starts if s["flow"] == group]
        body = posts[0]["body"] if posts else {}
        cid = body.get("command_request_id")
        result, after = item["result"], item["after"]
        report.append({"origin": item["env"]["origin"], "mode": mode, "flow": group, "isSecureContext": item["env"]["isSecureContext"],
                       "randomUUID": item["env"]["randomUUID"], "locks": item["env"]["locks"], "storage_before": item["storageBefore"],
                       "post_status": [p["status"] for p in posts], "request_body": body, "result_ok": result["ok"], "toast": result["toast"],
                       "storage_after": after["storage"], "db_rows": db["rows"], "db_events": db["events"], "db_runs": db["runs"]})
        assert item["env"]["isSecureContext"] is (origin == "local")
        if origin == "lan":
            assert item["env"]["randomUUID"] == "undefined" and item["env"]["locks"] == "undefined"
        # No pending command record before the click (the opening lookup may still hold its short cross-tab lease key).
        assert not [k for k in item["storageBefore"] if not k.startswith("flowgate.runCommandLock.v1.")], item["storageBefore"]
        assert [p["status"] for p in posts] == [201], posts
        assert body.get("mode") == ("step" if mode == "next" else "full")
        assert cid and SERVER_V4.match(cid), body
        assert [r["command_request_id"] for r in db["rows"]] == [cid] and db["rows"][0]["state"] == "accepted"
        assert len(db["runs"]) == 1
        assert result["ok"] is True and result["toast"] is None and after["error"] is None
        assert not [k for k in after["storage"] if k.startswith("flowgate.runCommand.v1.")]
    print("LAN_W7", json.dumps(report, ensure_ascii=False))
    (tmp_path / "w7_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def _r4b(env, tmp_path, inject: bool) -> dict:
    plan = []
    for i in range(R4B_ITERATIONS):
        plan.append({"scenario": 1, "flow": flow(env, f"flowgate.v02.{9101 + i}")})
        plan.append({"scenario": 2, "flow": flow(env, f"flowgate.v02.{9201 + i}", idle_run=True)})
    baseline = {p["flow"]: facts(env, p["flow"]) for p in plan}
    extra = {"inject": "uuid"} if inject else {}
    with Harness(env, tmp_path, {"A", "B"}) as h:
        urls = {tab: [h.page("lan", flow=p["flow"], tab=tab, phase="click", iter=str(n), lose="1" if tab == "A" else "0", **extra)
                      for n, p in enumerate(plan)] for tab in ("A", "B")}
        h.queues["A"], h.queues["B"] = urls["A"][1:], urls["B"][1:]
        first = urls["A"][0] + "&" + urlencode({"spawn": urls["B"][0]})
        reports = h.browse(first, expect_done=2, budget=1200)
        starts = list(h.coordinator.starts)
    (tmp_path / f"r4b_raw_{'prefix' if inject else 'default'}.json").write_text(json.dumps({"reports": reports, "starts": starts}, indent=1), encoding="utf-8")
    by = {(r.get("iter"), r.get("tab"), r.get("kind")): r for r in reports}
    iterations, totals = [], {"a": 0, "b": 0, "c": 0, "d": 0, "e": 0, "violating": 0}
    incomplete = []
    for n, p in enumerate(plan):
        k = str(n)
        got = {kind: {tab: by.get((k, tab, kind)) for tab in ("A", "B")} for kind in ("click", "f5")}
        if not all(got[kind][tab] for kind in got for tab in ("A", "B")):
            incomplete.append(n)
            continue
        before, db = baseline[p["flow"]], facts(env, p["flow"])
        known = {r["command_request_id"] for r in before["rows"]}
        accepted = [r["command_request_id"] for r in db["rows"] if r["state"] == "accepted" and r["command_request_id"] not in known]
        new_events = len(db["events"]) - len(before["events"])
        posts = [s for s in starts if s["flow"] == p["flow"]]
        resent = [s for s in posts if s["phase"] == "f5"]
        sent = sorted({s["body"].get("command_request_id") for s in posts if s["phase"] == "click"})
        confirmed = set()
        for kind in ("click", "f5"):
            for tab in ("A", "B"):
                snap = got[kind][tab]["after"]
                confirmed.update(snap["confirmed"])
                confirmed.update(s["id"] for s in snap["starts"] if s["status"] == 201 and not s["dropped"] and s["id"])
        stored = set()
        for tab in ("A", "B"):
            for key, value in got["f5"][tab]["after"]["storage"].items():
                data = json.loads(value)
                if key.startswith("flowgate.runCommand.v1."):
                    stored.add(data["commandRequestId"])
                elif key.startswith("flowgate.runCommandOrphan.v1."):
                    # 0156 rework: one journal key per identity (<scope prefix><commandRequestId>).
                    stored.add(data["commandRequestId"])
        lost = [c for c in accepted if c not in confirmed and c not in stored]
        v = {"a": bool(lost), "b": any(c not in confirmed for c in accepted) or bool(resent),
             "c": not (len(accepted) == new_events and len(accepted) <= 2), "d": new_events > 2 or bool(resent),
             "e": len(env["jobs"]) > 0}
        for key in ("a", "b", "c", "d", "e"):
            totals[key] += int(v[key])
        totals["violating"] += int(any(v.values()))
        clicks = {tab: (got["click"][tab]["after"]["clicks"] or [{}])[-1] for tab in ("A", "B")}
        iterations.append({"scenario": p["scenario"], "flow": p["flow"], "sent": sent, "accepted": accepted, "confirmed": sorted(confirmed),
                           "stored": sorted(stored), "lost": lost, "resent": len(resent), "events": new_events,
                           "attempts": db["attempts"] - before["attempts"], "violations": v,
                           "dropped": [s for s in got["click"]["A"]["after"]["starts"] if s["dropped"]],
                           "clicks": {tab: {"ok": c.get("ok"), "code": (c.get("error") or {}).get("code"), "outcome": (c.get("error") or {}).get("outcome")}
                                      for tab, c in clicks.items()}})
    env_info = next((r["env"] for r in reports if r.get("kind") == "click"), {})
    summary = {"inject": inject, "planned": len(plan), "measured": len(iterations), "incomplete": incomplete[:10], "per_scenario": R4B_ITERATIONS,
               "env": env_info, **totals, "dropped_answers": sum(1 for i in iterations if i["dropped"]),
               "same_id_both_tabs": sum(1 for i in iterations if len(i["sent"]) == 1), "distinct_ids": sum(1 for i in iterations if len(i["sent"]) == 2),
               "page_errors": [r for r in reports if r.get("kind") in ("page_error", "barrier_timeout")][:5]}
    (tmp_path / f"r4b_{'prefix' if inject else 'default'}.json").write_text(json.dumps({"summary": summary, "iterations": iterations}, indent=1), encoding="utf-8")
    print("LAN_R4B", json.dumps(summary))
    print("LAN_R4B_VIOLATIONS", json.dumps([i for i in iterations if any(i["violations"].values())][:5]))
    return summary


def test_r4b_default_wiring(env, decided, tmp_path):
    """The production wiring on the insecure origin: cross-tab lease + compare-and-set + orphan lookup."""
    summary = _r4b(env, tmp_path, inject=False)
    assert summary["measured"] == 2 * R4B_ITERATIONS, summary
    assert summary["env"].get("isSecureContext") is False
    assert summary["violating"] == 0, summary


def test_r4b_prefix_wiring(env, decided, tmp_path):
    """Measurement: the pre-0156 wiring (no lock on the insecure origin; a v4 source injected to keep R1 apart)."""
    summary = _r4b(env, tmp_path, inject=True)
    assert summary["measured"] == 2 * R4B_ITERATIONS, summary
