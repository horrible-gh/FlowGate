"""Locale-aware catalog and availability policy for remote source help."""
from __future__ import annotations

from copy import deepcopy
from typing import Optional

from modules.flow_gate.services import remote_tool_service
from modules.flow_gate.settings import source_mode_service

VERSION = "v1"
DISPLAY_ORDER = ("read", "grep", "glob", "write", "remove")
READ_TOOLS = frozenset({"read", "grep", "glob"})
WRITE_TOOLS = frozenset({"write", "remove"})
MUTATING_STEP_TYPES = frozenset({"T", "TR", "TSR"})

if set(DISPLAY_ORDER) != set(remote_tool_service.OPS):
    raise RuntimeError("remote tool catalog does not match remote_tool_service.OPS")

SUMMARY = {
    "ko": {
        "read": "원격 프로젝트 소스의 파일 하나를 읽는다.",
        "grep": "소스 트리에서 정규식으로 텍스트를 검색한다.",
        "glob": "글롭 패턴에 맞는 파일 경로 목록을 얻는다.",
        "write": "파일을 새로 만들거나 내용을 통째로 바꾼다(부분 패치 없음).",
        "remove": "파일 하나를 삭제한다.",
    },
    "ja": {
        "read": "リモートプロジェクトソースのファイルを1つ読み取る。",
        "grep": "ソースツリーを正規表現で検索する。",
        "glob": "globパターンに一致するファイルパス一覧を取得する。",
        "write": "ファイルを新規作成、または内容を丸ごと置き換える(部分パッチ不可)。",
        "remove": "ファイルを1つ削除する。",
    },
    "en": {
        "read": "Read a single file from the remote project source tree.",
        "grep": "Search the source tree with a regular expression.",
        "glob": "List file paths matching a glob pattern.",
        "write": "Create a file or replace its entire content (no partial patch).",
        "remove": "Delete a single file.",
    },
}

NOTES = {
    "ko": {
        "path_rule": "모든 경로는 프로젝트 소스 루트 기준 상대경로입니다. 절대경로나 '..' 세그먼트를 보내지 마세요.",
        "auth_rule": "요청 헤더는 멘트에서 받은 것과 같은 Authorization: Bearer <작업 토큰> 을 씁니다.",
        "no_disk_edit": "디스크의 프로젝트 소스를 직접 편집하지 마세요. 소스의 조회와 변경은 모두 이 도구를 거칩니다.",
        "read_only": "이 단계는 조사 전용입니다. write / remove 를 호출하지 마세요.",
        "report_changes": "write/remove가 성공하면 변경한 소스 파일을 작업 레포트에 요약해 남기세요.",
        "see_detail": "사용법 상세는 GET /flowgate/api/v1/help/tools/{name} 으로 도구별로 확인하세요.",
        "none_scope": "이 작업 단계에는 원격 소스 도구가 배정되지 않았습니다. 소스 트리에 접근하지 말고 배정된 문서 작업만 수행하세요.",
        "none_local": "이 프로젝트는 원격 소스 접근을 사용하지 않습니다. 소스는 작업 환경에서 직접 다루고, 이 도구는 호출하지 마세요.",
        "none_user": "이 인증 주체에는 원격 소스 도구가 배정되지 않았습니다. 도구는 작업 토큰에만 배정됩니다.",
    },
    "ja": {
        "path_rule": "すべてのパスはプロジェクトソースルートからの相対パスです。絶対パスや '..' セグメントを送らないでください。",
        "auth_rule": "リクエストヘッダーには、メントで受け取ったものと同じ Authorization: Bearer <作業トークン> を使います。",
        "no_disk_edit": "ディスク上のプロジェクトソースを直接編集しないでください。ソースの参照と変更はすべてこのツールを通します。",
        "read_only": "この段階は調査専用です。write / remove を呼ばないでください。",
        "report_changes": "write / remove が成功したら、変更したソースファイルを作業レポートに要約して残してください。",
        "see_detail": "使い方の詳細は GET /flowgate/api/v1/help/tools/{name} でツールごとに確認してください。",
        "none_scope": "この作業段階にはリモートソースツールが割り当てられていません。ソースツリーに触れず、割り当てられた文書作業のみ行ってください。",
        "none_local": "このプロジェクトはリモートソースアクセスを使用しません。ソースは作業環境で直接扱い、このツールは呼び出さないでください。",
        "none_user": "この認証主体にはリモートソースツールが割り当てられていません。ツールは作業トークンにのみ割り当てられます。",
    },
    "en": {
        "path_rule": "All paths are project-source-root relative; do not send absolute paths or '..' segments.",
        "auth_rule": "Use the same Authorization: Bearer <work token> you received in the ment.",
        "no_disk_edit": "Do not edit the project source on disk directly — every source read and change goes through these tools.",
        "read_only": "This step is investigation-only for source access. Do not call write or remove.",
        "report_changes": "After write or remove succeeds, summarize the changed source files in the task report.",
        "see_detail": "For usage detail, call GET /flowgate/api/v1/help/tools/{name} per tool.",
        "none_scope": "No remote source tool is assigned to this step. Do not touch the project source tree; carry out only the document work you were given.",
        "none_local": "This project does not use remote source access. Work with the source in your own environment and do not call these tools.",
        "none_user": "No remote source tool is assigned to this identity. Tools are assigned to work tokens only.",
    },
}

