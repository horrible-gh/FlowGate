import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Window from '@/features/designer/flowAttachments/FlowAttachmentWindow.vue'
import Host from '@/features/designer/flowAttachments/FlowAttachmentHost.vue'
import { flowAttachmentUi } from '@/features/designer/flowAttachments/state'

const api = vi.hoisted(() => ({ readFiles: vi.fn(), deleteFile: vi.fn(), uploadFile: vi.fn(), downloadFile: vi.fn(), downloadZip: vi.fn() }))
vi.mock('@/features/designer/flowAttachments/api', () => api)
vi.mock('@shared/api', () => ({ getRequest: vi.fn(async () => ({ data: { requests: [] } })) }))
vi.mock('@/features/designer/inspector/useInspectorResource', () => ({ useInspectorEvents: vi.fn() }))

const today = new Date(); today.setHours(9, 3, 0, 0)
const files = [
  { key: 'a', filename: 'shot.png', display_name: 'shot.png', doc_id: 'doc', size: 182400, content_type: 'image/png', uploaded_at: today.toISOString(), content_sha256: 'sha-a', source_type: 'CH', source_title: 'Chat', origin: 'ai' },
  { key: 'b', filename: 'notes.md', display_name: 'notes.md', doc_id: 'doc', size: 8400, content_type: 'text/markdown', uploaded_at: today.toISOString(), content_sha256: 'sha-b', source_type: 'R', source_title: 'Req', origin: 'requirement' },
]
const global = { plugins: [createI18n({ legacy: false, locale: 'ko', messages: { ko: {} } })], stubs: { AttachmentRequestItem: true, Teleport: true } }
const props = { flowId: 'A', projectId: 'p', title: '파일 첨부', position: 0, readOnly: false, current: true, focused: true, zIndex: 1 }
let wrapper: ReturnType<typeof mount> | undefined
beforeEach(() => { vi.clearAllMocks(); api.readFiles.mockResolvedValue(files.map(f => ({ ...f }))); api.deleteFile.mockResolvedValue({}); api.uploadFile.mockResolvedValue({}) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers() })

describe('Flow attachment window (final mockup)', () => {
  it('draws the mockup header, count, type tiles, source tags and time', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    expect(wrapper.find('.fa-title').text()).toBe('첨부파일')
    expect(wrapper.find('.fa-flow').text()).toBe('파일 첨부')
    expect(wrapper.find('[data-test="flow-attachment-count"]').text()).toBe('2개')
    expect(wrapper.find('.fa-add').text()).toContain('파일 추가')
    const rows = wrapper.findAll('[data-test="flow-attachment-row"]')
    expect(rows[0].find('.fa-glyph').classes()).toContain('image')
    expect(rows[1].find('.fa-glyph').classes()).toContain('text')
    expect(rows[0].find('.fa-tag').text()).toBe('AI 첨부 승인')
    expect(rows[1].find('.fa-tag').text()).toBe('요구사항 카드')
    expect(rows[0].find('.fa-source').text()).toContain('09:03')
    expect(rows[0].find('.fa-size').text()).toBe('178 KB')
    // No text buttons for delete / undo: icons only, words in the tooltip.
    expect(wrapper.find('[data-test="flow-attachment-delete"]').text()).toBe('')
  })

  it('🗑 strikes the row and shows ↶ in its place; ↶ keeps the file, five seconds delete it', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    const row = wrapper.find('[data-test="flow-attachment-row"]')
    expect(row.classes()).toContain('gone')
    expect(row.find('[data-test="flow-attachment-undo"] .fa-ring').exists()).toBe(true)
    expect(row.find('[data-test="flow-attachment-delete"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="flow-attachment-count"]').text()).toBe('1개')
    await row.find('[data-test="flow-attachment-undo"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(api.deleteFile).toHaveBeenCalledWith('A', expect.objectContaining({ key: 'a' }))
    expect(wrapper.findAll('[data-test="flow-attachment-row"]')).toHaveLength(1)
  })

  it('selection 🗑 turns into a numbered ↶ that brings all back; Ctrl+Z takes back the last delete', async () => {
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-select-all"]').setValue(true)
    expect(wrapper.find('.fa-selected').text()).toBe('2개 선택')
    await wrapper.find('[data-test="flow-attachment-bulk-delete"]').trigger('click')
    const bulk = wrapper.find('[data-test="flow-attachment-bulk-undo"]')
    expect(bulk.find('.fa-undo-badge').text()).toBe('2')
    expect(wrapper.findAll('.fa-row.gone')).toHaveLength(2)
    await bulk.trigger('click')
    expect(wrapper.findAll('.fa-row.gone')).toHaveLength(0)
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await wrapper.find('[data-test="flow-attachment-window"]').trigger('keydown', { key: 'z', ctrlKey: true })
    expect(wrapper.findAll('.fa-row.gone')).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('a failed delete brings the row back with the reason', async () => {
    api.deleteFile.mockRejectedValueOnce({ response: { data: { error: { message: 'The file changed. Check it again.' } } } })
    wrapper = mount(Window, { props, global })
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.find('[data-test="flow-attachment-delete"]').trigger('click')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('The file changed')
    expect(wrapper.findAll('[data-test="flow-attachment-row"]')).toHaveLength(2)
  })

  it('a name conflict asks in the window and overwrite sends the exact existing hash and key', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'existing', content_sha256: 'old' } } } } })
    wrapper = mount(Window, { props, global })
    await flushPromises()
    const file = new File(['replacement'], 'notes.md')
    const view = wrapper.vm as unknown as { add: (files: File[]) => Promise<void> }
    const work = view.add([file]); await flushPromises()
    const card = wrapper.find('[data-test="flow-attachment-conflict"]')
    expect(card.find('h3').text()).toBe('이름 중복')
    expect(card.findAll('button').map(b => b.text())).toEqual(['취소', '양쪽 보존', '덮어쓰기'])
    await card.find('[data-conflict="overwrite"]').trigger('click')
    await work
    expect(api.uploadFile).toHaveBeenLastCalledWith('A', file, 'overwrite', { key: 'existing', content_sha256: 'old' })
  })

  it('keep both uploads without an overwrite target', async () => {
    api.uploadFile.mockRejectedValueOnce({ response: { data: { error: { code: 'ATTACHMENT_NAME_CONFLICT', details: { key: 'existing', content_sha256: 'old' } } } } })
    wrapper = mount(Window, { props, global })
    await flushPromises()
    const file = new File(['other'], 'notes.md')
    const work = (wrapper.vm as unknown as { add: (f: File[]) => Promise<void> }).add([file]); await flushPromises()
    await wrapper.find('[data-conflict="keep_both"]').trigger('click')
    await work
    expect(api.uploadFile).toHaveBeenLastCalledWith('A', file, 'keep_both', undefined)
  })
})

