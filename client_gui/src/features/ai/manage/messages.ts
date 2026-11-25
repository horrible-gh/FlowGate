// v02test.0096 F96-3: words of the AI 관리 tabs shared by the full screen and the taskbar window (기타 설정, 방침, window
// title, taskbar menu cards). Merged into the global messages like the policy box's own (policy/messages.ts).
const messages = {
  ko: {
    window: 'AI 관리',
    window_title: 'AI 관리 · {tab}',
    tabs: { other: '기타 설정', policy: '방침' },
    other: {
      system: '시스템 (관리자)',
      mine: '내 값',
      repeat_hint: '검수 / 재시작 등 반복형 선택지의 상한',
      title_hint: '새 플로우·카드·대화의 제목을 지을 AI',
      system_repeat: '최대 반복 횟수 (시스템)',
      system_title: '제목 짓기 AI (시스템)',
      admin_only: '관리자만 볼 수 있습니다',
      unavailable: '불러오지 못했습니다',
      note: '관리자가 아니면 시스템 열은 읽기 전용입니다. 내 값은 시스템 상한을 넘을 수 없습니다.',
    },
    policy: { heading: '방침', sections_aria: '방침 항목', question: '질의' },
    cards: { other: '기타 설정', other_desc: '반복 횟수 · 제목 짓기 AI', policy: '방침', policy_desc: '질의 등 AI가 따를 기준' },
  },
  en: {
    window: 'AI management',
    window_title: 'AI management · {tab}',
    tabs: { other: 'Other settings', policy: 'Policies' },
    other: {
      system: 'System (admin)',
      mine: 'Mine',
      repeat_hint: 'Upper limit of repeat-type choices such as review and restart',
      title_hint: 'The AI that names new flows, cards and chats',
      system_repeat: 'Max repeat count (system)',
      system_title: 'Title AI (system)',
      admin_only: 'Visible to administrators only',
      unavailable: 'Could not load',
      note: 'The system column is read-only unless you are an administrator. Your value cannot pass the system limit.',
    },
    policy: { heading: 'Policies', sections_aria: 'Policy sections', question: 'Questions' },
    cards: { other: 'Other settings', other_desc: 'Repeat count · title AI', policy: 'Policies', policy_desc: 'Rules the AI follows, such as questions' },
  },
  ja: {
    window: 'AI 管理',
    window_title: 'AI 管理 · {tab}',
    tabs: { other: 'その他の設定', policy: '方針' },
    other: {
      system: 'システム（管理者）',
      mine: '自分の値',
      repeat_hint: 'レビュー / 再実行など繰り返し型の選択肢の上限',
      title_hint: '新しいフロー・カード・会話のタイトルを付けるAI',
      system_repeat: '最大繰り返し回数（システム）',
      system_title: 'タイトル生成AI（システム）',
      admin_only: '管理者のみ表示できます',
      unavailable: '読み込めませんでした',
      note: '管理者でなければシステム列は読み取り専用です。自分の値はシステムの上限を超えられません。',
    },
    policy: { heading: '方針', sections_aria: '方針の項目', question: '質問' },
    cards: { other: 'その他の設定', other_desc: '繰り返し回数 · タイトル生成AI', policy: '方針', policy_desc: '質問などAIが従う基準' },
  },
}

export function installAiManageMessages(composer: { mergeLocaleMessage(locale: string, message: Record<string, unknown>): void }) {
  for (const locale of ['ko', 'en', 'ja'] as const) composer.mergeLocaleMessage(locale, { gui: { aiManage: messages[locale] } })
}
