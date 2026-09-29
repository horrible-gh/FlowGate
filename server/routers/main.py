import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from . import files as _files_router
from modules.flow_gate.auth.auth_api import router as _auth_router
from modules.flow_gate.documents.routers.documents import router as _documents_router
from modules.flow_gate.documents.routers.conversation_turns import router as _conversation_turns_router
from modules.flow_gate.documents.routers.work_plan import router as _work_plan_router
from modules.flow_gate.documents.routers.tr2 import router as _tr2_router
from modules.flow_gate.settings.routers.system import router as _settings_system_router
from modules.flow_gate.settings.routers.users import router as _settings_users_router
from modules.flow_gate.settings.routers.project_settings import router as _settings_project_router
from modules.flow_gate.settings.routers.env_vars_commands import router as _env_vars_commands_router
from modules.flow_gate.settings.routers.ai_settings import router as _ai_settings_router
from modules.flow_gate.rbac.routers import router as _rbac_router
from modules.flow_gate.workflow.routers.workflow import router as _workflow_router
from modules.flow_gate.api.token_routes import router as _token_router
from modules.flow_gate.api.inbox_routes import router as _inbox_router
from modules.flow_gate.api.v1.list_routes import router as _list_router
from modules.flow_gate.api.v1.help_routes import router as _help_router
from modules.flow_gate.api.v1.document_routes import router as _document_router
from modules.flow_gate.api.v1.project_routes import router as _project_router
from modules.flow_gate.api.v1.group_routes import router as _group_router
from modules.flow_gate.api.v1.events.sse_routes import router as _sse_router
from modules.flow_gate.api.v1.qa_routes import router as _qa_router
from modules.flow_gate.api.v1.q_tapi_routes import router as _q_tapi_router
from modules.flow_gate.api.v1.workflow_head_routes import router as _workflow_head_router
from modules.flow_gate.api.v1.workflow_decision_routes import router as _workflow_decision_router
from modules.flow_gate.api.v1.module_routes import router as _module_router
from modules.flow_gate.api.v1.legacy_misc_routes import router as _legacy_misc_router
from modules.flow_gate.api.v1.tree_routes import router as _tree_router
from modules.flow_gate.api.v1.file_transfer_routes import router as _file_transfer_router
from modules.flow_gate.api.v1.slug_routes import router as _slug_router
from modules.flow_gate.api.v1.dashboard_routes import router as _dashboard_router
from modules.flow_gate.api.v1.remote_routes import router as _remote_router
from modules.flow_gate.api.v1.test_run_routes import router as _test_run_router
from modules.flow_gate.api.v1.ai_invoke_routes import router as _ai_invoke_router
from modules.flow_gate.api.v1.engine_recipe_routes import router as _engine_recipe_router
from modules.flow_gate.api.v1.git_routes import router as _git_router
from modules.flow_gate.api.v1.conversation_routes import router as _conversation_worker_router
from modules.flow_gate.api.v1.chat_settings_routes import router as _chat_settings_router
from modules.flow_gate.api.v1.ui_settings_routes import router as _ui_settings_router
from modules.flow_gate.api.v1.snapshot_routes import router as _snapshot_router
from modules.flow_gate.api.v1.source_bundle_routes import router as _source_bundle_router, overview_router as _source_bundle_overview_router
from modules.flow_gate.api.v1.agent_routes import router as _agent_router
from modules.flow_gate.api.v1.agent_job_routes import router as _agent_job_router
from modules.flow_gate.api.request_scope_middleware import RequestScopeMiddleware
from modules.flow_gate.services.git_service import GitServiceError
from modules.flow_gate.services.git.credentials import git_error_envelope
from modules.flow_gate.services.mutation_policy import (
    GroupMutationPolicyMiddleware,
    MutationPolicyError,
    annotate_mutation_routes,
    mutation_error_response,
)
from modules.flow_gate.utils.cors_settings import parse_allowed_origins, resolve_allow_credentials
from config import settings
from startup import run_all as _bootstrap
from modules.flow_gate import db as _flowgate_db
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import LogAssist.log as logger

