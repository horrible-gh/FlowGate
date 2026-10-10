/**
 * flowgate.v02.0048 T#1 — Inspector registry (D#1 §5): the pure collector over glob results
 * (contract checks that throw, `order` sorting), the declared conditions per target (card kind,
 * docTypes, ownership, document needed) and the feature string merge.
 */
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import {
  contributionsFor,
  createInspectorRegistry,
  inspectorRegistry,
  installInspectorMessages,
  registrationApplies,
  registrationViolations,
} from '@/features/designer/inspector/inspectorRegistry'
import {
  SELECTIONS,
  fixtureRegistry,
  probePart,
  registration,
  registrationModules,
  selectionOf,
  targetOf,
  GRAPH_GRAPH,
  GRAPH_TEST_NODE,
} from './support/inspectorSupport'

const PART = probePart('part')

describe('createInspectorRegistry — contract checks (D#1 §5.4)', () => {
  it('builds the registry in `order` from valid modules', () => {
    const registry = fixtureRegistry([
      registration({ key: 'b', order: 600, headerAction: PART }),
      registration({ key: 'a', order: 500, tab: { key: 'documents', component: PART } }),
    ])
    expect(registry.entries.map((entry) => entry.key)).toEqual(['a', 'b'])
  })

  it.each([
    ['a key that differs from its folder', [registration({ key: 'other', order: 1, headerMeta: PART })], ['alpha'], /features\/alpha: key 'other' differs from the folder name/],
    ['a duplicate key', [registration({ key: 'dup', order: 1, headerMeta: PART }), registration({ key: 'dup', order: 2, headerMeta: PART })], ['dup', 'dupe'], /features\/dupe: key 'dup' is already registered by features\/dup/],
    ['an order used twice', [registration({ key: 'a', order: 5, headerMeta: PART }), registration({ key: 'b', order: 5, headerMeta: PART })], undefined, /features\/b: order 5 is already used by features\/a/],
    ['an unknown card kind', [registration({ key: 'a', order: 1, headerMeta: PART, cardKinds: ['memo' as never] })], undefined, /cardKinds has unknown value 'memo'/],
    ['an unknown ownership', [registration({ key: 'a', order: 1, headerMeta: PART, ownerships: ['legacy'] })], undefined, /ownerships has unknown value 'legacy'/],
    ['an unknown doc type', [registration({ key: 'a', order: 1, headerMeta: PART, docTypes: ['M'] })], undefined, /docTypes has unknown value 'M'/],
    ['a doc type outside its card kinds', [registration({ key: 'a', order: 1, headerMeta: PART, cardKinds: ['test'], docTypes: ['TS', 'WP'] })], undefined, /docTypes 'WP' is not a type of cardKinds \[test\]/],
    ['an unknown tab key', [registration({ key: 'a', order: 1, tab: { key: 'memo' as never, component: PART } })], undefined, /tab key 'memo' is not an Inspector tab/],
    ['no contribution', [registration({ key: 'a', order: 1 })], undefined, /contributes no tab, header action, header meta, header kind or header menu/],
    ['a missing locale', [registration({ key: 'a', order: 1, headerMeta: PART, messages: { ko: {}, en: {} } as never })], undefined, /messages has no 'ja' block/],
  ])('throws on %s, naming the folder', (_label, entries, folders, message) => {
    const modules = registrationModules(entries, folders)
    expect(() => createInspectorRegistry(modules)).toThrow(message)
    expect(registrationViolations(modules).join('\n')).toMatch(message)
  })

  it('throws on a register.ts without a default export', () => {
    expect(() => createInspectorRegistry({ './features/empty/register.ts': {} })).toThrow(/features\/empty: register.ts has no default export/)
  })

  it('accepts docTypes of any card kind when cardKinds is empty', () => {
    expect(registrationViolations(registrationModules([registration({ key: 'a', order: 1, headerMeta: PART, docTypes: ['WP', 'TS'] })]))).toEqual([])
  })

  it('the module registry (real feature folders) builds without violations', () => {
    expect(Array.isArray(inspectorRegistry.entries)).toBe(true)
  })
})

