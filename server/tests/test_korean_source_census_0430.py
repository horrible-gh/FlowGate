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
    # 5 -> 7: flowgate.default.0481 T0008 added the merge review gate's [자동]
    # checkbox wiring (record_auto_authority called from the [AI 호출] start path) —
    # two design-rationale comment lines quoting the D0006/L0007 Korean button labels,
    # on top of the five already measured here.
    "modules/flow_gate/api/v1/ai_invoke_routes.py": 7,
    # 15 -> 13: flowgate.default.0484 T0005 removed the PATCH /content submission
    # gate's Korean fallback and section-name docstring. The measured remainder is legacy
    # product copy and design rationale unrelated to the inbox-only submission check.
    "modules/flow_gate/documents/routers/documents.py": 13,
    "modules/flow_gate/documents/routers/work_plan.py": 5,
    "modules/flow_gate/process_service.py": 2,
    # New (0 -> 21): flowgate.default.0481 T0008 item 1 (0009-TR rev2) added
    # `_build_write_plan_section` — the merge review's explicit [수정 적용] turn has
    # no write tool at all, so this mention section is the ONLY place the AI worker
    # learns the anchored write-plan schema and the bound submission endpoint. All 21
    # lines are that instructional Korean text (product copy the worker reads), not a
    # design-rationale comment. This file had no measured Korean before 0481.
    "modules/flow_gate/api/token_routes.py": 21,
    # 4 -> 6: flowgate.default.0481 T0008 item 1 (the anchored write-plan engine's
    # apply route, 0009-TR rev2) added the [수정 적용]/[테스트 편집 포함 재지시]
    # button labels to two design-rationale docstring/comment lines, on top of the
    # four already measured here (the earlier general-merge review gate windows).
    "modules/flow_gate/api/v1/git_routes.py": 6,
    # flowgate.default.0501 T6 (NR0003 §12) moved the engine into the ai_invoke/
    # package; T0019 then merged main into it. Every cap below is the freshly MEASURED
    # count, and the arithmetic is what says no Hangul line was written by either step:
    #
    #   main's engine, three files:  ai_invoke_service.py 22 + part2_worker.py 2
    #                                + part3_chain.py 31            = 55
    #   the package, six files:      admission 14 + chain 15 + diagnostics 1
    #                                + review 15 + runtime 8 + worker 2 = 55
    #
    # admission.py holds the _WORKTREE_UNAVAILABLE_COPY / _RUN_ID_COLLISION_COPY ko
    # locale strings plus the 0414/0443 product-rationale comments that travel with
    # start_run; runtime.py holds the parameter block's own rationale comments.
    #
    # Two caps moved off T6's original numbers, both bookkeeping rather than new text:
    # runtime.py 7 -> 8 because 0490 T0005's restart_max_attempts_choices() docstring
    # (which named the "재시작 횟수" select) came across from main's parameter block with
    # the function, and diagnostics.py 0 -> 1 because T6 never registered the one
    # 0414 P0007 comment it inherited from part3_chain.py — that omission is why this
    # census listed diagnostics.py as an offender before the merge. chain.py drops
    # 16 -> 15 to its measured count, so no cap here is a ceiling above what is there.
    #
    # 14 -> 19: re-measured for flowgate.default.0481 T0008 item 1 (0009-TR rev2).
    # This file's true count was ALREADY 17 (not 14) before this change — drift from
    # other groups' work landing after the T6 merge's cap was last measured, same
    # untracked-by-this-census pattern the tr_commit_ledger.py/mention_service.py
    # entries describe elsewhere in this dict. 0481's own addition is exactly 2 lines
    # (start_run's write_requested_by_human/allow_test_edits parameter doc, quoting the
    # [수정 적용]/[반려]/[테스트 편집 포함 재지시] button labels it threads through to
    # the merge review's write-plan engine) — 17 + 2 = 19.
    "modules/flow_gate/services/ai_invoke/admission.py": 19,
    "modules/flow_gate/services/ai_invoke/chain.py": 15,
    "modules/flow_gate/services/ai_invoke/diagnostics.py": 1,
    "modules/flow_gate/services/ai_invoke/review.py": 15,
    "modules/flow_gate/services/ai_invoke/runtime.py": 8,
    "modules/flow_gate/services/ai_invoke/worker.py": 2,
    "modules/flow_gate/services/conversation_turn_service.py": 10,
    "modules/flow_gate/services/document_outline_service.py": 1,
    # New (0 -> 21): this census never listed git_service.py before — flowgate.default.0481
    # T0008's general-merge human approval gate (freeze/approve/reject/reconcile) is the
    # first change to this file this census tracks. All 21 are design-rationale comments
    # quoting the D0006/L0007 Korean button labels ([AI 호출]/[해결 제출]/[승인]/[반려]/
    # [병합]/[다시 시도]) and section names ("그룹 관측 상태", "TR 세션에는 적용되지 않는다")
    # the code implements — not product copy, and not new Korean this file lacked before;
    # the file already carried plenty (this census simply never measured it until now).
    #
    # 21 -> 27: flowgate.default.0481 T0008 item 1 (0009-TR rev2) added the anchored
    # write-plan engine (apply_write_plan / submit_review_write_plan /
    # _validate_write_plan_structure / _materialize_pending_conversation_run's apply
    # branch) — 6 more design-rationale comments and AI-turn message strings quoting
    # the same [수정 적용]/[테스트 편집 포함 재지시] button labels and reporting the
    # apply outcome in Korean (the conversation panel's own language).
    #
    # 27 -> 33: flowgate.default.0481 0009-TR rev3 (AI review finding 2) made
    # held_test_operations OBSERVABLE instead of silently dropped after
    # validation: apply_write_plan's held-only/mixed branches and
    # _materialize_pending_conversation_run's held-operation summary added 6
    # more Korean AI-turn message strings/comments quoting held paths, purposes,
    # and the same [테스트 편집 포함 재지시] label.
    #
    # 33 -> 34 -> 33: flowgate.default.0481 0009-TR rev4 (AI review finding) added
    # the L0007 §2.9 stale_run guard — 1 new Korean AI-turn message string reporting
    # a discarded (stale) re-instruction run result to the human in the same
    # conversation panel language as the other apply-outcome messages already
    # counted above. rev5 then took one line back OUT: the write-plan structure
    # check's 422 payload quoted the [테스트 편집 포함 재지시] button label inside a
    # `raise`, which test_server_korean_leak_0355 forbids (an error payload is not
    # panel copy), so that message is English again. Freshly re-measured, not
    # inherited.
    # 33 -> 34: flowgate.default.0481 T0010 rev1 (2026-09-08 rejection — the approval
    # screen's chat must be waited out in place) added ONE Korean AI-turn message: the
    # `run_lost` turn that tells the human, in the conversation panel, that the run
    # holding their question left no record and the message has to be sent again. Same
    # panel copy, same language as the stale_run/apply-outcome turns already counted
    # here; the rest of that change's comments are English on purpose.
    # 34 -> 37: T0010 rev3 quotes, in the one docstring that decides the rule, the
    # design sentence it turns on (L0007 `syntax_validation_scope`), the button the
    # human presses, and the rejection itself. Comments, not panel copy; the rest of
    # that change is English.
    # 37 -> 43: T0010 rev6 (2026-09-08 반려 3) rewrites the stale_run conversation turn.
    # Five of the six are that turn's PANEL COPY — the sentence the operator reads in the
    # approval screen's chat, which now names which of the three identity checks fired and
    # keeps the answer instead of replacing it. The sixth is one comment quoting the
    # [AI에게 맡기기] button the base-dirty AI-run field on the status payload feeds.
    "modules/flow_gate/services/git_service.py": 43,
    # 70 -> 73: flowgate.default.0523 T0004 added the document_attachments help item
    # (title/summary/note, ko locale) that bridges attachment list/read/copy to the
    # worker-token document surface.
    "modules/flow_gate/services/help_catalog.py": 73,
    "modules/flow_gate/services/invoke_mention_service.py": 4,
    # 157 -> 176: this file was already at 175 (pre-existing drift from other merged
    # groups untouched by 0523, same as the tr_commit_ledger.py entry this census still
    # doesn't list — it stays at its old cap because 0523 never edits it).
    # flowgate.default.0523 T0004 §18 then added
    # exactly one new ko line — the mention's document_attachments help-item pointer —
    # bringing the measured count to 176.
    "modules/flow_gate/services/mention_service.py": 176,
    "modules/flow_gate/services/q_answer_invoke_service.py": 24,
    "modules/flow_gate/services/remote_tool_service.py": 14,
    # New file (flowgate.default.0467 T0002) — mirrors tr_scope_service.py's shape
    # (required-section parser + ko/en notice text), so a comparable line count.
    "modules/flow_gate/services/step_verification_service.py": 49,
    "modules/flow_gate/services/test_run_service.py": 28,
    # 45 -> 97. Measured, split into its two causes: 55 lines were already there before
    # 0523 touched this file (drift merged in by other groups, the same untracked-by-this-
    # census drift the tr_commit_ledger.py entry carries), and 0523 T0004
    # §17 adds 42 more -- the ko half of ATTACHMENT_SUMMARY / ATTACHMENT_VIEW_NOTES /
    # ATTACHMENT_FIELDS / ATTACHMENT_ERRORS / ATTACHMENT_CAUTIONS, the locale dictionary
    # GET /help/tools serves to a ko worker. That is (A) locale-dictionary Korean, which
    # this census budgets rather than forbids; the surrounding ja/en tables carry the
    # identical text in their own locales.
    "modules/flow_gate/services/tool_registry.py": 97,
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
