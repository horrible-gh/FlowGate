// (A) Retained — 0394 T0016 / NR0003 §6.3 "Guards for global invariants".
// flowgate.v02gui.0024: after removing v0.1 (client/), move this check to client_gui as well.
// Check matching ko/en/ja keys and placeholders, and wording rules in the shared catalog (src/shared/i18n).
import en from '@shared/i18n/en'
import ja from '@shared/i18n/ja'
import ko from '@shared/i18n/ko'

type Messages = Record<string, unknown>

function flattenMessages(
  messages: Messages,
  prefix = '',
  result = new Map<string, string>(),
): Map<string, string> {
  for (const [key, value] of Object.entries(messages)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      flattenMessages(value as Messages, path, result)
    } else {
      result.set(path, String(value))
    }
  }
  return result
}

function placeholders(message: string): string[] {
  return [...message.matchAll(/\{([^}]+)\}/g)]
    .map((match) => match[1])
    .sort()
}

function koreanSyllableKeys(messages: Map<string, string>): string[] {
  return [...messages.entries()]
    .filter(([, value]) => /[가-힣]/.test(value))
    .map(([key]) => key)
}

// 0399 M0020 rejection — displayed text must not contain decorative symbols such as circled characters (㉮ ㈀ ① Ⓐ …).
const ENCLOSED_SYMBOL = /[①-⓿㈀-㋿]/

function enclosedSymbolKeys(messages: Map<string, string>): string[] {
  return [...messages.entries()]
    .filter(([, value]) => ENCLOSED_SYMBOL.test(value))
    .map(([key]) => key)
}

describe('shared i18n locales', () => {
  const locales = {
    ko: flattenMessages(ko),
    en: flattenMessages(en),
    ja: flattenMessages(ja),
  }
  const referenceKeys = [...locales.ko.keys()].sort()

  it.each(Object.entries(locales))('%s has the same translation keys as ko', (_, messages) => {
    expect([...messages.keys()].sort()).toEqual(referenceKeys)
  })

  it.each(Object.entries(locales))('%s preserves interpolation placeholders', (_, messages) => {
    for (const key of referenceKeys) {
      expect(placeholders(messages.get(key) ?? ''), key).toEqual(
        placeholders(locales.ko.get(key) ?? ''),
      )
    }
  })

  it.each([
    ['en', locales.en],
    ['ja', locales.ja],
  ])('%s has no Korean syllable leakage', (_, messages) => {
    expect(koreanSyllableKeys(messages)).toEqual([])
  })

  it.each(Object.entries(locales))('%s uses no enclosed decorative symbols', (_, messages) => {
    expect(enclosedSymbolKeys(messages)).toEqual([])
  })
})
