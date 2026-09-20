// flowgate.default.0587 T0004 (NR0003) — generic-document Markdown upload.
//
// Mirrors the completion criteria for GenericDocumentBody's new upload pairing: a selected
// .md file is read, a leading UTF-8 BOM is stripped, the existing document-content PATCH
// (the same path saveEditContent already uses) persists it, and only a successful save
// refreshes the viewer / TR step-verification card. A cancelled pick, a read failure or a
// PATCH failure (403/409/422/500) must never touch the screen already showing.
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { expectDocumentBranchMounted, mountMainPanel } from '../helpers/mountMainPanel'

const { patchRequest, showToast } = vi.hoisted(() => ({
  patchRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: { questions: [] } }),
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
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

const NR_TAB = {
  id: 'test.test.0587.0003-NR',
  title: 'investigation',
  path: '',
  type: 'md' as const,
  typeCode: 'NR',
}
const TR_TAB = {
  id: 'test.test.0587.0004-TR',
  title: 'report',
  path: '',
  type: 'md' as const,
  typeCode: 'TR',
}
// An editable Markdown *source file* tab (opened from FileExplorer): projectId set, no
// typeCode — isFileTab(tab) is true. canEditTab() routes this through canDirectEditSource(),
// which is true here, so canEdit alone cannot be used to gate the document-upload button.
const MD_FILE_TAB = {
  id: 'file:test:README.md',
  title: 'README.md',
  path: 'README.md',
  type: 'md' as const,
  projectId: 'test',
}

function fakeDocHeader(canEditDocument = true) {
  return defineComponent({
    name: 'DocHeader',
    props: { tab: { type: Object, default: null } },
    setup(_props, { expose }) {
      expose({
        docTypeCode: 'NR',
        docProjectId: 'test',
        docModule: 'test',
        canEditDocument,
        docLoaded: true,
        testRun: null,
        downloadAvailable: true,
        markdownDownloadBusy: false,
        downloadMarkdown: vi.fn(),
      })
      return () => h('div', { class: 'doc-header-fake' })
    },
  })
}

function fakeMdViewer(loadContent = vi.fn()) {
  return defineComponent({
    name: 'MdViewer',
    props: { path: { default: null }, docId: { default: null }, projectId: { default: null }, gitGroupId: { default: null }, gitCommit: { default: null }, readOnly: { default: null } },
    setup(_props, { expose }) {
      expose({ loadContent })
      return () => h('div', { class: 'md-viewer-fake' })
    },
  })
}

function fakeStepVerificationCard(fetchData = vi.fn()) {
  return defineComponent({
    name: 'StepVerificationCard',
    props: { docId: { type: String, default: null } },
    setup(_props, { expose }) {
      expose({ fetchData })
      return () => h('div', { class: 'step-verification-fake' })
    },
  })
}

async function flushAll(times = 6) {
  const { flushPromises } = await import('@vue/test-utils')
  for (let i = 0; i < times; i++) await flushPromises()
}

function selectFile(wrapper: any, file: File) {
  const input = wrapper.find('.doc-markdown-upload-input')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  return input
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  patchRequest.mockReset()
  showToast.mockReset()
})

