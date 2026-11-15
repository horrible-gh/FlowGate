// (A) 유지 — 0394 T0016 / NR0003 §6.3 "전역 불변식을 지키는 가드".
// flowgate.v02gui.0024: v0.1(client/)이 없어지면서 이 검사도 client_gui 기준으로 옮겼습니다.
// 공용 카탈로그(src/shared/i18n)의 ko/en/ja 키·자리표시자 일치와 문구 규칙을 봅니다.
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

// 0399 M0020 반려 — 화면에 나가는 문구에는 동그라미 글자(㉮ ㈀ ① Ⓐ …) 같은 장식 기호를 쓰지 않는다.
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
