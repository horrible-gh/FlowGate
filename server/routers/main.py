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
from modules.flow_gate.api.v1.message_image_routes import router as _message_image_router
from modules.flow_gate.documents.routers.work_plan import router as _work_plan_router
from modules.flow_gate.documents.routers.tr2 import router as _tr2_router
from modules.flow_gate.settings.routers.system import router as _settings_system_router
from modules.flow_gate.settings.routers.users import router as _settings_users_router
from modules.flow_gate.settings.routers.system_users import router as _system_users_router
from modules.flow_gate.settings.routers.workspaces import router as _workspaces_router
from modules.flow_gate.settings.routers.project_settings import router as _settings_project_router
from modules.flow_gate.settings.routers.env_vars_commands import router as _env_vars_commands_router
from modules.flow_gate.settings.routers.ai_settings import router as _ai_settings_router
from modules.flow_gate.api.v1.ai_evaluation_routes import router as _ai_evaluation_router
from modules.flow_gate.api.v1.ai_connection_test_routes import router as _ai_connection_test_router
from modules.flow_gate.api.v1.ai_group_routes import router as _ai_group_router
from modules.flow_gate.api.v1.ai_overview_routes import router as _ai_overview_router
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
from modules.flow_gate.api.v1.client_report_routes import router as _client_report_router
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
# flowgate.v02.0032 T#1 (D0006 §2.1, §5.1): the v0.2 Conversation contract (server/flowgate/).
from flowgate.conversation.routes import router as _conversation_router
# flowgate.v02.0053 T#2: the v0.2 card (Node) run settings (server/flowgate/card_settings).
from flowgate.card_settings.routes import router as _card_settings_router
from flowgate.ai_roles.routes import router as _ai_roles_router
# flowgate.v02.0056 T#3: a card line's write and the v0.2 card memos (server/flowgate/card_documents, card_memos).
from flowgate.card_documents.routes import router as _card_documents_router
from flowgate.card_document_edit.routes import router as _card_document_edit_router
from flowgate.graph_read.routes import router as _ai_graph_read_router
from flowgate.chat_control.routes import router as _chat_control_router
from flowgate.card_memos.routes import router as _card_memos_router
from flowgate.card_merge.routes import router as _card_merge_router
from flowgate.work_plan_deploy.routes import router as _work_plan_deploy_router
# flowgate.v02.0061 T#13: the Graph Time Machine (server/flowgate/graph_time_machine).
from flowgate.graph_time_machine.routes import router as _graph_time_machine_router
# flowgate.v02.0085 T#2: the confirm gate decision history (server/flowgate/gate_decisions).
from flowgate.gate_decisions.routes import router as _gate_decisions_router
from modules.flow_gate.api.v1.chat_command_routes import router as _chat_command_router
# flowgate.chat.0017 CH0002: AI attachment upload requests, the user side (decision, preview, settings).
from modules.flow_gate.api.v1.attachment_request_routes import router as _attachment_request_router
from modules.flow_gate.api.v1.ui_settings_routes import router as _ui_settings_router
from modules.flow_gate.api.v1.voice_routes import router as _voice_router
from modules.flow_gate.api.v1.voice_stream_routes import router as _voice_stream_router
from modules.flow_gate.api.v1.message_audio_routes import router as _message_audio_router
from modules.flow_gate.api.v1.snapshot_routes import router as _snapshot_router
from modules.flow_gate.api.v1.self_check_routes import router as _self_check_router
from modules.flow_gate.api.v1.self_check_card_routes import router as _self_check_card_router
from modules.flow_gate.api.v1.self_check_routes import draft_router as _self_check_draft_router
from modules.flow_gate.api.v1.self_check_routes import group_router as _self_check_group_router
from modules.flow_gate.api.v1.self_check_routes import recovery_router as _self_check_recovery_router
from modules.flow_gate.api.v1.agent_routes import router as _agent_router
from modules.flow_gate.api.v1.agent_job_routes import router as _agent_job_router
from modules.flow_gate.graph_workflow.api.canvas_routes import router as _graph_canvas_router
from modules.flow_gate.graph_workflow.api.graph_routes import router as _graph_definition_router
from modules.flow_gate.graph_workflow.api.run_routes import router as _graph_run_router
from modules.flow_gate.api.request_scope_middleware import RequestScopeMiddleware
from modules.flow_gate.api.request_observability import RequestIdMiddleware, install_access_log_redaction
from modules.flow_gate.services.git_service import GitServiceError
from modules.flow_gate.services.git.credentials import git_error_envelope
from modules.flow_gate.workflow.pipeline_service import WorkflowSlotConflictError
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
    install_server_logs()  # idempotent; see its definition below
    # Cooperative-shutdown signal for long-lived SSE streams. Without this, an
    # open EventSource keeps its response generator running forever, and uvicorn
    # waits on that in-flight connection — so the server only exits once the
    # browser is closed (group 0102 R0001). SSE generators race this event and
    # stop promptly when it is set. (timeout_graceful_shutdown on every startup
    # path is the belt-and-suspenders backstop for any other stuck request.)
    app.state.shutdown_event = asyncio.Event()
    yield                  # ← server running
    app.state.shutdown_event.set()
    from modules.flow_gate.services.voice_service import manager as voice_manager
    from modules.flow_gate.services import voice_streams
    await asyncio.to_thread(voice_streams.shutdown)
    await asyncio.to_thread(voice_manager.shutdown)
    try:
        from modules.flow_gate.services import snapshot_materialization_service
        snapshot_materialization_service.shutdown()
    except Exception:
        logger.warning("snapshot cleanup shutdown failed", exc_info=True)
    try:
        from modules.flow_gate.services.git import instance_registry
        instance_registry.shutdown()
    except Exception:
        logger.warning("server instance shutdown failed", exc_info=True)
    try:
        # flowgate.v02.0129 T#2: the remote AI Job supervisor started by the bootstrap.
        from modules.flow_gate.services.ai_invoke import remote_supervisor
        remote_supervisor.stop_supervisor()
    except Exception:
        logger.warning("remote AI Job supervisor shutdown failed", exc_info=True)


