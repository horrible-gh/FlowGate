// flowgate.v02.0053 T#3 — what the document window decides without a screen or a server call: the
// title line's state dot, which body view a document gets (T#4: the special views), the frontmatter
// kept aside while the body is edited, and the short reason a failed read or save shows.
import type { DocumentErrorKind, DocumentRecord } from '@/features/document'

/** The state dot's color (the Inspector pill tones). */
export type DocumentDotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

export interface DocumentDot {
  readonly tone: DocumentDotTone
  /** Message key under `gui.document_window.state` (the dot's tooltip). */
  readonly key: string
}

const REVIEW_DOTS: Readonly<Record<string, DocumentDot>> = {
  approved: { tone: 'success', key: 'approved' },
  pending_review: { tone: 'warning', key: 'pending_review' },
  revised: { tone: 'warning', key: 'revised' },
  rejected: { tone: 'danger', key: 'rejected' },
}

const STATUS_DOTS: Readonly<Record<string, DocumentDot>> = {
  closed: { tone: 'success', key: 'closed' },
  open: { tone: 'info', key: 'open' },
  draft: { tone: 'neutral', key: 'draft' },
}

/** The dot of a document: its review status first, else its document status; neutral when unknown. */
export function documentDot(record: Pick<DocumentRecord, 'status' | 'docReviewStatus'> | null): DocumentDot {
  if (!record) return { tone: 'neutral', key: 'unknown' }
  return REVIEW_DOTS[record.docReviewStatus ?? ''] ?? STATUS_DOTS[record.status ?? ''] ?? { tone: 'neutral', key: 'unknown' }
}

/**
 * The view the window body gives a document (T#4, the v0.1 DocumentBodyRouter's branches for the documents
 * that have a view of their own): a work plan its plan table, a test scenario / test report its test view,
 * every other document the MD Viewer.
 */
export type DocumentBodyView = 'markdown' | 'work_plan' | 'test'

const SPECIAL_VIEWS: Readonly<Record<string, DocumentBodyView>> = {
  WP: 'work_plan',
  TS: 'test',
  TSR: 'test',
}

export function documentBodyView(typeCode: string | null): DocumentBodyView {
  return SPECIAL_VIEWS[(typeCode ?? '').trim().toUpperCase()] ?? 'markdown'
}

export interface DocumentSource {
  /** The YAML frontmatter block as stored ('' when the body has none). */
  readonly frontmatter: string
  /** What the editor shows: the body after the frontmatter. */
  readonly body: string
}

/** Splits the stored file into its frontmatter and body (the v0.1 editor's parseFrontmatter). */
export function splitDocumentSource(content: string): DocumentSource {
  if (!content.startsWith('---\n')) return { frontmatter: '', body: content }
  const closing = content.indexOf('\n---', 3)
  if (closing === -1) return { frontmatter: '', body: content }
  const end = closing + 4
  const rest = content.slice(end)
  return { frontmatter: content.slice(0, end), body: rest.startsWith('\n') ? rest.slice(1) : rest }
}

/** Puts the kept frontmatter back in front of the edited body (the v0.1 editor's save join). */
export function joinDocumentSource(source: DocumentSource): string {
  return source.frontmatter ? `${source.frontmatter}\n${source.body}` : source.body
}

/** Message key under `gui.document_window.error` for a failed read. */
export function documentReadErrorKey(kind: DocumentErrorKind): string {
  if (kind === 'not_found') return 'gone'
  if (kind === 'forbidden') return 'forbidden'
  if (kind === 'network') return 'network'
  return 'load'
}

/** Message key under `gui.document_window.error` for a failed save (422: not editable now). */
export function documentSaveErrorKey(kind: DocumentErrorKind, status: number | null): string {
  if (status === 422) return 'locked'
  if (kind === 'conflict') return 'busy'
  if (kind === 'forbidden') return 'forbidden'
  if (kind === 'not_found') return 'gone'
  if (kind === 'network') return 'network'
  return 'save'
}
