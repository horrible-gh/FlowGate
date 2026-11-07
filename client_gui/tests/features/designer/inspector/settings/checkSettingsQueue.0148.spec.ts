/**
 * flowgate.v02.0148 T#2 (0005-NR §5.5 checks (i)-(vii) and §5.4): the check card's settings client.
 *   (a) a PATCH answer marked `merge_targets_kept` takes the earlier merge target views over - or is discarded and read in full;
 *   (b) push / target show the requested value at once;
 *   (c) per-field state, one serial queue, a read that does not lock, a narrower `off`;
 *   (d) the opt-in query is sent only when the earlier views could be carried over.
 * The server below honours `keep_merge_targets=true` the way T#1's server does (T#1: `merge_targets_kept: true`).
 */
import { defineComponent, h } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(),
  getRequest: (...a: unknown[]) => http.get(...a), patchRequest: (...a: unknown[]) => http.patch(...a), postRequest: (...a: unknown[]) => http.post(...a) }))
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import { FIELD_SAVED_MS, MERGE_TARGET_CACHE_MS, useGateContext } from '@/features/designer/inspector/features/gateCard/useGateContext'
import { settingsNodeWrite } from '@/features/document'
import { SELECTIONS, prepareInspectorI18n, targetOf } from '../support/inspectorSupport'

const i18n = prepareInspectorI18n()
const wrappers: VueWrapper[] = []
const LIST = ['feature/v0.2.0', 'main', 'release/local']
const field = (value: unknown, stored = false) => ({ value, stored, default: null, choices: null })
const entry = (branch: string | null) => ({ branch, checked: true, error: null, unmerge_supported: false, warnings: [] })
const refusal = (code: string, reason?: string) => ({ isAxiosError: true, response: { status: 409, data: { error: { code, params: { reason } } } } })
let raw: any
let current: any
let target: ReturnType<typeof targetOf>
let context: ReturnType<typeof useGateContext>
let id = 0
/** Does the server know the opt-in (T#1)? false = an older server: the query is ignored and the answer is full. */
let honours = true
let slowViews: Promise<void> | null = null

function views(selected: string | null) {
  raw.merge_target_state = { status: raw.merge_target_options.length ? 'ok' : 'empty', code: null, reason: null,
    current: entry(selected ?? raw.default_target_branch), default: entry(raw.default_target_branch) }
}
async function serverPatch(url: string, body: any) {
  expect(body.expected_settings_version).toBe(raw.settings_version)
  if (body.field === 'push' && body.value === 'refuse') throw refusal('gate_settings_invalid', 'origin_not_configured')
  raw.fields[body.field] = field(body.value, body.value !== null)
  raw.settings_version++
  const out = structuredClone(raw)
  if (honours && url.includes('keep_merge_targets=true') && body.field !== 'merge_target_branch') {
    delete out.merge_target_options; delete out.default_target_branch; delete out.merge_target_state
    out.merge_targets_kept = true
  } else { views(raw.fields.merge_target_branch.value); Object.assign(out, structuredClone({ merge_target_options: raw.merge_target_options, default_target_branch: raw.default_target_branch, merge_target_state: raw.merge_target_state })) }
  return { data: out }
}
/** The next PATCH waits until `release()`; the server applies it at that moment. */
function holdPatch() {
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  http.patch.mockImplementationOnce(async (url: string, body: any) => { await gate; return serverPatch(url, body) })
  return release
}
async function show() {
  const w = mount(defineComponent({ setup() { context = useGateContext(() => target); return () => h(CheckSettings, { target }) } }), { global: { plugins: [i18n] } })
  wrappers.push(w); await flushPromises(); return w
}
const patchUrls = () => http.patch.mock.calls.map(([url]) => url as string)
const patchBodies = () => http.patch.mock.calls.map(([, body]) => body as { field: string; value: unknown; expected_settings_version: number })
const optionNames = (w: VueWrapper) => w.findAll('[data-test="check-settings-target"] option').map(o => o.text())
const toggle = (w: VueWrapper, name: string) => w.get(`[data-test="check-settings-${name}"]`)
const on = (w: VueWrapper, name: string) => toggle(w, name).attributes('aria-checked') === 'true'
const view = () => {
  const s = context.value!.settings.value!
  return { options: s.mergeTargetOptions, base: s.defaultTargetBranch, state: s.mergeTargetState, cached: context.value!.cachedTargets() }
}

