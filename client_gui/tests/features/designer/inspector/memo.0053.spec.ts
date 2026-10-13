/**
 * flowgate.v02.0053 T#9 — the header ✎ 메모 (R0052 §2.6) inside v0.2: the v0.1 memo API (a memo is a type M
 * document whose target is the card's document, made like the v0.1 "new related document" dialog), the
 * button that opens the newest memo in the document window or writes a new one and opens it in its editor,
 * a refusal shown in the tooltip only, and the registry that puts it on every card with a document while a
 * Node without one keeps the Frame's dim, disabled ✎ 메모.
 * rev1 (rej_01M4PAAWRD27JZRF): the memo list is read page after page (offset), so a memo past the first 500
 * rows of the Flow is found and opened instead of a second one being written; a page that fails is not "no memo".
 */
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

const memoListCall = vi.hoisted(() => vi.fn())
const memoCreateCall = vi.hoisted(() => vi.fn())
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    memoList: (...args: unknown[]) => memoListCall(...args),
    memoCreate: (...args: unknown[]) => memoCreateCall(...args),
  }
})

import { memoCreate, memoList } from '@/features/document/api/memo'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import MemoButton from '@/features/designer/inspector/features/memo/MemoButton.vue'
import { memoFailureOf, memoLatest, memoTitleWithin } from '@/features/designer/inspector/features/memo/memoModel'
import { contributionsFor, inspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import { SELECTIONS, prepareInspectorI18n, targetOf } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()
const global = { plugins: [i18n] }

const TARGET = targetOf(SELECTIONS.v01)
const DOC = TARGET.docId!

function axiosError(status: number) {
  return Object.assign(new Error('Request failed'), { isAxiosError: true, response: { status, data: { detail: 'refused' } } })
}

interface MemoRow {
  readonly doc_id: string
  readonly title: string
  readonly target_id: string
  readonly updated_at: string
}

/** `count` memos of another document, newest first, all newer than the target's memo that follows them. */
function otherMemos(count: number): MemoRow[] {
  return Array.from({ length: count }, (_, index) => ({
    doc_id: `flowgate.v02.0053.${String(9000 - index).padStart(4, '0')}-M`,
    title: `다른 문서 메모 ${index}`,
    target_id: 'flowgate.v02.0053.0001-R',
    updated_at: '2026-10-11T13:00:00Z',
  }))
}

const SECOND_PAGE_MEMO: MemoRow = { doc_id: 'flowgate.v02.0053.0040-M', title: '메모 — 둘째 쪽', target_id: DOC, updated_at: '2026-10-11T09:00:00Z' }

const TARGET_ROW = { data: { doc_id: DOC, project_id: 'flowgate', group_id: 'flowgate.v02.0053', module: 'v02' } }

/**
 * The HTTP boundary as the server answers it: the target document, and the Flow's M documents in their order
 * cut by limit (default 100, at most 500) and offset (documents.py list_documents). `fail(offset)` makes that
 * page answer 500. Answers the list offsets asked, in order.
 */
function pagedServer(rows: readonly MemoRow[], fail: (offset: number) => boolean = () => false): number[] {
  const offsets: number[] = []
  getRequest.mockImplementation(async (url: string, params?: Record<string, unknown>) => {
    if (url === `/api/v1/documents/${DOC}`) return TARGET_ROW
    if (url === '/api/v1/documents') {
      const limit = Math.min(Number(params?.limit ?? 100), 500)
      const offset = Number(params?.offset ?? 0)
      offsets.push(offset)
      if (fail(offset)) throw axiosError(500)
      return { data: rows.slice(offset, offset + limit) }
    }
    throw new Error(`unexpected ${url}`)
  })
  postRequest.mockResolvedValue({ data: { doc_id: 'flowgate.v02.0053.0099-M' } })
  return offsets
}

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  memoListCall.mockReset()
  memoCreateCall.mockReset()
  i18n.global.locale.value = 'ko'
})

