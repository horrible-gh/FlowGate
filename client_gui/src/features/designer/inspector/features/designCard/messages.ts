// flowgate.v02.0051 stage 2 — 설계 card strings, merged under `gui.inspector.designCard` (D#1 §5.5).
// flowgate.v02.0053 T#5 (0053 CH) — the lines are 지시서 · 레포트 like 조사 · 작업; no type code in a name, no
// "설명 대기" note. `empty.*` is the hollow dot's tooltip on a line without a document.
// flowgate.v02.0074 T#3 (R §3) — the two lines are named 「설계 지시」 (DS) · 「설계서」 (D / P / L / DB), as the
// work plan edit window names a design sheet; the 조사 · 작업 · 테스트 cards keep 지시서 · 레포트.
export default {
  ko: {
    slot: {
      instruction: '설계 지시',
      report: '설계서',
    },
    empty: {
      after_run: '실행 후',
      unconnected: '연결 전',
    },
  },
  en: {
    slot: {
      instruction: 'Design instruction',
      report: 'Design document',
    },
    empty: {
      after_run: 'After run',
      unconnected: 'Not linked',
    },
  },
  ja: {
    slot: {
      instruction: '設計指示',
      report: '設計書',
    },
    empty: {
      after_run: '実行後',
      unconnected: '未連携',
    },
  },
}
