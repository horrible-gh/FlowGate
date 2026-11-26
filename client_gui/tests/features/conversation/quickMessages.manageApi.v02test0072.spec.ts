import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PROJECT_ACTIONS, resetCapabilities, wireBlock } from '../../helpers/capabilities'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest: http.get, postRequest: http.post, patchRequest: http.patch, deleteRequest: http.del }))

import {
  checkQuickMessageInput,
  createChatQuickMessage,
  deleteChatQuickMessage,
  fetchCanEditQuickMessages,
  restoreChatQuickMessageOrder,
  saveChatQuickMessageOrder,
  updateChatQuickMessage,
  type QuickMessage,
} from '@/features/conversation/quickMessages/api'

// flowgate.v02test.0072 card 3: add / edit / delete / reorder go through the existing project message API.
beforeEach(() => { Object.values(http).forEach((fn) => fn.mockReset()); resetCapabilities() })

const row = (id: number, sortOrder: number): QuickMessage => ({ id, title: `t${id}`, message: `m${id}`, kind: 'once', sortOrder })

describe('checkQuickMessageInput', () => {
  it('refuses a blank title, a title over 12 characters and a blank text', () => {
    expect(checkQuickMessageInput({ title: '  ', message: '문구', kind: 'once' })).toBe('title')
    expect(checkQuickMessageInput({ title: '열두글자를넘는아주아주긴제목', message: '문구', kind: 'once' })).toBe('title')
    expect(checkQuickMessageInput({ title: '제목', message: '  \n ', kind: 'once' })).toBe('message')
    expect(checkQuickMessageInput({ title: '열두글자제목이에요오오오', message: '문구', kind: 'sticky' })).toBeNull()
    expect(checkQuickMessageInput({ title: '제목', message: '문구', kind: 'sticky' })).toBeNull()
  })
})

describe('create / update / delete', () => {
  it('POSTs a chat message with title, kind and the row position, and returns the stored row', async () => {
    http.post.mockResolvedValue({ data: { id: 9, project_id: 'p 1', usage: 'chat', title: '근거', message: '근거를 붙여 주세요.', kind: 'sticky', sort_order: 4 } })
    const saved = await createChatQuickMessage('p 1', { title: ' 근거 ', message: ' 근거를 붙여 주세요. ', kind: 'sticky' }, 4)
    expect(http.post).toHaveBeenCalledWith('/api/v1/projects/p%201/messages', {
      usage: 'chat', title: '근거', message: '근거를 붙여 주세요.', kind: 'sticky', sort_order: 4,
    })
    expect(saved).toEqual({ id: 9, title: '근거', message: '근거를 붙여 주세요.', kind: 'sticky', sortOrder: 4 })
  })

  it('PATCHes title, text and kind of one message', async () => {
    http.patch.mockResolvedValue({ data: { id: 3, title: '표로', message: '표로 정리', kind: 'once', sort_order: 2 } })
    const saved = await updateChatQuickMessage('p', 3, { title: '표로', message: '표로 정리', kind: 'once' })
    expect(http.patch).toHaveBeenCalledWith('/api/v1/projects/p/messages/3', { title: '표로', message: '표로 정리', kind: 'once' })
    expect(saved.sortOrder).toBe(2)
  })

  it('rejects an answer without a usable row', async () => {
    http.post.mockResolvedValue({ data: {} })
    await expect(createChatQuickMessage('p', { title: 'a', message: 'b', kind: 'once' }, 1)).rejects.toThrow()
  })

  it('DELETEs a message; an already deleted one (404) counts as deleted, other failures reject', async () => {
    http.del.mockResolvedValueOnce({ data: { detail: 'deleted' } })
    await deleteChatQuickMessage('p', 5)
    expect(http.del).toHaveBeenCalledWith('/api/v1/projects/p/messages/5')
    http.del.mockRejectedValueOnce(Object.assign(new Error('gone'), { response: { status: 404 } }))
    await expect(deleteChatQuickMessage('p', 5)).resolves.toBeUndefined()
    http.del.mockRejectedValueOnce(Object.assign(new Error('forbidden'), { response: { status: 403 } }))
    await expect(deleteChatQuickMessage('p', 5)).rejects.toThrow()
  })
})

