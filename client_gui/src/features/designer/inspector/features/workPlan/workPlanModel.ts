// flowgate.v02.0048 T#13 — the WP summary's rules (D#1 §8, §10.1 WP 요약 column, §10.2 T#13 row). Pure
// functions over the target and the WP read view of its document (`GET /documents/{doc_id}/work-plan`,
// normalized by `@/features/document`): the cards of the plan (an instruction / result pair is one card)
// and the progress tally (cards the workflow already started).
// flowgate.v02.0056 F1 (0056 CH §5) — the "where the plan is edited" line is gone with the tab's details.
//
// Operations (D#1 §8.3 rule 0): this section only reads — reading and ↗ 열기 are queries, possible in every
// ownership and view, read-only ones included. Editing the plan is the work plan edit window's (↗ 열기); the
// section has no change operation, so no reason ever disables anything here.
// flowgate.v02.0074 T#5: the header's deploy buttons are the card's one change operation (workPlanDeployActions
// below; whether a plan is deployed is told by the status read's counts only, the card keeps no state of its own).
// Not offered: Step editing here, a re-plan, the AI-written plan and its origin badge.
import type { InspectorReadOnly, InspectorTarget } from '../../inspectorTarget'

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
// flowgate.v02.0074 T#5 (D#1 §6.1 · §3.6.1) — the header's [전개] and [재전개 ▾] (its two items 「추가분 전개」 add ·
// 「정리 포함 재전개」 cleanup) deploy the plan onto the Flow's Graph draft through the T#4 API (`GET …/work-plan/deploy`
// for the counts, preview, then the execution with the preview's baseline). Which one is usable follows the counts of
// the status read: no deployed card and at least one target → [전개]; a deployed card → [재전개 ▾], whose 「추가분
// 전개」 needs a target and whose 「정리 포함 재전개」 needs nothing more (a plan saved with every quantity at 0 cleans up).

export type WorkPlanDeployOperation = 'deploy' | 'redeploy' | 'add' | 'cleanup'

/** The two header buttons (the menu items `add` · `cleanup` sit under `redeploy`). */
export const WORK_PLAN_DEPLOY_OPERATIONS: readonly WorkPlanDeployOperation[] = ['deploy', 'redeploy']

/** The [재전개 ▾] menu items, in order. */
export const WORK_PLAN_REDEPLOY_ITEMS: readonly ('add' | 'cleanup')[] = ['add', 'cleanup']

/** The deploy mode each runnable operation sends. */
export const WORK_PLAN_DEPLOY_MODE_OF: Readonly<Record<'deploy' | 'add' | 'cleanup', 'first' | 'add' | 'cleanup'>> = {
  deploy: 'first',
  add: 'add',
  cleanup: 'cleanup',
}