beforeEach(() => {
  Object.values(http).forEach(m => m.mockReset()); i18n.global.locale.value = 'ko'; honours = true; slowViews = null
  target = { ...targetOf(SELECTIONS.graphGate), nodeId: `check0148_${++id}`, readOnly: null }
  raw = { card_kind: 'check', settings_version: 6, locked: false, context: { run_id: 'run0148', graph_revision_no: 3, draft_version: 9 },
    fields: { archive: field(false), merge_mode: field('merge'), merge_target: field(null), merge_target_branch: field(null), push: field(false), keep_branch: field(false) },
    merge_target_options: [...LIST], default_target_branch: 'feature/v0.2.0', push_remote_check_required: false }
  views(null)
  current = { run_id: 'run0148', status: 'idle', node_runs: [{ node_key: target.nodeId, status: 'open', attempt_id: 'attempt0148', gate: {
    can_decide: true, settings_locked: false, decision_in_progress: false, decision: null, job: null, recovery: null } }] }
  http.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/settings')) { if (slowViews) await slowViews; return { data: structuredClone(raw) } }
    if (url.endsWith('/runs/current')) return { data: { run: structuredClone(current) } }
    return { data: {} }
  })
  http.patch.mockImplementation(serverPatch)
})
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers(); vi.restoreAllMocks() })

