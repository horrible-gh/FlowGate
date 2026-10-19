// flowgate.v02.0072 T#1 — the rules of the v0.2 work plan edit window, with no screen and no server call. New for
// v0.2 (the v0.1 WorkPlanEditor, client/src/main/components/WorkPlanEditor.vue, is the reference for what the editor
// does, not code taken over): the quantity boxes, the Steps drawn by card (a single Step, or a set's instruction with
// its result), the per-Step AI and one-line note, the plan default AI / note and [apply to all], and card moves.
//
// The server's plan body (wp_version 2) is the one source: the read answer's body is kept whole and goes back to
// `PUT …/work-plan` with only what the person changed — every field this window has no control for (provider
// candidates, top-level x_* extensions, a Step's review / pre-instruction) travels untouched. What the server
// checks is not decided here (quantities ⇔ Steps, card ids, providers, review counts — work_plan_service.validate);
// the window keeps the body in a shape the server takes (a quantity change edits that type's cards in place and
// renumbers keys / ordinals / pair keys by card order, like the server's expand_steps and _structure_errors expect).
//
// `defaults.provider_id` is only the value [apply to all] copies onto the Steps: it is never read as a Step's AI
// (the server's apply reads the Step's provider only — NR §2), so nothing here falls back to it.
//
// Rows are named by an identity that never changes while the window is open: the card's card_id, or a temporary
// id for a card the server has not named yet, plus the row's role (`main` | `result`). A Step's key (`T#1`) is
// renumbered whenever a card moves, so it never names a row (0649 T#3). The Step window (T#2) and the review popover
// (0073 T#2) edit a row through this identity (stepDraftOf / applyStepDraft, setStepReview).
//
// flowgate.v02.0073 T#1 — the window does not show the DS and T2 types (WORK_PLAN_HIDDEN_TYPES): no quantity box, no
// card, not in the shown totals. That is a filter of what is drawn only: their quantities, counted types and Steps stay
// in the body and go back whole, [apply to all] still reaches their unlocked Steps, and a card move among the shown
// cards leaves every hidden card in its slot (moveShownCard). A type's icon is the Designer Palette's (workPlanTypeIcon).
//
// flowgate.v02.0073 T#2 — a row's review is set in one place, the main window's review popover (setStepReview); the
// Step window edits the pre-instruction and its attachment only (WorkPlanStepDraft has no review), so neither can
// write over what the other set.
import type { SelectOption } from '@/components/ui/Select.vue'
import { DEFAULT_PALETTE_ITEMS } from '@/features/designer/palette/paletteCatalog'

// --- Server constants (documents/constants.py, work_plan_service.py) -----------------------------------

/** Types counted in sheets (one single Step each). */
export const WORK_PLAN_SHEET_TYPES: readonly string[] = ['DS', 'D', 'P', 'L', 'DB']
/** Instruction types counted in sets (an instruction Step and its result Step). */
export const WORK_PLAN_SET_TYPES: readonly string[] = ['N', 'T', 'T2', 'TS']
/** instruction → result (WORK_PLAN_PAIR_MAP). */
export const WORK_PLAN_PAIR: Readonly<Record<string, string>> = { N: 'NR', T: 'TR', T2: 'TR2', TS: 'TSR' }
/** Steps the server assembles itself: no AI, no note, no review, no pre-instruction (WORK_PLAN_LOCKED_TYPES). */
export const WORK_PLAN_LOCKED_TYPES: ReadonlySet<string> = new Set(['TSR'])
export const WORK_PLAN_COUNT_MIN = 0
export const WORK_PLAN_COUNT_MAX = 20

/** Types the window does not draw (0073 T#1); a T2 card carries its TR2 result with it. Display only. */
export const WORK_PLAN_HIDDEN_TYPES: ReadonlySet<string> = new Set(['DS', 'T2'])

/** The instruction type of a result type (TR → T), else the type itself. */
function instructionTypeOf(type: string): string {
  return Object.keys(WORK_PLAN_PAIR).find((instruction) => WORK_PLAN_PAIR[instruction] === type) ?? type
}

/** The window draws Steps of this type (a result follows its instruction). */
export function typeShown(type: string): boolean {
  return !WORK_PLAN_HIDDEN_TYPES.has(instructionTypeOf(type))
}

// --- The body ------------------------------------------------------------------------------------------

export type WorkPlanPairRole = 'instruction' | 'result' | 'single'

/** A Step of the body. Fields this window does not know stay on the object as they came. */
export interface WorkPlanEditStep {
  key: string
  card_id?: string | null
  type: string
  ordinal: number
  pair_key: string | null
  pair_role: WorkPlanPairRole
  provider_id: string | null
  provider_display_name: string | null
  note: string | null
  review_count: number
  reviewer_provider_id: string | null
  reviewer_provider_display_name: string | null
  pre_instruction_text: string | null
  pre_instruction_attachment: Record<string, unknown> | null
  locked: boolean
  locked_reason: string | null
  origin: string
  [field: string]: unknown
}

export interface WorkPlanEditQuantity {
  unit: 'sheet' | 'set'
  count: number
}