export interface WorkPlanDeployItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.workPlan.`) of why it is disabled, or null. */
  readonly reason: string | null
}

/** The parts of the status read (`WorkPlanDeployStatus` of `@/features/document`) the rules read. */
export interface WorkPlanDeployStatusFacts {
  readonly ownership: string | null
  readonly deployable: boolean
  readonly reason: string | null
  readonly deployedCount: number
  readonly targetCount: number
}

export interface WorkPlanDeployInput {
  readonly target: Pick<InspectorTarget, 'docId' | 'readOnly' | 'editLock' | 'ownership'> | null
  /** The last status read of the target's plan, or null (not read yet, or it failed). */
  readonly status: WorkPlanDeployStatusFacts | null
  /** The last status read failed. */
  readonly statusFailed: boolean
  /** A preview or an execution is on its way. */
  readonly busy: boolean
}

/** Ownerships whose Flow a deploy may write (the server's `unassigned` / `graph`). */
const DEPLOY_OWNERSHIPS: ReadonlySet<string> = new Set(['unassigned', 'graph'])

/** The status read's refusal code -> its reason key. */
const STATUS_REASONS: Readonly<Record<string, string>> = {
  flow_owned_by_v01: 'reason.owned_by_v01',
  flow_ownership_conflict: 'reason.ownership_conflict',
  wp_not_editable: 'reason.not_editable',
  flow_disposed: 'reason.disposed',
  graph_run_locked: 'reason.run_locked',
  GROUP_AI_RUN_LOCKED: 'reason.ai_running',
  wp_not_on_card: 'reason.not_on_card',
}

function ownershipReason(ownership: string | null | undefined): string | null {
  if (!ownership || DEPLOY_OWNERSHIPS.has(ownership)) return null
  if (ownership === 'v01') return 'reason.owned_by_v01'
  if (ownership === 'conflict') return 'reason.ownership_conflict'
  return 'reason.unavailable'
}

/** Why no deploy can run now (the rules every operation shares), or null. */
export function workPlanDeployCommonReason(input: WorkPlanDeployInput): string | null {
  const target = input.target
  if (!target || target.docId === null) return 'reason.no_document'
  if (target.readOnly === 'conflict') return 'reason.ownership_conflict'
  if (target.readOnly === 'revision') return 'reason.read_only_revision'
  if (target.editLock === 'run') return 'reason.run_locked'
  if (target.editLock) return 'reason.run_unknown'
  // The latest read failed: an earlier answer kept on screen does not count (its counts may have moved).
  const status = input.statusFailed ? null : input.status
  const ownership = ownershipReason(status?.ownership ?? target.ownership)
  if (ownership) return ownership
  if (input.statusFailed) return 'reason.status_failed'
  if (status === null) return 'reason.loading'
  if (!status.deployable) return (status.reason && STATUS_REASONS[status.reason]) || 'reason.unavailable'
  if (input.busy) return 'reason.busy'
  return null
}

const item = (reason: string | null): WorkPlanDeployItem => ({ visible: true, enabled: reason === null, reason })

/**
 * [전개] · [재전개 ▾] and its 「추가분 전개」 · 「정리 포함 재전개」: every one off with the common reason (no plan document,
 * read only, the run lock, a v0.1 / conflicting Flow, a plan that cannot be edited, the status read failed, a deploy on
 * its way); otherwise by the counts.
 */
export function workPlanDeployActions(input: WorkPlanDeployInput): Readonly<Record<WorkPlanDeployOperation, WorkPlanDeployItem>> {
  const common = workPlanDeployCommonReason(input)
  if (common !== null || input.status === null) {
    const off = item(common ?? 'reason.loading')
    return { deploy: off, redeploy: off, add: off, cleanup: off }
  }
  const { deployedCount, targetCount } = input.status
  const deployed = deployedCount > 0
  const redeploy = item(deployed ? null : 'reason.not_deployed')
  return {
    deploy: item(deployed ? 'reason.deployed' : targetCount < 1 ? 'reason.no_targets' : null),
    redeploy,
    add: redeploy.enabled ? item(targetCount < 1 ? 'reason.no_targets' : null) : redeploy,
    cleanup: redeploy,
  }
}

const BLOCKERS: ReadonlySet<string> = new Set([
  'wp_card_has_successor',
  'deploy_chain_nonlinear',
  'deploy_position_blocked',
  'deploy_draft_invalid',
  'deploy_already_deployed',
  'deploy_not_deployed',
])

/** The one-line key of a preview's first blocker: its own words, a refusal's reason, else the generic one. */
export function workPlanDeployBlockerKey(code: string | null | undefined): string {
  if (!code) return 'failure.blocked'
  if (BLOCKERS.has(code)) return `blocker.${code}`
  return STATUS_REASONS[code] ?? 'failure.blocked'
}

/** The one-line key of a failed deploy call (`WorkPlanDeployFailureKind` of `@/features/document`). */
export function workPlanDeployFailureKey(kind: string): string {
  switch (kind) {
    case 'forbidden':
    case 'not_found':
    case 'not_a_work_plan':
    case 'not_editable':
    case 'ai_running':
    case 'owned_by_v01':
    case 'ownership_conflict':
    case 'run_locked':
    case 'not_on_card':
    case 'disposed':
    case 'unreadable':
    case 'wp_changed':
    case 'attachment_unreadable':
    case 'already_deployed':
    case 'not_deployed':
    case 'draft_conflict':
    case 'blocked':
    case 'preview_stale':
    case 'target_changed':
    case 'restore_incomplete':
    case 'network':
      return `failure.${kind}`
    default:
      return 'failure.other'
  }
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
  // flowgate.v02.0056 F1: a document being written asks nothing of the operator — a neutral state draws no badge.
  return { key: 'slot_state.writing', raw: status, tone: 'neutral' }
}

/** A human decision waits on the plan (v7: the line is highlighted). */
export function workPlanSlotFocus(state: WorkPlanSlotState | null): boolean {
  return state !== null && (state.raw === 'pending_review' || state.raw === 'rejected')
}
