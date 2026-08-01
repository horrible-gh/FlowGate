"""Design-template help pointer and submission gate — group 0372 set 2."""
from __future__ import annotations

import json
import os

os.environ.setdefault("TESTING", "1")

from modules.flow_gate import template_provision as tp
from modules.flow_gate.api import inbox_routes


_TEMPLATE = """# Template instructions

## Document Structure

```markdown
# {name} Logic Design

## Purpose
## 1. Parameters
## 2. State Transitions (if applicable)
## 3. Decision Tree
## [DEFERRED]
```

## Authoring Notes

Do not copy this instructional section into the document.
"""


def _resolved(content: str) -> dict:
    return {
        "content": content,
        "resolution": "global-exact",
        "resolved_locale": "en",
        "scope": "global",
        "bytes": len(content.encode("utf-8")),
        "is_active": 1,
        "resolved_template_id": 1,
    }


def _response_json(response) -> dict:
    return json.loads(bytes(response.body).decode("utf-8"))


def test_required_headings_come_from_the_structure_block_and_skip_optional():
    assert tp.required_document_headings(_TEMPLATE) == [
        "Purpose",
        "1. Parameters",
        "3. Decision Tree",
        "[DEFERRED]",
    ]


def test_structure_validation_accepts_real_sections_and_harmless_spacing(monkeypatch):
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "L")
    monkeypatch.setattr(tp, "resolve_active_template", lambda *_args: _resolved(_TEMPLATE))
    document = """# Actual Logic

## Purpose
text
## 1. Parameters
text
## 3. Decision   Tree
text
## [DEFERRED]
none
"""
    result = tp.validate_design_document_structure("flowgate", "L", "en", document)
    assert result["valid"] is True
    assert result["locale"] == "en"


def test_structure_validation_rejects_missing_and_out_of_order_sections(monkeypatch):
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "L")
    monkeypatch.setattr(tp, "resolve_active_template", lambda *_args: _resolved(_TEMPLATE))
    document = """# Actual Logic

## 3. Decision Tree
text
## Purpose
text
## [DEFERRED]
none
"""
    result = tp.validate_design_document_structure("flowgate", "L", "en", document)
    assert result["valid"] is False
    assert "1. Parameters" in result["missing"]
    assert "3. Decision Tree" in result["out_of_order"]


def test_copying_the_outline_inside_a_fence_does_not_pass(monkeypatch):
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "L")
    monkeypatch.setattr(tp, "resolve_active_template", lambda *_args: _resolved(_TEMPLATE))
    document = """# Empty document

```markdown
## Purpose
## 1. Parameters
## 3. Decision Tree
## [DEFERRED]
```
"""
    result = tp.validate_design_document_structure("flowgate", "L", "en", document)
    assert result["valid"] is False
    assert result["missing"] == [
        "Purpose", "1. Parameters", "3. Decision Tree", "[DEFERRED]"
    ]


def test_protocol_scenario_placeholder_matches_a_concrete_scenario(monkeypatch):
    template = "## Notation\n## [scenario name]\n## Writing principles"
    document = "## Notation\ntext\n## [normal1] Login\ntext\n## Writing principles\ntext"
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "P")
    monkeypatch.setattr(tp, "resolve_active_template", lambda *_args: _resolved(template))
    result = tp.validate_design_document_structure("flowgate", "P", "en", document)
    assert result["valid"] is True


def test_protocol_scenario_placeholder_is_not_satisfied_by_deferred(monkeypatch):
    template = "## Notation\n## [scenario name]\n## Writing principles"
    document = "## Notation\ntext\n## [DEFERRED]\nnone\n## Writing principles\ntext"
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "P")
    monkeypatch.setattr(tp, "resolve_active_template", lambda *_args: _resolved(template))
    result = tp.validate_design_document_structure("flowgate", "P", "en", document)
    assert result["valid"] is False
    assert "[scenario name]" in result["missing"]


def test_an_explicit_help_locale_may_match_after_the_token_locale(monkeypatch):
    templates = {
        "ko": "## 목적\n## 결정",
        "en": "## Purpose\n## Decision",
        "ja": "## 目的\n## 決定",
    }
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "D")
    monkeypatch.setattr(
        tp,
        "resolve_active_template",
        lambda _project, _code, locale: _resolved(templates[locale]),
    )
    result = tp.validate_design_document_structure(
        "flowgate", "D", "ko", "## Purpose\nbody\n## Decision\nbody"
    )
    assert result["valid"] is True
    assert result["locale"] == "en"


def test_inbox_rejects_template_mismatch_with_actionable_help_path(monkeypatch):
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "P")
    monkeypatch.setattr(
        tp,
        "validate_design_document_structure",
        lambda *_args: {
            "valid": False,
            "missing": ["2. Resources and Endpoints"],
            "out_of_order": [],
        },
    )
    response = inbox_routes._design_template_submission_error(
        project="flowgate", doc_type="P", locale="en", content="# improvised"
    )
    payload = _response_json(response)
    assert response.status_code == 422
    assert "2. Resources and Endpoints" in payload["error_message"]
    assert payload["help_url"] == "/help/items/design_template/P"


def test_inbox_accepts_matching_design_and_ignores_non_design(monkeypatch):
    calls = []
    monkeypatch.setattr(tp, "is_design_type", lambda code: code == "D")
    monkeypatch.setattr(
        tp,
        "validate_design_document_structure",
        lambda *args: calls.append(args) or {"valid": True},
    )
    assert inbox_routes._design_template_submission_error(
        project="flowgate", doc_type="D", locale="ko", content="body"
    ) is None
    assert calls and calls[0][1] == "D"

    calls.clear()
    assert inbox_routes._design_template_submission_error(
        project="flowgate", doc_type="TR", locale="ko", content="body"
    ) is None
    assert calls == []


def test_inbox_fails_closed_when_active_template_cannot_be_checked(monkeypatch):
    monkeypatch.setattr(tp, "is_design_type", lambda code: True)

    def unavailable(*_args):
        raise RuntimeError("db down")

    monkeypatch.setattr(tp, "validate_design_document_structure", unavailable)
    response = inbox_routes._design_template_submission_error(
        project="flowgate", doc_type="DB", locale="ja", content="body"
    )
    payload = _response_json(response)
    assert response.status_code == 503
    assert payload["help_url"] == "/help/items/design_template/DB"