FIELDS = {
    "read": {
        "ko": [("path", "string", True, None, "소스 루트 기준 상대 경로. 절대경로와 '..' 금지."), ("max_bytes", "integer", False, None, "읽을 최대 바이트. 생략하면 파일 전체를 읽되 서버 상한을 넘으면 413."), ("encoding", "string", False, "utf-8", "디코딩 인코딩. 디코딩 실패 문자는 대체 문자로 바뀐다.")],
        "ja": [("path", "string", True, None, "ソースルートからの相対パス。絶対パスと '..' は禁止。"), ("max_bytes", "integer", False, None, "読み取る最大バイト数。省略時はファイル全体を読み取るが、サーバー上限を超えると413。"), ("encoding", "string", False, "utf-8", "デコード用エンコーディング。デコードできない文字は置換文字に変わる。")],
        "en": [("path", "string", True, None, "Path relative to the source root. Absolute paths and '..' are forbidden."), ("max_bytes", "integer", False, None, "Maximum bytes to read. If omitted, the whole file is read unless it exceeds the server limit, which returns 413."), ("encoding", "string", False, "utf-8", "Decoding charset. Undecodable characters are replaced.")],
    },
    "grep": {
        "ko": [("pattern", "string", True, None, "찾을 정규식(파이썬 re 문법)."), ("path", "string", False, "", "검색을 시작할 디렉터리. 비우면 소스 루트 전체."), ("glob", "string", False, None, "파일 필터. 예: **/*.py"), ("ignore_case", "boolean", False, False, "대소문자 무시."), ("max_results", "integer", False, None, "돌려줄 최대 매치 수. 채워지면 그 파일까지만 훑고 멈춘다.")],
        "ja": [("pattern", "string", True, None, "検索する正規表現(Python re構文)。"), ("path", "string", False, "", "検索を開始するディレクトリ。空の場合はソースルート全体。"), ("glob", "string", False, None, "ファイルフィルター。例: **/*.py"), ("ignore_case", "boolean", False, False, "大文字と小文字を区別しない。"), ("max_results", "integer", False, None, "返す最大マッチ数。到達するとそのファイルまで走査して停止する。")],
        "en": [("pattern", "string", True, None, "Regular expression to find (Python re syntax)."), ("path", "string", False, "", "Directory where the search starts. Empty means the entire source root."), ("glob", "string", False, None, "File filter. Example: **/*.py"), ("ignore_case", "boolean", False, False, "Ignore letter case."), ("max_results", "integer", False, None, "Maximum matches to return. Once filled, scanning stops after that file.")],
    },
    "glob": {
        "ko": [("pattern", "string", True, None, "파일 경로 패턴. 예: **/*.py"), ("path", "string", False, "", "패턴을 전개할 기준 디렉터리. 비우면 소스 루트.")],
        "ja": [("pattern", "string", True, None, "ファイルパスパターン。例: **/*.py"), ("path", "string", False, "", "パターンを展開する基準ディレクトリ。空の場合はソースルート。")],
        "en": [("pattern", "string", True, None, "File path pattern. Example: **/*.py"), ("path", "string", False, "", "Base directory for expanding the pattern. Empty means the source root.")],
    },
    "write": {
        "ko": [("path", "string", True, None, "소스 루트 기준 상대 경로. 상위 디렉터리는 필요하면 자동 생성된다."), ("content", "string", True, None, "파일 전체 내용. 부분 패치가 아니라 통째로 쓴다."), ("mode", "string", False, "overwrite", "create=새 파일만(있으면 409) / overwrite=통째 교체 / append=끝에 덧붙임."), ("encoding", "string", False, "utf-8", "인코딩. 이 인코딩으로 표현할 수 없는 문자가 있으면 422.")],
        "ja": [("path", "string", True, None, "ソースルートからの相対パス。親ディレクトリは必要に応じて自動作成される。"), ("content", "string", True, None, "ファイルの全内容。部分パッチではなく全体を書き込む。"), ("mode", "string", False, "overwrite", "create=新規ファイルのみ(存在時409) / overwrite=全体置換 / append=末尾に追記。"), ("encoding", "string", False, "utf-8", "エンコーディング。表現できない文字がある場合は422。")],
        "en": [("path", "string", True, None, "Path relative to the source root. Parent directories are created as needed."), ("content", "string", True, None, "Complete file content. The whole file is written; this is not a partial patch."), ("mode", "string", False, "overwrite", "create=new files only (409 if present) / overwrite=replace all / append=add at end."), ("encoding", "string", False, "utf-8", "Encoding. Characters that cannot be represented in it return 422.")],
    },
    "remove": {
        "ko": [("path", "string", True, None, "삭제할 파일의 소스 루트 기준 상대 경로.")],
        "ja": [("path", "string", True, None, "削除するファイルのソースルートからの相対パス。")],
        "en": [("path", "string", True, None, "Path of the file to delete, relative to the source root.")],
    },
}

