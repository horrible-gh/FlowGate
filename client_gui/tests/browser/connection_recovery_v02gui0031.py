"""flowgate.v02gui.0031 T0005 §3/§5 — isolated connection-recovery scenarios (Playwright, no real server touched).

Run (cwd client_gui, after `npm ci`):  python tests/browser/connection_recovery_v02gui0031.py

The product chat runs unchanged in headless Chrome (connection-recovery.v02gui0031.html/.ts: ConversationPanel, its HTTP
source, the shared axios client with token refresh, the project event hub, and the Designer's own read functions). The
FlowGate server is the fake below, an in-process HTTP server with a real streaming SSE endpoint, two Flows (groups) with
their chats, idempotent turn storage, AI runs that can be held in progress, and the Designer reads (Flow list, graph /
cards, Inspector document and settings, Git status). Failures are injected per scenario: network offline (Playwright),
502/503/504, a hung server (client timeout), the SSE stream alone, a token refresh failing during the outage, a turn
stored whose answer is lost, an outage while an AI run is in progress. Scenarios are repeated in another chat of the
same Flow and in another Flow, with chat and Flow switches during the outage. Every scenario checks that chat reads,
the event stream, the Designer reads and a new send come back WITHOUT a page reload, that the recovery itself sent no
AI start / stop request and resent no message, and records one row per phase (time, request kinds/status, SSE state,
turnsStatus/sending/runId, run history, Designer reads). F5 is compared in both Flows and while a run is in progress.
Every request is also logged one by one on one clock (NR0003 §6), from both ends: the browser's view
(connection-recovery.v02gui0031.netlog.js: start, method, path with the query reduced to known keys, HTTP status or
failure kind - offline / network / timeout / abort / stream error - and duration, including requests the offline
browser never sent and client timeouts) and the fake server's view (start, method, path, status or `hang` /
`stored_then_reset`, duration). Each row carries the scenario, the step (the last recorded phase) and the failure
injected at that moment. The merged log goes to report.json (`requests`) and requests.tsv; checks assert that it holds
no token / body, that every response the browser saw has its server row, and that every injected failure left its
per-request evidence.
The store is checked at the end: one row per message (no new idempotency key on a retry), exactly one start request per
AI press (no resend of a start), one AI run per accepted message, no stop request. Tokens and message bodies are never
printed: rows carry token generations and message labels only.
Prints `RECORDS <json>`, `REQUESTS_BY_STEP <json>` and `CHECKS <json>`; exit code 0 = every check passed.
"""
from __future__ import annotations

import base64
import bisect
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

CLIENT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
PROJECT = 'p'
F1, F2 = 'p.m.0001', 'p.m.0002'
CHAT, CHAT_B, CHAT_C = f'{F1}.0002-CH', f'{F1}.0003-CH', f'{F2}.0002-CH'  # A and B in Flow 1, C in Flow 2
CHATS = {F1: [CHAT, CHAT_B], F2: [CHAT_C]}
TOKEN_LIFE_S = 25  # the client rotates at exp - 15s: every ~10s
RUN_POLL_S = 2.5  # conversationSession RUN_POLL_MS
SERVE = 'tests/browser/connection-recovery.v02gui0031.serve.mjs'
# Browser profile, temp files and the run report stay in the gitignored node_modules/.cache.
CACHE = os.path.join(CLIENT_ROOT, 'node_modules', '.cache', 'v02gui0031-browser')


