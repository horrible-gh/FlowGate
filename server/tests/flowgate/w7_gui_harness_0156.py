"""flowgate.v02.0156 T1 W7 — the REAL built GUI on an insecure LAN origin: Designer buttons -> POST /runs 201.

Run directly (not a pytest module: the server test conftest would put the app in test mode):

    python server/tests/flowgate/w7_gui_harness_0156.py <scratch-dir>

What it does, every artifact inside <scratch-dir>/w7gui:
1. GUI: a production build of client_gui (``vite build``, the same bundle `npm run build` emits) with the deployed API
   base ``VITE_API_BASE_URL=/flowgate`` (build.sh's default), written to the scratch directory.
2. Server: the FULL application (``server/routers/main.py``: every router, the real lifespan startup, real JWT auth and
   RBAC, migrations applied at import) on a fresh SQLite file, served by uvicorn on 0.0.0.0 so the same process answers
   at http://<LAN-IP>:<port> (insecure context) and at http://127.0.0.1:<port> (secure, the contrast). The working
   directory is a scratch copy of server/sql (migrations / queries) so nothing is written into the source tree. A thin
   ASGI front serves the scratch build at /app/ exactly as main.py serves client_gui/dist (assets + SPA shell), serves
   the driver page under /harness/, and logs every POST /flows/{id}/runs and /graph/revisions (body, status). Nothing of
   the run contract is stubbed.
3. Data: one worker account (role_worker), project ``flowgate`` and four isolated Flows ``flowgate.v02.93xx``: an
   approved R card -> a person's T card, confirmed as revision 1 through the real graph API, then an unconfirmed draft
   change (the T card's label) so the Designer's own preparation (A6) confirms it before the command.
4. Browser: headless Chrome opens the driver page; it logs in through POST /flowgate/auth/login, seeds the session the
   way the login screen and the Designer leave it (tokens, Designer snapshot with the Flow tab), opens /app/designer in
   a same-origin window and presses the real toolbar button ``[data-test=designer-tool-run][data-command=run.next|all]``
   (Chrome refuses DevTools remote debugging inside the Self-check sandbox, so the page drives the window's DOM).
   It records the window's isSecureContext / crypto.randomUUID / navigator.locks, the run-command keys before and after,
   every toast, the diagnostics accessor and the IndexedDB databases.
5. Checks per case: exactly one POST /runs answered 201 with a v4 command_request_id, one accepted
   graph_run_command_requests row for that id, a run, the draft confirmed first (revision 2), no failure toast, no
   pending record or journal entry left.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode

REPO = Path(__file__).resolve().parents[3]
SERVER = REPO / "server"
CLIENT_GUI = REPO / "client_gui"
CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SERVER_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
START_PATH = re.compile(r"^/flowgate/api/v1/flows/([^/]+)/runs$")
REVISION_PATH = re.compile(r"^/flowgate/api/v1/flows/([^/]+)/graph/revisions$")
USER, PASSWORD = "w7user", "W7-harness-pass-0156!"
PROJECT, MODULE = "flowgate", "v02"
CASES = [("lan", "next", "flowgate.v02.9301"), ("lan", "all", "flowgate.v02.9302"),
         ("local", "next", "flowgate.v02.9303"), ("local", "all", "flowgate.v02.9304")]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def lan_ipv4() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent for a UDP connect
        return s.getsockname()[0]
    finally:
        s.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def log(*parts):
    print(*parts, flush=True)


# ── 1. GUI build ─────────────────────────────────────────────────────────────────────────────────────────────────────
def build_gui(work: Path) -> Path:
    out = work / "dist"
    node = shutil.which("node")
    assert node, "node not found"
    with open(work / "vite-build.log", "w", encoding="utf-8") as fh:
        done = subprocess.run([node, "node_modules/vite/bin/vite.js", "build", "--outDir", str(out), "--emptyOutDir"],
                              cwd=str(CLIENT_GUI), env={**os.environ, "VITE_API_BASE_URL": "/flowgate"},
                              stdout=fh, stderr=subprocess.STDOUT, timeout=900)
    assert done.returncode == 0, (work / "vite-build.log").read_text(encoding="utf-8")[-3000:]
    bundle = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (out / "assets").glob("*.js"))
    facts = {
        "index": sorted(p.name for p in (out / "assets").glob("index-*.js")),
        "api_base_flowgate": '"/flowgate"' in bundle,
        "dev_url_baked": "127.0.0.1:8088" in bundle,
        "runCommand_marker": "flowgate.runCommand.v1" in bundle,
        "lock_db_marker": "flowgate.runCommandLock.v1" in bundle,
        "journal_marker": "flowgate.runCommandOrphan.v1" in bundle,
        "unguarded_randomUUID_call": "(()=>crypto.randomUUID())" in bundle,
    }
    log("GUI_BUILD", json.dumps(facts))
    assert facts["api_base_flowgate"] and not facts["dev_url_baked"] and not facts["unguarded_randomUUID_call"], facts
    (work / "gui_build.json").write_text(json.dumps(facts, indent=1), encoding="utf-8")
    return out


# ── 2. Server ────────────────────────────────────────────────────────────────────────────────────────────────────────
DRIVER = r"""<!doctype html><meta charset="utf-8"><title>0156 W7 driver</title><pre id="log"></pre>
<script>
(async () => {
  const q = new URLSearchParams(location.search)
  const plan = JSON.parse(q.get('plan')), user = q.get('user'), pass = q.get('pass'), next = q.get('next')
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  const report = (o) => fetch('/harness/report', { method: 'POST', body: JSON.stringify({ origin: location.origin, ...o }) })
  try {
    const res = await fetch('/flowgate/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username: user, password: pass, locale: 'ko' }) })
    const login = await res.json()
    if (!res.ok || !login.access_token) throw new Error('login ' + res.status + ' ' + JSON.stringify(login).slice(0, 300))
    for (const c of plan) {
      // What the login screen and an earlier Designer visit leave behind: tokens, locale, the Flow's tab.
      sessionStorage.clear()
      sessionStorage.setItem('fg_access_token', login.access_token)
      sessionStorage.setItem('fg_refresh_token', login.refresh_token)
      localStorage.setItem('preferred_locale', 'ko')
      const group = 'module:' + c.project + ':' + c.module
      sessionStorage.setItem('flowgate.user.' + user + '.designer.session', JSON.stringify({ v: 1, projectId: c.project,
        explorer: { selection: { kind: 'flow', id: c.flow }, expandedGroupIds: [group] },
        tabs: [{ flowId: c.flow, name: c.flow, groupId: group }], activeFlowId: c.flow, leftMode: 'project', rightTool: 'palette' }))
      const w = window.open('/app/designer', 'w7_' + c.flow.replace(/\W/g, '_'))
      if (!w) throw new Error('window.open refused')
      const sel = 'button[data-test="designer-tool-run"][data-command="run.' + c.mode + '"]'
      let btn = null
      for (let i = 0; i < 900 && !btn; i++) {
        await sleep(100)
        try { const b = w.document.querySelector(sel); if (b && !b.disabled && w.document.querySelectorAll('[data-test="designer-canvas-node"]').length >= 2) btn = b } catch (e) {}
      }
      if (!btn) {
        let html = ''
        try { html = w.document.body.innerHTML.slice(0, 4000) } catch (e) {}
        await report({ kind: 'w7_error', flow: c.flow, reason: 'run button never enabled', html })
        w.close(); continue
      }
      const toasts = [], seen = new Set()
      const watch = setInterval(() => {
        try {
          for (const t of w.document.querySelectorAll('.ui-toast')) {
            const text = ((t.querySelector('.ui-toast-content') || t).textContent || '').trim()
            const key = t.className + '|' + text
            if (!seen.has(key)) { seen.add(key); toasts.push({ cls: t.className, text, at: Date.now() }) }
          }
        } catch (e) {}
      }, 50)
      const runKeys = () => Object.keys(w.localStorage).filter((k) => k.startsWith('flowgate.runCommand')).sort()
      const env = { origin: w.location.origin, path: w.location.pathname, isSecureContext: w.isSecureContext,
        randomUUID: typeof (w.crypto && w.crypto.randomUUID), locks: typeof w.navigator.locks, indexedDB: typeof w.indexedDB,
        button: { command: btn.dataset.command, run: btn.dataset.run, text: btn.textContent.trim() } }
      const storageBefore = runKeys()
      const clickedAt = Date.now()
      btn.click()
      let starts = []
      for (let i = 0; i < 300; i++) {
        await sleep(100)
        starts = await (await fetch('/harness/starts?flow=' + encodeURIComponent(c.flow))).json()
        if (starts.some((s) => s.status)) break
      }
      await sleep(3500) // the answer is applied (record cleared, run read, notices shown)
      clearInterval(watch)
      const storageAfter = {}
      for (const k of runKeys()) storageAfter[k] = w.localStorage.getItem(k)
      let idb = null
      try { idb = (await w.indexedDB.databases()).map((d) => d.name) } catch (e) { idb = 'unavailable: ' + e }
      const diagnostics = typeof w.__flowgateRunDiagnostics === 'function' ? w.__flowgateRunDiagnostics() : 'no accessor'
      const buttons = [...w.document.querySelectorAll('button[data-test="designer-tool-run"]')].map((b) => ({ command: b.dataset.command, run: b.dataset.run, disabled: b.disabled, text: b.textContent.trim() }))
      await report({ kind: 'w7', flow: c.flow, mode: c.mode, env, storageBefore, storageAfter, toasts, starts, idb, diagnostics, buttons, clickedAt })
      w.close()
      await sleep(300)
    }
  } catch (e) { await report({ kind: 'page_error', error: String((e && e.stack) || e) }) }
  await report({ kind: 'done' })
  if (next) location.href = next
})()
</script>"""


class Front:
    """ASGI front of the full app: the scratch GUI build at /app/, the driver at /harness/, a log of run commands."""

    def __init__(self, app, dist: Path):
        self.app, self.dist = app, dist.resolve()
        self.reports: list[dict] = []
        self.calls: list[dict] = []
        self.lock = threading.Lock()

    async def _send(self, send, status, body: bytes, ctype: str, extra=()):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype.encode()), (b"cache-control", b"no-cache")] + list(extra)})
        await send({"type": "http.response.body", "body": body})

    async def _body(self, receive) -> bytes:
        chunks = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                return b"".join(chunks)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        if path == "/harness/w7.html":
            return await self._send(send, 200, DRIVER.encode(), "text/html; charset=utf-8")
        if path == "/harness/report" and method == "POST":
            with self.lock:
                self.reports.append(json.loads(await self._body(receive) or b"{}"))
            return await self._send(send, 200, b"{}", "application/json")
        if path == "/harness/starts":
            flow = parse_qs(scope.get("query_string", b"").decode()).get("flow", [""])[0]
            with self.lock:
                rows = [c for c in self.calls if c["kind"] == "start" and c["flow"] == flow]
            return await self._send(send, 200, json.dumps(rows).encode(), "application/json")
        if method == "GET" and (path == "/app" or path.startswith("/app/")):
            # Same rules as main.py _serve_client: real non-HTML files under the build, else the SPA shell.
            rel = path[len("/app/"):] if path.startswith("/app/") else ""
            candidate = (self.dist / rel).resolve() if rel else None
            if candidate and str(candidate).startswith(str(self.dist)) and candidate.is_file() and candidate.suffix != ".html":
                ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                if candidate.suffix == ".js":
                    ctype = "text/javascript"
                return await self._send(send, 200, candidate.read_bytes(), ctype)
            if rel.startswith("assets/"):
                return await self._send(send, 404, b'{"detail":"Not Found"}', "application/json")
            return await self._send(send, 200, (self.dist / "index.html").read_bytes(), "text/html; charset=utf-8")
        if method == "GET" and path == "/flowgate/api/v1/me/ai-roles":
            # A3: the Designer's AI role check before preparing (firstRunSetup -> getMyAiRoles).
            async def send_roles(message):
                if message["type"] == "http.response.start":
                    with self.lock:
                        self.calls.append({"kind": "ai_roles", "flow": None, "status": message["status"], "at": int(time.time() * 1000)})
                await send(message)
            return await self.app(scope, receive, send_roles)
        start, revision = (START_PATH.match(path), REVISION_PATH.match(path)) if method == "POST" else (None, None)
        if not (start or revision):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        entry = {"kind": "start" if start else "revision", "flow": (start or revision).group(1), "body": b"", "status": None,
                 "at": int(time.time() * 1000), "client": headers.get("x-canvas-client-id")}

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
                self.calls.append(entry)


def start_server(work: Path, dist: Path):
    cwd = work / "srv"
    shutil.copytree(SERVER / "sql", cwd / "sql")
    (cwd / "logs").mkdir(parents=True, exist_ok=True)
    db = work / "w7.db"
    os.environ.update({
        "ALLOWED_ORIGIN": "", "SECRET_KEY": "w7-harness-secret-key-0156-not-for-production", "CONTEXT": "/flowgate",
        "DB_TYPE": "sqlite3", "DB_PATH": str(db), "AUTO_MIGRATION": "true", "DB_LOG": "false",
        "FLOWGATE_STORAGE_DIR": str(work / "storage"), "FLOWGATE_TOKEN_PEPPER_ACTIVE_ID": "w7harness",
        "FLOWGATE_TOKEN_PEPPER_w7harness": "w7-harness-token-pepper-0156", "RATE_LIMIT_DEFAULT": "100000/hour",
    })
    os.environ.pop("TESTING", None)
    # The Self-check sandbox has no account variables; pymysql (imported by sqloader) calls getpass.getuser() at import.
    for name in ("USERNAME", "USER", "LOGNAME"):
        os.environ.setdefault(name, "w7harness")
    os.chdir(cwd)  # config.py resolves sql/queries, sql/migrations and logs/ against the working directory
    sys.path.insert(0, str(SERVER))
    import uvicorn
    from routers.main import app  # migrations run here, on the fresh scratch SQLite

    front = Front(app, dist)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(front, host="0.0.0.0", port=port, log_level="warning", access_log=False,
                                           lifespan="on", timeout_graceful_shutdown=2))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(600):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "uvicorn did not start"
    return front, server, thread, port, db


# ── 3. Data ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def http(method: str, url: str, body=None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json", "x-canvas-client-id": "w7_seed"}
    if token:
        headers["authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read()
        try:
            return e.code, json.loads(text or b"{}")
        except ValueError:
            return e.code, {"raw": text[:500].decode(errors="replace")}


def seed(db: Path, base: str) -> dict:
    from modules.flow_gate.auth.password import hash_password
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    now = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid.uuid4())
    con = sqlite3.connect(db)
    con.execute("INSERT INTO users (user_id, username, email, password, is_active, is_admin, first_login_required, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, 0, 0, ?, ?)", (user_id, USER, USER + "@w7.invalid", hash_password(PASSWORD), now, now))
    con.execute("INSERT OR REPLACE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?, '__SYSTEM__', 'role_worker', ?)", (user_id, now))
    con.commit()
    con.close()
    db_projects.create({"project_id": PROJECT, "project_name": PROJECT})
    status, login = http("POST", base + "/flowgate/auth/login", {"username": USER, "password": PASSWORD, "locale": "ko"})
    assert status == 200 and login.get("access_token"), (status, login)
    token = login["access_token"]
    human = {"kind": "human", "user_id": user_id, "provider_id": None}
    drafts = {}
    for _, _, flow in CASES:
        db_groups.create({"group_id": flow, "project_id": PROJECT, "module": MODULE, "title": flow, "status": "OPEN"})
        doc_id = f"{flow}.0001-R"
        db_documents.create({"doc_id": doc_id, "project_id": PROJECT, "module": MODULE, "group_id": flow, "type_code": "R",
                             "seq": 1, "title": "R 1", "status": "open"})
        db_documents.update(doc_id, {"doc_review_status": "approved"})
        nodes = [
            {"node_key": None, "client_key": "tmp_r", "node_type": "document", "doc_type": "R", "palette_item_id": None, "label": "R", "assignee": None},
            {"node_key": None, "client_key": "tmp_t", "node_type": "document", "doc_type": "T", "palette_item_id": None, "label": "T", "assignee": human},
        ]
        edges = [{"edge_id": None, "client_key": "tmp_e", "source": "tmp_r", "target": "tmp_t", "condition": None}]
        status, saved = http("PUT", f"{base}/flowgate/api/v1/flows/{flow}/graph/draft", {"draft_version": 0, "nodes": nodes, "edges": edges}, token)
        assert status == 200, (flow, status, saved)
        status, confirmed = http("POST", f"{base}/flowgate/api/v1/flows/{flow}/graph/revisions", {"draft_version": 1}, token)
        assert status == 201, (flow, status, confirmed)
        drafts[flow] = pending_draft(base, flow, token)
    return {"user_id": user_id, "drafts": drafts}


def _unwrap(payload):
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def pending_draft(base: str, flow: str, token: str) -> dict:
    """An unconfirmed change on top of revision 1 (the T card's label), for the Designer's own A6 confirmation.

    A confirmed revision leaves no draft (``draft`` null): a new one is created with draft_version 0 from the revision's
    nodes and edges (graph_service.write_draft)."""
    status, graph = http("GET", f"{base}/flowgate/api/v1/flows/{flow}/graph", None, token)
    data = _unwrap(graph) if status == 200 else {}
    draft = data.get("draft") or {}
    source = draft if draft.get("nodes") else (data.get("latest_revision") or {})
    version = draft.get("draft_version", 0) if draft else 0
    nodes, edges = source.get("nodes"), source.get("edges")
    if not isinstance(nodes, list) or not nodes:
        return {"seeded": False, "reason": f"graph read {status}", "keys": sorted(data.keys()) if isinstance(data, dict) else None}

    def assignee(n):
        if isinstance(n.get("assignee"), dict) or n.get("assignee") is None and "assignee" in n:
            return n.get("assignee")
        kind = n.get("assignee_kind")
        return {"kind": kind, "user_id": n.get("assignee_user_id"), "provider_id": n.get("assignee_provider_id")} if kind else None

    changed = [{"node_key": n.get("node_key"), "client_key": None, "node_type": n.get("node_type"), "doc_type": n.get("doc_type"),
                "palette_item_id": n.get("palette_item_id"), "label": "T (W7 draft)" if n.get("doc_type") == "T" else n.get("label"),
                "assignee": assignee(n)} for n in nodes]
    body = {"draft_version": version, "nodes": changed,
            "edges": [{"edge_id": e.get("edge_id"), "client_key": None, "source": e.get("source"), "target": e.get("target"),
                       "condition": e.get("condition")} for e in (edges or [])]}
    status, saved = http("PUT", f"{base}/flowgate/api/v1/flows/{flow}/graph/draft", body, token)
    out = _unwrap(saved) if isinstance(saved, dict) else {}
    return {"seeded": status == 200, "status": status, "from_version": version,
            "draft_version": out.get("draft_version") if status == 200 and isinstance(out, dict) else None,
            "error": None if status == 200 else saved}


def facts(db: Path, flow: str) -> dict:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        q = lambda sql: [dict(r) for r in con.execute(sql, (flow,)).fetchall()]  # noqa: E731
        return {
            "rows": q("SELECT command_request_id, state, command_seq, cause FROM graph_run_command_requests WHERE group_id=? ORDER BY created_at"),
            "runs": q("SELECT run_id, status, idle_reason, revision_no FROM graph_runs WHERE group_id=?"),
            "events": [r["cause"] for r in q("SELECT e.cause FROM graph_run_events e JOIN graph_runs r ON r.run_id=e.run_id WHERE r.group_id=? AND e.cause IN ('user_start','user_command')")],
            "revisions": [r["revision_no"] for r in q("SELECT revision_no FROM graph_revisions WHERE group_id=? ORDER BY revision_no")],
        }
    finally:
        con.close()


# ── 4. Browser ───────────────────────────────────────────────────────────────────────────────────────────────────────
def browse(work: Path, first_url: str, front: Front, expect_done: int, budget: float):
    profile = work / "chrome"
    env = dict(os.environ)
    for name, sub in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming"), ("TEMP", "temp"), ("TMP", "temp"), ("USERPROFILE", "user")):
        env[name] = str(profile / f"env-{sub}")
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    with open(work / "chrome.log", "w", encoding="utf-8") as fh:
        proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run", "--no-default-browser-check",
                                 "--disable-extensions", "--disable-popup-blocking", "--disable-background-timer-throttling",
                                 "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
                                 "--window-size=1600,1000", f"--user-data-dir={profile / 'data'}", first_url],
                                env=env, stdout=fh, stderr=subprocess.STDOUT)
        end = time.time() + budget
        while time.time() < end:
            if len([r for r in front.reports if r.get("kind") == "done"]) >= expect_done:
                break
            time.sleep(0.5)
        proc.kill()
        proc.wait(timeout=30)


def main() -> int:
    scratch = Path(sys.argv[1]).resolve()
    work = scratch / "w7gui"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    assert Path(CHROME).exists(), CHROME
    dist = build_gui(work)
    front, server, thread, port, db = start_server(work, dist)
    host = lan_ipv4()
    assert not host.startswith("127."), host
    base = f"http://127.0.0.1:{port}"
    seeded = seed(db, base)
    log("SEEDED", json.dumps(seeded["drafts"]))
    plans = {o: [{"flow": g, "mode": m, "project": PROJECT, "module": MODULE} for oo, m, g in CASES if oo == o] for o in ("lan", "local")}
    origin = {"lan": f"http://{host}:{port}", "local": f"http://127.0.0.1:{port}"}
    url = lambda o, nxt=None: origin[o] + "/harness/w7.html?" + urlencode({"plan": json.dumps(plans[o]), "user": USER, "pass": PASSWORD, **({"next": nxt} if nxt else {})})  # noqa: E731
    browse(work, url("lan", url("local")), front, expect_done=2, budget=420)
    reports = list(front.reports)
    calls = list(front.calls)
    (work / "w7_gui_raw.json").write_text(json.dumps({"reports": reports, "calls": calls}, ensure_ascii=False, indent=1), encoding="utf-8")
    by_flow = {r["flow"]: r for r in reports if r.get("kind") == "w7"}
    problems = [r for r in reports if r.get("kind") in ("w7_error", "page_error")]
    result, failures = [], []
    for o, mode, flow in CASES:
        item = by_flow.get(flow)
        if not item:
            failures.append(f"{flow}: no measurement")
            continue
        db_facts = facts(db, flow)
        starts = [c for c in calls if c["kind"] == "start" and c["flow"] == flow]
        # The Designer's own confirmation (A6): revision POSTs after the click (the seed confirmed revision 1 earlier).
        revisions = [c for c in calls if c["kind"] == "revision" and c["flow"] == flow and c["at"] >= item["clickedAt"]]
        body = starts[0]["body"] if starts else {}
        cid = body.get("command_request_id")
        danger = [t for t in item["toasts"] if "ui-toast--danger" in t["cls"]]
        left = [k for k in item["storageAfter"] if k.startswith("flowgate.runCommand.v1.") or k.startswith("flowgate.runCommandOrphan.v1.")]
        failures_here = []
        env = item["env"]
        if env["isSecureContext"] is not (o == "local"):
            failures_here.append("isSecureContext")
        if o == "lan" and (env["randomUUID"] != "undefined" or env["locks"] != "undefined"):
            failures_here.append("lan context still has randomUUID/locks")
        if [k for k in item["storageBefore"] if not k.startswith("flowgate.runCommandLock.v1.")]:
            failures_here.append("pending record before the click")
        if [s["status"] for s in starts] != [201]:
            failures_here.append(f"start statuses {[s['status'] for s in starts]}")
        if body.get("mode") != ("step" if mode == "next" else "full"):
            failures_here.append(f"mode {body.get('mode')}")
        if not (cid and SERVER_V4.match(cid)):
            failures_here.append(f"command_request_id {cid}")
        if [r["command_request_id"] for r in db_facts["rows"]] != [cid] or db_facts["rows"][0]["state"] != "accepted":
            failures_here.append(f"registry {db_facts['rows']}")
        if len(db_facts["runs"]) != 1:
            failures_here.append(f"runs {db_facts['runs']}")
        if danger:
            failures_here.append(f"failure toast {danger}")
        if left:
            failures_here.append(f"left after success {left}")
        a6 = seeded["drafts"].get(flow, {})
        if a6.get("seeded") and not (revisions and [r["status"] for r in revisions] == [201] and revisions[0]["at"] <= starts[0]["at"] and db_facts["revisions"] == [1, 2]):
            failures_here.append(f"draft not confirmed before the command: {[(r['status'], r['at']) for r in revisions]} revisions={db_facts['revisions']}")
        roles = [c for c in calls if c["kind"] == "ai_roles" and item["clickedAt"] <= c["at"] <= (starts[0]["at"] if starts else c["at"])]
        if not roles or roles[0]["status"] != 200:
            failures_here.append(f"A3 AI role check not seen between click and command: {roles}")
        result.append({"origin": env["origin"], "mode": mode, "flow": flow, "page": env["path"], "button": env["button"],
                       "ai_role_check": [{"status": r["status"], "ms_before_start": starts[0]["at"] - r["at"] if starts else None} for r in roles],
                       "isSecureContext": env["isSecureContext"], "randomUUID": env["randomUUID"], "locks": env["locks"],
                       "indexedDB": env["indexedDB"], "storage_before": item["storageBefore"],
                       "draft_confirm": {"seeded": a6.get("seeded"), "gui_posts": [{"status": r["status"], "body": r["body"], "ms_before_start": starts[0]["at"] - r["at"] if starts else None} for r in revisions], "revisions": db_facts["revisions"]},
                       "post_status": [s["status"] for s in starts], "request_body": body, "db_rows": db_facts["rows"],
                       "db_runs": db_facts["runs"], "db_events": db_facts["events"], "toasts": item["toasts"],
                       "storage_after": item["storageAfter"], "idb_databases": item["idb"], "buttons_after": item["buttons"],
                       "diagnostics": item["diagnostics"], "failures": failures_here})
        failures += [f"{flow}: {f}" for f in failures_here]
    (work / "w7_gui_report.json").write_text(json.dumps({"cases": result, "problems": problems, "failures": failures}, ensure_ascii=False, indent=1), encoding="utf-8")
    log("LAN_W7_GUI", json.dumps(result, ensure_ascii=False))
    log("LAN_W7_GUI_PROBLEMS", json.dumps(problems, ensure_ascii=False)[:4000])
    log("LAN_W7_GUI_FAILURES", json.dumps(failures, ensure_ascii=False))
    server.should_exit = True
    thread.join(timeout=10)
    return 0 if not failures and len(result) == len(CASES) else 1


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except BaseException as exc:  # report, then leave without waiting on stray threads (SSE)
        import traceback
        traceback.print_exc()
        code = 2
    sys.stdout.flush()
    os._exit(code)
