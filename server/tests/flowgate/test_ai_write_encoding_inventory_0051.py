"""every write route an AI worker token can reach is classified for the encoding gate.

Corrupted Korean (``?? ????`` from a Windows command line) kept coming back because each
fix guarded only the route where it was found; question / answer registration was never
guarded. This inventory makes the next unguarded route a test failure instead of a
stored row: a POST/PUT/PATCH/DELETE handler under ``api/`` that verifies a worker token
must be listed below, either with the guard call its code makes or with the
reason it carries no AI-written prose.

Limits, stated so the list is not read as more than it is: it sees a worker token
verified in the ``api`` layer (directly or through helpers of the same module). Routes
that verify only inside a service (the remote source tools, card documents saved from
chat) have their own guards and are outside this scan.
"""
import ast
import pathlib
import re

import pytest

import modules.flow_gate.api as api_package

API_ROOT = pathlib.Path(api_package.__file__).resolve().parent
WRITE_METHODS = {"post", "put", "patch", "delete"}
# The calls that accept a worker token (alone or next to a login session).
WORKER_AUTH = ("token_service.verify(", "verify_bearer(", "inspect_for_replay(")

# "<file relative to api/>::<handler>" -> ("guard", "<call the handler's code makes>")
#                                       or ("exempt", "<why no AI-written prose is stored>")
CLASSIFIED: dict = {
    "inbox_routes.py::inbox": ("guard", "_encoding_guard("),
    # append_turn / dry_run_append run conversation_turn_service._encoding_violation.
    "v1/conversation_routes.py::append_worker_conversation_turn": ("guard", "conversation_turn_service.append_turn("),
    "v1/q_tapi_routes.py::post_add_questions": ("guard", "_encoding_gate("),
    "v1/q_tapi_routes.py::post_register_answer": ("guard", "_gate_answer_body("),
    "v1/q_tapi_routes.py::post_register_answer_by_path": ("guard", "_gate_answer_body("),
    "v1/q_tapi_routes.py::post_escalate_to_user": ("guard", "_encoding_gate("),
}

# Handlers that existed when this inventory was written and have not been read for it yet.
# Frozen: nothing is added here. A new handler is guarded or exempted in CLASSIFIED; a
# reviewed one moves there. UNREVIEWED_MAX only goes down.
UNREVIEWED = frozenset("""
v1/ai_invoke_routes.py::cancel_ai_invoke
v1/ai_invoke_routes.py::cancel_chat_start
v1/ai_invoke_routes.py::dismiss_ai_invoke_run_card
v1/ai_invoke_routes.py::pause_ai_invoke
v1/ai_invoke_routes.py::release_ai_invoke_lease
v1/ai_invoke_routes.py::release_paused_ai_invoke
v1/ai_invoke_routes.py::resume_ai_invoke
v1/ai_invoke_routes.py::start_ai_invoke
v1/chat_command_routes.py::create_chat_command
v1/document_routes.py::post_document_attachment_copy
v1/engine_recipe_routes.py::create_engine_recipe
v1/engine_recipe_routes.py::delete_engine_recipe
v1/engine_recipe_routes.py::update_engine_recipe
v1/file_transfer_routes.py::upload_files
v1/git_routes.py::post_branch_merge_resolve_token
v1/git_routes.py::post_branch_merge_write_plan_token
v1/git_routes.py::post_merge_resolve_token
v1/git_routes.py::post_merge_write_plan_token
v1/module_routes.py::archive_module
v1/module_routes.py::create_module
v1/module_routes.py::delete_module
v1/module_routes.py::patch_module
v1/module_routes.py::restore_module
v1/self_check_routes.py::cancel_draft_run
v1/self_check_routes.py::cancel_run
v1/self_check_routes.py::reverify_run
v1/self_check_routes.py::start_draft_run
v1/self_check_routes.py::start_run
v1/snapshot_routes.py::cli_access_snapshot
v1/test_run_routes.py::post_spec_case_run
v1/test_run_routes.py::post_spec_run
v1/test_run_routes.py::post_test_results
v1/test_run_routes.py::post_test_run
v1/test_run_routes.py::post_test_run_cancel
v1/test_run_routes.py::post_test_run_request
v1/test_run_routes.py::put_spec_asset
v1/tree_routes.py::delete_src_path
v1/workflow_decision_routes.py::patch_workflow_sequence_endpoint
v1/workflow_decision_routes.py::post_review_request
v1/workflow_decision_routes.py::post_workflow_advance
v1/workflow_decision_routes.py::post_workflow_advance_rpc
v1/workflow_decision_routes.py::post_workflow_decide
v1/workflow_decision_routes.py::post_workflow_decide_rpc
v1/workflow_decision_routes.py::post_workflow_decision_request
v1/workflow_decision_routes.py::post_workflow_sequence_edit_request
""".split())
UNREVIEWED_MAX = 45


def _handlers():
    """Write handlers that verify a worker token, directly or through helpers of their own module."""
    found = {}
    for path in sorted(API_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {node.name: ast.get_source_segment(source, node) or ""
                     for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        reaches = {name for name, text in functions.items() if any(marker in text for marker in WORKER_AUTH)}
        grew = True
        while grew:
            grew = False
            for name, text in functions.items():
                if name not in reaches and any(re.search(r"\b" + re.escape(other) + r"\b", text) for other in reaches):
                    reaches.add(name)
                    grew = True
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in reaches:
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in WRITE_METHODS
                       for d in node.decorator_list):
                continue
            found[f"{path.relative_to(API_ROOT).as_posix()}::{node.name}"] = _with_local_callees(functions, node.name)
    return found


def _with_local_callees(functions, name):
    """The handler's code plus the code of the same-module functions it calls, transitively."""
    seen, queue = [], [name]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.append(current)
        queue.extend(other for other in functions
                     if other not in seen and re.search(r"\b" + re.escape(other) + r"\b", functions[current]))
    return "\n".join(functions[item] for item in seen)


def test_every_worker_reachable_write_route_is_classified():
    handlers = _handlers()
    missing = sorted(set(handlers) - set(CLASSIFIED) - UNREVIEWED)
    stale = sorted((set(CLASSIFIED) | UNREVIEWED) - set(handlers))
    assert len(UNREVIEWED) <= UNREVIEWED_MAX and not UNREVIEWED & set(CLASSIFIED)
    assert not missing, (
        "Write routes that accept a worker token and are not classified for the encoding gate. "
        "Call ai_text_guard.check (or the route's existing guard) on the text an AI can send, then list "
        "the handler in CLASSIFIED:\n  " + "\n  ".join(missing))
    assert not stale, "CLASSIFIED names handlers that no longer exist:\n  " + "\n  ".join(stale)


@pytest.mark.parametrize("key", sorted(k for k, v in CLASSIFIED.items() if v[0] == "guard"))
def test_guarded_route_still_calls_its_guard(key):
    assert CLASSIFIED[key][1] in _handlers()[key], f"{key} no longer calls {CLASSIFIED[key][1]}"


def test_every_exemption_states_its_reason():
    for key, (kind, detail) in CLASSIFIED.items():
        assert kind in ("guard", "exempt") and len(detail.strip()) >= 10, key
