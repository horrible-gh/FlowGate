/**
 * flowgate.v02.0053 T#7 — which Nodes have a settings form (inspectorSettingsKind) and the registry condition
 * that puts the settings parts on them (`settingsKinds`): every Graph card by the kind the server keeps its
 * settings by (T#2 `fields.card_kind` — Custom runs, a gate is a check, a document card goes by its type),
 * just placed or done alike — a done card by its own (definition) type, not by the result its run bound (N → NR,
 * T → TR, TS → TSR); a v0.1 Flow's Node as before (run / test only as a sequence step).
 */
import { describe, expect, it } from 'vitest'
import { inspectorRegistry, registrationApplies, registrationViolations } from '@/features/designer/inspector/inspectorRegistry'
import type { FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import { inspectorCardNodeKey, inspectorSettingsKind, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, probePart, registration, registrationModules, targetOf } from '../support/inspectorSupport'

const PART = probePart('part')
const graphTest = targetOf(SELECTIONS.graph)
const v01Step = targetOf(SELECTIONS.v01NoDocument)

/** A Graph card of a node type / document type, just placed (no document, no run). */
const card = (nodeType: string, docType: string | null, fields: Partial<InspectorTarget> = {}): InspectorTarget => ({
  ...graphTest,
  nodeId: `gn_${nodeType}_${docType ?? 'none'}`,
  docId: null,
  docType,
  cardKind: 'other',
  nodeType,
  cardDocType: docType,
  nodeRun: null,
  ...fields,
})

const entry = (key: string) => inspectorRegistry.entries.find((item) => item.key === key)!
const SETTINGS_PARTS = ['settings', 'requirementSettings', 'testRunSettings', 'checkSettings']
const partsOf = (target: InspectorTarget) => inspectorRegistry.entries.filter((item) => SETTINGS_PARTS.includes(item.key) && registrationApplies(item, target)).map((item) => item.key)

describe('inspectorSettingsKind (flowgate.v02.0053 T#7)', () => {
  it.each([
    ['document', 'N', 'run'],
    ['document', 'T', 'run'],
    ['document', 'T2', 'run'],
    ['document', 'D', 'run'],
    ['document', 'P', 'run'],
    ['document', 'L', 'run'],
    ['document', 'DB', 'run'],
    ['document', 'TS', 'test'],
    ['document', 'R', 'requirement'],
    ['document', 'B', 'requirement'],
    ['gate', null, 'check'],
    ['custom', null, 'run'],
    // flowgate.v02.0072 T#3: a Graph work plan card keeps the plan settings.
    ['document', 'WP', 'plan'],
    ['document', 'NR', null],
    ['document', 'TR', null],
    ['document', 'XYZ', null],
    ['start', null, null],
  ] as const)('a Graph %s card of type %s has %s settings (the server card kind)', (nodeType, docType, kind) => {
    expect(inspectorSettingsKind(card(nodeType, docType))).toBe(kind)
  })

  it('a Graph card keeps its kind whatever its state: just placed, running, done, in a revision', () => {
    for (const fields of [{}, { nodeRun: graphTest.nodeRun }, { docId: 'x.0001-T', docReviewStatus: 'approved' }, { readOnly: 'revision' as const }]) {
      expect(inspectorSettingsKind(card('document', 'T', fields))).toBe('run')
    }
  })

  it('a done card is read by its own type, not by the result bound (the server keeps its settings by the card)', () => {
    for (const [own, result, kind] of [['N', 'NR', 'run'], ['T', 'TR', 'run'], ['T2', 'TR2', 'run'], ['TS', 'TSR', 'test']] as const) {
      expect(inspectorSettingsKind(card('document', own, { docType: result, docId: `x.0009-${result}` }))).toBe(kind)
    }
  })

  it('a v0.1 Node: run / test only as a sequence step; requirement and check always (drawn without a store)', () => {
    expect(inspectorSettingsKind(v01Step)).toBe('run')
    expect(inspectorSettingsKind({ ...v01Step, slot: null })).toBeNull()
    expect(inspectorSettingsKind({ ...v01Step, cardKind: 'test' })).toBe('test')
    expect(inspectorSettingsKind({ ...v01Step, cardKind: 'test', slot: null })).toBeNull()
    // flowgate.v02.0072 T#3: a v0.1 Flow's work plan Node has no store (only a Graph WP card keeps plan settings).
    expect(inspectorSettingsKind({ ...v01Step, cardKind: 'plan' })).toBeNull()
    expect(inspectorSettingsKind({ ...v01Step, cardKind: 'plan', slot: null })).toBeNull()
    expect(inspectorSettingsKind({ ...v01Step, cardKind: 'requirement', slot: null })).toBe('requirement')
    expect(inspectorSettingsKind({ ...v01Step, cardKind: 'check', slot: null })).toBe('check')
  })

  it('the stored card key: a Graph card’s node_key, never a `tmp_` card or a Node without a definition', () => {
    expect(inspectorCardNodeKey(card('document', 'T'))).toBe('gn_document_T')
    expect(inspectorCardNodeKey(card('document', 'T', { nodeId: 'tmp_1' }))).toBeNull()
    expect(inspectorCardNodeKey(v01Step)).toBeNull()
    expect(inspectorCardNodeKey(null)).toBeNull()
  })
})

describe('settingsKinds (registry condition)', () => {
  it('a registration applies only to Nodes of its settings kinds; absent or empty = every Node', () => {
    const only = registration({ key: 'a', order: 1, requiresDocument: false, settingsKinds: ['check'], headerMeta: PART })
    expect(registrationApplies(only, card('gate', null))).toBe(true)
    expect(registrationApplies(only, card('document', 'T'))).toBe(false)
    expect(registrationApplies(only, card('document', 'WP'))).toBe(false)
    for (const settingsKinds of [undefined, []]) {
      expect(registrationApplies(registration({ key: 'a', order: 1, requiresDocument: false, settingsKinds, headerMeta: PART }), card('document', 'WP'))).toBe(true)
    }
  })

  it('an unknown kind is a contract violation naming the folder', () => {
    const modules = registrationModules([registration({ key: 'a', order: 1, headerMeta: PART, settingsKinds: ['run', 'deploy' as never] })])
    expect(registrationViolations(modules)).toEqual(["features/a: settingsKinds has unknown value 'deploy'"])
  })

  it('the four settings parts declare their kinds', () => {
    expect(entry('settings').settingsKinds).toEqual(['run', 'test', 'plan'])
    expect(entry('testRunSettings').settingsKinds).toEqual(['test'])
    expect(entry('requirementSettings').settingsKinds).toEqual(['requirement'])
    expect(entry('checkSettings').settingsKinds).toEqual(['check'])
  })

  it.each([
    ['조사', 'document', 'N', ['settings']],
    ['작업', 'document', 'T', ['settings']],
    ['반영', 'document', 'T2', ['settings']],
    ['설계', 'document', 'D', ['settings']],
    ['DB 설계', 'document', 'DB', ['settings']],
    ['Custom', 'custom', null, ['settings']],
    ['테스트', 'document', 'TS', ['settings', 'testRunSettings']],
    ['요구사항', 'document', 'R', ['requirementSettings']],
    ['버그', 'document', 'B', ['requirementSettings']],
    ['확인', 'gate', null, ['checkSettings']],
    // flowgate.v02.0072 T#3: the settings tab draws the WP card's switch and run block.
    ['작업계획', 'document', 'WP', ['settings']],
  ] as const)('a just placed %s card gets its settings parts', (_name, nodeType, docType, parts) => {
    expect(partsOf(card(nodeType, docType))).toEqual(parts)
    // in every ownership, done or not
    expect(partsOf(card(nodeType, docType, { ownership: 'unassigned' }))).toEqual(parts)
    expect(partsOf(card(nodeType, docType, { canvasState: 'done', docId: 'x.0001-X' }))).toEqual(parts)
  })

  // The Canvas hands a done card its run's result as the bound document (graph_projector: node_run.result_doc_id).
  it.each([
    ['조사', 'research', 'N', 'NR', ['settings']],
    ['작업', 'task', 'T', 'TR', ['settings']],
    ['반영', 'task', 'T2', 'TR2', ['settings']],
    ['테스트', 'test', 'TS', 'TSR', ['settings', 'testRunSettings']],
  ] as const)('a done %s card whose run bound its result keeps its settings parts (real target translation)', (_name, kind, own, result, parts) => {
    const done: FlowGraphNode = {
      id: `gn_done_${own}`,
      kind,
      title: '완료',
      state: 'done',
      docType: own,
      x: 0,
      y: 0,
      definition: { nodeType: 'document', docType: own, paletteItemId: null, label: '완료', assignee: null, rootBound: false },
      document: { docId: `flowgate.v02.0049.0009-${result}`, typeCode: result, seq: 9, title: '완료', docReviewStatus: 'approved', status: 'closed', supersedes: [] },
      slot: null,
      nodeRun: { status: 'completed', openedAt: '2026-10-09T01:00:00Z', finishedAt: '2026-10-09T01:20:00Z', resultDocId: `flowgate.v02.0049.0009-${result}` },
    }
    const target = targetOf({ ...SELECTIONS.graph, node: done })
    expect(target).toMatchObject({ docType: result, cardDocType: own })
    expect(inspectorSettingsKind(target)).not.toBeNull()
    expect(partsOf(target)).toEqual(parts)
  })
})