ALLOWED_ORIGIN = parse_allowed_origins(settings.ALLOWED_ORIGIN)
CONTEXT = settings.CONTEXT

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT]
)


# ── Lifespan ─────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan handler executed on server startup/shutdown."""
    _bootstrap()          # console encoding + pre-build PercentileTable
    # Cooperative-shutdown signal for long-lived SSE streams. Without this, an
    # open EventSource keeps its response generator running forever, and uvicorn
    # waits on that in-flight connection — so the server only exits once the
    # browser is closed (group 0102 R0001). SSE generators race this event and
    # stop promptly when it is set. (timeout_graceful_shutdown on every startup
    # path is the belt-and-suspenders backstop for any other stuck request.)
    app.state.shutdown_event = asyncio.Event()
    yield                  # ← server running
    app.state.shutdown_event.set()
    try:
        from modules.flow_gate.services import snapshot_materialization_service
        snapshot_materialization_service.shutdown()
    except Exception:
        logger.warning("snapshot cleanup shutdown failed", exc_info=True)
    try:
        from modules.flow_gate.services import source_bundle_cleanup_service
        source_bundle_cleanup_service.shutdown()
    except Exception:
        logger.warning("Source Bundle cleanup shutdown failed", exc_info=True)


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# RequestValidationError handler must be registered BEFORE SlowAPIMiddleware
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Machine payloads can contain one-time enrollment secrets. Never log their body.
    if request.url.path.startswith(f"{CONTEXT}/api/v1/agent/") or request.url.path.startswith(f"{CONTEXT}/api/v1/system/agents"):
        first = exc.errors()[0] if exc.errors() else {}
        loc = first.get("loc") or ("body",)
        field = str(loc[-1]) if len(loc) > 1 else "body"
        return JSONResponse(status_code=422, content={"ok": False, "error": {
            "code": "validation_failed", "message": "Invalid Agent field",
            "details": {"field": field, "reason": first.get("type", "invalid")},
        }})
    logger.debug("💥 Validation error occurred")
    logger.debug("⛳ Path:", request.url)
    logger.debug("📦 Details:\n", exc.errors())
    logger.debug("📨 Original body:\n", await request.body())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()}
    )


# GitServiceError is a plain domain Exception carrying (http_status, code, message)
# — FastAPI will NOT auto-convert it to a 4xx. git_routes wraps every handler in a
# local `_guard` that turns it into the {"ok": false, "error": {...}} envelope, but
# other endpoints that reuse git_service (token/issue, ai-invoke/start build the
# conflict mention via git_service.list_conflicts) had no such guard, so a
# GitServiceError from a stale/non-open merge session leaked out as a bare 500
# (flowgate.default.0233 B0001). This global handler converts it once for every
# route — present and future — matching the git_routes envelope exactly.
@app.exception_handler(GitServiceError)
async def git_service_exception_handler(request: Request, exc: GitServiceError):
    if exc.diagnostic:
        logger.warning(f"Git operation failed ({exc.code}): {exc.diagnostic}")
    return JSONResponse(status_code=exc.status, content=git_error_envelope(exc))


@app.exception_handler(MutationPolicyError)
async def mutation_policy_exception_handler(request: Request, exc: MutationPolicyError):
    return mutation_error_response(exc)


app.add_middleware(SlowAPIMiddleware)
# 0291 P3-1 / 4-8: 요청 하나 = DB 읽기 캐시 하나 + 쿼리 계측 하나.
# add_middleware 는 스택을 바깥쪽으로 쌓으므로 **나중에 추가된 것이 먼저 실행된다.**
# 이 줄이 SlowAPIMiddleware 뒤에 오는 덕에 스코프가 바깥에 서고, 레이트리밋에 걸려
# 거절되는 요청까지 계측에 잡힌다. 스코프가 안쪽에 있으면 그 요청은 통계에서 사라진다.
app.add_middleware(RequestScopeMiddleware)
# 0378: the durable group lease is the authoritative write boundary for every route.
app.add_middleware(GroupMutationPolicyMiddleware)