describe('declared conditions per target (D#1 §5.3)', () => {
  const testSpec = registration({ key: 'testSpec', order: 420, cardKinds: ['test'], docTypes: ['TS', 'TSR'], tab: { key: 'documents', component: PART } })
  const docs = registration({ key: 'documents', order: 500, tab: { key: 'documents', component: PART } })
  const settings = registration({ key: 'settings', order: 900, ownerships: ['v01'], cardKinds: ['task', 'test'], tab: { key: 'settings', component: PART } })
  const header = registration({ key: 'meta', order: 100, requiresDocument: false, headerMeta: PART })
  const registry = fixtureRegistry([settings, docs, testSpec, header])

  const testTarget = (docType: string) =>
    targetOf(selectionOf({ ...GRAPH_GRAPH, nodes: [{ ...GRAPH_TEST_NODE, document: { ...GRAPH_TEST_NODE.document!, typeCode: docType } }] }, GRAPH_TEST_NODE.id))

  it.each([['TS', true], ['TSR', true], ['V', false]])('docTypes [TS, TSR] on a %s card -> %s', (docType, shown) => {
    const target = testTarget(docType)
    expect(target.cardKind).toBe('test')
    expect(registrationApplies(testSpec, target)).toBe(shown)
    expect(contributionsFor(registry, target).map((entry) => entry.key).includes('testSpec')).toBe(shown)
  })

  it('keeps `order` and filters by ownership / card kind', () => {
    expect(contributionsFor(registry, targetOf(SELECTIONS.v01)).map((entry) => entry.key)).toEqual(['meta', 'documents', 'settings'])
    expect(contributionsFor(registry, targetOf(SELECTIONS.graph)).map((entry) => entry.key)).toEqual(['meta', 'testSpec', 'documents'])
    expect(contributionsFor(registry, targetOf(SELECTIONS.unassigned)).map((entry) => entry.key)).toEqual(['meta', 'documents'])
  })

  it('a Node without a document gets only what does not need one', () => {
    expect(contributionsFor(registry, targetOf(SELECTIONS.graphGate)).map((entry) => entry.key)).toEqual(['meta'])
    expect(contributionsFor(registry, targetOf(SELECTIONS.v01NoDocument)).map((entry) => entry.key)).toEqual(['meta'])
  })

  it('nothing without a target', () => {
    expect(contributionsFor(registry, null)).toEqual([])
  })
})

describe('installInspectorMessages (D#1 §5.5)', () => {
  const registry = fixtureRegistry([
    registration({
      key: 'sample',
      order: 1,
      headerMeta: PART,
      messages: { ko: { title: '샘플', reason: { read_only_conflict: '충돌' } }, en: { title: 'Sample', reason: { read_only_conflict: 'Conflict' } }, ja: { title: 'サンプル', reason: { read_only_conflict: '競合' } } },
    }),
  ])

  it('merges each feature under gui.inspector.<key>, and again changes nothing', () => {
    const i18n = createI18n({ legacy: false, locale: 'ko', fallbackLocale: 'en', messages: { ko: { gui: { designer: { x: '유지' } } }, en: {}, ja: {} } })
    installInspectorMessages(i18n.global, registry)
    installInspectorMessages(i18n.global, registry)
    expect(i18n.global.t('gui.inspector.sample.title')).toBe('샘플')
    expect(i18n.global.t('gui.inspector.sample.reason.read_only_conflict')).toBe('충돌')
    expect(i18n.global.t('gui.designer.x')).toBe('유지')
    i18n.global.locale.value = 'ja'
    expect(i18n.global.t('gui.inspector.sample.title')).toBe('サンプル')
    expect(i18n.global.getLocaleMessage('ko')).toEqual({ gui: { designer: { x: '유지' }, inspector: { sample: { title: '샘플', reason: { read_only_conflict: '충돌' } } } } })
  })
})