from modules.flow_gate.api.input_validation import reject_nul_input
from modules.flow_gate.api.request_observability import unhandled_error_response

app = FastAPI(lifespan=lifespan, dependencies=[Depends(reject_nul_input)])
app.add_exception_handler(Exception, unhandled_error_response)
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


# 0660 T0004 §2 (RC2): a refused workflow slot write (the slot already holds another
# document, or the document already sits in another slot) is a workflow conflict, not a
# server fault. Uncaught it surfaced as a bare 500 the create dialog could only render as
# "an error occurred". The exception already carries the identified {"error": {...}}
# envelope (document/slot ids only, no paths), so answer it as the 409 it is — once,
# here, for every route that registers a workflow result.
@app.exception_handler(WorkflowSlotConflictError)
async def workflow_slot_conflict_exception_handler(request: Request, exc: WorkflowSlotConflictError):
    return JSONResponse(status_code=409, content=exc.body())


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
app.include_router(_message_image_router, prefix=f"{CONTEXT}/api/v1")
# Work plan (WP) routes live under the same /documents prefix but in their own module:
# documents.py is already 112KB and the plan paths share nothing with it but the prefix.
app.include_router(_work_plan_router, prefix=f"{CONTEXT}/api/v1", tags=["Documents"])
app.include_router(_tr2_router, prefix=f"{CONTEXT}/api/v1", tags=["Documents"])
app.include_router(_settings_system_router, prefix=f"{CONTEXT}/api/v1", tags=["SystemSettings"])
app.include_router(_settings_users_router, prefix=f"{CONTEXT}/api/v1", tags=["UserAdmin"])
app.include_router(_system_users_router, prefix=f"{CONTEXT}/api/v1", tags=["SystemUsers"])
app.include_router(_workspaces_router, prefix=f"{CONTEXT}/api/v1", tags=["Workspaces"])
app.include_router(_settings_project_router, prefix=f"{CONTEXT}/api/v1", tags=["ProjectSettings"])
app.include_router(_env_vars_commands_router, prefix=f"{CONTEXT}/api/v1", tags=["EnvVarsCommands"])
app.include_router(_ai_settings_router, prefix=f"{CONTEXT}/api/v1", tags=["AiSettings"])
app.include_router(_ai_evaluation_router, prefix=f"{CONTEXT}/api/v1", tags=["AiEvaluation"] )
app.include_router(_ai_connection_test_router, prefix=f"{CONTEXT}/api/v1", tags=["AiConnectionTest"])
app.include_router(_ai_group_router, prefix=f"{CONTEXT}/api/v1", tags=["AiGroups"])
app.include_router(_ai_overview_router, prefix=f"{CONTEXT}/api/v1", tags=["AiOverview"])
app.include_router(_rbac_router, prefix=f"{CONTEXT}/rbac", tags=["RBAC"])
app.include_router(_workflow_router, prefix=f"{CONTEXT}", tags=["Workflow"])
app.include_router(_token_router, prefix=f"{CONTEXT}", tags=["TokenIssue"])
app.include_router(_inbox_router, prefix=f"{CONTEXT}", tags=["Inbox"])
app.include_router(_list_router, prefix=f"{CONTEXT}", tags=["OutboundList"])
app.include_router(_help_router, prefix=f"{CONTEXT}", tags=["Help"])
app.include_router(_document_router, prefix=f"{CONTEXT}", tags=["OutboundDocument"])
app.include_router(_self_check_router, prefix=f"{CONTEXT}", tags=["TR Self-check"] )
app.include_router(_self_check_draft_router, prefix=f"{CONTEXT}", tags=["TR Self-check"])
app.include_router(_self_check_group_router, prefix=f"{CONTEXT}", tags=["TR Self-check"])
app.include_router(_self_check_recovery_router, prefix=f"{CONTEXT}", tags=["TR Self-check"])
app.include_router(_self_check_card_router, prefix=f"{CONTEXT}", tags=["TR Self-check"])
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
app.include_router(_client_report_router, prefix=f"{CONTEXT}", tags=["Tree"])
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
app.include_router(_conversation_router, prefix=f"{CONTEXT}/api/v1", tags=["Conversation"])
# 0670: registered after the v0.2 Conversation router, which serves the same
# /chat-commands and /chat-activity paths first.
app.include_router(_chat_command_router, prefix=f"{CONTEXT}/api/v1", tags=["ChatCommands"])
app.include_router(_ui_settings_router, prefix=f"{CONTEXT}/api/v1", tags=["UiSettings"])
app.include_router(_voice_router, prefix=f"{CONTEXT}/api/v1")
app.include_router(_voice_stream_router, prefix=f"{CONTEXT}/api/v1")
app.include_router(_message_audio_router, prefix=f"{CONTEXT}/api/v1")
app.include_router(_attachment_request_router, prefix=f"{CONTEXT}/api/v1", tags=["AttachmentRequests"])
app.include_router(_snapshot_router, prefix=f"{CONTEXT}", tags=["Snapshots"])
app.include_router(_agent_router, prefix=f"{CONTEXT}/api/v1", tags=["Agents"])
app.include_router(_agent_job_router, prefix=f"{CONTEXT}/api/v1", tags=["AgentJobs"])
app.include_router(_graph_canvas_router, prefix=f"{CONTEXT}", tags=["GraphCanvas"])
app.include_router(_graph_definition_router, prefix=f"{CONTEXT}", tags=["GraphDefinition"])
app.include_router(_graph_run_router, prefix=f"{CONTEXT}", tags=["GraphRun"])
app.include_router(_card_settings_router, prefix=f"{CONTEXT}", tags=["CardSettings"])
app.include_router(_ai_roles_router, prefix=f"{CONTEXT}", tags=["AiSettings"])
app.include_router(_card_documents_router, prefix=f"{CONTEXT}", tags=["CardDocuments"])
app.include_router(_card_document_edit_router, prefix=f"{CONTEXT}", tags=["CardDocumentEdit"])
app.include_router(_ai_graph_read_router, prefix=f"{CONTEXT}", tags=["AiGraphRead"])
# flowgate.v02test.0023: chat control tower stage 1 (CLI chat: place work cards, start the Flow's run).
app.include_router(_chat_control_router, prefix=f"{CONTEXT}", tags=["ChatControl"])
app.include_router(_card_memos_router, prefix=f"{CONTEXT}", tags=["CardMemos"])
app.include_router(_card_merge_router, prefix=f"{CONTEXT}", tags=["CardMerge"])   # flowgate.v02test.0042
app.include_router(_work_plan_deploy_router, prefix=f"{CONTEXT}", tags=["WorkPlanDeploy"])
app.include_router(_graph_time_machine_router, prefix=f"{CONTEXT}", tags=["GraphTimeMachine"])
app.include_router(_gate_decisions_router, prefix=f"{CONTEXT}", tags=["GateDecisions"])
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
    expose_headers=["Content-Disposition", "X-Request-ID"],
)
# flowgate.v02gui.0017 (NR 0003 B-R8): added last, so it is the outermost layer -- every response
# (CORS preflight, rate-limit rejections, errors) carries X-Request-ID, and handlers such as
# /auth/refresh can put the same id in their log lines.
app.add_middleware(RequestIdMiddleware)
# The SSE stream passes the access token as ?token=; keep its value out of the uvicorn access log.
install_access_log_redaction()


