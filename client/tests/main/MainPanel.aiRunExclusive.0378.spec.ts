// MainPanel — what an AI run does to the document on screen (0378 R0001 → 0398 → 0404).
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
// The rewrite met a moving contract head-on, and that is worth recording. 0378 made the
// run surface and the document MUTUALLY EXCLUSIVE — a run replaced the document outright.
// 0398 then reversed exactly that ("keep documents readable during AI runs") and 0404
// extended it to the workflow strip, so today the status card and the document coexist:
//
//   * only the BOOTSTRAP gate is still exclusive — until active-run discovery answers,
//     neither the document nor the run surface may be on screen (fail closed);
//   * during a run the document stays mounted and READ-ONLY, while the surfaces that
//     exist to mutate it — the info panel, the review action bar — are withdrawn;
//   * a chat's own run is the single carve-out: it neither covers nor locks its chat.
//
// So the contract is asserted where a user meets it: mount MainPanel through the shared
// helper and read the DOM in each state. The states are named by what is on screen, not
// by the flag that produces it — which is why the 0398/0404 reversal changed the
// expectations here without changing the shape of a single test.

import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'

const { getRequest, postRequest, workflowNextStepCode } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  workflowNextStepCode: { value: null as string | null },
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

// The action bar is one of the document-side surfaces the lease has to withdraw, and its
// mode comes from the network-driven workflow resolution. Pin it to `review` so the bar is
// genuinely renderable — otherwise "the bar is absent during a run" would hold for the
// wrong reason (NR0003 §5.1).
vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: false,
      currentStepCode: null,
      highlightStepCode: null,
      nextStepCode: workflowNextStepCode.value,
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
// document-side surfaces read their inputs from there. The stub also mirrors its own
// `read-only` prop into the DOM, so the lock can be read off the rendered tree rather
// than off a component-internals lookup.
const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  props: { readOnly: { type: Boolean, default: false } },
  inheritAttrs: false,
  setup(props, { expose }) {
    expose({
      docLoaded: true,
      docProjectId: 'flowgate',
      groupId: GROUP_ID,
      docReviewStatus: 'pending_review',
      fetchDoc,
    })
    return () => h('div', { class: 'doc-header-stub', 'data-read-only': String(props.readOnly) })
  },
})

// 0404: the workflow strip stays on screen through a run, read-only — it used to vanish
// with the rest of the document.
const DocWorkflowStub = defineComponent({
  name: 'DocWorkflow',
  props: { readOnly: { type: Boolean, default: false } },
  inheritAttrs: false,
  setup(props) {
    return () => h('div', { class: 'doc-workflow-stub', 'data-read-only': String(props.readOnly) })
  },
})

type Probe = { find: (s: string) => { exists: () => boolean; attributes: (a: string) => string | undefined } }

/** The loading card that holds every branch closed until discovery answers. */
const bootstrapCard = (w: Probe) => w.find('.ai-run-bootstrap-pending').exists()
/** The run surface — the status card the group's run renders into. */
const runSurface = (w: Probe) => w.find('ai-invoke-inline-stub').exists()
/** The document branch, and the two side surfaces that exist to mutate it. */
const documentHeader = (w: Probe) => w.find('.doc-header-stub').exists()
const workflowStrip = (w: Probe) => w.find('.doc-workflow-stub').exists()
const infoPanel = (w: Probe) => w.find('doc-info-panel-stub').exists()
const actionBar = (w: Probe) => w.find('review-action-bar-stub').exists()
/** Whether the still-mounted document is locked against writes. */
const documentLocked = (w: Probe) => w.find('.doc-header-stub').attributes('data-read-only') === 'true'
const workflowLocked = (w: Probe) => w.find('.doc-workflow-stub').attributes('data-read-only') === 'true'

function mountPanel(tabs = [R_TAB]) {
  return mountMainPanel({
    tabs,
    stubs: { DocHeader: DocHeaderStub, DocWorkflow: DocWorkflowStub },
  })
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
  workflowNextStepCode.value = null
})