describe('0587: GenericDocumentBody Markdown upload', () => {
  it('reads the selected .md file and PATCHes it through the existing content-save path', async () => {
    patchRequest.mockResolvedValue({ data: {} })
    const loadContent = vi.fn()
    const wrapper = await mountMainPanel({
      tabs: [NR_TAB],
      activeTabId: NR_TAB.id,
      stubs: { DocHeader: fakeDocHeader(true), MdViewer: fakeMdViewer(loadContent) },
    })
    expectDocumentBranchMounted(wrapper)

    const file = new File(['---\ntitle: x\n---\n\nbody'], 'doc.md', { type: 'text/markdown' })
    const input = selectFile(wrapper, file)
    await input.trigger('change')
    await flushAll()

    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/documents/content', {
      doc_id: NR_TAB.id,
      content: '---\ntitle: x\n---\n\nbody',
    })
    expect(loadContent).toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith('문서를 업로드했습니다.', 'success')
    // Re-selecting the same file must work again — the input value is reset after read.
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('strips a leading UTF-8 BOM before sending the PATCH', async () => {
    patchRequest.mockResolvedValue({ data: {} })
    const wrapper = await mountMainPanel({
      tabs: [NR_TAB],
      activeTabId: NR_TAB.id,
      stubs: { DocHeader: fakeDocHeader(true) },
    })
    expectDocumentBranchMounted(wrapper)

    const file = new File(['﻿---\ntitle: x\n---\n\nbody'], 'doc.md', { type: 'text/markdown' })
    const input = selectFile(wrapper, file)
    await input.trigger('change')
    await flushAll()

    expect(patchRequest).toHaveBeenCalledWith('/api/v1/documents/content', {
      doc_id: NR_TAB.id,
      content: '---\ntitle: x\n---\n\nbody',
    })
  })

  it('does not request anything when the file picker is cancelled (no file chosen)', async () => {
    const wrapper = await mountMainPanel({
      tabs: [NR_TAB],
      activeTabId: NR_TAB.id,
      stubs: { DocHeader: fakeDocHeader(true) },
    })
    expectDocumentBranchMounted(wrapper)

    const input = wrapper.find('.doc-markdown-upload-input')
    Object.defineProperty(input.element, 'files', { value: [], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(patchRequest).not.toHaveBeenCalled()
    expect(showToast).not.toHaveBeenCalled()
  })

  it('shows a read-error toast and never PATCHes when FileReader fails to read the file', async () => {
    const originalReadAsText = FileReader.prototype.readAsText
    FileReader.prototype.readAsText = function (this: FileReader) {
      Object.defineProperty(this, 'error', { value: new DOMException('boom', 'NotReadableError'), configurable: true })
      ;(this as any).onerror?.(new ProgressEvent('error'))
    }
    try {
      const loadContent = vi.fn()
      const wrapper = await mountMainPanel({
        tabs: [NR_TAB],
        activeTabId: NR_TAB.id,
        stubs: { DocHeader: fakeDocHeader(true), MdViewer: fakeMdViewer(loadContent) },
      })
      expectDocumentBranchMounted(wrapper)

      const file = new File(['---\ntitle: x\n---\n\nbody'], 'doc.md', { type: 'text/markdown' })
      const input = selectFile(wrapper, file)
      await input.trigger('change')
      await flushAll()

      expect(patchRequest).not.toHaveBeenCalled()
      expect(loadContent).not.toHaveBeenCalled()
      expect(showToast).toHaveBeenCalledWith('파일을 읽지 못했습니다.', 'danger')
      expect(showToast).not.toHaveBeenCalledWith('문서를 업로드했습니다.', 'success')
      // The busy state must not get stuck on a failed read — the button is usable again.
      expect(wrapper.find('.doc-markdown-upload').attributes('disabled')).toBeUndefined()
    } finally {
      FileReader.prototype.readAsText = originalReadAsText
    }
  })

  it.each([
    { status: 403, detail: '권한이 없습니다.' },
    { status: 409, detail: '문서가 잠겨 있습니다.' },
    { status: 422, detail: '검증 실패' },
    { status: 500, detail: undefined },
  ])('leaves the current document untouched and shows an error toast when the save PATCH fails ($status)', async ({ status, detail }) => {
    patchRequest.mockRejectedValueOnce(
      detail === undefined
        ? { response: { status } }
        : { response: { status, data: { detail } } },
    )
    const loadContent = vi.fn()
    const wrapper = await mountMainPanel({
      tabs: [NR_TAB],
      activeTabId: NR_TAB.id,
      stubs: { DocHeader: fakeDocHeader(true), MdViewer: fakeMdViewer(loadContent) },
    })
    expectDocumentBranchMounted(wrapper)

    const file = new File(['---\ntitle: x\n---\n\nbody'], 'doc.md', { type: 'text/markdown' })
    const input = selectFile(wrapper, file)
    await input.trigger('change')
    await flushAll()

    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(loadContent).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith(detail ?? '문서 저장에 실패했습니다.', 'danger')
    expect(showToast).not.toHaveBeenCalledWith('문서를 업로드했습니다.', 'success')
    // The busy state must not get stuck on a failed save — the button is usable again.
    expect(wrapper.find('.doc-markdown-upload').attributes('disabled')).toBeUndefined()
  })

  it('refreshes the TR StepVerificationCard after a successful upload', async () => {
    patchRequest.mockResolvedValue({ data: {} })
    const fetchData = vi.fn()
    const wrapper = await mountMainPanel({
      tabs: [TR_TAB],
      activeTabId: TR_TAB.id,
      stubs: { DocHeader: fakeDocHeader(true), StepVerificationCard: fakeStepVerificationCard(fetchData) },
    })
    expectDocumentBranchMounted(wrapper)

    const file = new File(['---\ntitle: report\n---\n\nbody'], 'doc.md', { type: 'text/markdown' })
    const input = selectFile(wrapper, file)
    await input.trigger('change')
    await flushAll()

    expect(patchRequest).toHaveBeenCalledTimes(1)
    expect(fetchData).toHaveBeenCalled()
  })

  it('does not render the upload control when the document is not editable', async () => {
    const wrapper = await mountMainPanel({
      tabs: [NR_TAB],
      activeTabId: NR_TAB.id,
      stubs: { DocHeader: fakeDocHeader(false) },
    })
    expectDocumentBranchMounted(wrapper)

    expect(wrapper.find('.doc-markdown-upload').exists()).toBe(false)
    // Download is a read capability and stays independent of editability.
    expect(wrapper.find('.doc-markdown-download').exists()).toBe(true)
  })

  it('hides only the upload control while the group AI run locks the document read-only', async () => {
    const wrapper = await mountMainPanel({
      tabs: [NR_TAB],
      activeTabId: NR_TAB.id,
      stubs: { DocHeader: fakeDocHeader(true) },
    })
    expectDocumentBranchMounted(wrapper)
    // canEdit stays true throughout — this is the readOnly lock, not the canEdit=false case.
    expect(wrapper.find('.doc-markdown-upload').exists()).toBe(true)
    expect(wrapper.find('.doc-markdown-download').exists()).toBe(true)

    useAiInvokeRunsStore().trackStarted({
      run_id: 'run-1',
      group_id: 'test.test.0587',
      doc_ref: 'test.test.0587.9999-X',
      status: 'running',
    })
    await flushAll()

    expect(wrapper.find('.doc-markdown-upload').exists()).toBe(false)
    // T0004 §3/§17-7 — read-only must not hide the download control.
    expect(wrapper.find('.doc-markdown-download').exists()).toBe(true)
  })

  it('never shows the document-upload control on an editable Markdown source file tab', async () => {
    // MD_FILE_TAB is a FileExplorer file tab (isFileTab === true), not a document tab.
    // canEditTab() still resolves true for it via canDirectEditSource(), so the upload
    // button must be excluded by tab kind, not by canEdit/readOnly alone — otherwise
    // clicking it PATCHes /api/v1/documents/content with the file tab's id as doc_id,
    // which is not a real document and can never save.
    const wrapper = await mountMainPanel({
      tabs: [MD_FILE_TAB],
      activeTabId: MD_FILE_TAB.id,
    })
    expectDocumentBranchMounted(wrapper)

    expect(wrapper.find('.doc-markdown-upload').exists()).toBe(false)
    expect(wrapper.find('.doc-markdown-upload-input').exists()).toBe(false)
    expect(patchRequest).not.toHaveBeenCalled()
  })
})
