// Screen strings of the chat quick message row (flowgate.v02test.0072), merged under `gui.quickMessages`.
// Card 3 (add / edit / delete / reorder) adds its own keys here.

const ko = {
  row_label: '퀵메시지',
  empty: '등록된 퀵메시지가 없습니다',
  tip_sticky: '[빨강 · 계속] {text}',
  tip_once: '[파랑 · 1회] {text}',
}

const en: typeof ko = {
  row_label: 'Quick messages',
  empty: 'No quick messages yet',
  tip_sticky: '[Red · Keep on] {text}',
  tip_once: '[Blue · Once] {text}',
}

const ja: typeof ko = {
  row_label: 'クイックメッセージ',
  empty: '登録されたクイックメッセージはありません',
  tip_sticky: '[赤 · 継続] {text}',
  tip_once: '[青 · 1回] {text}',
}

export const QUICK_MESSAGES_MESSAGES = { ko, en, ja } as const

/** Merges the quick message strings into the global i18n (merging again is harmless). */
export function installQuickMessages(composer: { mergeLocaleMessage(locale: string, message: Record<string, unknown>): void }): void {
  for (const locale of ['ko', 'en', 'ja'] as const) {
    composer.mergeLocaleMessage(locale, { gui: { quickMessages: QUICK_MESSAGES_MESSAGES[locale] } })
  }
}
