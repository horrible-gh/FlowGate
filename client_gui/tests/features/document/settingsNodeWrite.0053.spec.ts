/**
 * flowgate.v02.0053 T#7 — the card settings change the settings tab sends (features/document/api/settings.ts):
 * PATCH /api/v1/flows/{flow_id}/nodes/{node_key}/settings { field, value } (T#2, one value per request, null =
 * the default back) answered by the card's settings; why a change was refused; the AI Team choices
 * (GET /api/v1/ai-groups).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
}))

import { settingsNodeErrorOf, settingsNodeWrite, settingsTeamsRead, toSettingsTeams } from '@/features/document'

beforeEach(() => {
  getRequest.mockReset()
  patchRequest.mockReset()
})

const refusal = (status: number, code: string | null) => ({ isAxiosError: true, response: { status, data: code ? { ok: false, error: { code, message: code, params: {} } } : {} } })

describe('card settings change (flowgate.v02.0053 T#7)', () => {
  it('sends one field and its value to the card’s route (path parts encoded) and answers the card’s settings', async () => {
    patchRequest.mockResolvedValue({ data: { ok: true, flow_id: 'f/1', node_key: 'gn a', card_kind: 'run', locked: false, fields: { restart: { value: 2, stored: true, default: 0, choices: [-1, 0, 1, 2] } } } })
    const node = await settingsNodeWrite('f/1', 'gn a', 'restart', 2)
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/flows/f%2F1/nodes/gn%20a/settings', { field: 'restart', value: 2 })
    expect(node).toEqual({ cardKind: 'run', locked: false, fields: { restart: { value: 2, stored: true, default: 0, choices: [-1, 0, 1, 2] } } })
  })

  it('null (and undefined) take the default back: value is sent as null', async () => {
    patchRequest.mockResolvedValue({ data: { ok: true, card_kind: 'run', locked: false, fields: {} } })
    await settingsNodeWrite('f', 'gn_a', 'run_ai', null)
    await settingsNodeWrite('f', 'gn_a', 'run_ai', undefined)
    expect(patchRequest.mock.calls.map(([, body]) => body)).toEqual([
      { field: 'run_ai', value: null },
      { field: 'run_ai', value: null },
    ])
  })

  it('sends the gate settings version and reads the version returned by the server', async () => {
    patchRequest.mockResolvedValue({ data: { ok: true, card_kind: 'check', locked: false, settings_version: 4, fields: {} } })
    const node = await settingsNodeWrite('f', 'gn_gate', 'archive', true, 3)
    expect(patchRequest).toHaveBeenCalledWith('/api/v1/flows/f/nodes/gn_gate/settings', {
      field: 'archive', value: true, expected_settings_version: 3,
    })
    expect(node.settingsVersion).toBe(4)
  })

  it('a refusal is thrown as it is', async () => {
    patchRequest.mockRejectedValue(refusal(409, 'node_settings_locked'))
    await expect(settingsNodeWrite('f', 'gn_a', 'restart', 1)).rejects.toMatchObject({ response: { status: 409 } })
  })

  it.each([
    [refusal(409, 'node_settings_locked'), 'locked'],
    [refusal(409, 'flow_disposed'), 'disposed'],
    [refusal(409, 'card_settings_unavailable'), 'unavailable'],
    [refusal(422, 'invalid_request'), 'invalid'],
    [refusal(400, null), 'invalid'],
    [refusal(403, 'forbidden'), 'forbidden'],
    [refusal(401, null), 'forbidden'],
    [refusal(404, 'node_not_found'), 'not_found'],
    [refusal(500, 'internal_error'), 'other'],
    [{ isAxiosError: true, message: 'Network Error' }, 'network'],
  ] as const)('a refusal reads as %o -> %s', (error, kind) => {
    expect(settingsNodeErrorOf(error)).toBe(kind)
  })
})

describe('AI Team choices (flowgate.v02.0053 T#7)', () => {
  it('reads the AI Teams with their Supervisor', async () => {
    getRequest.mockResolvedValue({
      data: {
        items: [
          { ai_group_id: 'grp_a', name: '인증팀', enabled: true, supervisor: { provider_id: 'aip_opus', provider_name: 'Claude Opus 5' } },
          { ai_group_id: 'grp_b', name: '기본팀', enabled: false, supervisor: null },
        ],
      },
    })
    expect(await settingsTeamsRead()).toEqual([
      { id: 'grp_a', name: '인증팀', supervisorName: 'Claude Opus 5', enabled: true },
      { id: 'grp_b', name: '기본팀', supervisorName: null, enabled: false },
    ])
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-groups')
  })

  it('malformed rows read safely (no id: dropped; no name: the id; a Supervisor without a name: its AI id)', () => {
    expect(toSettingsTeams({ items: [{ name: 'x' }, { ai_group_id: 'g', supervisor: { provider_id: 'aip_x' } }, 'bad'] })).toEqual([
      { id: 'g', name: 'g', supervisorName: 'aip_x', enabled: true },
    ])
    expect(toSettingsTeams(null)).toEqual([])
  })
})
