// 0419 T0006: the sidebar [수정] entry point (DocInfoPanel's edit-rejection emit)
// reopens ReviewRejectDialog in edit mode, and a save in that mode must go through
// the PATCH-correction endpoint, not a new POST reject.
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mountMainPanel } from '../helpers/mountMainPanel'

const { patchRequest, postRequest } = vi.hoisted(() => ({
  patchRequest: vi.fn().mockResolvedValue({ data: { document: { doc_review_status: 'rejected' } } }),
  postRequest: vi.fn().mockResolvedValue({ data: { document: { doc_review_status: 'rejected' } } }),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  patchRequest,
  postRequest,
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
    composeMention: (token: any) => token?.mention ?? '',
    copyMentToClipboard: vi.fn().mockResolvedValue(true),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

const trTab = {
  id: 'flowgate.default.0419.0005-TR',
  title: '반려 다이얼로그 확대·수정 CRUD 시안 등록 결과',
  path: 'documents/flowgate/main/default/0419/0005-TR.md',
  type: 'md' as const,
  typeCode: 'TR',
  projectId: 'flowgate',
}

beforeEach(() => {
  setActivePinia(createPinia())
  patchRequest.mockClear()
  postRequest.mockClear()
})

describe('MainPanel edit-rejection wiring (0419 T0006)', () => {
  it('defaults ReviewRejectDialog to non-edit mode before any [수정] interaction', async () => {
    const wrapper = await mountMainPanel({ tabs: [trTab] })
    const dialog = wrapper.findComponent({ name: 'ReviewRejectDialog' })
    expect(dialog.props('editMode')).toBeFalsy()
  })

  it('opens ReviewRejectDialog in edit mode from the sidebar entry point', async () => {
    const wrapper = await mountMainPanel({ tabs: [trTab] })

    wrapper.findComponent({ name: 'DocInfoPanel' }).vm.$emit('edit-rejection')
    await flushPromises()

    const dialog = wrapper.findComponent({ name: 'ReviewRejectDialog' })
    expect(dialog.props('visible')).toBe(true)
    expect(dialog.props('editMode')).toBe(true)
    expect(dialog.props('docId')).toBe(trTab.id)
  })

  it('saves an edit-mode correction through PATCH .../rejection_reason, not a new reject POST', async () => {
    const wrapper = await mountMainPanel({ tabs: [trTab] })

    wrapper.findComponent({ name: 'DocInfoPanel' }).vm.$emit('edit-rejection')
    await flushPromises()

    wrapper.findComponent({ name: 'ReviewRejectDialog' }).vm.$emit('save-reason', '고친 반려 사유')
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledWith(
      `/api/v1/documents/${trTab.id}/rejection_reason`,
      { reason: '고친 반려 사유' },
    )
    expect(postRequest).not.toHaveBeenCalled()
  })
})
