// flowgate.v02.0051 stage 2 — 테스트 card settings strings, merged under `gui.inspector.testRunSettings`.
// flowgate.v02.0053 T#7 — the 실행 block only (지시서 · 검수 뒤 진행 are the settings feature's); no code on
// screen (TSC / TSR, automated / manual / external) and the explanations at tooltip length.
export default {
  ko: {
    run: {
      title: '실행',
      tbd: '수동 · 외부 흐름 그리지 않음',
      tbd_title: '수동 · 외부 케이스의 결과를 받는 흐름은 레포트 계약 정리 뒤',
      mode: '실행 방식',
      mode_hint: '카드 전체가 아니라 케이스마다 정함',
      per_case: '케이스별',
      decided_in_cases: '시나리오에서 정함',
      automated: '자동 {n}',
      manual: '수동 {n}',
      external: '외부 {n}',
      unknown: '—',
    },
  },
  en: {
    run: {
      title: 'Execution',
      tbd: 'Manual · external flow not drawn',
      tbd_title: 'How manual · external case results come in is drawn after the report contract',
      mode: 'Execution mode',
      mode_hint: 'Set per case, not for the whole card',
      per_case: 'Per case',
      decided_in_cases: 'set in the scenario',
      automated: 'Automated {n}',
      manual: 'Manual {n}',
      external: 'External {n}',
      unknown: '—',
    },
  },
  ja: {
    run: {
      title: '実行',
      tbd: '手動 · 外部の流れは未作図',
      tbd_title: '手動 · 外部ケースの結果を受け取る流れはレポート契約の整理後',
      mode: '実行方式',
      mode_hint: 'カード全体ではなくケースごとに決める',
      per_case: 'ケースごと',
      decided_in_cases: 'シナリオで決める',
      automated: '自動 {n}',
      manual: '手動 {n}',
      external: '外部 {n}',
      unknown: '—',
    },
  },
}
