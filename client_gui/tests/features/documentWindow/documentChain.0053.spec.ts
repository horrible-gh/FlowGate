/**
 * flowgate.v02.0053 T#3 (rework 2) — the document an open window shows: the end of the opened document's
 * supersede chain as the server's card line shows it (live_end), read one document per hop; and one window
 * per shown document (followDocumentWindow).
 * flowgate.v02.0070 T#4 (B4) — an exact window (`exact`, the 「전환 전 문서」 link): a window of its own for that very id —
 * never followed, never the one a usual open brings back, always the view; the usual open keeps following the chain.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { toDocumentRecord, type DocumentRecord } from '@/features/document'
import { DOCUMENT_CHAIN_MAX_HOPS, readDocumentChain } from '@/features/documentWindow/documentChain'
import {
  closeAllDocumentWindows,
  documentWindowList,
  closeDocumentWindow,
  documentWindowOf,
  followDocumentWindow,
  openDocumentWindow,
  setDocumentWindowMode,
} from '@/features/documentWindow'
import { defineComponent, h, isReactive } from 'vue'

const FLOW = 'flowgate.v02.0053'
const id = (n: number) => `${FLOW}.${String(n).padStart(4, '0')}-TR`

function store(rows: Record<string, Record<string, unknown>>) {
  const read = vi.fn(async (docId: string): Promise<DocumentRecord> => {
    const row = rows[docId]
    if (!row) throw { isAxiosError: true, response: { status: 404, data: {} } }
    return toDocumentRecord({ doc_id: docId, group_id: FLOW, status: 'open', ...row })
  })
  return read
}

const ids = (chain: DocumentRecord[]) => chain.map((item) => item.docId)

afterEach(() => closeAllDocumentWindows())

describe('readDocumentChain', () => {
  it('is the document alone when nothing supersedes it', async () => {
    const read = store({ [id(9)]: { status: 'draft' } })
    expect(ids(await readDocumentChain(id(9), read))).toEqual([id(9)])
    expect(read).toHaveBeenCalledTimes(1)
  })

  it('follows superseded_by to the live end (the run result and its next revisions)', async () => {
    const read = store({
      [id(9)]: { status: 'draft', superseded_by: id(12) },
      [id(12)]: { superseded_by: id(14) },
      [id(14)]: {},
    })
    expect(ids(await readDocumentChain(id(9), read))).toEqual([id(9), id(12), id(14)])
  })

  it('stops before a link it can not read, a cancelled document or another Flow, and at a cycle', async () => {
    expect(ids(await readDocumentChain(id(9), store({ [id(9)]: { superseded_by: id(12) } })))).toEqual([id(9)])
    expect(
      ids(await readDocumentChain(id(9), store({ [id(9)]: { superseded_by: id(12) }, [id(12)]: { status: 'cancelled' } }))),
    ).toEqual([id(9)])
    expect(
      ids(await readDocumentChain(id(9), store({ [id(9)]: { superseded_by: id(12) }, [id(12)]: { group_id: 'flowgate.v02.0052' } }))),
    ).toEqual([id(9)])
    const cycle = store({ [id(9)]: { superseded_by: id(12) }, [id(12)]: { superseded_by: id(9) } })
    expect(ids(await readDocumentChain(id(9), cycle))).toEqual([id(9), id(12)])
    expect(cycle).toHaveBeenCalledTimes(2)
  })

  it(`follows at most ${DOCUMENT_CHAIN_MAX_HOPS} links`, async () => {
    const rows: Record<string, Record<string, unknown>> = {}
    for (let n = 1; n <= 30; n += 1) rows[id(n)] = { superseded_by: id(n + 1) }
    const chain = await readDocumentChain(id(1), store(rows))
    expect(chain).toHaveLength(DOCUMENT_CHAIN_MAX_HOPS + 1)
  })

  it('throws the opened document read failure as it is', async () => {
    await expect(readDocumentChain(id(9), store({}))).rejects.toMatchObject({ response: { status: 404 } })
  })
})

describe('followDocumentWindow', () => {
  it('the window shows the followed document and is found by either id', () => {
    openDocumentWindow(id(9))
    expect(followDocumentWindow(id(9), id(12))).toBe(true)
    expect(documentWindowList().map((win) => [win.docId, win.shown])).toEqual([[id(9), id(12)]])
    expect(documentWindowOf(id(12))?.docId).toBe(id(9))

    const raise = documentWindowOf(id(9))!.raise
    openDocumentWindow(id(12), 'edit')
    expect(documentWindowList()).toHaveLength(1)
    expect(documentWindowOf(id(9))).toMatchObject({ mode: 'edit', raise: raise + 1 })
  })

  it('gives way to a window that already has the document: it closes, the other comes back', () => {
    openDocumentWindow(id(12))
    openDocumentWindow(id(9), 'edit')
    const other = documentWindowOf(id(12))!
    other.minimized = true
    const raise = other.raise

    expect(followDocumentWindow(id(9), id(12), 'edit')).toBe(false)
    expect(documentWindowList().map((win) => [win.docId, win.shown, win.mode])).toEqual([[id(12), id(12), 'edit']])
    expect(other).toMatchObject({ minimized: false, raise: raise + 1 })
  })
})

describe('exact windows (flowgate.v02.0070 T#4)', () => {
  const rows = () => documentWindowList().map((win) => [win.id, win.docId, win.shown, win.exact, win.mode])

  it('the usual open still follows the chain; an exact open of the same id is a window of its own that never follows', () => {
    openDocumentWindow(id(9))
    expect(followDocumentWindow(id(9), id(12))).toBe(true)
    openDocumentWindow(id(9), 'edit', { exact: true })
    expect(rows()).toEqual([
      [id(9), id(9), id(12), false, 'view'],
      [`exact:${id(9)}`, id(9), id(9), true, 'view'],
    ])
    // A follow request never moves the exact window, and a usual open never finds it.
    expect(followDocumentWindow(`exact:${id(9)}`, id(12))).toBe(false)
    expect(documentWindowOf(id(9))?.exact).toBe(false)
    expect(documentWindowOf(id(12))?.id).toBe(id(9))
    expect(rows()[1]).toEqual([`exact:${id(9)}`, id(9), id(9), true, 'view'])
  })

  it('a second exact open brings the exact window back (in its view); a usual open of the id opens the usual window', () => {
    openDocumentWindow(id(9), 'view', { exact: true })
    const exact = documentWindowList()[0]
    exact.minimized = true
    const raise = exact.raise
    openDocumentWindow(id(9), 'edit', { exact: true })
    expect(documentWindowList()).toHaveLength(1)
    expect(exact).toMatchObject({ minimized: false, raise: raise + 1, mode: 'view' })
    openDocumentWindow(id(9))
    expect(rows()).toEqual([
      [`exact:${id(9)}`, id(9), id(9), true, 'view'],
      [id(9), id(9), id(9), false, 'view'],
    ])
    // Another window showing the document does not make a usual window give way to the exact one.
    openDocumentWindow(id(12), 'view', { exact: true })
    expect(followDocumentWindow(id(9), id(12))).toBe(true)
    expect(documentWindowOf(id(12))?.id).toBe(id(9))
  })

  it('stays read only and closes by its own id; the usual window of the id is untouched', () => {
    openDocumentWindow(id(9))
    openDocumentWindow(id(9), 'view', { exact: true })
    setDocumentWindowMode(`exact:${id(9)}`, 'edit')
    setDocumentWindowMode(id(9), 'edit')
    expect(rows().map((row) => [row[0], row[4]])).toEqual([[id(9), 'edit'], [`exact:${id(9)}`, 'view']])
    closeDocumentWindow(`exact:${id(9)}`)
    expect(rows().map((row) => row[0])).toEqual([id(9)])
  })

  it('keeps its opener part (aside) as given, only on an exact window', () => {
    const Part = defineComponent({ props: { docId: String }, setup: (props) => () => h('i', props.docId) })
    openDocumentWindow(id(9), 'view', { exact: true, aside: { component: Part, props: { docId: id(9) } } })
    openDocumentWindow(id(12), 'view', { aside: { component: Part } })
    const [exact, usual] = documentWindowList()
    expect(exact.aside?.component).toBe(Part)
    expect(isReactive(exact.aside?.component)).toBe(false)
    expect(exact.aside?.props).toEqual({ docId: id(9) })
    expect(usual.aside).toBeNull()
  })
})
