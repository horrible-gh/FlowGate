// The quick message markers inside a sent chat message body (flowgate.v02test.0072 card 4, R rev 3 "보낸 메시지 표시").
//
// The composer wraps every quick message that goes with a send in an HTML comment pair; the timeline reads the same
// pair back to show only the typed text plus the small titles. The send path (conversationSession.ts /
// conversation_turn_service.py) is not touched: the markers are plain text of the body, so the AI reads the quick
// message text exactly as it was written, and the server stores it as is (it only does NFC, CRLF -> LF, trims line
// ends and the whole body; it never strips HTML comments).
//
//   요약해 줘
//
//   <!--fg-quick kind="sticky" title="Dev2확인"-->
//   (the quick message text, unchanged)
//   <!--/fg-quick-->
//
//   <!--fg-quick kind="once" title="반려정리"-->
//   ...
//   <!--/fg-quick-->
//
// Rules (build and parse live here together so the two never drift apart):
// - kind is "sticky" (계속) or "once" (1회). The blocks follow the typed text in row order, separated by a blank line;
//   with nothing typed the body starts with the first block.
// - The title is escaped as HTML character references: & " < > and line breaks become &amp; &quot; &lt; &gt; &#10;
//   (&#13;), so a title can never close the attribute or the comment ("-->" becomes "--&gt;").
// - The quick message text is not escaped or changed. A text that itself contains a marker cannot be wrapped
//   safely; such a body simply reads as broken below and is shown as written.
// - Parsing is all-or-nothing: the body must be "text, then only well-formed blocks to the end". Anything else — no
//   marker, an unknown kind, a missing close, text after the blocks, a stray marker in the typed text or in a quick
//   message — gives null, and the bubble shows the body as written (old messages, card 2 messages, broken markers).

import type { QuickMessageKind } from './api'

export interface QuickMarkupItem {
  readonly kind: QuickMessageKind
  readonly title: string
  readonly message: string
}

export interface QuickMarkupBody {
  /** The typed text without the quick message blocks ('' when only quick messages were sent). */
  readonly text: string
  /** The quick messages that went with the message, in row order (never empty). */
  readonly quick: readonly QuickMarkupItem[]
}

const OPEN_PREFIX = '<!--fg-quick '
const CLOSE = '<!--/fg-quick-->'
const OPEN_AT_LINE_START = /(^|\n)<!--fg-quick /
const BLOCK = /^<!--fg-quick kind="(sticky|once)" title="([^"\n]*)"-->\n([\s\S]*?)\n<!--\/fg-quick-->(?:\n\s*|$)/

export function escapeQuickTitle(title: string): string {
  return title
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\r/g, '&#13;')
    .replace(/\n/g, '&#10;')
}

export function unescapeQuickTitle(value: string): string {
  return value.replace(/&(amp|quot|lt|gt|#10|#13);/g, (_, name: string) =>
    name === 'amp' ? '&' : name === 'quot' ? '"' : name === 'lt' ? '<' : name === 'gt' ? '>' : name === '#10' ? '\n' : '\r')
}

/** One quick message as its marker block. */
export function quickBlock(item: QuickMarkupItem): string {
  return `<!--fg-quick kind="${item.kind}" title="${escapeQuickTitle(item.title)}"-->\n${item.message}\n${CLOSE}`
}

/**
 * The outgoing chat text: the typed text (trimmed), then each quick message in its marker block, in the given (row)
 * order. Blank parts are left out; without quick messages this is just the trimmed text.
 */
export function buildQuickBody(text: string, items: readonly QuickMarkupItem[]): string {
  const blocks = items.filter((item) => item.message.trim()).map(quickBlock)
  return [text.trim(), ...blocks].filter(Boolean).join('\n\n')
}

/** The typed text and the quick messages of a sent body; null when it has no (well-formed) markers. */
export function parseQuickBody(body: string | null | undefined): QuickMarkupBody | null {
  if (!body || !body.includes(OPEN_PREFIX)) return null
  const value = body.replace(/\r\n?/g, '\n')
  const start = value.search(OPEN_AT_LINE_START)
  if (start < 0) return null
  const at = value[start] === '\n' ? start + 1 : start
  const text = value.slice(0, at).trim()
  if (text.includes(OPEN_PREFIX) || text.includes(CLOSE)) return null
  const quick: QuickMarkupItem[] = []
  let rest = value.slice(at)
  while (rest) {
    const match = BLOCK.exec(rest)
    if (!match) return null
    const message = match[3]
    if (!message.trim() || message.includes(OPEN_PREFIX) || message.includes(CLOSE)) return null
    quick.push({ kind: match[1] as QuickMessageKind, title: unescapeQuickTitle(match[2]), message })
    rest = rest.slice(match[0].length)
  }
  return quick.length ? { text, quick } : null
}
