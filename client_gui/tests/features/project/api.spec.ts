import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const extractApiErrorMessage = vi.hoisted(() => vi.fn((_error: unknown, fallback: string) => fallback))

vi.mock('@shared/api', () => ({ getRequest, postRequest, extractApiErrorMessage }))

import { createProject, fetchActiveProjects, suggestProjectSlug } from '@/features/project/api'

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  extractApiErrorMessage.mockClear()
})

describe('fetchActiveProjects', () => {
  it('requests the active list and normalizes snake_case fields, dropping __SYSTEM__', async () => {
    getRequest.mockResolvedValue({
      data: {
        projects: [
          {
            project_id: 'demo',
            project_name: 'Demo',
            description: null,
            color: '#112233',
            is_active: 1,
            created_at: '2026-01-01T00:00:00+09:00',
            updated_at: '2026-01-02T00:00:00+09:00',
            modules: [{ name: 'flow', title: 'Flow' }],
          },
          { project_id: '__SYSTEM__', project_name: 'system', is_active: 1 },
        ],
      },
    })

    const projects = await fetchActiveProjects()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects', { status: 'active' })
    expect(projects).toEqual([
      {
        id: 'demo',
        name: 'Demo',
        description: '',
        color: '#112233',
        isActive: true,
        createdAt: '2026-01-01T00:00:00+09:00',
        updatedAt: '2026-01-02T00:00:00+09:00',
        modules: [{ name: 'flow', title: 'Flow' }],
      },
    ])
  })

  it('accepts a bare array response body', async () => {
    getRequest.mockResolvedValue({
      data: [{ project_id: 'a', project_name: 'A', is_active: 1 }],
    })
    const projects = await fetchActiveProjects()
    expect(projects.map((p) => p.id)).toEqual(['a'])
  })

  // flowgate.v02.0088 F10: the adapter keeps only the server's own diagnostic; the screen owns the localized
  // general text, so no English default is put in its place.
  it('propagates the server diagnostic as it was sent', async () => {
    getRequest.mockRejectedValue({ isAxiosError: true, response: { status: 500, data: { detail: '서버 점검 중입니다.' } } })
    extractApiErrorMessage.mockReturnValueOnce('서버 점검 중입니다.')
    await expect(fetchActiveProjects()).rejects.toThrow('서버 점검 중입니다.')
    expect(extractApiErrorMessage).toHaveBeenCalledWith(expect.anything(), '')
  })

  it('leaves the message empty without a server diagnostic (no English fallback, no transport line)', async () => {
    getRequest.mockRejectedValueOnce(new Error('boom'))
    await expect(fetchActiveProjects()).rejects.toThrow(/^$/)
    // The transport's own English line ("Request failed with status code 500") is not a server diagnostic.
    const transport = Object.assign(new Error('Request failed with status code 500'), {
      isAxiosError: true,
      response: { status: 500, data: '' },
    })
    getRequest.mockRejectedValueOnce(transport)
    extractApiErrorMessage.mockReturnValueOnce('Request failed with status code 500')
    const error = await fetchActiveProjects().catch((e: unknown) => e)
    expect((error as Error).message).toBe('')
  })
})

describe('createProject', () => {
  it('sends only the fields the backend actually reads', async () => {
    postRequest.mockResolvedValue({
      data: { project_id: 'demo', project_name: 'Demo', description: '', color: '#112233', is_active: 1 },
    })

    const created = await createProject({ name: 'Demo', id: 'demo', description: '', color: '#112233' })

    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects', {
      project_name: 'Demo',
      project_id: 'demo',
      description: '',
      color: '#112233',
    })
    expect(created.id).toBe('demo')
    expect(created.isActive).toBe(true)
  })

  it('always sends the given project_id -- never omits it', async () => {
    postRequest.mockResolvedValue({
      data: { project_id: 'auto', project_name: 'Auto', is_active: 1 },
    })
    await createProject({ name: 'Auto', id: 'auto' })
    expect(postRequest).toHaveBeenCalledWith('/api/v1/projects', {
      project_name: 'Auto',
      project_id: 'auto',
      description: null,
      color: null,
    })
  })

  it('propagates create errors (e.g. duplicate name / id conflict)', async () => {
    postRequest.mockRejectedValue(new Error('duplicate'))
    extractApiErrorMessage.mockReturnValueOnce('Failed to create project.')
    await expect(createProject({ name: 'Demo', id: 'demo' })).rejects.toThrow('Failed to create project.')
  })
})

describe('suggestProjectSlug', () => {
  it('returns the suggested slug from /api/v1/slug/romanize', async () => {
    postRequest.mockResolvedValue({ data: { suggested: 'demo-project' } })
    await expect(suggestProjectSlug('Demo Project')).resolves.toBe('demo-project')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/slug/romanize', { text: 'Demo Project' })
  })

  it('returns null for blank input without calling the API', async () => {
    await expect(suggestProjectSlug('   ')).resolves.toBeNull()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('propagates suggestion errors', async () => {
    postRequest.mockRejectedValue(new Error('boom'))
    extractApiErrorMessage.mockReturnValueOnce('Failed to suggest a project id.')
    await expect(suggestProjectSlug('Demo')).rejects.toThrow('Failed to suggest a project id.')
  })
})
