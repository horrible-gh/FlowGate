import type { FlowCommandMessage, FlowCommandNode, FlowCommandResult, FlowCommandRoleUse } from '@/features/project'

// flowgate.v02.0113 T#5 — what the run result windows say, built from the server's command result in the current
// language (`t`, `te`: vue-i18n). No fixed sentence: every line is the command's own state, scope, cards and counts.

export type Translate = (key: string, values?: Record<string, unknown>) => string
export type HasKey = (key: string) => boolean

const k = (key: string) => `gui.runResult.${key}`

/** A key's words when the catalog has them, else the raw value (a code the screen does not know yet). */
function word(t: Translate, te: HasKey, key: string, raw: string): string {
  return te(k(key)) ? t(k(key)) : raw
}

export function stateLabel(t: Translate, te: HasKey, result: FlowCommandResult): string {
  return word(t, te, `state.${result.state}`, result.state)
}

export function scopeLabel(t: Translate, result: FlowCommandResult): string {
  return t(k(`scope.${result.scope}`))
}

export function nextLabel(t: Translate, te: HasKey, result: FlowCommandResult): string {
  const next = result.nextState ?? ''
  return next ? word(t, te, `next.${next}`, next) : '—'
}

/**
 * flowgate.chat.0017 CH0002: "첨부 승인 대기 n건" for the AI attachment requests still waiting (this run's, with the
 * Flow's total when more wait there); empty when none wait or the server could not tell.
 */
export function attachmentPendingLine(t: Translate, result: FlowCommandResult | null): string {
  const counts = result?.attachmentRequests
  if (!counts || (counts.pending <= 0 && counts.flowPending <= 0)) return ''
  const line = t('gui.attachment_request.run_result_pending', { n: counts.pending > 0 ? counts.pending : counts.flowPending })
  return counts.pending > 0 && counts.flowPending > counts.pending
    ? `${line} ${t('gui.attachment_request.run_result_flow_total', { n: counts.flowPending })}`
    : line
}

export function countsLabel(result: FlowCommandResult): string {
  return `${result.counts.done} / ${result.counts.failed}`
}

/** One card of the command in a short line: its title and outcome (with why it waits or failed). */
export function nodeLine(t: Translate, te: HasKey, node: FlowCommandNode): string {
  const outcome = word(t, te, `outcome.${node.outcome}`, node.outcome)
  const why = node.reason
    ? (node.outcome === 'failed' ? word(t, te, `failure.${node.reason}`, node.reason) : word(t, te, `wait.${node.reason}`, node.reason))
    : ''
  return `${node.title ?? node.nodeKey} · ${outcome}${why ? ` (${why})` : ''}`
}

/** The steps / branches the command processed, at most `limit` lines and how many more. */
export function stepLines(t: Translate, te: HasKey, result: FlowCommandResult, limit = 5): { lines: string[]; more: number } {
  const lines = result.nodes.map((node) => nodeLine(t, te, node))
  return { lines: lines.slice(0, limit), more: Math.max(0, lines.length - limit) }
}

/** The failure in one line: its card and why (the detail the server gave in brackets). */
export function failureLine(t: Translate, te: HasKey, result: FlowCommandResult): string {
  const failure = result.failure
  if (!failure) return ''
  const reason = te(k(`failure.${failure.code}`)) ? t(k(`failure.${failure.code}`)) : t(k('failure.other'))
  const where = failure.title ? `${failure.title}: ` : ''
  return `${where}${reason}${failure.detail ? ` (${failure.detail})` : ''}`
}

/** The details window's head line: state · steps · scope. */
export function summaryLine(t: Translate, te: HasKey, result: FlowCommandResult): string {
  return [stateLabel(t, te, result), t(k('steps_summary'), { n: result.nodes.length }), scopeLabel(t, result)].join(' · ')
}

/** The AIs a role used in the command, in words (none: "없음"). */
export function roleUseLabel(t: Translate, te: HasKey, uses: readonly FlowCommandRoleUse[]): string {
  if (uses.length === 0) return t(k('use.none'))
  const names = uses.map((use) => (use.kind === 'ai' ? use.aiName ?? use.aiId ?? '—' : word(t, te, `use.${use.kind}`, use.kind)))
  return [...new Set(names)].join(', ')
}

export function roleLabel(t: Translate, te: HasKey, message: FlowCommandMessage): string {
  if (message.kind === 'event') return t(k(message.error ? 'role.failure' : 'role.event'))
  return message.role ? word(t, te, `role.${message.role}`, message.role) : '—'
}

/** The model a record requested and the one confirmed, told apart (§3.10). */
export function modelLabel(t: Translate, message: FlowCommandMessage): string {
  if (message.kind !== 'invocation') return ''
  const requested = message.requested?.models[0] ?? message.requested?.aiId ?? '—'
  const confirmedModels = message.confirmed?.models ?? []
  const confirmed = confirmedModels.length > 0 ? confirmedModels.join(', ') : t(k('model.unknown'))
  const ai = message.confirmed?.aiName ?? message.confirmed?.aiId ?? message.requested?.aiId
  return `${ai ? `${ai} · ` : ''}${t(k('model.requested'))} ${requested} → ${t(k('model.confirmed'))} ${confirmed}`
}

export function timeLabel(at: string | null, locale: string): string {
  if (!at) return ''
  const date = new Date(at)
  return Number.isNaN(date.getTime()) ? at : date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** An event message's own line (a node moving, or a failure with its detail). */
export function eventLine(t: Translate, te: HasKey, message: FlowCommandMessage): string {
  if (message.error) {
    const reason = message.code && te(k(`failure.${message.code}`)) ? t(k(`failure.${message.code}`)) : (message.code ?? '')
    return `${reason}${message.detail ? ` (${message.detail})` : ''}`
  }
  const from = message.from ? word(t, te, `status.${message.from}`, message.from) : '—'
  const to = message.to ? word(t, te, `status.${message.to}`, message.to) : '—'
  return `${from} → ${to}${message.code ? ` · ${message.code}` : ''}`
}

/** What a message's body is when it is not the launch's own output (empty: it is, or the message is an event). */
export function bodyNote(t: Translate, message: FlowCommandMessage): string {
  if (message.kind !== 'invocation') return ''
  if (message.bodySource === 'not_preserved') return t(k('body_source.not_preserved'))
  if (message.bodySource === 'run') return t(k('body_source.run'))
  return ''
}

/** Every message as plain text for [전체 복사]: a head line per message, its body and its full error. */
export function copyText(t: Translate, te: HasKey, result: FlowCommandResult, locale: string): string {
  const head = summaryLine(t, te, result)
  const blocks = result.messages.map((message) => {
    const parts = [roleLabel(t, te, message), message.title ?? message.nodeKey ?? '', modelLabel(t, message), timeLabel(message.at, locale)]
      .filter(Boolean)
    const body = message.kind === 'invocation' ? message.body : eventLine(t, te, message)
    const note = bodyNote(t, message)
    return [`[${parts.join(' · ')}]`, note ? `(${note})` : '', body, message.errorText ? `${t(k('error_head'))}:\n${message.errorText}` : '']
      .filter(Boolean)
      .join('\n')
  })
  return [head, ...blocks].join('\n\n-----\n\n')
}
