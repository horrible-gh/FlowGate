// v02test.0096 F96-3: words of the AI 관리 tabs shared by the full screen and the taskbar window (기타 설정, 방침, window
// title, taskbar menu cards). Merged into the global messages like the policy box's own (policy/messages.ts).
const messages = {
  ko: {
    window: 'AI 관리',
    window_title: 'AI 관리 · {tab}',
    tabs: { other: '기타 설정', policy: '방침' },
    other: {
      repeat_hint: '검수 / 재시작 등 반복형 선택지의 상한',
      title_hint: '새 플로우·카드·대화의 제목을 지을 AI',
      unavailable: '불러오지 못했습니다',
    },
    policy: { heading: '방침', sections_aria: '방침 항목', question: '질의' },
    cards: { other: '기타 설정', other_desc: '반복 횟수 · 제목 짓기 AI', policy: '방침', policy_desc: '질의 등 AI가 따를 기준' },
  },
  en: {
    window: 'AI management',
    window_title: 'AI management · {tab}',
    tabs: { other: 'Other settings', policy: 'Policies' },
    other: {
      repeat_hint: 'Upper limit of repeat-type choices such as review and restart',
      title_hint: 'The AI that names new flows, cards and chats',
      unavailable: 'Could not load',
    },
    policy: { heading: 'Policies', sections_aria: 'Policy sections', question: 'Questions' },
    cards: { other: 'Other settings', other_desc: 'Repeat count · title AI', policy: 'Policies', policy_desc: 'Rules the AI follows, such as questions' },
  },
  ja: {
    window: 'AI 管理',
    window_title: 'AI 管理 · {tab}',
    tabs: { other: 'その他の設定', policy: '方針' },
    other: {
      repeat_hint: 'レビュー / 再実行など繰り返し型の選択肢の上限',
      title_hint: '新しいフロー・カード・会話のタイトルを付けるAI',
      unavailable: '読み込めませんでした',
    },
    policy: { heading: '方針', sections_aria: '方針の項目', question: '質問' },
    cards: { other: 'その他の設定', other_desc: '繰り返し回数 · タイトル生成AI', policy: '方針', policy_desc: '質問などAIが従う基準' },
  },
}

export function installAiManageMessages(composer: { mergeLocaleMessage(locale: string, message: Record<string, unknown>): void }) {
  for (const locale of ['ko', 'en', 'ja'] as const) composer.mergeLocaleMessage(locale, { gui: { aiManage: messages[locale] } })
}