def install_server_logs() -> None:
    """flowgate.v02test.0050 T0005: every WARNING+ record (root logger and uvicorn.error) also goes to the
    server_logs table. logs/default.log and the console are unchanged. Idempotent. Not under TESTING=1, whose store
    has no DB: the server_logs tests install it themselves."""
    import os as _server_logs_os
    if _server_logs_os.environ.get("TESTING") == "1":
        return
    try:
        from modules.flow_gate.services import server_logs as _server_logs
        _server_logs.install()
    except Exception:  # noqa: BLE001 -- recording logs must never stop the server from starting
        import logging as _server_logs_logging
        _server_logs_logging.getLogger(__name__).warning("server_logs handler install failed", exc_info=True)


# At import (uvicorn has already configured its loggers) and again at startup, in case anything reconfigured them.
install_server_logs()

# ── Built client (staging/prod) ──────────────────────────────────
# Serve client_gui/dist when present (produced by client_gui/build.sh). flowgate.v02gui.0024:
# client_gui is the only client. Its build has two HTML entries — index.html is the /app SPA
# (every /app/* deep link), login.html the sign-in page served at / and /login (invitation
# links are /login?invite=<token>). v0.1's old /main and /settings addresses redirect into
# /app. In dev (no dist) Vite serves the client on its own port; the redirects still apply.
import os as _os
from typing import Optional as _Optional
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from .client_fallback import api_aware_fallback

