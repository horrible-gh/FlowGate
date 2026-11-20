"""One encoding gate for the texts an AI worker token writes as JSON values.

flowgate.v02test.0051: chat turns, documents, inbox submissions and step labels each
grew their own corruption check, one path at a time, and question / answer
registration never got one. A local CLI on a Windows console that puts Korean on the
command line posts ``?? ????``; it was stored as a question and as an answer, and the
project-wide answer reuse then handed that answer to another document.

Every route a worker token can write free text through calls ``check`` (the write-path
inventory test fails on a route that does not). The verdict is the one chat turns use:

1. A field that ends with this token's encoding probe (``encoding_probe``) is proven:
   the line is removed and nothing else is checked. A damaged probe always rejects.
2. Otherwise U+FFFD, reversible mojibake and the ascii-replace shape (a run of ``?``)
   reject. There is no force reason here: the probe is the way to send real ``????``.

A login session (browser, UTF-8 end to end) is not checked; callers pass no token.
"""
from __future__ import annotations

from typing import Iterable, Optional

from modules.flow_gate.services import encoding_probe

SIGNAL_PROBE_BROKEN = "probe_broken"
SIGNAL_QUESTION_MARKS = "question_marks"

_COPY = {
    "ko": (
        "{field} 값이 깨진 글자(예: ??????)로 보입니다. 아무것도 저장하지 않았습니다. "
        "ASCII가 아닌 글자를 명령줄 인자·셸 파이프·리다이렉트로 보내면 이렇게 깨집니다. "
        "요청 JSON을 UTF-8 파일로 쓰고, 그 파일을 읽어 ASCII 전용 JSON(예: Python "
        "json.dumps 기본값)으로 보내는 스크립트로 다시 보내세요. 물음표가 실제 내용이면 "
        "인코딩 탐침 줄을 그 값의 마지막 줄에 넣어 경로를 증명하세요."
    ),
    "en": (
        "The {field} value looks like corrupted text (e.g. ??????). Nothing was stored. "
        "Non-ASCII text sent through a command-line argument, shell pipe or redirect "
        "arrives like this. Write the request JSON to a UTF-8 file and resend it with a "
        "script that reads the file and posts ASCII-only JSON (e.g. Python json.dumps "
        "defaults). If the question marks are real content, prove the path by putting "
        "the encoding probe line on the last line of that value."
    ),
    "ja": (
        "{field} の値が文字化け（例: ??????）しているようです。何も保存していません。"
        "ASCII以外の文字をコマンドライン引数・シェルのパイプ・リダイレクトで送るとこのように"
        "壊れます。リクエストJSONをUTF-8ファイルとして書き出し、そのファイルを読んでASCIIのみの"
        "JSON（例: Python json.dumps の既定値）で送るスクリプトで送り直してください。疑問符が"
        "実際の内容であれば、エンコーディング検査行をその値の最終行に入れて経路を証明してください。"
    ),
}

# The rule a worker is told before it writes (mention text, help items).
TRANSPORT_RULE = {
    "ko": (
        "전송 규칙: 요청 JSON을 UTF-8 파일로 쓰고, 그 파일을 읽어 ASCII 전용 JSON으로 보내는 "
        "스크립트(예: Python json.dumps 기본값)로 보내십시오. 한글 등 ASCII가 아닌 글자를 "
        "명령줄 인자·셸 파이프·리다이렉트·Set-Content·Out-File에 넣지 마십시오. 깨진 글자"
        "(?? 연속, U+FFFD)는 HTTP 422로 거절되고 저장되지 않습니다."
    ),
    "en": (
        "Transport rule: write the request JSON to a UTF-8 file and send it with a script "
        "that reads the file and posts ASCII-only JSON (e.g. Python json.dumps defaults). "
        "Never put non-ASCII text in a command-line argument, shell pipe, redirect, "
        "Set-Content or Out-File. Corrupted text (runs of '?', U+FFFD) is refused with "
        "HTTP 422 and nothing is stored."
    ),
    "ja": (
        "送信ルール: リクエストJSONをUTF-8ファイルとして書き出し、そのファイルを読んでASCIIのみの"
        "JSON（例: Python json.dumps の既定値）で送るスクリプトで送信してください。ASCII以外の文字を"
        "コマンドライン引数・シェルのパイプ・リダイレクト・Set-Content・Out-File に入れないでください。"
        "文字化け（? の連続、U+FFFD）は HTTP 422 で拒否され、保存されません。"
    ),
}


def _locale(locale: Optional[str]) -> str:
    code = (locale or "ko").strip().lower()[:2]
    return code if code in _COPY else "ko"


def transport_rule(locale: Optional[str] = "ko") -> str:
    return TRANSPORT_RULE[_locale(locale)]


def corruption_signal(text: Optional[str]) -> Optional[str]:
    """Why ``text`` reads as damaged in transit, or None. Also used on stored rows."""
    if not text:
        return None
    hard = encoding_probe.hard_corruption_signal(text)
    if hard is not None:
        return hard
    from modules.flow_gate.services import workflow_decision_service

    if workflow_decision_service._text_is_corrupted(text):
        return SIGNAL_QUESTION_MARKS
    return None


def check(
    token_rec: Optional[dict],
    fields: dict,
    probe_fields: Iterable[str] = (),
    locale: Optional[str] = "ko",
) -> tuple[Optional[dict], dict]:
    """Verdict for one worker request: ``(violation, cleaned_fields)``.

    ``fields`` maps a field name (as the caller's JSON names it) to its text; None and
    non-string values are passed through unchecked. ``probe_fields`` are the multi-line
    values that may end with the probe line; in ``cleaned_fields`` they come back
    without it. ``violation`` is None or ``{"code", "field", "signal", "message"}``.
    """
    cleaned = dict(fields)
    if not token_rec:
        return None, cleaned
    loc = _locale(locale)
    expected = encoding_probe.probe_for(str(token_rec.get("token_id") or ""))
    probe_names = set(probe_fields)
    proven = set()
    for name in fields:
        value = fields[name]
        if name not in probe_names or not isinstance(value, str):
            continue
        text, status, received = encoding_probe.split_probe(value, expected)
        if status == encoding_probe.PROBE_BROKEN:
            return _violation(name, SIGNAL_PROBE_BROKEN, loc, received), cleaned
        if status == encoding_probe.PROBE_MATCHED:
            cleaned[name] = text
            proven.add(name)
    for name, value in cleaned.items():
        if name in proven or not isinstance(value, str):
            continue
        signal = corruption_signal(value)
        if signal is not None:
            return _violation(name, signal, loc), cleaned
    return None, cleaned


def _violation(field: str, signal: str, locale: str, received: Optional[str] = None) -> dict:
    if signal == SIGNAL_QUESTION_MARKS:
        message = _COPY[locale].format(field=field)
    else:
        from modules.flow_gate.services import conversation_turn_service

        kwargs = {"received": encoding_probe.describe_received(received)} if signal == SIGNAL_PROBE_BROKEN else {}
        message = f"{field}: " + conversation_turn_service._encoding_violation_string(signal, locale, **kwargs)
    return {"code": "encoding_corrupted", "field": field, "signal": signal, "message": message}
