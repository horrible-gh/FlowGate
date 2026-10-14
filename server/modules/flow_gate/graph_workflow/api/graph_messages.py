"""Display messages of validation issues (P0008 S3-5 / S4-4 / S4-5) in ko / en / ja.

Clients branch on ``code`` / ``structure`` only; ``message`` is for display and may change.
Keys the domain attaches for wording (``_`` prefix) are used here and never sent.
"""
from __future__ import annotations

SOURCE_MESSAGES = {
    "graph_git_required": {
        "ko": "문서 전용 Graph attempt의 소스 작업에는 Git 통합이 필요합니다.",
        "en": "Source access for a document-only Graph attempt requires Git integration.",
        "ja": "文書専用 Graph attempt のソース操作には Git 統合が必要です。",
    },
    "graph_worktree_unavailable": {
        "ko": "Graph attempt의 전용 작업 공간을 확인할 수 없습니다.",
        "en": "The dedicated Graph attempt worktree is unavailable.",
        "ja": "Graph attempt 専用のワークツリーを確認できません。",
    },
    "graph_attempt_source_unsupported": {
        "ko": "이 소스 경로는 Graph attempt를 지원하지 않습니다.",
        "en": "This source operation does not support Graph attempts.",
        "ja": "このソース操作は Graph attempt に対応していません。",
    },
    # flowgate.v02.0061 T#7: a late write / result of a stopping run or an attempt / execution that no longer owns it
    "graph_attempt_inactive": {
        "ko": "이 Graph attempt는 더 이상 쓰기·결과 반영을 받을 수 없습니다(실행 정지 중이거나 attempt가 무효화됨).",
        "en": "This Graph attempt no longer accepts writes or results (the run is stopping or the attempt was invalidated).",
        "ja": "この Graph attempt はもう書き込み・結果反映を受け付けません（実行の停止中、または attempt が無効化されました）。",
    },
}

RUN_EDIT_MESSAGES = {
    "graph_run_locked": {
        "ko": "실행 중·일시정지·정지 중인 Flow는 편집할 수 없습니다.",
        "en": "A running, paused or stopping flow cannot be edited.",
        "ja": "実行中・一時停止中・停止中のフローは編集できません。",
    },
    "graph_node_protected": {
        "ko": "완료·개방·리뷰 노드와 그 입력선은 변경할 수 없습니다.",
        "en": "Done, open and review nodes and their incoming edges cannot be changed.",
        "ja": "完了・開放・レビューノードとその入力エッジは変更できません。",
    },
    "graph_node_key_reused": {
        "ko": "삭제한 노드의 키를 다시 사용할 수 없습니다.",
        "en": "A deleted node key cannot be reused.",
        "ja": "削除したノードのキーは再利用できません。",
    },
    "graph_revision_migration_blocked": {
        "ko": "진행 중 attempt가 있어 최신 개정으로 이관할 수 없습니다.",
        "en": "The run cannot adopt the latest revision while an attempt is active.",
        "ja": "進行中の attempt があるため最新リビジョンへ移行できません。",
    },
    # flowgate.v02.0061 T#11: Hold cannot be set on a node done in the Flow's run
    "graph_hold_done_node": {
        "ko": "완료된 노드에는 Hold를 설정할 수 없습니다.",
        "en": "Hold cannot be set on a done node.",
        "ja": "完了したノードには Hold を設定できません。",
    },
}