describe('(a)+(d) the merge contract and the opt-in', () => {
  it('(i) an unrelated PATCH opts in, and options, inherited target, lookup status and options source stay as before', async () => {
    const w = await show()
    const before = view()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(patchUrls()[0]).toContain('?keep_merge_targets=true')
    expect(context.value!.settings.value?.fields.archive.value).toBe(true)
    expect(context.value!.settings.value?.mergeTargetsKept).toBeUndefined()
    expect(view()).toEqual(before)
    expect(optionNames(w)).toEqual(LIST)
    expect((w.get('[data-test="check-settings-target"]').element as HTMLSelectElement).value).toBe('feature/v0.2.0')
    expect(w.get('[data-test="check-settings-target"]').attributes('disabled')).toBeUndefined()
    // push, keep branch and Git scope take the same road
    await toggle(w, 'push').trigger('click'); await flushPromises()
    await toggle(w, 'keep-branch').trigger('click'); await flushPromises()
    await w.findAll('[data-test="check-settings-scope"] button')[1].trigger('click'); await flushPromises()
    expect(patchBodies().map(b => b.field)).toEqual(['archive', 'push', 'keep_branch', 'merge_mode'])
    expect(patchUrls().every(url => url.includes('keep_merge_targets=true'))).toBe(true)
    expect(view().options).toEqual(LIST)
    expect(http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length).toBe(1)
  })

  it('(ii) in timeout / failed the kept list keeps being used after an unrelated PATCH', async () => {
    const w = await show()
    raw.merge_target_options = []
    raw.merge_target_state = { status: 'timeout', code: 'merge_target_options_timeout', reason: null, current: { ...entry('feature/v0.2.0'), checked: false }, default: { ...entry('feature/v0.2.0'), checked: false } }
    await context.value!.reload(); await flushPromises()
    expect(optionNames(w)).toEqual(LIST)               // the 0119 kept list stands in
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(context.value!.settings.value?.mergeTargetState?.status).toBe('timeout')
    expect(context.value!.cachedTargets()).toEqual(LIST)
    expect(optionNames(w)).toEqual(LIST)
    expect(w.get('[data-test="check-settings-target-state"]').text()).toBe('대상 조회 시간 초과')
    expect(w.get('[data-test="check-settings-target"]').attributes('disabled')).toBeUndefined()
  })

  it('(iii) an answer without the marker (an older server) is used as it is: no merge, no extra read', async () => {
    honours = false
    const w = await show()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(LIST)
    expect(context.value!.settings.value?.mergeTargetsKept).toBeUndefined()
    expect(http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length).toBe(1)
    expect(optionNames(w)).toEqual(LIST)
  })

  it('(iii) a marked answer is never taken as a full one: the views come from the earlier read only through the contract', async () => {
    await show()
    const prev = context.value!.settings.value!
    const marked = await settingsNodeWrite(target.flowId, target.nodeId, 'archive', true, prev.settingsVersion, undefined, { keepMergeTargets: true })
    expect(marked.mergeTargetsKept).toBe(true)
    expect(marked.mergeTargetOptions).toBeUndefined()
    expect(marked.mergeTargetState).toBeUndefined()
    expect(marked.defaultTargetBranch).toBeUndefined()
    expect(http.patch.mock.calls.at(-1)![0]).toContain('?keep_merge_targets=true')
    // without the option the call is the plain URL of before
    raw.settings_version = prev.settingsVersion!
    await settingsNodeWrite(target.flowId, target.nodeId, 'archive', false, prev.settingsVersion)
    expect(http.patch.mock.calls.at(-1)![0]).not.toContain('keep_merge_targets')
  })

  it('(iv) an answer of another context is not merged: the full view is read', async () => {
    const w = await show()
    const release = holdPatch()
    const saving = context.value!.change('archive', true)
    await flushPromises()
    raw.context = { run_id: 'run0148', graph_revision_no: 3, draft_version: 10 }      // a run / Time Machine moved the card meanwhile
    raw.merge_target_options = ['feature/v0.2.0', 'main']
    release(); expect(await saving).toBe(true); await flushPromises()
    expect(context.value!.settings.value?.context?.draft_version).toBe(10)
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['feature/v0.2.0', 'main'])
    expect(optionNames(w)).toEqual(['feature/v0.2.0', 'main'])
    expect(http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length).toBe(2)
  })

  it('(v) a PATCH answered after the Inspector left the card is not merged and keeps no candidates', async () => {
    await show()
    expect(context.value!.targetCache.value?.options).toEqual(LIST)
    const release = holdPatch()
    expect(patchUrls()).toEqual([])
    const saving = context.value!.change('archive', true)
    await flushPromises()
    expect(patchUrls()[0]).toContain('keep_merge_targets=true')     // sent while the card was shown
    context.value!.leave()
    expect(context.value!.targetCache.value).toBeNull()
    release(); expect(await saving).toBe(true); await flushPromises()
    expect(context.value!.settings.value?.fields.archive.value).toBe(true)
    expect(context.value!.targetCache.value).toBeNull()
    // the candidates were read in full instead of being left empty, and that read keeps nothing either
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(LIST)
    expect(context.value!.cachedTargets()).toBeNull()
  })

  it('(vi) views older than 5 minutes are not carried over: the PATCH does not opt in and the answer is full', async () => {
    const now = vi.spyOn(Date, 'now')
    now.mockReturnValue(5_000_000)
    const w = await show()
    now.mockReturnValue(5_000_000 + MERGE_TARGET_CACHE_MS - 1)
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(patchUrls()[0]).toContain('keep_merge_targets=true')
    now.mockReturnValue(5_000_000 + MERGE_TARGET_CACHE_MS + 1_000)
    await toggle(w, 'keep-branch').trigger('click'); await flushPromises()
    expect(patchUrls()[1]).not.toContain('keep_merge_targets')
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(LIST)
    expect(optionNames(w)).toEqual(LIST)
  })

  it('(vi) views that expire while the opt-in PATCH is on its way are read in full afterwards', async () => {
    const now = vi.spyOn(Date, 'now')
    now.mockReturnValue(7_000_000)
    await show()
    const release = holdPatch()
    const saving = context.value!.change('archive', true)
    await flushPromises()
    now.mockReturnValue(7_000_000 + MERGE_TARGET_CACHE_MS)
    release(); await saving; await flushPromises()
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(LIST)
    expect(http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length).toBe(2)
  })

  it('(vii) a merge_target_branch PATCH never opts in and replaces the views by its answer', async () => {
    const w = await show()
    raw.merge_target_options = ['main', 'release/local']
    await w.get('[data-test="check-settings-target"]').setValue('release/local'); await flushPromises()
    expect(patchUrls()[0]).not.toContain('keep_merge_targets')
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['main', 'release/local'])
    expect(optionNames(w)).toEqual(['main', 'release/local'])
    expect((w.get('[data-test="check-settings-target"]').element as HTMLSelectElement).value).toBe('release/local')
  })
})

