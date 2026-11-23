import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Window from '@/features/designer/flowAttachments/FlowAttachmentWindow.vue'
import Host from '@/features/designer/flowAttachments/FlowAttachmentHost.vue'
import { disposeFlowAttachmentOperations } from '@/features/designer/flowAttachments/operations'

// flowgate.v02test.0074.0013-T (TR0014): a file dragged onto the Designer is never opened by the browser.
// dragover / drop are always prevented while the drag carries `Files`; a drop made while the Flow's archive state is
// still being read is held and goes up once the state is known; where nothing can be uploaded, a "cannot upload here"
// overlay shows the reason before the drop.
const api = vi.hoisted(() => ({ readFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn(), downloadFile: vi.fn(), downloadZip: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => api)
vi.mock('@shared/api', () => ({ getRequest: vi.fn(async () => ({ data: { requests: [] } })) }))
vi.mock('@/features/designer/inspector/useInspectorResource', () => ({ useInspectorEvents: vi.fn() }))

const global = { plugins: [createI18n({ legacy: false, locale: 'ko', messages: { ko: {} } })], stubs: { AttachmentRequestItem: true, Teleport: true } }
let wrapper: ReturnType<typeof mount> | undefined
beforeEach(() => { vi.clearAllMocks(); api.readFiles.mockResolvedValue([]); api.uploadFile.mockResolvedValue({}) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; disposeFlowAttachmentOperations() })

function fire(type: 'dragenter' | 'dragover' | 'drop', files: File[] = [], types: string[] = ['Files']) {
  const transfer = { types, files, dropEffect: 'copy' }
  const event = new Event(type, { bubbles: true, cancelable: true }); Object.assign(event, { dataTransfer: transfer })
  document.body.dispatchEvent(event)
  return { event, transfer }
}
// The Designer's real rule (designerCenterSession.isFlowReadOnly): a Flow is locked until its archive state is read as false.
function mountHost(archived: { value: boolean | null }, extra: Record<string, unknown> = {}) {
  wrapper = mount(Host, {
    props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: (_id: string) => archived.value !== false, isArchived: (_id: string) => archived.value, ...extra },
    global, attachTo: document.body,
  })
  return wrapper
}
const overlay = () => wrapper!.find('[data-test="flow-attachment-drop-overlay"]')
const blocked = () => wrapper!.find('[data-test="flow-attachment-drop-blocked"]')

describe('file drop never reaches the browser (0074 TR0014)', () => {
  it('archive state still being read: drop is prevented and held; once not archived, the window opens and the file goes up', async () => {
    const archived = ref<boolean | null>(null)
    mountHost(archived)
    const file = new File(['x'], 'shot.png', { type: 'image/png' })
    const over = fire('dragover')
    expect(over.event.defaultPrevented).toBe(true)
    expect(over.transfer.dropEffect).toBe('copy')
    await flushPromises()
    expect(overlay().text()).toContain('Flow A')
    expect(wrapper!.find('[data-test="flow-attachment-drop-checking"]').text()).toContain('확인')
    const drop = fire('drop', [file])
    expect(drop.event.defaultPrevented).toBe(true)
    await flushPromises()
    expect(overlay().exists()).toBe(false)
    expect(api.uploadFile).not.toHaveBeenCalled()
    expect(wrapper!.findAllComponents(Window)).toHaveLength(0)
    archived.value = false
    await flushPromises()
    expect(wrapper!.findAllComponents(Window).map(w => w.props('flowId'))).toEqual(['A'])
    expect(api.uploadFile).toHaveBeenCalledWith('A', file)
  })

  it('held drop of a Flow confirmed archived: nothing goes up, a notice says why', async () => {
    const archived = ref<boolean | null>(null)
    mountHost(archived)
    fire('drop', [new File(['x'], 'notes.md')])
    await flushPromises()
    archived.value = true
    await flushPromises()
    expect(api.uploadFile).not.toHaveBeenCalled()
    expect(wrapper!.findAllComponents(Window)).toHaveLength(0)
    expect(wrapper!.text()).toContain('파일을 올리지 않았습니다: 보관된 플로우입니다')
  })

  it('a held drop goes to the Flow it was dropped on, even after the user moved to another tab', async () => {
    const states = ref<Record<string, boolean | null>>({ A: null, B: false })
    wrapper = mount(Host, {
      props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: (id: string) => states.value[id] !== false, isArchived: (id: string) => states.value[id] ?? null },
      global, attachTo: document.body,
    })
    const file = new File(['x'], 'a.txt')
    fire('drop', [file])
    await wrapper.setProps({ flowId: 'B', title: 'Flow B' })
    states.value = { ...states.value, A: false }
    await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledWith('A', file)
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    expect(wrapper.findAllComponents(Window).map(w => w.props('title'))).toEqual(['Flow A'])
  })

  it('an archived Flow: "cannot upload here" before the drop, dropEffect none, drop prevented, nothing uploaded', async () => {
    mountHost({ value: true })
    const over = fire('dragover')
    expect(over.event.defaultPrevented).toBe(true)
    expect(over.transfer.dropEffect).toBe('none')
    await flushPromises()
    expect(overlay().exists()).toBe(false)
    expect(blocked().attributes('data-reason')).toBe('archived')
    expect(blocked().text()).toBe('여기에는 올릴 수 없음: 보관된 플로우입니다')
    expect(fire('drop', [new File(['x'], 'a.png')]).event.defaultPrevented).toBe(true)
    await flushPromises()
    expect(blocked().exists()).toBe(false)
    expect(api.uploadFile).not.toHaveBeenCalled()
    expect(wrapper!.text()).toContain('보관된 플로우입니다')
  })

  it('no open Flow: dragover / drop prevented, "cannot upload here" overlay with the reason, nothing uploaded', async () => {
    mountHost({ value: false }, { flowId: null, title: '' })
    for (const type of ['dragenter', 'dragover'] as const) {
      const { event, transfer } = fire(type)
      expect(event.defaultPrevented).toBe(true)
      expect(transfer.dropEffect).toBe('none')
    }
    await flushPromises()
    expect(blocked().attributes('data-reason')).toBe('noFlow')
    expect(blocked().text()).toBe('여기에는 올릴 수 없음: 열린 플로우가 없습니다')
    expect(fire('drop', [new File(['x'], 'a.png')]).event.defaultPrevented).toBe(true)
    await flushPromises()
    expect(api.uploadFile).not.toHaveBeenCalled()
    expect(wrapper!.findAllComponents(Window)).toHaveLength(0)
  })

  it('read-only Flow (state known, still locked; or no archive state at all): prevented, read-only overlay, nothing uploaded', async () => {
    mountHost({ value: false }, { isReadOnly: () => true })
    const over = fire('dragover')
    expect(over.event.defaultPrevented).toBe(true)
    expect(over.transfer.dropEffect).toBe('none')
    await flushPromises()
    expect(blocked().attributes('data-reason')).toBe('readOnly')
    expect(blocked().text()).toBe('여기에는 올릴 수 없음: 읽기 전용입니다')
    expect(fire('drop', [new File(['x'], 'a.png')]).event.defaultPrevented).toBe(true)
    await flushPromises()
    expect(api.uploadFile).not.toHaveBeenCalled()
    wrapper!.unmount()
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => true }, global, attachTo: document.body })
    expect(fire('dragover').event.defaultPrevented).toBe(true)
    await flushPromises()
    expect(blocked().attributes('data-reason')).toBe('readOnly')
  })

  it('a blocked drop is not stopped: an element under the pointer still receives it', async () => {
    mountHost({ value: false }, { flowId: null })
    const seen = vi.fn()
    document.body.addEventListener('drop', seen)
    fire('drop', [new File(['x'], 'a.png')])
    document.body.removeEventListener('drop', seen)
    expect(seen).toHaveBeenCalledTimes(1)
  })

  it('drags without files (cards, quick messages, text) are left alone', async () => {
    mountHost({ value: null })
    expect(fire('dragover', [], ['text/plain']).event.defaultPrevented).toBe(false)
    expect(fire('drop', [], ['text/plain']).event.defaultPrevented).toBe(false)
    await flushPromises()
    expect(overlay().exists()).toBe(false)
    expect(blocked().exists()).toBe(false)
  })
})
