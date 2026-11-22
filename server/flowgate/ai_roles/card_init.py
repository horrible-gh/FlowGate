"""L-1 §2.4: creation copy, WP precedence/redeploy, and one pending fill.

All writes share the caller's Flow guard. Account read failures are isolated by a
savepoint so a failed PostgreSQL SELECT cannot poison the draft transaction.

flowgate.v02test.0027 (0007-T, A-server): a new card of a kind with a 검수 횟수 (``review_count``) also copies the AI
role group's default review count once, in the same creation transaction: the card's document-type row, else the
common row, else 0 — stored explicitly, 0 included, set by the card's creator (a log line tells the level it came
from; no extra stored field, so a card's stored settings keep their shape). A value already stored before the copy
(a WP plan step's explicit count, also 0, or a row that does not parse) is kept. Pending initialization never reads
the group, and a group change never touches existing cards.
"""
import logging
from flowgate.ai_roles import availability, role_store
from flowgate.ai_roles.account import valid_value, same_value
from flowgate.ai_roles.resolve import applicable_roles
from flowgate.card_settings import fields, repository
from flowgate.ai_roles.legacy_adapter import (
    get_store, in_transaction, held_guard, isolated_read, creation_group_profiles,
    effective_review_count, stored_review_count, card_review_count_choices,
)

log = logging.getLogger(__name__)

REVIEW_COUNT = "review_count"


def _require_guard(flow_id):
    if held_guard() != ("flow", flow_id) or not in_transaction():
        raise RuntimeError("card role writes require their Flow guard transaction")


def _read_account(user_id, flow_id, node_key, wp=False):
    if user_id is None:
        return None
    store = get_store()
    with store.transaction():
        # The Flow guard's first DML has already opened SQLite's deferred transaction.
        store._execute("SAVEPOINT fg_role_acc_read")
        try:
            account = role_store.account_roles(user_id)
        except Exception:
            store._execute("ROLLBACK TO SAVEPOINT fg_role_acc_read")
            store._execute("RELEASE SAVEPOINT fg_role_acc_read")
            if wp:
                log.warning("ACCOUNT_READ_FAILED_ON_WP_DEPLOY flow_id=%s node_key=%s", flow_id, node_key)
            return None
        store._execute("RELEASE SAVEPOINT fg_role_acc_read")
        return account


def _read_group(flow_id, node_key):
    """Copy-time defaults only; pending initialization never reads today's group."""
    def read():
        rows = {row["type_key"]: row for row in creation_group_profiles()}
        if not rows:
            return {}
        node = repository.card_node(flow_id, node_key) or {}
        # Profile type_key is the document type (T/N/TS/...), not the palette label.
        typed = rows.get(node.get("doc_type"), {})
        common = rows.get("common", {})
        values = {}
        for role in role_store.ROLES:
            cell = typed if typed.get(role + "_kind") in ("assigned", "not_used") else common
            kind = cell.get(role + "_kind")
            if kind == "not_used":
                values[role] = role_store.RoleValue("not_used")
            elif kind == "assigned":
                pid = cell.get(role + "_provider_id")
                values[role] = role_store.RoleValue("assigned", pid, availability.display_name(pid))
        return values

    try:
        return isolated_read(read, "fg_role_group_read")
    except Exception:
        log.warning("GROUP_ROLE_READ_FAILED flow_id=%s node_key=%s", flow_id, node_key, exc_info=True)
        return {}


def _read_group_review_count(flow_id, node_key):
    """``(typed row, common row)`` of the card's document type; None when the group cannot be read."""
    def read():
        rows = {row["type_key"]: row for row in creation_group_profiles()}
        node = repository.card_node(flow_id, node_key) or {}
        return rows.get(node.get("doc_type")), rows.get("common")

    try:
        return isolated_read(read, "fg_role_group_count_read")
    except Exception:
        log.warning("GROUP_REVIEW_COUNT_READ_FAILED flow_id=%s node_key=%s", flow_id, node_key, exc_info=True)
        return None


def copy_review_count(flow_id, node_key, kind, actor):
    """Store the group's default review count on a just-created card, inside its Flow guard. True when written."""
    if fields.field_of(kind, REVIEW_COUNT) is None or not repository.available():
        return False
    if repository.stored(flow_id, node_key, REVIEW_COUNT) is not repository.ABSENT:
        return False                     # the WP plan's value (0 included) or an unreadable row stays as it is
    found = _read_group_review_count(flow_id, node_key)
    if found is None:
        return False                     # no row = the card default 0, the same as before this copy existed
    typed, common = found
    # The same judgment as the API's effective_review_count (ai_group_role_profile_service.profiles): a stored value
    # outside today's card choices copies 0 ("default").
    count, level = effective_review_count(typed, common, card_review_count_choices())
    stored, _ = stored_review_count(typed, common)
    if stored is not None and stored != count:
        log.warning("GROUP_REVIEW_COUNT_OUT_OF_CHOICES flow_id=%s node_key=%s value=%s", flow_id, node_key, stored)
    repository.put(flow_id, node_key, REVIEW_COUNT, count, actor)
    log.info("GROUP_REVIEW_COUNT_COPIED flow_id=%s node_key=%s value=%s level=%s", flow_id, node_key, count, level)
    return True


def slot_from_account(value, source="creation_copy", actor=None, role=None):
    if not valid_value(value):
        return None
    return role_store.RoleSlot(role, value, source, actor, role_store._now())


