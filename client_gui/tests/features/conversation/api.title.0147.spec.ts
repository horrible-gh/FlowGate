import { beforeEach, describe, expect, it, vi } from 'vitest'
const http = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), extractApiErrorMessage: vi.fn(() => '') }))
vi.mock('@shared/api', () => http)
import { fetchChats, autoTitleChat } from '@/features/conversation/api'
const A = 'p.m.0001.0002-CH'
beforeEach(() => { for (const fn of Object.values(http)) fn.mockReset() })
describe('0147 title metadata and strict success wire', () => {
  it.each(['owed', 'claimed', 'done', 'manual', 'failed'])('preserves %s from JSON and object meta', async (state) => {
    const meta = { chat_auto_title: { state, title: 'New chat' } }
    for (const wire of [meta, JSON.stringify(meta)]) {
      http.getRequest.mockResolvedValue({ data: [{ doc_id: A, title: 'New chat', meta: wire }] })
      expect((await fetchChats({ projectId: 'p', groupId: 'p.m.0001' }))[0].autoTitle).toEqual({ state, provisionalTitle: 'New chat' })
    }
  })
  it.each([null, undefined, '', '{', [], 0, { chat_auto_title: [] }, { chat_auto_title: { state: 'future', title: 'New chat' } }, { chat_auto_title: { state: 'owed' } }])('never infers owed from malformed or missing meta %j', async (meta) => {
    http.getRequest.mockResolvedValue({ data: [{ doc_id: A, title: 'New chat', meta }] })
    expect((await fetchChats({ projectId: 'p', groupId: 'p.m.0001' }))[0].autoTitle ?? null).toBeNull()
  })
  it.each([{}, { doc_id: 'other', title: 'valid' }, { doc_id: A }, { doc_id: A, title: '' }, { doc_id: A, title: '  ' }])('rejects malformed success %j', async (data) => {
    http.postRequest.mockResolvedValue({ data })
    await expect(autoTitleChat(A, 'local')).rejects.toMatchObject({ status: null })
  })
  it('uses the response id and title without inventing response metadata', async () => {
    http.postRequest.mockResolvedValue({ data: { doc_id: A, title: 'Stored title' } })
    expect(await autoTitleChat(A, 'local')).toEqual({ id: A, title: 'Stored title', updatedAt: null })
  })
})
