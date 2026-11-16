import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Window from '@/features/designer/flowAttachments/FlowAttachmentWindow.vue'
import Host from '@/features/designer/flowAttachments/FlowAttachmentHost.vue'
const api = vi.hoisted(() => ({ readFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn(), downloadFile: vi.fn(), downloadZip: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => api)
vi.mock('@shared/api', () => ({ getRequest: vi.fn(async () => ({ data: { requests: [] } })) }))
vi.mock('@/features/designer/inspector/useInspectorResource', () => ({ useInspectorEvents: vi.fn() }))
const files = ['a', 'b'].map(key => ({ key, filename: key + '.txt', display_name: key + '.txt', doc_id: 'doc', size: 5, content_sha256: 'sha', source_type: 'B', source_title: 'Bug' }))
const bringToFront = vi.fn(), setGeometry = vi.fn()
const global = { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en: {} } })], stubs: { ModelessShell: { methods: { bringToFront, setGeometry }, template: '<div><slot name="header"/><slot name="actions"/><slot/></div>' }, AttachmentRequestItem: true } }
let wrapper: ReturnType<typeof mount> | undefined
beforeEach(() => { vi.clearAllMocks(); api.readFiles.mockResolvedValue(files); api.deleteFile.mockResolvedValue({}); api.uploadFile.mockResolvedValue({}) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers() })
describe('Flow attachments window', () => {
  it('renders a row undo icon and keeps failed deletes visible with an error', async () => {
    wrapper = mount(Window, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', position: 0, readOnly: false }, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('.file-row button[aria-label="Delete"]').trigger('click')
    expect(wrapper.find('.file-row.pending button[aria-label="Undo"]').exists()).toBe(true)
    expect(api.deleteFile).not.toHaveBeenCalled()
    await wrapper.find('.file-row.pending button[aria-label="Undo"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
    api.deleteFile.mockRejectedValueOnce(new Error('offline'))
    await wrapper.find('.file-row button[aria-label="Delete"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledWith('A', files[0])
    expect(wrapper.findAll('.file-row')).toHaveLength(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
  })
  it('opens one window per Flow and keeps the earlier Flow when switching', async () => {
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => false }, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await host.open(); await flushPromises()
    expect(wrapper.findAllComponents(Window)).toHaveLength(1)
    await wrapper.setProps({ flowId: 'B', title: 'Flow B' })
    await host.open(); await flushPromises()
    expect(wrapper.findAllComponents(Window).map(w => w.props('flowId'))).toEqual(['A', 'B'])
    expect(bringToFront).toHaveBeenCalledTimes(3)
    await wrapper.find('button[aria-label="Arrange windows"]').trigger('click')
    expect(setGeometry).toHaveBeenCalledTimes(2)
    const file = new File(['data'], 'sample.txt')
    await host.open([file]); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledWith('B', file)
  })
  it('requires a conflict choice and sends the exact existing hash and key', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'existing', content_sha256: 'old' } } } } })
    wrapper = mount(Window, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', position: 0, readOnly: false }, global })
    await flushPromises()
    const file = new File(['replacement'], 'a.txt')
    const window = wrapper.vm as unknown as { add: (files: File[]) => Promise<void> }
    const work = window.add([file]); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    await wrapper.findAll('.collision button')[2].trigger('click')
    await work
    expect(api.uploadFile).toHaveBeenLastCalledWith('A', file, 'overwrite', { key: 'existing', content_sha256: 'old' })
  })
})
