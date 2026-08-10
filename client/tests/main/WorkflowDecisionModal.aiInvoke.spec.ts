// 0268 B0001 / NR0003 결함 1: the post-decision "시퀀스 수정" dialog offered a single button
// labelled "AI에게 수정 요청" with a robot icon that only wrote the clipboard — the label is
// precisely what hid the missing in-app call, since the surface *read* as though the AI path
// already existed. The copy and the invoke must now both be present and independently work.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

const postRequest = vi.fn()
const getRequest = vi.fn()
const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  postRequest: (...args: unknown[]) => postRequest(...args),
  getRequest: (...args: unknown[]) => getRequest(...args),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
}))

const showToast = vi.fn()
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

// The clipboard half is exercised by its own path; here it only has to be observable so
// the invoke assertions can prove the two entrances are genuinely independent.
const copiedTexts: string[] = []
vi.mock('@main/utils/clipboard', () => ({
  ClipboardAbort: class ClipboardAbort extends Error {},
  copyToClipboardDeferred: async (produce: () => Promise<string>) => {
    try {
      copiedTexts.push(await produce())
    } catch {
      return false
    }
    return true
  },
  consumeLastFailedCopyText: () => null,
}))

const requestSequenceEdit = vi.fn()
vi.mock('@main/composables/useFlowGateToken', async () => {
  // `issuing` must be a real ref: the footer binds :disabled="... || issuing", and a plain
  // object unwraps to a truthy value in the template, which would silently disable both
  // buttons and make every assertion below pass for the wrong reason.
  const { ref } = await import('vue')
  const issuing = ref(false)
  return {
    useFlowGateToken: () => ({
      requestSequenceEdit: (...args: unknown[]) => requestSequenceEdit(...args),
      composeMention: (token: { mention?: string }) => token?.mention ?? 'MENTION',
      issuing,
    }),
  }
})

import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'

const DOC_ID = 'proj.mod.0268.0004-T'

function mountModal() {
  return mount(WorkflowDecisionModal, {
    props: { visible: true, mode: 'edit', docId: DOC_ID },
    global: { plugins: [i18n], stubs: { teleport: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  postRequest.mockReset()
  getRequest.mockReset()
  patchRequest.mockReset()
  showToast.mockReset()
  requestSequenceEdit.mockReset()
  copiedTexts.length = 0
  i18n.global.locale.value = 'en'
  // A decided sequence with one locked and one pending step — the edit mode's precondition.
  // The provider list is answered too: since 0399 (D0010 §3.7 / L0011 §4.4) the picker is
  // drawn only when this project actually has a usable provider, so a run of this spec that
  // reported an empty list would be asserting the picker's position in a screen that, by
  // design, has no picker. One provider is also the honest precondition for these tests —
  // they are about handing the edit to an AI.
  getRequest.mockImplementation((url: string) => {
    if (String(url).includes('/ai-invoke/providers')) {
      return Promise.resolve({
        data: {
          ok: true,
          project: 'proj',
          providers: [{ id: 'claude', name: 'Claude', exec_type: 'cli', kind: 'claude' }],
          default_provider_id: 'claude',
        },
      })
    }
    return Promise.resolve({
      data: {
        items: [
          { type: 'N', label: 'investigate', status: 'done' },
          { type: 'T', label: 'implement', status: 'pending' },
        ],
      },
    })
  })
})

function footerButtons(wrapper: ReturnType<typeof mountModal>) {
  return wrapper.findAll('.modal-ft button')
}

function buttonByText(wrapper: ReturnType<typeof mountModal>, key: string) {
  const label = i18n.global.t(key)
  const found = footerButtons(wrapper).find((b) => b.text().includes(label))
  expect(found, `missing footer button for ${key} ("${label}")`).toBeTruthy()
  return found!
}

describe('WorkflowDecisionModal — sequence edit hand-off (0268 B0001)', () => {
  it('offers the mention copy and the in-app AI invoke side by side', async () => {
    const wrapper = mountModal()
    await flushPromises()
    // Both entrances present: 멘트복사와 AI 호출은 택일이 아니라 병행.
    const copyBtn = buttonByText(wrapper, 'main.workflow_edit_modal.mention_copy')
    const invokeBtn = buttonByText(wrapper, 'main.workflow_edit_modal.invoke_ai')
    // Present AND actually clickable — a disabled button is not an entrance. (Asserted
    // because an early version of this spec's useFlowGateToken mock returned a non-ref
    // `issuing`, which disabled both buttons while the presence checks still passed.)
    expect(copyBtn.attributes('disabled')).toBeUndefined()
    expect(invokeBtn.attributes('disabled')).toBeUndefined()
    // And the misleading label that hid the gap is gone for good.
    expect(wrapper.text()).not.toContain('AI에게 수정 요청')
    expect(wrapper.text()).not.toContain('Ask AI to edit')
  })

  it('puts the provider picker at the far left of the footer, ahead of every action', async () => {
    const wrapper = mountModal()
    await flushPromises()
    // rev1 review: the picker must lead the footer row, not sit between the copy and
    // invoke buttons. Asserted on DOM order within .modal-ft, so any re-ordering fails here.
    const footer = wrapper.find('.modal-ft')
    const children = Array.from(footer.element.children)
    const picker = wrapper.find('.wdm-provider')
    expect(picker.exists()).toBe(true)
    expect(children.indexOf(picker.element)).toBe(0)
    // ...and it precedes the first button in the same row.
    const firstButton = footer.element.querySelector('button')
    expect(firstButton).toBeTruthy()
    expect(
      picker.element.compareDocumentPosition(firstButton!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('invoke starts a workflow_sequence_edit run and keeps the token out of the browser', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'air_7' } })
    const wrapper = mountModal()
    await flushPromises()

    await buttonByText(wrapper, 'main.workflow_edit_modal.invoke_ai').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/start', {
      project: 'proj',
      module: 'mod',
      group: '0268',
      doc_ref: DOC_ID,
      action_scope: 'workflow_sequence_edit',
      mode: 'single',
      provider_id: 'claude',
    })
    // The server mints and delivers the mention on this path, so nothing is copied and
    // no token is issued client-side.
    expect(copiedTexts).toEqual([])
    expect(requestSequenceEdit).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.workflow_edit_modal.toast_ai_invoke_started'),
      'success',
    )
  })

  it('invoke failure surfaces the server message and leaves the dialog open', async () => {
    postRequest.mockRejectedValue({ response: { data: { message: 'sequence_not_decided' } } })
    const wrapper = mountModal()
    await flushPromises()

    await buttonByText(wrapper, 'main.workflow_edit_modal.invoke_ai').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledWith('sequence_not_decided', 'error')
    // A failed invoke must not close the dialog, or the user loses the copy fallback.
    expect(wrapper.emitted('update:visible')).toBeUndefined()
    buttonByText(wrapper, 'main.workflow_edit_modal.mention_copy')
  })

  it('the copy entrance still issues a token and copies, untouched by the invoke path', async () => {
    requestSequenceEdit.mockResolvedValue({ mention: 'SEQ EDIT MENTION + token' })
    const wrapper = mountModal()
    await flushPromises()

    await buttonByText(wrapper, 'main.workflow_edit_modal.mention_copy').trigger('click')
    await flushPromises()

    expect(requestSequenceEdit).toHaveBeenCalledWith(DOC_ID)
    expect(copiedTexts).toEqual(['SEQ EDIT MENTION + token'])
    expect(postRequest).not.toHaveBeenCalled()
  })
})
