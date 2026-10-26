/** 0098 D0004 §6: local branch choices, inheritance and receipt-only summaries. */
import { describe, expect, it } from 'vitest'
import { checkSettingsErrorKey, checkSettingsView, checkControlFailure, checkOperationErrorKey, checkSettingsSummary } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import { toSettingsNode, type RunGateResult } from '@/features/document'
import messages from '@/features/designer/inspector/features/checkSettings/messages'
const field = (value: unknown, stored = false) => ({ value, stored, default: null, choices: null })
const entry = (branch: string | null, extra = {}) => ({ branch, checked: true, error: null, unmerge_supported: true, warnings: [], ...extra })
const node = (extra = {}) => toSettingsNode({ card_kind: 'check', settings_version: 4, fields: { merge_mode: field('merge'), merge_target_branch: field(null), push: field(false) },
  merge_target_options: ['feature/v0.2.0', 'main'], default_target_branch: 'feature/v0.2.0',
  merge_target_state: { status: 'ok', current: entry('feature/v0.2.0'), default: entry('feature/v0.2.0') }, ...extra })
const base = { card: true, node: node(), pending: {}, branches: ['origin', 'slot/ignored'], readOnly: null, placing: false, disposed: false, failure: null } as const
const receipt = (extra = {}) => ({ settings: { shown: { merge_target_branch: 'release' }, effective: { merge_mode: 'merge', push: true }, target_basis: { target_branch: 'release', unmerge_supported: false, warnings: ['unmerge_unsupported_for_non_base_target'] } }, ...extra }) as RunGateResult