export interface WorkPlanEditBody {
  wp_version: number
  binding: string
  counted_types: string[]
  quantities: Record<string, WorkPlanEditQuantity>
  provider_candidates: Array<Record<string, unknown>>
  defaults: { provider_id: string | null; note: string; [field: string]: unknown }
  steps: WorkPlanEditStep[]
  [field: string]: unknown
}

/** A card: one single Step, or an instruction Step followed by its result Step. `id` never changes in a session. */
export interface WorkPlanCard {
  readonly id: string
  readonly steps: readonly WorkPlanEditStep[]
}

/** A card removed by a quantity decrease in this session, kept so raising the quantity brings it back. */
interface RemovedCard {
  readonly card: WorkPlanCard
  readonly index: number
}

/** What the window edits: the body (steps = the cards in order) and the session's removed cards. */
export interface WorkPlanDraft {
  readonly body: WorkPlanEditBody
  readonly cards: readonly WorkPlanCard[]
  /** Per type, the cards a decrease removed (last removed last); cleared by a read or a save. */
  readonly removed: Readonly<Record<string, readonly RemovedCard[]>>
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T

let tempSeq = 0
/** A temporary card id for a card the server has not named (never sent: the body carries no card_id for it). */
function tempCardId(): string {
  tempSeq += 1
  return `tmp:${tempSeq}`
}

/** Groups Steps into cards in body order (an instruction immediately followed by its result is one card). */
export function groupSteps(steps: readonly WorkPlanEditStep[]): WorkPlanEditStep[][] {
  const out: WorkPlanEditStep[][] = []
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i]
    const next = steps[i + 1]
    if (step.pair_role === 'instruction' && next && next.pair_role === 'result') {
      out.push([step, next])
      i += 1
    } else out.push([step])
  }
  return out
}

/**
 * The draft of a body read from the server (a copy: the read answer is never edited in place). A card of a legacy
 * body without card ids is named after its head Step's key as read (the server's legacy_card_id), so reading the same
 * body again gives the same row ids; the name stays on the window's side and is never sent.
 */
export function draftOf(body: WorkPlanEditBody): WorkPlanDraft {
  const copy = clone(body)
  const cards = groupSteps(copy.steps).map((steps) => ({ id: steps[0].card_id || `legacy:${steps[0].key}`, steps }))
  return { body: copy, cards, removed: {} }
}

/** The body as it is sent: a copy with the Steps of the cards in order. */
export function bodyOf(draft: WorkPlanDraft): WorkPlanEditBody {
  return clone({ ...draft.body, steps: draft.cards.flatMap((card) => card.steps) })
}

function withCards(draft: WorkPlanDraft, cards: WorkPlanCard[], patch: Partial<WorkPlanDraft> = {}): WorkPlanDraft {
  const renumbered = renumber(cards)
  return { ...draft, ...patch, cards: renumbered, body: { ...(patch.body ?? draft.body), steps: renumbered.flatMap((card) => card.steps) } }
}

const makeKey = (type: string, ordinal: number) => `${type}#${ordinal}`

/** Ordinals restart per type and follow the card order; keys and pair keys follow (server _structure_errors). */
export function renumber(cards: readonly WorkPlanCard[]): WorkPlanCard[] {
  const seen = new Map<string, number>()
  return cards.map((card) => {
    const head = card.steps[0]
    const n = (seen.get(head.type) ?? 0) + 1
    seen.set(head.type, n)
    if (card.steps.length === 1) return { id: card.id, steps: [{ ...head, ordinal: n, key: makeKey(head.type, n) }] }
    const result = card.steps[1]
    const headKey = makeKey(head.type, n)
    const resultKey = makeKey(result.type, n)
    return {
      id: card.id,
      steps: [
        { ...head, ordinal: n, key: headKey, pair_key: resultKey },
        { ...result, ordinal: n, key: resultKey, pair_key: headKey },
      ],
    }
  })
}

// --- Rows ---------------------------------------------------------------------------------------------

export type WorkPlanRowRole = 'main' | 'result'

/** The identity of one row: `<card id>|main` or `<card id>|result`. */
export const workPlanRowId = (cardId: string, role: WorkPlanRowRole) => `${cardId}|${role}`

export function rowRoleOf(step: Pick<WorkPlanEditStep, 'pair_role'>): WorkPlanRowRole {
  return step.pair_role === 'result' ? 'result' : 'main'
}

export interface WorkPlanRowRef {
  readonly rowId: string
  readonly cardIndex: number
  readonly stepIndex: number
  readonly step: WorkPlanEditStep
  /** 1-based position in the whole plan. */
  readonly no: number
}

/** Every row in plan order. */
export function rowsOf(draft: WorkPlanDraft): WorkPlanRowRef[] {
  const rows: WorkPlanRowRef[] = []
  draft.cards.forEach((card, cardIndex) => {
    card.steps.forEach((step, stepIndex) => {
      rows.push({ rowId: workPlanRowId(card.id, rowRoleOf(step)), cardIndex, stepIndex, step, no: rows.length + 1 })
    })
  })
  return rows
}

export function rowOf(draft: WorkPlanDraft, rowId: string): WorkPlanRowRef | null {
  return rowsOf(draft).find((row) => row.rowId === rowId) ?? null
}

