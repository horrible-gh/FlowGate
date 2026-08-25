"""Central actor-aware mutation policy and route inventory metadata (group 0378)."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Optional
from urllib.parse import unquote

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import group_ai_leases as db_leases

MutationResource = Literal["group", "project_substrate", "personal", "system"]
MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MutationPrincipal:
    kind: Literal["human", "worker", "system"]
    user_id: Optional[str] = None
    token_id: Optional[str] = None
    group_id: Optional[str] = None
    doc_ref: Optional[str] = None
    run_id: Optional[str] = None
    action_scope: Optional[str] = None


class MutationPolicyError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **fields: Any):
        super().__init__(message)
        self.status_code = status_code
        self.error = {"code": code, "message": message, **fields}

    def body(self) -> dict:
        return {"error": self.error}


def human_principal(user: Optional[dict] = None) -> MutationPrincipal:
    user = user or {}
    return MutationPrincipal(kind="human", user_id=user.get("user_id") or user.get("id"))


def worker_principal(token: dict) -> MutationPrincipal:
    return MutationPrincipal(
        kind="worker",
        user_id=token.get("issued_to"),
        token_id=token.get("token_id"),
        group_id=token.get("group_id"),
        doc_ref=token.get("doc_ref"),
        run_id=token.get("ai_run_id"),
        action_scope=token.get("action_scope"),
    )


def system_principal(
    *, user_id: Optional[str] = "system", group_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> MutationPrincipal:
    """Trusted internal mutation context for server-owned completion hooks."""
    return MutationPrincipal(
        kind="system", user_id=user_id, group_id=group_id, run_id=run_id
    )


def principal_from_request(request: Request) -> MutationPrincipal:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return MutationPrincipal(kind="human")
    raw = auth[7:]
    try:
        from modules.flow_gate.services import token_service
        token = token_service.inspect_for_replay(raw)
    except Exception:
        return MutationPrincipal(kind="human")
    return worker_principal(token)


def _lease_run_live(run_id: Optional[str]) -> bool:
    """0401 NR0003 SS3 cause 3 / T0004 item 4: is the lease's OWN run still alive?

    Shares ai_invoke_service.is_run_live so the 423 body and the screen it drives
    can never again say two different things. Deferred import -- mutation_policy is
    imported by app startup, ai_invoke_service is not (see _record_denied_mutation).
    Fails toward True ("assume it's real") on any lookup trouble, so an inability to
    answer never turns into an invitation to force-unlock a run that is actually busy.
    """
    if not run_id:
        return False
    try:
        from modules.flow_gate.services import ai_invoke_service
        return ai_invoke_service.is_run_live(str(run_id))
    except Exception:
        return True


def _locked(lease: dict) -> MutationPolicyError:
    run_id = lease.get("run_id")
    return MutationPolicyError(
        423,
        "GROUP_AI_RUN_LOCKED",
        "Modification not allowed while an AI run owns this group.",
        group_id=lease.get("group_id"),
        run_id=run_id,
        run_live=_lease_run_live(run_id),
    )


def assert_group_mutation_allowed(
    group_id: Optional[str], principal: MutationPrincipal, operation: str,
) -> Optional[dict]:
    """Authorize one group mutation against the durable lease owner."""
    if not group_id:
        return None
    lease = db_leases.get_active(group_id)
    if lease is None:
        return None
    if principal.kind == "system":
        return lease
    if principal.kind != "worker":
        raise _locked(lease)
    owner_match = (
        principal.group_id == group_id
        and principal.token_id
        and principal.token_id == lease.get("token_id")
        and principal.run_id
        and principal.run_id == lease.get("run_id")
        and principal.action_scope
        and principal.action_scope == lease.get("action_scope")
    )
    if not owner_match:
        raise MutationPolicyError(
            403,
            "GROUP_AI_RUN_OWNER_MISMATCH",
            "This worker token does not own the active group lease.",
            group_id=group_id,
            run_id=lease.get("run_id"),
            operation=operation,
        )
    db_leases.heartbeat(group_id, str(lease["run_id"]))
    return lease


def assert_project_substrate_mutation_allowed(
    project_id: Optional[str], principal: MutationPrincipal, operation: str,
) -> None:
    """Classification hook reserved for the follow-up project-level policy.

    Group 0378 intentionally does not block shared project substrate mutations; it only
    requires that they are inventory-classified instead of being mistaken for group writes.
    """
    return None


def mutation_error_response(exc: MutationPolicyError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body())


def group_id_from_doc_id(doc_id: Optional[str]) -> Optional[str]:
    if not doc_id:
        return None
    try:
        row = db_documents.get_by_id(doc_id)
    except Exception:
        row = None
    if row and row.get("group_id"):
        return str(row["group_id"])
    match = re.match(r"^(.+)\.[^.]+$", str(doc_id))
    return match.group(1) if match else None


def _find(mapping: Any, names: set[str]) -> Optional[str]:
    if isinstance(mapping, dict):
        for key in names:
            value = mapping.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value).strip()
        for value in mapping.values():
            found = _find(value, names)
            if found:
                return found
    elif isinstance(mapping, list):
        for value in mapping:
            found = _find(value, names)
            if found:
                return found
    return None


def _json_body(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}


def _multipart_value(raw: bytes, name: str) -> Optional[str]:
    if not raw:
        return None
    pattern = rb'name="' + re.escape(name.encode()) + rb'"[^\r\n]*\r?\n\r?\n([^\r\n]+)'
    match = re.search(pattern, raw)
    return match.group(1).decode("utf-8", "replace").strip() if match else None


def _path_candidate(path: str, markers: tuple[str, ...]) -> Optional[str]:
    segments = [unquote(part) for part in path.split("/") if part]
    for marker in markers:
        try:
            index = segments.index(marker)
        except ValueError:
            continue
        if index + 1 < len(segments):
            value = segments[index + 1]
            if value not in {"transitions", "archive", "restore", "purge", "git"}:
                return value
    return None


async def resolve_request_group(
    request: Request, principal: MutationPrincipal,
) -> Optional[str]:
    for name in ("group_id", "gid", "group_name"):
        value = request.query_params.get(name)
        if value:
            return value
    path = request.url.path
    direct = _path_candidate(path, ("groups", "group"))
    if direct:
        return direct

    raw = await request.body()
    body = _json_body(raw)
    direct = _find(body, {"group_id", "gid", "group_name"})
    if not direct:
        direct = _multipart_value(raw, "group_id")
    if direct:
        return direct

    doc_id = _find(body, {"doc_id", "doc_ref", "document_id", "target_doc_id"})
    if not doc_id:
        doc_id = _path_candidate(path, ("documents", "document", "workflow", "queries"))
    # 0394 T0016 item 4: this reads one document from the DB — run off the event loop for the
    # same reason as the dispatch comment above.
    resolved = await anyio.to_thread.run_sync(group_id_from_doc_id, doc_id)
    if resolved:
        return resolved

    # Worker-only surfaces such as /remote/write carry the group on the token, not the URL.
    return principal.group_id if principal.kind == "worker" else None


_SYSTEM_PATHS = (
    re.compile(r"/(?:api/v1/)?ai-invoke(?:/|$)"),
    re.compile(r"/(?:auth|rbac)(?:/|$)"),
    re.compile(r"/(?:events|health)(?:/|$)"),
)
_PERSONAL_PATHS = (
    re.compile(r"/(?:chat-settings|notification-seen|ui-settings|user-chat-settings)(?:/|$)"),
)


def is_policy_control_path(path: str) -> bool:
    """Run start/resume, cancel/pause, the manual lease release, and the explicit
    paused-chain release all own the lease lifecycle, so none of them may self-block
    (0401 T0004 item 2 adds the lease-release case; 0459 T0007 §2-7 adds the paused-
    chain DELETE -- its own internal active-run/lease conflict judgement in
    release_paused_chain still runs, this only keeps the group mutation lease
    middleware from pre-empting it)."""
    if "ai-invoke" not in path:
        return False
    return bool(
        re.search(r"/(?:start|resume)$", path)
        or re.search(r"/(?:cancel|pause)$", path)
        or re.search(r"/leases/[^/]+/release$", path)
        or re.search(r"/paused/[^/]+$", path)
    )


def classify_mutation_route(path: str, methods: set[str]) -> tuple[MutationResource, str]:
    if not (methods & MUTATION_METHODS):
        return "system", "read_only"
    if any(p.search(path) for p in _PERSONAL_PATHS):
        return "personal", "per_user_state"
    if any(p.search(path) for p in _SYSTEM_PATHS):
        return "system", "run_or_auth_control"
    if (
        any(token in path for token in ("{group_id}", "{gid}", "{doc_id}", "{doc_ref}"))
        or any(token in path for token in ("/inbox", "/remote/", "/workflow/", "/documents", "/queries", "/answers", "/git/", "/files/upload"))
    ):
        return "group", "standard_group_resolver"
    if "/projects" in path or "/settings" in path:
        return "project_substrate", "0378_follow_up_scope"
    return "system", "explicit_non_group_route"


def iter_mutation_routes(app: FastAPI):
    """Yield (APIRoute, effective_path) through FastAPI's lazy included routers."""
    seen: set[tuple[int, str]] = set()

    def walk(routes, prefix: str = ""):
        for route in routes:
            if isinstance(route, APIRoute):
                effective_path = f"{prefix}{route.path}"
                key = (id(route), effective_path)
                if key not in seen:
                    seen.add(key)
                    if set(route.methods or set()) & MUTATION_METHODS:
                        yield route, effective_path
                continue
            original = getattr(route, "original_router", None)
            context = getattr(route, "include_context", None)
            if original is not None:
                child_prefix = f"{prefix}{getattr(context, 'prefix', '') or ''}"
                yield from walk(getattr(original, "routes", []), child_prefix)

    yield from walk(app.routes)