describe('check settings branch contract — 0098', () => {
  it('uses only server branch names, displays the inherited branch without saving it', () => {
    expect(checkSettingsView(base)).toMatchObject({ usage: null, scope: 'merge', target: 'feature/v0.2.0', targetStored: false, targetOptions: ['feature/v0.2.0', 'main'], targetEnabled: true, archive: false, push: false })
    expect(checkSettingsView({ ...base, node: null }).targetOptions).toEqual([])
  })
  it('retains a stored branch independently of default changes and ignores old remote/usage values', () => {
    const saved = node({ fields: { usage: field('end', true), merge_mode: field('merge'), merge_target: field('origin', true), merge_target_branch: field('release', true) }, default_target_branch: 'new-base', merge_target_options: ['main'] })
    expect(checkSettingsView({ ...base, node: saved })).toMatchObject({ usage: null, target: 'release', targetStored: true, targetOptions: ['main'] })
  })
  it.each(['commit', 'hold'])('disables targets in %s while retaining the stored name', mode => {
    expect(checkSettingsView({ ...base, node: node({ fields: { merge_mode: field(mode), merge_target_branch: field('release', true) } }) })).toMatchObject({ target: 'release', targetEnabled: false, unmergeWarning: false })
  })
  it.each([['empty', 'target.empty'], ['failed', 'target.lookup_failed'], ['timeout', 'target.lookup_failed'], ['git_unavailable', 'target.git_unavailable']])('distinguishes %s from other lookup states', (status, lookupError) => {
    expect(checkSettingsView({ ...base, node: node({ merge_target_options: [], merge_target_state: { status, current: null, default: null } }) })).toMatchObject({ targetEnabled: false, lookupError })
  })
  it.each(['failed', 'timeout', 'git_unavailable'].flatMap(status => [false, true].map(stored => ({ status, stored }))))('keeps unchecked names without invalidating targets on $status (stored=$stored)', ({ status, stored }) => {
    const name = stored ? 'release' : 'feature/v0.2.0'
    const unchecked = { checked: false, error: null, unmerge_supported: null, warnings: [] }
    const saved = node({ fields: { merge_mode: field('merge'), merge_target_branch: field(stored ? name : null, stored) },
      merge_target_options: [], merge_target_state: { status, current: entry(name, unchecked), default: entry('feature/v0.2.0', unchecked) } })
    expect(checkSettingsView({ ...base, node: saved })).toMatchObject({ target: name, targetStored: stored, targetError: null,
      lookupError: status === 'git_unavailable' ? 'target.git_unavailable' : 'target.lookup_failed', targetEnabled: false, unmergeWarning: false })
  })
  it('keeps invalid stored names out of choices and never substitutes another inherited branch', () => {
    const missing = entry('gone', { error: 'merge_target_not_found' })
    const saved = node({ fields: { merge_mode: field('merge'), merge_target_branch: field('gone', true) }, merge_target_state: { status: 'ok', current: missing, default: missing } })
    expect(checkSettingsView({ ...base, node: saved })).toMatchObject({ target: 'gone', targetError: 'target.missing', targetOptions: ['feature/v0.2.0', 'main'], targetEnabled: true })
    expect(checkSettingsView({ ...base, node: { ...saved, fields: { merge_mode: field('merge'), merge_target_branch: field(null) } } })).toMatchObject({ target: null, targetError: 'target.missing', targetEnabled: true })
  })
  it('shows confirmed push until the complete server response arrives', () => {
    expect(checkSettingsView({ ...base, pending: { push: true } }).push).toBe(false)
    expect(checkSettingsView({ ...base, pending: { archive: true } }).archive).toBe(true)
    expect(checkSettingsView({ ...base, failure: { field: 'archive', kind: 'network' } })).toMatchObject({ archive: false, failures: { archive: 'failure.network' } })
  })
  it.each([
    ['merge_target_busy', 'busy'], ['merge_target_owner_mismatch', 'owner_mismatch'], ['merge_target_incoming_work_base', 'incoming'],
    ['merge_target_not_found', 'missing'], ['merge_target_invalid', 'invalid'], ['merge_target_remote_only', 'local_required'],
    ['merge_target_internal_slot', 'ineligible'], ['merge_target_same_branch', 'same_branch'], ['merge_target_preview_unavailable', 'check_failed'],
  ])('maps the current error %s without diagnostics', (reason, key) => {
    expect(checkControlFailure({ response: { status: 409, data: { error: { code: 'gate_settings_invalid', params: { reason, path: 'secret/worktree' } } } } })).toEqual({ field: 'merge_target_branch', key: `target.${key}` })
  })
  it.each([['push_requires_origin', 'push_check'], ['origin_not_configured', 'push_unavailable'], ['legacy_merge_target_conflict', 'legacy_conflict']])('puts %s on the push control', (reason, key) => {
    expect(checkControlFailure({ response: { data: { error: { code: 'gate_settings_invalid', params: { reason } } } } })).toEqual({ field: 'push', key: `target.${key}` })
  })
  it('uses live warnings before approval and only recorded target/warnings/push after receipt', () => {
    const live = node({ merge_target_state: { status: 'ok', current: entry('main'), default: entry('main') } })
    expect(checkSettingsSummary(live)).toMatchObject({ target: 'main', push: false, unmergeWarning: false })
    expect(checkSettingsSummary(live, receipt())).toMatchObject({ target: 'release', push: true, unmergeWarning: true })
    expect(checkSettingsSummary(live, receipt({ settings: { shown: { merge_target_branch: null }, effective: { merge_mode: 'merge' }, target_basis: null } }))).toMatchObject({ target: null, unmergeUnknown: true })
    expect(checkSettingsSummary(live, receipt({ settings: null }))).toMatchObject({ target: null, push: false, unmergeWarning: false })
  })
  it.each([{ result: 'rejected', status: 'rejected' }, { result: 'rejected' }, { status: 'rejected' }])('omits all Git summary facts for a rejected receipt: %j', rejected => {
    const saved = receipt({ ...rejected, settings: { shown: { merge_mode: 'merge', merge_target_branch: 'release', push: true },
      effective: { merge_mode: 'merge', push: true }, target_basis: null } })
    const empty = { target: null, unmergeWarning: false, unmergeUnknown: false, push: false }
    expect(checkSettingsSummary(node(), saved)).toEqual(empty)
    expect(checkSettingsSummary(node(), { ...saved, settings: null, settingsUnavailable: true })).toEqual(empty)
  })
  it('renders rejected pushes according to recorded local effects and maps direct unmerge refusals', () => {
    expect(checkOperationErrorKey('push_rejected', receipt({ effects: [{ phase: 'merge', status: 'done', sha_after: 'abc' }] }))).toBe('target.push_rejected_kept')
    expect(checkOperationErrorKey('push_rejected', receipt({ effects: [{ phase: 'merge', status: 'pending', sha_after: null }] }))).toBe('target.push_rejected')
    expect(checkOperationErrorKey('unmerge_unsupported_target')).toBe('target.unmerge_unsupported')
    expect(checkOperationErrorKey('unmerge_target_unknown')).toBe('target.unmerge_unknown')
  })
  it('keeps locks and all ko/en/ja labels', () => {
    expect(checkSettingsView({ ...base, node: { ...node(), locked: true } }).reason).toBe('reason.locked')
    expect(checkSettingsView({ ...base, readOnly: 'conflict' }).reason).toBe('reason.read_only_conflict')
    for (const locale of ['ko', 'en', 'ja'] as const) {
      expect(messages[locale].git.target).toBeTruthy()
      expect(Object.keys(messages[locale].target)).toEqual(Object.keys(messages.ko.target))
      for (const value of Object.values(messages[locale].target)) expect(value).toBeTruthy()
      for (const kind of ['locked', 'disposed', 'unavailable', 'invalid', 'forbidden', 'not_found', 'network', 'other'] as const) expect((messages[locale].error as Record<string, string>)[checkSettingsErrorKey(kind).split('.')[1]]).toBeTruthy()
    }
  })
})
