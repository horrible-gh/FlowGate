/**
 * flowgate.v02.0053 T#5 — the card settings read the documents tab uses (features/document/api/settings.ts):
 * GET /api/v1/flows/{flow_id}/nodes/{node_key}/settings (T#2) -> camelCase, and whether the card omits its
 * instruction (its 지시서 line is then not shown, v7).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  patchRequest: vi.fn(),
}))

import { settingsInstructionOmitted, settingsNodeRead, toSettingsNode } from '@/features/document'

/** A task card's settings as the T#2 route answers them. */
const RAW = {
  ok: true,
  server_ts: '2026-10-11T16:00:00+09:00',
  flow_id: 'f/1',
  node_key: 'gn a',
  card_kind: 'run',
  locked: false,
  fields: {
    instruction: { value: 'omit', stored: 'omit', default: 'use', choices: ['use', 'omit'] },
    run_ai: { value: null, stored: null, default: null, choices: null },
    time_limit: { value: 60, stored: null, default: 60, choices: [30, 45, 60, 90, 120, 180, 240] },
  },
}

beforeEach(() => {
  getRequest.mockReset()
})

describe('card settings read (flowgate.v02.0053 T#5)', () => {
  it('reads the card’s settings from its route (path parts encoded) and answers them in camelCase', async () => {
    getRequest.mockResolvedValue({ data: RAW })
    const node = await settingsNodeRead('f/1', 'gn a')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/flows/f%2F1/nodes/gn%20a/settings')
    expect(node).toEqual({
      cardKind: 'run',
      locked: false,
      fields: {
        instruction: { value: 'omit', stored: 'omit', default: 'use', choices: ['use', 'omit'] },
        run_ai: { value: null, stored: null, default: null, choices: null },
        time_limit: { value: 60, stored: null, default: 60, choices: [30, 45, 60, 90, 120, 180, 240] },
      },
    })
  })

  it('a card without settings, a locked one and malformed answers read safely', () => {
    expect(toSettingsNode({ card_kind: null, locked: true, fields: {} })).toEqual({ cardKind: null, locked: true, fields: {} })
    expect(toSettingsNode(null)).toEqual({ cardKind: null, locked: false, fields: {} })
    expect(toSettingsNode({ fields: { instruction: 'omit' } }).fields.instruction).toEqual({ value: null, stored: null, default: null, choices: null })
  })

  it('the instruction is omitted only when its value is omit', () => {
    expect(settingsInstructionOmitted(toSettingsNode(RAW))).toBe(true)
    expect(settingsInstructionOmitted(toSettingsNode({ fields: { instruction: { value: 'use', default: 'use' } } }))).toBe(false)
    expect(settingsInstructionOmitted(toSettingsNode({ card_kind: 'requirement', fields: { delegation: { value: 'on' } } }))).toBe(false)
    expect(settingsInstructionOmitted(null)).toBe(false)
  })

  it('a refused read rejects (the caller keeps the line)', async () => {
    getRequest.mockRejectedValue({ response: { status: 404, data: { ok: false, error: { code: 'node_not_found' } } } })
    await expect(settingsNodeRead('f', 'gn_x')).rejects.toBeTruthy()
  })
})
