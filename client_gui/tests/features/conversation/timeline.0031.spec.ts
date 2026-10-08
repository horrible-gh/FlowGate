import { describe, expect, it } from 'vitest'
import {
  buildTimeline,
  chatListDate,
  firstUnreadSeq,
  formatTimelineTime,
  mergeTurns,
  replaceServerActivities,
  summarizeChange,
  upsertActivity,
  type PendingTurn,
} from '@/features/conversation/timeline'
import type { ActivityAnchor, ConversationActivity, ConversationTurn } from '@/features/conversation/types'

// flowgate.v02.0031 — pure timeline rules: one flow (R0001 §3, §9-5). flowgate.v02.0032 —
// command requests and run changes are placed by their server anchor (D0006 §3.9.1).
const turn = (seq: number, at: string | null, speaker = 'user'): ConversationTurn => ({ seq, speaker, body: `t${seq}`, createdAt: at })
const at = (seq: number, position: 'before' | 'after'): ActivityAnchor => ({ seq, position, state: 'reply' })
const UNPLACED: ActivityAnchor = { seq: null, position: null, state: 'unresolved' }
const command = (id: string, anchor: ActivityAnchor | null, createdAt: string | null = null): ConversationActivity => ({
  kind: 'command',
  id,
  createdAt,
  command: `cmd ${id}`,
  state: 'pending_approval',
  anchor,
})
const change = (id: string, anchor: ActivityAnchor | null, createdAt: string | null = null): ConversationActivity => ({
  kind: 'change',
  id,
  runId: id,
  createdAt,
  files: [{ path: 'a/b.ts', status: 'M', insertions: 1, deletions: 0 }],
  filesChanged: 1,
  insertions: 1,
  deletions: 0,
  anchor,
})
const status = (id: string, createdAt: string | null): ConversationActivity => ({ kind: 'status', id, createdAt, tone: 'info', code: 'run_cancelled', text: null })

describe('mergeTurns', () => {
  it('orders by seq and ignores a seq already present', () => {
    const merged = mergeTurns([turn(2, null), turn(1, null)], [turn(3, null), { ...turn(2, null), body: 'dup' }])
    expect(merged.map((t) => t.seq)).toEqual([1, 2, 3])
    expect(merged[1].body).toBe('t2')
  })

  it('drops unnumbered turns', () => {
    expect(mergeTurns([], [turn(0, null)])).toEqual([])
  })
})

describe('buildTimeline', () => {
  it('places a command before and a run change after the turn the server anchored them to', () => {
    const items = buildTimeline(
      [turn(1, '2026-10-06T08:02:00Z'), turn(2, '2026-10-06T08:03:00Z', 'ai'), turn(3, '2026-10-06T08:05:00Z')],
      // createdAt says otherwise on purpose: only the anchor counts.
      [change('c', at(2, 'after'), '2026-10-06T07:00:00Z'), command('k', at(2, 'before'), '2026-10-06T09:00:00Z')],
      [],
    )
    expect(items.map((i) => i.key)).toEqual(['turn:1', 'command:k', 'turn:2', 'change:c', 'turn:3'])
  })

  it('keeps a run_start anchor after its turn, and a later message lands after it', () => {
    const runStart: ActivityAnchor = { seq: 1, position: 'after', state: 'run_start' }
    const before = buildTimeline([turn(1, '2026-10-06T08:00:00Z')], [command('k', runStart)], [])
    expect(before.map((i) => i.key)).toEqual(['turn:1', 'command:k'])
    const after = buildTimeline([turn(1, '2026-10-06T08:00:00Z'), turn(2, '2026-10-06T08:02:00Z')], [command('k', runStart)], [])
    expect(after.map((i) => i.key)).toEqual(['turn:1', 'command:k', 'turn:2'])
  })

  it('orders several activities at one place: before-anchors, then commands ahead of the change, then list order', () => {
    const items = buildTimeline(
      [turn(1, null), turn(2, null, 'ai')],
      [change('c', at(2, 'after')), command('k2', at(2, 'after')), command('k1', at(2, 'before')), command('k3', at(2, 'after'))],
      [],
    )
    expect(items.map((i) => i.key)).toEqual(['turn:1', 'command:k1', 'turn:2', 'command:k2', 'command:k3', 'change:c'])
  })

  it('puts an anchor older than the loaded turns first and one past them after them', () => {
    const items = buildTimeline([turn(5, null), turn(6, null)], [command('late', at(9, 'after')), command('old', at(2, 'after')), change('zero', at(0, 'after'))], [])
    expect(items.map((i) => i.key)).toEqual(['change:zero', 'command:old', 'turn:5', 'turn:6', 'command:late'])
  })

  it('never places a command or change by its time: an unplaced (or anchor-less) one stays after every turn', () => {
    const items = buildTimeline(
      [turn(1, '2026-10-06T08:00:00Z'), turn(2, '2026-10-06T08:05:00Z')],
      [command('unplaced', UNPLACED, '2026-10-06T08:01:00Z'), change('bare', null, '2026-10-06T08:01:00Z')],
      [],
    )
    expect(items.map((i) => i.key)).toEqual(['turn:1', 'turn:2', 'command:unplaced', 'change:bare'])
  })

  it('keeps the time rule for the client\'s own status events, untimed ones last', () => {
    const items = buildTimeline(
      [turn(1, null), turn(2, '2026-10-06T08:05:00Z')],
      [status('untimed', null), status('early', '2026-10-06T08:00:00Z'), command('k', at(2, 'after'))],
      [],
    )
    expect(items.map((i) => i.key)).toEqual(['turn:1', 'status:early', 'turn:2', 'command:k', 'status:untimed'])
  })

  it('appends unsent messages last, pending or failed', () => {
    const pending: PendingTurn[] = [
      { localId: 'a', idempotencyKey: 'a', body: 'hi', basedOnSeq: 1, createdAt: '2026-10-06T07:00:00Z', failed: false },
      { localId: 'b', idempotencyKey: 'b', body: 'oops', basedOnSeq: 1, createdAt: '2026-10-06T07:00:01Z', failed: true },
    ]
    const items = buildTimeline([turn(1, '2026-10-06T08:00:00Z')], [command('k', at(1, 'after'))], pending)
    expect(items.map((i) => i.key)).toEqual(['turn:1', 'command:k', 'local:a', 'local:b'])
    const [, , a, b] = items
    expect(a.kind === 'message' && a.pending && !a.failed).toBe(true)
    expect(b.kind === 'message' && !b.pending && b.failed).toBe(true)
  })

  it('draws the read boundary above the first unread turn only when something above was read', () => {
    const turns = [turn(1, null), turn(2, null), turn(3, null)]
    expect(buildTimeline(turns, [], [], 1).map((i) => i.key)).toEqual(['turn:1', 'boundary:2', 'turn:2', 'turn:3'])
    expect(buildTimeline(turns, [], [], 0).some((i) => i.kind === 'boundary')).toBe(false)
    expect(buildTimeline(turns, [], [], 3).some((i) => i.kind === 'boundary')).toBe(false)
    expect(firstUnreadSeq([turn(5, null)], 2)).toBe(0)
  })
})

