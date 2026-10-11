/**
 * flowgate.v02.0048 T#8 — the attachments tab rules as tables (D#1 §8.5, §10.3, §10.4): the target
 * document of every card kind (an instruction and its report are separate Nodes, each with its own
 * attachments; no document -> none), the rows (server order, one per stored name, the upload name only
 * when it differs, the size unit), the tab number, the operation classes, availability with the
 * read-only rows of §8.5 across ownership × view and the reason order, the upload checks against the
 * server limits, what a mutability refusal blocks, and the keys with their ko / en / ja wording.
 */
import { describe, expect, it } from 'vitest'
import attachmentsMessages from '@/features/designer/inspector/features/attachments/messages'
import {
  ATTACHMENTS_OPERATION_CLASS,
  attachmentsAvailability,
  attachmentsBlockedBy,
  attachmentsDisabledReason,
  attachmentsErrorKey,
  attachmentsFailureKey,
  attachmentsSize,
  attachmentsTabCount,
  attachmentsTargetDocId,
  attachmentsUploadRefusal,
  attachmentsView,
  type AttachmentsFailureKind,
  type AttachmentsInput,
} from '@/features/designer/inspector/features/attachments/attachmentsModel'
import { inspectorCardKind, inspectorReadOnly, type InspectorReadOnly } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, targetOf } from '../support/inspectorSupport'

// --- Target document (D#1 §10.3) -------------------------------------------------------------------

describe('target document rule (flowgate.v02.0048 T#8)', () => {
  it.each([
    // [docType, docId of the selected Node] -> the attachments read are that document's
    ['R', 'p.m.0001.0001-R'],
    ['B', 'p.m.0001.0001-B'],
    ['N', 'p.m.0001.0002-N'],
    ['NR', 'p.m.0001.0003-NR'],
    ['T', 'p.m.0001.0004-T'],
    ['TR', 'p.m.0001.0005-TR'],
    ['T2', 'p.m.0001.0006-T2'],
    ['TR2', 'p.m.0001.0007-TR2'],
    ['DS', 'p.m.0001.0008-DS'],
    ['D', 'p.m.0001.0009-D'],
    ['TS', 'p.m.0001.0010-TS'],
    ['TSR', 'p.m.0001.0011-TSR'],
    ['V', 'p.m.0001.0012-V'],
    ['AC', 'p.m.0001.0013-AC'],
    ['WP', 'p.m.0001.0014-WP'],
    ['M', 'p.m.0001.0015-M'],
  ])('%s card: the selected Node’s own document (%s)', (docType, docId) => {
    expect(attachmentsTargetDocId({ docId, docType, cardKind: inspectorCardKind(docType) })).toBe(docId)
  })

  it.each([
    ['task instruction / report', 'T', 'TR'],
    ['research instruction / report', 'N', 'NR'],
    ['test instruction / result', 'TS', 'TSR'],
    ['design instruction / design', 'DS', 'D'],
  ])('%s are two Nodes, each with its own attachments', (_label, instruction, report) => {
    const a = attachmentsTargetDocId({ docId: `p.m.0001.0001-${instruction}`, docType: instruction, cardKind: inspectorCardKind(instruction) })
    const b = attachmentsTargetDocId({ docId: `p.m.0001.0002-${report}`, docType: report, cardKind: inspectorCardKind(report) })
    expect(a).toBe(`p.m.0001.0001-${instruction}`)
    expect(b).toBe(`p.m.0001.0002-${report}`)
    expect(a).not.toBe(b)
  })

  it.each([
    ['a Node without a document (Graph gate)', { docId: null, docType: null, cardKind: 'other' as const }],
    ['a document Node before its run', { docId: null, docType: 'TR', cardKind: 'task' as const }],
    ['no selection', null],
  ])('%s has no target document', (_label, target) => {
    expect(attachmentsTargetDocId(target)).toBeNull()
  })

  it.each(Object.keys(SELECTIONS) as (keyof typeof SELECTIONS)[])('fixture selection %s: the target document is the Node’s docId', (name) => {
    const target = targetOf(SELECTIONS[name])
    expect(attachmentsTargetDocId(target)).toBe(target.docId)
  })
})

// --- Rows, size, tab number -----------------------------------------------------------------------