def wp_plan_roles(settings):
    values = dict(settings)
    return {role: values[name] for name, role in
            (("instruction_ai", "instruction"), ("run_ai", "execution"), ("review_ai", "review"))
            if isinstance(values.get(name), str) and values[name].strip()}


def initialize_new_card(flow_id, node_key, kind, creator_id):
    return _initialize(flow_id, node_key, kind, creator_id, {}, False)


def initialize_wp_card(flow_id, node_key, kind, deployer_id, plan_roles):
    return _initialize(flow_id, node_key, kind, deployer_id, plan_roles, True)


def _initialize(flow_id, node_key, kind, creator_id, plan_roles, wp):
    if not role_store.available() or not applicable_roles(kind):
        return "NO_CHANGE"
    _require_guard(flow_id)
    roles = applicable_roles(kind)
    copy_review_count(flow_id, node_key, kind, creator_id)
    group = _read_group(flow_id, node_key) if any(r not in plan_roles for r in roles) else {}
    acc = _read_account(creator_id, flow_id, node_key, wp) if any(
        r not in plan_roles and r not in group for r in roles) else None
    for role in roles:
        if role in plan_roles:
            pid = plan_roles[role]
            value = role_store.RoleValue("assigned", pid, availability.display_name(pid))
            slot = role_store.RoleSlot(role, value, "wp_plan", creator_id, role_store._now())
        else:
            value = group[role] if role in group else (acc.values.get(role) if acc else None)
            slot = slot_from_account(value, "creation_copy", creator_id, role)
        if slot:
            role_store.write_slot(flow_id, node_key, slot)
    state = "initialized" if all(r in role_store.slots(flow_id, node_key) for r in roles) else "pending_init"
    role_store.create_card_init(flow_id, node_key, state, creator_id)
    repository.bump_version(flow_id, node_key)
    return "CHANGED"


def cards_created(group, user_id, nodes):
    # Creation-only defaults: absent values on existing gates still mean manual approval.
    if repository.available():
        for node in nodes:
            if fields.card_kind(node.get("node_type"), node.get("doc_type")) == fields.CHECK:
                flow_id, key = group["group_id"], node["node_key"]
                stored = repository.values(flow_id, key)
                changed = False
                for name, value in (("auto_approve", True), ("merge_mode", "commit")):
                    if name not in stored:
                        changed = repository.put(flow_id, key, name, value, user_id) or changed
                if changed:
                    repository.bump_version(flow_id, key)
    if not role_store.available():
        return
    for node in nodes:
        initialize_new_card(group["group_id"], node["node_key"],
                            fields.card_kind(node.get("node_type"), node.get("doc_type")), user_id)


def _redeploy(flow_id, node_key, kind, plan_roles):
    roles = applicable_roles(kind)
    slots = role_store.slots(flow_id, node_key)
    puts, removes = {}, []
    marked = set(slots)
    for role in roles:
        old = slots.get(role)
        if role in plan_roles:
            if old is None or old.source != "wp_plan" or not same_value(old.value, role_store.RoleValue("assigned", plan_roles[role])):
                puts[role] = plan_roles[role]
            marked.add(role)
        elif old is not None and old.source == "wp_plan":
            removes.append(role)
            marked.discard(role)
    init = role_store.card_init(flow_id, node_key)
    initialize = bool(roles) and all(r in marked for r in roles) and (init is None or init.state == "pending_init")
    return puts, removes, initialize, init


def redeploy_role_changes(flow_id, node_key, kind, plan_roles):
    if not role_store.available() or not applicable_roles(kind):
        return False
    puts, removes, initialize, _ = _redeploy(flow_id, node_key, kind, plan_roles)
    return bool(puts or removes or initialize)


def apply_wp_redeploy_roles(flow_id, node_key, kind, deployer_id, plan_roles):
    if not role_store.available() or not applicable_roles(kind):
        return "NO_CHANGE"
    _require_guard(flow_id)
    puts, removes, initialize, init = _redeploy(flow_id, node_key, kind, plan_roles)
    for role, pid in puts.items():
        value = role_store.RoleValue("assigned", pid, availability.display_name(pid))
        role_store.write_slot(flow_id, node_key, role_store.RoleSlot(role, value, "wp_plan", deployer_id, role_store._now()))
    for role in removes:
        role_store.remove_wp_plan_slot(flow_id, node_key, role)
    if initialize:
        if init is None:
            role_store.create_card_init(flow_id, node_key, "initialized", None)
        else:
            role_store.mark_initialized(flow_id, node_key)
    if puts or removes or initialize:
        repository.bump_version(flow_id, node_key)
        return "CHANGED"
    return "NO_CHANGE"


def initialize_pending_card(flow_id, node_key, kind):
    if not role_store.available() or not applicable_roles(kind):
        return "NO_CHANGE"
    _require_guard(flow_id)
    init = role_store.card_init(flow_id, node_key)
    if init is None or init.state != "pending_init" or not init.creator_id:
        return "NO_CHANGE"
    acc = _read_account(init.creator_id, flow_id, node_key)
    if acc is None:
        return "NO_CHANGE"
    slots = role_store.slots(flow_id, node_key)
    changed = False
    for role in applicable_roles(kind):
        if role not in slots:
            slot = slot_from_account(acc.values.get(role), "pending_fill", None, role)
            if slot:
                changed = role_store.write_slot(flow_id, node_key, slot) or changed
    if all(r in role_store.slots(flow_id, node_key) for r in applicable_roles(kind)):
        changed = role_store.mark_initialized(flow_id, node_key) or changed
    if changed:
        repository.bump_version(flow_id, node_key)
    return "CHANGED" if changed else "NO_CHANGE"