describe('upsertActivity', () => {
  it('replaces by id and appends new ones', () => {
    const list = upsertActivity([command('k', null)], { kind: 'command', id: 'k', createdAt: null, command: 'cmd k', state: 'approved' })
    expect(list).toHaveLength(1)
    expect(list[0].kind === 'command' && list[0].state).toBe('approved')
    expect(upsertActivity(list, change('c', null))).toHaveLength(2)
  })
})

describe('summarizeChange', () => {
  const files = [
    { path: 'client/src/main/components/ConversationView.vue', status: 'M', insertions: 30, deletions: 10 },
    { path: 'x.ts', status: 'A', insertions: 4, deletions: 1 },
  ]

  it('uses the server totals and names the first file', () => {
    expect(summarizeChange({ files, filesChanged: 2, insertions: 34, deletions: 11 })).toEqual({
      fileCount: 2,
      added: 34,
      removed: 11,
      firstPath: 'client/src/main/components/ConversationView.vue',
      firstName: 'ConversationView.vue',
    })
    expect(summarizeChange({ files: [], filesChanged: 0, insertions: null, deletions: null })).toEqual({
      fileCount: 0,
      added: null,
      removed: null,
      firstPath: null,
      firstName: null,
    })
  })

  it('sums the files with numbers when a total is missing, and keeps "no numbers" (binary) as null', () => {
    const binary = { path: 'logo.png', status: 'A', insertions: null, deletions: null }
    expect(summarizeChange({ files: [...files, binary], filesChanged: 3, insertions: null, deletions: null })).toMatchObject({ fileCount: 3, added: 34, removed: 11 })
    expect(summarizeChange({ files: [binary], filesChanged: 1, insertions: null, deletions: null })).toMatchObject({ fileCount: 1, added: null, removed: null })
  })
})

describe('replaceServerActivities', () => {
  it('keeps local status events, takes every command / change from the server and drops what it no longer lists', () => {
    const current = [status('s', null), command('gone', null), command('k', null)]
    const next = replaceServerActivities(current, [{ ...(command('k', at(1, 'after')) as object), state: 'running' } as ConversationActivity, change('c', null)])
    expect(next.map((a) => `${a.kind}:${a.id}`)).toEqual(['status:s', 'command:k', 'change:c'])
    expect(next[1].kind === 'command' && next[1].state).toBe('running')
  })

  it('holds a card whose decision is in flight and carries a 403 refusal over', () => {
    const optimistic = { ...(command('k', null) as object), state: 'approving' } as ConversationActivity
    const refused = { ...(command('r', null) as object), decisionForbidden: true } as ConversationActivity
    const next = replaceServerActivities([optimistic, refused], [command('k', null), command('r', null)], ['k'])
    expect(next[0].kind === 'command' && next[0].state).toBe('approving')
    expect(next[1].kind === 'command' && next[1].decisionForbidden).toBe(true)
  })
})

describe('time labels', () => {
  const now = new Date(2026, 9, 6, 12, 0)
  it('shows HH:mm today and MM/DD HH:mm otherwise', () => {
    expect(formatTimelineTime(new Date(2026, 9, 6, 8, 3).toISOString(), now)).toBe('08:03')
    expect(formatTimelineTime(new Date(2026, 9, 4, 8, 3).toISOString(), now)).toBe('10/04 08:03')
    expect(formatTimelineTime('not a date', now)).toBe('not a date')
    expect(formatTimelineTime(null, now)).toBe('')
  })

  it('marks chat list dates as today / yesterday / MM/DD', () => {
    expect(chatListDate(new Date(2026, 9, 6, 9, 5).toISOString(), now)).toEqual({ kind: 'today', text: '09:05' })
    expect(chatListDate(new Date(2026, 9, 5, 9, 5).toISOString(), now)).toEqual({ kind: 'yesterday', text: '' })
    expect(chatListDate(new Date(2026, 9, 4, 9, 5).toISOString(), now)).toEqual({ kind: 'date', text: '10/04' })
    expect(chatListDate(null, now)).toBeNull()
  })
})
