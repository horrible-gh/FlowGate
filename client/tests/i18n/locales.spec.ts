// (A) 유지 — 0394 T0016 / NR0003 §6.3 "전역 불변식을 지키는 가드".
// 여기서 읽는 것은 어느 한 파일의 텍스트 배치가 아니라 client/src 전체다. (1) 로케일에 없는
// t() 키가 어디에도 없을 것, (2) 하드코딩 토스트 문구가 어디에도 없을 것 — 둘 다 소스를
// 전수로 훑어야만 지킬 수 있다. 마운트는 그때 마운트한 컴포넌트까지밖에 보지 못한다.
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import en from '../../shared/i18n/en'
import ja from '../../shared/i18n/ja'
import ko from '../../shared/i18n/ko'

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

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return sourceFiles(path)
    return /\.(?:js|ts|vue)$/.test(entry.name) ? [path] : []
  })
}

function koreanSyllableKeys(messages: Map<string, string>): string[] {
  return [...messages.entries()]
    .filter(([, value]) => /[가-힣]/.test(value))
    .map(([key]) => key)
}

// 0399 M0020 반려 — "이 이상한 기호는 뭐하러 쓴거야? ㉮㉯㉰".
// 화면에 나가는 문구에는 동그라미 글자(㉮ ㈀ ① Ⓐ …) 같은 장식 기호를 쓰지 않는다.
// 번호가 필요하면 '1.' '2.' 처럼 보통 숫자로 적는다.
const ENCLOSED_SYMBOL = /[\u2460-\u24FF\u3200-\u32FF]/

function enclosedSymbolKeys(messages: Map<string, string>): string[] {
  return [...messages.entries()]
    .filter(([, value]) => ENCLOSED_SYMBOL.test(value))
    .map(([key]) => key)
}

describe('i18n locales', () => {
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

  it('defines every statically referenced translation key', () => {
    const referencedKeys = sourceFiles(resolve(__dirname, '../../src')).flatMap((file) => {
      const source = readFileSync(file, 'utf8')
      return [...source.matchAll(/(?:\bt|\$t)\(\s*['"]([^'"]+)['"]/g)]
        .map((match) => match[1])
    })

    expect([...new Set(referencedKeys)].filter((key) => !locales.ko.has(key))).toEqual([])
  })

  it('does not use hardcoded toast messages', () => {
    const hardcodedToasts = sourceFiles(resolve(__dirname, '../../src')).flatMap((file) => {
      const source = readFileSync(file, 'utf8')
      return source.split(/\r?\n/).flatMap((line, index) => {
        const hasLiteralMessage = /showToast\(\s*['"`]/.test(line)
        const hasLiteralFallback = /showToast\([^)]*\?\?\s*['"`]/.test(line)
        return hasLiteralMessage || hasLiteralFallback
          ? [`${file}:${index + 1}`]
          : []
      })
    })

    expect(hardcodedToasts).toEqual([])
  })
})