// flowgate.v02test.0024 B (decision 1): the chat's own request failures are part of its conversation history.
//
// A refused send, AI call or command decision used to be only the notice line above the composer — gone on F5, on
// reopening the chat and on the next notice. It is now a status line of the timeline, placed at its time, and it is
// recorded so it comes back:
//   * the server refused the request (it answered 4xx / 5xx) → the server keeps it (POST /chat-activity/{doc}/errors,
//     read back with the chat's activity);
//   * the request got no answer (network / server down), or the record itself could not be stored → this browser
//     keeps it (localStorage), next to the chat's draft.
// Only the code, the server's words (redacted) and the time are kept — never a token or a request header.
import type { StatusActivity, StatusTone } from './types'

/** The conversation-progress failures the timeline keeps, with their tone. Other notices stay notices. */
export const CHAT_ERROR_CODES: Readonly<Record<string, StatusTone>> = Object.freeze({
  send_failed: 'danger',
  invoke_failed: 'danger',
  command_decision_failed: 'danger',
  commit_required: 'warning',
  source_locked: 'warning',
})

export function isChatErrorCode(code: string): boolean {
  return Object.prototype.hasOwnProperty.call(CHAT_ERROR_CODES, code)
}

/** One recorded error of a chat. `id` is this client's (8..200 of [A-Za-z0-9.:-]); the server keys its copy by it. */
export interface ChatErrorRecord {
  id: string
  code: string
  message: string
  occurredAt: string
}

const STORAGE_LIMIT = 50
const MESSAGE_LIMIT = 4000
const ACTIVITY_PREFIX = 'chat-error:'
// The same ordered rule as the server's chat_errors.redact (keep them identical): the line shows this text at once and
// the server's stored text after F5, so both mask with `***`. Applying it twice changes nothing.
const SECRETS: RegExp[] = [
  /(\bbearer\s+)[^\s"']+/gi,
  /\b((?:FLOWGATE_TOKEN|[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|SECRET_KEY|PASSWORD))\s*[:=]\s*)[^\s"']+/gi,
  /(\b(?:authorization|token|access_token|api[_-]?key|password|secret)\b\s*[=:]\s*)(?!bearer\b)[^\s,;&"']+/gi,
]

export function newChatErrorId(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `err-${random}`
}

/** The message without anything that looks like a credential (same rule as the server's record). */
export function redactChatError(text: string | null | undefined): string {
  let value = String(text ?? '')
  for (const pattern of SECRETS) value = value.replace(pattern, (_m, head: string) => `${head}***`)
  return value.slice(0, MESSAGE_LIMIT)
}

/** The timeline line of a recorded error: a status event at the time it happened. */
export function chatErrorActivity(chatId: string, record: ChatErrorRecord): StatusActivity {
  return {
    kind: 'status', id: ACTIVITY_PREFIX + record.id, chatId, createdAt: record.occurredAt,
    tone: CHAT_ERROR_CODES[record.code] ?? 'danger', code: record.code, text: record.message || null, chatError: true,
  }
}

function storageKey(userId: string, chatId: string): string {
  return `flowgate.user.${userId}.chat.errors.${chatId}`
}

function validRecord(value: unknown): value is ChatErrorRecord {
  const r = value as Partial<ChatErrorRecord> | null
  return !!r && typeof r.id === 'string' && typeof r.code === 'string' && isChatErrorCode(r.code) &&
    typeof r.message === 'string' && typeof r.occurredAt === 'string'
}

/** The errors this browser kept for a chat (oldest first); [] when there are none or storage is unavailable. */
export function readLocalChatErrors(userId: string, chatId: string): ChatErrorRecord[] {
  try {
    const raw = localStorage.getItem(storageKey(userId, chatId))
    const list = raw ? JSON.parse(raw) : []
    return Array.isArray(list) ? list.filter(validRecord) : []
  } catch {
    return []
  }
}

/** Keeps one error in this browser (the newest STORAGE_LIMIT per chat). Best effort: false when storage failed. */
export function keepLocalChatError(userId: string, chatId: string, record: ChatErrorRecord): boolean {
  try {
    const list = readLocalChatErrors(userId, chatId).filter((r) => r.id !== record.id)
    list.push({ id: record.id, code: record.code, message: redactChatError(record.message), occurredAt: record.occurredAt })
    localStorage.setItem(storageKey(userId, chatId), JSON.stringify(list.slice(-STORAGE_LIMIT)))
    return true
  } catch {
    return false
  }
}
