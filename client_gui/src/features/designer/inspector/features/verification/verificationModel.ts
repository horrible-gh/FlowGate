// flowgate.v02test.0053 1-D — what the card's `검증` line draws (mockup v15). Pure: no Vue, no i18n, no API.
// The line has a name, a state badge and buttons only; times and the card ahead live in the badge's tooltip.
import type { CardVerification, CardVerificationAction, CardVerificationState } from '@/features/document'
import type { SlotStateTone } from '../../parts/slotStateView'

/** The window event that asks the Designer to show one card (its Flow opened or activated, the card selected). */
export const OPEN_CARD_EVENT = 'fg:open_card'

/** How long a finished action's badge (`종료됨` / `취소됨`) stays before the line is read again (ms). */
export const VERIFICATION_FEEDBACK_MS = 3000
/** How often a shown line is read again (ms). */
export const VERIFICATION_SHOWN_POLL_MS = 5000
/** How often a hidden line is read again when no run says when it would appear (ms). */
export const VERIFICATION_IDLE_POLL_MS = 30000

export type VerificationButton = 'force_stop' | 'retry_stop' | 'mark_ended' | 'go' | 'cancel_waiting'

/** flowgate.v02test.0084 #13 — the stop reasons the line names; any other code the server sends reads as `unknown`. */
export const VERIFICATION_STOP_REASONS = ['host_changed', 'processes_remain', 'owner_alive'] as const
export type VerificationStopReason = (typeof VERIFICATION_STOP_REASONS)[number] | 'unknown'

/** The `종료 실패` line's reason key, or null (another state, or a server that sends no reason). */
export function verificationStopReason(verification: CardVerification | null): VerificationStopReason | null {
  if (!verification || verification.state !== 'stop_failed' || !verification.stopReason) return null
  return VERIFICATION_STOP_REASONS.find((reason) => reason === verification.stopReason) ?? 'unknown'
}

export interface VerificationRowView {
  readonly state: Exclude<CardVerificationState, 'hidden'>
  readonly tone: SlotStateTone
  /** Red bar: a run past its time (or one that would not stop). */
  readonly danger: boolean
  /** Amber bar: this card's request waits behind such a run. */
  readonly focus: boolean
  /** The buttons in order before a confirmation is asked. */
  readonly buttons: readonly VerificationButton[]
  /** `종료 실패` only: why the stop could not be confirmed, shown as one short phrase beside the badge. */
  readonly reason: VerificationStopReason | null
}

const TONES: Record<VerificationRowView['state'], SlotStateTone> = {
  overdue: 'danger',
  stop_failed: 'danger',
  waiting: 'warning',
  stopped: 'success',
  cancelled: 'hold',
}

/** The line for a verification answer, or null when nothing is drawn. `canGo`: the card ahead can be shown here. */
export function verificationRowView(verification: CardVerification | null, canGo: boolean): VerificationRowView | null {
  if (!verification || !verification.visible || verification.state === 'hidden') return null
  const state = verification.state
  const can = (action: CardVerificationAction) => verification.actions.includes(action) && !!verification.run
  const buttons: VerificationButton[] = []
  if (state === 'overdue' && can('force_stop')) buttons.push('force_stop')
  if (state === 'stop_failed' && can('force_stop')) buttons.push('retry_stop')
  if (state === 'stop_failed' && can('mark_ended')) buttons.push('mark_ended')
  if (state === 'waiting') {
    if (canGo && verification.blocker) buttons.push('go')
    if (can('cancel_waiting')) buttons.push('cancel_waiting')
  }
  return {
    state,
    tone: TONES[state],
    danger: state === 'overdue' || state === 'stop_failed',
    focus: state === 'waiting',
    buttons,
    reason: verificationStopReason(verification),
  }
}

/**
 * The action a button asks for; a confirmation is asked first for [강제 종료] and [취소] ([확인] [취소] in place) and for
 * [끝난 것으로 처리] (a dialog: it cannot be undone and processes may remain).
 */
export const BUTTON_ACTION: Readonly<Record<Exclude<VerificationButton, 'go'>, CardVerificationAction>> = {
  force_stop: 'force_stop',
  retry_stop: 'force_stop',
  mark_ended: 'mark_ended',
  cancel_waiting: 'cancel_waiting',
}
export const CONFIRMED_BUTTONS: readonly VerificationButton[] = ['force_stop', 'cancel_waiting', 'mark_ended']
/** The confirmed buttons asked in a dialog rather than in place. */
export const DIALOG_BUTTONS: readonly VerificationButton[] = ['mark_ended']

const stamp = (value: string | null | undefined): number | null => {
  if (!value) return null
  const ms = Date.parse(value)
  return Number.isFinite(ms) ? ms : null
}

/**
 * When to read the line again (ms from `now`): soon while it is shown; at the moment a running card's line would
 * appear (its `show_at`), but no later than the idle interval; otherwise the idle interval.
 */
export function nextReadDelay(verification: CardVerification | null, now: number): number {
  if (verification?.visible) return VERIFICATION_SHOWN_POLL_MS
  const showAt = stamp(verification?.run?.showAt)
  if (showAt === null) return VERIFICATION_IDLE_POLL_MS
  const wait = showAt - now
  // A clock ahead of or behind the server's: once the moment has passed, keep asking at the shown pace.
  if (wait <= 0) return VERIFICATION_SHOWN_POLL_MS
  return Math.min(wait + 500, VERIFICATION_IDLE_POLL_MS)
}

/** `17:42` in local time, or null. */
export function clockOf(value: string | null | undefined): string | null {
  const ms = stamp(value)
  if (ms === null) return null
  const at = new Date(ms)
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
}

/** `5:00` (minutes:seconds), or null. */
export function elapsedOf(seconds: number | null | undefined): string | null {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

/** The badge tooltip parts without words: launch time · elapsed (a running line), or the end time (a finished one). */
export function verificationTimes(verification: CardVerification): string | null {
  const run = verification.state === 'waiting' ? verification.blocker : verification.run
  if (!run) return null
  if (verification.state === 'stopped' || verification.state === 'cancelled') return clockOf(run.finishedAt)
  const parts = [clockOf(run.startedAt), elapsedOf(run.elapsedSeconds)].filter((part): part is string => !!part)
  return parts.length ? parts.join(' · ') : null
}
