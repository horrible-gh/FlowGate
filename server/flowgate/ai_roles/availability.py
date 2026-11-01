"""L-1 §2.2: account selection and card execution availability; public views only."""
from flowgate.ai_roles.legacy_adapter import ai_providers, workspace_service, isolated_read

ACCOUNT, CARD = "account", "card"


def lookup(provider_id):
    return ai_providers.get_public_row(provider_id)


def check_availability(provider_id, level, user_id=None, project_id=None):
    def check():
        row = lookup(provider_id)
        if row is None:
            return "deleted"
        if not row.get("enabled"):
            return "inactive"
        if level == ACCOUNT:
            return None if (row.get("owner_scope") == "system" or row.get("owner_user_id") == user_id) else "not_selectable"
        allowed = workspace_service.allowed_resource_ids(project_id, "provider")
        if allowed is not None and provider_id not in allowed:
            return "not_allowed_in_project"
        if row.get("owner_scope") != "system":
            return "personal_not_runnable"
        return None
    try:
        return isolated_read(check, "fg_role_avail_read")
    except Exception:
        return "registry_unreadable"


def selectable_ais(user_id):
    def read_rows():
        rows = list(ai_providers.list_scope(None))
        if user_id is not None:
            rows += list(ai_providers.list_user(user_id))
        return rows
    rows = isolated_read(read_rows, "fg_role_options_read")
    return [{"id": r["provider_id"], "name": r.get("name"), "exec_type": r.get("exec_type"),
             "kind": r.get("kind"), "owner_scope": r.get("owner_scope")}
            for r in rows if r.get("enabled")]


def display_name(provider_id, snapshot=None):
    try:
        row = isolated_read(lambda: lookup(provider_id), "fg_role_name_read")
        return row.get("name") if row and row.get("name") is not None else snapshot
    except Exception:
        return snapshot
