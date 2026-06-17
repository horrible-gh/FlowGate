"""TS004 T051 test data loading script.

Run from the server/ directory:
    python seed_ts004.py

Safe to re-run: skips groups/documents that already exist.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("TESTING", "0")

from modules.flow_gate import db

# ── Path constants ───────────────────────────────────────────────────

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_DOCS_ROOT  = os.path.join(_REPO_ROOT, "_documents", "FlowGate")

_INBOX_DIR     = db.INBOX_DIR
_PROCESSED_DIR = db.PROCESSED_DIR
_ACCEPT_DIR    = db.ACCEPT_DIR

# For the TC-02 relative-path test: place fixture files here under docs_root
_FIXTURE_DIR = os.path.join(_DOCS_ROOT, "90_test_scenario", "ts004")

PROJECT = "server"
MODULE  = "ts004"


# ── Helpers ────────────────────────────────────────────────────────

def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _ensure_doc(
    doc_id: str,
    doc_type: str,
    title: str,
    group_id: str,
    status: str = "open",
    target_id: str | None = None,
    next_action: str | None = None,
    direction: str | None = None,
    memo_file: str | None = None,
) -> None:
    if db.get_document_by_id(doc_id) is not None:
        print(f"  [skip] {doc_id} already exists")
        return
    db.insert_document(
        doc_id=doc_id,
        doc_type=doc_type,
        project=PROJECT,
        module=MODULE,
        title=title,
        target_id=target_id,
        group_id=group_id,
        priority="medium",
        next_action=next_action,
        direction=direction,
        status=status,
    )
    if memo_file:
        db.insert_event(doc_id, "created", memo_file=memo_file)
    print(f"  [ok]   {doc_id} ({doc_type}, {status})")


def _ensure_group(group_id: str, title: str, status: str = "OPEN") -> None:
    existing = db.get_group(group_id)
    if existing:
        print(f"  [skip] group {group_id} already exists")
        return
    
    db.insert_group(group_id, PROJECT, MODULE, title, "medium")
    if status != "OPEN":
        db.update_group_status(group_id, status)
    print(f"  [ok]   group {group_id} ({status})")


# ── Step 0: Register ts004 module as allowed ───────────────────────

print("=== Step 0: allowed_projects ===")
existing_allowed = {(r["project"], r["module"]) for r in db.get_allowed_projects()}
if (PROJECT, MODULE) not in existing_allowed:
    db.add_allowed_project(PROJECT, MODULE)
    print(f"  [ok]   {PROJECT}/{MODULE} registered")
else:
    print(f"  [skip] {PROJECT}/{MODULE} already exists")


# ── Step 1: Create fixture files under docs_root ───────────────────
# For TC-02, source_file/requirement_file/reference_qa.file
# must actually exist under docs_root to be converted to docs_root-relative paths.

print("=== Step 1: fixture file creation (_documents/FlowGate/90_test_scenario/ts004/) ===")
os.makedirs(_FIXTURE_DIR, exist_ok=True)

FIXTURE_R001     = "R001_requirement.md"
FIXTURE_Q001     = "Q001_question.md"
FIXTURE_A001     = "A001_answer.md"
FIXTURE_AR001    = "AR001_approval_request.md"
FIXTURE_DS001    = "DS001_design_spec.md"
FIXTURE_DS002    = "DS002_design_spec_T.md"
FIXTURE_D001     = "D001_design.md"
FIXTURE_DC001    = "DC001_design_complete.md"

_FIXTURE_FILES = {
    FIXTURE_R001: (
        "---\n"
        "group_id: server-ts004-9401\n"
        "type: R\n"
        "doc_id: server-ts004-9401-R0001\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 T051 Requirement Definition for Path Verification\n"
        "priority: medium\n"
        "---\n\n"
        "## Requirements\n\n"
        "- Requirement document used to validate docs_root-relative path conversion policy.\n"
    ),
    FIXTURE_Q001: (
        "---\n"
        "group_id: server-ts004-9401\n"
        "type: Q\n"
        "doc_id: server-ts004-9401-Q0001\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 T051 Requirement Clarification Question\n"
        "priority: medium\n"
        "target_id: server-ts004-9401-R0001\n"
        "---\n\n"
        "## Question\n\n"
        "- Question about the source_file path policy.\n"
    ),
    FIXTURE_A001: (
        "---\n"
        "group_id: server-ts004-9401\n"
        "type: A\n"
        "doc_id: server-ts004-9401-A0001\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 T051 Requirement Clarification Answer\n"
        "priority: medium\n"
        "target_id: server-ts004-9401-Q0001\n"
        "---\n\n"
        "## Answer\n\n"
        "- It returns paths relative to docs_root.\n"
    ),
    FIXTURE_AR001: (
        "---\n"
        "group_id: server-ts004-9401\n"
        "type: AR\n"
        "doc_id: server-ts004-9401-AR0001\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 T051 Requirement Approval Request\n"
        "priority: medium\n"
        "target_id: server-ts004-9401-R0001\n"
        "---\n\n"
        "Approval requested.\n"
    ),
    FIXTURE_DS001: (
        "---\n"
        "group_id: server-ts004-9401\n"
        "type: DS\n"
        "doc_id: server-ts004-9401-DS0001\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 DS Instruction for Path Verification\n"
        "priority: medium\n"
        "target_id: server-ts004-9401-AR0001\n"
        "next: D\n"
        "---\n\n"
        "## Design Instructions\n\n"
        "- Verify that source_file / requirement_file / reference_qa.file are returned as paths relative to docs_root.\n"
        "- Check whether the docs_root line is included in the copied-message output.\n"
    ),
    FIXTURE_DS002: (
        "---\n"
        "group_id: server-ts004-9402\n"
        "type: DS\n"
        "doc_id: server-ts004-9402-DS0002\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 DC Instruction for Verifying project_root in T Candidate\n"
        "priority: medium\n"
        "next: T\n"
        "---\n\n"
        "## Design Instructions\n\n"
        "- Verify that project_root is correctly included in T candidate next_actions.\n"
    ),
    FIXTURE_D001: (
        "---\n"
        "group_id: server-ts004-9402\n"
        "type: D\n"
        "doc_id: server-ts004-9402-D0001\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 Design Document\n"
        "priority: medium\n"
        "target_id: server-ts004-9402-DS0002\n"
        "---\n\n"
        "## Design Details\n\n"
        "- Design document for TS004 DC test.\n"
    ),
    FIXTURE_DC001: (
        "---\n"
        "group_id: server-ts004-9402\n"
        "type: DC\n"
        "doc_id: server-ts004-9402-DC0001\n"
        "project: server\n"
        "module: ts004\n"
        "title: TS004 DC For Verifying project_root in T Candidate\n"
        "priority: medium\n"
        "target_id: server-ts004-9402-DS0002\n"
        "approved_files:\n"
        "  - D001_design.md\n"
        "---\n\n"
        "## Design Complete\n\n"
        "- Verify that project_root is correctly included in T candidate next_actions.\n"
        "- Check that selecting T in the copied-message adds a project_root line.\n"
    ),
}

for fname, content in _FIXTURE_FILES.items():
    fpath = os.path.join(_FIXTURE_DIR, fname)
    if os.path.exists(fpath):
        print(f"  [skip] {fname} already exists")
    else:
        _write_text(fpath, content)
        print(f"  [ok]   {fname}")


# ── Step 2: Group 9401 (DS open, next=D) ─────────────────────────
# Chain: R001 ← AR001 ← DS001(open, next=D)
#        R001 ← Q001 ← A001  (for reference_qa tests)

print("=== Step 2: Group server-ts004-9401 ===")
_ensure_group("server-ts004-9401", "TS004 DS Path Verification")

_ensure_doc(
    doc_id="server-ts004-9401-R0001",
    doc_type="R",
    title="TS004 T051 Requirement Definition for Path Verification",
    group_id="server-ts004-9401",
    status="accepted",
    memo_file=FIXTURE_R001,
)
_ensure_doc(
    doc_id="server-ts004-9401-Q0001",
    doc_type="Q",
    title="TS004 T051 Requirement Clarification Question",
    group_id="server-ts004-9401",
    status="accepted",
    target_id="server-ts004-9401-R0001",
    memo_file=FIXTURE_Q001,
)
_ensure_doc(
    doc_id="server-ts004-9401-A0001",
    doc_type="A",
    title="TS004 T051 Requirement Clarification Answer",
    group_id="server-ts004-9401",
    status="accepted",
    target_id="server-ts004-9401-Q0001",
    memo_file=FIXTURE_A001,
)
_ensure_doc(
    doc_id="server-ts004-9401-AR0001",
    doc_type="AR",
    title="TS004 T051 Requirement Approval Request",
    group_id="server-ts004-9401",
    status="accepted",
    target_id="server-ts004-9401-R0001",
    memo_file=FIXTURE_AR001,
)
_ensure_doc(
    doc_id="server-ts004-9401-DS0001",
    doc_type="DS",
    title="TS004 DS Instruction for Path Verification",
    group_id="server-ts004-9401",
    status="open",
    target_id="server-ts004-9401-AR0001",
    next_action="D",
    direction="inbox",
    memo_file=FIXTURE_DS001,
)

# DS001 physical file → inbox
_inbox_ds001 = os.path.join(_INBOX_DIR, FIXTURE_DS001)
if not os.path.exists(_inbox_ds001):
    _write_text(_inbox_ds001, _FIXTURE_FILES[FIXTURE_DS001])
    print(f"  [ok]   inbox/{FIXTURE_DS001}")
else:
    print(f"  [skip] inbox/{FIXTURE_DS001} already exists")


# ── Step 3: Group 9402 (DC open, DS next=T) ───────────────────────
# Chain: DS002(accepted, next=T) ← D001(accepted) + DC001(open)

print("=== Step 3: Group server-ts004-9402 ===")
_ensure_group("server-ts004-9402", "TS004 DC T Candidate Verification")

_ensure_doc(
    doc_id="server-ts004-9402-DS0002",
    doc_type="DS",
    title="TS004 DC Instruction for Verifying project_root in T Candidate",
    group_id="server-ts004-9402",
    status="accepted",
    next_action="T",
    memo_file=FIXTURE_DS002,
)
_ensure_doc(
    doc_id="server-ts004-9402-D0001",
    doc_type="D",
    title="TS004 Design Document",
    group_id="server-ts004-9402",
    status="accepted",
    target_id="server-ts004-9402-DS0002",
    memo_file=FIXTURE_D001,
)
_ensure_doc(
    doc_id="server-ts004-9402-DC0001",
    doc_type="DC",
    title="TS004 DC For Verifying project_root in T Candidate",
    group_id="server-ts004-9402",
    status="open",
    target_id="server-ts004-9402-DS0002",
    direction="inbox",
    memo_file=FIXTURE_DC001,
)

# DC001 physical file → inbox
_inbox_dc001 = os.path.join(_INBOX_DIR, FIXTURE_DC001)
if not os.path.exists(_inbox_dc001):
    _write_text(_inbox_dc001, _FIXTURE_FILES[FIXTURE_DC001])
    print(f"  [ok]   inbox/{FIXTURE_DC001}")
else:
    print(f"  [skip] inbox/{FIXTURE_DC001} already exists")

# D001 physical file → processed (_locate_file searches here when approving DC)
_processed_d001 = os.path.join(_PROCESSED_DIR, FIXTURE_D001)
if not os.path.exists(_processed_d001):
    _write_text(_processed_d001, _FIXTURE_FILES[FIXTURE_D001])
    print(f"  [ok]   processed/{FIXTURE_D001}")
else:
    print(f"  [skip] processed/{FIXTURE_D001} already exists")


# ── Step 4: Guidance for checking the server entry in project_settings ─

print("=== Step 4: project_settings check ===")
settings = {r["project"]: r for r in db.get_project_settings()}
if "server" in settings:
    s = settings["server"]
    print(f"  [info] server docs_root   = {s['docs_root'] or '(not set)'}")
    print(f"  [info] server project_root = {s['project_root'] or '(not set)'}")
else:
    print("  [warn] server project_settings not configured")
    print(f"         Run TC-01 first or execute the following:")
    print(f"         Settings → server row → docs_root={_DOCS_ROOT}")
    print(f"                              → project_root={os.path.dirname(__file__)}")


# ── Done ───────────────────────────────────────────────────────────

print("")
print("=== Done ===")
print("Groups    : server-ts004-9401, server-ts004-9402")
print("Documents : server-ts004-9401-R0001, Q001, A001, AR001, DS001 (9401)")
print("            server-ts004-9402-DS0002, D001, DC001 (9402)")
print("Fixtures  : _documents/FlowGate/90_test_scenario/ts004/")
print("Inbox     : DS001_design_spec.md, DC001_design_complete.md")
print("")
print("TC-02 relative path test prerequisites:")
print(f"  docs_root = {_DOCS_ROOT}")
print(f"  fixture file location: {_FIXTURE_DIR}")
print("  In the Settings screen, set server/docs_root to the above path so that relative paths are returned.")
