"""Locale-aware catalog and availability policy for remote source help."""
from __future__ import annotations

from copy import deepcopy
import logging
from typing import Optional

from modules.flow_gate.services import remote_tool_service
from modules.flow_gate.settings import source_mode_service

_logger = logging.getLogger(__name__)

VERSION = "v1"
DISPLAY_ORDER = ("read", "grep", "glob", "stat", "diff", "log", "show", "merge_preview", "write", "patch", "remove", "resolve_base_dirty")
READ_TOOLS = frozenset({"read", "grep", "glob", "stat", "diff", "log", "show", "merge_preview"})
WRITE_TOOLS = frozenset({"write", "patch", "remove", "resolve_base_dirty"})
MUTATING_STEP_TYPES = frozenset({"TR", "TSR", "TS"})

# Tools that belong to ONE action scope rather than to a kind (0482 T0011 x 0507/0492).
# `resolve_base_dirty` reads its project identity from the run token and
# `remote_tool_service._exec_resolve_base_dirty` answers 403 to any token whose
# action_scope is not `resolve_base_dirty`. Leaving it in the plain read_write list
# advertised it to every TR/TSR/TS worker, which is precisely the "handed a tool its
# live token would 403 on" break D0004 D-2 forbids, so the kind decides the generic
# tools and the scope adds its own on top.
SCOPE_BOUND_TOOLS: dict[str, frozenset] = {
    "resolve_base_dirty": frozenset({"resolve_base_dirty"}),
}
_SCOPE_BOUND_NAMES = frozenset(name for names in SCOPE_BOUND_TOOLS.values() for name in names)

_catalog_names = frozenset(DISPLAY_ORDER)
_executable_names = frozenset(remote_tool_service.OPS)
_available_names = _catalog_names & _executable_names
if _catalog_names != _executable_names:
    _logger.error(
        "remote tool catalog mismatch; continuing with the intersection "
        "(catalog_only=%s, executable_only=%s)",
        sorted(_catalog_names - _executable_names),
        sorted(_executable_names - _catalog_names),
    )

SUMMARY = {
    "ko": {
        "read": "원격 프로젝트 소스의 파일 하나를 읽는다.",
        "grep": "소스 트리에서 정규식으로 텍스트를 검색한다.",
        "glob": "글롭 패턴에 맞는 파일 경로 목록을 얻는다.",
        "stat": "경로의 존재 여부와 종류·크기 등 메타데이터를 조회한다.",
        "diff": "merge-base 이후 상대 ref 쪽 변경 patch를 조회한다.",
        "log": "merge-base 이후 상대 ref 쪽 커밋을 최신순으로 조회한다.",
        "show": "커밋 하나의 메타데이터·변경 파일 통계·patch를 조회한다.",
        "merge_preview": "authorized group worktree HEAD를 target_ref에 얹었을 때의 3-way merge를 실제 변경 없이 시뮬레이션해 conflict 파일 목록·clean 여부와 병합 결과 tree oid(merge_tree)를 조회한다.",
        "write": "파일을 새로 만들거나 내용을 통째로 바꾼다.",
        "patch": "파일에서 글자 그대로 일치하는 텍스트를 찾아 일부를 바꾼다.",
        "remove": "파일 하나를 삭제한다.",
    },
    "ja": {
        "read": "リモートプロジェクトソースのファイルを1つ読み取る。",
        "grep": "ソースツリーを正規表現で検索する。",
        "glob": "globパターンに一致するファイルパス一覧を取得する。",
        "stat": "パスの存在有無と種類・サイズなどのメタデータを取得する。",
        "diff": "merge-base以降の相手ref側の変更patchを取得する。",
        "log": "merge-base以降の相手ref側のcommitを新しい順に取得する。",
        "show": "commit1件のメタデータ・変更ファイルの統計・patchを取得する。",
        "merge_preview": "authorized group worktreeのHEADをtarget_refに載せた場合の3-way mergeを実際の変更なしにシミュレーションし、conflictファイル一覧・clean可否と統合結果tree oid（merge_tree）を取得する。",
        "write": "ファイルを新規作成、または内容を丸ごと置き換える。",
        "patch": "ファイル内で完全一致する文字列を検索し、該当部分を置き換える。",
        "remove": "ファイルを1つ削除する。",
    },
    "en": {
        "read": "Read a single file from the remote project source tree.",
        "grep": "Search the source tree with a regular expression.",
        "glob": "List file paths matching a glob pattern.",
        "stat": "Inspect whether a path exists and return its type, size, and metadata.",
        "diff": "Return the target ref patch since its merge base with HEAD.",
        "log": "List target-ref commits since its merge base with HEAD, newest first.",
        "show": "Inspect one commit's metadata, per-file change stats, and patch.",
        "merge_preview": "Simulate a 3-way merge of the authorized group worktree's HEAD onto target_ref without making any change, and report conflicted files, whether the merge is clean, and the resulting tree oid (merge_tree).",
        "write": "Create a file or replace its entire content.",
        "patch": "Replace exact matching text within a file.",
        "remove": "Delete a single file.",
    },
}

NOTES = {
    "ko": {
        "path_rule": "모든 경로는 프로젝트 소스 루트 기준 상대경로입니다. 절대경로나 '..' 세그먼트를 보내지 마세요.",
        "auth_rule": "요청 헤더는 멘트에서 받은 것과 같은 Authorization: Bearer <작업 토큰> 을 씁니다.",
        "no_disk_edit": "디스크의 프로젝트 소스를 직접 편집하지 마세요. 소스의 조회와 변경은 모두 이 도구를 거칩니다.",
        "read_only": "이 단계는 조사 전용입니다. write / patch / remove 를 호출하지 마세요.",
        "report_changes": "write/patch/remove가 성공하면 변경한 소스 파일을 작업 레포트에 요약해 남기세요.",
        "scratch_rule": "조사·디버깅용 임시 파일, 덤프, 노트는 write/patch로 프로젝트 소스 트리에 남기지 말고 멘트에 동봉된 이 작업 토큰의 scratch 디렉터리에 두세요. 0382에서 소스 트리의 흔적이 finalize 흡수 커밋을 거쳐 main에 들어간 전례를 막기 위한 규정입니다.",
        "see_detail": "사용법 상세는 GET /flowgate/api/v1/help/tools/{name} 으로 도구별로 확인하세요.",
        "see_detail_items": "사용법 상세는 GET /flowgate/api/v1/help/items/source_tools/{name} 으로 도구별로 확인하세요.",
        "none_scope": "이 작업 단계에는 원격 소스 도구가 배정되지 않았습니다. 소스 트리에 접근하지 말고 배정된 문서 작업만 수행하세요.",
        "none_local": "이 프로젝트는 원격 소스 접근을 사용하지 않습니다. 소스는 작업 환경에서 직접 다루고, 이 도구는 호출하지 마세요.",
        "none_user": "이 인증 주체에는 원격 소스 도구가 배정되지 않았습니다. 도구는 작업 토큰에만 배정됩니다.",
        "attachments": "파일 관련이라도 문서 첨부파일은 이 소스 도구가 아닙니다. 첨부파일 목록/읽기/소스 복사는 이 응답의 document_attachments 블록과 GET /flowgate/api/v1/help/items/document_attachments 에 있습니다.",
        "attachments_none": "이 단계는 문서 첨부파일 API도 쓸 수 없습니다. 소스 접근이 없는 토큰에는 첨부파일 접근도 없습니다.",
    },
    "ja": {
        "path_rule": "すべてのパスはプロジェクトソースルートからの相対パスです。絶対パスや '..' セグメントを送らないでください。",
        "auth_rule": "リクエストヘッダーには、メントで受け取ったものと同じ Authorization: Bearer <作業トークン> を使います。",
        "no_disk_edit": "ディスク上のプロジェクトソースを直接編集しないでください。ソースの参照と変更はすべてこのツールを通します。",
        "read_only": "この段階は調査専用です。write / patch / remove を呼ばないでください。",
        "report_changes": "write / patch / remove が成功したら、変更したソースファイルを作業レポートに要約して残してください。",
        "scratch_rule": "調査・デバッグ用の一時ファイル、ダンプ、メモは write / patch でプロジェクトソースツリーに残さず、メントに記載されたこの作業トークンの scratch ディレクトリに置いてください。0382でソースツリーの残骸が finalize の吸収コミットを経て main に入った前例を防ぐための規則です。",
        "see_detail": "使い方の詳細は GET /flowgate/api/v1/help/tools/{name} でツールごとに確認してください。",
        "see_detail_items": "使い方の詳細は GET /flowgate/api/v1/help/items/source_tools/{name} でツールごとに確認してください。",
        "none_scope": "この作業段階にはリモートソースツールが割り当てられていません。ソースツリーに触れず、割り当てられた文書作業のみ行ってください。",
        "none_local": "このプロジェクトはリモートソースアクセスを使用しません。ソースは作業環境で直接扱い、このツールは呼び出さないでください。",
        "none_user": "この認証主体にはリモートソースツールが割り当てられていません。ツールは作業トークンにのみ割り当てられます。",
        "attachments": "ファイル関連であっても、文書の添付ファイルはこのソースツールではありません。添付ファイルの一覧/読み取り/ソースコピーはこのレスポンスの document_attachments ブロックと GET /flowgate/api/v1/help/items/document_attachments にあります。",
        "attachments_none": "この段階では文書の添付ファイルAPIも使えません。ソースアクセスのないトークンには添付ファイルへのアクセスもありません。",
    },
    "en": {
        "path_rule": "All paths are project-source-root relative; do not send absolute paths or '..' segments.",
        "auth_rule": "Use the same Authorization: Bearer <work token> you received in the ment.",
        "no_disk_edit": "Do not edit the project source on disk directly — every source read and change goes through these tools.",
        "read_only": "This step is investigation-only for source access. Do not call write, patch, or remove.",
        "report_changes": "After write, patch, or remove succeeds, summarize the changed source files in the task report.",
        "scratch_rule": "Put investigation/debugging temporary files, dumps, and notes in this work token's scratch directory supplied in the ment; never leave them in the project source tree via write or patch. This prevents a repeat of incident 0382, where source-tree debris entered main through the finalize absorb commit.",
        "see_detail": "For usage detail, call GET /flowgate/api/v1/help/tools/{name} per tool.",
        "see_detail_items": "For usage detail, call GET /flowgate/api/v1/help/items/source_tools/{name} per tool.",
        "none_scope": "No remote source tool is assigned to this step. Do not touch the project source tree; carry out only the document work you were given.",
        "none_local": "This project does not use remote source access. Work with the source in your own environment and do not call these tools.",
        "none_user": "No remote source tool is assigned to this identity. Tools are assigned to work tokens only.",
        "attachments": "Document attachments are file-related but they are not these source tools. Attachment list/read/copy-to-source live in this response's document_attachments block and at GET /flowgate/api/v1/help/items/document_attachments.",
        "attachments_none": "This step cannot use the document attachment API either. A token with no source access has no attachment access.",
    },
}

