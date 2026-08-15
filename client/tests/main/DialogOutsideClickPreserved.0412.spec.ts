import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

// flowgate.default.0412 T0004 / NR0003: dialog overlays must keep intercepting clicks
// (so background UI stays unreachable) but must no longer close/cancel the dialog or
// reset its state on a self-click. This spec drives an actual click event at the
// overlay element itself for a state-holding dialog (ContinuousWorkDialog,
// GitConflictResolverDialog, NextActionModal) and two view-only dialogs
// (QaHistoryDialog, QaReviewHistoryDialog — 0311 T0004 merged QaHistoryDialog +
// ReviewHistoryDialog into one component; TR0005 rev6 반려 §3 split QaHistoryDialog
// back out as the 질의-only full view), and confirms the existing explicit close
// paths (X button, Escape where it was already supported) still work unchanged.

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return actual
})

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
  putRequest,
}))

import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import NextActionModal from '@main/components/NextActionModal.vue'
import QaHistoryDialog from '@main/components/QaHistoryDialog.vue'
import QaReviewHistoryDialog from '@main/components/QaReviewHistoryDialog.vue'
import { parseConflictFile, type ConflictFileState } from '@main/composables/useConflictChunks'

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
})

describe('ContinuousWorkDialog — overlay click no longer closes it (0412)', () => {
  function seqResponse() {
    return {
      data: {
        doc_id: 'flowgate.default.0412.0001-B',
        doc_class: 'B',
        decided: true,
        items: [
          { id: 1, item_seq: 1, type: 'N', label: '조사지시', status: 'done' },
          { id: 2, item_seq: 2, type: 'NR', label: '조사레포트', status: 'done' },
          { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
          { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'pending' },
        ],
        head: { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
      },
    }
  }

  function mountDialog() {
    return mount(ContinuousWorkDialog, {
      props: { visible: true, docRef: 'flowgate.default.0412.0001-B' },
      global: { plugins: [i18n] },
    })
  }

  it('preserves the entered note and stays open when the overlay itself is clicked', async () => {
    getRequest.mockResolvedValue(seqResponse())
    mountDialog()
    await flushPromises()

    const messageTab = Array.from(document.querySelectorAll('.cwd-tab'))
      .find((el) => el.textContent?.includes(i18n.global.t('main.continuous_work.tab_message'))) as HTMLElement
    messageTab.click()
    await flushPromises()

    const input = document.querySelector<HTMLInputElement>('.cwd-message-default-input')!
    input.value = '유지되어야 하는 멘트'
    input.dispatchEvent(new Event('input'))
    await flushPromises()

    // Click the overlay itself (not a child) — this is exactly the event @click.self
    // used to react to.
    const overlay = document.querySelector<HTMLElement>('.modal-bg')!
    overlay.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(document.querySelector('.modal-bg')).toBeTruthy()
    expect(document.querySelector<HTMLInputElement>('.cwd-message-default-input')!.value).toBe('유지되어야 하는 멘트')

    document.body.innerHTML = ''
  })

  it('still closes via the header X button', async () => {
    getRequest.mockResolvedValue(seqResponse())
    const wrapper = mountDialog()
    await flushPromises()

    ;(document.querySelector('.modal-close') as HTMLButtonElement).click()
    await flushPromises()

    expect(wrapper.emitted('update:visible')).toBeTruthy()
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])

    document.body.innerHTML = ''
  })
})

describe('GitConflictResolverDialog — overlay click no longer emits close (0412)', () => {
  const FILE_CONTENT = [
    ...Array.from({ length: 4 }, (_, i) => `common ${i + 1}`),
    '<<<<<<< HEAD',
    'keep me',
    '=======',
    '>>>>>>> main',
    'alpha',
    '<<<<<<< HEAD',
    'left',
    '=======',
    'right',
    '>>>>>>> main',
    'tail',
  ].join('\n')

  function makeFile(path: string, content: string, conflictCount: number): ConflictFileState {
    const segments = parseConflictFile(content)
    if (!segments) throw new Error('fixture must parse: ' + path)
    return { path, conflict_count: conflictCount, directText: content, mode: 'chunk', segments, notice: '' }
  }

  function mountDialog() {
    const files = [makeFile('server/app/git_service.py', FILE_CONTENT, 2)]
    return mount(GitConflictResolverDialog, {
      props: {
        files,
        branch: 'flowgate_default_0412',
        baseBranch: 'main',
        busy: false,
        loadStatus: 'ready',
        errorMessage: '',
      },
      global: { plugins: [i18n], stubs: { AppIcon: true } },
      attachTo: document.body,
    })
  }

  it('keeps the current chunk selection and does not emit close on an overlay click', async () => {
    ;(Element.prototype as any).scrollTo = vi.fn()
    const wrapper = mountDialog()
    await flushPromises()

    const activeBefore = wrapper.find('.git-conflict-chip.active').text()

    await wrapper.find('.git-conflict-overlay').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('close')).toBeFalsy()
    expect(wrapper.find('.git-conflict-chip.active').text()).toBe(activeBefore)

    wrapper.unmount()
  })

  it('still emits close from the dialog close button', async () => {
    ;(Element.prototype as any).scrollTo = vi.fn()
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.git-dialog-close').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()

    wrapper.unmount()
  })
})

