import { describe, expect, it } from 'vitest'
import { TYPE_CODE_RE, isValidTypeCode, docIdTypeCode } from '../../shared/utils/typeCode'

describe('canonical type codes', () => {
  it('accepts alpha and alphanumeric codes through four characters', () => {
    for (const code of ['R', 'TR', 'TSR', 'WP', 'T2', 'TR2', 'A1', 'AB12']) {
      expect(isValidTypeCode(code)).toBe(true)
      expect(TYPE_CODE_RE.test(code)).toBe(true)
    }
  })
  it('rejects lowercase, leading digits, and compact T2001', () => {
    for (const code of ['', '2T', 'tr2', 'T-2', 'T_2', 'ABCDE', 'T2001']) {
      expect(isValidTypeCode(code)).toBe(false)
    }
  })
  it('extracts canonical document ID type', () => {
    expect(docIdTypeCode('flowgate.default.0565.0016-TR2')).toBe('TR2')
    expect(docIdTypeCode('flowgate.default.0565.0016-TR')).toBe('TR')
    expect(docIdTypeCode('flowgate.default.0565.T2001')).toBeNull()
  })
})
