// flowgate.v02.0088 F01~F05: the Project screens translate their own words, so their specs mount them with the app's
// i18n (the shared instance with the gui catalog registered, as main.ts does). Every test starts in ko — the
// existing assertions read the ko words — and a test that checks another language switches it itself.
import { config } from '@vue/test-utils'
import { beforeEach } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'

registerAppMessages(i18n)
if (!config.global.plugins.includes(i18n)) config.global.plugins.push(i18n)

export type TestLocale = 'ko' | 'ja' | 'en'

export function setTestLocale(locale: TestLocale): void {
  ;(i18n.global.locale as unknown as { value: string }).value = locale
}

beforeEach(() => setTestLocale('ko'))