ERRORS = {
    "read": {
        "ko": [(404, "not_found", "경로가 없거나 일반 파일이 아니다."), (413, "too_large", "max_bytes 를 생략했는데 파일이 서버 상한을 넘는다."), (422, "invalid_request", "경로가 소스 루트를 벗어나거나 형식이 잘못됐다."), (403, "forbidden", "이 토큰에 read 스코프가 없다.")],
        "ja": [(404, "not_found", "パスが存在しないか通常ファイルではない。"), (413, "too_large", "max_bytes を省略し、ファイルがサーバー上限を超えている。"), (422, "invalid_request", "パスがソースルート外か形式が正しくない。"), (403, "forbidden", "このトークンにreadスコープがない。")],
        "en": [(404, "not_found", "The path does not exist or is not a regular file."), (413, "too_large", "max_bytes was omitted and the file exceeds the server limit."), (422, "invalid_request", "The path escapes the source root or has an invalid form."), (403, "forbidden", "This token does not have the read scope.")],
    },
    "grep": {
        "ko": [(422, "invalid_request", "정규식이 잘못됐거나 path 가 소스 루트를 벗어난다."), (403, "forbidden", "이 토큰에 grep 스코프가 없다.")],
        "ja": [(422, "invalid_request", "正規表現が不正か、pathがソースルート外である。"), (403, "forbidden", "このトークンにgrepスコープがない。")],
        "en": [(422, "invalid_request", "The regular expression is invalid or path escapes the source root."), (403, "forbidden", "This token does not have the grep scope.")],
    },
    "glob": {
        "ko": [(422, "invalid_request", "path 가 소스 루트를 벗어난다."), (403, "forbidden", "이 토큰에 grep 스코프가 없다(glob 은 grep 스코프를 공유한다).")],
        "ja": [(422, "invalid_request", "pathがソースルート外である。"), (403, "forbidden", "このトークンにgrepスコープがない(globはgrepスコープを共有する)。")],
        "en": [(422, "invalid_request", "path escapes the source root."), (403, "forbidden", "This token does not have the grep scope (glob shares the grep scope).")],
    },
    "write": {
        "ko": [(409, "conflict", "mode=create 인데 파일이 이미 있다."), (409, "conflict", "그룹 작업 공간을 쓸 수 없다(다른 그룹의 병합 세션이 열려 있거나 워크트리 준비 실패). error.details.cause 에 이유가 실린다."), (413, "too_large", "content 가 서버 상한을 넘는다."), (422, "invalid_request", "경로가 소스 루트를 벗어나거나, content 를 요청 encoding 으로 표현할 수 없다."), (403, "forbidden", "이 토큰에 write 스코프가 없다(조사 전용 단계).")],
        "ja": [(409, "conflict", "mode=createでファイルがすでに存在する。"), (409, "conflict", "グループ作業領域を使用できない(別グループのマージセッションが開いているか、ワークツリー準備に失敗)。理由はerror.details.causeに入る。"), (413, "too_large", "contentがサーバー上限を超える。"), (422, "invalid_request", "パスがソースルート外か、contentを指定encodingで表現できない。"), (403, "forbidden", "このトークンにwriteスコープがない(調査専用段階)。")],
        "en": [(409, "conflict", "mode=create was requested but the file already exists."), (409, "conflict", "The group workspace is unavailable (another group's merge session is open or worktree preparation failed). The reason is in error.details.cause."), (413, "too_large", "content exceeds the server limit."), (422, "invalid_request", "The path escapes the source root or content cannot be represented in the requested encoding."), (403, "forbidden", "This token does not have the write scope (investigation-only step).")],
    },
    "remove": {
        "ko": [(404, "not_found", "경로가 없거나 일반 파일이 아니다."), (409, "conflict", "그룹 작업 공간을 쓸 수 없다."), (422, "invalid_request", "경로가 소스 루트를 벗어난다."), (403, "forbidden", "이 토큰에 remove 스코프가 없다.")],
        "ja": [(404, "not_found", "パスが存在しないか通常ファイルではない。"), (409, "conflict", "グループ作業領域を使用できない。"), (422, "invalid_request", "パスがソースルート外である。"), (403, "forbidden", "このトークンにremoveスコープがない。")],
        "en": [(404, "not_found", "The path does not exist or is not a regular file."), (409, "conflict", "The group workspace is unavailable."), (422, "invalid_request", "The path escapes the source root."), (403, "forbidden", "This token does not have the remove scope.")],
    },
}