describe('saveChatQuickMessageOrder', () => {
  it('sends sort_order 1, 2, 3 … only for the messages whose position changed', async () => {
    http.patch.mockResolvedValue({ data: {} })
    await saveChatQuickMessageOrder('p', [row(1, 1), row(2, 2), row(3, 3)], [3, 1, 2])
    expect(http.patch).toHaveBeenCalledTimes(3)
    expect(http.patch).toHaveBeenCalledWith('/api/v1/projects/p/messages/3', { sort_order: 1 })
    expect(http.patch).toHaveBeenCalledWith('/api/v1/projects/p/messages/1', { sort_order: 2 })
    expect(http.patch).toHaveBeenCalledWith('/api/v1/projects/p/messages/2', { sort_order: 3 })
    http.patch.mockClear()
    await saveChatQuickMessageOrder('p', [row(1, 1), row(2, 2), row(3, 3)], [1, 3, 2])
    expect(http.patch.mock.calls.map((call) => call[0]).sort()).toEqual(['/api/v1/projects/p/messages/2', '/api/v1/projects/p/messages/3'])
  })

  it('rejects when one of the saves fails', async () => {
    http.patch.mockResolvedValueOnce({ data: {} }).mockRejectedValueOnce(new Error('boom'))
    await expect(saveChatQuickMessageOrder('p', [row(1, 1), row(2, 2)], [2, 1])).rejects.toThrow()
  })

  it('rejects only after every request has finished, so none is still in flight when the caller undoes the save', async () => {
    let finishSlow!: () => void
    http.patch
      .mockRejectedValueOnce(new Error('boom'))
      .mockImplementationOnce(() => new Promise((resolve) => { finishSlow = () => resolve({ data: {} }) }))
    let settled = false
    const saving = saveChatQuickMessageOrder('p', [row(1, 1), row(2, 2)], [2, 1]).then(() => 'ok', () => 'failed').then((outcome) => { settled = true; return outcome })
    await Promise.resolve()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(settled).toBe(false)
    finishSlow()
    expect(await saving).toBe('failed')
  })
})

describe('restoreChatQuickMessageOrder', () => {
  it('writes the old sort_order of every row back, whatever was stored before', async () => {
    http.patch.mockResolvedValue({ data: {} })
    await restoreChatQuickMessageOrder('p', [row(1, 1), row(2, 5), row(3, 7)])
    expect(http.patch).toHaveBeenCalledTimes(3)
    expect(http.patch).toHaveBeenCalledWith('/api/v1/projects/p/messages/1', { sort_order: 1 })
    expect(http.patch).toHaveBeenCalledWith('/api/v1/projects/p/messages/2', { sort_order: 5 })
    expect(http.patch).toHaveBeenCalledWith('/api/v1/projects/p/messages/3', { sort_order: 7 })
  })

  it('still writes the other rows when one fails, then rejects', async () => {
    http.patch.mockRejectedValueOnce(new Error('boom')).mockResolvedValue({ data: {} })
    await expect(restoreChatQuickMessageOrder('p', [row(1, 1), row(2, 2), row(3, 3)])).rejects.toThrow('boom')
    expect(http.patch).toHaveBeenCalledTimes(3)
  })
})

describe('fetchCanEditQuickMessages', () => {
  const answer = (refused: Record<string, { reason: string; minimum_role?: string }> = {}) => ({ data: { project_id: 'p', capabilities: wireBlock(PROJECT_ACTIONS, 'role_developer', refused) } })

  it('asks the server\'s capabilities for the project (not /rbac/…/my-permissions) and answers project.settings.edit', async () => {
    http.get.mockResolvedValueOnce(answer())
    expect(await fetchCanEditQuickMessages('p 1')).toBe(true)
    expect(http.get).toHaveBeenCalledWith('/api/v1/projects/p%201/capabilities')
    expect(http.get.mock.calls.some(([path]) => String(path).includes('my-permissions'))).toBe(false)
  })

  it('is false when the server refuses project.settings.edit, whatever the role is called', async () => {
    http.get.mockResolvedValueOnce(answer({ 'project.settings.edit': { reason: 'role_limit', minimum_role: 'role_admin' } }))
    expect(await fetchCanEditQuickMessages('p2')).toBe(false)
  })

  it('reads again on every call (the person may have been given or lost the role)', async () => {
    http.get.mockResolvedValueOnce(answer({ 'project.settings.edit': { reason: 'role_limit' } }))
    expect(await fetchCanEditQuickMessages('p3')).toBe(false)
    http.get.mockResolvedValueOnce(answer())
    expect(await fetchCanEditQuickMessages('p3')).toBe(true)
  })

  it('is false on a malformed answer and on any failure', async () => {
    http.get.mockResolvedValueOnce({ data: {} })
    expect(await fetchCanEditQuickMessages('p4')).toBe(false)
    http.get.mockRejectedValueOnce(new Error('network'))
    expect(await fetchCanEditQuickMessages('p5')).toBe(false)
  })
})
