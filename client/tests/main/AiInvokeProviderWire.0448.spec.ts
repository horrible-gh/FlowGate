// 0448 T0005 §5 / §7-5, §7-6, §7-8 — the request contract, end to end inside the client.
//
// NR0003 §7-3 named the hole this file closes: the wire key `continuation_provider_overrides`
// appears exactly once in production (AiInvokeDialog.vue) and NO client test asserted it, while
// ContinuousWorkDialog.spec.ts checked the confirm payload and AiInvokeDialog.spec.ts checked
// the pin field. A renamed prop between those two halves would drop every per-step provider
// with both suites still green. So each case here starts at the dialog that produces the value
// and ends at the body POSTed to /ai-invoke/start.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest, patchRequest, putRequest, deleteRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
  putRequest: vi.fn(),
  deleteRequest: vi.fn(),
  showToast: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  patchRequest: (...a: unknown[]) => patchRequest(...a),
  putRequest: (...a: unknown[]) => putRequest(...a),
  deleteRequest: (...a: unknown[]) => deleteRequest(...a),
  extractApiErrorMessage: (_e: any, fallback: string) => fallback,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import ContinuousWarningDialog from '@main/components/ContinuousWarningDialog.vue'
import MainPanel from '@main/components/MainPanel.vue'
import { useAiProviderStore } from '@main/stores/aiProvider'

const PROJECT = 'flowgate'
const ROOT = 'flowgate.default.0448.0001-B'
const WP_DOC = 'flowgate.default.0448.0004-WP'
const PROVIDERS = [
  { id: 'aip_default', name: 'Default Provider', exec_type: 'cli', kind: 'claude' },
  { id: 'aip_stored', name: 'Stored Provider', exec_type: 'cli', kind: 'claude' },
  { id: 'aip_plan', name: 'Plan Provider', exec_type: 'cli', kind: 'claude' },
]

/** Two runnable rows: seq 1 stores a provider, seq 2 stores none. */
function planRows() {
  return [
    { id: 1, item_seq: 1, type: 'D', label: 'Design', status: 'pending',
      provider_id: 'aip_stored', provider_display_name: 'Stored Provider', provider_registered: true,
      note: 'stored sentence', source_doc_id: WP_DOC, source_revision_no: 8 },
    { id: 2, item_seq: 2, type: 'P', label: 'Plan', status: 'pending',
      provider_id: null, provider_display_name: null, provider_registered: null,
      note: '', source_doc_id: WP_DOC, source_revision_no: 8 },
  ]
}

function sequenceResponse() {
  const rows = planRows()
  return { data: { doc_id: ROOT, doc_class: 'B', decided: true, items: rows, head: rows[0] } }
}

function providersResponse() {
  return { data: { ok: true, project: PROJECT, providers: PROVIDERS, default_provider_id: 'aip_default' } }
}

/** The work-plan apply preview the dialog reads. `providers` is item_seq -> provider_id. */
function planFill(providers: Record<number, string>) {
  return { data: { wp_doc_id: WP_DOC, wp_revision_no: 8,
    fill_preview: { note_overrides: {}, provider_overrides: providers } } }
}

function startBody() {
  const call = postRequest.mock.calls.find(c => c[0] === '/api/v1/ai-invoke/start')
  expect(call, 'no /ai-invoke/start request was made').toBeTruthy()
  return call![1] as Record<string, unknown>
}

async function loadedStore() {
  const store = useAiProviderStore()
  await store.loadForProject(PROJECT)
  return store
}

function mountAutoStart(props: Record<string, unknown> = {}) {
  return mount(AiInvokeDialog, {
    props: {
      visible: true, project: PROJECT, module: 'default', group: '0448',
      docRef: ROOT, sequenceDocRef: ROOT, actionScope: 'new',
      continuationInstructionMode: 'auto_approved',
      initialMode: 'continuous', initialTargetSeq: 2, autoStart: true,
      ...props,
    },
    global: { plugins: [i18n] },
  })
}