afterEach(() => closeAllDocumentWindows())

describe('memo API (v0.1 documents, no new server API)', () => {
  it('lists the memos of a document: its Flow’s M documents whose target is the document', async () => {
    getRequest.mockImplementation(async (url: string) => {
      if (url === `/api/v1/documents/${DOC}`) return { data: { doc_id: DOC, project_id: 'flowgate', group_id: 'flowgate.v02.0053', module: 'v02' } }
      if (url === '/api/v1/documents') {
        return {
          data: [
            { doc_id: 'flowgate.v02.0053.0031-M', title: '메모 — 새것', target_id: DOC, updated_at: '2026-10-11T13:10:00Z' },
            { doc_id: 'flowgate.v02.0053.0030-M', title: '다른 문서 메모', target_id: 'flowgate.v02.0053.0001-R', updated_at: '2026-10-11T13:20:00Z' },
            { doc_id: 'flowgate.v02.0053.0029-M', title: '메모 — 옛것', target_id: DOC, updated_at: '2026-10-11T12:00:00Z' },
          ],
        }
      }
      throw new Error(`unexpected ${url}`)
    })
    expect(await memoList(DOC)).toEqual([
      { docId: 'flowgate.v02.0053.0031-M', title: '메모 — 새것', updatedAt: '2026-10-11T13:10:00Z' },
      { docId: 'flowgate.v02.0053.0029-M', title: '메모 — 옛것', updatedAt: '2026-10-11T12:00:00Z' },
    ])
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/documents', { project_id: 'flowgate', group_id: 'flowgate.v02.0053', type_code: 'M', limit: 500, offset: 0 })
  })

  it('reads the list page after page (offset): a memo past the first 500 rows is found', async () => {
    const offsets = pagedServer([...otherMemos(500), SECOND_PAGE_MEMO])
    expect(await memoList(DOC)).toEqual([{ docId: SECOND_PAGE_MEMO.doc_id, title: '메모 — 둘째 쪽', updatedAt: '2026-10-11T09:00:00Z' }])
    expect(offsets).toEqual([0, 500])
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/documents', { project_id: 'flowgate', group_id: 'flowgate.v02.0053', type_code: 'M', limit: 500, offset: 500 })

    // A full page is followed by one more read (it may not be the last); a short or empty page ends the list.
    const exact = pagedServer(otherMemos(1000))
    expect(await memoList(DOC)).toEqual([])
    expect(exact).toEqual([0, 500, 1000])
  })

  it('a row that comes twice across pages is kept once; the order stays newest first', async () => {
    const newer = { ...SECOND_PAGE_MEMO, doc_id: 'flowgate.v02.0053.0041-M', updated_at: '2026-10-11T14:00:00Z' }
    const rows = [...otherMemos(499), newer, SECOND_PAGE_MEMO]
    getRequest.mockImplementation(async (url: string, params?: Record<string, unknown>) => {
      if (url === `/api/v1/documents/${DOC}`) return TARGET_ROW
      // The second page starts a row early (a memo changed between the reads): the first page's last row again.
      return { data: Number(params?.offset ?? 0) === 0 ? rows.slice(0, 500) : rows.slice(499) }
    })
    expect((await memoList(DOC)).map((memo) => memo.docId)).toEqual([newer.doc_id, SECOND_PAGE_MEMO.doc_id])
  })

  it('a page that fails fails the list — it is never read as "no memo"', async () => {
    const offsets = pagedServer([...otherMemos(500), SECOND_PAGE_MEMO], (offset) => offset === 500)
    await expect(memoList(DOC)).rejects.toMatchObject({ response: { status: 500 } })
    expect(offsets).toEqual([0, 500])
    getRequest.mockImplementation(async (url: string) => (url === '/api/v1/documents' ? { data: { detail: 'not a list' } } : TARGET_ROW))
    await expect(memoList(DOC)).rejects.toThrow('memo list answered without rows')
  })

  it('writes a memo the way the v0.1 related-document dialog does (type M, target = the document)', async () => {
    getRequest.mockResolvedValue({ data: { doc_id: DOC, project_id: 'flowgate', group_id: 'flowgate.v02.0053', module: 'v02' } })
    postRequest.mockResolvedValue({ data: { doc_id: 'flowgate.v02.0053.0032-M' } })
    expect(await memoCreate(DOC, '  메모 — 토큰 갱신  ')).toBe('flowgate.v02.0053.0032-M')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/related', {
      project_id: 'flowgate',
      type_code: 'M',
      title: '메모 — 토큰 갱신',
      group_id: 'flowgate.v02.0053',
      target_id: DOC,
      template: 'default',
      module: 'v02',
    })
  })

  it('the rules: the newest memo, a title within the server limit, the reason of a refusal', () => {
    expect(memoLatest([{ docId: 'b' }, { docId: 'a' }])).toEqual({ docId: 'b' })
    expect(memoLatest([])).toBeNull()
    expect(memoTitleWithin('x'.repeat(130), 'd')).toHaveLength(100)
    expect(memoTitleWithin('  ', 'doc.1')).toBe('doc.1')
    expect(memoFailureOf({ kind: 'conflict', status: 409 })).toBe('running')
    expect(memoFailureOf({ kind: 'other', status: 423 })).toBe('running')
    expect(memoFailureOf({ kind: 'forbidden', status: 403 })).toBe('forbidden')
    expect(memoFailureOf({ kind: 'network', status: null })).toBe('network')
    expect(memoFailureOf({ kind: 'other', status: 500 })).toBe('other')
  })
})

