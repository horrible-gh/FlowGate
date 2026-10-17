// flowgate.v02.0056 F1 — what a slot line shows for its state, decided in one place for every card (0056 CH
// §1 / §5). The 0053 line drew every state as a coloured dot whose meaning lived in its tooltip; since most
// lines sit in a state that asks nothing of the operator ('작성 중', '완료'), the colour carried no
// information. Now a line carries a short text badge only when the state says something: a decision waits
// (warning / danger), something runs or holds (running / hold), the document was revised (info), or it is
// settled (success — drawn dim). A neutral state draws nothing at all. A line without a document says why
// in plain words (the former hollow dot's tooltip) where the badge would be.
// Pure: no Vue, no i18n — the caller hands the worded label, this decides whether and how it is drawn.

/**
 * flowgate.v02.0064 T#2: only these known body states warn (the server's `body_state`); an older server sends none and
 * a documentless line never warns. The same rule as features/document `lineWarns` and the Designer Center's
 * (`center/graph/flowGraph.ts`): Inspector parts import no feature, so they keep their own copy.
 */
export const UNREADABLE_BODY_STATES: ReadonlySet<string> = new Set([
  'file_path_missing', 'file_not_found', 'path_unresolved', 'read_error',
])

export function lineWarns(line: { readonly docId?: string | null; readonly bodyState?: string | null }): boolean {
  return line.docId != null && typeof line.bodyState === 'string' && UNREADABLE_BODY_STATES.has(line.bodyState)
}

export type SlotStateTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

export interface SlotStateInput {
  readonly label: string
  readonly tone: SlotStateTone
  /** More hover text after the label (a reason, "2/4"). */
  readonly sub?: string | null
  readonly title?: string
}

export interface SlotStateView {
  /**
   * badge: a text badge; empty: why the line has no document; blank (flowgate.v02.0056 F2-2): the line's document
   * has no body yet (no text — the row's shape says it; "비어 있음" the hover text); none: nothing is drawn.
   */
  readonly kind: 'badge' | 'empty' | 'blank' | 'none'
  readonly tone: SlotStateTone | 'none'
  /** The text drawn ('' for none). */
  readonly text: string
  /** Hover text: the label with its sub text and title (what the 0053 dot tooltip said). */
  readonly title: string | undefined
  /** The badge says something is settled (success): drawn dim so a waiting badge stands out. */
  readonly quiet: boolean
}

const NONE: SlotStateView = { kind: 'none', tone: 'none', text: '', title: undefined, quiet: false }

/** Tones that say something to the operator; `neutral` is silence. */
export const SLOT_STATE_SHOWN_TONES: readonly SlotStateTone[] = ['info', 'success', 'warning', 'danger', 'running', 'hold']

/** The hover text of a state: its label, then its sub text and title when given. */
export function slotStateTitle(state: SlotStateInput): string {
  const more = [state.sub, state.title].filter((text): text is string => !!text)
  return [state.label, ...more].join(' · ')
}

/**
 * What the line draws for its state. `empty` wins: a line without a document shows `emptyText` (the common
 * wording when the caller gives none) as plain muted text. A state of a shown tone is a badge; `neutral`
 * and no state draw nothing.
 * flowgate.v02.0056 F2-2: a line whose document has no body (`blankText` given: "비어 있음") draws no badge, whatever
 * its state — an empty document asks to be written before anything else.
 * flowgate.v02.0056 (0017-CH) — and no words either: the row shows it by its shape (dashed frame, dim name, hollow
 * document icon); "비어 있음" is only the hover text.
 */
export function slotStateView(
  state: SlotStateInput | null | undefined,
  empty: boolean,
  emptyText: string | null | undefined,
  blankText?: string | null,
): SlotStateView {
  if (empty) {
    const text = emptyText ?? ''
    return { kind: 'empty', tone: 'none', text, title: text || undefined, quiet: false }
  }
  if (blankText) return { kind: 'blank', tone: 'none', text: '', title: blankText, quiet: false }
  if (!state || !SLOT_STATE_SHOWN_TONES.includes(state.tone)) return NONE
  return { kind: 'badge', tone: state.tone, text: state.label, title: slotStateTitle(state), quiet: state.tone === 'success' }
}

/**
 * flowgate.v02.0056 F2-1 (O2 0007-TR §4-3) — who writes a card line, when it is worth saying. Answers the message
 * key (under `gui.designer.inspector.slot.author.`) or null.
 * flowgate.v02.0056 (0017-CH) — no author is drawn: "지시 AI 또는 사람" was words the line does not need.
 */
export const SLOT_AUTHORS_SHOWN: readonly string[] = []

export function slotAuthorKey(author: string | null | undefined): string | null {
  return author && SLOT_AUTHORS_SHOWN.includes(author) ? `gui.designer.inspector.slot.author.${author}` : null
}

// --- flowgate.v02.0056 F2-2 — the three states of a card line and its row actions (O3 0009-TR §4-1 · §4-2 · §4-3) --

