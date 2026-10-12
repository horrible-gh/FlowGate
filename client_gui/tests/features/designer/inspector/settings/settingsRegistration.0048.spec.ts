// flowgate.v02.0048 T#9 — the settings tab's declared conditions (D#2 §2.4 rule 3, D#1 §5.3 rules 1·6):
// the tab exists only on a Node that has a v0.1 sequence slot. A free document on a v0.1 Flow has no
// sequence row and therefore no tab — decided by the declared `requiresSlot`, not by the tab's content.
// flowgate.v02.0053 T#7: on a v0.1 Flow that is still so, now through the declared `settingsKinds` (a v0.1
// Node has run / test settings only as a sequence step); the `requiresSlot` condition itself is unchanged.
import { describe, expect, it } from 'vitest'
import settingsRegistration from '@/features/designer/inspector/features/settings/register'
import {
  inspectorRegistry,
  registrationApplies,
  registrationViolations,
} from '@/features/designer/inspector/inspectorRegistry'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, probePart, registration, registrationModules, targetOf } from '../support/inspectorSupport'

const PART = probePart('part')
const SLOT = targetOf(SELECTIONS.v01NoDocument).slot!
const v01 = targetOf(SELECTIONS.v01)
const freeDoc = (docType: string, cardKind: InspectorTarget['cardKind']): InspectorTarget => ({
  ...v01,
  nodeId: `doc:free-${docType}`,
  docId: `flowgate.v02.0048.0099-${docType}`,
  docType,
  cardKind,
  slot: null,
})

describe('requiresSlot (registry condition)', () => {
  // flowgate.v02.0053 T#7: the settings tab follows the Node's settings kind (a Graph card has its own settings,
  // a v0.1 Node keeps them as a sequence step), see settingsKinds.0053; the slot rule stays inside it.
  it('the settings registration declares settings kinds instead', () => {
    expect(settingsRegistration.requiresSlot).toBeUndefined()
    expect(settingsRegistration.settingsKinds).toEqual(['run', 'test'])
    expect(inspectorRegistry.entries.find((entry) => entry.key === 'settings')?.settingsKinds).toEqual(['run', 'test'])
  })

  it('a registration with requiresSlot applies only to a target with a slot', () => {
    const entry = registration({ key: 'a', order: 1, requiresDocument: false, requiresSlot: true, headerMeta: PART })
    expect(registrationApplies(entry, { ...v01, slot: SLOT })).toBe(true)
    expect(registrationApplies(entry, { ...v01, slot: null })).toBe(false)
  })

  it('absent or false changes nothing (other registrations keep applying to slot-less Nodes)', () => {
    for (const requiresSlot of [undefined, false]) {
      const entry = registration({ key: 'a', order: 1, requiresDocument: false, requiresSlot, headerMeta: PART })
      expect(registrationApplies(entry, { ...v01, slot: null })).toBe(true)
    }
  })

  it('a non-boolean value is a contract violation naming the folder', () => {
    const modules = registrationModules([registration({ key: 'a', order: 1, headerMeta: PART, requiresSlot: 'yes' as never })])
    expect(registrationViolations(modules)).toEqual(['features/a: requiresSlot is not a boolean'])
  })
})

describe('the settings tab on a v0.1 Flow', () => {
  it.each([
    ['N', 'research'],
    ['NR', 'research'],
    ['T', 'task'],
    ['TR', 'task'],
    ['D', 'design'],
    ['DS', 'design'],
    ['TS', 'test'],
    ['V', 'test'],
  ] as const)('a free %s document (no slot) gets no settings tab', (docType, cardKind) => {
    expect(registrationApplies(settingsRegistration, freeDoc(docType, cardKind))).toBe(false)
  })

  it('a step Node keeps it: with a document (started) and without one (not started)', () => {
    expect(registrationApplies(settingsRegistration, { ...v01, slot: SLOT })).toBe(true)
    expect(registrationApplies(settingsRegistration, targetOf(SELECTIONS.v01NoDocument))).toBe(true)
  })
})
