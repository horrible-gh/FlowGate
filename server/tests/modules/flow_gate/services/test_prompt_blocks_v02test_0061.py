"""flowgate.v02test.0061 card 4 -- the prompt block registry (design 0004-D section 1, 2.1, 4.1; requirement R1).

* Every shared chunk (unread-body rules, bounded document reads, encoding probe, turn contract, attachment
  upload request, Graph state read, source tool lines, remote CLI contracts) is defined once, in
  ``services/prompt_blocks/blocks``; no other server source carries a copy of its long sentences.
* The old owners delegate, so mentions and help render exactly the text they rendered before.
* Block modules never import each other; help never calls a mention builder.
* The two rework assemblies (``invoke_mention_service`` / ``ai_invoke_routes``) share one composition.
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

SERVER_DIR = Path(__file__).resolve().parents[4]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

BLOCKS_DIR = SERVER_DIR / "modules" / "flow_gate" / "services" / "prompt_blocks" / "blocks"
HELP_DIR = SERVER_DIR / "modules" / "flow_gate" / "services" / "help"
SCAN_ROOTS = (SERVER_DIR / "modules", SERVER_DIR / "flowgate")
SKIP_DIRS = {"tests", "__pycache__", ".venv", "venv", "node_modules", ".git"}
MIN_SEGMENT = 50

EXPECTED_BLOCKS = {
    "attachments.upload_request",
    "cards.state",
    "conversation.encoding",
    "conversation.turn",
    "documents.lookup",
    "documents.unreadable",
    "documents.unreadable_answer",
    "source_tools.crud",
    "submit.remote_cli",
    "submit.remote_cli_chat",
    "submit.remote_cli_q_answer",
}

BASE = "http://testserver/flowgate/api/v1"


# ── single definition ────────────────────────────────────────────────────────

def _block_files() -> list[Path]:
    return sorted(p for p in BLOCKS_DIR.glob("*.py") if p.name != "__init__.py")


def _long_segments(path: Path) -> set[str]:
    """Every line (split on newline) of every string literal in ``path`` that is long enough to be prose."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)
    }
    segments: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            for segment in node.value.split("\n"):
                segment = segment.strip()
                if len(segment) >= MIN_SEGMENT and '"' not in segment and "\\" not in segment:
                    segments.add(segment)
    return segments


def _source_files(roots) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            files.extend(Path(dirpath) / name for name in names if name.endswith(".py"))
    return sorted(files)


def _display(path: Path) -> str:
    try:
        return path.relative_to(SERVER_DIR).as_posix()
    except ValueError:
        return path.name


def duplicated_segments(block_files, roots) -> list[str]:
    """``file: segment`` for every long block sentence found in a source file other than its own block."""
    texts = {path: path.read_text(encoding="utf-8", errors="replace") for path in _source_files(roots)}
    offenders = []
    for block in block_files:
        for segment in sorted(_long_segments(block)):
            for path, text in texts.items():
                if path.resolve() != block.resolve() and segment in text:
                    offenders.append(f"{_display(path)}: {segment[:80]}")
    return offenders


def test_every_shared_chunk_is_defined_in_exactly_one_place():
    assert len(_block_files()) >= 6
    assert sum(len(_long_segments(p)) for p in _block_files()) >= 30, "segment scan inspected too little"
    offenders = duplicated_segments(_block_files(), SCAN_ROOTS)
    assert not offenders, "prompt block text copied outside its block module:\n" + "\n".join(offenders)


def test_the_duplicate_scan_catches_a_copy(tmp_path):
    block = tmp_path / "blocks" / "demo.py"
    block.parent.mkdir()
    sentence = "This sentence is long enough to count as shared prompt prose for the scan."
    block.write_text(f"TEXT = ({sentence!r})\n", encoding="utf-8")
    copy = tmp_path / "other" / "copy.py"
    copy.parent.mkdir()
    copy.write_text(f"COPY = {sentence!r}\n", encoding="utf-8")
    assert duplicated_segments([block], [tmp_path]) == [f"copy.py: {sentence[:80]}"]
    copy.write_text("COPY = 'unrelated'\n", encoding="utf-8")
    assert duplicated_segments([block], [tmp_path]) == []


# ── registry shape and dependency rules ──────────────────────────────────────

def test_registry_lists_every_block_with_a_resolvable_help_leaf():
    from modules.flow_gate.services import prompt_blocks
    from modules.flow_gate.services.help.tree import AREA_LEAVES, AREA_ORDER

    assert set(prompt_blocks.names()) == EXPECTED_BLOCKS
    for name in prompt_blocks.names():
        block = prompt_blocks.get(name)
        assert block.name == name and callable(block.brief) and callable(block.detail)
        assert block.visible(None) is True
        if block.help_leaf is None:
            continue
        area, _, leaf = block.help_leaf.partition("/")
        assert area in AREA_ORDER, block.help_leaf
        if leaf:
            assert leaf in AREA_LEAVES[area], block.help_leaf


def test_registering_a_different_block_under_a_taken_name_fails():
    from modules.flow_gate.services import prompt_blocks

    with pytest.raises(ValueError):
        prompt_blocks.register(prompt_blocks.Block("cards.state", brief=str, detail=str))


