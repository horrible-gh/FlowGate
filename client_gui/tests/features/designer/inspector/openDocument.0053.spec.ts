/**
 * flowgate.v02.0053 T#3 — openDocument opens the document in this tab, in its own modeless document
 * window (features/documentWindow), never in a browser tab: `view` (↗) or straight to the editor
 * (`edit`, ✎); one window per document — a second request brings the open window back (restored, in
 * front) and an edit request turns it to the editor without dropping an editor that is open.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openDocument } from '@/features/designer/inspector/openDocument'
import { closeAllDocumentWindows, documentWindowList, documentWindowOf, setDocumentWindowMinimized } from '@/features/documentWindow'

const DOC = 'flowgate.v02.0053.0007-TR'
const OTHER = 'flowgate.v02.0053.0006-T'

const windows = () => documentWindowList().map((win) => [win.docId, win.mode])

let open: ReturnType<typeof vi.fn>

beforeEach(() => {
  closeAllDocumentWindows()
  open = vi.fn(() => null)
  vi.stubGlobal('open', open)
})

afterEach(() => {
  closeAllDocumentWindows()
  vi.unstubAllGlobals()
})

describe('openDocument (flowgate.v02.0053 T#3)', () => {
  it('opens a document window in view mode by default — no browser tab, no URL', () => {
    openDocument(DOC)
    expect(windows()).toEqual([[DOC, 'view']])
    expect(open).not.toHaveBeenCalled()
  })

  it("'edit' opens the window straight in its editor", () => {
    openDocument(DOC, 'edit')
    expect(windows()).toEqual([[DOC, 'edit']])
  })

  it('keeps one window per document; several documents stack, cascaded so each stays visible', () => {
    openDocument(DOC)
    openDocument(OTHER)
    openDocument(DOC)
    expect(windows()).toEqual([[DOC, 'view'], [OTHER, 'view']])
    const [first, second] = documentWindowList()
    expect([second.x - first.x, second.y - first.y]).toEqual([28, 28])
  })

  it('a second request restores a minimized window and asks it to come to the front', () => {
    openDocument(DOC)
    setDocumentWindowMinimized(DOC, true)
    const raise = documentWindowOf(DOC)!.raise
    openDocument(DOC)
    expect(documentWindowOf(DOC)!.minimized).toBe(false)
    expect(documentWindowOf(DOC)!.raise).toBe(raise + 1)
  })

  it('✎ on an open view turns it to the editor; ↗ on an open editor leaves the editor (its text stays)', () => {
    openDocument(DOC)
    openDocument(DOC, 'edit')
    expect(windows()).toEqual([[DOC, 'edit']])
    openDocument(DOC)
    expect(windows()).toEqual([[DOC, 'edit']])
  })

  it('an empty doc_id opens nothing', () => {
    openDocument('')
    expect(windows()).toEqual([])
  })
})
