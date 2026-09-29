import en from './en'
import ja from './ja'
import ko from './ko'

// T0011 §13: v0.2 reuses the shared i18n instance (client/shared/i18n: locale detection,
// `preferred_locale`, fallback) instead of creating a second one, but keeps its own screen
// strings here under a `gui` namespace rather than adding them to the shared v0.1 catalogs.
// Only the strings the /app shell, Dashboard and placeholders need live in this bundle.
export const APP_MESSAGES = { ko, en, ja } as const

interface MergeableI18n {
  global: { mergeLocaleMessage: (locale: string, message: Record<string, unknown>) => void }
}

export function registerAppMessages(i18n: unknown): void {
  const target = i18n as MergeableI18n
  for (const [locale, messages] of Object.entries(APP_MESSAGES)) {
    target.global.mergeLocaleMessage(locale, { gui: messages })
  }
}
