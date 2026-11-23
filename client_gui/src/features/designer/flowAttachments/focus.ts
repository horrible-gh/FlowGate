// Go to one attachment of a Flow and point at it (0074 · item 3). Public entry for other screens — the chat's file
// names and file chips (card "6-화면") call `focusAttachment(flowId, attachmentId)`:
//
//   import { focusAttachment } from '@/features/designer/flowAttachments/focus'
//   const shown = await focusAttachment(flowId, attachmentKey)
//
// - flowId: the Flow (group id) whose attachment window to use.
// - attachmentId: the attachment key (`FlowFile.key`, the `<key>` of `fg-attachment:<key>` links).
// - Opens that Flow's window (an open one is brought to the front), drops the origin filter / search when they hide
//   the file, scrolls its row into view and lights it green for FOCUS_HIGHLIGHT_MS.
// - Resolves true when the row was shown. Resolves false — and changes nothing else — when the file is not in the
//   Flow's list, when no Designer attachment Host is mounted, or when the Flow is neither the Designer's open Flow nor
//   one with a window already open (a window needs the Flow's Project, which only those two know). Never rejects.
export const FOCUS_HIGHLIGHT_MS = 2000

export type AttachmentFocusHandler = (flowId: string, attachmentId: string) => Promise<boolean>

let handler: AttachmentFocusHandler | null = null

/** The Designer's attachment Host registers itself while mounted. Returns the unregister function. */
export function registerAttachmentFocus(next: AttachmentFocusHandler): () => void {
  handler = next
  return () => { if (handler === next) handler = null }
}

export async function focusAttachment(flowId: string, attachmentId: string): Promise<boolean> {
  const current = handler
  if (!current || !flowId || !attachmentId) return false
  try {
    return (await current(flowId, attachmentId)) === true
  } catch {
    return false
  }
}
