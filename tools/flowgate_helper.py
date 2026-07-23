"""FlowGate Worker API Helper — Phase 3 (D022 §4-5).

Helper module used by workers (AI agents) to call the FlowGate API.
- Automatic footer attachment (auto_footer=True by default)
- Raises FlowGateError on server error (ok field not hidden)

Usage example:
    from tools.flowgate_helper import FlowGateHelper, FlowGateError
    import os

    helper = FlowGateHelper(
        base_url=os.environ["FLOWGATE_BASE_URL"],
        token=os.environ["FLOWGATE_TOKEN"],
        worker_model="claude-sonnet-4.6",
        scratch_dir=os.environ["FLOWGATE_SCRATCH_DIR"],
    )
    try:
        result = helper.inbox_new(
            project="flowgate",
            group_name="G001",
            prev_doc_id="R015",
            doc_type="D",
            doc_path=os.path.join(os.environ["FLOWGATE_SCRATCH_DIR"], "doc.md"),
        )
    except FlowGateError as e:
        print(f"Error ({e.http_status}): {e.error_message}")
        print(f"Help: {e.help_url}")
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class FlowGateError(Exception):
    """Raised by the helper to expose server errors to the worker (D022 §4-5-4)."""

    def __init__(self, http_status: int, error_message: str, help_url: str):
        super().__init__(error_message)
        self.http_status = http_status
        self.error_message = error_message
        self.help_url = help_url


class FlowGateHelper:
    """FlowGate API call helper. Includes automatic footer attachment (D022 §4-5-2).

    Args:
        base_url: FlowGate server base URL (e.g., "https://example.com")
        token: Issued raw_token (for Bearer authentication)
        worker_model: Worker model name (recorded when footer is attached)
        auto_footer: If True, footer is automatically attached on inbox_new/inbox_edit calls (§4-5-3)
        scratch_dir: Token-owned scratch directory. When provided, inbox file paths
            are required to resolve inside this directory.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        worker_model: str = "unknown",
        auto_footer: bool = True,
        scratch_dir: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.worker_model = worker_model
        self.auto_footer = auto_footer
        self.scratch_dir = (
            os.path.realpath(os.path.abspath(scratch_dir))
            if scratch_dir is not None
            else None
        )

    # ── Inbound ──────────────────────────────────────────────────────────────────

    def inbox_new(
        self,
        project: str,
        group_name: str,
        prev_doc_id: str,
        doc_type: str,
        doc_path: str,
        related_doc_ids: Optional[list[str]] = None,
        module: str = "__ALL__",
    ) -> dict:
        """Register a new document with auto-assigned ID (action: new).

        Args:
            project: Project name
            group_name: Group identifier
            prev_doc_id: Parent document ID (e.g., "R015")
            doc_type: Document type to register (e.g., "NR", "Q", "D")
            doc_path: Path to the worker-created file inside the token scratch_dir
            related_doc_ids: List of related document IDs (optional, for Q registration)
            module: Module name (default: "__ALL__")

        Returns:
            {"ok": True, "doc_id": "NR042", "stored_path": "...", "message": "NR042 registered. You may end the session."}

        Raises:
            FlowGateError: On server error response
        """
        doc_path = self._validate_doc_path(doc_path)
        if self.auto_footer:
            doc_path = self._attach_footer(doc_path)

        body: dict[str, Any] = {
            "action": "new",
            "project": project,
            "module": module,
            "group_name": group_name,
            "prev_doc_id": prev_doc_id,
            "doc_type": doc_type,
            "doc_path": doc_path,
        }
        if related_doc_ids:
            body["related_doc_ids"] = related_doc_ids
        return self._post_inbox(body)

    def inbox_edit(
        self,
        project: str,
        group_name: str,
        doc_id: str,
        edit_reason: str,
        doc_path: str,
        linked_doc_id: Optional[str] = None,
        module: str = "__ALL__",
    ) -> dict:
        """Register an edit to an existing document (action: edit).

        Args:
            project: Project name
            group_name: Group identifier
            doc_id: ID of the document to edit
            edit_reason: Edit reason ("rejected" | "qna_followup" | "user_comment" | "worker_self")
            doc_path: Path to the newly created file inside the token scratch_dir
            linked_doc_id: Document ID that justifies the edit reason (optional — RJ document ID / Q ID, etc.)
            module: Module name (default: "__ALL__")

        Returns:
            {"ok": True, "doc_id": "NR042", "stored_path": "...", ...}

        Raises:
            FlowGateError: On server error response
        """
        doc_path = self._validate_doc_path(doc_path)
        if self.auto_footer:
            doc_path = self._attach_footer(doc_path)

        body: dict[str, Any] = {
            "action": "edit",
            "project": project,
            "module": module,
            "group_name": group_name,
            "doc_id": doc_id,
            "edit_reason": edit_reason,
            "doc_path": doc_path,
        }
        if linked_doc_id:
            body["linked_doc_id"] = linked_doc_id
        return self._post_inbox(body)

    # ── Outbound (query) ─────────────────────────────────────────────────────────

    def get_document(self, doc_id: str) -> dict:
        """Fetch a single document body and metadata."""
        return self._get(f"/api/v1/document/{doc_id}")

    def get_document_path(self, doc_id: str) -> dict:
        """Fetch the file path of a document."""
        return self._get(f"/api/v1/document/{doc_id}/path")

    def list_groups(self, project: str) -> dict:
        """List all groups within a project."""
        return self._get(f"/api/v1/list/projects/{project}/groups")

    def list_documents(self, group_id: str) -> dict:
        """List all documents within a group."""
        return self._get(f"/api/v1/list/groups/{group_id}/documents")

    def get_next_action(self, group_id: str) -> dict:
        """Fetch the last/expected next action for a group."""
        return self._get(f"/api/v1/group/{group_id}/next-action")

    def get_help(self) -> dict:
        """Single entry point for API usage instructions (authentication not required)."""
        url = f"{self.base_url}/api/v1/help"
        req = Request(url, method="GET")
        return self._send(req)

    # ── Internal utilities ───────────────────────────────────────────────────────

    def _validate_doc_path(self, doc_path: str) -> str:
        """Resolve doc_path and, when configured, enforce the token scratch boundary."""
        resolved_path = os.path.realpath(os.path.abspath(doc_path))
        if self.scratch_dir is None:
            return resolved_path

        try:
            inside_scratch = (
                os.path.normcase(os.path.commonpath([self.scratch_dir, resolved_path]))
                == os.path.normcase(self.scratch_dir)
            )
        except ValueError:
            inside_scratch = False
        if not inside_scratch:
            raise ValueError(
                "doc_path must resolve inside the token scratch_dir: "
                f"{self.scratch_dir}"
            )
        return resolved_path

    def _post_inbox(self, body: dict) -> dict:
        url = f"{self.base_url}/api/v1/inbox"
        data = json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        })
        return self._send(req)

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        req = Request(url, method="GET", headers={
            "Authorization": f"Bearer {self.token}",
        })
        return self._send(req)

    def _send(self, req: Request) -> dict:
        try:
            with urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {}
            raise FlowGateError(
                http_status=body.get("http_status", e.code),
                error_message=body.get("error_message", str(e)),
                help_url=body.get("help_url", ""),
            ) from e

    def _attach_footer(self, doc_path: str) -> str:
        """Appends a footer to the end of doc_path and returns the same path (§4-5-3)."""
        footer = self._build_footer()
        with open(doc_path, "a", encoding="utf-8") as f:
            f.write(footer)
        return doc_path

    def _build_footer(self) -> str:
        """Build the footer string in §4-5-3 format."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            "\n\n---\n\n"
            "```flowgate-meta\n"
            f"worker_model: {self.worker_model}\n"
            f"submitted_at: {ts}\n"
            "token_id: (auto)\n"
            "```\n"
        )
