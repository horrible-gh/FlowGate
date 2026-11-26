// F81 screen card: the first case of an empty scenario pins without a conflict (8a), and a person's remark resolution
// carries who resolved it (resolved_by).
import { describe, expect, it } from 'vitest'
import { toTestSpecRemark } from '@/features/document/api/testSpec'
import { newCaseDraft, pinMatches } from '@/features/designer/inspector/features/testSpec/windows/scenarioWindowModel'
import type { CasesPin } from '@/features/designer/inspector/features/testSpec/testCardSession'

const base = { docId: 'ts', revisionNo: 3, ctx: null } as unknown as CasesPin['base']

describe('first case of an empty scenario (F81 8a)', () => {
  it('a pin without a title matches the draft shown with the document title', () => {
    const draft = newCaseDraft([], 'F81 · 시나리오', 'ts')
    draft.pin = { base, title: null, cases: [] }
    expect(pinMatches(draft)).toBe(true)
  })
  it('a pin with the same title matches; another title or other cases conflict', () => {
    const draft = newCaseDraft([], 'Spec', 'ts')
    draft.pin = { base, title: 'Spec', cases: [] }
    expect(pinMatches(draft)).toBe(true)
    draft.pin = { base, title: 'Other', cases: [] }
    expect(pinMatches(draft)).toBe(false)
    const other = { caseId: 'TC-1', title: 'x', fields: {} } as unknown as CasesPin['cases'][number]
    draft.pin = { base, title: null, cases: [other] }
    expect(pinMatches(draft)).toBe(false)
  })
  it('no pin is never a match', () => {
    expect(pinMatches(newCaseDraft([], null, 'ts'))).toBe(false)
  })
})

describe('remark resolved by a person', () => {
  it('reads resolved_by as a name or an object with a name', () => {
    const row = { remark_id: 'r1', case_id: 'TC-1', text: 't', state: 'resolved', author: { kind: 'human', name: 'kim' } }
    expect(toTestSpecRemark({ ...row, resolved_by: 'lee' })?.resolvedBy).toBe('lee')
    expect(toTestSpecRemark({ ...row, resolved_by: { kind: 'human', name: 'park' } })?.resolvedBy).toBe('park')
    expect(toTestSpecRemark(row)?.resolvedBy).toBeNull()
  })
})
