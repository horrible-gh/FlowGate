/**
 * flowgate.v02.0053 T#3 — the v0.1 MD Viewer moved into v0.2 (features/documentWindow/MdViewer.vue): GFM
 * rendering with the frontmatter and the leading next-document header stripped, the copy buttons (body /
 * code block — flowgate.v02.0056 F1 took the "with header" copy away), a document whose file is not written
 * yet shown as an empty document (no
 * "no file" text), a failed read with a retry, and the reload on `fg:document_content_changed`.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import MdViewer from '@/features/documentWindow/MdViewer.vue'

const contentRead = vi.fn()
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return { ...actual, documentContentRead: (...args: unknown[]) => contentRead(...args) }
})

const copy = vi.fn()
vi.mock('@/core/interaction/clipboard', () => ({ copyToClipboard: (...args: unknown[]) => copy(...args) }))

registerAppMessages(i18n)

const DOC = 'flowgate.v02.0053.0007-TR'
const BODY = [
  '---',
  'title: 작업레포트',
  '---',
  '',
  'next_type: TR',
  'next_type_detail: 작업레포트',
  'project: flowgate',
  'module: v02',
  'group: 0053',
  'title: 작업레포트',
  'target_id: R0001',
  '',
  '# 작업레포트',
  '',
  '| 항목 | 결과 |',
  '|---|---|',
  '| 테스트 | 통과 |',
  '',
  '- 하나',
  '- 둘',
  '',
  '```ts',
  'const a = 1',
  '```',
  '',
].join('\n')

const axiosError = (status: number) => ({ isAxiosError: true, response: { status, data: { detail: 'x' } } })

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  contentRead.mockReset()
  copy.mockReset()
  copy.mockResolvedValue(true)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

async function mountViewer(docId: string | null = DOC) {
  wrapper = mount(MdViewer, { props: { docId }, global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const el = (w: VueWrapper<any>, id: string) => w.find(`[data-test="${id}"]`)

describe('MdViewer (flowgate.v02.0053 T#3)', () => {
  it('reads the document and renders GFM: heading, table, list and code — frontmatter and next header stripped', async () => {
    contentRead.mockResolvedValue({ docId: DOC, content: BODY, missing: false })
    const w = await mountViewer()
    expect(contentRead).toHaveBeenCalledWith(DOC)
    const content = el(w, 'md-viewer-content')
    expect(content.find('h1').text()).toBe('작업레포트')
    expect(content.findAll('td').map((cell) => cell.text())).toEqual(['테스트', '통과'])
    expect(content.findAll('li').map((item) => item.text())).toEqual(['하나', '둘'])
    expect(content.find('pre code').text()).toBe('const a = 1')
    expect(content.find('.code-copy-btn').attributes('aria-label')).toBe('코드 복사')
    expect(content.text()).not.toContain('next_type')
    expect(content.text()).not.toContain('title:')
    expect(el(w, 'md-viewer-copy').attributes('title')).toBe('Markdown 복사')
    // flowgate.v02.0056 F1 (0056 CH §5): the body copy is the one copy — no "with header" button.
    expect(el(w, 'md-viewer-copy-header').exists()).toBe(false)
    expect(w.findAll('.md-viewer__toolbar button')).toHaveLength(1)
  })

  it('a document whose file is not written yet is an empty document: no text, no toolbar, no regenerate', async () => {
    contentRead.mockResolvedValue({ docId: DOC, content: '', missing: true })
    const w = await mountViewer()
    expect(el(w, 'md-viewer-content').exists()).toBe(true)
    expect(el(w, 'md-viewer-content').text()).toBe('')
    expect(el(w, 'md-viewer-copy').exists()).toBe(false)
    expect(w.text()).toBe('')
  })

  // flowgate.v02.0070 T#3 (B3): the window footer picks its actions from the state the viewer reports.
  it.each([
    ['# 본문', 'body'],
    ['---\ntitle: 빈 문서\n---\n', 'empty'],
  ] as const)('reports a readable body %j as %s for the window footer', async (content, state) => {
    contentRead.mockResolvedValue({ docId: DOC, content, missing: false })
    const w = await mountViewer()
    expect(w.vm.status).toEqual({ state, recoverable: false, recovering: false })
  })

  it('a failed read shows a short reason and a retry that reads again', async () => {
    contentRead.mockRejectedValueOnce(axiosError(500))
    const w = await mountViewer()
    expect(el(w, 'md-viewer-error').text()).toContain('불러오지 못했습니다')
    expect(w.vm.status.state).toBe('error')
    contentRead.mockResolvedValue({ docId: DOC, content: '# 다시 읽음', missing: false })
    await el(w, 'md-viewer-retry').trigger('click')
    await flushPromises()
    expect(el(w, 'md-viewer-error').exists()).toBe(false)
    expect(el(w, 'md-viewer-content').find('h1').text()).toBe('다시 읽음')
  })

  it('⧉ copies the body without the frontmatter; the button shows ✓ for a moment', async () => {
    contentRead.mockResolvedValue({ docId: DOC, content: '---\na: 1\n---\n# 본문\n', missing: false })
    const w = await mountViewer()
    await el(w, 'md-viewer-copy').trigger('click')
    await flushPromises()
    expect(copy).toHaveBeenCalledTimes(1)
    expect(copy).toHaveBeenLastCalledWith('# 본문\n')
    expect(el(w, 'md-viewer-copy').text()).toBe('✓')
  })

  it('a failed copy marks the button instead of opening a fallback', async () => {
    contentRead.mockResolvedValue({ docId: DOC, content: '# 본문', missing: false })
    copy.mockResolvedValue(false)
    const w = await mountViewer()
    await el(w, 'md-viewer-copy').trigger('click')
    await flushPromises()
    expect(el(w, 'md-viewer-copy').text()).toBe('✕')
    expect(el(w, 'md-viewer-copy').attributes('title')).toBe('복사하지 못했습니다')
  })

  it('reads again when the server says this document changed (fg:document_content_changed), not for another one', async () => {
    contentRead.mockResolvedValue({ docId: DOC, content: '# 처음', missing: false })
    const w = await mountViewer()
    contentRead.mockResolvedValue({ docId: DOC, content: '# 바뀜', missing: false })
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: 'other-doc' } }))
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: DOC, revision_no: 2 } }))
    await new Promise((resolve) => setTimeout(resolve, 300))
    await flushPromises()
    expect(contentRead).toHaveBeenCalledTimes(2)
    expect(el(w, 'md-viewer-content').find('h1').text()).toBe('바뀜')
  })
})
