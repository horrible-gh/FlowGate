import { defineComponent, h, shallowRef } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(),
  getRequest: (...a: unknown[]) => http.get(...a), patchRequest: (...a: unknown[]) => http.patch(...a), postRequest: (...a: unknown[]) => http.post(...a) }))
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import GateSettingsSummary from '@/features/designer/inspector/features/gateCard/GateSettingsSummary.vue'
import { MERGE_TARGET_CACHE_MS, useGateContext } from '@/features/designer/inspector/features/gateCard/useGateContext'
import { checkTargetErrorKey } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import { settingsNodeRead, toSettingsNode } from '@/features/document'
import { SELECTIONS, prepareInspectorI18n, targetOf } from '../support/inspectorSupport'
const i18n = prepareInspectorI18n()
const wrappers: VueWrapper[] = []
const field = (value: unknown, stored = false) => ({ value, stored, default: null, choices: null })
const entry = (branch: string | null, extra = {}) => ({ branch, checked: true, error: null, unmerge_supported: false, warnings: ['unmerge_unsupported_for_non_base_target'], ...extra })
let raw: any
let current: any
let id = 0
let target: ReturnType<typeof targetOf>
let context: ReturnType<typeof useGateContext>
/** 0006-T: the lookup state a PATCH answer carries (null: judged from the options as before). */
let failing: 'timeout' | 'failed' | null = null
const refusal = (code: string, reason?: string) => ({ isAxiosError: true, response: { status: 409, data: { error: { code, params: { reason } } } } })
function targets(selected: string | null) {
  raw.merge_target_state = { status: raw.merge_target_options.length ? 'ok' : 'empty', code: null, reason: null,
    current: entry(selected ?? raw.default_target_branch), default: entry(raw.default_target_branch) }
}
const unchecked = { checked: false, error: null, unmerge_supported: null, warnings: [] }
const storedTarget = () => (raw.fields.merge_target_branch.stored ? raw.fields.merge_target_branch.value as string : null)
/** The server's answer when the candidate lookup timed out / failed: no candidates, unchecked current / default. */
function lookupFails(status: 'timeout' | 'failed', workBase: string = raw.default_target_branch) {
  raw.merge_target_options = []
  raw.default_target_branch = workBase
  raw.merge_target_state = { status, code: status === 'timeout' ? 'merge_target_options_timeout' : 'merge_target_options_unavailable',
    reason: status === 'timeout' ? null : 'probe_failed', current: entry(storedTarget() ?? workBase, unchecked), default: entry(workBase, unchecked) }
}
async function show() {
  const w = mount(defineComponent({ setup() {
    context = useGateContext(() => target)
    return () => h(CheckSettings, { target })
  } }), { global: { plugins: [i18n] } })
  wrappers.push(w); await flushPromises(); return w
}
const optionNames = (w: VueWrapper) => w.findAll('[data-test="check-settings-target"] option').map(o => o.text())
const selected = (w: VueWrapper) => (w.get('[data-test="check-settings-target"]').element as HTMLSelectElement).value
beforeEach(() => {
  Object.values(http).forEach(m => m.mockReset()); i18n.global.locale.value = 'ko'; failing = null
  target = { ...targetOf(SELECTIONS.graphGate), nodeId: `check0098_${++id}`, readOnly: null }
  raw = { card_kind: 'check', settings_version: 6, locked: false,
    fields: { archive: field(false), merge_mode: field('merge'), merge_target: field(null), merge_target_branch: field(null), push: field(false), keep_branch: field(false) },
    merge_target_options: ['feature/v0.2.0', 'main', 'release/local'], default_target_branch: 'feature/v0.2.0', push_remote_check_required: false }
  targets(null)
  current = { run_id: 'run0098', status: 'idle', node_runs: [{ node_key: target.nodeId, status: 'open', attempt_id: 'attempt0098', gate: {
    can_decide: true, settings_locked: false, decision_in_progress: false, decision: null, job: null, recovery: null } }] }
  http.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/settings')) return { data: structuredClone(raw) }
    if (url.endsWith('/runs/current')) return { data: { run: structuredClone(current) } }
    return { data: {} }
  })
  http.patch.mockImplementation(async (_url: string, body: any) => {
    expect(body.expected_settings_version).toBe(raw.settings_version)
    raw.fields[body.field] = field(body.value, body.value !== null)
    if (body.field === 'push' && body.value === true) { raw.fields.merge_target = field('origin', true); raw.push_remote_check_required = false }
    raw.settings_version++
    if (failing) lookupFails(failing)
    else targets(raw.fields.merge_target_branch.value)
    return { data: structuredClone(raw) }
  })
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); i18n.global.locale.value = 'ko' })