def test_block_modules_import_only_the_registry():
    for path in _block_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    assert node.level == 2 and module == "registry", (path.name, node.level, module)
                else:
                    assert "prompt_blocks" not in module, (path.name, module)
                    assert not module.startswith("flowgate"), (path.name, module)


def test_help_never_calls_a_mention_builder():
    help_files = [*sorted(HELP_DIR.glob("*.py")),
                  SERVER_DIR / "modules" / "flow_gate" / "services" / "chat_help_content.py"]
    for path in help_files:
        text = path.read_text(encoding="utf-8")
        assert "cli_mention" not in text, path.name
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id == "mention_service":
                assert not node.attr.startswith(("build_", "_build_")), (path.name, node.attr)


# ── old owners delegate: same text, same objects ─────────────────────────────

def test_old_owners_render_the_block_text():
    from modules.flow_gate.documents.attachments import request_tools
    from modules.flow_gate.services import (chat_help_content, invoke_mention_service, mention_service,
                                            prompt_blocks, terminal_prompt)
    from modules.flow_gate.services.ai_invoke import worker
    from modules.flow_gate.services.prompt_blocks.blocks import (attachments, cards, conversation, documents,
                                                                 source_tools, submit)
    from flowgate.graph_read import tools as graph_read_tools

    assert request_tools.contract_mention(BASE, "tok") == prompt_blocks.detail("attachments.upload_request", BASE, "tok")
    assert attachments.upload_request_contract(BASE, "tok").startswith("## Attachment upload request\n---\n")
    assert graph_read_tools.cli_mention(BASE, None) == cards.graph_state_read(BASE, None)
    assert cards.graph_state_read(BASE).startswith("[Bound Graph state read]\nGET http://testserver/flowgate/api/v2/")
    assert worker._REMOTE_CLI_TRANSPORT is submit.REMOTE_CLI_TRANSPORT
    assert worker._REMOTE_CLI_CHAT_TRANSPORT is submit.REMOTE_CLI_CHAT_TRANSPORT
    for locale in ("ko", "en", "ja"):
        assert terminal_prompt.q_transport(locale) == prompt_blocks.brief("submit.remote_cli_q_answer", locale)
    assert chat_help_content._encoding_probe_paragraph is conversation.encoding_probe_paragraph
    assert invoke_mention_service._encoding_probe_paragraph is conversation.encoding_probe_paragraph
    assert invoke_mention_service.conversation_turn_submission_lines is conversation.conversation_turn_submission_lines
    assert mention_service.BODY_UNREADABLE_RULES is documents.BODY_UNREADABLE_RULES
    assert mention_service.BODY_UNREADABLE_ANSWER_REFERENCE is documents.BODY_UNREADABLE_ANSWER_REFERENCE
    lines = mention_service._document_lookup_lines(BASE, "tok", project="p", doc_id="d")
    assert lines == documents.document_lookup_brief(BASE, "tok", project="p", doc_id="d")
    assert lines[-1] == "Authorization: Bearer tok"
    assert documents.BODY_UNREADABLE_RULES[4] in lines
    assert len(lines) < len(documents.document_lookup_lines(BASE, "tok"))
    assert mention_service._remote_source_crud_lines(BASE, "", "T") == source_tools.remote_source_crud_lines(BASE, "", "T") == []


def test_help_reads_the_block_detail(monkeypatch):
    from modules.flow_gate.services.help import content as help_content
    from modules.flow_gate.services.prompt_blocks.blocks import documents

    monkeypatch.setattr(documents, "BODY_UNREADABLE_RULES", ("ONLY RULE",))
    assert help_content._body_unreadable_rules() == ("ONLY RULE",)


# ── one rework composition ───────────────────────────────────────────────────

def test_both_rework_entries_share_one_composition():
    from modules.flow_gate.api.v1 import ai_invoke_routes
    from modules.flow_gate.services import invoke_mention_service as ims

    for fn in (ims.build_rework_mention, ai_invoke_routes.build_rework_mention):
        source = inspect.getsource(fn)
        assert "compose_rework_mention(" in source
        assert "build_rejection_section(" not in source
        assert "rejection_history" not in source
    assert "build_rejection_section(" in inspect.getsource(ims.compose_rework_mention)


def test_rework_composition_orders_rejection_then_handoff_then_base():
    from modules.flow_gate.services import invoke_mention_service as ims

    base = "## base\n---\nbody\n"
    assert ims.compose_rework_mention(base, {}, None) == base
    assert ims.compose_rework_mention(base, {"rejection_history": "not json"}, None) == base
    section = ims.build_rejection_section([], "too short")
    assert section
    assert ims.compose_rework_mention(base, {}, "too short") == section + "\n" + base
    assert ims.compose_rework_mention(base, {"rejection_reason": "too short"}, None) == section + "\n" + base
    assert ims.compose_rework_mention(base, {}, None, handoff="H\n") == "H\n\n" + base
    assert ims.compose_rework_mention(base, {}, "too short", handoff="H\n") == section + "\nH\n\n" + base
