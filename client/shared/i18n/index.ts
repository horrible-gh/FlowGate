import { createI18n } from 'vue-i18n'
import en from './en'
import ja from './ja'
import ko from './ko'

const supportedLocales = ['en', 'ko', 'ja'] as const

type SupportedLocale = (typeof supportedLocales)[number]

const isSupportedLocale = (value: string): value is SupportedLocale =>
  supportedLocales.includes(value as SupportedLocale)

const getBrowserLocale = (): SupportedLocale => {
  const lang = navigator.language?.split('-')[0] ?? 'ko'
  return isSupportedLocale(lang) ? lang : 'ko'
}

const getInitialLocale = (): SupportedLocale => {
  const stored = localStorage.getItem('preferred_locale')
  if (stored && isSupportedLocale(stored)) {
    return stored
  }
  return getBrowserLocale()
}

export default createI18n({
  legacy: false,
  locale: getInitialLocale(),
  fallbackLocale: 'ko',
  messages: { ko, en, ja },
})
