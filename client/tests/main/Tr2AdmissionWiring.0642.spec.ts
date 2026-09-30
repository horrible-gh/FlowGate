import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h, ref } from 'vue'
import { mountMainPanel } from '../helpers/mountMainPanel'

const { getRequest, refreshTr2 } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: { questions: [] } }),
  refreshTr2: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
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
    issueToken: vi.fn(),
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: false,
      currentStepCode: 'TR2',
      highlightStepCode: 'TR2',
      nextStepCode: null,
      nextStepActive: false,
      headDocLabel: 'TR2',
      headDocId: null,
      highlightDesignSeries: false,
      stepStates: [],
      nextStepIndex: null,
    }),
  }
})

const DOC_ID = 'flowgate.default.0642.0009-TR2'
const TAB = {
  id: DOC_ID,
  title: '0642 TR2',
  path: 'document.json',
  type: 'md' as const,
  typeCode: 'TR2',
  projectId: 'flowgate',
}

const DocHeaderStub = defineComponent({
  name: 'DocHeader',
  setup(_props, { expose }) {
    expose({
      groupId: ref('flowgate.default.0642'),
      docProjectId: ref('flowgate'),
      docReviewStatus: ref('pending_review'),
      canEditDocument: ref(true),
      groupDisposed: ref(false),
    })
    return () => h('div')
  },
})

const Tr2DocumentBodyStub = defineComponent({
  name: 'Tr2DocumentBody',
  setup(_props, { expose }) {
    expose({
      commandAdmissionFingerprint: ref('visible-admission-fp'),
      refresh: refreshTr2,
    })
    return () => h('div', { 'data-testid': 'tr2-body-stub' })
  },
})

const ReviewActionBarStub = defineComponent({
  name: 'ReviewActionBar',
  props: {
    commandAdmissionFingerprint: { type: String, default: null },
  },
  emits: ['tr2-stale'],
  setup(_props, { emit }) {
    return () => h('button', {
      'data-testid': 'emit-tr2-stale',
      onClick: () => emit('tr2-stale'),
    })
  },
})

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockClear()
  refreshTr2.mockClear()
})

describe('0642 TR2 admission wiring', () => {
  it('carries the visible body fingerprint to the action bar and refreshes that body on stale', async () => {
    const wrapper = await mountMainPanel({
      tabs: [TAB],
      stubs: {
        DocHeader: DocHeaderStub,
        Tr2DocumentBody: Tr2DocumentBodyStub,
        ReviewActionBar: ReviewActionBarStub,
      },
    })
    await flushPromises()

    const bar = wrapper.findComponent(ReviewActionBarStub)
    expect(bar.exists()).toBe(true)
    expect(bar.props('commandAdmissionFingerprint')).toBe('visible-admission-fp')

    await wrapper.find('[data-testid="emit-tr2-stale"]').trigger('click')
    await flushPromises()
    expect(refreshTr2).toHaveBeenCalledTimes(1)
  })
})
