"""Engine test-recipe API (flowgate.default.0157; R0001→D0002→P0003→L0004→DB0005→T0006).

  GET    /api/v1/test-commands/help            engine recipe list / single (worker or session)
  POST   /api/v1/engine-recipes                register a recipe (worker/system CRUD)
  PATCH  /api/v1/engine-recipes/{id}           edit an active recipe
  DELETE /api/v1/engine-recipes/{id}           suppress (tombstone) an active recipe
  GET    /api/v1/projects/{project_id}/engine-recipes   read-only Settings visualization (session)

The first four accept a Bearer worker token OR a user JWT (verify_bearer) — the unmanned chain needs
workers to read and manage recipes without a human session (P §auth). The project-scoped read reuses
the project.settings.read RBAC key, exactly like GET /projects/{id}/test-commands (0152).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services import engine_recipe_service as svc
from modules.flow_gate.services.auth_outbound import verify_bearer

router = APIRouter(prefix="/api/v1", tags=["EngineRecipes"])


def _actor(token_rec: dict) -> str:
    """updated_by identity: a worker token records its token_id; a user JWT records the user id."""
    if token_rec.get("_is_user_jwt"):
        return token_rec.get("issued_to") or "user"
    return token_rec.get("token_id") or token_rec.get("issued_to") or "worker"


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": {"code": code, "message": message}})


@router.get("/test-commands/help")
def get_engine_recipe_help(request: Request, engine: str | None = None):
    """P §help — active recipes (engine ASC). `?engine=` → exact-match single (empty list, not 404)."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    recipes = svc.list_help(engine)
    return JSONResponse(content={"ok": True, "recipes": recipes, "total": len(recipes)})


class RecipeCreate(BaseModel):
    engine: str | None = None
    label: str | None = None
    setup: str | None = None
    run_example: str | None = None
    notes: str | None = None


@router.post("/engine-recipes", status_code=201)
def create_engine_recipe(body: RecipeCreate, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        recipe = svc.create(
            body.engine or "", body.label or "", body.setup or "",
            body.run_example or "", body.notes or "", _actor(auth),
        )
    except svc.EngineRecipeConflictError as exc:
        return _error(409, "engine_recipe_conflict", str(exc))
    except svc.EngineRecipeValidationError as exc:
        return _error(422, "engine_recipe_invalid", str(exc))
    return JSONResponse(status_code=201, content={"ok": True, "recipe": recipe})


class RecipePatch(BaseModel):
    engine: str | None = None
    label: str | None = None
    setup: str | None = None
    run_example: str | None = None
    notes: str | None = None


@router.patch("/engine-recipes/{recipe_id}")
def update_engine_recipe(recipe_id: int, body: RecipePatch, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        recipe = svc.patch(recipe_id, body.model_dump(exclude_unset=True), _actor(auth))
    except svc.EngineRecipeConflictError as exc:
        return _error(409, "engine_recipe_conflict", str(exc))
    except svc.EngineRecipeValidationError as exc:
        return _error(422, "engine_recipe_invalid", str(exc))
    if recipe is None:
        return _error(404, "engine_recipe_not_found", f"no active recipe: {recipe_id}")
    return JSONResponse(content={"ok": True, "recipe": recipe})


@router.delete("/engine-recipes/{recipe_id}")
def delete_engine_recipe(recipe_id: int, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not svc.suppress(recipe_id, _actor(auth)):
        return _error(404, "engine_recipe_not_found", f"no active recipe: {recipe_id}")
    return JSONResponse(content={"ok": True, "suppressed": True, "id": recipe_id})


@router.get("/projects/{project_id}/engine-recipes")
def list_project_engine_recipes(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Read-only Settings visualization (P §visualization). Global recipes + a used_by_project flag."""
    recipes = svc.list_for_project_view(project_id)
    return {"ok": True, "recipes": recipes, "total": len(recipes)}
