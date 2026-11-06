import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getRequest } from '@shared/api'
import { checkFlowRevisionAdoption } from '@/features/project/flowRunApi'
import { createServerFlowGraphSource, type FlowCanvasLoaders } from '@/features/designer/center/graph/serverFlowGraphSource'

vi.mock('@shared/api', () => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
beforeEach(() => vi.clearAllMocks())

describe('revision adoption transport', () => {
  it('uses the read endpoint and canvas headers for an allowed assessment', async () => {
    vi.mocked(getRequest).mockResolvedValue({ data: { adoptable: true } } as never)
    expect(await checkFlowRevisionAdoption('a/b')).toBeNull()
    expect(getRequest).toHaveBeenCalledWith('/api/v1/flows/a%2Fb/runs/revision-adoption', {}, expect.any(Object))
  })
  it('keeps refusal code, params, status and message without treating it as a request failure', async () => {
    const error = { code: 'graph_revision_migration_blocked', params: { reason: 'active_attempts' }, status: 409, message: 'Blocked' }
    vi.mocked(getRequest).mockResolvedValue({ data: { adoptable: false, error } } as never)
    expect(await checkFlowRevisionAdoption('f')).toMatchObject(error)
  })
  it('rejects unavailable and malformed assessments for the caller fallback', async () => {
    vi.mocked(getRequest).mockRejectedValueOnce(new Error('offline'))
    await expect(checkFlowRevisionAdoption('f')).rejects.toThrow()
    vi.mocked(getRequest).mockResolvedValueOnce({ data: {} } as never)
    await expect(checkFlowRevisionAdoption('f')).rejects.toThrow()
  })
  it('wires the project loader into the Designer source', async () => {
    const check = vi.fn(async () => null)
    const source = createServerFlowGraphSource({ checkFlowRevisionAdoption: check } as unknown as FlowCanvasLoaders)
    expect(await source.checkRevisionAdoption!('p', 'f')).toBeNull()
    expect(check).toHaveBeenCalledWith('f')
  })
})
