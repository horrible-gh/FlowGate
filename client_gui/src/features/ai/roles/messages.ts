import settingsMessages from '@/features/designer/inspector/features/settings/messages'
const messages = {
  ko: { title: 'AI 역할 기본값', setup: 'AI 설정', instruction: '지시 AI', execution: '실행 AI', review: '검수 AI', not_used: '사용하지 않음', unset: '미설정', legacy: '이전 설정', changed: '설정 변경됨', card_review: '카드 검수 역할 값', unavailable: '카드 AI 역할 설정을 사용할 수 없습니다' },
  en: { title: 'AI role defaults', setup: 'AI setup', instruction: 'Instruction AI', execution: 'Execution AI', review: 'Review AI', not_used: 'Do not use', unset: 'Not configured', legacy: 'Legacy setting', changed: 'Settings changed', card_review: 'Card review role value', unavailable: 'Card AI role settings are unavailable' },
  ja: { title: 'AI役割の既定値', setup: 'AI設定', instruction: '指示AI', execution: '実行AI', review: 'レビューAI', not_used: '使用しない', unset: '未設定', legacy: '従来の設定', changed: '設定変更あり', card_review: 'カードのレビュー役割の値', unavailable: 'カードのAI役割設定を利用できません' },
}
export default messages
export function installAiRoleMessages(composer: { mergeLocaleMessage(locale: string, message: Record<string, unknown>): void }) {
  for (const locale of ['ko', 'en', 'ja'] as const) composer.mergeLocaleMessage(locale, { gui: { aiRoles: messages[locale], inspector: { settings: settingsMessages[locale] } } })
}
