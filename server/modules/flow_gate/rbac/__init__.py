"""FlowGate RBAC package.

Public interface:
  - permission_service : get_user_permissions, has_permission, invalidate_cache
  - role_service       : assign_role, revoke_role, get_user_role
  - decorators         : require_permission, require_system_permission, require_role
  - policies           : can_delete_document, can_update_document, evaluate, PolicyResult
  - routers.router     : FastAPI APIRouter (rbac_admin)
"""
from .permission_service import (
    get_user_permissions,
    has_permission,
    invalidate_cache,
    clear_all_cache,
    CACHE_TTL,
)
from .role_service import (
    assign_role,
    revoke_role,
    get_user_role,
    list_user_roles,
    list_project_members,
    SYSTEM_PROJECT,
)
from .decorators import require_permission, require_system_permission, require_role
from .policies import can_delete_document, can_update_document, evaluate, PolicyResult

__all__ = [
    "get_user_permissions",
    "has_permission",
    "invalidate_cache",
    "clear_all_cache",
    "CACHE_TTL",
    "assign_role",
    "revoke_role",
    "get_user_role",
    "list_user_roles",
    "list_project_members",
    "SYSTEM_PROJECT",
    "require_permission",
    "require_system_permission",
    "require_role",
    "can_delete_document",
    "can_update_document",
    "evaluate",
    "PolicyResult",
]
