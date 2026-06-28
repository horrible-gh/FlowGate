// Clipboard helpers (B0001 / group 0133 — "가끔 클립보드에 멘트가 복사되지 않음 / 잘림 / 공백").
//
// Root cause (NR0003): the mention-copy handlers issue a token over the network
// (`await issueToken(...)`) BETWEEN the user's click and `navigator.clipboard.writeText()`.
// The Clipboard API requires transient user activation; that activation can lapse across the
// awaited round-trip (or the document can lose focus), so the write intermittently rejects
// with NotAllowedError. The old `doClipboardCopy` swallowed that rejection and the callers
// reported success regardless — so the user pasted whatever was on the clipboard before
// (stale = "truncated", empty = "blank", unchanged = "nothing copied").
//
// Two primitives fix that:
//   • copyToClipboard(text)         — honest write of a ready string (focus-retry + execCommand
//                                     fallback), returns whether the clipboard was actually set.
//   • copyToClipboardDeferred(fn)   — preserves the click's activation while `fn` resolves the
//                                     text asynchronously, by handing a text Promise to a
//                                     ClipboardItem at gesture time (no await before the write).

/**
 * Sentinel a deferred producer can throw to abort the copy WITHOUT surfacing a
 * "copy failed" warning — e.g. token issuance already failed and showed its own toast,
 * or there is genuinely nothing to copy. `copyToClipboardDeferred` resolves to `false`.
 */
export class ClipboardAbort extends Error {
  constructor(message = 'clipboard copy aborted') {
    super(message)
    this.name = 'ClipboardAbort'
  }
}

function legacyExecCopy(text: string): boolean {
  if (typeof document === 'undefined') return false
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    return ok
  } catch {
    return false
  }
}

/**
 * Write a ready-built string to the clipboard, reporting honestly whether it landed.
 *
 * Order: `clipboard.writeText` → on failure re-focus the window and retry once (the most
 * common intermittent cause is the document not being focused at write time) → legacy
 * `execCommand('copy')` fallback. Returns `false` only when every path failed, so callers
 * can warn the user instead of falsely claiming success.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Most often "Document is not focused" / lapsed activation. Re-focus and retry once.
      try {
        if (typeof window !== 'undefined') window.focus?.()
        await navigator.clipboard.writeText(text)
        return true
      } catch {
        /* fall through to the legacy path */
      }
    }
  }
  return legacyExecCopy(text)
}

/**
 * Copy text that is produced asynchronously (e.g. it needs a freshly issued token) WITHOUT
 * losing the user activation that the originating click granted.
 *
 * When the async Clipboard API is available, a ClipboardItem is built with a text/plain
 * Promise and `clipboard.write` is invoked synchronously — before any `await` — so the
 * browser keeps the gesture's activation alive while `produce()` resolves. Where ClipboardItem
 * is unavailable (older engines, jsdom in tests) it degrades to awaiting the text and using
 * `copyToClipboard`. A `ClipboardAbort` thrown by `produce` resolves to `false` quietly.
 */
export async function copyToClipboardDeferred(produce: () => Promise<string>): Promise<boolean> {
  const hasAsyncClipboard =
    typeof navigator !== 'undefined' &&
    !!navigator.clipboard?.write &&
    typeof ClipboardItem !== 'undefined'

  if (hasAsyncClipboard) {
    let aborted = false
    // Build the text Promise synchronously so the ClipboardItem is constructed inside the
    // gesture. Swallow ClipboardAbort into an empty blob (we detect it via `aborted` after).
    const blobPromise = produce()
      .then((text) => new Blob([text], { type: 'text/plain' }))
      .catch((e) => {
        if (e instanceof ClipboardAbort) aborted = true
        throw e
      })
    try {
      const item = new ClipboardItem({ 'text/plain': blobPromise })
      await navigator.clipboard.write([item])
      return true
    } catch {
      if (aborted) return false
      // The async write failed (activation/focus). Try once more with the resolved text via
      // the focus-retry path — the text Promise has already settled by now.
      try {
        const blob = await blobPromise
        const text = await blob.text()
        return await copyToClipboard(text)
      } catch (e) {
        if (e instanceof ClipboardAbort) return false
        return false
      }
    }
  }

  // No ClipboardItem support: resolve the text first, then do an honest write.
  let text: string
  try {
    text = await produce()
  } catch (e) {
    if (e instanceof ClipboardAbort) return false
    return false
  }
  return copyToClipboard(text)
}
