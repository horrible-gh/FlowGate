<div align="center">

# FlowGate

### A document pipeline that makes AI agents *prove* their work before it counts.

*Requirement → Task → Task Report. Every handoff passes through a gate that a human (or another agent) must approve.*

<!-- TODO: drop a 10–15s demo GIF here — submit a doc via the remote API, watch it land in the inbox,
     get reviewed, rejected, revised, and approved. The GIF is the single most persuasive asset on this page. -->

</div>

---

## Why this exists

Hand a coding task to an autonomous AI agent and you eventually hit the same wall: **it tells you it's done when it isn't.** The tests "pass," the bug is "fixed," the feature is "fully implemented" — and none of it is true. The agent isn't lying maliciously; it just has no structural reason to be accountable. There's no gate between *"I claim I did X"* and *"X is accepted."*

FlowGate is that gate.

It turns work into a **typed document pipeline**. An agent can't just *say* a task is finished — it has to register an artifact (a Task, a Task Report, a Requirement) through an authenticated API. That artifact enters an **inbox**, gets a **review status**, and can be **approved, rejected with a reason, or sent back for revision**. Nothing moves to the next stage until the current one clears its gate. The agent's claim and the verified state are no longer the same thing — which is the entire point.

The system has been **dogfooding itself for its own development**: the requirement and work order that produced *this very README* were filed, reviewed, and approved through FlowGate.

---

## How it works

Each unit of work is a **document** with a type and a place in a sequence. Documents are grouped, numbered automatically, and chained: a Requirement (`R`) triggers a Task (`T`), which produces a Task Report (`TR`), and so on. Every transition is a reviewable gate.

![FlowGate workflow](assets/images/flowgate-workflow.png)

- **Typed documents & auto-numbering** — `R` (requirement), `T` (task), `TR` (task report), conversation docs, and more, each numbered and chained within a group.
- **Review gates** — approve / reject-with-reason / request-revision, with full rejection history kept on the document.
- **Remote worker API** — agents authenticate with **scoped Bearer tokens** and submit work over HTTP. A **dry-run** mode validates a submission (URL, token, fields, permissions) without consuming the token.
- **Structured clarification (Q)** — when an agent is unsure, it doesn't guess and it doesn't pop a dialog into the void: it **registers a question bound to the document**, which the system routes for a definite answer.
- **Live updates** — Server-Sent Events push status changes to every watcher in real time, with a notification feed and unread badge.
- **Mentions & handoffs** — generated, copy-ready mention blocks carry the exact context (references, predecessors) the next worker needs.
- **Continuous (unmanned) work** — a scoped continuation token lets an agent run a self-chaining sequence: the server auto-advances through the workflow toward a target stage, optionally pausing for human Q&A in review mode, so a long task can run unattended without dropping its gates.
- **Conversation documents** — a dedicated chat-style document type (`CH`) for back-and-forth that doesn't fit the requirement → task → report spine.

---

## Tech & engineering

| Area | What's in the box |
|------|-------------------|
| **Backend** | Python · FastAPI · ~31k LOC of source across ~17 v1 route modules (documents, workflow, RBAC, tokens, remote tools, SSE, dashboard, inbox, Q&A, …) |
| **Database** | SQLite / MySQL / PostgreSQL — **50 ordered migrations** generated per backend (one SQL set each) plus a runtime dialect-translation layer (`db/dialect.py`), clean module split (`api` / `auth` / `db` / `rbac` / `workflow` / `numbering`) |
| **Auth & security** | JWT + bcrypt + **TOTP 2FA** (with backup codes) + refresh/blacklist · per-token action scopes · `slowapi` rate limiting |
| **Frontend** | Vue 3 · Pinia · vue-i18n (ko / ja / en) · vue-router · Vite · ~27k LOC |
| **Testing** | **~33k LOC of tests** — the test suite is larger than the source it covers |
| **Ops** | **Docker / docker-compose** (one-command, SQLite or bundled Postgres/MySQL) · `systemd` unit (`deploy/flowgate.service`) · one-shot `setup.sh` (Linux) / `setup.ps1` (Windows) · selectable DB backend · Redis-ready |

---

## Quick start (local, SQLite)

> Requires Python 3 and Node.js.

```bash
# 1. Backend
cd server
cp .env.sample .env          # set SECRET_KEY, DB_TYPE=sqlite, CONTEXT=/flowgate
pip install -r requirements.txt
python dev.py                 # serves on http://0.0.0.0:8088  (auto-reload)

# 2. Frontend (separate terminal)
cd client
npm install
npm run dev                   # Vite dev server with HMR
```

Create the first user with `server/create_dev_user.py`, then open the client and log in.