# Legacy login/register endpoints removed — replaced by /flowgate/auth/login (B001 fix)
# app.include_router(login.router, prefix=f"{CONTEXT}/login", tags=["Login"])
# app.include_router(logout.router, prefix=f"{CONTEXT}/logout", tags=["Logout"])
# app.include_router(register.router, prefix=f"{CONTEXT}/register", tags=["Register"])
app.include_router(_auth_router, prefix=f"{CONTEXT}/auth", tags=["Auth"])
app.include_router(_documents_router, prefix=f"{CONTEXT}/api/v1", tags=["Documents"])
app.include_router(_conversation_turns_router, prefix=f"{CONTEXT}/api/v1", tags=["Documents"])
# Work plan (WP) routes live under the same /documents prefix but in their own module:
# documents.py is already 112KB and the plan paths share nothing with it but the prefix.
app.include_router(_work_plan_router, prefix=f"{CONTEXT}/api/v1", tags=["Documents"])
app.include_router(_tr2_router, prefix=f"{CONTEXT}/api/v1", tags=["Documents"])
app.include_router(_settings_system_router, prefix=f"{CONTEXT}/api/v1", tags=["SystemSettings"])
app.include_router(_settings_users_router, prefix=f"{CONTEXT}/api/v1", tags=["UserAdmin"])
app.include_router(_settings_project_router, prefix=f"{CONTEXT}/api/v1", tags=["ProjectSettings"])
app.include_router(_env_vars_commands_router, prefix=f"{CONTEXT}/api/v1", tags=["EnvVarsCommands"])
app.include_router(_ai_settings_router, prefix=f"{CONTEXT}/api/v1", tags=["AiSettings"])
app.include_router(_rbac_router, prefix=f"{CONTEXT}/rbac", tags=["RBAC"])
app.include_router(_workflow_router, prefix=f"{CONTEXT}", tags=["Workflow"])
app.include_router(_token_router, prefix=f"{CONTEXT}", tags=["TokenIssue"])
app.include_router(_inbox_router, prefix=f"{CONTEXT}", tags=["Inbox"])
app.include_router(_list_router, prefix=f"{CONTEXT}", tags=["OutboundList"])
app.include_router(_help_router, prefix=f"{CONTEXT}", tags=["Help"])
app.include_router(_document_router, prefix=f"{CONTEXT}", tags=["OutboundDocument"])
app.include_router(_project_router, prefix=f"{CONTEXT}", tags=["OutboundProject"])
app.include_router(_group_router, prefix=f"{CONTEXT}", tags=["OutboundGroup"])
app.include_router(_sse_router, prefix=f"{CONTEXT}", tags=["SSE"])
app.include_router(_qa_router, prefix=f"{CONTEXT}", tags=["QA"])
app.include_router(_q_tapi_router, prefix=f"{CONTEXT}", tags=["QTapi"])
app.include_router(_workflow_head_router, prefix=f"{CONTEXT}", tags=["WorkflowHead"])
app.include_router(_workflow_decision_router, prefix=f"{CONTEXT}", tags=["WorkflowDecision"])
app.include_router(_module_router, prefix=f"{CONTEXT}", tags=["Modules"])
app.include_router(_legacy_misc_router, prefix=f"{CONTEXT}", tags=["LegacyMisc"])
app.include_router(_tree_router, prefix=f"{CONTEXT}", tags=["Tree"])
app.include_router(_file_transfer_router, prefix=f"{CONTEXT}", tags=["FileTransfer"])
app.include_router(_slug_router, prefix=f"{CONTEXT}", tags=["Slug"])
app.include_router(_dashboard_router, prefix=f"{CONTEXT}", tags=["Dashboard"])
app.include_router(_remote_router, prefix=f"{CONTEXT}", tags=["RemoteTool"])
app.include_router(_test_run_router, prefix=f"{CONTEXT}", tags=["TestRun"])
app.include_router(_ai_invoke_router, prefix=f"{CONTEXT}", tags=["AiInvoke"])
app.include_router(_engine_recipe_router, prefix=f"{CONTEXT}", tags=["EngineRecipes"])
app.include_router(_git_router, prefix=f"{CONTEXT}", tags=["Git"])
app.include_router(_conversation_worker_router, prefix=f"{CONTEXT}", tags=["Conversation"])
app.include_router(_chat_settings_router, prefix=f"{CONTEXT}/api/v1", tags=["ChatSettings"])
app.include_router(_ui_settings_router, prefix=f"{CONTEXT}/api/v1", tags=["UiSettings"])
app.include_router(_snapshot_router, prefix=f"{CONTEXT}", tags=["Snapshots"])
app.include_router(_source_bundle_router, prefix=f"{CONTEXT}", tags=["SourceBundles"])
app.include_router(_source_bundle_overview_router, prefix=f"{CONTEXT}", tags=["SourceBundles"])
app.include_router(_agent_router, prefix=f"{CONTEXT}/api/v1", tags=["Agents"])
app.include_router(_agent_job_router, prefix=f"{CONTEXT}/api/v1", tags=["AgentJobs"])
app.include_router(_files_router.router, prefix="/api", tags=["Files"])
# Every mutation route must carry an inventory classification. Group routes also name
# the standard resolver used by GroupMutationPolicyMiddleware.
annotate_mutation_routes(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGIN,
    allow_credentials=resolve_allow_credentials(ALLOWED_ORIGIN),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ── Built client (staging/prod) ──────────────────────────────────
# Serve client/dist when present (produced by client/build.sh). Mirrors the
# dev-server multi-page routing in vite.config.ts: /main → main.html,
# /settings → settings.html, everything else → index.html (login). Skipped
# entirely in dev (no dist), where Vite serves the client on its own port.
import os as _os
from fastapi.staticfiles import StaticFiles

_CLIENT_DIST = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "..", "..", "client", "dist")
)
# v0.2 (T0005): a physically separate client with its own build output, mounted under
# /app and /app/assets/*. Kept independent of every _CLIENT_DIST check below so a
# missing/broken client_gui build can never block v0.1's own startup or static serving,
# and vice versa.
_CLIENT_GUI_DIST = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "..", "..", "client_gui", "dist")
)
_client_dist_exists = _os.path.isdir(_CLIENT_DIST)


