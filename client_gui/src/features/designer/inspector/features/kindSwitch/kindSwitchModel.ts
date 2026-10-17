// flowgate.v02.0048 T#10 — the R↔B kind switch's availability rules (D#1 §8, §10.2 종류 전환 row as
// D#2 §3.5 rewrote it, D#2 §3.4). Pure functions: card kind × document type × ownership / read only
// × AI running × group disposed × a switch in flight -> visible / enabled / reason, the kind a root
// switches to, and a refused switch -> the reason shown and whether the Canvas is read again.
//
// One operation, a change operation (D#1 §8.3 rule 0):
//   switch   the requirement (R) / bug (B) root with a bound document becomes the other kind
//            through the v0.1 converter — a new doc_id. Shown on ownership `unassigned` only:
//            a v01 Flow has a sequence and the converter always refuses it (D#2 E1); a Graph or
//            conflict Flow is refused by the server's v0.1 contact (D#2 E2, §3.3 C). A place that
//            always fails is not drawn (DS0007 §5), so those Flows show nothing — not even under
//            a Graph revision view (D#2 §3.5: 종류 전환 leaves D#1 §8.3 rule 5's "shown disabled" list).
// Whether child documents already reference the root is not known here for sure; the server
// refuses it (409) and the refusal is shown (D#2 §3.4). Permissions are the server's (403).
// flowgate.v02.0064 T#4 (P0006 §6.1): a root with children is succeeded instead of refused, and the
// server's judgement (`refusal`, read with the document) disables the switch with its reason. A judgement
// not read yet blocks nothing: the server decides when the switch runs.
import type { InspectorCardKind, InspectorReadOnly } from '../../inspectorTarget'

export type KindSwitchOperation = 'switch'

export const KIND_SWITCH_OPERATIONS: readonly KindSwitchOperation[] = ['switch']

/** The switch changes the server state (D#1 §8.3 rule 0). */
export const KIND_SWITCH_OPERATION_CLASS: Readonly<Record<KindSwitchOperation, 'change' | 'query'>> = {
  switch: 'change',
}

/** The one ownership the switch is offered on (D#2 §3.4). */
export const KIND_SWITCH_OWNERSHIPS: readonly string[] = ['unassigned']

/** The kinds a root switches between: requirement and bug. */
export type KindSwitchKind = 'R' | 'B'

export interface KindSwitchInput {
  readonly cardKind: InspectorCardKind
  readonly docType: string | null
  /** A document is bound to the Node. */
  readonly hasDocument: boolean
  readonly ownership: string | null
  readonly readOnly: InspectorReadOnly
  /** An AI run owns the Flow's group. */
  readonly aiRunning: boolean
  readonly groupDisposed: boolean
  /** The switch is waiting for its answer (or for the state read after it). */
  readonly busy: boolean
  /** The server judged the switch refused (the kind of its reason); null / absent: not refused or not read. */
  readonly refusal?: KindSwitchFailureKind | null
}

export interface KindSwitchAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.kindSwitch.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type KindSwitchAvailability = Readonly<Record<KindSwitchOperation, KindSwitchAvailabilityItem>>

const HIDDEN: KindSwitchAvailabilityItem = { visible: false, enabled: false, reason: null }

/** The kind a root of `docType` switches to; null when it is not a requirement / bug. */
export function kindSwitchTarget(docType: string | null | undefined): KindSwitchKind | null {
  const type = docType?.trim().toUpperCase()
  if (type === 'R') return 'B'
  if (type === 'B') return 'R'
  return null
}

function visibleOf(input: KindSwitchInput): boolean {
  return (
    input.hasDocument &&
    input.cardKind === 'requirement' &&
    kindSwitchTarget(input.docType) !== null &&
    input.ownership !== null &&
    KIND_SWITCH_OWNERSHIPS.includes(input.ownership)
  )
}

/**
 * Why the switch is disabled: one reason, in the order of D#1 §8.3 rule 6-1 — ownership
 * conflict, revision view, disposed, AI running, then this feature's own (a switch in flight).
 */
export function kindSwitchDisabledReason(input: KindSwitchInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.groupDisposed) return 'reason.disposed'
  if (input.aiRunning) return 'reason.ai_running'
  if (input.busy) return 'reason.busy'
  if (input.refusal) return kindSwitchFailureKey(input.refusal)
  return null
}

/** Shown at all, usable now, and why not. */
export function kindSwitchAvailability(input: KindSwitchInput): KindSwitchAvailability {
  if (!visibleOf(input)) return { switch: HIDDEN }
  const reason = kindSwitchDisabledReason(input)
  return { switch: { visible: true, enabled: reason === null, reason } }
}

/** Kinds of a refused switch (the `@/features/document` kindSwitchErrorOf kinds). */
export type KindSwitchFailureKind =
  | 'superseded'
  | 'body_unreadable'
  | 'identity_unsafe'
  | 'attachment'
  | 'graph'
  | 'ownership_conflict'
  | 'decided'
  | 'children'
  | 'collision'
  | 'disposed'
  | 'running'
  | 'conflict'
  | 'invalid'
  | 'forbidden'
  | 'not_found'
  | 'network'
  | 'other'

/** Message key of a refused switch; the state is read again after it is shown. */
export function kindSwitchFailureKey(kind: KindSwitchFailureKind): string {
  return `failure.${kind}`
}

/**
 * Refusals that mean the Canvas is out of date — the Flow's ownership moved (Graph / conflict /
 * a sequence was decided), documents now reference the root, the other identity or the document
 * itself changed, the root was already succeeded — so the Flow is read again (`refresh-flow`) as well
 * as the switch's own state. The body / identity / attachment refusals only read the state again.
 */
const CANVAS_STALE: readonly KindSwitchFailureKind[] = ['graph', 'ownership_conflict', 'decided', 'children', 'collision', 'conflict', 'not_found', 'superseded']

export function kindSwitchRefreshesFlow(kind: KindSwitchFailureKind): boolean {
  return CANVAS_STALE.includes(kind)
}