function replaceStep(draft: WorkPlanDraft, row: WorkPlanRowRef, step: WorkPlanEditStep): WorkPlanDraft {
  const cards = draft.cards.map((card, index) =>
    index === row.cardIndex ? { id: card.id, steps: card.steps.map((item, i) => (i === row.stepIndex ? step : item)) } : card,
  )
  return { ...draft, cards, body: { ...draft.body, steps: cards.flatMap((card) => card.steps) } }
}

/** A Step carries something a person set (v0.1 hasValue): its removal by a quantity decrease is confirmed first. */
export function stepHasValue(step: WorkPlanEditStep): boolean {
  if (step.locked) return false
  return !!step.provider_id
    || (step.note ?? '').trim() !== ''
    || (step.review_count ?? 0) !== 0
    || !!step.reviewer_provider_id
    || (step.pre_instruction_text ?? '').trim() !== ''
    || !!step.pre_instruction_attachment
}

/** A result Step (a report) takes no pre-instruction; a locked Step takes nothing. */
export function instructionEligible(step: Pick<WorkPlanEditStep, 'pair_role' | 'locked'>): boolean {
  return !step.locked && step.pair_role !== 'result'
}

export function stepHasInstruction(step: WorkPlanEditStep): boolean {
  return instructionEligible(step) && ((step.pre_instruction_text ?? '').trim() !== '' || !!step.pre_instruction_attachment)
}

// --- Quantities ---------------------------------------------------------------------------------------

export interface WorkPlanQuantityBox {
  readonly type: string
  readonly unit: 'sheet' | 'set'
  readonly count: number
}

/** The plan's unit of a type: the server's fixed one for a known type, else what the body says. */
export function unitOf(body: WorkPlanEditBody, type: string): 'sheet' | 'set' {
  if (WORK_PLAN_SET_TYPES.includes(type)) return 'set'
  if (WORK_PLAN_SHEET_TYPES.includes(type)) return 'sheet'
  return body.quantities[type]?.unit === 'set' ? 'set' : 'sheet'
}

/**
 * The boxes shown: the body's counted types in its order, then the known types it does not count yet (0) — without
 * the hidden types (0073 T#1), whose quantities stay in the body as they are.
 */
export function quantityBoxes(body: WorkPlanEditBody): WorkPlanQuantityBox[] {
  const order = [...body.counted_types]
  for (const type of [...WORK_PLAN_SHEET_TYPES, ...WORK_PLAN_SET_TYPES]) if (!order.includes(type)) order.push(type)
  return order.filter(typeShown).map((type) => ({ type, unit: unitOf(body, type), count: body.quantities[type]?.count ?? 0 }))
}

export interface WorkPlanQuantityPlan {
  /** ok: apply it; unchanged: nothing to do; blocked_started: only started cards are left to remove. */
  readonly status: 'ok' | 'unchanged' | 'blocked_started' | 'unsupported'
  readonly count: number
  /** Steps with values the change removes (a confirmation first when > 0). */
  readonly valueSteps: number
}

function cardStarted(card: WorkPlanCard, started: ReadonlySet<string>): boolean {
  return started.has(card.id)
}

function removalFor(draft: WorkPlanDraft, type: string, count: number, started: ReadonlySet<string>): WorkPlanCard[] | null {
  const ofType = draft.cards.filter((card) => card.steps[0].type === type)
  if (count >= ofType.length) return []
  const removable = ofType.filter((card) => !cardStarted(card, started))
  const need = ofType.length - count
  if (removable.length < need) return null
  return removable.slice(removable.length - need)
}

/** What setting the type's quantity to `next` would do (clamped to 0..20), before it is done. */
export function planQuantity(draft: WorkPlanDraft, type: string, next: number, started: ReadonlySet<string>): WorkPlanQuantityPlan {
  const count = Math.max(WORK_PLAN_COUNT_MIN, Math.min(WORK_PLAN_COUNT_MAX, Math.trunc(next)))
  const current = draft.body.quantities[type]?.count ?? 0
  if (count === current) return { status: 'unchanged', count, valueSteps: 0 }
  if (unitOf(draft.body, type) === 'set' && !WORK_PLAN_PAIR[type]) return { status: 'unsupported', count, valueSteps: 0 }
  const removed = removalFor(draft, type, count, started)
  if (removed === null) return { status: 'blocked_started', count, valueSteps: 0 }
  return { status: 'ok', count, valueSteps: removed.flatMap((card) => card.steps).filter(stepHasValue).length }
}

function blankStep(type: string, pairRole: WorkPlanPairRole): WorkPlanEditStep {
  const locked = WORK_PLAN_LOCKED_TYPES.has(type)
  return {
    key: makeKey(type, 0),
    type,
    ordinal: 0,
    pair_key: null,
    pair_role: pairRole,
    provider_id: null,
    provider_display_name: null,
    note: null,
    review_count: 0,
    reviewer_provider_id: null,
    reviewer_provider_display_name: null,
    pre_instruction_text: null,
    pre_instruction_attachment: null,
    locked,
    locked_reason: locked ? 'server_assembled' : null,
    origin: locked ? 'system' : 'human',
  }
}

/** A new card of the type: no card_id (the server assigns one on save) and no AI — never a default filled in. */
function newCard(body: WorkPlanEditBody, type: string): WorkPlanCard {
  if (unitOf(body, type) === 'sheet') return { id: tempCardId(), steps: [blankStep(type, 'single')] }
  return { id: tempCardId(), steps: [blankStep(type, 'instruction'), blankStep(WORK_PLAN_PAIR[type], 'result')] }
}

