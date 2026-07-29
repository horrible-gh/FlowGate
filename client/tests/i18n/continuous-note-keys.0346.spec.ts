import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import en from '../../shared/i18n/en'
import ja from '../../shared/i18n/ja'
import ko from '../../shared/i18n/ko'

// flowgate.default.0346 T0005 §2-1 항목 8 / 완료 기준 "i18n 3개 언어(ko/en/ja) 키 추가 확인".
//
// Why a dedicated spec instead of leaning on tests/i18n/locales.spec.ts: that suite is
// RED on this branch's base for an unrelated reason (main.git_finalize.archive.* keys are
// referenced by a component but missing from every locale — reproduced on a clean stash of
// this worktree), so it cannot serve as the gate for THIS group's keys. These assertions are
// scoped to the four keys T0005 introduced, so they stay meaningful once that base failure
// is fixed elsewhere.
//
// The failure this guards against is the quiet one: a key added to ko.ts only. Vue-i18n
// falls back to the key path, so a ja/en user sees the literal string
// "main.continuous_work.tab_message" on the tab — and no test would notice.

const LOCALES = { ko, en, ja } as const

const NEW_KEYS = [
  'tab_message',
  'message_default_label',
  'message_default_placeholder',
  'message_step_placeholder',
] as const

function continuousWork(locale: (typeof LOCALES)[keyof typeof LOCALES]): Record<string, unknown> {
  return (locale as any).main.continuous_work as Record<string, unknown>
}

describe('[전달멘트] i18n keys (0346 T0005)', () => {
  it.each(Object.keys(LOCALES))('%s defines every new key with a non-empty string', (name) => {
    const block = continuousWork(LOCALES[name as keyof typeof LOCALES])
    for (const key of NEW_KEYS) {
      expect(block, `${name}.main.continuous_work.${key} is missing`).toHaveProperty(key)
      const value = block[key]
      expect(typeof value, `${name}.${key} must be a string`).toBe('string')
      expect((value as string).trim(), `${name}.${key} must not be blank`).not.toBe('')
    }
  })

  it('places the new tab label beside the existing two, in all three locales', () => {
    // The tab bar renders tab_basic / tab_provider / tab_message; three distinct labels are
    // what makes the third tab identifiable at all.
    for (const [name, locale] of Object.entries(LOCALES)) {
      const block = continuousWork(locale)
      const labels = [block.tab_basic, block.tab_provider, block.tab_message]
      expect(new Set(labels).size, `${name}: tab labels must be distinct`).toBe(3)
    }
  })

  it('is actually referenced by ContinuousWorkDialog.vue', () => {
    // A key trio that exists in the locale files but is never rendered would pass the checks
    // above while the tab shows nothing.
    const source = readFileSync(
      resolve(__dirname, '../../src/main/components/ContinuousWorkDialog.vue'),
      'utf8',
    )
    for (const key of NEW_KEYS) {
      expect(source, `ContinuousWorkDialog.vue does not use ${key}`).toContain(
        `main.continuous_work.${key}`,
      )
    }
  })
})
