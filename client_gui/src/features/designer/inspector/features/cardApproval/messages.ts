// flowgate.v02.0056 F2-1 — card approval bar strings, merged under `gui.inspector.cardApproval`. The card states
// and the [승인] wording (button, refusals by code) are the Canvas card's own keys (`gui.designer.center.
// card_states` / `approval`, one wording for both places); only the bar's frame and reasons are here.
export default {
  ko: {
    label: '카드 승인',
    reason: {
      read_only_conflict: '담당이 겹친 플로우라 승인할 수 없습니다',
      busy: '승인하는 중',
    },
    open_refused: '거절된 문서 열기',
  },
  en: {
    label: 'Card approval',
    reason: {
      read_only_conflict: 'The flow has conflicting owners and cannot be approved',
      busy: 'Approving',
    },
    open_refused: 'Open the refused document',
  },
  ja: {
    label: 'カード承認',
    reason: {
      read_only_conflict: '担当が重なったフローのため承認できません',
      busy: '承認中',
    },
    open_refused: '拒否された文書を開く',
  },
}