/**
 * Sets the type's quantity (v0.1 setQuantity, 0649 T#3): only that type's cards change and the order a person made
 * stays. Growing brings back the cards removed in this session first (their id and values, at their place but never in
 * front of a started card), then appends new cards at the end; shrinking removes the type's last cards that have not
 * started. A type the body does not count yet joins counted_types / quantities. Call planQuantity first.
 */
export function applyQuantity(draft: WorkPlanDraft, type: string, next: number, started: ReadonlySet<string>): WorkPlanDraft {
  const plan = planQuantity(draft, type, next, started)
  if (plan.status !== 'ok') return draft
  const count = plan.count
  const removedNow = removalFor(draft, type, count, started) ?? []
  let cards = [...draft.cards]
  const stack = [...(draft.removed[type] ?? [])]
  for (const card of [...removedNow].reverse()) {
    const index = cards.findIndex((item) => item.id === card.id)
    stack.push({ card, index })
    cards.splice(index, 1)
  }
  const lastStarted = () => {
    for (let i = cards.length - 1; i >= 0; i--) if (cardStarted(cards[i], started)) return i
    return -1
  }
  let have = cards.filter((card) => card.steps[0].type === type).length
  while (have < count) {
    const restored = stack.pop()
    if (restored) cards.splice(Math.max(lastStarted() + 1, Math.min(restored.index, cards.length)), 0, restored.card)
    else cards.push(newCard(draft.body, type))
    have += 1
  }
  const body = { ...draft.body }
  if (!body.counted_types.includes(type)) body.counted_types = [...body.counted_types, type]
  body.quantities = { ...body.quantities, [type]: { unit: unitOf(draft.body, type), count } }
  return withCards(draft, cards, { body, removed: { ...draft.removed, [type]: stack } })
}

// --- Card moves (0649 T#3, NR0003 O7) ---------------------------------------------------------------------

/** Index of the last started card, or -1: nothing may be placed at or before it. */
export function lastStartedIndex(cards: readonly WorkPlanCard[], started: ReadonlySet<string>): number {
  for (let i = cards.length - 1; i >= 0; i--) if (cardStarted(cards[i], started)) return i
  return -1
}

export type WorkPlanMoveResult = { readonly status: 'moved'; readonly draft: WorkPlanDraft } | { readonly status: 'started' | 'before_started' | 'unchanged' }

/** Moves the card at `from` so it lands at index `to` of the list without it. */
export function moveCard(draft: WorkPlanDraft, from: number, to: number, started: ReadonlySet<string>): WorkPlanMoveResult {
  const moving = draft.cards[from]
  if (!moving) return { status: 'unchanged' }
  if (cardStarted(moving, started)) return { status: 'started' }
  const rest = draft.cards.filter((_, index) => index !== from)
  const target = Math.max(0, Math.min(rest.length, to))
  if (target <= lastStartedIndex(rest, started)) return { status: 'before_started' }
  if (target === from) return { status: 'unchanged' }
  rest.splice(target, 0, moving)
  return { status: 'moved', draft: withCards(draft, rest) }
}

/** The ▲ (-1) / ▼ (+1) of a card can be pressed. */
export function canMoveCardBy(draft: WorkPlanDraft, index: number, delta: number, started: ReadonlySet<string>): boolean {
  const card = draft.cards[index]
  const to = index + delta
  if (!card || cardStarted(card, started) || to < 0 || to >= draft.cards.length) return false
  const rest = draft.cards.filter((_, i) => i !== index)
  return to > lastStartedIndex(rest, started)
}

// --- The shown cards (0073 T#1) ------------------------------------------------------------------------------

/** The window draws this card (its head Step's type is not hidden). */
export function cardShown(card: WorkPlanCard): boolean {
  return typeShown(card.steps[0].type)
}

/** A card the window draws: `index` is its place in draft.cards, `position` its place among the shown cards. */
export interface WorkPlanShownCard {
  readonly card: WorkPlanCard
  readonly index: number
  readonly position: number
  /** Its rows; `no` counts the shown rows only (1-based). */
  readonly rows: readonly WorkPlanRowRef[]
}

/** The shown cards in plan order with their rows numbered among the shown rows. */
export function shownCardsOf(draft: WorkPlanDraft): WorkPlanShownCard[] {
  const out: WorkPlanShownCard[] = []
  let no = 0
  draft.cards.forEach((card, index) => {
    if (!cardShown(card)) return
    const rows = card.steps.map((step, stepIndex) => {
      no += 1
      return { rowId: workPlanRowId(card.id, rowRoleOf(step)), cardIndex: index, stepIndex, step, no }
    })
    out.push({ card, index, position: out.length, rows })
  })
  return out
}

/** Row ids of the cards the window does not draw. */
export function hiddenRowIds(draft: WorkPlanDraft): Set<string> {
  return new Set(rowsOf(draft).filter((row) => !cardShown(draft.cards[row.cardIndex])).map((row) => row.rowId))
}

/** A Step key (a server finding's) names a Step the window draws; an unknown key is judged by its type when given. */
export function stepKeyShown(draft: WorkPlanDraft, key: string, type: string | null = null): boolean {
  const row = rowsOf(draft).find((item) => item.step.key === key)
  if (row) return cardShown(draft.cards[row.cardIndex])
  return type === null || typeShown(type)
}

