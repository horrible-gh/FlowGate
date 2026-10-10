// flowgate.v02.0048 T#9 — the settings tab's declared conditions (D#2 §2.4 rule 3, D#1 §5.3 rules 1·6):
// the tab exists only on a Node that has a v0.1 sequence slot. A free document on a v0.1 Flow has no
// sequence row and therefore no tab — decided by the declared `requiresSlot`, not by the tab's content.
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
  it('is declared by the settings registration', () => {
    expect(settingsRegistration.requiresSlot).toBe(true)
    expect(inspectorRegistry.entries.find((entry) => entry.key === 'settings')?.requiresSlot).toBe(true)
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
