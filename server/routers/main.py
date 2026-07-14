import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from . import files as _files_router
from modules.flow_gate.auth.auth_api import router as _auth_router
from modules.flow_gate.documents.routers.documents import router as _documents_router
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
from modules.flow_gate.services.git_service import GitServiceError
from config import settings
from startup import run_all as _bootstrap
from modules.flow_gate import db as _flowgate_db
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import LogAssist.log as logger

ALLOWED_ORIGIN = settings.ALLOWED_ORIGIN.split(",")
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


app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# RequestValidationError handler must be registered BEFORE SlowAPIMiddleware
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
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
    error: dict = {"code": exc.code, "message": exc.message}
    if exc.details:
        error["details"] = exc.details
    return JSONResponse(
        status_code=exc.status,
        content={"ok": False, "error": error},
    )

app.add_middleware(SlowAPIMiddleware)

# Legacy login/register endpoints removed — replaced by /flowgate/auth/login (B001 fix)
# app.include_router(login.router, prefix=f"{CONTEXT}/login", tags=["Login"])
# app.include_router(logout.router, prefix=f"{CONTEXT}/logout", tags=["Logout"])
# app.include_router(register.router, prefix=f"{CONTEXT}/register", tags=["Register"])
app.include_router(_auth_router, prefix=f"{CONTEXT}/auth", tags=["Auth"])
app.include_router(_documents_router, prefix=f"{CONTEXT}/api/v1", tags=["Documents"])
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
app.include_router(_files_router.router, prefix="/api", tags=["Files"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGIN,
    allow_credentials=True,
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
if _os.path.isdir(_CLIENT_DIST):
    _assets_dir = _os.path.join(_CLIENT_DIST, "assets")
    if _os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    _CTX = CONTEXT.strip("/")

    # The HTML entry shells (index/main/settings.html) are NOT content-hashed, so a
    # browser that heuristically caches them keeps loading an OLD shell that still
    # references a pre-rebuild `main-<oldhash>.js` — the app renders entirely from
    # cache and a fresh `run.bat` rebuild never reaches the screen (group 0126: the
    # accordion was built into dist but the cached shell hid it). Force the shells to
    # revalidate every load; the hashed /assets/* stay immutably cacheable.
    def _html(path: str) -> FileResponse:
        return FileResponse(path, headers={"Cache-Control": "no-cache, max-age=0"})

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _serve_client(full_path: str):
        # Never shadow the API surface; let those 404 as JSON.
        if _CTX and (full_path == _CTX or full_path.startswith(f"{_CTX}/")):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        # Real file on disk (favicon, etc.; /assets handled by the mount above).
        # An HTML shell requested by its real name must still revalidate.
        candidate = _os.path.join(_CLIENT_DIST, full_path)
        if full_path and _os.path.isfile(candidate):
            if candidate.endswith(".html"):
                return _html(candidate)
            return FileResponse(candidate)

        # Multi-page history fallback (mirrors vite.config multiPageHistoryFallback).
        if full_path == "main" or full_path.startswith("main/"):
            return _html(_os.path.join(_CLIENT_DIST, "main.html"))
        if full_path == "settings" or full_path.startswith("settings/"):
            return _html(_os.path.join(_CLIENT_DIST, "settings.html"))
        return _html(_os.path.join(_CLIENT_DIST, "index.html"))