/**
 * Started cards stay pinned: the moved card lands after every other started card, and no other card changes sides
 * with a started one (that can only happen around a hidden card, which keeps its slot while the shown cards shift).
 */
function keepsStarted(before: readonly WorkPlanCard[], after: readonly WorkPlanCard[], moving: WorkPlanCard, started: ReadonlySet<string>): boolean {
  const rest = after.filter((card) => card !== moving)
  if (after.indexOf(moving) <= lastStartedIndex(rest, started)) return false
  const was = new Map(before.map((card, index) => [card, index]))
  const now = new Map(after.map((card, index) => [card, index]))
  for (const pinned of after) {
    if (!cardStarted(pinned, started)) continue
    for (const card of after) {
      if (card === pinned || card === moving) continue
      if ((was.get(pinned)! < was.get(card)!) !== (now.get(pinned)! < now.get(card)!)) return false
    }
  }
  return true
}

type ShownMovePlan = { readonly status: 'moved'; readonly cards: WorkPlanCard[] } | { readonly status: 'started' | 'before_started' | 'unchanged' }

function planShownMove(draft: WorkPlanDraft, from: number, to: number, started: ReadonlySet<string>): ShownMovePlan {
  const slots = draftShownSlots(draft)
  const slot = slots[from]
  if (slot === undefined) return { status: 'unchanged' }
  const moving = draft.cards[slot]
  if (cardStarted(moving, started)) return { status: 'started' }
  const order = slots.filter((_, position) => position !== from).map((index) => draft.cards[index])
  const target = Math.max(0, Math.min(order.length, to))
  order.splice(target, 0, moving)
  const cards = [...draft.cards]
  slots.forEach((index, position) => {
    cards[index] = order[position]
  })
  if (!keepsStarted(draft.cards, cards, moving, started)) return { status: 'before_started' }
  if (target === from) return { status: 'unchanged' }
  return { status: 'moved', cards }
}

/** draft.cards indexes of the shown cards in order: the slots the shown cards fill. */
function draftShownSlots(draft: WorkPlanDraft): number[] {
  return draft.cards.flatMap((card, index) => (cardShown(card) ? [index] : []))
}

/**
 * Moves the shown card at shown position `from` so it lands at shown position `to` of the shown list without it
 * (the screen's indexes). The shown cards are put back into the slots the shown cards held, so every hidden card keeps
 * its place in draft.cards — and, its type's order unchanged, its key. With no hidden card this is moveCard.
 */
export function moveShownCard(draft: WorkPlanDraft, from: number, to: number, started: ReadonlySet<string>): WorkPlanMoveResult {
  const plan = planShownMove(draft, from, to, started)
  return plan.status === 'moved' ? { status: 'moved', draft: withCards(draft, plan.cards) } : plan
}

/** The ▲ (-1) / ▼ (+1) of the shown card at shown position `position` can be pressed. */
export function canMoveShownCardBy(draft: WorkPlanDraft, position: number, delta: number, started: ReadonlySet<string>): boolean {
  const to = position + delta
  const count = draftShownSlots(draft).length
  if (position < 0 || position >= count || to < 0 || to >= count) return false
  return planShownMove(draft, position, to, started).status === 'moved'
}

// --- Per-Step values and the plan defaults -----------------------------------------------------------------

/** Sets a row's AI (null = unassigned). A locked Step takes none. */
export function setStepProvider(draft: WorkPlanDraft, rowId: string, providerId: string | null, displayName: string | null): WorkPlanDraft {
  const row = rowOf(draft, rowId)
  if (!row || row.step.locked) return draft
  return replaceStep(draft, row, { ...row.step, provider_id: providerId, provider_display_name: providerId ? displayName : null, origin: 'human' })
}

/** Sets a row's one-line note ('' is kept as written; the server stores what it is sent). */
export function setStepNote(draft: WorkPlanDraft, rowId: string, note: string): WorkPlanDraft {
  const row = rowOf(draft, rowId)
  if (!row || row.step.locked) return draft
  return replaceStep(draft, row, { ...row.step, note, origin: 'human' })
}

export function setDefaultProvider(draft: WorkPlanDraft, providerId: string | null): WorkPlanDraft {
  return { ...draft, body: { ...draft.body, defaults: { ...draft.body.defaults, provider_id: providerId } } }
}

export function setDefaultNote(draft: WorkPlanDraft, note: string): WorkPlanDraft {
  return { ...draft, body: { ...draft.body, defaults: { ...draft.body.defaults, note } } }
}

/**
 * [apply to all] (v0.1 applyDefaults): every Step that is not locked takes the default AI (unassigned included) and
 * the default note (null when empty), as a person's value. Review and pre-instruction are left alone.
 */
export function applyDefaults(draft: WorkPlanDraft, displayName: (providerId: string) => string | null): WorkPlanDraft {
  const { provider_id: providerId, note } = draft.body.defaults
  const cards = draft.cards.map((card) => ({
    id: card.id,
    steps: card.steps.map((step) =>
      step.locked
        ? step
        : { ...step, provider_id: providerId, provider_display_name: providerId ? displayName(providerId) : null, note: note || null, origin: 'human' },
    ),
  }))
  return { ...draft, cards, body: { ...draft.body, steps: cards.flatMap((card) => card.steps) } }
}

