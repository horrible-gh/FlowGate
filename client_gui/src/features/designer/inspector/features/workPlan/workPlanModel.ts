// flowgate.v02.0048 T#13 — the WP summary's rules (D#1 §8, §10.1 WP 요약 column, §10.2 T#13 row). Pure
// functions over the target and the WP read view of its document (`GET /documents/{doc_id}/work-plan`,
// normalized by `@/features/document`): the revision and totals line, the Step summary (one row per card:
// an instruction / result pair is one card), the progress tally (cards the workflow already started) and
// the provider assignment.
//
// Operations (D#1 §8.3 rule 0): this section only reads — reading and ↗ 열기 are queries, possible in every
// ownership and view, read-only ones included. Editing the plan is the v0.1 WorkPlanEditor's (↗ 열기); the
// section has no change operation, so no reason ever disables anything here.
// Not offered (D#1 §8.4, §10.2): Step editing, ⇉ / 재전개 / ⟳ 재계획 and their applied state (WP 전개), the
// AI-written plan and its origin badge.
import type { InspectorReadOnly } from '../../inspectorTarget'

/** The parts of a WP Step the summary reads (`WorkPlanStep` of `@/features/document`). */
export interface WorkPlanStepFacts {
  readonly key: string
  readonly cardId: string
  readonly type: string | null
  readonly providerName: string | null
  readonly reviewCount: number
  readonly locked: boolean
  readonly started: boolean
}

/** The parts of the WP read view the summary reads (`WorkPlanView` of `@/features/document`). */
export interface WorkPlanViewFacts {
  readonly revisionNo: number
  readonly editable: boolean
  readonly editLockedReason: string | null
  readonly totals: { readonly designSheets: number; readonly workSets: number; readonly steps: number }
  readonly steps: readonly WorkPlanStepFacts[]
  readonly unassignedSteps: number
}

/** One card of the plan: a single Step, or an instruction Step with its result Step. */
export interface WorkPlanCardRow {
  readonly cardId: string
  /** The Step keys of the card in plan order, e.g. `['T#1', 'TR#1']`. */
  readonly keys: readonly string[]
  /** Type of the card's first Step. */
  readonly type: string | null
  /** Distinct provider names of the card's Steps, in order (empty: nobody assigned). */
  readonly providers: readonly string[]
  /** Review count of the first Step that asks for one, else 0. */
  readonly reviewCount: number
  /** Every Step of the card is server-assembled (no provider is assigned to it). */
  readonly locked: boolean
  /** The workflow already started the card. */
  readonly started: boolean
}

/** Groups the Steps by card, keeping the plan's order (the first Step of a card places it). */
export function workPlanCards(view: Pick<WorkPlanViewFacts, 'steps'> | null): WorkPlanCardRow[] {
  if (!view) return []
  const byCard = new Map<string, WorkPlanStepFacts[]>()
  for (const step of view.steps) {
    const steps = byCard.get(step.cardId)
    if (steps) steps.push(step)
    else byCard.set(step.cardId, [step])
  }
  return [...byCard.entries()].map(([cardId, steps]) => {
    const providers: string[] = []
    for (const step of steps) if (step.providerName !== null && !providers.includes(step.providerName)) providers.push(step.providerName)
    return {
      cardId,
      keys: steps.map((step) => step.key),
      type: steps[0].type,
      providers,
      reviewCount: steps.find((step) => step.reviewCount > 0)?.reviewCount ?? 0,
      locked: steps.every((step) => step.locked),
      started: steps.some((step) => step.started),
    }
  })
}

export interface WorkPlanProgress {
  /** Cards of the plan. */
  readonly cards: number
  /** Cards the workflow already started. */
  readonly started: number
  /** Steps of the plan. */
  readonly steps: number
}

/** The progress tally: how many of the plan's cards already started in the workflow. */
export function workPlanProgress(view: Pick<WorkPlanViewFacts, 'steps'> | null): WorkPlanProgress {
  const cards = workPlanCards(view)
  return { cards: cards.length, started: cards.filter((card) => card.started).length, steps: view?.steps.length ?? 0 }
}

