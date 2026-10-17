// flowgate.v02.0061 T#15 — what a card's context menu (right click on a Designer card) offers, by the server's rules.
// Pure: no Vue, no transport. The Canvas draws it with the shared ContextMenu (components/ui) and sends the chosen key
// back; the Center session acts on it.
//
// - Hold 설정 / Hold 해제 (T#11, R's Hold): every stored Graph card (a `tmp_` card not saved yet has no settings) of a
//   Flow the Graph engine may run (not one v0.1 owns, nor an ownership conflict: Hold means nothing there). The one that
//   changes the card's current Hold is offered; an unknown Hold uses the new-card default (OFF) for this menu.
//   The item waits while the Flow's run is running / paused / stopping, or its status is not confirmed
//   (WP answer 1: the server refuses a change during a run, 409 graph_run_locked); 설정 is never offered enabled on a done
//   card (Q8, 409 graph_hold_done_node). A release only changes the setting: nothing runs before the next command.
// - 되돌리기 (Graph Time Machine, WP answer 3): only on a done card of a Graph-owned Flow, for the card and every card
//   below it — the server finds those by the run's execution history (never the shown Graph or its draft). It waits
//   unless the Flow's run is confirmed idle (the server's T#12 assessment decides the rest).
// - While another card action of the Flow is answered (a command, a Hold, an approval, a Time Machine) every item waits.
import type { FlowRunInfo } from '../graph/flowGraphSource'
import type { FlowEditLock } from './flowRunLock'

export type FlowCardMenuKey = 'hold_set' | 'hold_release' | 'time_machine'

/** Why an item waits: the run locks it, its status is not confirmed, the card is done, the run is not idle, busy. */
export type FlowCardMenuReason = 'run_locked' | 'run_unknown' | 'done_node' | 'run_not_idle' | 'busy'

export interface FlowCardMenuItem {
  readonly key: FlowCardMenuKey
  readonly disabled: boolean
  /** Why it is disabled, or null. */
  readonly reason: FlowCardMenuReason | null
  /** A change the user should read twice (되돌리기). */
  readonly danger: boolean
}

export interface FlowCardMenuInput {
  /** The card's state in the Flow's run (`done`, `open`, …), or null when the run has no node run of it. */
  readonly nodeStatus: string | null
  /** The card's Hold, or null when not known. */
  readonly hold: boolean | null
  /** The card is a Graph node stored on the server (not a `tmp_` card of the draft, not a v0.1 document card). */
  readonly stored: boolean
  /** The Flow's edit lock (flowRunLock.ts): `run`, `unknown` or null. */
  readonly lock: FlowEditLock
  /** The Flow's run (null: none) and whether its status is confirmed. */
  readonly run: Pick<FlowRunInfo, 'status'> | null
  readonly known: boolean
  /** Which engine owns the Flow (`graph` …), or null. */
  readonly ownership: string | null
  /** Another action of the Flow is answered now. */
  readonly busy: boolean
  /** The source can hold cards / has the Time Machine. */
  readonly canHold: boolean
  readonly canTimeMachine: boolean
}

export const DONE_NODE_STATUS = 'done'

const item = (key: FlowCardMenuKey, reason: FlowCardMenuReason | null, danger = false): FlowCardMenuItem => ({
  key,
  disabled: reason !== null,
  reason,
  danger,
})

function lockReason(lock: FlowEditLock): FlowCardMenuReason | null {
  if (lock === 'run') return 'run_locked'
  return lock === 'unknown' ? 'run_unknown' : null
}

/** The card's menu items in their order (empty: the card has no menu). */
export function flowCardMenu(input: FlowCardMenuInput): FlowCardMenuItem[] {
  const items: FlowCardMenuItem[] = []
  const done = input.nodeStatus === DONE_NODE_STATUS
  const busy = input.busy ? 'busy' : null
  if (input.canHold && input.stored && input.ownership !== 'v01' && input.ownership !== 'conflict') {
    const locked = lockReason(input.lock) ?? busy
    if (input.hold === true) items.push(item('hold_release', locked))
    else items.push(item('hold_set', (done ? 'done_node' : null) ?? locked))
  }
  if (input.canTimeMachine && done && input.ownership === 'graph') {
    let reason: FlowCardMenuReason | null = null
    if (!input.known) reason = 'run_unknown'
    else if (input.run?.status !== 'idle') reason = 'run_not_idle'
    items.push(item('time_machine', reason ?? busy, true))
  }
  return items
}
