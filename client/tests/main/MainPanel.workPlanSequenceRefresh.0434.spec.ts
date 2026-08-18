// MainPanel — pouring a plan into the sequence must refresh the open WP tab (0434 TR0005 rev1).
//
// Rejection on TR0005 rev0: "F5를 누르지 않으면 적용되지 않는거 내가 고치라는건 이것 하나뿐인데"
// ("it doesn't apply unless you press F5 — that's the ONLY thing I asked you to fix").
//
// Root cause: pouring a plan into the workflow sequence happens in DocWorkflow.vue, a sibling
// of WorkPlanEditor.vue under MainPanel.vue. DocWorkflow emits 'sequence-updated' after a pour
// (and after the sequence-edit modal saves), but MainPanel only ever forwarded that to
// docHeaderRefs[tab.id]?.fetchDoc — WorkPlanEditor was mounted with no ref binding at all, so
// its own state (including the [마지막 적용] "last application" line, one of the three screens
// NR0003 §8 named as disagreeing with each other) never refetched. Only onMounted, a docId
// change (switching tabs away and back), or its own 'fg:ai_invoke' listener called
// WorkPlanEditor.fetchPlan(). A user who pours while staying on the same WP tab saw stale
// "적용" state until a manual reload.
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mountMainPanel } from '../helpers/mountMainPanel'

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn(),
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
    requestWorkflowDecision: vi.fn(),
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

const GROUP_ID = 'flowgate.default.0434'
const WP_DOC_ID = `${GROUP_ID}.0002-WP`
const WP_TAB = { id: WP_DOC_ID, title: 'plan', path: '', type: 'md' as const, typeCode: 'WP', projectId: 'flowgate' }

const fetchPlanSpy = vi.fn()

const WorkPlanEditorStub = defineComponent({
  name: 'WorkPlanEditor',
  props: { docId: { type: String, default: '' } },
  setup(_props, { expose }) {
    expose({ fetchPlan: fetchPlanSpy })
    return () => h('div', { class: 'wp-editor-stub' })
  },
})

const DocWorkflowStub = defineComponent({
  name: 'DocWorkflow',
  emits: ['sequence-updated'],
  setup(_props, { emit }) {
    return () => h('button', {
      class: 'doc-workflow-pour-stub',
      onClick: () => emit('sequence-updated'),
    })
  },
})

function mountPanel() {
  return mountMainPanel({
    tabs: [WP_TAB],
    stubs: { DocWorkflow: DocWorkflowStub, WorkPlanEditor: WorkPlanEditorStub },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  fetchPlanSpy.mockReset()
})

describe('MainPanel: pour refreshes the open WorkPlanEditor tab (0434 regression)', () => {
  it('does not refetch the plan on mount alone', async () => {
    await mountPanel()
    expect(fetchPlanSpy).not.toHaveBeenCalled()
  })

  it('refetches the plan when DocWorkflow reports the sequence changed', async () => {
    const wrapper = await mountPanel()

    await wrapper.find('.doc-workflow-pour-stub').trigger('click')

    expect(fetchPlanSpy).toHaveBeenCalledTimes(1)
  })
})
