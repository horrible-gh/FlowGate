// A request is about to leave. The api layer reports it here and the capability store listens: this file imports
// nothing from either side, so shared/api.ts and the store need not import each other (the same shape as forbidden.ts).
//
// Why it exists: the server does not send a permission version with its answers, so the screen cannot tell that
// someone changed the person's role. The next request the person makes after the answers have grown old is the
// moment to ask again — a button that is off for a refused action can never start that itself.

const listeners = new Set<(path: string) => void>()

export function onRequestLeaving(listener: (path: string) => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** Called by the api layer for every request it sends (path without query). */
export function reportRequestLeaving(path: string): void {
  for (const listener of listeners) listener(path)
}
