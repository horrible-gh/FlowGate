// flowgate.v02.0048 T#13 — WP summary strings, merged under `gui.inspector.workPlan` (D#1 §5.5). The three
// locales keep the same keys (structure.0048 B12). The section only reads, so it has no disabling reason.
// flowgate.v02.0056 F1 (0056 CH §5) — the details' strings (rev, totals, provider assignment, per-card rows,
// "where to edit") are gone with the details; what stays under the lines is the progress and the warning.
export default {
  ko: {
    // flowgate.v02.0051 stage 2 — the v7 slot lines and header actions.
    // flowgate.v02.0074 T#1 — the author badge (R §1.2) and the no-cards text (R §1.1) are gone.
    slot: {
      plan: '작업계획',
      deploy: '전개',
    },
    slot_empty: {
      deploy: '전개 전',
    },
    deploy: {
      label: '전개',
      deploy: '⇉ 전개',
      redeploy: '⇉ 재전개 ▾',
      unconnected: 'WP 전개는 아직 연결되지 않았습니다 (0048 D#1 §8.4).',
    },
    title: '작업계획',
    progress: '시작한 카드 {started}/{cards}',
    unassigned: '담당 AI 미지정 Step {count}개',
    error: {
      unreadable: '이 작업계획은 표로 읽을 수 없습니다. 문서를 열어 원문을 확인하세요.',
      invalid: '작업계획 문서가 아닙니다.',
      forbidden: '작업계획을 볼 권한이 없습니다.',
      not_found: '작업계획 문서를 찾을 수 없습니다.',
      network: '서버에 연결하지 못했습니다.',
      other: '작업계획을 읽지 못했습니다.',
    },
  },
  en: {
    slot: {
      plan: 'Work plan (WP)',
      deploy: 'Deployment',
    },
    slot_empty: {
      deploy: 'Before deployment',
    },
    deploy: {
      label: 'Deploy',
      deploy: '⇉ Deploy',
      redeploy: '⇉ Redeploy ▾',
      unconnected: 'Deploying a work plan is not connected yet (0048 D#1 §8.4).',
    },
    title: 'Work plan',
    progress: 'Cards started {started}/{cards}',
    unassigned: '{count} Step(s) without an AI',
    error: {
      unreadable: 'This work plan cannot be read as a table. Open the document for the raw text.',
      invalid: 'This is not a work plan document.',
      forbidden: 'You do not have permission to view this work plan.',
      not_found: 'The work plan document was not found.',
      network: 'Could not reach the server.',
      other: 'Could not read the work plan.',
    },
  },
  ja: {
    slot: {
      plan: '作業計画',
      deploy: '展開',
    },
    slot_empty: {
      deploy: '展開前',
    },
    deploy: {
      label: '展開',
      deploy: '⇉ 展開',
      redeploy: '⇉ 再展開 ▾',
      unconnected: 'WP の展開はまだ連携されていません（0048 D#1 §8.4）。',
    },
    title: '作業計画',
    progress: '開始したカード {started}/{cards}',
    unassigned: '担当 AI 未指定の Step {count}件',
    error: {
      unreadable: 'この作業計画は表として読めません。文書を開いて原文を確認してください。',
      invalid: '作業計画の文書ではありません。',
      forbidden: '作業計画を表示する権限がありません。',
      not_found: '作業計画の文書が見つかりません。',
      network: 'サーバーに接続できませんでした。',
      other: '作業計画を読めませんでした。',
    },
  },
}
