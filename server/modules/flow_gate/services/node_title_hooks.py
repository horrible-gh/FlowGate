"""Card title ports of the AI naming (flowgate.v02.0064 T-B4d, 0062 L0006 2.4 / 2.6, P0006 §4.2.5 / §4.2.6).

The Designer card title is owned by the Graph package; the inbox (a v0.1 route) and the AI node start meet it here.

    after_registration   ``(flow_id, doc_id, token_id, raw_candidate, action_scope)``: an inbox new / edit
                         registration committed and its token is consumed. ``raw_candidate`` is the request's
                         ``node_title`` exactly as sent (absent and null are both None); the handler judges it. The
                         composition root (``startup.py`` through ``flowgate.card_settings.integration``) registers
                         the handler. With none registered nothing happens.

* The hook never raises and never changes what the inbox answers, stores or consumes: a handler exception is logged.
* One handler per port. Registering again replaces it (startup may run more than once in a process).

The title request a claiming AI start puts at the very end of its prompt (P0006 §4.2.6) is worded here too: the
inbox body (``node_title`` of the POST /inbox request) for a CLI AI, the ``register_document`` input for an API AI.
The claiming start only records that its token asks (``request_title``); the wording is chosen at the provider
boundary (worker ``_provider_prompt`` -> ``provider_prompt``) by the ``exec_type`` of the provider actually launched,
as the transport rules are: the start's job often names no provider (the project default chain picks it), and a
fallback or retry with the same token may hand the prompt to an AI of the other kind. A token that never claimed gets
its prompt untouched. Both wordings, in every locale, tell the AI not to repeat the card kind name. The locale is the
mention's (ko when unknown).
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

AFTER_REGISTRATION = "after_registration"
HOOK_NAMES = (AFTER_REGISTRATION,)
# The separator between the mention and the title request (one empty line), as between the other mention sections.
SECTION_SEPARATOR = "\n\n"
TITLE_REQUEST_LOCALES = ("ko", "en", "ja")
# P0006 §4.2.6, the CLI wording: the AI sends the inbox request itself.
TITLE_REQUEST_CLI = {
    "ko": "이 카드는 아직 기본 제목입니다. 작업 내용을 한눈에 알 수 있는 제목을 20~30자로 짓되, 카드 종류 명칭(예: 조사, "
          "요구사항)을 그대로 반복하지 마세요. 그 제목을 문서를 제출하는 inbox 요청 본문의 `node_title` 항목에 넣어 "
          "주세요. 제목은 선택 사항이며 문서 제출에는 영향을 주지 않습니다.",
    "en": "This card still has its default title. Write a title of 20 to 30 characters that tells at a glance what "
          "this work is, without simply repeating the card kind name (for example \"Research\" or \"Requirement\"), "
          "and put it in the `node_title` field of the body of your inbox submission. The title is optional and does "
          "not affect the document submission.",
    "ja": "このカードはまだ既定のタイトルです。作業内容がひと目で分かるタイトルを20〜30文字で考えてください。カード種類の"
          "名称（例：調査、要件）をそのまま繰り返さないでください。そのタイトルを、文書を提出する inbox リクエスト本文の "
          "`node_title` 項目に入れてください。タイトルは任意で、文書の提出には影響しません。",
}
# P0006 §4.2.6, the API wording: the AI registers through the register_document tool.
TITLE_REQUEST_API = {
    "ko": "이 카드는 아직 기본 제목입니다. 작업 내용을 한눈에 알 수 있는 제목을 20~30자로 짓되, 카드 종류 명칭(예: 조사, "
          "요구사항)을 그대로 반복하지 마세요. 그 제목을 문서를 등록하는 `register_document` 도구 입력의 `node_title` "
          "항목에 넣어 주세요. 제목은 선택 사항이며 문서 등록에는 영향을 주지 않습니다.",
    "en": "This card still has its default title. Write a title of 20 to 30 characters that tells at a glance what "
          "this work is, without simply repeating the card kind name (for example \"Research\" or \"Requirement\"), "
          "and put it in the `node_title` input of your `register_document` tool call. The title is optional and does "
          "not affect the document registration.",
    "ja": "このカードはまだ既定のタイトルです。作業内容がひと目で分かるタイトルを20〜30文字で考えてください。カード種類の"
          "名称（例：調査、要件）をそのまま繰り返さないでください。そのタイトルを、文書を登録する `register_document` "
          "ツール入力の `node_title` 項目に入れてください。タイトルは任意で、文書の登録には影響しません。",
}

# How many claiming tokens are remembered (process memory; a run's worker uses its token right after the issue).
REQUESTS_KEPT = 256

_handlers: dict[str, Callable[..., Any]] = {}
_requests: "OrderedDict[str, Optional[str]]" = OrderedDict()      # claiming token digest -> the mention locale
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in HOOK_NAMES:
        raise ValueError(f"unknown card title hook: {name}")
    if not callable(handler):
        raise TypeError(f"card title hook {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Optional[Callable[..., Any]]:
    with _lock:
        return _handlers.get(name)


def after_registration(flow_id: Optional[str], doc_id: Optional[str], token_id: Optional[str], raw_candidate,
                       action_scope: Optional[str]) -> None:
    """The inbox registration of ``doc_id`` with ``token_id`` committed and the token is consumed. Never raises."""
    handler = _handler(AFTER_REGISTRATION)
    if handler is None or not flow_id or not token_id:
        return
    try:
        handler(flow_id, doc_id, token_id, raw_candidate, action_scope)
    except Exception:  # noqa: BLE001 — a card title never changes a registration
        logger.warning("card title hook failed flow=%s doc=%s token=%s (ignored)", flow_id, doc_id, token_id,
                       exc_info=True)


def title_request(exec_type: Optional[str], locale: Optional[str] = None) -> str:
    """The title request of P0006 §4.2.6 for a provider of ``exec_type`` (``api``: the tool input, else the inbox
    body) and the locale."""
    loc = str(locale or "").strip().lower()[:2]
    loc = loc if loc in TITLE_REQUEST_LOCALES else "ko"
    return (TITLE_REQUEST_API if str(exec_type or "").strip().lower() == "api" else TITLE_REQUEST_CLI)[loc]


def append_title_request(prompt: str, exec_type: Optional[str], locale: Optional[str] = None) -> str:
    """``prompt`` with the title request at its very end (one empty line before it). A failing wording leaves
    the prompt as it is: the AI starts all the same (0062 L0006 5)."""
    try:
        request = title_request(exec_type, locale)
    except Exception:  # noqa: BLE001
        logger.warning("card title request wording failed exec_type=%s (the prompt goes out without it)",
                       exec_type, exc_info=True)
        return prompt
    return prompt + SECTION_SEPARATOR + request if prompt else request


def _request_key(raw_token: Optional[str]) -> Optional[str]:
    # The raw token is random (token ids are numbered per day), kept here only as its digest.
    if not isinstance(raw_token, str) or not raw_token:
        return None
    return hashlib.sha256(raw_token.encode("utf-8", "surrogatepass")).hexdigest()


def request_title(raw_token: Optional[str], locale: Optional[str] = None) -> None:
    """The AI start that minted ``raw_token`` claimed its card's title opportunity: every prompt handed to a provider
    with that token ends with the title request (``provider_prompt``). Never raises."""
    key = _request_key(raw_token)
    if key is None:
        return
    with _lock:
        _requests[key] = locale
        _requests.move_to_end(key)
        while len(_requests) > REQUESTS_KEPT:
            _requests.popitem(last=False)


def title_requested(raw_token: Optional[str]) -> bool:
    key = _request_key(raw_token)
    with _lock:
        return key is not None and key in _requests


def provider_prompt(prompt: str, raw_token: Optional[str], exec_type: Optional[str]) -> str:
    """``prompt`` as handed to a provider of ``exec_type`` with ``raw_token``: the title request, worded for that
    provider, at its very end when the token claimed; else ``prompt`` itself. Never raises."""
    key = _request_key(raw_token)
    if key is None:
        return prompt
    with _lock:
        if key not in _requests:
            return prompt
        locale = _requests[key]
    return append_title_request(prompt, exec_type, locale)