def b64(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def group_of(doc_id: str) -> str:
    return '.'.join(doc_id.split('.')[:3])


class Fake:
    """The FlowGate endpoints the chat and the Designer reads use, plus failure switches. All state under one lock."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.t0 = time.time()
        self.gen = 0
        self.tokens: dict[str, tuple[int, float]] = {}  # access token -> (generation, exp)
        self.refresh_token = ''
        self.turns: dict[str, list[dict]] = {chat: [] for chats in CHATS.values() for chat in chats}
        self.keys: dict[str, tuple[str, int]] = {}  # idempotency key -> (chat, seq)
        self.key_posts: dict[str, int] = {}
        self.runs: dict[str, dict] = {}  # run id -> {chat, group, status, hold, crid, command, anchor}
        self.starts: dict[str, str] = {}  # client_request_id -> run id
        self.start_log: list[dict] = []  # every POST /ai-invoke/start: {t, crid, run, new}
        self.stop_log: list[dict] = []  # every POST /ai-invoke/{run}/cancel and /ai-invoke/start/cancel
        self.hold_runs = False  # new runs stay in progress until finish_run (else they end on their first detail read)
        self.canvas = {F1: 'idle', F2: 'idle'}  # the card's run status of each Flow
        self.http_mode: object = None  # None | 502 | 503 | 504 | 'hang'
        self.sse_down = False
        self.auth_down = False
        self.lose_turn_answer = 0  # store the next N turns, then reset the connection without answering
        self.sse_clients: list[dict] = []
        self.log: list[dict] = []
        self.sse_connects: list[dict] = []
        self.refreshes: list[dict] = []
        # The per-request log's labels: the scenario, its step (last recorded phase) and the failure injected now.
        self.offline = False  # Playwright offline (set by the driver)
        self.switching: str | None = None  # 'to_offline' / 'to_online' while the browser's network switch is applied
        self.scenario, self.step = 'S0', 'start'
        self.timeline: list[dict] = [{'t': 0.0, 'scenario': 'S0', 'step': 'start', 'fault': 'none'}]
        for chat, seed in ((CHAT, ('hello', 'hi there')), (CHAT_B, ('B hello', 'B hi')), (CHAT_C, ('C hello', 'C hi'))):
            self._append(chat, 'user', seed[0])
            self._append(chat, 'ai', seed[1])

    # -- tokens -------------------------------------------------------------------------------
    def issue(self) -> tuple[str, str]:
        with self.lock:
            self.gen += 1
            exp = time.time() + TOKEN_LIFE_S
            token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'u1', 'exp': int(exp), 'gen': self.gen})}.sig"
            self.tokens[token] = (self.gen, exp)
            self.refresh_token = f'refresh-{self.gen}'
            return token, self.refresh_token

    def token_gen(self, token: str | None) -> int | None:
        with self.lock:
            entry = self.tokens.get(token or '')
            return entry[0] if entry else None

    def token_valid(self, token: str | None) -> bool:
        with self.lock:
            entry = self.tokens.get(token or '')
            return bool(entry and entry[1] > time.time())

    # -- store --------------------------------------------------------------------------------
    def _append(self, chat: str, speaker: str, body: str, key: str | None = None) -> dict:
        with self.lock:
            rows = self.turns[chat]
            seq = (rows[-1]['seq'] if rows else 0) + 1
            row = {'seq': seq, 'speaker': speaker, 'body': body, 'created_at': f'2026-11-16T10:{seq // 60 % 60:02d}:{seq % 60:02d}Z',
                   'participant_key': 'user:u1' if speaker == 'user' else 'ai:opus', 'display_name': None, 'key': key}
            rows.append(row)
        self.broadcast('conversation_turn_appended', {'seq': seq}, chat)
        return row

    def append_ai(self, chat: str, body: str) -> None:
        self._append(chat, 'ai', body)

    @staticmethod
    def raw(row: dict) -> dict:
        return {k: v for k, v in row.items() if k != 'key'}

    def bodies(self, chat: str) -> list[str]:
        with self.lock:
            return [r['body'] for r in self.turns[chat]]

    # -- AI runs ------------------------------------------------------------------------------
    def running_run(self, group: str) -> str | None:
        with self.lock:
            return next((r for r, info in self.runs.items() if info['group'] == group and info['status'] == 'running'), None)

    def progress(self, run: str, label: str, command: str | None = None) -> None:
        """The run is still in progress: it wrote a message and (optionally) its command reached `command`."""
        with self.lock:
            info = self.runs[run]
            if command:
                info['command'] = command
        self.append_ai(info['chat'], label)
        if command:
            self.broadcast('chat_command_updated', {'run_id': run}, info['chat'])

    def finish_run(self, run: str) -> None:
        with self.lock:
            info = self.runs[run]
            if info['status'] != 'running':
                return
            info['status'] = 'finished'
            if info['command'] in ('running', 'pending_approval'):
                info['command'] = 'succeeded'
        self.append_ai(info['chat'], f'reply {run}')
        self.broadcast('ai_invoke_finished', {'run_id': run}, info['chat'])

    def calls(self) -> dict:
        """AI start / stop requests so far (a recovery must add none)."""
        with self.lock:
            return {'start': len(self.start_log), 'stop': len(self.stop_log)}

    # -- Designer -----------------------------------------------------------------------------
    def touch_canvas(self, flow: str, status: str) -> None:
        """A card of `flow` changed (its run status): the Canvas hears it from the hub, or reads it on a resync."""
        with self.lock:
            self.canvas[flow] = status
        self.broadcast('graph_canvas_changed', {'flow_id': flow}, None, flow)

    # -- SSE ----------------------------------------------------------------------------------
    def broadcast(self, event: str, payload: dict, doc: str | None, group: str | None = None) -> None:
        frame = json.dumps({'event_type': event, 'payload': payload, 'timestamp': None, 'project': PROJECT,
                            'group_id': group or (group_of(doc) if doc else None), 'doc_id': doc})
        with self.lock:
            for client in self.sse_clients:
                client['q'].put(f'event: {event}\ndata: {frame}\n\n')

    def drop_streams(self) -> None:
        with self.lock:
            for client in self.sse_clients:
                client['alive'] = False

    def set_sse(self, down: bool) -> None:
        with self.lock:
            self.sse_down = down
        if down:
            self.drop_streams()
        self.mark()

    def set_auth(self, down: bool) -> None:
        with self.lock:
            self.auth_down = down
        self.mark()

    def fault(self) -> str:
        """The failure injected right now, e.g. `offline+sse_down`, `http_503+sse_down`, `hang`, `none` (plus `to_offline` /
        `to_online` during the few ms the browser's network switch takes: a request then may see either state)."""
        with self.lock:
            parts = (['offline'] if self.offline else []) \
                + ([f'http_{self.http_mode}' if isinstance(self.http_mode, int) else 'hang'] if self.http_mode is not None else []) \
                + (['auth_503'] if self.auth_down else []) + (['sse_down'] if self.sse_down else []) \
                + ([self.switching] if self.switching else [])
        return '+'.join(parts) or 'none'

    def mark(self, scenario: str | None = None, step: str | None = None) -> None:
        """A label change of the request log: a new scenario / step, or a failure switched on or off."""
        fault = self.fault()
        with self.lock:
            self.scenario = scenario or self.scenario
            self.step = step or self.step
            self.timeline.append({'t': round(time.time() - self.t0, 3), 'scenario': self.scenario, 'step': self.step,
                                  'fault': fault})

    def outage(self, mode: object) -> None:
        """A server outage: HTTP answers `mode` (or hangs) and the stream is cut / refused."""
        with self.lock:
            self.http_mode = mode
        self.set_sse(mode is not None)

    def note(self, started: float | None = None, **row) -> None:
        """One server-side request row: its start on the shared clock and what was answered."""
        with self.lock:
            self.log.append({'t': round((started or time.time()) - self.t0, 3), **row})


FAKE = Fake()
DOC_PATH = re.compile(r'^/api/v1/documents/([^/]+)(?:/(.*))?$')


def kind_of(path: str) -> str:
    """The request kinds the records count: chat, run (AI run / history), designer, auth, sse."""
    api = path.split('/flowgate', 1)[-1]
    if '/events/stream' in api:
        return 'sse'
    if '/auth/' in api:
        return 'auth'
    if '/ai-invoke/' in api or '/chat-activity/' in api:
        return 'run'
    if api.startswith('/api/v1/flows/') or api.startswith('/api/v1/projects/'):
        return 'designer'
    match = DOC_PATH.match(api)
    if match and match.group(2) is None and not unquote(match.group(1)).endswith('-CH'):
        return 'designer'  # the Inspector's document read
    return 'chat'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *_args) -> None:  # quiet
        return

    def cors(self) -> None:
        self.send_header('Access-Control-Allow-Origin', '*')
        # The wildcard does not cover Authorization (Fetch spec): echo what the preflight asks for, as a proxy would.
        self.send_header('Access-Control-Allow-Headers',
                         self.headers.get('Access-Control-Request-Headers') or 'Authorization, Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, PUT, DELETE, OPTIONS')

    def reply(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def do_OPTIONS(self) -> None:  # noqa: N802 — CORS preflight, answered like a proxy would
        self.send_response(204)
        self.cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.handle_api('GET')

    def do_POST(self) -> None:  # noqa: N802
        self.handle_api('POST')

    def do_PATCH(self) -> None:  # noqa: N802
        self.handle_api('PATCH')

    def body(self) -> dict:
        length = int(self.headers.get('Content-Length') or 0)
        try:
            return json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return {}

    def handle_api(self, method: str) -> None:
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        kind = kind_of(path)
        started = time.time()
        self.unknown = False
        if kind == 'sse':
            return self.stream(query, started)
        status = self.route(method, path, query)
        FAKE.note(started, kind='unknown' if self.unknown else kind, method=method, path=path.replace('/flowgate', ''),
                  status=status, ms=int((time.time() - started) * 1000))

    def route(self, method: str, path: str, query: dict) -> object:
        mode = FAKE.http_mode
        if mode == 'hang':
            time.sleep(6)  # longer than the fixture's client timeout; answered into a closed socket
            return 'hang'
        if isinstance(mode, int):
            self.reply(mode, {'ok': False, 'error_message': 'injected'})
            return mode
        if path.endswith('/auth/refresh') and method == 'POST':
            sent = self.body().get('refresh_token')
            if FAKE.auth_down:
                FAKE.refreshes.append({'t': round(time.time() - FAKE.t0, 2), 'result': 503})
                self.reply(503, {'ok': False})
                return 503
            if sent != FAKE.refresh_token:
                FAKE.refreshes.append({'t': round(time.time() - FAKE.t0, 2), 'result': 401})
                self.reply(401, {'ok': False})
                return 401
            access, refresh = FAKE.issue()
            FAKE.refreshes.append({'t': round(time.time() - FAKE.t0, 2), 'result': 200, 'gen': FAKE.gen})
            self.reply(200, {'access_token': access, 'refresh_token': refresh})
            return 200
        auth = self.headers.get('Authorization', '')
        if not FAKE.token_valid(auth[7:] if auth.startswith('Bearer ') else None):
            self.reply(401, {'ok': False, 'error_message': 'Token has expired'})
            return 401
        api = path.split('/flowgate', 1)[-1]
        q = lambda name, default=None: (query.get(name) or [default])[0]  # noqa: E731
        doc_match = DOC_PATH.match(api)
        if api == '/api/v1/documents' and method == 'GET':
            group = q('group_id')
            chats = CHATS.get(group, [])
            # The first chat of a Flow is the newest: it opens first.
            self.reply(200, {'documents': [{'doc_id': chat, 'title': chat.rsplit('.', 1)[-1], 'type_code': 'CH',
                                            'group_id': group, 'updated_at': f'2026-11-16T10:00:{50 - i:02d}Z'}
                                           for i, chat in enumerate(chats)]})
        elif doc_match and doc_match.group(2) is None and method == 'GET':
            doc = unquote(doc_match.group(1))
            self.reply(200, {'doc_id': doc, 'project_id': PROJECT, 'group_id': group_of(doc), 'type_code': doc.rsplit('-', 1)[-1],
                             'title': doc, 'status': 'open', 'revision_no': 1, 'updated_at': '2026-11-16T10:00:00Z'})
        elif doc_match and doc_match.group(2):
            return self.route_chat(method, unquote(doc_match.group(1)), doc_match.group(2), query)
        elif api.startswith('/api/v1/me/chat-settings'):
            # The session applies settings only for the scope they name (source_access_scope); without it the send
            # action stays `none` and no AI call is ever made.
            self.reply(200, {'source_access_scope': {'project_id': q('project_id'), 'group_id': q('group_id')},
                             'settings': {'send_action': 'invoke_ai', 'context_mode': 'recent', 'context_turns': 20,
                                          'source_access_mode': 'read_only'},
                             'domain': {'source_access_mode': ['read_only', 'execute', 'edit', 'edit_once']}})
        elif api == '/api/v1/ai-invoke/providers':
            self.reply(200, {'providers': [{'id': 'opus', 'name': 'Opus'}], 'default_provider_id': 'opus'})
        elif api == '/api/v1/ai-invoke/start' and method == 'POST':
            body = self.body()
            crid = body.get('client_request_id') or f'anon-{time.time()}'
            chat = body.get('doc_ref') or CHAT
            with FAKE.lock:
                run = FAKE.starts.get(crid)
                new = run is None
                if new:
                    run = f'run-{len(FAKE.runs) + 1}'
                    FAKE.starts[crid] = run
                    anchor = FAKE.turns[chat][-1]['seq'] if FAKE.turns.get(chat) else 0
                    FAKE.runs[run] = {'chat': chat, 'group': group_of(chat), 'status': 'running', 'hold': FAKE.hold_runs,
                                      'crid': crid, 'command': 'running', 'anchor': anchor}
                FAKE.start_log.append({'t': round(time.time() - FAKE.t0, 2), 'crid': crid, 'run': run, 'new': new})
            FAKE.broadcast('ai_invoke_started', {'run_id': run}, chat)
            self.reply(200, {'run_id': run})
        elif api == '/api/v1/ai-invoke/start/cancel' and method == 'POST':
            FAKE.stop_log.append({'t': round(time.time() - FAKE.t0, 2), 'path': api})
            self.reply(200, {'state': 'cancelled', 'run_id': None})
        elif api == '/api/v1/ai-invoke/active':
            run = FAKE.running_run(q('group_id', ''))
            with FAKE.lock:
                info = dict(FAKE.runs[run]) if run else None
            self.reply(200, {'active': True, 'run_id': run, 'status': 'running', 'doc_ref': info['chat'],
                             'group_id': info['group']} if info else {'active': False})
        elif api == '/api/v1/ai-invoke/runs':
            group = q('group_id')
            with FAKE.lock:
                items = [{'run_id': r, 'doc_ref': info['chat']} for r, info in FAKE.runs.items() if info['group'] == group]
            self.reply(200, {'items': items, 'total': len(items), 'has_more': False})
        elif re.match(r'^/api/v1/ai-invoke/run-\d+/cancel$', api) and method == 'POST':
            FAKE.stop_log.append({'t': round(time.time() - FAKE.t0, 2), 'path': api})
            self.reply(200, {'ok': True})
        elif re.match(r'^/api/v1/ai-invoke/run-\d+$', api) and method == 'GET':
            run = api.rsplit('/', 1)[-1]
            with FAKE.lock:
                info = FAKE.runs.get(run)
                finish_now = info is not None and info['status'] == 'running' and not info['hold']
            if info is None:
                self.reply(404, {'ok': False})
                return 404
            if finish_now:
                FAKE.finish_run(run)
            with FAKE.lock:
                info = dict(FAKE.runs[run])
            if info['status'] == 'running':
                # The server's running detail names the group and scope, not the CH: the client confirms through /active.
                self.reply(200, {'run_id': run, 'status': 'running', 'doc_ref': None, 'action_scope': 'chat',
                                 'group_id': info['group'], 'started_at': '2026-11-16T10:00:00Z',
                                 'provider': {'id': 'opus', 'name': 'Opus'}})
            else:
                self.reply(200, {'run_id': run, 'status': 'finished', 'end_reason': 'exited', 'outcome': 'complete',
                                 'exit_code': 0, 'doc_ref': info['chat'], 'action_scope': 'chat', 'group_id': info['group'],
                                 'succeeded': True, 'provider_id': 'opus', 'provider_name': 'Opus'})
        elif api.startswith('/api/v1/chat-activity/'):
            if method != 'GET':
                self.reply(200, {'ok': True})
            else:
                chat = unquote(api[len('/api/v1/chat-activity/'):].split('/', 1)[0])
                with FAKE.lock:
                    commands = [{'request_id': f'cmd-{r}', 'ai_run_id': r, 'status': info['command'], 'command': 'check',
                                 'created_at': '2026-11-16T10:00:00Z', 'anchor_seq': info['anchor'], 'anchor_position': 'after'}
                                for r, info in FAKE.runs.items() if info['chat'] == chat]
                self.reply(200, {'commands': commands, 'changes': [], 'attachments': [], 'errors': []})
        elif api == f'/api/v1/projects/{PROJECT}/groups/tree':
            nodes = [{'id': PROJECT, 'node_type': 'project', 'parent_id': None, 'label': PROJECT},
                     {'id': 'p.m', 'node_type': 'module', 'parent_id': PROJECT, 'label': 'm'}]
            nodes += [{'id': flow, 'node_type': 'group', 'parent_id': 'p.m', 'label': flow} for flow in CHATS]
            self.reply(200, {'data': {'nodes': nodes}})
        elif api == f'/api/v1/projects/{PROJECT}/git/status':
            self.reply(200, {'status': {'enabled': True, 'base_branch': 'main', 'base_branch_configured': True,
                                        'slots': [{'group_id': flow, 'branch': f'flow/{flow}', 'status': 'clean', 'writable': True}
                                                  for flow in CHATS], 'pending': []}})
        elif re.match(r'^/api/v1/flows/[^/]+/(canvas|graph)$', api) and method == 'GET':
            flow = unquote(api.split('/')[4])
            if flow not in CHATS:
                self.reply(404, {'ok': False})
                return 404
            if api.endswith('/graph'):
                self.reply(200, {'ok': True, 'ownership': {'state': 'graph', 'sequence_root_doc_id': None}})
            else:
                with FAKE.lock:
                    status = FAKE.canvas[flow]
                self.reply(200, {'flow_id': flow, 'project_id': PROJECT, 'ownership': {'state': 'graph'}, 'projection': 'graph',
                                 'nodes': [{'node_key': 'n1', 'kind': 'graph', 'title': 'card', 'state': 'active',
                                            'document': {'doc_id': f'{flow}.0001-B'}, 'node_run': {'status': status}}],
                                 'edges': []})
        else:
            self.unknown = True
            self.reply(200, {})
        return 200

    def route_chat(self, method: str, chat: str, sub: str, query: dict) -> object:
        if chat not in FAKE.turns:
            self.reply(404, {'ok': False})
            return 404
        if sub == 'conversation/turns':
            with FAKE.lock:
                rows = list(FAKE.turns[chat])
            limit = int((query.get('limit') or ['50'])[0])
            if 'after_seq' in query:
                rows = [r for r in rows if r['seq'] > int(query['after_seq'][0])][:limit]
            elif 'before_seq' in query:
                rows = [r for r in rows if r['seq'] < int(query['before_seq'][0])][-limit:]
            else:
                rows = rows[-limit:]
            head = FAKE.turns[chat][-1]['seq'] if FAKE.turns[chat] else 0
            self.reply(200, {'turns': [Fake.raw(r) for r in rows], 'participants': [], 'me': None, 'head_seq': head,
                             'next_after_seq': None, 'prev_before_seq': None})
        elif sub == 'conversation/turn' and method == 'POST':
            body = self.body()
            key = body.get('idempotency_key') or ''
            with FAKE.lock:
                FAKE.key_posts[key] = FAKE.key_posts.get(key, 0) + 1
                known = FAKE.keys.get(key)
                row = next((r for r in FAKE.turns[known[0]] if r['seq'] == known[1]), None) if known else None
                if row is None:
                    row = FAKE._append(chat, 'user', body.get('body') or '', key)
                    FAKE.keys[key] = (chat, row['seq'])
                lose = FAKE.lose_turn_answer > 0
                if lose:
                    FAKE.lose_turn_answer -= 1
            if lose:  # stored, but the answer never arrives
                self.close_connection = True
                try:
                    self.connection.shutdown(2)
                except OSError:
                    pass
                return 'stored_then_reset'
            self.reply(200, {'turn': Fake.raw(row), 'head_seq': FAKE.turns[chat][-1]['seq']})
        elif sub == 'conversation/read':
            self.reply(200, {'me': {'participant_key': 'user:u1', 'last_read_seq': self.body().get('last_read_seq', 0)}})
        elif sub == 'conversation/settings':
            self.reply(200, {'settings': {'provider_id': None}})
        else:
            self.unknown = True
            self.reply(200, {})
        return 200

    def stream(self, query: dict, started: float) -> None:
        token = (query.get('token') or [None])[0]
        gen = FAKE.token_gen(token)
        path = '/api/v1/events/stream'  # the token query is never logged (its generation is)
        if FAKE.sse_down or FAKE.http_mode is not None:
            FAKE.sse_connects.append({'t': round(time.time() - FAKE.t0, 2), 'gen': gen, 'status': 503})
            self.reply(503, {'ok': False})
            return FAKE.note(started, kind='sse', method='GET', path=path, status=503,
                             ms=int((time.time() - started) * 1000), gen=gen)
        if not FAKE.token_valid(token):
            FAKE.sse_connects.append({'t': round(time.time() - FAKE.t0, 2), 'gen': gen, 'status': 401})
            self.reply(401, {'ok': False, 'error_message': 'Session is invalid'})
            return FAKE.note(started, kind='sse', method='GET', path=path, status=401,
                             ms=int((time.time() - started) * 1000), gen=gen)
        FAKE.sse_connects.append({'t': round(time.time() - FAKE.t0, 2), 'gen': gen, 'status': 200})
        client = {'q': queue.Queue(), 'alive': True}
        self.send_response(200)
        self.cors()
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        with FAKE.lock:
            FAKE.sse_clients.append(client)
        try:
            self.wfile.write(b'retry: 1000\n\n')
            self.wfile.flush()
            while client['alive']:
                try:
                    chunk = client['q'].get(timeout=0.5)
                except queue.Empty:
                    chunk = ': ping\n\n'
                self.wfile.write(chunk.encode())
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with FAKE.lock:
                if client in FAKE.sse_clients:
                    FAKE.sse_clients.remove(client)
            self.close_connection = True
            # The stream's row: answered 200, open for `ms` (until the drop or the browser closed it).
            FAKE.note(started, kind='sse', method='GET', path=path, status=200, ms=int((time.time() - started) * 1000),
                      gen=gen, end='dropped by the server' if not client['alive'] else 'closed by the browser')


# -- scenario driver -----------------------------------------------------------------------------------
RECORDS: list[dict] = []
CHECKS: list[dict] = []
BROWSER: list[dict] = []  # the browser's per-request log (drained from the page), wall-clock ms


def check(name: str, ok: bool, detail: object = None) -> bool:
    CHECKS.append({'check': name, 'ok': bool(ok), **({'detail': detail} if detail is not None else {})})
    # Each result as it happens (console and checks.log), so a run cut short still says how far it got.
    line = f"CHECK {'OK' if ok else 'NG'} {round(time.time() - FAKE.t0, 1)} {name}"
    print(line, flush=True)
    with open(os.path.join(CACHE, 'checks.log'), 'a', encoding='utf-8') as handle:
        handle.write(line + '\n')
    return ok


# -- the per-request log (NR0003 §6: time, path, status, latency, failure kind of every request, on one clock) -------------
TSV_COLUMNS = ('id', 't', 'side', 'scenario', 'step', 'fault', 'kind', 'via', 'method', 'path', 'status', 'error', 'ms',
               'extra')


def request_log() -> list[dict]:
    """The browser's rows (BROWSER) and the server's rows (FAKE.log) on one clock, labelled with the scenario, the step
    (the last recorded phase) and the failure injected when each request started."""
    with FAKE.lock:
        timeline = sorted(FAKE.timeline, key=lambda e: e['t'])
        server = list(FAKE.log)
    times = [e['t'] for e in timeline]

    def label(t: float) -> dict:
        entry = timeline[max(bisect.bisect_right(times, t) - 1, 0)]
        return {'scenario': entry['scenario'], 'step': entry['step'], 'fault': entry['fault']}

    rows = []
    for e in BROWSER:
        t = round(e['start'] / 1000 - FAKE.t0, 3)
        extra = {k: e[k] for k in ('stream',) if k in e}
        rows.append({'t': t, 'side': 'browser', **label(t), 'kind': kind_of(e['path'].split('?', 1)[0]), 'via': e['via'],
                     'method': e['method'], 'path': e['path'], 'status': e['status'], 'error': e['error'], 'ms': e['ms'],
                     'extra': extra})
    for r in server:
        status = r['status'] if isinstance(r['status'], int) else None
        extra = {k: r[k] for k in ('gen', 'end') if k in r}  # gen = the stream token's generation, never the token
        rows.append({'t': r['t'], 'side': 'server', **label(r['t']), 'kind': r['kind'], 'via': 'http', 'method': r['method'],
                     'path': r['path'], 'status': status, 'error': None if status is not None else str(r['status']),
                     'ms': r['ms'], 'extra': extra})
    rows.sort(key=lambda r: (r['t'], r['side']))
    return [{'id': i, **r} for i, r in enumerate(rows, 1)]


def requests_by_step(rows: list[dict]) -> list[dict]:
    """Per scenario / step / failure: each side's outcomes (status or failure kind) by request kind."""
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r['scenario'], r['step'], r['fault'])
        entry = out.setdefault(key, {'scenario': key[0], 'step': key[1], 'fault': key[2], 'from_t': r['t'],
                                     'browser': {}, 'server': {}})
        bucket = entry[r['side']].setdefault(r['kind'], {})
        outcome = str(r['status'] if r['status'] is not None else r['error'])
        bucket[outcome] = bucket.get(outcome, 0) + 1
    return list(out.values())


def write_tsv(path: str, rows: list[dict]) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write('\t'.join(TSV_COLUMNS) + '\n')
        for r in rows:
            cells = [r['id'], r['t'], r['side'], r['scenario'], r['step'], r['fault'], r['kind'], r['via'], r['method'],
                     r['path'], '' if r['status'] is None else r['status'], r['error'] or '',
                     '' if r['ms'] is None else r['ms'], json.dumps(r['extra']) if r['extra'] else '']
            handle.write('\t'.join(str(c).replace('\t', ' ') for c in cells) + '\n')


def sha256_of(path: str) -> str:
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def check_request_log(rows: list[dict], secrets: list[str]) -> None:
    """The per-request log is complete enough to compare both ends, holds nothing secret, and shows every failure."""
    browser = [r for r in rows if r['side'] == 'browser']
    server = [r for r in rows if r['side'] == 'server']
    check('request log: browser rows (XHR and EventSource) and server rows on one clock',
          bool(server) and any(r['via'] == 'xhr' for r in browser) and any(r['via'] == 'eventsource' for r in browser),
          {'browser': len(browser), 'server': len(server)})
    text = json.dumps(rows, ensure_ascii=False)
    leaks = [s for s in secrets if s and s in text]
    check('request log: no token, refresh token or message body in it', not leaks, {'leaks': len(leaks)})
    # Every HTTP answer the browser saw was logged by the server too (same method, path, status).
    pool: dict[tuple, int] = {}
    for r in server:
        if r['status'] is not None and r['kind'] != 'sse':
            key = (r['method'], r['path'], r['status'])
            pool[key] = pool.get(key, 0) + 1
    missing = []
    for r in browser:
        if r['via'] == 'eventsource' or r['status'] is None:
            continue
        key = (r['method'], r['path'].split('?', 1)[0], r['status'])
        if pool.get(key, 0) > 0:
            pool[key] -= 1
        else:
            missing.append(key)
    check('request log: every HTTP response the browser saw has its server row (method, path, status)', not missing,
          {'missing': missing[:5], 'browser_responses': sum(1 for r in browser if r['status'] is not None
                                                            and r['via'] != 'eventsource')})
    # Every failure injected for a while (>= 0.4 s; the switches flip one after another) left its evidence in the log.
    with FAKE.lock:
        timeline = sorted(FAKE.timeline, key=lambda e: e['t'])
    end_t = max([r['t'] for r in rows] + [timeline[-1]['t']]) + 1
    spans: dict[tuple, list[tuple[float, float]]] = {}
    for entry, nxt in zip(timeline, timeline[1:] + [{'t': end_t}]):
        if entry['fault'] != 'none':
            spans.setdefault((entry['scenario'], entry['fault']), []).append((entry['t'], nxt['t']))

    def ends(r: dict) -> tuple[float, float]:
        return r['t'], r['t'] + (r['ms'] or 0) / 1000

    def within(r: dict, intervals: list, grace: float = 0.0) -> bool:
        start, stop = ends(r)
        return any(a <= start < b + grace or a <= stop < b + grace for a, b in intervals)

    evidence = {
        'offline': lambda r, iv: r['side'] == 'browser' and r['error'] == 'offline' and within(r, iv),
        'hang': lambda r, iv: r['side'] == 'browser' and r['error'] == 'timeout' and within(r, iv),
        'auth_503': lambda r, iv: r['side'] == 'browser' and r['kind'] == 'auth' and r['status'] == 503 and within(r, iv),
        # The stream: the browser saw it drop or fail, or the server refused / dropped it (the drop is seen up to 0.5 s
        # later: the server's stream loop wakes every 0.5 s).
        'sse_down': lambda r, iv: r['kind'] == 'sse' and within(r, iv, 1.0) and (
            (r['side'] == 'browser' and r['error'] not in (None, 'closed_by_client', 'cut_by_reload', 'open_at_end'))
            or (r['side'] == 'server' and (r['status'] == 503 or r['extra'].get('end') == 'dropped by the server'))),
    }
    for (scenario, fault), intervals in spans.items():
        if sum(b - a for a, b in intervals) < 0.4:
            continue
        for part in fault.split('+'):
            if part.startswith('http_'):
                code = int(part[5:])
                test = lambda r, iv, code=code: r['side'] == 'browser' and r['status'] == code and within(r, iv)  # noqa: E731
            elif part in evidence:
                test = evidence[part]
            else:
                continue  # to_offline / to_online: the switch itself, no failure of its own
            hits = [r['id'] for r in rows if test(r, intervals)]
            check(f'request log: {scenario} [{fault}] left per-request evidence of {part}', bool(hits),
                  {'rows': len(hits), 'first_ids': hits[:3]})


def main() -> int:
    # The Windows console code page cannot print every label (e.g. Korean): print UTF-8 whatever the console is.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    os.makedirs(CACHE, exist_ok=True)
    for name in ('checks.log', 'report.json', 'requests.tsv'):  # this run's files only
        if os.path.exists(os.path.join(CACHE, name)):
            os.remove(os.path.join(CACHE, name))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api_base = f'http://127.0.0.1:{server.server_address[1]}/flowgate'
    serve = subprocess.Popen(['node', SERVE, api_base], cwd=CLIENT_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
    try:
        line = serve.stdout.readline().strip()
        if not line.startswith('READY '):
            raise RuntimeError(f'fixture server did not start: {line} {serve.stdout.read()[:2000]}')
        return run(line[6:])
    finally:
        try:
            serve.stdin.close()
            serve.wait(timeout=10)
        except Exception:  # noqa: BLE001
            serve.kill()
        server.shutdown()


def run(page_url: str) -> int:
    # Browser profile and temp files stay in the gitignored node_modules/.cache (as the 0154 browser check keeps its own).
    cache = CACHE
    dirs = {name: os.path.join(cache, name.lower()) for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'TEMP')}
    for directory in dirs.values():
        os.makedirs(directory, exist_ok=True)
    env = {**os.environ, **dirs, 'TMP': dirs['TEMP']}
    # The browser is started the way the 0154 browser check starts Chrome (a debugging port; Playwright's own pipe launch
    # does not come up on the Self-check host) and Playwright attaches to it over CDP. Chrome first, then Edge.
    candidates = [path for path in (os.environ.get('CHROME_PATH'), 'C:/Program Files/Google/Chrome/Application/chrome.exe',
                                    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
                                    'C:/Program Files/Microsoft/Edge/Application/msedge.exe') if path and os.path.exists(path)]
    failures = []
    for executable in candidates:
        profile = os.path.join(cache, f'profile-{int(time.time() * 1000)}')
        os.makedirs(profile, exist_ok=True)
        log_path = os.path.join(cache, 'browser.log')
        with open(log_path, 'w', encoding='utf-8') as log:
            browser_process = subprocess.Popen([executable, '--headless=new', '--disable-gpu', '--no-first-run',
                                                '--no-default-browser-check', '--remote-debugging-port=0',
                                                f'--user-data-dir={profile}', 'about:blank'],
                                               env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            port = None
            for _ in range(300):
                try:
                    with open(os.path.join(profile, 'DevToolsActivePort'), encoding='utf-8') as handle:
                        port = int(handle.read().split()[0])
                    break
                except (OSError, ValueError, IndexError):
                    if browser_process.poll() is not None:
                        break
                    time.sleep(0.1)
            if port is not None:
                return drive(page_url, port)
        finally:
            browser_process.kill()
        with open(log_path, encoding='utf-8', errors='replace') as handle:
            tail = [line for line in handle.read().splitlines() if ':ERROR:' not in line and line.strip()][-5:]
        failures.append(f'{os.path.basename(executable)}: no debugging port (exit={browser_process.poll()}) {tail}')
    raise RuntimeError('ENVIRONMENT no browser with a debugging port: ' + ' | '.join(failures or ['no Chrome/Edge found']))



def drive(page_url: str, port: int) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
        print('BROWSER ' + browser.version)
        context = browser.contexts[0]
        page = context.new_page()
        console: list[str] = []
        page.on('pageerror', lambda e: console.append(f'pageerror: {e}'))
        sent_in: dict[str, str] = {}  # message label -> the chat it was sent in

        def drain(cut: str = '') -> list[dict]:
            """Moves the page's per-request log into BROWSER (with `cut`, the unfinished requests too, labelled so)."""
            try:
                rows = page.evaluate('cut => window.__netlog ? window.__netlog.drain(cut) : []', cut)
            except Exception:  # noqa: BLE001 — a page in the middle of a navigation: drained next time
                return []
            BROWSER.extend(rows)
            return rows

        def set_offline(down: bool) -> None:
            # The switch takes effect somewhere inside the CDP call: the requests started meanwhile are labelled so.
            with FAKE.lock:
                FAKE.switching = 'to_offline' if down else 'to_online'
            FAKE.mark()
            context.set_offline(down)
            with FAKE.lock:
                FAKE.offline = down
                FAKE.switching = None
            FAKE.mark()

        def reload() -> None:
            drain(cut='cut_by_reload')
            page.reload()
            page.wait_for_function('window.__fixture?.ready', timeout=30_000)

        def begin(scenario: str) -> None:
            FAKE.mark(scenario, 'start')

        def snap() -> dict:
            return page.evaluate('window.__fixture.snapshot()')

        def probe() -> dict:
            return page.evaluate('window.__fixture.designerProbe()')

        def wait_until(label: str, predicate, timeout: float = 20.0) -> bool:
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    if predicate(snap()):
                        return True
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.2)
            check(f'wait: {label}', False, snap())
            return False

        log_mark = [0]

        def record(scenario: str, phase: str, designer: dict | None = None, note: str = '') -> dict:
            s = snap()
            with FAKE.lock:
                window = FAKE.log[log_mark[0]:]
                log_mark[0] = len(FAKE.log)
                sse = FAKE.sse_connects[-1] if FAKE.sse_connects else None
            seen: dict[str, dict] = {}
            for row in window:
                bucket = seen.setdefault(row['kind'], {})
                bucket[str(row['status'])] = bucket.get(str(row['status']), 0) + 1
            # The browser's side of the same window (requests finished since the last row): status or failure kind.
            seen_browser: dict[str, dict] = {}
            for entry in drain():
                bucket = seen_browser.setdefault(kind_of(entry['path'].split('?', 1)[0]), {})
                outcome = str(entry['status'] if entry['status'] is not None else entry['error'])
                bucket[outcome] = bucket.get(outcome, 0) + 1
            row = {'t': round(time.time() - FAKE.t0, 1), 'scenario': scenario, 'phase': phase,
                   'flow': s['flow'], 'chat': s['activeChatId'], 'chatsStatus': s['chatsStatus'],
                   'requests': seen, 'browser_requests': seen_browser, 'designer': designer and {'flow': designer['flow'], 'status': designer['status'],
                                                               'data': designer['data']},
                   'canvas_model': s['canvas'], 'sse_readyState': s['sse']['readyState'],
                   'sse_streams': s['sse']['count'], 'sse_last_connect': sse,
                   'turnsStatus': s['turnsStatus'], 'sending': s['sending'], 'runId': s['runId'], 'running': s['running'],
                   'history': s['activities'], 'pending': [{'failed': p['failed']} for p in s['pending']],
                   'turns': len(s['turns']), 'ai_calls': FAKE.calls(), 'notice': s['notice'], 'counters': s['counters'],
                   'note': note}
            RECORDS.append(row)
            FAKE.mark(scenario, f'since {phase}')  # the requests that follow belong to this step
            return row

        def labels() -> list[str]:
            return snap()['turns']

        def idle(label: str) -> bool:
            # A send is refused while the chat sends, runs an AI call or is not read: wait for the previous one.
            return wait_until(f'idle: {label}', lambda s: s['turnsStatus'] == 'ready' and not s['sending']
                              and not s['running'] and not any(not p['failed'] for p in s['pending']), 30)

        def send(label: str, wait: bool = True) -> bool:
            if wait:
                idle(label)
            sent_in[label] = snap()['activeChatId']
            return bool(page.evaluate('t => window.__fixture.send(t)', label))

        def open_chat(chat_id: str) -> None:
            page.evaluate('id => window.__fixture.openChat(id)', chat_id)

        def switch_flow(flow: str) -> None:
            page.evaluate('id => window.__fixture.switchFlow(id)', flow)

        def designer_ok(result: dict) -> bool:
            return all(v == 200 for v in result['status'].values())

        def designer_down(result: dict) -> bool:
            return all(v != 200 for v in result['status'].values())

        def no_recovery_calls(sc: str, before: dict) -> bool:
            # T0005 §4: a recovery reads only; it never starts or stops an AI run (start / stop request counts).
            now = FAKE.calls()
            return check(f'{sc}: the recovery sent no AI start / stop request', now == before, {'before': before, 'after': now})

        def open_ready(chat_id: str, label: str) -> bool:
            return wait_until(label, lambda s: s['activeChatId'] == chat_id and s['turnsStatus'] == 'ready', 30)

        access, refresh = FAKE.issue()
        page.goto(f'{page_url}?access={access}&refresh={refresh}&flow={F1}')
        page.wait_for_function('window.__fixture?.ready', timeout=30_000)
        wait_until('initial chat ready', lambda s: s['activeChatId'] == CHAT and s['turnsStatus'] == 'ready'
                   and s['sse']['readyState'] == 1 and s['canvas']['flowId'] == F1, 30)
        d = probe()
        check('initial: chat and every Designer read (flows, canvas, document, settings, git)', designer_ok(d), d['status'])
        # Every send calls the AI (the AI-call checks below are not vacuous).
        check('initial: the send action is invoke_ai and an AI is selected',
              wait_until('settings applied', lambda s: s['sendAction'] == 'invoke_ai' and bool(s['providerId']), 15))
        record('S0', 'initial', d)

        # S1 — network offline (twice), chat A of Flow 1. Chat and Designer both fail; online brings both back without F5.
        # A card change and a message stored during the outage show afterwards (the Canvas by its resync).
        for cycle in (1, 2):
            sc = f'S1.{cycle} offline'
            begin(sc)
            idle(sc)
            record(sc, 'before', probe())
            page.evaluate("t => window.__fixture.setDraft(t)", f'draft {cycle}')
            calls0 = FAKE.calls()
            FAKE.set_sse(True)
            set_offline(True)
            time.sleep(0.5)
            d = probe()
            sent = send(f'S1.{cycle} sent offline', wait=False)
            FAKE.append_ai(CHAT, f'S1.{cycle} stored by the server meanwhile')
            FAKE.touch_canvas(F1, f'S1.{cycle}')
            row = record(sc, 'disconnected', d)
            check(f'{sc}: send fails while offline', not sent and any(p['failed'] for p in snap()['pending']))
            check(f'{sc}: scope = both (chat and every Designer read fail)', designer_down(d), row['designer']['status'])
            check(f'{sc}: the Canvas does not show the card change while offline', f'S1.{cycle}' not in snap()['canvas']['cards'])
            FAKE.set_sse(False)
            set_offline(False)
            ok = wait_until(f'{sc} chat recovered', lambda s: f'S1.{cycle} stored by the server meanwhile' in s['turns']
                            and s['sse']['readyState'] == 1, 20)
            canvas_ok = wait_until(f'{sc} Canvas recovered', lambda s: f'n1:S1.{cycle}' in s['canvas']['cards'], 20)
            d = probe()
            record(sc, 'restored (no F5)', d)
            check(f'{sc}: open chat read again on online (②) and stream back', ok)
            check(f'{sc}: Canvas read again by its resync (card change shown)', canvas_ok, snap()['canvas'])
            check(f'{sc}: every Designer read back', designer_ok(d), d['status'])
            check(f'{sc}: draft kept', snap()['draft'] == f'draft {cycle}', snap()['draft'])
            check(f'{sc}: the failed message was not resent by the recovery', f'S1.{cycle} sent offline' not in FAKE.bodies(CHAT))
            no_recovery_calls(sc, calls0)
            wait_until(f'{sc} ready for retry', lambda s: s['turnsStatus'] == 'ready' and not s['running'] and not s['sending'], 20)
            retried = page.evaluate('window.__fixture.retryFailed()')
            wait_until(f'{sc} retry stored', lambda s: not s['pending'] and f'S1.{cycle} sent offline' in s['turns'], 20)
            check(f'{sc}: [다시 시도] stores the message', bool(retried))
            check(f'{sc}: a new send works', send(f'S1.{cycle} sent after'))
            record(sc, 'after send')

        # S2/S3 — the server answers 502/503/504 or hangs (browser stays online: no `online` event). During the outage
        # the user switches to the other chat of the Flow (A <-> B, alternating), so that chat's first read fails
        # (turnsStatus error). Back up: the stream's reopen resyncs, and the failed chat reads again by itself (③).
        for mode in (502, 503, 504, 'hang'):
            sc = f'S2 {mode}' if mode != 'hang' else 'S3 timeout'
            begin(sc)
            idle(sc)
            other = CHAT_B if snap()['activeChatId'] == CHAT else CHAT
            record(sc, 'before', probe())
            calls0 = FAKE.calls()
            FAKE.outage(mode)
            d = probe()
            FAKE.append_ai(other, f'{sc} stored meanwhile')
            FAKE.touch_canvas(F1, f'{sc}')
            open_chat(other)
            failed = wait_until(f'{sc} first read of the other chat failed',
                                lambda s: s['activeChatId'] == other and s['turnsStatus'] == 'error', 30)
            row = record(sc, 'outage (chat switched)', d)
            check(f'{sc}: scope = both (chat read and every Designer read fail)', failed and designer_down(d), row['designer']['status'])
            FAKE.outage(None)
            ok = wait_until(f'{sc} chat recovered', lambda s: s['activeChatId'] == other and s['turnsStatus'] == 'ready'
                            and f'{sc} stored meanwhile' in s['turns'] and s['sse']['readyState'] == 1, 25)
            canvas_ok = wait_until(f'{sc} Canvas recovered', lambda s: f'n1:{sc}' in s['canvas']['cards'], 20)
            d = probe()
            record(sc, 'restored (no F5)', d)
            check(f'{sc}: the chat switched to during the outage reads again by itself (③)', ok)
            check(f'{sc}: Canvas read again by its resync', canvas_ok, snap()['canvas'])
            check(f'{sc}: every Designer read back', designer_ok(d), d['status'])
            no_recovery_calls(sc, calls0)
            check(f'{sc}: a new send works in that chat', send(f'{sc} sent after'))
            record(sc, 'after send')
        if snap()['activeChatId'] != CHAT:
            idle('back to chat A')
            open_chat(CHAT)
        open_ready(CHAT, 'chat A open again')

        # S4 — only the event stream drops; HTTP stays up. A message and a card change made meanwhile (their events
        # lost) show after the stream is back, through the resync.
        sc = 'S4 SSE only'
        begin(sc)
        idle(sc)
        record(sc, 'before', probe())
        FAKE.set_sse(True)
        time.sleep(1.0)
        d = probe()
        sent = send('S4 sent while the stream is down')
        idle(sc)  # its AI run ended and its final reads are done: nothing polls any more
        calls0 = FAKE.calls()
        FAKE.append_ai(CHAT, 'S4 stored, event lost')
        FAKE.touch_canvas(F1, 'S4')
        time.sleep(1.5)
        row = record(sc, 'stream down', d)
        check(f'{sc}: scope = event stream only (HTTP chat send and every Designer read fine)', sent and designer_ok(d),
              row['designer']['status'])
        missed_before = 'S4 stored, event lost' in labels()
        canvas_before = 'n1:S4' in snap()['canvas']['cards']
        check(f'{sc}: the message stored meanwhile is not shown while the stream is down', not missed_before)
        check(f'{sc}: the card change is not shown on the Canvas while the stream is down', not canvas_before)
        FAKE.set_sse(False)
        ok = wait_until(f'{sc} missed message caught up', lambda s: 'S4 stored, event lost' in s['turns'] and s['sse']['readyState'] == 1, 20)
        canvas_ok = wait_until(f'{sc} Canvas caught up', lambda s: 'n1:S4' in s['canvas']['cards'], 20)
        record(sc, 'restored (no F5)', probe(), note=f'missed message / card shown before restore: {missed_before} / {canvas_before}')
        check(f'{sc}: stream back and missed message caught up by resync', ok)
        check(f'{sc}: Canvas caught up by resync', canvas_ok, snap()['canvas'])
        no_recovery_calls(sc, calls0)

        # S5 — the token rotation fails during an outage of the auth endpoint and the stream; the screen is watched
        # (no focus / visibility / online). The rotation is retried by itself (④) and the stream keeps working.
        sc = 'S5 refresh failing'
        begin(sc)
        idle(sc)
        record(sc, 'before', probe())
        calls0 = FAKE.calls()
        before = snap()['counters']
        gen_before = FAKE.gen
        fails = lambda: sum(1 for r in FAKE.refreshes if r['result'] == 503)  # noqa: E731
        fails0 = fails()
        FAKE.set_auth(True)
        FAKE.set_sse(True)
        deadline = time.time() + 40
        while time.time() < deadline and fails() < fails0 + 2:
            time.sleep(0.2)
        record(sc, 'refresh failing', probe(), note=f'refresh 503 x{fails() - fails0}')
        check(f'{sc}: two failed rotations seen (timer + retry)', fails() >= fails0 + 2, FAKE.refreshes[-4:])
        FAKE.set_auth(False)
        FAKE.set_sse(False)
        deadline = time.time() + 30
        while time.time() < deadline and FAKE.gen == gen_before:
            time.sleep(0.2)
        after = snap()['counters']
        record(sc, 'restored (no F5)', probe(), note=f'token generation {gen_before} -> {FAKE.gen}')
        check(f'{sc}: rotation retried without focus/online/F5 (④)', FAKE.gen > gen_before
              and after['online'] == before['online'], {'gen': [gen_before, FAKE.gen], 'counters': [before, after]})
        # The next stream connection carries the current token (①).
        FAKE.set_sse(True)
        time.sleep(0.6)
        FAKE.set_sse(False)
        ok = wait_until(f'{sc} stream with current token', lambda s: s['sse']['readyState'] == 1 and s['sse']['tokenIsCurrent'], 20)
        last = next((c for c in reversed(FAKE.sse_connects) if c['status'] == 200), None)
        d = probe()
        record(sc, 'stream reconnected', d, note=f'last stream token generation {last and last["gen"]}, server {FAKE.gen}')
        check(f'{sc}: stream reconnects with the current token (①)', ok and last is not None and last['gen'] == FAKE.gen, last)
        check(f'{sc}: every Designer read with the rotated token', designer_ok(d), d['status'])
        no_recovery_calls(sc, calls0)
        check(f'{sc}: a new send works', send(f'{sc} sent after'))

        # S6 — the server stores a message but its answer is lost: the session resends the same key, no second row.
        sc = 'S6 answer lost'
        begin(sc)
        idle(sc)
        FAKE.lose_turn_answer = 1
        sent = send('S6 stored, answer lost', wait=False)
        wait_until(f'{sc} settled', lambda s: not any(not p['failed'] for p in s['pending']), 20)
        if not sent:
            page.evaluate('window.__fixture.retryFailed()')
            wait_until(f'{sc} retry stored', lambda s: not s['pending'], 20)
        record(sc, 'settled')
        with FAKE.lock:
            rows = [r for r in FAKE.turns[CHAT] if r['body'] == 'S6 stored, answer lost']
        key = rows[0]['key'] if rows else None
        check(f'{sc}: one stored row, same key re-sent', len(rows) == 1 and FAKE.key_posts.get(key, 0) >= 2,
              {'rows': len(rows), 'posts_for_key': FAKE.key_posts.get(key)})

        # S7 — an outage while an AI run is in progress (T0005 §4, B §3-A): the run is not stopped or started again;
        # the existing run is kept or found again (/ai-invoke/active) and adopted, and its messages, history (command
        # state) and progress come back. Three outages during one run: network offline (the poll keeps the run), a 503
        # with a chat switch away and back (the reopened chat adopts the run), an F5 (the reloaded page adopts it).
        # The run then finishes while only the stream is down. Exactly one start request, no stop request.
        sc = 'S7 run in progress'
        begin(sc)
        idle(sc)
        record(sc, 'before', probe())
        with FAKE.lock:
            FAKE.hold_runs = True
            runs0 = len(FAKE.runs)
        calls0 = FAKE.calls()
        check(f'{sc}: the message is sent', send('S7 long run'))
        started = wait_until(f'{sc} run started', lambda s: bool(s['runId']) and str(s['runId']).startswith('run-') and s['running'], 20)
        run_id = snap()['runId'] if started else None
        calls1 = FAKE.calls()
        check(f'{sc}: one start request for the press', started and calls1['start'] == calls0['start'] + 1, {'before': calls0, 'after': calls1})
        if run_id:
            FAKE.progress(run_id, 'S7 progress 1')
            wait_until(f'{sc} progress shown', lambda s: 'S7 progress 1' in s['turns'] and s['runId'] == run_id, 15)
            record(sc, 'running', probe())

            # 7a — network offline for more than two poll ticks; the server goes on with the run.
            begin(f'{sc} / offline')
            set_offline(True)
            FAKE.set_sse(True)
            time.sleep(RUN_POLL_S * 2 + 1)
            FAKE.progress(run_id, 'S7 progress while offline', 'succeeded')
            row = record(f'{sc} / offline', 'disconnected (run in progress)', probe())
            check(f'{sc} / offline: the run is kept while offline (not given up, not stopped)',
                  row['runId'] == run_id and row['running'], {'runId': row['runId'], 'running': row['running']})
            FAKE.set_sse(False)
            set_offline(False)
            ok = wait_until(f'{sc} / offline recovered', lambda s: 'S7 progress while offline' in s['turns'] and s['runId'] == run_id
                            and s['running'] and {'runId': run_id, 'state': 'succeeded'} in s['activities']
                            and s['sse']['readyState'] == 1, 25)
            record(f'{sc} / offline', 'restored (no F5)', probe())
            check(f'{sc} / offline: same run still followed; progress and history (command state) caught up', ok)
            no_recovery_calls(f'{sc} / offline', calls1)

            # 7b — 503 with a chat switch away and back during the outage: chat A's reopen fails, then reads again by
            # itself (③) and adopts the run the server still has (/ai-invoke/active), with what it wrote meanwhile.
            begin(f'{sc} / 503')
            FAKE.outage(503)
            open_chat(CHAT_B)
            wait_until(f'{sc} / 503 other chat failed', lambda s: s['activeChatId'] == CHAT_B and s['turnsStatus'] == 'error', 30)
            open_chat(CHAT)
            failed = wait_until(f'{sc} / 503 chat A reopen failed', lambda s: s['activeChatId'] == CHAT and s['turnsStatus'] == 'error', 30)
            FAKE.progress(run_id, 'S7 progress during 503')
            row = record(f'{sc} / 503', 'outage (chat switched away and back)', probe())
            check(f'{sc} / 503: chat A reopened during the outage fails and holds no run', failed and not row['running'],
                  {'turnsStatus': row['turnsStatus'], 'runId': row['runId']})
            FAKE.outage(None)
            ok = wait_until(f'{sc} / 503 recovered', lambda s: s['activeChatId'] == CHAT and s['turnsStatus'] == 'ready'
                            and s['runId'] == run_id and s['running'] and 'S7 progress during 503' in s['turns'], 30)
            record(f'{sc} / 503', 'restored (no F5)', probe())
            check(f'{sc} / 503: the existing run is found again and adopted (no new run), its messages shown', ok)
            no_recovery_calls(f'{sc} / 503', calls1)

            # 7c — F5 while the run is in progress: the reloaded page adopts the same run.
            begin(f'{sc} / F5')
            time.sleep(1.0)
            before_f5 = labels()
            record(f'{sc} / F5', 'before reload (run in progress)', probe())
            reload()
            ok = wait_until(f'{sc} / F5 adopted', lambda s: s['activeChatId'] == CHAT and s['turnsStatus'] == 'ready'
                            and s['runId'] == run_id and s['running'], 30)
            after_f5 = labels()
            record(f'{sc} / F5', 'after reload (run in progress)', probe())
            check(f'{sc} / F5: the reloaded page adopts the same run', ok)
            check(f'{sc} / F5: the screen before the reload already showed what the reload shows', before_f5 == after_f5,
                  {'before': len(before_f5), 'after': len(after_f5), 'only_after': [x for x in after_f5 if x not in before_f5][:5]})
            no_recovery_calls(f'{sc} / F5', calls1)

            # 7d — the run finishes while only the stream is down: the poll (HTTP) shows the reply and ends the run.
            begin(f'{sc} / finish, stream down')
            FAKE.set_sse(True)
            time.sleep(0.5)
            FAKE.finish_run(run_id)
            ok = wait_until(f'{sc} finished', lambda s: f'reply {run_id}' in s['turns'] and not s['runId'] and not s['running'], 30)
            row = record(sc, 'finished (stream down)', probe())
            FAKE.set_sse(False)
            wait_until(f'{sc} stream back', lambda s: s['sse']['readyState'] == 1, 20)
            check(f'{sc}: the run ends on the screen with its reply, the lock released', ok)
            check(f'{sc}: history shows the run\'s final command state', {'runId': run_id, 'state': 'succeeded'} in snap()['activities'],
                  snap()['activities'])
            check(f'{sc}: the reply is shown once and stored once',
                  labels().count(f'reply {run_id}') == 1 and FAKE.bodies(CHAT).count(f'reply {run_id}') == 1)
            with FAKE.lock:
                crid = FAKE.runs[run_id]['crid']
                posts_for_press = sum(1 for entry in FAKE.start_log if entry['crid'] == crid)
                runs1 = len(FAKE.runs)
            check(f'{sc}: exactly one start request for the press and one run (no duplicate AI call)',
                  posts_for_press == 1 and runs1 == runs0 + 1, {'start_posts_for_press': posts_for_press, 'runs_added': runs1 - runs0})
            check(f'{sc}: no AI stop request during the whole run', FAKE.calls()['stop'] == calls0['stop'], FAKE.calls())
        with FAKE.lock:
            FAKE.hold_runs = False

        # S8 — the same in another Flow: switch to Flow 2 (chat C), offline outage there, F5 there; then a Flow switch
        # back to Flow 1 DURING a 503 outage (its chat list read fails) and the recovery without F5.
        sc = 'S8 Flow 2'
        begin(sc)
        idle(sc)
        switch_flow(F2)
        ok = wait_until(f'{sc} opened', lambda s: s['scopeGroup'] == F2 and s['activeChatId'] == CHAT_C
                        and s['turnsStatus'] == 'ready' and s['canvas']['flowId'] == F2, 30)
        d = probe()
        record(sc, 'Flow switched (normal)', d)
        check(f'{sc}: Flow switch opens its chat and Canvas', ok, snap()['canvas'])
        check(f'{sc}: every Designer read of Flow 2', designer_ok(d) and d['flow'] == F2, d)
        idle(sc)
        begin(f'{sc} offline')
        calls0 = FAKE.calls()
        FAKE.set_sse(True)
        set_offline(True)
        time.sleep(0.5)
        d = probe()
        FAKE.append_ai(CHAT_C, 'S8 stored meanwhile')
        FAKE.touch_canvas(F2, 'S8')
        row = record(f'{sc} offline', 'disconnected', d)
        check(f'{sc} offline: scope = both (Flow 2 chat and Designer reads fail)', designer_down(d), row['designer']['status'])
        FAKE.set_sse(False)
        set_offline(False)
        ok = wait_until(f'{sc} offline recovered', lambda s: 'S8 stored meanwhile' in s['turns'] and s['sse']['readyState'] == 1
                        and 'n1:S8' in s['canvas']['cards'], 25)
        d = probe()
        record(f'{sc} offline', 'restored (no F5)', d)
        check(f'{sc} offline: Flow 2 chat and Canvas recovered without F5', ok, snap()['canvas'])
        check(f'{sc} offline: every Designer read of Flow 2 back', designer_ok(d), d['status'])
        no_recovery_calls(f'{sc} offline', calls0)
        check(f'{sc} offline: a new send works in Flow 2', send('S8 sent after'))
        idle(f'{sc} F5')
        time.sleep(1.0)
        before_f5, d_before = labels(), probe()
        record(f'{sc} F5', 'before reload', d_before)
        reload()
        wait_until(f'{sc} ready after F5', lambda s: s['activeChatId'] == CHAT_C and s['turnsStatus'] == 'ready'
                   and s['canvas']['flowId'] == F2, 30)
        after_f5, d_after = labels(), probe()
        record(f'{sc} F5', 'after reload', d_after)
        check(f'{sc} F5: same Flow, same messages, same Designer reads before and after the reload',
              before_f5 == after_f5 and d_before['data'] == d_after['data'] and snap()['flow'] == F2,
              {'turns': [len(before_f5), len(after_f5)], 'designer': [d_before['data'], d_after['data']]})

        sc = 'S8 Flow switch during 503'
        begin(sc)
        idle(sc)
        calls0 = FAKE.calls()
        FAKE.outage(503)
        switch_flow(F1)
        failed = wait_until(f'{sc} list read failed', lambda s: s['scopeGroup'] == F1 and s['chatsStatus'] == 'error', 30)
        d = probe()
        row = record(sc, 'outage (Flow switched)', d)
        check(f'{sc}: the Flow 1 chat list and Designer reads fail during the outage', failed and designer_down(d), row['designer']['status'])
        FAKE.touch_canvas(F1, 'S8 switch')
        FAKE.outage(None)
        ok = wait_until(f'{sc} recovered', lambda s: s['chatsStatus'] == 'ready' and CHAT in s['chats']
                        and s['canvas']['flowId'] == F1 and 'n1:S8 switch' in s['canvas']['cards'] and s['sse']['readyState'] == 1, 30)
        d = probe()
        opened = snap()['activeChatId']
        record(sc, 'restored (no F5)', d, note=f'chat opened by itself after the list came back: {opened}')
        check(f'{sc}: Flow 1 chat list and Canvas recovered without F5', ok, snap())
        check(f'{sc}: every Designer read of Flow 1 back', designer_ok(d) and d['flow'] == F1, d['status'])
        no_recovery_calls(sc, calls0)
        if opened != CHAT:  # TR §5-4: a list recovered after a failed first read opens no chat by itself (kept as found)
            open_chat(CHAT)
        open_ready(CHAT, f'{sc} chat A open')
        check(f'{sc}: a new send works in Flow 1', send('S8 sent in Flow 1 after the switch'))

        # F5 — compare with a reload in Flow 1: the same messages and Designer reads, nothing pending, nothing doubled.
        begin('F5')
        idle('F5')
        time.sleep(1.0)
        before_f5, d_before = labels(), probe()
        record('F5', 'before reload', d_before)
        reload()
        wait_until('chat ready after F5', lambda s: s['activeChatId'] == CHAT and s['turnsStatus'] == 'ready', 30)
        after_f5, d_after = labels(), probe()
        record('F5', 'after reload', d_after)
        check('F5: the screen before the reload already showed what the reload shows', before_f5 == after_f5
              and d_before['data'] == d_after['data'],
              {'before': len(before_f5), 'after': len(after_f5), 'only_after': [x for x in after_f5 if x not in before_f5][:5],
               'designer': [d_before['data'], d_after['data']]})

        # Store integrity: one row per message (a retry never minted a new key), each message in the chat it was sent
        # in, exactly one start request per AI press, one AI run per accepted message, no stop request.
        with FAKE.lock:
            users = [(chat, r) for chat, rows in FAKE.turns.items() for r in rows if r['speaker'] == 'user' and r['key']]
            dup = sorted({f'{chat}:{r["body"]}' for chat, r in users
                          if sum(1 for c2, r2 in users if c2 == chat and r2['body'] == r['body']) > 1})
            misplaced = sorted(label for label, chat in sent_in.items()
                               if any(r['body'] == label for c2, r in users if c2 != chat))
            runs = len(FAKE.runs)
            start_posts = len(FAKE.start_log)
            presses = len(FAKE.starts)
            resent = [e for e in FAKE.start_log if not e['new']]
            stops = list(FAKE.stop_log)
        check('store: one row per message (no duplicate from a retry)', not dup, dup)
        check('store: every message stored in the chat it was sent in (chat / Flow switches)', not misplaced, misplaced)
        check('store: one AI run per accepted message (no duplicate AI call, none missing)', runs == len(users) and runs > 0,
              {'messages': len(users), 'runs': runs})
        check('store: exactly one start request per AI press (no start resent by a recovery)',
              start_posts == presses == runs and not resent, {'start_posts': start_posts, 'presses': presses, 'runs': runs,
                                                              'resent': resent[:5]})
        check('store: no AI stop request (no recovery stopped a run)', not stops, stops[:5])
        check('no page error', not console, console[:5])
        drain(cut='open_at_end')  # the streams still open now
        browser.close()
    time.sleep(1.0)  # the fake server notes the streams the browser just closed

    requests = request_log()
    with FAKE.lock:
        secrets = list(FAKE.tokens) + [f'refresh-{n}' for n in range(1, FAKE.gen + 1)]
    secrets += list(sent_in)  # message labels (bodies)
    check_request_log(requests, secrets)

    failed = [c['check'] for c in CHECKS if not c['ok']]
    by_step = requests_by_step(requests)
    report = {'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(FAKE.t0)),
              'clock': 't = seconds since the fake server started (wall clock; the browser rows from Date.now())',
              'records': RECORDS, 'checks': CHECKS, 'sse': FAKE.sse_connects, 'refresh': FAKE.refreshes,
              'ai_calls': {'start': FAKE.start_log, 'stop': FAKE.stop_log},
              'unknown': sorted({r['path'] for r in FAKE.log if r['kind'] == 'unknown'}), 'failed': failed,
              'timeline': FAKE.timeline, 'requests_by_step': by_step, 'requests': requests}
    # The whole report also goes to files (gitignored cache) so a console that keeps only a tail loses nothing:
    # report.json (everything) and requests.tsv (the per-request log, one row per request and side).
    report_path, tsv_path = os.path.join(CACHE, 'report.json'), os.path.join(CACHE, 'requests.tsv')
    with open(report_path, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False)
    write_tsv(tsv_path, requests)
    print('RECORDS ' + json.dumps(RECORDS, ensure_ascii=False))
    print('REQUESTS_BY_STEP ' + json.dumps(by_step, ensure_ascii=False))
    print('SSE ' + json.dumps(FAKE.sse_connects, ensure_ascii=False))
    print('REFRESH ' + json.dumps(FAKE.refreshes, ensure_ascii=False))
    print('AI_CALLS ' + json.dumps(report['ai_calls'], ensure_ascii=False))
    print('UNKNOWN ' + json.dumps(report['unknown']))
    print('CHECKS ' + json.dumps(CHECKS, ensure_ascii=False))
    print(f'REQUEST_LOG rows={len(requests)} browser={sum(1 for r in requests if r["side"] == "browser")} '
          f'server={sum(1 for r in requests if r["side"] == "server")} started_at={report["started_at"]}')
    print(f'SUMMARY checks={len(CHECKS)} failed={len(failed)} report={report_path} sha256={sha256_of(report_path)} '
          f'requests={tsv_path} sha256={sha256_of(tsv_path)}')
    print('RESULT ' + ('PASS' if not failed else 'FAIL ' + json.dumps(failed, ensure_ascii=False)))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
