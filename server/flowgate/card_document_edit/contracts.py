"""Internal contracts. Transports supply authenticated identities, never client actor fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

CONTENT_MAX_CHARS = 500_000
FINGERPRINT_ALG = "sha256-utf8-v1"
EVENT_SCHEMA = "card_document_edit/v1"
PROJECTION_FAILED = object()


@dataclass(frozen=True)
class Actor:
    user_id: str
    kind: str = "person"


@dataclass(frozen=True)
class Origin:
    kind: str = "designer"
    run_id: Optional[str] = None
    token_id: Optional[str] = None
    server_call_id: Optional[str] = None
    token: Optional[dict] = None


@dataclass(frozen=True)
class ReviseRequest:
    flow_id: str
    node_key: str
    slot: str
    expected_doc_id: str
    expected_revision: int
    content: str
    actor: Actor
    origin: Origin = field(default_factory=Origin)
    idempotency_key: Optional[str] = None
    edit_reason: Optional[str] = None
    linked_doc_id: Optional[str] = None
    transport_extras: dict = field(default_factory=dict)
    validate_only: bool = False
    recheck: Optional[Callable] = None
    size_guard: Optional[Callable] = None
    locale: str = "ko"


@dataclass(frozen=True)
class Body:
    state: str
    data: Optional[bytes] = None
    text: Optional[str] = None
    path: Optional[Path] = None
    identity: Optional[tuple] = None


@dataclass
class Built:
    stored_bytes: bytes
    target_path: Optional[Path] = None
    row_updates: dict = field(default_factory=dict)
    snapshot_policy: str = "PREVIOUS"
    body_saved_source: Optional[str] = None
    type_tasks: list = field(default_factory=list)
    result: dict = field(default_factory=dict)


class Rule(Protocol):
    def validate_and_build(self, submitted_text: str, current_doc: dict, current_body: Body) -> Built: ...


@dataclass
class ContactPlan:
    project_id: str
    flow_id: Optional[str]
    doc: dict
    actor_user_id: str
    token_id: Optional[str] = None
    card_slot: Optional[bool] = None
    type_tasks: list = field(default_factory=list)
    consumed: bool = False
    legacy_non_card: bool = False


@dataclass
class Outcome:
    kind: str
    result: dict = field(default_factory=dict)
    line: object = None
    plan: Optional[ContactPlan] = None
    contacts: str = "NONE"
    verdict: Optional[dict] = None


@dataclass
class RestoreLog:
    failures: list = field(default_factory=list)

    def add(self, doc_id, keep):
        self.failures.append((doc_id, str(keep)))

    def has_failures(self):
        return bool(self.failures)


@dataclass
class ContactHolder:
    plan: Optional[ContactPlan] = None

    def put(self, plan):
        assert self.plan is None
        self.plan = plan

    def take(self):
        plan, self.plan = self.plan, None
        return plan


@dataclass(frozen=True)
class StoreInput:
    current: dict
    body: Body
    built: Built
    rule: object
    req: object
    binding: dict
    restore_log: RestoreLog
    allow_missing_previous: bool = False


@dataclass(frozen=True)
class DedicatedRequest:
    doc_id: str
    expected_revision: int
    actor: Actor
    rule_call: object  # precheck(fresh), conflict(fresh), build(fresh, body), needs_current
    origin: Origin = field(default_factory=lambda: Origin("wp_window"))
    event_reason: Optional[str] = None
    edit_reason: Optional[str] = None
    linked_doc_id: Optional[str] = None
    transport_extras: dict = field(default_factory=dict)
    content: str = ""
    new_document: bool = False  # CREATE: caller asserts this row was inserted in this transaction
