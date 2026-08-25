import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mountMainPanel } from '../helpers/mountMainPanel'

// TS0011 TC-4/TC-6 gap (rejection rev0): DocInfoPanel.spec.ts injects `candidateSlots`
// as a prop directly, and no client test ever exercised the real chain — a real
// GET /relations response read by DocHeader (workflowCandidateSlots), forwarded by
// MainPanel's `:candidate-slots` binding (MainPanel.vue:413) into the real DocInfoPanel.
// Either hop silently dropping candidate_slots (DocHeader never reading
// `res.data?.workflow?.candidate_slots`, or MainPanel forwarding the wrong field) would
// leave the button permanently enabled with a blind {} POST or permanently disabled —
// and TC-4/TC-6 as they existed before this revision would not have noticed either way.
// This mounts the real MainPanel → real DocHeader → real DocInfoPanel with only the
// network layer mocked, so the only thing that can make the button state come out
// right is the actual prop-forwarding path.

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
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

const DOC_ID = 'flowgate.default.0457.9001-TR'

const docTab = {
  id: DOC_ID,
  title: 'TR doc',
  path: `documents/${DOC_ID}.md`,
  type: 'md' as const,
  typeCode: 'TR',
}

type CandidateSlot = { item_seq: number; type: string; empty: boolean }

function mockNetwork(candidateSlots: CandidateSlot[]) {
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.startsWith('/api/v1/documents/detail')) {
      return Promise.resolve({
        data: {
          doc_id: DOC_ID,
          title: 'TR doc',
          status: 'open',
          doc_review_status: 'pending_review',
          type_code: 'TR',
        },
      })
    }
    if (url.endsWith('/relations')) {
      return Promise.resolve({ data: { workflow: { orphan: true, candidate_slots: candidateSlots } } })
    }
    return Promise.resolve({ data: { qa: { items: [] }, copied: false } })
  })
  postRequest.mockReset()
  postRequest.mockResolvedValue({ data: { ok: true } })
}

beforeEach(() => {
  setActivePinia(createPinia())
  showToast.mockReset()
})

describe('candidate_slots real /relations → DocHeader → MainPanel → DocInfoPanel (0457 T0009 TS0011 TC-4/TC-6)', () => {
  it('a type-matching empty slot from /relations reaches the button and drives the item_seq POST', async () => {
    mockNetwork([{ item_seq: 4, type: 'TR', empty: true }])

    const wrapper = await mountMainPanel({
      deep: true,
      tabs: [docTab],
      stubs: { DocHeader: false, DocInfoPanel: false },
    })

    const docInfoPanel = wrapper.findComponent({ name: 'DocInfoPanel' })
    expect(docInfoPanel.exists()).toBe(true)
    // MainPanel.vue:413 must have forwarded exactly what DocHeader read from /relations.
    expect(docInfoPanel.props('candidateSlots')).toEqual([{ item_seq: 4, type: 'TR', empty: true }])
    expect(docInfoPanel.props('orphan')).toBe(true)

    const button = wrapper.find('.dip-orphan-warning button')
    expect(button.exists()).toBe(true)
    expect(button.attributes('disabled')).toBeUndefined()

    await button.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(postRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${DOC_ID}/workflow/recover`,
      { item_seq: 4 },
    )
  })

  it('a type-mismatched slot from /relations reaches the button and keeps it disabled with no POST', async () => {
    mockNetwork([{ item_seq: 4, type: 'NR', empty: true }])

    const wrapper = await mountMainPanel({
      deep: true,
      tabs: [docTab],
      stubs: { DocHeader: false, DocInfoPanel: false },
    })

    const docInfoPanel = wrapper.findComponent({ name: 'DocInfoPanel' })
    expect(docInfoPanel.props('candidateSlots')).toEqual([{ item_seq: 4, type: 'NR', empty: true }])

    const button = wrapper.find('.dip-orphan-warning button')
    expect(button.exists()).toBe(true)
    expect(button.attributes('disabled')).toBeDefined()

    await button.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(postRequest).not.toHaveBeenCalled()
  })
})
