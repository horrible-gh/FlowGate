// flowgate.v02.0113 T#5 — the run result windows' words (ko / en / ja), merged under `gui.runResult` by the windows.
const ko = {
  title: '실행 결과',
  origin_unknown: '확인 불가',
  detail_title: '실행 결과 상세',
  // flowgate.v02gui.0001: the window titles naming the result's Flow.
  title_with_flow: '실행 결과 · {name}',
  detail_title_with_flow: '실행 결과 상세 · {name}',
  state: { completed: '완료', failed: '실패', waiting: '대기', running: '실행 중', stopped: '중지', superseded: '다른 명령으로 넘어감' },
  scope: { step: '다음 단계', full: '전체 실행' },
  label: { scope: '실행 범위', steps: '처리 단계', counts: '완료 / 실패', next: '다음 상태', origin_flow: '발생 플로우', reason: '실패 사유' },
  outcome: { done: '완료', failed: '실패', waiting: '대기', running: '실행 중', pending: '대기 전' },
  wait: { manual_write_wait: '수동 작성 대기', ai_setup_required: 'AI 설정 필요', ai_unavailable: 'AI 사용 불가', review: '검수·승인 대기' },
  next: {
    completed: '완료', step_done: '다음 단계 대기', waiting_approval: '사람 작성·승인 대기', no_targets: '실행할 카드 없음',
    error: '오류', blocked_rejected: '반려로 멈춤', merge_conflict: '병합 대기', hold: 'Hold 대기', stopped: '중지됨',
    gate_publish: '확정 반영 중', blocked_recovery: '복구 대기', running: '실행 중', paused: '일시정지', stopping: '중지 중',
  },
  failure: {
    ai_unavailable: 'AI를 사용할 수 없습니다', stage_failed: '단계를 진행하지 못했습니다', auto_approval_failed: '자동 승인이 되지 않았습니다',
    review_rejected: '검수에서 반려되었습니다', dispatch_failed: '카드를 시작하지 못했습니다', node_failed: '카드 실행에 오류가 있습니다',
    start_failed: 'AI를 시작하지 못했습니다', call_failed: 'AI 호출이 실패했습니다', admission_refused: 'AI 실행이 거절되었습니다',
    run_error: '실행 오류', other: '실행 오류',
  },
  steps_summary: '카드 {n}개',
  more: '외 {n}개',
  detail: '상세',
  close: '닫기',
  messages: '메시지 {n}개',
  copy_all: '전체 복사',
  copied: '복사했습니다',
  copy_failed: '복사하지 못했습니다',
  empty: '메시지 없음',
  ai: { instruction: '지시 AI', execution: '실행 AI', review: '검수 AI' },
  use: { none: '없음', person: '사용하지 않음', no_review: '검수 없음', wait_setup: 'AI 설정 필요', wait_unavailable: 'AI 사용 불가' },
  role: {
    instruction: '지시', execution: '실행', review: '검수', instruction_review: '지시서 검수', rework: '재작업', qa_response: '질의 응답',
    event: '진행', failure: '오류',
  },
  model: { requested: '요청', confirmed: '확인', unknown: '확인 불가' },
  status: { pending: '대기 전', open: '진행', review: '검수', done: '완료', rejected: '반려' },
  error_head: '오류 전문',
  body_source: {
    not_preserved: '이 실행(launch)의 출력은 보존되지 않았습니다',
    run: 'AI 실행의 최종 출력입니다 (이 실행만의 출력으로 보존된 기록이 아님)',
  },
}

const en: typeof ko = {
  title: 'Run result',
  origin_unknown: 'Unknown',
  detail_title: 'Run result details',
  title_with_flow: 'Run result · {name}',
  detail_title_with_flow: 'Run result details · {name}',
  state: { completed: 'Completed', failed: 'Failed', waiting: 'Waiting', running: 'Running', stopped: 'Stopped', superseded: 'Taken over by another command' },
  scope: { step: 'Next step', full: 'Run all' },
  label: { scope: 'Scope', steps: 'Steps processed', counts: 'Done / failed', next: 'Next state', origin_flow: 'Origin flow', reason: 'Failure' },
  outcome: { done: 'Done', failed: 'Failed', waiting: 'Waiting', running: 'Running', pending: 'Not started' },
  wait: { manual_write_wait: 'Waiting for manual writing', ai_setup_required: 'AI setup required', ai_unavailable: 'AI unavailable', review: 'Waiting for review / approval' },
  next: {
    completed: 'Completed', step_done: 'Waiting for the next step', waiting_approval: 'Waiting for a person', no_targets: 'No card to run',
    error: 'Error', blocked_rejected: 'Stopped by a rejection', merge_conflict: 'Waiting for merge', hold: 'Waiting on hold', stopped: 'Stopped',
    gate_publish: 'Applying the confirmation', blocked_recovery: 'Waiting for recovery', running: 'Running', paused: 'Paused', stopping: 'Stopping',
  },
  failure: {
    ai_unavailable: 'The AI cannot be used', stage_failed: 'The stage could not go on', auto_approval_failed: 'The automatic approval was not made',
    review_rejected: 'Rejected by the review', dispatch_failed: 'The card could not start', node_failed: 'The card ran into an error',
    start_failed: 'The AI could not start', call_failed: 'The AI call failed', admission_refused: 'The AI run was refused',
    run_error: 'Run error', other: 'Run error',
  },
  steps_summary: '{n} cards',
  more: '{n} more',
  detail: 'Details',
  close: 'Close',
  messages: '{n} messages',
  copy_all: 'Copy all',
  copied: 'Copied',
  copy_failed: 'Could not copy',
  empty: 'No messages',
  ai: { instruction: 'Instruction AI', execution: 'Execution AI', review: 'Review AI' },
  use: { none: 'None', person: 'Do not use', no_review: 'No review', wait_setup: 'AI setup required', wait_unavailable: 'AI unavailable' },
  role: {
    instruction: 'Instruction', execution: 'Execution', review: 'Review', instruction_review: 'Instruction review', rework: 'Rework',
    qa_response: 'Q&A answer', event: 'Progress', failure: 'Error',
  },
  model: { requested: 'Requested', confirmed: 'Confirmed', unknown: 'Not confirmed' },
  status: { pending: 'Not started', open: 'Open', review: 'Review', done: 'Done', rejected: 'Rejected' },
  error_head: 'Full error',
  body_source: {
    not_preserved: 'The output of this launch was not preserved',
    run: "The AI run's final output (not kept as this launch's own output)",
  },
}

