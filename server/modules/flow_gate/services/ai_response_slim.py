"""Slimmer AI-facing API responses.

What an AI reads on every call is pared down here; what a person's screen reads is not touched.
The rule is **take away, never rename**: a field that stays keeps its name and its meaning, and a
field that goes is either empty (null, "", [], {}), a copy of something the same response already
says, or a block that now lives one call deeper (``/help/tools/{name}``).

Who is "an AI" is decided in one place, ``is_ai_token``: a worker / chat token. A console user's
JWT (``_is_user_jwt``) is a person and always gets the full response, as does the session turn
reader and the SSE stream, which never pass through this module.

Nothing here reads a database or a request; the callers hand in plain dicts, so every rule is a
pure function and is tested as one (``test_ai_response_slim_0061.py``).
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

# ── who is an AI ─────────────────────────────────────────────────────────────

def is_ai_token(auth: Any) -> bool:
    """True for a worker / chat token record, False for a console user's JWT or anything unknown."""
    return isinstance(auth, dict) and not auth.get("_is_user_jwt")


def request_is_ai(request: Any) -> bool:
    """True when the route stored an AI token's record on ``request.state.fg_auth`` (see
    ``remember_auth``). A request that never stored one is treated as a person's: unknown means
    unchanged."""
    return is_ai_token(getattr(getattr(request, "state", None), "fg_auth", None))


def remember_auth(request: Any, auth: Any) -> None:
    """Keep the verified token record on the request so a route can tell an AI from a person after
    its shared prologue has already authenticated."""
    try:
        request.state.fg_auth = auth
    except Exception:  # noqa: BLE001 - a request without state simply stays "unknown"
        pass


# ── the generic rule: drop what is empty ─────────────────────────────────────

def _is_empty(value: Any) -> bool:
    """None, "", [], () and {} -- but never 0 or False, which are values."""
    return value is None or (isinstance(value, (str, list, tuple, dict)) and len(value) == 0)


def drop_empty(value: Any, keep: Iterable[str] = (), whole: Iterable[str] = ()) -> Any:
    """``value`` with every empty dict entry removed, recursively.

    ``keep`` names keys that stay even when empty because their emptiness is the answer ("no
    sections", "no turns in this page"). ``whole`` names keys whose value is copied untouched,
    inside included, because the AI sends that value back as it got it (``context`` ->
    ``expected_context``, a locator -> the next read); an empty ``whole`` value follows the
    ordinary rule. List items are slimmed inside but never removed, so a list keeps its length
    and positions.
    """
    keep_set = frozenset(keep)
    whole_set = frozenset(whole)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for key, item in node.items():
                slim = item if key in whole_set else walk(item)
                if _is_empty(slim) and key not in keep_set:
                    continue
                out[key] = slim
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(value)


def for_ai(request: Any, payload: Any, keep: Iterable[str] = (), whole: Iterable[str] = ()) -> Any:
    """``drop_empty(payload)`` when the request came from an AI token, ``payload`` itself otherwise."""
    return drop_empty(payload, keep, whole) if request_is_ai(request) else payload


# ── turn list ────────────────────────────────────────────────────────────────

# Top level: the reader's own participant rows and read boundary (the server already keeps them),
# and the page size it asked for.
TURN_PAGE_DROP = ("participants", "me", "limit")
# head: the same document id and head_seq the page itself carries one level up.
TURN_HEAD_DROP = ("doc_id", "head_seq")
# One turn: who-is-who bookkeeping an AI does not read (speaker + display_name say who spoke,
# edited_at / stale_since_seq say it changed, created_at says when).
TURN_DROP = ("participant_key", "locale", "message_version", "based_on_seq")
# A page with no turns, and a turn with no text, are answers, not noise.
TURN_PAGE_KEEP = ("turns", "body")


def slim_turn_page(page: dict) -> dict:
    """The turn page an AI reads (``GET /conversation/{doc_id}/turns``).

    The page is cut, never rewritten: paging (``next_after_seq`` / ``has_more`` / ``truncated_by``),
    ``head_seq`` and every turn's ``seq`` / ``speaker`` / ``display_name`` / ``body`` /
    ``created_at`` keep their names and values.
    """
    out = {key: value for key, value in page.items() if key not in TURN_PAGE_DROP}
    head = out.get("head")
    if isinstance(head, dict):
        out["head"] = {key: value for key, value in head.items() if key not in TURN_HEAD_DROP}
    turns = out.get("turns")
    if isinstance(turns, list):
        out["turns"] = [
            {key: value for key, value in turn.items() if key not in TURN_DROP}
            if isinstance(turn, dict) else turn
            for turn in turns
        ]
    return drop_empty(out, TURN_PAGE_KEEP)


# ── /help/tools summary ──────────────────────────────────────────────────────

