import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({ getRequest }))

import { fetchChatQuickMessages } from '@/features/conversation/quickMessages/api'

// flowgate.v02test.0072 card 2: the row reads card 1's chat usage of the existing project message API.
beforeEach(() => getRequest.mockReset())

describe('fetchChatQuickMessages', () => {
  it('reads GET /projects/{id}/messages?usage=chat and keeps the row order', async () => {
    getRequest.mockResolvedValue({ data: { data: [
      { id: 7, project_id: 'p 1', doc_type: '*', usage: 'chat', title: 'B', message: 'bee', kind: 'once', sort_order: 2 },
      { id: 3, project_id: 'p 1', doc_type: '*', usage: 'chat', title: 'A', message: 'ay', kind: 'sticky', sort_order: 1 },
    ] } })
    const list = await fetchChatQuickMessages('p 1')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/p%201/messages', { usage: 'chat' })
    expect(list.map((i) => [i.id, i.kind])).toEqual([[3, 'sticky'], [7, 'once']])
  })

  it('rejects on a refused read (403) and on a malformed answer', async () => {
    getRequest.mockRejectedValueOnce(Object.assign(new Error('forbidden'), { response: { status: 403 } }))
    await expect(fetchChatQuickMessages('p')).rejects.toThrow()
    getRequest.mockResolvedValueOnce({ data: {} })
    await expect(fetchChatQuickMessages('p')).rejects.toThrow()
  })
})