describe('Flow attachment host', () => {
  it('one window per Flow; while one is open, opening another Flow opens its window too', async () => {
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => false }, global })
    const host = wrapper.vm as unknown as { open: (files?: File[]) => Promise<void> }
    await host.open(); await host.open(); await flushPromises()
    expect(wrapper.findAllComponents(Window)).toHaveLength(1)
    expect(flowAttachmentUi.currentOpen).toBe(true)
    await wrapper.setProps({ flowId: 'B', title: 'Flow B' }); await flushPromises()
    const windows = wrapper.findAllComponents(Window)
    expect(windows.map(w => w.props('flowId'))).toEqual(['A', 'B'])
    expect(windows.map(w => w.props('current'))).toEqual([false, true])
    expect(windows[1].props('focused')).toBe(true)
    const file = new File(['data'], 'sample.txt')
    await host.open([file]); await flushPromises()
    expect(api.uploadFile).toHaveBeenCalledWith('B', file)
    await windows[1].find('[data-test="flow-attachment-close"]').trigger('click')
    expect(flowAttachmentUi.currentOpen).toBe(false)
  })

  it('a file dropped anywhere shows the drop card and goes to the open Flow', async () => {
    wrapper = mount(Host, { props: { flowId: 'A', projectId: 'p', title: 'Flow A', isReadOnly: () => false }, global, attachTo: document.body })
    const file = new File(['x'], 'drop.txt')
    const transfer = { types: ['Files'], files: [file], dropEffect: 'none' }
    const over = new Event('dragover', { bubbles: true, cancelable: true }); Object.assign(over, { dataTransfer: transfer })
    document.body.dispatchEvent(over); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-drop-overlay"]').text()).toContain('Flow A')
    const drop = new Event('drop', { bubbles: true, cancelable: true }); Object.assign(drop, { dataTransfer: transfer })
    document.body.dispatchEvent(drop); await flushPromises()
    expect(wrapper.find('[data-test="flow-attachment-drop-overlay"]').exists()).toBe(false)
    expect(api.uploadFile).toHaveBeenCalledWith('A', file)
  })
})
