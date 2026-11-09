import { toRaw } from 'vue'
import type { FlowRunFailure } from '../center/run/flowRunControl'
import { safeFlowLabel } from '../center/run/runFailureDiagnostic'

const CONSUMED_LIMIT = 64
const STORE_PREFIX = 'flowgate.run_failure_consumed.v1:'

interface RunFailurePublisherOptions {
  /** Same-tab storage (sessionStorage) so a reloaded tab does not tell a recovered failure again. Null: memory only. */
  readonly storage?: Pick<Storage, 'getItem' | 'setItem'> | null
  /** Who the persisted record belongs to (user/session owner); another owner never reads it. */
  readonly owner?: () => string | null
}

/**
 * The same-tab storage for the persisted consumed record, or null. Merely reading `sessionStorage` can throw
 * (SecurityError when storage access is denied), so the lookup itself is guarded; failure means memory-only.
 */
export function resolveSessionStorage(): Pick<Storage, 'getItem' | 'setItem'> | null {
  try { return typeof sessionStorage === 'undefined' ? null : sessionStorage ?? null } catch { return null }
}

/**
 * flowgate.v02.0156 T5 (E1): two separate things.
 * - observation (`ids`/`objects`): which failures this target observation already handed out. `clear()` resets it on
 *   Flow/Project/session-owner changes and nothing else.
 * - consumed record: failures already shown once. `clear()` never touches it, so a tab round trip, which re-reads the
 *   same `state.error`, does not bring a closed or already shown toast back. A new failure has a new `publicationId`
 *   and is never matched by its code or text. Legacy failures (no id/scope) keep object identity as before.
 * The record is bounded; after a reload (new `scope`) only a command-recovery failure of the same stored
 * `commandRequestId` is recognized, through the owner-scoped persisted record.
 */
export function createRunFailurePublisher(options: RunFailurePublisherOptions = {}) {
  let ids = new Set<string>()
  let objects = new WeakSet<object>()
  const consumed = new Set<string>()
  const storeKey = () => { const owner = options.owner?.(); return options.storage && owner ? `${STORE_PREFIX}${owner}` : null }
  const readStored = (): string[] => {
    const key = storeKey()
    if (!key) return []
    try {
      const parsed: unknown = JSON.parse(options.storage!.getItem(key) ?? '[]')
      return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string').slice(-CONSUMED_LIMIT) : []
    } catch { return [] }
  }
  const commandKey = (failure: FlowRunFailure) => failure.commandRequestId && failure.outcome === 'unconfirmed' ? `cmd:${failure.commandRequestId}` : null
  function remember(key: string) {
    consumed.add(key)
    if (consumed.size > CONSUMED_LIMIT) consumed.delete(consumed.values().next().value as string)
  }
  function persist(key: string) {
    const storeAt = storeKey()
    if (!storeAt) return
    try { options.storage!.setItem(storeAt, JSON.stringify([...readStored().filter((k) => k !== key), key].slice(-CONSUMED_LIMIT))) } catch { /* storage full/blocked: memory record still holds */ }
  }
  return {
    clear() { ids = new Set(); objects = new WeakSet() },
    accept(failure: FlowRunFailure): boolean {
      const raw = toRaw(failure)
      const cmd = commandKey(failure)
      if (failure.publicationId && failure.scope) {
        const key = `${failure.scope}/${failure.publicationId}`
        if (ids.has(key) || consumed.has(key)) return false
        if (cmd && failure.stage === 'command_lookup' && readStored().includes(cmd)) return false
        ids.add(key)
        remember(key)
        if (cmd) persist(cmd)
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
