// MainPanel — AI-run / document exclusivity (0378 R0001).
//
// 0394 T0016 (NR0003 §6.2-라): this suite used to read `MainPanel.vue` and
// `aiInvokeRuns.ts` as TEXT and assert where substrings sit inside them —
// `panel.indexOf('v-if="aiRunBootstrapPending"')` before
// `panel.indexOf('v-else-if="activeGroupRunActive"')`, and so on. NR0003 named it the
// strongest form of implementation coupling in this repo, with a receipt: the very
// change that introduced these assertions broke ELEVEN specs that check real
// rendering, and this file — the one called "rendering contract" — stayed green
// through all of it. Reordering the template without changing behaviour would have
// failed it; breaking the behaviour without moving the text would not.
//
// So the contract is now asserted where a user meets it: mount MainPanel through the
// shared helper and read the DOM in each of the three states. The states are named by
// what is on screen, not by the flag that produces it.

import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
  patchRequest: vi.fn(),
  putRequest: vi.fn(),
  deleteRequest: vi.fn(),
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({
    issuing: { value: false },
    issueToken: vi.fn(),
    requestReview: vi.fn(),
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

// The action bar is one of the three document-side surfaces the lease has to hide, and
// its mode comes from the network-driven workflow resolution. Pin it to `review` so the
// bar is genuinely renderable — otherwise "the bar is absent during a run" would hold
// for the wrong reason (NR0003 §5.1).
vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: false,
      currentStepCode: null,
      highlightStepCode: null,
      nextStepCode: null,
      nextStepActive: false,
      headDocLabel: null,
      headDocId: null,
      highlightDesignSeries: false,
      stepStates: [],
      nextStepIndex: null,
    }),
  }
})

import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { mountMainPanel } from '../helpers/mountMainPanel'

const GROUP_ID = 'flowgate.default.0394'
const DOC_ID = `${GROUP_ID}.0001-R`
const OTHER_DOC_ID = `${GROUP_ID}.0002-N`
const CHAT_ID = `${GROUP_ID}.0009-CH`

const R_TAB = { id: DOC_ID, title: 'requirement', path: '', type: 'md' as const, typeCode: 'R' }
const CH_TAB = { id: CHAT_ID, title: 'chat', path: '', type: 'md' as const, typeCode: 'CH' }

const fetchDoc = vi.fn()

// bindActiveRef registers this instance's exposed values as docHeaderRefs[tabId]; the
// document-side surfaces read their inputs from there.
const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  inheritAttrs: false,
  setup(_props, { expose }) {
    expose({
      docLoaded: true,
      docProjectId: 'flowgate',
      groupId: GROUP_ID,
      docReviewStatus: 'pending_review',
      fetchDoc,
    })
    return () => h('div', { class: 'doc-header-stub' })
  },
})

/** The loading card that holds every branch closed until discovery answers. */
const bootstrapCard = (w: { find: (s: string) => { exists: () => boolean } }) =>
  w.find('.ai-run-bootstrap-pending').exists()
/** The run surface that replaces the document while the group's run is live. */
const runSurface = (w: { find: (s: string) => { exists: () => boolean } }) =>
  w.find('ai-invoke-inline-stub').exists()
/** The document branch and the two side surfaces the lease locks out with it. */
const documentHeader = (w: { find: (s: string) => { exists: () => boolean } }) =>
  w.find('.doc-header-stub').exists()
const infoPanel = (w: { find: (s: string) => { exists: () => boolean } }) =>
  w.find('doc-info-panel-stub').exists()
const actionBar = (w: { find: (s: string) => { exists: () => boolean } }) =>
  w.find('review-action-bar-stub').exists()

function mountPanel(tabs = [R_TAB]) {
  return mountMainPanel({ tabs, stubs: { DocHeader: DocHeaderStub } })
}

