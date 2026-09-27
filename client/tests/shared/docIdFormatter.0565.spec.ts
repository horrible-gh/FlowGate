import { describe, expect, it } from 'vitest'
import { formatDocId, slashToNormalFormat, normalizeDashFormat } from '../../shared/utils/docIdFormatter'

describe('document ID formatter for T2 and TR2', () => {
  it('keeps canonical TR2 and strips redundant slash suffix', () => {
    const id = 'flowgate.default.0565.0016-TR2'
    expect(formatDocId(id)).toBe(id)
    expect(formatDocId(id + '/content')).toBe(id)
  })
  it('converts slash form and retains existing types', () => {
    expect(formatDocId('flowgate/default/0565/0016/TR2')).toBe('flowgate.default.0565.0016-TR2')
    expect(slashToNormalFormat('flowgate/default/0565/0016/TR2')).toBe('flowgate.default.0565.0016-TR2')
    expect(formatDocId('flowgate.default.0565.0016-TR')).toBe('flowgate.default.0565.0016-TR')
    expect(normalizeDashFormat('flowgate-default-0565-0016-TR2')).toBe('flowgate.default.0565.0016-TR2')
  })
})
