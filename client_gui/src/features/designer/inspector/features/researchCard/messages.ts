// flowgate.v02.0051 stage 2 — 조사 card strings, merged under `gui.inspector.researchCard` (D#1 §5.5).
// The three locales keep the same keys (structure.0048 B12). The dot wordings of a document state are
// the Frame's (gui.designer.inspector.review_status / slot_state).
// flowgate.v02.0053 T#5 — `empty.*` is the hollow dot's tooltip on a line without a document (no "not
// yet" text on the line, no ＋).
export default {
  ko: {
    slot: {
      instruction: '지시서',
      report: '레포트',
    },
    empty: {
      after_run: '실행 후',
      unconnected: '연결 전',
    },
  },
  en: {
    slot: {
      instruction: 'Instruction',
      report: 'Report',
    },
    empty: {
      after_run: 'After run',
      unconnected: 'Not linked',
    },
  },
  ja: {
    slot: {
      instruction: '指示書',
      report: 'レポート',
    },
    empty: {
      after_run: '実行後',
      unconnected: '未連携',
    },
  },
}
