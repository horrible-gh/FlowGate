// F81-9 (flowgate.v02test.0081.0017-T): a TS document never opens with a 「지시서」 title, whoever opens it (the Canvas,
// the document explorer, a notice): the document window's title and the Canvas 시나리오 button use documentTitleShown.
import { describe, expect, it } from 'vitest'
import { documentTitleShown } from '@/features/documentWindow/documentWindowModel'

describe('F81-9 documentTitleShown', () => {
  it('shows a TS stored as 「… · 테스트 · 지시서」 as its 시나리오', () => {
    expect(documentTitleShown('Card · 테스트 · 지시서', 'TS', '시나리오')).toBe('Card · 테스트 · 시나리오')
    expect(documentTitleShown('Card · Test · Instruction', ' ts ', 'Scenario')).toBe('Card · Test · Scenario')
    expect(documentTitleShown('Card · テスト · 指示書', 'TS', 'シナリオ')).toBe('Card · テスト · シナリオ')
  })
  it('keeps the card name as written and a TS title without the role', () => {
    expect(documentTitleShown('TS 지시서 제거 · 테스트 · 시나리오', 'TS', '시나리오')).toBe('TS 지시서 제거 · 테스트 · 시나리오')
    expect(documentTitleShown('Card', 'TS', '시나리오')).toBe('Card')
  })
  it('leaves every other document alone and an empty title empty', () => {
    expect(documentTitleShown('Card · 작업 · 지시서', 'T', '시나리오')).toBe('Card · 작업 · 지시서')
    expect(documentTitleShown('Card · 지시서', null, '시나리오')).toBe('Card · 지시서')
    expect(documentTitleShown(null, 'TS', '시나리오')).toBeNull()
    expect(documentTitleShown('', 'TS', '시나리오')).toBeNull()
  })
})
