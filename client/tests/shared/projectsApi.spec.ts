import { beforeEach, describe, expect, it, vi } from 'vitest'

const { postRequest } = vi.hoisted(() => ({
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  postRequest,
}))

describe('project archive API client', () => {
  beforeEach(() => {
    postRequest.mockReset()
  })

  it('archives and restores through the project status endpoints', async () => {
    postRequest
      .mockResolvedValueOnce({ data: { project_id: 'flowgate', is_active: 0 } })
      .mockResolvedValueOnce({ data: { project_id: 'flowgate', is_active: 1 } })
    const { setProjectArchiveState } = await import('@shared/projects')

    await expect(setProjectArchiveState('flowgate', true)).resolves.toMatchObject({ is_active: 0 })
    await expect(setProjectArchiveState('flowgate', false)).resolves.toMatchObject({ is_active: 1 })

    expect(postRequest).toHaveBeenNthCalledWith(1, '/api/v1/projects/flowgate/archive', {})
    expect(postRequest).toHaveBeenNthCalledWith(2, '/api/v1/projects/flowgate/restore', {})
  })
})
