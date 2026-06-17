import { flushPromises, shallowMount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore } from '@main/stores/tabs'

// Acceptance regression for the workflow-decision-button bug — group 0064, the APPROVED
// NR0003 root cause that SUPERSEDED the earlier function-ref / instance-identity diagnosis.
//
// NR0003 §3/§4: the client discarded the POST /workflow/decide 201 (which already returns
// the head) and re-derived the action bar INDIRECTLY through a DocHeader-internal flip →
// parent exposed-ref re-read → detail refetch. So the workflow → next transition depended
// on the DocHeader instance lifetime, its exposed refs, headerRevision, and a successful
// detail GET — any of which could fail (dead/remounted instance, slow/401 idle-window GET),
// leaving the [워크플로 결정] button up → re-click → 409 already_decided.
//
// Fix (NR0003 §6.2): MainPanel directly OWNS the decision result via the `workflow-decided`
// event and flips the action bar to `next` from that event alone. These tests assert the
// transition holds even though the (stubbed) DocHeader NEVER reports a decided state — i.e.
// it is independent of the exposed refs and the detail GET, the exact failure modes behind
// the repeated regression. The old ref-identity spec is kept only as an auxiliary check
// (NR0003 §7: "ref 테스트는 보조 테스트로만 유지한다").

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn().mockResolvedValue({ data: { content: '' } }), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: { content: '' } }),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({
    issueToken: vi.fn(),
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

// DocHeader stub whose LIVE workflow state stays undecided for the whole test (it models
// a dead/remounted instance, or a detail GET that never lands). The decided transition
// must therefore be driven entirely by the `workflow-decided` event, not by this state.
// `liveStatus`/`liveHead` are flippable so we can later prove the override is dropped once
// the live header genuinely catches up.
const liveStatus = ref<string | null>(null)
const liveHead = ref<string | null>(null)

const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  props: { tab: { type: Object, required: true } },
  emits: ['workflow-decided', 'doc-updated', 'related-doc-created'],
  setup(_props, { expose }) {
    expose({
      docReviewStatus: liveStatus,
      workflowHeadType: liveHead,
      workflowSteps: ref(null),
      workflowRootType: ref('R'),
      workflowHeadIndex: ref(null),
      headStatus: ref(null),
      headDocId: ref(null),
      headDocReviewStatus: ref(null),
      nextStepExists: ref(false),
      docTypeCode: ref('R'),
      docProjectId: ref('flowgate'),
      groupId: ref('flowgate.default.0064'),
      headDocTitle: ref(null),
      aiReview: ref(null),
      rejectionReason: ref(null),
      rejectionHistory: ref([]),
      aiReviewHistory: ref([]),
      fetchDoc: vi.fn(),
      applyReviewTransition: vi.fn(),
    })
    return () => null
  },
})

function mountPanel() {
  return shallowMount(MainPanel, {
    global: {
      plugins: [i18n],
      stubs: {
        TabBar: true,
        DocHeader: DocHeaderStub,
        DocWorkflow: true,
        MdViewer: true,
        TextViewer: true,
        DocInfoPanel: true,
        ReviewActionBar: true,
        ReviewRejectDialog: true,
        DesignHandoffDialog: true,
        NextActionModal: true,
        NextEmptyDocModal: true,
        CommandSelectorModal: true,
        QTDetailViewer: true,
        NewQModal: true,
      },
    },
  })
}

const rTab = {
  id: 'flowgate.default.0064.0001-R',
  title: 'requirement',
  path: 'documents/flowgate/main/default/0064/0001-R.md',
  type: 'md' as const,
  typeCode: 'R',
  projectId: 'flowgate',
}

const DECISION = {
  docId: rTab.id,
  reviewStatus: 'wf_in_progress',
  steps: ['N', 'NR'],
  headType: 'N',
  headLabel: '조사지시',
}

beforeEach(() => {
  setActivePinia(createPinia())
  liveStatus.value = null
  liveHead.value = null
})

describe('MainPanel workflow-decided transition (group 0064 — approved NR0003 acceptance)', () => {
  it('flips the action bar workflow → next from the decision event, independent of DocHeader state / detail GET', async () => {
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...rTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.activeTabId).toBe(rTab.id)

    // Undecided to start: the only offered action is [워크플로 결정].
    expect(vm.getActionBarMode(rTab.id)).toBe('workflow')

    // DocHeader emits the confirmed decision exactly as it does after a POST 201
    // (head from the response, sequence from the dialog payload).
    wrapper.findComponent(DocHeaderStub).vm.$emit('workflow-decided', { ...DECISION })
    await nextTick()

    // Transition happened from the event alone…
    expect(vm.getActionBarMode(rTab.id)).toBe('next')
    expect(vm.getWorkflowViewState(rTab.id).canNextAction).toBe(true)
    expect(vm.getNextStepCode(rTab.id)).toBe('N')
    expect(vm.getNextStepLabel(rTab.id)).toBeTruthy()

    // …while the live DocHeader NEVER reported decided. This is the whole point: the
    // 6-time regression was a transition that depended on exactly this (the exposed
    // refs / the detail GET). Here it does not.
    expect(vm.liveReportsDecided(rTab.id)).toBe(false)
  })

  it('removes the [워크플로 결정] affordance after the decision so a re-click / 409 is impossible', async () => {
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...rTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.getActionBarMode(rTab.id)).toBe('workflow') // decide offered

    wrapper.findComponent(DocHeaderStub).vm.$emit('workflow-decided', { ...DECISION })
    await nextTick()

    // 'workflow' is the ONLY mode that renders [워크플로 결정]; leaving it means the button
    // is gone, so the user cannot re-click and cannot trigger 409 already_decided.
    expect(vm.getActionBarMode(rTab.id)).not.toBe('workflow')
  })

  it('uses the server-confirmed head label captured at decision time', async () => {
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...rTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    wrapper.findComponent(DocHeaderStub).vm.$emit('workflow-decided', { ...DECISION })
    await nextTick()

    // The label comes from the decision payload (POST head.label / dialog sequence),
    // not from a re-fetch of the document the client already had the answer for.
    expect(vm.getNextStepLabel(rTab.id)).toBe('조사지시')
  })

  it('drops the override once the live header reports decided, so a later transition is not frozen at next', async () => {
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...rTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    wrapper.findComponent(DocHeaderStub).vm.$emit('workflow-decided', { ...DECISION })
    await nextTick()
    expect(vm.getActionBarMode(rTab.id)).toBe('next')
    expect(vm.decidedOverrides[rTab.id]).toBeTruthy()

    // Live header catches up — and later advances past 'next' to a completed sequence.
    liveStatus.value = 'wf_done'
    vm.onDocHeaderUpdated({ docId: rTab.id })
    await nextTick()

    // The override is GC'd and the mode follows live data (wf_done → info), not frozen
    // at 'next'. This is what keeps the override from masking real progression.
    expect(vm.decidedOverrides[rTab.id]).toBeUndefined()
    expect(vm.getActionBarMode(rTab.id)).toBe('info')
  })
})