_CLIENT_GUI_DIST = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "..", "..", "client_gui", "dist")
)
_LOGIN_HTML = "login.html"


def _dist_ready(dist_dir: str) -> bool:
    """A dist dir counts as ready only once it has an index.html, not merely once the
    directory exists — a half-written or emptied dist must give the clean 404 a missing
    build gives, not a FileResponse error on a file that isn't there."""
    return _os.path.isdir(dist_dir) and _os.path.isfile(_os.path.join(dist_dir, "index.html"))


_client_gui_dist_exists = _dist_ready(_CLIENT_GUI_DIST)
# Symlink-resolved once at startup so every per-request containment check below
# compares against the real on-disk directory, not a path an on-disk symlink
# could redirect elsewhere.
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


# flowgate.v02gui.0024 (R0001 §4.3): v0.1 address -> v0.2 address. A prefix matches itself and
# everything below it (`/settings/users/123` -> /app/settings/system/users); the v0.1 sub-path
# itself is not carried over because the v0.2 routes are laid out differently. More specific
# prefixes come first: settings/system/agents and settings/system/ai before settings/system,
# and the bare `settings` catch-all last.
_LEGACY_REDIRECTS = (
    ("main", "/app"),
    ("settings/security", "/app/settings/me/security"),
    ("settings/users", "/app/settings/system/users"),
    ("settings/projects", "/app/settings/system/projects"),
    ("settings/system/agents", "/app/settings/system/agents"),
    ("settings/system/ai", "/app/ai"),
    ("settings/system", "/app/settings/system/general"),
    ("settings/project", "/app/settings/project"),
    ("settings/ai-run-monitor", "/app/settings/me/account"),
    ("settings", "/app/settings"),
)


