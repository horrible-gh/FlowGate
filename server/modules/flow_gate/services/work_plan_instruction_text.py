"""The instruction text a work plan step's pre-instruction makes (flowgate.v02.0074 T#4, D0006 §3.5).

One composition rule for the v0.1 next-approved materializer (``documents._work_plan_instruction_document`` /
``_work_plan_instruction_body``) and the v0.2 work plan deploy (``flowgate.work_plan_deploy``): the attached
instruction file comes first (its own front matter dropped), the written text follows it under the
"additional instruction" heading, and a body that does not open with a heading gets the document title as one.
"""
from __future__ import annotations

import re
from typing import Optional

EXTRA_HEADING = {
    "ko": "추가 지시",
    "ja": "追加指示",
    "en": "Additional instruction",
}
LEADING_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def instruction_document(text: Optional[str], attachment: Optional[dict],
                         attachment_text: Optional[str]) -> Optional[dict]:
    """``{text, attachment, attachment_markdown}`` of a pre-instruction, or None when it holds nothing."""
    document = {
        "text": (text or "").strip(),
        "attachment": dict(attachment) if isinstance(attachment, dict) else None,
        "attachment_markdown": "",
    }
    if attachment_text is not None:
        normalized = attachment_text.replace("\r\n", "\n").replace("\r", "\n")
        document["attachment_markdown"] = LEADING_FRONTMATTER_RE.sub("", normalized, count=1).strip()
    if not document["text"] and not document["attachment_markdown"]:
        return None
    return document


def instruction_body(title: str, document: dict, locale: str) -> str:
    """Compose the canonical Markdown: the instruction file first, then the written text."""
    from modules.flow_gate.template_provision import normalize_locale

    file_md = document.get("attachment_markdown") or ""
    text = document.get("text") or ""
    if file_md and text:
        heading = EXTRA_HEADING.get(normalize_locale(locale), EXTRA_HEADING["ko"])
        body = f"{file_md}\n\n## {heading}\n\n{text}"
    else:
        body = file_md or text
    if not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"
    return body.rstrip() + "\n"
