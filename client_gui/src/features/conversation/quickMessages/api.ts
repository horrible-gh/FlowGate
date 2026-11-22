// Chat quick messages API adapter (flowgate.v02test.0072 card 2). It reads the project's existing message API, widened
// by card 1 (0004-TR) with a chat usage, a title, a kind and an order — no new server table or route:
//
//   list   GET /api/v1/projects/{project_id}/messages?usage=chat -> { data: [{ id, project_id, doc_type: '*',
//          message, updated_at, usage: 'chat', title, kind: 'sticky' | 'once', sort_order }] }
//          Gated on project.settings.read (403 without it). Ordered by sort_order, id.
//
// Create / update / delete / reorder (POST, PATCH, DELETE …/messages[/{id}]) belong to card 3.

import { getRequest } from '@shared/api'

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