describe('rows and tab number (flowgate.v02.0048 T#8)', () => {
  const item = (filename: string, fields: Partial<{ originalFilename: string | null; size: number; uploadedAt: string | null }> = {}) => ({
    filename,
    originalFilename: filename,
    size: 10,
    uploadedAt: null,
    ...fields,
  })

  it.each([
    [0, 'size.bytes', '0'],
    [1023, 'size.bytes', '1023'],
    [1024, 'size.kilobytes', '1.0'],
    [18342, 'size.kilobytes', '17.9'],
    [1024 * 1024 - 1, 'size.kilobytes', '1024.0'],
    [1024 * 1024, 'size.megabytes', '1.0'],
    [20971520, 'size.megabytes', '20.0'],
    [-5, 'size.bytes', '0'],
    [Number.NaN, 'size.bytes', '0'],
  ])('%s bytes -> %s %s', (bytes, key, value) => {
    expect(attachmentsSize(bytes)).toEqual({ key, value })
  })

  it('rows keep the server order, one per stored name, the upload name only when it differs', () => {
    const rows = attachmentsView([
      item('b.txt'),
      item('a.txt', { size: 2048, uploadedAt: '2026-10-09T10:00:00Z' }),
      item('a (1).txt', { originalFilename: 'a.txt' }),
      item('b.txt', { size: 99 }),
      item('', {}),
      item('c.bin', { originalFilename: null }),
    ])
    expect(rows).toEqual([
      { filename: 'b.txt', originalFilename: null, size: { key: 'size.bytes', value: '10' }, uploadedAt: null },
      { filename: 'a.txt', originalFilename: null, size: { key: 'size.kilobytes', value: '2.0' }, uploadedAt: '2026-10-09T10:00:00Z' },
      { filename: 'a (1).txt', originalFilename: 'a.txt', size: { key: 'size.bytes', value: '10' }, uploadedAt: null },
      { filename: 'c.bin', originalFilename: null, size: { key: 'size.bytes', value: '10' }, uploadedAt: null },
    ])
  })

  it.each([
    ['not read', null, null],
    ['no attachment', [], null],
    ['one', [1], 1],
    ['three', [1, 2, 3], 3],
    ['the per-document maximum', Array.from({ length: 200 }, (_, i) => i), 200],
  ] as const)('tab number: %s -> %s', (_label, items, count) => {
    expect(attachmentsTabCount(items as readonly unknown[] | null)).toBe(count)
  })
})

// --- Availability (D#1 §8.3 / §8.5) ---------------------------------------------------------------

