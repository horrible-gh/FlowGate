/**
 * flowgate.v02test.0081 (0029-T, 시안 v4) — the time settings as [number][unit ▾] boxes over seconds:
 * - every run card's 실행 AI ⚙: 실행 제한 시간 [4][시간] · 검증 1회 시간 [1][분] (`time_limit_sec` / `verify_run_sec`);
 * - the test card's one ⚙, beside the test run zone's 작업 AI (none in the case writing zone): 자동 케이스 · AI 판정
 *   [케이스 수 | 고정] (케이스 수 by default) · 1회 시간, and 횟수 only while 고정; a yellow dot once a value is not its default;
 *   the AI tab keeps its rows.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: http.get, patchRequest: http.patch }))
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import InspectorDurationInput from '@/features/designer/inspector/parts/InspectorDurationInput.vue'
import InspectorRunSettings from '@/features/designer/inspector/parts/InspectorRunSettings.vue'
import { target } from '../../ai/rolesFixtures.0113'
import { prepareInspectorI18n } from './support/inspectorSupport'
import ko from '@/app/i18n/ko'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import messages from '@/features/designer/inspector/features/settings/messages'

const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null

const TEST_TIME = { auto_case_timeout_sec: 120, ai_judge_mode: 'case_count', ai_judge_run_sec: 120, ai_judge_runs: 5 }
const RUN_TIME = { time_limit_sec: 14400, verify_max_runs: 10, verify_run_sec: 60 }

function settingsRaw(kind: 'run' | 'test', values: Record<string, unknown> = {}): any {
  const base: Record<string, unknown> = kind === 'test'
    ? { instruction: 'use', run_ai: null, review_ai: null, review_count: 1, case_review_count: 1, result_cause: 'ai', follow_scope: 'summary', ...TEST_TIME }
    : { instruction: 'use', instruction_ai: null, run_ai: null, review_ai: null, review_count: 1, restart: 3, review_basis: 'type', after_review_exhausted: 'continue', instruction_review_ai: null, instruction_review_count: 0, ...RUN_TIME }
  const defaults: Record<string, unknown> = { ...base }
  const all = { ...base, ...values }
  const choices = (name: string) => name.endsWith('count') || name === 'restart' ? [-1, 0, 1, 2, 3]
    : name === 'ai_judge_mode' ? ['case_count', 'fixed'] : name === 'ai_judge_runs' || name === 'verify_max_runs' ? [1, 2, 3, 5, 10]
      : name === 'review_basis' ? ['type', 'last'] : name === 'after_review_exhausted' ? ['continue', 'stop'] : name === 'instruction' ? ['use', 'omit']
        : name === 'result_cause' ? ['ai', 'human'] : name === 'follow_scope' ? ['summary', 'full'] : null
  const fields = Object.fromEntries(Object.entries(all).map(([name, value]) => [name, { value, stored: name in values ? value : null, default: defaults[name], choices: choices(name) }]))
  return { card_kind: kind, settings_version: 3, locked: false, fields, ai_roles: { options_state: 'ok', options: [{ id: 'a', name: 'Claude' }], card_init_state: 'explicit', has_legacy_roles: false, preserved: {},
    roles: {
      ...(kind === 'run' ? { instruction: { selected: { kind: 'not_used' }, state: 'not_used', outcome: 'none', legacy_flag: false, source: 'card_explicit' } } : {}),
      execution: { selected: { kind: 'assigned', provider_id: 'a' }, state: 'assigned_ok', outcome: 'ai', display_name: 'Claude', provider_id: 'a', legacy_flag: false, source: 'card_explicit' },
      review: { selected: { kind: 'assigned', provider_id: 'a' }, state: 'assigned_ok', outcome: 'ai', display_name: 'Claude', provider_id: 'a', legacy_flag: false, source: 'card_explicit' },
    } } }
}

let raw: any
beforeEach(() => {
  vi.clearAllMocks(); i18n.global.locale.value = 'ko'
  http.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/settings')) return { data: structuredClone(raw) }
    if (url.endsWith('/providers')) return { data: { providers: [{ id: 'a', name: 'Claude' }] } }
    if (url.endsWith('/ai-invocations')) return { data: { ok: true, available: false, invocations: [], next_cursor: null } }
    throw new Error('offline')
  })
  http.patch.mockImplementation(async (_url: string, body: any) => {
    if (body.field.endsWith('_role')) raw.ai_roles.roles[body.field.replace('_role', '')].selected = body.value
    else { raw.fields[body.field].value = body.value; raw.fields[body.field].stored = body.value }
    raw.settings_version++
    return { data: structuredClone(raw) }
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null })

async function tab(kind: 'run' | 'test', values: Record<string, unknown> = {}) {
  raw = settingsRaw(kind, values)
  wrapper = mount(SettingsTab, { props: { target: target(kind === 'test' ? 'TS' : 'T'), panel: 'ai' }, global: { plugins: [i18n] } })
  await flushPromises(); return wrapper
}
async function gear(w: VueWrapper<any>, role = 'execution') {
  await w.get(`[data-gear="${role}"]`).trigger('click'); await flushPromises()
  return w.get(`[data-test="inspector-ai-advanced"][data-for="${role}"]`)
}
const labels = (w: { findAll: VueWrapper<any>['findAll'] }) => w.findAll('[data-test="inspector-control-row"]').map(row => row.attributes('data-label'))
const boxes = (w: { get: VueWrapper<any>['get'] }, field: string) => {
  const amount = w.get(`[data-test="inspector-run-settings-${field}-amount"]`).element as HTMLInputElement
  const unit = w.get(`[data-test="inspector-run-settings-${field}-unit"]`).element as HTMLSelectElement
  return [amount.value, unit.value]
}
async function typeDuration(w: VueWrapper<any>, field: string, text: string) {
  const box = w.get(`[data-test="inspector-run-settings-${field}-amount"]`)
  // (0030-TR rework) the number is taken when focus leaves the [number][unit] pair (focusout to outside it), not on change
  await box.setValue(text); await box.trigger('focusout'); await flushPromises()
}
const sent = () => http.patch.mock.calls.map(call => [call[1].field, call[1].value])

describe('every run card — 실행 AI ⚙: the two time settings are [number][unit]', () => {
  it('shows 실행 제한 시간 [4][시간] and 검증 1회 시간 [1][분], in the largest unit that divides them', async () => {
    const w = await tab('run')
    const pop = await gear(w)
    expect(labels(pop)).toEqual(['재시작', '실행 제한 시간', '검증 최대 횟수', '검증 1회 시간'])
    expect(boxes(pop, 'time_limit_sec')).toEqual(['4', 'hour'])
    expect(boxes(pop, 'verify_run_sec')).toEqual(['1', 'minute'])
    expect(pop.get('[data-test="inspector-run-settings-time_limit_sec-unit"]').findAll('option').map(option => option.text())).toEqual(['시간', '분', '초'])
    expect(w.get('[data-gear="execution"]').attributes('data-modified')).toBeUndefined()
    expect(http.patch).not.toHaveBeenCalled()
  })

  it('sends number × unit as seconds: 90 분 → 5400; a unit picked keeps the number (2 분 → 2 시간 is out of range → 1800)', async () => {
    const w = await tab('run')
    await gear(w)
    await w.get('[data-test="inspector-run-settings-time_limit_sec-unit"]').setValue('minute'); await flushPromises()
    // [4][분] = 240 seconds is below 30 minutes: moved to the lowest value and said so
    expect(sent().at(-1)).toEqual(['time_limit_sec', 1800])
    expect(w.get('[data-test="inspector-run-settings-time_limit_sec-note"]').text()).toBe('최소값 30분에 맞췄습니다')
    expect(boxes(w, 'time_limit_sec')).toEqual(['30', 'minute'])
    await typeDuration(w, 'time_limit_sec', '90')
    expect(sent().at(-1)).toEqual(['time_limit_sec', 5400])
    expect(w.find('[data-test="inspector-run-settings-time_limit_sec-note"]').exists()).toBe(false)
    expect(boxes(w, 'time_limit_sec')).toEqual(['90', 'minute'])
    expect(w.get('[data-gear="execution"]').attributes('data-modified')).toBe('true')
    await typeDuration(w, 'verify_run_sec', '45')
    expect(sent().at(-1)).toEqual(['verify_run_sec', 1800])
    expect(w.get('[data-test="inspector-run-settings-verify_run_sec-note"]').text()).toBe('최대값 30분에 맞췄습니다')
  })

  it('a number that is not a whole number is not sent; the shown value comes back', async () => {
    const w = await tab('run')
    await gear(w)
    await typeDuration(w, 'time_limit_sec', '2.5')
    expect(http.patch).not.toHaveBeenCalled()
    expect(boxes(w, 'time_limit_sec')).toEqual(['4', 'hour'])
  })
})

describe('the test card — one ⚙ beside the test run 작업 AI', () => {
  it('the AI tab keeps its rows: case writing (작업 AI · 검수 AI · 검수 횟수), test run (작업 AI · 검수 AI · 검수 횟수); only the test run 작업 AI has a ⚙', async () => {
    const w = await tab('test')
    expect(labels(w)).toEqual(['작업 AI', '검수 AI', '검수 횟수', '작업 AI', '검수 AI', '검수 횟수'])
    expect(w.findAll('[data-test="inspector-ai-gear"]').map(item => item.attributes('data-gear'))).toEqual(['execution'])
    expect(w.find('[data-zone="case_write"] [data-test="inspector-ai-gear"]').exists()).toBe(false)
    const pop = await gear(w)
    expect(pop.get('.inspector-ai-pop-title').text()).toBe('테스트 실행 시간 설정')
    // the window's rows do not count to the tab's: the tab's own rows are the same with it open
    expect(labels(w).filter(label => !labels(pop).includes(label as string))).toEqual(['작업 AI', '검수 AI', '검수 횟수', '작업 AI', '검수 AI', '검수 횟수'])
  })

  it('케이스 수 is the default and shows no 횟수 row; 자동 케이스 [2][분] · 1회 시간 [2][분]', async () => {
    const w = await tab('test')
    const pop = await gear(w)
    expect(labels(pop)).toEqual(['자동 케이스', 'AI 판정', '1회 시간'])
    const mode = pop.get('[data-test="inspector-run-settings-ai_judge_mode"]')
    expect(mode.findAll('[data-test="inspector-segment-option"]').map(option => [option.text(), option.attributes('aria-checked')])).toEqual([['케이스 수', 'true'], ['고정', 'false']])
    expect(boxes(pop, 'auto_case_timeout_sec')).toEqual(['2', 'minute'])
    expect(boxes(pop, 'ai_judge_run_sec')).toEqual(['2', 'minute'])
    expect(pop.find('[data-test="inspector-run-settings-ai_judge_runs"]').exists()).toBe(false)
    expect(w.get('[data-gear="execution"]').attributes('data-modified')).toBeUndefined()
    expect(http.patch).not.toHaveBeenCalled()
  })

  it('고정 shows the 횟수 row (1 · 2 · 3 · 5 · 10회, 사용자 정의) and marks the ⚙; back to 케이스 수 hides it and keeps the count', async () => {
    const w = await tab('test')
    await gear(w)
    await w.get('[data-test="inspector-run-settings-ai_judge_mode"] [data-value="fixed"]').trigger('click'); await flushPromises()
    expect(sent()).toEqual([['ai_judge_mode', 'fixed']])
    const pop = w.get('[data-test="inspector-ai-advanced"]')
    expect(labels(pop)).toEqual(['자동 케이스', 'AI 판정', '1회 시간', '횟수'])
    const runs = pop.get('[data-test="inspector-run-settings-ai_judge_runs"]')
    expect(runs.findAll('option').map(option => option.text())).toEqual(['1회', '2회', '3회', '5회', '10회', '사용자 정의'])
    expect((runs.element as HTMLSelectElement).value).toBe('5')
    expect(w.get('[data-gear="execution"]').attributes('data-modified')).toBe('true')
    await runs.setValue('3'); await flushPromises()
    expect(sent().at(-1)).toEqual(['ai_judge_runs', 3])
    await w.get('[data-test="inspector-run-settings-ai_judge_mode"] [data-value="case_count"]').trigger('click'); await flushPromises()
    expect(sent().at(-1)).toEqual(['ai_judge_mode', 'case_count'])
    expect(w.find('[data-test="inspector-run-settings-ai_judge_runs"]').exists()).toBe(false)
    expect(raw.fields.ai_judge_runs.value).toBe(3)
    expect(sent().map(([field]) => field)).not.toContain('ai_judge_runs_reset')
  })

  it('a time setting sends seconds (5 분 → 300) and marks the ⚙', async () => {
    const w = await tab('test')
    await gear(w)
    await typeDuration(w, 'auto_case_timeout_sec', '5')
    expect(sent()).toEqual([['auto_case_timeout_sec', 300]])
    expect(w.get('[data-gear="execution"]').attributes('data-modified')).toBe('true')
    await w.get('[data-test="inspector-run-settings-ai_judge_run_sec-unit"]').setValue('second'); await flushPromises()
    expect(sent().at(-1)).toEqual(['ai_judge_run_sec', 60])
  })

  it('a stored 고정 opens with the 횟수 row; a stored 150 seconds shows [150][초]', async () => {
    const w = await tab('test', { ai_judge_mode: 'fixed', ai_judge_runs: 7, auto_case_timeout_sec: 150 })
    const pop = await gear(w)
    expect(labels(pop)).toEqual(['자동 케이스', 'AI 판정', '1회 시간', '횟수'])
    expect((pop.get('[data-test="inspector-run-settings-ai_judge_runs"]').element as HTMLSelectElement).value).toBe('7')
    expect(boxes(pop, 'auto_case_timeout_sec')).toEqual(['150', 'second'])
    expect(http.patch).not.toHaveBeenCalled()
  })

  it('(0030-TR rework) 고정 with 횟수 5 and no server default for 횟수: the contract default 5 applies — no dot on the row or the ⚙', async () => {
    raw = settingsRaw('test', { ai_judge_mode: 'fixed' })
    raw.fields.ai_judge_mode.default = 'fixed'
    delete raw.fields.ai_judge_runs.default
    wrapper = mount(SettingsTab, { props: { target: target('TS'), panel: 'ai' }, global: { plugins: [i18n] } }); await flushPromises()
    const pop = await gear(wrapper)
    expect(labels(pop)).toEqual(['자동 케이스', 'AI 판정', '1회 시간', '횟수'])
    expect(pop.get('[data-test="inspector-run-settings-ai_judge_runs"]').attributes('data-modified')).toBeUndefined()
    expect(wrapper.get('[data-gear="execution"]').attributes('data-modified')).toBeUndefined()
    // a count other than 5 is still marked
    await pop.get('[data-test="inspector-run-settings-ai_judge_runs"]').setValue('3'); await flushPromises()
    expect(wrapper.get('[data-test="inspector-run-settings-ai_judge_runs"]').attributes('data-modified')).toBe('true')
    expect(wrapper.get('[data-gear="execution"]').attributes('data-modified')).toBe('true')
  })

  it('(0030-TR rework) Esc in a time box of the open ⚙ window puts the number back and keeps the window; Esc again closes it', async () => {
    raw = settingsRaw('test')
    wrapper = mount(SettingsTab, { props: { target: target('TS'), panel: 'ai' }, global: { plugins: [i18n] }, attachTo: document.body }); await flushPromises()
    await gear(wrapper)
    const box = wrapper.get('[data-test="inspector-run-settings-auto_case_timeout_sec-amount"]')
    ;(box.element as HTMLInputElement).focus()
    ;(box.element as HTMLInputElement).value = '9'; await box.trigger('input')
    // a real key press: it bubbles from the box up to the document, where the window's Esc handler listens
    box.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await flushPromises()
    expect(wrapper.find('[data-test="inspector-ai-advanced"]').exists()).toBe(true)
    expect((box.element as HTMLInputElement).value).toBe('2')
    expect(http.patch).not.toHaveBeenCalled()
    // nothing typed now: Esc goes on to the document and closes the window
    box.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await flushPromises()
    expect(wrapper.find('[data-test="inspector-ai-advanced"]').exists()).toBe(false)
    expect(http.patch).not.toHaveBeenCalled()
  })

  it('(0030-TR 3rd) Esc in the unit box after Tab from a typed number puts the number back and keeps the window; Esc again closes it', async () => {
    raw = settingsRaw('test')
    wrapper = mount(SettingsTab, { props: { target: target('TS'), panel: 'ai' }, global: { plugins: [i18n] }, attachTo: document.body }); await flushPromises()
    await gear(wrapper)
    const box = wrapper.get('[data-test="inspector-run-settings-auto_case_timeout_sec-amount"]').element as HTMLInputElement
    const unit = wrapper.get('[data-test="inspector-run-settings-auto_case_timeout_sec-unit"]').element as HTMLSelectElement
    box.focus(); box.value = '9'; box.dispatchEvent(new Event('input', { bubbles: true }))
    // Tab: the browser moves the focus to the unit box (blur / focusout with relatedTarget = the unit box) — nothing taken
    unit.focus(); await flushPromises()
    expect(document.activeElement).toBe(unit)
    expect(box.value).toBe('9')
    expect(http.patch).not.toHaveBeenCalled()
    // a real Esc on the closed unit box bubbles up to the document, where the window's Esc handler listens
    unit.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await flushPromises()
    expect(wrapper.find('[data-test="inspector-ai-advanced"]').exists()).toBe(true)
    expect(box.value).toBe('2')
    expect(unit.value).toBe('minute')
    expect(http.patch).not.toHaveBeenCalled()
    // nothing typed now: Esc goes on to the document and closes the window, sending nothing
    unit.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); await flushPromises()
    expect(wrapper.find('[data-test="inspector-ai-advanced"]').exists()).toBe(false)
    expect(http.patch).not.toHaveBeenCalled()
  })

  it('(0030-TR 3rd) a click outside the open ⚙ window takes the typed number before closing it: 2 분 → 5 → 300 sent once', async () => {
    raw = settingsRaw('test')
    const outside = document.createElement('button'); outside.textContent = 'outside'; document.body.appendChild(outside)
    try {
      wrapper = mount(SettingsTab, { props: { target: target('TS'), panel: 'ai' }, global: { plugins: [i18n] }, attachTo: document.body }); await flushPromises()
      await gear(wrapper)
      const box = wrapper.get('[data-test="inspector-run-settings-auto_case_timeout_sec-amount"]').element as HTMLInputElement
      box.focus(); box.value = '5'; box.dispatchEvent(new Event('input', { bubbles: true }))
      // the click's pointerdown reaches the document's capture handler first, which closes the window (no focus moved yet)
      outside.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true })); await flushPromises()
      expect(wrapper.find('[data-test="inspector-ai-advanced"]').exists()).toBe(false)
      expect(sent()).toEqual([['auto_case_timeout_sec', 300]])
      // then the browser moves the focus and clicks: nothing more is sent
      outside.focus(); outside.dispatchEvent(new MouseEvent('click', { bubbles: true })); await flushPromises()
      expect(sent()).toEqual([['auto_case_timeout_sec', 300]])
      expect(raw.fields.auto_case_timeout_sec.value).toBe(300)
      expect(wrapper.get('[data-gear="execution"]').attributes('data-modified')).toBe('true')
    } finally { outside.remove() }
  })

  it('(0030-TR 3rd) a number typed then the unit box, then a click outside: taken once in the shown unit; closing by the ⚙ takes it too', async () => {
    raw = settingsRaw('test')
    wrapper = mount(SettingsTab, { props: { target: target('TS'), panel: 'ai' }, global: { plugins: [i18n] }, attachTo: document.body }); await flushPromises()
    await gear(wrapper)
    let box = wrapper.get('[data-test="inspector-run-settings-ai_judge_run_sec-amount"]').element as HTMLInputElement
    box.focus(); box.value = '3'; box.dispatchEvent(new Event('input', { bubbles: true }))
    ;(wrapper.get('[data-test="inspector-run-settings-ai_judge_run_sec-unit"]').element as HTMLSelectElement).focus()
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true })); await flushPromises()
    expect(wrapper.find('[data-test="inspector-ai-advanced"]').exists()).toBe(false)
    expect(sent()).toEqual([['ai_judge_run_sec', 180]])
    // reopen, type, close with the ⚙ itself (the window's own toggle)
    await gear(wrapper)
    box = wrapper.get('[data-test="inspector-run-settings-auto_case_timeout_sec-amount"]').element as HTMLInputElement
    box.focus(); box.value = '4'; box.dispatchEvent(new Event('input', { bubbles: true }))
    const gearButton = wrapper.get('[data-gear="execution"]').element as HTMLElement
    gearButton.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true }))
    gearButton.dispatchEvent(new MouseEvent('click', { bubbles: true })); await flushPromises()
    expect(wrapper.find('[data-test="inspector-ai-advanced"]').exists()).toBe(false)
    expect(sent()).toEqual([['ai_judge_run_sec', 180], ['auto_case_timeout_sec', 240]])
  })

  it('a server that has no time settings for the card yet draws no ⚙', async () => {
    raw = settingsRaw('test')
    for (const name of Object.keys(TEST_TIME)) delete raw.fields[name]
    wrapper = mount(SettingsTab, { props: { target: target('TS'), panel: 'ai' }, global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.find('[data-test="inspector-ai-gear"]').exists()).toBe(false)
  })
})

describe('InspectorDurationInput', () => {
  const mountInput = (props: Record<string, unknown>) => mount(InspectorDurationInput, { props: { min: 60, max: 1800, testId: 'd', ...props }, global: { plugins: [i18n] } })

  /** types into the box without leaving it (no change event). */
  async function type(box: ReturnType<VueWrapper<any>['get']>, text: string) {
    (box.element as HTMLInputElement).value = text; await box.trigger('input')
  }

  it('Esc puts the shown value back; Enter takes the number', async () => {
    const w = mountInput({ modelValue: 120 })
    const box = w.get('[data-test="d-amount"]')
    await type(box, '7'); await box.trigger('keydown', { key: 'Escape' }); await flushPromises()
    expect((box.element as HTMLInputElement).value).toBe('2')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    await type(box, '7'); await box.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('update:modelValue')).toEqual([[420]])
  })

  it('a number typed above the range is moved to the highest value and said so; one inside it clears the line', async () => {
    const w = mountInput({ modelValue: 120 })
    const box = w.get('[data-test="d-amount"]')
    await type(box, '45'); await box.trigger('keydown', { key: 'Enter' }); await flushPromises()
    expect(w.emitted('update:modelValue')).toEqual([[1800]])
    expect(w.get('[data-test="d-note"]').attributes('data-clamped')).toBe('max')
    await w.setProps({ modelValue: 1800 }); await flushPromises()
    expect((box.element as HTMLInputElement).value).toBe('30')
    await type(box, '10'); await box.trigger('focusout'); await flushPromises()
    expect(w.emitted('update:modelValue')!.at(-1)).toEqual([600])
    expect(w.find('[data-test="d-note"]').exists()).toBe(false)
  })

  it('(0030-TR rework) typing a number and then picking a unit keeps the number: [4][시간] → 90 → 분 sends 5400 once (real focus order)', async () => {
    const w = mount(InspectorDurationInput, { props: { modelValue: 14400, min: 1800, max: 14400, testId: 'd' }, global: { plugins: [i18n] }, attachTo: document.body })
    const box = w.get('[data-test="d-amount"]').element as HTMLInputElement
    const unit = w.get('[data-test="d-unit"]').element as HTMLSelectElement
    box.focus()
    box.value = '90'; box.dispatchEvent(new Event('input', { bubbles: true }))
    // what a browser does on a click on the unit box: the number box's change, then focus moves (blur / focusout with
    // the unit box as relatedTarget), then the unit box's change
    box.dispatchEvent(new Event('change', { bubbles: true }))
    unit.focus()
    expect(document.activeElement).toBe(unit)
    await flushPromises()
    expect(w.emitted('update:modelValue')).toBeUndefined()
    expect(box.value).toBe('90')
    unit.value = 'minute'; unit.dispatchEvent(new Event('change', { bubbles: true })); await flushPromises()
    expect(w.emitted('update:modelValue')).toEqual([[5400]])
    await w.setProps({ modelValue: 5400 }); await flushPromises()
    expect([box.value, unit.value]).toEqual(['90', 'minute'])
    expect(w.find('[data-test="d-note"]').exists()).toBe(false)
    // leaving the pair from the unit box without another pick sends nothing more
    unit.blur(); await flushPromises()
    expect(w.emitted('update:modelValue')).toEqual([[5400]])
    w.unmount()
  })

  it('(0030-TR rework) a number typed, focus to the unit box and then out of the pair: taken once in the shown unit', async () => {
    const outside = document.createElement('button'); document.body.appendChild(outside)
    const w = mount(InspectorDurationInput, { props: { modelValue: 120, min: 60, max: 1800, testId: 'd' }, global: { plugins: [i18n] }, attachTo: document.body })
    const box = w.get('[data-test="d-amount"]').element as HTMLInputElement
    const unit = w.get('[data-test="d-unit"]').element as HTMLSelectElement
    box.focus(); box.value = '5'; box.dispatchEvent(new Event('input', { bubbles: true }))
    unit.focus(); await flushPromises()
    expect(w.emitted('update:modelValue')).toBeUndefined()
    outside.focus(); await flushPromises()
    expect(w.emitted('update:modelValue')).toEqual([[300]])
    w.unmount(); outside.remove()
  })

  it('(0030-TR rework) a negative whole number is moved to the lowest value and said so', async () => {
    const w = mountInput({ modelValue: 120 })
    const box = w.get('[data-test="d-amount"]')
    await type(box, '-1'); await box.trigger('keydown', { key: 'Enter' }); await flushPromises()
    expect(w.emitted('update:modelValue')).toEqual([[60]])
    expect(w.get('[data-test="d-note"]').attributes('data-clamped')).toBe('min')
    expect(w.get('[data-test="d-note"]').text()).toBe('최소값 1분에 맞췄습니다')
  })

  it('no value: empty box; disabled: nothing is sent', async () => {
    const w = mountInput({ modelValue: null, disabled: true })
    expect((w.get('[data-test="d-amount"]').element as HTMLInputElement).value).toBe('')
    await w.get('[data-test="d-amount"]').setValue('5'); await w.get('[data-test="d-amount"]').trigger('focusout')
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('the legacy run block draws a field given with `duration` as the boxes and sends seconds text', async () => {
    const w = mount(InspectorRunSettings, { props: { fields: { time_limit_sec: { value: '14400', options: [], duration: { min: 1800, max: 14400 } } }, advancedOpen: true }, global: { plugins: [i18n] } })
    expect((w.get('[data-test="inspector-run-settings-time_limit_sec-amount"]').element as HTMLInputElement).value).toBe('4')
    await w.get('[data-test="inspector-run-settings-time_limit_sec-amount"]').setValue('3')
    await w.get('[data-test="inspector-run-settings-time_limit_sec-amount"]').trigger('focusout')
    expect(w.emitted('update:field')).toEqual([['time_limit_sec', '10800']])
  })
})

describe('strings', () => {
  it.each([['ko', ko], ['en', en], ['ja', ja]] as const)('%s has the time setting names and the [number][unit] words', (locale, app) => {
    const run = (app as any).designer.inspector.run_settings
    for (const key of ['time_limit_sec', 'verify_run_sec', 'auto_case_timeout_sec', 'ai_judge_mode', 'ai_judge_run_sec', 'ai_judge_runs']) expect(run[key], key).toBeTruthy()
    expect(run.time_limit).toBeUndefined(); expect(run.verify_run_minutes).toBeUndefined()
    expect(Object.keys(run.duration).sort()).toEqual(['amount', 'clamped_max', 'clamped_min', 'hour', 'minute', 'part_hour', 'part_minute', 'part_second', 'second', 'unit'])
    const m = messages[locale] as Record<string, any>
    expect(Object.keys(m.ai_judge_mode)).toEqual(['case_count', 'fixed'])
    expect(m.test_zone.time_title).toBeTruthy()
    expect(Object.keys(m.test_time).sort()).toEqual(['auto_case_hint', 'mode_case_count_hint', 'mode_fixed_hint', 'run_hint', 'runs_hint'])
  })
})