FIELDS = {
    "read": {
        "ko": [("path", "string", True, None, "소스 루트 기준 상대 경로. 절대경로와 '..' 금지."), ("max_bytes", "integer", False, None, "읽을 최대 바이트. 생략하면 파일 전체를 읽되 서버 상한을 넘으면 413."), ("offset", "integer", False, 0, "읽기를 시작할 0 기준 바이트 위치."), ("length", "integer", False, None, "읽을 바이트 창의 최대 길이. max_bytes와 함께 쓰면 더 작은 값이 적용된다."), ("encoding", "string", False, "utf-8", "디코딩 인코딩. 디코딩 실패 문자는 대체 문자로 바뀐다."), ("start_line", "integer", False, None, "1부터 시작하는 읽기 시작 줄 번호. end_line과 함께 써야 하며, offset/length/max_bytes와 동시에 쓸 수 없다."), ("end_line", "integer", False, None, "1부터 시작하는 읽기 끝 줄 번호(포함). start_line <= end_line 이어야 한다. 파일 끝을 넘으면 가능한 줄까지만 반환된다.")],
        "ja": [("path", "string", True, None, "ソースルートからの相対パス。絶対パスと '..' は禁止。"), ("max_bytes", "integer", False, None, "読み取る最大バイト数。省略時はファイル全体を読み取るが、サーバー上限を超えると413。"), ("offset", "integer", False, 0, "読み取りを開始する0基準のバイト位置。"), ("length", "integer", False, None, "読み取るバイト範囲の最大長。max_bytesと併用した場合は小さい方が適用される。"), ("encoding", "string", False, "utf-8", "デコード用エンコーディング。デコードできない文字は置換文字に変わる。"), ("start_line", "integer", False, None, "1始まりの読み取り開始行番号。end_lineと併用が必須で、offset/length/max_bytesとは同時に使えない。"), ("end_line", "integer", False, None, "1始まりの読み取り終了行番号(含む)。start_line <= end_line が必要。ファイル末尾を超える場合は取得できる行までだけ返す。")],
        "en": [("path", "string", True, None, "Path relative to the source root. Absolute paths and '..' are forbidden."), ("max_bytes", "integer", False, None, "Maximum bytes to read. If omitted, the whole file is read unless it exceeds the server limit, which returns 413."), ("offset", "integer", False, 0, "Zero-based byte position where reading starts."), ("length", "integer", False, None, "Maximum byte-window length. When used with max_bytes, the smaller value applies."), ("encoding", "string", False, "utf-8", "Decoding charset. Undecodable characters are replaced."), ("start_line", "integer", False, None, "1-based line number to start reading from. Must be sent together with end_line, and never together with offset/length/max_bytes."), ("end_line", "integer", False, None, "1-based, inclusive line number to stop reading at. Must satisfy start_line <= end_line. Past EOF, only the available lines are returned.")],
    },
    "diff": {
        "ko": [("path", "string", False, None, "선택적 소스 루트 상대 경로."), ("target_ref", "string", False, "origin/main", "비교할 상대 ref. 옵션형과 revspec은 금지.")],
        "ja": [("path", "string", False, None, "任意のソースルート相対パス。"), ("target_ref", "string", False, "origin/main", "比較対象ref。option形式とrevspecは禁止。")],
        "en": [("path", "string", False, None, "Optional source-root-relative path."), ("target_ref", "string", False, "origin/main", "Target ref; option-shaped values and revspecs are forbidden.")],
    },
    "log": {
        "ko": [("path", "string", False, None, "선택적 소스 루트 상대 경로."), ("target_ref", "string", False, "origin/main", "비교할 상대 ref. 옵션형과 revspec은 금지."), ("max_count", "integer", False, None, "반환할 최대 커밋 수. 양의 정수."), ("side", "string", False, "target", "target=merge-base..target_ref(기본값) / head=merge-base..HEAD. HEAD 쪽에만 있는 커밋은 side=head 로 조회한다.")],
        "ja": [("path", "string", False, None, "任意のソースルート相対パス。"), ("target_ref", "string", False, "origin/main", "比較対象ref。option形式とrevspecは禁止。"), ("max_count", "integer", False, None, "返す最大commit数。正の整数。"), ("side", "string", False, "target", "target=merge-base..target_ref(既定) / head=merge-base..HEAD。HEAD側にのみあるcommitはside=headで取得する。")],
        "en": [("path", "string", False, None, "Optional source-root-relative path."), ("target_ref", "string", False, "origin/main", "Target ref; option-shaped values and revspecs are forbidden."), ("max_count", "integer", False, None, "Maximum commits to return; a positive integer."), ("side", "string", False, "target", "target=merge-base..target_ref (default) / head=merge-base..HEAD. Use side=head to see commits that exist only on HEAD.")],
    },
    "show": {
        "ko": [("sha", "string", True, None, "조회할 commit의 hex id(4~64자). 옵션형 문자열이나 revspec은 거절된다.")],
        "ja": [("sha", "string", True, None, "取得するcommitのhex id(4〜64文字)。option形式の文字列やrevspecは拒否される。")],
        "en": [("sha", "string", True, None, "Hex id of the commit to inspect (4-64 chars). Option-shaped strings and revspecs are rejected.")],
    },
    "merge_preview": {
        "ko": [("target_ref", "string", False, "origin/main", "비교할 상대 ref. 옵션형과 revspec은 금지.")],
        "ja": [("target_ref", "string", False, "origin/main", "比較対象ref。option形式とrevspecは禁止。")],
        "en": [("target_ref", "string", False, "origin/main", "Target ref; option-shaped values and revspecs are forbidden.")],
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
    "stat": {
        "ko": [("path", "string", True, None, "메타데이터를 조회할 소스 루트 기준 상대 경로.")],
        "ja": [("path", "string", True, None, "メタデータを取得するソースルートからの相対パス。")],
        "en": [("path", "string", True, None, "Source-root-relative path whose metadata will be inspected.")],
    },
    "write": {
        "ko": [("path", "string", True, None, "소스 루트 기준 상대 경로. 상위 디렉터리는 필요하면 자동 생성된다."), ("content", "string", True, None, "파일 전체 내용. 통째로 쓴다."), ("mode", "string", False, "overwrite", "create=새 파일만(있으면 409) / overwrite=통째 교체 / append=끝에 덧붙임."), ("encoding", "string", False, "utf-8", "인코딩. 이 인코딩으로 표현할 수 없는 문자가 있으면 422.")],
        "ja": [("path", "string", True, None, "ソースルートからの相対パス。親ディレクトリは必要に応じて自動作成される。"), ("content", "string", True, None, "ファイルの全内容を丸ごと書き込む。"), ("mode", "string", False, "overwrite", "create=新規ファイルのみ(存在時409) / overwrite=全体置換 / append=末尾に追記。"), ("encoding", "string", False, "utf-8", "エンコーディング。表現できない文字がある場合は422。")],
        "en": [("path", "string", True, None, "Path relative to the source root. Parent directories are created as needed."), ("content", "string", True, None, "Complete file content written as a whole."), ("mode", "string", False, "overwrite", "create=new files only (409 if present) / overwrite=replace all / append=add at end."), ("encoding", "string", False, "utf-8", "Encoding. Characters that cannot be represented in it return 422.")],
    },
    "patch": {
        "ko": [("path", "string", True, None, "소스 루트 기준 상대 경로."), ("old_string", "string", True, None, "바꿀 대상 텍스트. 빈 문자열은 허용하지 않는다."), ("new_string", "string", True, None, "바꿔 넣을 텍스트."), ("replace_all", "boolean", False, False, "일치하는 여러 곳을 한 번에 바꿀지 여부."), ("encoding", "string", False, "utf-8", "파일을 읽고 쓸 인코딩.")],
        "ja": [("path", "string", True, None, "ソースルートからの相対パス。"), ("old_string", "string", True, None, "置換対象の文字列。空文字列は指定できない。"), ("new_string", "string", True, None, "置換後の文字列。"), ("replace_all", "boolean", False, False, "一致する複数箇所を一度に置換するか。"), ("encoding", "string", False, "utf-8", "ファイルの読み書きに使用するエンコーディング。")],
        "en": [("path", "string", True, None, "Path relative to the source root."), ("old_string", "string", True, None, "Exact text to replace. An empty string is forbidden."), ("new_string", "string", True, None, "Replacement text."), ("replace_all", "boolean", False, False, "Whether to replace every match at once."), ("encoding", "string", False, "utf-8", "Encoding used to read and write the file.")],
    },
    "remove": {
        "ko": [("path", "string", True, None, "삭제할 파일 또는 흔적 디렉터리의 소스 루트 기준 상대 경로."), ("recursive", "boolean", False, False, "디렉터리를 통째로 지운다. 도구가 남긴 흔적(.test-tmp-*, 점 디렉터리, node_modules 등)으로 판정되는 경로에만 허용되고, 그 밖에는 422다.")],
        "ja": [("path", "string", True, None, "削除するファイルまたは痕跡ディレクトリのソースルートからの相対パス。"), ("recursive", "boolean", False, False, "ディレクトリを丸ごと削除する。ツールが残した痕跡(.test-tmp-*、ドットディレクトリ、node_modules など)と判定されるパスにのみ許可され、それ以外は422になる。")],
        "en": [("path", "string", True, None, "Path of the file or artifact directory to delete, relative to the source root."), ("recursive", "boolean", False, False, "Delete a whole directory. Allowed only for paths judged tool artifacts (.test-tmp-*, dot directories, node_modules, …); anything else returns 422.")],
    },
}

ERRORS = {
    "diff": {
        "ko": [(503, "unavailable", "ref가 없거나 merge-base/diff git 명령이 실패했다."), (422, "invalid_request", "ref 또는 path 형식이 잘못됐다."), (403, "forbidden", "이 토큰에 read 스코프가 없다.")],
        "ja": [(503, "unavailable", "refがないかmerge-base/diffのgit commandが失敗した。"), (422, "invalid_request", "refまたはpathの形式が正しくない。"), (403, "forbidden", "このtokenにread scopeがない。")],
        "en": [(503, "unavailable", "The ref is missing or the merge-base/diff git command failed."), (422, "invalid_request", "The ref or path has an invalid form."), (403, "forbidden", "This token does not have the read scope.")],
    },
    "log": {
        "ko": [(503, "unavailable", "ref가 없거나 merge-base/log git 명령이 실패했다."), (422, "invalid_request", "ref, path, max_count 형식이 잘못됐거나 side 가 head/target 이 아니다(reason=invalid_side)."), (403, "forbidden", "이 토큰에 read 스코프가 없다.")],
        "ja": [(503, "unavailable", "refがないかmerge-base/logのgit commandが失敗した。"), (422, "invalid_request", "ref、path、max_countの形式が正しくないか、sideがhead/targetのいずれでもない(reason=invalid_side)。"), (403, "forbidden", "このtokenにread scopeがない。")],
        "en": [(503, "unavailable", "The ref is missing or the merge-base/log git command failed."), (422, "invalid_request", "The ref, path, or max_count has an invalid form, or side is neither head nor target (reason=invalid_side)."), (403, "forbidden", "This token does not have the read scope.")],
    },
    "show": {
        "ko": [(404, "not_found", "sha가 없거나 이 저장소의 commit이 아니다."), (422, "invalid_request", "sha 형식이 잘못됐다(reason=invalid_sha) 또는 요청에 show가 허용하지 않는 필드가 있다(reason=unknown_field)."), (403, "forbidden", "이 토큰에 read 스코프가 없다."), (503, "unavailable", "git show/diff-tree 명령이 실패했다.")],
        "ja": [(404, "not_found", "shaが存在しないか、このリポジトリのcommitではない。"), (422, "invalid_request", "shaの形式が正しくない(reason=invalid_sha)、またはshowが許可しないフィールドがリクエストに含まれる(reason=unknown_field)。"), (403, "forbidden", "このtokenにread scopeがない。"), (503, "unavailable", "git show/diff-treeコマンドが失敗した。")],
        "en": [(404, "not_found", "The sha is missing or is not a commit in this repository."), (422, "invalid_request", "The sha has an invalid form (reason=invalid_sha), or the request carries a field show does not allow (reason=unknown_field)."), (403, "forbidden", "This token does not have the read scope."), (503, "unavailable", "The git show/diff-tree command failed.")],
    },
    "merge_preview": {
        "ko": [(503, "unavailable", "ref가 없거나 merge-base/rev-parse/merge-tree git 명령이 실패했다."), (422, "invalid_request", "target_ref 형식이 잘못됐거나 요청에 merge_preview가 허용하지 않는 필드가 있다(reason=unknown_field)."), (403, "forbidden", "이 토큰에 read 스코프가 없다.")],
        "ja": [(503, "unavailable", "refがないか、merge-base/rev-parse/merge-treeのgit commandが失敗した。"), (422, "invalid_request", "target_refの形式が正しくないか、merge_previewが許可しないフィールドがリクエストに含まれる(reason=unknown_field)。"), (403, "forbidden", "このtokenにread scopeがない。")],
        "en": [(503, "unavailable", "The ref is missing or the merge-base/rev-parse/merge-tree git command failed."), (422, "invalid_request", "target_ref has an invalid form, or the request carries a field merge_preview does not allow (reason=unknown_field)."), (403, "forbidden", "This token does not have the read scope.")],
    },
    "read": {
        "ko": [(404, "not_found", "경로가 없거나 일반 파일이 아니다."), (413, "too_large", "max_bytes 를 생략했는데 파일이 서버 상한을 넘는다(줄 선택자를 쓴 경우도 파일 전체 크기 기준으로 동일하게 적용)."), (422, "invalid_request", "경로가 소스 루트를 벗어나거나 형식이 잘못됐다, 요청에 read가 허용하지 않는 필드가 있다(reason=unknown_field), 또는 start_line/end_line이 잘못됐거나(reason=invalid_line_range) offset/length/max_bytes와 함께 쓰였다(reason=line_and_byte_selector)."), (403, "forbidden", "이 토큰에 read 스코프가 없다.")],
        "ja": [(404, "not_found", "パスが存在しないか通常ファイルではない。"), (413, "too_large", "max_bytes を省略し、ファイルがサーバー上限を超えている(行選択子を使った場合もファイル全体のサイズで同様に判定する)。"), (422, "invalid_request", "パスがソースルート外か形式が正しくない、readが許可しないフィールドがリクエストに含まれる(reason=unknown_field)、またはstart_line/end_lineが不正(reason=invalid_line_range)かoffset/length/max_bytesと併用されている(reason=line_and_byte_selector)。"), (403, "forbidden", "このトークンにreadスコープがない。")],
        "en": [(404, "not_found", "The path does not exist or is not a regular file."), (413, "too_large", "max_bytes was omitted and the file exceeds the server limit (a line selector is judged against the same whole-file size)."), (422, "invalid_request", "The path escapes the source root or has an invalid form, the request carries a field read does not allow (reason=unknown_field), or start_line/end_line is invalid (reason=invalid_line_range) or combined with offset/length/max_bytes (reason=line_and_byte_selector)."), (403, "forbidden", "This token does not have the read scope.")],
    },
    "grep": {
        "ko": [(404, "not_found", "path 가 없거나 디렉터리가 아니다."), (422, "invalid_request", "정규식이 잘못됐거나 path 가 소스 루트를 벗어난다."), (403, "forbidden", "이 토큰에 grep 스코프가 없다.")],
        "ja": [(404, "not_found", "pathが存在しないか、ディレクトリではない。"), (422, "invalid_request", "正規表現が不正か、pathがソースルート外である。"), (403, "forbidden", "このトークンにgrepスコープがない。")],
        "en": [(404, "not_found", "The path does not exist or is not a directory."), (422, "invalid_request", "The regular expression is invalid or path escapes the source root."), (403, "forbidden", "This token does not have the grep scope.")],
    },
    "glob": {
        "ko": [(404, "not_found", "path 가 없거나 디렉터리가 아니다."), (422, "invalid_request", "path 가 소스 루트를 벗어난다."), (403, "forbidden", "이 토큰에 grep 스코프가 없다(glob 은 grep 스코프를 공유한다).")],
        "ja": [(404, "not_found", "pathが存在しないか、ディレクトリではない。"), (422, "invalid_request", "pathがソースルート外である。"), (403, "forbidden", "このトークンにgrepスコープがない(globはgrepスコープを共有する)。")],
        "en": [(404, "not_found", "The path does not exist or is not a directory."), (422, "invalid_request", "path escapes the source root."), (403, "forbidden", "This token does not have the grep scope (glob shares the grep scope).")],
    },
    "stat": {
        "ko": [(422, "invalid_request", "경로가 소스 루트를 벗어나거나 형식이 잘못됐다."), (403, "forbidden", "이 토큰에 read 스코프가 없다.")],
        "ja": [(422, "invalid_request", "パスがソースルート外か形式が正しくない。"), (403, "forbidden", "このトークンにreadスコープがない。")],
        "en": [(422, "invalid_request", "The path escapes the source root or has an invalid form."), (403, "forbidden", "This token does not have the read scope.")],
    },
    "write": {
        "ko": [(409, "conflict", "mode=create 인데 파일이 이미 있다."), (409, "conflict", "그룹 작업 공간을 쓸 수 없다(다른 그룹의 병합 세션이 열려 있거나 워크트리 준비 실패). error.details.cause 에 이유가 실린다."), (413, "too_large", "content 가 서버 상한을 넘는다."), (422, "invalid_request", "경로가 소스 루트를 벗어나거나, content 를 요청 encoding 으로 표현할 수 없다."), (403, "forbidden", "이 토큰에 write 스코프가 없다(조사 전용 단계).")],
        "ja": [(409, "conflict", "mode=createでファイルがすでに存在する。"), (409, "conflict", "グループ作業領域を使用できない(別グループのマージセッションが開いているか、ワークツリー準備に失敗)。理由はerror.details.causeに入る。"), (413, "too_large", "contentがサーバー上限を超える。"), (422, "invalid_request", "パスがソースルート外か、contentを指定encodingで表現できない。"), (403, "forbidden", "このトークンにwriteスコープがない(調査専用段階)。")],
        "en": [(409, "conflict", "mode=create was requested but the file already exists."), (409, "conflict", "The group workspace is unavailable (another group's merge session is open or worktree preparation failed). The reason is in error.details.cause."), (413, "too_large", "content exceeds the server limit."), (422, "invalid_request", "The path escapes the source root or content cannot be represented in the requested encoding."), (403, "forbidden", "This token does not have the write scope (investigation-only step).")],
    },
    "patch": {
        "ko": [(404, "not_found", "파일이 없거나 디렉터리이거나 old_string과 일치하는 텍스트가 없다."), (409, "conflict", "replace_all 없이 두 곳 이상 일치한다."), (413, "too_large", "변경 결과가 서버 상한을 넘는다."), (422, "invalid_request", "텍스트 파일이 아니거나, old_string과 new_string이 같거나, 인코딩할 수 없거나, 경로가 소스 루트를 벗어난다."), (403, "forbidden", "이 토큰에 write 스코프가 없다.")],
        "ja": [(404, "not_found", "ファイルが存在しない、ディレクトリである、またはold_stringに一致する文字列がない。"), (409, "conflict", "replace_allなしで2箇所以上に一致する。"), (413, "too_large", "変更後の結果がサーバー上限を超える。"), (422, "invalid_request", "テキストファイルではない、old_stringとnew_stringが同じ、エンコードできない、またはパスがソースルート外である。"), (403, "forbidden", "このトークンにwriteスコープがない。")],
        "en": [(404, "not_found", "The file is missing, is a directory, or contains no exact old_string match."), (409, "conflict", "Two or more matches exist without replace_all."), (413, "too_large", "The resulting file exceeds the server limit."), (422, "invalid_request", "The file is not text, old_string equals new_string, encoding fails, or the path escapes the source root."), (403, "forbidden", "This token does not have the write scope.")],
    },
    "remove": {
        "ko": [(404, "not_found", "경로가 없거나, 디렉터리인데 recursive 를 주지 않았다."), (409, "conflict", "그룹 작업 공간을 쓸 수 없다."), (409, "conflict", "읽기 전용/잠금이라 지울 수 없다. 재시도해도 같은 결과다(503 아님)."), (422, "invalid_request", "경로가 소스 루트를 벗어나거나, recursive 를 줬는데 흔적이 아닌 경로다."), (403, "forbidden", "이 토큰에 remove 스코프가 없다.")],
        "ja": [(404, "not_found", "パスが存在しないか、ディレクトリなのにrecursiveを指定していない。"), (409, "conflict", "グループ作業領域を使用できない。"), (409, "conflict", "読み取り専用/ロックで削除できない。再試行しても同じ結果(503ではない)。"), (422, "invalid_request", "パスがソースルート外か、recursiveを指定したが痕跡ではないパスである。"), (403, "forbidden", "このトークンにremoveスコープがない。")],
        "en": [(404, "not_found", "The path does not exist, or is a directory and recursive was not set."), (409, "conflict", "The group workspace is unavailable."), (409, "conflict", "The path is read-only or locked and cannot be deleted. Retrying will not help (this is not a 503)."), (422, "invalid_request", "The path escapes the source root, or recursive was set on a path that is not a tool artifact."), (403, "forbidden", "This token does not have the remove scope.")],
    },
}

CAUTIONS = {
    "diff": {
        "ko": ["HEAD와 target_ref의 merge-base부터 target_ref까지만 비교한다. HEAD 쪽 고유 변경은 포함하지 않는다.", "patch는 1 MiB에서 잘리며 truncated로 알린다."],
        "ja": ["HEADとtarget_refのmerge-baseからtarget_refまでだけを比較し、HEAD側固有の変更は含めない。", "patchは1 MiBで切り詰め、truncatedで示す。"],
        "en": ["Only merge-base-to-target_ref changes are compared; HEAD-only changes are excluded.", "The patch is capped at 1 MiB and truncation is reported."],
    },
    "log": {
        "ko": ["merge-base 이후 target_ref 쪽 커밋만 최신순으로 반환한다(side=head 를 주면 HEAD 쪽으로 바뀐다).", "서버 상한은 1000개이며 제한되면 truncated=true다.", "side=head 는 authorized group worktree HEAD에만 있는 커밋(target_ref 에는 없음)을, side=target(기본값)은 target_ref 쪽 변경을 본다."],
        "ja": ["merge-base以降のtarget_ref側commitだけを新しい順で返す(side=headを指定するとHEAD側になる)。", "server上限は1000件で、制限時はtruncated=true。", "side=headはauthorized group worktreeのHEADにのみあるcommit(target_refにはない)を、side=target(既定)はtarget_ref側の変更を見る。"],
        "en": ["Only target-ref commits after the merge base are returned, newest first (side=head switches to the HEAD side).", "The server cap is 1,000 commits; truncation is reported.", "side=head sees commits that exist only on the authorized group worktree's HEAD (not on target_ref); side=target (default) sees target_ref's own changes."],
    },
    "show": {
        "ko": ["HEAD/index/working tree를 바꾸지 않는 read-only 조회다(cat-file/show/diff-tree).", "patch는 diff와 같은 1 MiB 상한에서 잘리며 truncated로 알린다.", "sha는 이 저장소에 실제로 존재하는 commit이어야 한다 — 다른 그룹 worktree에만 있는 커밋은 404다."],
        "ja": ["HEAD/index/working treeを変更しないread-only参照である(cat-file/show/diff-tree)。", "patchはdiffと同じ1 MiB上限で切り詰められ、truncatedで示す。", "shaはこのリポジトリに実在するcommitでなければならない — 別グループのworktreeにのみあるcommitは404である。"],
        "en": ["A read-only inspection that never changes HEAD, the index, or the working tree (cat-file/show/diff-tree).", "The patch is capped at the same 1 MiB limit as diff, and truncation is reported.", "sha must be a commit that actually exists in this repository — a commit that exists only in another group's worktree returns 404."],
    },
    "merge_preview": {
        "ko": ["git merge-tree --write-tree만 쓰며 HEAD/index/working tree를 절대 바꾸지 않는다(내부적으로 object database에 병합 결과 tree/blob을 남길 수 있으나 어떤 ref에도 연결되지 않는다).", "clean=false일 때만 conflicts에 파일이 들어 있고, conflicts가 200(_MAX_CONFLICT_FILES)을 넘으면 잘리고 truncated=true가 된다.", "이 조회는 실제 merge를 수행하는 것이 아니라 미리보기다 — 실제로 병합하려면 이 도구가 아니라 사람의 개입 또는 다른 절차가 필요하다.", "merge_tree는 후보 병합 결과의 tree oid다. clean 여부와 무관하게 read/grep/glob의 ref에 그대로 넣으면 그 tree 기준 병합 결과를 조회할 수 있으며, 충돌 파일에는 <<<<<<< / ======= / >>>>>>> 마커가 포함된다."],
        "ja": ["git merge-tree --write-treeのみを使い、HEAD/index/working treeを絶対に変更しない(内部的にobject databaseへ統合結果のtree/blobを残す場合があるが、どのrefにも接続されない)。", "clean=falseのときのみconflictsにファイルが入り、conflictsが200(_MAX_CONFLICT_FILES)を超えると切り詰められてtruncated=trueになる。", "この参照は実際にmergeを行うものではなくプレビューである — 実際に統合するにはこのツールではなく人の介入または別の手順が必要である。", "merge_treeは候補統合結果のtree oidである。clean可否にかかわらずread/grep/globのrefへそのまま指定すると、そのtree基準の統合結果を参照でき、conflictファイルには<<<<<<< / ======= / >>>>>>>マーカーが含まれる。"],
        "en": ["Uses only git merge-tree --write-tree and never changes HEAD, the index, or the working tree (it may leave merged tree/blob objects in the object database internally, but none are reachable from any ref).", "conflicts is populated only when clean=false; if it exceeds 200 (_MAX_CONFLICT_FILES) it is truncated and truncated=true.", "This is a preview, not an actual merge -- actually merging requires human intervention or a different procedure, not this tool.", "merge_tree is the candidate merge result tree oid. Regardless of whether the merge is clean, pass it directly as the ref to read/grep/glob to inspect that tree's merge result; conflicted files include <<<<<<< / ======= / >>>>>>> markers."],
    },
    "read": {
        "ko": ["offset과 length로 바이트 단위 읽기 구간을 지정할 수 있다. offset은 0부터 시작하며 파일 끝을 넘으면 빈 내용과 eof=true를 돌려준다.", "size는 구간을 잘라 읽었을 때도 파일 전체 크기다.", "큰 소스 파일에서 필요한 줄만 보려면 start_line/end_line(1부터 시작, 끝 줄 포함)을 쓴다. offset/length/max_bytes와 동시에 보내면 422다. 응답에는 returned_start_line/returned_end_line/total_lines가 함께 실린다.", "read가 허용하지 않는 필드를 보내면 무시되지 않고 422(reason=unknown_field)로 거절된다 — 예를 들어 start_line 없이 잘못 붙인 오타 필드는 전체 파일을 읽어오지 않는다."],
        "ja": ["offsetとlengthでバイト単位の読み取り範囲を指定できる。offsetは0基準で、ファイル末尾を超えると空の内容とeof=trueを返す。", "sizeは範囲を切って読んだ場合もファイル全体のサイズである。", "大きなソースファイルで必要な行だけを見るにはstart_line/end_line(1始まり、終端行を含む)を使う。offset/length/max_bytesと同時に送ると422になる。応答にはreturned_start_line/returned_end_line/total_linesが含まれる。", "readが許可しないフィールドを送ると無視されず422(reason=unknown_field)で拒否される — 例えば誤って付けたタイプミスのフィールドはファイル全体を読み込ませない。"],
        "en": ["Use offset and length to select a byte window. Offset is zero-based; past EOF returns empty content with eof=true.", "size is the complete file size even when only a window is read.", "To see only the lines you need from a large source file, use start_line/end_line (1-based, end inclusive). Sending them together with offset/length/max_bytes returns 422. The response also carries returned_start_line/returned_end_line/total_lines.", "A field read does not allow is never silently ignored — it returns 422 (reason=unknown_field), so a mistyped field can no longer trigger a whole-file read."],
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
    "stat": {
        "ko": ["없는 경로도 404가 아니라 200과 exists=false로 답한다. 소스 루트를 벗어난 경로는 422다.", "바이너리 여부는 파일 앞 64KB 안에 NUL 바이트가 있는지로 판정한다."],
        "ja": ["存在しないパスも404ではなく200とexists=falseを返す。ソースルート外のパスは422になる。", "バイナリ判定はファイル先頭64KB以内にNULバイトがあるかで決まる。"],
        "en": ["A missing path returns 200 with exists=false, not 404. A path outside the source root returns 422.", "Binary status is determined by whether the first 64 KB contains a NUL byte."],
    },
    "write": {
        "ko": ["이 도구는 파일 전체를 덮어쓴다. 일부만 바꾸려면 patch를 쓴다.", "성공 응답의 continuation.ment는 다음 행동 지시다. 변경한 파일을 작업 레포트에 요약해 남긴다."],
        "ja": ["このツールはファイル全体を上書きする。一部だけ変更する場合はpatchを使う。", "成功応答のcontinuation.mentは次の行動指示である。変更したファイルを作業レポートに要約して残す。"],
        "en": ["This tool overwrites the entire file. Use patch to change only part of it.", "continuation.ment in a success response gives the next action. Summarize changed files in the task report."],
    },
    "patch": {
        "ko": ["줄 번호가 아니라 글자 그대로 일치하는 텍스트를 찾는다.", "일치 항목이 없으면 404, replace_all 없이 여러 곳에 일치하면 409이며 두 경우 모두 파일에 아무것도 쓰지 않는다.", "건드리지 않은 바이트는 그대로 두므로 줄바꿈 형식이 다른 파일도 통째 변경으로 번지지 않는다. 부분 수정이 필요하면 write 대신 이 도구를 쓴다."],
        "ja": ["行番号ではなく、文字列の完全一致で検索する。", "一致がなければ404、replace_allなしで複数箇所に一致すれば409となり、どちらの場合もファイルには何も書き込まない。", "変更しないバイトはそのまま保つため、改行形式が異なるファイルでも全体変更にならない。部分変更にはwriteではなくこのツールを使う。"],
        "en": ["Matching uses exact text, not line numbers.", "No match returns 404; multiple matches without replace_all return 409. Neither case writes anything to the file.", "Untouched bytes are preserved, so files with different line endings do not become whole-file changes. Use this tool instead of write for partial edits."],
    },
    "remove": {
        "ko": ["기본은 파일 하나다. 디렉터리를 통째로 지우려면 recursive=true 를 주는데, 도구가 남긴 흔적으로 판정되는 경로에만 열린다.", "읽기 전용 파일은 권한을 풀고 다시 시도한다. 그래도 막히면 409 이고, 그건 재시도해도 안 되는 상태라는 뜻이다.", "성공하면 지운 파일을 작업 레포트에 남긴다."],
        "ja": ["既定はファイル1つ。ディレクトリを丸ごと削除するにはrecursive=trueを指定するが、ツールが残した痕跡と判定されるパスにのみ開かれる。", "読み取り専用ファイルは権限を解除して再試行する。それでも失敗すると409で、再試行しても解決しない状態を意味する。", "成功したら削除したファイルを作業レポートに残す。"],
        "en": ["A single file by default. Set recursive=true to delete a whole directory — allowed only for paths judged tool artifacts.", "Read-only files are retried after clearing the flag. A remaining failure returns 409, meaning retrying will not help.", "On success, include the removed file in the task report."],
    },
}

EXAMPLE_BODIES = {
    "read": {"path": "app/main.py", "start_line": 1, "end_line": 40, "encoding": "utf-8"},
    "diff": {"target_ref": "origin/main", "path": "server/app.py"},
    "log": {"target_ref": "origin/main", "path": "server/app.py", "max_count": 20},
    "show": {"sha": "fedcba9876543210fedcba9876543210fedcba98"},
    "merge_preview": {"target_ref": "origin/main"},
    "grep": {"pattern": "TODO", "glob": "**/*.py", "ignore_case": True, "max_results": 20},
    "glob": {"pattern": "**/*.py"},
    "stat": {"path": "app/main.py"},
    "write": {"path": "app/main.py", "content": "<complete file content>", "mode": "create|overwrite|append", "encoding": "utf-8"},
    "patch": {"path": "app/main.py", "old_string": "old text", "new_string": "new text", "replace_all": False, "encoding": "utf-8"},
    "remove": {"path": "app/obsolete.py", "recursive": False},
}

# Worker-mention lines (0349 D0004 D-4/D-5, P0005 [notes], stage-2 mention tool section wording).
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
    "read": {"ok": True, "op": "read", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/main.py", "content": "<lines 1-40>", "encoding": "utf-8", "size": 18342, "start_line": 1, "end_line": 40, "returned_start_line": 1, "returned_end_line": 40, "total_lines": 512, "returned_bytes": 1180, "eof": False, "truncated": True},
    "diff": {"ok": True, "op": "diff", "server_ts": "2026-07-29T13:34:25+09:00", "merge_base": "0123456789abcdef0123456789abcdef01234567", "target_ref": "origin/main", "patch": "diff --git a/server/app.py b/server/app.py\n...", "returned_bytes": 58, "truncated": False},
    "log": {"ok": True, "op": "log", "server_ts": "2026-07-29T13:34:25+09:00", "merge_base": "0123456789abcdef0123456789abcdef01234567", "target_ref": "origin/main", "side": "target", "commits": [{"sha": "fedcba9876543210fedcba9876543210fedcba98", "subject": "fix: update app"}], "total": 1, "truncated": False},
    "show": {"ok": True, "op": "show", "server_ts": "2026-07-29T13:34:25+09:00", "sha": "fedcba9876543210fedcba9876543210fedcba98", "parents": ["0123456789abcdef0123456789abcdef01234567"], "author_name": "Jane Doe", "author_email": "jane@example.com", "author_date": "2026-07-29T13:20:00+09:00", "subject": "fix: update app", "files": [{"path": "server/app.py", "insertions": 3, "deletions": 1}], "patch": "diff --git a/server/app.py b/server/app.py\n...", "returned_bytes": 58, "truncated": False},
    "merge_preview": {"ok": True, "op": "merge_preview", "server_ts": "2026-07-29T13:34:25+09:00", "clean": False, "merge_base": "0123456789abcdef0123456789abcdef01234567", "head": "fedcba9876543210fedcba9876543210fedcba98", "target_ref": "origin/main", "target_sha": "89abcdef0123456789abcdef0123456789abcdef", "merge_tree": "abcdef0123456789abcdef0123456789abcdef01", "conflicts": ["server/app.py"], "truncated": False},
    "grep": {"ok": True, "op": "grep", "server_ts": "2026-07-29T13:34:25+09:00", "matches": [{"file": "server/modules/flow_gate/template_provision.py", "line": 194, "text": "def normalize_locale(x_locale: Optional[str]) -> str:"}], "total": 1, "truncated": False},
    "glob": {"ok": True, "op": "glob", "server_ts": "2026-07-29T13:34:31+09:00", "paths": ["server/modules/flow_gate/db/events.py", "server/modules/flow_gate/db/documents.py"], "total": 2},
    "stat": {"ok": True, "op": "stat", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/main.py", "exists": True, "type": "file", "size": 18342, "mtime": "2026-07-29T13:30:00+09:00", "eol": "lf", "binary": False},
    "write": {"ok": True, "op": "write", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/main.py", "bytes_written": 18342, "created": False, "continuation": {"ment": "작업을 완료했습니다. 변경 내용을 작업 레포트(TR)로 정리해 제출을 이어가 주세요.", "report_doc_id": None, "next_action": "write_report"}},
    "patch": {"ok": True, "op": "patch", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/main.py", "replacements": 1, "size_before": 18342, "size_after": 18342, "bytes_written": 18342, "encoding": "utf-8", "eol": "lf", "eol_normalized": False, "continuation": {"ment": "작업을 완료했습니다. 변경 내용을 작업 레포트(TR)로 정리해 제출을 이어가 주세요.", "report_doc_id": None, "next_action": "write_report"}},
    "remove": {"ok": True, "op": "remove", "server_ts": "2026-07-29T13:34:25+09:00", "path": "app/obsolete.py", "removed": True, "continuation": {"ment": "작업을 완료했습니다. 변경 내용을 작업 레포트(TR)로 정리해 제출을 이어가 주세요.", "report_doc_id": None, "next_action": "write_report"}},
}


# Group-less base-dirty action tool. It is advertised only to a token whose
# action_scope is `resolve_base_dirty` (see SCOPE_BOUND_TOOLS) and obtains
# project/root identity from the grant, never from request input.
for _locale in ("ko", "ja", "en"):
    SUMMARY[_locale]["resolve_base_dirty"] = "Apply validated commit/discard decisions to tracked base changes."
FIELDS["resolve_base_dirty"] = {
    locale: [
        ("decisions", "array", True, None, "[{path, action: commit|discard}] decision list."),
        ("complete", "boolean", True, None, "Whether this report covers the complete baseline."),
        ("commit_message", "string", False, None, "Required when a complete report commits files."),
    ] for locale in ("ko", "ja", "en")
}
ERRORS["resolve_base_dirty"] = {
    locale: [(422, "invalid_request", "The whole batch is rejected before mutation when invalid."),
             (409, "git_busy", "Another project Git operation owns the lock."),
             (403, "forbidden", "The token is not a resolve_base_dirty read_write token.")]
    for locale in ("ko", "ja", "en")
}
CAUTIONS["resolve_base_dirty"] = {
    locale: ["Project identity and source root come from the run token, never request input.",
             "complete=false returns partial; an incomplete complete=true report returns dirty."]
    for locale in ("ko", "ja", "en")
}
EXAMPLE_BODIES["resolve_base_dirty"] = {"decisions": [{"path": "app/main.py", "action": "commit"}], "complete": True, "commit_message": "fix: resolve base changes"}
EXAMPLE_RESPONSES["resolve_base_dirty"] = {"ok": True, "op": "resolve_base_dirty", "status": "resolved", "remaining": [], "commit": "0123456789abcdef"}

# ── Document attachments (0523 T0004 §17) ───────────────────────────────────
# An attachment hangs off a DOCUMENT, not off the source tree, so it is not a /remote/*
# operation and must stay out of DISPLAY_ORDER (test_tool_catalog_parity_0356 pins that
# tuple to remote_tool_service.OPS, and remote_tool_service has no attachment op to pin).
# It is advertised from this module anyway because GET /help/tools is the one "which file
# operations do I have?" surface every worker is told to call first: while that answer
# spoke only for the source tree, a worker asked to touch a document's attachment read
# the six read tools, read the "investigation only" note, and concluded that attachments
# cannot be reached at all — which is what 0523 TR0005 was rejected for twice.
ATTACHMENT_DISPLAY_ORDER = ("attachment_list", "attachment_read", "attachment_copy")
ATTACHMENT_ROUTES: dict[str, tuple[str, str]] = {
    "attachment_list": ("GET", "/document/{doc_id}/attachments"),
    "attachment_read": ("GET", "/document/{doc_id}/attachments/{name}/read"),
    "attachment_copy": ("POST", "/document/{doc_id}/attachments/{name}/copy"),
}
# Which effective kinds may call each operation. This is the SAME read/read_write
# judgment the worker routes themselves make (document_routes._attachment_read_kind, and
# the copy route's read_write check), so this catalog can never advertise an operation
# the server would answer 403 to — the D0004 D-2 rule the source tools already follow.
ATTACHMENT_KINDS: dict[str, frozenset] = {
    "attachment_list": frozenset({"read", "read_write"}),
    "attachment_read": frozenset({"read", "read_write"}),
    "attachment_copy": frozenset({"read_write"}),
}
ATTACHMENT_SCOPE = {
    "attachment_list": "read",
    "attachment_read": "read",
    "attachment_copy": "write",
}
# Attachment operations that exist on the Console screen but are NOT on the worker
# surface at all. Named out loud so "control an attachment" gets a complete answer from
# help instead of the worker having to infer absence from silence (T0004 §19's "unknown
# must never be answered as none", applied to the capability list itself).
ATTACHMENT_ABSENT_OPS = ("attachment_upload", "attachment_delete")

ATTACHMENT_SUMMARY = {
    "ko": {
        "attachment_list": "이 문서에 올라온 첨부파일의 목록과 크기·종류를 조회한다.",
        "attachment_read": "첨부파일 하나의 내용을 텍스트 또는 base64로 읽는다.",
        "attachment_copy": "첨부파일 하나를 내 그룹 워크트리의 소스 트리로 복사한다.",
        "attachment_upload": "첨부파일 올리기(콘솔 화면 전용).",
        "attachment_delete": "첨부파일 삭제(콘솔 화면 전용).",
    },
    "ja": {
        "attachment_list": "この文書にアップロードされた添付ファイルの一覧とサイズ・種類を取得する。",
        "attachment_read": "添付ファイル1つの内容をテキストまたはbase64で読み取る。",
        "attachment_copy": "添付ファイル1つを自分のグループワークツリーのソースツリーへコピーする。",
        "attachment_upload": "添付ファイルのアップロード(コンソール画面専用)。",
        "attachment_delete": "添付ファイルの削除(コンソール画面専用)。",
    },
    "en": {
        "attachment_list": "List the attachments uploaded to this document, with size and type.",
        "attachment_read": "Read one attachment's content as text or base64.",
        "attachment_copy": "Copy one attachment into your own group worktree's source tree.",
        "attachment_upload": "Upload an attachment (Console screen only).",
        "attachment_delete": "Delete an attachment (Console screen only).",
    },
}

ATTACHMENT_VIEW_NOTES = {
    "ko": {
        "what": "문서 첨부파일은 위 소스 도구(/remote/*)가 아니라 문서 API로 다룹니다. operations 의 method 와 path 를 그대로 호출하세요.",
        "not_source": "첨부파일은 copy 로 복사하기 전까지 프로젝트 소스가 아닙니다. /remote/read, grep, glob 으로 찾지 마세요.",
        "permission": "권한은 소스 접근 kind 를 그대로 따릅니다: read=목록/읽기, read_write=목록/읽기/복사, none=사용 불가.",
        "scope": "볼 수 있는 문서는 이 토큰이 바인딩된 문서와 같은 그룹의 문서뿐입니다. 다른 그룹의 문서 첨부파일은 403 입니다.",
        "denied": "이 토큰의 소스 접근 kind={kind} 에는 허용되지 않습니다.",
        "absent": "작업 토큰에는 열려 있지 않습니다. 첨부파일을 올리거나 지우는 일은 콘솔 화면에서 사람이 합니다.",
    },
    "ja": {
        "what": "文書の添付ファイルは上のソースツール(/remote/*)ではなく文書APIで扱います。operations の method と path をそのまま呼び出してください。",
        "not_source": "添付ファイルは copy でコピーするまでプロジェクトソースではありません。/remote/read、grep、glob で探さないでください。",
        "permission": "権限はソースアクセスの kind に従います: read=一覧/読み取り、read_write=一覧/読み取り/コピー、none=利用不可。",
        "scope": "参照できるのはこのトークンがバインドされた文書と同じグループの文書だけです。他グループの文書の添付ファイルは403です。",
        "denied": "このトークンのソースアクセス kind={kind} では許可されていません。",
        "absent": "作業トークンには開放されていません。添付ファイルの追加・削除はコンソール画面で人が行います。",
    },
    "en": {
        "what": "Document attachments are handled by the document API, not by the source tools above (/remote/*). Call the method and path in operations as written.",
        "not_source": "An attachment is not project source until copy puts it there. Do not look for it through /remote/read, grep or glob.",
        "permission": "Permission follows the source-access kind exactly: read=list/read, read_write=list/read/copy, none=unavailable.",
        "scope": "You may reach the document this token is bound to and other documents in its own group. Another group's document attachments answer 403.",
        "denied": "Not allowed for this token's source-access kind={kind}.",
        "absent": "Not open to work tokens. Uploading and deleting attachments is done by a person on the Console screen.",
    },
}

ATTACHMENT_FIELDS: dict[str, dict[str, list]] = {
    "attachment_list": {
        "ko": [("doc_id", "string", True, None, "path", "첨부파일을 조회할 문서 ID. 경로에 넣는다.")],
        "ja": [("doc_id", "string", True, None, "path", "添付ファイルを取得する文書ID。パスに入れる。")],
        "en": [("doc_id", "string", True, None, "path", "Id of the document whose attachments are listed; goes in the path.")],
    },
    "attachment_read": {
        "ko": [
            ("doc_id", "string", True, None, "path", "첨부파일이 달린 문서 ID."),
            ("name", "string", True, None, "path", "읽을 첨부파일 이름. 목록의 filename 을 그대로 쓴다."),
            ("mode", "string", False, "auto", "query", "auto=내용을 보고 판단 / text=텍스트로 강제 / base64=바이너리로 강제."),
            ("encoding", "string", False, "utf-8", "query", "텍스트로 디코딩할 인코딩."),
        ],
        "ja": [
            ("doc_id", "string", True, None, "path", "添付ファイルが付いている文書ID。"),
            ("name", "string", True, None, "path", "読み取る添付ファイル名。一覧の filename をそのまま使う。"),
            ("mode", "string", False, "auto", "query", "auto=内容から判定 / text=テキスト強制 / base64=バイナリ強制。"),
            ("encoding", "string", False, "utf-8", "query", "テキストとしてデコードするエンコーディング。"),
        ],
        "en": [
            ("doc_id", "string", True, None, "path", "Id of the document the attachment belongs to."),
            ("name", "string", True, None, "path", "Attachment name to read; use the filename from the list."),
            ("mode", "string", False, "auto", "query", "auto=decide from content / text=force text / base64=force binary."),
            ("encoding", "string", False, "utf-8", "query", "Charset used to decode as text."),
        ],
    },
    "attachment_copy": {
        "ko": [
            ("doc_id", "string", True, None, "path", "첨부파일이 달린 문서 ID."),
            ("name", "string", True, None, "path", "복사할 첨부파일 이름."),
            ("target_path", "string", True, None, "body", "소스 루트 기준 상대 POSIX 경로. 절대경로와 '..' 금지. 목적지 루트는 언제나 이 토큰 자신의 그룹 워크트리이며 요청으로 바꿀 수 없다."),
        ],
        "ja": [
            ("doc_id", "string", True, None, "path", "添付ファイルが付いている文書ID。"),
            ("name", "string", True, None, "path", "コピーする添付ファイル名。"),
            ("target_path", "string", True, None, "body", "ソースルートからの相対POSIXパス。絶対パスと '..' は禁止。コピー先ルートは常にこのトークン自身のグループワークツリーであり、リクエストでは変更できない。"),
        ],
        "en": [
            ("doc_id", "string", True, None, "path", "Id of the document the attachment belongs to."),
            ("name", "string", True, None, "path", "Attachment name to copy."),
            ("target_path", "string", True, None, "body", "Source-root-relative POSIX path. Absolute paths and '..' are forbidden. The destination root is always this token's own group worktree and the request cannot steer it elsewhere."),
        ],
    },
}

ATTACHMENT_ERRORS: dict[str, dict[str, list]] = {
    "attachment_list": {
        "ko": [(403, "forbidden", "이 토큰이 그 문서 범위 밖이거나 소스 접근 kind 가 none 이다."),
               (404, "not_found", "그 doc_id 의 문서가 없다."),
               (422, "invalid_request", "doc_id 형식이 잘못됐다.")],
        "ja": [(403, "forbidden", "このトークンがその文書のスコープ外か、ソースアクセスの kind が none。"),
               (404, "not_found", "その doc_id の文書が存在しない。"),
               (422, "invalid_request", "doc_id の形式が正しくない。")],
        "en": [(403, "forbidden", "This token is outside the document's scope, or its source-access kind is none."),
               (404, "not_found", "No document with that doc_id."),
               (422, "invalid_request", "The doc_id has an invalid form.")],
    },
    "attachment_read": {
        "ko": [(403, "forbidden", "이 토큰이 그 문서 범위 밖이거나 소스 접근 kind 가 none 이다."),
               (404, "ATTACHMENT_NOT_FOUND", "그 이름의 첨부파일이 이 문서에 없다."),
               (413, "READ_TOO_LARGE", "첨부파일이 읽기 상한을 넘는다. 내용을 잘라 주지 않고 읽기 자체를 거부한다."),
               (415, "INVALID_TEXT_ENCODING", "mode=text 인데 요청한 인코딩으로 디코딩할 수 없다."),
               (422, "INVALID_REQUEST", "mode 또는 encoding 값이 잘못됐다.")],
        "ja": [(403, "forbidden", "このトークンがその文書のスコープ外か、ソースアクセスの kind が none。"),
               (404, "ATTACHMENT_NOT_FOUND", "その名前の添付ファイルがこの文書に存在しない。"),
               (413, "READ_TOO_LARGE", "添付ファイルが読み取り上限を超える。内容を切り詰めず読み取り自体を拒否する。"),
               (415, "INVALID_TEXT_ENCODING", "mode=text だが要求されたエンコーディングでデコードできない。"),
               (422, "INVALID_REQUEST", "mode または encoding の値が正しくない。")],
        "en": [(403, "forbidden", "This token is outside the document's scope, or its source-access kind is none."),
               (404, "ATTACHMENT_NOT_FOUND", "This document has no attachment with that name."),
               (413, "READ_TOO_LARGE", "The attachment exceeds the read ceiling; the read is refused rather than truncated."),
               (415, "INVALID_TEXT_ENCODING", "mode=text but the content cannot be decoded with the requested encoding."),
               (422, "INVALID_REQUEST", "The mode or encoding value is invalid.")],
    },
    "attachment_copy": {
        "ko": [(403, "forbidden", "소스 접근 kind 가 read_write 가 아니거나, 그룹에 묶이지 않은 토큰이거나, 문서 범위 밖이다."),
               (400, "INVALID_PATH", "target_path 가 절대경로거나 '..' 를 포함하거나 소스 루트를 벗어난다."),
               (409, "TARGET_EXISTS", "그 경로에 파일이 이미 있다. 덮어쓰지 않는다."),
               (409, "DOCUMENT_NOT_MUTABLE", "다른 실행이 그룹을 잠그고 있거나 문서가 읽기 전용이다."),
               (422, "INVALID_REQUEST", "target_path 가 비어 있다.")],
        "ja": [(403, "forbidden", "ソースアクセスの kind が read_write でない、グループに紐づかないトークン、または文書のスコープ外。"),
               (400, "INVALID_PATH", "target_path が絶対パス、'..' を含む、またはソースルート外。"),
               (409, "TARGET_EXISTS", "そのパスにファイルが既に存在する。上書きしない。"),
               (409, "DOCUMENT_NOT_MUTABLE", "他の実行がグループをロックしているか、文書が読み取り専用。"),
               (422, "INVALID_REQUEST", "target_path が空。")],
        "en": [(403, "forbidden", "The source-access kind is not read_write, the token has no group binding, or the document is out of scope."),
               (400, "INVALID_PATH", "target_path is absolute, contains '..', or escapes the source root."),
               (409, "TARGET_EXISTS", "A file already exists at that path; copy never overwrites."),
               (409, "DOCUMENT_NOT_MUTABLE", "Another run holds the group lock, or the document is read-only."),
               (422, "INVALID_REQUEST", "target_path is empty.")],
    },
}

ATTACHMENT_CAUTIONS: dict[str, dict[str, list]] = {
    "attachment_list": {
        "ko": ["빈 배열은 '첨부파일 없음'을 확정한 답이다. 조회가 실패하면 빈 배열이 아니라 오류가 온다.",
               "GET /document/{doc_id} 응답의 attachments 필드로도 같은 목록을 받는다. 내용은 들어 있지 않다."],
        "ja": ["空配列は「添付ファイルなし」を確定した答え。取得に失敗した場合は空配列ではなくエラーが返る。",
               "GET /document/{doc_id} のレスポンスの attachments フィールドでも同じ一覧が得られる。内容は含まれない。"],
        "en": ["An empty array is a confirmed 'no attachments', not a shrug: a failed lookup returns an error instead.",
               "The same list arrives in the attachments field of GET /document/{doc_id}. It never carries content."],
    },
    "attachment_read": {
        "ko": ["내용은 절대 잘리지 않는다. 상한을 넘으면 413 으로 읽기 자체가 거부된다.",
               "kind=binary 면 content 는 base64 다. kind=text 면 그대로 쓸 수 있는 문자열이다.",
               "읽었다고 소스가 되지 않는다. 소스로 쓰려면 attachment_copy 로 복사한다."],
        "ja": ["内容は決して切り詰められない。上限を超えると413で読み取り自体が拒否される。",
               "kind=binary なら content は base64。kind=text ならそのまま使える文字列。",
               "読んでもソースにはならない。ソースとして使うには attachment_copy でコピーする。"],
        "en": ["Content is never truncated: past the ceiling the read itself is refused with 413.",
               "kind=binary means content is base64; kind=text means it is directly usable text.",
               "Reading does not make it source. To work on it as source, copy it with attachment_copy."],
    },
    "attachment_copy": {
        "ko": ["복사 목적지 루트는 언제나 이 토큰 자신의 그룹 워크트리다. 요청으로 다른 곳을 가리킬 수 없다.",
               "복사된 파일은 보통의 소스 변경이다. 작업 레포트의 '## 변경 파일' 절에 반드시 적는다.",
               "이미 있는 파일은 덮어쓰지 않고 409 다. 바꿔 쓰려면 소스 도구 write/patch 를 쓴다."],
        "ja": ["コピー先ルートは常にこのトークン自身のグループワークツリー。リクエストで別の場所を指すことはできない。",
               "コピーされたファイルは通常のソース変更。作業レポートの変更ファイル節に必ず記載する。",
               "既存ファイルは上書きせず409。書き換えるにはソースツールの write/patch を使う。"],
        "en": ["The destination root is always this token's own group worktree; the request cannot point anywhere else.",
               "A copied file is an ordinary source change. List it in the task report's changed-files section.",
               "An existing file is never overwritten — it answers 409. Use the write/patch source tools to change it."],
    },
}

ATTACHMENT_EXAMPLE_BODIES = {
    "attachment_copy": {"target_path": "assets/schema.json"},
}
ATTACHMENT_EXAMPLE_RESPONSES = {
    "attachment_list": {"ok": True, "doc_id": "flowgate.default.0523.0005-TR", "attachments": [{"filename": "schema.json", "size": 12345, "content_type": "application/json", "uploaded_at": "2026-09-04T09:14:30+09:00"}], "count": 1},
    "attachment_read": {"ok": True, "doc_id": "flowgate.default.0523.0005-TR", "attachment": {"filename": "schema.json", "size": 12345, "content_type": "application/json"}, "kind": "text", "encoding": "utf-8", "content_encoding": "identity", "content": "{\n  \"title\": \"...\"\n}", "truncated": False},
    "attachment_copy": {"ok": True, "doc_id": "flowgate.default.0523.0005-TR", "filename": "schema.json", "destination": {"project_id": "flowgate", "group_id": "flowgate.default.0523", "target_path": "assets/schema.json", "path_base": "source"}, "size": 12345, "content_sha256": "0123456789abcdef", "copied_at": "2026-09-04T09:20:00+09:00"},
}


def attachment_names(kind: str) -> list[str]:
    """Attachment operation names this kind may actually call, in display order."""
    return [name for name in ATTACHMENT_DISPLAY_ORDER if kind in ATTACHMENT_KINDS[name]]


def attachment_entry(name: str, locale: str, base_url: str) -> dict:
    """One catalog row, in the same shape a ``tools`` row has, plus the full URL.

    Same keys as the source-tool rows (name/method/path/scope/summary) so a worker that
    already parses ``tools`` can read this list with the same code; ``url`` is added
    because these paths carry {doc_id}/{name} placeholders rather than a fixed op path.
    """
    method, path = ATTACHMENT_ROUTES[name]
    return {
        "name": name,
        "method": method,
        "path": path,
        "url": f"{base_url}{path}",
        "scope": ATTACHMENT_SCOPE[name],
        "summary": ATTACHMENT_SUMMARY[locale][name],
    }


def attachment_view(kind: str, locale: str, base_url: str) -> dict:
    """The document-attachment half of "which file operations do I have?".

    ``operations`` carries only what this kind may actually call, the same D0004 D-2 rule
    the source-tool list follows. ``denied`` and ``absent`` then say out loud what is NOT
    callable and why, so an empty or short ``operations`` list can never be read as
    "attachments cannot be reached from here" — the wrong conclusion this block exists to
    prevent (0523 TR0005 rejections of 2026-09-04).
    """
    text = ATTACHMENT_VIEW_NOTES[locale]
    allowed = attachment_names(kind)
    return {
        "kind": kind,
        "available": bool(allowed),
        "operations": [attachment_entry(name, locale, base_url) for name in allowed],
        "denied": [
            {"name": name, "summary": ATTACHMENT_SUMMARY[locale][name],
             "reason": text["denied"].format(kind=kind)}
            for name in ATTACHMENT_DISPLAY_ORDER if name not in allowed
        ],
        "absent": [
            {"name": name, "summary": ATTACHMENT_SUMMARY[locale][name], "reason": text["absent"]}
            for name in ATTACHMENT_ABSENT_OPS
        ],
        "detail_url": f"{base_url}/help/tools/{{name}}",
        "help_item_url": f"{base_url}/help/items/document_attachments",
        "notes": [text["what"], text["not_source"], text["permission"], text["scope"]],
    }


def attachment_request_fields(name: str, locale: str) -> list[dict]:
    return [
        {"name": n, "type": t, "required": required, "default": default,
         "in": where, "description": description}
        for n, t, required, default, where, description in ATTACHMENT_FIELDS[name][locale]
    ]


def build_attachment_detail(name: str, locale: str, base_url: str) -> dict:
    """One localized attachment-operation block, shaped like ``build_tool_detail``."""
    method, path = ATTACHMENT_ROUTES[name]
    example_request: dict = {
        "method": method,
        "url": f"{base_url}{path}",
        "headers": {"Authorization": "Bearer <YOUR_TOKEN>"},
    }
    if name in ATTACHMENT_EXAMPLE_BODIES:
        example_request["headers"]["Content-Type"] = "application/json"
        example_request["body"] = deepcopy(ATTACHMENT_EXAMPLE_BODIES[name])
    return {
        "name": name,
        "method": method,
        "path": path,
        "scope": ATTACHMENT_SCOPE[name],
        "summary": ATTACHMENT_SUMMARY[locale][name],
        "request_fields": attachment_request_fields(name, locale),
        "example_request": example_request,
        "example_response": deepcopy(ATTACHMENT_EXAMPLE_RESPONSES[name]),
        "errors": [{"http_status": status, "code": code, "when": when}
                   for status, code, when in ATTACHMENT_ERRORS[name][locale]],
        "cautions": list(ATTACHMENT_CAUTIONS[name][locale]),
    }


def attachment_detail_notes(name: str, locale: str) -> list[str]:
    text = ATTACHMENT_VIEW_NOTES[locale]
    keys = ["what", "not_source", "permission", "scope"]
    if name == "attachment_copy":
        return [text[key] for key in keys] + [NOTES[locale]["report_changes"]]
    return [text[key] for key in keys]


def tool_names(kind: str, action_scope: Optional[str] = None) -> list[str]:
    """Tool names for a kind, in display order (P0005 §0-4).

    ``action_scope`` only ever ADDS the scope-bound tools of `SCOPE_BOUND_TOOLS`; it
    can never widen a kind. Omitting it therefore stays the conservative answer (the
    generic tool set), which is what a caller holding no scope should advertise.
    """
    scoped = SCOPE_BOUND_TOOLS.get(action_scope or "", frozenset())
    if kind == "read_write":
        return [name for name in DISPLAY_ORDER
                if name in _available_names
                and (name not in _SCOPE_BOUND_NAMES or name in scoped)]
    if kind == "read":
        return [name for name in DISPLAY_ORDER if name in READ_TOOLS and name in _available_names]
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
    if action_scope in {"review", "workflow_decide", "chat", "resolve_conflict"}:
        return "read", None
    if action_scope == "resolve_base_dirty":
        return "read_write", None
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
        keys = ("path_rule", "auth_rule", "no_disk_edit", "scratch_rule", "report_changes",
                "attachments", "see_detail")
    elif kind == "read":
        keys = ("path_rule", "auth_rule", "no_disk_edit", "read_only", "attachments", "see_detail")
    elif user_jwt:
        # A console user JWT is a person on the Console screen, which manages attachments
        # through its own /documents/... API. The worker attachment note would misdescribe
        # what that caller may do, so this branch deliberately does not carry it.
        keys = ("none_user",)
    elif reason == "source_mode_local":
        keys = ("none_local", "attachments_none")
    else:
        keys = ("none_scope", "attachments_none")
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
        for name in tool_names(kind, token_rec.get("action_scope"))
    ]
    return {
        "kind": kind,
        "source_mode": source_mode,
        "reason": reason,
        "tools": tools,
        "notes": _list_notes(kind, locale, reason, user_jwt),
    }


def items_view_notes(registry: dict, locale: str) -> list[str]:
    """``resolve_registry`` notes, re-pointed at the /help/items address.

    The 0372 help catalog serves the same tool list under a second path. Only the
    "where the per-tool detail lives" line differs, so it is swapped here rather
    than the note list being written out a second time — two copies would drift
    the moment one of the other notes changes.
    """
    notes = NOTES[locale]
    return [notes["see_detail_items"] if note == notes["see_detail"] else note
            for note in registry.get("notes", [])]


def _request_fields(name: str, locale: str) -> list[dict]:
    fields = [
        {"name": n, "type": t, "required": required, "default": default, "description": description}
        for n, t, required, default, description in FIELDS[name][locale]
    ]
    if name in {"read", "grep", "glob", "stat"}:
        descriptions = {
            "ko": "선택적 커밋 ref. 생략 또는 null이면 working tree(미커밋 변경 포함), 지정하면 committed tree를 읽는다.",
            "ja": "任意のcommit ref。省略またはnullはworking tree(未commit変更を含む)、指定時はcommitted treeを読む。",
            "en": "Optional committed-tree ref. Omit or send null for the working tree (including uncommitted changes); specify it to read a committed tree.",
        }
        fields.append({"name": "ref", "type": "string", "required": False, "default": None, "description": descriptions[locale]})
    return fields


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
        keys.extend(("scratch_rule", "report_changes"))
    return [NOTES[locale][key] for key in keys]


resolve = resolve_registry
get_tool_detail = build_tool_detail

