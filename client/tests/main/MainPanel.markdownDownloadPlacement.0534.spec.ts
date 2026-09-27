// flowgate.default.0534 — The Markdown download button in the "문서 내용 미리보기" card.
// TR0006/0010 built the download action but wired it into DocHeader's TITLE row.
// TR0012 rev1 (rej_01M1WH0B2S5DWNA8) moved it to the left of the document-detail [수정]
// button. TR0012 rev3 (rej_01M1WKS3QGRY4RH9) moved it to the far right of the card-actions.
//
// flowgate.default.0587 T0004/NR0003 — the far-right placement above is superseded: the
// generic-document card now mirrors the work-plan card's [다운로드][업로드][...] pairing at
// the front of card-actions, so download and (when the doc is editable) upload lead
// card-actions in that order, followed by [수정]/[전체보기].
//
// DocHeader keeps owning the fetch (doc.value.download_available) and the download
// logic, exposing downloadAvailable / markdownDownloadBusy / downloadMarkdown so
// MainPanel can drive a button without a second detail fetch. Upload is not a DocHeader
// concern (NR0003 §19) — MainPanel owns the read/PATCH/refresh orchestration itself.

import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { expectDocumentBranchMounted, mountMainPanel } from '../helpers/mountMainPanel'

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: { questions: [] } }),
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
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

const TAB = {
  id: 'test.test.0534.0006-TR',
  title: 'report',
  path: '',
  type: 'md' as const,
  typeCode: 'TR',
}

/** Honest DocHeader stand-in: exposes exactly what the real component exposes for this binding. */
function fakeDocHeader(downloadAvailable: boolean, downloadMarkdown = vi.fn(), canEditDocument = true) {
  return defineComponent({
    name: 'DocHeader',
    props: { tab: { type: Object, default: null } },
    setup(_props, { expose }) {
      expose({
        docTypeCode: 'TR',
        docProjectId: 'test',
        docModule: 'test',
        canEditDocument,
        docLoaded: true,
        testRun: null,
        downloadAvailable,
        markdownDownloadBusy: false,
        downloadMarkdown,
      })
      return () => h('div', { class: 'doc-header-fake' })
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('0534/0587: document-preview download/upload button placement', () => {
  it('renders download then upload as the first two children of card-actions', async () => {
    const wrapper = await mountMainPanel({
      tabs: [TAB],
      activeTabId: TAB.id,
      stubs: { DocHeader: fakeDocHeader(true) },
    })
    expectDocumentBranchMounted(wrapper)

    const actions = wrapper.find('.card-actions')
    expect(actions.exists()).toBe(true)
    const children = actions.element.children
    expect(children[0].classList.contains('doc-markdown-download')).toBe(true)
    expect(children[1].classList.contains('doc-markdown-upload')).toBe(true)
  })

  it('does not render the download button when the document has no download-available capability', async () => {
    const wrapper = await mountMainPanel({
      tabs: [TAB],
      activeTabId: TAB.id,
      stubs: { DocHeader: fakeDocHeader(false) },
    })
    expectDocumentBranchMounted(wrapper)

    expect(wrapper.find('.doc-markdown-download').exists()).toBe(false)
    // The [수정] control is unaffected by download availability.
    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(true)
  })

  it('hides the upload button (but keeps download) when the document is not editable', async () => {
    const wrapper = await mountMainPanel({
      tabs: [TAB],
      activeTabId: TAB.id,
      stubs: { DocHeader: fakeDocHeader(true, vi.fn(), false) },
    })
    expectDocumentBranchMounted(wrapper)

    expect(wrapper.find('.doc-markdown-download').exists()).toBe(true)
    expect(wrapper.find('.doc-markdown-upload').exists()).toBe(false)
  })

  it('delegates the download click to the DocHeader instance that owns the fetch/download logic', async () => {
    const downloadMarkdown = vi.fn()
    const wrapper = await mountMainPanel({
      tabs: [TAB],
      activeTabId: TAB.id,
      stubs: { DocHeader: fakeDocHeader(true, downloadMarkdown) },
    })
    expectDocumentBranchMounted(wrapper)

    await wrapper.find('.doc-markdown-download').trigger('click')
    expect(downloadMarkdown).toHaveBeenCalledTimes(1)
  })
})