/** Mount the continuous dialog against a plan whose values are `fill`, then press [next] and
 *  return the confirm payload the screen actually produced. */
async function confirmPayloadForPlan(fill: Record<number, string>) {
  postRequest.mockImplementation((url: string) =>
    url.includes('/work-plan/apply/preview')
      ? Promise.resolve(planFill(fill))
      : Promise.resolve({ data: { run_id: 'aiv_1', status: 'running' } }),
  )
  const wrapper = mount(ContinuousWorkDialog, {
    props: {
      visible: true, docRef: ROOT,
      providers: PROVIDERS.map(p => ({ id: p.id, name: p.name })),
      selectedProvider: 'aip_default', providerPinned: false,
    },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  ;([...document.querySelectorAll('.modal-ft .btn-primary')][0] as HTMLButtonElement).click()
  await flushPromises()
  const payload = wrapper.emitted('confirm')![0][0] as any
  wrapper.unmount()
  document.body.innerHTML = ''
  return payload
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  localStorage.clear()
  delete (window as any).__accessToken__
  for (const fn of [getRequest, postRequest, patchRequest, putRequest, deleteRequest, showToast]) fn.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url === '/api/v1/ai-invoke/providers') return Promise.resolve(providersResponse())
    if (url === '/api/v1/workflow/sequence') return Promise.resolve(sequenceResponse())
    return Promise.resolve({ data: {} })
  })
  postRequest.mockResolvedValue({ data: { run_id: 'aiv_1', status: 'running' } })
  patchRequest.mockResolvedValue({ data: {} })
})

afterEach(() => { document.body.innerHTML = '' })

describe('/ai-invoke/start provider request states (0448 T0005 §5-1)', () => {
  it('sends an ordinary selection as provider_id alone, with no provider_pinned', async () => {
    const store = await loadedStore()
    store.selectProvider('aip_plan')
    postRequest.mockClear()

    const wrapper = mountAutoStart()
    await flushPromises()

    const body = startBody()
    expect(body.provider_id).toBe('aip_plan')
    expect(body).not.toHaveProperty('provider_pinned')
    wrapper.unmount()
  })

  it('sends provider_pinned only for a run that went through the explicit force-all API', async () => {
    const store = await loadedStore()
    store.forceProviderForAllSteps('aip_plan')
    postRequest.mockClear()

    const wrapper = mountAutoStart()
    await flushPromises()

    // Positive control for the previous test's absence assertion: the field CAN be sent, so
    // "no provider_pinned" is a real difference, not a key this dialog never writes.
    expect(startBody()).toMatchObject({ provider_id: 'aip_plan', provider_pinned: true })
    wrapper.unmount()
  })

  it('drops provider_pinned again once the force-all is released', async () => {
    const store = await loadedStore()
    store.forceProviderForAllSteps('aip_plan')
    store.clearPin()
    postRequest.mockClear()

    const wrapper = mountAutoStart()
    await flushPromises()

    expect(startBody().provider_id).toBe('aip_plan')
    expect(startBody()).not.toHaveProperty('provider_pinned')
    wrapper.unmount()
  })
})

describe('continuation_provider_overrides wire key (0448 T0005 §5-2 / §7-8)', () => {
  it('carries the map under the exact wire key, alongside an unpinned provider_id', async () => {
    const store = await loadedStore()
    store.selectProvider('aip_default')
    postRequest.mockClear()

    const wrapper = mountAutoStart({ providerOverrides: { 1: 'aip_plan' } })
    await flushPromises()

    const body = startBody()
    // The three request states are independent: a per-step override does not imply a pin.
    expect(body.continuation_provider_overrides).toEqual({ 1: 'aip_plan' })
    expect(body.provider_id).toBe('aip_default')
    expect(body).not.toHaveProperty('provider_pinned')
    wrapper.unmount()
  })

  it('omits the key entirely for an empty map — one fixed rule, never {}', async () => {
    await loadedStore()
    postRequest.mockClear()

    const wrapper = mountAutoStart({ providerOverrides: {} })
    await flushPromises()

    expect(startBody()).not.toHaveProperty('continuation_provider_overrides')
    wrapper.unmount()
  })

  it('never sends the key on a single run', async () => {
    await loadedStore()
    postRequest.mockClear()

    const wrapper = mountAutoStart({ initialMode: 'single', providerOverrides: { 1: 'aip_plan' } })
    await flushPromises()

    expect(startBody()).not.toHaveProperty('continuation_provider_overrides')
    wrapper.unmount()
  })
})

