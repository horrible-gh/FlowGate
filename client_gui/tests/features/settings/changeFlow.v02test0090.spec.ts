import { describe, expect, it, vi } from 'vitest'
import { SettingsRequestError } from '@/features/settings/api'
import { useChangeFlow } from '@/features/settings/components/useChangeFlow'
import type { ChangeAnswer, ChangeImpactRow, ChangeMode } from '@/features/settings/types'

// v02test.0090 card 5 — the preview → impact dialog → apply-with-revision flow every access change of
// the settings screens goes through (components/useChangeFlow). Only the request is faked: it answers
// like the api adapter's ChangeRequest does (a preview for { preview: true }, the applied answer for
// { revision }), so the flow's own rules are what is checked.

const ROW: ChangeImpactRow = {
  user_id: 'u2',
  project_id: 'p1',
  before: 'role_reviewer',
  after: 'role_developer',
  gained: ['project.document.update'],
  lost: ['project.document.approve'],
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

function request(impact: ChangeImpactRow[], result: unknown = { ok: true }) {
  return vi.fn(async (mode: ChangeMode): Promise<ChangeAnswer<unknown>> =>
    'preview' in mode ? { revision: 7, impact } : { revision: 8, result })
}

describe('useChangeFlow', () => {
  it('applies at once when nobody\'s access changes: the preview, then the apply with its revision', async () => {
    const flow = useChangeFlow()
    const send = request([], 'done')
    const applied = await flow.run({ title: 't', request: send })
    expect(applied).toEqual({ result: 'done' })
    expect(send.mock.calls).toEqual([[{ preview: true }], [{ revision: 7 }]])
    expect(flow.dialog.value).toBeNull()
    expect(flow.busy.value).toBe(false)
  })

  it('opens the dialog with the impact and writes nothing until confirmed', async () => {
    const flow = useChangeFlow()
    const send = request([ROW], 'saved')
    const pending = flow.run({ title: '이두리: 리뷰어 → 개발자', request: send })
    await settle()
    expect(flow.dialog.value?.title).toBe('이두리: 리뷰어 → 개발자')
    expect(flow.dialog.value?.impact).toEqual([ROW])
    expect(flow.dialog.value?.revision).toBe(7)
    expect(send.mock.calls).toEqual([[{ preview: true }]])

    await flow.confirm()
    expect(await pending).toEqual({ result: 'saved' })
    expect(send.mock.calls).toEqual([[{ preview: true }], [{ revision: 7 }]])
    expect(flow.dialog.value).toBeNull()
  })

  it('confirmEmpty opens the dialog even with no impact', async () => {
    const flow = useChangeFlow()
    const send = request([])
    const pending = flow.run({ title: 't', request: send, confirmEmpty: true })
    await settle()
    expect(flow.dialog.value?.impact).toEqual([])
    expect(send.mock.calls).toEqual([[{ preview: true }]])
    flow.cancel()
    expect(await pending).toBeNull()
    expect(send.mock.calls).toEqual([[{ preview: true }]])
  })

  it('cancel closes the dialog without a write and resolves null', async () => {
    const flow = useChangeFlow()
    const send = request([ROW])
    const pending = flow.run({ title: 't', request: send })
    await settle()
    flow.cancel()
    expect(await pending).toBeNull()
    expect(flow.dialog.value).toBeNull()
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('409 revision_conflict on 적용 reads the preview again and applies only the newly confirmed one', async () => {
    const flow = useChangeFlow()
    let previews = 0
    const send = vi.fn(async (mode: ChangeMode): Promise<ChangeAnswer<unknown>> => {
      if ('preview' in mode) {
        previews += 1
        return { revision: previews === 1 ? 7 : 9, impact: previews === 1 ? [ROW] : [ROW, { ...ROW, user_id: 'u3' }] }
      }
      if (mode.revision === 7) throw new SettingsRequestError('다른 사람이 먼저 바꿨습니다.', 'revision_conflict', 409)
      return { revision: 10, result: 'saved' }
    })
    const pending = flow.run({ title: 't', request: send })
    await settle()
    await flow.confirm() // sends revision 7 → 409
    expect(flow.dialog.value?.refreshed).toBe(true)
    expect(flow.dialog.value?.revision).toBe(9)
    expect(flow.dialog.value?.impact).toHaveLength(2)
    expect(flow.dialog.value?.error).toBeNull()
    expect(send.mock.calls.filter(([mode]) => !('preview' in mode))).toEqual([[{ revision: 7 }]]) // nothing applied silently

    await flow.confirm() // the person confirmed the new preview
    expect(await pending).toEqual({ result: 'saved' })
    expect(send.mock.calls.at(-1)).toEqual([{ revision: 9 }])
  })

  it('a conflict on the immediate apply (no impact) also shows the new preview instead of retrying', async () => {
    const flow = useChangeFlow()
    let previews = 0
    const send = vi.fn(async (mode: ChangeMode): Promise<ChangeAnswer<unknown>> => {
      if ('preview' in mode) {
        previews += 1
        return { revision: 7 + previews, impact: previews === 1 ? [] : [ROW] }
      }
      if (mode.revision === 8) throw new SettingsRequestError('conflict', 'revision_conflict', 409)
      return { revision: 11, result: 'saved' }
    })
    const pending = flow.run({ title: 't', request: send })
    await settle()
    expect(flow.dialog.value?.refreshed).toBe(true)
    expect(flow.dialog.value?.impact).toEqual([ROW])
    await flow.confirm()
    expect(await pending).toEqual({ result: 'saved' })
  })

  it('last_project_admin stays in the dialog as its own message; the person can cancel', async () => {
    const flow = useChangeFlow()
    const send = vi.fn(async (mode: ChangeMode): Promise<ChangeAnswer<unknown>> => {
      if ('preview' in mode) return { revision: 7, impact: [ROW] }
      throw new SettingsRequestError('마지막 프로젝트 관리자는 빼거나 낮출 수 없습니다.', 'last_project_admin', 409)
    })
    const pending = flow.run({ title: 't', request: send })
    await settle()
    await flow.confirm()
    expect(flow.dialog.value?.error).toBe('마지막 프로젝트 관리자는 빼거나 낮출 수 없습니다.')
    expect(flow.dialog.value?.errorCode).toBe('last_project_admin')
    expect(flow.dialog.value?.revision).toBe(7) // no silent re-preview for a different refusal
    flow.cancel()
    expect(await pending).toBeNull()
  })

  it('a refused preview (403) goes to onError and nothing opens', async () => {
    const onError = vi.fn()
    const flow = useChangeFlow(onError)
    const send = vi.fn(async () => {
      throw new SettingsRequestError('권한이 없습니다.', 'forbidden', 403)
    })
    expect(await flow.run({ title: 't', request: send })).toBeNull()
    expect(onError).toHaveBeenCalledWith('권한이 없습니다.')
    expect(flow.dialog.value).toBeNull()
    expect(flow.busy.value).toBe(false)
  })
})
