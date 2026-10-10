// flowgate.v02.0051 stage 2 — 작업 card strings, merged under `gui.inspector.taskCard` (D#1 §5.5).
// The three locales keep the same keys (structure.0048 B12). The pill wordings of a document state are
// the Frame's (gui.designer.inspector.review_status / slot_state).
export default {
  ko: {
    slot: {
      instruction: '지시서',
      report: '레포트',
    },
    empty: {
      after_run: '실행 후',
      not_yet: '아직 없음',
      unconnected: '연결 전',
    },
    summary: {
      not_read: '상태 미연결',
    },
    write_unconnected: '작성 — 아직 연결되지 않음 (v0.1 문서 화면에서 작성)',
  },
  en: {
    slot: {
      instruction: 'Instruction',
      report: 'Report',
    },
    empty: {
      after_run: 'After run',
      not_yet: 'Not yet',
      unconnected: 'Not linked',
    },
    summary: {
      not_read: 'State not connected',
    },
    write_unconnected: 'Write — not connected yet (written in the v0.1 document screen)',
  },
  ja: {
    slot: {
      instruction: '指示書',
      report: 'レポート',
    },
    empty: {
      after_run: '実行後',
      not_yet: 'まだなし',
      unconnected: '未連携',
    },
    summary: {
      not_read: '状態は未連携',
    },
    write_unconnected: '作成 — まだ連携されていません（v0.1 の文書画面で作成）',
  },
}