/**
 * A card line as the server sends it (the Canvas `card_documents` line; FlowGraphCardDocument): what decides its
 * state and its history number. `hasBody` and the counts are absent when the server did not send them.
 */
export interface SlotCardLine {
  readonly slot: string
  readonly docType: string | null
  readonly docId: string | null
  readonly hasBody?: boolean
  readonly bodyState?: string | null
  readonly reviewCount?: number
  readonly rejectionCount?: number
  readonly historyCount?: number
}

/** missing: no document ("문서 없음" + [작성]); blank: a document without a body ("비어 있음", opens in its editor); filled: a usual line. */
export type SlotFill = 'missing' | 'blank' | 'filled'

/**
 * Line types whose `has_body` is always true (O3 §4-1): M (전개 · AI 위임 records — auto-completing by the v0.1
 * approval rule) and WP (an empty plan still has its JSON body). Such a line is never "비어 있음"; it is decided
 * here by type too, so an older answer cannot draw one blank either.
 */
export const SLOT_ALWAYS_FILLED_TYPES: readonly string[] = ['M', 'WP']

/**
 * The state of a card line: no document -> missing; a document the server says has no body -> blank (never an M
 * or WP line); else filled. A line whose `hasBody` was not sent is filled (nothing is claimed).
 */
export function slotFill(line: SlotCardLine | null | undefined): SlotFill {
  if (!line || line.docId === null) return 'missing'
  if (SLOT_ALWAYS_FILLED_TYPES.includes(line.docType?.trim().toUpperCase() ?? '')) return 'filled'
  return line.hasBody === false ? 'blank' : 'filled'
}

/**
 * Card line slots [작성] writes (O3 §4-2 `slot`). The test card's 시나리오 line shares its 지시서's document
 * (422 slot_shares_a_document), so it is written through the 지시서 line.
 */
export const SLOT_WRITABLE_SLOTS: readonly string[] = ['requirement', 'delegation', 'plan', 'deploy', 'instruction', 'report']

/** A row action of a line (the docRowActions list): [작성] of a line without a document, [AI 호출] of a line with one. */
export type SlotActionKey = 'write' | 'ai_invoke'

export interface SlotAction {
  readonly key: SlotActionKey
  /** running: the action's work goes on (an AI run on the line's document) — drawn with its running word, not pressable. */
  readonly state: 'enabled' | 'running'
}

/** What the card adds to one of its lines (spread into the line the card feature hands the slot list). */
export interface SlotLineCard {
  /** The card line behind the row (its slot), or null when the row shows no card line. */
  readonly cardSlot: string | null
  readonly bodyState?: string | null
  /** The row's document has no body yet: "비어 있음", and opening it opens its editor. */
  readonly blank: boolean
  /** The history button's number (`history_count`), or null when not sent. */
  readonly historyCount: number | null
  /** The two numbers behind it (AI reviews · rejections) for the history button's hover text, when both were sent. */
  readonly historyDetail: { readonly reviews: number; readonly rejections: number } | null
  readonly actions: readonly SlotAction[]
}

export interface SlotLineCardContext {
  /** The card is a stored card the user may change (not a `tmp_` card, not read only). */
  readonly changeable: boolean
  /** An AI run works on the document now (the single AI call, or the group's live run on it). */
  readonly running: (docId: string) => boolean
}

const NO_CARD: SlotLineCard = { cardSlot: null, blank: false, historyCount: null, historyDetail: null, actions: [] }

/**
 * What a card line adds to the row that shows it: the row `line` (its document and whether it is drawn empty) and
 * the card's own line for that row, if any.
 * - The row shows no document and the card line has none (missing): [작성] while the card may change and the slot
 *   is one [작성] writes.
 * - The row shows the card line's document: "비어 있음" when it has no body (blank), the history number, and
 *   [AI 호출] while the card may change — drawn running while an AI run works on the document.
 * - Any other row (a document the card line does not hold, no card line) gets nothing.
 */
export function slotLineCard(
  line: { readonly id?: string | null; readonly empty?: boolean },
  card: SlotCardLine | null | undefined,
  context: SlotLineCardContext,
): SlotLineCard {
  if (!card) return NO_CARD
  const id = line.id ?? null
  if (card.docId === null) {
    if (id !== null || !line.empty) return NO_CARD
    const write = context.changeable && SLOT_WRITABLE_SLOTS.includes(card.slot)
    return { ...NO_CARD, cardSlot: card.slot, actions: write ? [{ key: 'write', state: 'enabled' }] : [] }
  }
  if (id !== card.docId) return NO_CARD
  const historyDetail =
    card.reviewCount !== undefined && card.rejectionCount !== undefined ? { reviews: card.reviewCount, rejections: card.rejectionCount } : null
  return {
    cardSlot: card.slot,
    ...(card.bodyState !== undefined ? { bodyState: card.bodyState } : {}),
    blank: !lineWarns(card) && slotFill(card) === 'blank',
    historyCount: card.historyCount ?? null,
    historyDetail,
    actions: context.changeable ? [{ key: 'ai_invoke', state: context.running(card.docId) ? 'running' : 'enabled' }] : [],
  }
}
