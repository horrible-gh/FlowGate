// flowgate.v02.0051 stage 2 — Graph gate strings, merged under `gui.inspector.gateCard`.
// flowgate.v02.0053 T#5 — the Custom card's 지시서 · 레포트 lines.
// flowgate.v02.0053 T#9 — the gate decision: why it waits, why it was refused, the reject reason dialog.
export default {
  ko: {
    label: '확인',
    slot: {
      result: '확인 결과',
      document: '문서',
      instruction: '지시서',
      report: '레포트',
    },
    empty: {
      before: '확인 전',
      after_run: '실행 후',
    },
    confirm: '✓ 확인',
    reject: '✕ 반려',
    reason: {
      not_running: '실행 중일 때 확인할 수 있습니다',
      not_open: '이 확인 차례가 오면 확인할 수 있습니다',
      read_only_conflict: '담당이 겹친 흐름이라 확인할 수 없습니다',
      busy: '처리 중',
    },
    failure: {
      not_running: '실행이 멈춰 있어 확인하지 못했습니다',
      not_open: '이 확인 차례가 아니라 확인하지 못했습니다',
      undecided: '확인 규칙이 아직 정해지지 않아 확인하지 못했습니다',
      forbidden: '확인할 권한이 없습니다',
      network: '연결이 끊겨 확인하지 못했습니다',
      other: '확인하지 못했습니다',
    },
    reject_dialog: {
      title: '반려',
      placeholder: '반려 사유 (선택)',
      cancel: '취소',
      submit: '반려',
    },
  },
  en: {
    label: 'Check',
    slot: {
      result: 'Check result',
      document: 'Document',
      instruction: 'Instruction',
      report: 'Report',
    },
    empty: {
      before: 'Before check',
      after_run: 'After run',
    },
    confirm: '✓ Confirm',
    reject: '✕ Reject',
    reason: {
      not_running: 'It can be checked while the flow runs',
      not_open: 'It can be checked when its turn comes',
      read_only_conflict: 'This flow has two owners, so it cannot be checked',
      busy: 'Working',
    },
    failure: {
      not_running: 'The run is stopped, so it was not checked',
      not_open: 'This check is not open, so it was not checked',
      undecided: 'The check rule is not decided yet, so it was not checked',
      forbidden: 'You do not have permission to check it',
      network: 'The connection dropped and it was not checked',
      other: 'It could not be checked',
    },
    reject_dialog: {
      title: 'Reject',
      placeholder: 'Reason (optional)',
      cancel: 'Cancel',
      submit: 'Reject',
    },
  },
  ja: {
    label: '確認',
    slot: {
      result: '確認結果',
      document: '文書',
      instruction: '指示書',
      report: 'レポート',
    },
    empty: {
      before: '確認前',
      after_run: '実行後',
    },
    confirm: '✓ 確認',
    reject: '✕ 差し戻し',
    reason: {
      not_running: '実行中に確認できます',
      not_open: 'この確認の順番が来たら確認できます',
      read_only_conflict: '担当が重なったフローのため確認できません',
      busy: '処理中',
    },
    failure: {
      not_running: '実行が停止しているため確認できませんでした',
      not_open: 'この確認の順番ではないため確認できませんでした',
      undecided: '確認ルールがまだ決まっていないため確認できませんでした',
      forbidden: '確認する権限がありません',
      network: '接続が切れたため確認できませんでした',
      other: '確認できませんでした',
    },
    reject_dialog: {
      title: '差し戻し',
      placeholder: '差し戻しの理由（任意）',
      cancel: 'キャンセル',
      submit: '差し戻す',
    },
  },
}