// --- A row's review (0073 T#2: the main window's review popover) ---------------------------------------------

/**
 * Sets a row's review count and reviewer (null = the project's default reviewer at run time), as a person's value.
 * A count of 0 clears the reviewer (the server refuses a reviewer without review — reviewer_not_allowed). A locked Step
 * takes none; the same values give the draft back unchanged. Only this row's review changes.
 */
export function setStepReview(
  draft: WorkPlanDraft,
  rowId: string,
  reviewCount: number,
  reviewerProviderId: string | null,
  displayName: (providerId: string) => string | null,
): WorkPlanDraft {
  const row = rowOf(draft, rowId)
  if (!row || row.step.locked || !Number.isInteger(reviewCount)) return draft
  const reviewer = reviewCount === 0 ? null : reviewerProviderId
  if ((row.step.review_count ?? 0) === reviewCount && (row.step.reviewer_provider_id ?? null) === reviewer) return draft
  return replaceStep(draft, row, {
    ...row.step,
    review_count: reviewCount,
    reviewer_provider_id: reviewer,
    reviewer_provider_display_name: reviewer ? displayName(reviewer) : null,
    origin: 'human',
  })
}

/**
 * The review count picker's options: the server's choices in its order (review_count_choices — 0 none, -1 until
 * approved, n times), then the row's own count when the server no longer offers it (kept so the picker shows it).
 */
export function reviewCountOptions(choices: readonly number[], current: number, label: (count: number) => string): SelectOption[] {
  const counts = choices.includes(current) ? [...choices] : [...choices, current]
  return counts.map((count) => ({ value: String(count), label: label(count) }))
}

// --- The Step window boundary (T#2)---------------------------------------------------------------------

/** What the Step window edits of one row: its pre-instruction and attachment (the review is the popover's). */
export interface WorkPlanStepDraft {
  readonly preInstructionText: string | null
  readonly preInstructionAttachment: Readonly<Record<string, unknown>> | null
}

/** The row's current values for the Step window (a copy), or null when the row is gone or locked. */
export function stepDraftOf(draft: WorkPlanDraft, rowId: string): WorkPlanStepDraft | null {
  const row = rowOf(draft, rowId)
  if (!row || row.step.locked) return null
  return {
    preInstructionText: row.step.pre_instruction_text ?? null,
    preInstructionAttachment: row.step.pre_instruction_attachment ? clone(row.step.pre_instruction_attachment) : null,
  }
}

export type WorkPlanStepApply = { readonly status: 'applied'; readonly draft: WorkPlanDraft } | { readonly status: 'gone' | 'locked' | 'not_eligible' }

/**
 * Puts the Step window's values on its row only (no other row changes, and the row's review stays as it is). A result
 * Step takes no pre-instruction; values equal to the row's give the draft back unchanged.
 */
export function applyStepDraft(draft: WorkPlanDraft, rowId: string, values: WorkPlanStepDraft): WorkPlanStepApply {
  const row = rowOf(draft, rowId)
  if (!row) return { status: 'gone' }
  if (row.step.locked) return { status: 'locked' }
  if (!instructionEligible(row.step)) {
    return values.preInstructionText || values.preInstructionAttachment ? { status: 'not_eligible' } : { status: 'applied', draft }
  }
  const sameText = (row.step.pre_instruction_text ?? null) === values.preInstructionText
  const sameFile = JSON.stringify(row.step.pre_instruction_attachment ?? null) === JSON.stringify(values.preInstructionAttachment ?? null)
  if (sameText && sameFile) return { status: 'applied', draft }
  const step: WorkPlanEditStep = {
    ...row.step,
    pre_instruction_text: values.preInstructionText,
    pre_instruction_attachment: values.preInstructionAttachment ? clone(values.preInstructionAttachment) as Record<string, unknown> : null,
    origin: 'human',
  }
  return { status: 'applied', draft: replaceStep(draft, row, step) }
}

/**
 * After a save, the server's body names the new cards (same Steps, same order): the temporary ids are traded for the
 * server's by card position. Returns the old → new row ids of the cards that changed id.
 */
export function savedDraft(before: WorkPlanDraft, saved: WorkPlanEditBody): { draft: WorkPlanDraft; renamed: Map<string, string> } {
  const draft = draftOf(saved)
  const renamed = new Map<string, string>()
  const sameShape = draft.cards.length === before.cards.length
  const cards = draft.cards.map((card, index) => {
    const old = before.cards[index]
    if (!sameShape || !old) return card
    if (old.id !== card.id) {
      for (const role of ['main', 'result'] as const) renamed.set(workPlanRowId(old.id, role), workPlanRowId(card.id, role))
    }
    return card
  })
  return { draft: { ...draft, cards }, renamed }
}

// --- Summary ---------------------------------------------------------------------------------------------

const unassigned = (step: WorkPlanEditStep) => !step.locked && !step.provider_id

/** Steps that are not locked and have no AI (the server's unassigned_step_count; hidden types included). */
export function unassignedSteps(draft: WorkPlanDraft): number {
  return draft.cards.flatMap((card) => card.steps).filter(unassigned).length
}

