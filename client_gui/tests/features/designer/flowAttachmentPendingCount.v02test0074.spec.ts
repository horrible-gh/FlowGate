import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'

// v02test.0074 card 1 (rework): the badge of the 📎 button is the Flow's waiting-request count (the server's
// `GET /attachment-requests/counts?flow_id=` total), not the waiting rows of the open chat. These specs cover the reader
// the Dock uses (`useFlowAttachmentPending`) and the Dock handing the number to the Conversation.
const counts = vi.hoisted(() => vi.fn())
vi.mock('@/features/document/api/attachmentRequests', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/features/document/api/attachmentRequests')>(),
  attachmentRequestCounts: counts,
}))

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { useProjectStore } from '@/features/project'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { useFlowAttachmentPending } from '@/features/designer/flowAttachments/pendingCount'
import DesignerRightDock from '@/features/designer/shell/DesignerRightDock.vue'

registerAppMessages(i18n)

const result = (flowId: string, total: number) => ({ flowId, total, byNode: {} })
let wrapper: VueWrapper<any> | null = null
let subscription: { events: readonly string[]; event: (frame: any) => void; resync?: () => void } | null = null
let unsubscribed = 0

function install() {
  const pinia = createPinia()
  setActivePinia(pinia)
  vi.spyOn(useProjectStore(pinia), 'subscribeProjectEvents').mockImplementation((_projectId, sub: any) => {
    if (sub.events.includes('attachment_request_updated')) subscription = sub
    return () => { unsubscribed += 1 }
  })
  return pinia
}

const frame = (groupId: string | null, docId: string | null = null) => ({ type: 'attachment_request_updated', groupId, docId, payload: {} })
async function wait(ms = INSPECTOR_REFRESH_COALESCE_MS + 20) {
  await new Promise((r) => setTimeout(r, ms))
  await flushPromises()
}

beforeEach(() => {
  counts.mockReset()
  subscription = null
  unsubscribed = 0
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.restoreAllMocks()
})

describe('useFlowAttachmentPending', () => {
  const flow = ref<string | null>('p.m.0001')
  const Host = defineComponent({
    setup() {
      const pending = useFlowAttachmentPending(() => (flow.value ? { projectId: 'p', flowId: flow.value } : null))
      return () => h('span', { 'data-test': 'pending' }, String(pending.value))
    },
  })
  const mountHost = () => {
    wrapper = mount(Host, { global: { plugins: [install()] } })
    return wrapper
  }
  const shown = () => wrapper!.get('[data-test="pending"]').text()

  beforeEach(() => { flow.value = 'p.m.0001' })

  it('reads the Flow-level total of the counts API, whatever chat or card the requests belong to', async () => {
    counts.mockResolvedValue({ flowId: 'p.m.0001', total: 5, byNode: { n1: 2, n2: 3 } })
    mountHost()
    await flushPromises()
    expect(counts).toHaveBeenCalledWith('p.m.0001')
    expect(shown()).toBe('5')
  })

  it('reads again after an attachment_request_updated event of the Flow, from any chat or card', async () => {
    counts.mockResolvedValueOnce(result('p.m.0001', 1)).mockResolvedValueOnce(result('p.m.0001', 2))
    mountHost()
    await flushPromises()
    expect(shown()).toBe('1')
    expect(subscription!.events).toContain('attachment_request_updated')
    subscription!.event(frame('p.m.0001', 'p.m.0001.0009-CH')) // a chat other than the one open
    subscription!.event(frame('p.m.0001')) // and a burst of frames is one read again
    await wait()
    expect(counts).toHaveBeenCalledTimes(2)
    expect(shown()).toBe('2')
  })

  it('ignores the events of another Flow', async () => {
    counts.mockResolvedValue(result('p.m.0001', 1))
    mountHost()
    await flushPromises()
    subscription!.event(frame('p.m.0002'))
    await wait()
    expect(counts).toHaveBeenCalledTimes(1)
  })

  it('reads again after a reconnect of the event stream', async () => {
    counts.mockResolvedValueOnce(result('p.m.0001', 1)).mockResolvedValueOnce(result('p.m.0001', 4))
    mountHost()
    await flushPromises()
    subscription!.resync!()
    await wait()
    expect(shown()).toBe('4')
  })

  it('keeps the number on screen when a read fails, and a later read recovers it', async () => {
    counts.mockResolvedValueOnce(result('p.m.0001', 3)).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(result('p.m.0001', 2))
    mountHost()
    await flushPromises()
    subscription!.event(frame('p.m.0001'))
    await wait()
    expect(shown()).toBe('3')
    subscription!.event(frame('p.m.0001'))
    await wait()
    expect(shown()).toBe('2')
  })

  it('starts another Flow from 0 and drops a read answered after the Flow changed', async () => {
    let late!: (value: ReturnType<typeof result>) => void
    counts.mockResolvedValueOnce(result('p.m.0001', 6)).mockImplementationOnce(() => new Promise((r) => { late = r }))
    counts.mockResolvedValueOnce(result('p.m.0002', 1))
    mountHost()
    await flushPromises()
    expect(shown()).toBe('6')
    subscription!.event(frame('p.m.0001'))
    await wait() // the slow read of the first Flow is in flight
    flow.value = 'p.m.0002'
    await nextTick()
    expect(shown()).toBe('0')
    await flushPromises()
    expect(shown()).toBe('1')
    late(result('p.m.0001', 9)) // answered late: dropped
    await flushPromises()
    expect(shown()).toBe('1')
  })

  it('reads nothing and shows 0 while there is no Flow', async () => {
    flow.value = null
    mountHost()
    await flushPromises()
    expect(counts).not.toHaveBeenCalled()
    expect(shown()).toBe('0')
  })

  it('unsubscribes with its component', async () => {
    counts.mockResolvedValue(result('p.m.0001', 0))
    mountHost()
    await flushPromises()
    wrapper!.unmount()
    wrapper = null
    expect(unsubscribed).toBeGreaterThan(0)
  })
})

