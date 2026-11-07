import { toRaw } from 'vue'
import type { FlowRunFailure } from '../center/run/flowRunControl'
import { safeFlowLabel } from '../center/run/runFailureDiagnostic'

/** One active-target observation, independent of toast dismissal. Reset on target/session changes. */
export function createRunFailurePublisher() {
  let ids = new Set<string>()
  let objects = new WeakSet<object>()
  return {
    clear() { ids = new Set(); objects = new WeakSet() },
    accept(failure: FlowRunFailure): boolean {
      const raw = toRaw(failure)
      if (failure.publicationId && failure.scope) {
        const key = `${failure.scope}/${failure.publicationId}`
        if (ids.has(key)) return false
        ids.add(key)
      } else {
        // Legacy producers do not prove that equal numbers/nulls/codes represent one event.
        if (objects.has(raw)) return false
        objects.add(raw)
      }
      return true
    },
  }
}

export function runErrorText(error: FlowRunFailure, t: (key: string, values?: Record<string,string>) => string, te: (key:string)=>boolean, nodeLabel?: (key: string) => string | null): string {
  const root = 'gui.designer.top.'
  const key = error.code && /^[A-Za-z][A-Za-z0-9_]{0,127}$/.test(error.code) ? `${root}run_errors.${error.code}` : ''
  const reason = key && error.code !== 'other' && te(key) ? t(key) : ''
  const stage = error.stage
  const actionKey = stage === 'before_prepare' || stage === 'save_draft' || stage === 'prepare' ? 'prepare'
    : stage === 'revision' ? 'revision' : stage === 'target_read' ? 'target_read' : error.action ?? 'start'
  const action = actionKey === 'start' && error.mode ? t(`${root}tools.run_${error.mode}`)
    : ['pause', 'resume', 'stop'].includes(actionKey) ? t(`${root}tools.${actionKey}`)
    : t(`${root}run_request_actions.${actionKey}`)
  const head = t(`${root}run_request_${error.outcome === 'unconfirmed' ? 'unconfirmed' : 'failed'}`, {action})
  const label = safeFlowLabel(error.flowLabel)
  const target = label ? `${label} · ` : ''
  const blocked = error.blocked
  const reasonKey = blocked?.reason ? `${root}run_errors.blocked_reason_${blocked.reason}` : ''
  const detail = reasonKey && te(reasonKey) ? t(reasonKey) : ''
  const cards = (blocked?.nodeKeys ?? []).slice(0, 3).map((key) => {
    const raw = nodeLabel?.(key)
    const name = typeof raw === 'string' ? raw.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, 60) : ''
    const blocker = blocked?.blockers.find((b) => b.nodeKey === key)
    const kind = blocker?.activeAttempt ? 'active' : blocker?.rowStatus === 'review' && !blocker.activeAttempt ? 'review' : 'other'
    const suffix = blocked?.reason === 'active_attempts' && blocker ? ` (${t(`${root}run_errors.blocked_card_${kind}`)})` : ''
    return `${name || key}${suffix}`
  })
  const more = Math.max(0, (blocked?.total ?? blocked?.nodeKeys.length ?? 0) - cards.length)
  const listed = cards.length ? t(`${root}run_errors.blocked_cards`, {cards: cards.join(', ')})
    + (more ? ` ${t(`${root}run_errors.blocked_more`, {n: String(more)})}` : '') : ''
  const observation = error.outcome === 'unconfirmed' && error.observation?.status
    ? ` ${t(`${root}run_command_recovery.observation`, { status: error.observation.status })}` : ''
  return `${target}${head}${reason ? ` ${reason}` : ''}${detail ? ` ${detail}` : ''}${listed ? ` ${listed}` : ''}${observation}`
}