describe('availability (flowgate.v02.0048 T#8)', () => {
  const base: AttachmentsInput = { readOnly: null, groupDisposed: false, aiRunning: false, blocked: null, count: 2, maxCount: 200, busy: false }
  const QUERIES = { read: { visible: true, enabled: true, reason: null }, open: { visible: true, enabled: true, reason: null } }

  it('listing and opening are queries, adding and deleting changes (D#1 §8.3 rule 0)', () => {
    expect(ATTACHMENTS_OPERATION_CLASS).toEqual({ read: 'query', open: 'query', add: 'change', delete: 'change' })
  })

  it.each([
    // §8.5 read-only rows: ownership × view -> readOnly (computed as inspectorTarget does)
    ['graph × revision', 'graph', 'revision', false, 'reason.read_only_revision'],
    ['graph × draft', 'graph', 'draft', true, null],
    ['unassigned × revision', 'unassigned', 'revision', false, 'reason.read_only_revision'],
    ['unassigned × draft', 'unassigned', 'draft', true, null],
    ['v01 × none', 'v01', null, true, null],
    ['conflict × revision', 'conflict', 'revision', false, 'reason.read_only_conflict'],
    ['conflict × none', 'conflict', null, false, 'reason.read_only_conflict'],
  ] as const)('%s -> add / delete enabled %s, %s; list and open enabled', (_label, ownership, viewMode, enabled, reason) => {
    const readOnly = inspectorReadOnly(ownership, viewMode)
    expect(attachmentsAvailability({ ...base, readOnly })).toEqual({
      ...QUERIES,
      add: { visible: true, enabled, reason },
      delete: { visible: true, enabled, reason },
    })
  })

  it.each([
    ['revision × AI running', { readOnly: 'revision', aiRunning: true }, 'reason.read_only_revision', 'reason.read_only_revision'],
    ['revision × disposed', { readOnly: 'revision', groupDisposed: true }, 'reason.read_only_revision', 'reason.read_only_revision'],
    ['conflict over everything', { readOnly: 'conflict', groupDisposed: true, aiRunning: true, blocked: 'not_mutable', count: 200, busy: true }, 'reason.read_only_conflict', 'reason.read_only_conflict'],
    ['disposed', { groupDisposed: true }, 'reason.disposed', 'reason.disposed'],
    ['disposed + AI running + busy', { groupDisposed: true, aiRunning: true, busy: true }, 'reason.disposed', 'reason.disposed'],
    ['AI running', { aiRunning: true }, 'reason.ai_running', 'reason.ai_running'],
    ['409 group_disposed (no fact read says so)', { blocked: 'group_disposed' }, 'reason.disposed', 'reason.disposed'],
    ['409 group_ai_running / 423', { blocked: 'group_ai_running' }, 'reason.ai_running', 'reason.ai_running'],
    ['409 DOCUMENT_NOT_MUTABLE, other reason', { blocked: 'not_mutable' }, 'reason.not_mutable', 'reason.not_mutable'],
    ['409 not mutable + at the maximum + busy', { blocked: 'not_mutable', count: 200, busy: true }, 'reason.not_mutable', 'reason.not_mutable'],
    ['at the maximum: adding only', { count: 200 }, 'reason.limit', null],
    ['one below the maximum', { count: 199 }, null, null],
    ['at the maximum + busy', { count: 200, busy: true }, 'reason.limit', 'reason.busy'],
    ['list not read yet', { count: null }, null, null],
    ['a change in flight', { busy: true }, 'reason.busy', 'reason.busy'],
    ['nothing holds', {}, null, null],
  ] as const)('%s -> add %s, delete %s', (_label, fields, addReason, deleteReason) => {
    const input = { ...base, ...(fields as Partial<AttachmentsInput>) }
    expect(attachmentsDisabledReason(input, 'add')).toBe(addReason)
    expect(attachmentsDisabledReason(input, 'delete')).toBe(deleteReason)
    expect(attachmentsAvailability(input)).toEqual({
      ...QUERIES,
      add: { visible: true, enabled: addReason === null, reason: addReason },
      delete: { visible: true, enabled: deleteReason === null, reason: deleteReason },
    })
  })

  it.each(['conflict', 'revision', null] as InspectorReadOnly[])('listing and opening stay enabled when read only is %s, whatever else holds', (readOnly) => {
    const availability = attachmentsAvailability({ ...base, readOnly, groupDisposed: true, aiRunning: true, blocked: 'not_mutable', count: 200, busy: true })
    expect([availability.read, availability.open]).toEqual([QUERIES.read, QUERIES.open])
  })

  it('every card kind × ownership: the same rule (a document feature, D#1 §8.3 rules 2-4)', () => {
    for (const name of Object.keys(SELECTIONS) as (keyof typeof SELECTIONS)[]) {
      const target = targetOf(SELECTIONS[name])
      const availability = attachmentsAvailability({ ...base, readOnly: target.readOnly })
      expect(availability.add.visible && availability.delete.visible, name).toBe(true)
      expect(availability.add.enabled, name).toBe(target.readOnly === null)
    }
  })
})

// --- Upload checks, refusals ----------------------------------------------------------------------

