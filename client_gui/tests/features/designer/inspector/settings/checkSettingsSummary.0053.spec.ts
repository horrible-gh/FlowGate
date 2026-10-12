/**
 * flowgate.v02.0053 T#7 — the check card's Git summary line as a table (v7 scopeSummary of the values shown: the
 * merge target and push as chosen, a value not chosen as not set, `→ 그룹 종료` on a group-ending card), and the
 * read failure keys of the requirement and check cards — each key a ko / en / ja string.
 */
import { describe, expect, it } from 'vitest'
import { checkSettingsErrorKey, checkSettingsSummary, type CheckSettingsView } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import checkMessages from '@/features/designer/inspector/features/checkSettings/messages'
import { requirementSettingsErrorKey } from '@/features/designer/inspector/features/requirementSettings/requirementSettingsModel'
import requirementMessages from '@/features/designer/inspector/features/requirementSettings/messages'

type Shown = Pick<CheckSettingsView, 'scope' | 'usage' | 'target' | 'push'>

const shown = (fields: Partial<Shown>): Shown => ({ scope: 'merge', usage: 'mid', target: null, push: null, ...fields })

const at = (messages: Record<string, unknown>, key: string) =>
  key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], messages)

const KINDS = ['locked', 'disposed', 'unavailable', 'invalid', 'forbidden', 'not_found', 'network', 'other'] as const

describe('checkSettingsSummary', () => {
  it.each([
    [{ target: 'main', push: true }, 'git.summary_merge.push', { target: 'main' }],
    [{ target: 'release', push: false }, 'git.summary_merge.local', { target: 'release' }],
    [{ target: 'release', push: null }, 'git.summary_merge.push_unset', { target: 'release' }],
    [{ target: null, push: true }, 'git.summary_merge_untargeted.push', {}],
    [{ target: null, push: false }, 'git.summary_merge_untargeted.local', {}],
    [{ target: null, push: null }, 'git.summary_merge_untargeted.push_unset', {}],
  ] as const)('병합 with %o: its line names the target and push shown', (fields, key, params) => {
    expect(checkSettingsSummary(shown(fields))).toEqual({ key, params, end: false })
    expect(checkSettingsSummary(shown({ ...fields, usage: 'end' }))).toEqual({ key, params, end: true })
  })

  it('커밋만, 보류 and nothing chosen keep their v7 lines; 보류 on a group-ending card has its own', () => {
    expect(checkSettingsSummary(shown({ scope: 'commit', target: 'main', push: true }))).toEqual({ key: 'git.summary_commit', params: {}, end: false })
    expect(checkSettingsSummary(shown({ scope: 'commit', usage: 'end' }))).toEqual({ key: 'git.summary_commit', params: {}, end: true })
    expect(checkSettingsSummary(shown({ scope: 'hold' }))).toEqual({ key: 'git.summary_hold', params: {}, end: false })
    expect(checkSettingsSummary(shown({ scope: 'hold', usage: 'end' }))).toEqual({ key: 'git.summary_hold_end', params: {}, end: false })
    expect(checkSettingsSummary(shown({ scope: null, usage: 'end', target: 'main', push: true }))).toEqual({ key: 'git.summary_none', params: {}, end: false })
  })

  it('every summary key is a ko / en / ja string; the 병합 lines take the target, the 종료 line the summary', () => {
    const keys = [
      ...['push', 'local', 'push_unset'].flatMap((push) => [`git.summary_merge.${push}`, `git.summary_merge_untargeted.${push}`]),
      'git.summary_commit', 'git.summary_hold', 'git.summary_hold_end', 'git.summary_none', 'git.summary_end',
    ]
    for (const locale of ['ko', 'en', 'ja'] as const) {
      for (const key of keys) expect(typeof at(checkMessages[locale], key), `${locale} ${key}`).toBe('string')
      for (const push of ['push', 'local', 'push_unset']) {
        expect(at(checkMessages[locale], `git.summary_merge.${push}`)).toContain('{target}')
        expect(at(checkMessages[locale], `git.summary_merge_untargeted.${push}`)).not.toContain('{target}')
      }
      expect(at(checkMessages[locale], 'git.summary_end')).toContain('{summary}')
      // No branch of v7's sample is fixed in the wording any more.
      for (const push of ['push', 'local', 'push_unset']) expect(at(checkMessages[locale], `git.summary_merge.${push}`)).not.toMatch(/\bmain\b/)
    }
  })
})

describe('read failure keys of the requirement and check cards', () => {
  it.each(KINDS)('%s: its key, a ko / en / ja string', (kind) => {
    const key = ['forbidden', 'not_found', 'network', 'unavailable'].includes(kind) ? `error.${kind}` : 'error.other'
    expect(checkSettingsErrorKey(kind)).toBe(key)
    expect(requirementSettingsErrorKey(kind)).toBe(key)
    for (const locale of ['ko', 'en', 'ja'] as const) {
      expect(typeof at(checkMessages[locale], key), `check ${locale} ${key}`).toBe('string')
      expect(typeof at(requirementMessages[locale], key), `requirement ${locale} ${key}`).toBe('string')
    }
  })
})
