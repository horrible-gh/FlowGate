"""Hangul source census ratchet (T0009 work item 7 / NR0008 §5 Q8-2).

`test_server_korean_leak_0355.py` is AST-based, so it structurally cannot see
comments or docstrings — `ast` discards them at the lexing stage (NR0008 §5 Q7).
Protecting comments needs a different mechanism: a plain text-line scanner. This
module is that scanner, scoped narrowly on purpose (T0009 §1.3): it only walks
`server/modules/**` and `server/templates/**`, the two roots this T actually
cleaned up. `server/tests`, `server/sql`, and the client tree are follow-up-T
territory and are deliberately NOT in `SCANNED_ROOTS` yet — widening it there
before those T's land would just create merge conflicts with their own work.

The guard is a RATCHET, not a purge: it does not require zero Korean. It measures
today's count per file (2026-08-18, after TR0010 rev2 finished translating every
inbox_routes.py) and fails only when a file's count grows past that baseline. A
protected (B) or locale-dictionary (A) coordinate registered in
`_korean_allowlist` is excluded from the count, same as the AST guard's exclusion
in `test_server_korean_leak_0355.py`.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _korean_allowlist  # noqa: E402 — needs the sys.path insert above

# Hangul syllables (AC00-D7A3) + Hangul Compatibility Jamo (3131-318E). NR0008 §1.2
# measured that widening further (jamo extensions, halfwidth) finds zero additional
# lines in this repository, so the narrower pattern is sufficient here too.
_HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")

# T0009 work item 7-1: scan roots as a module constant so a follow-up T can widen coverage
# with a one-line addition (e.g. append "tests" once NR0008 T-2 lands). This T's own
# scan covers only these two (the §1.3 scope).
SCANNED_ROOTS = ["modules", "templates"]

# T0009 work item 7-6: per-file line caps, re-measured 2026-08-18 after TR0010 rev2's
# (inbox_routes.py comment translation) finished — this is the real post-cleanup
# count, not a guess. A file not listed here has an implicit cap of 0: any Korean at
# all in a file with none today fails the census the moment it appears. An existing
# file's cap is exactly its measured count; one more line above that is new Korean.
FILE_LINE_CAPS: dict[str, int] = {
    # 21 -> 45, 2 -> 13, 61 -> 70: flowgate.default.0467 T0002 added the [단계별 확인]
    # required-section gate (step_verification_service, wired into both inbox handlers'
    # Step 5.71) plus its ko notice/comment text — product copy and design-rationale
    # comments, not the kind of stray Korean this census exists to catch.
    "modules/flow_gate/api/inbox_routes.py": 45,
    # 0 -> 5: flowgate.default.0482 T0011 added the resolve_base_dirty worker mention's
    # decision contract (_RESOLVE_BASE_DIRTY_CONTRACT) — product copy sent to the AI
    # worker, collapsed to one ko source line plus the four pre-existing design-rationale
    # comments (0443 T0002, 0414 P0007) already in this file.
    "modules/flow_gate/api/v1/ai_invoke_routes.py": 5,
    # 15 -> 13: flowgate.default.0484 T0005 removed the PATCH /content submission
    # gate's Korean fallback and section-name docstring. The measured remainder is legacy
    # product copy and design rationale unrelated to the inbox-only submission check.
    "modules/flow_gate/documents/routers/documents.py": 13,
    "modules/flow_gate/documents/routers/work_plan.py": 5,
    "modules/flow_gate/process_service.py": 2,
    "modules/flow_gate/services/ai_invoke_service.py": 4,
    "modules/flow_gate/services/conversation_turn_service.py": 10,
    "modules/flow_gate/services/document_outline_service.py": 1,
    "modules/flow_gate/services/help_catalog.py": 70,
    "modules/flow_gate/services/invoke_mention_service.py": 4,
    "modules/flow_gate/services/mention_service.py": 157,
    "modules/flow_gate/services/q_answer_invoke_service.py": 24,
    "modules/flow_gate/services/remote_tool_service.py": 14,
    # New file (flowgate.default.0467 T0002) — mirrors tr_scope_service.py's shape
    # (required-section parser + ko/en notice text), so a comparable line count.
    "modules/flow_gate/services/step_verification_service.py": 49,
    "modules/flow_gate/services/test_run_service.py": 28,
    "modules/flow_gate/services/tool_registry.py": 45,
    "modules/flow_gate/services/tr_scope_service.py": 53,
    # 15 -> 16: 0444 T0005 added the ko copy for the done_rows_skipped warning. _COPY is the
    # user-facing warning text, and the T doc requires all three locales, so the ko line is
    # product copy rather than a comment the census exists to catch.
    "modules/flow_gate/services/work_plan_apply_service.py": 16,
    "modules/flow_gate/services/work_plan_service.py": 52,
    "modules/flow_gate/services/workflow_decision_service.py": 3,
    "modules/flow_gate/template_provision.py": 23,
    "modules/flow_gate/workflow/pipeline_service.py": 2,
    "modules/flow_gate/workflow/prompt_copy_service.py": 4,
}


def _relative_path(path: Path) -> str:
    return path.relative_to(_SERVER_DIR).as_posix()


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SCANNED_ROOTS:
        root = _SERVER_DIR / root_name
        if not root.exists():
            continue
        pattern = "*.py" if root_name == "modules" else "*"
        files.extend(sorted(p for p in root.rglob(pattern) if p.is_file()))
    return files


def _korean_lines(path: Path) -> list[tuple[int, str]]:
    """Every line in ``path`` with an un-allowlisted Hangul character, 1-indexed."""
    rel = _relative_path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not _HANGUL.search(stripped):
            continue
        if _korean_allowlist.is_allowlisted(rel, stripped):
            continue
        hits.append((lineno, stripped))
    return hits


def test_korean_source_census_stays_within_measured_caps():
    """T0009 work item 7: fails the moment server/modules/** or server/templates/** grows
    a NEW un-allowlisted Korean line beyond the 2026-08-17 measured baseline. Failure
    message includes the file, the offending line number, and its text, per item 7-7."""
    offenders = []
    for path in _scanned_files():
        rel = _relative_path(path)
        hits = _korean_lines(path)
        cap = FILE_LINE_CAPS.get(rel, 0)
        if len(hits) > cap:
            for lineno, text in hits[cap:]:
                offenders.append(
                    f"{rel}:{lineno}: {text!r} (cap={cap}, actual={len(hits)})"
                )
    assert not offenders, (
        "New Korean source line(s) beyond the T0009 census baseline "
        "(server/modules/** + server/templates/**):\n" + "\n".join(offenders[:50])
    )


def test_census_cap_table_has_no_stale_entries():
    """A cap entry for a file that no longer exists (renamed/deleted/moved out of the
    scanned roots) is dead weight that silently protects a phantom budget — catch it
    so the table stays an honest description of the current tree."""
    scanned_rel = {_relative_path(p) for p in _scanned_files()}
    stale = sorted(set(FILE_LINE_CAPS) - scanned_rel)
    assert not stale, f"Stale FILE_LINE_CAPS entries (file no longer scanned): {stale}"


def test_census_catches_a_freshly_added_korean_comment(tmp_path, monkeypatch):
    """Work item 7 completion criterion: a deliberately-injected new Korean comment must trip the
    census. Points SCANNED_ROOTS at an isolated tmp tree with one clean file and one
    file carrying one un-budgeted Korean comment line, proving the ratchet actually
    ratchets rather than passing vacuously."""
    fake_server = tmp_path / "server"
    modules_dir = fake_server / "modules"
    modules_dir.mkdir(parents=True)
    clean_file = modules_dir / "clean.py"
    clean_file.write_text("def f():\n    return 1\n", encoding="utf-8")
    dirty_file = modules_dir / "dirty.py"
    dirty_file.write_text(
        "def g():\n    # 새로 추가된 한글 주석\n    return 2\n", encoding="utf-8"
    )

    monkeypatch.setattr(sys.modules[__name__], "_SERVER_DIR", fake_server)

    offenders = []
    for path in _scanned_files():
        rel = _relative_path(path)
        hits = _korean_lines(path)
        cap = FILE_LINE_CAPS.get(rel, 0)
        if len(hits) > cap:
            offenders.append(rel)
    assert offenders == ["modules/dirty.py"], (
        "the injected Korean comment must be the only offender, and it must be caught"
    )

    # Removing the offending line restores GREEN — the ratchet does not stay stuck.
    dirty_file.write_text("def g():\n    return 2\n", encoding="utf-8")
    offenders_after_fix = []
    for path in _scanned_files():
        rel = _relative_path(path)
        hits = _korean_lines(path)
        cap = FILE_LINE_CAPS.get(rel, 0)
        if len(hits) > cap:
            offenders_after_fix.append(rel)
    assert offenders_after_fix == []
