import { describe, expect, it } from 'vitest'
import { designCardSlots, type DesignCardFacts } from '@/features/designer/inspector/features/designCard/designCardModel'

describe('T0017 design instruction document identity', () => {
  it('keeps the existing DS accessible independently of its report, including when execution omits it', () => {
    const facts = {
      docId: 'design-report', docType: 'D', docReviewStatus: null, docStatus: 'open', nodeRun: null,
      cardDocuments: [
        { slot: 'instruction', docType: 'DS', docId: 'design-instruction', status: 'open', docReviewStatus: 'approved' },
        { slot: 'report', docType: 'D', docId: 'design-report', status: 'open', docReviewStatus: null },
      ],
    } as DesignCardFacts
    for (const omitted of [false, true]) {
      expect(designCardSlots(facts, omitted).map(({ key, id, open }) => ({ key, id, open }))).toEqual([
        { key: 'instruction', id: 'design-instruction', open: 'enabled' },
        { key: 'report', id: 'design-report', open: 'enabled' },
      ])
    }
  })
})
