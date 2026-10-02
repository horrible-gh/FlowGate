import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import NextEmptyDocModal from '@main/components/NextEmptyDocModal.vue'
import { describeNextEmptyError } from '@main/components/nextEmptyErrors'

// flowgate.default.0660 T0004 §2 (RC2): the create dialog names WHY a next-empty request was
// refused — a worktree that will be ready in a moment (retry) is not the same message as a
// project with no source directory (place the source / enable Git), and a refused workflow
// slot is a conflict, not "an error occurred".

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
  patchRequest: vi.fn(),
}))

const t = i18n.global.t
const GENERIC = 'main.next_empty_doc_modal.error_create_failed'

function refusal(status: number, data: unknown) {
  return { isAxiosError: true, response: { status, data } }
}

const RETRYABLE = refusal(503, {
  code: 'tr2_git_unavailable', message: 'Git worktree is unavailable', retryable: true,
  attempt_id: null, details: { loc: 'source_root', reason: 'git_busy' },
})
const SOURCE_MISSING = refusal(409, {
  code: 'tr2_source_root_missing', message: 'Project source directory is missing', retryable: false,
  attempt_id: null, details: { loc: 'source_root', reason: 'project_source_missing' },
})
const SLOT_CONFLICT = refusal(409, {
  error: { code: 'workflow_slot_occupied', message: 'Workflow slot item_id=4 is already held', item_id: 4 },
})

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: { ok: true } })
})

describe('describeNextEmptyError', () => {
  it('tells a retryable worktree wait apart from a missing project source', () => {
    const wait = describeNextEmptyError(RETRYABLE, t)
    const missing = describeNextEmptyError(SOURCE_MISSING, t)
    expect(wait.retryable).toBe(true)
    expect(missing.retryable).toBe(false)
    expect(wait.text).toBe(t('main.next_empty_doc_modal.error_tr2_worktree_retry', {
      reason: t('main.next_empty_doc_modal.reason.git_busy'),
    }))
    expect(missing.text).toBe(t('main.next_empty_doc_modal.error_tr2_source_missing'))
    expect(wait.text).not.toBe(missing.text)
    for (const text of [wait.text, missing.text]) expect(text).not.toBe(t(GENERIC))
  })

  it('reports a refused workflow slot as a conflict', () => {
    const info = describeNextEmptyError(SLOT_CONFLICT, t)
    expect(info.code).toBe('workflow_slot_occupied')
    expect(info.text).toBe(t('main.next_empty_doc_modal.error_slot_conflict'))
  })

  it('says a pending Time Machine commit cancel blocks the group (RC3)', () => {
    const info = describeNextEmptyError(refusal(409, {
      ok: false,
      error: {
        code: 'workflow_revert_pending',
        message: 'a Time Machine commit cancel is still pending for this group; retry the cancel first',
        details: { doc_ids: ['p.default.0001.0003-TR2'] },
      },
    }), t)
    expect(info.code).toBe('workflow_revert_pending')
    expect(info.retryable).toBe(false)
    expect(info.text).toBe(t('main.git_errors.workflow_revert_pending'))
    expect(info.text).not.toContain('still pending for this group')
  })

  it('never shows server message text, and keeps plain detail strings', () => {
    const unknown = describeNextEmptyError(refusal(500, {
      code: 'tr2_internal_error', message: 'C:\\secret\\path exploded', retryable: false, details: {},
    }), t)
    expect(unknown.text).toBe(t('main.next_empty_doc_modal.error_create_failed_code', { code: 'tr2_internal_error' }))
    expect(unknown.text).not.toContain('secret')
    expect(describeNextEmptyError(refusal(409, { detail: 'Current next step is TR2. Requested type: T' }), t).text)
      .toBe('Current next step is TR2. Requested type: T')
    expect(describeNextEmptyError(refusal(500, {}), t).text).toBe(t(GENERIC))
  })
})

function mountModal(docType = 'TR2') {
  return mount(NextEmptyDocModal, {
    props: {
      visible: false, projectId: 'p', groupId: 'p.default.0001',
      prevDocId: 'p.default.0001.0002-T2', docType,
    },
    global: { plugins: [i18n], stubs: { teleport: true } },
    attachTo: document.body,
  })
}

describe('NextEmptyDocModal (RC2)', () => {
  it('shows the specific refusal instead of the generic line', async () => {
    postRequest.mockRejectedValue(SOURCE_MISSING)
    const wrapper = mountModal()
    await wrapper.setProps({ visible: true })
    await flushPromises()
    await wrapper.find('input.form-ctrl').setValue('반영안')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const flash = document.body.querySelector('[data-testid="next-empty-flash"]')
    expect(flash?.textContent).toContain(t('main.next_empty_doc_modal.error_tr2_source_missing'))
    expect(flash?.textContent).not.toContain(t(GENERIC))
    wrapper.unmount()
  })

  it('warns up front when the TR2 preflight says no retry can help', async () => {
    getRequest.mockResolvedValue({ data: { ok: false, error: SOURCE_MISSING.response.data } })
    const wrapper = mountModal()
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(expect.stringContaining('/api/v1/documents/next-empty/preflight?prev_doc_id='))
    const warning = document.body.querySelector('[data-testid="next-empty-preflight"]')
    expect(warning?.textContent).toContain(t('main.next_empty_doc_modal.error_tr2_source_missing'))
    wrapper.unmount()
  })

  it('does not preflight other document types', async () => {
    const wrapper = mountModal('N')
    await wrapper.setProps({ visible: true })
    await flushPromises()
    expect(getRequest).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