# One short line per tool for the summary list. The full sentence stays in ``/help/tools/{name}``.
TOOL_BRIEF = {
    "ko": {
        "read": "파일 읽기", "grep": "정규식 검색", "glob": "파일 경로 찾기", "stat": "경로 정보 조회",
        "diff": "ref 변경 patch 조회", "log": "ref 커밋 목록", "show": "커밋 하나 조회",
        "merge_preview": "병합 충돌 미리보기", "write": "파일 쓰기", "patch": "파일 일부 바꾸기",
        "move": "파일 이동·이름변경", "remove": "파일·빈 폴더 삭제",
    },
    "ja": {
        "read": "ファイルを読む", "grep": "正規表現で検索", "glob": "globでパス検索", "stat": "パス情報",
        "diff": "ref差分patch", "log": "refコミット一覧", "show": "コミット詳細",
        "merge_preview": "マージ競合の事前確認", "write": "ファイルを書く", "patch": "ファイルの一部を置換",
        "move": "ファイル移動・改名", "remove": "ファイル・空ディレクトリ削除",
    },
    "en": {
        "read": "Read a file", "grep": "Regex search", "glob": "Glob paths", "stat": "Path info",
        "diff": "Ref patch", "log": "Ref commits", "show": "Show a commit",
        "merge_preview": "Preview merge", "write": "Write a file", "patch": "Replace text",
        "move": "Move or rename", "remove": "Delete file or dir",
    },
}
_BRIEF_FALLBACK_CHARS = 40


def _brief(registry_entry: dict, locale: str) -> str:
    """The list line of one tool: the table above, else the first clause of its full summary."""
    name = registry_entry.get("name")
    line = TOOL_BRIEF.get(locale, {}).get(name)
    if line:
        return line
    full = str(registry_entry.get("summary") or name or "")
    for stop in ("。", ". ", "."):
        cut = full.find(stop)
        if 0 < cut < _BRIEF_FALLBACK_CHARS:
            return full[:cut]
    return full[:_BRIEF_FALLBACK_CHARS]


def tools_summary(registry: dict, locale: str, base_url: str) -> dict:
    """``GET /help/tools`` for an AI: name, one line and path per tool, nothing else.

    The attachment block, the denied / absent operations, the notes and the examples are one call
    deeper: ``/help/tools/{name}`` for an operation's request contract, ``/help/items/attachments/read``
    for what each kind may do with attachments. Only the *names* of the attachment operations this
    token can call stay here, so the list still answers "which file operations do I have?".
    """
    from modules.flow_gate.services import tool_registry

    out: dict = {"ok": True, "kind": registry["kind"]}
    if registry.get("reason"):
        out["reason"] = registry["reason"]
    out["detail_url"] = f"{base_url}/help/tools/{{name}}"
    out["tools"] = [
        {"name": tool["name"], "path": tool["path"], "summary": _brief(tool, locale)}
        for tool in registry["tools"]
    ]
    attachments = tool_registry.attachment_names(registry["kind"])
    if attachments:
        out["attachments"] = attachments
    return out


# ── document reads (meta / outline / section) ────────────────────────────────

# An outline with no headings answers ``items: []``; a section's text is the answer even when blank.
OUTLINE_KEEP = ("items",)
# Outline items and the section locators chain one read into the next ("outline -> section ->
# search"): they are copied exactly as the service built them.
OUTLINE_WHOLE = ("items",)
SECTION_KEEP = ("text",)
SECTION_WHOLE = ("locator", "next_locator")


# ── lists that belong to one route family (chat control, card questions) ─────

# ai-options: an empty list means "no AI to pick" / "no AI group" -- the answer, not noise.
AI_OPTIONS_KEEP = ("options", "role_groups", "type_keys")
# card roles: ``selected: null`` is "nothing chosen", and an empty options list is "none selectable".
CARD_ROLES_KEEP = ("roles", "options", "applicable_roles", "applicable_settings", "settings",
                   "selected", "value", "default")
# ``context`` goes back as ``expected_context`` of the change and ``selected`` is a role value in the
# change's own grammar: both are handed back exactly as read.
CARD_ROLES_WHOLE = ("context", "selected")
# card questions: a document listed with no question shown is still listed.
CARD_QUESTIONS_KEEP = ("questions", "documents")


def slim_ai_options(answer: dict) -> dict:
    return drop_empty(answer, AI_OPTIONS_KEEP)


def slim_card_roles(answer: dict) -> dict:
    return drop_empty(answer, CARD_ROLES_KEEP, CARD_ROLES_WHOLE)


def slim_question_view(view: dict, keep: Optional[Iterable[str]] = None) -> dict:
    return drop_empty(view, CARD_QUESTIONS_KEEP if keep is None else keep)