def annotate_mutation_routes(app: FastAPI) -> None:
    """Attach mandatory inventory metadata to every mutation APIRoute."""
    for route, effective_path in iter_mutation_routes(app):
        methods = set(route.methods or set())
        resource, reason = classify_mutation_route(effective_path, methods)
        route.mutation_resource = resource
        route.mutation_reason = reason
        route.group_resolver = "path_query_body_doc_or_worker" if resource == "group" else None
        extra = dict(route.openapi_extra or {})
        extra["x-flowgate-mutation-resource"] = resource
        extra["x-flowgate-mutation-reason"] = reason
        if route.group_resolver:
            extra["x-flowgate-group-resolver"] = route.group_resolver
        route.openapi_extra = extra

class GroupMutationPolicyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() not in MUTATION_METHODS or is_policy_control_path(request.url.path):
            return await call_next(request)
        # 0394 T0016 item 4: the two steps below read the DB synchronously (token lookup, lease
        # lookup) and write to it (heartbeat). dispatch is an async function **every** mutating
        # request passes through, so calling those reads inline stalls every other request for
        # that duration — the exact defect 0275/0279 removed from route handlers, still sitting
        # where all traffic passes. (It stayed hidden because that guard only looked at
        # `@router.*` and never at middleware.) It moves to a worker thread — same verdicts, same order, only a different thread.
        principal = await anyio.to_thread.run_sync(principal_from_request, request)
        group_id = await resolve_request_group(request, principal)
        if group_id:
            try:
                await anyio.to_thread.run_sync(
                    assert_group_mutation_allowed,
                    group_id,
                    principal,
                    f"{request.method.upper()} {request.url.path}",
                )
            except MutationPolicyError as exc:
                await _record_denied_mutation(exc, request, principal)
                return mutation_error_response(exc)
        return await call_next(request)


