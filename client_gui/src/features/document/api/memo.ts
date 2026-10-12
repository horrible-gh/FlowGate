// flowgate.v02.0053 T#9 — the Inspector header's ✎ 메모 on the v0.1 memo API (R0052 §2.6). No new server
// API: a memo is a v0.1 memo document (type M) whose target is the card's document, made the way the v0.1
// "new related document" dialog makes it (NewRelatedDocModal).
//
//   target   GET  /api/v1/documents/{doc_id}                         -> its project_id / group_id / module
//   list     GET  /api/v1/documents?project_id&group_id&type_code=M&limit&offset
//                                                                    -> rows, newest change first; a
//                                                                       document's memos are the rows whose
//                                                                       target_id is that document
//
// The list is read page after page (offset) until a short page, so a memo past the first page is not missed.
// A page that fails fails the whole list: it is never taken as "no memo", which would write a second one.
//   create   POST /api/v1/documents/related { project_id, type_code: 'M', title, group_id, target_id,
//                                             template: 'default', module } -> 201 { doc_id }
//
// The server refuses a create while the Flow's AI runs (409) and without perm_document_create (403); the
// refusal is thrown as it is (see documentErrorOf). The memo itself opens in the document window.
import { getRequest, postRequest } from '@shared/api'

const API = '/api/v1'

/** The longest title the server takes (documents.py create_related_document). */
export const MEMO_TITLE_MAX_LENGTH = 100

/** How many memo rows of a Flow one page takes (the list API's upper bound). */
const MEMO_LIST_LIMIT = 500

/** Pages read at most; a list longer than this (or a server that ignores offset) fails rather than ends short. */
const MEMO_LIST_PAGES_MAX = 200

export interface MemoRecord {
  readonly docId: string
  readonly title: string
  readonly updatedAt: string | null
}

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)

interface MemoTarget {
  readonly docId: string
  readonly projectId: string
  readonly groupId: string
  readonly module: string
}

async function memoTargetRead(docId: string): Promise<MemoTarget> {
  const response = await getRequest<Raw>(`${API}/documents/${encodeURIComponent(docId)}`)
  const row = response.data ?? {}
  const projectId = text(row.project_id)
  const groupId = text(row.group_id)
  if (!projectId || !groupId) throw new Error('memo target without project or group')
  return { docId, projectId, groupId, module: text(row.module) ?? 'none' }
}

/** The memos of a document, newest change first (empty when it has none); throws when a page cannot be read. */
export async function memoList(docId: string): Promise<MemoRecord[]> {
  const target = await memoTargetRead(docId)
  const memos: MemoRecord[] = []
  // A memo changed while the pages are read moves to the front and pushes the rest a row down: a row may
  // come twice and is kept once.
  const seen = new Set<string>()
  for (let page = 0; page < MEMO_LIST_PAGES_MAX; page++) {
    const response = await getRequest<unknown>(`${API}/documents`, {
      project_id: target.projectId,
      group_id: target.groupId,
      type_code: 'M',
      limit: MEMO_LIST_LIMIT,
      offset: page * MEMO_LIST_LIMIT,
    })
    if (!Array.isArray(response.data)) throw new Error('memo list answered without rows')
    const rows = response.data as Raw[]
    for (const row of rows) {
      if (!row || typeof row !== 'object' || row.target_id !== docId) continue
      const id = text(row.doc_id)
      if (!id || seen.has(id)) continue
      seen.add(id)
      memos.push({ docId: id, title: typeof row.title === 'string' ? row.title : '', updatedAt: text(row.updated_at) })
    }
    if (rows.length < MEMO_LIST_LIMIT) return memos
  }
  throw new Error('memo list longer than it can be read')
}

/** Makes a memo of the document (its own Flow, project and module); answers the new memo's id. */
export async function memoCreate(docId: string, title: string): Promise<string> {
  const target = await memoTargetRead(docId)
  const response = await postRequest<Raw>(`${API}/documents/related`, {
    project_id: target.projectId,
    type_code: 'M',
    title: title.trim().slice(0, MEMO_TITLE_MAX_LENGTH),
    group_id: target.groupId,
    target_id: docId,
    template: 'default',
    module: target.module,
  })
  const created = text(response.data?.doc_id)
  if (!created) throw new Error('memo create answered without doc_id')
  return created
}
