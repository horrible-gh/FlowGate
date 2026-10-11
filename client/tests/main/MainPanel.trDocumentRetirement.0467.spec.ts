import { DOMWrapper, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'
import DocWorkflow from '@main/components/DocWorkflow.vue'
import ReviewActionBar from '@main/components/ReviewActionBar.vue'
import TestFailStrip from '@main/components/TestFailStrip.vue'
import TestRunStrip from '@main/components/TestRunStrip.vue'
import GenericDocumentBody from '@main/components/documents/GenericDocumentBody.vue'
import type { Tab } from '@main/stores/tabs'
import type { TrScopeVerdict } from '@main/types/trScope'
import { expectDocumentBranchMounted, mountMainPanel } from '../helpers/mountMainPanel'

type ContentSave = { readonly doc_id: string; readonly content: string }
type Panel = Awaited<ReturnType<typeof mountMainPanel>>

const { getRequest, patchRequest, apiGet } = vi.hoisted(() => ({
  getRequest: vi.fn<(url: string) => Promise<{ readonly data: unknown }>>(),
  patchRequest: vi.fn<(url: string, payload: ContentSave) => Promise<{ readonly data: unknown }>>(),
  apiGet: vi.fn<(url: string) => Promise<{ readonly data: unknown }>>(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest,
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  deleteRequest: vi.fn(),
  postFormRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const TR_TAB = {
  id: 'flowgate.default.0467.9101-TR',
  title: 'report',
  path: 'documents/flowgate/main/default/0467/9101-TR_document.md',
  type: 'md',
  typeCode: 'TR',
  projectId: 'flowgate',
} satisfies Tab
const CONTENT_URL = `/api/v1/documents/content?doc_id=${encodeURIComponent(TR_TAB.id)}`
const DETAIL_URL = `/api/v1/documents/detail?doc_id=${encodeURIComponent(TR_TAB.id)}`
const LEGACY_BODY = '# TR\n\n## \ub2e8\uacc4\ubcc4 \ud655\uc778\n\nLegacy body\n'
const TR_SCOPE = {
  verdict: 'pass',
  evaluated: true,
  reported: { count: 1, items: ['client/src/main/components/MainPanel.vue'] },
} satisfies TrScopeVerdict
const DOCUMENT = {
  doc_id: TR_TAB.id,
  title: TR_TAB.title,
  type_code: 'TR',
  status: 'draft',
  doc_review_status: 'pending',
  project_id: TR_TAB.projectId,
  group_id: 'flowgate.default.0467',
  module: 'default',
  is_editable: true,
  download_available: true,
  group_test_run: { active: false },
  tr_scope: TR_SCOPE,
} as const
const panels: Panel[] = []
let content = LEGACY_BODY

async function mountTrPanel(): Promise<Panel> {
  const wrapper = await mountMainPanel({
    tabs: [{ ...TR_TAB }],
    activeTabId: TR_TAB.id,
    deep: true,
    attachTo: document.body,
    stubs: { DocHeader: false, MdViewer: false },
  })
  panels.push(wrapper)
  expectDocumentBranchMounted(wrapper)
  expect(wrapper.findComponent(GenericDocumentBody).exists()).toBe(true)
  expect(wrapper.get('.md-viewer__content').text()).toContain('Legacy body')
  return wrapper
}

function expectRetired(wrapper: Panel): void {
  expect(wrapper.find('.step-verify-card').exists()).toBe(false)
  expect(wrapper.findComponent({ name: 'StepVerificationCard' }).exists()).toBe(false)
  for (const get of [getRequest, apiGet]) {
    expect(get.mock.calls.filter(([url]) => /\/step-verification(?:[/?]|$)/.test(url))).toHaveLength(0)
  }
}

async function openEditor(wrapper: Panel): Promise<DOMWrapper<HTMLElement>> {
  await wrapper.get('.edit-dropdown-wrap > button').trigger('click')
  await wrapper.get('.edit-dropdown-item').trigger('click')
  await flushPromises()
  return new DOMWrapper(document.body)
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  content = LEGACY_BODY
  getRequest.mockReset().mockImplementation(async (url) => {
    if (url === CONTENT_URL) return { data: { content } }
    if (url === DETAIL_URL) return { data: { ...DOCUMENT } }
    return { data: { runs: [], questions: [], providers: [] } }
  })
  apiGet.mockReset().mockResolvedValue({ data: {} })
  patchRequest.mockReset().mockImplementation(async (_url, payload) => {
    content = payload.content
    return { data: {} }
  })
})

afterEach(() => {
  for (const wrapper of panels.splice(0)) wrapper.unmount()
  document.body.replaceChildren()
  localStorage.clear()
})

describe('TR document UI after obsolete card retirement', () => {
  it('keeps the mounted document shell and actions without a dedicated card or GET', async () => {
    const wrapper = await mountTrPanel()

    expect(wrapper.get('.doc-header .doc-title').text()).toBe(TR_TAB.title)
    expect(wrapper.findComponent(DocWorkflow).exists()).toBe(true)
    expect(wrapper.find('.attach-card').exists()).toBe(true)
    expect(wrapper.findComponent(DocInfoPanel).props('trScope')).toEqual(TR_SCOPE)
    expect(wrapper.find('[data-testid="tr-self-check-panel"]').exists()).toBe(true)
    expect(wrapper.findComponent(TestFailStrip).exists()).toBe(true)
    expect(wrapper.findComponent(TestRunStrip).exists()).toBe(true)
    expect(wrapper.findComponent(ReviewActionBar).exists()).toBe(true)
    expect(wrapper.find('.doc-markdown-download').exists()).toBe(true)
    expect(wrapper.find('.doc-markdown-upload').exists()).toBe(true)
    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(true)
    expect(wrapper.get('.md-viewer__content h2').text()).toBe('\ub2e8\uacc4\ubcc4 \ud655\uc778')
    expectRetired(wrapper)
  })

  it('saves legacy Markdown and refreshes the preview and header when an edit succeeds', async () => {
    const wrapper = await mountTrPanel()
    const editor = await openEditor(wrapper)
    const textarea = editor.get<HTMLTextAreaElement>('.document-editor__textarea textarea')
    expect(textarea.element.value).toBe(LEGACY_BODY)
    const edited = LEGACY_BODY.replace('Legacy body', 'Edited body')
    await textarea.setValue(edited)
    getRequest.mockClear()

    await editor.get('[data-dialog-action-id="save"]').trigger('click')
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledWith('/api/v1/documents/content', { doc_id: TR_TAB.id, content: edited })
    expect(wrapper.get('.md-viewer__content').text()).toContain('Edited body')
    expect(wrapper.findComponent(DocHeader).exists()).toBe(true)
    expect(getRequest).toHaveBeenCalledWith(CONTENT_URL)
    expect(getRequest).toHaveBeenCalledWith(DETAIL_URL)
    expect(editor.find('.document-edit-dialog').exists()).toBe(false)
    expectRetired(wrapper)
  })

  it('keeps the edited text retryable and the existing preview when a save fails', async () => {
    const wrapper = await mountTrPanel()
    const editor = await openEditor(wrapper)
    const edited = LEGACY_BODY.replace('Legacy body', 'Unsaved body')
    await editor.get<HTMLTextAreaElement>('.document-editor__textarea textarea').setValue(edited)
    patchRequest.mockRejectedValueOnce({ response: { data: { detail: 'Conflict' } } })
    getRequest.mockClear()

    await editor.get('[data-dialog-action-id="save"]').trigger('click')
    await flushPromises()

    expect(editor.get<HTMLTextAreaElement>('.document-editor__textarea textarea').element.value).toBe(edited)
    expect(editor.get<HTMLButtonElement>('[data-dialog-action-id="save"]').element.disabled).toBe(false)
    expect(editor.get('.document-editor__save-error').text()).toBe('Conflict')
    expect(wrapper.get('.md-viewer__content').text()).toContain('Legacy body')
    expect(getRequest).not.toHaveBeenCalledWith(CONTENT_URL)
    expectRetired(wrapper)
  })

  it('imports Markdown through the normal save path and refreshes the mounted preview', async () => {
    const wrapper = await mountTrPanel()
    const imported = LEGACY_BODY.replace('Legacy body', 'Imported body')
    const input = wrapper.get<HTMLInputElement>('.doc-markdown-upload-input')
    Object.defineProperty(input.element, 'files', {
      value: [new File([imported], 'report.md', { type: 'text/markdown' })],
      configurable: true,
    })
    getRequest.mockClear()

    await input.trigger('change')
    await vi.waitFor(() => expect(wrapper.get('.md-viewer__content').text()).toContain('Imported body'))

    expect(patchRequest).toHaveBeenCalledWith('/api/v1/documents/content', { doc_id: TR_TAB.id, content: imported })
    expect(getRequest).toHaveBeenCalledWith(CONTENT_URL)
    expect(wrapper.get('.doc-header .doc-title').text()).toBe(TR_TAB.title)
    expect(input.element.value).toBe('')
    expectRetired(wrapper)
  })
})