def _dist_ready(dist_dir: str) -> bool:
    """A dist dir counts as ready only once it has an index.html, not merely once the
    directory exists. The Docker image's client_gui build stage (Dockerfile) creates
    an empty client_gui/dist as a failure fallback so the runtime stage's COPY never
    fails outright — plain isdir() would then be True and _serve_client's
    FileResponse(index.html) would error on a file that isn't there, instead of the
    clean 404 a genuinely-missing v0.2 build gives everywhere else.
    """
    return _os.path.isdir(dist_dir) and _os.path.isfile(_os.path.join(dist_dir, "index.html"))


_client_gui_dist_exists = _dist_ready(_CLIENT_GUI_DIST)
# Symlink-resolved once at startup so every per-request containment check below
# compares against the real on-disk directory, not a path an on-disk symlink
# could redirect elsewhere.
_CLIENT_DIST_REAL = _os.path.realpath(_CLIENT_DIST)
_CLIENT_GUI_DIST_REAL = _os.path.realpath(_CLIENT_GUI_DIST)


def _safe_dist_file(dist_dir_real: str, relative: str):
    """Resolve `relative` under `dist_dir_real` and return the real path, or None if
    it escapes the dist directory.

    `full_path` is the URL-decoded value Starlette's `{full_path:path}` converter
    hands the route — a request for `/app/%2e%2e/%2e%2e/server/.env` arrives here as
    the string "app/../../server/.env". Joining that onto `_CLIENT_GUI_DIST` without
    resolving `..`/symlinks first would let FileResponse serve arbitrary files outside
    dist (e.g. server/.env). Every candidate must clear this check before FileResponse.
    """
    candidate = _os.path.realpath(_os.path.join(dist_dir_real, relative))
    if candidate != dist_dir_real and not candidate.startswith(dist_dir_real + _os.sep):
        return None
    return candidate

