// flowgate.v02.0073 T#4 — the work plan as JSON, on the client only (no server API: NR §2 — the server has no import,
// export or raw route). What goes out is the edit window's draft as `PUT …/work-plan` would send it (bodyOf): the
// canonical body with its wp_version as it is, DS / T2 Steps, quantities, counted_types and every x_* field included.
// What comes in is checked here only as far as the client can (the fields of the body, quantities ⇔ counted_types,
// quantities ⇔ cards, an instruction with its result, the Step values the window reads, the card ids it names rows
// by) and becomes the window's draft (draftOf) — a refused JSON leaves the draft as it was; the save is still the
// parent's one PUT with base_revision_no and the whole body, and the final check is the server's
// (work_plan_service.validate / assign_card_ids: providers, review counts, card ids of the previous body, keys and
// ordinals).
// The one server rule checked before that is the started cards' (work_plan_card_order.check_started_prefix, the
// window's moveCard / quantity rules): the cards up to the last started one stay as they are, in their order.
// Not the Inspector's DocumentIoGroup (the document file upload / download, 0068): that is a separate feature.
import {
  WORK_PLAN_COUNT_MAX,
  WORK_PLAN_COUNT_MIN,
  WORK_PLAN_LOCKED_TYPES,
  WORK_PLAN_PAIR,
  WORK_PLAN_SET_TYPES,
  WORK_PLAN_SHEET_TYPES,
  bodyOf,
  editBodyOf,
  groupSteps,
  lastStartedIndex,
  type WorkPlanDraft,
  type WorkPlanEditBody,
  type WorkPlanEditStep,
} from './workPlanEditModel'

/** The draft as JSON text (pretty, one trailing newline): what the export file and the raw view hold. */
export function workPlanJsonText(draft: WorkPlanDraft): string {
  return `${JSON.stringify(bodyOf(draft), null, 2)}\n`
}

/** The export file's name: the document id, made safe for a file name. */
export function workPlanJsonFileName(docId: string): string {
  const base = docId.replace(/[^\w.-]+/g, '_').replace(/^\.+/, '')
  return `${base || 'work-plan'}.json`
}

// --- Reading a JSON in ------------------------------------------------------------------------------------------

export type WorkPlanJsonIssueCode =
  | 'syntax'
  | 'not_object'
  | 'missing_field'
  | 'field_type'
  | 'wp_version'
  | 'unknown_type'
  | 'quantities_types'
  | 'quantity'
  | 'step'
  | 'step_value'
  | 'pair'
  | 'card_id'
  | 'card_id_duplicate'
  | 'step_count'
  | 'started'

/** Why a JSON is not taken. `type` names a document type (shown by its name), `n` a Step (1-based), `field` a field. */
export interface WorkPlanJsonIssue {
  readonly code: WorkPlanJsonIssueCode
  readonly field?: string
  readonly type?: string
  readonly n?: number
  readonly version?: number
  readonly expected?: number
}

export type WorkPlanJsonRead = { readonly ok: true; readonly body: WorkPlanEditBody } | { readonly ok: false; readonly issues: readonly WorkPlanJsonIssue[] }

type Raw = Record<string, unknown>

const isRecord = (value: unknown): value is Raw => !!value && typeof value === 'object' && !Array.isArray(value)
const isInt = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value)

/** The body's top-level fields (the server's layer 2, wp_version first) and the shape each must have. */
const TOP_FIELDS: ReadonlyArray<readonly [string, (value: unknown) => boolean]> = [
  ['wp_version', isInt],
  ['binding', (value) => typeof value === 'string'],
  ['counted_types', Array.isArray],
  ['quantities', isRecord],
  // The AI pickers read each candidate's fields.
  ['provider_candidates', (value) => Array.isArray(value) && value.every(isRecord)],
  ['defaults', isRecord],
  ['steps', Array.isArray],
]

const KNOWN_TYPES: ReadonlySet<string> = new Set([...WORK_PLAN_SHEET_TYPES, ...WORK_PLAN_SET_TYPES])
const RESULT_TYPES: ReadonlySet<string> = new Set(Object.values(WORK_PLAN_PAIR))
const ROLES: ReadonlySet<unknown> = new Set(['instruction', 'result', 'single'])

const unitOfKnown = (type: string): 'sheet' | 'set' => (WORK_PLAN_SET_TYPES.includes(type) ? 'set' : 'sheet')

function fail(issues: WorkPlanJsonIssue[]): WorkPlanJsonRead {
  return { ok: false, issues }
}