CAUTIONS = {
    "read": {
        "ko": ["offset 파라미터는 없다. 큰 파일은 max_bytes 로 잘라 읽고, truncated=true 면 잘렸다는 뜻이다.", "size 는 잘라 읽었을 때도 파일 전체 크기다."],
        "ja": ["offsetパラメータはない。大きなファイルはmax_bytesで切って読み、truncated=trueは切り詰められたことを示す。", "sizeは切って読んだ場合もファイル全体のサイズである。"],
        "en": ["There is no offset parameter. Use max_bytes to trim large files; truncated=true means the result was trimmed.", "size is the complete file size even when the read was trimmed."],
    },
    "grep": {
        "ko": ["total 은 truncated=false 일 때만 정확한 개수이고, true 면 훑다 멈춘 시점까지의 하한값이다.", ".venv / venv / node_modules / .git / __pycache__ 는 순회에서 제외된다. 그 안을 보려면 path 로 직접 가리켜야 한다.", "너무 큰 파일과 바이너리 파일은 건너뛴다."],
        "ja": ["totalはtruncated=falseの場合のみ正確な件数で、trueの場合は走査を止めた時点までの下限値である。", ".venv / venv / node_modules / .git / __pycache__ は走査対象外。中を見るにはpathで直接指定する。", "大きすぎるファイルとバイナリファイルはスキップする。"],
        "en": ["total is exact only when truncated=false; when true, it is a lower bound at the point scanning stopped.", ".venv / venv / node_modules / .git / __pycache__ are excluded from traversal. Point path directly into one to inspect it.", "Very large and binary files are skipped."],
    },
    "glob": {
        "ko": ["결과에 truncated 필드가 없다. 매치한 경로를 전부 돌려준다.", ".venv / venv / node_modules / .git / __pycache__ 는 결과에서 빠진다.", "돌려주는 경로는 소스 루트 기준 상대경로이며 그대로 read 의 path 에 넣을 수 있다."],
        "ja": ["結果にtruncatedフィールドはない。一致したパスをすべて返す。", ".venv / venv / node_modules / .git / __pycache__ は結果から除外される。", "返されるパスはソースルートからの相対パスで、そのままreadのpathに使える。"],
        "en": ["The result has no truncated field; every matching path is returned.", ".venv / venv / node_modules / .git / __pycache__ are omitted from results.", "Returned paths are relative to the source root and can be used directly as read.path."],
    },
    "write": {
        "ko": ["부분 수정 기능이 없다. 먼저 read 로 전체를 받아 로컬에서 고친 뒤 전체를 다시 쓴다.", "성공 응답의 continuation.ment 는 다음 행동 지시다. 변경한 파일을 작업 레포트에 요약해 남긴다."],
        "ja": ["部分変更機能はない。まずreadで全体を取得し、ローカルで修正してから全体を書き戻す。", "成功応答のcontinuation.mentは次の行動指示である。変更したファイルを作業レポートに要約して残す。"],
        "en": ["There is no partial-edit operation. First read the entire file, modify it locally, then write the whole file back.", "continuation.ment in a success response gives the next action. Summarize changed files in the task report."],
    },
    "remove": {
        "ko": ["파일 하나만 지운다. 디렉터리 통째 삭제는 없다.", "성공하면 지운 파일을 작업 레포트에 남긴다."],
        "ja": ["ファイルを1つだけ削除する。ディレクトリ全体の削除はない。", "成功したら削除したファイルを作業レポートに残す。"],
        "en": ["Only one file is removed; whole-directory deletion is not supported.", "On success, include the removed file in the task report."],
    },
}