describe('MainPanel — what an AI run does to the document on screen', () => {
  it('shows the document, unlocked, with both side surfaces when no run is active', async () => {
    const wrapper = await mountPanel()

    expect(documentHeader(wrapper)).toBe(true)
    expect(workflowStrip(wrapper)).toBe(true)
    expect(infoPanel(wrapper)).toBe(true)
    expect(actionBar(wrapper)).toBe(true)
    expect(documentLocked(wrapper)).toBe(false)
    expect(workflowLocked(wrapper)).toBe(false)
    expect(runSurface(wrapper)).toBe(false)
    expect(bootstrapCard(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('keeps a group file tab read-only and does not expose src-content editing', async () => {
    const fileTab = {
      id: 'file:flowgate:flowgate.default.0394:src/a.ts',
      title: 'a.ts',
      path: 'src/a.ts',
      type: 'text' as const,
      projectId: 'flowgate',
      gitGroupId: GROUP_ID,
      readonly: true,
    }
    const wrapper = await mountPanel([fileTab as any])
    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(false)
    expect(wrapper.find('.doc-action-edit').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the document readable but locked while the group runs, and withdraws the write surfaces', async () => {
    const wrapper = await mountPanel()
    // Proven unlocked and renderable a line earlier, so the changes below mean something.
    expect(documentLocked(wrapper)).toBe(false)
    expect(infoPanel(wrapper)).toBe(true)

    startRun(OTHER_DOC_ID)
    await flushPromises()

    // 0398/0404: the run reports alongside the document instead of replacing it — the
    // header and the workflow strip are still there to read, just not to write to.
    expect(runSurface(wrapper)).toBe(true)
    expect(documentHeader(wrapper)).toBe(true)
    expect(workflowStrip(wrapper)).toBe(true)
    expect(documentLocked(wrapper)).toBe(true)
    expect(workflowLocked(wrapper)).toBe(true)
    // The surfaces that exist only to mutate the document do go.
    expect(infoPanel(wrapper)).toBe(false)
    expect(actionBar(wrapper)).toBe(false)
    wrapper.unmount()
  })

  it('closes an already-open work-plan proposal when the group run starts', async () => {
    const wrapper = await mountPanel()
    wrapper.findComponent(DocWorkflowStub).vm.$emit('create-work-plan', { docId: DOC_ID })
    await nextTick()

    const proposal = wrapper.findComponent({ name: 'WorkPlanProposalDialog' })
    expect(proposal.props('visible')).toBe(true)

    startRun(OTHER_DOC_ID)
    await flushPromises()

    expect(proposal.props('visible')).toBe(false)
    wrapper.unmount()
  })

  it('closes an already-open work-plan create dialog when the group run starts', async () => {
    const wrapper = await mountPanel()
    ;(wrapper.vm as any).workPlanCreateVisible = true
    await nextTick()

    const createDialog = wrapper.findComponent({ name: 'WorkPlanCreateDialog' })
    expect(createDialog.props('visible')).toBe(true)

    startRun(OTHER_DOC_ID)
    await flushPromises()

    expect(createDialog.props('visible')).toBe(false)
    wrapper.unmount()
  })

  it('shows only the loading card until active-run discovery answers', async () => {
    // Discovery never answers: the gate has to fail CLOSED, so neither the document nor
    // the run surface may be on screen — a run the client has not heard about yet would
    // otherwise be edited straight through. This is the one state 0398 left exclusive.
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
    store.trackFinished({
      run_id: 'run-1',
      group_id: GROUP_ID,
      doc_ref: OTHER_DOC_ID,
      outcome: 'complete',
    })
    await flushPromises()

    expect(store.bootstrapPending).toBe(false)
    wrapper.unmount()
  })

  it('refetches the document when the run ends, so nothing is left over from the pre-run read', async () => {
    const wrapper = await mountPanel()
    startRun(OTHER_DOC_ID)
    await flushPromises()
    expect(documentLocked(wrapper)).toBe(true)
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

    // The header is never unmounted now, so a remount cannot do the refresh for it: the
    // run transition has to tell the still-mounted instance to refetch, or the user reads
    // the document as it was before the AI wrote to it.
    expect(fetchDoc).toHaveBeenCalledWith(DOC_ID)
    // ...and the lock lifts with the write surfaces coming back.
    expect(documentLocked(wrapper)).toBe(false)
    expect(infoPanel(wrapper)).toBe(true)
    expect(actionBar(wrapper)).toBe(true)
    wrapper.unmount()
  })

  it('locks a chat like any other document when the run targets another one', async () => {
    // 0378 removed the per-tab `suppress-doc-ref` prop: a run aimed at another document is
    // a next-document transition and applies to every doc type, chat included.
    const wrapper = await mountPanel([CH_TAB])

    startRun(OTHER_DOC_ID)
    await flushPromises()

    expect(runSurface(wrapper)).toBe(true)
    expect(documentHeader(wrapper)).toBe(true)
    expect(documentLocked(wrapper)).toBe(true)
    wrapper.unmount()
  })

  it('hands the lock to the work-plan editor, the card a WP tab renders instead of MdViewer', async () => {
    // 0424 B0001 rev2. Every other card in the document column takes aiRunDocumentLocked;
    // WorkPlanEditor took no lock at all, so a WP tab kept 저장 / 수량 / 공급자 / 멘트 /
    // [AI 제안 불러오기] live through a run and answered the click with a 423 toast. That is
    // the screen both rejections were written from — the two runs that preceded them were
    // work_plan_fill runs whose doc_ref was the WP document itself.
    const wpTab = { id: `${GROUP_ID}.0004-WP`, title: 'work plan', path: '', type: 'md' as const, typeCode: 'WP' }
    const wrapper = await mountPanel([wpTab])
    expect(wrapper.find('work-plan-editor-stub').exists()).toBe(true)
    expect(wrapper.find('work-plan-editor-stub').attributes('readonly')).toBe('false')

    startRun(wpTab.id)
    await flushPromises()

    expect(wrapper.find('work-plan-editor-stub').exists()).toBe(true)
    expect(wrapper.find('work-plan-editor-stub').attributes('readonly')).toBe('true')
    wrapper.unmount()
  })

  it('leaves a chat unlocked under its own run', async () => {
    // 0251/0258/0386 B0001: the chat's own send must not lock or bury the conversation it
    // wrote into. This is the single carve-out, and it is decided by the run's doc_ref —
    // not by a prop handed down per tab.
    const wrapper = await mountPanel([CH_TAB])

    startRun(CHAT_ID)
    await flushPromises()

    expect(runSurface(wrapper)).toBe(false)
    expect(documentHeader(wrapper)).toBe(true)
    expect(documentLocked(wrapper)).toBe(false)
    expect(actionBar(wrapper)).toBe(true)
    wrapper.unmount()
  })
})
