import { readonly, ref } from 'vue'

// A 403 from any request lands here. The screen that asked may handle its own refusal; ForbiddenNotice
// (mounted once in the app shell) shows the common "no permission" box for the rest, so a refusal never
// ends in a blank screen or a spinner that never stops.
//
// This file imports nothing from the api layer: shared/api.ts reports into it, and the capability store
// subscribes to it (a refusal means the screen's idea of the person's permissions may be stale).

export interface ForbiddenEvent {
  /** The refused request's path (no query), for the box's detail line. */
  path: string
  /** The server's reason text/code (`detail`), when it sent one. */
  detail: string | null
  at: number
}

/** Request config flag: this call shows its own refusal, the common box is not raised for it. */
export const SUPPRESS_FORBIDDEN_FLAG = 'fgSuppressForbidden'

/**
 * Refusals that are an ordinary answer, not a "you may not": the sign-in endpoints, the permission
 * reads themselves, and the self-check line / chat image checks that hide themselves on 403 by design.
 * They still tell the listeners (the permission answers may be stale) but never raise the box.
 */
const QUIET_PATHS: readonly RegExp[] = [
  /^\/?auth\//,
  /\/capabilities$/,
  /\/my-permissions$/,
  /\/self-check(\/|$)/,
  /\/images\/[^/]+\/content$/,
]

export function isQuietForbiddenPath(path: string): boolean {
  const clean = path.split('?')[0] ?? path
  return QUIET_PATHS.some((pattern) => pattern.test(clean.replace(/^https?:\/\/[^/]+/, '').replace(/^\/flowgate/, '')))
}

const current = ref<ForbiddenEvent | null>(null)
const listeners = new Set<(event: ForbiddenEvent) => void>()

export function onForbidden(listener: (event: ForbiddenEvent) => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function detailText(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && typeof (detail as { code?: unknown }).code === 'string') {
    return (detail as { code: string }).code
  }
  return null
}

/** `silent`: only tell the listeners (capabilities go stale), do not raise the box. */
export function reportForbidden(path: string, detail: unknown, options: { silent?: boolean } = {}): void {
  const event: ForbiddenEvent = { path: path.split('?')[0] ?? path, detail: detailText(detail), at: Date.now() }
  if (!options.silent) current.value = event
  for (const listener of listeners) listener(event)
}

export function dismissForbidden(): void {
  current.value = null
}

export function useForbidden() {
  return { event: readonly(current), dismiss: dismissForbidden }
}