For a one-command staging deploy on **Linux** (venv + `.env` + client build + `systemd` install + admin account), run `./setup.sh` from the repo root. On **Windows**, run `.\setup.ps1` (venv + `.env` + client build + generated `run.bat` launcher + admin account). Both default to SQLite; target MySQL/MariaDB or PostgreSQL by presetting `DB_TYPE` (e.g. `DB_TYPE=postgres DB_HOST=… ./setup.sh`, or `.\setup.ps1 -DbType postgres -DbHost …`).

## Quick start (Docker)

> Requires Docker with the Compose plugin. Builds the client and server into one image; persistent state (SQLite DB, document storage, generated secrets) lives in the `flowgate-data` volume.

```bash
# SQLite (default) — one container, state on a named volume
docker compose up -d --build
# → http://localhost:8089/flowgate

# Seed the first admin (idempotent; re-runs skip if it already exists)
docker compose exec flowgate python create_dev_user.py \
  --username admin --email admin@flowgate.local --password 'ChangeMe!' --admin
```

`SECRET_KEY` and the token pepper are generated **once** on first start and persisted to the data volume (never rotated on restart, so issued tokens keep working). To run against a bundled database instead, start with a profile and set `DB_TYPE` — the app waits for the DB and auto-migrates the schema on boot:

```bash
DB_TYPE=postgres docker compose --profile postgres up -d --build   # + Postgres 16
DB_TYPE=mysql    docker compose --profile mysql    up -d --build   # + MariaDB 11
```

### Talking to it as an agent

```bash
# Submit an artifact (use dry_run:true first to validate without consuming the token)
curl -X POST http://<host>:8088/flowgate/api/v1/inbox \
  -H "Authorization: Bearer <scoped-token>" \
  -H "Content-Type: application/json" \
  -d '{ "action":"new", "project":"flowgate", "module":"default",
        "group_name":"flowgate.default.0072", "doc_type":"TR",
        "prev_doc_id":"flowgate.default.0072.0002-T",
        "title":"...", "content":"..." }'
```

The API exposes typed help endpoints (`GET /flowgate/api/v1/help/doc_type`) so an agent can discover document types and required fields at runtime.

---

## Project layout

```
FlowGate/
├── server/                    # FastAPI backend
│   ├── routers/               # app wiring (main.py mounts every sub-router)
│   ├── modules/flow_gate/     # the real domain
│   │   ├── api/               # inbox, tokens, v1 routes (documents, workflow, SSE, dashboard, q&a, remote…)
│   │   ├── auth/  rbac/        # JWT + 2FA, role-based access
│   │   ├── workflow/  numbering/
│   │   ├── documents/  conversation.py  process_service.py
│   │   └── db/                # multi-backend data access
│   ├── sql/migrations/        # 50 ordered migrations × {sqlite, mysql, postgres}
│   └── tests/                 # ~33k LOC
├── client/                    # Vue 3 + Pinia + Vite SPA
├── Dockerfile                 # multi-stage: build client → Python runtime
├── docker-compose.yml         # one-command stack (SQLite / Postgres / MySQL profiles)
├── deploy/
│   ├── flowgate.service       # systemd unit template (rendered by setup.sh)
│   └── docker-entrypoint.sh   # container init: secrets, DB wait, admin seed
├── setup.sh                   # one-shot staging deploy (Linux)
└── setup.ps1                  # one-shot staging deploy (Windows)
```

---

## Status

FlowGate is a **working system**, not a prototype — it runs the document pipeline that drives its own development. The backend, auth, workflow engine, multi-database support, and remote API are production-grade; the frontend's conversation and review-UI polish is the area under active iteration.

Recently landed: full **multi-database** migration sets (MySQL / PostgreSQL alongside SQLite) plus a runtime dialect-translation layer · **continuous (unmanned) work** chains · a notification feed · conversation documents · and a cleaned-up router layout (stray test files moved out of `routers/`).

**Roadmap:**

- **Git integration** — tie the document pipeline to the actual repository. Link artifacts to commits / branches / PRs so an approved Task Report maps to a verifiable code state, and let gate transitions require or trigger Git actions (branch, commit, open PR). The gate stops being a claim *about* the code and becomes anchored *to* it.
- **Command-based AI integration** — drive the whole pipeline natively from CLI agents (e.g. Claude Code) instead of hand-assembled HTTP calls. An agent submits, advances, clarifies, and reviews work through first-class commands, so the gate becomes part of the agent's normal command loop.
- **GUI transition** — evolve the current browser SPA toward a full graphical client for authoring, reviewing, and conversing across pipelines — a desktop-grade workspace rather than a set of web pages.

---

## License

Released under the [MIT License](LICENSE) — © 2026 horrible-gh.
