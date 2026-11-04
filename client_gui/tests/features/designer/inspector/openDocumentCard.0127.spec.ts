/**
 * flowgate.v02.0127 T#5 — the card coordinates an opener hands to the document window (0009-L §2.11): taken from the
 * target and the pressed line's slot at the time of the press; a sharing line resolves to its owner; nothing is guessed.
 */
import { describe, expect, it } from 'vitest'
import { cardDocumentContext } from '@/features/designer/inspector/openDocument'

describe('the opener coordinates (§2.11)', () => {
  const target = (cardDocuments: object[], nodeId = 'gn_a', nodeType: string | null = 'document') =>
    ({ projectId: 'flowgate', flowId: 'flowgate.v02.0127', nodeId, nodeType, cardDocuments }) as never
  const line = (slot: string, docId: string | null) => ({ slot, docId, docType: 'TS', title: 't', author: 'human', status: 'open', docReviewStatus: null })

  it('takes flow / node / slot from the target and the pressed line, never from a later global read', () => {
    const t = target([line('requirement', 'flowgate.v02.0127.0001-R'), line('instruction', 'flowgate.v02.0127.0002-N')])
    expect(cardDocumentContext(t, 'flowgate.v02.0127.0002-N')).toEqual({
      projectId: 'flowgate', flowId: 'flowgate.v02.0127', nodeKey: 'gn_a', ownerSlot: 'instruction', openedDocId: 'flowgate.v02.0127.0002-N',
    })
    expect(cardDocumentContext(t, 'flowgate.v02.0127.0002-N', 'requirement')?.ownerSlot).toBe('requirement')
  })

  it('the test card scenario line shares the instruction document and is edited through the instruction line', () => {
    const ts = 'flowgate.v02.0127.0030-TS'
    expect(cardDocumentContext(target([line('instruction', ts), line('scenario', ts)]), ts, 'scenario')?.ownerSlot).toBe('instruction')
    expect(cardDocumentContext(target([line('scenario', ts)]), ts)?.ownerSlot).toBe('scenario')
  })

  it('no context for a node without a stored card, a tmp_ card or a document no line carries (never guessed)', () => {
    expect(cardDocumentContext(null, 'd')).toBeNull()
    expect(cardDocumentContext(target([line('instruction', 'd')], 'gn_a', null), 'd')).toBeNull()
    expect(cardDocumentContext(target([line('instruction', 'd')], 'tmp_1'), 'd')).toBeNull()
    expect(cardDocumentContext(target([line('instruction', 'd')]), 'other')).toBeNull()
  })
})
