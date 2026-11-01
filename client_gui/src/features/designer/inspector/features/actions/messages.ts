// flowgate.v02.0048 T#6 — header action strings, merged under `gui.inspector.actions` (D#1 §5.5).
// The three locales keep the same keys (structure.0048 B12). Reason keys follow D#1 §8.3 rule 6-2.
// flowgate.v02.0056 F2-1: the header's approve / reject (with its reason dialog and the TR commit line) are gone —
// a card is approved with its own [승인] (cardApproval / the Canvas card); only the v0.1 run / rework stay.
export default {
  ko: {
    label: '문서 실행',
    run: '▶ 실행',
    run_hint: 'v0.1 단건 실행: AI가 이 문서를 다시 작업합니다',
    rework: '↻ 재작업',
    rework_hint: 'v0.1 재작업: AI가 반려 사유를 반영해 다시 작업합니다',
    reason: {
      read_only_conflict: '소유 충돌 플로우라 바꿀 수 없습니다.',
      read_only_revision: 'Graph revision 보기에서는 바꿀 수 없습니다. draft 보기로 옮기세요.',
      disposed: '폐기된 그룹이라 바꿀 수 없습니다.',
      ai_running: 'AI 실행 중이라 바꿀 수 없습니다.',
      busy: '처리 중입니다.',
    },
    failure: {
      running: 'AI 실행 중이라 거절되었습니다. 상태를 다시 읽었습니다.',
      disposed: '폐기된 그룹이라 거절되었습니다. 상태를 다시 읽었습니다.',
      conflict: '지금 상태에서는 처리할 수 없어 거절되었습니다. 상태를 다시 읽었습니다.',
      forbidden: '이 조작을 할 권한이 없습니다.',
      not_found: '문서를 찾을 수 없습니다.',
      invalid: '요청이 거절되었습니다.',
      network: '서버에 연결하지 못했습니다.',
      other: '조작을 마치지 못했습니다.',
    },
  },
  en: {
    label: 'Document run',
    run: '▶ Run',
    run_hint: 'v0.1 single run: the AI works on this document again',
    rework: '↻ Rework',
    rework_hint: 'v0.1 rework: the AI works again with the rejection reason',
    reason: {
      read_only_conflict: 'This Flow has an ownership conflict and cannot be changed.',
      read_only_revision: 'A Graph revision view cannot be changed. Switch to the draft view.',
      disposed: 'This group was disposed and cannot be changed.',
      ai_running: 'An AI run is in progress, so nothing can be changed.',
      busy: 'Working…',
    },
    failure: {
      running: 'Refused: an AI run is in progress. The state was read again.',
      disposed: 'Refused: the group was disposed. The state was read again.',
      conflict: 'Refused in the current state. The state was read again.',
      forbidden: 'You do not have permission for this action.',
      not_found: 'The document was not found.',
      invalid: 'The request was refused.',
      network: 'Could not reach the server.',
      other: 'The action could not be completed.',
    },
  },
  ja: {
    label: '文書実行',
    run: '▶ 実行',
    run_hint: 'v0.1 単発実行: AIがこの文書をもう一度作業します',
    rework: '↻ 再作業',
    rework_hint: 'v0.1 再作業: AIが差し戻し理由を反映して作業し直します',
    reason: {
      read_only_conflict: '所有が競合しているフローのため変更できません。',
      read_only_revision: 'Graph revision 表示では変更できません。draft 表示に切り替えてください。',
      disposed: '破棄されたグループのため変更できません。',
      ai_running: 'AI実行中のため変更できません。',
      busy: '処理中です。',
    },
    failure: {
      running: 'AI実行中のため拒否されました。状態を読み直しました。',
      disposed: '破棄されたグループのため拒否されました。状態を読み直しました。',
      conflict: '現在の状態では処理できないため拒否されました。状態を読み直しました。',
      forbidden: 'この操作を行う権限がありません。',
      not_found: '文書が見つかりません。',
      invalid: 'リクエストが拒否されました。',
      network: 'サーバーに接続できませんでした。',
      other: '操作を完了できませんでした。',
    },
  },
}