function quantityIssues(raw: Raw): WorkPlanJsonIssue[] {
  const issues: WorkPlanJsonIssue[] = []
  const counted = raw.counted_types as unknown[]
  const quantities = raw.quantities as Raw
  const seen = new Set<string>()
  let unknown = false
  let mismatch = false
  for (const type of counted) {
    if (typeof type !== 'string' || !KNOWN_TYPES.has(type)) unknown = true
    else if (seen.has(type)) mismatch = true
    else seen.add(type)
  }
  if (unknown) issues.push({ code: 'unknown_type' })
  const keys = Object.keys(quantities)
  if (mismatch || keys.length !== seen.size || keys.some((type) => !seen.has(type))) issues.push({ code: 'quantities_types' })
  for (const type of keys) {
    if (!KNOWN_TYPES.has(type)) continue
    const box = quantities[type]
    const ok =
      isRecord(box) &&
      box.unit === unitOfKnown(type) &&
      isInt(box.count) &&
      box.count >= WORK_PLAN_COUNT_MIN &&
      box.count <= WORK_PLAN_COUNT_MAX
    if (!ok) issues.push({ code: 'quantity', type })
  }
  return issues
}

const absent = (value: unknown) => value === undefined || value === null
const stringOrAbsent = (value: unknown) => absent(value) || typeof value === 'string'

/**
 * The values of a Step the window reads as it draws and edits it (a picker, a note, the review button, the
 * pre-instruction mark — stepHasInstruction trims the text): absent, null or of their shape. Whether a value is
 * allowed (a known AI, a review count the server takes, the note's length) stays the server's check.
 */
const STEP_VALUES: ReadonlyArray<readonly [string, (value: unknown) => boolean]> = [
  ['pair_key', stringOrAbsent],
  ['provider_id', stringOrAbsent],
  ['provider_display_name', stringOrAbsent],
  ['note', stringOrAbsent],
  ['review_count', (value) => absent(value) || isInt(value)],
  ['reviewer_provider_id', stringOrAbsent],
  ['reviewer_provider_display_name', stringOrAbsent],
  ['pre_instruction_text', stringOrAbsent],
  ['pre_instruction_attachment', (value) => absent(value) || isRecord(value)],
  ['locked', (value) => absent(value) || typeof value === 'boolean'],
]

function stepIssues(steps: readonly unknown[]): WorkPlanJsonIssue[] {
  const issues: WorkPlanJsonIssue[] = []
  const keys = new Set<string>()
  steps.forEach((step, index) => {
    const ok =
      isRecord(step) &&
      typeof step.key === 'string' &&
      step.key !== '' &&
      !keys.has(step.key) &&
      typeof step.type === 'string' &&
      isInt(step.ordinal) &&
      ROLES.has(step.pair_role) &&
      // A Step the server assembles stays locked (the window draws no control for it).
      (!WORK_PLAN_LOCKED_TYPES.has(step.type) || step.locked === true)
    if (isRecord(step) && typeof step.key === 'string') keys.add(step.key)
    if (!ok) issues.push({ code: 'step', n: index + 1 })
    else if (STEP_VALUES.some(([field, shape]) => !shape(step[field]))) issues.push({ code: 'step_value', n: index + 1 })
  })
  return issues
}

/** An instruction is followed by its own result (type, ordinal, pair keys); a result never stands alone. */
function pairIssues(steps: readonly WorkPlanEditStep[]): WorkPlanJsonIssue[] {
  const issues: WorkPlanJsonIssue[] = []
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i]
    if (step.pair_role === 'instruction') {
      const next = steps[i + 1]
      const ok =
        !!WORK_PLAN_PAIR[step.type] &&
        !!next &&
        next.pair_role === 'result' &&
        next.type === WORK_PLAN_PAIR[step.type] &&
        next.ordinal === step.ordinal &&
        step.pair_key === next.key &&
        next.pair_key === step.key
      if (ok) i += 1
      else issues.push({ code: 'pair', n: i + 1 })
    } else if (step.pair_role === 'result' || WORK_PLAN_PAIR[step.type] || RESULT_TYPES.has(step.type)) {
      issues.push({ code: 'pair', n: i + 1 })
    }
  }
  return issues
}

/** Each counted type has as many cards as its quantity, and no card is of a type the body does not count. */
function countIssues(quantities: Raw, cards: readonly WorkPlanEditStep[][]): WorkPlanJsonIssue[] {
  const issues: WorkPlanJsonIssue[] = []
  const have = new Map<string, number>()
  for (const card of cards) have.set(card[0].type, (have.get(card[0].type) ?? 0) + 1)
  for (const [type, box] of Object.entries(quantities)) {
    if ((box as { count: number }).count !== (have.get(type) ?? 0)) issues.push({ code: 'step_count', type })
  }
  for (const type of have.keys()) if (!(type in quantities)) issues.push({ code: 'step_count', type })
  return issues
}