/** unassignedSteps split by what the window draws (0073 T#1): the shown Steps, and the hidden ones. */
export function unassignedByShown(draft: WorkPlanDraft): { shown: number; hidden: number } {
  let shown = 0
  let hidden = 0
  for (const card of draft.cards) {
    const n = card.steps.filter(unassigned).length
    if (cardShown(card)) shown += n
    else hidden += n
  }
  return { shown, hidden }
}

/**
 * The summary line in what the window draws (0073 T#1): the hidden types' sheets / sets and Steps are not counted.
 * The body (quantities, counted_types, Steps) is not changed by this.
 */
export function totalsOf(draft: WorkPlanDraft): { designSheets: number; workSets: number; steps: number } {
  let designSheets = 0
  let workSets = 0
  for (const type of draft.body.counted_types) {
    const box = draft.body.quantities[type]
    if (!box || !typeShown(type)) continue
    if (unitOf(draft.body, type) === 'set') workSets += box.count
    else designSheets += box.count
  }
  return { designSheets, workSets, steps: draft.cards.filter(cardShown).reduce((sum, card) => sum + card.steps.length, 0) }
}

// --- Type icons (0073 T#1) -----------------------------------------------------------------------------------

export interface WorkPlanTypeIcon {
  /** A Palette glyph key (designer/palette/paletteIcons.ts paletteIconPath). */
  readonly icon: string
  /** The Palette accent of the type, or null (no Palette item: the window's muted color). */
  readonly accent: string | null
}

/**
 * The icon of a Step type as the Designer Palette draws it (DEFAULT_PALETTE_ITEMS, no table of its own); a result
 * type (NR / TR / TR2 / TSR) is the `file` glyph in its instruction's accent, a type with no Palette item `document`.
 */
export function workPlanTypeIcon(type: string): WorkPlanTypeIcon {
  const instruction = instructionTypeOf(type)
  const item = DEFAULT_PALETTE_ITEMS.find((entry) => entry.docType === instruction)
  if (instruction !== type) return { icon: 'file', accent: item?.accent ?? null }
  return item ? { icon: item.icon, accent: item.accent } : { icon: 'document', accent: null }
}

/** The Step's place among its type (v0.1 stepDocParts): n/total sheets, n sheet, or the n-th set. */
export function stepPlace(body: WorkPlanEditBody, step: WorkPlanEditStep): { key: 'sheet_of' | 'sheet' | 'set'; n: number; total: number } {
  const own = body.quantities[step.type]
  if (own && unitOf(body, step.type) === 'sheet') {
    return own.count <= 1 ? { key: 'sheet', n: step.ordinal, total: own.count } : { key: 'sheet_of', n: step.ordinal, total: own.count }
  }
  return { key: 'set', n: step.ordinal, total: 0 }
}

// --- The read view ----------------------------------------------------------------------------------------

export interface WorkPlanProvider {
  readonly id: string
  readonly name: string
}

export interface WorkPlanEditView {
  readonly docId: string
  readonly title: string | null
  readonly revisionNo: number
  readonly docReviewStatus: string | null
  readonly editable: boolean
  /** `final_approved` | `status` | null. */
  readonly editLockedReason: string | null
  /** The project's registered AIs read with the body (registered_providers), or null when the answer has none. */
  readonly registered: readonly WorkPlanProvider[] | null
  /** Card ids the workflow already started (pinned: no move, no removal). */
  readonly startedCardIds: ReadonlySet<string>
  /** The review counts the server takes (0 none, -1 until approved, n times). */
  readonly reviewCountChoices: readonly number[]
  readonly noteMaxChars: number
  readonly preInstructionMaxChars: number
  readonly attachmentMaxBytes: number
  readonly body: WorkPlanEditBody
}

const rec = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {})
const str = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const num = (value: unknown, fallback: number): number => (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallback)

/** The body as the window edits it: the fields it reads are given a shape, everything else stays as sent. */
export function editBodyOf(raw: unknown): WorkPlanEditBody {
  const body = clone(rec(raw)) as Record<string, unknown>
  const defaults = rec(body.defaults)
  return {
    ...body,
    wp_version: typeof body.wp_version === 'number' ? body.wp_version : 2,
    binding: typeof body.binding === 'string' ? body.binding : 'advisory',
    counted_types: Array.isArray(body.counted_types) ? body.counted_types.filter((item): item is string => typeof item === 'string') : [],
    quantities: rec(body.quantities) as Record<string, WorkPlanEditQuantity>,
    provider_candidates: Array.isArray(body.provider_candidates) ? (body.provider_candidates as Array<Record<string, unknown>>) : [],
    defaults: { ...defaults, provider_id: str(defaults.provider_id), note: typeof defaults.note === 'string' ? defaults.note : '' },
    steps: Array.isArray(body.steps) ? (body.steps as WorkPlanEditStep[]) : [],
  }
}