const ja: typeof ko = {
  title: '実行結果',
  origin_unknown: '確認不可',
  detail_title: '実行結果の詳細',
  title_with_flow: '実行結果 · {name}',
  detail_title_with_flow: '実行結果の詳細 · {name}',
  state: { completed: '完了', failed: '失敗', waiting: '待機', running: '実行中', stopped: '停止', superseded: '別のコマンドに引き継ぎ' },
  scope: { step: '次のステップ', full: '全体実行' },
  label: { scope: '実行範囲', steps: '処理ステップ', counts: '完了 / 失敗', next: '次の状態', origin_flow: '発生フロー', reason: '失敗理由' },
  outcome: { done: '完了', failed: '失敗', waiting: '待機', running: '実行中', pending: '未開始' },
  wait: { manual_write_wait: '手動作成待ち', ai_setup_required: 'AI設定が必要', ai_unavailable: 'AI使用不可', review: 'レビュー・承認待ち' },
  next: {
    completed: '完了', step_done: '次のステップ待ち', waiting_approval: '人の作成・承認待ち', no_targets: '実行するカードなし',
    error: 'エラー', blocked_rejected: '差戻しで停止', merge_conflict: 'マージ待ち', hold: 'Hold待ち', stopped: '停止済み',
    gate_publish: '確定を反映中', blocked_recovery: '復旧待ち', running: '実行中', paused: '一時停止', stopping: '停止中',
  },
  failure: {
    ai_unavailable: 'AIを使用できません', stage_failed: 'ステップを進められませんでした', auto_approval_failed: '自動承認されませんでした',
    review_rejected: 'レビューで差し戻されました', dispatch_failed: 'カードを開始できませんでした', node_failed: 'カードの実行でエラーが発生しました',
    start_failed: 'AIを開始できませんでした', call_failed: 'AI呼び出しが失敗しました', admission_refused: 'AI実行が拒否されました',
    run_error: '実行エラー', other: '実行エラー',
  },
  steps_summary: 'カード {n}件',
  more: 'ほか {n}件',
  detail: '詳細',
  close: '閉じる',
  messages: 'メッセージ {n}件',
  copy_all: 'すべてコピー',
  copied: 'コピーしました',
  copy_failed: 'コピーできませんでした',
  empty: 'メッセージなし',
  ai: { instruction: '指示AI', execution: '実行AI', review: 'レビューAI' },
  use: { none: 'なし', person: '使用しない', no_review: 'レビューなし', wait_setup: 'AI設定が必要', wait_unavailable: 'AI使用不可' },
  role: {
    instruction: '指示', execution: '実行', review: 'レビュー', instruction_review: '指示書レビュー', rework: '再作業', qa_response: '質疑応答',
    event: '進行', failure: 'エラー',
  },
  model: { requested: '要求', confirmed: '確認', unknown: '確認不可' },
  status: { pending: '未開始', open: '進行', review: 'レビュー', done: '完了', rejected: '差戻し' },
  error_head: 'エラー全文',
  body_source: {
    not_preserved: 'この実行(launch)の出力は保存されていません',
    run: 'AI実行の最終出力です (この実行だけの出力として保存された記録ではありません)',
  },
}

const messages = { ko, en, ja }
export default messages

export function installRunResultMessages(composer: { mergeLocaleMessage(locale: string, message: Record<string, unknown>): void }) {
  for (const locale of ['ko', 'en', 'ja'] as const) composer.mergeLocaleMessage(locale, { gui: { runResult: messages[locale] } })
}