describe('NextActionModal — overlay click no longer closes it (0412)', () => {
  function installApis() {
    getRequest.mockImplementation(async (path: string, params: any = {}) => {
      if (path === '/api/v1/modules') {
        return { data: { items: [{ module_id: 'default', title: 'default' }] } }
      }
      if (/\/groups$/.test(path)) {
        return {
          data: {
            ok: true,
            total: 1,
            offset: 0,
            limit: 100,
            items: [{ group_id: 'flowgate.default.0412', title: 'group 0412' }],
          },
        }
      }
      if (/\/documents$/.test(path)) return { data: { items: [] } }
      if (/\/predecessors$/.test(path)) return { data: { predecessor_doc_ids: [] } }
      return { data: {} }
    })
  }

  async function mountModal() {
    const wrapper = mount(NextActionModal, {
      props: {
        visible: false,
        nextStepLabel: 'TR',
        nextTypeCode: 'TR',
        projectId: 'flowgate',
        docModule: 'default',
        groupId: 'flowgate.default.0412',
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await wrapper.setProps({ visible: true })
    await flushPromises()
    await flushPromises()
    return wrapper
  }

  it('preserves the search input and stays open when the overlay itself is clicked', async () => {
    installApis()
    const wrapper = await mountModal()

    const search = wrapper.find<HTMLInputElement>('.nad-doc-search')
    await search.setValue('유지되어야 하는 검색어')

    await wrapper.find('.modal-bg').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('update:visible')).toBeFalsy()
    expect(wrapper.find<HTMLInputElement>('.nad-doc-search').element.value).toBe('유지되어야 하는 검색어')

    wrapper.unmount()
  })

  it('still closes via the header X button', async () => {
    installApis()
    const wrapper = await mountModal()

    await wrapper.find('.modal-close').trigger('click')

    expect(wrapper.emitted('update:visible')).toBeTruthy()
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])

    wrapper.unmount()
  })
})

// 0311 T0004 merged QaHistoryDialog + ReviewHistoryDialog into QaReviewHistoryDialog.
// TR0005 rev6 반려 §3 ("질의는 빼라") split QaHistoryDialog back out as its own
// 질의-only full view; QaReviewHistoryDialog stays as the 검수·반려-only dialog.
describe('QaHistoryDialog — overlay click no longer closes it (0412)', () => {
  it('overlay click does not close it, but X and Escape still do', async () => {
    const wrapper = mount(QaHistoryDialog, {
      props: {
        visible: true,
        items: [{ id: 1, seq: 1, title: 'q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] }],
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()

    await wrapper.find('.modal-bg').trigger('click')
    expect(wrapper.emitted('update:visible')).toBeFalsy()

    await wrapper.find('.modal-bg').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:visible')).toBeTruthy()
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])

    wrapper.unmount()

    const wrapper2 = mount(QaHistoryDialog, {
      props: {
        visible: true,
        items: [{ id: 1, seq: 1, title: 'q', body: 'b', asker_kind: 'human', answer_count: 0, answers: [] }],
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()
    await wrapper2.find('.modal-close').trigger('click')
    expect(wrapper2.emitted('update:visible')).toBeTruthy()
    expect(wrapper2.emitted('update:visible')![0]).toEqual([false])
    wrapper2.unmount()
  })
})

describe('QaReviewHistoryDialog — overlay click no longer closes it (0412, merged 0311 T0004)', () => {
  it('overlay click does not close it, but X and Escape still do', async () => {
    const wrapper = mount(QaReviewHistoryDialog, {
      props: { visible: true, reviews: [], rejections: [] },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()

    await wrapper.find('.modal-bg').trigger('click')
    expect(wrapper.emitted('update:visible')).toBeFalsy()

    await wrapper.find('.modal-bg').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:visible')).toBeTruthy()
    expect(wrapper.emitted('update:visible')![0]).toEqual([false])

    wrapper.unmount()

    const wrapper2 = mount(QaReviewHistoryDialog, {
      props: { visible: true, reviews: [], rejections: [] },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()
    await wrapper2.find('.modal-close').trigger('click')
    expect(wrapper2.emitted('update:visible')).toBeTruthy()
    expect(wrapper2.emitted('update:visible')![0]).toEqual([false])
    wrapper2.unmount()
  })
})
