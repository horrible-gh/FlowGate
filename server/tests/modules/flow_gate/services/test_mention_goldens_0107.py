"""F107 final snapshots. Authoring only; run explicitly in the test stage.

Help visibility is stubbed for deterministic text comparison, never activation
proof. No test updates snapshots or rollout evidence automatically.
"""
import json
from pathlib import Path

import pytest

from modules.flow_gate.services import terminal_prompt as terminal
from modules.flow_gate.services.help import loader
from modules.flow_gate.services.prompt_blocks import mention
from modules.flow_gate.services.prompt_blocks.matrix import MentionFacts, select_mention, transition_leaves

ROOT = Path(__file__).parent / "golden" / "mention_0107"
CASES = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))["cases"]


def prepared(case, monkeypatch):
    mode, locale = case["mode"], case["locale"]
    facts = MentionFacts(**case["facts"])
    first, more = select_mention(facts)
    rows = {name: {"visible": True, "response": True, "context": True}
            for name in set(first + more + transition_leaves(mode))}
    record = {"mode": mode, "token_mode": mode, "checked_at": "synthetic-only",
              "passed": True, "items": rows}
    read = loader.read_json
    monkeypatch.setattr(loader, "read_json", lambda name, root=None:
                        {mode: {"enabled": True, "verification": record}}
                        if name == "mention_rollout" else read(name, root))
    monkeypatch.setattr(mention, "help_selection", lambda f, c, l: (*select_mention(f), l))
    legacy = terminal.prepare("legacy", kind=case["kind"], locale=locale)
    localized = {locale: case["localized"], "ko": next(
        c["localized"] for c in CASES if c["mode"] == mode and c["locale"] == "ko")}
    result = mention.render_mention(legacy, facts=facts, values=case["values"],
                                    context={}, locale=locale, localized_values=localized)
    assert result.fallback_reason is None
    return result.text


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["file"])
def test_complete_provider_golden_and_budget(case, monkeypatch):
    prompt = prepared(case, monkeypatch)
    actual = terminal.finalize(mention.provider_prompt(prompt, case["workspace"]))
    expected = (ROOT / case["file"]).read_text(encoding="utf-8")
    assert actual == expected
    assert len(expected) == case["chars"]
    assert len(actual) <= case["max_chars"]
    assert actual.count("Authorization: Bearer FAKE-GOLDEN-TOKEN") == 1
    assert actual.count("https://example.invalid/flowgate/api/v1/help") == 1
    assert "Original {{token}} material. 사용자 원문 / 原文" in actual
    assert "flowgate-terminal-task" not in actual
    assert actual.startswith(terminal.render(terminal.specification(case["kind"], case["locale"])))


def test_exact_mode_locale_coverage_and_rollout_evidence():
    assert {(c["mode"], c["locale"]) for c in CASES} == {
        (mode, locale) for mode in mention.MODES for locale in ("ko", "en", "ja")}
    assert len(CASES) == 18
    rollout = loader.read_json("mention_rollout")
    assert set(rollout) == set(mention.MODES)
    for mode, row in rollout.items():
        if not row["enabled"]:
            continue
        # An enabled mode carries a passing probe covering every transition leaf.
        record = row["verification"]
        assert record["passed"] is True and record["mode"] == mode
        assert mention.probe_covers(record["token_mode"], mode)
        assert set(transition_leaves(mode)) <= set(record["items"])
        assert all(all(item[k] is True for k in ("visible", "response", "context"))
                   for item in record["items"].values())