/** The read answer of `GET …/work-plan` as the edit window uses it. */
export function toWorkPlanEditView(raw: unknown): WorkPlanEditView {
  const data = rec(raw)
  const limits = rec(data.limits)
  const started = new Set<string>()
  for (const entry of Array.isArray(data.step_execution_status) ? data.step_execution_status : []) {
    const row = rec(entry)
    if (row.started === true && typeof row.card_id === 'string') started.add(row.card_id)
  }
  const registered = Array.isArray(data.registered_providers)
    ? data.registered_providers.flatMap((entry) => {
      const row = rec(entry)
      const id = str(row.id)
      return id ? [{ id, name: str(row.name) ?? id }] : []
    })
    : null
  const choices = Array.isArray(data.review_count_choices)
    ? data.review_count_choices.filter((item): item is number => typeof item === 'number' && Number.isInteger(item))
    : []
  const editable = data.editable === undefined ? true : data.editable === true
  return {
    docId: str(data.doc_id) ?? '',
    title: str(data.title),
    revisionNo: typeof data.revision_no === 'number' ? data.revision_no : 0,
    docReviewStatus: str(data.doc_review_status),
    editable,
    editLockedReason: editable ? null : str(data.edit_locked_reason),
    registered,
    startedCardIds: started,
    reviewCountChoices: choices.length ? choices : [0],
    noteMaxChars: num(limits.note_max_chars, 1000),
    preInstructionMaxChars: num(limits.pre_instruction_text_max_chars, 20000),
    attachmentMaxBytes: num(limits.pre_instruction_attachment_max_bytes, 0),
    body: editBodyOf(data.body),
  }
}

// --- AIs ---------------------------------------------------------------------------------------------------

/** The AIs a person may pick: the registered list read with the body, else the plan's candidate snapshot. */
export function selectableProviders(view: Pick<WorkPlanEditView, 'registered' | 'body'>): WorkPlanProvider[] {
  if (view.registered) return [...view.registered]
  return view.body.provider_candidates.flatMap((candidate) => {
    const id = str(candidate.provider_id)
    return id ? [{ id, name: str(candidate.display_name) ?? id }] : []
  })
}

/** An AI's name now: the registered list, the candidate snapshot, the Step's own snapshot, else its id. */
export function providerName(view: Pick<WorkPlanEditView, 'registered' | 'body'>, providerId: string, snapshot: string | null = null): string {
  const live = selectableProviders(view).find((provider) => provider.id === providerId)
  if (live) return live.name
  const candidate = view.body.provider_candidates.find((item) => item.provider_id === providerId)
  return str(candidate?.display_name) ?? snapshot ?? providerId
}

/**
 * The options of an AI picker: `emptyLabel` (unassigned, or the project default for a reviewer) first, the
 * selectable AIs, then any AI the plan uses that is no longer selectable, marked `unavailable` (kept visible so the
 * picker can show the value it holds).
 */
export function providerOptions(
  view: Pick<WorkPlanEditView, 'registered' | 'body'>,
  used: readonly (string | null)[],
  emptyLabel: string,
  unavailable: (name: string) => string,
): SelectOption[] {
  const options: SelectOption[] = [{ value: '', label: emptyLabel }]
  const seen = new Set<string>()
  for (const provider of selectableProviders(view)) {
    if (seen.has(provider.id)) continue
    seen.add(provider.id)
    options.push({ value: provider.id, label: provider.name })
  }
  for (const id of used) {
    if (!id || seen.has(id)) continue
    seen.add(id)
    options.push({ value: id, label: unavailable(providerName(view, id)) })
  }
  return options
}

// --- Lock ---------------------------------------------------------------------------------------------------

/**
 * Why the plan cannot be edited now, first reason first: the opener's read-only state (ownership / revision view),
 * the Flow's run lock, the Flow's AI run, an upload of this document in progress, an upload that may have left the
 * view stale, final approval, status, and a save the server refused although its read still said editable (422
 * detail: not editable; 409 detail: the Flow was discarded — the read's editable does not check that). null when it
 * can be edited.
 */
export type WorkPlanLockReason = 'read_only' | 'run_locked' | 'ai_running' | 'upload' | 'final_approved' | 'status' | 'not_editable' | 'disposed'

/** A save the server refused for good (not a conflict or a field error): the window stays locked. */
export type WorkPlanRefusal = 'not_editable' | 'disposed'

export interface WorkPlanLockInput {
  readonly external: 'read_only' | 'run_locked' | null
  readonly aiRunning: boolean
  readonly uploadGuarded: boolean
  readonly editable: boolean
  readonly editLockedReason: string | null
  readonly refused?: WorkPlanRefusal | null
}

export function workPlanLockReason(input: WorkPlanLockInput): WorkPlanLockReason | null {
  if (input.external) return input.external
  if (input.aiRunning) return 'ai_running'
  if (input.uploadGuarded) return 'upload'
  if (!input.editable) return input.editLockedReason === 'final_approved' ? 'final_approved' : 'status'
  return input.refused ?? null
}

/** Row errors of a failed save, pinned to the row identity of the Step key the server named (0649 T#3). */
export function errorsByRow<E extends { readonly key: string | null }>(draft: WorkPlanDraft, errors: readonly E[]): { rows: Record<string, E[]>; top: E[] } {
  const rows: Record<string, E[]> = {}
  const top: E[] = []
  const byKey = new Map(rowsOf(draft).map((row) => [row.step.key, row.rowId]))
  for (const error of errors) {
    const rowId = error.key ? byKey.get(error.key) : undefined
    if (rowId) (rows[rowId] ??= []).push(error)
    else top.push(error)
  }
  return { rows, top }
}
