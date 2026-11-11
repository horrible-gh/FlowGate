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
    expect(checkSettingsView(base)).toMatchObject({ usage: null, scope: 'merge', target: 'feature/v0.2.0', targetStored: false, defaultTarget: 'feature/v0.2.0', targetOptions: ['feature/v0.2.0', 'main'], targetEnabled: true, archive: false, push: false })
    expect(checkSettingsView({ ...base, node: null }).targetOptions).toEqual([])
  })
  it('retains a stored branch independently of default changes and ignores old remote/usage values', () => {
    const saved = node({ fields: { usage: field('end', true), merge_mode: field('merge'), merge_target: field('origin', true), merge_target_branch: field('release', true) }, default_target_branch: 'new-base', merge_target_options: ['main'] })
    expect(checkSettingsView({ ...base, node: saved })).toMatchObject({ usage: null, target: 'release', targetStored: true, targetOptions: ['main'] })
  })
  it.each(['commit', 'hold'])('disables targets in %s while retaining the stored name', mode => {
    expect(checkSettingsView({ ...base, node: node({ fields: { merge_mode: field(mode), merge_target_branch: field('release', true) } }) })).toMatchObject({ target: 'release', targetEnabled: false })
  })
  it.each([['empty', 'target.empty'], ['failed', 'target.lookup_failed'], ['timeout', 'target.lookup_timeout'], ['git_unavailable', 'target.git_unavailable']])('distinguishes %s from other lookup states', (status, lookupError) => {
    // flowgate.v02test.0020: after a timeout / failure the inherited name stays choosable, unverified; empty / Git refused lock
    const usable = status === 'failed' || status === 'timeout'
    expect(checkSettingsView({ ...base, node: node({ merge_target_options: [], merge_target_state: { status, current: null, default: null } }) })).toMatchObject({
      targetEnabled: usable, lookupError, optionsSource: usable ? 'unverified' : 'none', targetOptions: usable ? ['feature/v0.2.0'] : [] })
  })
  it.each(['failed', 'timeout', 'git_unavailable'].flatMap(status => [false, true].map(stored => ({ status, stored }))))('keeps unchecked names without invalidating targets on $status (stored=$stored)', ({ status, stored }) => {
    const name = stored ? 'release' : 'feature/v0.2.0'
    const unchecked = { checked: false, error: null, unmerge_supported: null, warnings: [] }
    const saved = node({ fields: { merge_mode: field('merge'), merge_target_branch: field(stored ? name : null, stored) },
      merge_target_options: [], merge_target_state: { status, current: entry(name, unchecked), default: entry('feature/v0.2.0', unchecked) } })
    // flowgate.v02test.0020: on a timeout / failure with nothing kept the stored and inherited names are offered, unverified
    const usable = status !== 'git_unavailable'
    expect(checkSettingsView({ ...base, node: saved })).toMatchObject({ target: name, targetStored: stored, targetError: null,
      lookupError: status === 'git_unavailable' ? 'target.git_unavailable' : status === 'timeout' ? 'target.lookup_timeout' : 'target.lookup_failed', targetEnabled: usable,
      optionsSource: usable ? 'unverified' : 'none', targetOptions: usable ? [...new Set([name, 'feature/v0.2.0'])] : [] })
  })
  it('keeps invalid stored names out of choices and never substitutes another inherited branch', () => {
    const missing = entry('gone', { error: 'merge_target_not_found' })
    const saved = node({ fields: { merge_mode: field('merge'), merge_target_branch: field('gone', true) }, merge_target_state: { status: 'ok', current: missing, default: missing } })
    expect(checkSettingsView({ ...base, node: saved })).toMatchObject({ target: 'gone', targetError: 'target.missing', targetOptions: ['feature/v0.2.0', 'main'], targetEnabled: true })
    expect(checkSettingsView({ ...base, node: { ...saved, fields: { merge_mode: field('merge'), merge_target_branch: field(null) } } })).toMatchObject({ target: 'feature/v0.2.0', targetError: 'target.missing', targetEnabled: true })
  })
  it('T0012: names the default branch even when Git refuses it, with one lookup message and no blank target', () => {
    const refused = entry('main', { error: 'gate_git_unavailable', unmerge_supported: null })
    const saved = node({ default_target_branch: 'main', merge_target_options: [],
      merge_target_state: { status: 'git_unavailable', code: 'gate_git_unavailable', current: refused, default: refused } })
    expect(checkSettingsView({ ...base, node: saved })).toMatchObject({ target: 'main', defaultTarget: 'main', targetError: null, lookupError: 'target.git_unavailable', targetEnabled: false })
    const busy = entry('main', { error: 'merge_target_busy' })
    expect(checkSettingsView({ ...base, node: node({ default_target_branch: 'main', merge_target_options: ['release'],
      merge_target_state: { status: 'ok', current: busy, default: busy } }) })).toMatchObject({ target: 'main', targetError: 'target.busy', targetEnabled: true })
    expect(checkSettingsView({ ...base, node: node({ default_target_branch: null, merge_target_options: [], merge_target_state: { status: 'git_unavailable', current: null, default: null } }) }))
      .toMatchObject({ target: null, defaultTarget: null, targetError: null })
    for (const locale of ['ko', 'en', 'ja'] as const) {
      expect(messages[locale].target).not.toHaveProperty('unavailable')
      // T0012 rework: the default button and the unsupported-undo notice are deleted, not reworded.
      for (const key of ['use_default', 'use_default_named', 'unmerge_unsupported']) expect(messages[locale].target).not.toHaveProperty(key)
    }
  })
  it('shows the requested push and target at once and the confirmed ones again after a refusal (flowgate.v02.0148 T#2)', () => {
    expect(checkSettingsView({ ...base, pending: { push: true } }).push).toBe(true)
    expect(checkSettingsView({ ...base, pending: {} }).push).toBe(false)
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
    expect(checkSettingsSummary(live)).toMatchObject({ target: 'main', push: false })
    expect(checkSettingsSummary(live, receipt())).toEqual({ target: 'release', push: true, unmergeUnknown: false })
    expect(checkSettingsSummary(live, receipt({ settings: { shown: { merge_target_branch: null }, effective: { merge_mode: 'merge' }, target_basis: null } }))).toMatchObject({ target: null, unmergeUnknown: true })
    expect(checkSettingsSummary(live, receipt({ settings: null }))).toMatchObject({ target: null, push: false })
  })
  it.each([{ result: 'rejected', status: 'rejected' }, { result: 'rejected' }, { status: 'rejected' }])('omits all Git summary facts for a rejected receipt: %j', rejected => {
    const saved = receipt({ ...rejected, settings: { shown: { merge_mode: 'merge', merge_target_branch: 'release', push: true },
      effective: { merge_mode: 'merge', push: true }, target_basis: null } })
    const empty = { target: null, unmergeUnknown: false, push: false }
    expect(checkSettingsSummary(node(), saved)).toEqual(empty)
    expect(checkSettingsSummary(node(), { ...saved, settings: null, settingsUnavailable: true })).toEqual(empty)
  })
  it('renders rejected pushes according to recorded local effects and maps only the unknown-target unmerge refusal', () => {
    expect(checkOperationErrorKey('push_rejected', receipt({ effects: [{ phase: 'merge', status: 'succeeded', sha_after: 'abc' }] }))).toBe('target.push_rejected_kept')
    expect(checkOperationErrorKey('push_rejected', receipt({ effects: [{ phase: 'merge', status: 'pending', sha_after: null }] }))).toBe('target.push_rejected')
    expect(checkOperationErrorKey('unmerge_unsupported_target')).toBe('target.operation_unknown')
    expect(checkOperationErrorKey('unmerge_target_unknown')).toBe('target.unmerge_unknown')
  })
  it.each(['done', 'failed', 'unknown', 'pending', 'skipped'])('does not claim local preservation for %s', status => {
    expect(checkOperationErrorKey('push_rejected', receipt({ effects: [{ phase: 'merge', status, sha_after: 'abc' }] }))).toBe('target.push_rejected')
  })
  it.each([null, '', undefined])('requires a nonempty applied SHA: %s', sha_after => {
    expect(checkOperationErrorKey('push_rejected', receipt({ effects: [{ phase: 'merge', status: 'succeeded', sha_after }] }))).toBe('target.push_rejected')
  })
  it.each([
    ['merge_conflict', 'merge_conflict'], ['merge_conflict_aborted', 'merge_aborted'], ['target_moved', 'target_check'],
    ['merge_target_busy', 'target_check'], ['remote_head_unknown', 'remote_unknown'], ['push_result_unknown', 'push_unknown'],
    ['push_auth_failed', 'push_auth_failed'], ['group_branch_missing', 'group_branch_missing'], ['private_unknown', 'operation_unknown'],
  ])('explains %s in every locale without raw diagnostics', (code, key) => {
    expect(checkOperationErrorKey(code)).toBe(`target.${key}`)
    for (const locale of ['ko', 'en', 'ja'] as const) expect((messages[locale].target as Record<string, string>)[key]).toBeTruthy()
  })
  it('never offers a stale list beside a failed lookup: only the inherited name, unverified (flowgate.v02test.0020)', () => {
    expect(checkSettingsView({ ...base, node: node({ merge_target_state: { status: 'failed' } }) })).toMatchObject({
      targetOptions: ['feature/v0.2.0'], optionsSource: 'unverified', targetEnabled: true })
  })
  it('prefers the kept confirmed list to the unverified names, and offers none of them in commit / hold or under a lock', () => {
    const failed = node({ merge_target_options: [], merge_target_state: { status: 'timeout' } })
    expect(checkSettingsView({ ...base, node: failed, cachedTargets: ['main', 'release'] })).toMatchObject({ targetOptions: ['main', 'release'], optionsSource: 'cache', targetEnabled: true })
    expect(checkSettingsView({ ...base, node: { ...failed, fields: { ...failed.fields, merge_mode: field('hold') } } }).targetEnabled).toBe(false)
    expect(checkSettingsView({ ...base, node: { ...failed, locked: true } }).targetEnabled).toBe(false)
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