@pytest.mark.parametrize("locale", ("ko", "en", "ja"))
@pytest.mark.parametrize("exec_type", ("api", "remote_cli"))
def test_rework_preinstruction_and_title_survive_provider(locale, exec_type, monkeypatch):
    from modules.flow_gate.services import invoke_mention_service as invoke, mention_service, node_title_hooks
    case = next(c for c in CASES if c["mode"] == "card.edit" and c["locale"] == locale)
    prompt = prepared(case, monkeypatch)
    _, task = terminal.split(prompt)
    heading = mention_service._CARD_MATERIAL_HEADERS[locale][0]
    reason = "Original rejection {{workspace}}"
    task["mention"]["values"]["materials"] = f"## {heading}\n---\n{reason}"
    prompt = terminal._with_spec("discarded preview", task)
    doc = {"rejection_reason": reason, "rejection_history": [
        {"reason": "Prior original", "rejected_at": "2026-01-01"}, {"reason": reason}]}
    prompt = invoke.compose_rework_mention(prompt, doc, None, handoff="Original handoff")
    prompt = invoke.prepend_pre_instruction_section(prompt, text="Original pre-instruction {{token}}",
                                                    attachment=None, locale="ko")
    # Applied mention locale, including fallback, owns the provider title wording.
    prompt = node_title_hooks.append_title_request(prompt, exec_type, "ko")
    actual = terminal.finalize(mention.provider_prompt(prompt, case["workspace"]))
    for original in (reason, "Prior original", "Original handoff", "Original pre-instruction {{token}}",
                     node_title_hooks.title_request(exec_type, locale)):
        assert actual.count(original) == 1
    assert actual.startswith(terminal.render(terminal.specification("rework", locale)))
    assert actual.index(reason) > actual.index("https://example.invalid/flowgate/api/v1/help")


@pytest.mark.parametrize("kind", ("new", "first_write", "edit", "rework"))
@pytest.mark.parametrize("mode", ("card.read", "card.edit"))
def test_card_kinds_keep_zero_and_omit_absent_optional_lines(kind, mode):
    asset, _, _ = loader.load_mention(mode, "en")
    spec = loader.definition("mention/" + mode)
    case = next(c for c in CASES if c["mode"] == mode and c["locale"] == "en")
    values = dict(case["values"], **case["localized"], expected_revision=0, revision_no=0,
                  task_block=terminal.render(terminal.specification(kind, "en")), help_first="notices")
    for name in ("workspace", "rejection_ref", "next_header", "self_check", "reference_doc_ids"):
        values.pop(name, None)
    actual = mention.render_body(asset["body"], spec, values, loader.read_json("routes"))
    assert "Expected revision: 0" in actual and "Revision: 0" in actual
    for label in ("Workspace:", "Rejection reference:", "Next document header values:", "Self-check:"):
        assert label not in actual


def test_graph_start_passes_job_node_without_unbound_local(monkeypatch):
    from modules.flow_gate.graph_workflow.services import ai_node_starter as starter
    node = {"node_key": "n1", "title": "Original card"}
    job = {"doc_ref": "root", "flow_id": "demo.test.0001", "project_id": "demo",
           "issued_to": "user", "node_key": "n1", "doc_type": "D", "node": node}
    monkeypatch.setattr(starter.db_documents, "get_by_id", lambda _: {"doc_id": "root"})
    monkeypatch.setattr(starter.token_service, "issue", lambda **kw:
                        {"token_id": "fake-id", "raw_token": "fake-token", "scratch_dir": "scratch"})
    monkeypatch.setattr(starter, "_map_if_open", lambda *args: False)
    monkeypatch.setattr(starter, "api_base_url", lambda _: "https://example.invalid/api/v1")
    captured = {}
    def build(**kwargs):
        captured.update(kwargs)
        return "legacy"
    monkeypatch.setattr(starter.mention_service, "build_mention_from_token_rec", build)
    monkeypatch.setattr(starter.ai_start_precheck, "node_mention", lambda text, *args: text)
    monkeypatch.setattr(starter.card_memo_hooks, "prepend_memo_section", lambda text, *args, **kw: text)
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    monkeypatch.setattr(card_settings_ports, "upstream_notice", lambda job: "")
    monkeypatch.setattr(starter.ai_invoke_service, "start_run", lambda **kw: kw["issue_builder"]("run"))
    result = starter.start_ai_invoke(job)
    assert captured["card_node"] is node
    assert result["mention"] == "legacy"
