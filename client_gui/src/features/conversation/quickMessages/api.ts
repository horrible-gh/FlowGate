// Chat quick messages API adapter (flowgate.v02test.0072 card 2). It reads the project's existing message API, widened
// by card 1 (0004-TR) with a chat usage, a title, a kind and an order — no new server table or route:
//
//   list   GET /api/v1/projects/{project_id}/messages?usage=chat -> { data: [{ id, project_id, doc_type: '*',
//          message, updated_at, usage: 'chat', title, kind: 'sticky' | 'once', sort_order }] }
//          Gated on project.settings.read (403 without it). Ordered by sort_order, id.
//
// Card 3 manages the same rows from the row itself (no new route, no new table):
//
//   create POST   …/messages            { usage: 'chat', title, message, kind, sort_order } -> 201 row
//   update PATCH  …/messages/{id}       { title?, message?, kind?, sort_order? } -> row
//   delete DELETE …/messages/{id}       -> { detail: 'deleted' }   (404 = already gone)
//   order  PATCH  …/messages/{id}       { sort_order } for every row whose position changed (a failed save is undone by
//          writing the old sort_order of every row back, after all requests of the save have finished)
//   edit?  GET    /rbac/projects/{project_id}/my-permissions -> { permissions: string[], is_admin }
//          ＋ 추가 / ✎ 편집 / right-click edit are offered only with project.settings.edit (the server checks it too).
//
// A blank title / text or a title over 12 characters is refused here before any call (the server answers 422 too).

import { deleteRequest, getRequest, patchRequest, postRequest } from '@shared/api'

/** 'sticky' = 계속 (stays on until pressed again, survives F5); 'once' = 1회 (off after one send). */
export type QuickMessageKind = 'sticky' | 'once'

export interface QuickMessage {
  readonly id: number
  /** Short button label (up to 12 characters). */
  readonly title: string
  /** The text appended to the outgoing chat message. */
  readonly message: string
  readonly kind: QuickMessageKind
  readonly sortOrder: number
}

type Raw = Record<string, unknown>

export const quickMessagesPath = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/messages`

/** One row of the list -> QuickMessage; rows without an id or text are dropped. */
export function toQuickMessage(value: unknown): QuickMessage | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const row = value as Raw
  const id = typeof row.id === 'number' && Number.isInteger(row.id) ? row.id : null
  const message = typeof row.message === 'string' ? row.message.trim() : ''
  if (id === null || !message) return null
  const rawTitle = typeof row.title === 'string' ? row.title.trim() : ''
  return {
    id,
    title: rawTitle || message.slice(0, 12),
    message,
    kind: row.kind === 'sticky' ? 'sticky' : 'once',
    sortOrder: typeof row.sort_order === 'number' && Number.isFinite(row.sort_order) ? row.sort_order : 0,
  }
}

export const QUICK_TITLE_MAX = 12

/** What the add / edit window sends. */
export interface QuickMessageInput {
  readonly title: string
  readonly message: string
  readonly kind: QuickMessageKind
}

export type QuickMessageInputProblem = 'title' | 'message'

/** The first blank / too long field of an input, or null when it can be saved. */
export function checkQuickMessageInput(input: QuickMessageInput): QuickMessageInputProblem | null {
  const title = input.title.trim()
  if (!title || [...title].length > QUICK_TITLE_MAX) return 'title'
  if (!input.message.trim()) return 'message'
  return null
}

const messagePath = (projectId: string, id: number) => `${quickMessagesPath(projectId)}/${id}`

function rowOrThrow(response: { data?: unknown } | undefined): QuickMessage {
  const row = toQuickMessage(response?.data)
  if (!row) throw new Error('quick_messages_invalid_response')
  return row
}

/** Adds a chat quick message at `sortOrder` and returns the stored row. */
export async function createChatQuickMessage(projectId: string, input: QuickMessageInput, sortOrder: number): Promise<QuickMessage> {
  const response = await postRequest<unknown>(quickMessagesPath(projectId), {
    usage: 'chat',
    title: input.title.trim(),
    message: input.message.trim(),
    kind: input.kind,
    sort_order: sortOrder,
  })
  return rowOrThrow(response)
}

/** Changes a quick message's title / text / kind and returns the stored row. */
export async function updateChatQuickMessage(projectId: string, id: number, input: QuickMessageInput): Promise<QuickMessage> {
  const response = await patchRequest<unknown>(messagePath(projectId, id), {
    title: input.title.trim(),
    message: input.message.trim(),
    kind: input.kind,
  })
  return rowOrThrow(response)
}

/** Deletes a quick message; a row that is already gone (404) counts as deleted. */
export async function deleteChatQuickMessage(projectId: string, id: number): Promise<void> {
  try {
    await deleteRequest(messagePath(projectId, id))
  } catch (error) {
    if ((error as { response?: { status?: number } })?.response?.status !== 404) throw error
  }
}

/** Waits until every request has finished (none is left in flight), then rejects with the first failure, if any. */
async function settleAll(requests: Promise<unknown>[]): Promise<void> {
  const results = await Promise.allSettled(requests)
  const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
  if (failed) throw failed.reason
}

/**
 * Saves a new row order: `order` lists the rows' ids in their new order and gets sort_order 1, 2, 3 … (only changed rows
 * are sent). It settles only after every request has finished, so a rejection means no save is still on its way and part
 * of the order may have been stored.
 */
export async function saveChatQuickMessageOrder(projectId: string, before: readonly QuickMessage[], order: readonly number[]): Promise<void> {
  const known = new Map(before.map((item) => [item.id, item.sortOrder]))
  const changed = order.filter((id, index) => known.has(id) && known.get(id) !== index + 1)
  await settleAll(changed.map((id) => patchRequest(messagePath(projectId, id), { sort_order: order.indexOf(id) + 1 })))
}

/**
 * Puts every row of `before` back at its own sort_order. Used after a failed order save: which rows were stored is not
 * known, so all of them are written (the same value again is harmless). Settles after every request has finished and
 * rejects when any of them failed.
 */
export async function restoreChatQuickMessageOrder(projectId: string, before: readonly QuickMessage[]): Promise<void> {
  await settleAll(before.map((item) => patchRequest(messagePath(projectId, item.id), { sort_order: item.sortOrder })))
}

/** Whether the signed-in user may add / edit / delete quick messages of the project (project.settings.edit). Any failure = no. */
export async function fetchCanEditQuickMessages(projectId: string): Promise<boolean> {
  try {
    const response = await getRequest<{ permissions?: unknown; is_admin?: unknown }>(`/rbac/projects/${encodeURIComponent(projectId)}/my-permissions`)
    const body = response?.data
    if (body?.is_admin === true) return true
    return Array.isArray(body?.permissions) && body.permissions.includes('project.settings.edit')
  } catch {
    return false
  }
}

/** The project's chat quick messages in row order. Any failure (403 included) rejects. */
export async function fetchChatQuickMessages(projectId: string): Promise<QuickMessage[]> {
  const response = await getRequest<{ data?: unknown }>(quickMessagesPath(projectId), { usage: 'chat' })
  const data = response?.data?.data
  if (!Array.isArray(data)) throw new Error('quick_messages_invalid_response')
  return data
    .map(toQuickMessage)
    .filter((item): item is QuickMessage => item !== null)
    .sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id)
}
