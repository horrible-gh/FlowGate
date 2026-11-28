"""Current chat mode, derived from the issued grant rather than saved preferences."""
from __future__ import annotations

from typing import Optional

from ..registry import Block, register


EXECUTE_MODE_NOTE = (
    "This chat is in execute mode: you may run commands, but you must not change source or "
    "documents. For commands in this repository, only read, git read and test/build commands run; a command that changes files "
    "(rm/mv/cp/mkdir, git add/commit/checkout/reset, package install/update, --fix/--write "
    "formatters, unknown programs) is refused with chat_command_execute_mode_denied. Source and "
    "document edit tools are not available."
)


def current_mode_section(
    *, source_access: Optional[str], command_access: Optional[str] = None,
    one_shot_claimed: bool = False, locale: str = "ko",
) -> str:
    """A claim flag can label an actual write grant; it can never upgrade a read grant."""
    if source_access == "read_write":
        mode = "edit_once" if one_shot_claimed else "edit"
    elif source_access == "read" and command_access == "execute":
        mode = "execute"
    else:
        mode = "read"
    if locale == "ko":
        title = "현재 모드"
        names = {"read": "읽기", "execute": "실행", "edit": "편집", "edit_once": "1회 편집"}
        rules = {
            "read": "소스·문서 조회가 가능합니다. 아래 허용 범위 외의 명령 실행은 불가합니다. 이 대화에 답 턴 1개를 올리는 것(POST …/turn)은 모든 모드에서 해야 할 일이며 문서 수정이 아닙니다. 조회·제출을 위한 FlowGate API 호출은 명령 실행에 들어가지 않습니다. 사용자가 대화에서 안내한 외부 테스트 서버·시안서버의 기동·종료·업로드는 이 모드에서도 할 수 있습니다. 이 레포의 소스·문서 수정은 하지 않습니다.",
            "execute": "소스·문서 조회와 허용된 명령 실행이 가능합니다. 소스·문서 수정은 불가합니다. 이 대화에 답 턴 1개를 올리는 것(POST …/turn)은 모든 모드에서 해야 할 일이며 문서 수정이 아닙니다. 조회·제출을 위한 FlowGate API 호출은 명령 실행에 들어가지 않습니다. 사용자가 대화에서 안내한 외부 테스트 서버·시안서버의 기동·종료·업로드는 이 모드에서도 할 수 있습니다. 이 레포의 소스·문서 수정은 하지 않습니다.",
            "edit": "소스·문서 조회, 명령 실행, 소스·문서 수정이 가능합니다.",
        }
        policy = "사용자가 고른 모드입니다. 모드를 바꾸라고 요구하지 말고 이 모드 안에서 끝내십시오. 더 높은 모드가 꼭 필요하면 한 줄로 한 번만 말하십시오."
        once = "이번 한 번만 편집 가능, 다음 대화부터 읽기."
    elif locale == "ja":
        title = "現在のモード"
        names = {"read": "読み取り", "execute": "実行", "edit": "編集", "edit_once": "一回編集"}
        rules = {
            "read": "ソース・文書を参照できます。以下の許可範囲外のコマンド実行はできません。 この会話への回答ターン1件の投稿（POST …/turn）はすべてのモードで必須であり、文書の変更ではありません。 参照・提出のためのFlowGate API呼び出しはコマンド実行に含まれません。 ユーザーが会話で案内した外部テストサーバー・プレビューサーバーの起動・停止・アップロードは、このモードでも可能です。このリポジトリのソース・文書は変更しません。",
            "execute": "ソース・文書の参照と許可されたコマンド実行が可能です。ソース・文書の変更はできません。 この会話への回答ターン1件の投稿（POST …/turn）はすべてのモードで必須であり、文書の変更ではありません。 参照・提出のためのFlowGate API呼び出しはコマンド実行に含まれません。 ユーザーが会話で案内した外部テストサーバー・プレビューサーバーの起動・停止・アップロードは、このモードでも可能です。このリポジトリのソース・文書は変更しません。",
            "edit": "ソース・文書の参照、コマンド実行、ソース・文書の変更が可能です。",
        }
        policy = "ユーザーが選んだモードです。モード変更を要求せず、このモード内で完了してください。上位モードが不可欠な場合のみ、一行で一度だけ伝えてください。"
        once = "今回だけ編集可能です。次の会話から読み取りモードになります。"
    else:
        title = "Current mode"
        names = {"read": "Read", "execute": "Execute", "edit": "Edit", "edit_once": "Edit once"}
        rules = {
            "read": "You may read source and documents. Command execution outside the permissions below is not allowed. Posting one reply turn to this conversation (POST …/turn) is required in every mode and is not a document edit. FlowGate API calls for reading and submission do not count as command execution. You may start, stop, and upload to external test or preview servers identified by the user in this conversation, even in this mode. Do not edit source or documents in this repository.",
            "execute": "You may read source/documents and run permitted commands. Source/document changes are not allowed. Posting one reply turn to this conversation (POST …/turn) is required in every mode and is not a document edit. FlowGate API calls for reading and submission do not count as command execution. You may start, stop, and upload to external test or preview servers identified by the user in this conversation, even in this mode. Do not edit source or documents in this repository.",
            "edit": "You may read source/documents, run commands, and edit source/documents.",
        }
        policy = "The user chose this mode. Do not ask them to change modes; finish within this mode. If a higher mode is indispensable, mention it only once in one line."
        once = "Editing is allowed for this invocation only; the next conversation returns to read mode."
    lines = [f"## {title}", "---", names[mode], rules["edit" if mode == "edit_once" else mode], policy]
    if mode == "edit_once":
        lines.append(once)
    if mode == "execute":
        lines.append(EXECUTE_MODE_NOTE)
    return "\n".join(lines)


register(Block("chat.current_mode", brief=current_mode_section, detail=current_mode_section))