describe('(b) push and the target show the request at once', () => {
  it('push ON and the chosen branch are shown while the PATCH is unanswered, as saving, never as saved', async () => {
    const w = await show()
    const release = holdPatch()
    await toggle(w, 'push').trigger('click'); await flushPromises()
    expect(on(w, 'push')).toBe(true)
    expect(w.get('[data-field-state="saving"][data-mark-field="push"]').text()).toBe('저장 중…')
    expect(w.find('[data-field-state="saved"]').exists()).toBe(false)
    release(); await flushPromises()
    expect(on(w, 'push')).toBe(true)
    expect(w.get('[data-field-state="saved"][data-mark-field="push"]').text()).toBe('저장됨')
    const hold2 = holdPatch()
    await w.get('[data-test="check-settings-target"]').setValue('release/local'); await flushPromises()
    expect((w.get('[data-test="check-settings-target"]').element as HTMLSelectElement).value).toBe('release/local')
    expect(w.find('[data-test="check-settings-target-display"]').exists()).toBe(false)
    hold2(); await flushPromises()
    expect((w.get('[data-test="check-settings-target"]').element as HTMLSelectElement).value).toBe('release/local')
  })

  it('saved is shown only for the field whose answer carried the requested value, and goes away by itself', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const w = await show()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(context.value!.fieldStates.value).toEqual({ archive: 'saved' })
    vi.advanceTimersByTime(FIELD_SAVED_MS)
    await flushPromises()
    expect(context.value!.fieldStates.value).toEqual({})
    expect(w.find('[data-field-state]').exists()).toBe(false)
  })

  it('a refusal brings the confirmed push value back, marks the field failed and offers a retry', async () => {
    const w = await show()
    http.patch.mockRejectedValueOnce(refusal('gate_settings_invalid', 'origin_not_configured'))
    await toggle(w, 'push').trigger('click'); await flushPromises()
    expect(on(w, 'push')).toBe(false)
    expect(context.value!.fieldStates.value.push).toBe('failed')
    expect(w.get('[data-field="push"]').attributes('title')).toContain('원격 반영 사용 불가')
    await w.get('[data-test="check-settings-retry"][data-mark-field="push"]').trigger('click'); await flushPromises()
    expect(patchBodies().map(b => [b.field, b.value])).toEqual([['push', true], ['push', true]])
    expect(on(w, 'push')).toBe(true)
    expect(context.value!.fieldStates.value.push).toBe('saved')
    expect(w.find('[data-test="check-settings-retry"]').exists()).toBe(false)
    expect(w.find('[data-field="push"]').exists()).toBe(false)
  })
})