describe('the header ✎ 메모 (MemoButton)', () => {
  it('no memo yet: dim, "메모 쓰기"; a click writes one titled after the card and opens it in its editor', async () => {
    memoListCall.mockResolvedValueOnce([]).mockResolvedValue([{ docId: 'm.new', title: '메모', updatedAt: null }])
    memoCreateCall.mockResolvedValue('m.new')
    const w = mount(MemoButton, { props: { target: TARGET }, global })
    await flushPromises()
    const button = w.get('[data-test="designer-inspector-memo"]')
    expect(button.text()).toBe('✎ 메모')
    expect(button.classes()).toContain('is-none')
    expect(button.attributes('title')).toBe('메모 쓰기')

    await button.trigger('click')
    await flushPromises()
    expect(memoCreateCall).toHaveBeenCalledWith(DOC, `메모 — ${TARGET.title}`)
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([['m.new', 'edit']])
    expect(button.classes()).not.toContain('is-none')
    expect(button.attributes('title')).toBe('메모 열기')
    expect(button.attributes('data-memo')).toBe('m.new')
  })

  it('a memo exists: the click opens the newest one as it reads, and writes nothing', async () => {
    memoListCall.mockResolvedValue([{ docId: 'm.2', title: '메모', updatedAt: null }, { docId: 'm.1', title: '메모', updatedAt: null }])
    const w = mount(MemoButton, { props: { target: TARGET }, global })
    await flushPromises()
    await w.get('[data-test="designer-inspector-memo"]').trigger('click')
    await flushPromises()
    expect(memoCreateCall).not.toHaveBeenCalled()
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([['m.2', 'view']])
  })

  it('a click before the memos are read waits for them, so a memo is never written twice', async () => {
    let answer: (value: unknown) => void = () => undefined
    memoListCall.mockReturnValueOnce(new Promise((resolve) => (answer = resolve))).mockResolvedValue([{ docId: 'm.0', title: '', updatedAt: null }])
    const w = mount(MemoButton, { props: { target: TARGET }, global })
    const button = w.get('[data-test="designer-inspector-memo"]')
    const click = button.trigger('click')
    answer([{ docId: 'm.0', title: '', updatedAt: null }])
    await click
    await flushPromises()
    expect(memoCreateCall).not.toHaveBeenCalled()
    expect(documentWindowList().map((win) => win.docId)).toEqual(['m.0'])
  })

  it('rev1: the existing memo on the second page of the list opens as it reads, and nothing is written', async () => {
    // The real memo API over the server's paging (the button's read and write are not stubbed here).
    memoListCall.mockImplementation((docId: string) => memoList(docId))
    memoCreateCall.mockImplementation((docId: string, title: string) => memoCreate(docId, title))
    const offsets = pagedServer([...otherMemos(500), SECOND_PAGE_MEMO])
    const w = mount(MemoButton, { props: { target: TARGET }, global })
    await flushPromises()
    const button = w.get('[data-test="designer-inspector-memo"]')
    expect(button.classes()).not.toContain('is-none')
    expect(button.attributes('title')).toBe('메모 열기')
    expect(button.attributes('data-memo')).toBe(SECOND_PAGE_MEMO.doc_id)

    await button.trigger('click')
    await flushPromises()
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[SECOND_PAGE_MEMO.doc_id, 'view']])
    expect(memoCreateCall).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(offsets).toEqual([0, 500])
  })

  it('rev1: a later page that fails is not "no memo": the click reads again and writes nothing until a read answers', async () => {
    memoListCall.mockImplementation((docId: string) => memoList(docId))
    memoCreateCall.mockImplementation((docId: string, title: string) => memoCreate(docId, title))
    let down = true
    const offsets = pagedServer([...otherMemos(500), SECOND_PAGE_MEMO], (offset) => down && offset === 500)
    const w = mount(MemoButton, { props: { target: TARGET }, global })
    await flushPromises()
    const button = w.get('[data-test="designer-inspector-memo"]')
    expect(button.classes()).not.toContain('is-none')
    expect(button.classes()).toContain('is-failed')
    expect(button.attributes('title')).toBe('메모를 읽지 못했습니다')

    await button.trigger('click')
    await flushPromises()
    expect(offsets).toEqual([0, 500, 0, 500])
    expect(memoCreateCall).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(documentWindowList()).toEqual([])
    expect(button.attributes('title')).toBe('메모를 읽지 못했습니다')

    // Once the pages answer, the same click opens the existing memo (still nothing written).
    down = false
    await button.trigger('click')
    await flushPromises()
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[SECOND_PAGE_MEMO.doc_id, 'view']])
    expect(postRequest).not.toHaveBeenCalled()
    expect(button.classes()).not.toContain('is-failed')
    expect(button.attributes('title')).toBe('메모 열기')
  })

  it('a refused write (the Flow’s AI is working) shows in the tooltip only and opens nothing', async () => {
    memoListCall.mockResolvedValue([])
    memoCreateCall.mockRejectedValue(axiosError(409))
    const w = mount(MemoButton, { props: { target: TARGET }, global })
    await flushPromises()
    const button = w.get('[data-test="designer-inspector-memo"]')
    await button.trigger('click')
    await flushPromises()
    expect(button.attributes('title')).toBe('AI가 작업 중이라 지금은 메모를 쓸 수 없습니다')
    expect(button.classes()).toContain('is-failed')
    expect(documentWindowList()).toEqual([])
    expect(w.text()).not.toContain('409')
  })

  it('the registry puts it on every card with a document; a Node without one keeps the Frame’s disabled ✎ 메모', () => {
    const memoOf = (selection: (typeof SELECTIONS)[keyof typeof SELECTIONS]) =>
      contributionsFor(inspectorRegistry, targetOf(selection)).find((entry) => entry.headerMemo)?.key ?? null
    expect(memoOf(SELECTIONS.v01)).toBe('memo')
    // flowgate.v02.0056 (0017-CH): a Graph card's ✎ 메모 writes a card memo (features/cardMemo).
    expect(memoOf(SELECTIONS.graphGate)).toBe('cardMemo')
    expect(memoOf(SELECTIONS.v01NoDocument)).toBeNull()
  })
})