async def _record_denied_mutation(
    exc: MutationPolicyError, request: Request, principal: Optional[MutationPrincipal] = None,
) -> None:
    """Leave the refusal on the AI run that owns the lease (0393 B0001 / T0005 §2-6).

    Until now a denial was a bare 403/423 on the wire and nothing else: the run record kept
    an empty error list and exit code 0, so the only account of B0001's three dead reviews
    was a sentence each worker volunteered in its last message. The lease names the run, so
    the run can be told.

    Two hard constraints, both from T0005 §2-6:

    * `dispatch` is async. Calling the DB/registry write inline would trip the event-loop
      blocking guard (`test_event_loop_blocking_*`), and wrapping it in a helper does not
      launder it — the work goes to a worker thread via `anyio.to_thread`.
    * Observability must never break the gate. The import is deferred (mutation_policy is
      imported by app startup, ai_invoke_service is not) and every failure is swallowed, so
      the original 403/423 always reaches the caller.
    """
    run_id = exc.error.get("run_id")
    if not run_id:
        return
    group_id = exc.error.get("group_id")
    code = str(exc.error.get("code") or "")
    operation = f"{request.method.upper()} {request.url.path}"
    # WHOSE refusal this is decides where it lands. A worker turned away is the run failing
    # (B0001) — that belongs in the run's own error list and ends the run. A person turned
    # away is the lease doing its job while the AI works; filing that as the AI's error
    # would put a row on nearly every run and would eventually relabel a healthy run as
    # failed. Both are still recorded, in the two different places (T0005 §2-6).
    by_worker = principal is not None and principal.kind == "worker"

    def _mark() -> None:
        from modules.flow_gate.services import ai_invoke_service

        ai_invoke_service.mark_group_lease_denied(
            group_id=str(group_id) if group_id else None,
            run_id=str(run_id),
            code=code,
            operation=operation,
            status_code=exc.status_code,
            by_worker=by_worker,
        )

    try:
        await anyio.to_thread.run_sync(_mark)
    except Exception:
        logger.warning(
            "group lease denial record failed for run %s (ignored)", run_id, exc_info=True,
        )