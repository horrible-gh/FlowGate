import { describe, expect, it } from 'vitest'
import { toDocumentRecord } from '@/features/document/api/document'
import { wireBlock } from '../../helpers/capabilities'

describe('toDocumentRecord keeps the server\'s answer for this document', () => {
  const base = { doc_id: 'p.m.0001.0001-R', project_id: 'p', title: 't' }
  it('parses capabilities (context rule included)', () => {
    const record = toDocumentRecord({ ...base, capabilities: wireBlock(['project.document.update'], 'role_developer', { 'project.document.update': { reason: 'context_rule', rule: 'document_editable' } }) })
    expect(record.capabilities?.role).toBe('role_developer')
    expect(record.capabilities?.actions['project.document.update']).toMatchObject({ allowed: false, reason: 'context_rule', rule: 'document_editable' })
  })
  it('a response without one carries none (nothing is guessed)', () => {
    expect('capabilities' in toDocumentRecord(base)).toBe(false)
    expect('capabilities' in toDocumentRecord({ ...base, capabilities: 'x' })).toBe(false)
  })
})