/** The card id the window names a card by (draftOf). */
const cardIdOf = (card: readonly WorkPlanEditStep[]) => card[0].card_id || `legacy:${card[0].key}`

/** The server's card id format (work_plan_service.card_id_is_valid: CARD_ID_PATTERN, not `retired:`). */
const CARD_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_#.:-]{0,63}$/
/** Prefixes of the names the window gives a card itself (draftOf `legacy:`, a new card's `tmp:`): never sent, never taken. */
const CARD_ID_RESERVED = ['retired:', 'legacy:', 'tmp:']

const cardIdValid = (value: unknown) =>
  absent(value) || (typeof value === 'string' && CARD_ID_PATTERN.test(value) && !CARD_ID_RESERVED.some((prefix) => value.startsWith(prefix)))

/**
 * The card ids the window names its rows by (a card's row is `<card id>|main` / `|result`): a given card_id has the
 * server's format, a set's instruction and result carry the same one (both may have none), and no two cards share a
 * name — a card without an id is named after its head Step's key, so a body without ids is still taken. Whether an id
 * is one of the previous body's (card_id_unknown / card_id_type_changed) stays the server's check.
 */
function cardIdIssues(steps: readonly WorkPlanEditStep[], cards: readonly WorkPlanEditStep[][]): WorkPlanJsonIssue[] {
  const issues: WorkPlanJsonIssue[] = []
  steps.forEach((step, index) => {
    if (!cardIdValid(step.card_id)) issues.push({ code: 'card_id', n: index + 1 })
  })
  if (issues.length) return issues
  const names = new Set<string>()
  let n = 0
  for (const card of cards) {
    const [head, result] = card
    if (result && (head.card_id ?? null) !== (result.card_id ?? null)) issues.push({ code: 'card_id', n: n + 2 })
    const name = cardIdOf(card)
    if (names.has(name)) issues.push({ code: 'card_id_duplicate', n: n + 1 })
    names.add(name)
    n += card.length
  }
  return issues
}

/**
 * Reads a JSON text as the plan to put in `current`'s place. Taken: the body as the window edits it (editBodyOf: a
 * copy, every field kept). Refused: every issue of the first check that fails (the JSON, the top-level fields, the
 * version, the quantities, the Steps and their values, the pairs, the card ids, the counts, the started cards).
 */
export function readWorkPlanJson(text: string, current: WorkPlanDraft, started: ReadonlySet<string>): WorkPlanJsonRead {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return fail([{ code: 'syntax' }])
  }
  if (!isRecord(raw)) return fail([{ code: 'not_object' }])

  const fields: WorkPlanJsonIssue[] = []
  for (const [field, ok] of TOP_FIELDS) {
    if (!(field in raw)) fields.push({ code: 'missing_field', field })
    else if (!ok(raw[field])) fields.push({ code: 'field_type', field })
  }
  if (fields.length) return fail(fields)
  // The format is not changed here: the plan's own version only.
  if (raw.wp_version !== current.body.wp_version) {
    return fail([{ code: 'wp_version', version: raw.wp_version as number, expected: current.body.wp_version }])
  }

  const quantities = quantityIssues(raw)
  if (quantities.length) return fail(quantities)
  const shapes = stepIssues(raw.steps as unknown[])
  if (shapes.length) return fail(shapes)
  const steps = raw.steps as WorkPlanEditStep[]
  const pairs = pairIssues(steps)
  if (pairs.length) return fail(pairs)
  const cards = groupSteps(steps)
  const ids = cardIdIssues(steps, cards)
  if (ids.length) return fail(ids)
  const counts = countIssues(raw.quantities as Raw, cards)
  if (counts.length) return fail(counts)

  // The cards up to the last started one stay, as they are and in their order (nothing is put before a started card).
  const last = lastStartedIndex(current.cards, started)
  if (last >= 0) {
    const kept = current.cards.slice(0, last + 1).map((card) => card.id)
    const coming = cards.slice(0, last + 1).map(cardIdOf)
    const types = current.cards.slice(0, last + 1).map((card) => card.steps[0].type)
    if (kept.some((id, index) => coming[index] !== id || cards[index]?.[0].type !== types[index])) return fail([{ code: 'started' }])
  }

  return { ok: true, body: editBodyOf(raw) }
}