def _legacy_redirect_target(full_path: str) -> _Optional[str]:
    """The v0.2 address for a v0.1 /main or /settings path, or None for any other path."""
    path = full_path.strip("/")
    for prefix, target in _LEGACY_REDIRECTS:
        if path == prefix or path.startswith(prefix + "/"):
            return target
    return None


if _client_gui_dist_exists:
    _app_assets_dir = _os.path.join(_CLIENT_GUI_DIST, "assets")
    if _os.path.isdir(_app_assets_dir):
        app.mount("/app/assets", StaticFiles(directory=_app_assets_dir), name="app_assets")

_CTX = CONTEXT.strip("/")


# The HTML entry shells (index.html, login.html) are NOT content-hashed, so a browser that
# heuristically caches them keeps loading an OLD shell that still references a pre-rebuild
# `main-<oldhash>.js` — the app renders entirely from cache and a fresh rebuild never reaches
# the screen (group 0126). Force the shells to revalidate every load; the hashed
# /app/assets/* stay immutably cacheable.
def _html(path: str) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": "no-cache, max-age=0"})


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


async def _serve_client(full_path: str, request: Request):
    # Never shadow the API surface; let those 404 as JSON.
    if _CTX and (full_path == _CTX or full_path.startswith(f"{_CTX}/")):
        return _not_found()
    if full_path == "api" or full_path.startswith("api/"):
        return _not_found()

    # v0.1's old addresses (bookmarks, links in old documents) — whether or not a build is
    # present. The query string is kept; an unauthenticated visitor is sent on to
    # /login?next=... by the /app guard.
    target = _legacy_redirect_target(full_path)
    if target:
        query = request.url.query
        return RedirectResponse(f"{target}?{query}" if query else target, status_code=302)

    if not _client_gui_dist_exists:
        return _not_found()

    # /app and every /app/* deep-link/refresh resolve to the SPA shell (T0005 §6).
    if full_path == "app" or full_path.startswith("app/"):
        # /app/assets/* is served by the mount above; a request that still reaches
        # here under app/assets/ is a genuine miss (e.g. a stale hashed filename),
        # not a route to fall back to the SPA shell for.
        if full_path == "app/assets" or full_path.startswith("app/assets/"):
            return _not_found()
        app_relative = full_path[len("app/") :] if full_path != "app" else ""
        if app_relative:
            app_candidate = _safe_dist_file(_CLIENT_GUI_DIST_REAL, app_relative)
            if app_candidate and _os.path.isfile(app_candidate) and not app_candidate.endswith(".html"):
                return FileResponse(app_candidate)
        return _html(_os.path.join(_CLIENT_GUI_DIST, "index.html"))

    # Everything else is the sign-in page: / and /login (also /login?invite=<token> and
    # /login?next=...), and any other unknown path, as v0.1's index.html shell was before.
    login_html = _os.path.join(_CLIENT_GUI_DIST, _LOGIN_HTML)
    if not _os.path.isfile(login_html):
        return _not_found()
    return _html(login_html)


app.router.add_api_route(
    "/{full_path:path}", _serve_client, methods=["GET"], include_in_schema=False,
    route_class_override=api_aware_fallback(CONTEXT),
)