_RULE = {
    "multiple_start": ("시작 노드가 여러 개인 그래프", "a graph with several start nodes", "開始ノードが複数あるグラフ"),
    "branch": ("분기(나가는 엣지 여러 개)", "a branch (several outgoing edges)", "分岐（出ていくエッジが複数）"),
    "edge_condition": ("엣지 조건", "an edge condition", "エッジ条件"),
    "join": ("합류(들어오는 엣지 여러 개)", "a join (several incoming edges)", "合流（入ってくるエッジが複数）"),
    "report_pair": ("보고 문서 노드(지시-보고 쌍)", "a report document node (instruction-report pair)",
                    "報告文書ノード（指示-報告ペア）"),
    "cycle": ("순환(되돌아가는 엣지)", "a cycle (an edge going back)", "循環（戻るエッジ）"),
}
_MEANING = {
    "gate_node": ("confirm 카드", "the confirm card", "confirm カード"),
    "custom_node": ("사용자 정의 카드", "a user-defined card", "ユーザー定義カード"),
}
_ROOT_PREFIX = (
    "root 문서를 결합할 시작 노드를 정할 수 없습니다: ",
    "Cannot decide the start node to bind the root document to: ",
    "ルート文書を結合する開始ノードを決められません: ",
)
_FIXED = {
    "empty_graph": ("노드가 하나도 없습니다.", "The graph has no node.", "ノードが一つもありません。"),
    "dangling_edge": ("끝점 노드가 없는 엣지입니다.", "An edge points at a node that does not exist.",
                      "端点ノードがないエッジです。"),
    "duplicate_edge": ("같은 두 노드를 잇는 엣지가 둘 이상입니다.", "Two or more edges connect the same two nodes.",
                       "同じ二つのノードを結ぶエッジが複数あります。"),
    "unreachable_node": ("시작 노드에서 도달할 수 없는 노드입니다.", "The node cannot be reached from a start node.",
                         "開始ノードから到達できないノードです。"),
    # flowgate.v02.0061 pre-run validation (``flowgate.graph_validation``, 0002-CH turn 13 rules 3 / 4)
    "excluded_path_join": (
        "실행 대상이 아닌 경로가 실행 경로에 합류합니다. 요구사항·버그 노드에서 시작하지 않는 경로는 실행 경로에 연결할 수 없습니다.",
        "A path that is not run (it does not start from a requirement / bug node) joins the run path.",
        "実行対象ではない経路（要求・バグノードから始まらない経路）が実行経路に合流しています。",
    ),
    "cycle": ("순환 연결은 허용하지 않습니다.", "A cycle (an edge going back) is not allowed.",
              "循環（戻るエッジ）は許可されません。"),
    "non_palette_doc_type": (
        "Palette 기본 문서 타입이 아닌 노드를 허용할지 아직 정해지지 않았습니다.",
        "Whether a node of a non-Palette document type is allowed has not been decided yet.",
        "Palette の基本文書タイプ以外のノードを許可するかはまだ決まっていません。",
    ),
    "root_binding_start_removed": (
        "결합된 시작 노드를 지운 초안은 확정할 수 없습니다.",
        "A draft that removed the bound start node cannot be confirmed.",
        "結合された開始ノードを削除した下書きは確定できません。",
    ),
    "root_binding_condition_changed": (
        "결합된 시작 노드의 조건(시작 노드·문서 타입)이 바뀌었습니다.",
        "The bound start node no longer meets its condition (start node, document type).",
        "結合された開始ノードの条件（開始ノード・文書タイプ）が変わりました。",
    ),
}
_LOCALES = ("ko", "en", "ja")


def _pick(texts: tuple, locale: str) -> str:
    return texts[_LOCALES.index(locale) if locale in _LOCALES else 0]


def issue_message(issue: dict, locale: str) -> str:
    code, structure = issue.get("code"), issue.get("structure")
    if code == "rule_undecided" and structure in _RULE:
        subject = _pick(_RULE[structure], locale)
        return _pick((f"{subject}의 실행 규칙이 아직 정해지지 않았습니다.",
                      f"The execution rule for {subject} has not been decided yet.",
                      f"{subject}の実行ルールはまだ決まっていません。"), locale)
    if code == "rule_undecided" and structure in _MEANING:
        subject = _pick(_MEANING[structure], locale)
        return _pick((f"{subject}의 실행 의미가 아직 정해지지 않았습니다.",
                      f"The execution meaning of {subject} has not been decided yet.",
                      f"{subject}の実行上の意味はまだ決まっていません。"), locale)
    if code == "rule_undecided" and structure == "non_palette_doc_type":
        return _pick(_FIXED["non_palette_doc_type"], locale)
    if code == "root_binding_start_removed":
        return _pick(_FIXED["root_binding_condition_changed" if issue.get("_condition_changed") else code], locale)
    doc_type = issue.get("_root_doc_type") or "R/B"
    if code == "root_binding_ambiguous_root":
        n = issue.get("_root_count") or len(issue.get("node_keys") or [])
        return _pick(_ROOT_PREFIX, locale) + _pick((f"요구 문서(R/B)가 {n}건입니다.", f"there are {n} R/B documents.",
                                                    f"要求文書（R/B）が {n} 件あります。"), locale)
    if code == "root_binding_no_start_node":
        return _pick(_ROOT_PREFIX, locale) + _pick((f"문서 타입이 {doc_type}인 시작 노드가 없습니다.",
                                                    f"no start node has document type {doc_type}.",
                                                    f"文書タイプ {doc_type} の開始ノードがありません。"), locale)
    if code == "root_binding_ambiguous_start":
        return _pick(_ROOT_PREFIX, locale) + _pick((f"문서 타입이 {doc_type}인 시작 노드가 둘 이상입니다.",
                                                    f"several start nodes have document type {doc_type}.",
                                                    f"文書タイプ {doc_type} の開始ノードが複数あります。"), locale)
    return _pick(_FIXED.get(code, (code or "", code or "", code or "")), locale)


def fill_issue_messages(issues: list[dict], locale: str) -> list[dict]:
    """Copies of ``issues`` with ``message`` set and the internal ``_`` keys removed."""
    out = []
    for item in issues:
        public = {k: v for k, v in item.items() if not k.startswith("_")}
        public["message"] = issue_message(item, locale)
        out.append(public)
    return out