function startRun(docRef: string, runId = 'run-1') {
  useAiInvokeRunsStore().trackStarted({
    run_id: runId,
    group_id: GROUP_ID,
    doc_ref: docRef,
    status: 'running',
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  sessionStorage.clear()
  fetchDoc.mockReset()
  getRequest.mockReset().mockResolvedValue({ data: { ok: true, runs: [], paused: [], questions: [] } })
  postRequest.mockReset().mockResolvedValue({ data: {} })
})

describe('MainPanel — the run surface and the document are mutually exclusive (0378)', () => {
  it('shows the document and both side surfaces when no run is active', async () => {
    const wrapper = await mountPanel()

    expect(documentHeader(wrapper)).toBe(true)
    expect(infoPanel(wrapper)).toBe(true)
    expect(actionBar(wrapper)).toBe(true)
    expect(runSurface(wrapper)).toBe(false)
    expect(bootstrapCard(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('replaces the document — header, info panel and action bar — while the group runs', async () => {
    const wrapper = await mountPanel()
    // Proven renderable a line earlier, so the absences below mean something.
    expect(documentHeader(wrapper)).toBe(true)

    startRun(OTHER_DOC_ID)
    await flushPromises()

    expect(runSurface(wrapper)).toBe(true)
    expect(documentHeader(wrapper)).toBe(false)
    expect(infoPanel(wrapper)).toBe(false)
    expect(actionBar(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('shows only the loading card until active-run discovery answers', async () => {
    // Discovery never answers: the gate has to fail CLOSED, so neither the document nor
    // the run surface may be on screen — a run the client has not heard about yet would
    // otherwise be edited straight through.
    getRequest.mockImplementation(async (url: string) => {
      if (String(url).includes('active-all')) return new Promise(() => {})
      return { data: { ok: true, questions: [] } }
    })

    const wrapper = await mountPanel()

    expect(bootstrapCard(wrapper)).toBe(true)
    expect(documentHeader(wrapper)).toBe(false)
    expect(runSurface(wrapper)).toBe(false)
    expect(infoPanel(wrapper)).toBe(false)
    expect(actionBar(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('opens the gate once discovery answers, and never re-closes it', async () => {
    const store = useAiInvokeRunsStore()
    // The store's own half of the fail-closed contract, read as state rather than as the
    // text `const bootstrapPending = ref(true)`.
    expect(store.bootstrapPending).toBe(true)

    const wrapper = await mountPanel()

    expect(store.bootstrapPending).toBe(false)
    expect(documentHeader(wrapper)).toBe(true)

    startRun(OTHER_DOC_ID)
    await flushPromises()
    useAiInvokeRunsStore().trackFinished({
      run_id: 'run-1',
      group_id: GROUP_ID,
      doc_ref: OTHER_DOC_ID,
      outcome: 'complete',
    })
    await flushPromises()

    expect(store.bootstrapPending).toBe(false)
    wrapper.unmount()
  })

  it('refetches the document when the run ends, so nothing comes from the pre-run cache', async () => {
    const wrapper = await mountPanel()
    startRun(OTHER_DOC_ID)
    await flushPromises()
    expect(documentHeader(wrapper)).toBe(false)
    fetchDoc.mockClear()

    useAiInvokeRunsStore().trackFinished({
      run_id: 'run-1',
      group_id: GROUP_ID,
      doc_ref: OTHER_DOC_ID,
      outcome: 'complete',
    })
    await flushPromises()
    await nextTick()
    await nextTick()

    // The v-else branch remounts a fresh header; it must be told to refetch.
    expect(documentHeader(wrapper)).toBe(true)
    expect(fetchDoc).toHaveBeenCalledWith(DOC_ID)
    wrapper.unmount()
  })

  it('covers a chat when the run targets another document', async () => {
    // 0378 removed the per-tab `suppress-doc-ref` prop: a run aimed at another document
    // is a next-document transition and covers every doc type, chat included.
    const wrapper = await mountPanel([CH_TAB])

    startRun(OTHER_DOC_ID)
    await flushPromises()

    expect(runSurface(wrapper)).toBe(true)
    expect(documentHeader(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('leaves a chat readable under its own run', async () => {
    // 0251/0258/0386 B0001: the chat's own send must not bury the conversation it wrote
    // into. This is the single carve-out, and it is decided by the run's doc_ref — not by
    // a prop handed down per tab.
    const wrapper = await mountPanel([CH_TAB])

    startRun(CHAT_ID)
    await flushPromises()

    expect(runSurface(wrapper)).toBe(false)
    expect(documentHeader(wrapper)).toBe(true)
    wrapper.unmount()
  })
})
