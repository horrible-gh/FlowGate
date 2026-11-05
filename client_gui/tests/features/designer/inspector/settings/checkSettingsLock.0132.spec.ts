/** flowgate.v02.0132 T#4: the check card tells why its settings are locked (per `settings_lock_reason`), and the new error codes. */
import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
const ctx = vi.hoisted(() => ({ value: null as any }))
vi.mock('@/features/designer/inspector/features/gateCard/useGateContext', async original => ({
  ...await original<typeof import('@/features/designer/inspector/features/gateCard/useGateContext')>(),
  useGateContext: () => ({ get value() { return ctx.value } }),
}))
import { checkLockedReason, checkOperationErrorKey, checkSettingsView } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import { useCheckSettings } from '@/features/designer/inspector/features/checkSettings/useCheckSettings'
import messages from '@/features/designer/inspector/features/checkSettings/messages'
import { toSettingsNode } from '@/features/document'

const field = (value: unknown, stored = false) => ({ value, stored, default: null, choices: null })
const settings = (extra = {}) => toSettingsNode({ card_kind: 'check', settings_version: 4, fields: { merge_mode: field('merge'), merge_target_branch: field(null), push: field(false) },
  merge_target_options: ['main'], default_target_branch: 'main', merge_target_state: { status: 'ok', current: null, default: null }, ...extra })
const base = { card: true, pending: {}, branches: [], readOnly: null, placing: false, disposed: false, failure: null } as const
const lockedNode = (extra: Record<string, unknown>) => ({ ...settings(), locked: true, ...extra })
const textOf = (locale: 'ko' | 'en' | 'ja', key: string) => key.split('.').reduce<any>((o, k) => o?.[k], messages[locale])

describe('checkSettings lock reason per settings_lock_reason (0132 T#4)', () => {
  it.each([
    [{}, 'reason.locked'],
    [{ lockReason: 'run_not_editable' }, 'reason.locked'],
    [{ lockReason: 'something_new' }, 'reason.locked'],
    [{ lockReason: 'decision_reserved' }, 'reason.lock_decision_reserved'],
    [{ lockReason: 'merge_conflict' }, 'reason.lock_merge_conflict'],
    [{ lockReason: 'time_machine' }, 'reason.lock_time_machine'],
    [{ lockReason: 'gate_decided' }, 'reason.lock_gate_decided'],
    [{ lockReason: 'recovery_reset_required' }, 'reason.lock_recovery_reset'],
    [{ lockReason: 'decision_reserved', parked: true }, 'reason.lock_parked'],
    [{ parked: true }, 'reason.lock_parked'],
    [{ lockReason: 'merge_conflict', parked: true }, 'reason.lock_merge_conflict'],
    [{ lockReason: 'decision_reserved', gitActive: true }, 'reason.lock_git_active'],
    [{ lockReason: 'decision_reserved', gitActive: true, parked: true }, 'reason.lock_git_active'],
  ])('%j -> %s', (facts, key) => {
    expect(checkSettingsView({ ...base, node: lockedNode(facts) }).reason).toBe(key)
    expect(checkLockedReason(facts)).toBe(key)
  })
  it('an unlocked card shows no reason whatever the Git facts are, and the other reasons keep their order', () => {
    expect(checkSettingsView({ ...base, node: { ...settings(), locked: false, gitActive: true, parked: true } }).reason).toBeNull()
    expect(checkSettingsView({ ...base, node: lockedNode({ gitActive: true }), disposed: true }).reason).toBe('reason.disposed')
    expect(checkSettingsView({ ...base, node: lockedNode({ gitActive: true }), readOnly: 'conflict' }).reason).toBe('reason.read_only_conflict')
  })
  it.each(['ko', 'en', 'ja'] as const)('%s: every lock reason has its own sentence', locale => {
    const keys = ['reason.locked', 'reason.lock_git_active', 'reason.lock_parked', 'reason.lock_decision_reserved', 'reason.lock_merge_conflict', 'reason.lock_time_machine', 'reason.lock_gate_decided', 'reason.lock_recovery_reset']
    const texts = keys.map(key => textOf(locale, key))
    for (const text of texts) expect(typeof text === 'string' && text.length > 0).toBe(true)
    expect(new Set(texts).size).toBe(keys.length)
  })
  it('ko words the reasons as the work order asks', () => {
    expect(textOf('ko', 'reason.lock_decision_reserved')).toBe('확인 결정 반영 대기')
    expect(textOf('ko', 'reason.lock_merge_conflict')).toBe('병합 충돌 해결 대기')
    expect(textOf('ko', 'reason.lock_git_active')).toBe('자동 동기화 중')
    expect(textOf('ko', 'reason.lock_parked')).toBe('복구 대기')
  })
})

