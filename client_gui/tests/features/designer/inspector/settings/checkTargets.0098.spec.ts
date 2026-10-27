import { defineComponent, h } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(),
  getRequest: (...a: unknown[]) => http.get(...a), patchRequest: (...a: unknown[]) => http.patch(...a), postRequest: (...a: unknown[]) => http.post(...a) }))
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import GateSettingsSummary from '@/features/designer/inspector/features/gateCard/GateSettingsSummary.vue'
import { useGateContext } from '@/features/designer/inspector/features/gateCard/useGateContext'
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
const refusal = (code: string, reason?: string) => ({ isAxiosError: true, response: { status: 409, data: { error: { code, params: { reason } } } } })
function targets(selected: string | null) {
  raw.merge_target_state = { status: raw.merge_target_options.length ? 'ok' : 'empty', code: null, reason: null,
    current: entry(selected ?? raw.default_target_branch), default: entry(raw.default_target_branch) }
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
  Object.values(http).forEach(m => m.mockReset()); i18n.global.locale.value = 'ko'
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
    targets(raw.fields.merge_target_branch.value)
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
    expect(w.get('[data-test="check-settings-target-state"]').text()).toBe(status === 'git_unavailable' ? 'Git 사용 불가' : '대상 조회 실패')
    expect(control.attributes('disabled')).toBeDefined()
    expect(control.attributes('aria-invalid')).toBe('false')
    expect(control.classes()).not.toContain('is-invalid')
    expect(w.get('[data-test="check-settings-target-display"]').text()).toBe(name)
    expect(w.find('[data-test="check-settings-target-error"]').exists()).toBe(false)
    expect(optionNames(w)).toEqual([])
    if (status === 'failed' || status === 'timeout') {
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
    expect(w.find('[data-test="gate-summary-error"]').exists()).toBe(false)
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
    const receipt: any = { decision_id: 'd1', attempt_id: 'attempt0098', result: 'confirmed', status: 'succeeded', archived: false, effects: [{ phase: 'merge', status: 'done', sha_after: 'abc' }],
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
    expect(w.find('[data-test="gate-summary-push-create"]').exists()).toBe(true)
    // The card's document tab (GateSlots) passes hideTarget: neither the target line nor a substitute is drawn.
    await w.setProps({ hideTarget: true })
    expect(w.find('[data-test="gate-summary-target"]').exists()).toBe(false)
    expect(w.text()).not.toContain('병합 대상')
    expect(w.text()).not.toContain('release/local')
  })
  it('the document tab without a decision draws no target line and no unsupported-undo notice', async () => {
    const w = mount(GateSettingsSummary, { props: { node: toSettingsNode(raw), hideTarget: true }, global: { plugins: [i18n] } }); wrappers.push(w)
    expect(raw.merge_target_state.current.unmerge_supported).toBe(false)
    expect(w.find('[data-test="gate-settings-summary"]').exists()).toBe(false)
    expect(w.text()).toBe('')
  })
})
