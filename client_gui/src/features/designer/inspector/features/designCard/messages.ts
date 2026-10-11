// flowgate.v02.0051 stage 2 — 설계 card strings, merged under `gui.inspector.designCard` (D#1 §5.5).
export default {
  ko: {
    tbd: {
      title: '설계 카드는 아직 비워 두었습니다.',
      text: '설계는 결과 문서가 여러 장(D · L · P · DB)일 수 있어 “문서 슬롯 1개” 규칙의 예외인지 작업계획과 함께 정합니다. 여기에는 결정이 필요 없는 것만 보입니다.',
    },
    slot: {
      document: '설계 문서',
    },
    type: {
      DS: '설계 요약',
      D: '기본 설계',
      P: '프로토콜',
      L: '로직',
      DB: 'DB 설계',
    },
    empty: {
      not_yet: '아직 없음',
    },
    write_unconnected: '작성 — 아직 연결되지 않음',
  },
  en: {
    tbd: {
      title: 'The design card is still left empty.',
      text: 'A design may produce several sheets (D · L · P · DB), so whether it is an exception to the one-slot rule is decided together with the work plan. Only what needs no decision is shown here.',
    },
    slot: {
      document: 'Design document',
    },
    type: {
      DS: 'Design summary',
      D: 'Basic design',
      P: 'Protocol',
      L: 'Logic',
      DB: 'DB design',
    },
    empty: {
      not_yet: 'Not yet',
    },
    write_unconnected: 'Write — not connected yet',
  },
  ja: {
    tbd: {
      title: '設計カードはまだ空のままです。',
      text: '設計は成果文書が複数（D · L · P · DB）になり得るため、「文書スロット1つ」の例外かどうかは作業計画と合わせて決めます。ここには決定が不要なものだけを表示します。',
    },
    slot: {
      document: '設計文書',
    },
    type: {
      DS: '設計サマリー',
      D: '基本設計',
      P: 'プロトコル',
      L: 'ロジック',
      DB: 'DB設計',
    },
    empty: {
      not_yet: 'まだなし',
    },
    write_unconnected: '作成 — まだ連携されていません',
  },
}