describe('upload checks and refusals (flowgate.v02.0048 T#8)', () => {
  const LIMITS = { maxBytes: 20971520, maxFiles: 20, maxCount: 200 }
  const file = (name: string, size = 10) => ({ name, size })

  it.each([
    ['no file', [], 2, { key: 'refusal.no_file', params: {} }],
    ['one small file', [file('a.txt')], 2, null],
    ['exactly 20 MiB', [file('a.bin', 20971520)], 2, null],
    ['one byte over 20 MiB', [file('ok.txt'), file('big.bin', 20971521)], 2, { key: 'refusal.too_large', params: { name: 'big.bin', limit: 20 } }],
    ['20 files', Array.from({ length: 20 }, (_, i) => file(`f${i}`)), 0, null],
    ['21 files', Array.from({ length: 21 }, (_, i) => file(`f${i}`)), 0, { key: 'refusal.too_many_files', params: { limit: 20 } }],
    ['fills the document exactly', [file('a'), file('b')], 198, null],
    ['one over the document maximum', [file('a'), file('b')], 199, { key: 'refusal.limit', params: { limit: 200 } }],
    ['list not read: the server decides', [file('a')], null, null],
  ] as const)('%s', (_label, files, count, refusal) => {
    expect(attachmentsUploadRefusal(files, count, LIMITS)).toEqual(refusal)
  })

  it.each([
    ['not_mutable', 'group_disposed', 'group_disposed'],
    ['not_mutable', 'group_ai_running', 'group_ai_running'],
    ['not_mutable', 'reserved_work_plan_attachment', 'not_mutable'],
    ['not_mutable', null, 'not_mutable'],
    ['running', null, 'group_ai_running'],
    ['limit', 'per_document_limit', null],
    ['too_large', null, null],
    ['not_found', null, null],
    ['forbidden', null, null],
    ['invalid', null, null],
    ['server', 'storage', null],
    ['network', null, null],
    ['other', null, null],
  ] as const)('a %s refusal (reason %s) blocks %s', (kind, reason, blocked) => {
    expect(attachmentsBlockedBy(kind, reason)).toBe(blocked)
  })
})

// --- Keys and wording -----------------------------------------------------------------------------

describe('keys and wording (flowgate.v02.0048 T#8)', () => {
  const kinds: AttachmentsFailureKind[] = ['not_mutable', 'running', 'limit', 'too_large', 'not_found', 'forbidden', 'invalid', 'server', 'network', 'other']
  const lookup = (messages: Record<string, unknown>, key: string) =>
    key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), messages)

  it('failure keys by operation', () => {
    expect(kinds.map((kind) => attachmentsFailureKey('add', kind))).toEqual([
      'failure.not_mutable',
      'failure.running',
      'failure.add_limit',
      'failure.add_too_large',
      'failure.not_found',
      'failure.forbidden',
      'failure.add_invalid',
      'failure.server',
      'failure.network',
      'failure.add_other',
    ])
    expect(kinds.map((kind) => attachmentsFailureKey('delete', kind))).toEqual([
      'failure.not_mutable',
      'failure.running',
      'failure.delete_other',
      'failure.delete_other',
      'failure.not_found',
      'failure.forbidden',
      'failure.delete_other',
      'failure.server',
      'failure.network',
      'failure.delete_other',
    ])
    expect(attachmentsFailureKey('open', 'other')).toBe('failure.open_other')
    expect(attachmentsFailureKey('open', 'not_found')).toBe('failure.not_found')
  })

  it('read error keys', () => {
    expect(kinds.map(attachmentsErrorKey)).toEqual([
      'error.other',
      'error.other',
      'error.other',
      'error.other',
      'error.not_found',
      'error.forbidden',
      'error.other',
      'error.other',
      'error.network',
      'error.other',
    ])
  })

  it.each(['ko', 'en', 'ja'] as const)('every key the rules produce has %s wording', (locale) => {
    const keys = [
      ...['read_only_conflict', 'read_only_revision', 'disposed', 'ai_running', 'not_mutable', 'limit', 'busy'].map((reason) => `reason.${reason}`),
      ...(['add', 'delete', 'open'] as const).flatMap((operation) => kinds.map((kind) => attachmentsFailureKey(operation, kind))),
      ...kinds.map(attachmentsErrorKey),
      ...['no_file', 'too_many_files', 'too_large', 'limit'].map((refusal) => `refusal.${refusal}`),
      ...['bytes', 'kilobytes', 'megabytes'].map((unit) => `size.${unit}`),
      'empty',
      'add',
      'add_tip',
      'open',
      'delete',
      'delete_confirm',
      'delete_yes',
      'delete_no',
      'original',
    ]
    for (const key of keys) expect(typeof lookup(attachmentsMessages[locale] as Record<string, unknown>, key), `${locale} ${key}`).toBe('string')
  })

  it('no AI hand-off wording (D#1 §8.4 첨부 🤖 AI 전달)', () => {
    const text = JSON.stringify(attachmentsMessages)
    for (const word of ['AI 전달', '🤖', 'AI hand', 'AI 伝達']) expect(text).not.toContain(word)
  })
})