if _client_dist_exists:
    _assets_dir = _os.path.join(_CLIENT_DIST, "assets")
    if _os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

if _client_gui_dist_exists:
    # Deliberately its own mount, not shared with /assets above, so a v0.2 rebuild's
    # hashed filenames can never collide with or shadow v0.1's (T0005 §3).
    _app_assets_dir = _os.path.join(_CLIENT_GUI_DIST, "assets")
    if _os.path.isdir(_app_assets_dir):
        app.mount("/app/assets", StaticFiles(directory=_app_assets_dir), name="app_assets")

if _client_dist_exists or _client_gui_dist_exists:
    _CTX = CONTEXT.strip("/")

    # The HTML entry shells (index/main/settings.html, and v0.2's index.html) are NOT
    # content-hashed, so a browser that heuristically caches them keeps loading an OLD
    # shell that still references a pre-rebuild `main-<oldhash>.js` — the app renders
    # entirely from cache and a fresh rebuild never reaches the screen (group 0126: the
    # accordion was built into dist but the cached shell hid it). Force the shells to
    # revalidate every load; the hashed /assets/* and /app/assets/* stay immutably
    # cacheable.
    def _html(path: str) -> FileResponse:
        return FileResponse(path, headers={"Cache-Control": "no-cache, max-age=0"})

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_client(full_path: str):
        # Never shadow the API surface; let those 404 as JSON.
        if _CTX and (full_path == _CTX or full_path.startswith(f"{_CTX}/")):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        # v0.2 SPA (T0005 §6): /app and every /app/* deep-link/refresh resolve to the
        # client_gui shell, independent of whether v0.1's own dist exists. This branch is
        # checked before v0.1's real-file/main/settings handling below so a v0.2 route
        # can never fall through into v0.1's index.html (the /login shell).
        if full_path == "app" or full_path.startswith("app/"):
            if not _client_gui_dist_exists:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            # /app/assets/* is served by the mount above; a request that still reaches
            # here under app/assets/ is a genuine miss (e.g. a stale hashed filename),
            # not a route to fall back to the SPA shell for.
            if full_path == "app/assets" or full_path.startswith("app/assets/"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            app_relative = full_path[len("app/") :] if full_path != "app" else ""
            if app_relative:
                app_candidate = _safe_dist_file(_CLIENT_GUI_DIST_REAL, app_relative)
                if app_candidate and _os.path.isfile(app_candidate) and not app_candidate.endswith(".html"):
                    return FileResponse(app_candidate)
            return _html(_os.path.join(_CLIENT_GUI_DIST, "index.html"))

        if not _client_dist_exists:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        # Real file on disk (favicon, etc.; /assets handled by the mount above).
        # An HTML shell requested by its real name must still revalidate.
        candidate = _safe_dist_file(_CLIENT_DIST_REAL, full_path) if full_path else None
        if candidate and _os.path.isfile(candidate):
            if candidate.endswith(".html"):
                return _html(candidate)
            return FileResponse(candidate)

        # Multi-page history fallback (mirrors vite.config multiPageHistoryFallback).
        if full_path == "main" or full_path.startswith("main/"):
            return _html(_os.path.join(_CLIENT_DIST, "main.html"))
        if full_path == "settings" or full_path.startswith("settings/"):
            return _html(_os.path.join(_CLIENT_DIST, "settings.html"))
        return _html(_os.path.join(_CLIENT_DIST, "index.html"))

