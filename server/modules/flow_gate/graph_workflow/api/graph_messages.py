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
    "graph_time_machine_blocked": {
        "ko": "Graph Time Machine을 안전하게 실행할 수 없습니다. 차단 사유를 확인하세요.",
        "en": "Graph Time Machine cannot run safely. Check the blocking reasons.",
        "ja": "Graph Time Machine を安全に実行できません。ブロック理由を確認してください。",
    },
    # 0061 T#13: an unfinished Graph Time Machine operation refuses commands / edits; a partial failure
    "graph_time_machine_in_progress": {
        "ko": "Graph Time Machine 작업이 끝나지 않았습니다. 완료 또는 복구 뒤에 다시 시도하세요.",
        "en": "A Graph Time Machine operation is unfinished. Try again once it is finished or recovered.",
        "ja": "Graph Time Machine の操作が完了していません。完了または復旧の後に再試行してください。",
    },
    "graph_time_machine_partial_failed": {
        "ko": "Graph Time Machine의 Git 되돌리기 뒤 상태 반영에 실패했습니다. 서버 재시작 시 복구되며 그 전에는 실행할 수 없습니다.",
        "en": "Graph Time Machine reverted Git but could not update the run state. It is recovered at the server "
              "restart; until then the flow cannot run.",
        "ja": "Graph Time Machine は Git を戻しましたが状態の反映に失敗しました。サーバー再起動時に復旧され、それまでは実行できません。",
    },
    "graph_engine_owned": {
        "ko": "이 Flow는 Graph 엔진이 담당합니다. Graph Time Machine을 사용하세요.",
        "en": "This flow is owned by the Graph engine. Use Graph Time Machine.",
        "ja": "このフローは Graph エンジンの担当です。Graph Time Machine を使用してください。",
    },
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

# flowgate.v02.0061 T#14 (WP answer 2): a chat's edit / edit-once source access while the Flow's Graph run is running,
# paused or stopping — refused with the T#3 lock code ``graph_run_locked`` and this wording.
CHAT_SOURCE_LOCKED = {
    "ko": "이 Flow의 Graph 실행이 진행 중(실행·일시정지·정지 중)이라 채팅으로 소스를 수정할 수 없습니다. 읽기 모드로 사용하세요.",
    "en": "This flow's Graph run is running, paused or stopping, so a chat cannot change the source. Use the read mode.",
    "ja": "このフローの Graph 実行が進行中（実行中・一時停止中・停止中）のため、チャットでソースを変更できません。読み取りモードを使用してください。",
}

# 0061 T#12: stable reason codes; message text is display-only.
TIME_MACHINE_REASONS = {
    "run_missing": (
        "되돌릴 Graph 실행이 없습니다.", "There is no Graph run to rewind.", "巻き戻す Graph 実行がありません。"),
    "run_not_idle": (
        "실행이 idle 상태가 아닙니다.", "The run is not idle.", "実行が idle 状態ではありません。"),
    "executions_in_progress": (
        "요청 또는 실행 중인 AI 실행이 있습니다.", "AI executions are requested or running.",
        "要求中または実行中の AI 実行があります。"),
    "integrations_in_progress": (
        "대기 또는 진행 중인 결과 통합이 있습니다.", "Result integrations are pending or in progress.",
        "待機中または進行中の結果統合があります。"),
    "merge_conflict_open": (
        "Graph attempt 병합 충돌 세션이 열려 있습니다.", "A Graph attempt merge conflict session is open.",
        "Graph attempt のマージ競合セッションが開いています。"),
    "integration_head_input": (
        "통합 브랜치 HEAD를 입력으로 사용한 attempt가 대상에 있습니다.",
        "A target attempt used the integration branch HEAD as its input.",
        "統合ブランチの HEAD を入力に使った attempt が対象に含まれています。"),
    "commit_attribution_missing": (
        "대상의 커밋 귀속 기록이 없습니다.", "Commit attribution is missing for a target.",
        "対象のコミット帰属記録がありません。"),
    "node_not_done": (
        "지정 노드가 완료 상태가 아닙니다.", "The selected node is not done.", "指定ノードは完了していません。"),
    "node_outside_run_scope": (
        "지정 노드가 실행 대상 밖입니다.", "The selected node is outside the run scope.",
        "指定ノードは実行対象外です。"),
    "previous_operation_incomplete": (
        "이전 Graph Time Machine 작업의 완료 또는 복구가 필요합니다.",
        "A previous Graph Time Machine operation must finish or be recovered.",
        "以前の Graph Time Machine 操作の完了または復旧が必要です。"),
    "execution_history_incomplete": (
        "실행 개정 또는 입력 의존 기록이 불완전합니다.", "Execution revision or input dependency history is incomplete.",
        "実行リビジョンまたは入力依存の記録が不完全です。"),
    # 0061 T#13: judged by the operation itself (its plan, its Git step)
    "integration_commit_shared": (
        "대상의 병합 커밋을 대상 밖 attempt도 가리킵니다.", "A target's merge commit is shared with an attempt outside the targets.",
        "対象のマージコミットを対象外の attempt も指しています。"),
    "git_revert_blocked": (
        "병합 커밋을 안전하게 되돌릴 수 없어 그룹 브랜치를 원래 상태로 두었습니다.",
        "A merge commit could not be reverted safely; the group branch was left as it was.",
        "マージコミットを安全に戻せないため、グループブランチを元の状態のままにしました。"),
}


def time_machine_reasons(reasons: list[dict], locale: str) -> list[dict]:
    index = ("ko", "en", "ja").index(locale) if locale in ("ko", "en", "ja") else 0
    return [{**r, "message": TIME_MACHINE_REASONS[r["code"]][index]} for r in reasons]


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
