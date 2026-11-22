"""Submit blocks: the remote CLI execution contracts appended at the provider boundary.

Moved verbatim from ``ai_invoke.worker`` (``_REMOTE_CLI_TRANSPORT`` / ``_REMOTE_CLI_CHAT_TRANSPORT``)
and ``terminal_prompt.q_transport``. The worker keeps its old names as
aliases of these dictionaries; nothing here is served by help.
"""
from __future__ import annotations

from ..registry import Block, register


REMOTE_CLI_TRANSPORT: dict[str, str] = {
    "ko": (
        "## 원격 CLI 실행 규약\n---\n"
        "TR을 edit로 제출할 때도 commit_message를 영어(ASCII) 한 줄, 200자 이내로 넣으십시오. "
        "권장 형식은 type(scope): summary이며 유효한 저장 메시지가 있을 때만 생략할 수 있습니다.\n"
        "이 작업은 원격 Agent에서 실행됩니다. FLOWGATE_TOKEN·FLOWGATE_SCRATCH·FLOWGATE_API_BASE "
        "환경 변수는 없으므로 이 프롬프트에 적힌 토큰과 주소를 쓰십시오. `{SCRATCH}`와 scratch "
        "디렉터리는 현재 작업 디렉터리를 뜻합니다. 제출할 본문(리뷰 JSON 포함)은 현재 작업 "
        "디렉터리에 UTF-8 파일로 작성하고, 그 파일 내용을 POST /inbox 요청 본문의 content로 "
        "보내십시오(명령줄 문자열에 직접 넣지 않음). body_chars와 body_sha256을 함께 보내고 "
        "doc_path는 쓰지 마십시오. 이 규약은 위의 doc_path 제출 안내보다 우선합니다."
    ),
    "ja": (
        "## リモートCLI実行規約\n---\n"
        "TRのedit提出にもcommit_messageを英語(ASCII)1行、200文字以内で指定してください。 "
        "推奨形式はtype(scope): summaryです。有効な保存済み値がある場合のみ省略できます。\n"
        "この作業はリモートAgentで実行されます。FLOWGATE_TOKEN・FLOWGATE_SCRATCH・FLOWGATE_API_BASE "
        "環境変数はないため、このプロンプトに記載されたトークンとアドレスを使ってください。"
        "`{SCRATCH}`とscratchディレクトリは現在の作業ディレクトリを指します。提出する本文"
        "(レビューJSONを含む)は現在の作業ディレクトリにUTF-8ファイルとして作成し、そのファイルの"
        "内容をPOST /inboxリクエスト本文のcontentとして送ってください(コマンドライン文字列に直接"
        "入れない)。body_charsとbody_sha256も送り、doc_pathは使わないでください。この規約は上記の"
        "doc_path提出案内より優先されます。"
    ),
    "en": (
        "## Remote CLI execution contract\n---\n"
        "TR edit submissions also need commit_message: one English (ASCII) line, max 200 characters; "
        "recommended type(scope): summary. Omit only to retain a valid saved message.\n"
        "This work runs on a remote Agent. There are no FLOWGATE_TOKEN, FLOWGATE_SCRATCH or "
        "FLOWGATE_API_BASE variables; use the token and address written in this prompt. "
        "`{SCRATCH}` and the scratch directory mean the current working directory. Write every "
        "body you submit (review JSON included) as a UTF-8 file in the current working directory "
        "and send that file's content as `content` in the POST /inbox request body (never inline "
        "on a command line), together with body_chars and body_sha256; do not use doc_path. This "
        "contract overrides the doc_path submission guidance above."
    ),
}


REMOTE_CLI_CHAT_TRANSPORT: dict[str, str] = {
    "ko": (
        "## 원격 CLI 실행 규약\n---\n"
        "이 작업은 원격 Agent에서 실행됩니다. FLOWGATE_TOKEN·FLOWGATE_SCRATCH·FLOWGATE_API_BASE "
        "환경 변수는 없으므로 이 프롬프트에 적힌 토큰과 주소를 쓰십시오. `{SCRATCH}`와 scratch "
        "디렉터리는 현재 작업 디렉터리를 뜻합니다. 제출할 본문은 현재 작업 디렉터리에 UTF-8 "
        "파일로 작성하고, 위의 대화 턴 제출 안내에 따라 보내십시오(명령줄 문자열에 직접 넣지 않음)."
    ),
    "ja": (
        "## リモートCLI実行規約\n---\n"
        "この作業はリモートAgentで実行されます。FLOWGATE_TOKEN・FLOWGATE_SCRATCH・FLOWGATE_API_BASE "
        "環境変数はないため、このプロンプトに記載されたトークンとアドレスを使ってください。"
        "`{SCRATCH}`とscratchディレクトリは現在の作業ディレクトリを指します。提出する本文は"
        "現在の作業ディレクトリにUTF-8ファイルとして作成し、上記の会話ターン提出案内に従って"
        "送ってください(コマンドライン文字列に直接入れない)。"
    ),
    "en": (
        "## Remote CLI execution contract\n---\n"
        "This work runs on a remote Agent. There are no FLOWGATE_TOKEN, FLOWGATE_SCRATCH or "
        "FLOWGATE_API_BASE variables; use the token and address written in this prompt. "
        "`{SCRATCH}` and the scratch directory mean the current working directory. Write the "
        "body you submit as a UTF-8 file in the current working directory and submit it "
        "following the conversation turn instructions above (never inline on a command line)."
    ),
}


REMOTE_CLI_Q_ANSWER_TRANSPORT: dict[str, str] = {
    'ko': '## 원격 CLI Q 답변 규약\nFLOWGATE_TOKEN·FLOWGATE_SCRATCH·FLOWGATE_API_BASE 환경 변수는 없으므로 위 주소와 Bearer를 사용한다. 현재 작업 디렉터리의 UTF-8 파일로 요청 JSON을 준비한다. 위 answers 또는 허용된 escalate 엔드포인트에 보내고 성공 응답을 확인한다.',
    'en': '## Remote CLI Q answer transport\nFLOWGATE_TOKEN, FLOWGATE_SCRATCH and FLOWGATE_API_BASE are unavailable; use the URL and Bearer above. Prepare request JSON in a UTF-8 file in the current working directory. Send it to the answers or permitted escalate endpoint above and verify success.',
    'ja': '## リモートCLI Q回答規約\nFLOWGATE_TOKEN・FLOWGATE_SCRATCH・FLOWGATE_API_BASE環境変数はないため、上記URLとBearerを使う。現在の作業ディレクトリのUTF-8ファイルで要求JSONを用意する。上記answersまたは許可されたescalateエンドポイントに送信し、成功を確認する。',
}


def _transport(locale: str = "ko") -> str:
    return REMOTE_CLI_TRANSPORT[locale]


def _chat_transport(locale: str = "ko") -> str:
    return REMOTE_CLI_CHAT_TRANSPORT[locale]


def _q_answer_transport(locale: str = "ko") -> str:
    return REMOTE_CLI_Q_ANSWER_TRANSPORT[locale]


register(Block("submit.remote_cli", brief=_transport, detail=_transport))
register(Block("submit.remote_cli_chat", brief=_chat_transport, detail=_chat_transport))
register(Block("submit.remote_cli_q_answer", brief=_q_answer_transport, detail=_q_answer_transport))
