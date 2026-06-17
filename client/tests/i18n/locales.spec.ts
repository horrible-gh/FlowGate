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
