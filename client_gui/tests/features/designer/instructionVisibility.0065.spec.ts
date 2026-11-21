import { describe, expect, it } from 'vitest'
import { instructionVisible } from '@/features/designer/center/graph/instructionVisibility'
import { cardDocumentButtons } from '@/features/designer/center/canvas/cardDocumentButtons'
import { designCardSlots } from '@/features/designer/inspector/features/designCard/designCardModel'
import { researchCardSlots } from '@/features/designer/inspector/features/researchCard/researchCardModel'
import { taskCardSlots } from '@/features/designer/inspector/features/taskCard/taskCardModel'
import { gateCardSlots } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import { testSpecSlots } from '@/features/designer/inspector/features/testSpec/testSpecModel'
import { toCardDocument } from '@/features/project/flowCanvasApi'
import { toCardLineRecord } from '@/features/document/api/cardLine'

const line = { slot: 'instruction', docId: 'instruction', docType: 'DS', title: '', author: null, status: 'draft', docReviewStatus: null }
const cases = [
  { name: 'use', omitted: false, metadata: { docId: null }, visible: true },
  { name: 'omit absent', omitted: true, metadata: { docId: null }, visible: false },
  { name: 'omit empty', omitted: true, metadata: { hasBody: false, bodyState: 'readable' }, visible: false },
  { name: 'omit written', omitted: true, metadata: { hasBody: true, bodyState: 'readable' }, visible: true },
  { name: 'omit unknown', omitted: true, metadata: {}, visible: true },
  ...['read_error', 'file_not_found', 'file_path_missing', 'path_unresolved', 'future_state'].map(bodyState => ({ name: bodyState, omitted: true, metadata: { hasBody: false, bodyState }, visible: true })),
]
describe('0065 #33 instruction visibility', () => {
  it.each(cases)('$name: five Inspector models and Canvas share the body rule', ({ omitted, metadata, visible }) => {
    const instruction = { ...line, ...metadata, instructionOmitted: omitted }
    expect(instructionVisible(omitted, instruction)).toBe(visible)
    const facts = { cardKind: 'test' as const, docId: null, docType: null, docReviewStatus: null, docStatus: null, nodeRun: null, nodeType: 'custom', cardDocuments: [instruction] }
    for (const slots of [designCardSlots(facts, omitted), researchCardSlots(facts, omitted), taskCardSlots(facts, omitted), gateCardSlots(facts, omitted), testSpecSlots(null, facts, omitted)]) {
      expect(slots.some(slot => slot.key === 'instruction')).toBe(visible)
    }
    const buttons = cardDocumentButtons({ cardDocuments: [instruction], document: null })
    expect(buttons.some(button => button.ownerSlot === 'instruction')).toBe(visible && !!instruction.docId)
  })
  it.each([false, true])('keeps the shared TS scenario, independent of slot ordering (%s)', reverse => {
    const instruction = { ...line, docType: 'TS', hasBody: false, bodyState: 'readable', instructionOmitted: true }
    const scenario = { ...instruction, slot: 'scenario' }
    const lines = reverse ? [scenario, instruction] : [instruction, scenario]
    const buttons = cardDocumentButtons({ cardDocuments: lines, document: null })
    expect(buttons.map(button => [button.ownerSlot, button.scenario])).toEqual([['scenario', true]])
    expect(testSpecSlots(null, { cardKind: 'test', docId: null, docType: 'TS', docReviewStatus: null, docStatus: null, cardDocuments: lines }, true).map(slot => slot.key)).toEqual(['scenario', 'report'])
    expect(cardDocumentButtons({ cardDocuments: lines.map(row => ({ ...row, instructionOmitted: false })), document: null }).map(button => button.ownerSlot)).toEqual(['instruction', 'scenario'])
  })
  it('retains omission metadata across both transports', () => {
    const raw = { slot: 'instruction', doc_id: 'ds', instruction_omitted: true, has_body: false, body_state: 'readable' }
    expect(toCardDocument(raw).instructionOmitted).toBe(true)
    expect(toCardLineRecord(raw)?.instructionOmitted).toBe(true)
  })
})
