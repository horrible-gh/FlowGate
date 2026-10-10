// flowgate.v02.0051 stage 2 — Graph gate strings, merged under `gui.inspector.gateCard`.
export default {
  ko: {
    label: '확인',
    slot: {
      result: '확인 결과',
      document: '문서',
    },
    empty: {
      before: '확인 전',
    },
    confirm: '✓ 확인',
    reject: '✕ 반려',
    reason: {
      unconnected: 'Graph 확인 Node의 확인·반려는 아직 연결되지 않았습니다. 용도(중간 승인 · 그룹 종료 · AI 위임 마지막 확인)는 토론 중입니다.',
    },
  },
  en: {
    label: 'Check',
    slot: {
      result: 'Check result',
      document: 'Document',
    },
    empty: {
      before: 'Before check',
    },
    confirm: '✓ Confirm',
    reject: '✕ Reject',
    reason: {
      unconnected: 'Confirming or rejecting a Graph check Node is not connected yet. Its role (mid approval · group end · final check of AI delegation) is under discussion.',
    },
  },
  ja: {
    label: '確認',
    slot: {
      result: '確認結果',
      document: '文書',
    },
    empty: {
      before: '確認前',
    },
    confirm: '✓ 確認',
    reject: '✕ 差し戻し',
    reason: {
      unconnected: 'Graph 確認ノードの確認・差し戻しはまだ連携されていません。用途（中間承認 · グループ終了 · AI委任の最終確認）は検討中です。',
    },
  },
}