describe('(c) per-field state, one serial queue, no lock by saving or reading', () => {
  it('quick successive changes of different fields are all sent, in order, each with the version of the answer before', async () => {
    const w = await show()
    const release = holdPatch()
    await toggle(w, 'archive-value').trigger('click')
    await toggle(w, 'push').trigger('click')
    await toggle(w, 'keep-branch').trigger('click')
    await flushPromises()
    expect(patchBodies().map(b => b.field)).toEqual(['archive'])              // one in flight; the others wait their turn
    expect(on(w, 'archive-value')).toBe(true); expect(on(w, 'push')).toBe(true); expect(on(w, 'keep-branch')).toBe(true)
    expect(context.value!.fieldStates.value).toEqual({ archive: 'saving', push: 'saving', keep_branch: 'saving' })
    release(); await flushPromises()
    expect(patchBodies().map(b => [b.field, b.value, b.expected_settings_version])).toEqual([['archive', true, 6], ['push', true, 7], ['keep_branch', true, 8]])
    expect(raw.settings_version).toBe(9)
    expect(context.value!.settings.value?.settingsVersion).toBe(9)
    expect(Object.keys(context.value!.pending.value)).toEqual([])
    expect(on(w, 'archive-value') && on(w, 'push') && on(w, 'keep-branch')).toBe(true)
    expect(optionNames(w)).toEqual(LIST)
  })

  it('a value typed again for a field still waiting replaces the waiting one (latest wins); both inputs are answered by that save', async () => {
    await show()
    const release = holdPatch()
    const first = context.value!.change('archive', true)
    const second = context.value!.change('push', true)
    const third = context.value!.change('push', false)
    await flushPromises()
    expect(context.value!.pending.value).toEqual({ archive: true, push: false })
    release()
    expect(await Promise.all([first, second, third])).toEqual([true, true, true])
    expect(patchBodies().map(b => [b.field, b.value])).toEqual([['archive', true], ['push', false]])
    expect(raw.fields.push.value).toBe(false)
  })

  it('a value typed while the same field is in flight is sent after it, with the next version', async () => {
    await show()
    const release = holdPatch()
    const a = context.value!.change('archive', true)
    await flushPromises()
    const b = context.value!.change('archive', false)
    release(); expect(await Promise.all([a, b])).toEqual([true, true]); await flushPromises()
    expect(patchBodies().map(x => [x.field, x.value, x.expected_settings_version])).toEqual([['archive', true, 6], ['archive', false, 7]])
    expect(raw.fields.archive.value).toBe(false)
    expect(context.value!.settings.value?.fields.archive.value).toBe(false)
  })

  it('a server refusal of one field leaves the others saved and the confirmed value of the refused one', async () => {
    const w = await show()
    http.patch.mockImplementationOnce(async () => { throw refusal('gate_settings_invalid', 'push_requires_origin') })
    const refused = context.value!.change('push', true)
    const kept = context.value!.change('keep_branch', true)
    expect(await Promise.all([refused, kept])).toEqual([false, true]); await flushPromises()
    expect(context.value!.fieldStates.value.push).toBe('failed')
    expect(context.value!.fieldStates.value.keep_branch).toBe('saved')
    expect(raw.fields.keep_branch.value).toBe(true)
    expect(on(w, 'push')).toBe(false)
    expect(on(w, 'keep-branch')).toBe(true)
    // the refused one was read again before the next write, so the version was current
    expect(patchBodies().map(b => b.expected_settings_version)).toEqual([6, 6])
  })

  it('failures are kept per field: two refused fields both show their own icon', async () => {
    const w = await show()
    http.patch.mockRejectedValueOnce(refusal('node_settings_locked'))
    http.patch.mockRejectedValueOnce(refusal('gate_settings_invalid', 'origin_not_configured'))
    await Promise.all([context.value!.change('archive', true), context.value!.change('push', true)]); await flushPromises()
    expect(w.find('[data-field="archive"]').exists()).toBe(true)
    expect(w.find('[data-field="push"]').exists()).toBe(true)
    expect(w.findAll('[data-test="check-settings-retry"]').length).toBe(2)
    await context.value!.change('archive', true); await flushPromises()
    expect(w.find('[data-field="archive"]').exists()).toBe(false)
    expect(w.find('[data-field="push"]').exists()).toBe(true)
  })

  it('a background read does not lock the controls, and a toggle goes out at once while the slow Git read is unanswered', async () => {
    const w = await show()
    let finish!: () => void
    slowViews = new Promise<void>(resolve => { finish = resolve })
    const reading = context.value!.reload()
    await flushPromises()
    expect(context.value!.status.value).toBe('ready')                // stale-while-revalidate: never 'loading' again
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeUndefined()
    expect(w.get('[data-test="check-settings-target"]').attributes('disabled')).toBeUndefined()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(patchBodies().map(b => b.field)).toEqual(['archive'])     // sent while the read is unanswered
    expect(on(w, 'archive-value')).toBe(true)
    finish(); await reading; await flushPromises()
    expect(on(w, 'archive-value')).toBe(true)                        // the late read did not bring the old value back
    expect(raw.fields.archive.value).toBe(true)
  })

  it('rej_01M6V2RSPP4ZK7NB: a read that lands only after the queue ended is read again (the candidates and values of the server arrive)', async () => {
    const w = await show()
    let finish!: () => void
    slowViews = new Promise<void>(resolve => { finish = resolve })
    const reading = context.value!.reload()
    await flushPromises()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(patchBodies().map(b => b.field)).toEqual(['archive'])          // the queue is done, the read is still unanswered
    const settingsGets = () => http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length
    expect(settingsGets()).toBe(2)
    raw.merge_target_options = ['main', 'release/local']
    finish(); await reading; await flushPromises()
    expect(settingsGets()).toBe(3)                                         // the dropped read was recovered by one more read
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['main', 'release/local'])
    expect(optionNames(w)).toEqual(['main', 'release/local'])
    expect(on(w, 'archive-value')).toBe(true)
  })

  it('rej_01M6V35VT858JFZ1: a second field changed while the slow read is still unanswered is written at once; the recovery read comes after and shows the latest state', async () => {
    const w = await show()
    let finish!: () => void
    slowViews = new Promise<void>(resolve => { finish = resolve })
    const reading = context.value!.reload()
    await flushPromises()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(patchBodies().map(b => b.field)).toEqual(['archive'])          // first PATCH answered, the GET is still on its way
    const settingsGets = () => http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length
    expect(settingsGets()).toBe(2)
    const second = context.value!.change('push', true)
    await flushPromises()
    expect(patchBodies().map(b => b.field)).toEqual(['archive', 'push'])  // not held back by the unanswered GET
    expect(patchBodies()[1].expected_settings_version).toBe(7)
    expect(await second).toBe(true)
    expect(settingsGets()).toBe(2)                                         // the recovery waits for the slow read
    raw.merge_target_options = ['main', 'release/local']
    finish(); await reading; await flushPromises()
    expect(settingsGets()).toBe(3)                                         // one recovery read, after both writes
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['main', 'release/local'])
    expect(context.value!.settings.value?.settingsVersion).toBe(raw.settings_version)
    expect(on(w, 'archive-value')).toBe(true)
    expect(raw.fields.push.value).toBe(true)
    expect(context.value!.settings.value?.fields.push?.value).toBe(true)
  })

  it('rej_01M6V3JZMY9N63XR: after an ordinary save has finished, a read dropped as stale is still recovered (recovering is never left on a finished promise)', async () => {
    const w = await show()
    const settingsGets = () => http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length
    await toggle(w, 'keep-branch').trigger('click'); await flushPromises()   // an ordinary save first: its recover() ends at once
    expect(patchBodies().map(b => b.field)).toEqual(['keep_branch'])
    expect(settingsGets()).toBe(1)
    let finish!: () => void
    slowViews = new Promise<void>(resolve => { finish = resolve })
    const reading = context.value!.reload()
    await flushPromises()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(patchBodies().map(b => b.field)).toEqual(['keep_branch', 'archive'])
    expect(settingsGets()).toBe(2)
    raw.merge_target_options = ['main', 'release/local']
    finish(); await reading; await flushPromises()
    expect(settingsGets()).toBe(3)                                         // the follow-up read is not lost
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['main', 'release/local'])
    expect(optionNames(w)).toEqual(['main', 'release/local'])
    expect(on(w, 'archive-value')).toBe(true)
  })

  it('rej_01M6V3JZMY9N63XR: after an ordinary save has finished, a refused version still brings the follow-up read of the server version', async () => {
    const w = await show()
    const settingsGets = () => http.get.mock.calls.filter(([url]) => String(url).endsWith('/settings')).length
    await toggle(w, 'keep-branch').trigger('click'); await flushPromises()
    expect(context.value!.settings.value?.settingsVersion).toBe(7)
    expect(settingsGets()).toBe(1)
    raw.settings_version = 20                                              // another client wrote meanwhile
    raw.merge_target_options = ['main', 'release/local']
    http.patch.mockImplementationOnce(async () => { throw refusal('gate_context_changed') })
    expect(await context.value!.change('archive', true)).toBe(false)
    await flushPromises()
    expect(settingsGets()).toBe(2)                                         // the recovery read was sent
    expect(context.value!.settings.value?.settingsVersion).toBe(20)
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['main', 'release/local'])
    // and the next change goes out with the server version
    expect(await context.value!.change('push', true)).toBe(true)
    expect(patchBodies()[patchBodies().length - 1].expected_settings_version).toBe(20)
  })

  it('rej_01M6V2RSPP4ZK7NB: views read before the card was left cannot back an opt-in after the return, not even for a PATCH sent at once', async () => {
    const w = await show()
    context.value!.leave()                                                 // the Inspector went away; the candidates change meanwhile
    raw.merge_target_options = ['main', 'release/local']
    let finish!: () => void
    slowViews = new Promise<void>(resolve => { finish = resolve })
    const reading = context.value!.reload()                                // back on the card: the full read is slow
    await flushPromises()
    await toggle(w, 'archive-value').trigger('click'); await flushPromises()
    expect(patchUrls()[0]).not.toContain('keep_merge_targets')             // the old views are not a ground to keep
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['main', 'release/local'])
    finish(); await reading; await flushPromises()
    // a valid full answer is in: the opt-in is back
    await toggle(w, 'keep-branch').trigger('click'); await flushPromises()
    expect(patchUrls()[1]).toContain('keep_merge_targets=true')
    expect(context.value!.settings.value?.mergeTargetOptions).toEqual(['main', 'release/local'])
  })

  it('the first read still shows the card as not read, and the refresh button alone waits for a candidate re-read', async () => {
    let finish!: () => void
    slowViews = new Promise<void>(resolve => { finish = resolve })
    const w = await show()
    expect(context.value!.status.value).toBe('loading')
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeDefined()
    finish(); await flushPromises()
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeUndefined()
    raw.merge_target_options = []
    raw.merge_target_state = { status: 'failed', code: 'merge_target_options_unavailable', reason: 'probe_failed', current: { ...entry('feature/v0.2.0'), checked: false }, default: { ...entry('feature/v0.2.0'), checked: false } }
    await context.value!.reload(); await flushPromises()
    slowViews = new Promise<void>(resolve => { finish = resolve })
    await w.get('[data-test="check-settings-refresh"]').trigger('click'); await flushPromises()
    expect(w.get('[data-test="check-settings-refresh"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeUndefined()   // C5: never a lock
    expect(w.get('[data-test="check-settings-push"]').attributes('disabled')).toBeUndefined()
    finish(); await flushPromises()
  })

  it('a server or safety lock still switches every control off', async () => {
    raw.locked = true
    const w = await show()
    for (const name of ['archive-value', 'push', 'keep-branch']) expect(toggle(w, name).attributes('disabled')).toBeDefined()
    expect(await context.value!.change('archive', true)).toBe(false)
    expect(http.patch).not.toHaveBeenCalled()
  })

  it('F5 / leaving and coming back: the last confirmed values come from the server, whatever was queued or refused', async () => {
    const w = await show()
    http.patch.mockRejectedValueOnce(refusal('gate_settings_invalid', 'origin_not_configured'))
    await Promise.all([context.value!.change('push', true), context.value!.change('archive', true), context.value!.change('keep_branch', true)]); await flushPromises()
    context.value!.leave(); context.value!.dropTargets()
    await context.value!.reload(); await flushPromises()
    expect([raw.fields.push.value, raw.fields.archive.value, raw.fields.keep_branch.value]).toEqual([false, true, true])
    expect(on(w, 'push')).toBe(false); expect(on(w, 'archive-value')).toBe(true); expect(on(w, 'keep-branch')).toBe(true)
  })

  it('the public context API T#3 uses is unchanged: decide / reload / acquire / release / leave / change keep their signatures', async () => {
    await show()
    const c = context.value!
    for (const name of ['decide', 'reload', 'acquire', 'release', 'leave', 'change', 'replay', 'hasRequest', 'cachedTargets', 'dropTargets'] as const) expect(typeof c[name]).toBe('function')
    expect(c.reload.length).toBe(0)         // (refreshMergeTargets = false, keeps = rights())
    expect(c.decide.length).toBe(2)         // (decision, comment?)
    expect(await c.reload()).toBe(true)
    expect(await c.change('not_a_setting', 1)).toBe(false)
  })
})
