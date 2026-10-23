// flowgate.v02.0088 F10/F11 (D0004 §3.1): the server's own diagnostic of a failed request, kept
// apart from the app's localized general text. A network failure, an empty body or a body that
// only repeats the transport's own English line (axios "Request failed with status code …",
// "Network Error") carries no diagnostic, so the screen shows its localized fallback instead.
import { extractApiErrorMessage } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'

/** Whether the response body itself carries `text` (a string anywhere in its first levels). */
export function bodyCarries(value: unknown, text: string, depth = 0): boolean {
  if (typeof value === 'string') return value.trim() === text
  if (depth > 3 || !value || typeof value !== 'object') return false
  return Object.values(value as Record<string, unknown>).some((child) => bodyCarries(child, text, depth + 1))
}

// The message is read the way the shared extractor reads it, and — for the `{ ok: false, error: { code, message } }`
// envelope it leaves out — from the normalized error source, as the card line / memo / AI adapters already do.
// It is told apart by where it came from, not by its wording: a text the response body carries is the
// server's diagnostic even when the thrown Error repeats it; only a text the body does not carry and
// that is the Error's own message is the transport's line.
export function serverDetailOf(error: unknown): string {
  const normalized = normalizeApiError(error)
  if (normalized.network) return ''
  const source = normalized.source as { message?: unknown } | null | undefined
  const sent = typeof source?.message === 'string' ? source.message.trim() : ''
  const detail = extractApiErrorMessage(error, '').trim() || sent
  if (!detail || detail === normalized.code) return ''
  const body = (error as { response?: { data?: unknown } } | null)?.response?.data
  const transport = error instanceof Error ? error.message.trim() : ''
  return detail === transport && !bodyCarries(body, detail) ? '' : detail
}
