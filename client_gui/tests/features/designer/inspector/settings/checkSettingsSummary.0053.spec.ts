/** D0007 §8-10 replaces the old summary/usage contract with the mockup settings model. */
import { describe, expect, it } from 'vitest'
import { checkSettingsErrorKey, checkSettingsView } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import messages from '@/features/designer/inspector/features/checkSettings/messages'
const base = { card: true, node: { locked: false, fields: {} }, pending: {}, branches: ['main', 'release'], readOnly: null, placing: false, disposed: false, failure: null } as const
const field = (value: unknown) => ({ value })
describe('gate settings model — D §8-10', () => {
  it('keeps unselected/null values and exposes only origin, with archive off by default', () => {
    expect(checkSettingsView(base)).toMatchObject({ usage: null, scope: null, target: null, targetOptions: ['origin'], archive: false, push: null, keepBranch: null, reason: null })
  })
  it('never turns old branch targets into remote choices and does not use legacy usage', () => {
    const view = checkSettingsView({ ...base, node: { locked: false, fields: { usage: field('end'), merge_mode: field('hold'), merge_target: field('main') } } })
    expect(view).toMatchObject({ usage: null, scope: 'hold', target: null, targetOptions: ['origin'] })
  })
  it('shows the pending field only until a failed save restores the confirmed value and failure', () => {
    const input = { ...base, node: { locked: false, fields: { archive: field(false), merge_target: field('origin') } } }
    expect(checkSettingsView({ ...input, pending: { archive: true } }).archive).toBe(true)
    expect(checkSettingsView({ ...input, failure: { field: 'archive', kind: 'locked' } })).toMatchObject({ archive: false, target: 'origin', failures: { archive: 'failure.locked' } })
  })
  it('keeps the confirmed values and field error after a failed network save has no replacement read', () => {
    const node = { locked: false, fields: { archive: field(false), merge_mode: field('merge'), merge_target: field('origin'), push: field(true) } }
    const view = checkSettingsView({ ...base, node, pending: {}, failure: { field: 'archive', kind: 'network' } })
    expect(view).toMatchObject({ archive: false, scope: 'merge', target: 'origin', push: true, failures: { archive: 'failure.network' } })
  })
  it('uses the server lock for a run revision and keeps ownership conflicts read only', () => {
    expect(checkSettingsView({ ...base, readOnly: 'revision' }).reason).toBeNull()
    expect(checkSettingsView({ ...base, node: { locked: true, fields: {} } }).reason).toBe('reason.locked')
    expect(checkSettingsView({ ...base, readOnly: 'conflict' }).reason).toBe('reason.read_only_conflict')
  })
  it('all shown labels and read failures have ko/en/ja text', () => {
    for (const locale of ['ko', 'en', 'ja'] as const) {
      expect(messages[locale].archive).toBeTruthy()
      for (const kind of ['locked', 'disposed', 'unavailable', 'invalid', 'forbidden', 'not_found', 'network', 'other'] as const) {
        const key = checkSettingsErrorKey(kind).split('.')[1]
        expect(typeof (messages[locale].error as Record<string, string>)[key]).toBe('string')
      }
    }
  })
})
