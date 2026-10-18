/** 0068 review regressions: real connected groups, stable event context and cache invalidation. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import DocumentIoGroup from '@/features/documentWindow/DocumentIoGroup.vue'
import { documentIoDownload, documentIoForget, documentIoInfo, documentIoLeave, resetDocumentIo } from '@/features/documentWindow/documentIo'
import { resetDocumentEditRegistry } from '@/features/documentWindow/documentEditRegistry'

const fixture = vi.hoisted(() => ({ read: vi.fn(), download: vi.fn(), record: vi.fn(), subscribe: vi.fn(), subscriptions: [] as any[] }))
vi.mock('@/features/project', () => ({ useProjectStore: () => ({ subscribeProjectEvents: fixture.subscribe }) }))
vi.mock('@/features/document', async (original) => ({
  ...await original<typeof import('@/features/document')>(),
  documentInfoRead: (...args: unknown[]) => fixture.read(...args),
  documentDownload: (...args: unknown[]) => fixture.download(...args),
  documentRead: (...args: unknown[]) => fixture.record(...args),
}))
const DOC = 'flowgate.v02.0068.0008-TR'
const ready = (editable = true) => ({ docId: DOC, projectId: 'flowgate', groupId: 'flowgate.v02.0068', isEditable: editable, downloadAvailable: true })
const http = (status: number) => ({ isAxiosError: true, response: { status, data: {} } })
let wrapper: VueWrapper | undefined
registerAppMessages(i18n)
beforeEach(() => {
  resetDocumentEditRegistry(); resetDocumentIo(); vi.useFakeTimers()
  fixture.read.mockReset().mockResolvedValue(ready())
  fixture.download.mockReset(); fixture.record.mockReset()
  fixture.subscriptions.length = 0
  fixture.subscribe.mockReset().mockImplementation((projectId, handlers) => {
    const subscription = { projectId, handlers, active: true }
    fixture.subscriptions.push(subscription)
    return () => { subscription.active = false }
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; resetDocumentEditRegistry(); resetDocumentIo(); vi.useRealTimers() })
async function draw(context = true) {
  wrapper = mount(DocumentIoGroup, { props: { docId: DOC, ...(context ? { projectId: 'flowgate', groupId: 'flowgate.v02.0068' } : {}) }, global: { plugins: [createPinia(), i18n] } })
  await flushPromises()
}
async function signal(kind: string) {
  for (const sub of fixture.subscriptions.filter(s => s.active)) {
    if (kind === 'resync') sub.handlers.resync()
    else sub.handlers.event({ type: kind === 'flow' ? 'group_view_refresh' : 'document_explorer_refresh', docId: kind === 'doc' ? DOC : null, groupId: 'flowgate.v02.0068', payload: {} })
  }
  await vi.advanceTimersByTimeAsync(200)
  await flushPromises()
}
describe('information failure does not determine subscription lifetime', () => {
  it.each([403, 404, 500].flatMap(status => ['doc', 'flow', 'resync'].map(kind => [status, kind] as const)))('initial %i recovers on %s using parent context', async (status, kind) => {
    fixture.read.mockRejectedValueOnce(http(status))
    await draw()
    expect(documentIoInfo(DOC).state).toBe(status === 403 ? 'DENIED' : status === 404 ? 'GONE' : 'ERROR')
    expect(fixture.subscriptions.filter(s => s.active)).toHaveLength(1)
    await signal(kind)
    expect(fixture.read).toHaveBeenCalledTimes(2)
    expect(documentIoInfo(DOC).state).toBe('READY')
  })
  it.each([403, 404])('keeps learned context after a download %i and resync restores permission', async (status) => {
    await draw(false)
    fixture.download.mockRejectedValueOnce(http(status))
    fixture.record.mockRejectedValueOnce(http(status))
    await documentIoDownload(DOC)
    expect(documentIoInfo(DOC).record).toBeNull()
    expect(fixture.subscriptions.filter(s => s.active)).toHaveLength(1)
    await signal('resync')
    expect(documentIoInfo(DOC).state).toBe('READY')
  })
})
describe('cache reset with the same mounted group', () => {
  it.each(['leave', 'forget'])('%s starts a new read without close or document-id change', async (kind) => {
    await draw()
    const element = wrapper!.element
    fixture.read.mockResolvedValueOnce(ready(false))
    if (kind === 'leave') documentIoLeave()
    else documentIoForget()
    await flushPromises()
    expect(fixture.read).toHaveBeenCalledTimes(2)
    expect(wrapper!.element).toBe(element)
    expect(documentIoInfo(DOC).state).toBe('READY')
    expect((wrapper!.get('[data-test="document-io-upload"]').element as HTMLButtonElement).disabled).toBe(true)
  })
})
