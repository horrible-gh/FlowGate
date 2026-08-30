import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { summarizeEditSaveError } from '@main/utils/editSaveError'

const { getRequest, patchRequest, apiGet, apiPatch, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: apiPatch },
  getRequest,
  patchRequest,
  postRequest: vi.fn(),
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

function mountPanel() {
  return mount(MainPanel, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: {
        TabBar: true,
        DocHeader: true,
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
        StepVerificationCard: true,
      },
    },
  })
}

const trTab = {
  id: 'flowgate.default.0484.9001-TR',
  title: 'legacy tr',
  path: 'documents/flowgate/main/default/0484/9001-TR_document.md',
  type: 'md' as const,
  typeCode: 'TR',
  projectId: 'flowgate',
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  patchRequest.mockReset()
  apiGet.mockReset()
  apiPatch.mockReset()
  showToast.mockReset()
  document.body.innerHTML = ''
})

describe('MainPanel edit save failure recovery (0484 T0005)', () => {
  it('keeps the edited text and enabled save control, shows the full banner, then closes on retry success', async () => {
    const detail = 'Modification not allowed after final approval.\n[1] reason\n[3] next action'
    getRequest.mockResolvedValue({ data: { content: '# user text' } })
    patchRequest
      .mockRejectedValueOnce({ response: { data: { detail } } })
      .mockResolvedValueOnce({ data: {} })
    const wrapper = mountPanel()
    const vm = wrapper.vm as any

    await vm.openEditModal({ ...trTab })
    await flushPromises()
    const textarea = document.querySelector<HTMLTextAreaElement>('.document-editor__textarea')!
    textarea.value = '# user changed text'
    textarea.dispatchEvent(new Event('input'))
    await nextTick()
    await vm.saveEditContent()
    await flushPromises()

    expect(document.querySelector('.document-editor__textarea')).not.toBeNull()
    expect(document.querySelector<HTMLTextAreaElement>('.document-editor__textarea')!.value)
      .toBe('# user changed text')
    expect(document.querySelector('.document-editor__save-error')!.textContent).toContain(detail)
    expect(document.querySelector<HTMLButtonElement>('.document-modal--edit .btn-primary')!.disabled)
      .toBe(false)
    expect(showToast).toHaveBeenLastCalledWith(
      'Modification not allowed after final approval. See the edit window for details.',
      'danger',
      7000,
    )

    await vm.saveEditContent()
    await flushPromises()

    expect(document.querySelector('.document-modal--edit')).toBeNull()
    expect(showToast).toHaveBeenLastCalledWith('Document saved.', 'success')
    wrapper.unmount()
  })

  it('still blocks editing when the initial content load fails', async () => {
    getRequest.mockRejectedValue(new Error('load failed'))
    const wrapper = mountPanel()
    const vm = wrapper.vm as any

    await vm.openEditModal({ ...trTab })
    await flushPromises()

    expect(document.querySelector('.document-editor__textarea')).toBeNull()
    expect(document.querySelector('.document-editor__state--error')!.textContent).toContain('load failed')
    expect(document.querySelector<HTMLButtonElement>('.document-modal--edit .btn-primary')!.disabled)
      .toBe(true)
    wrapper.unmount()
  })

  it.each([
    [{ ...trTab, id: 'flowgate.default.0484.9002-N', typeCode: 'N' }, 'document'],
    [{
      id: 'src-note',
      title: 'note',
      path: 'client/src/note.txt',
      sourcePath: 'client/src/note.txt',
      type: 'text' as const,
      projectId: 'flowgate',
    }, 'source'],
  ])('keeps the editor retryable for a non-TR or source tab: %s', async (tab, kind) => {
    const detail = 'conflict\nserver details'
    getRequest.mockResolvedValue({ data: { content: 'draft' } })
    apiGet.mockResolvedValue({ data: 'draft', headers: { etag: '"v1"' } })
    patchRequest.mockRejectedValue({ response: { data: { detail } } })
    apiPatch.mockRejectedValue({ response: { data: { detail } } })
    const wrapper = mountPanel()
    const vm = wrapper.vm as any

    await vm.openEditModal(tab)
    await flushPromises()
    await vm.saveEditContent()
    await flushPromises()

    expect(document.querySelector('.document-editor__textarea')).not.toBeNull()
    expect(document.querySelector<HTMLButtonElement>('.document-modal--edit .btn-primary')!.disabled)
      .toBe(false)
    expect(document.querySelector('.document-editor__save-error')!.textContent).toContain(detail)
    if (kind === 'source') expect(apiPatch).toHaveBeenCalled()
    else expect(patchRequest).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('summarizes only the first line and caps long toast text', () => {
    expect(summarizeEditSaveError('first line\nsecond line')).toBe('first line')
    expect(summarizeEditSaveError('abcdef', 4)).toBe('abc…')
  })
})
