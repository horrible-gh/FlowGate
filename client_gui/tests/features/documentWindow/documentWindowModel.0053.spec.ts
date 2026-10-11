/** flowgate.v02.0053 T#3 — the document window's pure decisions (state dot, body view, frontmatter, reasons). */
import { describe, expect, it } from 'vitest'
import {
  documentBodyView,
  documentDot,
  documentReadErrorKey,
  documentSaveErrorKey,
  joinDocumentSource,
  splitDocumentSource,
} from '@/features/documentWindow/documentWindowModel'

describe('documentWindowModel (flowgate.v02.0053 T#3)', () => {
  it('the state dot: the review status first, then the document status, neutral when unknown', () => {
    expect(documentDot({ status: 'open', docReviewStatus: 'approved' })).toEqual({ tone: 'success', key: 'approved' })
    expect(documentDot({ status: 'open', docReviewStatus: 'pending_review' })).toEqual({ tone: 'warning', key: 'pending_review' })
    expect(documentDot({ status: 'open', docReviewStatus: 'revised' })).toEqual({ tone: 'warning', key: 'revised' })
    expect(documentDot({ status: 'open', docReviewStatus: 'rejected' })).toEqual({ tone: 'danger', key: 'rejected' })
    expect(documentDot({ status: 'closed', docReviewStatus: null })).toEqual({ tone: 'success', key: 'closed' })
    expect(documentDot({ status: 'open', docReviewStatus: null })).toEqual({ tone: 'info', key: 'open' })
    expect(documentDot({ status: 'draft', docReviewStatus: null })).toEqual({ tone: 'neutral', key: 'draft' })
    expect(documentDot({ status: 'weird', docReviewStatus: 'other' })).toEqual({ tone: 'neutral', key: 'unknown' })
    expect(documentDot(null)).toEqual({ tone: 'neutral', key: 'unknown' })
  })

  it('every document type goes to the MD Viewer for now (the special views come with the next task)', () => {
    for (const type of ['TR', 'T', 'WP', 'TS', 'TSR', 'R', null]) expect(documentBodyView(type)).toBe('markdown')
  })

  it('splits the frontmatter off for the editor and puts it back unchanged on save', () => {
    const stored = '---\ntitle: 작업레포트\ntype: TR\n---\n# 본문\n\n내용\n'
    const source = splitDocumentSource(stored)
    expect(source).toEqual({ frontmatter: '---\ntitle: 작업레포트\ntype: TR\n---', body: '# 본문\n\n내용\n' })
    expect(joinDocumentSource(source)).toBe(stored)
    expect(joinDocumentSource({ ...source, body: '바뀐 본문' })).toBe('---\ntitle: 작업레포트\ntype: TR\n---\n바뀐 본문')
  })

  it('a body without (or with an unclosed) frontmatter is edited whole', () => {
    expect(splitDocumentSource('# 제목\n')).toEqual({ frontmatter: '', body: '# 제목\n' })
    expect(splitDocumentSource('---\nopen only\n')).toEqual({ frontmatter: '', body: '---\nopen only\n' })
    expect(splitDocumentSource('')).toEqual({ frontmatter: '', body: '' })
    expect(joinDocumentSource({ frontmatter: '', body: '새 문서' })).toBe('새 문서')
  })

  it('short reasons: read failures and save refusals (422 = not editable now, 409 = busy)', () => {
    expect(documentReadErrorKey('not_found')).toBe('gone')
    expect(documentReadErrorKey('forbidden')).toBe('forbidden')
    expect(documentReadErrorKey('network')).toBe('network')
    expect(documentReadErrorKey('other')).toBe('load')
    expect(documentSaveErrorKey('other', 422)).toBe('locked')
    expect(documentSaveErrorKey('conflict', 409)).toBe('busy')
    expect(documentSaveErrorKey('forbidden', 403)).toBe('forbidden')
    expect(documentSaveErrorKey('not_found', 404)).toBe('gone')
    expect(documentSaveErrorKey('network', null)).toBe('network')
    expect(documentSaveErrorKey('other', 500)).toBe('save')
  })
})