describe('DesignerRightDock hands the Flow count to the Conversation', () => {
  const Stub = defineComponent({
    name: 'ConversationPanel',
    props: { projectId: { type: String, default: null }, groupId: { type: String, default: null }, attachmentsPending: { type: Number, default: -1 } },
    setup: (props) => () => h('section', { 'data-test': 'conversation-panel', 'data-pending': String(props.attachmentsPending) }),
  })
  const GitStub = defineComponent({ name: 'DesignerGitPanel', setup: () => () => h('section') })
  const mountDock = async (props: Record<string, unknown> = {}) => {
    wrapper = mount(DesignerRightDock, {
      attachTo: document.body,
      props: { projectId: 'p', activeFlow: { groupId: 'p.m.0001', name: 'Flow' }, tool: 'conversation', collapsed: false, ...props },
      global: { plugins: [i18n, install()], stubs: { ConversationPanel: Stub, DesignerGitPanel: GitStub } },
    })
    await flushPromises()
    return wrapper
  }
  const pendingOf = () => wrapper!.get('[data-test="conversation-panel"]').attributes('data-pending')

  it('passes the count of the open Flow, and follows an event of another chat of that Flow', async () => {
    counts.mockResolvedValueOnce(result('p.m.0001', 2)).mockResolvedValueOnce(result('p.m.0001', 3))
    await mountDock()
    expect(counts).toHaveBeenCalledWith('p.m.0001')
    expect(pendingOf()).toBe('2')
    subscription!.event(frame('p.m.0001', 'p.m.0001.0007-CH'))
    await wait()
    expect(pendingOf()).toBe('3')
  })

  it('shows the count of the Flow it switched to, not the previous one', async () => {
    counts.mockImplementation(async (flowId: string) => result(flowId, flowId === 'p.m.0001' ? 2 : 0))
    await mountDock()
    expect(pendingOf()).toBe('2')
    await wrapper!.setProps({ activeFlow: { groupId: 'p.m.0002', name: 'Other' } })
    await flushPromises()
    expect(counts).toHaveBeenLastCalledWith('p.m.0002')
    expect(pendingOf()).toBe('0')
  })

  it('reads nothing before the Conversation was shown and without a work target', async () => {
    await mountDock({ tool: 'palette' })
    expect(counts).not.toHaveBeenCalled()
    wrapper!.unmount()
    wrapper = null
    await mountDock({ unselected: true })
    expect(counts).not.toHaveBeenCalled()
  })
})