describe('useCheckSettings carries the server lock reason and Git state (0132 T#4)', () => {
  const contextOf = (gate: Record<string, unknown> | null, saved = settings()) => ({
    settings: ref(saved), gate: ref(gate), busy: ref(null), hasRequest: () => false, pending: ref({}), status: ref('ready'),
    failure: ref(null), decisionError: ref(null), error: ref(null), change: async () => true, reload: async () => undefined,
  })
  const nodeOf = (gate: Record<string, unknown> | null, saved = settings()) => {
    ctx.value = contextOf(gate, saved)
    return useCheckSettings(() => null).node.value as any
  }
  it('takes the gate reason, else the settings read reason, and the git_active / parked flags', () => {
    expect(nodeOf({ settings_locked: true, settings_lock_reason: 'decision_reserved', git_active: true })).toMatchObject({ locked: true, lockReason: 'decision_reserved', gitActive: true, parked: false })
    expect(nodeOf({ settings_locked: true, settings_lock_reason: 'decision_reserved', parked: true })).toMatchObject({ locked: true, lockReason: 'decision_reserved', gitActive: false, parked: true })
    expect(nodeOf({ settings_locked: true }, settings({ locked: true, lock_reason: 'merge_conflict' }))).toMatchObject({ locked: true, lockReason: 'merge_conflict' })
    expect(nodeOf(null)).toMatchObject({ locked: false, lockReason: null, gitActive: false, parked: false })
  })
  it('the whole chain: gate wire -> node -> view reason', () => {
    const reasonOf = (gate: Record<string, unknown>) => checkSettingsView({ ...base, node: nodeOf(gate) }).reason
    expect(reasonOf({ settings_locked: true, settings_lock_reason: 'decision_reserved' })).toBe('reason.lock_decision_reserved')
    expect(reasonOf({ settings_locked: true, settings_lock_reason: 'decision_reserved', parked: true })).toBe('reason.lock_parked')
    expect(reasonOf({ settings_locked: true, settings_lock_reason: 'decision_reserved', git_active: true })).toBe('reason.lock_git_active')
    expect(reasonOf({ settings_locked: true, settings_lock_reason: 'merge_conflict' })).toBe('reason.lock_merge_conflict')
    expect(reasonOf({ settings_locked: false })).toBeNull()
  })
})

describe('checkOperationErrorKey — the new codes (0132 T#4)', () => {
  const CODES = { push_remote_ahead: 'remote_ahead', push_protected: 'push_protected', push_remote_race: 'remote_race', target_checkout_mismatch: 'checkout_mismatch',
    target_diverged: 'diverged', sync_unsettled: 'sync_unsettled', push_sync_conflict_aborted: 'sync_conflict_aborted' }
  it.each(Object.entries(CODES))('%s -> target.%s in ko / en / ja', (code, name) => {
    expect(checkOperationErrorKey(code)).toBe(`target.${name}`)
    for (const locale of ['ko', 'en', 'ja'] as const) expect(textOf(locale, `target.${name}`).length).toBeGreaterThan(0)
  })
  it('keeps the older codes', () => {
    expect(checkOperationErrorKey('target_moved')).toBe('target.target_check')
    expect(checkOperationErrorKey('push_rejected')).toBe('target.push_rejected')
    expect(checkOperationErrorKey('never_heard_of_it')).toBe('target.operation_unknown')
    expect(checkOperationErrorKey(null)).toBeNull()
  })
  it('the park texts name the wait, the sync texts do not', () => {
    expect(textOf('ko', 'target.sync_unsettled')).toContain('복구 대기')
    expect(textOf('ko', 'target.remote_ahead')).not.toContain('복구 대기')
    expect(textOf('ko', 'target.syncing')).toBe('자동 동기화 중')
  })
  it.each(['ko', 'en', 'ja'] as const)('push_remote_ahead tells the refusal and what to do, never an automatic sync (commit mode parks it) in %s', locale => {
    // publish._push: only merge mode syncs the fetched remote tip; commit mode parks push_remote_ahead with no remote_sync
    const text = textOf(locale, 'target.remote_ahead')
    expect(text.length).toBeGreaterThan(0)
    expect(text).not.toMatch(/자동|automatic|自動/i)
    expect(text).not.toBe(textOf(locale, 'target.syncing'))
  })
})
