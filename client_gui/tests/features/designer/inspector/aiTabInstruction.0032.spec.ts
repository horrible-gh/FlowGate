import { describe, expect, it } from 'vitest'
import { cardRoleRows, instructionWritten } from '@/features/designer/inspector/features/settings/cardRolesModel'
import type { CardAiRoles } from '@/features/document'

/**
 * flowgate.v02gui.0032 (decision 7): a card whose 지시서 has a written body never calls its 지시 AI, so the AI tab shows
 * the 지시 AI row as 사용하지 않음 (held) without changing the stored value; an empty 지시서 shows the stored AI again.
 */
function roles(): CardAiRoles {
  const row = (providerId: string) => ({ state: 'assigned_ok', outcome: 'ai', source: 'card', legacyFlag: false, providerId,
    displayName: providerId, cause: null, selected: { kind: 'assigned', providerId } })
  return { roles: { instruction: row('aip_i'), execution: row('aip_e'), review: row('aip_r') }, options: [], optionsState: 'ok' } as unknown as CardAiRoles
}
const line = (slot: string, hasBody?: boolean, docId: string | null = 'd.0001-T') => ({ slot, docId, ...(hasBody === undefined ? {} : { hasBody }) })

describe('written 지시서 and the 지시 AI row (v02gui.0032)', () => {
  it('reads a written body from the 지시서 line only', () => {
    expect(instructionWritten([line('instruction', true)])).toBe(true)
    expect(instructionWritten([line('instruction', false)])).toBe(false)
    expect(instructionWritten([line('instruction')])).toBe(false)                // not sent: nothing is claimed
    expect(instructionWritten([line('instruction', true, null)])).toBe(false)    // no document
    expect(instructionWritten([line('report', true)])).toBe(false)
    expect(instructionWritten(null)).toBe(false)
  })
  it('shows 사용하지 않음 held for a written 지시서 and keeps the stored value and the other rows', () => {
    const data = roles(); const before = structuredClone(data)
    const rows = cardRoleRows(data, 'use', {}, true)
    expect(rows.map(r => [r.role, r.held, r.written])).toEqual([['instruction', true, true], ['execution', false, false], ['review', false, false]])
    expect(rows[0].selected).toEqual({ kind: 'not_used' })
    expect(rows[1].selected).toEqual({ kind: 'assigned', providerId: 'aip_e' })
    expect(data).toEqual(before)
  })
  it('shows the stored 지시 AI again once the 지시서 is empty', () => {
    const rows = cardRoleRows(roles(), 'use', {}, false)
    expect(rows[0]).toMatchObject({ held: false, written: false, selected: { kind: 'assigned', providerId: 'aip_i' } })
  })
})