describe('check card target and push controls — 0098', () => {
  it('reads inherited names without PATCH; explicit selection survives GET; the default branch is picked from the list like any branch', async () => {
    const w = await show()
    expect(optionNames(w)).toEqual(raw.merge_target_options)
    expect(optionNames(w)).not.toContain('origin'); expect(optionNames(w)).not.toContain('—')
    expect(selected(w)).toBe('feature/v0.2.0'); expect(http.patch).not.toHaveBeenCalled()
    // T0012 rework: no default button and no text about it.
    expect(w.find('[data-test="check-settings-default"]').exists()).toBe(false)
    expect(w.text()).not.toContain('기본값 사용')
    await w.get('[data-test="check-settings-target"]').setValue('release/local'); await flushPromises()
    expect(http.patch).toHaveBeenLastCalledWith(expect.stringContaining('/settings'), { field: 'merge_target_branch', value: 'release/local', expected_settings_version: 6 })
    raw.default_target_branch = 'main'; targets('release/local'); await context.value!.reload(); await flushPromises()
    expect(selected(w)).toBe('release/local')
    await w.get('[data-test="check-settings-target"]').setValue('main'); await flushPromises()
    expect(http.patch).toHaveBeenLastCalledWith(expect.any(String), { field: 'merge_target_branch', value: 'main', expected_settings_version: 7 })
    expect(selected(w)).toBe('main')
    expect(http.patch).toHaveBeenCalledTimes(2)
  })
  it('choosing the work base explicitly stores its name and keeps it after the default changes', async () => {
    const w = await show()
    await w.get('[data-test="check-settings-target"]').setValue('feature/v0.2.0'); await flushPromises()
    expect(http.patch).toHaveBeenLastCalledWith(expect.any(String), { field: 'merge_target_branch', value: 'feature/v0.2.0', expected_settings_version: 6 })
    raw.default_target_branch = 'main'; targets('feature/v0.2.0'); await context.value!.reload(); await flushPromises()
    expect(selected(w)).toBe('feature/v0.2.0')
  })
  it.each(['commit', 'hold'])('keeps target settings untouched and disables selection in %s', async mode => {
    raw.fields.merge_mode = field(mode, true); raw.fields.merge_target_branch = field('release/local', true); targets('release/local')
    const w = await show()
    expect(selected(w)).toBe('release/local')
    expect(w.get('[data-test="check-settings-target"]').attributes('disabled')).toBeDefined()
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('restores the saved selection on refusal, shows the control error and refreshes target state', async () => {
    raw.fields.merge_target_branch = field('main', true); targets('main')
    const w = await show()
    http.patch.mockRejectedValueOnce(refusal('merge_target_busy'))
    await w.get('[data-test="check-settings-target"]').setValue('release/local'); await flushPromises()
    expect(selected(w)).toBe('main'); expect(raw.settings_version).toBe(6)
    expect(w.get('[data-test="check-settings-target-error"]').text()).toBe('대상 사용 중')
    expect(http.get.mock.calls.filter(([url]) => url.endsWith('/settings')).length).toBe(2)
  })
  it('retains an unavailable stored name as a control display without adding an option', async () => {
    raw.fields.merge_target_branch = field('gone', true); raw.merge_target_options = []; targets('gone'); raw.merge_target_state.current.error = 'merge_target_not_found'
    const w = await show()
    expect(optionNames(w)).toEqual([])
    expect(w.get('[data-test="check-settings-target-display"]').text()).toBe('gone')
    expect(w.get('[data-test="check-settings-target-error"]').text()).toBe('대상 없음')
    expect(w.find('[data-test="check-settings-default"]').exists()).toBe(false)
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('T0012: shows an unavailable default by its branch name with its own short error while valid candidates remain selectable', async () => {
    raw.merge_target_options = ['main', 'release/local']; targets(null); raw.merge_target_state.default.error = 'merge_target_busy'
    const w = await show()
    expect(optionNames(w)).toEqual(['main', 'release/local']); expect(w.get('[data-test="check-settings-target"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-test="check-settings-target-display"]').text()).toBe('feature/v0.2.0')
    expect(w.get('[data-test="check-settings-target-error"]').text()).toBe('대상 사용 중')
    expect(w.find('[data-test="check-settings-target-state"]').exists()).toBe(false)
    expect(w.text()).not.toContain('대상 사용 불가')
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('T0012: a Git refusal of the whole lookup keeps the default name and shows one short line', async () => {
    raw.default_target_branch = 'main'; raw.merge_target_options = []
    const refused = entry('main', { error: 'gate_git_unavailable', unmerge_supported: null, warnings: [] })
    raw.merge_target_state = { status: 'git_unavailable', code: 'gate_git_unavailable', reason: 'git_disabled', current: refused, default: refused }
    const w = await show()
    expect(w.get('[data-test="check-settings-target-display"]').text()).toBe('main')
    expect(w.find('[data-test="check-settings-default"]').exists()).toBe(false)
    expect(w.findAll('[role="status"], [role="alert"]').map(e => e.text())).toEqual(['Git 사용 불가'])
    expect(w.text()).not.toContain('대상 사용 불가')
  })
  it('distinguishes an empty candidate list from a lookup failure', async () => {
    raw.merge_target_options = []; targets(null)
    const w = await show()
    expect(w.get('[data-test="check-settings-target-state"]').text()).toBe('선택 가능한 대상 없음')
    expect(w.get('[data-test="check-settings-target"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="check-settings-refresh"]').exists()).toBe(false)
    expect(http.patch).not.toHaveBeenCalled()
  })
  it.each(['failed', 'timeout', 'git_unavailable'].flatMap(status => [false, true].map(stored => ({ status, stored }))))('retains the target name without a control error on $status (stored=$stored)', async ({ status, stored }) => {
    const name = stored ? 'release/local' : raw.default_target_branch
    raw.fields.merge_target_branch = field(stored ? name : null, stored)
    raw.merge_target_options = []
    const unchecked = { checked: false, error: null, unmerge_supported: null, warnings: [] }
    raw.merge_target_state = { status, current: entry(name, unchecked), default: entry(raw.default_target_branch, unchecked) }
    const before = structuredClone(raw.fields)
    const w = await show()
    const control = w.get('[data-test="check-settings-target"]')
    expect(w.get('[data-test="check-settings-target-state"]').text()).toBe(status === 'git_unavailable' ? 'Git 사용 불가' : status === 'timeout' ? '대상 조회 시간 초과' : '대상 조회 실패')
    expect(control.attributes('disabled')).toBeDefined()
    expect(control.attributes('aria-invalid')).toBe('false')
    expect(control.classes()).not.toContain('is-invalid')
    expect(w.get('[data-test="check-settings-target-display"]').text()).toBe(name)
    expect(w.find('[data-test="check-settings-target-error"]').exists()).toBe(false)
    expect(optionNames(w)).toEqual([])
    if (status === 'failed' || status === 'timeout') {
      // 0006-T §3.5: the first read failed and nothing is kept: no list, but the refresh is open.
      expect(w.get('[data-test="check-settings-refresh"]').attributes('disabled')).toBeUndefined()
      await w.get('[data-test="check-settings-refresh"]').trigger('click'); await flushPromises()
      expect(http.get).toHaveBeenCalledWith(expect.stringContaining('/settings'), { refresh_merge_targets: true })
      expect(w.get('[data-test="check-settings-target-display"]').text()).toBe(name)
      expect(w.find('[data-test="check-settings-target-error"]').exists()).toBe(false)
    }
    expect(raw.fields).toEqual(before)
    expect(raw.settings_version).toBe(6)
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('shows no unsupported-undo notice, no default button and no standing push explanation (T0012 rework)', async () => {
    raw.fields.push = field(true, true)
    const w = await show()
    expect(raw.merge_target_state.current.unmerge_supported).toBe(false)
    expect(w.text()).not.toContain('병합 취소')
    expect(w.text()).not.toContain('기본값 사용')
    expect(w.find('[data-test="check-settings-push-create"]').exists()).toBe(false)
    expect(w.text()).not.toContain('없는 원격 브랜치는 새로 생성')
    expect(optionNames(w)).toEqual(raw.merge_target_options)
  })
  it('re-saves push=true with the current version and replaces all fields only after the full response', async () => {
    raw.fields.push = field(true, true); raw.push_remote_check_required = true
    const w = await show()
    let resolve!: (v: any) => void
    http.patch.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    await w.get('[data-test="check-settings-push-recheck"]').trigger('click')
    expect(http.patch).toHaveBeenLastCalledWith(expect.any(String), { field: 'push', value: true, expected_settings_version: 6 })
    expect(w.find('[data-test="check-settings-push-check"]').exists()).toBe(true)
    raw.fields.merge_target = field('origin', true); raw.fields.keep_branch = field(true, true); raw.push_remote_check_required = false; raw.settings_version = 7
    resolve({ data: structuredClone(raw) }); await flushPromises()
    expect(w.find('[data-test="check-settings-push-check"]').exists()).toBe(false)
    expect(w.get('[data-test="check-settings-keep-branch"]').attributes('aria-checked')).toBe('true')
    expect(context.value!.settings.value?.settingsVersion).toBe(7)
    expect(http.post).not.toHaveBeenCalled()
  })
  it('does not optimistically confirm push ON and restores OFF after a refusal', async () => {
    const w = await show(); let reject!: (e: any) => void
    http.patch.mockImplementationOnce(() => new Promise((_a, b) => { reject = b }))
    await w.get('[data-test="check-settings-push"]').trigger('click')
    expect(w.get('[data-test="check-settings-push"]').attributes('aria-checked')).toBe('false')
    reject(refusal('gate_settings_invalid', 'origin_not_configured')); await flushPromises()
    expect(w.get('[data-test="check-settings-push"]').attributes('aria-checked')).toBe('false')
    expect(w.get('[data-field="push"]').attributes('title')).toContain('원격 반영 사용 불가')
  })
  it('preserves every value and version on legacy conflict and never auto-approves', async () => {
    raw.fields.push = field(true, true); raw.push_remote_check_required = true
    const w = await show(); const before = structuredClone(context.value!.settings.value)
    http.patch.mockRejectedValueOnce(refusal('gate_settings_invalid', 'legacy_merge_target_conflict'))
    await w.get('[data-test="check-settings-push-recheck"]').trigger('click'); await flushPromises()
    expect(context.value!.settings.value).toEqual(before)
    expect(w.get('[data-field="push"]').attributes('title')).toContain('과거 설정 확인 필요')
    expect(http.post).not.toHaveBeenCalled()
  })
  it('routes approval push_requires_origin to the same correction control without re-approval', async () => {
    raw.fields.push = field(true, true)
    const w = await show()
    http.post.mockRejectedValueOnce(refusal('gate_settings_invalid', 'push_requires_origin'))
    expect(await context.value!.decide('confirm')).toBe(false); await flushPromises()
    expect(w.get('[data-test="check-settings-push-check"]').text()).toBe('원격 반영 설정 확인 필요')
    await w.get('[data-test="check-settings-push-recheck"]').trigger('click'); await flushPromises()
    expect(http.patch).toHaveBeenLastCalledWith(expect.any(String), { field: 'push', value: true, expected_settings_version: 6 })
    expect(http.post).toHaveBeenCalledTimes(1)
    expect(w.find('[data-test="check-settings-push-check"]').exists()).toBe(false)
  })
  it('keeps extended GET/PATCH facts and sends refresh only on explicit request', async () => {
    const mapped = await settingsNodeRead(target.flowId, target.nodeId)
    expect(mapped).toMatchObject({ mergeTargetOptions: raw.merge_target_options, defaultTargetBranch: raw.default_target_branch, mergeTargetState: raw.merge_target_state, pushRemoteCheckRequired: false, settingsVersion: 6 })
    expect(http.get).toHaveBeenLastCalledWith(expect.stringContaining('/settings'))
    await settingsNodeRead(target.flowId, target.nodeId, true)
    expect(http.get).toHaveBeenLastCalledWith(expect.stringContaining('/settings'), { refresh_merge_targets: true })
  })
  it.each(['ko', 'en', 'ja'] as const)('disables undo for an unknown receipt target and prints no unsupported-undo text in %s', async locale => {
    i18n.global.locale.value = locale
    const decision: any = { settings: { shown: { merge_target_branch: null }, effective: { merge_mode: 'merge' }, target_basis: null }, target_branch: 'release/local' }
    const w = mount(GateSettingsSummary, { props: { decision, errorCode: 'unmerge_target_unknown' }, global: { plugins: [i18n] } }); wrappers.push(w)
    expect(w.get('[data-test="gate-summary-unmerge-unknown"]').text()).toBe(i18n.global.t('gui.inspector.checkSettings.target.unmerge_unknown'))
    expect(w.get('[data-test="gate-unmerge"]').attributes('disabled')).toBeDefined()
    await w.setProps({ errorCode: 'unmerge_unsupported_target' })
    expect(w.get('[data-test="gate-summary-error"]').text()).toBe(i18n.global.t('gui.inspector.checkSettings.target.operation_unknown'))
    expect(w.html()).not.toMatch(/병합 취소 미지원|Undo merge is unsupported|マージ取り消し未対応/)
  })
  it.each(['ko', 'en', 'ja'] as const)('hides rejected merge receipts even with push enabled in %s', async locale => {
    i18n.global.locale.value = locale
    raw.fields.push = field(true, true)
    const receipt: any = { decision_id: 'rejected0098', attempt_id: 'attempt0098', result: 'rejected', status: 'rejected', effects: [],
      settings: { shown: { merge_mode: 'merge', merge_target_branch: 'release/local', push: true }, effective: { merge_mode: 'merge', push: true }, target_basis: null } }
    const w = mount(GateSettingsSummary, { props: { node: toSettingsNode(raw), decision: receipt }, global: { plugins: [i18n] } }); wrappers.push(w)
    expect(w.find('[data-test="gate-settings-summary"]').exists()).toBe(false)
    expect(w.find('[data-test="gate-summary-unmerge-unknown"]').exists()).toBe(false)
    expect(w.find('[data-test="gate-unmerge"]').exists()).toBe(false)
    expect(w.find('[data-test="gate-summary-push-create"]').exists()).toBe(false)
    await w.setProps({ decision: { ...receipt, settings: null, settingsUnavailable: true }, errorCode: 'unmerge_target_unknown' })
    expect(w.find('[data-test="gate-settings-summary"]').exists()).toBe(false)
  })
  it('renders the receipt target without the unsupported-undo notice; the document tab hides the target line', async () => {
    const receipt: any = { decision_id: 'd1', attempt_id: 'attempt0098', result: 'confirmed', status: 'succeeded', archived: false, effects: [{ phase: 'merge', status: 'succeeded', sha_after: 'abc' }],
      settings: { shown: { merge_target_branch: 'release/local' }, effective: { merge_mode: 'merge', push: true }, target_basis: { target_branch: 'release/local', unmerge_supported: false, warnings: ['unmerge_unsupported_for_non_base_target'] } } }
    const w = mount(GateSettingsSummary, { props: { node: toSettingsNode(raw), decision: receipt, errorCode: 'push_rejected' }, global: { plugins: [i18n] } }); wrappers.push(w)
    expect(w.get('[data-test="gate-summary-target"]').text()).toBe('release/local')
    expect(w.find('[data-test="gate-summary-unmerge"]').exists()).toBe(false)
    expect(w.find('[data-test="gate-unmerge"]').exists()).toBe(false)
    expect(w.text()).not.toContain('병합 취소')
    expect(w.get('[data-test="gate-summary-error"]').text()).toBe('원격 갱신 거절 · 로컬 반영 보존')
    raw.fields.push = field(false); raw.default_target_branch = 'main'; targets(null)
    await w.setProps({ node: toSettingsNode(raw) })
    expect(w.get('[data-test="gate-summary-target"]').text()).toBe('release/local')
    // flowgate.v02test.0002 R0001 #1: the recorded push=true no longer draws the "missing remote branches are created" line.
    expect(w.find('[data-test="gate-summary-push-create"]').exists()).toBe(false)
    expect(w.text()).not.toContain('없는 원격 브랜치는 새로 생성')
    // The card's document tab (GateSlots) passes hideTarget: neither the target line nor a substitute is drawn.
    await w.setProps({ hideTarget: true })
    expect(w.find('[data-test="gate-summary-target"]').exists()).toBe(false)
    expect(w.text()).not.toContain('병합 대상')
    expect(w.text()).not.toContain('release/local')
  })
  it.each(['ko', 'en', 'ja'] as const)('flowgate.v02test.0002 R0001 #1: no remote-branch creation line with push ON or OFF, before or after the decision, in %s', async locale => {
    i18n.global.locale.value = locale
    const creation = /없는 원격 브랜치는 새로 생성|Missing remote branches will be created|存在しないリモートブランチを新規作成/
    const receipt = (push: boolean): any => ({ decision_id: 'd0002', attempt_id: 'attempt0098', result: 'confirmed', status: 'succeeded', archived: false, effects: [{ phase: 'merge', status: 'succeeded', sha_after: 'abc' }],
      settings: { shown: { merge_target_branch: 'release/local' }, effective: { merge_mode: 'merge', push }, target_basis: { target_branch: 'release/local', unmerge_supported: true, warnings: [] } } })
    for (const push of [true, false]) {
      raw.fields.push = field(push, true)
      for (const decision of [null, receipt(push)]) {
        const w = mount(GateSettingsSummary, { props: { node: toSettingsNode(raw), decision }, global: { plugins: [i18n] } }); wrappers.push(w)
        expect(w.find('[data-test="gate-summary-push-create"]').exists()).toBe(false)
        expect(w.html()).not.toMatch(creation)
        // The target line stays: the live default before the decision, the recorded target after it.
        expect(w.get('[data-test="gate-summary-target"]').text()).toBe(decision ? 'release/local' : 'feature/v0.2.0')
        // With the target line hidden and nothing else to say, no empty summary box is left (push ON too).
        await w.setProps({ hideTarget: true })
        expect(w.find('[data-test="gate-settings-summary"]').exists()).toBe(false)
        // A real error is still shown.
        await w.setProps({ errorCode: 'push_rejected' })
        expect(w.find('[data-test="gate-summary-error"]').exists()).toBe(true)
      }
    }
    // The settings form itself has no such line either, with push ON.
    raw.fields.push = field(true, true)
    const form = await show()
    expect(form.get('[data-test="check-settings-push"]').attributes('aria-checked')).toBe('true')
    expect(form.html()).not.toMatch(creation)
  })
  it('the document tab without a decision draws no target line and no unsupported-undo notice', async () => {
    const w = mount(GateSettingsSummary, { props: { node: toSettingsNode(raw), hideTarget: true }, global: { plugins: [i18n] } }); wrappers.push(w)
    expect(raw.merge_target_state.current.unmerge_supported).toBe(false)
    expect(w.find('[data-test="gate-settings-summary"]').exists()).toBe(false)
    expect(w.text()).toBe('')
  })
})


describe('0119 refresh icon and field help', () => {
  it('keeps its accessible name and data-test; disables/spins until the actual refreshed GET finishes', async () => {
    raw.merge_target_options = []
    raw.merge_target_state = { status: 'timeout', code: 'merge_target_options_timeout', reason: 'budget', current: entry('main', { checked: false }), default: entry('main', { checked: false }) }
    const w = await show()
    const button = w.get('[data-test="check-settings-refresh"]')
    expect(button.element.tagName).toBe('BUTTON')
    expect(button.text()).toBe('')
    expect(button.attributes('aria-label')).toBe('새로 조회')
    expect(button.attributes('title')).toBe('새로 조회')
    const help = w.get('[data-test="check-settings-target-state"]')
    expect(help.attributes('role')).toBe('status')
    expect(help.element.parentElement).toBe(w.get('.inspector-check-settings-target-wrap').element)
    expect(w.get('.inspector-check-settings-target-control').find('[data-test="check-settings-target-state"]').exists()).toBe(false)
    let resolve!: (answer: unknown) => void
    const answer = new Promise(r => { resolve = r })
    http.get.mockImplementation(async (url: string) => url.endsWith('/settings') ? answer : { data: { run: structuredClone(current) } })
    await button.trigger('click')
    expect(http.get).toHaveBeenLastCalledWith(expect.stringContaining('/settings'), { refresh_merge_targets: true })
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('aria-busy')).toBe('true')
    expect(button.get('svg').classes()).toContain('is-spinning')
    const count = http.get.mock.calls.length
    await button.trigger('click')
    expect(http.get.mock.calls.length).toBe(count)
    raw.merge_target_options = ['main', 'release/local']; targets('main')
    resolve({ data: structuredClone(raw) }); await flushPromises()
    expect(w.find('[data-test="check-settings-target-state"]').exists()).toBe(false)
    expect(w.get('[data-test="check-settings-target"]').attributes('disabled')).toBeUndefined()
    expect(optionNames(w)).toEqual(['main', 'release/local'])
    expect(w.find('[data-test="check-settings-refresh"]').exists()).toBe(false)
  })
  it('places target errors under the Select, outside the overlay coordinate box', async () => {
    raw.fields.merge_target_branch = field('missing', true)
    raw.merge_target_state = { status: 'ok', current: entry('missing', { error: 'merge_target_not_found' }), default: entry(raw.default_target_branch) }
    const w = await show()
    const error = w.get('[data-test="check-settings-target-error"]')
    expect(error.attributes('role')).toBe('alert')
    expect(error.classes()).toContain('inspector-check-settings-target-help')
    expect(error.element.parentElement).toBe(w.get('.inspector-check-settings-target-wrap').element)
    expect(w.get('[data-test="check-settings-target-display"]').element.parentElement).toBe(w.get('.inspector-check-settings-target-control').element)
    await w.get('[data-test="check-settings-target"]').setValue('main'); await flushPromises()
    expect(w.find('[data-test="check-settings-target-error"]').exists()).toBe(false)
  })
  it('flowgate.v02test.0002 R0001 #2: the two switches stay the same Toggles, in order, to the right of the target in its row', async () => {
    raw.fields.push = field(true, true)
    const w = await show()
    const git = w.get('[data-test="check-settings-git"]')
    expect(git.classes()).toContain('inspector-check-settings-git')
    const targetRow = w.get('[data-test="check-settings-target"]').element.closest('[data-test="inspector-control-row"]')!
    expect(targetRow.getAttribute('data-label')).toBe('병합 대상')
    const switches = w.get('[data-test="check-settings-switches"]')
    expect(switches.element.closest('[data-test="inspector-control-row"]')).toBe(targetRow)
    // The target area comes first, then the switch pairs (원격 반영, 브랜치 보관) with their own labels.
    const value = targetRow.querySelector('.inspector-control-value')!
    expect([...value.children].map(el => el.className)).toEqual(['inspector-check-settings-target-group', 'inspector-check-settings-switches'])
    expect(switches.findAll('.inspector-check-settings-toggle').map(l => l.text())).toEqual(['원격 반영', '브랜치 보관'])
    const toggles = switches.findAll('[role="switch"]')
    expect(toggles.map(t => t.attributes('data-test'))).toEqual(['check-settings-push', 'check-settings-keep-branch'])
    expect(toggles.every(t => t.classes().includes('ui-toggle'))).toBe(true)
    expect(toggles.map(t => t.attributes('aria-checked'))).toEqual(['true', 'false'])
    // Saving is unchanged: one PATCH per switch with the current version.
    await toggles[1].trigger('click'); await flushPromises()
    expect(http.patch).toHaveBeenLastCalledWith(expect.stringContaining('/settings'), { field: 'keep_branch', value: true, expected_settings_version: 6 })
    expect(w.get('[data-test="check-settings-keep-branch"]').attributes('aria-checked')).toBe('true')
  })
})


// flowgate.v02test.0002 T-b (0006-T, R0001 requirement 3): before the confirmation, a lookup timeout / failure keeps the
// last candidates the server confirmed (memory only, 5 minutes, same project / flow / card / work base). The live lookup
// status stays as answered, no name is invented, and every choice is still judged by the PATCH.
describe('0006-T T-b: kept confirmed merge targets before the confirmation', () => {
  const LIST = ['feature/v0.2.0', 'main', 'release/local']
  function lookupSucceeds(options: readonly string[] = LIST) { raw.merge_target_options = [...options]; targets(storedTarget()) }
  async function reread() { await context.value!.reload(); await flushPromises() }
  const control = (w: VueWrapper) => w.get('[data-test="check-settings-target"]')
  const refresh = (w: VueWrapper) => w.get('[data-test="check-settings-refresh"]')
  function expectNothingKept(w: VueWrapper, name: string) {
    expect(optionNames(w)).toEqual([])
    expect(control(w).attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="check-settings-target-display"]').text()).toBe(name)
    expect(w.find('[data-test="check-settings-target-state"]').exists()).toBe(true)
    expect(refresh(w).attributes('disabled')).toBeUndefined()
  }

  it.each(['timeout', 'failed'] as const)('after a %s the selection stays, another kept name is saved by PATCH, and a refresh brings the live list back', async status => {
    const w = await show()
    expect(optionNames(w)).toEqual(LIST)
    failing = status; lookupFails(status); await reread()
    // A lookup failure inside a successful read: no common error, the error line stays, the choice stays open.
    expect(context.value!.status.value).toBe('ready')
    expect(context.value!.settings.value?.mergeTargetState?.status).toBe(status)
    expect(w.get('[data-test="check-settings-target-state"]').text()).toBe(status === 'timeout' ? '대상 조회 시간 초과' : '대상 조회 실패')
    expect(optionNames(w)).toEqual(LIST)
    expect(selected(w)).toBe('feature/v0.2.0')
    expect(control(w).attributes('disabled')).toBeUndefined()
    expect(w.find('[data-test="check-settings-target-display"]').exists()).toBe(false)
    expect(refresh(w).attributes('disabled')).toBeUndefined()
    await control(w).setValue('release/local'); await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(1)
    expect(http.patch).toHaveBeenLastCalledWith(expect.stringContaining('/settings'), { field: 'merge_target_branch', value: 'release/local', expected_settings_version: 6 })
    expect(raw.settings_version).toBe(7)
    expect(context.value!.settings.value?.settingsVersion).toBe(7)
    expect(selected(w)).toBe('release/local')
    // GET again while the lookup still fails: the saved name is still the chosen kept option.
    await reread()
    expect(selected(w)).toBe('release/local')
    expect(optionNames(w)).toEqual(LIST)
    failing = null; lookupSucceeds(['main', 'release/local'])
    await refresh(w).trigger('click'); await flushPromises()
    expect(http.get).toHaveBeenCalledWith(expect.stringContaining('/settings'), { refresh_merge_targets: true })
    expect(optionNames(w)).toEqual(['main', 'release/local'])
    expect(selected(w)).toBe('release/local')
    expect(w.find('[data-test="check-settings-target-state"]').exists()).toBe(false)
    // F5 keeps nothing in memory: the stored choice comes back from the server alone.
    context.value!.dropTargets(); await reread()
    expect(selected(w)).toBe('release/local')
    expect(raw.fields.merge_target_branch).toEqual(field('release/local', true))
  })

  it('each confirmed answer replaces the kept list; names beside a failed answer are never listed', async () => {
    const w = await show()
    lookupSucceeds(['main']); await reread()
    expect(optionNames(w)).toEqual(['main'])
    lookupFails('failed'); await reread()
    expect(optionNames(w)).toEqual(['main'])
    // A failed answer carrying names (not the server contract) is still no live list.
    raw.merge_target_options = ['invented']; await reread()
    expect(optionNames(w)).toEqual(['main'])
    expect(optionNames(w)).not.toContain('invented')
  })

  it('does not use the kept list once it is 5 minutes old: no invented list, the error and the refresh stay', async () => {
    const now = vi.spyOn(Date, 'now')
    try {
      now.mockReturnValue(1_000_000)
      const w = await show()
      lookupFails('timeout')
      now.mockReturnValue(1_000_000 + MERGE_TARGET_CACHE_MS - 1); await reread()
      expect(optionNames(w)).toEqual(LIST)
      now.mockReturnValue(1_000_000 + MERGE_TARGET_CACHE_MS); await reread()
      expectNothingKept(w, 'feature/v0.2.0')
      expect(http.patch).not.toHaveBeenCalled()
    } finally { now.mockRestore() }
  })

  it('drops the kept list by its own timer after 5 minutes', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
    try {
      const w = mount(defineComponent({ setup() {
        context = useGateContext(() => target)
        return () => h(CheckSettings, { target })
      } }), { global: { plugins: [i18n] } })
      wrappers.push(w)
      await context.value!.reload()
      expect(context.value!.targetCache.value).toMatchObject({ projectId: target.projectId, flowId: target.flowId, nodeKey: target.nodeId, workBase: 'feature/v0.2.0', options: LIST })
      vi.advanceTimersByTime(MERGE_TARGET_CACHE_MS - 1)
      expect(context.value!.targetCache.value).not.toBeNull()
      vi.advanceTimersByTime(1)
      expect(context.value!.targetCache.value).toBeNull()
    } finally { vi.useRealTimers() }
  })

  it('does not use the kept list for another work base, and does not bring it back for the old one', async () => {
    const w = await show()
    lookupFails('timeout', 'main'); await reread()
    expectNothingKept(w, 'main')
    lookupFails('timeout', 'feature/v0.2.0'); await reread()
    expectNothingKept(w, 'feature/v0.2.0')
  })

  it('does not use the kept list for another project', async () => {
    await show()
    lookupFails('timeout'); await reread()
    expect(context.value!.cachedTargets(target.projectId)).toEqual(LIST)
    expect(context.value!.cachedTargets(`${target.projectId}_other`)).toBeNull()
  })

  it.each(['nodeId', 'flowId'] as const)('drops the kept list when the Inspector moves to another %s and back', async key => {
    const first = target
    const shown = shallowRef(first)
    const w = mount(defineComponent({ setup() {
      context = useGateContext(() => shown.value)
      return () => h(CheckSettings, { target: shown.value })
    } }), { global: { plugins: [i18n] } })
    wrappers.push(w); await flushPromises()
    const kept = context.value!
    expect(kept.targetCache.value?.options).toEqual(LIST)
    shown.value = key === 'nodeId' ? { ...first, nodeId: `${first.nodeId}_other` } : { ...first, flowId: `${first.flowId}_other` }
    await flushPromises()
    expect(kept.targetCache.value).toBeNull()
    lookupFails('timeout'); shown.value = first; await flushPromises()
    expectNothingKept(w, 'feature/v0.2.0')
  })

  // Review rej_01M6P2MB5DH1YH84: an answer still on its way when the Inspector leaves the card must not keep its
  // candidates again. Order: request held -> switch -> (the old answer arrives -> back | back -> the old answer arrives)
  // -> the read after the return fails: no list from before the switch.
  async function switchable(key: 'nodeId' | 'flowId') {
    const first = target
    const shown = shallowRef(first)
    const w = mount(defineComponent({ setup() {
      context = useGateContext(() => shown.value)
      return () => h(CheckSettings, { target: shown.value })
    } }), { global: { plugins: [i18n] } })
    wrappers.push(w); await flushPromises()
    const other = key === 'nodeId' ? { ...first, nodeId: `${first.nodeId}_other` } : { ...first, flowId: `${first.flowId}_other` }
    return { w, kept: context.value!, away: () => { shown.value = other }, back: () => { shown.value = first } }
  }
  /** Holds the next answer of `mock` (taken when sent) until `release()`. */
  function holdNext(mock: typeof http.get, matches: (url: string) => boolean) {
    const answer = mock.getMockImplementation()!
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    let armed = true
    mock.mockImplementation(async (url: string, ...rest: unknown[]) => {
      if (!armed || !matches(url)) return answer(url, ...rest)
      armed = false
      const value = await answer(url, ...rest)
      await held
      return value
    })
    return () => release()
  }

  it.each((['nodeId', 'flowId'] as const).flatMap(key => (['arrives-away', 'arrives-back'] as const).map(order => ({ key, order }))))(
    'a GET sent before moving to another $key keeps no list when its ok answer arrives late ($order)', async ({ key, order }) => {
      const { w, kept, away, back } = await switchable(key)
      expect(kept.targetCache.value?.options).toEqual(LIST)
      const release = holdNext(http.get, url => url.endsWith('/settings'))
      const late = kept.reload()
      await flushPromises()
      away(); await flushPromises()
      expect(kept.targetCache.value).toBeNull()
      if (order === 'arrives-away') {
        release(); await late; await flushPromises()
        // The old ok answer arrived while away: it may set the values, never the kept list.
        expect(kept.settings.value?.mergeTargetState?.status).toBe('ok')
        expect(kept.targetCache.value).toBeNull()
        lookupFails('timeout'); back(); await flushPromises()
      } else {
        back(); await flushPromises()
        release(); await late; await flushPromises()
        // Back on the card the old answer is a live ok answer (shown as live), but it keeps nothing.
        expect(optionNames(w)).toEqual(LIST)
        expect(kept.targetCache.value).toBeNull()
        lookupFails('timeout'); await kept.reload(); await flushPromises()
      }
      expect(kept.settings.value?.mergeTargetState?.status).toBe('timeout')
      expect(kept.cachedTargets()).toBeNull()
      expectNothingKept(w, 'feature/v0.2.0')
      expect(http.patch).not.toHaveBeenCalled()
      // A read sent after the return keeps candidates again.
      lookupSucceeds(); await kept.reload(); await flushPromises()
      expect(kept.targetCache.value?.options).toEqual(LIST)
    })

  it('a PATCH answered after the Inspector moved away keeps its saved value but no list', async () => {
    const { w, kept, away, back } = await switchable('nodeId')
    const release = holdNext(http.patch, url => url.endsWith('/settings'))
    const saving = kept.change('merge_target_branch', 'main')
    await flushPromises()
    away(); await flushPromises()
    expect(kept.targetCache.value).toBeNull()
    release(); expect(await saving).toBe(true); await flushPromises()
    // The saved answer is authoritative for the value and the version, not for keeping candidates.
    expect(kept.settings.value?.settingsVersion).toBe(7)
    expect(kept.targetCache.value).toBeNull()
    lookupFails('timeout'); back(); await flushPromises()
    expect(kept.cachedTargets()).toBeNull()
    expectNothingKept(w, 'main')
  })

  // Review rej_01M6QXCBVZYSAGRV: a PATCH sent before moving to another check card and refused after the move reads the card
  // again; that follow-up read carries the PATCH's right (none since the move), so its ok answer keeps nothing.
  it.each(['merge_target_not_found', 'merge_target_busy'] as const)(
    'a PATCH refused (%s) after the Inspector moved to another check card keeps no list through its follow-up read', async code => {
      const { w, kept, away, back } = await switchable('nodeId')
      expect(kept.targetCache.value?.options).toEqual(LIST)
      let release!: () => void
      const held = new Promise<void>(resolve => { release = resolve })
      http.patch.mockImplementationOnce(async () => { await held; throw refusal(code) })
      const saving = kept.change('merge_target_branch', 'main')
      await flushPromises()
      away(); await flushPromises()
      expect(kept.targetCache.value).toBeNull()
      const reads = http.get.mock.calls.filter(([url]) => url.endsWith(`/nodes/${encodeURIComponent(target.nodeId)}/settings`)).length
      release(); expect(await saving).toBe(false); await flushPromises()
      // The refusal is read again and the follow-up GET answers ok with the live list, but keeps nothing for the card left.
      expect(http.get.mock.calls.filter(([url]) => url.endsWith(`/nodes/${encodeURIComponent(target.nodeId)}/settings`)).length).toBe(reads + 1)
      expect(kept.settings.value?.mergeTargetState?.status).toBe('ok')
      expect(kept.settings.value?.settingsVersion).toBe(6)
      expect(kept.targetCache.value).toBeNull()
      expect(kept.cachedTargets()).toBeNull()
      lookupFails('timeout'); back(); await flushPromises()
      expect(kept.settings.value?.mergeTargetState?.status).toBe('timeout')
      expect(kept.cachedTargets()).toBeNull()
      // No list from before the move: the control is off with the saved name; the refusal stays as the control error.
      expect(optionNames(w)).toEqual([])
      expect(control(w).attributes('disabled')).toBeDefined()
      expect(w.get('[data-test="check-settings-target-display"]').text()).toBe('feature/v0.2.0')
      expect(w.get('[data-test="check-settings-target-error"]').text()).toBe(i18n.global.t(`gui.inspector.checkSettings.${checkTargetErrorKey(code)}`))
      expect(refresh(w).attributes('disabled')).toBeUndefined()
      expect(http.patch).toHaveBeenCalledTimes(1)
    })

  it('a lookup failure from the first read offers no list, shows the saved name and keeps the refresh open', async () => {
    raw.fields.merge_target_branch = field('release/local', true); lookupFails('timeout')
    const w = await show()
    expectNothingKept(w, 'release/local')
    expect(context.value!.targetCache.value).toBeNull()
    await refresh(w).trigger('click'); await flushPromises()
    expect(http.get).toHaveBeenCalledWith(expect.stringContaining('/settings'), { refresh_merge_targets: true })
    expectNothingKept(w, 'release/local')
    expect(http.patch).not.toHaveBeenCalled()
  })

  it('a Git refusal of the lookup drops the kept list', async () => {
    const w = await show()
    const refused = entry('feature/v0.2.0', { error: 'gate_git_unavailable', unmerge_supported: null, warnings: [] })
    raw.merge_target_options = []
    raw.merge_target_state = { status: 'git_unavailable', code: 'gate_git_unavailable', reason: 'git_disabled', current: refused, default: refused }
    await reread()
    expect(optionNames(w)).toEqual([])
    expect(control(w).attributes('disabled')).toBeDefined()
    lookupFails('timeout'); await reread()
    expectNothingKept(w, 'feature/v0.2.0')
  })

  it.each([['merge_target_not_found', false], ['merge_target_same_branch', false], ['merge_target_busy', true]] as const)(
    'a %s refusal of a kept name keeps the saved value (kept list stays: %s) and reads the card again', async (code, keeps) => {
      const w = await show()
      lookupFails('timeout'); await reread()
      const reads = http.get.mock.calls.filter(([url]) => url.endsWith('/settings')).length
      http.patch.mockRejectedValueOnce(refusal(code))
      await control(w).setValue('release/local'); await flushPromises()
      expect(raw.settings_version).toBe(6)
      expect(raw.fields.merge_target_branch).toEqual(field(null))
      expect(http.get.mock.calls.filter(([url]) => url.endsWith('/settings')).length).toBe(reads + 1)
      expect(w.get('[data-test="check-settings-target-error"]').text()).toBe(i18n.global.t(`gui.inspector.checkSettings.${checkTargetErrorKey(code)}`))
      if (keeps) {
        expect(optionNames(w)).toEqual(LIST)
        expect(selected(w)).toBe('feature/v0.2.0')
      } else {
        expect(optionNames(w)).toEqual([])
        expect(context.value!.targetCache.value).toBeNull()
        expect(w.get('[data-test="check-settings-target-display"]').text()).toBe('feature/v0.2.0')
      }
    })

  it('a version conflict keeps the kept list and the confirmed value', async () => {
    const w = await show()
    lookupFails('timeout'); await reread()
    http.patch.mockRejectedValueOnce(refusal('gate_context_changed', 'settings_version'))
    await control(w).setValue('main'); await flushPromises()
    expect(raw.settings_version).toBe(6)
    expect(optionNames(w)).toEqual(LIST)
    expect(selected(w)).toBe('feature/v0.2.0')
  })

  it('a GET answered after the save overwrites neither the saved value nor the kept list, and the controls do not stay waiting', async () => {
    const w = await show()
    const stale = structuredClone(raw)
    stale.merge_target_options = ['old-only']
    let answerPatch!: () => void
    http.patch.mockImplementationOnce((_url: string, body: any) => new Promise(resolve => {
      answerPatch = () => {
        raw.fields.merge_target_branch = field(body.value, true); raw.settings_version++; targets(body.value)
        resolve({ data: structuredClone(raw) })
      }
    }))
    let staleOnce = true
    let releaseStale!: () => void
    http.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/settings')) {
        if (staleOnce) { staleOnce = false; await new Promise<void>(r => { releaseStale = r }); return { data: stale } }
        return { data: structuredClone(raw) }
      }
      if (url.endsWith('/runs/current')) return { data: { run: structuredClone(current) } }
      return { data: {} }
    })
    await control(w).setValue('release/local')
    void context.value!.reload()                 // an event's read, started while the PATCH is unanswered
    await flushPromises()
    answerPatch(); await flushPromises()
    releaseStale(); await flushPromises()
    expect(selected(w)).toBe('release/local')
    expect(optionNames(w)).toEqual(LIST)
    expect(optionNames(w)).not.toContain('old-only')
    expect(context.value!.targetCache.value?.options).toEqual(LIST)
    expect(context.value!.settings.value?.settingsVersion).toBe(7)
    expect(context.value!.status.value).toBe('ready')
    expect(control(w).attributes('disabled')).toBeUndefined()
  })

  it.each(['settings', 'runs/current'])('keeps the safety lock when the %s read itself fails, even with a kept list', async failed => {
    const w = await show()
    lookupFails('timeout')
    http.get.mockImplementation(async (url: string) => {
      if (url.endsWith(`/${failed}`)) throw { isAxiosError: true, response: { status: 500, data: {} } }
      if (url.endsWith('/settings')) return { data: structuredClone(raw) }
      if (url.endsWith('/runs/current')) return { data: { run: structuredClone(current) } }
      return { data: {} }
    })
    await reread()
    expect(context.value!.status.value).toBe('error')
    expect(control(w).attributes('disabled')).toBeDefined()
    // A failed settings read keeps the last answer (lookup ok: no refresh drawn); a failed run read keeps it locked.
    const button = w.find('[data-test="check-settings-refresh"]')
    expect(button.exists()).toBe(failed === 'runs/current')
    if (button.exists()) expect(button.attributes('disabled')).toBeDefined()
    await control(w).setValue('main'); await flushPromises()
    expect(http.patch).not.toHaveBeenCalled()
  })

  it.each([
    ['the card is locked', () => { raw.locked = true; raw.lock_reason = 'decision_in_progress' }],
    ['the gate locks its settings', () => { current.node_runs[0].gate.settings_locked = true }],
  ] as const)('keeps the safety lock when %s, even with a kept list', async (_name, lock) => {
    const w = await show()
    lookupFails('timeout'); lock(); await reread()
    expect(control(w).attributes('disabled')).toBeDefined()
    expect(refresh(w).attributes('disabled')).toBeDefined()
    await control(w).setValue('main'); await flushPromises()
    expect(http.patch).not.toHaveBeenCalled()
  })

  it.each([
    { run: 'idle', idleReason: 'waiting_confirm', node: 'open' },
    { run: 'running', idleReason: null, node: 'open' },
    { run: 'running', idleReason: null, node: 'pending' },
  ])('before the confirmation (run $run / $idleReason, card $node) another real candidate is saved with a new version, stays after GET, and confirm carries that version', async s => {
    current.status = s.run; current.idle_reason = s.idleReason; current.node_runs[0].status = s.node
    const w = await show()
    await control(w).setValue('main'); await flushPromises()
    expect(http.patch).toHaveBeenLastCalledWith(expect.any(String), { field: 'merge_target_branch', value: 'main', expected_settings_version: 6 })
    expect(raw.settings_version).toBe(7)
    await reread()
    expect(selected(w)).toBe('main')
    http.post.mockResolvedValueOnce({ data: { run: structuredClone(current) } })
    expect(await context.value!.decide('confirm')).toBe(true)
    expect(http.post).toHaveBeenLastCalledWith(expect.stringContaining(`/nodes/${target.nodeId}/gate-decision`), expect.objectContaining({ decision: 'confirm', expected_settings_version: 7 }))
  })
})
