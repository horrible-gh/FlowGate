// flowgate.v02.0051 stage 2 — 설계 card strings, merged under `gui.inspector.designCard` (D#1 §5.5).
// flowgate.v02.0053 T#5 (0053 CH) — the lines are 지시서 · 레포트 like 조사 · 작업; no type code in a name, no
// "설명 대기" note. `empty.*` is the hollow dot's tooltip on a line without a document.
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