EXAMPLE_BODIES = {
    "read": {"path": "app/main.py", "max_bytes": 20000, "encoding": "utf-8"},
    "grep": {"pattern": "TODO", "glob": "**/*.py", "ignore_case": True, "max_results": 20},
    "glob": {"pattern": "**/*.py"},
    "write": {"path": "app/main.py", "content": "<complete file content>", "mode": "create|overwrite|append", "encoding": "utf-8"},
    "remove": {"path": "app/obsolete.py"},
}

# Worker-mention lines (0349 D0004 D-4/D-5, P0005 [참고] 2단계 멘트 도구 섹션 문면).
# The mention keeps only what a worker that never calls help must still know: call help
# first, do not touch the disk, the tool names, the token, and where the detail lives.
# Everything else moved into the help responses above. The registry owns the strings;
# assembling them into a mention paragraph belongs to the mention builders (D0004 §1).
MENTION_LINES = {
    "ko": {
        "first_action": "첫 행동으로 GET {url} 를 호출해 각 도구의 사용법을 확인하세요.",
        "no_disk_edit": "디스크의 프로젝트 소스를 직접 편집하지 마세요. 소스의 조회와 변경은 모두 아래 도구를 거칩니다.",
        "tools_label": "도구",
        "detail": "도구별 상세: GET {url}",
    },
    "ja": {
        "first_action": "最初の行動として GET {url} を呼び出し、各ツールの使い方を確認してください。",
        "no_disk_edit": "ディスク上のプロジェクトソースを直接編集しないでください。ソースの参照と変更はすべて下記のツールを通します。",
        "tools_label": "ツール",
        "detail": "ツール別の詳細: GET {url}",
    },
    "en": {
        "first_action": "As your first action, call GET {url} to learn how each tool is used.",
        "no_disk_edit": "Do not edit the project source on disk directly — every source read and change goes through the tools below.",
        "tools_label": "Tools",
        "detail": "Per-tool detail: GET {url}",
    },
}

