// flowgate.v02.0031 — pure timeline rules. Messages, command requests, work changes and status
// events are ONE flow (R0001 §3, §9-5): nothing is pinned under the log, and a new message after
// an activity pushes that activity up like any other entry. flowgate.v02.0032: command requests
// and run changes are placed by their server anchor (D0006 §3.9.1), not by their time.
import type {
  CommandRequestActivity,
  ConversationActivity,
  ConversationTurn,
  StatusActivity,
  WorkChangeActivity,
  WorkChangeFile,
} from './types'

/** A message the composer has sent but the server has not numbered yet (L0004 §2-17). */
export interface PendingTurn {
  localId: string
  idempotencyKey: string
  body: string
  basedOnSeq: number
  createdAt: string
  failed: boolean
}

export type TimelineItem =
  | { kind: 'message'; key: string; turn: ConversationTurn; pending: boolean; failed: boolean; localId: string | null }
  | { kind: 'boundary'; key: string }
  | { kind: 'command'; key: string; activity: CommandRequestActivity }
  | { kind: 'change'; key: string; activity: WorkChangeActivity }
  | { kind: 'status'; key: string; activity: StatusActivity }

function timeOf(iso: string | null | undefined): number | null {
  if (!iso) return null
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? null : ms
}

/** Insert in seq order, ignoring a seq already present. Returns a new array. */
export function mergeTurns(current: readonly ConversationTurn[], incoming: readonly ConversationTurn[]): ConversationTurn[] {
  const bySeq = new Map<number, ConversationTurn>()
  for (const turn of current) if (turn.seq > 0) bySeq.set(turn.seq, turn)
  for (const turn of incoming) if (turn.seq > 0 && !bySeq.has(turn.seq)) bySeq.set(turn.seq, turn)
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

/** Replace an activity with the same id (a state update) or add it. Returns a new array. */
export function upsertActivity(current: readonly ConversationActivity[], activity: ConversationActivity): ConversationActivity[] {
  const at = current.findIndex((a) => a.id === activity.id)
  if (at < 0) return [...current, activity]
  const next = current.slice()
  next.splice(at, 1, activity)
  return next
}

/**
 * The activity list after a full server read (D0006 §3.9.2): this client's status events stay,
 * every command request / run change is the server's, and one the server no longer lists is
 * dropped. A command in `held` keeps the card on screen as it is — an in-flight decision's
 * optimistic state stands until that decision answers. A 403 the user already got for a
 * command (decisionForbidden) carries over, so its buttons stay hidden.
 */
export function replaceServerActivities(
  current: readonly ConversationActivity[],
  server: readonly ConversationActivity[],
  held: readonly string[] = [],
): ConversationActivity[] {
  const commandsNow = new Map<string, CommandRequestActivity>()
  for (const a of current) if (a.kind === 'command') commandsNow.set(a.id, a)
  const fromServer = server.map((activity): ConversationActivity => {
    if (activity.kind !== 'command') return activity
    const now = commandsNow.get(activity.id)
    if (now && held.includes(activity.id)) return now
    return now?.decisionForbidden ? { ...activity, decisionForbidden: true } : activity
  })
  return [...current.filter((a) => a.kind === 'status'), ...fromServer]
}

function activityItem(activity: ConversationActivity): TimelineItem {
  const key = `${activity.kind}:${activity.id}`
  if (activity.kind === 'command') return { kind: 'command', key, activity }
  if (activity.kind === 'change') return { kind: 'change', key, activity }
  return { kind: 'status', key, activity }
}

/**
 * One list. Turns keep their server order (seq). A command request or run change goes where
 * the server anchored it — before or after turn `anchor.seq` (D0006 §3.9.1) — and never by its
 * `createdAt`: an anchor older than the loaded turns lands before them, one past the loaded
 * turns after them, and an activity the server could not place yet stays after every turn
 * until a later read places it. Status events are this client's own (run started / finished):
 * they keep the time rule — in front of the first turn later than them, an untimed one last.
 * Unsent messages are always last — they are, by definition, the newest thing this client knows.
 */
export function buildTimeline(
  turns: readonly ConversationTurn[],
  activities: readonly ConversationActivity[],
  pending: readonly PendingTurn[],
  readSeq = 0,
): TimelineItem[] {
  const statuses = activities
    .map((activity, index) => ({ activity, index, at: timeOf(activity.createdAt) }))
    .filter((s) => s.activity.kind === 'status')
    .sort((a, b) => {
      if (a.at == null || b.at == null) return a.at == null && b.at == null ? a.index - b.index : a.at == null ? 1 : -1
      return a.at - b.at || a.index - b.index
    })
  const anchored: Array<{ activity: ConversationActivity; index: number; seq: number; after: boolean }> = []
  const unplaced: ConversationActivity[] = []
  activities.forEach((activity, index) => {
    if (activity.kind === 'status') return
    const anchor = activity.anchor
    if (anchor && anchor.seq != null && anchor.position) anchored.push({ activity, index, seq: anchor.seq, after: anchor.position === 'after' })
    else unplaced.push(activity)
  })
  // Same place: before-anchors first, then a run's commands ahead of its change, then list order.
  anchored.sort((a, b) => a.seq - b.seq || Number(a.after) - Number(b.after) || kindRank(a.activity) - kindRank(b.activity) || a.index - b.index)

  const boundarySeq = firstUnreadSeq(turns, readSeq)
  const out: TimelineItem[] = []
  let nextAnchored = 0
  let nextStatus = 0
  for (const turn of turns) {
    while (nextAnchored < anchored.length) {
      const a = anchored[nextAnchored]
      if (a.seq > turn.seq || (a.seq === turn.seq && a.after)) break
      out.push(activityItem(a.activity))
      nextAnchored += 1
    }
    const turnAt = timeOf(turn.createdAt)
    while (nextStatus < statuses.length && turnAt != null && statuses[nextStatus].at != null && statuses[nextStatus].at! < turnAt) {
      out.push(activityItem(statuses[nextStatus].activity))
      nextStatus += 1
    }
    if (turn.seq === boundarySeq) out.push({ kind: 'boundary', key: `boundary:${turn.seq}` })
    out.push({ kind: 'message', key: `turn:${turn.seq}`, turn, pending: false, failed: false, localId: null })
    while (nextAnchored < anchored.length && anchored[nextAnchored].seq === turn.seq && anchored[nextAnchored].after) {
      out.push(activityItem(anchored[nextAnchored].activity))
      nextAnchored += 1
    }
  }
  for (; nextAnchored < anchored.length; nextAnchored++) out.push(activityItem(anchored[nextAnchored].activity))
  for (const activity of unplaced) out.push(activityItem(activity))
  for (; nextStatus < statuses.length; nextStatus++) out.push(activityItem(statuses[nextStatus].activity))
  for (const p of pending) {
    out.push({
      kind: 'message',
      key: `local:${p.localId}`,
      turn: { seq: 0, speaker: 'user', body: p.body, basedOnSeq: p.basedOnSeq, createdAt: p.createdAt },
      pending: !p.failed,
      failed: p.failed,
      localId: p.localId,
    })
  }
  return out
}

function kindRank(activity: ConversationActivity): number {
  return activity.kind === 'command' ? 0 : 1
}

/** Seq of the first unread turn when some turn above it was read (D0002 §6), else 0. */
export function firstUnreadSeq(turns: readonly ConversationTurn[], readSeq: number): number {
  if (readSeq <= 0) return 0
  if (!turns.some((t) => t.seq > 0 && t.seq <= readSeq)) return 0
  return turns.find((t) => t.seq > readSeq)?.seq ?? 0
}

/**
 * Totals of a run change (R0001 §6: file count, added / removed, representative file). The
 * server totals win; a total it leaves null is summed from the files that have numbers, and
 * stays null when none has (binary only) — "no numbers" is not "0 lines".
 */
export function summarizeChange(change: Pick<WorkChangeActivity, 'files' | 'filesChanged' | 'insertions' | 'deletions'>): {
  fileCount: number
  added: number | null
  removed: number | null
  firstPath: string | null
  firstName: string | null
} {
  const files = change.files
  const sum = (pick: (f: WorkChangeFile) => number | null): number | null => {
    const known = files.map(pick).filter((n): n is number => n != null)
    return known.length > 0 ? known.reduce((n, v) => n + Math.max(0, v), 0) : null
  }
  const added = change.insertions ?? sum((f) => f.insertions)
  const removed = change.deletions ?? sum((f) => f.deletions)
  const firstPath = files[0]?.path ?? null
  const firstName = firstPath ? firstPath.split('/').pop() || firstPath : null
  return { fileCount: Math.max(change.filesChanged || 0, files.length), added, removed, firstPath, firstName }
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

/** Message time: `HH:mm` today, `MM/DD HH:mm` otherwise; the raw text when unparsable. */
export function formatTimelineTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return sameDay(d, now) ? hm : `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${hm}`
}

/** Chat list meta: `today` / `yesterday` markers or `MM/DD`; null when there is no date. */
export function chatListDate(iso: string | null | undefined, now: Date = new Date()): { kind: 'today' | 'yesterday'; text: string } | { kind: 'date'; text: string } | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  if (sameDay(d, now)) return { kind: 'today', text: `${pad(d.getHours())}:${pad(d.getMinutes())}` }
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (sameDay(d, yesterday)) return { kind: 'yesterday', text: '' }
  return { kind: 'date', text: `${pad(d.getMonth() + 1)}/${pad(d.getDate())}` }
}