/** Message key (under `gui.inspector.workPlan.`) of where the plan is edited. */
export function workPlanEditKey(view: Pick<WorkPlanViewFacts, 'editable' | 'editLockedReason'> | null): string | null {
  if (!view) return null
  if (view.editable) return 'edit.open'
  return view.editLockedReason === 'final_approved' ? 'edit.final_approved' : 'edit.locked'
}

/** Message key of a failed read (`WorkPlanErrorKind` of `@/features/document`). */
export function workPlanErrorKey(kind: string): string {
  switch (kind) {
    case 'unreadable':
    case 'invalid':
    case 'forbidden':
    case 'not_found':
    case 'network':
      return `error.${kind}`
    default:
      return 'error.other'
  }
}

export type WorkPlanOperation = 'read' | 'open'

/** Both are queries (D#1 §8.3 rule 0): the section changes nothing on the server. */
export const WORK_PLAN_OPERATION_CLASS: Readonly<Record<WorkPlanOperation, 'change' | 'query'>> = {
  read: 'query',
  open: 'query',
}

export interface WorkPlanAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.workPlan.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type WorkPlanAvailability = Readonly<Record<WorkPlanOperation, WorkPlanAvailabilityItem>>

export interface WorkPlanInput {
  readonly readOnly: InspectorReadOnly
  /** The Node has a bound WP document. */
  readonly hasDocument: boolean
}

const OPEN: WorkPlanAvailabilityItem = { visible: true, enabled: true, reason: null }
const HIDDEN: WorkPlanAvailabilityItem = { visible: false, enabled: false, reason: null }

/** Queries only: possible whenever there is a document, read-only views included (D#1 §8.3 rules 1, 5). */
export function workPlanAvailability(input: WorkPlanInput): WorkPlanAvailability {
  const item = input.hasDocument ? OPEN : HIDDEN
  return { read: item, open: item }
}

// --- flowgate.v02.0051 stage 2 — the v7 작업계획 card shape (0035 plan.m.*) --------------------------------

export type WorkPlanDeployOperation = 'deploy' | 'redeploy'

export const WORK_PLAN_DEPLOY_OPERATIONS: readonly WorkPlanDeployOperation[] = ['deploy', 'redeploy']

export interface WorkPlanDeployItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.workPlan.`) of why it is disabled, or null. */
  readonly reason: string | null
}

/**
 * ⇉ 전개 · ⇉ 재전개 ▾: both are drawn on every plan card and neither is usable — deploying a plan onto
 * the Canvas has no Inspector API (D#1 §8.4). Shown disabled with that reason (0051-R §2.3).
 */
export function workPlanDeployActions(_input: { readonly hasDocument: boolean }): Readonly<Record<WorkPlanDeployOperation, WorkPlanDeployItem>> {
  const item: WorkPlanDeployItem = { visible: true, enabled: false, reason: 'deploy.unconnected' }
  return { deploy: item, redeploy: item }
}

export type WorkPlanSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

/** The plan line's state pill: a message key under `gui.designer.inspector.` with the raw server value. */
export interface WorkPlanSlotState {
  readonly key: string
  readonly raw: string
  readonly tone: WorkPlanSlotTone
}

const REVIEW_TONES: Readonly<Record<string, WorkPlanSlotTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** The pill of the plan document: its review status, else its document status (draft / open / closed). */
export function workPlanSlotState(reviewStatus: string | null, docStatus: string | null): WorkPlanSlotState | null {
  const review = reviewStatus?.trim() ?? ''
  if (review) return { key: `review_status.${review}`, raw: review, tone: REVIEW_TONES[review] ?? 'neutral' }
  const status = docStatus?.trim() ?? ''
  if (!status) return null
  if (status === 'closed') return { key: 'slot_state.closed', raw: status, tone: 'neutral' }
  return { key: 'slot_state.writing', raw: status, tone: 'info' }
}

/** A human decision waits on the plan (v7: the line is highlighted). */
export function workPlanSlotFocus(state: WorkPlanSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}