EXAMPLE_RESPONSES = {
    "read": {"ok": True, "op": "read", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/main.py", "content": "<파일 텍스트>", "encoding": "utf-8", "size": 18342, "truncated": False},
    "grep": {"ok": True, "op": "grep", "server_ts": "2026-07-29T13:34:25+09:00", "matches": [{"file": "server/modules/flow_gate/template_provision.py", "line": 194, "text": "def normalize_locale(x_locale: Optional[str]) -> str:"}], "total": 1, "truncated": False},
    "glob": {"ok": True, "op": "glob", "server_ts": "2026-07-29T13:34:31+09:00", "paths": ["server/modules/flow_gate/db/events.py", "server/modules/flow_gate/db/documents.py"], "total": 2},
    "write": {"ok": True, "op": "write", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/main.py", "bytes_written": 18342, "created": False, "continuation": {"ment": "작업을 완료했습니다. 변경 내용을 작업 레포트(TR)로 정리해 제출을 이어가 주세요.", "report_doc_id": None, "next_action": "write_report"}},
    "remove": {"ok": True, "op": "remove", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/obsolete.py", "removed": True, "continuation": {"ment": "작업을 완료했습니다. 변경 내용을 작업 레포트(TR)로 정리해 제출을 이어가 주세요.", "report_doc_id": None, "next_action": "write_report"}},
}


def tool_names(kind: str) -> list[str]:
    """Tool names for a kind, in display order (P0005 §0-4)."""
    if kind == "read_write":
        return list(DISPLAY_ORDER)
    if kind == "read":
        return [name for name in DISPLAY_ORDER if name in READ_TOOLS]
    return []


_tool_names = tool_names


def kind_for_step(
    action_scope: Optional[str],
    step_type: Optional[str] = None,
    lookup_failed: bool = False,
) -> tuple[str, Optional[str]]:
    """The one kind judgment (D0004 §3-1 / P0005 §0-4), source mode NOT applied.

    Callers that already know the step type (the mention builders, which are building the
    mention *for* that step) pass it directly; callers holding only a token go through
    ``kind_for_token``. Source mode gates advertising only, never permission (D0004 D-1),
    so it is layered on top by ``resolve_registry`` rather than folded in here.

    A failed step lookup is always demoted to ``read`` — advertising narrower than reality
    costs an unused tool, advertising wider hands the worker a 403 (D0004 D-6).
    """
    if action_scope in {"review", "workflow_decide"}:
        return "read", None
    if action_scope not in {"new", "edit"}:
        return "none", "token_scope_none"
    if lookup_failed:
        return "read", "step_lookup_failed"
    if step_type in MUTATING_STEP_TYPES:
        return "read_write", None
    return "read", None


def kind_for_token(token_rec: dict) -> tuple[str, Optional[str]]:
    """``kind_for_step`` for a caller that holds a token but not its step type."""
    action_scope = token_rec.get("action_scope")
    step_type: Optional[str] = None
    lookup_failed = False
    if action_scope in {"new", "edit"}:
        step_type, lookup_failed = remote_tool_service._worker_token_step_type_result(token_rec)
    return kind_for_step(action_scope, step_type, lookup_failed)


def _list_notes(kind: str, locale: str, reason: Optional[str], user_jwt: bool) -> list[str]:
    notes = NOTES[locale]
    if kind == "read_write":
        keys = ("path_rule", "auth_rule", "no_disk_edit", "report_changes", "see_detail")
    elif kind == "read":
        keys = ("path_rule", "auth_rule", "no_disk_edit", "read_only", "see_detail")
    elif user_jwt:
        keys = ("none_user",)
    elif reason == "source_mode_local":
        keys = ("none_local",)
    else:
        keys = ("none_scope",)
    return [notes[key] for key in keys]


def resolve_registry(token_rec: dict, project: Optional[str], locale: str) -> dict:
    """Return the effective kind, source mode, catalog entries, and list notes."""
    kind, reason = kind_for_token(token_rec)

    user_jwt = bool(token_rec.get("_is_user_jwt"))
    source_mode: Optional[str] = None
    if project:
        try:
            source_mode = source_mode_service.resolve_effective_mode(project)
        except Exception:
            source_mode = "remote"
        if source_mode != "remote":
            kind = "none"
            reason = "source_mode_local"
    elif user_jwt:
        reason = None

    tools = [
        {"name": name, "method": "POST", "path": f"/remote/{name}",
         "scope": remote_tool_service.OP_SCOPE[name], "summary": SUMMARY[locale][name]}
        for name in tool_names(kind)
    ]
    return {
        "kind": kind,
        "source_mode": source_mode,
        "reason": reason,
        "tools": tools,
        "notes": _list_notes(kind, locale, reason, user_jwt),
    }


def _request_fields(name: str, locale: str) -> list[dict]:
    return [
        {"name": n, "type": t, "required": required, "default": default, "description": description}
        for n, t, required, default, description in FIELDS[name][locale]
    ]


def _errors(name: str, locale: str) -> list[dict]:
    return [{"http_status": status, "code": code, "when": when}
            for status, code, when in ERRORS[name][locale]]


def build_tool_detail(name: str, locale: str, base_url: str) -> dict:
    """Build one localized tool block. ``name`` must be in ``OPS``."""
    return {
        "name": name,
        "method": "POST",
        "path": f"/remote/{name}",
        "scope": remote_tool_service.OP_SCOPE[name],
        "summary": SUMMARY[locale][name],
        "request_fields": _request_fields(name, locale),
        "example_request": {
            "method": "POST",
            "url": f"{base_url}/remote/{name}",
            "headers": {"Authorization": "Bearer <YOUR_TOKEN>", "Content-Type": "application/json"},
            "body": deepcopy(EXAMPLE_BODIES[name]),
        },
        "example_response": deepcopy(EXAMPLE_RESPONSES[name]),
        "errors": _errors(name, locale),
        "cautions": list(CAUTIONS[name][locale]),
    }


def detail_notes(name: str, locale: str) -> list[str]:
    keys = ["path_rule", "auth_rule", "no_disk_edit"]
    if name in WRITE_TOOLS:
        keys.append("report_changes")
    return [NOTES[locale][key] for key in keys]


resolve = resolve_registry
get_tool_detail = build_tool_detail