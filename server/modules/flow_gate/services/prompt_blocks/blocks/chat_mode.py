"""Current chat mode, derived from the issued grant rather than saved preferences."""
from __future__ import annotations

from typing import Optional

from ..registry import Block, register


EXECUTE_MODE_NOTE = (
    "This chat is in execute mode: you may run commands, but you must not change source or "
    "documents. Only read, git read and test/build commands run; a command that changes files "
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
        names = {"read": "읽기", "execute": "실행", "edit": "편집", "edit_once": "편1"}
        rules = {
            "read": "소스·문서 조회만 가능합니다. 명령 실행 및 소스·문서 수정은 불가합니다.",
            "execute": "소스·문서 조회와 허용된 명령 실행이 가능합니다. 소스·문서 수정은 불가합니다.",
            "edit": "소스·문서 조회, 명령 실행, 소스·문서 수정이 가능합니다.",
        }
        policy = "사용자가 고른 모드입니다. 모드를 바꾸라고 요구하지 말고 이 모드 안에서 끝내십시오. 더 높은 모드가 꼭 필요하면 한 줄로 한 번만 말하십시오."
        once = "이번 한 번만 편집 가능, 다음 대화부터 읽기."
    elif locale == "ja":
        title = "現在のモード"
        names = {"read": "読み取り", "execute": "実行", "edit": "編集", "edit_once": "一回編集"}
        rules = {
            "read": "ソース・文書の参照のみ可能です。コマンド実行とソース・文書の変更はできません。",
            "execute": "ソース・文書の参照と許可されたコマンド実行が可能です。ソース・文書の変更はできません。",
            "edit": "ソース・文書の参照、コマンド実行、ソース・文書の変更が可能です。",
        }
        policy = "ユーザーが選んだモードです。モード変更を要求せず、このモード内で完了してください。上位モードが不可欠な場合のみ、一行で一度だけ伝えてください。"
        once = "今回だけ編集可能です。次の会話から読み取りモードになります。"
    else:
        title = "Current mode"
        names = {"read": "Read", "execute": "Execute", "edit": "Edit", "edit_once": "Edit once"}
        rules = {
            "read": "You may only read source and documents. Commands and source/document changes are not allowed.",
            "execute": "You may read source/documents and run permitted commands. Source/document changes are not allowed.",
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