describe('plan value vs sequence value, screen to request (0448 T0005 §7-5 / §7-6)', () => {
  it('§7-5 plan == sequence: no override is produced, so the request carries no map at all', async () => {
    // The plan says exactly what the row already stores, so ContinuousWorkDialog.applyPlanFill
    // creates nothing — and the run must still be the STORED provider, which the server
    // resolves from the sequence row (test_ai_invoke_provider_selection_0448.py §7-5).
    const payload = await confirmPayloadForPlan({ 1: 'aip_stored' })
    expect(payload.providerOverrides).toEqual({})

    await loadedStore()
    postRequest.mockClear()
    const wrapper = mountAutoStart({ providerOverrides: payload.providerOverrides })
    await flushPromises()

    const body = startBody()
    expect(body).not.toHaveProperty('continuation_provider_overrides')
    expect(body).not.toHaveProperty('provider_pinned')
    expect(body.provider_id).toBe('aip_default')
    wrapper.unmount()
  })

  it('§7-6 plan != sequence: the item_seq override reaches the request verbatim', async () => {
    const payload = await confirmPayloadForPlan({ 1: 'aip_plan' })
    expect(payload.providerOverrides).toEqual({ 1: 'aip_plan' })

    await loadedStore()
    postRequest.mockClear()
    const wrapper = mountAutoStart({ providerOverrides: payload.providerOverrides })
    await flushPromises()

    expect(startBody().continuation_provider_overrides).toEqual({ 1: 'aip_plan' })
    wrapper.unmount()
  })
})

describe('MainPanel is the hop between the two dialogs (0448 T0005 §7-8)', () => {
  it('hands the confirm payload map to AiInvokeDialog under the providerOverrides prop', async () => {
    // The typo NR0003 §7-3 warned about lives exactly here: ContinuousWorkDialog emits
    // `providerOverrides`, MainPanel stores it and re-exposes it as `:provider-overrides`.
    // Neither existing suite crossed this boundary.
    await loadedStore()
    const wrapper = mount(MainPanel, { attachTo: document.body, shallow: true, global: { plugins: [i18n] } })
    await flushPromises()
    // The action-bar entry point (onActionBarContinuousWork) normally stamps these from the
    // active tab; the consent gate refuses to open the invoke dialog without them.
    const vm = wrapper.vm as any
    vm.continuousProjectId = PROJECT
    vm.continuousGroupId = 'flowgate.default.0448'
    vm.continuousDocRef = ROOT

    await wrapper.findComponent(ContinuousWorkDialog).vm.$emit('confirm', {
      targetSeq: 2, targetType: 'P', targetLabel: 'Plan', reviewMode: false,
      instructionMode: 'auto_approved', stepCount: 2, fromDecision: false,
      providerOverrides: { 1: 'aip_plan' },
      defaultMessage: '', messageOverrides: {}, autoApproveItemSeqs: [],
      stepTimeoutSec: 3600, restartMaxAttempts: 1,
    })
    await flushPromises()
    await wrapper.findComponent(ContinuousWarningDialog).vm.$emit('confirm')
    await flushPromises()

    expect(wrapper.findComponent(AiInvokeDialog).props('providerOverrides')).toEqual({ 1: 'aip_plan' })
    wrapper.unmount()
  })
})
